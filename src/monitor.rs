//! Bounded recording of transitions already executed by an application.
//!
//! The recorder never calls `Model::step`, executes outputs, or schedules work.
//! It checks each observation once and retains exact encoded states. Full
//! checking remains mandatory; check-only sampling uses `check_observed`.
//! Retention budgets count serialized evidence, not process memory or arbitrary
//! allocations inside application callbacks and legacy codecs.

use crate::execution::{CheckPolicy, check_observed_into};
use crate::model::{
    Check, EncodeBuffer, Model, ModelCodec, ModelError, ModelMetadata, Transition, TransitionRef,
};
use crate::trace::{
    EncodedSize, FILE_HEADER_SIZE, FRAME_OVERHEAD, ReadLimits, RunConfig, Termination, Trace,
    TraceError, TraceStep, TraceView, end_size, error_termination, reserved_end_size, run_size,
    step_size, validate_totals,
};
use std::cell::Cell;
use std::collections::VecDeque;
use std::fmt::Write as _;
use std::io::Write;

const PARAMETER_PREFIX: &str = "stateless.monitor.";

/// Runtime retention and persistence limits. Bytes refer to the complete encoded
/// artifact, including its checkpoint, metadata, checks, framing and footer.
/// Capacity retained by reusable buffers and model callback allocations are not
/// included. Active prefixes reserve space for a bounded error footer. A frozen
/// property failure needs only its actual terminal footer.
#[derive(Clone, Debug)]
pub struct RecorderOptions {
    pub max_steps: usize,
    pub max_retained_bytes: u64,
    pub limits: ReadLimits,
}

impl Default for RecorderOptions {
    fn default() -> Self {
        let limits = ReadLimits::default();
        Self {
            max_steps: 1024,
            max_retained_bytes: limits.max_total_bytes,
            limits,
        }
    }
}

struct Observation {
    step: TraceStep,
    state_check_count: usize,
    size: EncodedSize,
}

fn empty_step() -> TraceStep {
    TraceStep {
        input: Vec::new(),
        disposition: crate::model::Disposition::Accepted,
        outputs: Vec::new(),
        post_state: Vec::new(),
        checks: Vec::new(),
    }
}

/// A contiguous window of runtime observations and its replayable checkpoint.
///
/// Freeze on the first property failure or recording error. Applications decide
/// how to react; the recorder does not stop application execution. Callers must
/// supply every transition in order, including ignored and rejected inputs.
/// State continuity cannot detect omitted transitions that return to the same
/// state; reliable delivery of observations remains the application's obligation.
pub struct Recorder {
    metadata: ModelMetadata,
    config: RunConfig,
    initial_state: Vec<u8>,
    initial_checks: Vec<Check>,
    steps: VecDeque<Observation>,
    options: RecorderOptions,
    observed_steps: u64,
    evicted_steps: u64,
    termination: Termination,
    step_size: EncodedSize,
    run_size: EncodedSize,
    reserved_footer: EncodedSize,
    before_scratch: Vec<u8>,
    spare_step: TraceStep,
}

impl Recorder {
    /// Starts at the application's supplied state, which may be a mid-run
    /// checkpoint. Does not call `initial_state` or `step`. The state must include
    /// pending work and property history needed for subsequent replay.
    ///
    /// Uses the default finite format limits and a 64 MiB encoded retention
    /// budget. Use `with_options` to select other limits. `max_steps` is positive
    /// and may not exceed the selected format's step limit.
    pub fn new<M: ModelCodec>(
        model: &M,
        initial_state: &M::State,
        config: RunConfig,
        max_steps: usize,
    ) -> Result<Self, ModelError> {
        Self::with_options(
            model,
            initial_state,
            config,
            RecorderOptions {
                max_steps,
                ..RecorderOptions::default()
            },
        )
    }

