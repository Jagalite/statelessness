//! Versioned evidence with check phases and host-supplied causal provenance.
//!
//! Version 2 wraps an unchanged v1 exact trace plus bounded phase/provenance
//! metadata. It never changes application codecs or executes external effects.
//! Provenance labels and CRCs are assertions/integrity checks, not authentication.
//! [`Artifact::read`] also accepts original v1 artifacts without inventing phases.
use crate::execution::{
    ReplayObservation, ReplayOptions, ReplayOutcome, replay_with_observations_bounded,
};
use crate::explore::CheckPhase;
use crate::monitor::{Recorder, RecorderOptions};
use crate::observation::{CheckedInitial, CheckedTurn};
use crate::trace::{ReadLimits, RunConfig, Termination, Trace};
use crate::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use crate::{CheckStatus, EncodeBuffer, ModelCodec, ModelError};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::path::Path;

pub const FORMAT_VERSION: u16 = 2;
const MAGIC: &[u8; 8] = b"STLESS\x1a\n";
const OVERHEAD: u64 = 32;

#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct Provenance {
    pub run: String,
    pub epoch: u64,
    pub manifest: Option<String>,
    pub source: Option<String>,
    /// Declared bounds, schedules and assumptions; does not certify their truth.
    pub environment: Vec<(String, String)>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OutputOrigin {
    /// Absolute host sequence, including a possibly evicted prefix.
    pub sequence: u64,
    pub output: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct CausalContext {
    pub machine: Option<String>,
    pub generation: Option<u64>,
    pub correlation: Option<String>,
    /// Modeled time, never a substitute for a telemetry clock domain.
    pub logical_time: Option<u64>,
    pub causes: Vec<OutputOrigin>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StepEvidence {
    pub sequence: u64,
    /// Checks before this boundary are state checks; the rest are transition checks.
    pub state_check_count: u32,
    pub causal: CausalContext,
}

#[derive(Clone, Debug)]
pub struct EvidenceLimits {
    pub trace: ReadLimits,
    pub max_metadata_bytes: usize,
    pub max_labels: usize,
    pub max_label_bytes: usize,
    pub max_causes_per_step: usize,
    pub max_total_causes: usize,
}
impl Default for EvidenceLimits {
    fn default() -> Self {
        Self {
            trace: ReadLimits::default(),
            max_metadata_bytes: 4 * 1024 * 1024,
            max_labels: 1024,
            max_label_bytes: 4096,
            max_causes_per_step: 32,
            max_total_causes: 100_000,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TraceV2 {
    pub trace: Trace,
    pub checkpoint_after: u64,
    pub provenance: Provenance,
    pub steps: Vec<StepEvidence>,
}

macro_rules! codec {
    ($name:ident {$($field:ident),+}) => {
        impl TraceEncode for $name {
            fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
                $(self.$field.trace_encode(out)?;)+
                Ok(())
            }
        }
        impl TraceDecode for $name {
            fn trace_decode(input: &mut Decoder<'_>) -> Result<Self, ModelError> {
                Ok(Self {$($field: input.value()?,)+})
            }
        }
    }
}
codec!(Provenance {
    run,
    epoch,
    manifest,
    source,
    environment
});
codec!(OutputOrigin { sequence, output });
codec!(CausalContext {
    machine,
    generation,
    correlation,
    logical_time,
    causes
});
codec!(StepEvidence {
    sequence,
    state_check_count,
    causal
});

fn invalid(message: impl Into<String>) -> ModelError {
    ModelError::new(message)
}
fn label(value: &str, limits: &EvidenceLimits) -> Result<(), ModelError> {
    if value.len() > limits.max_label_bytes {
        return Err(invalid("evidence label byte limit"));
    }
    Ok(())
}
fn provenance(value: &Provenance, limits: &EvidenceLimits) -> Result<(), ModelError> {
    label(&value.run, limits)?;
    for value in value.manifest.iter().chain(value.source.iter()) {
        label(value, limits)?;
    }
    if value.environment.len() > limits.max_labels {
        return Err(invalid("evidence environment label limit"));
    }
    let mut names = std::collections::BTreeSet::new();
    for (name, value) in &value.environment {
        label(name, limits)?;
        label(value, limits)?;
        if name.is_empty() || !names.insert(name) {
            return Err(invalid("empty or duplicate evidence environment name"));
        }
    }
    Ok(())
}
fn context(
    value: &CausalContext,
    sequence: u64,
    limits: &EvidenceLimits,
) -> Result<(), ModelError> {
    for value in value.machine.iter().chain(value.correlation.iter()) {
        label(value, limits)?;
    }
    if value.causes.len() > limits.max_causes_per_step {
        return Err(invalid("causal links per step limit"));
    }
    let mut seen = std::collections::BTreeSet::new();
    for origin in &value.causes {
        if origin.sequence == 0
            || origin.sequence >= sequence
            || !seen.insert((origin.sequence, origin.output))
        {
            return Err(invalid(
                "causal origin must be unique and precede its input",
            ));
        }
    }
    Ok(())
}

impl TraceV2 {
    /// Verify semantic bytes and the persisted check phase boundaries. External
    /// effects remain data; host-provided causal labels are not authenticated.
    pub fn replay<M: ModelCodec>(
        &self,
        model: &M,
        options: ReplayOptions,
        limits: &EvidenceLimits,
    ) -> Result<crate::execution::ReplayReport, ModelError> {
        self.validate(limits)?;
        let mut phase_mismatch = None;
        let result = replay_with_observations_bounded(
            model,
            &self.trace,
            options,
            &limits.trace,
            |observation, report| {
                if let ReplayObservation::Turn(turn) = observation {
                    let index = turn.actual.sequence as usize - 1;
                    if report.outcome == ReplayOutcome::Exact
                        && self.steps[index].state_check_count as usize
                            != turn.actual.state_check_count
                    {
                        phase_mismatch = Some(index + 1);
                        return Err(invalid("persisted check phase differs from replay"));
                    }
                }
                Ok(())
            },
        );
        match (result, phase_mismatch) {
            (Ok(report), _) => Ok(report),
            (Err(mut error), Some(sequence)) => {
                error.report.outcome = ReplayOutcome::Diverged {
                    step: Some(sequence),
                    field: "check_phase",
                };
                error.report.steps_verified = sequence - 1;
                error.report.failure_reproduced = false;
                Ok(error.report)
            }
            (Err(error), None) => Err(error.error),
        }
    }

    pub fn validate(&self, limits: &EvidenceLimits) -> Result<(), ModelError> {
        provenance(&self.provenance, limits)?;
        crate::execution::validate_recording(&self.trace)?;
        let mut measured = CountingBytes(0);
        self.trace
            .write_with_limits(&mut measured, &limits.trace)
            .map_err(|e| invalid(e.to_string()))?;
        let metadata = self.metadata_bytes(limits.max_metadata_bytes)?;
        if measured
            .0
            .checked_add(metadata.len() as u64)
            .and_then(|n| n.checked_add(OVERHEAD))
            .is_none_or(|n| n > limits.trace.max_total_bytes)
        {
            return Err(invalid("evidence total byte limit"));
        }
        if self.steps.len() != self.trace.steps.len() {
            return Err(invalid("phase evidence does not cover every recorded step"));
        }
        self.checkpoint_after
            .checked_add(self.steps.len() as u64)
            .ok_or_else(|| invalid("evidence sequence overflow"))?;
        let mut causes = 0usize;
        for (index, (evidence, step)) in self.steps.iter().zip(&self.trace.steps).enumerate() {
            if evidence.sequence != self.checkpoint_after + index as u64 + 1
                || evidence.state_check_count as usize > step.checks.len()
            {
                return Err(invalid("invalid phase evidence sequence or check boundary"));
            }
            context(&evidence.causal, evidence.sequence, limits)?;
            causes = causes
                .checked_add(evidence.causal.causes.len())
                .ok_or_else(|| invalid("causal count overflow"))?;
            if causes > limits.max_total_causes {
                return Err(invalid("total causal links limit"));
            }
            for origin in &evidence.causal.causes {
                if origin.sequence > self.checkpoint_after {
                    let source =
                        &self.trace.steps[(origin.sequence - self.checkpoint_after - 1) as usize];
                    if origin.output as usize >= source.outputs.len() {
                        return Err(invalid(
                            "causal origin output index outside retained evidence",
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    /// Local sequence zero denotes the stored checkpoint, including ring capture.
    pub fn check_phase(&self, sequence: usize, check: usize) -> Option<CheckPhase> {
        if sequence == 0 {
            return self
                .trace
                .initial_checks
                .get(check)
                .map(|_| CheckPhase::InitialState);
        }
        let step = self.trace.steps.get(sequence - 1)?;
        step.checks.get(check)?;
        let evidence = self.steps.get(sequence - 1)?;
        Some(if check < evidence.state_check_count as usize {
            CheckPhase::State
        } else {
            CheckPhase::Transition
        })
    }

    fn metadata_bytes(&self, maximum: usize) -> Result<Vec<u8>, ModelError> {
        let mut bytes = Vec::new();
        let mut out = EncodeBuffer::new(&mut bytes, maximum);
        self.checkpoint_after.trace_encode(&mut out)?;
        self.provenance.trace_encode(&mut out)?;
        self.steps.trace_encode(&mut out)?;
        out.finish()?;
        Ok(bytes)
    }

    /// Preflights the entire bounded envelope before writing any bytes.
    pub fn write(&self, mut writer: impl Write, limits: &EvidenceLimits) -> Result<(), ModelError> {
        self.validate(limits)?;
        let metadata = self.metadata_bytes(limits.max_metadata_bytes)?;
        let mut exact =
            LimitedBytes::new(usize::try_from(limits.trace.max_total_bytes).unwrap_or(usize::MAX));
        self.trace
            .write_with_limits(&mut exact, &limits.trace)
            .map_err(|e| invalid(e.to_string()))?;
        let total = OVERHEAD
            .checked_add(exact.bytes.len() as u64)
            .and_then(|n| n.checked_add(metadata.len() as u64))
            .ok_or_else(|| invalid("evidence size overflow"))?;
        if total > limits.trace.max_total_bytes {
            return Err(invalid("evidence total byte limit"));
        }
        let mut header = Vec::with_capacity(28);
        header.extend_from_slice(MAGIC);
        header.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        header.extend_from_slice(&[0, 0]);
        header.extend_from_slice(&(exact.bytes.len() as u64).to_le_bytes());
        header.extend_from_slice(&(metadata.len() as u64).to_le_bytes());
        let checksum = crc32_parts([
            header.as_slice(),
            exact.bytes.as_slice(),
            metadata.as_slice(),
        ]);
        for bytes in [
            &header[..],
            &exact.bytes,
            &metadata,
            &checksum.to_le_bytes(),
        ] {
            writer
                .write_all(bytes)
                .map_err(|e| invalid(e.to_string()))?;
        }
        Ok(())
    }

    /// Explicit raw export; never overwrites. Unix files are created mode 0600.
    pub fn write_raw_new(&self, path: &Path, limits: &EvidenceLimits) -> Result<(), ModelError> {
        let mut bytes =
            LimitedBytes::new(usize::try_from(limits.trace.max_total_bytes).unwrap_or(usize::MAX));
        self.write(&mut bytes, limits)?;
        private_new(path, &bytes.bytes)
    }

    /// Explicit replay-based v1 upgrade. Every recorded observation must match;
    /// phase boundaries come from actual complete replay checks, never guesses.
    /// This is a verification operation, not a passive runtime observer.
    pub fn verify_upgrade<M: ModelCodec>(
        model: &M,
        trace: Trace,
        provenance: Provenance,
        checkpoint_after: u64,
        options: ReplayOptions,
        limits: &EvidenceLimits,
    ) -> Result<Self, ModelError> {
        let mut steps = Vec::new();
        let report = replay_with_observations_bounded(
            model,
            &trace,
            options,
            &limits.trace,
            |observation, _| {
                if let ReplayObservation::Turn(turn) = observation {
                    steps
                        .try_reserve(1)
                        .map_err(|_| invalid("phase evidence allocation"))?;
                    steps.push(StepEvidence {
                        sequence: checkpoint_after
                            .checked_add(turn.actual.sequence)
                            .ok_or_else(|| invalid("evidence sequence overflow"))?,
                        state_check_count: u32::try_from(turn.actual.state_check_count)
                            .map_err(|_| invalid("phase check count overflow"))?,
                        causal: CausalContext::default(),
                    });
                }
                Ok(())
            },
        )
        .map_err(|e| e.error)?;
        if report.outcome != ReplayOutcome::Exact {
            return Err(invalid("v1 upgrade requires exact compatible replay"));
        }
        let evidence = Self {
            trace,
            checkpoint_after,
            provenance,
            steps,
        };
        evidence.validate(limits)?;
        Ok(evidence)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Artifact {
    V1(Trace),
    V2(TraceV2),
}
impl Artifact {
    pub fn trace(&self) -> &Trace {
        match self {
            Self::V1(trace) => trace,
            Self::V2(trace) => &trace.trace,
        }
    }
    pub fn check_phase(&self, sequence: usize, check: usize) -> Option<CheckPhase> {
        match self {
            Self::V1(trace) if sequence == 0 => trace
                .initial_checks
                .get(check)
                .map(|_| CheckPhase::InitialState),
            Self::V1(_) => None,
            Self::V2(trace) => trace.check_phase(sequence, check),
        }
    }
    /// Bounded exact reader; takes ownership of no host resources and runs no model.
    pub fn read(mut reader: impl Read, limits: &EvidenceLimits) -> Result<Self, ModelError> {
        let maximum = usize::try_from(limits.trace.max_total_bytes).unwrap_or(usize::MAX);
        let mut bytes = LimitedBytes::new(maximum);
        let mut chunk = [0u8; 8192];
        loop {
            let count = reader
                .read(&mut chunk)
                .map_err(|e| invalid(e.to_string()))?;
            if count == 0 {
                break;
            }
            bytes
                .write_all(&chunk[..count])
                .map_err(|e| invalid(e.to_string()))?;
        }
        let data = bytes.bytes;
        if data.len() < 12 || &data[..8] != MAGIC {
            return Err(invalid("truncated or invalid evidence header"));
        }
        let version = u16::from_le_bytes([data[8], data[9]]);
        if version == 1 {
            return Trace::read_from(data.as_slice(), &limits.trace)
                .map(Self::V1)
                .map_err(|e| invalid(e.to_string()));
        }
        if version != FORMAT_VERSION {
            return Err(invalid("unsupported evidence version"));
        }
        if data[10..12] != [0, 0] || data.len() < OVERHEAD as usize {
            return Err(invalid("invalid or truncated v2 header"));
        }
        let trace_len = u64::from_le_bytes(data[12..20].try_into().expect("checked header"));
        let meta_len = u64::from_le_bytes(data[20..28].try_into().expect("checked header"));
        let total = trace_len
            .checked_add(meta_len)
            .and_then(|n| n.checked_add(OVERHEAD));
        if total != Some(data.len() as u64) || meta_len > limits.max_metadata_bytes as u64 {
            return Err(invalid("invalid evidence lengths or metadata limit"));
        }
        let end = data.len() - 4;
        let expected = u32::from_le_bytes(data[end..].try_into().expect("checksum bytes"));
        if crc32_parts([&data[..end]]) != expected {
            return Err(invalid("evidence checksum mismatch"));
        }
        let split = 28 + trace_len as usize;
        let trace = Trace::read_from(&data[28..split], &limits.trace)
            .map_err(|e| invalid(e.to_string()))?;
        let decode = DecodeLimits {
            bytes: limits.max_metadata_bytes,
            // This fixed metadata schema has no zero-byte collection elements.
            // Allow its in-memory Option/String/vector overhead independently of
            // the embedded v1 item quota; encoded bytes still bound all counts.
            allocation: limits.max_metadata_bytes.saturating_mul(16),
            elements: limits.max_metadata_bytes,
            depth: 16,
            work: limits.max_metadata_bytes.saturating_mul(16),
        };
        let mut metadata = Decoder::new(&data[split..end], decode)?;
        let value = TraceV2 {
            trace,
            checkpoint_after: metadata.value()?,
            provenance: metadata.value()?,
            steps: metadata.value()?,
        };
        metadata.finish()?;
        value.validate(limits)?;
        Ok(Self::V2(value))
    }
}

/// Captures phases from sealed checks while delegating exact bytes/ring eviction
/// to the established recorder. No reducer or checker is called by this adapter.
pub struct EvidenceRecorder {
    recorder: Recorder,
    provenance: Provenance,
    origin: u64,
    steps: VecDeque<(StepEvidence, usize)>,
    metadata_bytes: usize,
    limits: EvidenceLimits,
}
impl EvidenceRecorder {
    /// Reserve the v2 framing and the entire selected metadata allowance inside
    /// the smaller of the retention and format byte budgets. Select a smaller
    /// metadata allowance when recording with a small complete-envelope budget.
    pub fn new<M: ModelCodec>(
        initial: &CheckedInitial<'_, M>,
        origin: u64,
        config: RunConfig,
        mut options: RecorderOptions,
        provenance: Provenance,
        limits: EvidenceLimits,
    ) -> Result<Self, ModelError> {
        let overhead = OVERHEAD
            .checked_add(limits.max_metadata_bytes as u64)
            .ok_or_else(|| invalid("evidence reservation overflow"))?;
        let available = options
            .max_retained_bytes
            .min(limits.trace.max_total_bytes)
            .checked_sub(overhead)
            .ok_or_else(|| invalid("evidence metadata reservation exceeds total limit"))?;
        options.max_retained_bytes = available;
        options.limits = limits.trace.clone();
        let base = provenance
            .trace_bytes(limits.max_metadata_bytes)?
            .len()
            .checked_add(12)
            .ok_or_else(|| invalid("metadata size overflow"))?;
        if base > limits.max_metadata_bytes {
            return Err(invalid("metadata budget exceeded"));
        }
        self::provenance(&provenance, &limits)?;
        let recorder = Recorder::with_checked_initial_at(initial, origin, config, options)?;
        Ok(Self {
            recorder,
            provenance,
            origin,
            steps: VecDeque::new(),
            metadata_bytes: base,
            limits,
        })
    }

    pub fn observe<M: ModelCodec>(
        &mut self,
        turn: &CheckedTurn<'_, M>,
        causal: CausalContext,
    ) -> Result<(), ModelError> {
        if self.recorder.is_frozen() {
            return Err(invalid(
                "runtime evidence recorder is frozen; export its retained trace",
            ));
        }
        let prepare = (|| {
            context(&causal, turn.sequence(), &self.limits)?;
            let step = StepEvidence {
                sequence: turn.sequence(),
                state_check_count: u32::try_from(turn.state_check_count())
                    .map_err(|_| invalid("phase boundary overflow"))?,
                causal,
            };
            let size = step.trace_bytes(self.limits.max_metadata_bytes)?.len();
            let mut bytes = self
                .metadata_bytes
                .checked_add(size)
                .ok_or_else(|| invalid("phase metadata size overflow"))?;
            let mut links = self
                .steps
                .iter()
                .map(|(s, _)| s.causal.causes.len())
                .try_fold(step.causal.causes.len(), usize::checked_add)
                .ok_or_else(|| invalid("phase causal count overflow"))?;
            let mut evictions = 0;
            for (old, old_size) in &self.steps {
                if bytes <= self.limits.max_metadata_bytes && links <= self.limits.max_total_causes
                {
                    break;
                }
                bytes -= old_size;
                links -= old.causal.causes.len();
                evictions += 1;
            }
            if bytes > self.limits.max_metadata_bytes || links > self.limits.max_total_causes {
                return Err(invalid("candidate phase metadata exceeds retention limits"));
            }
            // Resolve every retained causal origin before accepting its reference.
            let checkpoint = self
                .origin
                .checked_add(self.recorder.evicted_steps())
                .ok_or_else(|| invalid("checkpoint overflow"))?;
            for origin in &step.causal.causes {
                if origin.sequence > checkpoint
                    && self
                        .recorder
                        .retained_output_count(origin.sequence)
                        .is_none_or(|count| origin.output as usize >= count)
                {
                    return Err(invalid("causal origin outside retained output range"));
                }
            }
            self.steps
                .try_reserve(1)
                .map_err(|_| invalid("phase journal allocation"))?;
            Ok((step, size, evictions))
        })();
        let (step, size, evictions) = match prepare {
            Ok(value) => value,
            Err(error) => return Err(self.recorder.stop_with_error(error)),
        };
        let previous = self.recorder.evicted_steps();
        self.recorder.observe_checked_retaining(turn, evictions)?;
        for _ in previous..self.recorder.evicted_steps() {
            let (_, bytes) = self
                .steps
                .pop_front()
                .expect("phase journal tracks retained steps");
            self.metadata_bytes -= bytes;
        }
        self.metadata_bytes += size;
        self.steps.push_back((step, size));
        Ok(())
    }
    pub fn is_frozen(&self) -> bool {
        self.recorder.is_frozen()
    }
    pub fn snapshot(&self) -> TraceV2 {
        TraceV2 {
            trace: self.recorder.snapshot(),
            checkpoint_after: self.origin + self.recorder.evicted_steps(),
            provenance: self.provenance.clone(),
            steps: self.steps.iter().map(|(s, _)| s.clone()).collect(),
        }
    }
    pub fn into_trace(self) -> TraceV2 {
        let checkpoint_after = self.origin + self.recorder.evicted_steps();
        TraceV2 {
            trace: self.recorder.into_trace(),
            checkpoint_after,
            provenance: self.provenance,
            steps: self.steps.into_iter().map(|(s, _)| s).collect(),
        }
    }
}

struct LimitedBytes {
    bytes: Vec<u8>,
    maximum: usize,
}
struct CountingBytes(u64);
impl Write for CountingBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0 = self
            .0
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| std::io::Error::other("evidence size overflow"))?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl LimitedBytes {
    fn new(maximum: usize) -> Self {
        Self {
            bytes: Vec::new(),
            maximum,
        }
    }
}
impl Write for LimitedBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if self
            .bytes
            .len()
            .checked_add(bytes.len())
            .is_none_or(|n| n > self.maximum)
        {
            return Err(std::io::Error::other("evidence byte limit"));
        }
        self.bytes
            .try_reserve(bytes.len())
            .map_err(|_| std::io::Error::other("evidence allocation failed"))?;
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn crc32_parts<'a>(parts: impl IntoIterator<Item = &'a [u8]>) -> u32 {
    let mut crc = u32::MAX;
    for part in parts {
        for byte in part {
            crc ^= u32::from(*byte);
            for _ in 0..8 {
                crc = (crc >> 1) ^ (0xedb88320 & (0u32.wrapping_sub(crc & 1)));
            }
        }
    }
    !crc
}
fn private_new(path: &Path, bytes: &[u8]) -> Result<(), ModelError> {
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(|e| invalid(e.to_string()))?;
    file.write_all(bytes)
        .and_then(|_| file.sync_all())
        .map_err(|e| invalid(format!("incomplete newly created evidence file: {e}")))
}

/// Diagnostic-only export. Payloads, details, model/build identifiers and causal
/// labels are omitted. Only explicitly allowlisted property IDs can be included.
pub struct ExportPolicy<'a> {
    pub max_steps: usize,
    pub max_bytes: usize,
    pub property_ids: &'a [&'a str],
}
impl Default for ExportPolicy<'_> {
    fn default() -> Self {
        Self {
            max_steps: 1000,
            max_bytes: 1024 * 1024,
            property_ids: &[],
        }
    }
}
fn json_string(out: &mut impl Write, value: &str) -> std::io::Result<()> {
    write!(out, "\"")?;
    for ch in value.chars() {
        match ch {
            '\"' => write!(out, "\\\"")?,
            '\\' => write!(out, "\\\\")?,
            ch if ch <= '\u{1f}' => write!(out, "\\u{:04x}", ch as u32)?,
            ch => write!(out, "{ch}")?,
        }
    }
    write!(out, "\"")
}
pub fn diagnostic_json(
    artifact: &Artifact,
    policy: &ExportPolicy<'_>,
) -> Result<Vec<u8>, ModelError> {
    let trace = artifact.trace();
    let (checkpoint, omitted_prefix) = match artifact {
        Artifact::V1(_) => ("null".to_string(), "null"),
        Artifact::V2(value) => (
            value.checkpoint_after.to_string(),
            if value.checkpoint_after == 0 {
                "false"
            } else {
                "true"
            },
        ),
    };
    let mut out = LimitedBytes::new(policy.max_bytes);
    let encode = (|| -> std::io::Result<()> {
        let termination = match trace.termination {
            Termination::Completed => "completed_sequence",
            Termination::PropertyFailed => "property_failure",
            Termination::StepLimit => "step_limit",
            Termination::Interrupted => "interrupted",
            Termination::ModelError(_) => "model_error",
        };
        write!(
            out,
            "{{\"schema\":\"statelessness.diagnostic-export/1\",\"replayable\":false,\"redaction_policy\":\"omit-payloads-v1\",\"raw_payloads_included\":false,\"checkpoint_after\":{checkpoint},\"omitted_prefix\":{omitted_prefix},\"termination\":\"{termination}\",\"truncated\":{},\"observations\":[",
            trace.steps.len() > policy.max_steps
        )?;
        for sequence in 0..=trace.steps.len().min(policy.max_steps) {
            if sequence != 0 {
                write!(out, ",")?;
            }
            let checks = if sequence == 0 {
                &trace.initial_checks
            } else {
                &trace.steps[sequence - 1].checks
            };
            write!(out, "{{\"sequence\":{sequence},\"checks\":[")?;
            for (index, check) in checks.iter().enumerate() {
                if index != 0 {
                    write!(out, ",")?;
                }
                let status = match check.status {
                    CheckStatus::Passed => "passed",
                    CheckStatus::Failed(_) => "failed",
                    CheckStatus::Skipped(_) => "skipped",
                };
                let phase = match artifact.check_phase(sequence, index) {
                    Some(CheckPhase::InitialState) => "initial_state",
                    Some(CheckPhase::State) => "state",
                    Some(CheckPhase::Transition) => "transition",
                    None => "unknown_legacy",
                };
                write!(
                    out,
                    "{{\"status\":\"{status}\",\"phase\":\"{phase}\",\"id\":"
                )?;
                if policy.property_ids.contains(&check.id.as_str()) {
                    json_string(&mut out, check.id.as_str())?;
                } else {
                    write!(out, "null")?;
                }
                write!(out, "}}")?;
            }
            write!(out, "]}}")?;
        }
        write!(out, "]}}")
    })();
    encode.map_err(|e| invalid(e.to_string()))?;
    Ok(out.bytes)
}
pub fn export_diagnostic_new(
    artifact: &Artifact,
    path: &Path,
    policy: &ExportPolicy<'_>,
) -> Result<(), ModelError> {
    private_new(path, &diagnostic_json(artifact, policy)?)
}
