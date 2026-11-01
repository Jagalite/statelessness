//! continued-failure-evidence-v1 (S5); see spec/continued-failure-evidence-v1.md.
//!
//! A continued trace may record steps after a failed check. It is a distinct
//! versioned format: [`crate::execution::validate_recording`] and
//! [`crate::execution::replay`] reject it ("trace continues after property
//! failure") rather than silently reinterpreting a later failure as the end of
//! a stop-at-failure trace. `record`, `replay` and the binary trace format are
//! unchanged.
//!
//! The portable form is the trace-json v1 object with `format` =
//! `stateless.continued-trace-json`, `version` = 1 and a `failing_sequences`
//! array. Like trace-json v1 it carries no [`RunConfig`]: a parsed trace has
//! `RunConfig::default()`. Rust has no trace-json v1 reader; its stop-at-failure
//! artifacts use the binary format, whose reader rejects these JSON bytes.

mod json;

use std::collections::BTreeMap;

use crate::execution::{
    InfallibleInputs, ReplayObservation, ReplayOptions, ReplayOutcome, ReplayReport,
    record_continued_ops, replay_observations_validated,
};
use crate::model::{Check, CheckStatus, Disposition, ModelCodec, ModelError, ModelMetadata};
use crate::operations::NativeOps;
use crate::trace::{ReadLimits, RunConfig, Termination, Trace, TraceError, TraceStep};
use json::Json;

pub use crate::explore::{
    ContinuedFinding, ContinuedShrinkReport, shrink_continued, shrink_continued_with_limits,
};

pub const CONTINUED_TRACE_FORMAT: &str = "stateless.continued-trace-json";
pub const CONTINUED_TRACE_VERSION: u64 = 1;

/// Termination is completed, step_limit, interrupted or model_error;
/// property_failed is never used because failures do not end recording.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContinuedTrace {
    pub trace: Trace,
}

impl ContinuedTrace {
    /// 0 for a failing initial state; otherwise the 1-based steps whose checks failed.
    pub fn failing_sequences(&self) -> Vec<u64> {
        let initial = self
            .trace
            .initial_checks
            .iter()
            .any(Check::is_failure)
            .then_some(0);
        initial
            .into_iter()
            .chain(
                self.trace
                    .steps
                    .iter()
                    .enumerate()
                    .filter(|(_, step)| step.checks.iter().any(Check::is_failure))
                    .map(|(index, _)| index as u64 + 1),
            )
            .collect()
    }
}

fn validate_continued_trace(trace: &Trace) -> Result<(), ModelError> {
    if trace.termination == Termination::PropertyFailed {
        return Err(ModelError::new(
            "continued traces do not terminate on property failure",
        ));
    }
    Ok(())
}

/// Error text exists only with model_error termination, enforced by
/// [`Termination::ModelError`] itself.
pub fn validate_continued_recording(trace: &ContinuedTrace) -> Result<(), ModelError> {
    validate_continued_trace(&trace.trace)
}

/// Records every input until the inputs end (`completed`), the step limit
/// (`step_limit`), or a model, checker or codec error (`model_error`, with
/// every completed step retained), continuing past failed checks. Uses the
/// same kernel and default format limits as [`crate::execution::record`].
pub fn record_continued<M: ModelCodec>(
    model: &M,
    inputs: impl IntoIterator<Item = M::Input>,
    config: RunConfig,
    max_steps: usize,
) -> Result<ContinuedTrace, ModelError> {
    record_continued_with_limits(model, inputs, config, max_steps, &ReadLimits::default())
}

