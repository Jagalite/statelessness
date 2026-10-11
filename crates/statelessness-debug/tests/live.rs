use stateless::execution::{CheckPolicy, ReplayOptions, check_initial, check_turn, replay};
use stateless::monitor::RecorderOptions;
use stateless::trace::{RunConfig, Termination};
use stateless::{
    Check, Disposition, Model, ModelCodec, ModelError, ModelMetadata, Transition, TransitionRef,
};
use statelessness_debug::live::*;
use std::cell::Cell;
use std::num::NonZeroU64;

#[derive(Default)]
struct HostModel {
    steps: Cell<usize>,
    states: Cell<usize>,
    transitions: Cell<usize>,
    version: Cell<u32>,
}
impl Model for HostModel {
    type State = u8;
    type Input = i8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "synthetic-live-host".into(),
            model_version: self.version.get(),
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, before: &u8, input: &i8) -> Result<Transition<u8, ()>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        Ok(match input {
            0 => Transition {
                state: *before,
                outputs: vec![],
                disposition: Disposition::Rejected("rejected".into()),
            },
            2 => Transition {
                state: *before,
                outputs: vec![],
                disposition: Disposition::Ignored("ignored".into()),
            },
            9 => Transition::accepted(9, vec![()]),
            _ => Transition::accepted(before.wrapping_add_signed(*input), vec![()]),
        })
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        self.states.set(self.states.get() + 1);
        Ok(vec![if *state == 9 {
            Check::failed("not-nine", "synthetic failure")
        } else {
            Check::passed("not-nine")
        }])
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &i8,
        _: &TransitionRef<'_, u8, ()>,
    ) -> Result<Vec<Check>, ModelError> {
        self.transitions.set(self.transitions.get() + 1);
        Ok(vec![Check::passed("host-turn")])
    }
}
impl ModelCodec for HostModel {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [value] => Ok(*value),
            _ => Err(ModelError::new("bad state")),
        }
    }
    fn encode_input(&self, input: &i8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input as u8])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<i8, ModelError> {
        self.decode_state(bytes).map(|v| v as i8)
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
}
fn bridge(
    model: &HostModel,
    initial: u8,
    origin: u64,
    opts: RecorderOptions,
    limits: LiveLimits,
) -> LiveBridge<HostModel> {
    LiveBridge::attach(
        &check_initial(model, &initial).unwrap(),
        origin,
        1,
        RunConfig::default(),
        opts,
        limits,
    )
    .unwrap()
}
fn observe(
    model: &HostModel,
    bridge: &mut LiveBridge<HostModel>,
    state: &mut u8,
    input: i8,
    sequence: u64,
) -> Result<(), LiveError> {
    let actual = model.step(state, &input).unwrap();
    let checked = check_turn(
        model,
        state,
        &input,
        &actual,
        sequence,
        CheckPolicy::default(),
    )
    .unwrap();
    let result = bridge.observe(sequence, &checked);
    *state = actual.state;
    result
}
fn controller(detach: DetachPolicy, overflow: OverflowPolicy) -> LiveController<i8> {
    LiveController::new(
        1,
        0,
        ControllerOptions {
            authority: ControlAuthority {
                pause_and_step: true,
                inject: true,
            },
            detach,
            overflow,
            max_arrivals: 2,
            max_arrival_bytes: 4,
            max_outputs: 2,
        },
    )
    .unwrap()
}
fn pause(control: &mut LiveController<i8>) {
    let s = control.status();
    control.request_pause(s.epoch, s.revision).unwrap();
    control.safe_point().unwrap().unwrap();
}
fn deliver(control: &mut LiveController<i8>, input: i8, outputs: usize) {
    control.external_arrival(input, 1).unwrap();
    let (permit, actual) = control.begin_next().unwrap().unwrap();
    assert_eq!(actual, input);
    control.delivered(permit, outputs).unwrap();
}

#[test]
fn coherent_midrun_attach_shares_one_checked_batch_and_exports_exact_suffix() {
    let model = HostModel::default();
    let mut state = 4;
    let mut capture = bridge(
        &model,
        state,
        20,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    for (i, input) in [1, 0, 2, -1].into_iter().enumerate() {
        observe(&model, &mut capture, &mut state, input, 21 + i as u64).unwrap();
    }
    assert_eq!(
        (
            model.steps.get(),
            model.states.get(),
            model.transitions.get()
        ),
        (4, 5, 4)
    );
    assert_eq!(capture.health().host_sequence, 24);
    assert_eq!(capture.health().attached_after, 20);
    let trace = capture.export();
    assert_eq!(trace.steps.len(), 4);
    assert_eq!(trace.initial_state, vec![4]);
    assert!(
        trace
            .config
            .parameters
            .iter()
            .any(|(k, v)| k == "stateless.monitor.sequence_origin" && v == "20")
    );
    assert_eq!(
        replay(&model, &trace, ReplayOptions::default())
            .unwrap()
            .steps_verified,
        4
    );
}

#[test]
fn omitted_roundtrip_freezes_before_gap_even_if_before_state_matches() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    let mut state = 0;
    observe(&model, &mut capture, &mut state, 0, 1).unwrap();
    state = model.step(&state, &1).unwrap().state;
    state = model.step(&state, &-1).unwrap().state;
    assert_eq!(state, 0);
    assert_eq!(
        observe(&model, &mut capture, &mut state, 1, 4),
        Err(LiveError::Gap {
            expected: 2,
            actual: 4
        })
    );
    observe(&model, &mut capture, &mut state, 1, 5).unwrap();
    let trace = capture.export();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    assert!(capture.health().recorder_frozen);
    assert!(!capture.health().capture_complete);
    assert_eq!(capture.health().host_sequence, 5);
    assert_eq!(
        replay(&model, &trace, ReplayOptions::default())
            .unwrap()
            .steps_verified,
        1
    );
}

