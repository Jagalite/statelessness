//! Optional, versioned verification adequacy over actual checked transitions.
//!
//! A manifest is an independent requirements catalog, not an input generator.
//! Missing requirements, graph completion, and cover goals are separate facts.
//! The engine remains dependency-free; JSON is an export format, not a runtime.

use std::collections::{BTreeMap, BTreeSet};
use std::hash::Hash;

use crate::explore::{
    self, FuzzConfig, FuzzReport, FuzzTermination, RunLimits, SearchConfig, SearchLimits,
    SearchObserver, SearchReport, SearchTermination,
};
use crate::{
    Check, CheckStatus, Enumerate, Generate, Model, ModelError, ModelMetadata, TransitionRef,
};

pub const ASSURANCE_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum PropertyPhase {
    State,
    Transition,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct RequiredProperty {
    pub id: String,
    pub phase: PropertyPhase,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FamilyCoverage {
    Required,
    Excluded { reason: String },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InputFamily {
    pub name: String,
    pub coverage: FamilyCoverage,
}
/// Transition goals may use history encoded in exact model state. History goals
/// inspect the concrete prefix and cannot be proved unreachable by graph dedup.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GoalScope {
    Transition,
    History,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CoverGoal {
    pub id: String,
    pub scope: GoalScope,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RequiredSearchOutcome {
    GraphExhausted,
    Completed,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Provenance {
    pub manifest: String,
    pub model: String,
    pub properties: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Manifest {
    pub assurance_version: u32,
    pub model: String,
    pub required_properties: Vec<RequiredProperty>,
    pub input_families: Vec<InputFamily>,
    /// Payload, generation, timer, schedule and queue assumptions. These are
    /// declarations, not engine-enforced limits; reviewers must inspect the model.
    pub bounds: BTreeMap<String, String>,
    pub cover_goals: Vec<CoverGoal>,
    pub required_search_outcome: RequiredSearchOutcome,
    pub provenance: Provenance,
}
impl Manifest {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.assurance_version != ASSURANCE_VERSION {
            return Err(ModelError::new("unsupported assurance manifest version"));
        }
        for value in [
            &self.model,
            &self.provenance.manifest,
            &self.provenance.model,
            &self.provenance.properties,
        ] {
            if value.trim().is_empty() {
                return Err(ModelError::new(
                    "assurance identity/provenance must not be empty",
                ));
            }
        }
        let mut properties = BTreeSet::new();
        for p in &self.required_properties {
            if p.id.trim().is_empty() || !properties.insert(p.clone()) {
                return Err(ModelError::new("empty or duplicate required property"));
            }
        }
        let mut families = BTreeSet::new();
        for f in &self.input_families {
            if f.name.trim().is_empty() || !families.insert(&f.name) {
                return Err(ModelError::new("empty or duplicate input family"));
            }
            if matches!(&f.coverage, FamilyCoverage::Excluded { reason } if reason.trim().is_empty())
            {
                return Err(ModelError::new("excluded input family needs a rationale"));
            }
        }
        let mut goals = BTreeSet::new();
        for g in &self.cover_goals {
            if g.id.trim().is_empty() || !goals.insert(&g.id) {
                return Err(ModelError::new("empty or duplicate cover goal"));
            }
        }
        if self
            .bounds
            .iter()
            .any(|(k, v)| k.trim().is_empty() || v.trim().is_empty())
        {
            return Err(ModelError::new(
                "environment bound names and assumptions must not be empty",
            ));
        }
        Ok(())
    }
    /// Copy an existing domain's declared assumptions, retaining manual additions.
    /// This does not certify that all inputs in that domain were exercised.
    pub fn add_domain(&mut self, domain: &crate::modeling::DomainDescriptor) {
        self.bounds.insert(
            format!("domain.{}.assumptions", domain.name),
            domain.assumptions.into(),
        );
        self.bounds.insert(
            format!("domain.{}.max_entries", domain.name),
            domain.max_entries.to_string(),
        );
    }
    /// Explicit property metadata can be supplied by handwritten/generated hosts.
    /// It is never inferred from only the checks that happened to run.
    pub fn require(&mut self, id: impl Into<String>, phase: PropertyPhase) {
        self.required_properties.push(RequiredProperty {
            id: id.into(),
            phase,
        });
    }
}

/// Observation callbacks must be deterministic and free of application effects.
/// They receive the actual reducer result and never invoke the reducer themselves.
/// Prefix history includes the current input. For BFS it is the representative
/// shortest path to this edge, not every possible history reaching its source.
pub trait Coverage<M: Model> {
    fn observe(
        &self,
        before: &M::State,
        input: &M::Input,
        after: &TransitionRef<'_, M::State, M::Output>,
        history: &[M::Input],
        hits: &mut CoverageHits,
    ) -> Result<(), ModelError>;
}
impl<M: Model> Coverage<M> for () {
    fn observe(
        &self,
        _: &M::State,
        _: &M::Input,
        _: &TransitionRef<'_, M::State, M::Output>,
        _: &[M::Input],
        _: &mut CoverageHits,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}
#[derive(Default)]
pub struct CoverageHits {
    families: BTreeSet<String>,
    goals: BTreeSet<String>,
}
impl CoverageHits {
    pub fn family(&mut self, name: &str) {
        self.families.insert(name.into());
    }
    pub fn goal(&mut self, id: &str) {
        self.goals.insert(id.into());
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CheckCounts {
    pub passed: u64,
    pub failed: u64,
    pub skipped: u64,
    /// Applicable completed batches that omitted this required property.
    pub missing: u64,
}
impl CheckCounts {
    fn record(&mut self, check: &Check) {
        let target = match check.status {
            CheckStatus::Passed => &mut self.passed,
            CheckStatus::Failed(_) => &mut self.failed,
            CheckStatus::Skipped(_) => &mut self.skipped,
        };
        *target = target.saturating_add(1);
    }
    fn merge(&mut self, other: &Self) {
        self.passed = self.passed.saturating_add(other.passed);
        self.failed = self.failed.saturating_add(other.failed);
        self.skipped = self.skipped.saturating_add(other.skipped);
        self.missing = self.missing.saturating_add(other.missing);
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PropertyStatus {
    Passed,
    Failed,
    Skipped,
    Missing,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PropertyResult {
    pub property: RequiredProperty,
    pub status: PropertyStatus,
    pub counts: CheckCounts,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CoverageStatus {
    Observed,
    UnreachableUnderDeclaredModel,
    NotReachedBeforeStop,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GoalResult {
    pub goal: CoverGoal,
    pub status: CoverageStatus,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FamilyStatus {
    Observed,
    Missing,
    Excluded { reason: String },
    ExclusionViolated { reason: String },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FamilyResult {
    pub name: String,
    pub status: FamilyStatus,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchOutcome {
    GraphExhausted,
    CasesCompleted,
    DepthLimited,
    BudgetLimited,
    Interrupted,
    TimedOut,
    Failed,
    NotStarted,
}
impl SearchOutcome {
    fn completed(self) -> bool {
        matches!(self, Self::GraphExhausted | Self::CasesCompleted)
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SearchEvidence {
    pub outcome: SearchOutcome,
    pub termination: String,
    pub budgets: BTreeMap<String, String>,
    pub checked_transitions: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AssuranceReport {
    pub manifest: Manifest,
    /// Actual model identity, independently of the manifest's claimed provenance.
    pub metadata: ModelMetadata,
    pub search: SearchEvidence,
    pub properties: Vec<PropertyResult>,
    pub families: Vec<FamilyResult>,
    pub coverage: Vec<GoalResult>,
    /// Includes undeclared failures/skips: adding a manifest cannot hide them.
    pub observed_properties: BTreeMap<RequiredProperty, CheckCounts>,
    /// Retained callback hits, independent of the derived family result catalog.
    pub observed_families: BTreeSet<String>,
    /// Retained callback hits, independent of the derived goal result catalog.
    pub observed_goals: BTreeSet<String>,
    pub callback_errors: Vec<String>,
    pub unclassified_transitions: u64,
}
/// Green requires an explicitly chosen policy. Strict has no vacuous empty
/// property/input catalog success; cover goals may intentionally be absent.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AssurancePolicy {
    Strict,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Assessment {
    pub adequate: bool,
    pub reasons: Vec<String>,
    pub confidence: String,
}
impl AssuranceReport {
    /// Validate internal evidence consistency before assessing or merging a
    /// public report. This detects missing/altered derived catalogs, not forged
    /// underlying observations; provenance and callback honesty remain external
    /// obligations, and this is not cryptographic attestation.
    pub fn validate(&self) -> Result<(), ModelError> {
        self.manifest.validate()?;
        if self.manifest.model != self.metadata.name || self.search.termination.trim().is_empty() {
            return Err(ModelError::new(
                "assurance report identity or termination is inconsistent",
            ));
        }
        if self.properties != property_results(&self.manifest, &self.observed_properties) {
            return Err(ModelError::new(
                "assurance property catalog does not match observed evidence",
            ));
        }
        if self.observed_families.iter().any(|name| {
            !self
                .manifest
                .input_families
                .iter()
                .any(|family| &family.name == name)
        }) || self
            .observed_goals
            .iter()
            .any(|id| !self.manifest.cover_goals.iter().any(|goal| &goal.id == id))
        {
            return Err(ModelError::new(
                "assurance coverage evidence contains an undeclared identity",
            ));
        }
        if self.families != family_results(&self.manifest, &self.observed_families) {
            return Err(ModelError::new(
                "assurance family catalog does not match manifest",
            ));
        }
        if self.coverage
            != goal_results(
                &self.manifest,
                &self.observed_goals,
                self.search.outcome == SearchOutcome::GraphExhausted
                    && self.callback_errors.is_empty(),
            )
        {
            return Err(ModelError::new(
                "assurance goal catalog does not match manifest or completion",
            ));
        }
        if self.search.checked_transitions == 0
            && (!self.observed_families.is_empty() || !self.observed_goals.is_empty())
        {
            return Err(ModelError::new(
                "assurance coverage has no checked transition evidence",
            ));
        }
        if self.unclassified_transitions > self.search.checked_transitions {
            return Err(ModelError::new(
                "assurance unclassified count exceeds checked transitions",
            ));
        }
        Ok(())
    }
    pub fn assess(&self, policy: AssurancePolicy) -> Assessment {
        match policy {
            AssurancePolicy::Strict => {}
        }
        let mut reasons = Vec::new();
        if let Err(error) = self.validate() {
            return Assessment {
                adequate: false,
                reasons: vec![format!("inconsistent assurance report: {error}")],
                confidence:
                    "The report is internally inconsistent; no adequacy claim is established."
                        .into(),
            };
        }
        if self.manifest.required_properties.is_empty() {
            reasons.push("no expected properties declared".into());
        }
        if !self
            .manifest
            .input_families
            .iter()
            .any(|f| matches!(f.coverage, FamilyCoverage::Required))
        {
            reasons.push("no required input families declared".into());
        }
        for p in &self.properties {
            if p.status != PropertyStatus::Passed {
                reasons.push(format!(
                    "property {} {:?}: {:?}",
                    p.property.id, p.property.phase, p.status
                ));
            }
        }
        if self
            .observed_properties
            .values()
            .any(|c| c.failed > 0 || c.skipped > 0 || c.missing > 0)
        {
            reasons.push("observed failed, skipped, or omitted required checks".into());
        }
        for f in &self.families {
            if matches!(
                f.status,
                FamilyStatus::Missing | FamilyStatus::ExclusionViolated { .. }
            ) {
                reasons.push(format!("input family {}: {:?}", f.name, f.status));
            }
        }
        for g in &self.coverage {
            if g.status != CoverageStatus::Observed {
                reasons.push(format!("cover goal {}: {:?}", g.goal.id, g.status));
            }
        }
        if self.unclassified_transitions > 0 {
            reasons.push("some checked inputs had no declared family classification".into());
        }
        if !self.callback_errors.is_empty() {
            reasons.push("model or observation callback error".into());
        }
        let complete = match self.manifest.required_search_outcome {
            RequiredSearchOutcome::GraphExhausted => {
                self.search.outcome == SearchOutcome::GraphExhausted
            }
            RequiredSearchOutcome::Completed => self.search.outcome.completed(),
        };
        if !complete {
            reasons.push(format!(
                "required search outcome not met: {:?}",
                self.search.outcome
            ));
        }
        let adequate = reasons.is_empty();
        let confidence = if adequate && self.search.outcome == SearchOutcome::GraphExhausted {
            "The declared properties held for the exhausted finite model and declared adequacy goals were observed."
        } else if adequate {
            "The declared properties held for the completed sampled cases and declared adequacy goals were observed."
        } else { "Declared assurance requirements are not fully satisfied; no broader adequacy claim is established." }.into();
        Assessment {
            adequate,
            reasons,
            confidence,
        }
    }
}

/// Existing explorers without this optional layer make no adequacy assessment.
pub const ADEQUACY_NOT_ASSESSED: &str = "adequacy not assessed";
pub struct AssuredRun<R> {
    pub search: Result<R, ModelError>,
    pub assurance: AssuranceReport,
}

/// Caller-owned observations survive model errors and partial search termination.
pub struct Observer<C> {
    manifest: Manifest,
    metadata: ModelMetadata,
    coverage: C,
    properties: BTreeMap<RequiredProperty, CheckCounts>,
    families: BTreeSet<String>,
    goals: BTreeSet<String>,
    errors: BTreeSet<String>,
    transitions: u64,
    unclassified: u64,
}
impl<C> Observer<C> {
    pub fn new(
        manifest: Manifest,
        metadata: ModelMetadata,
        coverage: C,
    ) -> Result<Self, ModelError> {
        manifest.validate()?;
        if manifest.model != metadata.name {
            return Err(ModelError::new(
                "assurance manifest model does not match actual model metadata",
            ));
        }
        Ok(Self {
            manifest,
            metadata,
            coverage,
            properties: BTreeMap::new(),
            families: BTreeSet::new(),
            goals: BTreeSet::new(),
            errors: BTreeSet::new(),
            transitions: 0,
            unclassified: 0,
        })
    }
    fn checks(&mut self, phase: PropertyPhase, checks: &[Check], complete: bool) {
        let mut emitted = BTreeSet::new();
        for check in checks {
            if !emitted.insert(check.id.as_str()) {
                self.errors.insert(format!(
                    "ambiguous duplicate property ID in {phase:?} batch: {}",
                    check.id
                ));
            }
            self.properties
                .entry(RequiredProperty {
                    id: check.id.to_string(),
                    phase,
                })
                .or_default()
                .record(check);
        }
        if complete {
            for property in &self.manifest.required_properties {
                if property.phase == phase && !emitted.contains(property.id.as_str()) {
                    let counts = self.properties.entry(property.clone()).or_default();
                    counts.missing = counts.missing.saturating_add(1);
                }
            }
        }
    }
    pub fn finish(
        mut self,
        outcome: SearchOutcome,
        termination: impl Into<String>,
        budgets: BTreeMap<String, String>,
        error: Option<&ModelError>,
    ) -> AssuranceReport {
        if let Some(error) = error {
            self.errors.insert(error.to_string());
        }
        let properties = property_results(&self.manifest, &self.properties);
        let families = family_results(&self.manifest, &self.families);
        let coverage = goal_results(
            &self.manifest,
            &self.goals,
            outcome == SearchOutcome::GraphExhausted && self.errors.is_empty(),
        );
        AssuranceReport {
            manifest: self.manifest,
            metadata: self.metadata,
            search: SearchEvidence {
                outcome,
                termination: termination.into(),
                budgets,
                checked_transitions: self.transitions,
            },
            properties,
            families,
            coverage,
            observed_properties: self.properties,
            observed_families: self.families,
            observed_goals: self.goals,
            callback_errors: self.errors.into_iter().collect(),
            unclassified_transitions: self.unclassified,
        }
    }
}
impl<M: Model, C: Coverage<M>> SearchObserver<M> for Observer<C> {
    fn needs_history(&self) -> bool {
        self.manifest
            .cover_goals
            .iter()
            .any(|g| g.scope == GoalScope::History)
    }
    fn initial(&mut self, _: &M::State, checks: &[Check]) {
        self.checks(PropertyPhase::State, checks, true);
    }
    fn check_error(&mut self, phase: explore::CheckPhase, checks: &[Check], complete: bool) {
        self.checks(
            match phase {
                explore::CheckPhase::InitialState | explore::CheckPhase::State => {
                    PropertyPhase::State
                }
                explore::CheckPhase::Transition => PropertyPhase::Transition,
            },
            checks,
            complete,
        );
    }
    fn transition(
        &mut self,
        before: &M::State,
        input: &M::Input,
        after: &TransitionRef<'_, M::State, M::Output>,
        state_checks: &[Check],
        transition_checks: &[Check],
        history: &[M::Input],
    ) {
        self.checks(PropertyPhase::State, state_checks, true);
        self.checks(PropertyPhase::Transition, transition_checks, true);
        self.transitions = self.transitions.saturating_add(1);
        let mut hits = CoverageHits::default();
        if let Err(error) = self
            .coverage
            .observe(before, input, after, history, &mut hits)
        {
            self.errors.insert(format!("coverage callback: {error}"));
            // A failed callback's partially accumulated hits do not certify goals.
            self.unclassified = self.unclassified.saturating_add(1);
            return;
        }
        let mut classified = false;
        for name in hits.families {
            if self.manifest.input_families.iter().any(|f| f.name == name) {
                classified = true;
                self.families.insert(name);
            } else {
                self.errors
                    .insert(format!("undeclared input family: {name}"));
            }
        }
        if !classified {
            self.unclassified = self.unclassified.saturating_add(1);
        }
        for id in hits.goals {
            if self.manifest.cover_goals.iter().any(|g| g.id == id) {
                self.goals.insert(id);
            } else {
                self.errors.insert(format!("undeclared cover goal: {id}"));
            }
        }
    }
}
fn property_results(
    manifest: &Manifest,
    observed: &BTreeMap<RequiredProperty, CheckCounts>,
) -> Vec<PropertyResult> {
    let mut results: Vec<_> = manifest
        .required_properties
        .iter()
        .map(|p| {
            let counts = observed.get(p).cloned().unwrap_or_default();
            let status = if counts.failed > 0 {
                PropertyStatus::Failed
            } else if counts.skipped > 0 {
                PropertyStatus::Skipped
            } else if counts.missing > 0 {
                PropertyStatus::Missing
            } else if counts.passed > 0 {
                PropertyStatus::Passed
            } else {
                PropertyStatus::Missing
            };
            PropertyResult {
                property: p.clone(),
                status,
                counts,
            }
        })
        .collect();
    results.sort_by(|a, b| a.property.cmp(&b.property));
    results
}
fn family_results(manifest: &Manifest, observed: &BTreeSet<String>) -> Vec<FamilyResult> {
    let mut results: Vec<_> = manifest
        .input_families
        .iter()
        .map(|f| FamilyResult {
            name: f.name.clone(),
            status: match &f.coverage {
                FamilyCoverage::Required if observed.contains(&f.name) => FamilyStatus::Observed,
                FamilyCoverage::Required => FamilyStatus::Missing,
                FamilyCoverage::Excluded { reason } if observed.contains(&f.name) => {
                    FamilyStatus::ExclusionViolated {
                        reason: reason.clone(),
                    }
                }
                FamilyCoverage::Excluded { reason } => FamilyStatus::Excluded {
                    reason: reason.clone(),
                },
            },
        })
        .collect();
    results.sort_by(|a, b| a.name.cmp(&b.name));
    results
}
fn goal_results(
    manifest: &Manifest,
    observed: &BTreeSet<String>,
    exhausted: bool,
) -> Vec<GoalResult> {
    let mut results: Vec<_> = manifest
        .cover_goals
        .iter()
        .map(|g| GoalResult {
            goal: g.clone(),
            status: if observed.contains(&g.id) {
                CoverageStatus::Observed
            } else if exhausted && g.scope == GoalScope::Transition {
                CoverageStatus::UnreachableUnderDeclaredModel
            } else {
                CoverageStatus::NotReachedBeforeStop
            },
        })
        .collect();
    results.sort_by(|a, b| a.goal.id.cmp(&b.goal.id));
    results
}
fn search_outcome(t: SearchTermination) -> SearchOutcome {
    match t {
        SearchTermination::GraphExhausted => SearchOutcome::GraphExhausted,
        SearchTermination::DepthBound => SearchOutcome::DepthLimited,
        SearchTermination::StateLimit
        | SearchTermination::TransitionLimit
        | SearchTermination::EstimatedStateByteLimit => SearchOutcome::BudgetLimited,
        SearchTermination::FailureFound => SearchOutcome::Failed,
        SearchTermination::Deadline => SearchOutcome::TimedOut,
        SearchTermination::Cancelled => SearchOutcome::Interrupted,
    }
}
fn fuzz_outcome(t: FuzzTermination) -> SearchOutcome {
    match t {
        FuzzTermination::CasesCompleted => SearchOutcome::CasesCompleted,
        FuzzTermination::TransitionLimit => SearchOutcome::BudgetLimited,
        FuzzTermination::FailureFound => SearchOutcome::Failed,
        FuzzTermination::Deadline => SearchOutcome::TimedOut,
        FuzzTermination::Cancelled => SearchOutcome::Interrupted,
    }
}
fn control_budgets(limits: RunLimits<'_>, budgets: &mut BTreeMap<String, String>) {
    if let Some(duration) = limits.max_duration {
        budgets.insert("max_duration_ns".into(), duration.as_nanos().to_string());
    }
    budgets.insert(
        "cancellation_enabled".into(),
        limits.cancellation.is_some().to_string(),
    );
}
fn fuzz_budgets(config: &FuzzConfig, limits: RunLimits<'_>) -> BTreeMap<String, String> {
    let mut budgets = BTreeMap::from([
        ("seed".into(), config.seed.to_string()),
        ("cases".into(), config.cases.to_string()),
        ("max_steps".into(), config.max_steps.to_string()),
        ("max_transitions".into(), config.max_transitions.to_string()),
        (
            "mutation_percent".into(),
            config.mutation_percent.to_string(),
        ),
    ]);
    control_budgets(limits, &mut budgets);
    budgets
}
pub fn enumerate<M: Enumerate, C: Coverage<M>>(
    model: &M,
    manifest: Manifest,
    coverage: C,
    config: SearchConfig,
    limits: SearchLimits<'_>,
) -> Result<AssuredRun<SearchReport<M::Input>>, ModelError>
where
    M::State: Hash,
{
    let mut observer = Observer::new(manifest, model.metadata(), coverage)?;
    let mut budgets = BTreeMap::from([
        ("max_states".into(), config.max_states.to_string()),
        ("max_transitions".into(), config.max_transitions.to_string()),
        ("max_depth".into(), config.max_depth.to_string()),
    ]);
    control_budgets(limits.control, &mut budgets);
    if let Some(bytes) = limits.max_estimated_state_bytes {
        budgets.insert("max_estimated_state_bytes".into(), bytes.to_string());
    }
    let search = explore::enumerate_observed(model, config, limits, &mut observer);
    let (outcome, termination) = match &search {
        Ok(r) => (
            search_outcome(r.termination),
            format!("{:?}", r.termination),
        ),
        Err(_) => (SearchOutcome::Failed, "ModelError".into()),
    };
    let assurance = observer.finish(outcome, termination, budgets, search.as_ref().err());
    Ok(AssuredRun { search, assurance })
}
pub fn fuzz<M: Generate, C: Coverage<M>>(
    model: &M,
    manifest: Manifest,
    coverage: C,
    config: FuzzConfig,
    limits: RunLimits<'_>,
) -> Result<AssuredRun<FuzzReport<M::Input>>, ModelError> {
    let mut observer = Observer::new(manifest, model.metadata(), coverage)?;
    let budgets = fuzz_budgets(&config, limits);
    let search = explore::fuzz_observed(model, config, limits, &mut observer);
    Ok(finish_fuzz(observer, search, budgets))
}
#[allow(clippy::too_many_arguments)]
pub fn guided_fuzz<M: Generate, C: Coverage<M>, F: crate::guided::Feedback<M>>(
    model: &M,
    manifest: Manifest,
    coverage: C,
    config: FuzzConfig,
    guidance: crate::guided::GuidanceConfig,
    feedback: F,
    limits: RunLimits<'_>,
) -> Result<AssuredRun<FuzzReport<M::Input>>, ModelError> {
    let mut observer = Observer::new(manifest, model.metadata(), coverage)?;
    let mut budgets = fuzz_budgets(&config, limits);
    let identity = feedback.metadata();
    budgets.extend([
        ("guidance.algorithm".into(), crate::guided::ALGORITHM.into()),
        (
            "guidance.max_corpus_entries".into(),
            guidance.max_corpus_entries.to_string(),
        ),
        (
            "guidance.max_corpus_inputs".into(),
            guidance.max_corpus_inputs.to_string(),
        ),
        (
            "guidance.max_features".into(),
            guidance.max_features.to_string(),
        ),
        (
            "guidance.max_features_per_observation".into(),
            guidance.max_features_per_observation.to_string(),
        ),
        ("guidance.feedback.name".into(), identity.name),
        (
            "guidance.feedback.version".into(),
            identity.version.to_string(),
        ),
        ("guidance.feedback.build".into(), identity.build),
    ]);
    let search =
        crate::guided::fuzz_observed(model, config, guidance, feedback, limits, &mut observer);
    Ok(finish_fuzz(observer, search, budgets))
}
fn finish_fuzz<I, C>(
    observer: Observer<C>,
    search: Result<FuzzReport<I>, ModelError>,
    budgets: BTreeMap<String, String>,
) -> AssuredRun<FuzzReport<I>> {
    let (outcome, termination) = match &search {
        Ok(r) => (fuzz_outcome(r.termination), format!("{:?}", r.termination)),
        Err(_) => (SearchOutcome::Failed, "ModelError".into()),
    };
    let assurance = observer.finish(outcome, termination, budgets, search.as_ref().err());
    AssuredRun { search, assurance }
}

/// Deterministic campaign merge. Every job and stop reason remains inspectable;
/// missing reports are explicit and cannot be replaced by a completed sibling.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MergedReport {
    pub report: AssuranceReport,
    pub jobs: BTreeMap<u64, AssuranceReport>,
    pub missing_jobs: Vec<u64>,
}
pub fn merge_reports(
    manifest: Manifest,
    metadata: ModelMetadata,
    expected_jobs: impl IntoIterator<Item = u64>,
    reports: impl IntoIterator<Item = (u64, AssuranceReport)>,
) -> Result<MergedReport, ModelError> {
    manifest.validate()?;
    if manifest.model != metadata.name {
        return Err(ModelError::new("merge model identity mismatch"));
    }
    let mut expected = BTreeSet::new();
    for id in expected_jobs {
        if !expected.insert(id) {
            return Err(ModelError::new("duplicate expected assurance job"));
        }
    }
    let mut jobs = BTreeMap::new();
    for (id, report) in reports {
        report.validate()?;
        if !expected.contains(&id) {
            return Err(ModelError::new("unexpected assurance job"));
        }
        if report.manifest != manifest || report.metadata != metadata {
            return Err(ModelError::new(
                "cannot merge different assurance manifests or executable identities",
            ));
        }
        if jobs.insert(id, report).is_some() {
            return Err(ModelError::new("duplicate assurance job report"));
        }
    }
    let missing_jobs: Vec<_> = expected
        .iter()
        .copied()
        .filter(|id| !jobs.contains_key(id))
        .collect();
    let mut properties = BTreeMap::<RequiredProperty, CheckCounts>::new();
    let mut families = BTreeSet::new();
    let mut goals = BTreeSet::new();
    let mut errors = BTreeSet::new();
    let mut transitions = 0u64;
    let mut unclassified = 0u64;
    for (id, job) in &jobs {
        for (property, counts) in &job.observed_properties {
            properties
                .entry(property.clone())
                .or_default()
                .merge(counts);
        }
        families.extend(job.observed_families.iter().cloned());
        goals.extend(job.observed_goals.iter().cloned());
        for error in &job.callback_errors {
            errors.insert(format!("job {id}: {error}"));
        }
        transitions = transitions.saturating_add(job.search.checked_transitions);
        unclassified = unclassified.saturating_add(job.unclassified_transitions);
    }
    let outcome = if expected.is_empty() || jobs.is_empty() {
        SearchOutcome::NotStarted
    } else if jobs
        .values()
        .any(|j| j.search.outcome == SearchOutcome::Failed)
    {
        SearchOutcome::Failed
    } else if !missing_jobs.is_empty() {
        SearchOutcome::Interrupted
    } else if jobs
        .values()
        .all(|j| j.search.outcome == SearchOutcome::GraphExhausted)
    {
        SearchOutcome::GraphExhausted
    } else if jobs.values().all(|j| j.search.outcome.completed()) {
        SearchOutcome::CasesCompleted
    } else if jobs
        .values()
        .any(|j| j.search.outcome == SearchOutcome::TimedOut)
    {
        SearchOutcome::TimedOut
    } else if jobs.values().any(|j| {
        j.search.outcome == SearchOutcome::Interrupted
            || j.search.outcome == SearchOutcome::NotStarted
    }) {
        SearchOutcome::Interrupted
    } else if jobs
        .values()
        .any(|j| j.search.outcome == SearchOutcome::BudgetLimited)
    {
        SearchOutcome::BudgetLimited
    } else {
        SearchOutcome::DepthLimited
    };
    let report = AssuranceReport {
        properties: property_results(&manifest, &properties),
        families: family_results(&manifest, &families),
        coverage: goal_results(
            &manifest,
            &goals,
            outcome == SearchOutcome::GraphExhausted && errors.is_empty(),
        ),
        manifest,
        metadata,
        observed_properties: properties,
        observed_families: families,
        observed_goals: goals,
        callback_errors: errors.into_iter().collect(),
        unclassified_transitions: unclassified,
        search: SearchEvidence {
            outcome,
            termination: "MergedJobs".into(),
            budgets: BTreeMap::from([
                ("requested_jobs".into(), expected.len().to_string()),
                ("reported_jobs".into(), jobs.len().to_string()),
            ]),
            checked_transitions: transitions,
        },
    };
    Ok(MergedReport {
        report,
        jobs,
        missing_jobs,
    })
}

pub struct AssuredCampaign {
    pub campaign: crate::campaign::CampaignReport,
    /// Factory failures or jobs never started are listed as missing, never green.
    pub assurance: MergedReport,
}
/// Add job-local observation to the existing campaign scheduler. Metadata is the
/// expected executable identity, checked against every job that starts a model.
/// The original job sink still receives counterexamples and factory/panic errors.
/// Reports survive sink failure and are merged in job-ID order, not finish order.
#[allow(clippy::too_many_arguments)]
pub fn run_campaign<M, F, B, C, S>(
    config: crate::campaign::CampaignConfig,
    limits: RunLimits<'_>,
    manifest: Manifest,
    metadata: ModelMetadata,
    factory: F,
    coverage_factory: B,
    sink: S,
) -> Result<AssuredCampaign, ModelError>
where
    M: Generate,
    M::Input: Send,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
    B: Fn(u64, &M) -> Result<C, ModelError> + Sync,
    C: Coverage<M>,
    S: FnMut(crate::campaign::JobReport<M::Input>) -> Result<(), ModelError>,
{
    config.validate()?;
    manifest.validate()?;
    if manifest.model != metadata.name {
        return Err(ModelError::new("campaign model identity mismatch"));
    }
    let expected = (0..config.jobs)
        .map(|offset| config.first_job + offset as u64)
        .collect::<Vec<_>>();
    let mut campaign_budgets = BTreeMap::from([
        (
            "campaign.master_seed".into(),
            config.master_seed.to_string(),
        ),
        ("campaign.first_job".into(), config.first_job.to_string()),
        ("campaign.workers".into(), config.workers.to_string()),
        (
            "campaign.result_buffer".into(),
            config.result_buffer.to_string(),
        ),
        (
            "campaign.stop_on_failure".into(),
            config.stop_on_failure.to_string(),
        ),
    ]);
    control_budgets(limits, &mut campaign_budgets);
    let reports = std::sync::Mutex::new(BTreeMap::new());
    let campaign = crate::campaign::run_campaign_using(
        config,
        limits,
        factory,
        sink,
        |id, model, config, limits| {
            if model.metadata() != metadata {
                return Err(ModelError::new(
                    "campaign assurance executable identity mismatch",
                ));
            }
            let mut observer = Observer::new(
                manifest.clone(),
                model.metadata(),
                coverage_factory(id, model)?,
            )?;
            let budgets = fuzz_budgets(&config, limits);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                explore::fuzz_observed(model, config, limits, &mut observer)
            }));
            match result {
                Ok(result) => {
                    let run = finish_fuzz(observer, result, budgets);
                    reports
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .insert(id, run.assurance);
                    run.search
                }
                Err(payload) => {
                    let error = ModelError::new("job panicked; partial observations retained");
                    let report =
                        observer.finish(SearchOutcome::Failed, "Panicked", budgets, Some(&error));
                    reports
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .insert(id, report);
                    std::panic::resume_unwind(payload)
                }
            }
        },
    )?;
    let jobs = reports
        .into_inner()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut assurance = merge_reports(manifest, metadata, expected, jobs)?;
    let report = &mut assurance.report;
    report.search.budgets.extend(campaign_budgets);
    report.search.budgets.extend([
        ("campaign.started".into(), campaign.started.to_string()),
        ("campaign.finished".into(), campaign.finished.to_string()),
        ("campaign.delivered".into(), campaign.delivered.to_string()),
        (
            "campaign.accounting_complete".into(),
            campaign.accounting_complete.to_string(),
        ),
    ]);
    report.search.termination = format!("Campaign::{:?}", campaign.termination);
    use crate::campaign::CampaignTermination;
    match &campaign.termination {
        CampaignTermination::JobsCompleted => {}
        CampaignTermination::FailureFound => report.search.outcome = SearchOutcome::Failed,
        CampaignTermination::CallbackError(error) | CampaignTermination::WorkerError(error) => {
            report.search.outcome = SearchOutcome::Failed;
            report
                .callback_errors
                .push(format!("campaign completion/delivery error: {error}"));
        }
        CampaignTermination::Cancelled if report.search.outcome != SearchOutcome::Failed => {
            report.search.outcome = SearchOutcome::Interrupted;
        }
        CampaignTermination::Deadline if report.search.outcome != SearchOutcome::Failed => {
            report.search.outcome = SearchOutcome::TimedOut;
        }
        _ => {}
    }
    if !campaign.accounting_complete {
        report
            .callback_errors
            .push("campaign accounting is incomplete".into());
    }
    if campaign.delivered != campaign.requested {
        report
            .callback_errors
            .push("not every requested campaign report was delivered successfully".into());
    }
    report.callback_errors.sort();
    report.callback_errors.dedup();
    report.coverage = goal_results(
        &report.manifest,
        &report.observed_goals,
        report.search.outcome == SearchOutcome::GraphExhausted && report.callback_errors.is_empty(),
    );
    Ok(AssuredCampaign {
        campaign,
        assurance,
    })
}

// Small deterministic JSON writer keeps assurance optional and core dependency-free.
fn quoted(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < '\u{20}' => {
                use std::fmt::Write;
                write!(out, "\\u{:04x}", c as u32).unwrap();
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
fn object(fields: impl IntoIterator<Item = (String, String)>) -> String {
    format!(
        "{{{}}}",
        fields
            .into_iter()
            .map(|(k, v)| format!("{}:{v}", quoted(&k)))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn array(values: impl IntoIterator<Item = String>) -> String {
    format!("[{}]", values.into_iter().collect::<Vec<_>>().join(","))
}
fn string_map(values: &BTreeMap<String, String>) -> String {
    object(values.iter().map(|(k, v)| (k.clone(), quoted(v))))
}
fn property_json(p: &RequiredProperty) -> String {
    object([
        ("id".into(), quoted(&p.id)),
        (
            "phase".into(),
            quoted(match p.phase {
                PropertyPhase::State => "state",
                PropertyPhase::Transition => "transition",
            }),
        ),
    ])
}
fn counts_json(c: &CheckCounts) -> String {
    object([
        ("passed".into(), c.passed.to_string()),
        ("failed".into(), c.failed.to_string()),
        ("skipped".into(), c.skipped.to_string()),
        ("missing".into(), c.missing.to_string()),
    ])
}
fn goal_json(g: &CoverGoal) -> String {
    object([
        ("id".into(), quoted(&g.id)),
        (
            "scope".into(),
            quoted(match g.scope {
                GoalScope::Transition => "transition",
                GoalScope::History => "history",
            }),
        ),
    ])
}
impl Manifest {
    pub fn to_json(&self) -> String {
        object([
            (
                "assurance_version".into(),
                self.assurance_version.to_string(),
            ),
            ("model".into(), quoted(&self.model)),
            (
                "required_properties".into(),
                array(self.required_properties.iter().map(property_json)),
            ),
            (
                "input_families".into(),
                array(self.input_families.iter().map(|f| {
                    let mut fields = vec![("name".into(), quoted(&f.name))];
                    match &f.coverage {
                        FamilyCoverage::Required => {
                            fields.push(("coverage".into(), quoted("required")))
                        }
                        FamilyCoverage::Excluded { reason } => {
                            fields.push(("coverage".into(), quoted("excluded")));
                            fields.push(("reason".into(), quoted(reason)));
                        }
                    }
                    object(fields)
                })),
            ),
            ("bounds".into(), string_map(&self.bounds)),
            (
                "cover_goals".into(),
                array(self.cover_goals.iter().map(goal_json)),
            ),
            (
                "required_search_outcome".into(),
                quoted(match self.required_search_outcome {
                    RequiredSearchOutcome::GraphExhausted => "exhausted_within_declared_bounds",
                    RequiredSearchOutcome::Completed => "completed",
                }),
            ),
            (
                "provenance".into(),
                object([
                    ("manifest".into(), quoted(&self.provenance.manifest)),
                    ("model".into(), quoted(&self.provenance.model)),
                    ("properties".into(), quoted(&self.provenance.properties)),
                ]),
            ),
        ])
    }
}
impl AssuranceReport {
    pub fn to_json(&self) -> String {
        object([
            ("assurance_version".into(), ASSURANCE_VERSION.to_string()),
            ("manifest".into(), self.manifest.to_json()),
            (
                "metadata".into(),
                object([
                    ("name".into(), quoted(&self.metadata.name)),
                    (
                        "model_version".into(),
                        self.metadata.model_version.to_string(),
                    ),
                    (
                        "properties_version".into(),
                        self.metadata.properties_version.to_string(),
                    ),
                    (
                        "codec_version".into(),
                        self.metadata.codec_version.to_string(),
                    ),
                    ("build".into(), quoted(&self.metadata.build)),
                ]),
            ),
            (
                "search".into(),
                object([
                    (
                        "outcome".into(),
                        quoted(&format!("{:?}", self.search.outcome)),
                    ),
                    ("termination".into(), quoted(&self.search.termination)),
                    ("budgets".into(), string_map(&self.search.budgets)),
                    (
                        "checked_transitions".into(),
                        self.search.checked_transitions.to_string(),
                    ),
                ]),
            ),
            (
                "properties".into(),
                array(self.properties.iter().map(|p| {
                    object([
                        ("property".into(), property_json(&p.property)),
                        ("status".into(), quoted(&format!("{:?}", p.status))),
                        ("counts".into(), counts_json(&p.counts)),
                    ])
                })),
            ),
            (
                "families".into(),
                array(self.families.iter().map(|f| {
                    let (status, reason) = match &f.status {
                        FamilyStatus::Observed => ("Observed", None),
                        FamilyStatus::Missing => ("Missing", None),
                        FamilyStatus::Excluded { reason } => ("Excluded", Some(reason)),
                        FamilyStatus::ExclusionViolated { reason } => {
                            ("ExclusionViolated", Some(reason))
                        }
                    };
                    object([
                        ("name".into(), quoted(&f.name)),
                        ("status".into(), quoted(status)),
                        (
                            "reason".into(),
                            reason.map_or_else(|| "null".into(), |s| quoted(s)),
                        ),
                    ])
                })),
            ),
            (
                "coverage".into(),
                array(self.coverage.iter().map(|g| {
                    object([
                        ("goal".into(), goal_json(&g.goal)),
                        ("status".into(), quoted(&format!("{:?}", g.status))),
                    ])
                })),
            ),
            (
                "observed_properties".into(),
                array(self.observed_properties.iter().map(|(p, c)| {
                    object([
                        ("property".into(), property_json(p)),
                        ("counts".into(), counts_json(c)),
                    ])
                })),
            ),
            (
                "observed_families".into(),
                array(self.observed_families.iter().map(|name| quoted(name))),
            ),
            (
                "observed_goals".into(),
                array(self.observed_goals.iter().map(|id| quoted(id))),
            ),
            (
                "callback_errors".into(),
                array(self.callback_errors.iter().map(|e| quoted(e))),
            ),
            (
                "unclassified_transitions".into(),
                self.unclassified_transitions.to_string(),
            ),
        ])
    }
}
impl MergedReport {
    pub fn to_json(&self) -> String {
        object([
            ("assurance_version".into(), ASSURANCE_VERSION.to_string()),
            ("report".into(), self.report.to_json()),
            (
                "jobs".into(),
                object(
                    self.jobs
                        .iter()
                        .map(|(id, report)| (id.to_string(), report.to_json())),
                ),
            ),
            (
                "missing_jobs".into(),
                array(self.missing_jobs.iter().map(u64::to_string)),
            ),
        ])
    }
}
