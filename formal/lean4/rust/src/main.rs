//! Public-API bridge: no duplicate implementation of the assessment algorithm.
use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, BufRead, Write};

use stateless::assurance::*;
use stateless::explore::SearchObserver;
use stateless::{Check, Model, ModelError, ModelMetadata, Transition, TransitionRef};

fn metadata() -> ModelMetadata {
    ModelMetadata {
        name: "lean-assurance-poc".into(), model_version: 1,
        properties_version: 1, codec_version: 1, build: "poc-v1".into(),
    }
}

fn property(phase: PropertyPhase) -> RequiredProperty {
    RequiredProperty { id: "p".into(), phase }
}

fn manifest(mask: u64, family: bool, goal: u64, requirement: u64) -> Manifest {
    let mut required_properties = Vec::new();
    if mask & 1 != 0 { required_properties.push(property(PropertyPhase::State)); }
    if mask & 2 != 0 { required_properties.push(property(PropertyPhase::Transition)); }
    let mut input_families = Vec::new();
    if family {
        input_families.push(InputFamily { name: "input".into(), coverage: FamilyCoverage::Required });
    }
    input_families.push(InputFamily { name: "outside".into(),
        coverage: FamilyCoverage::Excluded { reason: "out of scope".into() } });
    Manifest {
        assurance_version: ASSURANCE_VERSION, model: metadata().name,
        required_properties, input_families, bounds: BTreeMap::new(),
        cover_goals: if goal == 0 { vec![] } else { vec![CoverGoal {
            id: "goal".into(), scope: if goal == 1 { GoalScope::Transition } else { GoalScope::History },
        }] },
        required_search_outcome: if requirement == 0 {
            RequiredSearchOutcome::GraphExhausted
        } else { RequiredSearchOutcome::Completed },
        provenance: Provenance { manifest: "poc-manifest".into(), model: "poc-model".into(),
            properties: "poc-properties".into() },
    }
}

fn counts(ns: &[u64]) -> CheckCounts {
    CheckCounts { passed: ns[0], failed: ns[1], skipped: ns[2], missing: ns[3] }
}
fn values(c: &CheckCounts) -> Vec<u64> { vec![c.passed, c.failed, c.skipped, c.missing] }
fn status(n: u64) -> PropertyStatus {
    match n { 0 => PropertyStatus::Passed, 1 => PropertyStatus::Failed,
        2 => PropertyStatus::Skipped, _ => PropertyStatus::Missing }
}
fn status_code(s: PropertyStatus) -> u64 {
    match s { PropertyStatus::Passed => 0, PropertyStatus::Failed => 1,
        PropertyStatus::Skipped => 2, PropertyStatus::Missing => 3 }
}
fn outcome(n: u64) -> SearchOutcome {
    match n {
        0 => SearchOutcome::GraphExhausted, 1 => SearchOutcome::CasesCompleted,
        2 => SearchOutcome::DepthLimited, 3 => SearchOutcome::BudgetLimited,
        4 => SearchOutcome::Interrupted, 5 => SearchOutcome::TimedOut,
        6 => SearchOutcome::Failed, _ => SearchOutcome::NotStarted,
    }
}
fn format_numbers(ns: &[u64]) -> String {
    ns.iter().map(u64::to_string).collect::<Vec<_>>().join(" ")
}

// Report construction is test input, not trusted evidence. The real validator
// decides whether each candidate status agrees with its underlying counts.
fn candidate_report(c: CheckCounts, candidate: PropertyStatus) -> AssuranceReport {
    let p = property(PropertyPhase::State);
    AssuranceReport {
        manifest: manifest(1, true, 0, 0), metadata: metadata(),
        search: SearchEvidence { outcome: SearchOutcome::GraphExhausted,
            termination: "POCFixture".into(), budgets: BTreeMap::new(), checked_transitions: 1 },
        properties: vec![PropertyResult { property: p.clone(), status: candidate, counts: c.clone() }],
        families: vec![FamilyResult { name: "input".into(), status: FamilyStatus::Observed },
            FamilyResult { name: "outside".into(), status: FamilyStatus::Excluded {
                reason: "out of scope".into() } }],
        coverage: vec![], observed_properties: BTreeMap::from([(p, c)]),
        observed_families: BTreeSet::from(["input".into()]), observed_goals: BTreeSet::new(),
        callback_errors: vec![], unclassified_transitions: 0,
    }
}
fn validated_report(c: CheckCounts) -> Result<AssuranceReport, String> {
    let mut reports = (0..4).map(|s| candidate_report(c.clone(), status(s)))
        .filter(|r| r.validate().is_ok()).collect::<Vec<_>>();
    if reports.len() != 1 { return Err("validator did not select exactly one status".into()); }
    Ok(reports.remove(0))
}