#[test]
fn property_failure_freezes_capture_without_stopping_host_or_rewriting_failure() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    let mut state = 0;
    observe(&model, &mut capture, &mut state, 9, 1).unwrap();
    observe(&model, &mut capture, &mut state, 1, 2).unwrap();
    capture.stop_recording(ModelError::new("later host failure"));
    assert_eq!(state, 10);
    assert_eq!(capture.health().host_sequence, 2);
    assert_eq!(capture.health().recorded_steps, 1);
    assert_eq!(capture.export().termination, Termination::PropertyFailed);
    assert!(
        replay(&model, &capture.export(), ReplayOptions::default())
            .unwrap()
            .failure_reproduced
    );
}

#[test]
fn metadata_loss_is_separate_from_exact_retention_and_effect_dispatch_facts() {
    let model = HostModel::default();
    let options = RecorderOptions {
        max_steps: 2,
        ..RecorderOptions::default()
    };
    let limits = LiveLimits {
        max_events: 1,
        max_event_bytes: std::mem::size_of::<LiveEvent>(),
        max_effect_origins: 2,
    };
    let mut capture = bridge(&model, 0, 0, options, limits);
    let mut state = 0;
    for sequence in 1..=4 {
        observe(&model, &mut capture, &mut state, 1, sequence).unwrap();
    }
    let health = capture.health();
    assert!(health.capture_complete);
    assert_eq!(
        (
            health.recorded_steps,
            health.retained_steps,
            health.evicted_steps
        ),
        (4, 2, 2)
    );
    assert_eq!((health.event_drops, health.origin_evictions), (4, 2));
    let origin = EffectOrigin {
        epoch: 1,
        transition: 4,
        output_index: 0,
    };
    capture.pop_event();
    capture.effect_dispatched(origin).unwrap();
    assert_eq!(capture.effect_dispatched(origin), Err(LiveError::Duplicate));
    assert_eq!(
        capture.effect_dispatched(EffectOrigin {
            transition: 1,
            ..origin
        }),
        Err(LiveError::UnknownEffect)
    );
    assert_eq!(
        capture.effect_dispatched(EffectOrigin { epoch: 2, ..origin }),
        Err(LiveError::WrongEpoch)
    );
    assert!(matches!(
        capture.pop_event().unwrap().kind,
        LiveEventKind::EffectDispatched { .. }
    ));
    assert_eq!(
        replay(&model, &capture.export(), ReplayOptions::default())
            .unwrap()
            .steps_verified,
        2
    );
}

#[test]
fn identity_changes_are_rejected_even_after_capture_freezes() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        9,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    model.version.set(7);
    assert_eq!(
        observe(&model, &mut capture, &mut 9, 0, 1),
        Err(LiveError::IdentityChanged)
    );
    assert_eq!(capture.health().host_sequence, 0);
    assert_eq!(capture.export().termination, Termination::PropertyFailed);
}

#[test]
fn oversized_output_batch_has_capacity_bounded_diagnostic_work() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits {
            max_effect_origins: 2,
            ..LiveLimits::default()
        },
    );
    // ZSTs let this exercise a genuinely enormous borrowed output count without
    // allocating a huge application payload or relying on a timing assertion.
    let actual = Transition::accepted(1, vec![(); usize::MAX]);
    let checked = check_turn(&model, &0, &1, &actual, 1, CheckPolicy::default()).unwrap();
    assert_eq!(
        capture.observe(1, &checked),
        Err(LiveError::RecordingFailed)
    );
    assert_eq!(capture.health().recorded_steps, 0);
    assert_eq!(
        capture.health().origin_evictions,
        u64::try_from(usize::MAX - 2).unwrap()
    );
    capture
        .effect_dispatched(EffectOrigin {
            epoch: 1,
            transition: 1,
            output_index: usize::MAX - 1,
        })
        .unwrap();
    assert_eq!(
        capture.effect_dispatched(EffectOrigin {
            epoch: 1,
            transition: 1,
            output_index: 0
        }),
        Err(LiveError::UnknownEffect)
    );
}

