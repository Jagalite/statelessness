use stateless::execution::{CheckPolicy, ReplayOptions, ReplayOutcome, check_observed, replay};
use stateless::monitor::Recorder;
use stateless::observation::*;
use stateless::trace::{ReadLimits, RunConfig, Trace};
use stateless::{Check, CheckStatus, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use std::cell::Cell;
use std::io::{self, Write};
use std::num::NonZeroU64;

#[derive(Default)]
struct Counter {
    steps: Cell<usize>,
    checks: Cell<usize>,
    encodes: Cell<usize>,
    bad_codec: Cell<bool>,
}
impl Model for Counter {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "logging-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        Ok(Transition::accepted(state + input, vec![state + input]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        self.checks.set(self.checks.get() + 1);
        Ok(vec![if *state > 2 {
            Check::failed("limit", "too high")
        } else {
            Check::passed("limit")
        }])
    }
}
impl Counter {
    fn encode(&self, value: u8) -> Result<Vec<u8>, ModelError> {
        self.encodes.set(self.encodes.get() + 1);
        if self.bad_codec.get() {
            Err(ModelError::new("codec failed"))
        } else {
            Ok(vec![value])
        }
    }
}
impl ModelCodec for Counter {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        self.encode(*state)
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        self.encode(*input)
    }
    fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
        self.encode(*output)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [n] => Ok(*n),
            _ => Err(ModelError::new("invalid byte")),
        }
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decode_state(bytes)
    }
}
fn event<'a>(
    sequence: u64,
    before: &'a u8,
    transition: &'a Transition<u8, u8>,
    checks: &'a [Check],
) -> Event<'a, Counter> {
    Event::Transition {
        sequence: NonZeroU64::new(sequence).unwrap(),
        before,
        input: &1,
        transition: transition.as_ref(),
        checks,
    }
}
fn logger(level: LogLevel, snapshots: SnapshotPolicy) -> TextObserver<Vec<u8>, EncodedPayloads> {
    TextObserver::new(
        Vec::new(),
        "test",
        ObservationOptions {
            level,
            snapshots,
            ..ObservationOptions::default()
        },
        EncodedPayloads::new(1024),
    )
}

#[test]
fn filtering_never_encodes_below_trace_and_does_not_run_model_or_checks() {
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![1]);
    for (level, expected) in [
        (LogLevel::Off, false),
        (LogLevel::Error, false),
        (LogLevel::Warn, false),
        (LogLevel::Info, false),
        (LogLevel::Debug, true),
        (LogLevel::Trace, true),
    ] {
        let mut log = logger(level, SnapshotPolicy::EveryTransition);
        let old = model.encodes.get();
        log.observe(&model, event(1, &0, &transition, &[Check::passed("ok")]))
            .unwrap();
        assert_eq!(!log.get_ref().is_empty(), expected);
        assert_eq!(
            model.encodes.get() - old,
            if level == LogLevel::Trace { 3 } else { 0 }
        );
    }
    assert_eq!(model.steps.get(), 0);
    assert_eq!(model.checks.get(), 0);
}

#[test]
fn failures_and_skips_are_visible_at_their_severity_and_strings_are_escaped() {
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![1]);
    for (level, status, visible) in [
        (
            LogLevel::Error,
            CheckStatus::Failed("bad\nline".into()),
            true,
        ),
        (
            LogLevel::Error,
            CheckStatus::Skipped("disabled".into()),
            false,
        ),
        (
            LogLevel::Warn,
            CheckStatus::Skipped("disabled".into()),
            true,
        ),
        (LogLevel::Off, CheckStatus::Failed("bad".into()), false),
    ] {
        let mut log = logger(level, SnapshotPolicy::None);
        log.observe(
            &model,
            event(
                1,
                &0,
                &transition,
                &[Check {
                    id: "property".into(),
                    status,
                }],
            ),
        )
        .unwrap();
        let text = String::from_utf8(log.into_inner()).unwrap();
        assert_eq!(!text.is_empty(), visible);
        assert_eq!(text.lines().count(), usize::from(visible));
    }
}

