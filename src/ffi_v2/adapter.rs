use super::*;
use crate::model::{CheckSink, EncodeBuffer, Transition, TransitionRef};
use crate::operations::{
    CodecOps, EnumerateOps, GenerateOps, InputCursor, InputIdentityOps, ModelOps,
};

pub(super) struct HostModel(pub(super) Rc<Session>);
struct ActiveGuard<'a>(&'a Session);
impl Drop for ActiveGuard<'_> {
    fn drop(&mut self) {
        let pending = self.0.active.borrow_mut().take();
        drop(pending);
    }
}

impl HostModel {
    pub(super) fn cursor_next(&self, cursor: &HostValue) -> Result<Option<HostValue>, ModelError> {
        Ok(self.invoke(9, &[cursor.token()], &[], None)?.values.pop())
    }
    fn invoke(
        &self,
        operation: u32,
        args: &[u64],
        bytes: &[u8],
        rng: Option<Rng>,
    ) -> Result<Invocation, ModelError> {
        let s = &self.0;
        let nargs = u32::try_from(args.len())
            .map_err(|_| ModelError::new("callback argument count exceeds the ABI width"))?;
        let invocation = next_handle().map_err(|err| ModelError::new(err.message))?;
        *s.active.borrow_mut() = Some(Invocation::new(invocation, operation, rng));
        let _cleanup = ActiveGuard(s);
        // SAFETY: Registration validates dispatch, arguments are borrowed live
        // host roots, and all spans remain valid for the synchronous callback.
        let status = unsafe {
            s.callbacks.dispatch.unwrap()(
                s.callbacks.context,
                s.handle,
                invocation,
                operation,
                args.as_ptr(),
                nargs,
                bytes.as_ptr(),
                bytes.len() as u64,
            )
        };
        let mut result = s
            .active
            .borrow_mut()
            .take()
            .expect("active callback missing");
        let validation = if let Some(err) = result.failure.take() {
            Err(err)
        } else if status != OK {
            Err(error(
                MODEL_ERROR,
                format!("host callback {operation} returned {status}"),
            ))
        } else {
            validate(&result)
        };
        if let Err(mut err) = validation {
            err.phase = operation;
            let message = err.message.clone();
            *s.callback_error.borrow_mut() = Some(err);
            return Err(ModelError::new(message));
        }
        Ok(result)
    }
    fn scalar(&self, operation: u32, args: &[u64]) -> Result<u64, ModelError> {
        self.invoke(operation, args, &[], None)?
            .scalar
            .ok_or_else(|| ModelError::new("callback omitted required scalar"))
    }
    fn boolean(&self, operation: u32, args: &[u64]) -> Result<bool, ModelError> {
        match self.scalar(operation, args)? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(ModelError::new("boolean callback must return zero or one")),
        }
    }
    fn one(&self, operation: u32, args: &[u64], bytes: &[u8]) -> Result<HostValue, ModelError> {
        self.invoke(operation, args, bytes, None)?
            .values
            .pop()
            .ok_or_else(|| ModelError::new("callback omitted required value"))
    }
}

fn validate(result: &Invocation) -> ApiResult<()> {
    let required = match result.operation {
        1 | 11 => Some(STATE),
        8 | 18 => Some(CURSOR),
        13 => Some(INPUT),
        _ => None,
    };
    if let Some(kind) = required
        && (result.values.len() != 1 || result.values[0].kind() != kind)
    {
        return Err(error(
            MODEL_ERROR,
            "callback must publish exactly one value of the required kind",
        ));
    }
    if result.operation == 2 && (result.values.is_empty() || result.values[0].kind() != STATE) {
        return Err(error(
            MODEL_ERROR,
            "step callback must publish its next state first",
        ));
    }
    if matches!(result.operation, 5 | 6 | 16 | 17) && result.scalar.is_none() {
        return Err(error(MODEL_ERROR, "callback must publish a scalar"));
    }
    if matches!(result.operation, 5 | 16 | 17) && result.scalar.is_some_and(|value| value > 1) {
        return Err(error(
            MODEL_ERROR,
            "boolean callback must publish zero or one",
        ));
    }
    Ok(())
}

