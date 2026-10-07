//! Feedback-guided sequence fuzzing with bounded feature and corpus counts.
//! Features guide input selection only; full model/oracle checks still run on
//! every transition. Feature equality never merges application states for replay.
use crate::explore::{self, FuzzConfig, FuzzDriver, FuzzReport, RunLimits};
use crate::model::{Generate, Model, ModelError, Rng, Transition, TransitionRef};
use std::collections::{HashMap, VecDeque, hash_map::Entry};
use std::hash::Hash;

/// Identifies the corpus admission and mutation policy in audit evidence.
pub const ALGORITHM: &str = "feature-corpus-v2";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FeedbackMetadata {
    pub name: String,
    pub version: u32,
    pub build: String,
}

/// A callback's reusable output. Overrun errors are sticky even if ignored.
pub struct FeatureBuffer<'a, K> {
    values: &'a mut Vec<K>,
    maximum: usize,
    failed: bool,
}
impl<K> FeatureBuffer<'_, K> {
    pub fn push(&mut self, feature: K) -> Result<(), ModelError> {
        if self.failed || self.values.len() >= self.maximum {
            self.failed = true;
            return Err(ModelError::new(
                "feedback exceeds per-observation feature limit",
            ));
        }
        self.values.push(feature);
        Ok(())
    }
}
/// Supply compact, deterministic feature keys in a stable emission order.
/// New keys can represent states, transitions, effects, or application branches.
/// Hash collisions are resolved using Eq. Callback and key allocations are owned
/// by the application; the engine's limits bound counts, not process memory.
pub trait Feedback<M: Model> {
    type Feature: Eq + Hash;
    fn metadata(&self) -> FeedbackMetadata;
    fn initial_features(
        &self,
        _state: &M::State,
        _out: &mut FeatureBuffer<'_, Self::Feature>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
    fn transition_features(
        &self,
        before: &M::State,
        input: &M::Input,
        after: &TransitionRef<'_, M::State, M::Output>,
        out: &mut FeatureBuffer<'_, Self::Feature>,
    ) -> Result<(), ModelError>;
}
/// Project application state into a feature key. A compact projection can omit
/// sequence counters while checking and persisted state still retain everything.
pub struct StateFeedback<P> {
    metadata: FeedbackMetadata,
    project: P,
}
impl<P> StateFeedback<P> {
    pub fn new(metadata: FeedbackMetadata, project: P) -> Self {
        Self { metadata, project }
    }
}
impl<M: Model, K: Eq + Hash, P: Fn(&M::State) -> K> Feedback<M> for StateFeedback<P> {
    type Feature = K;
    fn metadata(&self) -> FeedbackMetadata {
        self.metadata.clone()
    }
    fn initial_features(
        &self,
        state: &M::State,
        out: &mut FeatureBuffer<'_, K>,
    ) -> Result<(), ModelError> {
        out.push((self.project)(state))
    }
    fn transition_features(
        &self,
        _: &M::State,
        _: &M::Input,
        after: &TransitionRef<'_, M::State, M::Output>,
        out: &mut FeatureBuffer<'_, K>,
    ) -> Result<(), ModelError> {
        out.push((self.project)(after.state))
    }
}
/// Exact state novelty; stores full cloned states, including oracle history.
/// Prefer a compact StateFeedback projection for large or monotonic state.
pub fn exact_state<M: Model>() -> impl Feedback<M, Feature = M::State>
where
    M::State: Hash,
{
    StateFeedback::new(
        FeedbackMetadata {
            name: "exact-state".into(),
            version: 1,
            build: "clone-eq-hash-v1".into(),
        },
        M::State::clone,
    )
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct GuidanceConfig {
    pub max_corpus_entries: usize,
    /// Total input values retained across all corpus sequences.
    pub max_corpus_inputs: usize,
    pub max_features: usize,
    pub max_features_per_observation: usize,
}
impl Default for GuidanceConfig {
    fn default() -> Self {
        Self {
            max_corpus_entries: 256,
            max_corpus_inputs: 25_600,
            max_features: 100_000,
            max_features_per_observation: 256,
        }
    }
}
impl GuidanceConfig {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.max_corpus_entries == 0
            || self.max_corpus_inputs == 0
            || self.max_features == 0
            || self.max_features_per_observation == 0
        {
            Err(ModelError::new("guidance limits must be positive"))
        } else {
            Ok(())
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CorpusEntry<I> {
    pub inputs: Vec<I>,
    /// Newly tracked keys; zero when recovering previously dropped discoveries.
    pub new_features: usize,
    pub discovered_case: usize,
    pub discovered_transition: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GuidanceReport<I> {
    pub algorithm: &'static str,
    pub config: GuidanceConfig,
    pub feedback: FeedbackMetadata,
    pub unique_features: usize,
    pub corpus_inputs: usize,
    pub corpus: Vec<CorpusEntry<I>>,
    pub mutation_cases: usize,
    /// Novel prefixes too long to fit the total retained-input limit.
    pub dropped_prefixes: u64,
    /// Oldest prefixes replaced to admit new discoveries within corpus limits.
    pub evicted_prefixes: u64,
    pub feature_limit_reached: bool,
    pub corpus_limit_reached: bool,
}

pub fn fuzz<M: Generate, F: Feedback<M>>(
    model: &M,
    config: FuzzConfig,
    guidance: GuidanceConfig,
    feedback: F,
) -> Result<FuzzReport<M::Input>, ModelError> {
    fuzz_with_limits(model, config, guidance, feedback, RunLimits::default())
}
pub fn fuzz_with_limits<M: Generate, F: Feedback<M>>(
    model: &M,
    config: FuzzConfig,
    guidance: GuidanceConfig,
    feedback: F,
    limits: RunLimits<'_>,
) -> Result<FuzzReport<M::Input>, ModelError> {
    guidance.validate()?;
    if config.mutation_percent > 100 {
        return Err(ModelError::new("mutation_percent must be in 0..=100"));
    }
    let control = explore::Control::new(limits)?;
    let mut driver = Driver {
        report: GuidanceReport {
            algorithm: ALGORITHM,
            config: guidance,
            feedback: feedback.metadata(),
            unique_features: 0,
            corpus_inputs: 0,
            corpus: Vec::new(),
            mutation_cases: 0,
            dropped_prefixes: 0,
            evicted_prefixes: 0,
            feature_limit_reached: false,
            corpus_limit_reached: false,
        },
        feedback,
        seen: HashMap::new(),
        scratch: Vec::new(),
        corpus: VecDeque::new(),
    };
    let mut report = explore::fuzz_with_driver(model, config, control.remaining(), &mut driver)?;
    driver.report.corpus = driver.corpus.into();
    report.guidance = Some(Box::new(driver.report));
    Ok(report)
}
struct Driver<I, F, K> {
    report: GuidanceReport<I>,
    feedback: F,
    // Whether a tracked key has an initial-state or retainable-prefix witness.
    // Eviction leaves this true: corpus replacement intentionally uses FIFO.
    seen: HashMap<K, bool>,
    scratch: Vec<K>,
    corpus: VecDeque<CorpusEntry<I>>,
}
impl<I: Clone, F, K: Eq + Hash> Driver<I, F, K> {
    fn learn(&mut self, inputs: &[I], case: usize, transition: u64) {
        let mut novel = 0;
        let mut recovered = false;
        let retainable = inputs.len() <= self.report.config.max_corpus_inputs;
        for key in self.scratch.drain(..) {
            let at_capacity = self.seen.len() == self.report.config.max_features;
            match self.seen.entry(key) {
                Entry::Occupied(mut entry) => {
                    if retainable && !*entry.get() {
                        *entry.get_mut() = true;
                        recovered = true;
                    }
                }
                Entry::Vacant(entry) => {
                    if at_capacity {
                        self.report.feature_limit_reached = true;
                    } else {
                        entry.insert(retainable);
                        novel += 1;
                    }
                }
            }
        }
        self.report.unique_features = self.seen.len();
        if (novel == 0 && !recovered) || inputs.is_empty() {
            return;
        }
        if !retainable {
            self.report.corpus_limit_reached = true;
            self.report.dropped_prefixes += 1;
            return;
        }
        let allowance = self.report.config.max_corpus_inputs - inputs.len();
        while self.corpus.len() >= self.report.config.max_corpus_entries
            || self.report.corpus_inputs > allowance
        {
            let oldest = self.corpus.pop_front().unwrap();
            self.report.corpus_inputs -= oldest.inputs.len();
            self.report.evicted_prefixes += 1;
            self.report.corpus_limit_reached = true;
        }
        self.report.corpus_inputs += inputs.len();
        self.corpus.push_back(CorpusEntry {
            inputs: inputs.to_vec(),
            new_features: novel,
            discovered_case: case,
            discovered_transition: transition,
        });
    }
}
impl<M: Generate, F: Feedback<M>> FuzzDriver<M> for Driver<M::Input, F, F::Feature> {
    fn candidate(
        &mut self,
        _: &[M::Input],
        rng: &mut Rng,
        mutation_percent: u8,
    ) -> (Vec<M::Input>, Option<usize>) {
        if self.corpus.is_empty() || rng.index(100).unwrap() >= usize::from(mutation_percent) {
            return (Vec::new(), None);
        }
        self.report.mutation_cases += 1;
        let index = rng.index(self.corpus.len()).unwrap();
        let mut candidate = self.corpus[index].inputs.clone();
        match rng.index(3).unwrap() {
            // Preserve a novel prefix and explore a freshly generated suffix.
            0 => (candidate, None),
            1 => {
                let start = rng.index(candidate.len()).unwrap();
                let length = 1 + rng.index(candidate.len() - start).unwrap();
                candidate.drain(start..start + length);
                let replacement = rng.index(candidate.len().saturating_add(1));
                (candidate, replacement)
            }
            _ => {
                let replacement = rng.index(candidate.len());
                (candidate, replacement)
            }
        }
    }
    fn initial(&mut self, state: &M::State, case: usize) -> Result<(), ModelError> {
        self.scratch.clear();
        let mut out = FeatureBuffer {
            values: &mut self.scratch,
            maximum: self.report.config.max_features_per_observation,
            failed: false,
        };
        self.feedback.initial_features(state, &mut out)?;
        if out.failed {
            return Err(ModelError::new(
                "feedback exceeds per-observation feature limit",
            ));
        }
        self.learn(&[], case, 0);
        Ok(())
    }
    fn transition(
        &mut self,
        before: &M::State,
        input: &M::Input,
        after: &Transition<M::State, M::Output>,
        inputs: &[M::Input],
        case: usize,
        transition: u64,
    ) -> Result<(), ModelError> {
        self.scratch.clear();
        let mut out = FeatureBuffer {
            values: &mut self.scratch,
            maximum: self.report.config.max_features_per_observation,
            failed: false,
        };
        self.feedback
            .transition_features(before, input, &after.as_ref(), &mut out)?;
        if out.failed {
            return Err(ModelError::new(
                "feedback exceeds per-observation feature limit",
            ));
        }
        self.learn(inputs, case, transition);
        Ok(())
    }
}
