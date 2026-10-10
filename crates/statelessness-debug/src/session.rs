//! Typed, single-owner simulation. Reducers, checks and observations are separate.
use stateless::execution::{CheckPolicy, check_initial, check_turn};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::observation::{CheckedInitial, CheckedTurn};
use stateless::observation::{DebugObservation, TurnObservation};
use stateless::trace::{RunConfig, Trace};
use stateless::{Check, Disposition, Enumerate, Model, ModelCodec, ModelError, TransitionRef};
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT_SESSION_EPOCH: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    TraceView,
    VerifiedReplay,
    Simulation,
    LiveObservation,
    LiveControl,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Ready,
    Running,
    Paused,
    Ended,
    Disconnected,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StopReason {
    PreBreakpoint(u64),
    PostBreakpoint(u64),
    PropertyFailure,
    InitialCheckError(String),
    ModelError(String),
    CheckerError(String),
    ObserverError(String),
    RecordingError(String),
    BudgetExhausted,
    Cancelled,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DebugError {
    InvalidCommand(String),
    StaleRevision { expected: u64, actual: u64 },
    Unsupported(&'static str),
    Ended,
    InputNotPermitted,
    Callback(String),
}
impl std::fmt::Display for DebugError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for DebugError {}

/// Limits apply to debugger-owned work, not blocking or allocating host callbacks.
#[derive(Clone, Debug)]
pub struct SessionLimits {
    pub max_turns: u64,
    pub max_continue: usize,
    pub max_breakpoints: usize,
    pub max_candidates: usize,
}
impl Default for SessionLimits {
    fn default() -> Self {
        Self {
            max_turns: 100_000,
            max_continue: 1_000,
            max_breakpoints: 64,
            max_candidates: 1_000,
        }
    }
}

/// Admission is environmental validity, not the application's disposition.
/// The caller explicitly supplies the domain; no validity is inferred from fields.
type InputValidator<M> =
    fn(&M, &<M as Model>::State, &<M as Model>::Input) -> Result<bool, ModelError>;
type BoundedInputValidator<M> =
    fn(&M, &<M as Model>::State, &<M as Model>::Input, usize) -> Result<bool, ModelError>;
pub struct InputPolicy<M: Model> {
    pub label: String,
    bounded_validate: Option<BoundedInputValidator<M>>,
    validate: Option<InputValidator<M>>,
}
impl<M: Model> Clone for InputPolicy<M> {
    fn clone(&self) -> Self {
        Self {
            label: self.label.clone(),
            bounded_validate: self.bounded_validate,
            validate: self.validate,
        }
    }
}
impl<M: Model> InputPolicy<M> {
    pub fn declared(label: impl Into<String>, validate: InputValidator<M>) -> Self {
        Self {
            label: label.into(),
            bounded_validate: None,
            validate: Some(validate),
        }
    }
    /// An explicit experiment outside declared environmental assumptions.
    pub fn unrestricted() -> Self {
        Self {
            label: "out-of-domain-injection".into(),
            bounded_validate: None,
            validate: None,
        }
    }
    pub fn is_unrestricted(&self) -> bool {
        self.validate.is_none()
    }
    /// Shared admission path for delivery and checkpoint-root minimization.
    /// Iterator work is bounded; application callbacks remain cooperative.
    pub(crate) fn permits(
        &self,
        model: &M,
        state: &M::State,
        input: &M::Input,
        maximum: usize,
    ) -> Result<bool, ModelError> {
        if let Some(bounded) = self.bounded_validate {
            bounded(model, state, input, maximum)
        } else if let Some(validate) = self.validate {
            validate(model, state, input)
        } else {
            Ok(true)
        }
    }
}
impl<M: Enumerate> InputPolicy<M> {
    pub fn enumerated() -> Self {
        let mut policy = Self::declared("enumerated-domain", |_, _, _| Ok(false));
        policy.bounded_validate = Some(|model, state, input, maximum| {
            let mut iter = model.input_iter(state)?;
            for _ in 0..maximum {
                match iter.next() {
                    Some(candidate) if &candidate == input => return Ok(true),
                    Some(_) => (),
                    None => return Ok(false),
                }
            }
            if iter.next().is_some() {
                return Err(ModelError::new(
                    "environmental admission work budget exhausted",
                ));
            }
            Ok(false)
        });
        policy
    }
}

type RecordTurn<M> = for<'a> fn(&mut Recorder, &CheckedTurn<'a, M>) -> Result<(), ModelError>;
type PrePredicate<M> = fn(&<M as Model>::State, &<M as Model>::Input) -> bool;
type PostPredicate<M> = for<'a> fn(&TurnObservation<'a, M>) -> bool;
struct PreBreakpoint<M: Model> {
    id: u64,
    predicate: PrePredicate<M>,
}
struct PostBreakpoint<M: Model> {
    id: u64,
    predicate: PostPredicate<M>,
}
struct LastTurn<M: Model> {
    before: M::State,
    input: M::Input,
    outputs: Vec<M::Output>,
    disposition: Disposition,
    checks: Vec<Check>,
    complete_checks: bool,
    state_check_count: usize,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StepResult {
    pub sequence: u64,
    pub delivered: bool,
    pub stop: Option<StopReason>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Capabilities {
    pub mode: Mode,
    pub typed_input: bool,
    pub exact_recording: bool,
    pub enumerate_inputs: bool,
    pub effects_dispatched: bool,
    pub live_restore: bool,
}

/// Does not require codecs, Debug, Send, Sync or 'static on model values.
/// All callbacks run synchronously. Rust's exclusive borrow prevents reentrant stepping.
pub struct DebugSession<M: Model> {
    id: String,
    epoch: u64,
    model: M,
    state: M::State,
    initial_checks: Vec<Check>,
    sequence: u64,
    revision: u64,
    phase: Phase,
    stop: Option<StopReason>,
    policy: InputPolicy<M>,
    check_policy: CheckPolicy,
    limits: SessionLimits,
    pre: Vec<PreBreakpoint<M>>,
    post: Vec<PostBreakpoint<M>>,
    next_breakpoint: u64,
    pending: Option<(u64, M::Input)>,
    last: Option<LastTurn<M>>,
    recorder: Option<Recorder>,
    record_turn: Option<RecordTurn<M>>,
    issues: Vec<String>,
}
impl<M: Model> DebugSession<M> {
    pub fn new(
        id: impl Into<String>,
        model: M,
        policy: InputPolicy<M>,
        limits: SessionLimits,
    ) -> Result<Self, DebugError> {
        let state = model
            .initial_state()
            .map_err(|e| DebugError::Callback(format!("initial state: {e}")))?;
        Self::from_state(id, model, state, policy, limits)
    }
    /// The application is responsible for the independence and coherence of this
    /// snapshot. Prefer codec restoration when constructing persistent branches.
    pub fn from_state(
        id: impl Into<String>,
        model: M,
        state: M::State,
        policy: InputPolicy<M>,
        limits: SessionLimits,
    ) -> Result<Self, DebugError> {
        Self::initialize(id.into(), model, state, policy, limits, |_| Ok(None), None)
    }
    fn initialize(
        id: String,
        model: M,
        state: M::State,
        policy: InputPolicy<M>,
        limits: SessionLimits,
        make_recorder: impl FnOnce(&CheckedInitial<'_, M>) -> Result<Option<Recorder>, ModelError>,
        record_turn: Option<RecordTurn<M>>,
    ) -> Result<Self, DebugError> {
        if id.is_empty()
            || limits.max_turns == 0
            || limits.max_continue == 0
            || limits.max_candidates == 0
        {
            return Err(DebugError::InvalidCommand(
                "nonempty identity and positive work budgets required".into(),
            ));
        }
        let epoch = NEXT_SESSION_EPOCH
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| DebugError::InvalidCommand("session identity exhausted".into()))?;
        let checked = check_initial(&model, &state);
        let mut recorder = None;
        let mut issues = Vec::new();
        let (initial_checks, mut stop) = match checked {
            Ok(batch) => {
                match make_recorder(&batch) {
                    Ok(value) => recorder = value,
                    Err(error) => issues.push(error.to_string()),
                }
                let stop = batch
                    .checks()
                    .iter()
                    .any(Check::is_failure)
                    .then_some(StopReason::PropertyFailure);
                (batch.into_checks(), stop)
            }
            Err(error) => (
                Vec::new(),
                Some(StopReason::InitialCheckError(error.to_string())),
            ),
        };
        if stop.is_none() && !issues.is_empty() {
            stop = Some(StopReason::RecordingError(issues[0].clone()));
        }
        Ok(Self {
            id,
            epoch,
            model,
            state,
            initial_checks,
            sequence: 0,
            revision: 0,
            phase: if stop.is_some() {
                Phase::Ended
            } else {
                Phase::Ready
            },
            stop,
            policy,
            check_policy: CheckPolicy::default(),
            limits,
            pre: Vec::new(),
            post: Vec::new(),
            next_breakpoint: 1,
            pending: None,
            last: None,
            recorder,
            record_turn,
            issues,
        })
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    /// Process-local generation, unique even when a display session ID is reused.
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
    pub fn model(&self) -> &M {
        &self.model
    }
    pub fn state(&self) -> &M::State {
        &self.state
    }
    pub fn initial_checks(&self) -> &[Check] {
        &self.initial_checks
    }
    pub fn sequence(&self) -> u64 {
        self.sequence
    }
    pub fn revision(&self) -> u64 {
        self.revision
    }
    pub fn phase(&self) -> Phase {
        self.phase
    }
    pub fn stop_reason(&self) -> Option<&StopReason> {
        self.stop.as_ref()
    }
    pub fn input_policy(&self) -> &str {
        &self.policy.label
    }
    pub fn limits(&self) -> &SessionLimits {
        &self.limits
    }
    pub fn checks_complete(&self) -> bool {
        self.last.as_ref().map_or(
            !matches!(self.stop, Some(StopReason::InitialCheckError(_))),
            |turn| turn.complete_checks,
        )
    }
    pub fn capabilities(&self) -> Capabilities {
        Capabilities {
            mode: Mode::Simulation,
            typed_input: true,
            exact_recording: self.recorder.is_some(),
            enumerate_inputs: self.policy.bounded_validate.is_some(),
            effects_dispatched: false,
            live_restore: false,
        }
    }
    pub fn observation(&self) -> DebugObservation<'_, M> {
        match &self.last {
            None => DebugObservation::Initial {
                state: &self.state,
                checks: &self.initial_checks,
            },
            Some(last) => DebugObservation::Turn(TurnObservation {
                sequence: self.sequence,
                before: &last.before,
                input: &last.input,
                transition: TransitionRef {
                    state: &self.state,
                    outputs: &last.outputs,
                    disposition: &last.disposition,
                },
                checks: &last.checks,
                state_check_count: last.state_check_count,
            }),
        }
    }
    pub fn diagnostic_errors(&self) -> &[String] {
        &self.issues
    }
    pub fn recorder(&self) -> Option<&Recorder> {
        self.recorder.as_ref()
    }
    pub fn export_trace(&self) -> Result<Trace, DebugError> {
        self.recorder
            .as_ref()
            .map(Recorder::snapshot)
            .ok_or(DebugError::Unsupported("exact recording was not enabled"))
    }
    pub fn add_pre_breakpoint(&mut self, predicate: PrePredicate<M>) -> Result<u64, DebugError> {
        let id = self.breakpoint_id()?;
        self.pre.push(PreBreakpoint { id, predicate });
        Ok(id)
    }
    pub fn add_post_breakpoint(&mut self, predicate: PostPredicate<M>) -> Result<u64, DebugError> {
        let id = self.breakpoint_id()?;
        self.post.push(PostBreakpoint { id, predicate });
        Ok(id)
    }
    fn breakpoint_id(&mut self) -> Result<u64, DebugError> {
        if self.pre.len() + self.post.len() >= self.limits.max_breakpoints {
            return Err(DebugError::InvalidCommand(
                "breakpoint budget exceeded".into(),
            ));
        }
        let id = self.next_breakpoint;
        self.next_breakpoint = id
            .checked_add(1)
            .ok_or_else(|| DebugError::InvalidCommand("breakpoint identity exhausted".into()))?;
        Ok(id)
    }
    pub fn remove_breakpoint(&mut self, id: u64) -> bool {
        let count = self.pre.len() + self.post.len();
        self.pre.retain(|b| b.id != id);
        self.post.retain(|b| b.id != id);
        count != self.pre.len() + self.post.len()
    }
    pub fn check_revision(&self, expected: u64) -> Result<(), DebugError> {
        if expected == self.revision {
            Ok(())
        } else {
            Err(DebugError::StaleRevision {
                expected,
                actual: self.revision,
            })
        }
    }
    pub fn step(
        &mut self,
        expected_revision: u64,
        input: M::Input,
    ) -> Result<StepResult, DebugError> {
        self.step_with_observer(expected_revision, input, |_| Ok(()))
    }
    /// A pre-breakpoint leaves the input pending. Repeating step/continue with
    /// that exact input and revision bypasses the stopping boundary once.
    pub fn step_with_observer(
        &mut self,
        expected_revision: u64,
        input: M::Input,
        mut observer: impl FnMut(DebugObservation<'_, M>) -> Result<(), ModelError>,
    ) -> Result<StepResult, DebugError> {
        self.check_revision(expected_revision)?;
        if self.phase == Phase::Ended || self.phase == Phase::Disconnected {
            return Err(DebugError::Ended);
        }
        if self.sequence >= self.limits.max_turns || self.revision == u64::MAX {
            self.phase = Phase::Ended;
            self.stop = Some(StopReason::BudgetExhausted);
            return Ok(self.result(false));
        }
        if !self.policy.is_unrestricted() {
            let old_phase = self.phase;
            self.phase = Phase::Ended;
            let valid =
                self.policy
                    .permits(&self.model, &self.state, &input, self.limits.max_candidates);
            self.phase = old_phase;
            if !valid.map_err(|e| DebugError::Callback(format!("input validation: {e}")))? {
                return Err(DebugError::InputNotPermitted);
            }
        }
        let bypass = self
            .pending
            .as_ref()
            .is_some_and(|(revision, pending)| *revision == self.revision && pending == &input);
        self.pending = None;
        let old_phase = self.phase;
        self.phase = Phase::Ended;
        let pre_stop = if bypass {
            None
        } else {
            self.pre
                .iter()
                .find(|b| (b.predicate)(&self.state, &input))
                .map(|b| b.id)
        };
        self.phase = old_phase;
        if let Some(id) = pre_stop {
            self.pending = Some((self.revision, input));
            self.phase = Phase::Paused;
            self.stop = Some(StopReason::PreBreakpoint(id));
            return Ok(self.result(false));
        }
        // Leave the session terminal if a callback unwinds. We do not promise
        // panic recovery or roll back application work that already happened.
        self.phase = Phase::Ended;
        self.stop = None;
        let transition = match self.model.step(&self.state, &input) {
            Ok(transition) => transition,
            Err(error) => {
                if let Some(recorder) = &mut self.recorder {
                    recorder.stop_with_error(error.clone());
                }
                self.stop = Some(StopReason::ModelError(error.to_string()));
                return Ok(self.result(false));
            }
        };
        self.sequence += 1;
        self.revision += 1;
        let checked = check_turn(
            &self.model,
            &self.state,
            &input,
            &transition,
            self.sequence,
            self.check_policy,
        );
        let (checks, complete_checks, state_check_count) = match checked {
            Ok(batch) => {
                if batch.checks().iter().any(Check::is_failure) {
                    self.stop = Some(StopReason::PropertyFailure);
                }
                if let (Some(recorder), Some(record)) = (&mut self.recorder, self.record_turn)
                    && let Err(error) = record(recorder, &batch)
                {
                    self.issues.push(format!("exact recording: {error}"));
                    // Preserve a property failure even if its exact encoding failed.
                    if self.stop.is_none() {
                        self.stop = Some(StopReason::RecordingError(error.to_string()));
                    }
                }
                let state_check_count = batch.state_check_count();
                (batch.into_checks(), true, state_check_count)
            }
            Err(error) => {
                if let Some(recorder) = &mut self.recorder {
                    recorder.stop_with_error(error.clone());
                }
                self.stop = Some(StopReason::CheckerError(error.to_string()));
                (Vec::new(), false, 0)
            }
        };
        let actual = TurnObservation {
            sequence: self.sequence,
            before: &self.state,
            input: &input,
            transition: transition.as_ref(),
            checks: &checks,
            state_check_count,
        };
        if self.stop.is_none()
            && let Some(breakpoint) = self.post.iter().find(|b| (b.predicate)(&actual))
        {
            self.stop = Some(StopReason::PostBreakpoint(breakpoint.id));
        }
        // Commit before publishing; diagnostic failure cannot undo a transition.
        let before = std::mem::replace(&mut self.state, transition.state);
        self.last = Some(LastTurn {
            before,
            input,
            outputs: transition.outputs,
            disposition: transition.disposition,
            checks,
            complete_checks,
            state_check_count,
        });
        let terminal = self.stop.as_ref().is_some_and(|reason| {
            !matches!(
                reason,
                StopReason::PreBreakpoint(_) | StopReason::PostBreakpoint(_)
            )
        });
        if let Err(error) = observer(self.observation()) {
            self.issues.push(format!("observer: {error}"));
            if self.stop.is_none() {
                self.stop = Some(StopReason::ObserverError(error.to_string()));
            }
            self.phase = Phase::Ended;
        } else {
            self.phase = if terminal {
                Phase::Ended
            } else {
                Phase::Paused
            };
        }
        Ok(self.result(true))
    }
    fn result(&self, delivered: bool) -> StepResult {
        StepResult {
            sequence: self.sequence,
            delivered,
            stop: self.stop.clone(),
        }
    }
    pub fn run_bounded(
        &mut self,
        expected_revision: u64,
        inputs: impl IntoIterator<Item = M::Input>,
        max_turns: usize,
    ) -> Result<Vec<StepResult>, DebugError> {
        self.check_revision(expected_revision)?;
        if max_turns == 0 || max_turns > self.limits.max_continue {
            return Err(DebugError::InvalidCommand(
                "continue requires a positive bounded turn count".into(),
            ));
        }
        let mut results = Vec::new();
        let mut inputs = inputs.into_iter();
        for _ in 0..max_turns {
            let Some(input) = inputs.next() else {
                return Ok(results);
            };
            let result = self.step(self.revision, input)?;
            let stopped = result.stop.is_some();
            results.push(result);
            if stopped {
                return Ok(results);
            }
        }
        self.stop = Some(StopReason::BudgetExhausted);
        self.phase = Phase::Paused;
        Ok(results)
    }
    pub fn cancel(&mut self, expected_revision: u64) -> Result<(), DebugError> {
        self.check_revision(expected_revision)?;
        if matches!(self.phase, Phase::Ended | Phase::Disconnected) {
            return Err(DebugError::Ended);
        }
        self.phase = Phase::Ended;
        self.stop = Some(StopReason::Cancelled);
        Ok(())
    }
}

/// The input value is bound to the snapshot and domain; selection never reads a
/// new value from an old list index. Tokens cannot be manufactured by clients.
pub struct CandidateToken<I> {
    session: String,
    epoch: u64,
    revision: u64,
    domain: String,
    input: I,
}
impl<I> CandidateToken<I> {
    pub fn input(&self) -> &I {
        &self.input
    }
    pub fn revision(&self) -> u64 {
        self.revision
    }
}
pub struct CandidatePage<I> {
    pub candidates: Vec<CandidateToken<I>>,
    pub next_offset: Option<usize>,
    pub complete: bool,
}
impl<M: Enumerate> DebugSession<M> {
    pub fn inputs(
        &self,
        expected_revision: u64,
        offset: usize,
        limit: usize,
    ) -> Result<CandidatePage<M::Input>, DebugError> {
        self.check_revision(expected_revision)?;
        if limit == 0
            || offset
                .checked_add(limit)
                .is_none_or(|n| n > self.limits.max_candidates)
        {
            return Err(DebugError::InvalidCommand(
                "candidate work budget exceeded".into(),
            ));
        }
        let mut iter = self
            .model
            .input_iter(&self.state)
            .map_err(|e| DebugError::Callback(e.to_string()))?
            .skip(offset);
        let candidates = iter
            .by_ref()
            .take(limit)
            .map(|input| CandidateToken {
                session: self.id.clone(),
                epoch: self.epoch,
                revision: self.revision,
                domain: self.policy.label.clone(),
                input,
            })
            .collect();
        let more = iter.next().is_some();
        Ok(CandidatePage {
            candidates,
            next_offset: more.then_some(offset + limit),
            complete: !more,
        })
    }
    /// Bounded revalidation of a concrete candidate, including explicit
    /// unrestricted sessions. A changed/faulty domain cannot hang selection.
    pub fn candidate_permitted(&self, input: &M::Input) -> Result<bool, DebugError> {
        let policy = InputPolicy::<M>::enumerated();
        policy.bounded_validate.expect("enumerated validator")(
            &self.model,
            &self.state,
            input,
            self.limits.max_candidates,
        )
        .map_err(|e| DebugError::Callback(e.to_string()))
    }
    pub fn select(&mut self, token: CandidateToken<M::Input>) -> Result<StepResult, DebugError> {
        if token.session != self.id
            || token.epoch != self.epoch
            || token.domain != self.policy.label
        {
            return Err(DebugError::InvalidCommand(
                "candidate belongs to another session/domain".into(),
            ));
        }
        self.check_revision(token.revision)?;
        // Revalidate the exact selected value even for an unrestricted session.
        if !self.candidate_permitted(&token.input)? {
            return Err(DebugError::InputNotPermitted);
        }
        self.step(token.revision, token.input)
    }
}

impl<M: ModelCodec> DebugSession<M> {
    pub fn recording(
        id: impl Into<String>,
        model: M,
        policy: InputPolicy<M>,
        limits: SessionLimits,
        config: RunConfig,
        options: RecorderOptions,
    ) -> Result<Self, DebugError> {
        let state = model
            .initial_state()
            .map_err(|e| DebugError::Callback(format!("initial state: {e}")))?;
        Self::recording_from_state(id, model, state, policy, limits, config, options)
    }
    pub fn recording_from_state(
        id: impl Into<String>,
        model: M,
        state: M::State,
        policy: InputPolicy<M>,
        limits: SessionLimits,
        config: RunConfig,
        options: RecorderOptions,
    ) -> Result<Self, DebugError> {
        if config
            .parameters
            .iter()
            .any(|(key, _)| key.starts_with("stateless.debug."))
        {
            return Err(DebugError::InvalidCommand(
                "stateless.debug. parameters are reserved".into(),
            ));
        }
        Self::recording_from_state_with_provenance(
            id, model, state, policy, limits, config, options,
        )
    }
    pub(crate) fn recording_from_state_with_provenance(
        id: impl Into<String>,
        model: M,
        state: M::State,
        policy: InputPolicy<M>,
        limits: SessionLimits,
        mut config: RunConfig,
        options: RecorderOptions,
    ) -> Result<Self, DebugError> {
        config
            .parameters
            .push(("stateless.debug.environment".into(), policy.label.clone()));
        config.parameters.push((
            "stateless.debug.origin".into(),
            if policy.is_unrestricted() {
                "out-of-domain-injection"
            } else {
                "simulation"
            }
            .into(),
        ));
        Self::initialize(
            id.into(),
            model,
            state,
            policy,
            limits,
            |checked| Recorder::with_checked_initial(checked, config, options).map(Some),
            Some(|recorder, checked| recorder.observe_checked(checked).map(|_| ())),
        )
    }
    pub fn step_encoded(
        &mut self,
        expected_revision: u64,
        bytes: &[u8],
    ) -> Result<StepResult, DebugError> {
        self.check_revision(expected_revision)?;
        if bytes.len() > stateless::trace::ReadLimits::default().max_blob_bytes {
            return Err(DebugError::InvalidCommand(
                "input payload exceeds byte limit".into(),
            ));
        }
        let input = self
            .model
            .decode_input(bytes)
            .map_err(|e| DebugError::InvalidCommand(format!("decode input: {e}")))?;
        self.step(expected_revision, input)
    }
}
