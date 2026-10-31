use crate::fixture::{Events, State, TableModel};
use serde::Deserialize;
use serde_json::{Value, json};
use stateless::guided::{FeatureBuffer, Feedback, FeedbackMetadata};
use stateless::{ModelError, TransitionRef};
use std::hash::{Hash, Hasher};
use std::rc::Rc;

#[derive(Clone, Deserialize)]
pub struct Row {
    pub case: Option<usize>,
    pub depth: Option<i64>,
    pub keys: Vec<Value>,
    pub error: Option<String>,
}
#[derive(Clone, Deserialize)]
pub struct FeedbackSpec {
    pub kind: String,
    pub initial: Option<Vec<Value>>,
    pub initial_error: Option<String>,
    pub transitions: Vec<Row>,
    pub project: usize,
    pub hash: String,
    pub ignore_overrun: bool,
}

// No Clone/Send/Sync requirement is added to Rust's public feature contract.
// Constant hashing is deliberate: distinct values must still be distinguished by Eq.
pub struct Key {
    pub value: Value,
    pub constant_hash: bool,
}
impl PartialEq for Key {
    fn eq(&self, other: &Self) -> bool {
        self.value == other.value
    }
}
impl Eq for Key {}
impl Hash for Key {
    fn hash<H: Hasher>(&self, state: &mut H) {
        if self.constant_hash {
            0u8.hash(state);
        } else {
            self.value.to_string().hash(state);
        }
    }
}
pub fn metadata() -> FeedbackMetadata {
    FeedbackMetadata {
        name: "table-features".into(),
        version: 1,
        build: "guided-v2-fixture-v1".into(),
    }
}

pub struct Custom {
    pub spec: FeedbackSpec,
    pub events: Rc<Events>,
}
impl Custom {
    fn emit(
        &self,
        keys: &[Value],
        error: &Option<String>,
        out: &mut FeatureBuffer<'_, Key>,
    ) -> Result<(), ModelError> {
        for value in keys {
            let result = out.push(Key {
                value: value.clone(),
                constant_hash: self.spec.hash == "constant",
            });
            self.events.log(json!(["push", value, result.is_ok()]));
            if !self.spec.ignore_overrun {
                result?;
            }
        }
        error.as_ref().map_or(Ok(()), |e| Err(ModelError::new(e)))
    }
}
impl Feedback<TableModel> for Custom {
    type Feature = Key;
    fn metadata(&self) -> FeedbackMetadata {
        self.events.log(json!(["metadata"]));
        self.events.done("metadata");
        metadata()
    }
    fn initial_features(
        &self,
        state: &State,
        out: &mut FeatureBuffer<'_, Key>,
    ) -> Result<(), ModelError> {
        self.events
            .log(json!(["initial_feedback", self.events.case.get(), state]));
        let result = self.emit(
            self.spec.initial.as_deref().unwrap_or_default(),
            &self.spec.initial_error,
            out,
        );
        self.events.done("initial_feedback");
        result
    }
    fn transition_features(
        &self,
        before: &State,
        input: &i64,
        after: &TransitionRef<'_, State, i64>,
        out: &mut FeatureBuffer<'_, Key>,
    ) -> Result<(), ModelError> {
        self.events.log(json!([
            "transition_feedback",
            self.events.case.get(),
            before,
            input,
            after.state,
            after.outputs,
            crate::normalize::disposition(after.disposition)
        ]));
        let row = self.spec.transitions.iter().find(|r| {
            r.case.is_none_or(|c| c == self.events.case.get())
                && r.depth.is_none_or(|d| d == after.state[0])
        });
        let result = row.map_or(Ok(()), |r| self.emit(&r.keys, &r.error, out));
        self.events.done("transition_feedback");
        result
    }
}

/// This implementation genuinely uses the trait's default empty initial hook.
pub struct DefaultInitial(pub Custom);
impl Feedback<TableModel> for DefaultInitial {
    type Feature = Key;
    fn metadata(&self) -> FeedbackMetadata {
        self.0.metadata()
    }
    fn transition_features(
        &self,
        before: &State,
        input: &i64,
        after: &TransitionRef<'_, State, i64>,
        out: &mut FeatureBuffer<'_, Key>,
    ) -> Result<(), ModelError> {
        self.0.transition_features(before, input, after, out)
    }
}

/// Trace the public StateFeedback/exact_state implementation without replacing it.
/// Pushes inside these public helpers are opaque; only callback boundaries are
/// traced. The wrapper never admits or tracks a feature.
pub struct Projected<F> {
    pub inner: F,
    pub events: Rc<Events>,
}
impl<F: Feedback<TableModel>> Feedback<TableModel> for Projected<F> {
    type Feature = F::Feature;
    fn metadata(&self) -> FeedbackMetadata {
        self.events.log(json!(["metadata"]));
        let metadata = self.inner.metadata();
        self.events.done("metadata");
        metadata
    }
    fn initial_features(
        &self,
        state: &State,
        out: &mut FeatureBuffer<'_, F::Feature>,
    ) -> Result<(), ModelError> {
        self.events
            .log(json!(["initial_feedback", self.events.case.get(), state]));
        let result = self.inner.initial_features(state, out);
        self.events.done("initial_feedback");
        result
    }
    fn transition_features(
        &self,
        before: &State,
        input: &i64,
        after: &TransitionRef<'_, State, i64>,
        out: &mut FeatureBuffer<'_, F::Feature>,
    ) -> Result<(), ModelError> {
        self.events.log(json!([
            "transition_feedback",
            self.events.case.get(),
            before,
            input,
            after.state,
            after.outputs,
            crate::normalize::disposition(after.disposition)
        ]));
        let result = self.inner.transition_features(before, input, after, out);
        self.events.done("transition_feedback");
        result
    }
}
