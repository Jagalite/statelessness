//! Finite independent reference ledger, explored with the Statelessness engine.
//! One effect/attempt/delivery isolates all ordering and duplicate boundaries;
//! separate fixtures qualify sequential retries and multiple deliveries.
use stateless::explore::{SearchConfig, SearchTermination, enumerate};
use stateless::{Check, Enumerate, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::effects::*;
use statelessness_debug::metrics::*;
use std::{
    hash::{Hash, Hasher},
    sync::Arc,
};
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Op {
    Accept,
    Reject,
    Create,
    Ready,
    RemoveReady,
    Start,
    Finish,
    Resolve,
    Cancel,
    ConfirmCancel,
    Publish,
    Commit,
    PublicationFail,
    Begin,
    Observe,
    Settle,
    Abandon,
}
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
struct Spec {
    admission: Option<bool>,
    attempt: bool,
    ready: bool,
    started: bool,
    finished: bool,
    resolved: bool,
    cancel: bool,
    cancel_ack: bool,
    delivery: bool,
    committed: bool,
    publication_failed: bool,
    begun: bool,
    observed: bool,
    settled: bool,
    abandoned: bool,
}
impl Spec {
    fn apply(&mut self, op: Op) -> bool {
        use Op::*;
        match op {
            Accept | Reject => {
                if self.admission.is_some() || self.resolved {
                    return false;
                }
                self.admission = Some(matches!(op, Accept));
            }
            Create => {
                if self.admission != Some(true) || self.resolved {
                    return false;
                }
                self.attempt = true;
            }
            Ready => {
                if !self.attempt
                    || self.ready
                    || self.started
                    || self.finished
                    || self.abandoned
                    || self.resolved
                {
                    return false;
                }
                self.ready = true;
            }
            RemoveReady => {
                if !self.attempt || !self.ready || self.started {
                    return false;
                }
                self.ready = false;
            }
            Start => {
                if !self.attempt || self.started || self.finished || self.abandoned || self.resolved
                {
                    return false;
                }
                self.started = true;
                self.ready = false;
            }
            Finish => {
                if !self.attempt || self.finished || !self.started {
                    return false;
                }
                self.finished = true;
            }
            Resolve => {
                if self.resolved || self.admission != Some(true) {
                    return false;
                }
                self.resolved = true;
            }
            Cancel => {
                if self.cancel {
                    return false;
                }
                self.cancel = true;
            }
            ConfirmCancel => {
                if self.cancel_ack || !self.cancel {
                    return false;
                }
                self.cancel_ack = true;
            }
            Publish => {
                if !self.resolved {
                    return false;
                }
                self.delivery = true;
            }
            Commit => {
                if !self.delivery || self.publication_failed {
                    return false;
                }
                self.committed = true;
            }
            PublicationFail => {
                if !self.delivery || self.publication_failed || self.begun || self.committed {
                    return false;
                }
                self.publication_failed = true;
            }
            Begin => {
                if !self.delivery || self.begun || self.publication_failed {
                    return false;
                }
                self.begun = true;
                self.committed = true;
            }
            Observe => {
                if !self.delivery || self.observed || !self.begun || self.publication_failed {
                    return false;
                }
                self.observed = true;
            }
            Settle => {
                if self.settled {
                    return false;
                }
                self.settled = true;
            }
            Abandon => {
                if !self.attempt {
                    return false;
                }
                if !self.finished {
                    self.abandoned = true;
                }
            }
        }
        true
    }
}
struct NoClock;
impl Clock for NoClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        panic!("counters-only ledger must not read a clock")
    }
}
struct Harness {
    o: EffectObserver,
    e: EffectHandle,
    a: Option<AttemptHandle>,
    d: Option<DeliveryHandle>,
}
impl Harness {
    fn new() -> Self {
        let o = EffectObserver::new(
            EffectOptions {
                timings: false,
                details: false,
                ..EffectOptions::default()
            },
            Arc::new(NoClock),
        )
        .unwrap();
        let e = o
            .requested(
                RequestOrigin {
                    run: 1,
                    epoch: 1,
                    machine: 1,
                    transition_sequence: 1,
                    output_index: 0,
                },
                Labels::default(),
            )
            .unwrap();
        Self {
            o,
            e,
            a: None,
            d: None,
        }
    }
    fn apply(&mut self, op: Op) -> bool {
        use Op::*;
        match op {
            Accept => self.o.admission(&self.e, Admission::Accepted).is_ok(),
            Reject => self.o.admission(&self.e, Admission::Rejected).is_ok(),
            Create => match self.o.attempt_created(&self.e) {
                Ok(a) => {
                    self.a = Some(a);
                    true
                }
                Err(_) => false,
            },
            Ready => self
                .a
                .as_ref()
                .is_some_and(|a| self.o.ready_queued(a).is_ok()),
            RemoveReady => self
                .a
                .as_ref()
                .is_some_and(|a| self.o.ready_removed(a).is_ok()),
            Start => self
                .a
                .as_ref()
                .is_some_and(|a| self.o.attempt_started(a).is_ok()),
            Finish => self
                .a
                .as_ref()
                .is_some_and(|a| self.o.attempt_finished(a, RuntimeOutcome::Success).is_ok()),
            Resolve => self
                .o
                .resolved(&self.e, RuntimeOutcome::Cancelled, None)
                .is_ok(),
            Cancel => self.o.cancellation_requested(&self.e).is_ok(),
            ConfirmCancel => self.o.cancellation_acknowledged(&self.e).is_ok(),
            Publish => match self.o.reserve_publication(&self.e) {
                Ok(d) => {
                    self.d = Some(d);
                    true
                }
                Err(_) => false,
            },
            Commit => self
                .d
                .as_ref()
                .is_some_and(|d| self.o.publication_committed(d).is_ok()),
            PublicationFail => self
                .d
                .as_ref()
                .is_some_and(|d| self.o.publication_failed(d).is_ok()),
            Begin => self
                .d
                .as_ref()
                .is_some_and(|d| self.o.delivery_begun(d).is_ok()),
            Observe => self.d.as_ref().is_some_and(|d| {
                self.o
                    .delivery_observed(d, DeliveryDisposition::Ignored)
                    .is_ok()
            }),
            Settle => self.o.settled(&self.e).is_ok(),
            Abandon => self
                .a
                .as_ref()
                .is_some_and(|a| self.o.attempt_abandoned(a).is_ok()),
        }
    }
    fn number(&self, f: MetricFamily) -> u64 {
        self.o
            .metric_snapshot()
            .series
            .iter()
            .filter(|(k, _)| k.family == f)
            .map(|(_, v)| match v {
                MetricValue::Counter(v) | MetricValue::Gauge(v) => *v,
                _ => 0,
            })
            .sum()
    }
    fn agrees(&self, s: &Spec) -> bool {
        [
            (MetricFamily::Requests, 1),
            (MetricFamily::Admissions, u64::from(s.admission.is_some())),
            (
                MetricFamily::Unresolved,
                u64::from(s.admission == Some(true) && !s.resolved),
            ),
            (MetricFamily::Running, u64::from(s.started && !s.finished)),
            (MetricFamily::ReadyDepth, u64::from(s.ready)),
            (MetricFamily::Attempts, u64::from(s.started)),
            (MetricFamily::AttemptOutcomes, u64::from(s.finished)),
            (MetricFamily::Resolutions, u64::from(s.resolved)),
            (
                MetricFamily::CompletionDepth,
                u64::from(s.delivery && !s.begun && !s.publication_failed),
            ),
            (MetricFamily::Deliveries, u64::from(s.observed)),
            (MetricFamily::CancellationRequests, u64::from(s.cancel)),
            (
                MetricFamily::CancellationAcknowledgements,
                u64::from(s.cancel_ack),
            ),
        ]
        .into_iter()
        .all(|(f, n)| self.number(f) == n)
    }
}
#[derive(Clone, Debug)]
struct State {
    spec: Spec,
    history: Vec<Op>,
    mismatch: Option<String>,
}
impl PartialEq for State {
    fn eq(&self, o: &Self) -> bool {
        self.spec == o.spec && self.mismatch == o.mismatch
    }
}
impl Eq for State {}
impl Hash for State {
    fn hash<H: Hasher>(&self, h: &mut H) {
        self.spec.hash(h);
        self.mismatch.hash(h);
    }
}
struct Ledger;
impl Model for Ledger {
    type State = State;
    type Input = Op;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "independent-effect-ledger".into(),
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
        let mut s = before.clone();
        let mut h = Harness::new();
        for previous in &s.history {
            h.apply(*previous);
        }
        let expected = s.spec.apply(*op);
        let actual = h.apply(*op);
        s.history.push(*op);
        if expected != actual || !h.agrees(&s.spec) {
            s.mismatch = Some(format!(
                "{:?}: actual={actual}, expected={expected}, ledger={:?}",
                s.history, s.spec
            ));
        }
        Ok(Transition::accepted(s, vec![]))
    }
    fn check_state(&self, s: &State) -> Result<Vec<Check>, ModelError> {
        Ok(vec![match &s.mismatch {
            Some(m) => Check::failed("independent-effect-accounting", m.clone()),
            None => Check::passed("independent-effect-accounting"),
        }])
    }
}
impl Enumerate for Ledger {
    fn inputs(&self, s: &State) -> Result<Vec<Op>, ModelError> {
        use Op::*;
        let mut ops = vec![
            Accept,
            Reject,
            Ready,
            RemoveReady,
            Start,
            Finish,
            Resolve,
            Cancel,
            ConfirmCancel,
            Commit,
            PublicationFail,
            Begin,
            Observe,
            Settle,
            Abandon,
        ];
        if !s.spec.attempt {
            ops.push(Create);
        }
        if !s.spec.delivery {
            ops.push(Publish);
        }
        Ok(ops)
    }
}
#[test]
fn exhaustive_independent_ledger_preserves_counts_and_physical_work() {
    let r = enumerate(
        &Ledger,
        SearchConfig {
            max_states: 20_000,
            max_transitions: 500_000,
            max_depth: 100,
        },
    )
    .unwrap();
    assert!(r.failure.is_none(), "{r:?}");
    assert_eq!(r.termination, SearchTermination::GraphExhausted, "{r:?}");
    assert!(r.states > 100 && r.transitions > 1000);
    eprintln!(
        "effect ledger: {} states, {} edges",
        r.states, r.transitions
    );
}
