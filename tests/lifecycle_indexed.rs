use stateless::lifecycle::indexed::*;
use stateless::lifecycle::{Event, History, LifecycleMonitor, LifecycleSpec};
use stateless::value_codec::{TraceDecode, TraceEncode};
use stateless::*;

struct Machine;
impl Model for Machine {
    type State = ();
    type Input = Vec<IndexedEvent<u8>>;
    type Output = IndexedEvent<u8>;
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<(), ModelError> {
        Ok(())
    }
    fn step(&self, _: &(), _: &Self::Input) -> Result<Transition<(), Self::Output>, ModelError> {
        Ok(Transition::accepted((), vec![]))
    }
    fn check_state(&self, _: &()) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
struct Spec;
impl IndexedLifecycleSpec<Machine> for Spec {
    type Key = u8;
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn input_events(
        &self,
        i: &Vec<IndexedEvent<u8>>,
        _: &Disposition,
    ) -> Result<Vec<IndexedEvent<u8>>, ModelError> {
        Ok(i.clone())
    }
    fn output_events(&self, o: &[IndexedEvent<u8>]) -> Result<Vec<IndexedEvent<u8>>, ModelError> {
        Ok(o.to_vec())
    }
}
impl LifecycleSpec<Machine> for Spec {
    type Key = ScopedKey<u8>;
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn input_events(
        &self,
        i: &Vec<IndexedEvent<u8>>,
        _: &Disposition,
    ) -> Result<Vec<Event<Self::Key>>, ModelError> {
        Ok(i.iter()
            .filter_map(|e| match e {
                IndexedEvent::Lifecycle(e) => Some(e.clone()),
                IndexedEvent::Fence { .. } => None,
            })
            .collect())
    }
    fn output_events(&self, o: &[IndexedEvent<u8>]) -> Result<Vec<Event<Self::Key>>, ModelError> {
        <Self as LifecycleSpec<Machine>>::input_events(self, &o.to_vec(), &Disposition::Accepted)
    }
}
fn key(epoch: u64, generation: u64) -> ScopedKey<u8> {
    ScopedKey {
        epoch,
        generation,
        key: 7,
    }
}
fn event(e: Event<ScopedKey<u8>>) -> IndexedEvent<u8> {
    IndexedEvent::Lifecycle(e)
}
fn monitor(max: usize) -> IndexedLifecycleMonitor<Spec> {
    IndexedLifecycleMonitor {
        spec: Spec,
        limits: RetentionLimits {
            obligations: max,
            resources: max,
            violations: 1000,
        },
    }
}
fn advance(h: &IndexedHistory<u8>, events: Vec<IndexedEvent<u8>>) -> IndexedHistory<u8> {
    monitor(1000)
        .advance(h, &events, &[], &Disposition::Accepted)
        .unwrap()
}

#[test]
fn baseline_violations_are_not_lost_in_short_exhaustive_histories() {
    let baseline = LifecycleMonitor {
        spec: Spec,
        max_history: 1000,
    };
    let indexed = monitor(1000);
    let k = key(0, 0);
    let alphabet = [
        Event::Begin {
            key: k.clone(),
            deadline: 1,
        },
        Event::Settle(k.clone()),
        Event::Cancel(k.clone()),
        Event::Publish(k.clone()),
        Event::Acquire(k.clone()),
        Event::Release(k),
        Event::Tick(1),
        Event::Tick(2),
    ]
    .map(event);
    let mut frontier = vec![(History::default(), IndexedHistory::default())];
    for _ in 0..4 {
        let mut next = vec![];
        for (full, compact) in frontier {
            for e in &alphabet {
                let a = baseline
                    .advance(&full, &vec![e.clone()], &[], &Disposition::Accepted)
                    .unwrap();
                let b = indexed
                    .advance(&compact, &vec![e.clone()], &[], &Disposition::Accepted)
                    .unwrap();
                assert_eq!(a.pending(), b.retention().active_obligations);
                assert!(
                    a.violations.is_empty() || !b.violations().is_empty(),
                    "lost baseline evidence: {a:?} / {b:?}"
                );
                for (k, deadline, cancelled, settled) in &a.obligations {
                    let record = &b.obligations()[k];
                    assert_eq!(
                        (*deadline, *cancelled, *settled),
                        (record.deadline, record.cancelled, record.settled)
                    );
                }
                next.push((a, b));
            }
        }
        frontier = next;
    }
}

#[test]
fn delivery_fences_bound_history_and_survive_midstream_restart() {
    let compact = monitor(1);
    let full = LifecycleMonitor {
        spec: Spec,
        max_history: 2000,
    };
    let mut a = full.initial_state().unwrap();
    let mut b = compact.initial_state().unwrap();
    for epoch in 0..1000 {
        let k = key(epoch, 0);
        let events = vec![
            event(Event::Acquire(k.clone())),
            event(Event::Begin {
                key: k.clone(),
                deadline: u64::MAX,
            }),
            event(Event::Settle(k.clone())),
            event(Event::Publish(k.clone())),
            event(Event::Release(k)),
            IndexedEvent::Fence { before: epoch + 1 },
        ];
        a = full
            .advance(&a, &events, &[], &Disposition::Accepted)
            .unwrap();
        b = compact
            .advance(&b, &events, &[], &Disposition::Accepted)
            .unwrap();
        assert!(a.violations.is_empty());
        assert!(b.violations().is_empty());
        assert!(b.obligations().is_empty());
        assert!(b.resources().is_empty());
        assert_eq!(b.retired_before(), epoch + 1);
        b = compact
            .decode_history(&compact.encode_history(&b).unwrap())
            .unwrap();
    }
    assert_eq!(a.obligations.len(), 1000);
    let late = compact
        .advance(
            &b,
            &vec![event(Event::Settle(key(0, 0)))],
            &[],
            &Disposition::Accepted,
        )
        .unwrap();
    assert_eq!(late.violations()[0].code, "retired_epoch_reference");
    assert_eq!(late.violations()[0].key, Some(key(0, 0)));
}

#[test]
fn fence_is_not_completion_cancellation_or_passage_of_time() {
    let k = key(2, 4);
    let active = advance(
        &IndexedHistory::default(),
        vec![
            event(Event::Begin {
                key: k.clone(),
                deadline: 100,
            }),
            event(Event::Acquire(k.clone())),
        ],
    );
    let refused = advance(&active, vec![IndexedEvent::Fence { before: 3 }]);
    assert_eq!(refused.retired_before(), 0);
    assert_eq!(refused.violations()[0].code, "fence_pending_obligation");
    let cancelled = advance(&active, vec![event(Event::Cancel(k.clone()))]);
    let refused = advance(&cancelled, vec![IndexedEvent::Fence { before: 3 }]);
    assert_eq!(refused.violations()[0].code, "fence_acquired_resource");
    assert_eq!(refused.retired_before(), 0);
    let finished = advance(
        &cancelled,
        vec![event(Event::Release(k)), IndexedEvent::Fence { before: 3 }],
    );
    assert!(finished.violations().is_empty());
    assert_eq!(finished.retired_before(), 3);
    let backwards = advance(&finished, vec![IndexedEvent::Fence { before: 2 }]);
    assert_eq!(backwards.violations()[0].code, "fence_backwards");
    let elapsed = advance(&active, vec![event(Event::Tick(1000))]);
    assert_eq!(elapsed.obligations().len(), 1);
    assert_eq!(elapsed.retired_before(), 0);
}

#[test]
fn generation_reuse_and_publication_duplicates_have_structured_evidence() {
    let a = key(1, 1);
    let b = key(1, 2);
    let h = advance(
        &IndexedHistory::default(),
        vec![
            event(Event::Begin {
                key: a.clone(),
                deadline: 10,
            }),
            event(Event::Settle(a.clone())),
            event(Event::Publish(a.clone())),
            event(Event::Begin {
                key: b.clone(),
                deadline: 11,
            }),
        ],
    );
    assert!(h.violations().is_empty());
    let duplicate = advance(&h, vec![event(Event::Publish(a.clone()))]);
    let evidence = &duplicate.violations()[0];
    assert_eq!(evidence.code, "duplicate_publication");
    assert_eq!(evidence.key, Some(a.clone()));
    assert_eq!(evidence.prior.as_ref().unwrap().deadline, 10);
    assert!(evidence.prior.as_ref().unwrap().published);
    let reuse = advance(
        &h,
        vec![event(Event::Begin {
            key: a,
            deadline: 12,
        })],
    );
    assert_eq!(reuse.violations()[0].code, "key_reused");
    let before_close = advance(
        &h,
        vec![event(Event::Settle(b)), IndexedEvent::Fence { before: 2 }],
    );
    assert!(before_close.violations().is_empty());
    let after_close = advance(&before_close, vec![event(Event::Settle(key(1, 2)))]);
    assert_eq!(after_close.violations()[0].code, "retired_epoch_reference");
}

#[test]
fn quotas_fail_explicitly_and_codecs_reject_invalid_fences_and_map_order() {
    let h = advance(
        &IndexedHistory::default(),
        vec![
            event(Event::Begin {
                key: key(0, 0),
                deadline: 10,
            }),
            event(Event::Settle(key(0, 0))),
        ],
    );
    let error = monitor(1)
        .advance(
            &h,
            &vec![event(Event::Begin {
                key: key(0, 1),
                deadline: 10,
            })],
            &[],
            &Disposition::Accepted,
        )
        .unwrap_err();
    assert!(error.0.contains("verification incomplete"));
    assert!(error.0.contains("historical=1"));
    let mut bytes = h.trace_bytes(4096).unwrap();
    bytes[9..17].copy_from_slice(&1u64.to_le_bytes());
    assert!(IndexedHistory::<u8>::from_trace(&bytes, Default::default()).is_err());
    let mut bytes = vec![];
    let mut out = EncodeBuffer::new(&mut bytes, 4096);
    1u8.trace_encode(&mut out).unwrap();
    0u64.trace_encode(&mut out).unwrap();
    0u64.trace_encode(&mut out).unwrap();
    2u32.trace_encode(&mut out).unwrap();
    let obligation = Obligation {
        deadline: 10,
        cancelled: false,
        settled: false,
        published: false,
    };
    for k in [key(0, 1), key(0, 0)] {
        k.trace_encode(&mut out).unwrap();
        obligation.trace_encode(&mut out).unwrap();
    }
    assert!(
        IndexedHistory::<u8>::from_trace(&bytes, Default::default())
            .unwrap_err()
            .0
            .contains("noncanonical")
    );
}