struct Fixture;
impl Model for Fixture {
    type State = ();
    type Input = ();
    type Output = ();
    fn metadata(&self) -> ModelMetadata { metadata() }
    fn initial_state(&self) -> Result<(), ModelError> { Ok(()) }
    fn step(&self, _: &(), _: &()) -> Result<Transition<(), ()>, ModelError> {
        Ok(Transition::accepted((), vec![]))
    }
    fn check_state(&self, _: &()) -> Result<Vec<Check>, ModelError> { Ok(vec![]) }
}
struct Hits { mask: u64, fail: bool }
impl Coverage<Fixture> for Hits {
    fn observe(&self, _: &(), _: &(), _: &TransitionRef<'_, (), ()>, _: &[()],
        hits: &mut CoverageHits) -> Result<(), ModelError> {
        if self.mask & 1 != 0 { hits.family("input"); }
        if self.mask & 2 != 0 { hits.family("outside"); }
        if self.mask & 4 != 0 { hits.goal("goal"); }
        if self.mask & 8 != 0 { hits.family("unknown-family"); }
        if self.mask & 16 != 0 { hits.goal("unknown-goal"); }
        if self.fail { Err(ModelError::new("injected coverage error after hits")) } else { Ok(()) }
    }
}
fn batch(code: u64) -> Vec<Check> {
    match code {
        0 => vec![],
        1 => vec![Check::passed("p")],
        2 => vec![Check::failed("p", "fixture")],
        3 => vec![Check::skipped("p", "fixture")],
        4 => vec![Check::passed("p"), Check::passed("p")],
        5 => vec![Check::passed("other")],
        6 => vec![Check::failed("other", "fixture")],
        7 => vec![Check::skipped("other", "fixture")],
        8 => vec![Check::passed("p"), Check::failed("other", "fixture")],
        _ => vec![Check::passed("p"), Check::skipped("other", "fixture")],
    }
}

fn observation_case(ns: &[u64]) -> Result<String, String> {
    if ns.len() < 9 || (ns.len() - 8) % 2 != 1 || ns[0] > 3 || ns[1] > 1
        || ns[2] > 2 || ns[3] > 1 || ns[4] > 7 || ns[5] > 31 || ns[6] > 1
        || ns[7] > 12 || ns[8..].iter().any(|&n| n > 9) {
        return Err("invalid R protocol".into());
    }
    if ns[7] != 0 && (ns[..7] != [3, 1, 1, 0, 0, 5, 0] || ns[8..] != [1, 1, 1]) {
        return Err("tamper cases require the documented good baseline".into());
    }
    let m = manifest(ns[0], ns[1] == 1, ns[2], ns[3]);
    let mut observer = Observer::new(m, metadata(), Hits { mask: ns[5], fail: ns[6] == 1 })
        .map_err(|e| e.to_string())?;
    SearchObserver::<Fixture>::initial(&mut observer, &(), &batch(ns[8]));
    let transition = Transition::<(), ()>::accepted((), vec![]);
    for pair in ns[9..].chunks_exact(2) {
        SearchObserver::<Fixture>::transition(&mut observer, &(), &(), &transition.as_ref(),
            &batch(pair[0]), &batch(pair[1]), &[]);
    }
    let mut report = observer.finish(outcome(ns[4]), "POCObserver", BTreeMap::new(), None);
    // Emit observer evidence BEFORE deliberate report tampering.
    let mut answer = values(&report.observed_properties.get(&property(PropertyPhase::State))
        .cloned().unwrap_or_default());
    answer.extend(values(&report.observed_properties.get(&property(PropertyPhase::Transition))
        .cloned().unwrap_or_default()));
    let errors = u64::from(!report.callback_errors.is_empty());
    let unclassified = report.unclassified_transitions;
    match ns[7] {
        0 => {},
        1 => report.properties.clear(),
        2 => report.families.clear(),
        3 => report.coverage.clear(),
        4 => report.metadata.name = "wrong".into(),
        5 => report.search.checked_transitions = 0,
        6 => report.unclassified_transitions = report.search.checked_transitions + 1,
        7 => { report.observed_families.insert("unknown".into()); },
        8 => { report.observed_goals.insert("unknown".into()); },
        9 => report.observed_properties.clear(),
        10 => report.properties[0].status = PropertyStatus::Missing,
        11 => report.properties.push(report.properties[0].clone()),
        _ => report.manifest.assurance_version += 1,
    }
    answer.extend([u64::from(report.validate().is_ok()),
        u64::from(report.assess(AssurancePolicy::Strict).adequate), errors, unclassified]);
    Ok(format_numbers(&answer))
}

fn respond(line: &str) -> Result<String, String> {
    let mut words = line.split_whitespace();
    let mode = words.next().ok_or("empty protocol line")?;
    let ns = words.map(|s| s.parse::<u64>().map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    match mode {
        "C" if ns.len() == 5 && ns[4] <= 3 => {
            let report = candidate_report(counts(&ns), status(ns[4]));
            Ok(format_numbers(&[u64::from(report.validate().is_ok()),
                u64::from(report.assess(AssurancePolicy::Strict).adequate)]))
        },
        "M" if ns.len() == 8 => {
            let a = validated_report(counts(&ns))?;
            let b = validated_report(counts(&ns[4..]))?;
            let merged = merge_reports(a.manifest.clone(), metadata(), [0, 1], [(0, a), (1, b)])
                .map_err(|e| e.to_string())?.report;
            let p = &merged.properties[0];
            let mut answer = values(&p.counts);
            answer.extend([status_code(p.status), u64::from(merged.assess(AssurancePolicy::Strict).adequate)]);
            Ok(format_numbers(&answer))
        },
        "R" => observation_case(&ns),
        _ => Err("invalid protocol command or arity".into()),
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for (number, line) in stdin.lock().lines().enumerate() {
        let answer = respond(&line?).map_err(|e| format!("line {}: {e}", number + 1))?;
        writeln!(stdout, "{answer}")?;
    }
    Ok(())
}
