use stateless::{
    Model,
    demo::{Input, RequestModel},
    execution::{CheckPolicy, ReplayOptions, check_observed, record, replay},
    trace::RunConfig,
};
use std::hint::black_box;

mod support;

fn main() {
    let config = support::Config::from_env();
    let model = RequestModel::fixed();
    let before = model.initial_state().unwrap();
    let input = Input::Start;
    eprintln!(
        "Stateless bounded request fixture; {} iterations/sample\nBuild: {}",
        config.iterations,
        model.metadata().build
    );
    config.measure("engine", "direct_transition", 1, "transition", || {
        black_box(model.step(black_box(&before), black_box(&input)).unwrap());
    });
    config.measure("engine", "transition_state_checks", 1, "transition", || {
        let transition = model.step(black_box(&before), black_box(&input)).unwrap();
        black_box(
            check_observed(
                &model,
                &before,
                &input,
                &transition,
                1,
                CheckPolicy {
                    transition_checks: false,
                    ..CheckPolicy::default()
                },
            )
            .unwrap(),
        );
    });
    config.measure("engine", "transition_all_checks", 1, "transition", || {
        let transition = model.step(black_box(&before), black_box(&input)).unwrap();
        black_box(
            check_observed(
                &model,
                &before,
                &input,
                &transition,
                1,
                CheckPolicy::default(),
            )
            .unwrap(),
        );
    });
    let inputs = [Input::Start, Input::Complete(1), Input::Cancel];
    let trace = record(&model, inputs.clone(), RunConfig::default(), 3).unwrap();
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    eprintln!(
        "3-transition trace: {} bytes (includes header, checks, and exact snapshots)",
        bytes.len()
    );
    let mut trace_config = config;
    trace_config.iterations = config.iterations / 10 + 1;
    trace_config.measure("engine", "record_three", 3, "transition", || {
        black_box(record(&model, inputs.clone(), RunConfig::default(), 3).unwrap());
    });
    trace_config.measure("engine", "encode_trace", 1, "trace", || {
        let mut bytes = Vec::new();
        trace.write_to(&mut bytes).unwrap();
        black_box(bytes);
    });
    trace_config.measure("engine", "exact_replay_three", 3, "transition", || {
        black_box(replay(&model, &trace, ReplayOptions::default()).unwrap());
    });
    eprintln!(
        "These measurements characterize only this small Rust fixture, not production or foreign-runtime overhead."
    );
}
