//! continued-failure-evidence-v1 (S5). Counter: state += input, outputs
//! [input, state]; state check `high` fails at state >= 3; transition check
//! `big_step` fails for input >= 5.
use stateless::continued::*;
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay, validate_recording};
use stateless::explore::{
    CheckPhase, Failure, PropertyFailure, ShrinkConfig, ShrinkOutcome, ShrinkTermination, shrink,
};
use stateless::explore::{SearchConfig, SearchLimits};
use stateless::graph::{FailurePolicy, GraphSearchOptions, explore_graph};
use stateless::model::*;
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep};

fn bytes(v: i64) -> Vec<u8> {
    v.to_le_bytes().to_vec()
}
fn int(b: &[u8]) -> Result<i64, ModelError> {
    Ok(i64::from_le_bytes(
        b.try_into().map_err(|_| ModelError::new("bad integer"))?,
    ))
}
fn metadata(name: &str, version: u32) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: version,
        properties_version: 1,
        codec_version: 1,
        build: "test".into(),
    }
}

struct Counter {
    version: u32,
    throw_at: Option<i64>,
}
fn counter() -> Counter {
    Counter {
        version: 1,
        throw_at: None,
    }
}
impl Model for Counter {
    type State = i64;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        metadata("counter", self.version)
    }
    fn initial_state(&self) -> Result<i64, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &i64, i: &i64) -> Result<Transition<i64, i64>, ModelError> {
        Ok(Transition::accepted(s + i, vec![*i, s + i]))
    }
    fn check_state(&self, s: &i64) -> Result<Vec<Check>, ModelError> {
        if self.throw_at == Some(*s) {
            return Err(ModelError::new("checker exploded"));
        }
        Ok(vec![if *s >= 3 {
            Check::failed("high", s.to_string())
        } else {
            Check::passed("high")
        }])
    }
    fn check_transition(
        &self,
        _: &i64,
        i: &i64,
        _: &TransitionRef<'_, i64, i64>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if *i >= 5 {
            vec![Check::failed("big_step", i.to_string())]
        } else {
            vec![]
        })
    }
}
impl ModelCodec for Counter {
    fn encode_state(&self, s: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*s))
    }
    fn decode_state(&self, b: &[u8]) -> Result<i64, ModelError> {
        int(b)
    }
    fn encode_input(&self, i: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*i))
    }
    fn decode_input(&self, b: &[u8]) -> Result<i64, ModelError> {
        int(b)
    }
    fn encode_output(&self, o: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*o))
    }
}
impl Generate for Counter {
    fn generate(&self, _: &i64, _: &mut Rng) -> Result<Option<i64>, ModelError> {
        Ok(None)
    }
}

/// 0 unarmed, 1 armed, 2 fired. Input 0 waits, 1 arms (warning), 2 fires and
/// is enabled only when armed.
struct Trigger;
impl Model for Trigger {
    type State = i64;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        metadata("trigger", 1)
    }
    fn initial_state(&self) -> Result<i64, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &i64, i: &i64) -> Result<Transition<i64, i64>, ModelError> {
        let next = match (*s, *i) {
            (0, 1) => 1,
            (1, 2) => 2,
            _ => *s,
        };
        Ok(Transition::accepted(next, vec![]))
    }
    fn check_state(&self, s: &i64) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *s == 2 {
            Check::failed("fired", "boom")
        } else {
            Check::passed("fired")
        }])
    }
    fn check_transition(
        &self,
        s: &i64,
        i: &i64,
        _: &TransitionRef<'_, i64, i64>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if *i == 1 && *s == 0 {
            vec![Check::failed("armed_warning", "armed")]
        } else {
            vec![]
        })
    }
}
impl ModelCodec for Trigger {
    fn encode_state(&self, s: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*s))
    }
    fn decode_state(&self, b: &[u8]) -> Result<i64, ModelError> {
        int(b)
    }
    fn encode_input(&self, i: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*i))
    }
    fn decode_input(&self, b: &[u8]) -> Result<i64, ModelError> {
        int(b)
    }
    fn encode_output(&self, o: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(bytes(*o))
    }
}
impl Enumerate for Trigger {
    fn inputs(&self, s: &i64) -> Result<Vec<i64>, ModelError> {
        Ok(match s {
            0 => vec![0, 1],
            1 => vec![0, 2],
            _ => vec![],
        })
    }
}
impl Generate for Trigger {
    fn generate(&self, _: &i64, _: &mut Rng) -> Result<Option<i64>, ModelError> {
        Ok(None)
    }
    fn is_enabled(&self, s: &i64, i: &i64) -> Result<bool, ModelError> {
        Ok(*i != 2 || *s == 1)
    }
}

