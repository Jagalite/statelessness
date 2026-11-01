//! reduction-v1 (S4) and the migration negative controls: each control
//! introduces one defect and requires the library evidence to expose it.
use std::cell::Cell;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::sync::atomic::AtomicBool;

use stateless::explore::{RunLimits, SearchConfig, SearchLimits};
use stateless::graph::*;
use stateless::model::*;
use stateless::progress::{ProgressGraph, ProgressKind, ProgressReport, analyze_progress};
use stateless::reduction::*;

type Step = Box<dyn Fn(i64, i64) -> Transition<i64, i64>>;

/// Inputs 10..=19 are "late result" inputs whose class is input / 10. Unless a
/// test overrides step, input k leads to state k % 10.
struct Late {
    step: Step,
    fail_edge: HashSet<i64>,
    inputs: HashMap<i64, Vec<i64>>,
}
impl Late {
    fn new() -> Self {
        Self {
            step: Box::new(|_, i| Transition::accepted(i % 10, vec![i % 10])),
            fail_edge: HashSet::new(),
            inputs: HashMap::from([(0, vec![10, 11, 12, 1]), (1, vec![2]), (2, vec![])]),
        }
    }
    fn stepping(step: impl Fn(i64, i64) -> Transition<i64, i64> + 'static) -> Self {
        Self {
            step: Box::new(step),
            ..Self::new()
        }
    }
    fn failing(mut self, inputs: &[i64]) -> Self {
        self.fail_edge = inputs.iter().copied().collect();
        self
    }
}
impl Model for Late {
    type State = i64;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "late".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<i64, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &i64, i: &i64) -> Result<Transition<i64, i64>, ModelError> {
        Ok((self.step)(*s, *i))
    }
    fn check_state(&self, _: &i64) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("ok")])
    }
    fn check_transition(
        &self,
        _: &i64,
        i: &i64,
        _: &TransitionRef<'_, i64, i64>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if self.fail_edge.contains(i) {
            vec![Check::failed("late", i.to_string())]
        } else {
            vec![]
        })
    }
}
impl Enumerate for Late {
    fn inputs(&self, s: &i64) -> Result<Vec<i64>, ModelError> {
        Ok(self.inputs.get(s).cloned().unwrap_or_default())
    }
}

struct ByDecade;
impl EquivalenceReduction<i64, i64, i64> for ByDecade {
    fn eligible_class(&mut self, _: &i64, i: &i64) -> Result<Option<String>, ModelError> {
        Ok((*i >= 10).then(|| "late".to_owned()))
    }
    fn projection(
        &mut self,
        _: &i64,
        t: &TransitionRef<'_, i64, i64>,
        _: &[String],
    ) -> Result<Vec<u8>, ModelError> {
        Ok(t.outputs.iter().map(|&o| o as u8).collect())
    }
}

type Report = GraphSearchReport<i64, i64, i64>;

fn collect() -> FailurePolicy {
    FailurePolicy::Collect {
        max_retained_findings: 10,
        expand_failing_successors: true,
    }
}

fn explore_with(m: &Late, reduce: bool, policy: FailurePolicy) -> Report {
    let mut reduction = ByDecade;
    explore_graph(
        m,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            failure_policy: policy,
            reduction: if reduce { Some(&mut reduction) } else { None },
            ..GraphSearchOptions::default()
        },
    )
    .unwrap()
}
fn explore(m: &Late) -> Report {
    explore_with(m, false, collect())
}
fn explore_reduced(m: &Late) -> Report {
    explore_with(m, true, collect())
}

struct Predicates<F: FnMut(&GraphEdge<i64, i64>) -> bool> {
    fair: F,
    terminal: i64,
}
impl<F: FnMut(&GraphEdge<i64, i64>) -> bool> ReductionProgressPredicates<i64, i64, i64>
    for Predicates<F>
{
    fn fair(&mut self, edge: &GraphEdge<i64, i64>) -> Result<bool, ModelError> {
        Ok((self.fair)(edge))
    }
    fn kind(&mut self, state: &i64) -> Result<ProgressKind, ModelError> {
        Ok(if *state == self.terminal {
            ProgressKind::Terminal
        } else {
            ProgressKind::Transaction
        })
    }
}

