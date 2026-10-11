//! Stored evidence browsing is separate from execution and verification.
use crate::session::{DebugError, DebugSession, InputPolicy, SessionLimits};
use stateless::execution::{
    ReplayOptions, ReplayOutcome, ReplayReport, replay_with_observations_bounded,
    validate_recording,
};
use stateless::explore::{
    CheckPhase, Failure, PropertyFailure, ShrinkConfig, ShrinkLimits, ShrinkReport,
    shrink_with_limits,
};
use stateless::monitor::RecorderOptions;
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{
    Check, CheckSink, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng, Transition,
    TransitionRef,
};
use std::io::{Read, Write};

/// A content binding for accidental sidecar mismatch detection, not authentication.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArtifactIdentity {
    pub fnv1a64: u64,
    pub bytes: u64,
}
struct DigestWriter {
    hash: u64,
    bytes: u64,
}
impl Write for DigestWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        for byte in bytes {
            self.hash = (self.hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3);
        }
        self.bytes = self
            .bytes
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| std::io::Error::other("artifact size overflow"))?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
pub fn artifact_identity(trace: &Trace) -> Result<ArtifactIdentity, ModelError> {
    let mut writer = DigestWriter {
        hash: 0xcbf29ce484222325,
        bytes: 0,
    };
    trace
        .write_to(&mut writer)
        .map_err(|e| ModelError::new(e.to_string()))?;
    Ok(ArtifactIdentity {
        fnv1a64: writer.hash,
        bytes: writer.bytes,
    })
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EncodedPreview {
    pub bytes: Vec<u8>,
    pub total_bytes: usize,
    pub complete: bool,
    pub provenance: &'static str,
}
impl EncodedPreview {
    fn new(bytes: &[u8], maximum: usize) -> Self {
        Self {
            bytes: bytes[..bytes.len().min(maximum)].to_vec(),
            total_bytes: bytes.len(),
            complete: bytes.len() <= maximum,
            provenance: "stored-exact-encoding",
        }
    }
}
/// Outputs cannot be reconstructed generically from ModelCodec. An application
/// can provide this optional adapter, or clients can inspect exact byte previews.
pub trait HistoricalOutputDecoder<M: Model> {
    fn decode_output(&self, bytes: &[u8]) -> Result<M::Output, ModelError>;
}
pub struct TraceViewer {
    trace: Trace,
    identity: ArtifactIdentity,
    position: usize,
    checkpoint_after: u64,
}
impl TraceViewer {
    pub fn read(reader: impl Read, limits: &ReadLimits) -> Result<Self, ModelError> {
        Self::new(Trace::read_from(reader, limits).map_err(|e| ModelError::new(e.to_string()))?)
    }
    pub fn new(trace: Trace) -> Result<Self, ModelError> {
        validate_recording(&trace)?;
        let identity = artifact_identity(&trace)?;
        let checkpoint_after = trace
            .config
            .parameters
            .iter()
            .find(|(k, _)| k == "stateless.monitor.checkpoint_after_sequence")
            .map(|(_, v)| {
                v.parse::<u64>()
                    .map_err(|_| ModelError::new("invalid retained checkpoint sequence"))
            })
            .transpose()?
            .unwrap_or(0);
        checkpoint_after
            .checked_add(trace.steps.len() as u64)
            .ok_or_else(|| ModelError::new("retained sequence overflow"))?;
        Ok(Self {
            trace,
            identity,
            position: 0,
            checkpoint_after,
        })
    }
    pub fn trace(&self) -> &Trace {
        &self.trace
    }
    pub fn identity(&self) -> &ArtifactIdentity {
        &self.identity
    }
    pub fn position(&self) -> usize {
        self.position
    }
    pub fn retained_range(&self) -> (u64, u64) {
        (
            self.checkpoint_after,
            self.checkpoint_after + self.trace.steps.len() as u64,
        )
    }
    pub fn seek(&mut self, sequence: usize) -> Result<(), ModelError> {
        if sequence > self.trace.steps.len() {
            return Err(ModelError::new("snapshot outside retained history"));
        }
        self.position = sequence;
        Ok(())
    }
    pub fn state_bytes(&self, maximum: usize) -> EncodedPreview {
        EncodedPreview::new(
            self.snapshot_bytes(self.position)
                .expect("validated cursor"),
            maximum,
        )
    }
    pub fn checks(&self) -> &[Check] {
        if self.position == 0 {
            &self.trace.initial_checks
        } else {
            &self.trace.steps[self.position - 1].checks
        }
    }
    pub fn outputs(
        &self,
        offset: usize,
        limit: usize,
        max_bytes_each: usize,
    ) -> Result<(Vec<EncodedPreview>, bool), ModelError> {
        if limit == 0 || limit > 1000 || max_bytes_each > 4 * 1024 {
            return Err(ModelError::new("output preview budget exceeded"));
        }
        let outputs = if self.position == 0 {
            &[][..]
        } else {
            self.trace.steps[self.position - 1].outputs.as_slice()
        };
        if offset > outputs.len() {
            return Err(ModelError::new("output offset outside snapshot"));
        }
        let end = offset.saturating_add(limit).min(outputs.len());
        Ok((
            outputs[offset..end]
                .iter()
                .map(|bytes| EncodedPreview::new(bytes, max_bytes_each))
                .collect(),
            end == outputs.len(),
        ))
    }
    pub fn snapshot_bytes(&self, sequence: usize) -> Result<&[u8], ModelError> {
        if sequence == 0 {
            return Ok(&self.trace.initial_state);
        }
        self.trace
            .steps
            .get(sequence - 1)
            .map(|step| step.post_state.as_slice())
            .ok_or_else(|| ModelError::new("snapshot outside retained history"))
    }
    pub fn decode_state<M: ModelCodec>(
        &self,
        model: &M,
        sequence: usize,
    ) -> Result<M::State, ModelError> {
        if model.metadata() != self.trace.metadata {
            return Err(ModelError::new(
                "snapshot decoder identity differs; verify the selected build before branching",
            ));
        }
        model.decode_state(self.snapshot_bytes(sequence)?)
    }
    /// Explicit verification does not move the view cursor or mutate the trace.
    pub fn verify<M: ModelCodec>(
        &self,
        model: &M,
        options: ReplayOptions,
    ) -> Result<ReplayReport, ModelError> {
        replay_with_observations_bounded(
            model,
            &self.trace,
            options,
            &ReadLimits::default(),
            |_, _| Ok(()),
        )
        .map_err(|error| error.error)
    }
    fn prefix(&self, sequence: usize) -> Result<Trace, ModelError> {
        self.snapshot_bytes(sequence)?;
        if self.trace.initial_checks.iter().any(Check::is_failure)
            || (sequence > 0
                && self.trace.steps[sequence - 1]
                    .checks
                    .iter()
                    .any(Check::is_failure))
        {
            return Err(ModelError::new(
                "normal branches must start before property failure",
            ));
        }
        let mut prefix = self.trace.clone();
        prefix.steps.truncate(sequence);
        prefix.termination = Termination::Completed;
        Ok(prefix)
    }
    /// Re-executes the chosen prefix with the selected build before decoding the
    /// fork checkpoint. New-build intermediate restores are never silently trusted.
    #[allow(clippy::too_many_arguments)] // Independent explicit authority, replay and retention policies.
    pub fn fork_verified<M: ModelCodec>(
        &self,
        id: impl Into<String>,
        model: M,
        sequence: usize,
        options: ReplayOptions,
        policy: InputPolicy<M>,
        limits: SessionLimits,
        recorder: RecorderOptions,
    ) -> Result<Branch<M>, DebugError> {
        let prefix = self
            .prefix(sequence)
            .map_err(|e| DebugError::InvalidCommand(e.to_string()))?;
        let report = replay_with_observations_bounded(
            &model,
            &prefix,
            options,
            &ReadLimits::default(),
            |_, _| Ok(()),
        )
        .map_err(|e| DebugError::Callback(e.to_string()))?;
        if report.outcome != ReplayOutcome::Exact {
            return Err(DebugError::InvalidCommand(format!(
                "fork prefix is not verified: {:?}",
                report.outcome
            )));
        }
        if let Some((_, parent_policy)) = self
            .trace
            .config
            .parameters
            .iter()
            .find(|(key, _)| key == "stateless.debug.environment")
            && parent_policy != &policy.label
            && !policy.is_unrestricted()
        {
            return Err(DebugError::InvalidCommand("branch environment differs; select explicit unrestricted injection or retain the parent's policy".into()));
        }
        let state = model
            .decode_state(
                self.snapshot_bytes(sequence)
                    .map_err(|e| DebugError::InvalidCommand(e.to_string()))?,
            )
            .map_err(|e| DebugError::Callback(e.to_string()))?;
        let provenance = BranchProvenance {
            version: 1,
            parent: self.identity.clone(),
            fork_sequence: sequence,
            checkpoint: bytes_identity(
                self.snapshot_bytes(sequence)
                    .map_err(|e| DebugError::InvalidCommand(e.to_string()))?,
            ),
            parent_metadata: self.trace.metadata.clone(),
            model_metadata: model.metadata(),
            parent_prefix_verified: true,
            environment: policy.label.clone(),
            origin: if policy.is_unrestricted() {
                "out-of-domain-injection"
            } else {
                "verified-simulation-branch"
            }
            .into(),
        };
        let config = RunConfig {
            strategy: "debugger-branch".into(),
            seed: self.trace.config.seed,
            parameters: provenance.parameters(),
        };
        let session = DebugSession::recording_from_state_with_provenance(
            id, model, state, policy, limits, config, recorder,
        )?;
        Ok(Branch {
            session,
            provenance,
        })
    }
}
fn bytes_identity(bytes: &[u8]) -> ArtifactIdentity {
    let mut writer = DigestWriter {
        hash: 0xcbf29ce484222325,
        bytes: 0,
    };
    writer
        .write_all(bytes)
        .expect("a Rust slice cannot overflow its own byte length");
    ArtifactIdentity {
        fnv1a64: writer.hash,
        bytes: writer.bytes,
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BranchProvenance {
    pub version: u32,
    pub parent: ArtifactIdentity,
    pub fork_sequence: usize,
    pub checkpoint: ArtifactIdentity,
    pub parent_metadata: ModelMetadata,
    pub model_metadata: ModelMetadata,
    pub parent_prefix_verified: bool,
    pub environment: String,
    pub origin: String,
}
impl BranchProvenance {
    pub fn validate_parent(&self, trace: &Trace) -> Result<(), ModelError> {
        if self.version != 1
            || artifact_identity(trace)? != self.parent
            || trace.metadata != self.parent_metadata
        {
            return Err(ModelError::new(
                "branch sidecar does not match parent artifact",
            ));
        }
        let snapshot = if self.fork_sequence == 0 {
            &trace.initial_state
        } else {
            &trace
                .steps
                .get(self.fork_sequence - 1)
                .ok_or_else(|| ModelError::new("fork outside parent history"))?
                .post_state
        };
        if bytes_identity(snapshot) != self.checkpoint {
            return Err(ModelError::new("fork checkpoint binding differs"));
        }
        Ok(())
    }
    fn parameters(&self) -> Vec<(String, String)> {
        vec![
            (
                "stateless.debug.parent.digest".into(),
                format!("fnv1a64:{:016x}", self.parent.fnv1a64),
            ),
            (
                "stateless.debug.parent.bytes".into(),
                self.parent.bytes.to_string(),
            ),
            (
                "stateless.debug.fork.sequence".into(),
                self.fork_sequence.to_string(),
            ),
            (
                "stateless.debug.fork.checkpoint".into(),
                format!("fnv1a64:{:016x}", self.checkpoint.fnv1a64),
            ),
            (
                "stateless.debug.parent.build".into(),
                self.parent_metadata.build.clone(),
            ),
            (
                "stateless.debug.parent_prefix_verified".into(),
                self.parent_prefix_verified.to_string(),
            ),
            ("stateless.debug.branch.origin".into(), self.origin.clone()),
        ]
    }
}
pub struct Branch<M: Model> {
    pub session: DebugSession<M>,
    pub provenance: BranchProvenance,
}

/// Decode a fresh independent checkpoint for every shrink candidate. Delegates
/// generation/causality to the same application model rather than resetting it.
struct CheckpointModel<'a, M: ModelCodec> {
    model: &'a M,
    checkpoint: &'a [u8],
    policy: Option<&'a InputPolicy<M>>,
    max_admission_candidates: usize,
}
impl<M: ModelCodec> Model for CheckpointModel<'_, M> {
    type State = M::State;
    type Input = M::Input;
    type Output = M::Output;
    fn metadata(&self) -> ModelMetadata {
        self.model.metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        self.model.decode_state(self.checkpoint)
    }
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.model.step(state, input)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        self.model.check_state(state)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model.check_state_into(state, checks)
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
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model
            .check_transition_into(before, input, transition, checks)
    }
}
impl<M: ModelCodec + Generate> Generate for CheckpointModel<'_, M> {
    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError> {
        self.model.generate(state, rng)
    }
    fn is_enabled(&self, state: &Self::State, input: &Self::Input) -> Result<bool, ModelError> {
        if let Some(policy) = self.policy
            && !policy.permits(self.model, state, input, self.max_admission_candidates)?
        {
            return Ok(false);
        }
        self.model.is_enabled(state, input)
    }
    fn simpler_inputs(&self, input: &Self::Input) -> Vec<Self::Input> {
        self.model.simpler_inputs(input)
    }
}
/// Bounded causal minimization from the artifact's actual composite checkpoint.
/// Legacy traces without debugger policy provenance retain Generate's causal
/// contract. Explicitly unrestricted debugger traces are also supported. A trace
/// captured under a declared policy requires [`minimize_with_policy`]; a policy
/// label cannot reconstruct its validation callback.
///
/// The application must identify the target check phase; core traces preserve
/// ordered checks but do not encode the state/transition split.
pub fn minimize<M: ModelCodec + Generate>(
    model: &M,
    trace: &Trace,
    target: PropertyFailure,
    config: ShrinkConfig,
    limits: ShrinkLimits<'_>,
) -> Result<ShrinkReport<M::Input>, ModelError> {
    validate_minimization_policy::<M>(trace, None)?;
    minimize_impl(model, trace, None, target, config, limits)
}

