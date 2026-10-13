//! Indexed lifecycle history with an explicit, model-declared epoch fence.
//!
//! A fence asserts that no future legitimate event can reference a lower epoch.
//! It is not inferred from time, cancellation, or apparent completion. Terminal
//! obligations and released resources below the fence can then be discarded;
//! the monotonic watermark rejects *all* subsequent references to those epochs.
//! The application must model why the fence is valid (for example a generation
//! barrier acknowledged by every sender). This monitor cannot prove that premise.
//! Legacy [`super::History`] and its codec are unchanged.
use std::collections::BTreeMap;

use super::Event;
use crate::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use crate::*;

/// All three fields participate in identity. Reuse requires a new generation or
/// epoch; advancing time alone never permits reuse.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ScopedKey<K> {
    pub epoch: u64,
    pub generation: u64,
    pub key: K,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum IndexedEvent<K> {
    Lifecycle(Event<ScopedKey<K>>),
    /// Assert a delivery fence and retire details strictly below `before`.
    /// Refused if it moves backwards or any affected obligation/resource is live.
    Fence {
        before: u64,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Obligation {
    pub deadline: u64,
    pub cancelled: bool,
    pub settled: bool,
    pub published: bool,
}
impl Obligation {
    fn pending(&self) -> bool {
        !self.cancelled && !self.settled
    }
}

/// Structured evidence is retained even when the corresponding epoch retires.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Violation<K> {
    pub key: Option<ScopedKey<K>>,
    pub code: String,
    pub event: String,
    pub observed_at: u64,
    pub prior: Option<Obligation>,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct IndexedHistory<K: Ord> {
    now: u64,
    retired_before: u64,
    obligations: BTreeMap<ScopedKey<K>, Obligation>,
    resources: BTreeMap<ScopedKey<K>, bool>,
    violations: Vec<Violation<K>>,
}
impl<K: Ord> Default for IndexedHistory<K> {
    fn default() -> Self {
        Self {
            now: 0,
            retired_before: 0,
            obligations: BTreeMap::new(),
            resources: BTreeMap::new(),
            violations: Vec::new(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RetentionLimits {
    pub obligations: usize,
    pub resources: usize,
    pub violations: usize,
}
impl Default for RetentionLimits {
    fn default() -> Self {
        Self {
            obligations: 1024,
            resources: 1024,
            violations: 1024,
        }
    }
}

/// Counts are diagnostic; no quota ever silently evicts an entry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RetentionStatus<K> {
    pub active_obligations: usize,
    pub historical_obligations: usize,
    pub acquired_resources: usize,
    pub historical_resources: usize,
    pub oldest_unresolved: Option<(ScopedKey<K>, u64)>,
    pub retired_before: u64,
}

pub trait IndexedLifecycleSpec<M: Model> {
    type Key: Clone + Ord + std::fmt::Debug;
    fn metadata(&self) -> ModelMetadata;
    fn input_events(
        &self,
        input: &M::Input,
        disposition: &Disposition,
    ) -> Result<Vec<IndexedEvent<Self::Key>>, ModelError>;
    fn output_events(
        &self,
        outputs: &[M::Output],
    ) -> Result<Vec<IndexedEvent<Self::Key>>, ModelError>;
}

pub struct IndexedLifecycleMonitor<S> {
    pub spec: S,
    pub limits: RetentionLimits,
}

impl<K: Clone + Ord> IndexedHistory<K> {
    pub fn now(&self) -> u64 {
        self.now
    }
    pub fn retired_before(&self) -> u64 {
        self.retired_before
    }
    pub fn obligations(&self) -> &BTreeMap<ScopedKey<K>, Obligation> {
        &self.obligations
    }
    pub fn resources(&self) -> &BTreeMap<ScopedKey<K>, bool> {
        &self.resources
    }
    pub fn violations(&self) -> &[Violation<K>] {
        &self.violations
    }
    pub fn retention(&self) -> RetentionStatus<K> {
        let active_obligations = self.obligations.values().filter(|v| v.pending()).count();
        let acquired_resources = self.resources.values().filter(|v| **v).count();
        RetentionStatus {
            active_obligations,
            historical_obligations: self.obligations.len() - active_obligations,
            acquired_resources,
            historical_resources: self.resources.len() - acquired_resources,
            oldest_unresolved: self
                .obligations
                .iter()
                .filter(|(_, v)| v.pending())
                .min_by_key(|(_, v)| v.deadline)
                .map(|(k, v)| (k.clone(), v.deadline)),
            retired_before: self.retired_before,
        }
    }
    fn validate(&self, limits: RetentionLimits) -> Result<(), ModelError> {
        if self.obligations.len() > limits.obligations
            || self.resources.len() > limits.resources
            || self.violations.len() > limits.violations
        {
            return Err(ModelError::new(
                "indexed lifecycle retention limit; verification incomplete",
            ));
        }
        if self
            .obligations
            .keys()
            .chain(self.resources.keys())
            .any(|k| k.epoch < self.retired_before)
        {
            return Err(ModelError::new(
                "indexed lifecycle detail below retirement fence",
            ));
        }
        if self.obligations.values().any(|v| v.published && !v.settled) {
            return Err(ModelError::new(
                "indexed lifecycle invalid publication history",
            ));
        }
        Ok(())
    }
    fn violation(
        &mut self,
        key: Option<ScopedKey<K>>,
        code: &str,
        event: &str,
        limits: RetentionLimits,
    ) -> Result<(), ModelError> {
        if self.violations.len() >= limits.violations {
            return Err(ModelError::new(
                "indexed lifecycle violation limit; verification incomplete",
            ));
        }
        let prior = key.as_ref().and_then(|k| self.obligations.get(k)).cloned();
        self.violations.push(Violation {
            key,
            code: code.into(),
            event: event.into(),
            observed_at: self.now,
            prior,
        });
        Ok(())
    }
    fn overdue(&self, strict: bool, now: u64) -> Option<ScopedKey<K>> {
        self.obligations
            .iter()
            .find(|(_, v)| {
                v.pending()
                    && if strict {
                        v.deadline < now
                    } else {
                        v.deadline <= now
                    }
            })
            .map(|(k, _)| k.clone())
    }
    fn quota_error(&self, kind: &str) -> ModelError {
        let r = self.retention();
        ModelError::new(format!(
            "indexed lifecycle {kind} limit; active={}, historical={}, acquired={}; explicit delivery fence required for retirement; verification incomplete",
            r.active_obligations, r.historical_obligations, r.acquired_resources,
        ))
    }
    fn apply(&mut self, event: IndexedEvent<K>, limits: RetentionLimits) -> Result<(), ModelError> {
        let event = match event {
            IndexedEvent::Fence { before } => {
                if before < self.retired_before {
                    return self.violation(None, "fence_backwards", "fence", limits);
                }
                if let Some(key) = self
                    .obligations
                    .iter()
                    .find(|(k, v)| k.epoch < before && v.pending())
                    .map(|(k, _)| k.clone())
                {
                    return self.violation(Some(key), "fence_pending_obligation", "fence", limits);
                }
                if let Some(key) = self
                    .resources
                    .iter()
                    .find(|(k, v)| k.epoch < before && **v)
                    .map(|(k, _)| k.clone())
                {
                    return self.violation(Some(key), "fence_acquired_resource", "fence", limits);
                }
                self.obligations.retain(|k, _| k.epoch >= before);
                self.resources.retain(|k, _| k.epoch >= before);
                self.retired_before = before;
                return Ok(());
            }
            IndexedEvent::Lifecycle(event) => event,
        };
        let (key, kind) = match &event {
            Event::Begin { key, .. } => (Some(key), "begin"),
            Event::Cancel(key) => (Some(key), "cancel"),
            Event::Settle(key) => (Some(key), "settle"),
            Event::Publish(key) => (Some(key), "publish"),
            Event::Acquire(key) => (Some(key), "acquire"),
            Event::Release(key) => (Some(key), "release"),
            Event::Tick(_) => (None, "tick"),
        };
        if let Some(key) = key.filter(|k| k.epoch < self.retired_before) {
            return self.violation(Some(key.clone()), "retired_epoch_reference", kind, limits);
        }
        match event {
            Event::Tick(now) => {
                if now < self.now {
                    self.violation(None, "time_backwards", kind, limits)?;
                } else {
                    if let Some(key) = self.overdue(true, now) {
                        self.violation(Some(key), "missing_before_advance", kind, limits)?;
                    }
                    self.now = now;
                }
            }
            Event::Begin { key, deadline } => {
                if self.obligations.contains_key(&key) {
                    self.violation(Some(key), "key_reused", kind, limits)?;
                } else {
                    if self.obligations.len() >= limits.obligations {
                        return Err(self.quota_error("obligation"));
                    }
                    self.obligations.insert(
                        key.clone(),
                        Obligation {
                            deadline,
                            cancelled: false,
                            settled: false,
                            published: false,
                        },
                    );
                    if deadline < self.now {
                        self.violation(Some(key), "began_after_deadline", kind, limits)?;
                    }
                }
            }
            Event::Cancel(key) => match self.obligations.get_mut(&key) {
                Some(v) => v.cancelled = true,
                None => self.violation(Some(key), "cancel_without_obligation", kind, limits)?,
            },
            Event::Settle(key) => {
                let error = match self.obligations.get(&key) {
                    Some(v) if v.cancelled => Some("settlement_after_cancel"),
                    Some(v) if v.settled => Some("duplicate_settlement"),
                    Some(v) if v.deadline < self.now => Some("settlement_after_deadline"),
                    Some(_) => None,
                    None => Some("settlement_without_obligation"),
                };
                if let Some(code) = error {
                    self.violation(Some(key.clone()), code, kind, limits)?;
                }
                if let Some(v) = self.obligations.get_mut(&key)
                    && !v.cancelled
                {
                    v.settled = true;
                }
            }
            Event::Publish(key) => {
                let code = match self.obligations.get(&key) {
                    Some(v) if v.cancelled || !v.settled => Some("stale_or_premature_publication"),
                    Some(v) if v.published => Some("duplicate_publication"),
                    Some(_) => None,
                    None => Some("stale_or_premature_publication"),
                };
                if let Some(code) = code {
                    self.violation(Some(key), code, kind, limits)?;
                } else if let Some(v) = self.obligations.get_mut(&key) {
                    v.published = true;
                }
            }
            Event::Acquire(key) => match self.resources.get_mut(&key) {
                Some(true) => {
                    self.violation(Some(key), "resource_already_acquired", kind, limits)?
                }
                Some(v) => *v = true,
                None => {
                    if self.resources.len() >= limits.resources {
                        return Err(self.quota_error("resource"));
                    }
                    self.resources.insert(key, true);
                }
            },
            Event::Release(key) => {
                if self.obligations.get(&key).is_some_and(Obligation::pending) {
                    self.violation(Some(key.clone()), "release_before_settlement", kind, limits)?;
                }
                match self.resources.get_mut(&key) {
                    Some(false) => self.violation(Some(key), "duplicate_release", kind, limits)?,
                    Some(v) => *v = false,
                    None => {
                        self.violation(Some(key), "release_without_acquisition", kind, limits)?
                    }
                }
            }
        }
        Ok(())
    }
}

impl<M: Model, S: IndexedLifecycleSpec<M>> Oracle<M> for IndexedLifecycleMonitor<S> {
    type State = IndexedHistory<S::Key>;
    fn metadata(&self) -> ModelMetadata {
        let mut m = self.spec.metadata();
        m.name = format!(
            "indexed-lifecycle-v1:{}:{};limits={},{},{}",
            m.name.len(),
            m.name,
            self.limits.obligations,
            self.limits.resources,
            self.limits.violations
        );
        m.build.push_str(concat!(
            ";indexed-lifecycle-v1:",
            env!("STATELESS_BUILD_ID")
        ));
        m
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(IndexedHistory::default())
    }
    fn advance(
        &self,
        before: &Self::State,
        input: &M::Input,
        outputs: &[M::Output],
        disposition: &Disposition,
    ) -> Result<Self::State, ModelError> {
        before.validate(self.limits)?;
        let mut next = before.clone();
        if let Some(key) = before.overdue(false, before.now) {
            next.violation(
                Some(key),
                "missing_in_prior_history",
                "observation",
                self.limits,
            )?;
        }
        for event in self
            .spec
            .input_events(input, disposition)?
            .into_iter()
            .chain(self.spec.output_events(outputs)?)
        {
            next.apply(event, self.limits)?;
        }
        if let Some(key) = next.overdue(false, next.now) {
            next.violation(Some(key), "missing_at_deadline", "observation", self.limits)?;
        }
        Ok(next)
    }
    fn check_state_into(
        &self,
        h: &Self::State,
        _: &M::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        h.validate(self.limits)?;
        checks.push(
            if h.violations.is_empty() && h.overdue(false, h.now).is_none() {
                Check::passed("lifecycle.indexed.safety")
            } else {
                Check::failed(
                    "lifecycle.indexed.safety",
                    format!(
                        "violations={:?}; retention={:?}",
                        h.violations,
                        h.retention()
                    ),
                )
            },
        );
        let retention = h.retention();
        checks.push(if retention.active_obligations == 0 {
            Check::passed("lifecycle.indexed.obligations")
        } else {
            Check::skipped(
                "lifecycle.indexed.obligations",
                format!("finite history: {retention:?}"),
            )
        });
        Ok(())
    }
}

impl<M: Model, S: IndexedLifecycleSpec<M>> OracleCodec<M> for IndexedLifecycleMonitor<S>
where
    S::Key: TraceEncode + TraceDecode,
{
    fn encode_history(&self, h: &Self::State) -> Result<Vec<u8>, ModelError> {
        h.validate(self.limits)?;
        h.trace_bytes(usize::MAX)
    }
    fn encode_history_into(
        &self,
        h: &Self::State,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        h.validate(self.limits)?;
        h.trace_encode(o)
    }
    fn decode_history(&self, b: &[u8]) -> Result<Self::State, ModelError> {
        let h = Self::State::from_trace(b, DecodeLimits::default())?;
        h.validate(self.limits)?;
        Ok(h)
    }
}

impl<K: TraceEncode> TraceEncode for ScopedKey<K> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.epoch.trace_encode(o)?;
        self.generation.trace_encode(o)?;
        self.key.trace_encode(o)
    }
}
impl<K: TraceDecode> TraceDecode for ScopedKey<K> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            epoch: r.value()?,
            generation: r.value()?,
            key: r.value()?,
        })
    }
}
impl TraceEncode for Obligation {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.deadline.trace_encode(o)?;
        self.cancelled.trace_encode(o)?;
        self.settled.trace_encode(o)?;
        self.published.trace_encode(o)
    }
}
impl TraceDecode for Obligation {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            deadline: r.value()?,
            cancelled: r.value()?,
            settled: r.value()?,
            published: r.value()?,
        })
    }
}
impl<K: TraceEncode> TraceEncode for Violation<K> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.key.trace_encode(o)?;
        self.code.trace_encode(o)?;
        self.event.trace_encode(o)?;
        self.observed_at.trace_encode(o)?;
        self.prior.trace_encode(o)
    }
}
impl<K: TraceDecode> TraceDecode for Violation<K> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            key: r.value()?,
            code: r.value()?,
            event: r.value()?,
            observed_at: r.value()?,
            prior: r.value()?,
        })
    }
}
fn encode_map<K: TraceEncode, V: TraceEncode>(
    map: &BTreeMap<K, V>,
    o: &mut EncodeBuffer<'_>,
) -> Result<(), ModelError> {
    u32::try_from(map.len())
        .map_err(|_| ModelError::new("indexed map length overflow"))?
        .trace_encode(o)?;
    for (k, v) in map {
        k.trace_encode(o)?;
        v.trace_encode(o)?;
    }
    Ok(())
}
fn decode_map<K: TraceDecode + Ord, V: TraceDecode>(
    r: &mut Decoder<'_>,
) -> Result<BTreeMap<K, V>, ModelError> {
    let n = r.value::<u32>()? as usize;
    // Include a conservative node overhead in the decoder's requested-allocation budget.
    r.collection(n, std::mem::size_of::<(K, V)>().saturating_add(64))?;
    let mut map = BTreeMap::new();
    for _ in 0..n {
        let key = r.value::<K>()?;
        if map.last_key_value().is_some_and(|(prior, _)| prior >= &key) {
            return Err(ModelError::new("noncanonical indexed map order"));
        }
        map.insert(key, r.value()?);
    }
    Ok(map)
}
impl<K: TraceEncode + Ord> TraceEncode for IndexedHistory<K> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        1u8.trace_encode(o)?;
        self.now.trace_encode(o)?;
        self.retired_before.trace_encode(o)?;
        encode_map(&self.obligations, o)?;
        encode_map(&self.resources, o)?;
        self.violations.trace_encode(o)
    }
}
impl<K: TraceDecode + Clone + Ord> TraceDecode for IndexedHistory<K> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        if r.value::<u8>()? != 1 {
            return Err(ModelError::new("indexed lifecycle codec version"));
        }
        let h = Self {
            now: r.value()?,
            retired_before: r.value()?,
            obligations: decode_map(r)?,
            resources: decode_map(r)?,
            violations: r.value()?,
        };
        h.validate(RetentionLimits {
            obligations: usize::MAX,
            resources: usize::MAX,
            violations: usize::MAX,
        })?;
        Ok(h)
    }
}
