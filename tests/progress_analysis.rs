//! progress-analysis-v1 (S3): the shared conformance/progress-analysis-v1.json
//! graphs and hand-authored cases independent of any application.
#[path = "support/json.rs"]
mod json;

use std::collections::HashMap;

use stateless::explore::{SearchConfig, SearchLimits};
use stateless::graph::{GraphSearchOptions, explore_graph};
use stateless::model::*;
use stateless::progress::*;

fn graph(n: usize, edges: &[(usize, usize, bool)], unmeasured: &[usize]) -> ProgressGraph {
    ProgressGraph::new(
        n,
        edges
            .iter()
            .map(|&(source, target, fair)| ProgressEdge {
                source,
                target,
                fair,
            })
            .collect(),
        (0..n).map(|v| !unmeasured.contains(&v)).collect(),
    )
    .unwrap()
}

fn kinds(values: &[ProgressKind]) -> impl FnMut(usize) -> Result<ProgressKind, ModelError> + '_ {
    move |v| Ok(values[v])
}

fn analyze(g: &ProgressGraph, k: &[ProgressKind], ranks: Option<&[u64]>) -> ProgressReport {
    match ranks {
        Some(ranks) => analyze_progress(g, kinds(k), Some(&mut |v: usize| Ok(ranks[v]))).unwrap(),
        None => analyze_progress(g, kinds(k), None).unwrap(),
    }
}

use ProgressClass as C;
use ProgressKind as K;

#[test]
fn progress_analysis_fixtures() {
    let doc = json::parse(include_str!("../conformance/progress-analysis-v1.json"));
    assert_eq!(doc.get("version").int(), 1);
    let cases = doc.get("cases").arr();
    assert_eq!(cases.len(), 9);
    for c in cases {
        let name = c.get("case").str();
        let n = c.get("nodes").int() as usize;
        let edges: Vec<(usize, usize, bool)> = c
            .get("edges")
            .arr()
            .iter()
            .map(|e| {
                let e = e.arr();
                (e[0].int() as usize, e[1].int() as usize, e[2].bool())
            })
            .collect();
        let unmeasured: Vec<usize> = c
            .get("unmeasured")
            .ints()
            .iter()
            .map(|&v| v as usize)
            .collect();
        let g = graph(n, &edges, &unmeasured);
        let k: Vec<ProgressKind> = c
            .get("kinds")
            .strings()
            .iter()
            .map(|s| match s.as_str() {
                "terminal" => K::Terminal,
                "intentionalWait" => K::IntentionalWait,
                "externalWait" => K::ExternalWait,
                "transaction" => K::Transaction,
                "other" => K::Other,
                other => panic!("unknown kind {other}"),
            })
            .collect();
        let ranks: Option<Vec<u64>> = (!c.get("ranks").is_null())
            .then(|| c.get("ranks").ints().iter().map(|&r| r as u64).collect());
        let r = analyze(&g, &k, ranks.as_deref());
        let x = c.get("expected");
        assert_eq!(
            r.classes.iter().map(|c| c.as_str()).collect::<Vec<_>>(),
            x.get("classes").strings(),
            "{name}"
        );
        let closed: Vec<Vec<usize>> = x
            .get("closed")
            .arr()
            .iter()
            .map(|c| c.ints().iter().map(|&v| v as usize).collect())
            .collect();
        assert_eq!(r.closed_nonterminal_components, closed, "{name}");
        assert_eq!(
            r.unsettled_transaction_states,
            x.get("unsettled")
                .ints()
                .iter()
                .map(|&v| v as usize)
                .collect::<Vec<_>>(),
            "{name}"
        );
        assert_eq!(
            r.settled_transaction_states as i64,
            x.get("settled_transactions").int(),
            "{name}"
        );
        assert_eq!(
            r.unmeasured_components as i64,
            x.get("unmeasured_components").int(),
            "{name}"
        );
        assert_eq!(r.graph_complete, x.get("graph_complete").bool(), "{name}");
    }
}

#[test]
fn settling_transaction() {
    let r = analyze(
        &graph(2, &[(0, 1, true)], &[]),
        &[K::Transaction, K::Terminal],
        None,
    );
    assert_eq!(r.classes, [C::TransactionProgress, C::Terminal]);
    assert_eq!(r.transaction_states, 1);
    assert_eq!(r.settled_transaction_states, 1);
    assert!(r.closed_nonterminal_components.is_empty());
    assert!(r.unsettled_transaction_states.is_empty());
    assert!(r.graph_complete);
}

