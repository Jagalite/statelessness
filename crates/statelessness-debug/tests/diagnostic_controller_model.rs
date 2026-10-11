//! Independent finite subscription-control model. Every explored edge is checked
//! against a real DiagnosticHub reconstructed from a witness path; witness paths
//! are excluded from equality so equivalent control states close cycles.
use stateless::explore::{SearchConfig, SearchTermination, enumerate};
use stateless::{Check, Enumerate, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::diagnostic::*;
use statelessness_debug::inspect::{NodeKind, Scalar};
use std::hash::{Hash, Hasher};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Op {
    Configure(bool),
    StaleConfigure,
    UnauthorizedConfigure,
    OverCapacity,
    Ack { producer: usize, boundary: u64 },
    StaleAck(usize),
    Turn { producer: usize, boundary: u64 },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Reason {
    Initial,
    Reconfigured,
    Gap,
}
impl Reason {
    fn public(self) -> ProbeBaselineReason {
        match self {
            Self::Initial => ProbeBaselineReason::Initial,
            Self::Reconfigured => ProbeBaselineReason::Reconfigured,
            Self::Gap => ProbeBaselineReason::ObservationGap,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct ProducerSpec {
    revision: u64,
    enabled: bool,
    effective: u64,
    last: Option<u64>,
    sequence: u64,
    baseline: bool,
    reason: Reason,
}
impl Default for ProducerSpec {
    fn default() -> Self {
        Self {
            revision: 0,
            enabled: false,
            effective: 0,
            last: None,
            sequence: 0,
            baseline: false,
            reason: Reason::Initial,
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
struct Spec {
    revision: u64,
    enabled: bool,
    producers: [ProducerSpec; 2],
    captured: u64,
    gaps: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
enum Reply {
    Configured {
        revision: u64,
        pending: Vec<u64>,
    },
    Acked {
        producer: u64,
        epoch: u64,
        revision: u64,
        boundary: u64,
    },
    Turn,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Emission {
    producer: u64,
    epoch: u64,
    revision: u64,
    sequence: u64,
    boundary: u64,
    baseline: bool,
    reason: Option<ProbeBaselineReason>,
    unknown: bool,
    value: u64,
}
type Applied = (Result<Reply, DiagnosticError>, Vec<Emission>);
impl Spec {
    fn apply(&mut self, op: Op) -> Applied {
        let mut events = vec![];
        let result = match op {
            Op::Configure(enabled) => {
                self.revision += 1;
                self.enabled = enabled;
                Ok(Reply::Configured {
                    revision: self.revision,
                    pending: vec![1, 2],
                })
            }
            Op::StaleConfigure | Op::StaleAck(_) => Err(DiagnosticError::StaleRevision),
            Op::UnauthorizedConfigure => Err(DiagnosticError::Unauthorized),
            Op::OverCapacity => Err(DiagnosticError::Capacity),
            Op::Ack { producer, boundary } => {
                let p = &mut self.producers[producer];
                if p.last.is_some_and(|last| boundary <= last) {
                    Err(DiagnosticError::StaleBoundary)
                } else {
                    p.reason = if p.revision == 0 {
                        Reason::Initial
                    } else {
                        Reason::Reconfigured
                    };
                    p.revision = self.revision;
                    p.enabled = self.enabled;
                    p.effective = boundary;
                    p.baseline = false;
                    Ok(Reply::Acked {
                        producer: producer as u64 + 1,
                        epoch: producer as u64 + 7,
                        revision: self.revision,
                        boundary,
                    })
                }
            }
            Op::Turn { producer, boundary } => {
                let p = &mut self.producers[producer];
                if p.last.is_some_and(|last| boundary <= last) {
                    Err(DiagnosticError::StaleBoundary)
                } else if boundary < p.effective {
                    Err(DiagnosticError::PendingConfiguration)
                } else {
                    if p.last.is_some_and(|last| boundary != last + 1) {
                        p.baseline = false;
                        p.reason = Reason::Gap;
                        if p.enabled {
                            self.gaps += 1;
                        }
                    }
                    p.last = Some(boundary);
                    if p.enabled {
                        p.sequence += 1;
                        self.captured += 1;
                        events.push(Emission {
                            producer: producer as u64 + 1,
                            epoch: producer as u64 + 7,
                            revision: p.revision,
                            sequence: p.sequence,
                            boundary,
                            baseline: !p.baseline,
                            reason: (!p.baseline).then_some(p.reason.public()),
                            unknown: !p.baseline && p.reason != Reason::Initial,
                            value: boundary,
                        });
                        p.baseline = true;
                    }
                    Ok(Reply::Turn)
                }
            }
        };
        (result, events)
    }
}
fn subscription(sink: u64) -> Subscription {
    Subscription {
        sink,
        site: "value".into(),
        kind: SiteKind::Probe,
        path: vec![],
        trigger: Trigger::Every,
        sample_every: 1,
        minimum_severity: Severity::Debug,
    }
}
fn hub() -> DiagnosticHub {
    let mut hub = DiagnosticHub::new(DiagnosticLimits {
        subscriptions: 1,
        ..Default::default()
    });
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.add_sink(2, SinkPermissions::deny_all(), SinkLimits::default())
        .unwrap();
    hub.register_producer(1, 7).unwrap();
    hub.register_producer(2, 8).unwrap();
    hub
}
fn configure(
    hub: &mut DiagnosticHub,
    expected: u64,
    config: Vec<Subscription>,
) -> Result<Reply, DiagnosticError> {
    hub.configure(expected, config)
        .map(|ack| Reply::Configured {
            revision: ack.capture_revision,
            pending: ack.pending_producers,
        })
}
fn ack(
    hub: &mut DiagnosticHub,
    producer: usize,
    revision: u64,
    boundary: u64,
) -> Result<Reply, DiagnosticError> {
    hub.acknowledge(producer as u64 + 1, revision, boundary)
        .map(|ack| Reply::Acked {
            producer: ack.producer,
            epoch: ack.epoch,
            revision: ack.capture_revision,
            boundary: ack.effective_transition,
        })
}
fn apply(hub: &mut DiagnosticHub, op: Op) -> Applied {
    let revision = hub.revision();
    let result = match op {
        Op::Configure(enabled) => configure(
            hub,
            revision,
            if enabled {
                vec![subscription(1)]
            } else {
                vec![]
            },
        ),
        Op::StaleConfigure => configure(hub, revision + 1, vec![]),
        Op::UnauthorizedConfigure => configure(hub, revision, vec![subscription(2)]),
        Op::OverCapacity => configure(hub, revision, vec![subscription(1), subscription(1)]),
        Op::Ack { producer, boundary } => ack(hub, producer, revision, boundary),
        Op::StaleAck(producer) => ack(hub, producer, revision + 1, 0),
        Op::Turn { producer, boundary } => {
            match hub.begin_turn(producer as u64 + 1, 91, boundary, DiagnosticOrigin::Test) {
                Ok(mut scope) => {
                    scope.probe("value", || boundary);
                    scope.finish(true);
                    Ok(Reply::Turn)
                }
                Err(error) => Err(error),
            }
        }
    };
    let mut events = vec![];
    while let Some(event) = hub.pop(1) {
        assert_eq!(event.run_id, 91);
        assert_eq!(event.origin, DiagnosticOrigin::Test);
        assert!(!event.incomplete_turn);
        let value = match event.payload.unwrap().kind {
            NodeKind::Scalar(Scalar::Integer { decimal, .. }) => decimal.parse().unwrap(),
            other => panic!("unexpected projection {other:?}"),
        };
        events.push(Emission {
            producer: event.producer,
            epoch: event.epoch,
            revision: event.capture_revision,
            sequence: event.producer_sequence,
            boundary: event.transition,
            baseline: event.baseline,
            reason: event.baseline_reason,
            unknown: event.change_unknown,
            value,
        });
    }
    assert!(hub.pop(2).is_none());
    (result, events)
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
struct SubscriptionModel;
impl Model for SubscriptionModel {
    type State = State;
    type Input = Op;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "subscription-controller-reference".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State {
            spec: Spec::default(),
            history: vec![],
            mismatch: None,
        })
    }
    fn step(&self, before: &State, op: &Op) -> Result<Transition<State, ()>, ModelError> {
        let mut actual = hub();
        for prior in &before.history {
            let _ = apply(&mut actual, *prior);
        }
        let mut after = before.clone();
        let expected = after.spec.apply(*op);
        let result = apply(&mut actual, *op);
        after.history.push(*op);
        let health = actual.health(1).unwrap();
        if result != expected
            || actual.revision() != after.spec.revision
            || health.captured != after.spec.captured
            || health.observation_gaps != after.spec.gaps
        {
            after.mismatch = Some(format!(
                "path {:?}: actual {result:?}, expected {expected:?}; revision {}; health {health:?}; reference {:?}",
                after.history,
                actual.revision(),
                after.spec
            ));
        }
        Ok(Transition::accepted(after, vec![]))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        Ok(vec![match &state.mismatch {
            Some(error) => Check::failed("subscription-reference", error.clone()),
            None => Check::passed("subscription-reference"),
        }])
    }
}
impl Enumerate for SubscriptionModel {
    fn inputs(&self, state: &State) -> Result<Vec<Op>, ModelError> {
        let mut ops = vec![
            Op::StaleConfigure,
            Op::UnauthorizedConfigure,
            Op::OverCapacity,
        ];
        if state.spec.revision < 2 {
            ops.extend([Op::Configure(false), Op::Configure(true)]);
        }
        for producer in 0..2 {
            ops.push(Op::StaleAck(producer));
            for boundary in 0..=2 {
                ops.push(Op::Ack { producer, boundary });
                ops.push(Op::Turn { producer, boundary });
            }
        }
        Ok(ops)
    }
}
#[test]
fn bounded_two_producer_subscription_interleavings_match_reference() {
    let report = enumerate(
        &SubscriptionModel,
        SearchConfig {
            max_states: 200_000,
            max_transitions: 4_000_000,
            max_depth: 32,
        },
    )
    .unwrap();
    assert!(report.failure.is_none(), "{:?}", report.failure);
    assert_eq!(
        report.termination,
        SearchTermination::GraphExhausted,
        "{report:?}"
    );
    assert!(report.states > 1000 && report.transitions > 10_000);
    eprintln!(
        "subscription controller qualification: {} states, {} edges, max depth {}; two producers, two configuration revisions, boundaries 0..=2, enabled/disabled routing, stale/unauthorized/capacity admission, per-producer ack/pending/gap/baseline envelopes",
        report.states, report.transitions, report.max_depth_reached
    );
}