const WITNESS: [i64; 7] = [1, 1, 1, 1, 5, 0, 7];

fn record_counter(model: &Counter, inputs: &[i64]) -> ContinuedTrace {
    record_continued(model, inputs.to_vec(), RunConfig::default(), 100_000).unwrap()
}

fn rebuilt(t: &Trace, steps: Vec<TraceStep>) -> ContinuedTrace {
    ContinuedTrace {
        trace: Trace { steps, ..t.clone() },
    }
}

/// The JSON form carries no RunConfig; compare everything else.
fn same_evidence(a: &ContinuedTrace, b: &ContinuedTrace) -> bool {
    a.trace.metadata == b.trace.metadata
        && a.trace.initial_state == b.trace.initial_state
        && a.trace.initial_checks == b.trace.initial_checks
        && a.trace.steps == b.trace.steps
        && a.trace.termination == b.trace.termination
}

fn violation(phase: CheckPhase, id: &'static str, details: &str) -> PropertyFailure {
    PropertyFailure {
        phase,
        check: Check::failed(id, details),
    }
}

#[test]
fn record_continues_past_failures_and_old_readers_reject() {
    let t = record_counter(&counter(), &WITNESS);
    assert_eq!(t.trace.steps.len(), 7);
    assert_eq!(t.trace.termination, Termination::Completed);
    assert_eq!(t.failing_sequences(), [3, 4, 5, 6, 7]);
    validate_continued_recording(&t).unwrap();
    let error = validate_recording(&t.trace).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("continues after property failure")
    );
    let error = replay(&counter(), &t.trace, ReplayOptions::default()).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("continues after property failure")
    );
    // The stop-at-failure recorder ends at the first failure, unchanged.
    let legacy = record(&counter(), WITNESS, RunConfig::default(), 100_000).unwrap();
    assert_eq!(legacy.steps.len(), 3);
    assert_eq!(legacy.termination, Termination::PropertyFailed);
    let encoded = encode_continued_trace(&t, &ReadLimits::default()).unwrap();
    let parsed = parse_continued_trace(&encoded, &ReadLimits::default()).unwrap();
    assert!(same_evidence(&parsed, &t));
    assert_eq!(parsed.trace.config, RunConfig::default());
    // Re-encoding the parsed artifact is byte-identical.
    assert_eq!(
        encode_continued_trace(&parsed, &ReadLimits::default()).unwrap(),
        encoded
    );
    // Rust's stop-at-failure artifact reader rejects the bytes.
    assert!(Trace::read_from(encoded.as_slice(), &ReadLimits::default()).is_err());
    // A trace-json v1 document is not a continued trace.
    let text = String::from_utf8(encoded.clone()).unwrap();
    let v1 = text
        .replace("stateless.continued-trace-json", "stateless.trace-json")
        .replace(",\"failing_sequences\":[3,4,5,6,7]", "");
    assert!(parse_continued_trace(v1.as_bytes(), &ReadLimits::default()).is_err());
}

/// CONT-002: a failure only on the last step is rejected by old readers
/// through the termination check, since continued traces never end with
/// property_failed.
#[test]
fn old_readers_reject_a_last_step_only_failure() {
    let t = record_counter(&counter(), &[1, 1, 1]);
    assert_eq!(t.failing_sequences(), [3]);
    assert_eq!(t.trace.termination, Termination::Completed);
    let error = validate_recording(&t.trace).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("trace termination disagrees with recorded checks"),
        "{error}"
    );
    let error = replay(&counter(), &t.trace, ReplayOptions::default()).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("trace termination disagrees with recorded checks")
    );
    assert!(
        replay_continued(&counter(), &t, ReplayOptions::default())
            .unwrap()
            .complete_reproduction
    );
}

