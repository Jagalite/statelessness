//! Offline v1/v2 evidence inspection. Reading and selecting a stored failure do
//! not run a model. Replay and minimization are separate explicit operations.
use stateless::evidence::{Artifact, EvidenceLimits, StepEvidence};
use stateless::explore::{PropertyFailure, ShrinkConfig, ShrinkLimits, ShrinkReport};
use stateless::{Check, Generate, ModelCodec, ModelError};
use std::io::Read;

pub struct EvidenceViewer {
    artifact: Artifact,
    limits: EvidenceLimits,
}

pub struct Boundary<'a> {
    /// Exact bytes may contain secrets; inspectors do not sanitize this view.
    pub before: &'a [u8],
    pub after: &'a [u8],
    pub input: Option<&'a [u8]>,
    pub outputs: &'a [Vec<u8>],
    pub checks: &'a [Check],
    pub causal: Option<&'a StepEvidence>,
}

impl EvidenceViewer {
    pub fn read(reader: impl Read, limits: &EvidenceLimits) -> Result<Self, ModelError> {
        Self::new(Artifact::read(reader, limits)?, limits)
    }
    pub fn new(artifact: Artifact, limits: &EvidenceLimits) -> Result<Self, ModelError> {
        match &artifact {
            Artifact::V1(trace) => {
                stateless::execution::validate_recording(trace)?;
                trace
                    .write_with_limits(std::io::sink(), &limits.trace)
                    .map_err(|e| ModelError::new(e.to_string()))?;
            }
            Artifact::V2(evidence) => evidence.validate(limits)?,
        }
        Ok(Self {
            artifact,
            limits: limits.clone(),
        })
    }
    pub fn artifact(&self) -> &Artifact {
        &self.artifact
    }
    /// Local sequence zero is the retained checkpoint, not necessarily run start.
    pub fn boundary(&self, sequence: usize) -> Option<Boundary<'_>> {
        let trace = self.artifact.trace();
        if sequence == 0 {
            return Some(Boundary {
                before: &trace.initial_state,
                after: &trace.initial_state,
                input: None,
                outputs: &[],
                checks: &trace.initial_checks,
                causal: None,
            });
        }
        let step = trace.steps.get(sequence - 1)?;
        let before = if sequence == 1 {
            &trace.initial_state
        } else {
            &trace.steps[sequence - 2].post_state
        };
        let causal = match &self.artifact {
            Artifact::V2(v) => v.steps.get(sequence - 1),
            Artifact::V1(_) => None,
        };
        Some(Boundary {
            before,
            after: &step.post_state,
            input: Some(&step.input),
            outputs: &step.outputs,
            checks: &step.checks,
            causal,
        })
    }
    /// Select a failing check and its recorded phase. Legacy noninitial phases
    /// must be explicitly supplied to workbench::minimize or verified/upgraded.
    pub fn failure(&self, sequence: usize, check: usize) -> Result<PropertyFailure, ModelError> {
        let selected = self
            .boundary(sequence)
            .and_then(|b| b.checks.get(check))
            .ok_or_else(|| ModelError::new("unknown evidence check"))?;
        if !selected.is_failure() {
            return Err(ModelError::new("selected check is not a failure"));
        }
        let phase = self.artifact.check_phase(sequence, check).ok_or_else(|| {
            ModelError::new("v1 phase unknown; explicitly verify and upgrade or supply a phase")
        })?;
        Ok(PropertyFailure {
            phase,
            check: selected.clone(),
        })
    }
    pub fn minimize<M: ModelCodec + Generate>(
        &self,
        model: &M,
        sequence: usize,
        check: usize,
        config: ShrinkConfig,
        limits: ShrinkLimits<'_>,
    ) -> Result<ShrinkReport<M::Input>, ModelError> {
        let target = self.failure(sequence, check)?;
        let mut remaining = limits;
        if let Artifact::V2(evidence) = &self.artifact {
            #[cfg(all(target_family = "wasm", target_os = "unknown"))]
            if limits.control.max_duration.is_some() {
                return Err(ModelError::new(
                    "wall-clock minimization limits are unavailable on this Wasm target",
                ));
            }
            let count = evidence.trace.steps.len() as u64;
            if limits
                .max_replayed_transitions
                .is_some_and(|maximum| count > maximum)
            {
                return Err(ModelError::new(
                    "minimization budget cannot cover v2 phase verification",
                ));
            }
            let started = limits
                .control
                .max_duration
                .map(|_| std::time::Instant::now());
            let stopped = || -> Result<(), ModelError> {
                if limits
                    .control
                    .cancellation
                    .is_some_and(|flag| flag.load(std::sync::atomic::Ordering::Relaxed))
                {
                    return Err(ModelError::new(
                        "minimization cancelled during v2 verification",
                    ));
                }
                if started
                    .zip(limits.control.max_duration)
                    .is_some_and(|(start, maximum)| start.elapsed() >= maximum)
                {
                    return Err(ModelError::new(
                        "minimization deadline exhausted during v2 verification",
                    ));
                }
                Ok(())
            };
            stopped()?;
            let verified = stateless::execution::replay_with_observations_bounded(
                model,
                &evidence.trace,
                stateless::execution::ReplayOptions::default(),
                &self.limits.trace,
                |observation, report| {
                    stopped()?;
                    if let stateless::execution::ReplayObservation::Turn(turn) = observation {
                        let index = usize::try_from(turn.actual.sequence - 1)
                            .map_err(|_| ModelError::new("v2 phase index overflow"))?;
                        if report.outcome == stateless::execution::ReplayOutcome::Exact
                            && evidence.steps[index].state_check_count as usize
                                != turn.actual.state_check_count
                        {
                            return Err(ModelError::new("v2 phase evidence differs from replay"));
                        }
                    }
                    Ok(())
                },
            )
            .map_err(|error| error.error)?;
            if verified.outcome != stateless::execution::ReplayOutcome::Exact
                || !verified.failure_reproduced
            {
                return Err(ModelError::new(
                    "minimization requires exact v2 phase and failure replay",
                ));
            }
            remaining.max_replayed_transitions = limits
                .max_replayed_transitions
                .map(|maximum| maximum - count);
            if let Some((started, maximum)) = started.zip(limits.control.max_duration) {
                remaining.control.max_duration =
                    Some(maximum.checked_sub(started.elapsed()).ok_or_else(|| {
                        ModelError::new("minimization deadline exhausted during v2 verification")
                    })?);
            }
        }
        let mut report =
            crate::workbench::minimize(model, self.artifact.trace(), target, config, remaining)?;
        if matches!(self.artifact, Artifact::V2(_)) {
            report.replayed_transitions = report
                .replayed_transitions
                .checked_add(self.artifact.trace().steps.len() as u64)
                .ok_or_else(|| ModelError::new("minimization replay count overflow"))?;
        }
        Ok(report)
    }
}
