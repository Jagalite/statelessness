//! Background-worker host: one reducer owner and one ordered effect executor.
//! Worker handles stay here; reducers persist only generation/attempt identities.
use super::*;
use crate::monitor::Recorder;
use crate::trace::RunConfig;
use std::collections::BTreeMap;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::thread::{self, JoinHandle};
use std::time::Duration;

type Work = dyn Fn(u64, u8) -> Outcome + Send + Sync + 'static;
struct Worker {
    cancelled: Arc<AtomicBool>,
    join: JoinHandle<()>,
}

pub struct Host {
    model: Controller,
    state: State,
    sender: mpsc::Sender<Input>,
    receiver: mpsc::Receiver<Input>,
    workers: BTreeMap<(u64, u8), Worker>,
    work: Arc<Work>,
    recorder: Option<Recorder>,
    pub published: Vec<u64>,
    pub settled: BTreeMap<u64, Settlement>,
    pub diagnostics: Vec<String>,
    pub executed_effects: usize,
}
impl Host {
    /// Real work can block on file/command I/O in the worker. It must eventually
    /// return; cooperative cancellation is acknowledged at that boundary. The
    /// example does not pretend to interrupt arbitrary blocking system calls.
    pub fn new(
        bounds: Bounds,
        work: impl Fn(u64, u8) -> Outcome + Send + Sync + 'static,
        record: bool,
    ) -> Result<Self, ModelError> {
        let model = Controller {
            bounds,
            mutation: None,
        };
        let state = model.initial_state()?;
        let recorder = if record {
            Some(Recorder::new(
                &model,
                &state,
                RunConfig {
                    strategy: "reference-live-host".into(),
                    ..RunConfig::default()
                },
                1024,
            )?)
        } else {
            None
        };
        let (sender, receiver) = mpsc::channel();
        Ok(Self {
            model,
            state,
            sender,
            receiver,
            workers: BTreeMap::new(),
            work: Arc::new(work),
            recorder,
            published: Vec::new(),
            settled: BTreeMap::new(),
            diagnostics: Vec::new(),
            executed_effects: 0,
        })
    }
    pub fn state(&self) -> &State {
        &self.state
    }
    pub fn recording(&self) -> Option<crate::trace::Trace> {
        self.recorder.as_ref().map(Recorder::snapshot)
    }
    /// Apply each delivered event once, then execute the returned effects once
    /// in order. Logging/recording failures never rerun or suppress these effects.
    /// Durable exactly-once execution across crashes requires an outbox protocol.
    pub fn deliver(&mut self, input: Input) -> Result<Vec<Effect>, ModelError> {
        let transition = reduce(&self.state, &input, self.model.bounds)?;
        if let Some(recorder) = &mut self.recorder {
            match recorder.observe(&self.model, &self.state, &input, &transition) {
                Ok(checks) => {
                    for check in checks.iter().filter(|c| c.is_failure()) {
                        self.diagnostics
                            .push(format!("property failure: {}", check.id));
                    }
                }
                Err(error) => self
                    .diagnostics
                    .push(format!("recording unavailable: {error}")),
            }
        }
        self.state = transition.state;
        for effect in &transition.outputs {
            self.execute(effect)?;
        }
        Ok(transition.outputs)
    }
    fn execute(&mut self, effect: &Effect) -> Result<(), ModelError> {
        self.executed_effects += 1;
        match *effect {
            Effect::Start {
                generation,
                attempt,
            } => {
                if self.workers.contains_key(&(generation, attempt)) {
                    return Err(ModelError::new("duplicate effect dispatch"));
                }
                let cancelled = Arc::new(AtomicBool::new(false));
                let worker_cancelled = Arc::clone(&cancelled);
                let sender = self.sender.clone();
                let work = Arc::clone(&self.work);
                let spawned = thread::Builder::new()
                    .name(format!("request-{generation}-{attempt}"))
                    .spawn(move || {
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            work(generation, attempt)
                        }))
                        .unwrap_or(Outcome::RetryableFailure);
                        let event = if worker_cancelled.load(Ordering::Acquire) {
                            Input::Cancelled {
                                generation,
                                attempt,
                            }
                        } else {
                            Input::Complete {
                                generation,
                                attempt,
                                outcome: result,
                            }
                        };
                        // A closed host has ceased accepting events; no foreign state
                        // can be mutated by this late delivery.
                        let _ = sender.send(event);
                    });
                match spawned {
                    Ok(join) => {
                        self.workers
                            .insert((generation, attempt), Worker { cancelled, join });
                    }
                    Err(error) => {
                        self.diagnostics
                            .push(format!("worker spawn failed: {error}"));
                        self.sender
                            .send(Input::Complete {
                                generation,
                                attempt,
                                outcome: Outcome::RetryableFailure,
                            })
                            .map_err(|e| ModelError::new(e.to_string()))?;
                    }
                }
            }
            Effect::Cancel {
                generation,
                attempt,
            } => {
                if let Some(worker) = self.workers.get(&(generation, attempt)) {
                    worker.cancelled.store(true, Ordering::Release);
                }
            }
            Effect::Publish { generation } => self.published.push(generation),
            Effect::Settle { generation, result } => {
                if self.settled.insert(generation, result).is_some() {
                    return Err(ModelError::new("host saw duplicate settlement"));
                }
            }
            Effect::Release { generation } => {
                if !self.settled.contains_key(&generation) {
                    return Err(ModelError::new("host saw release before settlement"));
                }
                let keys = self
                    .workers
                    .keys()
                    .copied()
                    .filter(|(g, _)| *g == generation)
                    .collect::<Vec<_>>();
                for key in keys {
                    if let Some(worker) = self.workers.remove(&key)
                        && worker.join.join().is_err()
                    {
                        self.diagnostics
                            .push("worker panicked outside its work callback".into());
                    }
                }
            }
        }
        Ok(())
    }
    /// Deliver one actual worker event. Timeouts leave pending state intact.
    pub fn receive(&mut self, timeout: Duration) -> Result<bool, ModelError> {
        match self.receiver.recv_timeout(timeout) {
            Ok(input) => {
                self.deliver(input)?;
                Ok(true)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => Ok(false),
            Err(error) => Err(ModelError::new(error.to_string())),
        }
    }
    /// A caller supplies the logical time. UI timers, Tokio timers and actor
    /// messages all enter through this same input boundary.
    pub fn tick(&mut self, now: u64) -> Result<(), ModelError> {
        self.deliver(Input::Tick { now })?;
        Ok(())
    }
    pub fn pending_workers(&self) -> usize {
        self.workers.len()
    }
}
impl Drop for Host {
    fn drop(&mut self) {
        // Destruction cannot fabricate cancellation acknowledgments or claim all
        // resources settled. Request cancellation and detach pending workers.
        for worker in self.workers.values() {
            worker.cancelled.store(true, Ordering::Release);
        }
    }
}