#[test]
fn intentional_and_external_waits() {
    let r = analyze(
        &graph(3, &[(0, 1, true), (0, 2, true), (1, 1, false)], &[]),
        &[K::Transaction, K::IntentionalWait, K::ExternalWait],
        None,
    );
    assert_eq!(
        r.classes,
        [
            C::TransactionProgress,
            C::StableIntentionalWait,
            C::DeclaredExternalWait
        ]
    );
    assert_eq!(r.stable_intentional_wait_states, 1);
    assert_eq!(r.external_wait_states, 1);
    assert_eq!(r.settled_transaction_states, 1);
    // A self-looping wait is settled, not a closed nonterminal component.
    assert!(r.closed_nonterminal_components.is_empty());
}

#[test]
fn closed_nonterminal_cycle() {
    let r = analyze(
        &graph(3, &[(0, 1, true), (1, 0, true), (2, 0, true)], &[]),
        &[K::Transaction; 3],
        Some(&[4, 4, 4]),
    );
    assert_eq!(r.closed_nonterminal_components, [vec![0, 1]]);
    assert_eq!(r.unsettled_transaction_states, [0, 1, 2]);
    assert_eq!(r.maximum_rank, 4);
}

#[test]
fn unfair_only_exit() {
    let only = analyze(
        &graph(2, &[(0, 1, false)], &[]),
        &[K::Transaction, K::Terminal],
        None,
    );
    assert_eq!(only.classes, [C::Stuck, C::Terminal]);
    assert_eq!(only.stuck_states, [0]);
    assert!(only.closed_nonterminal_components.is_empty());
    assert!(
        only.unsettled_transaction_states.is_empty(),
        "a stuck transaction is reported as stuck, not unsettled"
    );
    let looped = analyze(
        &graph(2, &[(0, 0, true), (0, 1, false)], &[]),
        &[K::Transaction, K::Terminal],
        None,
    );
    assert_eq!(looped.classes, [C::TransactionProgress, C::Terminal]);
    assert_eq!(looped.unsettled_transaction_states, [0]);
    assert!(
        looped.closed_nonterminal_components.is_empty(),
        "an unfair exit still leaves the component"
    );
}

#[test]
fn fair_rank_decrease_opens_component() {
    let ranks = [2, 1];
    let decreasing = analyze(
        &graph(2, &[(0, 1, true), (1, 0, true)], &[]),
        &[K::Transaction; 2],
        Some(&ranks),
    );
    assert!(decreasing.closed_nonterminal_components.is_empty());
    let unfair = analyze(
        &graph(2, &[(0, 1, false), (1, 0, true)], &[]),
        &[K::Transaction; 2],
        Some(&ranks),
    );
    assert_eq!(
        unfair.closed_nonterminal_components,
        [vec![0, 1]],
        "an unfair decrease does not count"
    );
}

#[test]
fn depth_cutoff_is_unmeasured() {
    let r = analyze(&graph(2, &[(0, 1, true)], &[1]), &[K::Transaction; 2], None);
    assert_eq!(r.classes, [C::TransactionProgress, C::UnmeasuredCutoff]);
    assert_eq!(r.unmeasured_states, 1);
    assert_eq!(r.unsettled_transaction_states, [0]);
    assert!(r.closed_nonterminal_components.is_empty());
    assert!(!r.graph_complete);
}

#[test]
fn partial_expansion_blocks_closed_claim() {
    // Node 1 was interrupted: its known edge closes a cycle but other edges are unknown.
    let r = analyze(
        &graph(2, &[(0, 1, true), (1, 0, true)], &[1]),
        &[K::Transaction; 2],
        None,
    );
    assert!(r.closed_nonterminal_components.is_empty());
    assert_eq!(r.unmeasured_components, 1);
}

