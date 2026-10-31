//! Fixture callbacks only. Search, mutation, admission, replay, and shrinking
//! are performed by the public statelessness dependency.
use serde::Deserialize;
use serde_json::{Value, json};
use stateless::{
    Check, CheckSink, Disposition, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng,
    Transition, TransitionRef,
};
use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};

pub type State = [i64; 2];

#[derive(Clone, Deserialize)]
pub struct CheckSpec {
    pub id: String,
    pub status: String,
    pub message: Option<String>,
}
impl CheckSpec {
    pub fn check(&self) -> Check {
        match self.status.as_str() {
            "Passed" => Check::passed(self.id.clone()),
            "Failed" => Check::failed(self.id.clone(), self.message.as_deref().unwrap_or_default()),
            "Skipped" => {
                Check::skipped(self.id.clone(), self.message.as_deref().unwrap_or_default())
            }
            other => panic!("unsupported check status: {other}"),
        }
    }
}
#[derive(Clone, Deserialize)]
pub struct Rule {
    pub phase: String,
    pub case: Option<usize>,
    pub depth: Option<i64>,
    pub checks: Vec<CheckSpec>,
    pub error: Option<String>,
}
#[derive(Clone, Deserialize)]
pub struct Fault {
    pub callback: String,
    pub call: usize,
    pub message: String,
}
#[derive(Clone, Deserialize)]
pub struct ModelSpec {
    pub generate: Vec<Vec<Option<i64>>>,
    pub fallback: Option<i64>,
    pub draws: usize,
    pub rng_mod: Option<u64>,
    pub enabled: String,
    pub checks: Vec<Rule>,
    pub fail_total: Option<i64>,
    pub fault: Option<Fault>,
    pub disposition: Option<DispositionSpec>,
}
#[derive(Clone, Deserialize)]
pub struct DispositionSpec {
    pub kind: String,
    pub reason: Option<String>,
}
#[derive(Clone, Deserialize)]
pub struct CancelAfter {
    pub callback: String,
    pub call: usize,
}
#[derive(Clone, Deserialize)]
pub struct ControlSpec {
    pub cancelled: bool,
    pub zero_duration: bool,
    pub cancel_after: Option<CancelAfter>,
}

pub struct Events {
    pub case: Cell<usize>,
    pub trace: RefCell<Vec<Value>>,
    pub rng: RefCell<Vec<Value>>,
    pub cancelled: AtomicBool,
    calls: RefCell<BTreeMap<String, usize>>,
    cancel_after: Option<CancelAfter>,
}
impl Events {
    pub fn new(control: &ControlSpec) -> Rc<Self> {
        Rc::new(Self {
            case: Cell::new(0),
            trace: RefCell::new(Vec::new()),
            rng: RefCell::new(Vec::new()),
            cancelled: AtomicBool::new(control.cancelled),
            calls: RefCell::new(BTreeMap::new()),
            cancel_after: control.cancel_after.clone(),
        })
    }
    pub fn quiet() -> Rc<Self> {
        Self::new(&ControlSpec {
            cancelled: false,
            zero_duration: false,
            cancel_after: None,
        })
    }
    pub fn log(&self, event: Value) {
        self.trace.borrow_mut().push(event);
    }
    pub fn done(&self, callback: &str) -> usize {
        let mut calls = self.calls.borrow_mut();
        let call = calls.entry(callback.to_owned()).or_default();
        *call += 1;
        if self
            .cancel_after
            .as_ref()
            .is_some_and(|c| c.callback == callback && c.call == *call)
        {
            self.cancelled.store(true, Ordering::Relaxed);
        }
        *call
    }
}