fn rejected(representative: GraphEdgeId, reason: RejectionCause) -> RejectedEquivalence {
    RejectedEquivalence {
        representative,
        reason,
    }
}

#[test]
fn equivalent_edges_collapse_into_representative() {
    // 10 -> 0, 11 -> 1, 12 -> 2: distinct successors, so no collapse.
    let distinct = explore_reduced(&Late::new());
    assert_eq!(distinct.reduced_edges, 0);
    assert_eq!(
        distinct
            .reduction_decisions
            .iter()
            .map(|d| d.rejected.len())
            .collect::<Vec<_>>(),
        [0, 1, 2]
    );
    assert_eq!(
        distinct.reduction_decisions[1].rejected,
        [rejected(0, RejectionCause::ProjectionDiffers)]
    );
    // Every late input reaches state 1 with identical observations.
    let same = Late::stepping(|_, i| Transition::accepted(if i >= 10 { 1 } else { i }, vec![7]));
    let unreduced = explore(&same);
    let reduced = explore_reduced(&same);
    use EdgeTarget::*;
    assert_eq!(
        reduced.edges.iter().map(|e| e.target).collect::<Vec<_>>(),
        [
            Admitted(1),
            Reduced(0),
            Reduced(0),
            Duplicate(1),
            Admitted(2)
        ]
    );
    assert_eq!(reduced.reduced_edges, 2);
    assert_eq!(
        reduced.transitions, unreduced.transitions,
        "reduction never skips execution"
    );
    let decision = |edge, reduced_to| ReductionDecision {
        edge,
        source: 0,
        equivalence_class: "late".into(),
        reduced_to,
        rejected: vec![],
    };
    assert_eq!(
        reduced.reduction_decisions,
        [
            decision(0, None),
            decision(1, Some(0)),
            decision(2, Some(0))
        ]
    );
    let comparison = compare_reduction(&unreduced, &reduced, None).unwrap();
    assert!(comparison.equivalent(), "{comparison:?}");
    assert_eq!(
        comparison.progress_match, None,
        "progress is not compared without predicates"
    );
    // Progress results agree on the reduced graph.
    let progress = |r: &Report| -> ProgressReport {
        let g = ProgressGraph::from_report(r, |_| Ok(true)).unwrap();
        analyze_progress(
            &g,
            |n| {
                Ok(if r.nodes[n].state == 2 {
                    ProgressKind::Terminal
                } else {
                    ProgressKind::Transaction
                })
            },
            None,
        )
        .unwrap()
    };
    assert_eq!(progress(&reduced), progress(&unreduced));
    let mut predicates = Predicates {
        fair: |_: &GraphEdge<i64, i64>| true,
        terminal: 2,
    };
    let with_progress = compare_reduction(&unreduced, &reduced, Some(&mut predicates)).unwrap();
    assert_eq!(with_progress.progress_match, Some(true));
    assert!(with_progress.equivalent());
}

/// Review regression: progress is compared only when predicates are supplied.
#[test]
fn progress_difference_is_not_equivalent() {
    // Same states 0 -> 1, same dispositions and checks; only the input
    // differs, and fairness depends on it.
    let one_edge = |input: i64| Late {
        inputs: HashMap::from([(0, vec![input])]),
        ..Late::stepping(|_, _| Transition::accepted(1, vec![]))
    };
    let a = explore(&one_edge(5));
    let b = explore(&one_edge(6));
    let structural = compare_reduction(&a, &b, None).unwrap();
    assert!(structural.equivalent(), "structure alone matches");
    assert_eq!(structural.progress_match, None);
    let mut predicates = Predicates {
        fair: |e: &GraphEdge<i64, i64>| e.input == 5,
        terminal: 1,
    };
    let c = compare_reduction(&a, &b, Some(&mut predicates)).unwrap();
    assert_eq!(c.progress_match, Some(false));
    assert_eq!(c.progress_mismatches, [0]);
    assert!(!c.equivalent());
}