pub fn record_continued_with_limits<M: ModelCodec>(
    model: &M,
    inputs: impl IntoIterator<Item = M::Input>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
) -> Result<ContinuedTrace, ModelError> {
    let trace = record_continued_ops(
        &NativeOps::new(model),
        InfallibleInputs(inputs),
        config,
        max_steps,
        limits,
    )?;
    Ok(ContinuedTrace { trace })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContinuedReplayReport {
    pub replay: ReplayReport,
    pub recorded_failures: Vec<u64>,
    /// Recorded failing sequences whose replayed checks failed every recorded
    /// failed check ID.
    pub reproduced_failures: Vec<u64>,
    /// Exact outcome, every recorded step verified (dispositions, ordered
    /// outputs, post-states and complete check batches), and every recorded
    /// failure reproduced. A verified prefix is never complete reproduction.
    pub complete_reproduction: bool,
}

fn reproduces(expected: &[Check], actual: &[Check]) -> bool {
    let mut failed = expected.iter().filter(|c| c.is_failure()).peekable();
    failed.peek().is_some() && failed.all(|e| actual.iter().any(|a| a.is_failure() && a.id == e.id))
}

/// Replays with the standard replay kernel and continued validation, stopping
/// at the first divergence.
pub fn replay_continued<M: ModelCodec>(
    model: &M,
    trace: &ContinuedTrace,
    options: ReplayOptions,
) -> Result<ContinuedReplayReport, ModelError> {
    let mut reproduced = Vec::new();
    let replay = replay_observations_validated(
        model,
        &trace.trace,
        options,
        validate_continued_trace,
        |observation, _| {
            match observation {
                ReplayObservation::Initial(o) => {
                    if reproduces(o.expected_checks, o.checks) {
                        reproduced.push(0);
                    }
                }
                ReplayObservation::Turn(o) => {
                    if reproduces(&o.expected.checks, o.actual.checks) {
                        reproduced.push(o.actual.sequence);
                    }
                }
                ReplayObservation::Error { .. } => {}
            }
            Ok(())
        },
    )
    .map_err(|error| error.error)?;
    let recorded = trace.failing_sequences();
    let complete_reproduction = replay.outcome == ReplayOutcome::Exact
        && replay.steps_verified == trace.trace.steps.len()
        && reproduced == recorded;
    Ok(ContinuedReplayReport {
        replay,
        recorded_failures: recorded,
        reproduced_failures: reproduced,
        complete_reproduction,
    })
}

// MARK: stateless.continued-trace-json v1

fn invalid(message: &'static str) -> TraceError {
    TraceError::InvalidData(message)
}

fn hex(bytes: &[u8]) -> Json {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut text = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        text.push(char::from(DIGITS[usize::from(b >> 4)]));
        text.push(char::from(DIGITS[usize::from(b & 15)]));
    }
    Json::String(text)
}

fn number(n: u64) -> Json {
    Json::Number(n.to_string())
}

fn check_json(check: &Check) -> Json {
    let (status, details) = match &check.status {
        CheckStatus::Passed => ("passed", ""),
        CheckStatus::Failed(details) => ("failed", details.as_str()),
        CheckStatus::Skipped(details) => ("skipped", details.as_str()),
    };
    Json::Object(BTreeMap::from([
        ("id".into(), Json::String(check.id.as_str().into())),
        ("status".into(), Json::String(status.into())),
        ("details".into(), Json::String(details.into())),
    ]))
}

fn disposition_json(disposition: &Disposition) -> Json {
    let (kind, reason) = match disposition {
        Disposition::Accepted => ("accepted", ""),
        Disposition::Rejected(reason) => ("rejected", reason.as_str()),
        Disposition::Ignored(reason) => ("ignored", reason.as_str()),
    };
    Json::Object(BTreeMap::from([
        ("kind".into(), Json::String(kind.into())),
        ("reason".into(), Json::String(reason.into())),
    ]))
}

fn termination_json(termination: &Termination) -> (&'static str, &str) {
    match termination {
        Termination::Completed => ("completed", ""),
        Termination::PropertyFailed => ("property_failed", ""),
        Termination::StepLimit => ("step_limit", ""),
        Termination::Interrupted => ("interrupted", ""),
        Termination::ModelError(error) => ("model_error", error.as_str()),
    }
}

fn limit(ok: bool, name: &'static str) -> Result<(), TraceError> {
    if ok {
        Ok(())
    } else {
        Err(TraceError::LimitExceeded(name))
    }
}

