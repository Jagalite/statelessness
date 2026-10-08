//! Explicit two-machine routing. Each step executes one child. Messages remain
//! in exact state until a separate Deliver input selects an index; no queue drain.
use crate::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use crate::*;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Address<L, R> {
    Left(L),
    Right(R),
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Input<L, R> {
    Local(Address<L, R>),
    Deliver(u32),
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct State<L, R, LI, RI> {
    pub left: L,
    pub right: R,
    pub pending: Vec<Address<LI, RI>>,
}
pub type PairState<L, R> =
    State<<L as Model>::State, <R as Model>::State, <L as Model>::Input, <R as Model>::Input>;
pub type Message<L, R> = Address<<L as Model>::Input, <R as Model>::Input>;
pub type PairInput<L, R> = Input<<L as Model>::Input, <R as Model>::Input>;
pub type PairOutput<L, R> = Address<<L as Model>::Output, <R as Model>::Output>;

/// Application-owned routing and global invariants. Outputs remain ordered and
/// observable even when routing produces no messages. Child histories, if any,
/// remain embedded in their states through WithOracle.
pub trait Wiring<L: Model, R: Model> {
    fn metadata(&self) -> ModelMetadata;
    fn messages(&self, output: &PairOutput<L, R>) -> Result<Vec<Message<L, R>>, ModelError>;
    fn check_state_into(
        &self,
        state: &PairState<L, R>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError>;
    fn check_transition_into(
        &self,
        _before: &PairState<L, R>,
        _input: &PairInput<L, R>,
        _transition: &TransitionRef<'_, PairState<L, R>, PairOutput<L, R>>,
        _checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}
pub struct Pair<L, R, W> {
    pub left: L,
    pub right: R,
    pub wiring: W,
    pub max_pending: usize,
}
fn append_namespaced(
    prefix: &str,
    checks: Vec<Check>,
    sink: &mut CheckSink<'_>,
) -> Result<(), ModelError> {
    for mut check in checks {
        check.id = format!("{prefix}::{}", check.id).into();
        if sink.as_slice().iter().any(|prior| prior.id == check.id) {
            return Err(ModelError::new(format!(
                "duplicate composed property id: {}",
                check.id
            )));
        }
        sink.push(check);
    }
    Ok(())
}
impl<L: Model, R: Model, W: Wiring<L, R>> Pair<L, R, W> {
    fn addressed<'a>(
        &self,
        s: &'a PairState<L, R>,
        i: &'a PairInput<L, R>,
    ) -> Result<&'a Address<L::Input, R::Input>, ModelError> {
        match i {
            Input::Local(a) => Ok(a),
            Input::Deliver(index) => s
                .pending
                .get(*index as usize)
                .ok_or_else(|| ModelError::new("pending message index out of range")),
        }
    }
}
impl<L: Model, R: Model, W: Wiring<L, R>> Model for Pair<L, R, W> {
    type State = PairState<L, R>;
    type Input = PairInput<L, R>;
    type Output = PairOutput<L, R>;
    fn metadata(&self) -> ModelMetadata {
        let l = self.left.metadata();
        let r = self.right.metadata();
        let w = self.wiring.metadata();
        // Length-prefix each identity. Semantic versions never disappear when a
        // caller permits build mismatch during replay.
        let identities = [&l, &r, &w].map(|m| {
            format!(
                "{}:{}:{}/{}/{}",
                m.name.len(),
                m.name,
                m.model_version,
                m.properties_version,
                m.codec_version
            )
        });
        let names = identities
            .iter()
            .map(|s| format!("{}:{s}", s.len()))
            .collect::<String>();
        let builds = [l.build, r.build, w.build]
            .iter()
            .map(|s| format!("{}:{s}", s.len()))
            .collect::<String>();
        ModelMetadata {
            name: format!("pair-v1:{names};pending={}", self.max_pending),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!("{builds};{}", env!("STATELESS_BUILD_ID")),
        }
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(State {
            left: self.left.initial_state()?,
            right: self.right.initial_state()?,
            pending: Vec::new(),
        })
    }
    fn step(
        &self,
        s: &Self::State,
        i: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        if s.pending.len() > self.max_pending {
            return Err(ModelError::new("pending message bound exceeded"));
        }
        let addressed = self.addressed(s, i)?;
        let mut next = s.clone();
        if let Input::Deliver(index) = i {
            next.pending.remove(*index as usize);
        }
        let (outputs, disposition) = match addressed {
            Address::Left(input) => {
                let t = self.left.step(&s.left, input)?;
                next.left = t.state;
                (
                    t.outputs.into_iter().map(Address::Left).collect::<Vec<_>>(),
                    t.disposition,
                )
            }
            Address::Right(input) => {
                let t = self.right.step(&s.right, input)?;
                next.right = t.state;
                (
                    t.outputs
                        .into_iter()
                        .map(Address::Right)
                        .collect::<Vec<_>>(),
                    t.disposition,
                )
            }
        };
        for output in &outputs {
            for message in self.wiring.messages(output)? {
                if next.pending.len() >= self.max_pending {
                    return Err(ModelError::new("pending message bound exceeded"));
                }
                next.pending.push(message);
            }
        }
        Ok(Transition {
            state: next,
            outputs,
            disposition,
        })
    }
    fn check_state(&self, s: &Self::State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(s, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        s: &Self::State,
        sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let mut checks = Vec::new();
        let result = self
            .left
            .check_state_into(&s.left, &mut CheckSink::new(&mut checks));
        append_namespaced("left", checks, sink)?;
        result?;
        let mut checks = Vec::new();
        let result = self
            .right
            .check_state_into(&s.right, &mut CheckSink::new(&mut checks));
        append_namespaced("right", checks, sink)?;
        result?;
        let mut checks = Vec::new();
        let result = self
            .wiring
            .check_state_into(s, &mut CheckSink::new(&mut checks));
        append_namespaced("global", checks, sink)?;
        result
    }
    fn check_transition(
        &self,
        s: &Self::State,
        i: &Self::Input,
        t: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_transition_into(s, i, t, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_transition_into(
        &self,
        s: &Self::State,
        i: &Self::Input,
        t: &TransitionRef<'_, Self::State, Self::Output>,
        sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let mut checks = Vec::new();
        let (prefix, result) = match self.addressed(s, i)? {
            Address::Left(input) => {
                let outputs = t
                    .outputs
                    .iter()
                    .map(|o| match o {
                        Address::Left(o) => Ok(o.clone()),
                        _ => Err(ModelError::new("wrong child output address")),
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                (
                    "left",
                    self.left.check_transition_into(
                        &s.left,
                        input,
                        &TransitionRef {
                            state: &t.state.left,
                            outputs: &outputs,
                            disposition: t.disposition,
                        },
                        &mut CheckSink::new(&mut checks),
                    ),
                )
            }
            Address::Right(input) => {
                let outputs = t
                    .outputs
                    .iter()
                    .map(|o| match o {
                        Address::Right(o) => Ok(o.clone()),
                        _ => Err(ModelError::new("wrong child output address")),
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                (
                    "right",
                    self.right.check_transition_into(
                        &s.right,
                        input,
                        &TransitionRef {
                            state: &t.state.right,
                            outputs: &outputs,
                            disposition: t.disposition,
                        },
                        &mut CheckSink::new(&mut checks),
                    ),
                )
            }
        };
        append_namespaced(prefix, checks, sink)?;
        result?;
        let mut checks = Vec::new();
        let result = self
            .wiring
            .check_transition_into(s, i, t, &mut CheckSink::new(&mut checks));
        append_namespaced("global", checks, sink)?;
        result
    }
}
impl<L: Enumerate, R: Enumerate, W: Wiring<L, R>> Enumerate for Pair<L, R, W> {
    fn inputs(&self, s: &Self::State) -> Result<Vec<Self::Input>, ModelError> {
        let mut inputs = self
            .left
            .inputs(&s.left)?
            .into_iter()
            .map(|i| Input::Local(Address::Left(i)))
            .chain(
                self.right
                    .inputs(&s.right)?
                    .into_iter()
                    .map(|i| Input::Local(Address::Right(i))),
            )
            .collect::<Vec<_>>();
        for i in 0..s.pending.len() {
            inputs.push(Input::Deliver(
                u32::try_from(i).map_err(|_| ModelError::new("message index overflow"))?,
            ));
        }
        Ok(inputs)
    }
}
impl<L: TraceEncode, R: TraceEncode> TraceEncode for Address<L, R> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        match self {
            Self::Left(v) => {
                0u8.trace_encode(o)?;
                v.trace_encode(o)
            }
            Self::Right(v) => {
                1u8.trace_encode(o)?;
                v.trace_encode(o)
            }
        }
    }
}
impl<L: TraceDecode, R: TraceDecode> TraceDecode for Address<L, R> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        match r.value::<u8>()? {
            0 => Ok(Self::Left(r.value()?)),
            1 => Ok(Self::Right(r.value()?)),
            _ => Err(ModelError::new("invalid address tag")),
        }
    }
}
impl<L: TraceEncode, R: TraceEncode> TraceEncode for Input<L, R> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        match self {
            Self::Local(v) => {
                0u8.trace_encode(o)?;
                v.trace_encode(o)
            }
            Self::Deliver(v) => {
                1u8.trace_encode(o)?;
                v.trace_encode(o)
            }
        }
    }
}
impl<L: TraceDecode, R: TraceDecode> TraceDecode for Input<L, R> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        match r.value::<u8>()? {
            0 => Ok(Self::Local(r.value()?)),
            1 => Ok(Self::Deliver(r.value()?)),
            _ => Err(ModelError::new("invalid composition input tag")),
        }
    }
}
impl<L: TraceEncode, R: TraceEncode, LI: TraceEncode, RI: TraceEncode> TraceEncode
    for State<L, R, LI, RI>
{
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.left.trace_encode(o)?;
        self.right.trace_encode(o)?;
        self.pending.trace_encode(o)
    }
}
impl<L: TraceDecode, R: TraceDecode, LI: TraceDecode, RI: TraceDecode> TraceDecode
    for State<L, R, LI, RI>
{
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            left: r.value()?,
            right: r.value()?,
            pending: r.value()?,
        })
    }
}
impl<L: Model, R: Model, W: Wiring<L, R>> ModelCodec for Pair<L, R, W>
where
    L::State: TraceEncode + TraceDecode,
    R::State: TraceEncode + TraceDecode,
    L::Input: TraceEncode + TraceDecode,
    R::Input: TraceEncode + TraceDecode,
    L::Output: TraceEncode,
    R::Output: TraceEncode,
{
    fn encode_state(&self, s: &Self::State) -> Result<Vec<u8>, ModelError> {
        s.trace_bytes(usize::MAX)
    }
    fn encode_input(&self, s: &Self::Input) -> Result<Vec<u8>, ModelError> {
        s.trace_bytes(usize::MAX)
    }
    fn encode_output(&self, s: &Self::Output) -> Result<Vec<u8>, ModelError> {
        s.trace_bytes(usize::MAX)
    }
    fn encode_state_into(
        &self,
        s: &Self::State,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        s.trace_encode(o)
    }
    fn encode_input_into(
        &self,
        s: &Self::Input,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        s.trace_encode(o)
    }
    fn encode_output_into(
        &self,
        s: &Self::Output,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        s.trace_encode(o)
    }
    fn decode_state(&self, b: &[u8]) -> Result<Self::State, ModelError> {
        TraceDecode::from_trace(b, DecodeLimits::default())
    }
    fn decode_input(&self, b: &[u8]) -> Result<Self::Input, ModelError> {
        TraceDecode::from_trace(b, DecodeLimits::default())
    }
}
