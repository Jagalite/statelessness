//! Host-only reference boundaries; these do not claim cross-language goldens.
use serde_json::{Value, json};
use stateless::explore::{FuzzConfig, FuzzTermination, RunLimits};
use stateless::guided::{
    self, FeatureBuffer, Feedback, FeedbackMetadata, GuidanceConfig, StateFeedback,
};
use stateless::*;
use std::cell::Cell;
use std::hash::{Hash, Hasher};
use std::rc::Rc;
use std::sync::atomic::AtomicBool;

struct Walk;
impl Model for Walk {
    type State = Vec<u8>;
    type Input = Vec<u8>;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        panic!("bare fuzz must not request model metadata")
    }
    fn initial_state(&self) -> Result<Vec<u8>, ModelError> {
        Ok(Vec::new())
    }
    fn step(
        &self,
        state: &Vec<u8>,
        input: &Vec<u8>,
    ) -> Result<Transition<Vec<u8>, ()>, ModelError> {
        let mut next = state.clone();
        next.extend(input);
        Ok(Transition::accepted(next, Vec::new()))
    }
    fn check_state(&self, _: &Vec<u8>) -> Result<Vec<Check>, ModelError> {
        Ok(Vec::new())
    }
}
impl Generate for Walk {
    fn generate(&self, _: &Vec<u8>, _: &mut Rng) -> Result<Option<Vec<u8>>, ModelError> {
        Ok(Some(vec![1]))
    }
}
fn config() -> FuzzConfig {
    FuzzConfig {
        seed: 0,
        cases: 1,
        max_steps: 3,
        max_transitions: 3,
        mutation_percent: 0,
    }
}
fn metadata() -> FeedbackMetadata {
    FeedbackMetadata {
        name: String::new(),
        version: 0,
        build: String::new(),
    }
}

// Intentionally no Clone, Send, or Sync. The feature owns an Rc and collisions
// force the engine to consult equality. Rust's trait only requires Eq + Hash.
struct LocalKey(Rc<Vec<u8>>);
impl PartialEq for LocalKey {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}
impl Eq for LocalKey {}
impl Hash for LocalKey {
    fn hash<H: Hasher>(&self, h: &mut H) {
        0u8.hash(h);
    }
}

struct TransitionOnly<'a> {
    metadata_calls: &'a Cell<usize>,
    own_error: bool,
    spam: bool,
}
impl Feedback<Walk> for TransitionOnly<'_> {
    type Feature = LocalKey;
    fn metadata(&self) -> FeedbackMetadata {
        self.metadata_calls.set(self.metadata_calls.get() + 1);
        metadata()
    }
    fn transition_features(
        &self,
        _: &Vec<u8>,
        _: &Vec<u8>,
        after: &TransitionRef<'_, Vec<u8>, ()>,
        out: &mut FeatureBuffer<'_, LocalKey>,
    ) -> Result<(), ModelError> {
        for _ in 0..if self.spam { 3 } else { 1 } {
            let _ = out.push(LocalKey(Rc::new(after.state.clone())));
        }
        if self.own_error {
            Err(ModelError::new("callback wins"))
        } else {
            Ok(())
        }
    }
}