#[test]
fn duplicate_and_mismatched_tokens_do_not_consume_a_delivery() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    let actual = model.step(&0, &1).unwrap();
    let checked = check_turn(&model, &0, &1, &actual, 1, CheckPolicy::default()).unwrap();
    assert_eq!(
        capture.observe(2, &checked),
        Err(LiveError::SequenceMismatch)
    );
    capture.observe(1, &checked).unwrap();
    assert_eq!(capture.observe(1, &checked), Err(LiveError::Duplicate));
    assert_eq!(capture.health().recorded_steps, 1);
    assert_eq!(capture.health().duplicate_observations, 1);
}

#[test]
fn sampled_checks_end_exact_capture_and_preserve_actual_host_transition() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    let actual = model.step(&0, &1).unwrap();
    let checked = check_turn(
        &model,
        &0,
        &1,
        &actual,
        1,
        CheckPolicy {
            state_every: NonZeroU64::new(2).unwrap(),
            transition_checks: true,
        },
    )
    .unwrap();
    assert_eq!(
        capture.observe(1, &checked),
        Err(LiveError::RecordingFailed)
    );
    assert_eq!(actual.state, 1);
    assert!(matches!(
        capture.export().termination,
        Termination::ModelError(_)
    ));
}

#[test]
fn one_turn_is_exactly_one_delivered_input_including_rejection_and_ignore() {
    let mut c = controller(DetachPolicy::RemainPaused, OverflowPolicy::Backpressure);
    pause(&mut c);
    c.external_arrival(0, 1).unwrap();
    c.external_arrival(2, 1).unwrap();
    for revision in 0..2 {
        c.authorize_one(1, revision).unwrap();
        let (permit, _) = c.begin_next().unwrap().unwrap();
        assert!(matches!(c.begin_next(), Err(LiveError::WrongPhase)));
        assert!(matches!(c.safe_point(), Err(LiveError::WrongPhase)));
        c.delivered(permit, 0).unwrap();
        assert_eq!(c.status().phase, ControllerPhase::PauseRequested);
        let ack = c.safe_point().unwrap().unwrap();
        assert_eq!(ack.revision, revision + 1);
        assert_eq!(ack.effect_phase, EffectPhase::Dispatched);
        assert!(matches!(c.begin_next(), Err(LiveError::WrongPhase)));
    }
}

#[test]
fn staged_effects_can_resume_or_precede_one_turn_without_duplicate_dispatch() {
    let mut c = controller(DetachPolicy::RemainPaused, OverflowPolicy::Backpressure);
    deliver(&mut c, 1, 2);
    pause(&mut c);
    assert_eq!(c.status().effect_phase, EffectPhase::Staged);
    c.authorize_one(1, 1).unwrap();
    let first = c.take_dispatch(0).unwrap();
    assert_eq!(c.status().effect_phase, EffectPhase::Dispatching);
    assert!(matches!(c.take_dispatch(0), Err(LiveError::Duplicate)));
    assert!(matches!(c.begin_next(), Err(LiveError::WrongPhase)));
    c.dispatched(first).unwrap();
    assert_eq!(c.status().effect_phase, EffectPhase::Staged);
    let second = c.take_dispatch(1).unwrap();
    c.dispatched(second).unwrap();
    c.external_arrival(0, 1).unwrap();
    let (permit, _) = c.begin_next().unwrap().unwrap();
    c.delivered(permit, 0).unwrap();
    assert_eq!(c.safe_point().unwrap().unwrap().revision, 2);
}

#[test]
fn pause_racing_with_delivery_or_dispatch_waits_for_a_complete_boundary() {
    let mut c = controller(DetachPolicy::RemainPaused, OverflowPolicy::Backpressure);
    c.external_arrival(1, 1).unwrap();
    let (permit, _) = c.begin_next().unwrap().unwrap();
    c.request_pause(1, 0).unwrap();
    assert_eq!(c.safe_point(), Err(LiveError::WrongPhase));
    c.delivered(permit, 1).unwrap();
    let output = c.take_dispatch(0).unwrap();
    assert_eq!(c.safe_point(), Err(LiveError::WrongPhase));
    c.dispatched(output).unwrap();
    assert_eq!(
        c.safe_point().unwrap().unwrap().effect_phase,
        EffectPhase::Dispatched
    );
}

#[test]
fn disconnect_preserves_inflight_result_under_every_detach_policy() {
    for detach in [
        DetachPolicy::RemainPaused,
        DetachPolicy::Resume,
        DetachPolicy::Terminate,
    ] {
        let mut c = controller(detach, OverflowPolicy::Backpressure);
        c.external_arrival(0, 1).unwrap();
        let (permit, _) = c.begin_next().unwrap().unwrap();
        c.disconnect();
        c.disconnect();
        assert_eq!(c.reconnect(), Err(LiveError::WrongPhase));
        c.delivered(permit, 0).unwrap();
        assert_eq!(c.status().revision, 1);
        match detach {
            DetachPolicy::RemainPaused => {
                assert_eq!(c.status().phase, ControllerPhase::PauseRequested);
                c.safe_point().unwrap();
                assert_eq!(c.reconnect(), Ok(2));
            }
            DetachPolicy::Resume => {
                assert_eq!(c.status().phase, ControllerPhase::Running);
                assert_eq!(c.reconnect(), Ok(2));
            }
            DetachPolicy::Terminate => {
                assert_eq!(c.status().phase, ControllerPhase::Terminated);
                assert_eq!(c.reconnect(), Err(LiveError::WrongPhase));
            }
        }
    }
}

