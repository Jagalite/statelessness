//! Optional diagnostics for application-owned execution. Observers never execute
//! transitions or checks. Text is diagnostic output, not an exact replay artifact;
//! use [`crate::monitor::Recorder`] alongside it for durable replay evidence.
use crate::trace::RunConfig;
use crate::{Check, CheckStatus, EncodeBuffer, Model, ModelCodec, ModelMetadata, TransitionRef};
use std::io::{self, Write};
use std::num::NonZeroU64;

/// Increasing verbosity. Rejected/ignored inputs are ordinary debug events;
/// they are not automatically warnings or property failures.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
pub enum LogLevel {
    #[default]
    Off,
    Error,
    Warn,
    Info,
    Debug,
    Trace,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum SnapshotPolicy {
    #[default]
    None,
    Every(NonZeroU64),
    EveryTransition,
}

impl SnapshotPolicy {
    fn includes(self, sequence: u64) -> bool {
        match self {
            Self::None => false,
            Self::Every(n) => sequence.is_multiple_of(n.get()),
            Self::EveryTransition => true,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ObservationOptions {
    pub level: LogLevel,
    /// Applies only at Trace. Explicit checkpoints include state whenever this
    /// is not None, regardless of the periodic transition interval.
    pub snapshots: SnapshotPolicy,
    /// Bounds each formatted record, including its newline. Not a bound on
    /// allocations inside application callbacks or legacy codecs.
    pub max_record_bytes: usize,
}

impl Default for ObservationOptions {
    fn default() -> Self {
        Self {
            level: LogLevel::Off,
            snapshots: SnapshotPolicy::None,
            max_record_bytes: 1024 * 1024,
        }
    }
}

/// Borrowed observations of one application execution stream. The caller owns
/// ordering and supplies every transition, including rejected/ignored inputs.
/// Exploration branches must use separate stream IDs. No continuity or replay
/// guarantee is inferred from these diagnostic events.
pub enum Event<'a, M: Model> {
    Started {
        metadata: &'a ModelMetadata,
        config: &'a RunConfig,
    },
    /// Sequence of the last applied transition, or zero for the initial state.
    Checkpoint {
        sequence: u64,
        state: &'a M::State,
        checks: &'a [Check],
    },
    Transition {
        sequence: NonZeroU64,
        before: &'a M::State,
        input: &'a M::Input,
        transition: TransitionRef<'a, M::State, M::Output>,
        checks: &'a [Check],
    },
    /// Application-provided diagnostics, including callback errors and gaps.
    Message {
        level: LogLevel,
        sequence: Option<u64>,
        message: &'a str,
    },
    Finished {
        transitions: u64,
        reason: &'a str,
    },
}

pub trait Observer<M: Model> {
    fn observe(&mut self, model: &M, event: Event<'_, M>) -> io::Result<()>;
}

/// Payload presentation is optional and does not add bounds to Model. Custom
/// implementations can render application-specific text without a codec.
/// Escape newlines and other control characters to preserve single-line records.
/// Formatters should not mutate application state or execute external effects.
pub trait PayloadFormat<M: Model> {
    fn state(&mut self, model: &M, state: &M::State, out: &mut dyn Write) -> io::Result<()>;
    fn input(&mut self, model: &M, input: &M::Input, out: &mut dyn Write) -> io::Result<()>;
    fn output(&mut self, model: &M, output: &M::Output, out: &mut dyn Write) -> io::Result<()>;
}

/// Explicitly marks payloads unavailable, even at Trace; needs only Model.
#[derive(Default)]
pub struct NoPayloads;
impl<M: Model> PayloadFormat<M> for NoPayloads {
    fn state(&mut self, _: &M, _: &M::State, out: &mut dyn Write) -> io::Result<()> {
        out.write_all(b"unavailable")
    }
    fn input(&mut self, _: &M, _: &M::Input, out: &mut dyn Write) -> io::Result<()> {
        out.write_all(b"unavailable")
    }
    fn output(&mut self, _: &M, _: &M::Output, out: &mut dyn Write) -> io::Result<()> {
        out.write_all(b"unavailable")
    }
}

/// Canonical codec bytes rendered as hex. Reuses one bounded encoding buffer.
/// Legacy encode_* defaults may allocate before the bound is checked.
pub struct EncodedPayloads {
    bytes: Vec<u8>,
    max_blob_bytes: usize,
}
impl EncodedPayloads {
    pub fn new(max_blob_bytes: usize) -> Self {
        Self {
            bytes: Vec::new(),
            max_blob_bytes,
        }
    }
    fn encode(
        &mut self,
        stage: &'static str,
        out: &mut dyn Write,
        encode: impl FnOnce(&mut EncodeBuffer<'_>) -> Result<(), crate::ModelError>,
    ) -> io::Result<()> {
        let mut buffer = EncodeBuffer::new(&mut self.bytes, self.max_blob_bytes);
        let contextual =
            |error| io::Error::other(crate::ModelError::new(format!("{stage}: {error}")));
        encode(&mut buffer).map_err(contextual)?;
        buffer.finish().map_err(contextual)?;
        out.write_all(b"hex:")?;
        for byte in &self.bytes {
            write!(out, "{byte:02x}")?;
        }
        Ok(())
    }
}
impl<M: ModelCodec> PayloadFormat<M> for EncodedPayloads {
    fn state(&mut self, model: &M, state: &M::State, out: &mut dyn Write) -> io::Result<()> {
        self.encode("encode state", out, |buffer| {
            model.encode_state_into(state, buffer)
        })
    }
    fn input(&mut self, model: &M, input: &M::Input, out: &mut dyn Write) -> io::Result<()> {
        self.encode("encode input", out, |buffer| {
            model.encode_input_into(input, buffer)
        })
    }
    fn output(&mut self, model: &M, output: &M::Output, out: &mut dyn Write) -> io::Result<()> {
        self.encode("encode output", out, |buffer| {
            model.encode_output_into(output, buffer)
        })
    }
}

/// Synchronous, bounded text formatting. Filtering happens before payload work.
/// Formatting errors write no record. A writer error can leave a partial record;
/// it permanently disables this sink, and every later call returns an error.
/// The caller decides whether logging errors should stop application execution.
pub struct TextObserver<W, F = NoPayloads> {
    writer: W,
    payloads: F,
    stream_id: String,
    options: ObservationOptions,
    record: Vec<u8>,
    failed: bool,
}
impl<W: Write, F> TextObserver<W, F> {
    pub fn new(
        writer: W,
        stream_id: impl Into<String>,
        options: ObservationOptions,
        payloads: F,
    ) -> Self {
        Self {
            writer,
            payloads,
            stream_id: stream_id.into(),
            options,
            record: Vec::new(),
            failed: false,
        }
    }
    /// Changing verbosity cannot recover earlier history. Emit a new checkpoint
    /// when enabling snapshots mid-run.
    pub fn set_level(&mut self, level: LogLevel) {
        self.options.level = level;
    }
    pub fn set_snapshots(&mut self, snapshots: SnapshotPolicy) {
        self.options.snapshots = snapshots;
    }
    pub fn get_ref(&self) -> &W {
        &self.writer
    }
    /// Does not flush. Call flush before taking ownership if required.
    pub fn into_inner(self) -> W {
        self.writer
    }
    pub fn flush(&mut self) -> io::Result<()> {
        self.ensure_healthy()?;
        let result = self.writer.flush();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn ensure_healthy(&self) -> io::Result<()> {
        if self.failed {
            Err(io::Error::other(
                "observation sink failed; output may be incomplete",
            ))
        } else {
            Ok(())
        }
    }
}

fn check_level(checks: &[Check], ordinary: LogLevel) -> LogLevel {
    if checks.iter().any(Check::is_failure) {
        LogLevel::Error
    } else if checks
        .iter()
        .any(|c| matches!(c.status, CheckStatus::Skipped(_)))
    {
        LogLevel::Warn
    } else {
        ordinary
    }
}

impl<M: Model, W: Write, F: PayloadFormat<M>> Observer<M> for TextObserver<W, F> {
    fn observe(&mut self, model: &M, event: Event<'_, M>) -> io::Result<()> {
        self.ensure_healthy()?;
        if self.options.level == LogLevel::Off {
            return Ok(());
        }
        let level = match &event {
            Event::Started { .. } | Event::Finished { .. } => LogLevel::Info,
            Event::Checkpoint { checks, .. } => check_level(checks, LogLevel::Trace),
            Event::Transition { checks, .. } => check_level(checks, LogLevel::Debug),
            Event::Message { level, .. } => *level,
        };
        if level == LogLevel::Off || level > self.options.level {
            return Ok(());
        }
        self.record.clear();
        let mut out = BoundedRecord {
            bytes: &mut self.record,
            maximum: self.options.max_record_bytes,
            failed: false,
        };
        write!(out, "{level:?} stream={:?}", self.stream_id)?;
        match event {
            Event::Started { metadata, config } => write!(
                out,
                " event=started metadata={metadata:?} config={config:?}"
            )?,
            Event::Finished {
                transitions,
                reason,
            } => write!(
                out,
                " event=finished transitions={transitions} reason={reason:?}"
            )?,
            Event::Message {
                sequence, message, ..
            } => write!(
                out,
                " event=message sequence={sequence:?} message={message:?}"
            )?,
            Event::Checkpoint {
                sequence,
                state,
                checks,
            } => {
                write!(out, " event=checkpoint sequence={sequence}")?;
                write_checks(&mut out, checks, self.options.level)?;
                if self.options.level == LogLevel::Trace
                    && self.options.snapshots != SnapshotPolicy::None
                {
                    out.write_all(b" state=")?;
                    self.payloads.state(model, state, &mut out)?;
                }
            }
            Event::Transition {
                sequence,
                before: _,
                input,
                transition,
                checks,
            } => {
                write!(
                    out,
                    " event=transition sequence={sequence} disposition={:?} outputs={}",
                    transition.disposition,
                    transition.outputs.len()
                )?;
                write_checks(&mut out, checks, self.options.level)?;
                if self.options.level == LogLevel::Trace {
                    out.write_all(b" input=")?;
                    self.payloads.input(model, input, &mut out)?;
                    for (index, output) in transition.outputs.iter().enumerate() {
                        write!(out, " output[{index}]=")?;
                        self.payloads.output(model, output, &mut out)?;
                    }
                    if self.options.snapshots.includes(sequence.get()) {
                        out.write_all(b" post_state=")?;
                        self.payloads.state(model, transition.state, &mut out)?;
                    }
                }
            }
        }
        out.write_all(b"\n")?;
        let result = self.writer.write_all(&self.record);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
}

fn write_checks(out: &mut dyn Write, checks: &[Check], level: LogLevel) -> io::Result<()> {
    write!(out, " checks={}", checks.len())?;
    for check in checks {
        if level >= LogLevel::Trace || !matches!(check.status, CheckStatus::Passed) {
            write!(out, " check={:?}:{:?}", check.id, check.status)?;
        }
    }
    Ok(())
}

struct BoundedRecord<'a> {
    bytes: &'a mut Vec<u8>,
    maximum: usize,
    failed: bool,
}
impl Write for BoundedRecord<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.failed || bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            self.failed = true;
            return Err(io::Error::other("observation record exceeds byte limit"));
        }
        let required = self.bytes.len() + bytes.len();
        let capacity = required
            .max(self.bytes.capacity().saturating_mul(2))
            .max(64)
            .min(self.maximum);
        if required > self.bytes.capacity()
            && self
                .bytes
                .try_reserve_exact(capacity - self.bytes.len())
                .is_err()
        {
            self.failed = true;
            return Err(io::Error::other("unable to allocate observation record"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