#[test]
fn rejected_equivalences_are_inspectable() {
    // Same projection, different successor: successor equality is required.
    let successor = Late::stepping(|_, i| Transition::accepted(i % 10, vec![7]));
    assert_eq!(
        explore_reduced(&successor).reduction_decisions[1].rejected,
        [rejected(0, RejectionCause::SuccessorDiffers)]
    );
    // Same successor, a failing check on one edge only.
    let same = Late::stepping(|_, i| Transition::accepted(if i >= 10 { 1 } else { i }, vec![7]))
        .failing(&[11]);
    let checks = explore_reduced(&same);
    assert_eq!(
        checks.reduction_decisions[1].rejected,
        [rejected(0, RejectionCause::ChecksDiffer)]
    );
    assert_eq!(checks.reduction_decisions[2].reduced_to, Some(0));
    assert_eq!(checks.finding_count, 1);
    // Dispositions must match.
    let dispositions = Late::stepping(|_, i| Transition {
        state: if i >= 10 { 1 } else { i },
        outputs: vec![7],
        disposition: if i == 11 {
            Disposition::Rejected("stale".into())
        } else {
            Disposition::Accepted
        },
    });
    assert_eq!(
        explore_reduced(&dispositions).reduction_decisions[1]
            .rejected
            .iter()
            .map(|r| r.reason.as_str())
            .collect::<Vec<_>>(),
        ["disposition_differs"]
    );
    assert!(
        compare_reduction(&explore(&same), &checks, None)
            .unwrap()
            .equivalent()
    );
}

#[test]
fn incorrect_reduction_is_detected() {
    // Negative control: a reduced report missing a state, an edge class, or a
    // finding must not compare equivalent to the unreduced search.
    let same = Late::stepping(|_, i| Transition::accepted(if i >= 10 { 1 } else { i }, vec![7]))
        .failing(&[12]);
    let unreduced = explore(&same);
    let reduced = explore_reduced(&same);
    assert!(
        compare_reduction(&unreduced, &reduced, None)
            .unwrap()
            .equivalent()
    );
    let mut missing_state = reduced.clone();
    missing_state.nodes.pop();
    missing_state.edges[4].target = EdgeTarget::Rejected(GraphRejection::StateLimit);
    let a = compare_reduction(&unreduced, &missing_state, None).unwrap();
    assert!(!a.equivalent());
    assert_eq!(a.missing_in_reduced, [2]);
    assert_eq!(a.unrepresented_edges, [4]);
    let mut lost_finding = reduced.clone();
    lost_finding.finding_count = 0;
    assert!(
        !compare_reduction(&unreduced, &lost_finding, None)
            .unwrap()
            .findings_match
    );
    let mut wrong_target = reduced.clone();
    wrong_target.edges[3].target = EdgeTarget::Duplicate(2);
    let c = compare_reduction(&unreduced, &wrong_target, None).unwrap();
    // Unreduced 0->1 edges remain represented by edge 0; the 0->2 edge has no
    // unreduced counterpart.
    assert!(!c.equivalent());
    assert!(c.unrepresented_edges.is_empty());
    assert_eq!(c.extra_reduced_edges, [3]);
    let stopped = explore_with(&same, false, FailurePolicy::StopAtFirst);
    assert!(compare_reduction(&stopped, &reduced, None).is_err());
}

