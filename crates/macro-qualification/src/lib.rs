use stateless::*;
use statelessness_macros::{TraceDecode, TraceEncode, model};
#[derive(Clone, Debug, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
pub struct State<T>
where
    T: Clone,
{
    pub value: T,
    #[trace(length = "u8")]
    pub pending: Vec<u8>,
}
#[derive(Clone, Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
pub enum Input {
    #[trace(tag = 0)]
    Start,
    #[trace(tag = 1)]
    Cancel,
    #[trace(tag = 2)]
    Complete(u8),
}
pub struct Counter;
#[model(state=u8,input=u8,output=u8,codec)]
impl Counter {
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata {
        ModelMetadata {
            name: "macro-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture".into(),
        }
    }
    #[stateless(initial)]
    fn initial(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    #[stateless(step)]
    fn reduce(&self, s: &u8, i: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(s.saturating_add(*i), vec![*i]))
    }
    #[stateless(state_check(id = "counter.bound"))]
    fn bound(&self, s: &u8) -> Result<CheckStatus, ModelError> {
        Ok(if *s <= 3 {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed("overflow".into())
        })
    }
    #[stateless(inputs)]
    fn deliveries(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![1, 2])
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use stateless::value_codec::{DecodeLimits, TraceDecode, TraceEncode};
    #[test]
    fn golden() {
        let s = State {
            value: 0x1234u16,
            pending: vec![4, 5],
        };
        let b = s.trace_bytes(100).unwrap();
        assert_eq!(b, [0x34, 0x12, 2, 4, 5]);
        assert_eq!(
            State::<u16>::from_trace(&b, DecodeLimits::default()).unwrap(),
            s
        );
        assert_eq!(Input::Complete(7).trace_bytes(10).unwrap(), [2, 7]);
        assert!(Input::from_trace(&[3], DecodeLimits::default()).is_err());
    }
    #[test]
    fn adapter() {
        let m = Counter;
        assert_eq!(m.step(&0, &2).unwrap().state, 2);
        assert_eq!(m.check_state(&4).unwrap()[0].id, "counter.bound");
        assert_eq!(m.encode_state(&7).unwrap(), [7]);
        let report = stateless::explore::enumerate(&m, Default::default()).unwrap();
        assert!(report.failure.is_some());
    }
}

pub struct Request(pub demo::RequestModel);
statelessness_macros::input_domain! {
    pub fn deliveries(_model: &Request, state: &demo::State) -> demo::Input {
        assumptions = "At most two generations; pending deliveries survive cancellation";
        limit = 4;
        variants {
            demo::Input::Start => "bounded generation",
            demo::Input::Cancel => "active cancellation",
            demo::Input::Complete(_) => "every pending generation, including stale ones"
        };
        one demo::Input::Start if state.generation < 2;
        one demo::Input::Cancel if state.active;
        many demo::Input::Complete(g) for g in state.pending.iter().copied();
    }
}
#[model(state=demo::State,input=demo::Input,output=demo::Output)]
impl Request {
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata {
        self.0.metadata()
    }
    #[stateless(initial)]
    fn initial(&self) -> Result<demo::State, ModelError> {
        self.0.initial_state()
    }
    #[stateless(step)]
    fn reduce(
        &self,
        s: &demo::State,
        i: &demo::Input,
    ) -> Result<Transition<demo::State, demo::Output>, ModelError> {
        self.0.step(s, i)
    }
    #[stateless(state_checks)]
    fn properties(&self, s: &demo::State, checks: &mut CheckSink<'_>) -> Result<(), ModelError> {
        self.0.check_state_into(s, checks)
    }
    #[stateless(transition_checks)]
    fn effects(
        &self,
        s: &demo::State,
        i: &demo::Input,
        t: &TransitionRef<'_, demo::State, demo::Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.0.check_transition_into(s, i, t, checks)
    }
    #[stateless(inputs)]
    fn inputs(&self, s: &demo::State) -> Result<Vec<demo::Input>, ModelError> {
        Ok(deliveries(self, s)?.into_entries())
    }
}
impl ModelCodec for Request {
    fn encode_state(&self, s: &demo::State) -> Result<Vec<u8>, ModelError> {
        self.0.encode_state(s)
    }
    fn decode_state(&self, b: &[u8]) -> Result<demo::State, ModelError> {
        self.0.decode_state(b)
    }
    fn encode_input(&self, i: &demo::Input) -> Result<Vec<u8>, ModelError> {
        self.0.encode_input(i)
    }
    fn decode_input(&self, b: &[u8]) -> Result<demo::Input, ModelError> {
        self.0.decode_input(b)
    }
    fn encode_output(&self, o: &demo::Output) -> Result<Vec<u8>, ModelError> {
        self.0.encode_output(o)
    }
}
pub mod jobs;
