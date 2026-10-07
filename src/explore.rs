//! Deterministic sequence fuzzing, causal shrinking, and bounded breadth-first
//! exploration. These functions execute modeled transitions only: outputs are
//! data and must never perform external effects.

use std::collections::{HashMap, VecDeque, hash_map::RandomState};
use std::hash::{BuildHasher, Hash};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use crate::model::{Check, CheckStatus, Enumerate, Generate, Model, ModelError, Rng, Transition};

/// Cooperative limits checked between model callbacks/fully checked transitions.
/// A callback cannot be interrupted, and allocations inside it cannot be capped.
#[derive(Clone, Copy, Debug, Default)]
pub struct RunLimits<'a> {
    /// Wall-clock limits are unavailable on unknown-OS Wasm targets. Requesting
    /// one there returns ModelError; runs without one never access the clock.
    pub max_duration: Option<Duration>,
    pub cancellation: Option<&'a AtomicBool>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SearchLimits<'a> {
    pub control: RunLimits<'a>,
    /// Sum of model-supplied state estimates for admitted states, excluding
    /// predecessor inputs, map/frontier storage, temporary successors and callbacks.
    /// Every admitted state needs an estimate when this is set. This is not RSS.
    pub max_estimated_state_bytes: Option<usize>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ShrinkLimits<'a> {
    pub control: RunLimits<'a>,
    /// Aggregate executed transitions, including validation of the original.
    pub max_replayed_transitions: Option<u64>,
}

#[derive(Clone, Copy)]
enum Stop {
    Deadline,
    Cancelled,
}

pub(crate) struct Control<'a> {
    limits: RunLimits<'a>,
    started: Option<Instant>,
}
impl<'a> Control<'a> {
    pub(crate) fn new(limits: RunLimits<'a>) -> Result<Self, ModelError> {
        // std's clock panics on wasm*-unknown-unknown. Cancellation and logical
        // budgets remain usable there, and unconstrained runs need no clock.
        #[cfg(all(target_family = "wasm", target_os = "unknown"))]
        if limits.max_duration.is_some() {
            return Err(ModelError::new(
                "wall-clock search limits are unavailable on this Wasm target",
            ));
        }
        Ok(Self {
            started: limits.max_duration.map(|_| Instant::now()),
            limits,
        })
    }
    pub(crate) fn remaining(&self) -> RunLimits<'a> {
        RunLimits {
            max_duration: self
                .started
                .zip(self.limits.max_duration)
                .map(|(start, limit)| limit.saturating_sub(start.elapsed())),
            cancellation: self.limits.cancellation,
        }
    }
    fn stop(&self) -> Option<Stop> {
        if self
            .limits
            .cancellation
            .is_some_and(|flag| flag.load(Ordering::Relaxed))
        {
            Some(Stop::Cancelled)
        } else if self
            .started
            .zip(self.limits.max_duration)
            .is_some_and(|(started, limit)| started.elapsed() >= limit)
        {
            Some(Stop::Deadline)
        } else {
            None
        }
    }
}

/// Property phases have distinct identities when shrinking a counterexample.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CheckPhase {
    InitialState,
    State,
    Transition,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PropertyFailure {
    pub phase: CheckPhase,
    pub check: Check,
}

/// Concrete inputs up to and including the first failing transition.
/// An initial-state failure has an empty input sequence.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Failure<I> {
    pub inputs: Vec<I>,
    pub violations: Vec<PropertyFailure>,
}

#[derive(Clone, Debug)]
pub struct SearchConfig {
    /// Maximum unique retained states, including the initial state. Must be > 0.
    pub max_states: usize,
    /// Maximum executed edges, including edges to already visited states.
    pub max_transitions: u64,
    /// Maximum number of transitions from the initial state.
    pub max_depth: usize,
}