#[test]
fn reconnect_retains_dispatch_ownership_and_invalidates_old_commands() {
    let mut c = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    deliver(&mut c, 1, 2);
    let first = c.take_dispatch(0).unwrap();
    c.disconnect();
    assert_eq!(c.reconnect(), Err(LiveError::WrongPhase));
    c.dispatched(first).unwrap();
    assert_eq!(c.reconnect(), Ok(2));
    assert_eq!(c.request_pause(1, 1), Err(LiveError::WrongEpoch));
    assert_eq!(
        c.request_pause(2, 0),
        Err(LiveError::StaleRevision {
            expected: 0,
            actual: 1
        })
    );
    assert!(matches!(c.take_dispatch(0), Err(LiveError::Duplicate)));
    let second = c.take_dispatch(1).unwrap();
    assert_eq!(second.epoch(), 1); // Origin belongs to the completed turn.
    c.dispatched(second).unwrap();
}

#[test]
fn admission_backpressure_and_disconnect_return_owned_input_and_bound_queue() {
    for policy in [OverflowPolicy::Backpressure, OverflowPolicy::Disconnect] {
        let mut c = controller(DetachPolicy::RemainPaused, policy);
        pause(&mut c);
        c.external_arrival(1, 3).unwrap();
        let rejected = c.external_arrival(2, 2).unwrap_err();
        assert_eq!((rejected.error, rejected.input), (LiveError::Overflow, 2));
        assert_eq!(
            (c.status().buffered_arrivals, c.status().buffered_bytes),
            (1, 3)
        );
        assert_eq!(c.status().overflow_count, 1);
        assert_eq!(c.status().connected, policy == OverflowPolicy::Backpressure);
    }
}

#[test]
fn default_observation_authority_cannot_escalate_on_reconnect() {
    let mut c = LiveController::new(1, 0, ControllerOptions::default()).unwrap();
    assert_eq!(c.request_pause(1, 0), Err(LiveError::Unauthorized));
    assert_eq!(
        c.inject(1, 0, 0, 1).unwrap_err().error,
        LiveError::Unauthorized
    );
    c.disconnect();
    c.safe_point().unwrap();
    c.reconnect().unwrap();
    assert_eq!(c.authorize_one(2, 0), Err(LiveError::Unauthorized));
    assert_eq!(c.resume(2, 0), Err(LiveError::Unauthorized));
    assert_eq!(
        c.inject(2, 0, 0, 1).unwrap_err().error,
        LiveError::Unauthorized
    );
}

#[test]
fn permits_are_bound_to_their_controller_and_uncertain_work_is_not_retried() {
    let mut a = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    let mut b = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    a.external_arrival(0, 1).unwrap();
    b.external_arrival(0, 1).unwrap();
    let (ap, _) = a.begin_next().unwrap().unwrap();
    let (bp, _) = b.begin_next().unwrap().unwrap();
    assert_eq!(a.delivered(bp, 0), Err(LiveError::WrongPhase));
    a.delivered(ap, 1).unwrap();
    let dispatch = a.take_dispatch(0).unwrap();
    a.dispatch_failed(dispatch).unwrap();
    a.disconnect();
    assert_eq!(a.reconnect(), Err(LiveError::WrongPhase));
    assert!(matches!(a.take_dispatch(0), Err(LiveError::WrongPhase)));
    assert_eq!(b.status().phase, ControllerPhase::Delivering);
}

#[test]
fn oversized_outputs_and_host_failure_terminate_without_hidden_retry() {
    let mut c = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    c.external_arrival(0, 1).unwrap();
    let (permit, _) = c.begin_next().unwrap().unwrap();
    assert_eq!(c.delivered(permit, usize::MAX), Err(LiveError::Overflow));
    assert_eq!(c.status().revision, 1);
    assert_eq!(c.status().phase, ControllerPhase::Terminated);
    assert_eq!(c.status().effect_phase, EffectPhase::Staged);
    let mut c = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    c.external_arrival(0, 1).unwrap();
    let (permit, _) = c.begin_next().unwrap().unwrap();
    c.delivery_failed(permit).unwrap();
    c.disconnect();
    assert_eq!(c.reconnect(), Err(LiveError::WrongPhase));
    assert_eq!(c.status().revision, 0);
}