#[test]
fn periodic_and_explicit_snapshots_and_runtime_level_changes() {
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![1]);
    let mut log = logger(
        LogLevel::Off,
        SnapshotPolicy::Every(NonZeroU64::new(2).unwrap()),
    );
    log.observe(&model, event(1, &0, &transition, &[])).unwrap();
    assert_eq!(model.encodes.get(), 0);
    log.set_level(LogLevel::Trace);
    log.observe(
        &model,
        Event::Checkpoint {
            sequence: 1,
            state: &0,
            checks: &[],
        },
    )
    .unwrap();
    for sequence in 2..=3 {
        log.observe(&model, event(sequence, &0, &transition, &[]))
            .unwrap();
    }
    let text = String::from_utf8(log.get_ref().clone()).unwrap();
    assert!(text.lines().next().unwrap().contains("state=hex:00"));
    assert_eq!(text.matches("post_state=").count(), 1);
    assert_eq!(model.encodes.get(), 6); // checkpoint, two inputs/outputs, one post-state
    log.set_snapshots(SnapshotPolicy::None);
    log.observe(&model, event(4, &0, &transition, &[])).unwrap();
    assert_eq!(model.encodes.get(), 8);
}

#[test]
fn formatting_and_codec_limits_never_publish_partial_records() {
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![1]);
    let mut log = logger(LogLevel::Trace, SnapshotPolicy::EveryTransition);
    model.bad_codec.set(true);
    assert!(log.observe(&model, event(1, &0, &transition, &[])).is_err());
    assert!(log.get_ref().is_empty());
    model.bad_codec.set(false);
    log.observe(&model, event(2, &0, &transition, &[])).unwrap();
    assert_eq!(
        String::from_utf8(log.into_inner()).unwrap().lines().count(),
        1
    );
    let mut log = TextObserver::new(
        Vec::new(),
        "test",
        ObservationOptions {
            level: LogLevel::Trace,
            max_record_bytes: 16,
            ..ObservationOptions::default()
        },
        EncodedPayloads::new(1024),
    );
    assert!(log.observe(&model, event(1, &0, &transition, &[])).is_err());
    assert!(log.get_ref().is_empty());
    let mut log = TextObserver::new(
        Vec::new(),
        "test",
        ObservationOptions {
            level: LogLevel::Trace,
            ..ObservationOptions::default()
        },
        EncodedPayloads::new(0),
    );
    assert!(log.observe(&model, event(1, &0, &transition, &[])).is_err());
    assert!(log.get_ref().is_empty());
}

struct BrokenWriter {
    calls: usize,
    fail_flush: bool,
}
impl Write for BrokenWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.calls += 1;
        if self.fail_flush {
            Ok(bytes.len())
        } else if self.calls == 1 {
            Ok(1)
        } else {
            Err(io::Error::other("disk full"))
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        Err(io::Error::other("flush failed"))
    }
}
#[test]
fn partial_writes_and_flush_errors_permanently_disable_the_sink() {
    let model = Counter::default();
    for fail_flush in [false, true] {
        let mut log = TextObserver::new(
            BrokenWriter {
                calls: 0,
                fail_flush,
            },
            "test",
            ObservationOptions {
                level: LogLevel::Info,
                ..ObservationOptions::default()
            },
            NoPayloads,
        );
        let result = log.observe(
            &model,
            Event::Finished {
                transitions: 0,
                reason: "done",
            },
        );
        if fail_flush {
            result.unwrap();
            assert!(log.flush().is_err());
        } else {
            assert!(result.is_err());
        }
        let calls = log.get_ref().calls;
        log.set_level(LogLevel::Off);
        assert!(
            log.observe(
                &model,
                Event::Finished {
                    transitions: 0,
                    reason: "done"
                }
            )
            .is_err()
        );
        assert_eq!(log.get_ref().calls, calls);
    }
}

#[test]
fn recorder_and_logging_share_checks_preserve_failure_and_allow_later_diagnostics() {
    let model = Counter::default();
    let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 2).unwrap();
    let mut log = logger(LogLevel::Trace, SnapshotPolicy::EveryTransition);
    let mut state = 0;
    for sequence in 1..=3 {
        let transition = model.step(&state, &1).unwrap();
        let checks = recorder.observe(&model, &state, &1, &transition).unwrap();
        log.observe(&model, event(sequence, &state, &transition, checks))
            .unwrap();
        state = transition.state;
    }
    assert_eq!(model.steps.get(), 3);
    assert_eq!(model.checks.get(), 4);
    assert!(recorder.is_frozen());
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
    let report = replay(&model, &restored, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
    log.observe(
        &model,
        Event::Message {
            level: LogLevel::Error,
            sequence: Some(3),
            message: "recorder frozen; continuing diagnostic logging",
        },
    )
    .unwrap();
    let transition = model.step(&state, &1).unwrap();
    let checks =
        check_observed(&model, &state, &1, &transition, 4, CheckPolicy::default()).unwrap();
    log.observe(&model, event(4, &state, &transition, &checks))
        .unwrap();
    assert!(
        String::from_utf8(log.into_inner())
            .unwrap()
            .contains("sequence=4")
    );
}

