//! Repeatable ownership, composition, oracle, recording and search experiments.
//! Candidate sharing is local to this benchmark, never a production default.
use stateless::composition::{self, Pair};
use stateless::execution::{CheckPolicy, check_turn, record};
use stateless::explore::{self, FuzzConfig, SearchConfig};
use stateless::lifecycle::{Event, History, LifecycleMonitor, LifecycleSpec};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::network::{self, MachineId, Network, QueuePolicy};
use stateless::oracle::WithOracle;
use stateless::trace::RunConfig;
use stateless::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use stateless::*;
use std::hash::Hash;
use std::hint::black_box;
use std::marker::PhantomData;
use std::sync::Arc;
mod support;
#[global_allocator]
static ALLOCATOR: support::allocations::MeteredSystem = support::allocations::MeteredSystem;
trait Payload: Clone + Eq + Hash + TraceEncode + TraceDecode {
    fn zeros(size: usize) -> Self;
}
#[derive(Clone, PartialEq, Eq, Hash)]
struct Owned(Vec<u8>);
#[derive(Clone, PartialEq, Eq, Hash)]
struct Shared(Arc<Vec<u8>>);
impl Payload for Owned {
    fn zeros(n: usize) -> Self {
        Self(vec![0; n])
    }
}
impl Payload for Shared {
    fn zeros(n: usize) -> Self {
        Self(Arc::new(vec![0; n]))
    }
}
impl TraceEncode for Owned {
    fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.0.trace_encode(out)
    }
}
impl TraceDecode for Owned {
    fn trace_decode(d: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self(d.value()?))
    }
}
impl TraceEncode for Shared {
    fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.0.as_ref().trace_encode(out)
    }
}
impl TraceDecode for Shared {
    fn trace_decode(d: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self(Arc::new(d.value()?)))
    }
}
#[derive(Clone, PartialEq, Eq, Hash)]
struct State<P> {
    tick: u64,
    payload: P,
}
impl<P: TraceEncode> TraceEncode for State<P> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.tick.trace_encode(o)?;
        self.payload.trace_encode(o)
    }
}
impl<P: TraceDecode> TraceDecode for State<P> {
    fn trace_decode(d: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            tick: d.value()?,
            payload: d.value()?,
        })
    }
}
#[derive(Clone, Copy)]
struct Fixture<P> {
    bytes: usize,
    outputs: usize,
    _payload: PhantomData<P>,
}
impl<P> Fixture<P> {
    fn new(bytes: usize, outputs: usize) -> Self {
        Self {
            bytes,
            outputs,
            _payload: PhantomData,
        }
    }
}
fn identity(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: stateless::modeling::RUNTIME_BUILD_ID.into(),
    }
}
impl<P: Payload> Model for Fixture<P> {
    type State = State<P>;
    type Input = u64;
    type Output = u64;
    fn metadata(&self) -> ModelMetadata {
        identity(&format!("roadmap-fixture-{}-{}", self.bytes, self.outputs))
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(State {
            tick: 0,
            payload: P::zeros(self.bytes),
        })
    }
    fn step(
        &self,
        s: &Self::State,
        input: &u64,
    ) -> Result<Transition<Self::State, u64>, ModelError> {
        let mut next = s.clone();
        next.tick = (next.tick + input) % 64;
        Ok(Transition::accepted(
            next,
            (0..self.outputs as u64).collect(),
        ))
    }
    fn check_state(&self, s: &Self::State) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if s.tick < 64 {
            Check::passed("tick_range")
        } else {
            Check::failed("tick_range", "outside model")
        }])
    }
}
impl<P: Payload> Enumerate for Fixture<P> {
    fn inputs(&self, _: &Self::State) -> Result<Vec<u64>, ModelError> {
        Ok(vec![1, 2])
    }
}
impl<P: Payload> Generate for Fixture<P> {
    fn generate(&self, _: &Self::State, rng: &mut Rng) -> Result<Option<u64>, ModelError> {
        Ok(rng.index(2).map(|n| n as u64 + 1))
    }
}
impl<P: Payload> ModelCodec for Fixture<P> {
    fn encode_state(&self, v: &Self::State) -> Result<Vec<u8>, ModelError> {
        v.trace_bytes(usize::MAX)
    }
    fn decode_state(&self, b: &[u8]) -> Result<Self::State, ModelError> {
        State::from_trace(b, DecodeLimits::default())
    }
    fn encode_input(&self, v: &u64) -> Result<Vec<u8>, ModelError> {
        v.trace_bytes(8)
    }
    fn decode_input(&self, b: &[u8]) -> Result<u64, ModelError> {
        u64::from_trace(b, DecodeLimits::default())
    }
    fn encode_output(&self, v: &u64) -> Result<Vec<u8>, ModelError> {
        v.trace_bytes(8)
    }
    fn encode_state_into(
        &self,
        v: &Self::State,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        v.trace_encode(o)
    }
}
struct NoRoutes;
impl<P: Payload> composition::Wiring<Fixture<P>, Fixture<P>> for NoRoutes {
    fn metadata(&self) -> ModelMetadata {
        identity("no-routes")
    }
    fn messages(
        &self,
        _: &composition::PairOutput<Fixture<P>, Fixture<P>>,
    ) -> Result<Vec<composition::Message<Fixture<P>, Fixture<P>>>, ModelError> {
        Ok(vec![])
    }
    fn check_state_into(
        &self,
        _: &composition::PairState<Fixture<P>, Fixture<P>>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("global"));
        Ok(())
    }
}
impl<P: Payload> network::Wiring<Fixture<P>> for NoRoutes {
    fn metadata(&self) -> ModelMetadata {
        identity("no-routes")
    }
    fn routes(
        &self,
        _: &network::NetworkOutput<Fixture<P>>,
    ) -> Result<Vec<network::Route<u64>>, ModelError> {
        Ok(vec![])
    }
    fn check_state_into(
        &self,
        _: &network::NetworkState<Fixture<P>>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("global"));
        Ok(())
    }
}
struct HistorySpec;
impl<P: Payload> LifecycleSpec<Fixture<P>> for HistorySpec {
    type Key = u64;
    fn metadata(&self) -> ModelMetadata {
        identity("history-bench")
    }
    fn input_events(&self, input: &u64, _: &Disposition) -> Result<Vec<Event<u64>>, ModelError> {
        Ok(vec![Event::Tick(*input)])
    }
    fn output_events(&self, _: &[u64]) -> Result<Vec<Event<u64>>, ModelError> {
        Ok(vec![])
    }
}
fn measure(
    config: support::Config,
    case: &str,
    units: usize,
    unit: &str,
    details: &[(&str, u64)],
    operation: impl FnMut(),
) {
    config.measure_detailed(
        "roadmap",
        case,
        units,
        unit,
        details,
        operation,
        support::allocations::measure,
    );
}
fn pair<P: Payload>() -> Pair<Fixture<P>, Fixture<P>, NoRoutes> {
    Pair {
        left: Fixture::new(32 * 1024, 4),
        right: Fixture::new(32 * 1024, 4),
        wiring: NoRoutes,
        max_pending: 16,
    }
}
fn main() {
    let mut config = support::Config::from_env();
    config.iterations = config.iterations / 100 + 1;
    // Representative application cycle: retry, modeled time, cancellation,
    // acknowledgment, successful publication, and duplicate completion.
    use stateless::reference::{Controller, Input as ControllerInput, Outcome};
    let controller = Controller::default();
    let cycle = [
        ControllerInput::Submit,
        ControllerInput::Complete {
            generation: 1,
            attempt: 1,
            outcome: Outcome::RetryableFailure,
        },
        ControllerInput::Tick { now: 1 },
        ControllerInput::Cancel { generation: 1 },
        ControllerInput::Cancelled {
            generation: 1,
            attempt: 2,
        },
        ControllerInput::Submit,
        ControllerInput::Complete {
            generation: 2,
            attempt: 1,
            outcome: Outcome::Success,
        },
        ControllerInput::Complete {
            generation: 2,
            attempt: 1,
            outcome: Outcome::Success,
        },
    ];
    let qualified = record(
        &controller,
        cycle.clone(),
        RunConfig::default(),
        cycle.len(),
    )
    .unwrap();
    assert_eq!(
        qualified.termination,
        stateless::trace::Termination::Completed
    );
    assert_eq!(qualified.steps.len(), cycle.len());
    for (case, checked) in [
        ("controller_reducer_cycle", false),
        ("controller_checked_cycle", true),
    ] {
        measure(
            config,
            case,
            cycle.len(),
            "transition",
            &[
                ("generations", 2),
                ("attempts", 2),
                ("logical_time_bound", 2),
                ("seed", 0),
            ],
            || {
                let mut state = controller.initial_state().unwrap();
                for (index, input) in cycle.iter().enumerate() {
                    let next = controller.step(&state, input).unwrap();
                    if checked {
                        black_box(
                            check_turn(
                                &controller,
                                &state,
                                input,
                                &next,
                                index as u64 + 1,
                                CheckPolicy::default(),
                            )
                            .unwrap(),
                        );
                    }
                    black_box(&next.outputs);
                    state = next.state;
                }
                black_box(state);
            },
        );
    }
    let owned = Fixture::<Owned>::new(32 * 1024, 4);
    let shared = Fixture::<Shared>::new(32 * 1024, 4);
    // Exact canonical evidence parity is a precondition, outside all timing/probes.
    let inputs = vec![1, 2, 1, 1, 2];
    let a = record(&owned, inputs.clone(), RunConfig::default(), 5).unwrap();
    let b = record(&shared, inputs, RunConfig::default(), 5).unwrap();
    assert_eq!(a, b);
    let own = owned.initial_state().unwrap();
    let share = shared.initial_state().unwrap();
    let details = [
        ("state_payload_bytes", 32768),
        ("output_bytes", 32),
        ("seed", 42),
    ];
    measure(config, "owned_step_32k", 1, "transition", &details, || {
        black_box(owned.step(black_box(&own), &1).unwrap());
    });
    measure(config, "shared_step_32k", 1, "transition", &details, || {
        black_box(shared.step(black_box(&share), &1).unwrap());
    });
    for (count, case) in [(1, "outputs_1"), (64, "outputs_64")] {
        let model = Fixture::<Shared>::new(0, count);
        let initial = model.initial_state().unwrap();
        measure(
            config,
            case,
            1,
            "transition",
            &[
                ("state_payload_bytes", 0),
                ("output_bytes", count as u64 * 8),
                ("seed", 42),
            ],
            || {
                black_box(model.step(&initial, &1).unwrap());
            },
        );
    }
    let pair_own = pair::<Owned>();
    let pair_shared = pair::<Shared>();
    let input = composition::Input::Local(composition::Address::Left(1));
    let a = record(&pair_own, vec![input.clone(); 4], RunConfig::default(), 4).unwrap();
    let b = record(
        &pair_shared,
        vec![input.clone(); 4],
        RunConfig::default(),
        4,
    )
    .unwrap();
    assert_eq!(a, b);
    let mut before = pair_own.initial_state().unwrap();
    before.pending = vec![composition::Address::Right(1); 8];
    let mut candidate = pair_shared.initial_state().unwrap();
    candidate.pending = before.pending.clone();
    for input in [
        composition::Input::Local(composition::Address::Left(1)),
        composition::Input::Local(composition::Address::Right(2)),
        composition::Input::Deliver(0),
        composition::Input::Deliver(7),
    ] {
        let old = pair_own.step(&before, &input).unwrap();
        let new = pair_shared.step(&candidate, &input).unwrap();
        assert_eq!(
            pair_own.encode_state(&old.state).unwrap(),
            pair_shared.encode_state(&new.state).unwrap()
        );
        assert_eq!(old.outputs, new.outputs);
        assert_eq!(old.disposition, new.disposition);
        let old_checks =
            check_turn(&pair_own, &before, &input, &old, 1, CheckPolicy::default()).unwrap();
        let new_checks = check_turn(
            &pair_shared,
            &candidate,
            &input,
            &new,
            1,
            CheckPolicy::default(),
        )
        .unwrap();
        assert_eq!(old_checks.checks(), new_checks.checks());
        assert_eq!(
            old_checks.state_check_count(),
            new_checks.state_check_count()
        );
    }
    let pair_details = [
        ("state_payload_bytes", 65536),
        ("output_bytes", 32),
        ("machines", 2),
        ("queue_depth", 8),
        ("seed", 42),
    ];
    measure(
        config,
        "pair_owned_32k",
        1,
        "transition",
        &pair_details,
        || {
            black_box(pair_own.step(&before, &input).unwrap());
        },
    );
    measure(
        config,
        "pair_shared_32k",
        1,
        "transition",
        &pair_details,
        || {
            black_box(pair_shared.step(&candidate, &input).unwrap());
        },
    );
    for (entries, case) in [(0, "oracle_history_0"), (256, "oracle_history_256")] {
        let model = WithOracle::new(
            Fixture::<Shared>::new(0, 0),
            LifecycleMonitor {
                spec: HistorySpec,
                max_history: 1024,
            },
        );
        let history = History {
            obligations: (0..entries).map(|key| (key, 100, false, true)).collect(),
            ..History::default()
        };
        let initial = model.attach(model.model().initial_state().unwrap(), history);
        measure(
            config,
            case,
            1,
            "transition",
            &[
                ("history_entries", entries),
                ("state_payload_bytes", 0),
                ("output_bytes", 0),
                ("seed", 42),
            ],
            || {
                let result = model.step(&initial, &1).unwrap();
                black_box(model.check_state(&result.state).unwrap());
                black_box(result);
            },
        );
    }
    for (window, case) in [(1, "recorder_window_1"), (16, "recorder_window_16")] {
        let model = Fixture::<Shared>::new(128, 4);
        measure(
            config,
            case,
            64,
            "transition",
            &[
                ("retained_steps", window),
                ("state_payload_bytes", 128),
                ("output_bytes", 32),
                ("seed", 42),
            ],
            || {
                let mut state = model.initial_state().unwrap();
                let mut recorder = Recorder::with_options(
                    &model,
                    &state,
                    RunConfig::default(),
                    RecorderOptions {
                        max_steps: window as usize,
                        ..RecorderOptions::default()
                    },
                )
                .unwrap();
                for sequence in 1..=64 {
                    let transition = model.step(&state, &1).unwrap();
                    let checked = check_turn(
                        &model,
                        &state,
                        &1,
                        &transition,
                        sequence,
                        CheckPolicy::default(),
                    )
                    .unwrap();
                    recorder.observe_checked(&checked).unwrap();
                    state = transition.state;
                }
                assert_eq!(recorder.retained_steps(), window as usize);
                black_box(recorder.into_trace());
            },
        );
    }
    measure(config, "encode_state_32k", 1, "state", &details, || {
        black_box(shared.encode_state(&share).unwrap());
    });
    let graph = Fixture::<Shared>::new(128, 0);
    measure(
        config,
        "bfs_64",
        128,
        "transition",
        &[
            ("graph_states", 64),
            ("state_payload_bytes", 128),
            ("output_bytes", 0),
            ("seed", 42),
        ],
        || {
            let report = explore::enumerate(&graph, SearchConfig::default()).unwrap();
            assert_eq!(report.transitions, 128);
            black_box(report);
        },
    );
    let fuzz = FuzzConfig {
        seed: 42,
        cases: 1,
        max_steps: 64,
        max_transitions: 64,
        mutation_percent: 50,
    };
    measure(
        config,
        "fuzz_64",
        64,
        "transition",
        &[
            ("graph_states", 64),
            ("state_payload_bytes", 128),
            ("output_bytes", 0),
            ("seed", 42),
        ],
        || {
            black_box(explore::fuzz(&graph, fuzz.clone()).unwrap());
        },
    );
    measure(
        config,
        "guided_fuzz_64",
        64,
        "transition",
        &[
            ("graph_states", 64),
            ("state_payload_bytes", 128),
            ("output_bytes", 0),
            ("seed", 42),
        ],
        || {
            black_box(
                guided::fuzz(
                    &graph,
                    fuzz.clone(),
                    guided::GuidanceConfig::default(),
                    guided::exact_state::<Fixture<Shared>>(),
                )
                .unwrap(),
            );
        },
    );
    let network = Network::new(
        (0..3).map(|n| (MachineId(n), Fixture::<Shared>::new(32768, 4))),
        NoRoutes,
        QueuePolicy::default(),
    )
    .unwrap();
    let state = network.initial_state().unwrap();
    let input = network::Input::Local(network::Address {
        machine: MachineId(1),
        value: 1,
    });
    measure(
        config,
        "network_three",
        1,
        "transition",
        &[
            ("machines", 3),
            ("state_payload_bytes", 98304),
            ("output_bytes", 32),
            ("queue_depth", 0),
            ("seed", 42),
        ],
        || {
            black_box(network.step(&state, &input).unwrap());
        },
    );
    eprintln!(
        "Owned/shared model and Pair exact trace parity asserted before sampling; no production representation changed. Allocation probes are separate passes; no RSS/CPU inference."
    );
}
