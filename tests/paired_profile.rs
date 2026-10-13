//! Hand-authored portable expectations; actual/reference reducers have different
//! layouts and algorithms. Compilation is not cross-language runtime qualification.
use stateless::conformance::{Paired, Projection};
use stateless::value_codec::{TraceDecode, TraceEncode};
use stateless::*;

struct Queue(&'static str);
struct Reference;
struct Semantic;
fn metadata(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "paired-fixture-v1".into(),
    }
}
impl Model for Queue {
    type State = Vec<u8>;
    type Input = u8;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        metadata(&format!("paired-queue-actual:{}", self.0))
    }
    fn initial_state(&self) -> Result<Vec<u8>, ModelError> {
        Ok(if self.0 == "initial" { vec![1] } else { vec![] })
    }
    fn step(&self, state: &Vec<u8>, input: &u8) -> Result<Transition<Vec<u8>, String>, ModelError> {
        let mut next = state.clone();
        if *input < 2 {
            if state.len() == 2 {
                return Ok(Transition {
                    state: next,
                    outputs: vec![],
                    disposition: Disposition::Rejected("full".into()),
                });
            }
            next.push(if self.0 == "state" { 1 - input } else { *input });
            let mut outputs = vec![(10 + input).to_string(), (20 + next.len()).to_string()];
            if self.0 == "order" {
                outputs.reverse();
            }
            if self.0 == "omit" {
                outputs.truncate(1);
            }
            Ok(Transition::accepted(next, outputs))
        } else if *input == 2 {
            if state.is_empty() {
                return Ok(Transition {
                    state: next,
                    outputs: vec![],
                    disposition: Disposition::Ignored(
                        if self.0 == "reason" {
                            "empty!"
                        } else {
                            "empty"
                        }
                        .into(),
                    ),
                });
            }
            let value = next.remove(0);
            let outputs = vec![(30 + value).to_string(), (20 + next.len()).to_string()];
            Ok(Transition::accepted(next, outputs))
        } else {
            Err(ModelError::new("invalid queue input"))
        }
    }
    fn check_state(&self, state: &Vec<u8>) -> Result<Vec<Check>, ModelError> {
        Ok(vec![stateless::check!(
            "queue.bound",
            state.len() <= 2,
            "overfull"
        )])
    }
    fn check_transition(
        &self,
        _: &Vec<u8>,
        _: &u8,
        _: &TransitionRef<'_, Vec<u8>, String>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("queue.transition")])
    }
}
impl Enumerate for Queue {
    fn inputs(&self, _: &Vec<u8>) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0, 1, 2])
    }
}
impl ModelCodec for Queue {
    fn encode_state(&self, state: &Vec<u8>) -> Result<Vec<u8>, ModelError> {
        state.trace_bytes(6)
    }
    fn decode_state(&self, data: &[u8]) -> Result<Vec<u8>, ModelError> {
        // Deliberately permissive test-only codec for the negative replay case.
        let data = if self.0 == "codec-alias" {
            data.strip_suffix(&[255]).unwrap_or(data)
        } else {
            data
        };
        let values = Vec::<u8>::from_trace(data, Default::default())?;
        if values.len() > 2 || values.iter().any(|v| *v > 1) {
            return Err(ModelError::new("invalid queue checkpoint"));
        }
        Ok(values)
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }
    fn decode_input(&self, data: &[u8]) -> Result<u8, ModelError> {
        match data {
            [v] if *v < 3 => Ok(*v),
            _ => Err(ModelError::new("invalid queue input")),
        }
    }
    fn encode_output(&self, output: &String) -> Result<Vec<u8>, ModelError> {
        Ok(output.as_bytes().to_vec())
    }
}
impl Model for Reference {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("paired-queue-reference")
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, packed: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        let size = u8::from(*packed != 0) + u8::from(*packed >= 3);
        if *input == 2 {
            if size == 0 {
                Ok(Transition {
                    state: 0,
                    outputs: vec![],
                    disposition: Disposition::Ignored("empty".into()),
                })
            } else {
                Ok(Transition::accepted(
                    packed / 3,
                    vec![29 + packed % 3, 19 + size],
                ))
            }
        } else if *input < 2 {
            if size == 2 {
                Ok(Transition {
                    state: *packed,
                    outputs: vec![],
                    disposition: Disposition::Rejected("full".into()),
                })
            } else {
                Ok(Transition::accepted(
                    packed + (input + 1) * 3u8.pow(u32::from(size)),
                    vec![10 + input, 21 + size],
                ))
            }
        } else {
            Err(ModelError::new("invalid reference input"))
        }
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![stateless::check!(
            "reference.bound",
            [0, 1, 2, 4, 5, 7, 8].contains(state),
            "invalid digits"
        )])
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &u8,
        _: &TransitionRef<'_, u8, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("reference.transition")])
    }
}
impl ModelCodec for Reference {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, data: &[u8]) -> Result<u8, ModelError> {
        match data {
            [v] if [0, 1, 2, 4, 5, 7, 8].contains(v) => Ok(*v),
            _ => Err(ModelError::new("invalid reference checkpoint")),
        }
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Queue("none").encode_input(input)
    }
    fn decode_input(&self, data: &[u8]) -> Result<u8, ModelError> {
        Queue("none").decode_input(data)
    }
    fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*output])
    }
}
impl Projection<Queue, Reference> for Semantic {
    type State = Vec<u8>;
    type Effect = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("paired-queue-projection")
    }
    fn actual_state(&self, state: &Vec<u8>) -> Result<Vec<u8>, ModelError> {
        Ok(state.clone())
    }
    fn reference_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        let mut packed = *state;
        let mut values = vec![];
        while packed != 0 {
            values.push(packed % 3 - 1);
            packed /= 3;
        }
        Ok(values)
    }
    fn actual_effect(&self, effect: &String) -> Result<u8, ModelError> {
        effect
            .parse()
            .map_err(|_| ModelError::new("invalid projected effect"))
    }
    fn reference_effect(&self, effect: &u8) -> Result<u8, ModelError> {
        Ok(*effect)
    }
}
fn paired(mutation: &'static str) -> WithOracle<Queue, Paired<Reference, Semantic>> {
    WithOracle::new(
        Queue(mutation),
        Paired {
            reference: Reference,
            projection: Semantic,
        },
    )
}
fn termination(value: explore::SearchTermination) -> &'static str {
    use explore::SearchTermination::*;
    match value {
        GraphExhausted => "graph_exhausted",
        DepthBound => "depth_bound",
        StateLimit => "state_limit",
        TransitionLimit => "transition_limit",
        FailureFound => "failure_found",
        _ => "outside_profile",
    }
}