/// Retained-collection limits shared by the reader and writer. These are
/// [`ReadLimits`]: `max_total_bytes` bounds the JSON bytes and the aggregate
/// blob payload.
struct Budget<'a> {
    limits: &'a ReadLimits,
    items: usize,
    payload: u64,
}

impl Budget<'_> {
    fn items(&mut self, n: usize) -> Result<(), TraceError> {
        self.items = self
            .items
            .checked_add(n)
            .filter(|&total| total <= self.limits.max_items)
            .ok_or(TraceError::LimitExceeded("aggregate items"))?;
        Ok(())
    }
    fn blob(&mut self, n: usize) -> Result<(), TraceError> {
        limit(n <= self.limits.max_blob_bytes, "blob bytes")?;
        self.payload = self
            .payload
            .checked_add(n as u64)
            .filter(|&total| total <= self.limits.max_total_bytes)
            .ok_or(TraceError::LimitExceeded("aggregate payload bytes"))?;
        Ok(())
    }
    fn text(&self, text: &str) -> Result<(), TraceError> {
        limit(text.len() <= self.limits.max_string_bytes, "string bytes")
    }
    fn checks(&mut self, checks: &[Check]) -> Result<(), TraceError> {
        limit(
            checks.len() <= self.limits.max_checks_per_step,
            "checks per step",
        )?;
        self.items(checks.len())?;
        for check in checks {
            self.text(check.id.as_str())?;
            if let CheckStatus::Failed(text) | CheckStatus::Skipped(text) = &check.status {
                self.text(text)?;
            }
        }
        Ok(())
    }
}

/// Encodes a continued trace. Bytes are byte-identical to the Swift reference
/// writer for the same trace (sorted keys, no whitespace, lowercase hex).
pub fn encode_continued_trace(
    trace: &ContinuedTrace,
    limits: &ReadLimits,
) -> Result<Vec<u8>, TraceError> {
    validate_continued_recording(trace)
        .map_err(|_| invalid("continued traces do not terminate on property failure"))?;
    let t = &trace.trace;
    let mut budget = Budget {
        limits,
        items: 0,
        payload: 0,
    };
    limit(t.steps.len() <= limits.max_steps, "steps")?;
    budget.text(&t.metadata.name)?;
    budget.text(&t.metadata.build)?;
    let (termination, error) = termination_json(&t.termination);
    budget.text(error)?;
    budget.blob(t.initial_state.len())?;
    budget.checks(&t.initial_checks)?;
    let mut steps = Vec::with_capacity(t.steps.len());
    for step in &t.steps {
        limit(
            step.outputs.len() <= limits.max_outputs_per_step,
            "outputs per step",
        )?;
        budget.items(1 + step.outputs.len())?;
        budget.blob(step.input.len())?;
        if let Disposition::Rejected(reason) | Disposition::Ignored(reason) = &step.disposition {
            budget.text(reason)?;
        }
        for output in &step.outputs {
            budget.blob(output.len())?;
        }
        budget.blob(step.post_state.len())?;
        budget.checks(&step.checks)?;
        steps.push(Json::Object(BTreeMap::from([
            ("input".into(), hex(&step.input)),
            ("disposition".into(), disposition_json(&step.disposition)),
            (
                "outputs".into(),
                Json::Array(step.outputs.iter().map(|o| hex(o)).collect()),
            ),
            ("post_state".into(), hex(&step.post_state)),
            (
                "checks".into(),
                Json::Array(step.checks.iter().map(check_json).collect()),
            ),
        ])));
    }
    let m = &t.metadata;
    let metadata = Json::Object(BTreeMap::from([
        ("name".into(), Json::String(m.name.clone())),
        ("model_version".into(), number(u64::from(m.model_version))),
        (
            "properties_version".into(),
            number(u64::from(m.properties_version)),
        ),
        ("codec_version".into(), number(u64::from(m.codec_version))),
        ("build".into(), Json::String(m.build.clone())),
    ]));
    let value = Json::Object(BTreeMap::from([
        ("format".into(), Json::String(CONTINUED_TRACE_FORMAT.into())),
        ("version".into(), number(CONTINUED_TRACE_VERSION)),
        ("metadata".into(), metadata),
        ("initial_state".into(), hex(&t.initial_state)),
        (
            "initial_checks".into(),
            Json::Array(t.initial_checks.iter().map(check_json).collect()),
        ),
        ("steps".into(), Json::Array(steps)),
        ("termination".into(), Json::String(termination.into())),
        ("error".into(), Json::String(error.into())),
        (
            "failing_sequences".into(),
            Json::Array(trace.failing_sequences().into_iter().map(number).collect()),
        ),
    ]));
    let bytes = json::render(&value, limits.max_total_bytes)?;
    // The writer never publishes bytes its own reader rejects.
    parse_continued_trace(&bytes, limits)?;
    Ok(bytes)
}