impl Default for SearchConfig {
    fn default() -> Self {
        Self {
            max_states: 100_000,
            max_transitions: 1_000_000,
            max_depth: 100,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchTermination {
    GraphExhausted,
    /// At least one state at the depth boundary had unexamined inputs.
    DepthBound,
    StateLimit,
    TransitionLimit,
    FailureFound,
    EstimatedStateByteLimit,
    Deadline,
    Cancelled,
}

#[derive(Clone, Debug)]
pub struct SearchReport<I> {
    pub termination: SearchTermination,
    /// Unique retained states. A checked successor rejected by an admission
    /// budget is not included. Zero for pre-start cancellation/deadline or when
    /// the initial state exceeds its byte cap.
    pub states: usize,
    pub transitions: u64,
    pub max_depth_reached: usize,
    pub skipped_checks: u64,
    /// Sum of adapter estimates for retained states; None if unknown or overflowed.
    pub estimated_retained_state_bytes: Option<usize>,
    pub failure: Option<Failure<I>>,
}

struct Node<S, I> {
    state: Rc<S>,
    predecessor: Option<(usize, I)>,
    depth: usize,
    // Full hashes select a collision chain; Eq decides actual state identity.
    same_hash: Option<usize>,
}

/// Explore exact state equality. Hash collisions are resolved by `Eq`.
///
/// `GraphExhausted` means every reachable edge supplied by the adapter was
/// checked, within its declared model. It does not establish unspecified or
/// skipped properties. Depth cutoffs and resource cutoffs are distinct results.
/// A transition's properties are checked before deduplication or the state cap.
pub fn enumerate<M: Enumerate>(
    model: &M,
    config: SearchConfig,
) -> Result<SearchReport<M::Input>, ModelError>
where
    M::State: Hash,
{
    enumerate_with_limits(model, config, SearchLimits::default())
}

/// Uses incremental adapter input iteration and optional cooperative limits.
/// A pre-start cancellation/deadline invokes no model callbacks. Once initial
/// state or transition checking starts, its checks complete before a cutoff is
/// reported, so a discovered property failure is never replaced by a cutoff.
/// Byte limits govern admission, not allocation of an incoming candidate.
pub fn enumerate_with_limits<M: Enumerate>(
    model: &M,
    config: SearchConfig,
    limits: SearchLimits<'_>,
) -> Result<SearchReport<M::Input>, ModelError>
where
    M::State: Hash,
{
    if config.max_states == 0 {
        return Err(ModelError::new(
            "enumeration max_states must be greater than zero",
        ));
    }
    let control = Control::new(limits.control)?;
    let mut checks = Vec::new();
    let mut report = SearchReport {
        termination: SearchTermination::GraphExhausted,
        states: 0,
        transitions: 0,
        max_depth_reached: 0,
        skipped_checks: 0,
        estimated_retained_state_bytes: None,
        failure: None,
    };
    if let Some(stop) = control.stop() {
        report.termination = search_stop(stop);
        return Ok(report);
    }
    let state = model
        .initial_state()
        .map_err(|e| context("initial_state", e))?;
    report.states = 1;
    let violations = initial_checks(model, &state, &mut report.skipped_checks, &mut checks)?;
    if !violations.is_empty() {
        report.termination = SearchTermination::FailureFound;
        report.failure = Some(Failure {
            inputs: Vec::new(),
            violations,
        });
        return Ok(report);
    }
    if let Some(stop) = control.stop() {
        report.termination = search_stop(stop);
        return Ok(report);
    }
    let initial_bytes = model.estimated_state_bytes(&state);
    if let Some(maximum) = limits.max_estimated_state_bytes {
        let bytes = initial_bytes
            .ok_or_else(|| ModelError::new("state byte budget requires estimated_state_bytes"))?;
        if bytes > maximum {
            report.states = 0;
            report.estimated_retained_state_bytes = Some(0);
            report.termination = SearchTermination::EstimatedStateByteLimit;
            return Ok(report);
        }
    }
    report.estimated_retained_state_bytes = initial_bytes;
    let hashes = RandomState::new();
    let mut visited = HashMap::new();
    visited.insert(hashes.hash_one(&state), 0);
    let mut nodes = vec![Node {
        state: Rc::new(state),
        predecessor: None,
        depth: 0,
        same_hash: None,
    }];
    let mut queue = VecDeque::from([0]);
    let mut depth_cutoff = false;
    while let Some(index) = queue.pop_front() {
        if let Some(stop) = control.stop() {
            report.termination = search_stop(stop);
            return Ok(report);
        }
        let state = Rc::clone(&nodes[index].state);
        let depth = nodes[index].depth;
        let mut inputs = model
            .input_iter(&state)
            .map_err(|e| context("enumerate inputs", e))?;
        if let Some(stop) = control.stop() {
            report.termination = search_stop(stop);
            return Ok(report);
        }
        if depth == config.max_depth {
            // Probe only one item: no transition is executed at the depth boundary.
            let unexamined = inputs.next().is_some();
            if let Some(stop) = control.stop() {
                report.termination = search_stop(stop);
                return Ok(report);
            }
            depth_cutoff |= unexamined;
            continue;
        }
        loop {
            if let Some(stop) = control.stop() {
                report.termination = search_stop(stop);
                return Ok(report);
            }
            // One probe is necessary even at the edge cap to distinguish an
            // exhausted graph from an unexamined edge.
            let next = inputs.next();
            if let Some(stop) = control.stop() {
                report.termination = search_stop(stop);
                return Ok(report);
            }
            let Some(input) = next else {
                break;
            };
            if report.transitions == config.max_transitions {
                report.termination = SearchTermination::TransitionLimit;
                return Ok(report);
            }
            let transition = model.step(&state, &input).map_err(|e| context("step", e))?;
            report.transitions += 1;
            report.max_depth_reached = report.max_depth_reached.max(depth + 1);
            let violations = transition_checks(
                model,
                &state,
                &input,
                &transition,
                &mut report.skipped_checks,
                &mut checks,
            )?;
            if !violations.is_empty() {
                let mut inputs = path(&nodes, index);
                inputs.push(input);
                report.failure = Some(Failure { inputs, violations });
                report.termination = SearchTermination::FailureFound;
                return Ok(report);
            }
            if let Some(stop) = control.stop() {
                report.termination = search_stop(stop);
                return Ok(report);
            }
            let hash = hashes.hash_one(&transition.state);
            let mut candidate = visited.get(&hash).copied();
            let mut duplicate = false;
            while let Some(other) = candidate {
                if let Some(stop) = control.stop() {
                    report.termination = search_stop(stop);
                    return Ok(report);
                }
                if *nodes[other].state == transition.state {
                    duplicate = true;
                    break;
                }
                candidate = nodes[other].same_hash;
            }
            if duplicate {
                continue;
            }
            if nodes.len() == config.max_states {
                report.termination = SearchTermination::StateLimit;
                return Ok(report);
            }
            let estimate = model.estimated_state_bytes(&transition.state);
            let total = match (report.estimated_retained_state_bytes, estimate) {
                (Some(retained), Some(bytes)) => retained.checked_add(bytes),
                _ => None,
            };
            if let Some(maximum) = limits.max_estimated_state_bytes {
                if estimate.is_none() {
                    return Err(ModelError::new(
                        "state byte budget requires estimated_state_bytes",
                    ));
                }
                if total.is_none_or(|bytes| bytes > maximum) {
                    report.termination = SearchTermination::EstimatedStateByteLimit;
                    return Ok(report);
                }
            }
            let successor_index = nodes.len();
            let same_hash = visited.insert(hash, successor_index);
            nodes.push(Node {
                state: Rc::new(transition.state),
                predecessor: Some((index, input)),
                depth: depth + 1,
                same_hash,
            });
            queue.push_back(successor_index);
            report.states = nodes.len();
            report.estimated_retained_state_bytes = total;
        }
    }
    if depth_cutoff {
        report.termination = SearchTermination::DepthBound;
    }
    Ok(report)
}

fn search_stop(stop: Stop) -> SearchTermination {
    match stop {
        Stop::Deadline => SearchTermination::Deadline,
        Stop::Cancelled => SearchTermination::Cancelled,
    }
}

fn path<S, I: Clone>(nodes: &[Node<S, I>], mut index: usize) -> Vec<I> {
    let mut inputs = Vec::new();
    while let Some((predecessor, input)) = &nodes[index].predecessor {
        inputs.push(input.clone());
        index = *predecessor;
    }
    inputs.reverse();
    inputs
}

#[derive(Clone, Debug)]
pub struct FuzzConfig {
    pub seed: u64,
    pub cases: usize,
    pub max_steps: usize,
    pub max_transitions: u64,
    /// Chance, in 0..=100, to mutate the previous passing sequence. Mutation
    /// deletes a random span and replaces a position using state-aware generation.
    pub mutation_percent: u8,
}

impl Default for FuzzConfig {
    fn default() -> Self {
        Self {
            seed: 0,
            cases: 1_000,
            max_steps: 100,
            max_transitions: 100_000,
            mutation_percent: 50,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FuzzTermination {
    CasesCompleted,
    TransitionLimit,
    FailureFound,
    Deadline,
    Cancelled,
}

#[derive(Clone, Debug)]
pub struct FuzzReport<I> {
    pub termination: FuzzTermination,
    pub seed: u64,
    /// Cases started, including a case stopped by failure or the global budget.
    pub cases: usize,
    pub transitions: u64,
    pub skipped_checks: u64,
    pub failure: Option<Failure<I>>,
    /// Present for feedback-guided runs, with bounded concrete corpus evidence.
    pub guidance: Option<Box<crate::guided::GuidanceReport<I>>>,
}

/// Seeded stateful generation with a bounded, one-sequence mutation corpus.
/// A completed run reports only that these cases found no violation.
pub fn fuzz<M: Generate>(
    model: &M,
    config: FuzzConfig,
) -> Result<FuzzReport<M::Input>, ModelError> {
    fuzz_with_limits(model, config, RunLimits::default())
}

pub fn fuzz_with_limits<M: Generate>(
    model: &M,
    config: FuzzConfig,
    limits: RunLimits<'_>,
) -> Result<FuzzReport<M::Input>, ModelError> {
    fuzz_with_driver(model, config, limits, &mut PreviousSequence)
}

pub(crate) trait FuzzDriver<M: Generate> {
    fn candidate(
        &mut self,
        previous: &[M::Input],
        rng: &mut Rng,
        mutation_percent: u8,
    ) -> (Vec<M::Input>, Option<usize>);
    fn initial(&mut self, _state: &M::State, _case: usize) -> Result<(), ModelError> {
        Ok(())
    }
    fn transition(
        &mut self,
        _before: &M::State,
        _input: &M::Input,
        _after: &Transition<M::State, M::Output>,
        _inputs: &[M::Input],
        _case: usize,
        _transition: u64,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}
struct PreviousSequence;
impl<M: Generate> FuzzDriver<M> for PreviousSequence {
    fn candidate(
        &mut self,
        previous: &[M::Input],
        rng: &mut Rng,
        mutation_percent: u8,
    ) -> (Vec<M::Input>, Option<usize>) {
        let mutate =
            !previous.is_empty() && rng.index(100).unwrap() < usize::from(mutation_percent);
        if !mutate {
            return (Vec::new(), None);
        }
        let mut candidate = previous.to_vec();
        let start = rng.index(candidate.len()).unwrap();
        let length = 1 + rng.index(candidate.len() - start).unwrap();
        candidate.drain(start..start + length);
        let replacement = rng.index(candidate.len().saturating_add(1));
        (candidate, replacement)
    }
}
pub(crate) fn fuzz_with_driver<M: Generate, D: FuzzDriver<M>>(
    model: &M,
    config: FuzzConfig,
    limits: RunLimits<'_>,
    driver: &mut D,
) -> Result<FuzzReport<M::Input>, ModelError> {
    let control = Control::new(limits)?;
    let mut checks = Vec::new();
    if config.mutation_percent > 100 {
        return Err(ModelError::new("mutation_percent must be in 0..=100"));
    }
    let mut rng = Rng::new(config.seed);
    let mut previous = Vec::new();
    let mut report = FuzzReport {
        termination: FuzzTermination::CasesCompleted,
        seed: config.seed,
        cases: 0,
        transitions: 0,
        skipped_checks: 0,
        failure: None,
        guidance: None,
    };
    if let Some(stop) = control.stop() {
        report.termination = fuzz_stop(stop);
        return Ok(report);
    }
    for _ in 0..config.cases {
        if let Some(stop) = control.stop() {
            report.termination = fuzz_stop(stop);
            return Ok(report);
        }
        let mut state = model
            .initial_state()
            .map_err(|e| context("initial_state", e))?;
        report.cases += 1;
        let violations = initial_checks(model, &state, &mut report.skipped_checks, &mut checks)?;
        if !violations.is_empty() {
            report.failure = Some(Failure {
                inputs: Vec::new(),
                violations,
            });
            report.termination = FuzzTermination::FailureFound;
            return Ok(report);
        }
        if let Some(stop) = control.stop() {
            report.termination = fuzz_stop(stop);
            return Ok(report);
        }
        driver
            .initial(&state, report.cases)
            .map_err(|e| context("initial feedback", e))?;
        if let Some(stop) = control.stop() {
            report.termination = fuzz_stop(stop);
            return Ok(report);
        }
        let (mutated, replace_at) = driver.candidate(&previous, &mut rng, config.mutation_percent);
        let mut inputs = Vec::new();
        for step in 0..config.max_steps {
            if report.transitions == config.max_transitions {
                report.termination = FuzzTermination::TransitionLimit;
                return Ok(report);
            }
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            let reused = if replace_at != Some(step) {
                match mutated.get(step) {
                    Some(input)
                        if model
                            .is_enabled(&state, input)
                            .map_err(|e| context("is_enabled", e))? =>
                    {
                        Some(input.clone())
                    }
                    _ => None,
                }
            } else {
                None
            };
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            let already_enabled = reused.is_some();
            let next = match reused {
                Some(input) => Some(input),
                None => model
                    .generate(&state, &mut rng)
                    .map_err(|e| context("generate", e))?,
            };
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            let Some(input) = next else {
                break;
            };
            let enabled = already_enabled
                || model
                    .is_enabled(&state, &input)
                    .map_err(|e| context("is_enabled", e))?;
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            if !enabled {
                return Err(ModelError::new(
                    "generate returned a causally disabled input",
                ));
            }
            let transition = model.step(&state, &input).map_err(|e| context("step", e))?;
            report.transitions += 1;
            let violations = transition_checks(
                model,
                &state,
                &input,
                &transition,
                &mut report.skipped_checks,
                &mut checks,
            )?;
            inputs.push(input);
            if !violations.is_empty() {
                report.failure = Some(Failure { inputs, violations });
                report.termination = FuzzTermination::FailureFound;
                return Ok(report);
            }
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            driver
                .transition(
                    &state,
                    inputs.last().unwrap(),
                    &transition,
                    &inputs,
                    report.cases,
                    report.transitions,
                )
                .map_err(|e| context("transition feedback", e))?;
            if let Some(stop) = control.stop() {
                report.termination = fuzz_stop(stop);
                return Ok(report);
            }
            state = transition.state;
        }
        previous = inputs;
    }
    Ok(report)
}

fn fuzz_stop(stop: Stop) -> FuzzTermination {
    match stop {
        Stop::Deadline => FuzzTermination::Deadline,
        Stop::Cancelled => FuzzTermination::Cancelled,
    }
}

#[derive(Clone, Debug)]
pub struct ShrinkConfig {
    /// Includes the initial replay that validates the original failure. Must be > 0.
    pub max_attempts: usize,
}

impl Default for ShrinkConfig {
    fn default() -> Self {
        Self {
            max_attempts: 10_000,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShrinkTermination {
    /// No remaining candidate in the implemented deletion/simplification search.
    /// This does not promise a globally minimal counterexample.
    SearchComplete,
    AttemptLimit,
    TransitionLimit,
    Deadline,
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ShrinkOperation {
    ValidateOriginal,
    Remove { start: usize, end: usize },
    Simplify { index: usize, alternative: usize },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShrinkOutcome {
    Retained,
    InvalidCausality,
    DifferentFailure,
    NoFailure,
    AlreadySeen,
    /// Candidate replay stopped before it could establish an outcome.
    ResourceLimit,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShrinkAttempt {
    pub attempt: usize,
    pub candidate_length: usize,
    pub operation: ShrinkOperation,
    pub outcome: ShrinkOutcome,
}

#[derive(Clone, Debug)]
pub struct ShrinkReport<I> {
    pub original: Failure<I>,
    pub minimized: Failure<I>,
    pub attempts: usize,
    pub termination: ShrinkTermination,
    pub history: Vec<ShrinkAttempt>,
    /// Includes the replay used to validate the original failure.
    pub replayed_transitions: u64,
    /// False if a resource limit interrupted the initial validation. In that
    /// case minimized preserves the supplied evidence, without asserting replay.
    pub validated_original: bool,
}

/// Preserve the phase and stable check ID of the original's first violation.
/// Every accepted sequence is replayed with `is_enabled` checked before each
/// transition. Callback errors remain errors; they never count as reproductions.
/// Cyclic simplification hints are bounded by visited sequences and the budget.
pub fn shrink<M: Generate>(
    model: &M,
    original: &Failure<M::Input>,
    config: ShrinkConfig,
) -> Result<ShrinkReport<M::Input>, ModelError> {
    shrink_with_limits(model, original, config, ShrinkLimits::default())
}

/// Limits aggregate replay work in addition to attempted sequences. The original
/// is preserved even if its validation cannot finish; inspect validated_original.
pub fn shrink_with_limits<M: Generate>(
    model: &M,
    original: &Failure<M::Input>,
    config: ShrinkConfig,
    limits: ShrinkLimits<'_>,
) -> Result<ShrinkReport<M::Input>, ModelError> {
    let mut work = ShrinkWork {
        control: Control::new(limits.control)?,
        maximum: limits.max_replayed_transitions,
        max_attempts: config.max_attempts,
        transitions: 0,
        checks: Vec::new(),
    };
    if config.max_attempts == 0 {
        return Err(ModelError::new(
            "shrink max_attempts must be greater than zero",
        ));
    }
    let target = original
        .violations
        .first()
        .filter(|failure| failure.check.is_failure())
        .ok_or_else(|| ModelError::new("cannot shrink a sequence without a target violation"))?;
    let validation = replay_candidate(model, &original.inputs, &mut work)?;
    let (minimized, validated_original, termination, outcome) = match validation {
        Candidate::Stopped(reason) => (
            original.clone(),
            false,
            reason,
            ShrinkOutcome::ResourceLimit,
        ),
        Candidate::Failure(validated) => {
            if !has_target(&validated, target) {
                return Err(ModelError::new(
                    "original failure does not reproduce the target check and phase",
                ));
            }
            (
                validated,
                true,
                ShrinkTermination::SearchComplete,
                ShrinkOutcome::Retained,
            )
        }
        _ => {
            return Err(ModelError::new(
                "original failure does not reproduce with valid causality",
            ));
        }
    };
    let mut report = ShrinkReport {
        original: original.clone(),
        minimized,
        attempts: 1,
        termination,
        history: vec![ShrinkAttempt {
            attempt: 1,
            candidate_length: original.inputs.len(),
            operation: ShrinkOperation::ValidateOriginal,
            outcome,
        }],
        replayed_transitions: work.transitions,
        validated_original,
    };
    if !validated_original {
        return Ok(report);
    }
    let mut seen = vec![report.minimized.inputs.clone()];

    loop {
        // Chunk deletion, then progressively finer deletions. Restart after an
        // accepted simplification because different values can enable deletions.
        let mut groups = 2;
        while !report.minimized.inputs.is_empty() {
            let length = report.minimized.inputs.len();
            let chunk = length.div_ceil(groups);
            let mut accepted = false;
            for start in (0..length).step_by(chunk) {
                if let Some(stop) = work.control.stop() {
                    report.termination = shrink_stop(stop);
                    return Ok(report);
                }
                let end = start.saturating_add(chunk).min(length);
                let mut candidate = report.minimized.inputs.clone();
                candidate.drain(start..end);
                if seen.contains(&candidate) {
                    continue;
                }
                if !attempt(
                    model,
                    target,
                    candidate,
                    ShrinkOperation::Remove { start, end },
                    &mut report,
                    &mut seen,
                    &mut work,
                )? {
                    return Ok(report);
                }
                if report
                    .history
                    .last()
                    .is_some_and(|a| a.outcome == ShrinkOutcome::Retained)
                {
                    accepted = true;
                    groups = groups.saturating_sub(1).max(2);
                    break;
                }
            }
            if accepted {
                continue;
            }
            if groups >= length {
                break;
            }
            groups = groups.saturating_mul(2).min(length);
        }

        let mut simplified = false;
        'positions: for index in 0..report.minimized.inputs.len() {
            if let Some(stop) = work.control.stop() {
                report.termination = shrink_stop(stop);
                return Ok(report);
            }
            let alternatives = model.simpler_inputs(&report.minimized.inputs[index]);
            if let Some(stop) = work.control.stop() {
                report.termination = shrink_stop(stop);
                return Ok(report);
            }
            for (alternative, input) in alternatives.into_iter().enumerate() {
                if let Some(stop) = work.control.stop() {
                    report.termination = shrink_stop(stop);
                    return Ok(report);
                }
                let mut candidate = report.minimized.inputs.clone();
                candidate[index] = input;
                if seen.contains(&candidate) {
                    continue;
                }
                if !attempt(
                    model,
                    target,
                    candidate,
                    ShrinkOperation::Simplify { index, alternative },
                    &mut report,
                    &mut seen,
                    &mut work,
                )? {
                    return Ok(report);
                }
                if report
                    .history
                    .last()
                    .is_some_and(|a| a.outcome == ShrinkOutcome::Retained)
                {
                    simplified = true;
                    break 'positions;
                }
            }
        }
        if !simplified {
            return Ok(report);
        }
    }
}

fn attempt<M: Generate>(
    model: &M,
    target: &PropertyFailure,
    candidate: Vec<M::Input>,
    operation: ShrinkOperation,
    report: &mut ShrinkReport<M::Input>,
    seen: &mut Vec<Vec<M::Input>>,
    work: &mut ShrinkWork<'_>,
) -> Result<bool, ModelError> {
    if report.attempts == work.max_attempts {
        report.termination = ShrinkTermination::AttemptLimit;
        return Ok(false);
    }
    report.attempts += 1;
    let candidate_length = candidate.len();
    let replayed = replay_candidate(model, &candidate, work)?;
    report.replayed_transitions = work.transitions;
    let mut stopped = false;
    let outcome = match replayed {
        Candidate::Stopped(reason) => {
            report.termination = reason;
            stopped = true;
            ShrinkOutcome::ResourceLimit
        }
        Candidate::Invalid => ShrinkOutcome::InvalidCausality,
        Candidate::Pass => ShrinkOutcome::NoFailure,
        Candidate::Failure(failure) if has_target(&failure, target) => {
            // Replaying can expose the same failure earlier. Avoid returning to
            // an already accepted prefix even when the full candidate is new.
            if seen.contains(&failure.inputs) {
                ShrinkOutcome::AlreadySeen
            } else {
                seen.push(failure.inputs.clone());
                report.minimized = failure;
                ShrinkOutcome::Retained
            }
        }
        Candidate::Failure(_) => ShrinkOutcome::DifferentFailure,
    };
    report.history.push(ShrinkAttempt {
        attempt: report.attempts,
        candidate_length,
        operation,
        outcome,
    });
    Ok(!stopped)
}

struct ShrinkWork<'a> {
    control: Control<'a>,
    maximum: Option<u64>,
    max_attempts: usize,
    transitions: u64,
    checks: Vec<Check>,
}

enum Candidate<I> {
    Pass,
    Invalid,
    Failure(Failure<I>),
    Stopped(ShrinkTermination),
}

fn replay_candidate<M: Generate>(
    model: &M,
    inputs: &[M::Input],
    work: &mut ShrinkWork<'_>,
) -> Result<Candidate<M::Input>, ModelError> {
    if let Some(stop) = work.control.stop() {
        return Ok(Candidate::Stopped(shrink_stop(stop)));
    }
    let mut state = model
        .initial_state()
        .map_err(|e| context("shrink initial_state", e))?;
    let mut skipped = 0;
    let violations = initial_checks(model, &state, &mut skipped, &mut work.checks)?;
    if !violations.is_empty() {
        return Ok(Candidate::Failure(Failure {
            inputs: Vec::new(),
            violations,
        }));
    }
    if let Some(stop) = work.control.stop() {
        return Ok(Candidate::Stopped(shrink_stop(stop)));
    }
    for (index, input) in inputs.iter().enumerate() {
        if let Some(stop) = work.control.stop() {
            return Ok(Candidate::Stopped(shrink_stop(stop)));
        }
        if work
            .maximum
            .is_some_and(|maximum| work.transitions >= maximum)
        {
            return Ok(Candidate::Stopped(ShrinkTermination::TransitionLimit));
        }
        let enabled = model
            .is_enabled(&state, input)
            .map_err(|e| context("shrink is_enabled", e))?;
        if let Some(stop) = work.control.stop() {
            return Ok(Candidate::Stopped(shrink_stop(stop)));
        }
        if !enabled {
            return Ok(Candidate::Invalid);
        }
        let transition = model
            .step(&state, input)
            .map_err(|e| context("shrink step", e))?;
        work.transitions += 1;
        let violations = transition_checks(
            model,
            &state,
            input,
            &transition,
            &mut skipped,
            &mut work.checks,
        )?;
        if !violations.is_empty() {
            return Ok(Candidate::Failure(Failure {
                inputs: inputs[..=index].to_vec(),
                violations,
            }));
        }
        if let Some(stop) = work.control.stop() {
            return Ok(Candidate::Stopped(shrink_stop(stop)));
        }
        state = transition.state;
    }
    Ok(Candidate::Pass)
}

fn shrink_stop(stop: Stop) -> ShrinkTermination {
    match stop {
        Stop::Deadline => ShrinkTermination::Deadline,
        Stop::Cancelled => ShrinkTermination::Cancelled,
    }
}

fn has_target<I>(failure: &Failure<I>, target: &PropertyFailure) -> bool {
    failure.violations.iter().any(|violation| {
        violation.phase == target.phase
            && violation.check.id == target.check.id
            && violation.check.is_failure()
    })
}

fn context(phase: &str, error: ModelError) -> ModelError {
    ModelError::new(format!("{phase}: {error}"))
}

fn observe(
    checks: &mut Vec<Check>,
    state_count: usize,
    initial: bool,
    skipped: &mut u64,
) -> Vec<PropertyFailure> {
    checks
        .drain(..)
        .enumerate()
        .filter_map(|(index, check)| {
            let phase = if initial {
                CheckPhase::InitialState
            } else if index < state_count {
                CheckPhase::State
            } else {
                CheckPhase::Transition
            };
            match &check.status {
                CheckStatus::Failed(_) => Some(PropertyFailure { phase, check }),
                CheckStatus::Skipped(_) => {
                    *skipped = skipped.saturating_add(1);
                    None
                }
                CheckStatus::Passed => None,
            }
        })
        .collect()
}

fn initial_checks<M: Model>(
    model: &M,
    state: &M::State,
    skipped: &mut u64,
    checks: &mut Vec<Check>,
) -> Result<Vec<PropertyFailure>, ModelError> {
    checks.clear();
    model
        .check_state_into(state, checks)
        .map_err(|e| context("initial check_state", e))?;
    Ok(observe(checks, checks.len(), true, skipped))
}

fn transition_checks<M: Model>(
    model: &M,
    before: &M::State,
    input: &M::Input,
    transition: &Transition<M::State, M::Output>,
    skipped: &mut u64,
    checks: &mut Vec<Check>,
) -> Result<Vec<PropertyFailure>, ModelError> {
    checks.clear();
    model
        .check_state_into(&transition.state, checks)
        .map_err(|e| context("check_state", e))?;
    let state_count = checks.len();
    model
        .check_transition_into(before, input, &transition.as_ref(), checks)
        .map_err(|e| context("check_transition", e))?;
    if checks.len() < state_count {
        return Err(ModelError::new(
            "check_transition_into removed state observations",
        ));
    }
    Ok(observe(checks, state_count, false, skipped))
}