/// Minimize with the same environmental admission policy used for recording.
/// Recorded policy labels and unrestricted/declared origins must match. Labels
/// identify application contracts, not authenticate callbacks: the caller must
/// supply the original policy implementation. Legacy traces without a policy
/// may be explicitly narrowed by the supplied policy.
///
/// Every candidate uses both this policy and Generate::is_enabled, so neither
/// environmental admission nor the shrinker's causal restrictions are bypassed.
/// Enumerated admission examines at most max_admission_candidates plus one
/// lookahead value per candidate input. Application callbacks are cooperative.
pub fn minimize_with_policy<M: ModelCodec + Generate>(
    model: &M,
    trace: &Trace,
    policy: &InputPolicy<M>,
    max_admission_candidates: usize,
    target: PropertyFailure,
    config: ShrinkConfig,
    limits: ShrinkLimits<'_>,
) -> Result<ShrinkReport<M::Input>, ModelError> {
    if max_admission_candidates == 0 {
        return Err(ModelError::new(
            "minimization admission budget must be positive",
        ));
    }
    validate_minimization_policy(trace, Some(policy))?;
    minimize_impl(
        model,
        trace,
        Some((policy, max_admission_candidates)),
        target,
        config,
        limits,
    )
}

fn validate_minimization_policy<M: Model>(
    trace: &Trace,
    policy: Option<&InputPolicy<M>>,
) -> Result<(), ModelError> {
    let unique = |name: &str| -> Result<Option<&str>, ModelError> {
        let mut values = trace
            .config
            .parameters
            .iter()
            .filter(|(key, _)| key == name);
        let value = values.next().map(|(_, value)| value.as_str());
        if values.next().is_some() {
            return Err(ModelError::new("ambiguous recorded environmental policy"));
        }
        Ok(value)
    };
    let environment = unique("stateless.debug.environment")?;
    let origin = unique("stateless.debug.origin")?;
    match (environment, origin) {
        (None, None) => Ok(()),
        (Some(environment), Some(origin)) => {
            let unrestricted = match origin {
                "out-of-domain-injection" => true,
                "simulation" => false,
                _ => {
                    return Err(ModelError::new(
                        "unknown recorded environmental policy origin",
                    ));
                }
            };
            if let Some(policy) = policy {
                if policy.label != environment || policy.is_unrestricted() != unrestricted {
                    return Err(ModelError::new(
                        "minimization environment differs from recorded policy",
                    ));
                }
                Ok(())
            } else if unrestricted && environment == "out-of-domain-injection" {
                Ok(())
            } else {
                Err(ModelError::new(
                    "recorded environmental policy requires minimize_with_policy",
                ))
            }
        }
        _ => Err(ModelError::new(
            "incomplete recorded environmental policy provenance",
        )),
    }
}

