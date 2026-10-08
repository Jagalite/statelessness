use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::Deref;
use std::sync::Arc;

/// A stable property name: literals are borrowed and dynamic names are shared.
/// Cloning an ID never allocates. Equality compares names, regardless of storage.
#[derive(Clone)]
pub struct PropertyId(PropertyName);

#[derive(Clone)]
enum PropertyName {
    Static(&'static str),
    Shared(Arc<str>),
}

impl PropertyId {
    pub fn as_str(&self) -> &str {
        match &self.0 {
            PropertyName::Static(value) => value,
            PropertyName::Shared(value) => value,
        }
    }
}
impl From<&'static str> for PropertyId {
    fn from(value: &'static str) -> Self {
        Self(PropertyName::Static(value))
    }
}
impl From<String> for PropertyId {
    fn from(value: String) -> Self {
        Self(PropertyName::Shared(value.into()))
    }
}
impl From<Arc<str>> for PropertyId {
    fn from(value: Arc<str>) -> Self {
        Self(PropertyName::Shared(value))
    }
}
impl Deref for PropertyId {
    type Target = str;
    fn deref(&self) -> &str {
        self.as_str()
    }
}
impl AsRef<str> for PropertyId {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}
impl fmt::Debug for PropertyId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.as_str().fmt(f)
    }
}
impl fmt::Display for PropertyId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}
impl PartialEq for PropertyId {
    fn eq(&self, other: &Self) -> bool {
        self.as_str() == other.as_str()
    }
}
impl Eq for PropertyId {}
impl PartialEq<str> for PropertyId {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}
impl PartialEq<&str> for PropertyId {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}
impl PartialEq<String> for PropertyId {
    fn eq(&self, other: &String) -> bool {
        self.as_str() == other
    }
}
impl Hash for PropertyId {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.as_str().hash(state);
    }
}

/// Append-only observations for model and oracle callbacks.
/// Earlier observations cannot be removed or changed through this interface.
/// The caller owns the vector and controls clearing and capacity reuse.
/// This protects composition in safe Rust; callbacks are not a security sandbox.
///
/// ```
/// use stateless::{Check, CheckSink};
/// let mut checks = vec![Check::failed("earlier", "must survive")];
/// let mut sink = CheckSink::new(&mut checks);
/// sink.push(Check::passed("later"));
/// sink.extend([Check::passed("another")]);
/// assert!(checks[0].is_failure());
/// assert_eq!(checks.len(), 3);
/// ```
///
/// Clearing and replacing earlier checks is not available:
/// ```compile_fail
/// use stateless::{Check, CheckSink};
/// fn faulty(sink: &mut CheckSink<'_>) {
///     sink.clear();
///     sink.push(Check::passed("replacement"));
/// }
/// ```
/// ```compile_fail
/// use stateless::{Check, CheckSink};
/// fn faulty(sink: &mut CheckSink<'_>) {
///     sink[0] = Check::passed("replacement");
/// }
/// ```
/// ```compile_fail
/// use stateless::{Check, CheckSink};
/// fn faulty(sink: &mut CheckSink<'_>) {
///     sink.as_slice()[0] = Check::passed("replacement");
/// }
/// ```
pub struct CheckSink<'a> {
    checks: &'a mut Vec<Check>,
}

impl<'a> CheckSink<'a> {
    /// Wrap existing storage without clearing earlier observations.
    pub fn new(checks: &'a mut Vec<Check>) -> Self {
        Self { checks }
    }

    /// Inspect observations without granting mutable access.
    pub fn as_slice(&self) -> &[Check] {
        self.checks
    }

    /// Reserve room for additional observations without changing existing ones.
    pub fn reserve(&mut self, additional: usize) {
        self.checks.reserve(additional);
    }

    pub fn push(&mut self, check: Check) {
        self.checks.push(check);
    }

    pub fn len(&self) -> usize {
        self.checks.len()
    }

    pub fn is_empty(&self) -> bool {
        self.checks.is_empty()
    }
}

impl Extend<Check> for CheckSink<'_> {
    fn extend<T: IntoIterator<Item = Check>>(&mut self, checks: T) {
        self.checks.extend(checks);
    }
}