const MAX_JSON_INTEGER: u64 = (1 << 53) - 1;

fn blob(value: Option<Json>, budget: &mut Budget<'_>) -> Result<Vec<u8>, TraceError> {
    let text = json::string(value)?;
    let digits = text.as_bytes();
    if digits.len() % 2 != 0 {
        return Err(invalid("invalid hexadecimal bytes"));
    }
    budget.blob(digits.len() / 2)?;
    let digit = |c: u8| match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        _ => Err(invalid("invalid lowercase hex")),
    };
    let (pairs, _) = digits.as_chunks::<2>();
    pairs
        .iter()
        .map(|[high, low]| Ok(digit(*high)? * 16 + digit(*low)?))
        .collect()
}

fn check_from_json(value: Json, budget: &Budget<'_>) -> Result<Check, TraceError> {
    let mut o = json::object(value, &["id", "status"], &["details"])?;
    let id = json::string(o.remove("id"))?;
    let status = json::string(o.remove("status"))?;
    let details = match o.remove("details") {
        Some(details) => json::string(Some(details))?,
        None => String::new(),
    };
    budget.text(&id)?;
    budget.text(&details)?;
    match status.as_str() {
        "passed" if details.is_empty() => Ok(Check::passed(id)),
        "passed" => Err(invalid("passing check has details")),
        "failed" => Ok(Check::failed(id, details)),
        "skipped" => Ok(Check::skipped(id, details)),
        _ => Err(invalid("invalid check status")),
    }
}

fn checks_from_json(
    value: Option<Json>,
    budget: &mut Budget<'_>,
) -> Result<Vec<Check>, TraceError> {
    let values = json::array(value, budget.limits.max_checks_per_step)?;
    budget.items(values.len())?;
    values
        .into_iter()
        .map(|value| check_from_json(value, budget))
        .collect()
}

fn disposition_from_json(
    value: Option<Json>,
    budget: &Budget<'_>,
) -> Result<Disposition, TraceError> {
    let mut o = json::object(
        value.ok_or(invalid("expected object"))?,
        &["kind"],
        &["reason"],
    )?;
    let kind = json::string(o.remove("kind"))?;
    let reason = match o.remove("reason") {
        Some(reason) => json::string(Some(reason))?,
        None => String::new(),
    };
    budget.text(&reason)?;
    match kind.as_str() {
        "accepted" if reason.is_empty() => Ok(Disposition::Accepted),
        "rejected" => Ok(Disposition::Rejected(reason)),
        "ignored" => Ok(Disposition::Ignored(reason)),
        _ => Err(invalid("invalid disposition")),
    }
}

fn metadata_from_json(
    value: Option<Json>,
    budget: &Budget<'_>,
) -> Result<ModelMetadata, TraceError> {
    let mut o = json::object(
        value.ok_or(invalid("expected object"))?,
        &[
            "name",
            "model_version",
            "properties_version",
            "codec_version",
            "build",
        ],
        &[],
    )?;
    let version = |o: &BTreeMap<String, Json>, key: &str| {
        json::integer(o.get(key), u64::from(u32::MAX)).map(|n| n as u32)
    };
    let metadata = ModelMetadata {
        model_version: version(&o, "model_version")?,
        properties_version: version(&o, "properties_version")?,
        codec_version: version(&o, "codec_version")?,
        name: json::string(o.remove("name"))?,
        build: json::string(o.remove("build"))?,
    };
    budget.text(&metadata.name)?;
    budget.text(&metadata.build)?;
    Ok(metadata)
}