    pub fn with_options<M: ModelCodec>(
        model: &M,
        initial_state: &M::State,
        config: RunConfig,
        options: RecorderOptions,
    ) -> Result<Self, ModelError> {
        if options.max_steps == 0 || options.max_steps > options.limits.max_steps {
            return Err(ModelError::new(
                "runtime recorder max_steps must be positive and within trace limits",
            ));
        }
        if options.max_retained_bytes == 0 {
            return Err(ModelError::new(
                "runtime recorder byte budget must be positive",
            ));
        }
        if config
            .parameters
            .iter()
            .any(|(key, _)| key.starts_with(PARAMETER_PREFIX))
        {
            return Err(ModelError::new(
                "stateless.monitor. parameters are reserved",
            ));
        }
        let mut config = crate::execution::stamp_config(config)?;
        for (key, value) in [
            ("max_steps", options.max_steps.to_string()),
            ("max_retained_bytes", options.max_retained_bytes.to_string()),
            ("checking", "full".to_string()),
            ("observed_steps", "0".to_string()),
            ("evicted_steps", "0".to_string()),
            ("checkpoint_after_sequence", "0".to_string()),
        ] {
            config
                .parameters
                .push((format!("{PARAMETER_PREFIX}{key}"), value));
        }
        // The final three counter strings are updated in place without cloning
        // the configuration on every observation.
        for (_, value) in config.parameters.iter_mut().rev().take(3) {
            value.reserve(20);
        }
        let metadata = model.metadata();
        let reserved_footer = reserved_end_size(&options.limits).map_err(format_error)?;
        let mut encoded = Vec::new();
        let mut encoder = EncodeBuffer::new(&mut encoded, blob_limit(&options));
        model
            .encode_state_into(initial_state, &mut encoder)
            .map_err(|e| stage("encode checkpoint", e))?;
        encoder
            .finish()
            .map_err(|e| stage("encode checkpoint", e))?;
        let mut checks = Vec::new();
        model
            .check_state_into(initial_state, &mut checks)
            .map_err(|e| stage("check checkpoint", e))?;
        let failed = checks.iter().any(Check::is_failure);
        let run_size = run_size(&metadata, &config, &encoded, &checks, &options.limits)
            .map_err(format_error)?;
        let footer = if failed {
            end_size(0, &Termination::PropertyFailed, &options.limits).map_err(format_error)?
        } else {
            reserved_footer
        };
        validate_budget(&options, run_size, EncodedSize::default(), footer, 0)?;
        Ok(Self {
            metadata,
            config,
            initial_state: encoded,
            initial_checks: checks,
            steps: VecDeque::new(),
            options,
            observed_steps: 0,
            evicted_steps: 0,
            termination: if failed {
                Termination::PropertyFailed
            } else {
                Termination::Interrupted
            },
            step_size: EncodedSize::default(),
            run_size,
            reserved_footer,
            before_scratch: Vec::new(),
            spare_step: empty_step(),
        })
    }

    /// Checks and records an existing transition without executing it again.
    /// Returns that observation's state and transition checks in replay order.
    ///
    /// Candidate encoding and all limit checks finish before eviction. An
    /// oversized observation or callback error freezes the last coherent prefix;
    /// the returned error is complete, while the exported reason is bounded to
    /// 256 UTF-8 bytes (and may be shorter for restrictive format limits).
    pub fn observe<M: ModelCodec>(
        &mut self,
        model: &M,
        before: &M::State,
        input: &M::Input,
        transition: &Transition<M::State, M::Output>,
    ) -> Result<&[Check], ModelError> {
        if self.is_frozen() {
            return Err(ModelError::new(
                "runtime recorder is frozen; export its retained trace",
            ));
        }
        let prepared = self.prepare(model, before, input, transition);
        let observation = match prepared {
            Ok(observation) => observation,
            Err(error) => return Err(self.freeze(error)),
        };
        let fit = self.fit(&observation);
        let (evictions, run_size, step_size) = match fit {
            Ok(fit) => fit,
            Err(error) => return Err(self.freeze(error)),
        };
        if evictions == 0 && self.steps.try_reserve(1).is_err() {
            self.update_counts(self.observed_steps, self.evicted_steps);
            return Err(self.freeze(ModelError::new("unable to allocate runtime trace storage")));
        }
        for _ in 0..evictions {
            let mut evicted = self.steps.pop_front().expect("validated eviction count");
            std::mem::swap(&mut self.initial_state, &mut evicted.step.post_state);
            // Transition checks do not become checkpoint state checks.
            evicted.step.checks.truncate(evicted.state_check_count);
            std::mem::swap(&mut self.initial_checks, &mut evicted.step.checks);
            self.spare_step = evicted.step;
        }
        if observation.step.checks.iter().any(Check::is_failure) {
            self.termination = Termination::PropertyFailed;
        }
        self.steps.push_back(observation);
        self.observed_steps += 1;
        self.evicted_steps += evictions as u64;
        self.run_size = run_size;
        self.step_size = step_size;
        Ok(&self
            .steps
            .back()
            .expect("just inserted observation")
            .step
            .checks)
    }