/// CONT-002: object key order is not normative; readers accept any order.
#[test]
fn readers_accept_any_key_order() {
    let limits = ReadLimits::default();
    let t = record_counter(&counter(), &[1, 1, 1]);
    let text = String::from_utf8(encode_continued_trace(&t, &limits).unwrap()).unwrap();
    let field = ",\"failing_sequences\":[3]";
    assert!(text.contains(field));
    // Move failing_sequences last and format before error.
    let without = text.replace(field, "");
    let last = format!(
        "{},\"failing_sequences\":[3]}}",
        without.strip_suffix('}').unwrap()
    );
    let swapped = "{\"format\":\"stateless.continued-trace-json\",\"error\":\"\",";
    let reordered = last.replacen(
        "{\"error\":\"\",\"format\":\"stateless.continued-trace-json\",",
        swapped,
        1,
    );
    assert!(reordered.starts_with(swapped) && reordered.ends_with(",\"failing_sequences\":[3]}"));
    let parsed = parse_continued_trace(reordered.as_bytes(), &limits).unwrap();
    assert!(same_evidence(&parsed, &t));
}

#[test]
fn encoding_matches_the_reference_layout() {
    let t = record_counter(&counter(), &[1, 1, 1]);
    let text =
        String::from_utf8(encode_continued_trace(&t, &ReadLimits::default()).unwrap()).unwrap();
    // Sorted keys, no whitespace, lowercase hex, integers as integers.
    assert!(text.starts_with(
        "{\"error\":\"\",\"failing_sequences\":[3],\"format\":\"stateless.continued-trace-json\",\
         \"initial_checks\":[{\"details\":\"\",\"id\":\"high\",\"status\":\"passed\"}],\
         \"initial_state\":\"0000000000000000\",\"metadata\":{\"build\":\"test\",\"codec_version\":1,\
         \"model_version\":1,\"name\":\"counter\",\"properties_version\":1},\"steps\":[{\"checks\":\
         [{\"details\":\"\",\"id\":\"high\",\"status\":\"passed\"}],\"disposition\":{\"kind\":\"accepted\",\
         \"reason\":\"\"},\"input\":\"0100000000000000\",\"outputs\":[\"0100000000000000\",\
         \"0100000000000000\"],\"post_state\":\"0100000000000000\"}"
    ), "{text}");
    assert!(
        text.ends_with("],\"termination\":\"completed\",\"version\":1}"),
        "{text}"
    );
}