/// Parses a continued trace. Rejects trace-json v1, other versions, unknown or
/// missing fields, truncated bytes, property_failed termination, and a
/// `failing_sequences` array that disagrees with the recorded checks.
pub fn parse_continued_trace(
    bytes: &[u8],
    limits: &ReadLimits,
) -> Result<ContinuedTrace, TraceError> {
    let Json::Object(mut fields) = json::parse(bytes, limits.max_total_bytes)? else {
        return Err(invalid("expected object"));
    };
    let format = json::string(fields.get("format").cloned())?;
    let version = json::integer(fields.get("version"), MAX_JSON_INTEGER)?;
    if format != CONTINUED_TRACE_FORMAT || version != CONTINUED_TRACE_VERSION {
        return Err(invalid("unsupported continued trace format"));
    }
    let declared = json::array(
        fields.remove("failing_sequences"),
        limits.max_steps.saturating_add(1),
    )?
    .iter()
    .map(|n| json::integer(Some(n), MAX_JSON_INTEGER))
    .collect::<Result<Vec<_>, _>>()?;
    let mut o = json::object(
        Json::Object(fields),
        &[
            "format",
            "version",
            "metadata",
            "initial_state",
            "initial_checks",
            "steps",
            "termination",
            "error",
        ],
        &[],
    )?;
    let mut budget = Budget {
        limits,
        items: 0,
        payload: 0,
    };
    let initial_state = blob(o.remove("initial_state"), &mut budget)?;
    let initial_checks = checks_from_json(o.remove("initial_checks"), &mut budget)?;
    let mut steps = Vec::new();
    for value in json::array(o.remove("steps"), limits.max_steps)? {
        let mut s = json::object(
            value,
            &["input", "disposition", "outputs", "post_state", "checks"],
            &[],
        )?;
        let outputs = json::array(s.remove("outputs"), limits.max_outputs_per_step)?;
        budget.items(1 + outputs.len())?;
        let input = blob(s.remove("input"), &mut budget)?;
        let disposition = disposition_from_json(s.remove("disposition"), &budget)?;
        let outputs = outputs
            .into_iter()
            .map(|output| blob(Some(output), &mut budget))
            .collect::<Result<Vec<_>, _>>()?;
        let post_state = blob(s.remove("post_state"), &mut budget)?;
        let checks = checks_from_json(s.remove("checks"), &mut budget)?;
        steps.push(TraceStep {
            input,
            disposition,
            outputs,
            post_state,
            checks,
        });
    }
    let error = json::string(o.remove("error"))?;
    budget.text(&error)?;
    let termination = match json::string(o.remove("termination"))?.as_str() {
        "completed" => Termination::Completed,
        "property_failed" => Termination::PropertyFailed,
        "step_limit" => Termination::StepLimit,
        "interrupted" => Termination::Interrupted,
        "model_error" => Termination::ModelError(error.clone()),
        _ => return Err(invalid("invalid termination")),
    };
    if !error.is_empty() && !matches!(termination, Termination::ModelError(_)) {
        return Err(invalid("error text requires model_error termination"));
    }
    let trace = ContinuedTrace {
        trace: Trace {
            metadata: metadata_from_json(o.remove("metadata"), &budget)?,
            config: RunConfig::default(),
            initial_state,
            initial_checks,
            steps,
            termination,
        },
    };
    validate_continued_recording(&trace)
        .map_err(|_| invalid("continued traces do not terminate on property failure"))?;
    if declared != trace.failing_sequences() {
        return Err(invalid("failing_sequences disagree with recorded checks"));
    }
    Ok(trace)
}