fn minimize_impl<M: ModelCodec + Generate>(
    model: &M,
    trace: &Trace,
    policy: Option<(&InputPolicy<M>, usize)>,
    target: PropertyFailure,
    config: ShrinkConfig,
    limits: ShrinkLimits<'_>,
) -> Result<ShrinkReport<M::Input>, ModelError> {
    if config.max_attempts == 0 {
        return Err(ModelError::new(
            "minimization max_attempts must be greater than zero",
        ));
    }
    if !target.check.is_failure() {
        return Err(ModelError::new("minimization target must be a failure"));
    }
    let target_checks = if target.phase == CheckPhase::InitialState {
        trace.initial_checks.as_slice()
    } else {
        trace
            .steps
            .last()
            .map_or(&[][..], |step| step.checks.as_slice())
    };
    if !target_checks
        .iter()
        .any(|check| check.id == target.check.id && check.is_failure())
    {
        return Err(ModelError::new(
            "minimization target is not a recorded failure at the selected phase",
        ));
    }
    let verification_steps = trace.steps.len() as u64;
    if limits
        .max_replayed_transitions
        .is_some_and(|n| verification_steps > 0 && verification_steps >= n)
    {
        return Err(ModelError::new(
            "minimization budget cannot cover verification and original validation",
        ));
    }
    let started = limits
        .control
        .max_duration
        .map(|_| std::time::Instant::now());
    if limits
        .control
        .cancellation
        .is_some_and(|flag| flag.load(std::sync::atomic::Ordering::Relaxed))
    {
        return Err(ModelError::new(
            "minimization cancelled before verification",
        ));
    }
    let report = replay_with_observations_bounded(
        model,
        trace,
        ReplayOptions::default(),
        &ReadLimits::default(),
        |_, _| {
            if limits
                .control
                .cancellation
                .is_some_and(|flag| flag.load(std::sync::atomic::Ordering::Relaxed))
            {
                return Err(ModelError::new(
                    "minimization cancelled during verification",
                ));
            }
            if started
                .zip(limits.control.max_duration)
                .is_some_and(|(start, maximum)| start.elapsed() >= maximum)
            {
                return Err(ModelError::new(
                    "minimization deadline exhausted during verification",
                ));
            }
            Ok(())
        },
    )
    .map_err(|error| error.error)?;
    if report.outcome != ReplayOutcome::Exact || !report.failure_reproduced {
        return Err(ModelError::new(
            "minimization requires an exactly reproduced failure",
        ));
    }
    let inputs = trace
        .steps
        .iter()
        .map(|step| model.decode_input(&step.input))
        .collect::<Result<Vec<_>, _>>()?;
    let original = Failure {
        inputs,
        violations: vec![target],
    };
    let remaining_duration = match (started, limits.control.max_duration) {
        (Some(start), Some(maximum)) => {
            Some(maximum.checked_sub(start.elapsed()).ok_or_else(|| {
                ModelError::new("minimization deadline exhausted during verification")
            })?)
        }
        _ => None,
    };
    let mut result = shrink_with_limits(
        &CheckpointModel {
            model,
            checkpoint: &trace.initial_state,
            policy: policy.map(|(policy, _)| policy),
            max_admission_candidates: policy.map_or(0, |(_, maximum)| maximum),
        },
        &original,
        config,
        ShrinkLimits {
            control: stateless::explore::RunLimits {
                max_duration: remaining_duration,
                cancellation: limits.control.cancellation,
            },
            max_replayed_transitions: limits
                .max_replayed_transitions
                .map(|n| n - verification_steps),
        },
    )?;
    result.replayed_transitions = result
        .replayed_transitions
        .checked_add(verification_steps)
        .ok_or_else(|| ModelError::new("minimization work counter overflow"))?;
    Ok(result)
}