struct ConstantActual;
struct HiddenReference;
struct HiddenProjection;
impl Model for ConstantActual {
    type State = ();
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("constant-actual")
    }
    fn initial_state(&self) -> Result<(), ModelError> {
        Ok(())
    }
    fn step(&self, _: &(), _: &u8) -> Result<Transition<(), u8>, ModelError> {
        Ok(Transition::accepted((), vec![]))
    }
    fn check_state(&self, _: &()) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl Enumerate for ConstantActual {
    fn inputs(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0, 1])
    }
}
impl Model for HiddenReference {
    type State = bool;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("hidden-reference")
    }
    fn initial_state(&self) -> Result<bool, ModelError> {
        Ok(false)
    }
    fn step(&self, state: &bool, input: &u8) -> Result<Transition<bool, u8>, ModelError> {
        Ok(Transition::accepted(
            if *input == 0 { !*state } else { *state },
            if *state && *input == 1 {
                vec![7]
            } else {
                vec![]
            },
        ))
    }
    fn check_state(&self, _: &bool) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl Projection<ConstantActual, HiddenReference> for HiddenProjection {
    type State = ();
    type Effect = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("hidden-projection")
    }
    fn actual_state(&self, _: &()) -> Result<(), ModelError> {
        Ok(())
    }
    fn reference_state(&self, _: &bool) -> Result<(), ModelError> {
        Ok(())
    }
    fn actual_effect(&self, output: &u8) -> Result<u8, ModelError> {
        Ok(*output)
    }
    fn reference_effect(&self, output: &u8) -> Result<u8, ModelError> {
        Ok(*output)
    }
}

#[test]
fn same_actual_state_keeps_distinct_reference_history() {
    let model = WithOracle::new(
        ConstantActual,
        Paired {
            reference: HiddenReference,
            projection: HiddenProjection,
        },
    );
    let report = explore::enumerate(&model, Default::default()).unwrap();
    assert_eq!(report.termination, explore::SearchTermination::FailureFound);
    assert_eq!(
        (report.states, report.transitions, report.skipped_checks),
        (2, 4, 0)
    );
    let failure = report.failure.unwrap();
    assert_eq!(failure.inputs, [0, 1]);
    assert_eq!(
        failure
            .violations
            .iter()
            .map(|v| (v.phase, v.check.id.as_str()))
            .collect::<Vec<_>>(),
        [(explore::CheckPhase::Transition, "pair.effects")]
    );
    let bounded = explore::enumerate(
        &model,
        explore::SearchConfig {
            max_states: 10,
            max_transitions: 3,
            max_depth: 10,
        },
    )
    .unwrap();
    assert_eq!(
        bounded.termination,
        explore::SearchTermination::TransitionLimit
    );
    assert_eq!((bounded.states, bounded.transitions), (2, 3));
}