pub fn run() -> Result<Value, Box<dyn std::error::Error>> {
    let mut passed = Vec::new();
    let defaults = GuidanceConfig::default();
    assert_eq!(
        (
            defaults.max_corpus_entries,
            defaults.max_corpus_inputs,
            defaults.max_features,
            defaults.max_features_per_observation
        ),
        (256, 25_600, 100_000, 256)
    );
    passed.push("guidance_defaults_count_limits");

    let calls = Cell::new(0);
    let feedback = || TransitionOnly {
        metadata_calls: &calls,
        own_error: false,
        spam: false,
    };
    let report = guided::fuzz(&Walk, config(), defaults, feedback())?;
    assert_eq!(calls.get(), 1);
    let guidance = report.guidance.as_ref().unwrap();
    assert_eq!(guidance.unique_features, 3); // No initial feature.
    assert_eq!(guidance.corpus.len(), 3);
    assert_eq!(guidance.feedback, metadata()); // No invented metadata restrictions.
    assert_eq!(guidance.algorithm, "feature-corpus-v2");
    passed.push("default_empty_initial_nonclone_nonsend_colliding_owned_keys");
    passed.push("bare_search_metadata_once_no_model_metadata_validation");

    let exact = guided::fuzz(&Walk, config(), defaults, guided::exact_state::<Walk>())?;
    assert_eq!(exact.guidance.as_ref().unwrap().unique_features, 4);
    let compact = guided::fuzz(
        &Walk,
        config(),
        defaults,
        StateFeedback::new(metadata(), |_: &Vec<u8>| 0u8),
    )?;
    assert_eq!(compact.transitions, 3);
    assert_eq!(compact.guidance.unwrap().unique_features, 1);
    assert_eq!(exact.transitions, 3);
    passed.push("exact_state_owns_cloned_vectors_projection_does_not_merge_states");

    let mut changed = report.guidance.clone().unwrap();
    changed.corpus[2].inputs[0][0] = 99;
    assert_eq!(
        report.guidance.as_ref().unwrap().corpus[2].inputs[0],
        vec![1]
    );
    assert_eq!(changed.corpus[0].inputs[0], vec![1]);
    passed.push("report_and_corpus_prefix_clone_independence");

    for own_error in [false, true] {
        let result = guided::fuzz(
            &Walk,
            config(),
            GuidanceConfig {
                max_features_per_observation: 1,
                ..defaults
            },
            TransitionOnly {
                metadata_calls: &calls,
                own_error,
                spam: true,
            },
        );
        assert_eq!(
            result.unwrap_err().to_string(),
            if own_error {
                "transition feedback: callback wins"
            } else {
                "transition feedback: feedback exceeds per-observation feature limit"
            }
        );
    }
    passed.push("duplicate_emissions_sticky_overrun_callback_error_precedence");

    calls.set(0);
    let flag = AtomicBool::new(true);
    let report = guided::fuzz_with_limits(
        &Walk,
        config(),
        defaults,
        feedback(),
        RunLimits {
            cancellation: Some(&flag),
            max_duration: None,
        },
    )?;
    assert_eq!(calls.get(), 1);
    assert_eq!(report.termination, FuzzTermination::Cancelled);
    assert_eq!((report.cases, report.transitions), (0, 0));
    let invalid = guided::fuzz_with_limits(
        &Walk,
        config(),
        GuidanceConfig {
            max_features: 0,
            ..defaults
        },
        feedback(),
        RunLimits {
            cancellation: Some(&flag),
            max_duration: None,
        },
    );
    assert_eq!(
        invalid.unwrap_err().to_string(),
        "guidance limits must be positive"
    );
    assert_eq!(calls.get(), 1);
    passed.push("controlled_precancel_metadata_and_validation_precedence");

    // The public Rust observer/feedback interfaces are synchronous. Panic is
    // deliberately allowed through bare search, and is caught by this caller.
    let panic_result = std::panic::catch_unwind(|| {
        guided::fuzz(
            &Walk,
            config(),
            defaults,
            StateFeedback::new(metadata(), |_: &Vec<u8>| -> u8 {
                panic!("host boundary sentinel")
            }),
        )
    });
    assert!(panic_result.is_err());
    passed.push("bare_feedback_panic_unwinds_to_caller");

    let mut actual = Rng::new(7);
    let mut comparison = actual.clone();
    assert_eq!(actual.index(0), None);
    assert_eq!(actual.next_u64(), comparison.next_u64());
    assert_eq!(actual.index(1), Some(0));
    comparison.next_u64();
    assert_eq!(actual.next_u64(), comparison.next_u64());
    passed.push("public_rng_zero_domain_no_draw_singleton_consumes_draw");
    Ok(json!({"schema": "stateless-guided-rust-host/1", "passed": passed, "count": passed.len()}))
}