/// Reusable bounded output for codecs. Limits apply before growing the buffer.
/// A rejected write is sticky, even if a codec accidentally ignores its error.
pub struct EncodeBuffer<'a> {
    bytes: &'a mut Vec<u8>,
    maximum: usize,
    failed: bool,
}

impl<'a> EncodeBuffer<'a> {
    pub fn new(bytes: &'a mut Vec<u8>, maximum: usize) -> Self {
        bytes.clear();
        Self {
            bytes,
            maximum,
            failed: false,
        }
    }
    pub fn extend_from_slice(&mut self, bytes: &[u8]) -> Result<(), ModelError> {
        if self.failed
            || self
                .bytes
                .len()
                .checked_add(bytes.len())
                .is_none_or(|n| n > self.maximum)
        {
            self.failed = true;
            return Err(ModelError::new("encoded blob exceeds byte limit"));
        }
        let required = self.bytes.len() + bytes.len();
        if required > self.bytes.capacity() {
            let capacity = required
                .max(self.bytes.capacity().saturating_mul(2))
                .max(64)
                .min(self.maximum);
            if self
                .bytes
                .try_reserve_exact(capacity - self.bytes.len())
                .is_err()
            {
                self.failed = true;
                return Err(ModelError::new("unable to allocate encoded blob"));
            }
        }
        self.bytes.extend_from_slice(bytes);
        Ok(())
    }
    /// Write a length-prefixed component directly into this bounded sink.
    pub fn framed(
        &mut self,
        encode: impl FnOnce(&mut Self) -> Result<(), ModelError>,
    ) -> Result<(), ModelError> {
        let start = self.bytes.len();
        self.extend_from_slice(&[0; 8])?;
        if let Err(error) = encode(self) {
            self.failed = true;
            return Err(error);
        }
        self.finish()?;
        let length = u64::try_from(self.bytes.len() - start - 8)
            .map_err(|_| ModelError::new("encoded component length overflow"))?;
        self.bytes[start..start + 8].copy_from_slice(&length.to_le_bytes());
        Ok(())
    }
    pub fn finish(&self) -> Result<(), ModelError> {
        if self.failed {
            Err(ModelError::new(
                "encoded blob exceeds byte or allocation limit",
            ))
        } else {
            Ok(())
        }
    }
    /// Compatibility path for codecs that already allocated their entire result.
    /// Moves an empty destination's buffer rather than copying the payload again.
    /// The source allocation happened outside this sink's allocation limit.
    pub fn extend_from_vec(&mut self, bytes: Vec<u8>) -> Result<(), ModelError> {
        if self.failed
            || self
                .bytes
                .len()
                .checked_add(bytes.len())
                .is_none_or(|n| n > self.maximum)
        {
            self.failed = true;
            return Err(ModelError::new("encoded blob exceeds byte limit"));
        }
        if self.bytes.is_empty() {
            *self.bytes = bytes;
            Ok(())
        } else {
            self.extend_from_slice(&bytes)
        }
    }
}

/// Identifies the executable model and its interpretation of persisted data.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelMetadata {
    pub name: String,
    pub model_version: u32,
    pub properties_version: u32,
    pub codec_version: u32,
    /// Supply a content fingerprint of the actual build, including local changes.
    pub build: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ModelError(pub String);

impl ModelError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for ModelError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Disposition {
    Accepted,
    Rejected(String),
    Ignored(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CheckStatus {
    Passed,
    Failed(String),
    Skipped(String),
}

/// A named observation. A skipped check is never a passing check.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Check {
    pub id: PropertyId,
    pub status: CheckStatus,
}

impl Check {
    pub fn passed(id: impl Into<PropertyId>) -> Self {
        Self {
            id: id.into(),
            status: CheckStatus::Passed,
        }
    }
    pub fn failed(id: impl Into<PropertyId>, details: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            status: CheckStatus::Failed(details.into()),
        }
    }
    pub fn skipped(id: impl Into<PropertyId>, reason: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            status: CheckStatus::Skipped(reason.into()),
        }
    }
    pub fn is_failure(&self) -> bool {
        matches!(self.status, CheckStatus::Failed(_))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Transition<S, O> {
    pub state: S,
    pub outputs: Vec<O>,
    pub disposition: Disposition,
}

/// A borrowed transition for checking without cloning state or outputs.
pub struct TransitionRef<'a, S, O> {
    pub state: &'a S,
    pub outputs: &'a [O],
    pub disposition: &'a Disposition,
}

