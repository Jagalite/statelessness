//! Repeated mode comparisons. Probe macro compilation and runtime enablement
//! are distinct axes. The effect APIs themselves are not compiled away.
use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::execution::{CheckPolicy, check_initial, check_turn};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use statelessness_debug::diagnostic::*;
use statelessness_debug::effects::*;
use statelessness_debug::live::{ControllerOptions, LiveBridge, LiveController, LiveLimits};
use statelessness_debug::metrics::*;
use std::hint::black_box;
use std::sync::Arc;
mod support;
#[global_allocator]
static ALLOCATOR: support::allocations::MeteredSystem = support::allocations::MeteredSystem;
fn effect(observer: &EffectObserver, sequence: u64) {
    let event = observer
        .requested(
            RequestOrigin {
                run: 1,
                epoch: 1,
                machine: 1,
                transition_sequence: sequence,
                output_index: 0,
            },
            Labels::default(),
        )
        .unwrap();
    observer.admission(&event, Admission::Accepted).unwrap();
    let attempt = observer.attempt_created(&event).unwrap();
    observer.ready_queued(&attempt).unwrap();
    observer.attempt_started(&attempt).unwrap();
    black_box(sequence.wrapping_mul(31));
    observer
        .attempt_finished(&attempt, RuntimeOutcome::Success)
        .unwrap();
    observer
        .resolved(&event, RuntimeOutcome::Success, Some(&attempt))
        .unwrap();
    let delivery = observer.reserve_publication(&event).unwrap();
    observer.publication_committed(&delivery).unwrap();
    observer.delivery_begun(&delivery).unwrap();
    observer
        .delivery_observed(&delivery, DeliveryDisposition::Accepted)
        .unwrap();
}
fn measure(config: support::Config, case: &str, operation: impl FnMut()) {
    let suite = if cfg!(feature = "compiled-out-diagnostics") {
        "diagnostics_compiled_out"
    } else {
        "diagnostics_enabled"
    };
    config.measure_detailed(
        suite,
        case,
        1,
        "operation",
        &[
            ("seed", 0),
            (
                "compiled_out",
                u64::from(cfg!(feature = "compiled-out-diagnostics")),
            ),
        ],
        operation,
        support::allocations::measure,
    );
}
fn main() {
    let mut config = support::Config::from_env();
    config.iterations = config.iterations / 100 + 1;
    for (name, counters, timings, details) in [
        ("effects_disabled", false, false, false),
        ("effects_counters", true, false, false),
        ("effects_timings", false, true, false),
        ("effects_detail", true, true, true),
    ] {
        let observer = EffectObserver::new(
            EffectOptions {
                counters,
                timings,
                details,
                max_details: 32,
                max_detail_bytes: 65536,
                ..EffectOptions::default()
            },
            Arc::new(MonotonicClock::new(ClockDomain(1))),
        )
        .unwrap();
        let mut sequence = 0;
        measure(config, name, || {
            sequence += 1;
            effect(&observer, sequence);
        });
        black_box(observer.telemetry_health());
    }
    for (capture, name) in [(false, "probe_runtime_disabled"), (true, "probe_enabled")] {
        let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
        hub.add_sink(
            1,
            SinkPermissions::local_all(),
            SinkLimits {
                records: 32,
                ..SinkLimits::default()
            },
        )
        .unwrap();
        hub.register_producer(1, 1).unwrap();
        if capture {
            let ack = hub
                .configure(
                    0,
                    vec![Subscription {
                        sink: 1,
                        site: "bench".into(),
                        kind: SiteKind::Probe,
                        path: vec![],
                        trigger: Trigger::Every,
                        sample_every: 1,
                        minimum_severity: Severity::Debug,
                    }],
                )
                .unwrap();
            hub.acknowledge(1, ack.capture_revision, 0).unwrap();
        }
        let mut sequence = 0;
        let mut evaluations = 0u64;
        measure(config, name, || {
            sequence += 1;
            let mut scope = hub
                .begin_turn(1, 1, sequence, DiagnosticOrigin::Test)
                .unwrap();
            let _ = &mut scope;
            let _ = &mut evaluations;
            statelessness_debug::probe!(scope, "bench", || {
                evaluations += 1;
                black_box(sequence)
            });
            scope.finish(true);
        });
        if capture && !cfg!(feature = "compiled-out-diagnostics") {
            assert_eq!(evaluations, sequence);
        } else {
            assert_eq!(evaluations, 0);
        }
    }
    let mut controller = LiveController::new(1, 0, ControllerOptions::default()).unwrap();
    measure(config, "controller_delivery", || {
        controller.external_arrival(1u64, 8).unwrap();
        let (permit, input) = controller.begin_next().unwrap().unwrap();
        black_box(input);
        controller.delivered(permit, 0).unwrap();
    });
    let model = RequestModel::fixed();
    let mut state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    let mut bridge = LiveBridge::attach(
        &initial,
        0,
        1,
        RunConfig::default(),
        RecorderOptions {
            max_steps: 16,
            ..RecorderOptions::default()
        },
        LiveLimits::default(),
    )
    .unwrap();
    drop(initial);
    let mut sequence = 0;
    measure(config, "live_bridge_recording", || {
        sequence += 1;
        let transition = model.step(&state, &Input::Cancel).unwrap();
        let checked = check_turn(
            &model,
            &state,
            &Input::Cancel,
            &transition,
            sequence,
            CheckPolicy::default(),
        )
        .unwrap();
        bridge.observe(sequence, &checked).unwrap();
        state = transition.state;
    });
    black_box(bridge.health());
    eprintln!(
        "Separate allocation passes; probe feature/runtime axes are explicit. Effect APIs run in both builds. Setup excluded except where repeated host work is required; no production SLA claim."
    );
}