    fn prepare<M: ModelCodec>(
        &mut self,
        model: &M,
        before: &M::State,
        input: &M::Input,
        transition: &Transition<M::State, M::Output>,
    ) -> Result<Observation, ModelError> {
        if model.metadata() != self.metadata {
            return Err(ModelError::new("runtime recorder model identity changed"));
        }
        let sequence = self
            .observed_steps
            .checked_add(1)
            .ok_or_else(|| ModelError::new("runtime observation sequence overflow"))?;
        let maximum = blob_limit(&self.options);
        let mut encoder = EncodeBuffer::new(&mut self.before_scratch, maximum);
        model
            .encode_state_into(before, &mut encoder)
            .map_err(|e| stage("encode before state", e))?;
        encoder
            .finish()
            .map_err(|e| stage("encode before state", e))?;
        let expected_before = self
            .steps
            .back()
            .map_or(&self.initial_state, |s| &s.step.post_state);
        if &self.before_scratch != expected_before {
            return Err(ModelError::new(
                "runtime before state is discontinuous; observations are missing or reordered",
            ));
        }
        if transition.outputs.len() > self.options.limits.max_outputs_per_step {
            return Err(format_error(TraceError::LimitExceeded("outputs per step")));
        }
        if transition.outputs.len() > self.options.limits.max_items {
            return Err(format_error(TraceError::LimitExceeded("aggregate items")));
        }
        // Every encoded output has a four-byte length even when empty. Reject
        // impossible counts before allocating the vector of encoded outputs.
        let frame_payload_budget = (self.options.limits.max_frame_bytes.min(u32::MAX as usize)
            as u64)
            .min(self.options.max_retained_bytes)
            .min(self.options.limits.max_total_bytes);
        if transition.outputs.len() as u64 > frame_payload_budget / 4 {
            return Err(format_error(TraceError::LimitExceeded("frame bytes")));
        }
        if let crate::model::Disposition::Ignored(reason)
        | crate::model::Disposition::Rejected(reason) = &transition.disposition
        {
            // The transition belongs to the application; validate its borrowed
            // reason before copying it into the recorder's owned candidate.
            if reason.len() > self.options.limits.max_string_bytes.min(u32::MAX as usize) {
                return Err(format_error(TraceError::LimitExceeded("string bytes")));
            }
            if reason.len() as u64 > frame_payload_budget {
                return Err(format_error(TraceError::LimitExceeded("frame bytes")));
            }
        }
        let mut step = std::mem::replace(&mut self.spare_step, empty_step());
        let mut encoder = EncodeBuffer::new(&mut step.input, maximum);
        model
            .encode_input_into(input, &mut encoder)
            .map_err(|e| stage("encode input", e))?;
        encoder.finish().map_err(|e| stage("encode input", e))?;
        let capture = CaptureStateCheckCount {
            model,
            count: Cell::new(0),
        };
        check_observed_into(
            &capture,
            before,
            input,
            transition,
            sequence,
            CheckPolicy::default(),
            &mut step.checks,
        )?;
        step.disposition = transition.disposition.clone();
        step.post_state.clear();
        if transition.outputs.len() > step.outputs.len() {
            step.outputs
                .try_reserve_exact(transition.outputs.len() - step.outputs.len())
                .map_err(|_| format_error(TraceError::AllocationFailed))?;
        }
        step.outputs.resize_with(transition.outputs.len(), Vec::new);
        for output in &mut step.outputs {
            output.clear();
        }
        // Measure fixed fields and check strings first. Subsequent blobs share
        // one remaining frame allowance, so many individually legal outputs
        // cannot accumulate an oversized temporary observation.
        let base = step_size(&step, &self.options.limits).map_err(format_error)?;
        let frame_budget =
            self.options.limits.max_frame_bytes.min(u32::MAX as usize) as u64 + FRAME_OVERHEAD;
        let frame_budget = frame_budget
            .min(self.options.max_retained_bytes)
            .min(self.options.limits.max_total_bytes);
        let mut remaining = frame_budget
            .checked_sub(base.bytes)
            .ok_or_else(|| format_error(TraceError::LimitExceeded("frame bytes")))?;
        for (output, encoded) in transition.outputs.iter().zip(&mut step.outputs) {
            let limit = maximum.min(usize::try_from(remaining).unwrap_or(usize::MAX));
            let mut encoder = EncodeBuffer::new(encoded, limit);
            model
                .encode_output_into(output, &mut encoder)
                .map_err(|e| stage("encode output", e))?;
            encoder.finish().map_err(|e| stage("encode output", e))?;
            remaining -= encoded.len() as u64;
        }
        let limit = maximum.min(usize::try_from(remaining).unwrap_or(usize::MAX));
        let mut encoder = EncodeBuffer::new(&mut step.post_state, limit);
        model
            .encode_state_into(&transition.state, &mut encoder)
            .map_err(|e| stage("encode post state", e))?;
        encoder
            .finish()
            .map_err(|e| stage("encode post state", e))?;
        let size = step_size(&step, &self.options.limits).map_err(format_error)?;
        Ok(Observation {
            step,
            state_check_count: capture.count.get(),
            size,
        })
    }