// A separately represented finite reference machine. Statelessness explores its
// reachable graph; every edge is also replayed against a fresh real controller.
// Histories are witnesses, not part of equality, so interleaving cycles close.
mod exhaustive_controller {
    use super::*;
    use stateless::Enumerate;
    use stateless::explore::{SearchConfig, SearchTermination, enumerate};
    use std::hash::{Hash, Hasher};

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum Op {
        Pause,
        Boundary,
        One,
        Resume,
        Arrival,
        Inject,
        Begin,
        Zero,
        Output,
        Fail,
        Reserve,
        Dispatch,
        DispatchFail,
        Disconnect,
        Reconnect,
        OldRevision,
        WrongEpoch,
        Terminate,
    }
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
    enum Output {
        None,
        Waiting,
        Reserved,
        Sent,
    }
    #[derive(Clone, Debug, PartialEq, Eq, Hash)]
    struct Spec {
        connected: bool,
        epoch: u64,
        revision: u64,
        paused: bool,
        stopping: bool,
        ticket: bool,
        busy: bool,
        stop_after: bool,
        dead: bool,
        kill_after: bool,
        queued: usize,
        output: Output,
        output_epoch: u64,
        dispatch_permit: bool,
        generations: u64,
    }
    impl Spec {
        fn initial() -> Self {
            Self {
                connected: true,
                epoch: 1,
                revision: 0,
                paused: false,
                stopping: false,
                ticket: false,
                busy: false,
                stop_after: false,
                dead: false,
                kill_after: false,
                queued: 0,
                output: Output::None,
                output_epoch: 1,
                dispatch_permit: false,
                generations: 0,
            }
        }
        fn phase(&self) -> ControllerPhase {
            if self.dead {
                ControllerPhase::Terminated
            } else if self.busy {
                ControllerPhase::Delivering
            } else if self.paused {
                ControllerPhase::Paused
            } else if self.stopping {
                ControllerPhase::PauseRequested
            } else if self.ticket {
                ControllerPhase::OneTurnAuthorized
            } else {
                ControllerPhase::Running
            }
        }
        fn effects(&self) -> EffectPhase {
            match self.output {
                Output::None | Output::Sent => EffectPhase::Dispatched,
                Output::Waiting => EffectPhase::Staged,
                Output::Reserved => EffectPhase::Dispatching,
            }
        }
        fn command(&self, authority: bool) -> Result<(), LiveError> {
            if !self.connected {
                Err(LiveError::Disconnected)
            } else if !authority {
                Err(LiveError::Unauthorized)
            } else {
                Ok(())
            }
        }
        fn disconnect(&mut self, detach: DetachPolicy) {
            if !self.connected {
                return;
            }
            self.connected = false;
            match detach {
                DetachPolicy::Terminate => {
                    self.kill_after = true;
                    if !self.busy {
                        self.dead = true;
                    }
                }
                DetachPolicy::RemainPaused => {
                    if self.busy {
                        self.stop_after = true;
                    } else if !self.dead && !self.paused {
                        self.stopping = true;
                        self.ticket = false;
                    }
                }
                DetachPolicy::Resume => {
                    if self.busy {
                        self.stop_after = false;
                    } else if !self.dead {
                        self.paused = false;
                        self.stopping = false;
                        self.ticket = false;
                    }
                }
            }
        }
        fn apply(&mut self, op: Op, model: &ControlModel) -> Result<(), LiveError> {
            match op {
                Op::Pause => {
                    self.command(model.authority.pause_and_step)?;
                    if self.dead {
                        return Err(LiveError::WrongPhase);
                    }
                    if self.busy {
                        self.stop_after = true;
                    } else if self.ticket {
                        self.ticket = false;
                        self.stopping = true;
                    } else if !self.paused {
                        self.stopping = true;
                    }
                }
                Op::Boundary => {
                    if self.output == Output::Reserved || self.dead || self.busy {
                        return Err(LiveError::WrongPhase);
                    }
                    if self.stopping {
                        self.stopping = false;
                        self.paused = true;
                        self.generations += 1;
                    }
                }
                Op::One => {
                    self.command(model.authority.pause_and_step)?;
                    if !self.paused || self.dead {
                        return Err(LiveError::WrongPhase);
                    }
                    self.paused = false;
                    self.ticket = true;
                }
                Op::Resume => {
                    self.command(model.authority.pause_and_step)?;
                    if self.dead || self.busy || !(self.paused || self.stopping || self.ticket) {
                        return Err(LiveError::WrongPhase);
                    }
                    self.paused = false;
                    self.stopping = false;
                    self.ticket = false;
                }
                Op::Arrival | Op::Inject => {
                    if op == Op::Inject {
                        self.command(model.authority.inject)?;
                    }
                    if self.dead {
                        return Err(LiveError::WrongPhase);
                    }
                    if self.queued == 2 {
                        if model.overflow == OverflowPolicy::Disconnect {
                            self.disconnect(model.detach);
                        }
                        return Err(LiveError::Overflow);
                    }
                    self.queued += 1;
                }
                Op::Begin => {
                    if self.dead
                        || self.busy
                        || self.paused
                        || self.stopping
                        || self.effects() != EffectPhase::Dispatched
                    {
                        return Err(LiveError::WrongPhase);
                    }
                    if self.queued != 0 {
                        self.queued -= 1;
                        self.busy = true;
                        self.stop_after = self.ticket;
                        self.ticket = false;
                    }
                }
                Op::Zero | Op::Output => {
                    if !self.busy || self.dead {
                        return Err(LiveError::WrongPhase);
                    }
                    self.busy = false;
                    self.revision += 1;
                    self.output = if op == Op::Zero {
                        Output::None
                    } else {
                        Output::Waiting
                    };
                    self.dead = self.kill_after;
                    self.stopping = self.stop_after;
                    self.stop_after = false;
                }
                Op::Fail => {
                    if !self.busy || self.dead {
                        return Err(LiveError::WrongPhase);
                    }
                    self.busy = false;
                    self.dead = true;
                }
                Op::Reserve => {
                    if self.dead || self.paused || self.busy {
                        return Err(LiveError::WrongPhase);
                    }
                    match self.output {
                        Output::None => return Err(LiveError::UnknownEffect),
                        Output::Reserved | Output::Sent => return Err(LiveError::Duplicate),
                        Output::Waiting => {
                            self.output = Output::Reserved;
                            self.dispatch_permit = true;
                        }
                    }
                }
                Op::Dispatch | Op::DispatchFail => {
                    if !self.dispatch_permit {
                        return Err(LiveError::WrongPhase);
                    }
                    self.dispatch_permit = false;
                    if op == Op::Dispatch {
                        self.output = Output::Sent;
                    } else {
                        self.dead = true;
                    }
                }
                Op::Terminate => {
                    self.kill_after = true;
                    if !self.busy {
                        self.dead = true;
                    }
                }
                Op::Disconnect => self.disconnect(model.detach),
                Op::Reconnect => {
                    if self.connected || self.busy || self.dead || self.output == Output::Reserved {
                        return Err(LiveError::WrongPhase);
                    }
                    self.epoch += 1;
                    self.connected = true;
                }
                Op::OldRevision => {
                    if !self.connected {
                        return Err(LiveError::Disconnected);
                    }
                    return Err(LiveError::StaleRevision {
                        expected: self.revision + 1,
                        actual: self.revision,
                    });
                }
                Op::WrongEpoch => {
                    return Err(if self.connected {
                        LiveError::WrongEpoch
                    } else {
                        LiveError::Disconnected
                    });
                }
            }
            Ok(())
        }
    }
    struct Harness {
        control: LiveController<i8>,
        delivery: Option<DeliveryPermit>,
        dispatch: Option<DispatchPermit>,
        overflow_count: u64,
    }
    impl Harness {
        fn new(model: &ControlModel) -> Self {
            Self {
                control: LiveController::new(
                    1,
                    0,
                    ControllerOptions {
                        authority: model.authority,
                        detach: model.detach,
                        overflow: model.overflow,
                        max_arrivals: 2,
                        max_arrival_bytes: 2,
                        max_outputs: 1,
                    },
                )
                .unwrap(),
                delivery: None,
                dispatch: None,
                overflow_count: 0,
            }
        }
        fn apply(&mut self, op: Op) -> Result<(), LiveError> {
            let s = self.control.status();
            match op {
                Op::Pause => self.control.request_pause(s.epoch, s.revision),
                Op::Boundary => self.control.safe_point().map(|_| ()),
                Op::One => self.control.authorize_one(s.epoch, s.revision),
                Op::Resume => self.control.resume(s.epoch, s.revision),
                Op::Arrival | Op::Inject => {
                    let result = if op == Op::Arrival {
                        self.control.external_arrival(0, 1)
                    } else {
                        self.control.inject(s.epoch, s.revision, 0, 1)
                    }
                    .map_err(|rejected| {
                        assert_eq!(rejected.input, 0);
                        rejected.error
                    });
                    if result == Err(LiveError::Overflow) {
                        self.overflow_count += 1;
                    }
                    result
                }
                Op::Begin => {
                    if let Some((permit, input)) = self.control.begin_next()? {
                        assert_eq!(input, 0);
                        assert!(self.delivery.is_none());
                        self.delivery = Some(permit);
                    }
                    Ok(())
                }
                Op::Zero | Op::Output => self.control.delivered(
                    self.delivery.take().ok_or(LiveError::WrongPhase)?,
                    usize::from(op == Op::Output),
                ),
                Op::Fail => self
                    .control
                    .delivery_failed(self.delivery.take().ok_or(LiveError::WrongPhase)?),
                Op::Reserve => {
                    let permit = self.control.take_dispatch(0)?;
                    assert!(self.dispatch.is_none());
                    self.dispatch = Some(permit);
                    Ok(())
                }
                Op::Dispatch => self
                    .control
                    .dispatched(self.dispatch.take().ok_or(LiveError::WrongPhase)?),
                Op::DispatchFail => self
                    .control
                    .dispatch_failed(self.dispatch.take().ok_or(LiveError::WrongPhase)?),
                Op::Terminate => {
                    self.control.terminate();
                    Ok(())
                }
                Op::Disconnect => {
                    self.control.disconnect();
                    Ok(())
                }
                Op::Reconnect => self.control.reconnect().map(|_| ()),
                Op::OldRevision => self.control.request_pause(s.epoch, s.revision + 1),
                Op::WrongEpoch => self.control.request_pause(s.epoch + 1, s.revision),
            }
        }
    }
    #[derive(Clone, Debug)]
    struct State {
        spec: Spec,
        history: Vec<Op>,
        mismatch: Option<String>,
    }
    impl PartialEq for State {
        fn eq(&self, other: &Self) -> bool {
            self.spec == other.spec && self.mismatch == other.mismatch
        }
    }
    impl Eq for State {}
    impl Hash for State {
        fn hash<H: Hasher>(&self, h: &mut H) {
            self.spec.hash(h);
            self.mismatch.hash(h);
        }
    }
    struct ControlModel {
        detach: DetachPolicy,
        overflow: OverflowPolicy,
        authority: ControlAuthority,
    }
    impl Model for ControlModel {
        type State = State;
        type Input = Op;
        type Output = ();
        fn metadata(&self) -> ModelMetadata {
            HostModel::default().metadata()
        }
        fn initial_state(&self) -> Result<State, ModelError> {
            Ok(State {
                spec: Spec::initial(),
                history: vec![],
                mismatch: None,
            })
        }
        fn step(&self, before: &State, op: &Op) -> Result<Transition<State, ()>, ModelError> {
            let mut after = before.clone();
            let mut harness = Harness::new(self);
            for previous in &before.history {
                let _ = harness.apply(*previous);
            }
            let expected = after.spec.apply(*op, self);
            let actual = harness.apply(*op);
            after.history.push(*op);
            let s = harness.control.status();
            let r = &after.spec;
            let correct = expected == actual
                && s.epoch == r.epoch
                && s.origin_epoch == r.output_epoch
                && s.revision == r.revision
                && s.phase == r.phase()
                && s.connected == r.connected
                && s.effect_phase == r.effects()
                && s.buffered_arrivals == r.queued
                && s.buffered_bytes == r.queued
                && s.pause_generation == r.generations
                && s.overflow_count == harness.overflow_count
                && harness.delivery.is_some() == r.busy
                && harness.dispatch.is_some() == r.dispatch_permit
                && harness.dispatch.as_ref().is_none_or(|p| {
                    p.epoch() == r.output_epoch
                        && p.revision() == r.revision
                        && p.output_index() == 0
                });
            if !correct {
                after.mismatch = Some(format!(
                    "path {:?}; result {actual:?}, expected {expected:?}; status {s:?}; reference {r:?}",
                    after.history
                ));
            }
            Ok(Transition::accepted(after, vec![]))
        }
        fn check_state(&self, s: &State) -> Result<Vec<Check>, ModelError> {
            Ok(vec![if let Some(message) = &s.mismatch {
                Check::failed("reference-state-machine", message.clone())
            } else {
                Check::passed("reference-state-machine")
            }])
        }
    }
    impl Enumerate for ControlModel {
        fn inputs(&self, s: &State) -> Result<Vec<Op>, ModelError> {
            let mut ops = vec![
                Op::Boundary,
                Op::One,
                Op::Resume,
                Op::Arrival,
                Op::Inject,
                Op::Zero,
                Op::Output,
                Op::Fail,
                Op::Reserve,
                Op::Dispatch,
                Op::DispatchFail,
                Op::Disconnect,
                Op::OldRevision,
                Op::WrongEpoch,
                Op::Terminate,
            ];
            // Concrete bounds keep this graph finite without coalescing distinct
            // revisions or epochs. Overflow cycles close under exact state Eq.
            if s.spec.generations < 2 {
                ops.push(Op::Pause);
            }
            if s.spec.revision < 2 {
                ops.push(Op::Begin);
            }
            if s.spec.epoch < 2 {
                ops.push(Op::Reconnect);
            }
            Ok(ops)
        }
    }
    #[test]
    fn all_finite_controller_interleavings_match_independent_model() {
        let mut total_states = 0;
        let mut total_edges = 0;
        for detach in [
            DetachPolicy::RemainPaused,
            DetachPolicy::Resume,
            DetachPolicy::Terminate,
        ] {
            for overflow in [OverflowPolicy::Backpressure, OverflowPolicy::Disconnect] {
                for pause_and_step in [false, true] {
                    for inject in [false, true] {
                        let model = ControlModel {
                            detach,
                            overflow,
                            authority: ControlAuthority {
                                pause_and_step,
                                inject,
                            },
                        };
                        let report = enumerate(
                            &model,
                            SearchConfig {
                                max_states: 100_000,
                                max_transitions: 2_000_000,
                                max_depth: 100,
                            },
                        )
                        .unwrap();
                        assert_eq!(
                            report.termination,
                            SearchTermination::GraphExhausted,
                            "{detach:?}/{overflow:?}/{pause_and_step}/{inject}: {report:?}"
                        );
                        assert!(report.failure.is_none());
                        total_states += report.states;
                        total_edges += report.transitions;
                    }
                }
            }
        }
        assert!(total_states > 1000 && total_edges > 10_000);
        eprintln!(
            "live controller qualification: {total_states} states and {total_edges} edges across 24 exhaustive finite configurations"
        );
    }
}

