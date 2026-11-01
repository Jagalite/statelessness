//! progress-analysis-v1 (S3); see spec/progress-analysis-v1.md. Applications
//! supply node kinds, ranks and edge fairness; this module owns only graph
//! algorithms. Fair-path reachability and rank checks over a bounded graph are
//! evidence, not a proof that every scheduler execution eventually completes.

use std::collections::BTreeSet;
use std::fmt;

use crate::graph::{EdgeTarget, GraphEdge, GraphSearchReport, NodeExpansion};
use crate::model::ModelError;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ProgressEdge {
    pub source: usize,
    pub target: usize,
    pub fair: bool,
}

/// Dense node IDs, directed edges with a fairness flag, and one `measured`
/// flag per node. Endpoints are validated on construction.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProgressGraph {
    node_count: usize,
    edges: Vec<ProgressEdge>,
    measured: Vec<bool>,
}

fn endpoint_error() -> ModelError {
    ModelError::new("progress edge endpoint out of range")
}

impl ProgressGraph {
    pub fn new(
        node_count: usize,
        edges: Vec<ProgressEdge>,
        measured: Vec<bool>,
    ) -> Result<Self, ModelError> {
        if measured.len() != node_count {
            return Err(ModelError::new(
                "progress graph needs one measured flag per node",
            ));
        }
        if edges
            .iter()
            .any(|e| e.source >= node_count || e.target >= node_count)
        {
            return Err(endpoint_error());
        }
        Ok(Self {
            node_count,
            edges,
            measured,
        })
    }

    /// Admitted and duplicate edges keep their targets; reduced edges take
    /// their representative's target. A node is measured only when its
    /// expansion is `Complete` and none of its edges was rejected. Requires
    /// retained edges.
    pub fn from_report<S, I, O>(
        report: &GraphSearchReport<S, I, O>,
        mut fair: impl FnMut(&GraphEdge<I, O>) -> Result<bool, ModelError>,
    ) -> Result<Self, ModelError> {
        if !report.edges_retained {
            return Err(ModelError::new("progress analysis requires retained edges"));
        }
        let mut measured: Vec<bool> = report
            .nodes
            .iter()
            .map(|node| node.expansion == NodeExpansion::Complete)
            .collect();
        let mut edges = Vec::new();
        for edge in &report.edges {
            let mut target = edge.target;
            let mut hops = 0;
            while let EdgeTarget::Reduced(representative) = target {
                match report.edges.get(representative) {
                    Some(next) if hops <= report.edges.len() => {
                        target = next.target;
                        hops += 1;
                    }
                    _ => break,
                }
            }
            match target {
                EdgeTarget::Admitted(node) | EdgeTarget::Duplicate(node) => {
                    let fair = fair(edge)
                        .map_err(|e| ModelError::new(format!("progress fairness: {e}")))?;
                    edges.push(ProgressEdge {
                        source: edge.source,
                        target: node,
                        fair,
                    });
                }
                _ => {
                    *measured.get_mut(edge.source).ok_or_else(endpoint_error)? = false;
                }
            }
        }
        Self::new(report.nodes.len(), edges, measured)
    }

