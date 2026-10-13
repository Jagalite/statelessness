//! Independently declared requirement catalog and transition adequacy goals.
use super::*;
use crate::assurance::{
    self, CoverGoal, Coverage, CoverageHits, FamilyCoverage, GoalScope, InputFamily, Manifest,
    PropertyPhase, Provenance, RequiredProperty, RequiredSearchOutcome,
};

pub const COVER_GOALS: &[&str] = &[
    "supersede_running",
    "stale_success",
    "cancel_ack",
    "retry_started",
    "duplicate_completion",
    "settled_cleanup",
];

/// Required property IDs come from a separate catalog, never from observations or
/// from PROPERTY_IDS. Removing a check therefore yields a missing requirement.
pub fn manifest(model: &Controller) -> Result<Manifest, ModelError> {
    let required_properties = include_str!("../../docs/requirements/controller-properties.txt")
        .lines()
        .map(
            |line| match line.split_whitespace().collect::<Vec<_>>().as_slice() {
                [phase, id] => Ok(RequiredProperty {
                    id: (*id).into(),
                    phase: match *phase {
                        "state" => PropertyPhase::State,
                        "transition" => PropertyPhase::Transition,
                        _ => return Err(ModelError::new("invalid independent property phase")),
                    },
                }),
                _ => Err(ModelError::new("invalid independent property declaration")),
            },
        )
        .collect::<Result<Vec<_>, _>>()?;
    let fingerprint = |text: &str| {
        format!(
            "fnv1a64:{:016x}",
            text.bytes()
                .fold(0xcbf29ce484222325u64, |h, b| (h ^ u64::from(b))
                    .wrapping_mul(0x100000001b3))
        )
    };
    let manifest = Manifest {
        assurance_version: assurance::ASSURANCE_VERSION,
        model: "reference-controller".into(),
        required_properties,
        input_families: ["submit", "cancel", "complete", "cancelled", "tick"]
            .into_iter()
            .map(|name| InputFamily {
                name: name.into(),
                coverage: FamilyCoverage::Required,
            })
            .collect(),
        bounds: [
            ("max_generations", model.bounds.generations.to_string()),
            ("max_attempts", model.bounds.attempts.to_string()),
            ("max_time", model.bounds.time.to_string()),
            (
                "payload",
                "outcome abstraction; no real file/network effects during verification".into(),
            ),
            (
                "delivery",
                "unknown/stale/duplicate deliveries included; eventual delivery not proven".into(),
            ),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), v))
        .collect(),
        cover_goals: COVER_GOALS
            .iter()
            .map(|id| CoverGoal {
                id: (*id).into(),
                scope: GoalScope::Transition,
            })
            .collect(),
        required_search_outcome: RequiredSearchOutcome::GraphExhausted,
        provenance: Provenance {
            manifest: fingerprint(include_str!("../../docs/requirements/controller.json")),
            model: model.metadata().build,
            properties: fingerprint(include_str!(
                "../../docs/requirements/controller-properties.txt"
            )),
        },
    };
    manifest.validate()?;
    Ok(manifest)
}
pub struct ReferenceCoverage;
impl Coverage<Controller> for ReferenceCoverage {
    fn observe(
        &self,
        before: &State,
        input: &Input,
        after: &TransitionRef<'_, State, Effect>,
        _: &[Input],
        hits: &mut CoverageHits,
    ) -> Result<(), ModelError> {
        hits.family(match input {
            Input::Submit => "submit",
            Input::Cancel { .. } => "cancel",
            Input::Complete { .. } => "complete",
            Input::Cancelled { .. } => "cancelled",
            Input::Tick { .. } => "tick",
        });
        if matches!(input, Input::Submit)
            && before
                .current
                .is_some_and(|g| before.job(g).is_some_and(|j| j.phase == Phase::Running))
            && after.state.jobs.len() > before.jobs.len()
        {
            hits.goal("supersede_running");
        }
        if matches!(input, Input::Complete { generation, outcome: Outcome::Success, .. } if before.current != Some(*generation) && before.job(*generation).is_some_and(|j| j.phase == Phase::Cancelling))
        {
            hits.goal("stale_success");
        }
        if matches!(input, Input::Cancelled { generation, attempt } if before.job(*generation).is_some_and(|j| j.phase == Phase::Cancelling && j.attempt == *attempt))
        {
            hits.goal("cancel_ack");
        }
        if matches!(input, Input::Tick { .. })
            && after
                .outputs
                .iter()
                .any(|e| matches!(e, Effect::Start { attempt, .. } if *attempt > 1))
        {
            hits.goal("retry_started");
        }
        if matches!(input, Input::Complete { generation, .. } if before.job(*generation).is_some_and(|j| matches!(j.phase, Phase::Settled(_))))
        {
            hits.goal("duplicate_completion");
        }
        if after
            .outputs
            .iter()
            .any(|e| matches!(e, Effect::Release { .. }))
        {
            hits.goal("settled_cleanup");
        }
        Ok(())
    }
}