#[test]
fn paired_v1_independent_queue_goldens() {
    for line in include_str!("../conformance/paired-v1.tsv").lines().skip(1) {
        let c = line.split('\t').collect::<Vec<_>>();
        assert_eq!(c.len(), 12);
        let report = explore::enumerate(
            &paired(c[1]),
            explore::SearchConfig {
                max_states: c[2].parse().unwrap(),
                max_transitions: c[3].parse().unwrap(),
                max_depth: c[4].parse().unwrap(),
            },
        )
        .unwrap();
        assert_eq!(termination(report.termination), c[5], "{}", c[0]);
        assert_eq!(report.states, c[6].parse::<usize>().unwrap(), "{}", c[0]);
        assert_eq!(report.transitions, c[7].parse::<u64>().unwrap(), "{}", c[0]);
        assert_eq!(
            report.skipped_checks,
            c[11].parse::<u64>().unwrap(),
            "{}",
            c[0]
        );
        let phases = report
            .failure
            .as_ref()
            .map(|failure| {
                failure
                    .violations
                    .iter()
                    .map(|v| match v.phase {
                        explore::CheckPhase::InitialState => "initial_state",
                        explore::CheckPhase::State => "state",
                        explore::CheckPhase::Transition => "transition",
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        assert_eq!(
            phases,
            if c[10] == "-" {
                vec![]
            } else {
                c[10].split(',').collect::<Vec<_>>()
            },
            "{}",
            c[0]
        );
        let ids = report
            .failure
            .as_ref()
            .map(|f| {
                f.violations
                    .iter()
                    .map(|v| v.check.id.as_str())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        assert_eq!(
            ids,
            if c[8] == "-" {
                vec![]
            } else {
                c[8].split(',').collect::<Vec<_>>()
            },
            "{}",
            c[0]
        );
        let witness = report
            .failure
            .as_ref()
            .map(|f| f.inputs.clone())
            .unwrap_or_default();
        assert_eq!(
            witness,
            if c[9] == "-" {
                vec![]
            } else {
                c[9].split(',')
                    .map(|v| v.parse::<u8>().unwrap())
                    .collect::<Vec<_>>()
            },
            "{}",
            c[0]
        );
        if report.failure.is_some() {
            let trace = execution::record(&paired(c[1]), witness, Default::default(), 10).unwrap();
            let batch = trace
                .steps
                .last()
                .map(|step| &step.checks)
                .unwrap_or(&trace.initial_checks);
            let mut expected = vec!["queue.bound", "reference.bound", "pair.state"];
            if !trace.steps.is_empty() {
                expected.extend([
                    "queue.transition",
                    "reference.transition",
                    "pair.effects",
                    "pair.outcome",
                ]);
            }
            assert_eq!(
                batch
                    .iter()
                    .map(|check| (
                        check.id.as_str(),
                        match &check.status {
                            CheckStatus::Passed => "passed",
                            CheckStatus::Failed(_) => "failed",
                            CheckStatus::Skipped(_) => "skipped",
                        }
                    ))
                    .collect::<Vec<_>>(),
                expected
                    .iter()
                    .map(|id| (*id, if ids.contains(id) { "failed" } else { "passed" }))
                    .collect::<Vec<_>>(),
                "{}",
                c[0]
            );
        }
    }
}

#[test]
fn paired_profile_check_order_restart_exact_replay_and_strict_identity() {
    let model = paired("none");
    let before = model.initial_state().unwrap();
    let transition = model.step(&before, &1).unwrap();
    let mut checks = model.check_state(&transition.state).unwrap();
    checks.extend(
        model
            .check_transition(&before, &1, &transition.as_ref())
            .unwrap(),
    );
    assert_eq!(
        checks.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
        [
            "queue.bound",
            "reference.bound",
            "pair.state",
            "queue.transition",
            "reference.transition",
            "pair.effects",
            "pair.outcome"
        ]
    );
    let restored = model
        .decode_state(&model.encode_state(&transition.state).unwrap())
        .unwrap();
    assert_eq!(
        model.step(&restored, &2).unwrap(),
        model.step(&transition.state, &2).unwrap()
    );
    let initial = model.encode_state(&before).unwrap();
    let hex = initial
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    assert_eq!(hex, "4f524331040000000000000000000000010000000000000000");
    for bad in [
        initial[..initial.len() - 1].to_vec(),
        [initial.as_slice(), &[0]].concat(),
        [b"ORC1".as_slice(), &[255; 8]].concat(),
    ] {
        assert!(model.decode_state(&bad).is_err());
    }
    let faulty = paired("order");
    let trace = execution::record(&faulty, vec![0, 1, 2], Default::default(), 10).unwrap();
    assert_eq!(trace.steps.len(), 1);
    let replay = execution::replay(&faulty, &trace, Default::default()).unwrap();
    assert_eq!(replay.outcome, execution::ReplayOutcome::Exact);
    assert!(replay.failure_reproduced);
    let incompatible = execution::replay(
        &model,
        &trace,
        execution::ReplayOptions {
            allow_build_mismatch: true,
        },
    )
    .unwrap();
    assert!(matches!(
        incompatible.outcome,
        execution::ReplayOutcome::Incompatible { .. }
    ));
}

#[test]
fn exact_replay_rejects_permissive_component_decoder_alias() {
    let model = paired("codec-alias");
    let mut trace = execution::record(&model, Vec::<u8>::new(), Default::default(), 10).unwrap();
    // The empty queue is four bytes. Add a codec-accepted alias inside ORC1.
    trace.initial_state[4..12].copy_from_slice(&5u64.to_le_bytes());
    trace.initial_state.insert(16, 255);
    assert_eq!(
        model.decode_state(&trace.initial_state).unwrap(),
        model.initial_state().unwrap()
    );
    let error = execution::replay(&model, &trace, Default::default()).unwrap_err();
    assert!(error.to_string().contains("not canonical"));
}