    pub fn node_count(&self) -> usize {
        self.node_count
    }
    pub fn edges(&self) -> &[ProgressEdge] {
        &self.edges
    }
    /// False for a node whose outgoing edges are not all known: a depth
    /// frontier, an interrupted or unexpanded node, or a rejected successor.
    pub fn measured(&self) -> &[bool] {
        &self.measured
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ProgressKind {
    Terminal,
    IntentionalWait,
    ExternalWait,
    Transaction,
    Other,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ProgressClass {
    Terminal,
    StableIntentionalWait,
    DeclaredExternalWait,
    TransactionProgress,
    Stuck,
    UnmeasuredCutoff,
}

impl ProgressClass {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Terminal => "terminal",
            Self::StableIntentionalWait => "stableIntentionalWait",
            Self::DeclaredExternalWait => "declaredExternalWait",
            Self::TransactionProgress => "transactionProgress",
            Self::Stuck => "stuck",
            Self::UnmeasuredCutoff => "unmeasuredCutoff",
        }
    }
    fn settled(self) -> bool {
        matches!(
            self,
            Self::Terminal | Self::StableIntentionalWait | Self::DeclaredExternalWait
        )
    }
}

impl fmt::Display for ProgressClass {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProgressReport {
    pub classes: Vec<ProgressClass>,
    pub terminal_states: usize,
    pub stable_intentional_wait_states: usize,
    pub external_wait_states: usize,
    pub unmeasured_states: usize,
    pub transaction_states: usize,
    pub settled_transaction_states: usize,
    /// `TransactionProgress` nodes with no fair path to a settled node.
    pub unsettled_transaction_states: Vec<usize>,
    pub stuck_states: Vec<usize>,
    /// Members sorted ascending; components sorted by first member.
    pub closed_nonterminal_components: Vec<Vec<usize>>,
    /// Closed candidates excluded because a member is unmeasured.
    pub unmeasured_components: usize,
    pub maximum_rank: u64,
    /// Every node is measured; otherwise absence of a finding is not evidence.
    pub graph_complete: bool,
}

/// Tarjan's algorithm with an explicit work stack (no recursion). Members are
/// sorted ascending and components are ordered by their smallest member.
pub fn strongly_connected_components(
    node_count: usize,
    edges: &[(usize, usize)],
) -> Result<Vec<Vec<usize>>, ModelError> {
    let mut adjacency = vec![Vec::new(); node_count];
    for &(source, target) in edges {
        if target >= node_count {
            return Err(endpoint_error());
        }
        adjacency
            .get_mut(source)
            .ok_or_else(endpoint_error)?
            .push(target);
    }
    let mut index = vec![usize::MAX; node_count];
    let mut low = vec![0usize; node_count];
    let mut on_stack = vec![false; node_count];
    let mut stack = Vec::new();
    let mut components = Vec::new();
    let mut counter = 0usize;
    // (node, next adjacency position)
    let mut work: Vec<(usize, usize)> = Vec::new();
    for root in 0..node_count {
        if index[root] != usize::MAX {
            continue;
        }
        work.push((root, 0));
        while let Some(&(v, next)) = work.last() {
            if next == 0 && index[v] == usize::MAX {
                index[v] = counter;
                low[v] = counter;
                counter += 1;
                stack.push(v);
                on_stack[v] = true;
            }
            if next < adjacency[v].len() {
                let w = adjacency[v][next];
                work.last_mut().expect("frame").1 += 1;
                if index[w] == usize::MAX {
                    work.push((w, 0));
                } else if on_stack[w] {
                    low[v] = low[v].min(index[w]);
                }
                continue;
            }
            work.pop();
            if let Some(&(parent, _)) = work.last() {
                low[parent] = low[parent].min(low[v]);
            }
            if low[v] == index[v] {
                let mut component = Vec::new();
                while let Some(w) = stack.pop() {
                    on_stack[w] = false;
                    component.push(w);
                    if w == v {
                        break;
                    }
                }
                component.sort_unstable();
                components.push(component);
            }
        }
    }
    components.sort_by_key(|c: &Vec<usize>| c[0]);
    Ok(components)
}

/// Nodes with a path to any target using edges accepted by `include`,
/// breadth-first over the reversed edges.
pub fn reverse_reachable(
    node_count: usize,
    edges: &[ProgressEdge],
    targets: &[usize],
    mut include: impl FnMut(&ProgressEdge) -> bool,
) -> Result<Vec<bool>, ModelError> {
    let mut reverse = vec![Vec::new(); node_count];
    for edge in edges {
        if edge.source >= node_count {
            return Err(endpoint_error());
        }
        if include(edge) {
            reverse
                .get_mut(edge.target)
                .ok_or_else(endpoint_error)?
                .push(edge.source);
        }
    }
    let mut reached = vec![false; node_count];
    let mut queue = Vec::new();
    for &target in targets {
        let slot = reached.get_mut(target).ok_or_else(endpoint_error)?;
        if !*slot {
            *slot = true;
            queue.push(target);
        }
    }
    let mut head = 0;
    while head < queue.len() {
        let v = queue[head];
        head += 1;
        for &u in &reverse[v] {
            if !reached[u] {
                reached[u] = true;
                queue.push(u);
            }
        }
    }
    Ok(reached)
}

/// Classification order: unmeasured, terminal, external wait, intentional
/// wait, transaction with a fair outgoing edge, otherwise stuck. A closed
/// nonterminal component contains a transaction or stuck node, has no exit
/// edge (fair or unfair), no fair rank-decreasing edge, and no unmeasured
/// member. Ranks default to zero.
pub fn analyze_progress(
    graph: &ProgressGraph,
    mut kind: impl FnMut(usize) -> Result<ProgressKind, ModelError>,
    mut rank: Option<&mut dyn FnMut(usize) -> Result<u64, ModelError>>,
) -> Result<ProgressReport, ModelError> {
    let n = graph.node_count;
    let mut outgoing = vec![Vec::new(); n];
    for edge in &graph.edges {
        outgoing[edge.source].push(*edge);
    }
    let mut classes = Vec::with_capacity(n);
    let mut ranks = Vec::with_capacity(n);
    for (v, out) in outgoing.iter().enumerate() {
        ranks.push(match rank.as_mut() {
            Some(rank) => rank(v).map_err(|e| ModelError::new(format!("progress rank: {e}")))?,
            None => 0,
        });
        if !graph.measured[v] {
            classes.push(ProgressClass::UnmeasuredCutoff);
            continue;
        }
        classes.push(
            match kind(v).map_err(|e| ModelError::new(format!("progress kind: {e}")))? {
                ProgressKind::Terminal => ProgressClass::Terminal,
                ProgressKind::ExternalWait => ProgressClass::DeclaredExternalWait,
                ProgressKind::IntentionalWait => ProgressClass::StableIntentionalWait,
                ProgressKind::Transaction if out.iter().any(|e| e.fair) => {
                    ProgressClass::TransactionProgress
                }
                ProgressKind::Transaction | ProgressKind::Other => ProgressClass::Stuck,
            },
        );
    }
    let settled: Vec<usize> = (0..n).filter(|&v| classes[v].settled()).collect();
    let can_settle = reverse_reachable(n, &graph.edges, &settled, |e| e.fair)?;
    let pairs: Vec<(usize, usize)> = graph.edges.iter().map(|e| (e.source, e.target)).collect();
    let mut closed = Vec::new();
    let mut unmeasured_components = 0;
    for component in strongly_connected_components(n, &pairs)? {
        if !component.iter().any(|&v| {
            matches!(
                classes[v],
                ProgressClass::TransactionProgress | ProgressClass::Stuck
            )
        }) {
            continue;
        }
        let members: BTreeSet<usize> = component.iter().copied().collect();
        let mut exits = component.iter().flat_map(|&v| outgoing[v].iter());
        if exits
            .any(|e| !members.contains(&e.target) || (e.fair && ranks[e.target] < ranks[e.source]))
        {
            continue;
        }
        if component.iter().any(|&v| !graph.measured[v]) {
            unmeasured_components += 1;
            continue;
        }
        closed.push(component);
    }
    let count = |class: ProgressClass| classes.iter().filter(|&&c| c == class).count();
    let transactions: Vec<usize> = (0..n)
        .filter(|&v| classes[v] == ProgressClass::TransactionProgress)
        .collect();
    Ok(ProgressReport {
        terminal_states: count(ProgressClass::Terminal),
        stable_intentional_wait_states: count(ProgressClass::StableIntentionalWait),
        external_wait_states: count(ProgressClass::DeclaredExternalWait),
        unmeasured_states: count(ProgressClass::UnmeasuredCutoff),
        transaction_states: transactions.len(),
        settled_transaction_states: transactions.iter().filter(|&&v| can_settle[v]).count(),
        unsettled_transaction_states: transactions
            .iter()
            .copied()
            .filter(|&v| !can_settle[v])
            .collect(),
        stuck_states: (0..n)
            .filter(|&v| classes[v] == ProgressClass::Stuck)
            .collect(),
        closed_nonterminal_components: closed,
        unmeasured_components,
        maximum_rank: ranks.iter().copied().max().unwrap_or(0),
        graph_complete: graph.measured.iter().all(|&m| m),
        classes,
    })
}
