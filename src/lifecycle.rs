//! Independent lifecycle monitor templates. Classifiers specify obligations from
//! delivered inputs and observed outputs, never from the application's next state.
//! Keys must include generation when reuse denotes a new logical operation.
use crate::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use crate::*;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event<K> {
    /// One settlement is required no later than the explicit logical deadline.
    Begin {
        key: K,
        deadline: u64,
    },
    Cancel(K),
    Settle(K),
    Publish(K),
    Acquire(K),
    Release(K),
    /// Logical time is supplied by the environment. It may not move backwards.
    Tick(u64),
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct History<K> {
    pub now: u64,
    /// key, deadline, cancelled, settled. Completed keys remain retained.
    pub obligations: Vec<(K, u64, bool, bool)>,
    pub resources: Vec<(K, bool)>,
    pub violations: Vec<String>,
}
impl<K> Default for History<K> {
    fn default() -> Self {
        Self {
            now: 0,
            obligations: Vec::new(),
            resources: Vec::new(),
            violations: Vec::new(),
        }
    }
}
impl<K: TraceEncode> TraceEncode for History<K> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.now.trace_encode(o)?;
        self.obligations.trace_encode(o)?;
        self.resources.trace_encode(o)?;
        self.violations.trace_encode(o)
    }
}
impl<K: TraceDecode> TraceDecode for History<K> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            now: r.value()?,
            obligations: r.value()?,
            resources: r.value()?,
            violations: r.value()?,
        })
    }
}