/// Two counters; input k increments counter k while it is below 2.
struct Counters;
impl Model for Counters {
    type State = [i64; 2];
    type Input = usize;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "counters".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<[i64; 2], ModelError> {
        Ok([0, 0])
    }
    fn step(&self, s: &[i64; 2], i: &usize) -> Result<Transition<[i64; 2], i64>, ModelError> {
        let mut n = *s;
        n[*i] += 1;
        Ok(Transition::accepted(n, vec![]))
    }
    fn check_state(&self, _: &[i64; 2]) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl Enumerate for Counters {
    fn inputs(&self, s: &[i64; 2]) -> Result<Vec<usize>, ModelError> {
        Ok([0, 1].into_iter().filter(|&i| s[i] < 2).collect())
    }
}

/// Doubling (input 0) and incrementing (input 1) below 3.
struct Arithmetic;
impl Model for Arithmetic {
    type State = i64;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "arithmetic".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<i64, ModelError> {
        Ok(1)
    }
    fn step(&self, s: &i64, i: &i64) -> Result<Transition<i64, i64>, ModelError> {
        Ok(Transition::accepted(
            if *i == 0 { s * 2 } else { s + 1 },
            vec![*i],
        ))
    }
    fn check_state(&self, _: &i64) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl Enumerate for Arithmetic {
    fn inputs(&self, s: &i64) -> Result<Vec<i64>, ModelError> {
        Ok(if *s < 3 { vec![0, 1] } else { vec![] })
    }
}

fn explore_plain<M: Enumerate>(
    m: &M,
    max_depth: usize,
) -> GraphSearchReport<M::State, M::Input, M::Output>
where
    M::State: Hash,
{
    explore_graph(
        m,
        SearchConfig {
            max_depth,
            ..SearchConfig::default()
        },
        SearchLimits::default(),
        GraphSearchOptions::default(),
    )
    .unwrap()
}

#[test]
fn diamond_validation() {
    // Two independent counters commute.
    let report = explore_plain(&Counters, 100);
    let ok = validate_diamonds(
        &Counters,
        &report,
        |_, _| Ok(true),
        |_, _| Ok(vec![]),
        RunLimits::default(),
    )
    .unwrap();
    // Pairs are checked at (0,0) (1,0) (0,1) (1,1); one input is disabled at the other bound.
    assert_eq!(ok.termination, DiamondTermination::SearchComplete);
    assert_eq!(ok.validated, 4);
    assert!(ok.non_commuting.is_empty());
    // Doubling and incrementing do not commute.
    let bounded = explore_plain(&Arithmetic, 0);
    let bad = validate_diamonds(
        &Arithmetic,
        &bounded,
        |_, _| Ok(true),
        |_, _| Ok(vec![]),
        RunLimits::default(),
    )
    .unwrap();
    // Depth-frontier nodes are not completely expanded and are not validated.
    assert_eq!(bad.checked_nodes, 0);
    assert_eq!(bad.validated, 0);
    let deep = validate_diamonds(
        &Arithmetic,
        &explore_plain(&Arithmetic, 100),
        |_, _| Ok(true),
        |_, _| Ok(vec![]),
        RunLimits::default(),
    )
    .unwrap();
    // At 1: 1*2+1=3 but (1+1)*2=4. At 2, doubling disables both orders.
    assert_eq!(deep.validated, 1);
    assert_eq!(
        deep.non_commuting,
        [DiamondWitness {
            node: 0,
            first: 0,
            second: 1,
            reason: DiamondDifference::StateDiffers
        }]
    );
    assert_eq!(deep.non_commuting[0].reason.as_str(), "state_differs");
    // Same final state, different observations.
    let observed = validate_diamonds(
        &Counters,
        &report,
        |_, _| Ok(true),
        |i, t| Ok(vec![*i as u8, t.state[0] as u8]),
        RunLimits::default(),
    )
    .unwrap();
    assert_eq!(
        observed
            .non_commuting
            .iter()
            .map(|w| w.reason)
            .collect::<Vec<_>>(),
        [DiamondDifference::ObservationsDiffer; 4]
    );
    let cancelled = AtomicBool::new(true);
    let stopped = validate_diamonds(
        &Counters,
        &report,
        |_, _| Ok(true),
        |_, _| Ok(vec![]),
        RunLimits {
            max_duration: None,
            cancellation: Some(&cancelled),
        },
    )
    .unwrap();
    assert_eq!(stopped.termination, DiamondTermination::Cancelled);
    assert_eq!(stopped.termination.as_str(), "cancelled");
}

// MARK: Migration negative controls

/// A state whose identity can be made lossy for the identity-merging control:
/// two lossy values 1 and 2 compare equal (and 2 hashes like 1). Comparisons
/// involving an exact value use exact equality, as the reference model does.
#[derive(Clone, Debug)]
struct Id {
    n: i64,
    lossy: bool,
}
impl PartialEq for Id {
    fn eq(&self, other: &Self) -> bool {
        self.n == other.n
            || (self.lossy && other.lossy && (1..3).contains(&self.n) && (1..3).contains(&other.n))
    }
}
impl Eq for Id {}
impl Hash for Id {
    fn hash<H: Hasher>(&self, h: &mut H) {
        if self.lossy && self.n == 2 { 1 } else { self.n }.hash(h);
    }
}

struct Control {
    edges: HashMap<i64, Vec<i64>>,
    lossy: bool,
    failing: HashSet<i64>,
    calls: Cell<u64>,
}
impl Control {
    fn new() -> Self {
        Self {
            edges: HashMap::from([(0, vec![1, 2]), (1, vec![3]), (2, vec![3])]),
            lossy: false,
            failing: HashSet::from([3]),
            calls: Cell::new(0),
        }
    }
}
impl Model for Control {
    type State = Id;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "control".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<Id, ModelError> {
        Ok(Id {
            n: 0,
            lossy: self.lossy,
        })
    }
    fn step(&self, _: &Id, i: &i64) -> Result<Transition<Id, i64>, ModelError> {
        self.calls.set(self.calls.get() + 1);
        Ok(Transition::accepted(
            Id {
                n: *i,
                lossy: self.lossy,
            },
            vec![],
        ))
    }
    fn check_state(&self, s: &Id) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if self.failing.contains(&s.n) {
            Check::failed("bad", s.n.to_string())
        } else {
            Check::passed("bad")
        }])
    }
}
impl Enumerate for Control {
    fn inputs(&self, s: &Id) -> Result<Vec<i64>, ModelError> {
        Ok(self.edges.get(&s.n).cloned().unwrap_or_default())
    }
}

