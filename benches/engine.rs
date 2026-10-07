use stateless::{
    Model,
    demo::{Input, RequestModel},
    execution::{CheckPolicy, ReplayOptions, check_observed, record, replay},
    trace::RunConfig,
};
use std::{hint::black_box, time::Instant};

fn main() {
    let iterations = std::env::var("STATELESS_BENCH_ITERS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(20_000)
        .max(1);
    let model = RequestModel::fixed();
    let before = model.initial_state().unwrap();
    let input = Input::Start;
    println!(
        "Stateless bounded request fixture; release build recommended; {iterations} iterations/sample; 7 samples\nBuild: {}",
        model.metadata().build
    );
    measure("direct transition", iterations, || {
        black_box(model.step(black_box(&before), black_box(&input)).unwrap());
    });
    measure("transition + all checks", iterations, || {
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
    println!(
        "3-transition trace: {} bytes (includes header, checks, and exact snapshots)",
        bytes.len()
    );
    measure("record 3 transitions", iterations / 10 + 1, || {
        black_box(record(&model, inputs.clone(), RunConfig::default(), 3).unwrap());
    });
    measure("encode trace", iterations / 10 + 1, || {
        let mut bytes = Vec::new();
        trace.write_to(&mut bytes).unwrap();
        black_box(bytes);
    });
    measure("exact replay 3 transitions", iterations / 10 + 1, || {
        black_box(replay(&model, &trace, ReplayOptions::default()).unwrap());
    });
    println!(
        "These measurements characterize only this small Rust fixture, not production or foreign-runtime overhead."
    );
}

fn measure(name: &str, iterations: usize, mut operation: impl FnMut()) {
    for _ in 0..100 {
        operation();
    }
    let mut samples = Vec::new();
    for _ in 0..7 {
        let start = Instant::now();
        for _ in 0..iterations {
            operation();
        }
        samples.push(start.elapsed().as_nanos() as f64 / iterations as f64);
    }
    samples.sort_by(f64::total_cmp);
    println!(
        "{name}: median {:.1} ns/op; sample range {:.1}..{:.1} ns/op",
        samples[3], samples[0], samples[6]
    );
}
