//! Explicit bounded composition of a finite, typed family of machines.
//!
//! One input executes one child, delivers one message, drops one message, or
//! duplicates one message. Routing appends in output/route order; it never drains
//! the queue. `M` can be an application enum implementing `Model` to represent
//! heterogeneous machines with checked state/input tags. No dynamic `Any`,
//! erased byte payloads, hidden scheduler, or partial-order reduction is used.
use std::collections::{BTreeMap, BTreeSet};

use crate::value_codec::{DecodeLimits, Decoder, TraceDecode, TraceEncode};
use crate::*;

/// Application-stable identity, independent of registration order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct MachineId(pub u32);

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Address<T> {
    pub machine: MachineId,
    pub value: T,
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Route<I> {
    pub destination: MachineId,
    pub channel: u32,
    pub input: I,
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Message<I> {
    pub source: MachineId,
    pub route: Route<I>,
}
impl<I> Message<I> {
    fn channel(&self) -> (MachineId, MachineId, u32) {
        (self.source, self.route.destination, self.route.channel)
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Input<I> {
    Local(Address<I>),
    Deliver(u32),
    Drop(u32),
    /// Append an exact copy; it retains the original channel identity.
    Duplicate(u32),
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct State<S, I> {
    /// Strictly increasing MachineId order is part of the canonical encoding.
    pub machines: Vec<(MachineId, S)>,
    pub pending: Vec<Message<I>>,
}
impl<S, I> State<S, I> {
    pub fn machine(&self, id: MachineId) -> Option<&S> {
        self.machines
            .binary_search_by_key(&id, |(id, _)| *id)
            .ok()
            .map(|n| &self.machines[n].1)
    }
}
pub type NetworkState<M> = State<<M as Model>::State, <M as Model>::Input>;
pub type NetworkInput<M> = Input<<M as Model>::Input>;
pub type NetworkOutput<M> = Address<<M as Model>::Output>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeliveryOrder {
    Unordered,
    /// Only the earliest message on (source, destination, channel) may deliver.
    FifoPerChannel,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct QueuePolicy {
    pub delivery: DeliveryOrder,
    pub max_pending: usize,
    pub max_per_channel: usize,
    pub max_routes_per_output: usize,
    pub allow_drop: bool,
    pub allow_duplicate: bool,
}
impl Default for QueuePolicy {
    fn default() -> Self {
        Self {
            delivery: DeliveryOrder::FifoPerChannel,
            max_pending: 32,
            max_per_channel: 32,
            max_routes_per_output: 8,
            allow_drop: false,
            allow_duplicate: false,
        }
    }
}

/// Bounded application-owned routing and global/pairwise invariants. Global
/// oracle history can wrap the network with `WithOracle`, observing only its
/// addressed inputs and ordered outputs when advancing history.
pub trait Wiring<M: Model> {
    fn metadata(&self) -> ModelMetadata;
    fn routes(&self, output: &NetworkOutput<M>) -> Result<Vec<Route<M::Input>>, ModelError>;
    fn check_state_into(
        &self,
        state: &NetworkState<M>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError>;
    fn check_transition_into(
        &self,
        _before: &NetworkState<M>,
        _input: &NetworkInput<M>,
        _transition: &TransitionRef<'_, NetworkState<M>, NetworkOutput<M>>,
        _checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}

pub struct Network<M, W> {
    machines: BTreeMap<MachineId, M>,
    wiring: W,
    policy: QueuePolicy,
}
impl<M: Model, W: Wiring<M>> Network<M, W> {
    /// IDs and policy become immutable model semantics. Register at least one
    /// machine; duplicate IDs and an unaddressable queue bound are rejected.
    pub fn new(
        machines: impl IntoIterator<Item = (MachineId, M)>,
        wiring: W,
        policy: QueuePolicy,
    ) -> Result<Self, ModelError> {
        let mut registered = BTreeMap::new();
        for (id, model) in machines {
            if registered.insert(id, model).is_some() {
                return Err(ModelError::new("duplicate network machine id"));
            }
        }
        if registered.is_empty() || policy.max_pending > u32::MAX as usize {
            return Err(ModelError::new(
                "invalid network machine count or queue bound",
            ));
        }
        Ok(Self {
            machines: registered,
            wiring,
            policy,
        })
    }
    pub fn policy(&self) -> QueuePolicy {
        self.policy
    }
    pub fn machine_ids(&self) -> impl Iterator<Item = MachineId> + '_ {
        self.machines.keys().copied()
    }
    pub fn wiring(&self) -> &W {
        &self.wiring
    }
    fn validate_state(&self, s: &NetworkState<M>) -> Result<(), ModelError> {
        if !s.machines.iter().map(|(id, _)| id).eq(self.machines.keys()) {
            return Err(ModelError::new(
                "network state machine IDs differ from registration",
            ));
        }
        if s.pending.len() > self.policy.max_pending {
            return Err(ModelError::new(
                "network pending bound exceeded; verification incomplete",
            ));
        }
        let mut counts = BTreeMap::new();
        for message in &s.pending {
            if !self.machines.contains_key(&message.source)
                || !self.machines.contains_key(&message.route.destination)
            {
                return Err(ModelError::new("unknown network route machine"));
            }
            let count = counts.entry(message.channel()).or_insert(0usize);
            *count += 1;
            if *count > self.policy.max_per_channel {
                return Err(ModelError::new(
                    "network channel bound exceeded; verification incomplete",
                ));
            }
        }
        Ok(())
    }
    fn selected<'a>(
        &self,
        s: &'a NetworkState<M>,
        index: u32,
    ) -> Result<&'a Message<M::Input>, ModelError> {
        s.pending
            .get(index as usize)
            .ok_or_else(|| ModelError::new("network pending index out of range"))
    }
    fn deliverable(&self, s: &NetworkState<M>, index: usize) -> bool {
        self.policy.delivery == DeliveryOrder::Unordered
            || !s.pending[..index]
                .iter()
                .any(|prior| prior.channel() == s.pending[index].channel())
    }
    fn addressed<'a>(
        &self,
        s: &'a NetworkState<M>,
        input: &'a NetworkInput<M>,
    ) -> Result<Option<(MachineId, &'a M::Input)>, ModelError> {
        match input {
            Input::Local(a) => {
                if !self.machines.contains_key(&a.machine) {
                    return Err(ModelError::new("unknown network local machine"));
                }
                Ok(Some((a.machine, &a.value)))
            }
            Input::Deliver(index) => {
                let message = self.selected(s, *index)?;
                if !self.deliverable(s, *index as usize) {
                    return Err(ModelError::new("network FIFO channel order violation"));
                }
                Ok(Some((message.route.destination, &message.route.input)))
            }
            Input::Drop(index) => {
                self.selected(s, *index)?;
                if !self.policy.allow_drop {
                    return Err(ModelError::new("network dropping disabled"));
                }
                Ok(None)
            }
            Input::Duplicate(index) => {
                self.selected(s, *index)?;
                if !self.policy.allow_duplicate {
                    return Err(ModelError::new("network duplication disabled"));
                }
                Ok(None)
            }
        }
    }
    fn enqueue(
        &self,
        state: &mut NetworkState<M>,
        message: Message<M::Input>,
    ) -> Result<(), ModelError> {
        if !self.machines.contains_key(&message.route.destination) {
            return Err(ModelError::new("unknown network route machine"));
        }
        if state.pending.len() >= self.policy.max_pending {
            return Err(ModelError::new(
                "network pending bound exceeded; verification incomplete",
            ));
        }
        if state
            .pending
            .iter()
            .filter(|m| m.channel() == message.channel())
            .count()
            >= self.policy.max_per_channel
        {
            return Err(ModelError::new(
                "network channel bound exceeded; verification incomplete",
            ));
        }
        state.pending.push(message);
        Ok(())
    }
}

fn append_checks(
    prefix: &str,
    checks: Vec<Check>,
    sink: &mut CheckSink<'_>,
) -> Result<(), ModelError> {
    for mut check in checks {
        check.id = format!("{prefix}::{}", check.id).into();
        if sink.as_slice().iter().any(|prior| prior.id == check.id) {
            return Err(ModelError::new(format!(
                "duplicate network property id: {}",
                check.id
            )));
        }
        sink.push(check);
    }
    Ok(())
}
fn identity(m: &ModelMetadata) -> String {
    format!(
        "{}:{}/{}/{}/{}",
        m.name.len(),
        m.name,
        m.model_version,
        m.properties_version,
        m.codec_version
    )
}
impl<M: Model, W: Wiring<M>> Model for Network<M, W> {
    type State = NetworkState<M>;
    type Input = NetworkInput<M>;
    type Output = NetworkOutput<M>;
    fn metadata(&self) -> ModelMetadata {
        let mut names = String::new();
        let mut builds = String::new();
        for (id, model) in &self.machines {
            let m = model.metadata();
            let name = identity(&m);
            names.push_str(&format!("{}={}:{};", id.0, name.len(), name));
            builds.push_str(&format!("{}:{}", m.build.len(), m.build));
        }
        let w = self.wiring.metadata();
        let name = identity(&w);
        names.push_str(&format!("w={}:{};", name.len(), name));
        builds.push_str(&format!("{}:{}", w.build.len(), w.build));
        let p = self.policy;
        ModelMetadata {
            name: format!(
                "network-v1:{names}policy={:?},{},{},{},{},{}",
                p.delivery,
                p.max_pending,
                p.max_per_channel,
                p.max_routes_per_output,
                p.allow_drop,
                p.allow_duplicate
            ),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!("{builds};{}", env!("STATELESS_BUILD_ID")),
        }
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(State {
            machines: self
                .machines
                .iter()
                .map(|(id, m)| Ok((*id, m.initial_state()?)))
                .collect::<Result<_, ModelError>>()?,
            pending: Vec::new(),
        })
    }
    fn step(
        &self,
        before: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.validate_state(before)?;
        let addressed = self.addressed(before, input)?;
        let mut next = before.clone();
        match input {
            Input::Deliver(index) | Input::Drop(index) => {
                next.pending.remove(*index as usize);
            }
            Input::Duplicate(index) => {
                self.enqueue(&mut next, self.selected(before, *index)?.clone())?
            }
            Input::Local(_) => {}
        }
        let Some((id, child_input)) = addressed else {
            return Ok(Transition::accepted(next, Vec::new()));
        };
        let position = before
            .machines
            .binary_search_by_key(&id, |(id, _)| *id)
            .map_err(|_| ModelError::new("missing network machine state"))?;
        let child = self
            .machines
            .get(&id)
            .ok_or_else(|| ModelError::new("unknown network child"))?;
        let t = child.step(&before.machines[position].1, child_input)?;
        next.machines[position].1 = t.state;
        let outputs = t
            .outputs
            .into_iter()
            .map(|value| Address { machine: id, value })
            .collect::<Vec<_>>();
        for output in &outputs {
            let routes = self.wiring.routes(output)?;
            if routes.len() > self.policy.max_routes_per_output {
                return Err(ModelError::new(
                    "network route fanout bound exceeded; verification incomplete",
                ));
            }
            for route in routes {
                self.enqueue(&mut next, Message { source: id, route })?;
            }
        }
        Ok(Transition {
            state: next,
            outputs,
            disposition: t.disposition,
        })
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.validate_state(state)?;
        for (id, s) in &state.machines {
            let mut checks = Vec::new();
            let result = self.machines[id].check_state_into(s, &mut CheckSink::new(&mut checks));
            append_checks(&format!("machine/{}", id.0), checks, sink)?;
            result?;
        }
        let mut checks = Vec::new();
        let result = self
            .wiring
            .check_state_into(state, &mut CheckSink::new(&mut checks));
        append_checks("global", checks, sink)?;
        result
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Self::Input,
        t: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_transition_into(before, input, t, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        t: &TransitionRef<'_, Self::State, Self::Output>,
        sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.validate_state(before)?;
        self.validate_state(t.state)?;
        if let Some((id, child_input)) = self.addressed(before, input)? {
            let outputs = t
                .outputs
                .iter()
                .map(|o| {
                    if o.machine == id {
                        Ok(o.value.clone())
                    } else {
                        Err(ModelError::new("wrong network child output address"))
                    }
                })
                .collect::<Result<Vec<_>, _>>()?;
            let mut checks = Vec::new();
            let result = self.machines[&id].check_transition_into(
                before
                    .machine(id)
                    .ok_or_else(|| ModelError::new("missing network child state"))?,
                child_input,
                &TransitionRef {
                    state: t
                        .state
                        .machine(id)
                        .ok_or_else(|| ModelError::new("missing network child state"))?,
                    outputs: &outputs,
                    disposition: t.disposition,
                },
                &mut CheckSink::new(&mut checks),
            );
            append_checks(&format!("machine/{}", id.0), checks, sink)?;
            result?;
        } else if !t.outputs.is_empty() {
            return Err(ModelError::new("network queue action emitted child output"));
        }
        let mut checks = Vec::new();
        let result =
            self.wiring
                .check_transition_into(before, input, t, &mut CheckSink::new(&mut checks));
        append_checks("global", checks, sink)?;
        result
    }
}
impl<M: Enumerate, W: Wiring<M>> Enumerate for Network<M, W> {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Self::Input>, ModelError> {
        self.validate_state(state)?;
        let mut inputs = Vec::new();
        for (id, s) in &state.machines {
            inputs.extend(self.machines[id].inputs(s)?.into_iter().map(|value| {
                Input::Local(Address {
                    machine: *id,
                    value,
                })
            }));
        }
        for (index, message) in state.pending.iter().enumerate() {
            let index = index as u32;
            if self.deliverable(state, index as usize) {
                inputs.push(Input::Deliver(index));
            }
            if self.policy.allow_drop {
                inputs.push(Input::Drop(index));
            }
            if self.policy.allow_duplicate
                && state.pending.len() < self.policy.max_pending
                && state
                    .pending
                    .iter()
                    .filter(|m| m.channel() == message.channel())
                    .count()
                    < self.policy.max_per_channel
            {
                inputs.push(Input::Duplicate(index));
            }
        }
        Ok(inputs)
    }
}

impl TraceEncode for MachineId {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.0.trace_encode(o)
    }
}
impl TraceDecode for MachineId {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self(r.value()?))
    }
}
impl<T: TraceEncode> TraceEncode for Address<T> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.machine.trace_encode(o)?;
        self.value.trace_encode(o)
    }
}
impl<T: TraceDecode> TraceDecode for Address<T> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            machine: r.value()?,
            value: r.value()?,
        })
    }
}
impl<I: TraceEncode> TraceEncode for Route<I> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.destination.trace_encode(o)?;
        self.channel.trace_encode(o)?;
        self.input.trace_encode(o)
    }
}
impl<I: TraceDecode> TraceDecode for Route<I> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            destination: r.value()?,
            channel: r.value()?,
            input: r.value()?,
        })
    }
}
impl<I: TraceEncode> TraceEncode for Message<I> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.source.trace_encode(o)?;
        self.route.trace_encode(o)
    }
}
impl<I: TraceDecode> TraceDecode for Message<I> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            source: r.value()?,
            route: r.value()?,
        })
    }
}
impl<I: TraceEncode> TraceEncode for Input<I> {
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
            Self::Drop(v) => {
                2u8.trace_encode(o)?;
                v.trace_encode(o)
            }
            Self::Duplicate(v) => {
                3u8.trace_encode(o)?;
                v.trace_encode(o)
            }
        }
    }
}
impl<I: TraceDecode> TraceDecode for Input<I> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        match r.value::<u8>()? {
            0 => Ok(Self::Local(r.value()?)),
            1 => Ok(Self::Deliver(r.value()?)),
            2 => Ok(Self::Drop(r.value()?)),
            3 => Ok(Self::Duplicate(r.value()?)),
            _ => Err(ModelError::new("invalid network action tag")),
        }
    }
}
impl<S: TraceEncode, I: TraceEncode> TraceEncode for State<S, I> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        1u8.trace_encode(o)?;
        self.machines.trace_encode(o)?;
        self.pending.trace_encode(o)
    }
}
impl<S: TraceDecode, I: TraceDecode> TraceDecode for State<S, I> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        if r.value::<u8>()? != 1 {
            return Err(ModelError::new("network state codec version"));
        }
        let s = Self {
            machines: r.value()?,
            pending: r.value()?,
        };
        if s.machines.windows(2).any(|w| w[0].0 >= w[1].0) {
            return Err(ModelError::new("noncanonical network machine order"));
        }
        let ids = s.machines.iter().map(|(id, _)| id).collect::<BTreeSet<_>>();
        if s.pending
            .iter()
            .any(|m| !ids.contains(&m.source) || !ids.contains(&m.route.destination))
        {
            return Err(ModelError::new("unknown network route machine"));
        }
        Ok(s)
    }
}
impl<M: Model, W: Wiring<M>> ModelCodec for Network<M, W>
where
    M::State: TraceEncode + TraceDecode,
    M::Input: TraceEncode + TraceDecode,
    M::Output: TraceEncode,
{
    fn encode_state(&self, s: &Self::State) -> Result<Vec<u8>, ModelError> {
        self.validate_state(s)?;
        s.trace_bytes(usize::MAX)
    }
    fn encode_input(&self, i: &Self::Input) -> Result<Vec<u8>, ModelError> {
        i.trace_bytes(usize::MAX)
    }
    fn encode_output(&self, o: &Self::Output) -> Result<Vec<u8>, ModelError> {
        o.trace_bytes(usize::MAX)
    }
    fn encode_state_into(
        &self,
        s: &Self::State,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.validate_state(s)?;
        s.trace_encode(o)
    }
    fn encode_input_into(
        &self,
        i: &Self::Input,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        i.trace_encode(o)
    }
    fn encode_output_into(
        &self,
        v: &Self::Output,
        o: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        v.trace_encode(o)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        let state = Self::State::from_trace(bytes, DecodeLimits::default())?;
        self.validate_state(&state)?;
        Ok(state)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError> {
        let input = Self::Input::from_trace(bytes, DecodeLimits::default())?;
        match &input {
            Input::Local(a) if !self.machines.contains_key(&a.machine) => {
                return Err(ModelError::new("unknown network local machine"));
            }
            Input::Drop(_) if !self.policy.allow_drop => {
                return Err(ModelError::new("network dropping disabled"));
            }
            Input::Duplicate(_) if !self.policy.allow_duplicate => {
                return Err(ModelError::new("network duplication disabled"));
            }
            _ => {}
        }
        Ok(input)
    }
}