impl ModelOps for HostModel {
    type State = HostValue;
    type Input = HostValue;
    type Output = HostValue;
    fn initial_state(&self) -> Result<HostValue, ModelError> {
        self.one(1, &[], &[])
    }
    fn step(
        &self,
        state: &HostValue,
        input: &HostValue,
    ) -> Result<Transition<HostValue, HostValue>, ModelError> {
        let mut result = self.invoke(2, &[state.token(), input.token()], &[], None)?;
        let outputs = result.values.split_off(1);
        let state = result.values.pop().expect("validated step state");
        Ok(Transition {
            state,
            outputs,
            disposition: result.disposition.unwrap_or(Disposition::Accepted),
        })
    }
    fn check_state_into(
        &self,
        state: &HostValue,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.extend(self.invoke(3, &[state.token()], &[], None)?.checks);
        Ok(())
    }
    fn check_transition_into(
        &self,
        before: &HostValue,
        input: &HostValue,
        transition: &TransitionRef<'_, HostValue, HostValue>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let (disposition, reason) = disposition_parts(transition.disposition);
        let mut args = Vec::with_capacity(4 + transition.outputs.len());
        args.extend([
            before.token(),
            input.token(),
            transition.state.token(),
            disposition as u64,
        ]);
        args.extend(transition.outputs.iter().map(HostValue::token));
        checks.extend(self.invoke(4, &args, reason.as_bytes(), None)?.checks);
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &HostValue) -> Result<Option<usize>, ModelError> {
        if self.0.callbacks.capabilities & CAP_ESTIMATE == 0 {
            return Ok(None);
        }
        self.invoke(7, &[state.token()], &[], None)?
            .scalar
            .map(|value| {
                usize::try_from(value)
                    .map_err(|_| ModelError::new("state estimate exceeds address space"))
            })
            .transpose()
    }
}