#[test]
fn replay_compares_every_step() {
    let t = record_counter(&counter(), &WITNESS);
    let exact = replay_continued(&counter(), &t, ReplayOptions::default()).unwrap();
    assert!(exact.complete_reproduction);
    assert_eq!(exact.reproduced_failures, [3, 4, 5, 6, 7]);
    assert_eq!(exact.replay.steps_verified, 7);
    // Corrupted outputs at a later step: the verified prefix is not complete reproduction.
    let mut steps = t.trace.steps.clone();
    steps[4].outputs = vec![bytes(5), bytes(0)];
    let corrupt = replay_continued(
        &counter(),
        &rebuilt(&t.trace, steps),
        ReplayOptions::default(),
    )
    .unwrap();
    assert!(!corrupt.complete_reproduction);
    assert_eq!(
        corrupt.replay.outcome,
        ReplayOutcome::Diverged {
            step: Some(5),
            field: "outputs"
        }
    );
    // Reordered effects diverge.
    let mut steps = t.trace.steps.clone();
    steps[1].outputs.reverse();
    assert_eq!(
        replay_continued(
            &counter(),
            &rebuilt(&t.trace, steps),
            ReplayOptions::default()
        )
        .unwrap()
        .replay
        .outcome,
        ReplayOutcome::Diverged {
            step: Some(2),
            field: "outputs"
        }
    );
    // A suppressed later failure diverges on checks.
    let mut steps = t.trace.steps.clone();
    steps[6].checks = vec![Check::passed("high")];
    let suppressed = replay_continued(
        &counter(),
        &rebuilt(&t.trace, steps),
        ReplayOptions::default(),
    )
    .unwrap();
    assert!(!suppressed.complete_reproduction);
    assert_eq!(
        suppressed.replay.outcome,
        ReplayOutcome::Diverged {
            step: Some(7),
            field: "checks"
        }
    );
    assert_eq!(suppressed.recorded_failures, [3, 4, 5, 6]);
    // Incompatible model version.
    let incompatible = replay_continued(
        &Counter {
            version: 2,
            throw_at: None,
        },
        &t,
        ReplayOptions::default(),
    )
    .unwrap();
    assert!(matches!(
        incompatible.replay.outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    assert!(!incompatible.complete_reproduction);
    // A property_failed trace is not a continued trace.
    let legacy = record(&counter(), WITNESS, RunConfig::default(), 100_000).unwrap();
    assert!(
        replay_continued(
            &counter(),
            &ContinuedTrace { trace: legacy },
            ReplayOptions::default()
        )
        .is_err()
    );
}

#[test]
fn corrupted_and_truncated_artifacts_are_rejected() {
    let limits = ReadLimits::default();
    let encoded = encode_continued_trace(&record_counter(&counter(), &WITNESS), &limits).unwrap();
    assert!(parse_continued_trace(&encoded[..encoded.len() / 2], &limits).is_err());
    let text = String::from_utf8(encoded).unwrap();
    assert!(text.contains("\"failing_sequences\":[3,4,5,6,7]"));
    let error = parse_continued_trace(
        text.replace(
            "\"failing_sequences\":[3,4,5,6,7]",
            "\"failing_sequences\":[3,4]",
        )
        .as_bytes(),
        &limits,
    )
    .unwrap_err();
    assert!(error.to_string().contains("failing_sequences disagree"));
    assert!(
        parse_continued_trace(
            text.replace("\"version\":1", "\"version\":2").as_bytes(),
            &limits
        )
        .is_err()
    );
    // Unknown fields, uppercase hex and a tampered check batch are rejected.
    assert!(
        parse_continued_trace(text.replacen("{", "{\"extra\":1,", 1).as_bytes(), &limits).is_err()
    );
    assert!(
        parse_continued_trace(
            text.replace(
                "\"initial_state\":\"0000000000000000\"",
                "\"initial_state\":\"000000000000000A\""
            )
            .as_bytes(),
            &limits
        )
        .is_err()
    );
    assert!(
        parse_continued_trace(
            text.replacen("\"status\":\"failed\"", "\"status\":\"passed\"", 1)
                .as_bytes(),
            &limits
        )
        .is_err()
    );
    let failed = ContinuedTrace {
        trace: Trace {
            metadata: metadata("counter", 1),
            config: RunConfig::default(),
            initial_state: bytes(0),
            initial_checks: vec![],
            steps: vec![],
            termination: Termination::PropertyFailed,
        },
    };
    assert!(encode_continued_trace(&failed, &limits).is_err());
    assert!(validate_continued_recording(&failed).is_err());
}

#[test]
fn initial_failure_is_sequence_zero() {
    let t = record_counter(&counter(), &[1]);
    let failing = ContinuedTrace {
        trace: Trace {
            initial_checks: vec![Check::failed("high", "seeded")],
            ..t.trace.clone()
        },
    };
    assert_eq!(failing.failing_sequences(), [0]);
    let limits = ReadLimits::default();
    let parsed =
        parse_continued_trace(&encode_continued_trace(&failing, &limits).unwrap(), &limits)
            .unwrap();
    assert!(same_evidence(&parsed, &failing));
}

#[test]
fn incomplete_checks_after_failure() {
    let t = record_counter(
        &Counter {
            version: 1,
            throw_at: Some(4),
        },
        &[1, 1, 1, 1, 1],
    );
    let Termination::ModelError(error) = &t.trace.termination else {
        panic!("expected model_error, got {:?}", t.trace.termination);
    };
    assert!(error.contains("checker exploded"));
    assert_eq!(t.trace.steps.len(), 3);
    assert_eq!(t.failing_sequences(), [3]);
    let limits = ReadLimits::default();
    let parsed =
        parse_continued_trace(&encode_continued_trace(&t, &limits).unwrap(), &limits).unwrap();
    assert!(same_evidence(&parsed, &t));
}

#[test]
fn step_limit_ends_a_continued_recording() {
    let t = record_continued(&counter(), WITNESS, RunConfig::default(), 4).unwrap();
    assert_eq!(t.trace.termination, Termination::StepLimit);
    assert_eq!(t.failing_sequences(), [3, 4]);
}

#[test]
fn two_failures_on_one_turn() {
    let target = violation(CheckPhase::Transition, "big_step", "");
    let r = shrink_continued(&counter(), &WITNESS, &target, ShrinkConfig::default()).unwrap();
    assert!(r.base.validated_original);
    assert_eq!(r.base.termination, ShrinkTermination::SearchComplete);
    assert_eq!(r.base.original.inputs, WITNESS);
    assert_eq!(r.base.minimized.inputs, [7]);
    assert_eq!(
        r.original_findings
            .iter()
            .map(|f| f.sequence)
            .collect::<Vec<_>>(),
        [3, 4, 5, 6, 7]
    );
    assert_eq!(
        r.minimized_findings,
        [ContinuedFinding {
            sequence: 1,
            violations: vec![
                violation(CheckPhase::State, "high", "7"),
                violation(CheckPhase::Transition, "big_step", "7")
            ]
        }]
    );
    // Stop-at-failure shrinking cannot reach the later target.
    assert!(
        shrink(
            &counter(),
            &Failure {
                inputs: WITNESS.to_vec(),
                violations: vec![target]
            },
            ShrinkConfig::default()
        )
        .is_err()
    );
}

#[test]
fn selected_later_target_on_successive_turns() {
    let r = shrink_continued(
        &Trigger,
        &[0, 1, 0, 2],
        &violation(CheckPhase::State, "fired", ""),
        ShrinkConfig::default(),
    )
    .unwrap();
    assert_eq!(r.base.minimized.inputs, [1, 2]);
    assert_eq!(
        r.minimized_findings,
        [
            ContinuedFinding {
                sequence: 1,
                violations: vec![violation(CheckPhase::Transition, "armed_warning", "armed")]
            },
            ContinuedFinding {
                sequence: 2,
                violations: vec![violation(CheckPhase::State, "fired", "boom")]
            }
        ]
    );
    let outcomes: Vec<_> = r.base.history.iter().map(|a| a.outcome).collect();
    assert!(outcomes.contains(&ShrinkOutcome::InvalidCausality));
    assert!(outcomes.contains(&ShrinkOutcome::DifferentFailure));
    // A target that does not terminate the witness is a wrong target.
    let error = shrink_continued(
        &Trigger,
        &[0, 1, 0, 2],
        &violation(CheckPhase::Transition, "armed_warning", ""),
        ShrinkConfig::default(),
    )
    .unwrap_err();
    assert!(error.to_string().contains("does not reproduce"));
    // A passing check is not a target.
    assert!(
        shrink_continued(
            &Trigger,
            &[1, 2],
            &PropertyFailure {
                phase: CheckPhase::State,
                check: Check::passed("fired")
            },
            ShrinkConfig::default()
        )
        .is_err()
    );
}

#[test]
fn budget_keeps_original_and_minimized_witnesses() {
    let r = shrink_continued(
        &counter(),
        &WITNESS,
        &violation(CheckPhase::Transition, "big_step", ""),
        ShrinkConfig { max_attempts: 2 },
    )
    .unwrap();
    assert_eq!(r.base.termination, ShrinkTermination::AttemptLimit);
    assert_eq!(r.base.original.inputs, WITNESS);
    assert_eq!(r.base.minimized.inputs, [5, 0, 7]);
    assert_eq!(r.minimized_findings.last().unwrap().sequence, 3);
}

#[test]
fn graph_findings_flow_into_continued_evidence() {
    let report = explore_graph(
        &Trigger,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            failure_policy: FailurePolicy::Collect {
                max_retained_findings: 10,
                expand_failing_successors: true,
            },
            ..GraphSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(
        report
            .findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [vec![1], vec![1, 2]]
    );
    assert_eq!(
        report
            .findings
            .iter()
            .map(|f| f.requires_continued_evidence)
            .collect::<Vec<_>>(),
        [false, true]
    );
    let later = &report.findings[1];
    let shrunk = shrink_continued(
        &Trigger,
        &later.witness,
        &later.violations[0],
        ShrinkConfig::default(),
    )
    .unwrap();
    assert_eq!(shrunk.base.minimized.inputs, [1, 2]);
    let trace =
        record_continued(&Trigger, later.witness.clone(), RunConfig::default(), 100).unwrap();
    assert!(
        replay_continued(&Trigger, &trace, ReplayOptions::default())
            .unwrap()
            .complete_reproduction
    );
    assert_eq!(trace.failing_sequences(), [1, 2]);
}
