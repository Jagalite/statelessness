//! External public-API consumer. The library owns every request and wire frame.
use stateless::{
    Check, Disposition, EncodeBuffer, Enumerate, Model, ModelCodec, ModelError,
    ModelMetadata, Transition, TransitionRef,
};
use statelessness_debug::protocol::{
    ProtocolAuthority, ProtocolLimits, ProtocolSession, serve,
};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
use std::fmt;
use std::io::{self, Write};

const SESSION_ID: &str = "wire\té😀";

fn callback(arguments: fmt::Arguments<'_>) {
    let mut stderr = io::stderr().lock();
    writeln!(stderr, "CALL {arguments}").expect("write callback log");
    stderr.flush().expect("flush callback log");
}

fn decode_callback(bytes: &[u8]) {
    let mut stderr = io::stderr().lock();
    write!(stderr, "CALL decode ").expect("write callback log");
    for byte in bytes {
        write!(stderr, "{byte:02x}").expect("write callback log");
    }
    writeln!(stderr).expect("write callback log");
    stderr.flush().expect("flush callback log");
}

struct Counter;

impl Model for Counter {
    type State = u64;
    type Input = u8;
    type Output = u64;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "n12-wire-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "n12-wire-v1".into(),
        }
    }

    fn initial_state(&self) -> Result<u64, ModelError> {
        callback(format_args!("initial"));
        Ok(0)
    }

    fn step(&self, state: &u64, input: &u8) -> Result<Transition<u64, u64>, ModelError> {
        callback(format_args!("step {state} {input}"));
        match input {
            0 => Ok(Transition {
                state: *state,
                outputs: Vec::new(),
                disposition: Disposition::Ignored("fixture ignored".into()),
            }),
            1 => {
                let next = state
                    .checked_add(1)
                    .ok_or_else(|| ModelError::new("fixture state overflow"))?;
                Ok(Transition::accepted(next, vec![next]))
            }
            2 => Ok(Transition {
                state: *state,
                outputs: Vec::new(),
                disposition: Disposition::Rejected("fixture rejection".into()),
            }),
            3 => Err(ModelError::new("private reducer detail")),
            _ => Err(ModelError::new("invalid fixture input")),
        }
    }

    fn check_state(&self, state: &u64) -> Result<Vec<Check>, ModelError> {
        callback(format_args!("state {state}"));
        Ok(vec![if *state <= 2 {
            Check::passed("bounded")
        } else {
            Check::failed("bounded", "private state detail")
        }])
    }

    fn check_transition(
        &self,
        before: &u64,
        input: &u8,
        transition: &TransitionRef<'_, u64, u64>,
    ) -> Result<Vec<Check>, ModelError> {
        callback(format_args!(
            "transition {before} {input} {}",
            transition.state
        ));
        Ok(vec![Check::passed("transition")])
    }
}

impl ModelCodec for Counter {
    fn encode_state(&self, state: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(state.to_le_bytes().to_vec())
    }

    fn decode_state(&self, bytes: &[u8]) -> Result<u64, ModelError> {
        let encoded: [u8; 8] = bytes
            .try_into()
            .map_err(|_| ModelError::new("fixture state requires eight bytes"))?;
        Ok(u64::from_le_bytes(encoded))
    }

    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }

    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        decode_callback(bytes);
        match bytes {
            [input @ 0..=3] => Ok(*input),
            _ => Err(ModelError::new("fixture input requires one byte in 0..=3")),
        }
    }

    fn encode_output(&self, output: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(output.to_le_bytes().to_vec())
    }

    fn encode_state_into(&self, state: &u64, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&state.to_le_bytes())
    }

    fn encode_input_into(&self, input: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&[*input])
    }

    fn encode_output_into(&self, output: &u64, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&output.to_le_bytes())
    }
}

// ProtocolSession requires Enumerate even when delivery uses unrestricted policy.
// No protocol command is intercepted to implement enumeration or input delivery.
impl Enumerate for Counter {
    fn inputs(&self, _state: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0, 1, 2, 3])
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut authority = ProtocolAuthority {
        deliver_inputs: true,
        ..ProtocolAuthority::default()
    };
    let mut limits = ProtocolLimits::default();
    for argument in std::env::args_os().skip(1) {
        match argument.to_str() {
            Some("--read-only") => authority.deliver_inputs = false,
            Some("--exact") => authority.read_exact_values = true,
            Some("--cache-one") => limits.max_cache_entries = 1,
            _ => return Err("unrecognized N12 Rust host option".into()),
        }
    }
    let session = DebugSession::new(
        SESSION_ID,
        Counter,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
    )?;
    let mut endpoint = ProtocolSession::new(session, 1, authority, limits)
        .map_err(|error| format!("protocol initialization: {}", error.code()))?;
    // Normal Rust std startup ignores SIGPIPE on Unix, so an actual closed
    // stdout pipe returns an I/O error through the public serve API.
    let outcome = serve(
        &mut endpoint,
        &mut io::stdin().lock(),
        &mut io::stdout().lock(),
    );
    let session = endpoint.session();
    let final_log = {
        let mut stderr = io::stderr().lock();
        writeln!(
            stderr,
            "FINAL {} {} {}",
            session.state(),
            session.revision(),
            session.sequence()
        )
        .and_then(|()| stderr.flush())
    };
    // Try to publish the actual state on either outcome, then preserve the
    // original transport error if serving failed.
    outcome?;
    final_log?;
    Ok(())
}