pub(super) struct Cursor {
    model: HostModel,
    cursor: HostValue,
    remaining: Option<u64>,
    // Keep the source rooted through cursor close, including owned simplifiers.
    _source: HostValue,
}
impl InputCursor for Cursor {
    type Input = HostValue;
    fn next(&mut self) -> Result<Option<HostValue>, ModelError> {
        let value = self
            .model
            .invoke(9, &[self.cursor.token()], &[], None)?
            .values
            .pop();
        if value.is_some()
            && let Some(remaining) = &mut self.remaining
        {
            *remaining = remaining
                .checked_sub(1)
                .ok_or_else(|| ModelError::new("simplification cursor item limit exceeded"))?;
        }
        Ok(value)
    }
}
impl EnumerateOps for HostModel {
    type Cursor<'a> = Cursor;
    fn input_cursor<'a>(&'a self, state: &'a HostValue) -> Result<Cursor, ModelError> {
        Ok(Cursor {
            model: HostModel(Rc::clone(&self.0)),
            cursor: self.one(8, &[state.token()], &[])?,
            remaining: None,
            _source: state.clone(),
        })
    }
    fn state_equal(&self, left: &HostValue, right: &HostValue) -> Result<bool, ModelError> {
        self.boolean(5, &[left.token(), right.token()])
    }
    fn state_hash(&self, state: &HostValue) -> Result<u64, ModelError> {
        if self.0.callbacks.capabilities & CAP_HASH == 0 {
            return Ok(0);
        }
        self.scalar(6, &[state.token()])
    }
}
impl CodecOps for HostModel {
    fn metadata(&self) -> Result<ModelMetadata, ModelError> {
        Ok(self.0.metadata.clone())
    }
    fn encode_state_into(
        &self,
        state: &HostValue,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&self.invoke(10, &[state.token()], &[], None)?.bytes)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<HostValue, ModelError> {
        self.one(11, &[], bytes)
    }
    fn encode_input_into(
        &self,
        input: &HostValue,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&self.invoke(12, &[input.token()], &[], None)?.bytes)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<HostValue, ModelError> {
        self.one(13, &[], bytes)
    }
    fn encode_output_into(
        &self,
        output: &HostValue,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&self.invoke(14, &[output.token()], &[], None)?.bytes)
    }
}
impl InputIdentityOps for HostModel {
    fn input_equal(&self, left: &HostValue, right: &HostValue) -> Result<bool, ModelError> {
        self.boolean(17, &[left.token(), right.token()])
    }
}
impl GenerateOps for HostModel {
    type Simplifications<'a> = Cursor;
    fn generate(&self, state: &HostValue, rng: &mut Rng) -> Result<Option<HostValue>, ModelError> {
        if self.0.callbacks.capabilities & CAP_AUTO_GENERATE != 0 {
            return crate::automatic::generate_ops(
                self,
                state,
                rng,
                crate::automatic::AutoOptions {
                    max_candidates: self.0.callbacks.max_items as usize,
                },
            );
        }
        let mut result = self.invoke(15, &[state.token()], &[], Some(rng.clone()))?;
        *rng = result.rng.take().expect("generator RNG");
        Ok(result.values.pop())
    }
    fn is_enabled(&self, state: &HostValue, input: &HostValue) -> Result<bool, ModelError> {
        if self.0.callbacks.capabilities & CAP_AUTO_GENERATE != 0 {
            return crate::automatic::is_enabled_ops(
                self,
                state,
                input,
                crate::automatic::AutoOptions {
                    max_candidates: self.0.callbacks.max_items as usize,
                },
            );
        }
        self.boolean(16, &[state.token(), input.token()])
    }
    fn simpler_inputs(&self, input: &HostValue) -> Result<Cursor, ModelError> {
        Ok(Cursor {
            model: HostModel(Rc::clone(&self.0)),
            cursor: self.one(18, &[input.token()], &[])?,
            remaining: Some(self.0.callbacks.max_items),
            _source: input.clone(),
        })
    }
}

pub(super) fn disposition_parts(disposition: &Disposition) -> (u32, &str) {
    match disposition {
        Disposition::Accepted => (0, ""),
        Disposition::Rejected(reason) => (1, reason),
        Disposition::Ignored(reason) => (2, reason),
    }
}
pub(super) fn disposition(kind: u32, reason: String) -> ApiResult<Disposition> {
    match kind {
        0 if reason.is_empty() => Ok(Disposition::Accepted),
        1 => Ok(Disposition::Rejected(reason)),
        2 => Ok(Disposition::Ignored(reason)),
        _ => Err(error(
            INVALID_ARGUMENT,
            "invalid disposition or accepted reason",
        )),
    }
}

pub(super) fn builder(
    handle: u64,
    invocation: u64,
    call: impl FnOnce(&Rc<Session>, &mut Invocation) -> ApiResult<i32>,
) -> ApiResult<i32> {
    let session = session(handle)?;
    let mut active = session.active.borrow_mut();
    let active = active
        .as_mut()
        .ok_or_else(|| error(REENTRANT, "no active callback"))?;
    if let Some(err) = &active.failure {
        return Err(err.clone());
    }
    let result = if active.handle != invocation {
        Err(error(INVALID_HANDLE, "stale callback invocation"))
    } else {
        call(&session, active)
    };
    if let Err(err) = &result {
        active.failure = Some(err.clone());
    }
    result
}

pub(super) fn charge(session: &Session, active: &mut Invocation, bytes: u64) -> ApiResult<()> {
    let total = active
        .retained_bytes
        .checked_add(bytes)
        .ok_or_else(|| error(LIMIT, "callback byte limit overflow"))?;
    if total > session.callbacks.max_bytes {
        return Err(error(LIMIT, "callback byte limit exceeded"));
    }
    active.retained_bytes = total;
    Ok(())
}