    fn fit(
        &mut self,
        observation: &Observation,
    ) -> Result<(usize, EncodedSize, EncodedSize), ModelError> {
        let mut steps = EncodedSize {
            bytes: self
                .step_size
                .bytes
                .checked_add(observation.size.bytes)
                .ok_or_else(|| format_error(TraceError::LimitExceeded("total bytes")))?,
            items: self
                .step_size
                .items
                .checked_add(observation.size.items)
                .ok_or_else(|| format_error(TraceError::LimitExceeded("aggregate items")))?,
        };
        let observed = self.observed_steps + 1; // prepare checked overflow.
        let footer = if observation.step.checks.iter().any(Check::is_failure) {
            end_size(0, &Termination::PropertyFailed, &self.options.limits).map_err(format_error)?
        } else {
            self.reserved_footer
        };
        // A full step-limited window must evict its first entry. Start at that
        // candidate instead of manufacturing and discarding a limit error on
        // every ordinary observation. No retained evidence is changed here.
        let minimum_evictions = usize::from(self.steps.len() == self.options.max_steps);
        if minimum_evictions == 1 {
            let evicted = self.steps.front().expect("positive full recorder capacity");
            steps.bytes -= evicted.size.bytes;
            steps.items -= evicted.size.items;
        }
        let mut last_error = None;
        for evictions in minimum_evictions..=self.steps.len() {
            self.update_counts(observed, self.evicted_steps + evictions as u64);
            let (state, checks) = if evictions == 0 {
                (
                    self.initial_state.as_slice(),
                    self.initial_checks.as_slice(),
                )
            } else {
                let checkpoint = &self.steps[evictions - 1];
                (
                    checkpoint.step.post_state.as_slice(),
                    &checkpoint.step.checks[..checkpoint.state_check_count],
                )
            };
            let run = run_size(
                &self.metadata,
                &self.config,
                state,
                checks,
                &self.options.limits,
            )
            .map_err(format_error);
            let retained = self.steps.len() + 1 - evictions;
            match run.and_then(|run| {
                validate_budget(&self.options, run, steps, footer, retained)?;
                Ok(run)
            }) {
                Ok(run) => return Ok((evictions, run, steps)),
                Err(error) => last_error = Some(error),
            }
            if let Some(evicted) = self.steps.get(evictions) {
                steps.bytes -= evicted.size.bytes;
                steps.items -= evicted.size.items;
            }
        }
        self.update_counts(self.observed_steps, self.evicted_steps);
        Err(last_error.expect("at least one retained window was considered"))
    }

    fn update_counts(&mut self, observed: u64, evicted: u64) {
        let offset = self.config.parameters.len() - 3;
        for (index, count) in [observed, evicted, evicted].into_iter().enumerate() {
            let value = &mut self.config.parameters[offset + index].1;
            value.clear();
            write!(value, "{count}").expect("writing to a String cannot fail");
        }
    }

    fn freeze(&mut self, error: ModelError) -> ModelError {
        self.termination = error_termination(&error.0, &self.options.limits);
        error
    }

    /// Copies retained evidence. For persistence without this deep copy, use
    /// `write_to`; to transfer ownership, use `into_trace`.
    pub fn snapshot(&self) -> Trace {
        Trace {
            metadata: self.metadata.clone(),
            config: self.config.clone(),
            initial_state: self.initial_state.clone(),
            initial_checks: self.initial_checks.clone(),
            steps: self.steps.iter().map(|s| s.step.clone()).collect(),
            termination: self.termination.clone(),
        }
    }

