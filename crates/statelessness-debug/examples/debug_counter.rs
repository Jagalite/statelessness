//! A minimal codec-free model and optional macro-assisted display projection.
use stateless::{Check, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::inspect::{
    self, FieldView, Inspect, InspectContext, InspectError, InspectNode, InspectQuery, PathSegment,
};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
struct Counter;
impl Model for Counter {
    type State = u8;
    type Input = ();
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "handwritten-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 0,
            build: "v1".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, _: &()) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(state + 1, vec![state + 1]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state <= 2 {
            Check::passed("bounded")
        } else {
            Check::failed("bounded", "counter exceeds two")
        }])
    }
}
#[derive(statelessness_macros::Inspect)]
struct GeneratedView {
    count: u8,
}
struct HandwrittenView {
    count: u8,
}
impl Inspect for HandwrittenView {
    fn inspect(
        &self,
        path: &[PathSegment],
        context: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        context.object(
            path,
            "HandwrittenView",
            &[FieldView::new("count", "count", &self.count)],
        )
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut session = DebugSession::new(
        "counter",
        Counter,
        InputPolicy::declared("unit-input", |_, _, _| Ok(true)),
        SessionLimits::default(),
    )?;
    for _ in 0..3 {
        session.step(session.revision(), ())?;
    }
    let query = InspectQuery {
        path: vec![PathSegment::Field("count".into())],
        ..Default::default()
    };
    let handwritten = inspect::inspect(
        &HandwrittenView {
            count: *session.state(),
        },
        &query,
    )?;
    let generated = inspect::inspect(
        &GeneratedView {
            count: *session.state(),
        },
        &query,
    )?;
    assert_eq!(handwritten.node, generated.node);
    assert!(session.export_trace().is_err());
    println!(
        "count={} stop={:?}; no codec or effect runner required",
        session.state(),
        session.stop_reason()
    );
    Ok(())
}