/// Disposition policies are explicit in the classifier, including rejected and
/// ignored inputs. Returned events are processed in order, inputs before outputs.
pub trait LifecycleSpec<M: Model> {
    type Key: Clone + Eq;
    fn metadata(&self) -> ModelMetadata;
    fn input_events(
        &self,
        input: &M::Input,
        disposition: &Disposition,
    ) -> Result<Vec<Event<Self::Key>>, ModelError>;
    fn output_events(&self, outputs: &[M::Output]) -> Result<Vec<Event<Self::Key>>, ModelError>;
}
pub struct LifecycleMonitor<S> {
    pub spec: S,
    pub max_history: usize,
}
impl<K: Clone + Eq> History<K> {
    fn violation(&mut self, message: &str, max: usize) -> Result<(), ModelError> {
        if self.violations.len() >= max {
            return Err(ModelError::new(
                "lifecycle violation history limit; verification incomplete",
            ));
        }
        self.violations.push(message.into());
        Ok(())
    }
    fn apply(&mut self, event: Event<K>, max: usize) -> Result<(), ModelError> {
        match event {
            Event::Tick(t) => {
                if t < self.now {
                    self.violation("logical time moved backwards", max)?;
                } else {
                    // Crossing a deadline is irreversible, even if another event
                    // in this observation subsequently cancels the obligation.
                    if self.obligations.iter().any(|v| !v.2 && !v.3 && v.1 < t) {
                        self.violation("required settlement missing before time advance", max)?;
                    }
                    self.now = t;
                }
            }
            Event::Begin { key, deadline } => {
                if self.obligations.iter().any(|(k, _, _, _)| k == &key) {
                    self.violation("correlation key reused", max)?;
                } else {
                    if self.obligations.len() >= max {
                        return Err(ModelError::new(
                            "lifecycle obligation history limit; verification incomplete",
                        ));
                    }
                    self.obligations.push((key, deadline, false, false));
                    if deadline < self.now {
                        self.violation("obligation began after its deadline", max)?;
                    }
                }
            }
            Event::Cancel(key) => {
                if let Some(v) = self.obligations.iter_mut().find(|v| v.0 == key) {
                    v.2 = true;
                } else {
                    self.violation("cancellation without obligation", max)?;
                }
            }
            Event::Settle(key) => {
                if let Some(v) = self.obligations.iter_mut().find(|v| v.0 == key) {
                    if v.2 {
                        self.violation("settlement after cancellation", max)?;
                    } else if v.3 {
                        self.violation("duplicate settlement", max)?;
                    } else {
                        let late = v.1 < self.now;
                        v.3 = true;
                        if late {
                            self.violation("settlement after deadline", max)?;
                        }
                    }
                } else {
                    self.violation("settlement without obligation", max)?;
                }
            }
            Event::Publish(key) => {
                if !self.obligations.iter().any(|v| v.0 == key && !v.2 && v.3) {
                    self.violation("stale or premature publication", max)?;
                }
            }
            Event::Acquire(key) => {
                if let Some(v) = self.resources.iter_mut().find(|v| v.0 == key) {
                    if v.1 {
                        self.violation("resource already acquired", max)?;
                    } else {
                        v.1 = true;
                    }
                } else {
                    if self.resources.len() >= max {
                        return Err(ModelError::new(
                            "lifecycle resource history limit; verification incomplete",
                        ));
                    }
                    self.resources.push((key, true));
                }
            }
            Event::Release(key) => {
                if self.obligations.iter().any(|v| v.0 == key && !v.2 && !v.3) {
                    self.violation("resource released before settlement", max)?;
                }
                if let Some(v) = self.resources.iter_mut().find(|v| v.0 == key) {
                    if !v.1 {
                        self.violation("duplicate resource release", max)?;
                    } else {
                        v.1 = false;
                    }
                } else {
                    self.violation("release without acquisition", max)?;
                }
            }
        }
        Ok(())
    }
    /// Incomplete finite histories remain explicit; no unbounded eventuality claim.
    pub fn pending(&self) -> usize {
        self.obligations.iter().filter(|v| !v.2 && !v.3).count()
    }
}
impl<M: Model, S: LifecycleSpec<M>> Oracle<M> for LifecycleMonitor<S> {
    type State = History<S::Key>;
    fn metadata(&self) -> ModelMetadata {
        let mut m = self.spec.metadata();
        m.build
            .push_str(concat!(";lifecycle-v1:", env!("STATELESS_BUILD_ID")));
        m.name = format!("{};history-limit={}", m.name, self.max_history);
        m
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(History::default())
    }
    fn advance(
        &self,
        before: &Self::State,
        input: &M::Input,
        outputs: &[M::Output],
        disposition: &Disposition,
    ) -> Result<Self::State, ModelError> {
        if before.obligations.len() > self.max_history
            || before.resources.len() > self.max_history
            || before.violations.len() > self.max_history
        {
            return Err(ModelError::new("attached history exceeds lifecycle limit"));
        }
        let mut next = before.clone();
        if before
            .obligations
            .iter()
            .any(|v| !v.2 && !v.3 && v.1 <= before.now)
        {
            next.violation(
                "required settlement missing in prior history",
                self.max_history,
            )?;
        }
        for event in self
            .spec
            .input_events(input, disposition)?
            .into_iter()
            .chain(self.spec.output_events(outputs)?)
        {
            next.apply(event, self.max_history)?;
        }
        // A deadline is inclusive: required outputs must appear in this transition.
        if next
            .obligations
            .iter()
            .any(|v| !v.2 && !v.3 && v.1 <= next.now)
        {
            next.violation("required settlement missing at deadline", self.max_history)?;
        }
        Ok(next)
    }
    fn check_state_into(
        &self,
        h: &Self::State,
        _: &M::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        if h.obligations.len() > self.max_history
            || h.resources.len() > self.max_history
            || h.violations.len() > self.max_history
        {
            return Err(ModelError::new("attached history exceeds lifecycle limit"));
        }
        let overdue = h.obligations.iter().any(|v| !v.2 && !v.3 && v.1 <= h.now);
        checks.push(if h.violations.is_empty() && !overdue {
            Check::passed("lifecycle.safety")
        } else {
            Check::failed(
                "lifecycle.safety",
                if h.violations.is_empty() {
                    "required settlement missing at deadline".into()
                } else {
                    h.violations.join("; ")
                },
            )
        });
        checks.push(if h.pending() == 0 {
            Check::passed("lifecycle.obligations")
        } else {
            Check::skipped(
                "lifecycle.obligations",
                format!("{} pending obligations in finite history", h.pending()),
            )
        });
        Ok(())
    }
}
impl<M: Model, S: LifecycleSpec<M>> OracleCodec<M> for LifecycleMonitor<S>
where
    S::Key: TraceEncode + TraceDecode,
{
    fn encode_history(&self, h: &Self::State) -> Result<Vec<u8>, ModelError> {
        h.trace_bytes(usize::MAX)
    }
    fn encode_history_into(
        &self,
        h: &Self::State,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        h.trace_encode(o)
    }
    fn decode_history(&self, b: &[u8]) -> Result<Self::State, ModelError> {
        let h = History::from_trace(b, DecodeLimits::default())?;
        if h.obligations.len() > self.max_history
            || h.resources.len() > self.max_history
            || h.violations.len() > self.max_history
        {
            return Err(ModelError::new(
                "decoded lifecycle history exceeds retention limit",
            ));
        }
        Ok(h)
    }
}
