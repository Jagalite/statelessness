//! Application-owned job lifecycle: commands and effects are data. The same
//! reducer can be called by an application; neither adapter executes effects.
use stateless::*;
use statelessness_macros::{TraceDecode, TraceEncode, input_domain, model};
#[derive(Clone, Debug, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
pub struct State {
    pub issued: u8,
    pub pending: Vec<u8>,
    pub cancelled: Vec<u8>,
}
#[derive(Clone, Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
pub enum Input {
    #[trace(tag = 0)]
    Submit,
    #[trace(tag = 1)]
    Cancel(u8),
    #[trace(tag = 2)]
    Complete(u8),
}
#[derive(Clone, Debug, PartialEq, Eq, TraceEncode)]
pub enum Effect {
    #[trace(tag = 0)]
    Launch(u8),
    #[trace(tag = 1)]
    Publish(u8),
    #[trace(tag = 2)]
    Cleanup(u8),
}
pub fn reduce(
    before: &State,
    input: &Input,
    omit_cleanup: bool,
) -> Result<Transition<State, Effect>, ModelError> {
    let mut s = before.clone();
    let mut effects = vec![];
    match input {
        Input::Submit if s.issued < 2 => {
            s.issued += 1;
            s.pending.push(s.issued);
            effects.push(Effect::Launch(s.issued));
        }
        Input::Cancel(id) if s.pending.contains(id) && !s.cancelled.contains(id) => {
            s.cancelled.push(*id);
        }
        Input::Complete(id) if s.pending.contains(id) => {
            s.pending.retain(|i| i != id);
            if s.cancelled.contains(id) {
                if !omit_cleanup {
                    effects.push(Effect::Cleanup(*id));
                }
            } else {
                effects.push(Effect::Publish(*id));
            }
        }
        _ => {
            return Ok(Transition {
                state: s,
                outputs: effects,
                disposition: Disposition::Ignored("duplicate or unavailable command".into()),
            });
        }
    }
    Ok(Transition::accepted(s, effects))
}
pub struct Adapter {
    pub omit_cleanup: bool,
}
input_domain! {
    pub fn domain(_model:&Adapter,state:&State)->Input {
        assumptions="Two jobs, arbitrary delayed and duplicate completions; cancellation does not withdraw deliveries";
        limit=5;
        variants{Input::Submit=>"bounded launch",Input::Cancel(_)=>"pending cancellation",Input::Complete(_)=>"all issued IDs, including duplicates"};
        one Input::Submit if state.issued<2;
        many Input::Cancel(id) for id in state.pending.iter().copied().filter(|id|!state.cancelled.contains(id));
        many Input::Complete(id) for id in 1..=state.issued;
    }
}
#[model(state=State,input=Input,output=Effect,codec)]
impl Adapter {
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata {
        let hash = include_bytes!("jobs.rs")
            .iter()
            .fold(0xcbf29ce484222325u64, |h, b| {
                (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
            });
        ModelMetadata {
            name: "application-jobs".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!("jobs:{hash:016x}:omit={}", self.omit_cleanup),
        }
    }
    #[stateless(initial)]
    fn initial(&self) -> Result<State, ModelError> {
        Ok(State {
            issued: 0,
            pending: vec![],
            cancelled: vec![],
        })
    }
    #[stateless(step)]
    fn transition(&self, s: &State, i: &Input) -> Result<Transition<State, Effect>, ModelError> {
        reduce(s, i, self.omit_cleanup)
    }
    #[stateless(state_check(id = "jobs.bounded"))]
    fn bound(&self, s: &State) -> Result<CheckStatus, ModelError> {
        Ok(if s.issued <= 2 && s.pending.len() <= 2 {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed("job bound exceeded".into())
        })
    }
    #[stateless(transition_check(id = "jobs.cancelled_cleanup"))]
    fn cleanup(
        &self,
        before: &State,
        input: &Input,
        t: &TransitionRef<'_, State, Effect>,
    ) -> Result<CheckStatus, ModelError> {
        Ok(match input {
            Input::Complete(id)
                if before.pending.contains(id)
                    && before.cancelled.contains(id)
                    && !t.outputs.contains(&Effect::Cleanup(*id)) =>
            {
                CheckStatus::Failed("cancelled completion omitted cleanup".into())
            }
            _ => CheckStatus::Passed,
        })
    }
    #[stateless(inputs)]
    fn deliveries(&self, s: &State) -> Result<Vec<Input>, ModelError> {
        Ok(domain(self, s)?.into_entries())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_effect_and_healthy_graph() {
        let good = stateless::explore::enumerate(
            &Adapter {
                omit_cleanup: false,
            },
            Default::default(),
        )
        .unwrap();
        assert_eq!(
            good.termination,
            stateless::explore::SearchTermination::GraphExhausted
        );
        assert!(good.failure.is_none());
        assert_eq!(good.skipped_checks, 0);
        let bad = Adapter { omit_cleanup: true };
        let failure = stateless::explore::enumerate(&bad, Default::default())
            .unwrap()
            .failure
            .unwrap();
        assert_eq!(
            failure.inputs,
            [Input::Submit, Input::Cancel(1), Input::Complete(1)]
        );
        let trace =
            stateless::execution::record(&bad, failure.inputs, Default::default(), 10).unwrap();
        assert!(
            stateless::execution::replay(&bad, &trace, Default::default())
                .unwrap()
                .failure_reproduced
        );
    }
}
