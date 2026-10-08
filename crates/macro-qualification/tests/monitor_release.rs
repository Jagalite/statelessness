use stateless::composition::{Address, Input, Pair, PairOutput, Wiring};
use stateless::lifecycle::{Event, LifecycleMonitor, LifecycleSpec};
use stateless::{execution, trace, *};
use statelessness_macros::{TraceDecode, TraceEncode, model};

#[derive(Clone, Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
#[trace(tag_type = "u8")]
enum Command {
    #[trace(tag = 0)]
    Begin,
    #[trace(tag = 1)]
    Tick,
}
struct Machine;
#[model(state = u8, input = Command, output = u8, codec, unchecked)]
impl Machine {
    #[stateless(metadata)]
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    #[stateless(initial)]
    fn initial(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    #[stateless(step)]
    fn transition(&self, state: &u8, _: &Command) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(*state, vec![]))
    }
}
struct Spec;
impl LifecycleSpec<Machine> for Spec {
    type Key = u8;
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn input_events(&self, input: &Command, _: &Disposition) -> Result<Vec<Event<u8>>, ModelError> {
        Ok(vec![match input {
            Command::Begin => Event::Begin {
                key: 1,
                deadline: 2,
            },
            Command::Tick => Event::Tick(3),
        }])
    }
    fn output_events(&self, _: &[u8]) -> Result<Vec<Event<u8>>, ModelError> {
        Ok(vec![])
    }
}
type Monitored = WithOracle<Machine, LifecycleMonitor<Spec>>;
fn monitored() -> Monitored {
    WithOracle::new(
        Machine,
        LifecycleMonitor {
            spec: Spec,
            max_history: 8,
        },
    )
}
fn persisted_replay<M: Model + ModelCodec>(model: &M, inputs: Vec<M::Input>) {
    let original = execution::record(model, inputs, Default::default(), 10).unwrap();
    let mut bytes = vec![];
    original.write_to(&mut bytes).unwrap();
    let restored = trace::Trace::read_from(bytes.as_slice(), &Default::default()).unwrap();
    let report = execution::replay(model, &restored, Default::default()).unwrap();
    assert_eq!(report.outcome, execution::ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
}
#[test]
fn attached_history_and_serialized_monitor_trace_replay() {
    let model = monitored();
    let initial = model.initial_state().unwrap();
    let started = model.step(&initial, &Command::Begin).unwrap().state;
    assert_eq!(initial.model, started.model);
    assert_ne!(initial, started);
    let restored = model
        .decode_state(&model.encode_state(&started).unwrap())
        .unwrap();
    let attached = model.attach(restored.model, restored.oracle);
    let overdue = model.step(&attached, &Command::Tick).unwrap().state;
    assert!(
        model
            .check_state(&overdue)
            .unwrap()
            .iter()
            .any(Check::is_failure)
    );
    persisted_replay(&model, vec![Command::Begin, Command::Tick]);
}
struct Route;
impl Wiring<Monitored, Monitored> for Route {
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn messages(
        &self,
        _: &PairOutput<Monitored, Monitored>,
    ) -> Result<Vec<Address<Command, Command>>, ModelError> {
        Ok(vec![])
    }
    fn check_state_into(
        &self,
        _: &stateless::composition::PairState<Monitored, Monitored>,
        _: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}
#[test]
fn composed_monitor_histories_affect_equality_and_serialized_replay() {
    let model = Pair {
        left: monitored(),
        right: monitored(),
        wiring: Route,
        max_pending: 8,
    };
    let initial = model.initial_state().unwrap();
    let started = model
        .step(&initial, &Input::Local(Address::Left(Command::Begin)))
        .unwrap()
        .state;
    assert_eq!(initial.left.model, started.left.model);
    assert_eq!(initial.right, started.right);
    assert_ne!(initial, started);
    assert_eq!(
        model
            .decode_state(&model.encode_state(&started).unwrap())
            .unwrap(),
        started
    );
    persisted_replay(
        &model,
        vec![
            Input::Local(Address::Left(Command::Begin)),
            Input::Local(Address::Left(Command::Tick)),
        ],
    );
}