fn explore_control(m: &Control) -> GraphSearchReport<Id, i64, i64> {
    explore_graph(
        m,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            failure_policy: FailurePolicy::Collect {
                max_retained_findings: 8,
                expand_failing_successors: true,
            },
            ..GraphSearchOptions::default()
        },
    )
    .unwrap()
}

#[test]
fn missing_transition_is_detected() {
    let full = explore_control(&Control::new());
    let missing = explore_control(&Control {
        edges: HashMap::from([(0, vec![1, 2]), (1, vec![3])]),
        ..Control::new()
    });
    let c = compare_reduction(&full, &missing, None).unwrap();
    assert!(!c.equivalent());
    assert_eq!(c.unrepresented_edges, [3]);
    assert!(!c.findings_match);
}

#[test]
fn incorrect_identity_merging_is_detected() {
    // States 1 and 2 are merged by a lossy equality with a colliding hash.
    let correct = explore_control(&Control::new());
    let lossy = explore_control(&Control {
        lossy: true,
        ..Control::new()
    });
    assert_eq!(lossy.nodes.len(), 3);
    let c = compare_reduction(&correct, &lossy, None).unwrap();
    assert!(!c.equivalent());
    assert_eq!(c.missing_in_reduced, [2]);
}

#[test]
fn suppressed_check_is_detected() {
    let checked = explore_control(&Control::new());
    let suppressed = explore_control(&Control {
        failing: HashSet::new(),
        ..Control::new()
    });
    assert_eq!(checked.finding_count, 2);
    assert_eq!(suppressed.finding_count, 0);
    let c = compare_reduction(&checked, &suppressed, None).unwrap();
    assert!(!c.findings_match);
    assert_eq!(
        c.unrepresented_edges,
        [2, 3],
        "edges carry their check batches"
    );
}

#[test]
fn reduction_never_skips_execution() {
    let plain = Control::new();
    let unreduced = explore_control(&plain);
    let counted = Control::new();
    struct All;
    impl EquivalenceReduction<Id, i64, i64> for All {
        fn eligible_class(&mut self, _: &Id, _: &i64) -> Result<Option<String>, ModelError> {
            Ok(Some("all".into()))
        }
        fn projection(
            &mut self,
            _: &i64,
            _: &TransitionRef<'_, Id, i64>,
            _: &[String],
        ) -> Result<Vec<u8>, ModelError> {
            Ok(vec![])
        }
    }
    let mut all = All;
    let reduced = explore_graph(
        &counted,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            failure_policy: FailurePolicy::Collect {
                max_retained_findings: 8,
                expand_failing_successors: true,
            },
            reduction: Some(&mut all),
            ..GraphSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(plain.calls.get(), counted.calls.get());
    assert_eq!(reduced.transitions, unreduced.transitions);
    assert_eq!(reduced.failed_properties, unreduced.failed_properties);
}