// Deliberately has no ModelCodec or Debug implementations on model types.
#[derive(Clone, PartialEq, Eq)]
struct Opaque;
struct OpaqueModel;
impl Model for OpaqueModel {
    type State = Opaque;
    type Input = Opaque;
    type Output = Opaque;
    fn metadata(&self) -> ModelMetadata {
        unreachable!()
    }
    fn initial_state(&self) -> Result<Opaque, ModelError> {
        unreachable!()
    }
    fn step(&self, _: &Opaque, _: &Opaque) -> Result<Transition<Opaque, Opaque>, ModelError> {
        unreachable!()
    }
    fn check_state(&self, _: &Opaque) -> Result<Vec<Check>, ModelError> {
        unreachable!()
    }
}
#[test]
fn diagnostic_logging_requires_neither_codec_nor_debug() {
    let mut log = TextObserver::new(
        Vec::new(),
        "opaque",
        ObservationOptions {
            level: LogLevel::Trace,
            snapshots: SnapshotPolicy::EveryTransition,
            ..ObservationOptions::default()
        },
        NoPayloads,
    );
    let transition = Transition::accepted(Opaque, vec![Opaque]);
    log.observe(
        &OpaqueModel,
        Event::Transition {
            sequence: NonZeroU64::new(1).unwrap(),
            before: &Opaque,
            input: &Opaque,
            transition: transition.as_ref(),
            checks: &[],
        },
    )
    .unwrap();
    let text = String::from_utf8(log.into_inner()).unwrap();
    assert!(text.contains("input=unavailable"));
    assert!(text.contains("post_state=unavailable"));
}

#[test]
fn verbosity_does_not_change_execution_checks_or_replay_evidence() {
    let mut baseline = None;
    for level in [
        LogLevel::Off,
        LogLevel::Error,
        LogLevel::Warn,
        LogLevel::Info,
        LogLevel::Debug,
        LogLevel::Trace,
    ] {
        let model = Counter::default();
        let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 8).unwrap();
        let mut log = logger(level, SnapshotPolicy::EveryTransition);
        let mut state = 0;
        for sequence in 1..=3 {
            let transition = model.step(&state, &1).unwrap();
            let checks = recorder.observe(&model, &state, &1, &transition).unwrap();
            log.observe(&model, event(sequence, &state, &transition, checks))
                .unwrap();
            state = transition.state;
        }
        assert_eq!((state, model.steps.get(), model.checks.get()), (3, 3, 4));
        let trace = recorder.into_trace();
        if let Some(expected) = &baseline {
            assert_eq!(&trace, expected);
        } else {
            baseline = Some(trace);
        }
    }
}

#[test]
fn full_text_includes_actual_outputs_disposition_checks_and_snapshot() {
    let model = Counter::default();
    let mut log = logger(LogLevel::Trace, SnapshotPolicy::EveryTransition);
    // Observe actual production output, even when it disagrees with the model.
    let transition = Transition {
        state: 7,
        outputs: vec![99, 100],
        disposition: stateless::Disposition::Ignored("duplicate".into()),
    };
    log.observe(
        &model,
        event(
            12,
            &7,
            &transition,
            &[Check::passed("first"), Check::skipped("second", "policy")],
        ),
    )
    .unwrap();
    let text = String::from_utf8(log.into_inner()).unwrap();
    for field in [
        "sequence=12",
        "Ignored(\"duplicate\")",
        "input=hex:01",
        "output[0]=hex:63",
        "output[1]=hex:64",
        "post_state=hex:07",
        "check=\"first\":Passed",
        "check=\"second\":Skipped(\"policy\")",
    ] {
        assert!(text.contains(field), "missing {field}: {text}");
    }
    assert_eq!(model.steps.get(), 0);
}