#[test]
fn iterative_scc_handles_deep_graphs() {
    let n = 200_000;
    let mut cycle: Vec<(usize, usize)> = (0..n - 1).map(|v| (v, v + 1)).collect();
    cycle.push((n - 1, 0));
    let components = strongly_connected_components(n, &cycle).unwrap();
    assert_eq!(components.len(), 1);
    assert_eq!(components[0].len(), n);
    assert_eq!(
        strongly_connected_components(4, &[(0, 1), (1, 0), (2, 3)]).unwrap(),
        [vec![0, 1], vec![2], vec![3]]
    );
    let edges = [
        ProgressEdge {
            source: 0,
            target: 1,
            fair: true,
        },
        ProgressEdge {
            source: 2,
            target: 1,
            fair: false,
        },
    ];
    assert_eq!(
        reverse_reachable(4, &edges, &[1], |e| e.fair).unwrap(),
        [true, true, false, false]
    );
    assert!(strongly_connected_components(1, &[(0, 1)]).is_err());
}

/// step(state, input) = input over a fixed adjacency list.
struct Lookup(HashMap<i64, Vec<i64>>);
impl Model for Lookup {
    type State = i64;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "lookup".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<i64, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &i64, input: &i64) -> Result<Transition<i64, i64>, ModelError> {
        Ok(Transition::accepted(*input, vec![]))
    }
    fn check_state(&self, _: &i64) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl Enumerate for Lookup {
    fn inputs(&self, state: &i64) -> Result<Vec<i64>, ModelError> {
        Ok(self.0.get(state).cloned().unwrap_or_default())
    }
}

#[test]
fn graph_from_search_report() {
    let m = Lookup(HashMap::from([(0, vec![1, 2]), (1, vec![3]), (2, vec![2])]));
    let report = explore_graph(
        &m,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions::default(),
    )
    .unwrap();
    let g = ProgressGraph::from_report(&report, |e| Ok(e.input != 2)).unwrap();
    let edge = |source, target, fair| ProgressEdge {
        source,
        target,
        fair,
    };
    assert_eq!(
        g.edges(),
        [
            edge(0, 1, true),
            edge(0, 2, false),
            edge(1, 3, true),
            edge(2, 2, false)
        ]
    );
    let r = analyze_progress(
        &g,
        |v| {
            Ok(if report.nodes[v].state == 3 {
                K::Terminal
            } else {
                K::Transaction
            })
        },
        None,
    )
    .unwrap();
    assert_eq!(
        r.classes,
        [
            C::TransactionProgress,
            C::TransactionProgress,
            C::Stuck,
            C::Terminal
        ]
    );
    assert_eq!(r.closed_nonterminal_components, [vec![2]]);
    let bounded = explore_graph(
        &m,
        SearchConfig {
            max_depth: 1,
            ..SearchConfig::default()
        },
        SearchLimits::default(),
        GraphSearchOptions::default(),
    )
    .unwrap();
    assert_eq!(
        ProgressGraph::from_report(&bounded, |_| Ok(true))
            .unwrap()
            .measured(),
        [true, false, false]
    );
    assert!(ProgressGraph::new(1, vec![edge(0, 1, true)], vec![true]).is_err());
}

#[test]
fn false_progress_is_detected() {
    // 0 <-> 1 is a closed transaction cycle. Omitting the back edge or claiming
    // an unmeasured node is complete changes the evidence.
    let truth = analyze(
        &graph(2, &[(0, 1, true), (1, 0, true)], &[]),
        &[K::Transaction; 2],
        None,
    );
    assert_eq!(truth.closed_nonterminal_components, [vec![0, 1]]);
    let truncated = analyze(&graph(2, &[(0, 1, true)], &[1]), &[K::Transaction; 2], None);
    assert!(truncated.closed_nonterminal_components.is_empty());
    assert!(
        !truncated.graph_complete,
        "a cutoff is not reported as progress"
    );
    assert_ne!(truth, truncated);
    // A depth-bounded search cannot be mistaken for a complete graph.
    let m = Lookup(HashMap::from([(0, vec![1, 2]), (1, vec![3]), (2, vec![3])]));
    let bounded = explore_graph(
        &m,
        SearchConfig {
            max_depth: 1,
            ..SearchConfig::default()
        },
        SearchLimits::default(),
        GraphSearchOptions::default(),
    )
    .unwrap();
    let g = ProgressGraph::from_report(&bounded, |_| Ok(true)).unwrap();
    assert!(
        !analyze_progress(&g, |_| Ok(K::Transaction), None)
            .unwrap()
            .graph_complete
    );
}
