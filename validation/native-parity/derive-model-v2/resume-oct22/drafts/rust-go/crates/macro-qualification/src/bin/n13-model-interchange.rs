//! Finite installed binder/Recorder/evidence application. Output has no decoder.
use stateless::{
    evidence::*,
    execution::{self, CheckPolicy},
    monitor::{Recorder, RecorderOptions},
    trace::{ReadLimits, RunConfig, Trace},
    value_codec::{DecodeLimits, Decoder, TraceDecode as _, TraceEncode as _},
    *,
};
use statelessness_macros::{TraceDecode, TraceEncode};
use std::{
    cell::Cell,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
};
static STATE_E: AtomicUsize = AtomicUsize::new(0);
static STATE_D: AtomicUsize = AtomicUsize::new(0);
static INPUT_E: AtomicUsize = AtomicUsize::new(0);
static INPUT_D: AtomicUsize = AtomicUsize::new(0);
static OUTPUT_E: AtomicUsize = AtomicUsize::new(0);
static FAIL_OUTPUT: AtomicBool = AtomicBool::new(false);
static OUTPUT_FAILURES: AtomicUsize = AtomicUsize::new(0);
const OUTPUT_FAILURE: &str = "n13 output encode failure";
static LIMIT_CALLS: AtomicUsize = AtomicUsize::new(0);
static BYTE_LIMIT: AtomicUsize = AtomicUsize::new(64);
mod count {
    use super::*;
    pub fn encode(v: &u32, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        STATE_E.fetch_add(1, Ordering::SeqCst);
        v.trace_encode(o)
    }
    pub fn decode(r: &mut Decoder<'_>) -> Result<u32, ModelError> {
        STATE_D.fetch_add(1, Ordering::SeqCst);
        r.value()
    }
}
mod amount {
    use super::*;
    pub fn encode(v: &u8, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        INPUT_E.fetch_add(1, Ordering::SeqCst);
        v.trace_encode(o)
    }
    pub fn decode(r: &mut Decoder<'_>) -> Result<u8, ModelError> {
        INPUT_D.fetch_add(1, Ordering::SeqCst);
        r.value()
    }
}
#[derive(Clone, Debug, Eq, PartialEq, Hash, TraceEncode, TraceDecode)]
struct State {
    #[trace(with = "count")]
    count: u32,
}
#[derive(Clone, Debug, Eq, PartialEq, Hash, TraceEncode, TraceDecode)]
enum Input {
    #[trace(tag = 1)]
    Add(#[trace(with = "amount")] u8),
}
#[derive(Clone, Debug, Eq, PartialEq)]
struct Output(u32);
impl stateless::value_codec::TraceEncode for Output {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        OUTPUT_E.fetch_add(1, Ordering::SeqCst);
        self.0.trace_encode(o)?;
        // Fail inside the actual bound Output encoder, after candidate bytes exist.
        if FAIL_OUTPUT.load(Ordering::SeqCst) {
            OUTPUT_FAILURES.fetch_add(1, Ordering::SeqCst);
            return Err(ModelError::new(OUTPUT_FAILURE));
        }
        Ok(())
    }
}
fn fresh_limits() -> DecodeLimits {
    LIMIT_CALLS.fetch_add(1, Ordering::SeqCst);
    DecodeLimits {
        bytes: BYTE_LIMIT.load(Ordering::SeqCst),
        ..Default::default()
    }
}
#[derive(statelessness_macros::ModelCodec)]
#[model_codec(version=1,decode_limits=fresh_limits())]
struct Bound {
    version: u32,
    initial: Cell<usize>,
    reducer: Cell<usize>,
    state: Cell<usize>,
    edge: Cell<usize>,
}
impl Bound {
    fn new() -> Self {
        Self {
            version: 1,
            initial: Cell::new(0),
            reducer: Cell::new(0),
            state: Cell::new(0),
            edge: Cell::new(0),
        }
    }
    fn counts(&self) -> [usize; 4] {
        [
            self.initial.get(),
            self.reducer.get(),
            self.state.get(),
            self.edge.get(),
        ]
    }
}
impl Model for Bound {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "n13-installed-model".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: self.version,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.initial.set(self.initial.get() + 1);
        Ok(State { count: 0 })
    }
    fn step(&self, s: &State, i: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.reducer.set(self.reducer.get() + 1);
        let Input::Add(n) = i;
        let count = s.count + u32::from(*n);
        Ok(Transition::accepted(State { count }, vec![Output(count)]))
    }
    fn check_state(&self, s: &State) -> Result<Vec<Check>, ModelError> {
        self.state.set(self.state.get() + 1);
        Ok(vec![if s.count >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        }])
    }
    fn check_transition(
        &self,
        _: &State,
        _: &Input,
        _: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.edge.set(self.edge.get() + 1);
        Ok(vec![Check::passed("edge.checked")])
    }
}
#[derive(statelessness_macros::ModelCodec)]
#[model_codec(version = 1)]
struct DefaultBound(Bound);
impl Model for DefaultBound {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        self.0.metadata()
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.0.initial_state()
    }
    fn step(&self, s: &State, i: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.0.step(s, i)
    }
    fn check_state(&self, s: &State) -> Result<Vec<Check>, ModelError> {
        self.0.check_state(s)
    }
}
fn limits() -> ReadLimits {
    ReadLimits {
        max_total_bytes: 16384,
        max_frame_bytes: 4096,
        max_blob_bytes: 64,
        max_string_bytes: 256,
        max_steps: 16,
        max_checks_per_step: 16,
        max_outputs_per_step: 16,
        max_parameters: 32,
        max_items: 256,
    }
}
fn el() -> EvidenceLimits {
    EvidenceLimits {
        trace: limits(),
        max_metadata_bytes: 1024,
        max_labels: 8,
        max_label_bytes: 128,
        max_causes_per_step: 4,
        max_total_causes: 8,
    }
}
fn options() -> RecorderOptions {
    RecorderOptions {
        max_steps: 3,
        max_retained_bytes: 16384,
        limits: limits(),
    }
}
fn config() -> RunConfig {
    RunConfig {
        strategy: "manual".into(),
        seed: Some(7),
        parameters: vec![("region".into(), "test".into())],
    }
}
fn provenance() -> Provenance {
    Provenance {
        run: "n13-installed-run".into(),
        epoch: 7,
        manifest: Some("manifest-v1".into()),
        source: Some("consumer-source".into()),
        environment: vec![("region".into(), "test".into())],
    }
}
fn causal(n: u64) -> CausalContext {
    CausalContext {
        machine: Some("worker-a".into()),
        generation: Some(9),
        correlation: Some("request-1".into()),
        logical_time: Some(10 + n),
        causes: if n == 1 {
            vec![]
        } else {
            vec![OutputOrigin {
                sequence: n - 1,
                output: 0,
            }]
        },
    }
}
fn codecs() -> [usize; 6] {
    [
        STATE_E.load(Ordering::SeqCst),
        INPUT_E.load(Ordering::SeqCst),
        OUTPUT_E.load(Ordering::SeqCst),
        STATE_D.load(Ordering::SeqCst),
        INPUT_D.load(Ordering::SeqCst),
        LIMIT_CALLS.load(Ordering::SeqCst),
    ]
}
fn reset() {
    FAIL_OUTPUT.store(false, Ordering::SeqCst);
    OUTPUT_FAILURES.store(0, Ordering::SeqCst);
    for x in [
        &STATE_E,
        &INPUT_E,
        &OUTPUT_E,
        &STATE_D,
        &INPUT_D,
        &LIMIT_CALLS,
    ] {
        x.store(0, Ordering::SeqCst)
    }
}
fn encode(t: &Trace, v: &TraceV2) -> Result<(Vec<u8>, Vec<u8>), Box<dyn std::error::Error>> {
    let mut a = vec![];
    t.write_with_limits(&mut a, &limits())?;
    let mut b = vec![];
    v.write(&mut b, &el())?;
    Ok((a, b))
}
fn produce() -> Result<(Vec<u8>, Vec<u8>), Box<dyn std::error::Error>> {
    reset();
    let m = Bound::new();
    let mut state = State { count: 0 };
    let initial = execution::check_initial(&m, &state)?;
    let mut rec = Recorder::with_checked_initial(&initial, config(), options())?;
    let mut ev = EvidenceRecorder::new(&initial, 0, config(), options(), provenance(), el())?;
    for n in 1..=3 {
        let input = Input::Add(1);
        let actual = m.step(&state, &input)?;
        let checked =
            execution::check_turn(&m, &state, &input, &actual, n, CheckPolicy::default())?;
        let before = m.counts();
        rec.observe_checked(&checked)?;
        ev.observe(&checked, causal(n))?;
        assert_eq!(before, m.counts());
        state = actual.state;
    }
    assert_eq!(m.counts(), [0, 3, 4, 3]);
    assert_eq!(codecs(), [14, 6, 6, 0, 0, 0]);
    let v = ev.snapshot();
    assert_eq!(v.provenance, provenance());
    assert_eq!(
        v.steps
            .iter()
            .map(|x| x.state_check_count)
            .collect::<Vec<_>>(),
        vec![1, 1, 1]
    );
    encode(&rec.snapshot(), &v)
}
fn validate_trace(t: &Trace) {
    assert_eq!(t.metadata, Bound::new().metadata());
    assert_eq!(t.config.strategy, "manual");
    assert_eq!(t.config.seed, Some(7));
    assert_eq!(t.config.parameters[0], ("region".into(), "test".into()));
    assert_eq!(t.initial_state, [0, 0, 0, 0]);
    assert_eq!(t.steps.len(), 3);
    for (n, s) in t.steps.iter().enumerate() {
        assert_eq!(s.input, [1, 1]);
        assert_eq!(s.outputs, vec![(n as u32 + 1).to_le_bytes().to_vec()]);
        assert_eq!(s.post_state, (n as u32 + 1).to_le_bytes());
        assert_eq!(s.checks.len(), 2);
    }
    assert_eq!(t.termination, stateless::trace::Termination::PropertyFailed);
}
fn transcode(a: &[u8], b: &[u8]) -> Result<(Vec<u8>, Vec<u8>), Box<dyn std::error::Error>> {
    reset();
    let t = Trace::read_from(a, &limits())?;
    let artifact = Artifact::read(b, &el())?;
    let Artifact::V2(v) = artifact else {
        panic!("expected v2")
    };
    assert_eq!(codecs(), [0; 6]);
    validate_trace(&t);
    validate_trace(&v.trace);
    assert_eq!(v.provenance, provenance());
    assert_eq!(v.checkpoint_after, 0);
    for (n, s) in v.steps.iter().enumerate() {
        assert_eq!(s.sequence, n as u64 + 1);
        assert_eq!(s.state_check_count, 1);
        assert_eq!(s.causal, causal(n as u64 + 1));
    }
    for (t, v2) in [(&t, false), (&v.trace, true)] {
        reset();
        let m = Bound::new();
        let r = if v2 {
            v.replay(&m, Default::default(), &el())?
        } else {
            execution::replay(&m, t, Default::default())?
        };
        assert_eq!(r.outcome, execution::ReplayOutcome::Exact);
        assert_eq!(r.steps_verified, 3);
        assert!(r.failure_reproduced && r.build_matches);
        assert_eq!(m.counts(), [0, 3, 4, 3]);
        assert_eq!(codecs(), [4, 3, 3, 1, 3, 4]);
    }
    let rewritten = encode(&t, &v)?;
    assert_eq!(rewritten.0, a);
    assert_eq!(rewritten.1, b);
    Ok(rewritten)
}
// Negative captures are local controls; none of their bytes enter success artifacts.
fn assert_capture_counts(m: &Bound, model: [usize; 4], codec: [usize; 6], failures: usize) {
    assert_eq!(m.counts(), model);
    assert_eq!(codecs(), codec);
    assert_eq!(OUTPUT_FAILURES.load(Ordering::SeqCst), failures);
}
fn assert_first_capture(t: &Trace) {
    assert_eq!(t.metadata, Bound::new().metadata());
    assert_eq!(t.config.strategy, "manual");
    assert_eq!(t.config.seed, Some(7));
    for (key, value) in [
        ("region", "test"),
        ("stateless.monitor.checking", "full"),
        ("stateless.monitor.observed_steps", "1"),
        ("stateless.monitor.evicted_steps", "0"),
        ("stateless.monitor.checkpoint_after_sequence", "0"),
    ] {
        assert_eq!(
            t.config
                .parameters
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v.as_str()),
            Some(value)
        );
    }
    assert_eq!(t.initial_state, [0, 0, 0, 0]);
    assert_eq!(t.initial_checks, vec![Check::passed("threshold.bound")]);
    assert_eq!(
        t.steps,
        vec![stateless::trace::TraceStep {
            input: vec![1, 1],
            disposition: Disposition::Accepted,
            outputs: vec![vec![1, 0, 0, 0]],
            post_state: vec![1, 0, 0, 0],
            checks: vec![
                Check::passed("threshold.bound"),
                Check::passed("edge.checked")
            ],
        }]
    );
    assert_eq!(t.termination, stateless::trace::Termination::Interrupted);
}
fn assert_failed_prefix(t: &Trace, prefix: &Trace, message: &str) {
    assert_eq!(t.metadata, prefix.metadata);
    assert_eq!(t.config, prefix.config);
    assert_eq!(t.initial_state, prefix.initial_state);
    assert_eq!(t.initial_checks, prefix.initial_checks);
    assert_eq!(t.steps, prefix.steps);
    assert_eq!(
        t.termination,
        stateless::trace::Termination::ModelError(message.into())
    );
}
fn capture_failure_controls() -> Result<(), Box<dyn std::error::Error>> {
    reset();
    let m = Bound::new();
    let state = State { count: 0 };
    let input = Input::Add(1);
    let initial = execution::check_initial(&m, &state)?;
    assert_capture_counts(&m, [0, 0, 1, 0], [0; 6], 0);
    let mut rec = Recorder::with_checked_initial(&initial, config(), options())?;
    assert_capture_counts(&m, [0, 0, 1, 0], [1, 0, 0, 0, 0, 0], 0);
    let mut ev = EvidenceRecorder::new(&initial, 0, config(), options(), provenance(), el())?;
    assert_capture_counts(&m, [0, 0, 1, 0], [2, 0, 0, 0, 0, 0], 0);

    let first = m.step(&state, &input)?;
    let checked = execution::check_turn(&m, &state, &input, &first, 1, CheckPolicy::default())?;
    assert_capture_counts(&m, [0, 1, 2, 1], [2, 0, 0, 0, 0, 0], 0);
    assert_eq!(rec.observe_checked(&checked)?, checked.checks());
    assert_capture_counts(&m, [0, 1, 2, 1], [4, 1, 1, 0, 0, 0], 0);
    ev.observe(&checked, causal(1))?;
    assert_capture_counts(&m, [0, 1, 2, 1], [6, 2, 2, 0, 0, 0], 0);
    let prefix = rec.snapshot();
    let evidence_prefix = ev.snapshot();
    assert_first_capture(&prefix);
    assert_first_capture(&evidence_prefix.trace);
    assert!(!rec.is_frozen() && !ev.is_frozen());
    assert_eq!(evidence_prefix.checkpoint_after, 0);
    assert_eq!(evidence_prefix.provenance, provenance());
    assert_eq!(
        evidence_prefix.steps,
        vec![StepEvidence {
            sequence: 1,
            state_check_count: 1,
            causal: causal(1),
        }]
    );

    let second = m.step(&first.state, &input)?;
    let checked =
        execution::check_turn(&m, &first.state, &input, &second, 2, CheckPolicy::default())?;
    assert_eq!(second.state, State { count: 2 });
    assert_eq!(
        checked.checks(),
        &[
            Check::passed("threshold.bound"),
            Check::passed("edge.checked")
        ]
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [6, 2, 2, 0, 0, 0], 0);
    // Arm only after successful checking. Both failures must come from capture.
    FAIL_OUTPUT.store(true, Ordering::SeqCst);
    let message = format!("runtime encode output: {OUTPUT_FAILURE}");
    assert_eq!(
        rec.observe_checked(&checked).unwrap_err(),
        ModelError::new(&message)
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [7, 3, 3, 0, 0, 0], 1);
    // This valid causal reference resolves to the retained first output.
    assert_eq!(
        ev.observe(&checked, causal(2)).unwrap_err(),
        ModelError::new(&message)
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [8, 4, 4, 0, 0, 0], 2);
    assert!(rec.is_frozen() && ev.is_frozen());
    assert_eq!(
        (
            rec.observed_steps(),
            rec.retained_steps(),
            rec.evicted_steps()
        ),
        (1, 1, 0)
    );
    let failed = rec.snapshot();
    let failed_evidence = ev.snapshot();
    assert_failed_prefix(&failed, &prefix, &message);
    assert_failed_prefix(&failed_evidence.trace, &evidence_prefix.trace, &message);
    assert_eq!(
        failed_evidence.checkpoint_after,
        evidence_prefix.checkpoint_after
    );
    assert_eq!(failed_evidence.provenance, evidence_prefix.provenance);
    assert_eq!(failed_evidence.steps, evidence_prefix.steps);
    assert_eq!(
        failed_evidence.check_phase(0, 0),
        Some(stateless::explore::CheckPhase::InitialState)
    );
    assert_eq!(
        failed_evidence.check_phase(1, 0),
        Some(stateless::explore::CheckPhase::State)
    );
    assert_eq!(
        failed_evidence.check_phase(1, 1),
        Some(stateless::explore::CheckPhase::Transition)
    );
    assert_eq!(failed_evidence.check_phase(2, 0), None);

    // A repaired encoder cannot resume a frozen recorder or overwrite its error.
    FAIL_OUTPUT.store(false, Ordering::SeqCst);
    assert_eq!(
        rec.observe_checked(&checked).unwrap_err(),
        ModelError::new("runtime recorder is frozen; export its retained trace")
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [8, 4, 4, 0, 0, 0], 2);
    assert_eq!(
        ev.observe(&checked, causal(2)).unwrap_err(),
        ModelError::new("runtime evidence recorder is frozen; export its retained trace")
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [8, 4, 4, 0, 0, 0], 2);
    assert_eq!(rec.snapshot(), failed);
    assert_eq!(ev.snapshot(), failed_evidence);
    // Persist/read only these negative snapshots in memory; never replay the
    // prefix as proof that its terminal callback failure has been reproduced.
    let (a, b) = encode(&failed, &failed_evidence)?;
    assert_eq!(Trace::read_from(a.as_slice(), &limits())?, failed);
    assert_eq!(
        Artifact::read(b.as_slice(), &el())?,
        Artifact::V2(failed_evidence)
    );
    assert_capture_counts(&m, [0, 2, 3, 2], [8, 4, 4, 0, 0, 0], 2);
    reset();
    Ok(())
}
fn controls() -> Result<(), Box<dyn std::error::Error>> {
    reset();
    let mut m = Bound::new();
    m.version = 2;
    let s = State { count: 9 };
    let i = Input::Add(255);
    let o = Output(9);
    for error in [
        m.encode_state(&s).unwrap_err(),
        m.encode_input(&i).unwrap_err(),
        m.encode_output(&o).unwrap_err(),
        m.decode_state(&[]).unwrap_err(),
        m.decode_input(&[]).unwrap_err(),
    ] {
        assert!(error.to_string().contains("version"));
    }
    let mut bytes = vec![];
    {
        let mut sink = EncodeBuffer::new(&mut bytes, 1);
        sink.extend_from_slice(&[99])?;
        for e in [
            m.encode_state_into(&s, &mut sink).unwrap_err(),
            m.encode_input_into(&i, &mut sink).unwrap_err(),
            m.encode_output_into(&o, &mut sink).unwrap_err(),
        ] {
            assert!(e.to_string().contains("version"));
        }
    }
    assert_eq!(bytes, [99]);
    assert_eq!(codecs(), [0; 6]);
    m.version = 1;
    for b in [&[][..], &[0, 0, 0][..], &[0, 0, 0, 0, 0][..]] {
        assert!(m.decode_state(b).is_err());
    }
    for b in [&[][..], &[1][..], &[1, 1, 0][..]] {
        assert!(m.decode_input(b).is_err());
    }
    BYTE_LIMIT.store(4, Ordering::SeqCst);
    assert_eq!(m.decode_state(&[7, 0, 0, 0])?, State { count: 7 });
    BYTE_LIMIT.store(3, Ordering::SeqCst);
    assert!(m.decode_state(&[7, 0, 0, 0]).is_err());
    BYTE_LIMIT.store(4, Ordering::SeqCst);
    assert!(m.decode_state(&[7, 0, 0, 0]).is_ok());
    let before = LIMIT_CALLS.load(Ordering::SeqCst);
    m.encode_state(&s)?;
    m.encode_input(&i)?;
    m.encode_output(&o)?;
    let mut bytes = vec![];
    let mut sink = EncodeBuffer::new(&mut bytes, 64);
    m.encode_state_into(&s, &mut sink)?;
    m.encode_input_into(&i, &mut sink)?;
    m.encode_output_into(&o, &mut sink)?;
    sink.finish()?;
    assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), before);
    BYTE_LIMIT.store(2, Ordering::SeqCst);
    assert_eq!(m.decode_input(&[1, 7])?, Input::Add(7));
    BYTE_LIMIT.store(1, Ordering::SeqCst);
    assert!(m.decode_input(&[1, 7]).is_err());
    BYTE_LIMIT.store(2, Ordering::SeqCst);
    assert_eq!(m.decode_input(&[1, 7])?, Input::Add(7));
    let default = DefaultBound(Bound::new());
    let before = LIMIT_CALLS.load(Ordering::SeqCst);
    assert_eq!(default.decode_state(&[7, 0, 0, 0])?, State { count: 7 });
    assert_eq!(default.decode_input(&[1, 7])?, Input::Add(7));
    assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), before);
    BYTE_LIMIT.store(64, Ordering::SeqCst);
    Ok(())
}
fn hex(v: &[u8]) -> String {
    v.iter().map(|x| format!("{x:02x}")).collect()
}
fn unhex(s: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    if s.len() % 2 != 0
        || !s
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("noncanonical hex".into());
    }
    Ok((0..s.len())
        .step_by(2)
        .map(|n| u8::from_str_radix(&s[n..n + 2], 16).unwrap())
        .collect())
}
fn field<'a>(s: &'a str, key: &str) -> Result<&'a str, Box<dyn std::error::Error>> {
    let token = format!("\"{key}\"");
    let tail = s
        .split_once(&token)
        .ok_or("missing envelope field")?
        .1
        .trim_start();
    let tail = tail
        .strip_prefix(':')
        .ok_or("missing colon")?
        .trim_start()
        .strip_prefix('"')
        .ok_or("missing hex string")?;
    Ok(tail.split_once('"').ok_or("unterminated hex")?.0)
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    controls()?;
    capture_failure_controls()?;
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let (a, b, out) = match args.as_slice() {
        [mode, out] if mode == "produce" => {
            let (a, b) = produce()?;
            (a, b, out)
        }
        [mode, input, out] if mode == "transcode" => {
            let text = std::fs::read_to_string(input)?;
            let (a, b) = transcode(&unhex(field(&text, "v1")?)?, &unhex(field(&text, "v2")?)?)?;
            (a, b, out)
        }
        _ => return Err("produce DEST | transcode SOURCE DEST".into()),
    };
    std::fs::write(
        out,
        format!("{{\"v1\":\"{}\",\"v2\":\"{}\"}}\n", hex(&a), hex(&b)),
    )?;
    println!(
        "{{\"engine\":\"rust\",\"capture_failure_controls\":true,\"version_hooks\":8,\"fresh_limits\":true,\"encode_only_output\":true,\"checked_handoff\":true,\"reducer\":3,\"state_checks\":4,\"transition_checks\":3}}"
    );
    Ok(())
}