pub struct TableModel {
    pub spec: ModelSpec,
    pub events: Rc<Events>,
}
impl TableModel {
    pub fn fresh(spec: &ModelSpec) -> Self {
        Self {
            spec: spec.clone(),
            events: Events::quiet(),
        }
    }
    fn done(&self, callback: &str) -> Result<(), ModelError> {
        let call = self.events.done(callback);
        if let Some(fault) = &self.spec.fault {
            if fault.callback == callback && fault.call == call {
                return Err(ModelError::new(&fault.message));
            }
        }
        Ok(())
    }
    fn checks(&self, phase: &str, state: &State) -> (Vec<Check>, Option<String>) {
        let rule = self.spec.checks.iter().find(|r| {
            r.phase == phase
                && r.case.is_none_or(|c| c == self.events.case.get())
                && r.depth.is_none_or(|d| d == state[0])
        });
        let (mut checks, error) = rule.map_or_else(
            || (Vec::new(), None),
            |r| {
                (
                    r.checks.iter().map(CheckSpec::check).collect(),
                    r.error.clone(),
                )
            },
        );
        if phase != "Transition"
            && error.is_none()
            && self.spec.fail_total.is_some_and(|limit| state[1] >= limit)
        {
            checks.push(Check::failed("limit", "total reached limit"));
        }
        (checks, error)
    }
}
impl Model for TableModel {
    type State = State;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "guided-table-walk".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "table-walk-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        let case = self.events.case.get() + 1;
        self.events.case.set(case);
        self.events.log(json!(["initial_state", case]));
        self.done("initial_state")?;
        Ok([0, 0])
    }
    fn step(&self, state: &State, input: &i64) -> Result<Transition<State, i64>, ModelError> {
        self.events
            .log(json!(["step", self.events.case.get(), state, input]));
        self.done("step")?;
        let next = [state[0] + 1, state[1] + input];
        let disposition = match self.spec.disposition.as_ref() {
            None => Disposition::Accepted,
            Some(spec) => match spec.kind.as_str() {
                "Accepted" => Disposition::Accepted,
                "Rejected" => Disposition::Rejected(spec.reason.clone().unwrap_or_default()),
                "Ignored" => Disposition::Ignored(spec.reason.clone().unwrap_or_default()),
                other => {
                    return Err(ModelError::new(format!(
                        "unknown fixture disposition {other}"
                    )));
                }
            },
        };
        Ok(Transition {
            state: next,
            outputs: vec![next[1]],
            disposition,
        })
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(&self, state: &State, sink: &mut CheckSink<'_>) -> Result<(), ModelError> {
        let phase = if state[0] == 0 {
            "InitialState"
        } else {
            "State"
        };
        let (checks, error) = self.checks(phase, state);
        self.events.log(json!([
            "check_state",
            self.events.case.get(),
            state,
            crate::normalize::checks(&checks),
            error
        ]));
        sink.extend(checks);
        self.events.done("check_state");
        error.map_or(Ok(()), |e| Err(ModelError::new(e)))
    }
    fn check_transition_into(
        &self,
        before: &State,
        input: &i64,
        after: &TransitionRef<'_, State, i64>,
        sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let (checks, error) = self.checks("Transition", after.state);
        self.events.log(json!([
            "check_transition",
            self.events.case.get(),
            before,
            input,
            after.state,
            crate::normalize::checks(&checks),
            error
        ]));
        sink.extend(checks);
        self.events.done("check_transition");
        error.map_or(Ok(()), |e| Err(ModelError::new(e)))
    }
}
impl Generate for TableModel {
    fn generate(&self, state: &State, rng: &mut Rng) -> Result<Option<i64>, ModelError> {
        let raw: Vec<u64> = (0..self.spec.draws).map(|_| rng.next_u64()).collect();
        let values: Vec<String> = raw.iter().map(ToString::to_string).collect();
        let index = self.events.case.get().saturating_sub(1);
        let row = self
            .spec
            .generate
            .get(index)
            .or_else(|| self.spec.generate.last());
        let mut input = row
            .and_then(|r| r.get(state[0] as usize))
            .copied()
            .unwrap_or(self.spec.fallback);
        if input.is_some() {
            if let Some(modulus) = self.spec.rng_mod {
                input =
                    Some(1 + (raw.last().expect("rng_mod requires draws >= 1") % modulus) as i64);
            }
        }
        self.events.log(json!([
            "generate",
            self.events.case.get(),
            state,
            input,
            values
        ]));
        self.events
            .rng
            .borrow_mut()
            .push(json!({"case": self.events.case.get(), "state": state, "values": values}));
        self.done("generate")?;
        Ok(input)
    }
    fn is_enabled(&self, state: &State, input: &i64) -> Result<bool, ModelError> {
        let enabled = *input >= 0 && (self.spec.enabled != "successor" || *input == state[1] + 1);
        self.events.log(json!([
            "is_enabled",
            self.events.case.get(),
            state,
            input,
            enabled
        ]));
        self.done("is_enabled")?;
        Ok(enabled)
    }
}
impl ModelCodec for TableModel {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        let depth = u64::try_from(state[0]).map_err(|_| ModelError::new("negative depth"))?;
        let mut bytes = depth.to_le_bytes().to_vec();
        bytes.extend_from_slice(&state[1].to_le_bytes());
        Ok(bytes)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        if bytes.len() != 16 {
            return Err(ModelError::new("state requires 16 bytes"));
        }
        let depth = u64::from_le_bytes(bytes[..8].try_into().unwrap());
        Ok([
            i64::try_from(depth).map_err(|_| ModelError::new("fixture depth overflow"))?,
            decode_integer(&bytes[8..])?,
        ])
    }
    fn encode_input(&self, input: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(input.to_le_bytes().to_vec())
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<i64, ModelError> {
        decode_integer(bytes)
    }
    fn encode_output(&self, output: &i64) -> Result<Vec<u8>, ModelError> {
        Ok(output.to_le_bytes().to_vec())
    }
}
fn decode_integer(bytes: &[u8]) -> Result<i64, ModelError> {
    let bytes = bytes
        .try_into()
        .map_err(|_| ModelError::new("integer requires 8 bytes"))?;
    Ok(i64::from_le_bytes(bytes))
}