    /// Transfer retained byte buffers and check observations into a trace.
    pub fn into_trace(self) -> Trace {
        Trace {
            metadata: self.metadata,
            config: self.config,
            initial_state: self.initial_state,
            initial_checks: self.initial_checks,
            steps: self.steps.into_iter().map(|s| s.step).collect(),
            termination: self.termination,
        }
    }

    /// Export directly under the capture limits, without cloning retained data.
    /// As with `Trace::write_to`, the caller owns flushing and durable publication.
    pub fn write_to(&self, writer: impl Write) -> Result<(), TraceError> {
        self.write_with_limits(writer, &self.options.limits)
    }

    pub fn write_with_limits(
        &self,
        writer: impl Write,
        limits: &ReadLimits,
    ) -> Result<(), TraceError> {
        TraceView {
            metadata: &self.metadata,
            config: &self.config,
            initial_state: &self.initial_state,
            initial_checks: &self.initial_checks,
            steps: self.steps.iter().map(|s| &s.step),
            step_count: self.steps.len(),
            termination: &self.termination,
        }
        .write_with_limits(writer, limits)
    }

    /// Exact serialized size of the current artifact. Active capture additionally
    /// reserves room for a possible error footer; neither figure is process RSS.
    pub fn retained_bytes(&self) -> u64 {
        FILE_HEADER_SIZE
            + self.run_size.bytes
            + self.step_size.bytes
            + end_size(self.steps.len(), &self.termination, &self.options.limits)
                .expect("validated footer")
                .bytes
    }

    pub fn retained_steps(&self) -> usize {
        self.steps.len()
    }
    pub fn observed_steps(&self) -> u64 {
        self.observed_steps
    }
    pub fn evicted_steps(&self) -> u64 {
        self.evicted_steps
    }
    pub fn is_frozen(&self) -> bool {
        !matches!(self.termination, Termination::Interrupted)
    }
}

fn blob_limit(options: &RecorderOptions) -> usize {
    options
        .limits
        .max_blob_bytes
        .min(u32::MAX as usize)
        .min(options.limits.max_frame_bytes)
        .min(
            usize::try_from(
                options
                    .max_retained_bytes
                    .min(options.limits.max_total_bytes),
            )
            .unwrap_or(usize::MAX),
        )
}

fn validate_budget(
    options: &RecorderOptions,
    run: EncodedSize,
    steps: EncodedSize,
    footer: EncodedSize,
    count: usize,
) -> Result<(), ModelError> {
    let bytes = FILE_HEADER_SIZE
        .checked_add(run.bytes)
        .and_then(|n| n.checked_add(steps.bytes))
        .and_then(|n| n.checked_add(footer.bytes))
        .ok_or_else(|| format_error(TraceError::LimitExceeded("total bytes")))?;
    let items = run
        .items
        .checked_add(steps.items)
        .ok_or_else(|| format_error(TraceError::LimitExceeded("aggregate items")))?;
    validate_totals(bytes, items, count, &options.limits).map_err(format_error)?;
    if bytes > options.max_retained_bytes {
        return Err(ModelError::new(
            "runtime recording exceeds retained byte budget",
        ));
    }
    if count > options.max_steps {
        return Err(ModelError::new(
            "runtime recording exceeds retained step budget",
        ));
    }
    Ok(())
}

// Keep the state-check boundary without running or cloning checks a second time.
struct CaptureStateCheckCount<'a, M> {
    model: &'a M,
    count: Cell<usize>,
}
impl<M: Model> Model for CaptureStateCheckCount<'_, M> {
    type State = M::State;
    type Input = M::Input;
    type Output = M::Output;
    fn metadata(&self) -> ModelMetadata {
        self.model.metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        self.model.initial_state()
    }
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.model.step(state, input)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        let checks = self.model.check_state(state)?;
        self.count.set(checks.len());
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut Vec<Check>,
    ) -> Result<(), ModelError> {
        let start = checks.len();
        self.model.check_state_into(state, checks)?;
        self.count.set(checks.len() - start);
        Ok(())
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.model.check_transition(before, input, transition)
    }
    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
        checks: &mut Vec<Check>,
    ) -> Result<(), ModelError> {
        self.model
            .check_transition_into(before, input, transition, checks)
    }
}

fn stage(stage: &str, error: ModelError) -> ModelError {
    ModelError::new(format!("runtime {stage}: {error}"))
}
fn format_error(error: TraceError) -> ModelError {
    ModelError::new(format!("runtime recording limit: {error}"))
}
