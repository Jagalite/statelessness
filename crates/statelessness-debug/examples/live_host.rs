//! A synthetic application owns every reducer/checker call and every dispatch.
//! Run: cargo run -p statelessness-debug --example live_host
use stateless::demo::{Input, Output, RequestModel};
use stateless::execution::{CheckPolicy, ReplayOptions, check_initial, check_turn, replay};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use stateless::{Model, ModelCodec, Transition};
use statelessness_debug::effects::{
    Admission, ClockDomain, DeliveryDisposition, DeliveryHandle, EffectHandle, EffectObserver,
    EffectOptions, MonotonicClock, RequestOrigin, RuntimeOutcome,
};
use statelessness_debug::live::{
    ControlAuthority, ControllerOptions, DeliveryPermit, LiveBridge, LiveController, LiveError,
    LiveLimits,
};
use statelessness_debug::metrics::{GaugeScope, MeasurementOrigin, MetricFamily, MetricValue};
use std::collections::VecDeque;
use std::sync::Arc;

/// A completed application transition is owned independently of diagnostics.
/// On checker/control termination, retain its outputs for the host's shutdown
/// policy; never dispatch them after losing control authority or retry the turn.
struct CommittedTurn<O> {
    pending_outputs: Vec<O>,
    stop: Option<String>,
    capture_error: Option<LiveError>,
}
fn commit_actual<M: ModelCodec>(
    model: &M,
    state: &mut M::State,
    input: &M::Input,
    actual: Transition<M::State, M::Output>,
    permit: DeliveryPermit,
    gate: &mut LiveController<M::Input>,
    capture: &mut LiveBridge<M>,
) -> CommittedTurn<M::Output> {
    let sequence = gate.status().revision + 1;
    let (mut stop, capture_error) = match check_turn(
        model,
        state,
        input,
        &actual,
        sequence,
        CheckPolicy::default(),
    ) {
        Ok(checked) => {
            let failed = checked.checks().iter().any(|check| check.is_failure());
            (
                failed.then(|| "host property policy stopped execution".into()),
                capture.observe(sequence, &checked).err(),
            )
        }
        Err(error) => {
            capture.stop_recording(error.clone());
            (Some(format!("host checker failed: {error}")), None)
        }
    };
    // Preserve the real result before any fallible controller bookkeeping.
    *state = actual.state;
    if let Err(error) = gate.delivered(permit, actual.outputs.len()) {
        stop = Some(format!("host control accounting failed: {error}"));
    }
    if stop.is_some() {
        gate.terminate();
    }
    CommittedTurn {
        pending_outputs: actual.outputs,
        stop,
        capture_error,
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let model = RequestModel::fixed();
    // The checkpoint includes pending work and generation history. A production
    // oracle/composition wrapper must include its corresponding state here too.
    let mut state = model.initial_state()?;
    let mut capture = LiveBridge::attach(
        &check_initial(&model, &state)?,
        0,
        1,
        RunConfig::default(),
        RecorderOptions::default(),
        LiveLimits::default(),
    )?;
    let mut gate = LiveController::new(
        1,
        0,
        ControllerOptions {
            authority: ControlAuthority {
                pause_and_step: true,
                inject: false,
            },
            ..ControllerOptions::default()
        },
    )?;
    // This independent host observer records real synthetic boundaries. The
    // bridge/controller do not generate timings or dispatch these effects.
    let telemetry = EffectObserver::new(
        EffectOptions {
            origin: MeasurementOrigin::DebugControlledLive,
            gauge_scope: GaugeScope::CompleteParticipation,
            ..EffectOptions::default()
        },
        Arc::new(MonotonicClock::new(ClockDomain(1))),
    )
    .map_err(|error| format!("telemetry configuration: {error:?}"))?;
    let mut pending_completion: Option<(u8, Option<EffectHandle>, Option<DeliveryHandle>)> = None;
    let mut effect_ids = Vec::new();
    gate.request_pause(1, 0)?;
    let ack = gate.safe_point()?.expect("requested initial pause");
    telemetry.debugger_pause(true);
    println!("pause {} at revision {}", ack.generation, ack.revision);
    let mut dispatches = 0;
    let mut retained_on_stop = Vec::new();
    let mut stopped = None;
    for input in [Input::Start, Input::Cancel, Input::Complete(1)] {
        // This synthetic arrival stands for an application event/completion.
        // Workers and external arrivals may continue while delivery is paused.
        gate.external_arrival(input, std::mem::size_of::<Input>())
            .map_err(|rejected| rejected.error)?;
        let status = gate.status();
        gate.authorize_one(status.epoch, status.revision)?;
        telemetry.debugger_pause(false);
        let (permit, input) = gate.begin_next()?.expect("one buffered arrival");
        if let (Input::Complete(generation), Some((expected, _, Some(delivery)))) =
            (&input, &pending_completion)
            && generation == expected
        {
            let _ = telemetry.delivery_begun(delivery);
        }
        let actual = match model.step(&state, &input) {
            Ok(actual) => actual,
            Err(error) => {
                gate.delivery_failed(permit)?;
                return Err(error.into());
            }
        };
        // Observe immediately after the actual reducer, excluding checking and
        // exact recording. Diagnostic errors never skip or retry application work.
        if let (Input::Complete(generation), Some((expected, _, Some(delivery)))) =
            (&input, &pending_completion)
            && generation == expected
        {
            let disposition = match actual.disposition {
                stateless::Disposition::Accepted => DeliveryDisposition::Accepted,
                stateless::Disposition::Rejected(_) => DeliveryDisposition::Rejected,
                stateless::Disposition::Ignored(_) => DeliveryDisposition::Ignored,
            };
            let _ = telemetry.delivery_observed(delivery, disposition);
        }
        let sequence = status.revision + 1;
        // Request observation is the output boundary, before checker/recorder
        // work and independent of whether dispatch is eventually permitted.
        let requested_effects: Vec<_> = actual
            .outputs
            .iter()
            .enumerate()
            .map(|(index, _)| {
                telemetry
                    .requested(
                        RequestOrigin {
                            run: 1,
                            epoch: status.origin_epoch,
                            machine: 1,
                            transition_sequence: sequence,
                            output_index: index as u32,
                        },
                        Default::default(),
                    )
                    .ok()
            })
            .collect();
        let committed = commit_actual(
            &model,
            &mut state,
            &input,
            actual,
            permit,
            &mut gate,
            &mut capture,
        );
        if let Some(error) = committed.capture_error {
            // Loss of exact diagnostics never suppresses an authorized host action.
            eprintln!("exact capture stopped: {error}");
        }
        if let Some(reason) = committed.stop {
            retained_on_stop = committed.pending_outputs;
            stopped = Some(reason);
            break;
        }
        // The host chooses a post-dispatch safe point. To stop before dispatch,
        // call safe_point first; its acknowledgement honestly reports Staged.
        let mut pending_outputs = VecDeque::from(committed.pending_outputs);
        let mut requested_effects = requested_effects.into_iter();
        let mut index = 0;
        while !pending_outputs.is_empty() {
            let dispatch = match gate.take_dispatch(index) {
                Ok(permit) => permit,
                Err(error) => {
                    gate.terminate();
                    stopped = Some(format!("dispatch admission stopped: {error}"));
                    break;
                }
            };
            let output = pending_outputs.pop_front().expect("pending output");
            let effect = requested_effects.next().flatten();
            let origin = dispatch.origin();
            let request_origin = RequestOrigin {
                run: 1,
                epoch: origin.epoch,
                machine: 1,
                transition_sequence: origin.transition,
                output_index: u32::try_from(origin.output_index)?,
            };
            if let Some(effect) = &effect {
                let _ = telemetry.admission(effect, Admission::Accepted);
            }
            let attempt = effect
                .as_ref()
                .and_then(|effect| telemetry.attempt_created(effect).ok());
            if let Some(attempt) = &attempt {
                let _ = telemetry.ready_queued(attempt);
                let _ = telemetry.attempt_started(attempt);
            }
            // This is the real synthetic host action, once. A production adapter
            // substitutes its dispatcher here; telemetry errors never gate it.
            println!("host dispatches {output:?}");
            dispatches += 1;
            if let Some(attempt) = &attempt {
                let _ = telemetry.attempt_finished(attempt, RuntimeOutcome::Success);
            }
            if let Some(effect) = &effect {
                let _ = telemetry.resolved(effect, RuntimeOutcome::Success, attempt.as_ref());
                effect_ids.push(effect.id());
                assert_eq!(
                    telemetry.effect_details(effect.id()).unwrap().origin,
                    request_origin
                );
            }
            if let Err(error) = capture.effect_dispatched(origin) {
                eprintln!("dispatch fact unavailable for {origin:?}: {error}");
            }
            match output {
                Output::Request(generation) => {
                    // Reserve under the synthetic queue's exclusive owner before
                    // publishing completion availability. Missing telemetry never
                    // prevents the real completion from becoming available.
                    let delivery = effect
                        .as_ref()
                        .and_then(|effect| telemetry.reserve_publication(effect).ok());
                    pending_completion = Some((generation, effect, delivery));
                    if let Some((_, _, Some(delivery))) = &pending_completion {
                        let _ = telemetry.publication_committed(delivery);
                    }
                }
                Output::Release(generation) => {
                    if let Some((expected, Some(effect), _)) = &pending_completion
                        && generation == *expected
                    {
                        // Explicit application-specific cleanup acknowledgement;
                        // reducer acceptance alone is never interpreted as settle.
                        let _ = telemetry.settled(effect);
                    }
                }
                Output::Publish(_) => {}
            }
            // Account only after the actual action/publication. A bookkeeping
            // error cannot erase that fact or cause it to be attempted twice.
            if let Err(error) = gate.dispatched(dispatch) {
                gate.terminate();
                stopped = Some(format!("dispatch accounting stopped: {error}"));
                break;
            }
            index += 1;
        }
        if stopped.is_some() {
            retained_on_stop = pending_outputs.into();
            break;
        }
        let ack = gate.safe_point()?.expect("one turn pauses again");
        telemetry.debugger_pause(true);
        println!(
            "pause {} at revision {}, effects {:?}",
            ack.generation, ack.revision, ack.effect_phase
        );
    }
    if let Some(reason) = stopped {
        // The synthetic harness ends here. A real host persists or explicitly
        // rejects this owned queue under its application shutdown policy.
        return Err(format!(
            "{reason}; {} undispatched outputs retained",
            retained_on_stop.len()
        )
        .into());
    }
    let metric_snapshot = telemetry.metric_snapshot();
    assert!(
        metric_snapshot
            .series
            .iter()
            .any(|(key, value)| key.family == MetricFamily::EndToEnd
                && key.labels.debugger_affected
                && matches!(value, MetricValue::Histogram(histogram) if histogram.count == 1))
    );
    assert!(
        metric_snapshot.production().series.is_empty(),
        "controlled measurements are separate from production latency"
    );
    assert_eq!(effect_ids.len(), 2);
    let recorded_origins: Vec<_> = std::iter::from_fn(|| capture.pop_event())
        .filter_map(|event| {
            if let statelessness_debug::live::LiveEventKind::EffectDispatched { origin } =
                event.kind
            {
                Some(origin)
            } else {
                None
            }
        })
        .collect();
    for (effect_id, origin) in effect_ids.iter().zip(recorded_origins.iter()) {
        let details = telemetry.effect_details(*effect_id).unwrap();
        assert_eq!(details.origin.epoch, origin.epoch);
        assert_eq!(details.origin.transition_sequence, origin.transition);
        assert_eq!(details.origin.output_index as usize, origin.output_index);
    }
    assert_eq!(recorded_origins.len(), 2);
    let trace = capture.export();
    let report = replay(&model, &trace, ReplayOptions::default())?;
    println!(
        "{} actual host dispatches; {} recorded turns replayed without dispatch",
        dispatches, report.steps_verified
    );
    assert_eq!(dispatches, 2);
    assert_eq!(report.steps_verified, 3);
    // Reading/replaying model evidence does not ingest another actual effect.
    let after_replay = telemetry.metric_snapshot();
    assert_eq!(metric_snapshot.series, after_replay.series);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use stateless::{Check, ModelError, ModelMetadata};
    use statelessness_debug::live::{ControllerPhase, DetachPolicy};

    #[derive(Clone, Copy)]
    enum Fault {
        None,
        Checker,
        Property,
        Codec,
    }
    struct Host {
        fault: Fault,
        outputs: usize,
    }
    impl Model for Host {
        type State = u8;
        type Input = ();
        type Output = u8;
        fn metadata(&self) -> ModelMetadata {
            ModelMetadata {
                name: "live-host-error-fixture".into(),
                model_version: 1,
                properties_version: 1,
                codec_version: 1,
                build: "test".into(),
            }
        }
        fn initial_state(&self) -> Result<u8, ModelError> {
            Ok(0)
        }
        fn step(&self, before: &u8, _: &()) -> Result<Transition<u8, u8>, ModelError> {
            Ok(Transition::accepted(before + 1, vec![7; self.outputs]))
        }
        fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
            if *state != 0 {
                match self.fault {
                    Fault::Checker => return Err(ModelError::new("injected checker error")),
                    Fault::Property => {
                        return Ok(vec![Check::failed(
                            "host-policy",
                            "injected property failure",
                        )]);
                    }
                    _ => {}
                }
            }
            Ok(vec![])
        }
    }
    impl ModelCodec for Host {
        fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
            if *state != 0 && matches!(self.fault, Fault::Codec) {
                return Err(ModelError::new("injected codec error"));
            }
            Ok(vec![*state])
        }
        fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
            match bytes {
                [state] => Ok(*state),
                _ => Err(ModelError::new("invalid state")),
            }
        }
        fn encode_input(&self, _: &()) -> Result<Vec<u8>, ModelError> {
            Ok(vec![])
        }
        fn decode_input(&self, _: &[u8]) -> Result<(), ModelError> {
            Ok(())
        }
        fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
            Ok(vec![*output])
        }
    }
    fn capture(model: &Host) -> LiveBridge<Host> {
        LiveBridge::attach(
            &check_initial(model, &0).unwrap(),
            0,
            1,
            RunConfig::default(),
            RecorderOptions::default(),
            LiveLimits::default(),
        )
        .unwrap()
    }
    #[test]
    fn checker_property_and_output_limit_stops_retain_actual_result_without_dispatch() {
        for (fault, max_outputs) in [(Fault::Checker, 2), (Fault::Property, 2), (Fault::None, 1)] {
            let model = Host { fault, outputs: 2 };
            let mut capture = capture(&model);
            let mut gate = LiveController::new(
                1,
                0,
                ControllerOptions {
                    max_outputs,
                    ..Default::default()
                },
            )
            .unwrap();
            gate.external_arrival((), 0).unwrap();
            let (permit, input) = gate.begin_next().unwrap().unwrap();
            let mut state = 0;
            let actual = model.step(&state, &input).unwrap();
            let committed = commit_actual(
                &model,
                &mut state,
                &input,
                actual,
                permit,
                &mut gate,
                &mut capture,
            );
            assert_eq!(state, 1);
            assert_eq!(committed.pending_outputs, vec![7, 7]);
            assert!(committed.stop.is_some());
            assert_eq!(gate.status().revision, 1);
            assert_eq!(gate.status().phase, ControllerPhase::Terminated);
            assert!(matches!(gate.take_dispatch(0), Err(LiveError::WrongPhase)));
            assert!(
                !std::iter::from_fn(|| capture.pop_event()).any(|event| matches!(
                    event.kind,
                    statelessness_debug::live::LiveEventKind::EffectDispatched { .. }
                ))
            );
        }
    }
    #[test]
    fn codec_failure_keeps_authorized_dispatch_and_single_actual_fact() {
        let model = Host {
            fault: Fault::Codec,
            outputs: 1,
        };
        let mut capture = capture(&model);
        let mut gate = LiveController::new(1, 0, ControllerOptions::default()).unwrap();
        gate.external_arrival((), 0).unwrap();
        let (permit, input) = gate.begin_next().unwrap().unwrap();
        let mut state = 0;
        let actual = model.step(&state, &input).unwrap();
        let committed = commit_actual(
            &model,
            &mut state,
            &input,
            actual,
            permit,
            &mut gate,
            &mut capture,
        );
        assert_eq!(state, 1);
        assert_eq!(committed.pending_outputs, vec![7]);
        assert!(committed.stop.is_none());
        assert_eq!(committed.capture_error, Some(LiveError::RecordingFailed));
        let permit = gate.take_dispatch(0).unwrap();
        let origin = permit.origin();
        let dispatches = 1;
        gate.dispatched(permit).unwrap();
        capture.effect_dispatched(origin).unwrap();
        assert_eq!(capture.effect_dispatched(origin), Err(LiveError::Duplicate));
        assert_eq!(dispatches, 1);
        assert_eq!(
            std::iter::from_fn(|| capture.pop_event())
                .filter(|event| matches!(
                    event.kind,
                    statelessness_debug::live::LiveEventKind::EffectDispatched { .. }
                ))
                .count(),
            1
        );
    }
    #[test]
    fn detach_termination_preserves_completed_state_and_staged_output() {
        let model = Host {
            fault: Fault::None,
            outputs: 1,
        };
        let mut capture = capture(&model);
        let mut gate = LiveController::new(
            1,
            0,
            ControllerOptions {
                detach: DetachPolicy::Terminate,
                ..Default::default()
            },
        )
        .unwrap();
        gate.external_arrival((), 0).unwrap();
        let (permit, input) = gate.begin_next().unwrap().unwrap();
        gate.disconnect();
        let mut state = 0;
        let actual = model.step(&state, &input).unwrap();
        let committed = commit_actual(
            &model,
            &mut state,
            &input,
            actual,
            permit,
            &mut gate,
            &mut capture,
        );
        assert_eq!(state, 1);
        assert_eq!(committed.pending_outputs, vec![7]);
        assert_eq!(gate.status().phase, ControllerPhase::Terminated);
        assert!(matches!(gate.take_dispatch(0), Err(LiveError::WrongPhase)));
        assert_eq!(capture.health().recorded_steps, 1);
    }
}