impl<S, O> Transition<S, O> {
    pub fn as_ref(&self) -> TransitionRef<'_, S, O> {
        TransitionRef {
            state: &self.state,
            outputs: &self.outputs,
            disposition: &self.disposition,
        }
    }
    pub fn accepted(state: S, outputs: Vec<O>) -> Self {
        Self {
            state,
            outputs,
            disposition: Disposition::Accepted,
        }
    }
}

/// Models must be deterministic: all relevant clocks, random choices, pending
/// work, and property history belong in state or input. Hidden mutable state,
/// external I/O, and real effect execution violate this contract.
///
/// Equality must preserve future behavior and checked properties. Inputs are
/// ordered; outputs and check observations are compared in their emitted order.
pub trait Model {
    type State: Clone + Eq;
    type Input: Clone + Eq;
    type Output: Clone + Eq;

    fn metadata(&self) -> ModelMetadata;
    fn initial_state(&self) -> Result<Self::State, ModelError>;
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError>;
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError>;
    /// Append checks in the same order as `check_state`. Override to reuse caller
    /// storage. The default preserves existing implementations and allocations.
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.extend(self.check_state(state)?);
        Ok(())
    }
    fn check_transition(
        &self,
        _before: &Self::State,
        _input: &Self::Input,
        _transition: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(Vec::new())
    }
    /// Append transition checks without allocating a separate result vector.
    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.extend(self.check_transition(before, input, transition)?);
        Ok(())
    }
    /// Application estimate including owned heap data, for opt-in search budgets.
    /// None means unknown; this is never a hard process-memory guarantee.
    fn estimated_state_bytes(&self, _state: &Self::State) -> Option<usize> {
        None
    }
}

/// Persistence is optional. Execution and exploration do not require encoding.
/// Encoding must be canonical and decode must reject malformed/trailing data.
pub trait ModelCodec: Model {
    fn encode_state(&self, state: &Self::State) -> Result<Vec<u8>, ModelError>;
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError>;
    fn encode_input(&self, input: &Self::Input) -> Result<Vec<u8>, ModelError>;
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError>;
    fn encode_output(&self, output: &Self::Output) -> Result<Vec<u8>, ModelError>;
    /// Override these methods to write incrementally within the allocation limit.
    /// Defaults keep old codecs working, but their temporary Vec is not bounded.
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_vec(self.encode_state(state)?)
    }
    fn encode_input_into(
        &self,
        input: &Self::Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_vec(self.encode_input(input)?)
    }
    fn encode_output_into(
        &self,
        output: &Self::Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_vec(self.encode_output(output)?)
    }
}

/// Enumerate every permitted input in the declared finite model, in stable order.
/// Bounds are model semantics and should be described in run metadata.
pub trait Enumerate: Model {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Self::Input>, ModelError>;
    /// Override for lazy generation: the default still materializes `inputs()`.
    fn input_iter<'a>(
        &'a self,
        state: &'a Self::State,
    ) -> Result<Box<dyn Iterator<Item = Self::Input> + 'a>, ModelError> {
        Ok(Box::new(self.inputs(state)?.into_iter()))
    }
}

/// Random generation is independent from exhaustive enumeration.
pub trait Generate: Model {
    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError>;
    /// Validate causality before replaying a candidate during shrinking.
    fn is_enabled(&self, _state: &Self::State, _input: &Self::Input) -> Result<bool, ModelError> {
        Ok(true)
    }
    fn simpler_inputs(&self, _input: &Self::Input) -> Vec<Self::Input> {
        Vec::new()
    }
}

/// SplitMix64 with a fixed algorithm. Reproducible, not cryptographic.
#[derive(Clone, Debug)]
pub struct Rng {
    state: u64,
}
impl Rng {
    pub fn new(seed: u64) -> Self {
        Self { state: seed }
    }
    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        z ^ (z >> 31)
    }
    /// Unbiased choice from 0..upper; None for an empty domain.
    pub fn index(&mut self, upper: usize) -> Option<usize> {
        if upper == 0 {
            return None;
        }
        let upper = upper as u64;
        let threshold = upper.wrapping_neg() % upper;
        loop {
            let value = self.next_u64();
            if value >= threshold {
                return Some((value % upper) as usize);
            }
        }
    }
}