#[test]
fn bridge_and_controller_origins_continue_correlating_after_client_reconnect() {
    let model = HostModel::default();
    let mut capture = bridge(
        &model,
        0,
        0,
        RecorderOptions::default(),
        LiveLimits::default(),
    );
    let mut c = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    let mut state = 0;
    for sequence in 1..=2 {
        c.external_arrival(1, 1).unwrap();
        let (permit, input) = c.begin_next().unwrap().unwrap();
        observe(&model, &mut capture, &mut state, input, sequence).unwrap();
        c.delivered(permit, 1).unwrap();
        let dispatch = c.take_dispatch(0).unwrap();
        assert_eq!(dispatch.origin().epoch, 1);
        capture.effect_dispatched(dispatch.origin()).unwrap();
        c.dispatched(dispatch).unwrap();
        c.disconnect();
        c.reconnect().unwrap();
    }
    assert_eq!(c.status().epoch, 3);
    assert_eq!(c.status().origin_epoch, 1);
    assert_eq!(capture.health().epoch, 1);
    assert_eq!(capture.health().recorded_steps, 2);
}

#[test]
fn reserved_limits_and_provenance_cannot_be_forged() {
    let model = HostModel::default();
    let initial = check_initial(&model, &0).unwrap();
    let mut config = RunConfig::default();
    config
        .parameters
        .push(("stateless.debug.origin".into(), "pretend-replay".into()));
    assert!(
        LiveBridge::attach(
            &initial,
            0,
            1,
            config,
            RecorderOptions::default(),
            LiveLimits::default()
        )
        .is_err()
    );
    assert!(
        LiveBridge::attach(
            &initial,
            0,
            0,
            RunConfig::default(),
            RecorderOptions::default(),
            LiveLimits::default()
        )
        .is_err()
    );
    assert!(
        LiveBridge::attach(
            &initial,
            0,
            1,
            RunConfig::default(),
            RecorderOptions::default(),
            LiveLimits {
                max_event_bytes: 1,
                ..LiveLimits::default()
            }
        )
        .is_err()
    );
    assert!(matches!(
        LiveController::<()>::new(0, 0, ControllerOptions::default()),
        Err(LiveError::InvalidLimits)
    ));
}