#[test]
fn lifecycle_and_explicit_messages_follow_the_threshold() {
    let model = Counter::default();
    for level in [
        LogLevel::Off,
        LogLevel::Error,
        LogLevel::Warn,
        LogLevel::Info,
        LogLevel::Debug,
        LogLevel::Trace,
    ] {
        let mut log = logger(level, SnapshotPolicy::None);
        log.observe(
            &model,
            Event::Started {
                metadata: &model.metadata(),
                config: &RunConfig::default(),
            },
        )
        .unwrap();
        for severity in [
            LogLevel::Off,
            LogLevel::Error,
            LogLevel::Warn,
            LogLevel::Info,
            LogLevel::Debug,
            LogLevel::Trace,
        ] {
            log.observe(
                &model,
                Event::Message {
                    level: severity,
                    sequence: None,
                    message: "explicit",
                },
            )
            .unwrap();
        }
        log.observe(
            &model,
            Event::Finished {
                transitions: 0,
                reason: "done",
            },
        )
        .unwrap();
        let text = String::from_utf8(log.into_inner()).unwrap();
        assert_eq!(text.contains("event=started"), level >= LogLevel::Info);
        assert_eq!(text.contains("event=finished"), level >= LogLevel::Info);
        assert_eq!(text.matches("event=message").count(), level as usize);
    }
}

#[test]
fn codec_errors_identify_the_failed_payload() {
    let model = Counter::default();
    model.bad_codec.set(true);
    let mut payloads = EncodedPayloads::new(1024);
    let mut bytes = Vec::new();
    for (stage, result) in [
        ("state", payloads.state(&model, &0, &mut bytes)),
        ("input", payloads.input(&model, &0, &mut bytes)),
        ("output", payloads.output(&model, &0, &mut bytes)),
    ] {
        assert_eq!(
            result.unwrap_err().to_string(),
            format!("encode {stage}: codec failed")
        );
    }
    assert!(bytes.is_empty());
}

struct IgnoringFormatter;
impl PayloadFormat<Counter> for IgnoringFormatter {
    fn state(&mut self, _: &Counter, _: &u8, _: &mut dyn Write) -> io::Result<()> {
        Ok(())
    }
    fn input(&mut self, _: &Counter, _: &u8, out: &mut dyn Write) -> io::Result<()> {
        // A misbehaving callback must not turn a rejected write into success.
        let _ = out.write_all(&[b'x'; 1024]);
        Ok(())
    }
    fn output(&mut self, _: &Counter, _: &u8, _: &mut dyn Write) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn swallowed_formatter_write_errors_cannot_publish_partial_records() {
    let mut log = TextObserver::new(
        Vec::new(),
        "test",
        ObservationOptions {
            level: LogLevel::Trace,
            max_record_bytes: 512,
            ..ObservationOptions::default()
        },
        IgnoringFormatter,
    );
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![]);
    assert!(log.observe(&model, event(1, &0, &transition, &[])).is_err());
    assert!(log.get_ref().is_empty());
    log.set_level(LogLevel::Debug);
    log.observe(&model, event(2, &0, &transition, &[])).unwrap();
    assert_eq!(
        String::from_utf8(log.into_inner()).unwrap().lines().count(),
        1
    );
}

#[derive(Default)]
struct ShortWriter {
    bytes: Vec<u8>,
    calls: usize,
}
impl Write for ShortWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.calls += 1;
        if self.calls % 3 == 1 {
            return Err(io::ErrorKind::Interrupted.into());
        }
        let length = bytes.len().min(3);
        self.bytes.extend_from_slice(&bytes[..length]);
        Ok(length)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn interrupted_and_short_writes_deliver_complete_records_with_exact_limit() {
    let model = Counter::default();
    let transition = Transition::accepted(1, vec![1]);
    let mut reference = logger(LogLevel::Trace, SnapshotPolicy::EveryTransition);
    reference
        .observe(&model, event(1, &0, &transition, &[]))
        .unwrap();
    let expected = reference.into_inner();
    for limit in [expected.len() - 1, expected.len()] {
        let mut log = TextObserver::new(
            ShortWriter::default(),
            "test",
            ObservationOptions {
                level: LogLevel::Trace,
                snapshots: SnapshotPolicy::EveryTransition,
                max_record_bytes: limit,
            },
            EncodedPayloads::new(1024),
        );
        let result = log.observe(&model, event(1, &0, &transition, &[]));
        if limit < expected.len() {
            assert!(result.is_err());
            assert_eq!(log.get_ref().calls, 0);
        } else {
            result.unwrap();
            log.flush().unwrap();
            assert_eq!(log.get_ref().bytes, expected);
        }
    }
}