#[test]
fn counter_exhaustion_is_atomic_and_keeps_buffered_arrival_owned() {
    let mut c = LiveController::new(u64::MAX, u64::MAX, ControllerOptions::default()).unwrap();
    c.external_arrival(7, 1).unwrap();
    assert!(matches!(c.begin_next(), Err(LiveError::Exhausted)));
    assert_eq!(c.status().buffered_arrivals, 1);
    assert_eq!(c.status().revision, u64::MAX);
    c.disconnect();
    assert_eq!(c.reconnect(), Err(LiveError::Exhausted));
    assert!(!c.status().connected);
    assert_eq!(c.status().epoch, u64::MAX);
}

#[test]
fn controller_property_stop_and_pause_correlation_are_explicit() {
    let mut c = controller(DetachPolicy::Resume, OverflowPolicy::Backpressure);
    pause(&mut c);
    let first = c.safe_point().unwrap().unwrap();
    assert_eq!(first.generation, 1);
    c.authorize_one(1, 0).unwrap();
    c.request_pause(1, 0).unwrap();
    let second = c.safe_point().unwrap().unwrap();
    assert_eq!(second.generation, 2);
    assert_eq!(first.revision, second.revision); // No modeled turn or clock tick.
    c.authorize_one(1, 0).unwrap();
    c.external_arrival(9, 1).unwrap();
    let (permit, _) = c.begin_next().unwrap().unwrap();
    c.terminate(); // An already admitted host result must still be accounted for.
    c.delivered(permit, 1).unwrap();
    assert_eq!(c.status().revision, 1);
    assert_eq!(c.status().phase, ControllerPhase::Terminated);
    assert!(matches!(c.take_dispatch(0), Err(LiveError::WrongPhase)));
}

#[test]
fn recorder_owns_bounded_error_footer_even_for_external_stop() {
    let model = HostModel::default();
    let options = RecorderOptions::default();
    let mut capture = bridge(&model, 0, 0, options.clone(), LiveLimits::default());
    observe(&model, &mut capture, &mut 0, 1, 1).unwrap();
    capture.stop_recording(ModelError::new(
        "x".repeat(options.limits.max_string_bytes + 100),
    ));
    let trace = capture.export();
    let mut encoded = Vec::new();
    trace
        .write_with_limits(&mut encoded, &options.limits)
        .unwrap();
    assert!(encoded.len() as u64 <= options.max_retained_bytes);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    assert_eq!(trace.steps.len(), 1);
}
