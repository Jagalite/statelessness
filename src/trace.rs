//! Version 1 audit format. All integers are little endian. A 12-byte file header
//! is followed by a run record, indexed transition records, and a mandatory end
//! record. Every record has a tag (u8), payload length (u32), payload, and CRC32
//! over the tag, length, and payload. CRC32 detects accidental corruption; it
//! does not authenticate a trace. Application payloads are opaque byte strings.
//!
//! Readers reject unsupported versions, unknown records, missing footers, and
//! trailing bytes. Limits bound both encoded bytes and aggregate collection
//! items, since many empty values can consume substantial decoded memory.
use crate::model::{Check, CheckStatus, Disposition, ModelMetadata};
use std::fmt;
use std::io::{self, Read, Write};

const MAGIC: &[u8; 8] = b"STLESS\x1a\n";
pub const FORMAT_VERSION: u16 = 1;
const RUN: u8 = 1;
const STEP: u8 = 2;
const END: u8 = 3;
pub(crate) const FILE_HEADER_SIZE: u64 = 12;
pub(crate) const FRAME_OVERHEAD: u64 = 9;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunConfig {
    pub strategy: String,
    pub seed: Option<u64>,
    pub parameters: Vec<(String, String)>,
}

impl Default for RunConfig {
    fn default() -> Self {
        Self {
            strategy: "manual".into(),
            seed: None,
            parameters: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TraceStep {
    pub input: Vec<u8>,
    pub disposition: Disposition,
    pub outputs: Vec<Vec<u8>>,
    pub post_state: Vec<u8>,
    pub checks: Vec<Check>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Termination {
    /// The supplied sequence ended. This does not mean the application reached
    /// a terminal state or that an exploration exhausted its reachable graph.
    Completed,
    PropertyFailed,
    /// At least one supplied input was left unexecuted by the step budget.
    StepLimit,
    Interrupted,
    /// The stored prefix precedes an adapter error. Replaying the prefix alone
    /// does not reproduce or verify this terminal error.
    ModelError(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Trace {
    pub metadata: ModelMetadata,
    pub config: RunConfig,
    pub initial_state: Vec<u8>,
    pub initial_checks: Vec<Check>,
    pub steps: Vec<TraceStep>,
    pub termination: Termination,
}

/// Limits apply to writing as well as reading. Raising one field does not
/// implicitly raise the others. Total bytes includes framing and the footer.
#[derive(Clone, Debug)]
pub struct ReadLimits {
    pub max_total_bytes: u64,
    pub max_frame_bytes: usize,
    pub max_blob_bytes: usize,
    pub max_string_bytes: usize,
    pub max_steps: usize,
    pub max_checks_per_step: usize,
    pub max_outputs_per_step: usize,
    pub max_parameters: usize,
    /// Sum of steps, checks, outputs, and parameter pairs in the entire trace.
    pub max_items: usize,
}

impl Default for ReadLimits {
    fn default() -> Self {
        Self {
            max_total_bytes: 64 * 1024 * 1024,
            max_frame_bytes: 8 * 1024 * 1024,
            max_blob_bytes: 4 * 1024 * 1024,
            max_string_bytes: 64 * 1024,
            max_steps: 100_000,
            max_checks_per_step: 4096,
            max_outputs_per_step: 4096,
            max_parameters: 1024,
            max_items: 250_000,
        }
    }
}

#[derive(Debug)]
pub enum TraceError {
    Io(io::Error),
    Truncated,
    UnsupportedVersion(u16),
    ChecksumMismatch,
    InvalidData(&'static str),
    LimitExceeded(&'static str),
    AllocationFailed,
}

impl fmt::Display for TraceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(err) => write!(f, "trace I/O error: {err}"),
            Self::Truncated => f.write_str("trace is truncated (missing record data or footer)"),
            Self::UnsupportedVersion(v) => write!(f, "unsupported trace version {v}"),
            Self::ChecksumMismatch => f.write_str("trace record checksum mismatch"),
            Self::InvalidData(msg) => write!(f, "invalid trace: {msg}"),
            Self::LimitExceeded(name) => write!(f, "trace exceeds {name} limit"),
            Self::AllocationFailed => f.write_str("unable to allocate trace storage"),
        }
    }
}

impl std::error::Error for TraceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(err) => Some(err),
            _ => None,
        }
    }
}

impl From<io::Error> for TraceError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

fn limit(value: usize, maximum: usize, name: &'static str) -> Result<(), TraceError> {
    if value > maximum {
        Err(TraceError::LimitExceeded(name))
    } else {
        Ok(())
    }
}

fn items(total: &mut usize, count: usize, limits: &ReadLimits) -> Result<(), TraceError> {
    *total = total
        .checked_add(count)
        .ok_or(TraceError::LimitExceeded("aggregate items"))?;
    limit(*total, limits.max_items, "aggregate items")
}

struct Encoder<'a> {
    data: Option<Vec<u8>>,
    len: usize,
    limits: &'a ReadLimits,
    items: &'a mut usize,
}

impl<'a> Encoder<'a> {
    fn new(limits: &'a ReadLimits, items: &'a mut usize, data: Option<Vec<u8>>) -> Self {
        Self {
            data,
            len: 0,
            limits,
            items,
        }
    }
    fn bytes(&mut self, bytes: &[u8]) -> Result<(), TraceError> {
        self.len = self
            .len
            .checked_add(bytes.len())
            .ok_or(TraceError::LimitExceeded("frame bytes"))?;
        limit(
            self.len,
            self.limits.max_frame_bytes.min(u32::MAX as usize),
            "frame bytes",
        )?;
        if let Some(data) = &mut self.data {
            data.try_reserve(bytes.len())
                .map_err(|_| TraceError::AllocationFailed)?;
            data.extend_from_slice(bytes);
        }
        Ok(())
    }
    fn u8(&mut self, n: u8) -> Result<(), TraceError> {
        self.bytes(&[n])
    }
    fn u32(&mut self, n: u32) -> Result<(), TraceError> {
        self.bytes(&n.to_le_bytes())
    }
    fn u64(&mut self, n: u64) -> Result<(), TraceError> {
        self.bytes(&n.to_le_bytes())
    }
    fn sized(&mut self, data: &[u8], maximum: usize, name: &'static str) -> Result<(), TraceError> {
        limit(data.len(), maximum.min(u32::MAX as usize), name)?;
        self.u32(data.len() as u32)?;
        self.bytes(data)
    }
    fn string(&mut self, value: &str) -> Result<(), TraceError> {
        self.sized(
            value.as_bytes(),
            self.limits.max_string_bytes,
            "string bytes",
        )
    }
    fn blob(&mut self, value: &[u8]) -> Result<(), TraceError> {
        self.sized(value, self.limits.max_blob_bytes, "blob bytes")
    }
    fn count(&mut self, n: usize, maximum: usize, name: &'static str) -> Result<(), TraceError> {
        limit(n, maximum.min(u32::MAX as usize), name)?;
        items(self.items, n, self.limits)?;
        self.u32(n as u32)
    }
    fn checks(&mut self, checks: &[Check]) -> Result<(), TraceError> {
        self.count(
            checks.len(),
            self.limits.max_checks_per_step,
            "checks per step",
        )?;
        for check in checks {
            self.string(&check.id)?;
            match &check.status {
                CheckStatus::Passed => self.u8(0)?,
                CheckStatus::Failed(reason) => {
                    self.u8(1)?;
                    self.string(reason)?;
                }
                CheckStatus::Skipped(reason) => {
                    self.u8(2)?;
                    self.string(reason)?;
                }
            }
        }
        Ok(())
    }
}

fn encode_run(
    e: &mut Encoder<'_>,
    metadata: &ModelMetadata,
    config: &RunConfig,
    initial_state: &[u8],
    initial_checks: &[Check],
) -> Result<(), TraceError> {
    e.string(&metadata.name)?;
    e.u32(metadata.model_version)?;
    e.u32(metadata.properties_version)?;
    e.u32(metadata.codec_version)?;
    e.string(&metadata.build)?;
    e.string(&config.strategy)?;
    match config.seed {
        None => e.u8(0)?,
        Some(seed) => {
            e.u8(1)?;
            e.u64(seed)?;
        }
    }
    e.count(
        config.parameters.len(),
        e.limits.max_parameters,
        "parameters",
    )?;
    for (key, value) in &config.parameters {
        e.string(key)?;
        e.string(value)?;
    }
    e.blob(initial_state)?;
    e.checks(initial_checks)
}

fn encode_step(e: &mut Encoder<'_>, index: usize, s: &TraceStep) -> Result<(), TraceError> {
    e.u64(index as u64)?;
    e.blob(&s.input)?;
    match &s.disposition {
        Disposition::Accepted => e.u8(0)?,
        Disposition::Rejected(reason) => {
            e.u8(1)?;
            e.string(reason)?;
        }
        Disposition::Ignored(reason) => {
            e.u8(2)?;
            e.string(reason)?;
        }
    }
    e.count(
        s.outputs.len(),
        e.limits.max_outputs_per_step,
        "outputs per step",
    )?;
    for output in &s.outputs {
        e.blob(output)?;
    }
    e.blob(&s.post_state)?;
    e.checks(&s.checks)
}

fn encode_end(
    e: &mut Encoder<'_>,
    step_count: usize,
    termination: &Termination,
) -> Result<(), TraceError> {
    e.u64(step_count as u64)?;
    match termination {
        Termination::Completed => e.u8(0),
        Termination::PropertyFailed => e.u8(1),
        Termination::StepLimit => e.u8(2),
        Termination::Interrupted => e.u8(3),
        Termination::ModelError(reason) => {
            e.u8(4)?;
            e.string(reason)
        }
    }
}

/// Serialized size, including framing. Collection items use `ReadLimits`'s
/// accounting and are distinct from the process allocator's memory usage.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct EncodedSize {
    pub bytes: u64,
    pub items: usize,
}

pub(crate) fn run_size(
    metadata: &ModelMetadata,
    config: &RunConfig,
    state: &[u8],
    checks: &[Check],
    limits: &ReadLimits,
) -> Result<EncodedSize, TraceError> {
    let mut count = 0;
    let mut e = Encoder::new(limits, &mut count, None);
    encode_run(&mut e, metadata, config, state, checks)?;
    Ok(EncodedSize {
        bytes: e.len as u64 + FRAME_OVERHEAD,
        items: count,
    })
}

pub(crate) fn step_size(step: &TraceStep, limits: &ReadLimits) -> Result<EncodedSize, TraceError> {
    let mut count = 1;
    limit(count, limits.max_items, "aggregate items")?;
    let mut e = Encoder::new(limits, &mut count, None);
    // The index has a fixed width, so its value does not affect the size.
    encode_step(&mut e, 0, step)?;
    Ok(EncodedSize {
        bytes: e.len as u64 + FRAME_OVERHEAD,
        items: count,
    })
}

pub(crate) fn end_size(
    steps: usize,
    termination: &Termination,
    limits: &ReadLimits,
) -> Result<EncodedSize, TraceError> {
    let mut count = 0;
    let mut e = Encoder::new(limits, &mut count, None);
    encode_end(&mut e, steps, termination)?;
    Ok(EncodedSize {
        bytes: e.len as u64 + FRAME_OVERHEAD,
        items: 0,
    })
}

fn error_text_limit(limits: &ReadLimits) -> usize {
    // Footer payload: u64 step count, u8 termination tag, u32 string length.
    256.min(limits.max_string_bytes)
        .min(limits.max_frame_bytes.saturating_sub(13))
}

/// Retain enough room to export a terminal recording error without discarding
/// the last valid prefix. The original error remains available to the caller.
pub(crate) fn reserved_end_size(limits: &ReadLimits) -> Result<EncodedSize, TraceError> {
    end_size(
        0,
        &Termination::ModelError("x".repeat(error_text_limit(limits))),
        limits,
    )
}

pub(crate) fn error_termination(message: &str, limits: &ReadLimits) -> Termination {
    let maximum = error_text_limit(limits);
    let mut end = message.len().min(maximum);
    let truncated = end < message.len();
    if truncated && maximum >= 3 {
        end = maximum - 3;
    }
    while !message.is_char_boundary(end) {
        end -= 1;
    }
    let mut reason = message[..end].to_owned();
    if truncated && maximum >= 3 {
        reason.push_str("...");
    }
    Termination::ModelError(reason)
}

pub(crate) fn validate_totals(
    bytes: u64,
    item_count: usize,
    steps: usize,
    limits: &ReadLimits,
) -> Result<(), TraceError> {
    if bytes > limits.max_total_bytes {
        return Err(TraceError::LimitExceeded("total bytes"));
    }
    limit(item_count, limits.max_items, "aggregate items")?;
    limit(steps, limits.max_steps, "steps")
}

/// Borrowed export shared by owned traces and the runtime recorder. Only one
/// frame buffer is materialized, and its allocation is reused between frames.
pub(crate) struct TraceView<'a, I> {
    pub metadata: &'a ModelMetadata,
    pub config: &'a RunConfig,
    pub initial_state: &'a [u8],
    pub initial_checks: &'a [Check],
    pub steps: I,
    pub step_count: usize,
    pub termination: &'a Termination,
}

impl<'a, I> TraceView<'a, I>
where
    I: Iterator<Item = &'a TraceStep> + Clone,
{
    pub(crate) fn write_with_limits(
        &self,
        mut writer: impl Write,
        limits: &ReadLimits,
    ) -> Result<(), TraceError> {
        // Validate all lengths before the first byte is published. The sizing
        // pass visits field lengths without copying or scanning blob contents.
        let run = run_size(
            self.metadata,
            self.config,
            self.initial_state,
            self.initial_checks,
            limits,
        )?;
        let footer = end_size(self.step_count, self.termination, limits)?;
        let mut bytes = FILE_HEADER_SIZE
            .checked_add(run.bytes)
            .and_then(|n| n.checked_add(footer.bytes))
            .ok_or(TraceError::LimitExceeded("total bytes"))?;
        let mut count = run.items;
        let mut maximum_frame = run.bytes.max(footer.bytes) - FRAME_OVERHEAD;
        let mut actual_steps = 0usize;
        for step in self.steps.clone() {
            let size = step_size(step, limits)?;
            bytes = bytes
                .checked_add(size.bytes)
                .ok_or(TraceError::LimitExceeded("total bytes"))?;
            count = count
                .checked_add(size.items)
                .ok_or(TraceError::LimitExceeded("aggregate items"))?;
            maximum_frame = maximum_frame.max(size.bytes - FRAME_OVERHEAD);
            actual_steps = actual_steps
                .checked_add(1)
                .ok_or(TraceError::LimitExceeded("steps"))?;
            validate_totals(bytes, count, actual_steps, limits)?;
        }
        if actual_steps != self.step_count {
            return Err(TraceError::InvalidData("transition count"));
        }
        validate_totals(bytes, count, actual_steps, limits)?;
        // Reserve once from validated frame lengths. Reusing this buffer avoids
        // reallocating and copying its accumulated fields for every transition.
        let mut buffer = reserve_vec(maximum_frame as usize)?;
        writer.write_all(MAGIC)?;
        writer.write_all(&FORMAT_VERSION.to_le_bytes())?;
        writer.write_all(&0u16.to_le_bytes())?;
        let mut item_count = self.step_count;
        let mut e = Encoder::new(limits, &mut item_count, Some(buffer));
        encode_run(
            &mut e,
            self.metadata,
            self.config,
            self.initial_state,
            self.initial_checks,
        )?;
        buffer = e.data.unwrap();
        write_frame(&mut writer, RUN, &buffer)?;
        for (index, step) in self.steps.clone().enumerate() {
            buffer.clear();
            let mut e = Encoder::new(limits, &mut item_count, Some(buffer));
            encode_step(&mut e, index, step)?;
            buffer = e.data.unwrap();
            write_frame(&mut writer, STEP, &buffer)?;
        }
        buffer.clear();
        let mut e = Encoder::new(limits, &mut item_count, Some(buffer));
        encode_end(&mut e, self.step_count, self.termination)?;
        write_frame(&mut writer, END, &e.data.unwrap())
    }
}

impl Trace {
    /// Validates the complete trace before writing. I/O failure can still leave
    /// a partial stream; use a temporary file plus flush/sync/rename for durable
    /// publication. This method never flushes or closes the caller's writer.
    pub fn write_to(&self, writer: impl Write) -> Result<(), TraceError> {
        self.write_with_limits(writer, &ReadLimits::default())
    }

    pub fn write_with_limits(
        &self,
        mut writer: impl Write,
        limits: &ReadLimits,
    ) -> Result<(), TraceError> {
        TraceView {
            metadata: &self.metadata,
            config: &self.config,
            initial_state: &self.initial_state,
            initial_checks: &self.initial_checks,
            steps: self.steps.iter(),
            step_count: self.steps.len(),
            termination: &self.termination,
        }
        .write_with_limits(&mut writer, limits)
    }

    /// Reads exactly one complete artifact and verifies EOF after its footer.
    /// A caller reading from a persistent socket must provide a bounded reader;
    /// otherwise the EOF check can wait for the peer to close its stream.
    pub fn read_from(reader: impl Read, limits: &ReadLimits) -> Result<Self, TraceError> {
        let mut reader = LimitedReader {
            reader,
            consumed: 0,
            limits,
        };
        let mut header = [0u8; FILE_HEADER_SIZE as usize];
        reader.exact(&mut header)?;
        if &header[..8] != MAGIC {
            return Err(TraceError::InvalidData("file magic"));
        }
        let version = u16::from_le_bytes([header[8], header[9]]);
        if version != FORMAT_VERSION {
            return Err(TraceError::UnsupportedVersion(version));
        }
        if header[10..] != [0, 0] {
            return Err(TraceError::InvalidData("unsupported header flags"));
        }
        let (tag, payload) = reader.frame()?;
        if tag != RUN {
            return Err(TraceError::InvalidData("first record must be run metadata"));
        }
        let mut item_count = 0;
        let mut d = Decoder::new(&payload, limits, &mut item_count);
        let metadata = ModelMetadata {
            name: d.string()?,
            model_version: d.u32()?,
            properties_version: d.u32()?,
            codec_version: d.u32()?,
            build: d.string()?,
        };
        let strategy = d.string()?;
        let seed = match d.u8()? {
            0 => None,
            1 => Some(d.u64()?),
            _ => return Err(TraceError::InvalidData("seed tag")),
        };
        let n = d.count(limits.max_parameters, "parameters", 8)?;
        let mut parameters = reserve_vec(n)?;
        for _ in 0..n {
            parameters.push((d.string()?, d.string()?));
        }
        let initial_state = d.blob()?;
        let initial_checks = d.checks()?;
        d.finish()?;
        let config = RunConfig {
            strategy,
            seed,
            parameters,
        };
        let mut steps = Vec::new();
        let termination = loop {
            let (tag, payload) = reader.frame()?;
            let mut d = Decoder::new(&payload, limits, &mut item_count);
            match tag {
                STEP => {
                    limit(steps.len().saturating_add(1), limits.max_steps, "steps")?;
                    items(d.items, 1, limits)?;
                    if d.u64()? != steps.len() as u64 {
                        return Err(TraceError::InvalidData("transition index"));
                    }
                    let input = d.blob()?;
                    let disposition = match d.u8()? {
                        0 => Disposition::Accepted,
                        1 => Disposition::Rejected(d.string()?),
                        2 => Disposition::Ignored(d.string()?),
                        _ => return Err(TraceError::InvalidData("disposition tag")),
                    };
                    let n = d.count(limits.max_outputs_per_step, "outputs per step", 4)?;
                    let mut outputs = reserve_vec(n)?;
                    for _ in 0..n {
                        outputs.push(d.blob()?);
                    }
                    let post_state = d.blob()?;
                    let checks = d.checks()?;
                    d.finish()?;
                    steps
                        .try_reserve(1)
                        .map_err(|_| TraceError::AllocationFailed)?;
                    steps.push(TraceStep {
                        input,
                        disposition,
                        outputs,
                        post_state,
                        checks,
                    });
                }
                END => {
                    if d.u64()? != steps.len() as u64 {
                        return Err(TraceError::InvalidData("footer transition count"));
                    }
                    let termination = match d.u8()? {
                        0 => Termination::Completed,
                        1 => Termination::PropertyFailed,
                        2 => Termination::StepLimit,
                        3 => Termination::Interrupted,
                        4 => Termination::ModelError(d.string()?),
                        _ => return Err(TraceError::InvalidData("termination tag")),
                    };
                    d.finish()?;
                    reader.require_eof()?;
                    break termination;
                }
                _ => return Err(TraceError::InvalidData("unknown or misplaced record")),
            }
        };
        Ok(Self {
            metadata,
            config,
            initial_state,
            initial_checks,
            steps,
            termination,
        })
    }
}

fn reserve_vec<T>(n: usize) -> Result<Vec<T>, TraceError> {
    let mut result = Vec::new();
    result
        .try_reserve_exact(n)
        .map_err(|_| TraceError::AllocationFailed)?;
    Ok(result)
}

struct Decoder<'a> {
    data: &'a [u8],
    pos: usize,
    limits: &'a ReadLimits,
    items: &'a mut usize,
}

impl<'a> Decoder<'a> {
    fn new(data: &'a [u8], limits: &'a ReadLimits, items: &'a mut usize) -> Self {
        Self {
            data,
            pos: 0,
            limits,
            items,
        }
    }
    fn bytes(&mut self, n: usize) -> Result<&'a [u8], TraceError> {
        let end = self
            .pos
            .checked_add(n)
            .ok_or(TraceError::InvalidData("field length overflow"))?;
        let bytes = self
            .data
            .get(self.pos..end)
            .ok_or(TraceError::InvalidData("field exceeds frame"))?;
        self.pos = end;
        Ok(bytes)
    }
    fn u8(&mut self) -> Result<u8, TraceError> {
        Ok(self.bytes(1)?[0])
    }
    fn u32(&mut self) -> Result<u32, TraceError> {
        Ok(u32::from_le_bytes(self.bytes(4)?.try_into().unwrap()))
    }
    fn u64(&mut self) -> Result<u64, TraceError> {
        Ok(u64::from_le_bytes(self.bytes(8)?.try_into().unwrap()))
    }
    fn sized(&mut self, maximum: usize, name: &'static str) -> Result<&'a [u8], TraceError> {
        let len = self.u32()? as usize;
        limit(len, maximum, name)?;
        self.bytes(len)
    }
    fn string(&mut self) -> Result<String, TraceError> {
        let bytes = self.sized(self.limits.max_string_bytes, "string bytes")?;
        let value =
            std::str::from_utf8(bytes).map_err(|_| TraceError::InvalidData("invalid UTF-8"))?;
        let mut result = String::new();
        result
            .try_reserve_exact(value.len())
            .map_err(|_| TraceError::AllocationFailed)?;
        result.push_str(value);
        Ok(result)
    }
    fn blob(&mut self) -> Result<Vec<u8>, TraceError> {
        let bytes = self.sized(self.limits.max_blob_bytes, "blob bytes")?;
        let mut result = reserve_vec(bytes.len())?;
        result.extend_from_slice(bytes);
        Ok(result)
    }
    fn count(
        &mut self,
        maximum: usize,
        name: &'static str,
        min_item_bytes: usize,
    ) -> Result<usize, TraceError> {
        let n = self.u32()? as usize;
        limit(n, maximum, name)?;
        items(self.items, n, self.limits)?;
        if n > (self.data.len() - self.pos) / min_item_bytes {
            return Err(TraceError::InvalidData("collection count exceeds frame"));
        }
        Ok(n)
    }
    fn checks(&mut self) -> Result<Vec<Check>, TraceError> {
        let n = self.count(self.limits.max_checks_per_step, "checks per step", 5)?;
        let mut checks = reserve_vec(n)?;
        for _ in 0..n {
            let id = self.string()?;
            let status = match self.u8()? {
                0 => CheckStatus::Passed,
                1 => CheckStatus::Failed(self.string()?),
                2 => CheckStatus::Skipped(self.string()?),
                _ => return Err(TraceError::InvalidData("check status tag")),
            };
            checks.push(Check {
                id: id.into(),
                status,
            });
        }
        Ok(checks)
    }
    fn finish(self) -> Result<(), TraceError> {
        if self.pos == self.data.len() {
            Ok(())
        } else {
            Err(TraceError::InvalidData("trailing record fields"))
        }
    }
}

struct LimitedReader<'a, R> {
    reader: R,
    consumed: u64,
    limits: &'a ReadLimits,
}

impl<R: Read> LimitedReader<'_, R> {
    fn exact(&mut self, buffer: &mut [u8]) -> Result<(), TraceError> {
        self.consumed = self
            .consumed
            .checked_add(buffer.len() as u64)
            .ok_or(TraceError::LimitExceeded("total bytes"))?;
        if self.consumed > self.limits.max_total_bytes {
            return Err(TraceError::LimitExceeded("total bytes"));
        }
        self.reader.read_exact(buffer).map_err(|err| {
            if err.kind() == io::ErrorKind::UnexpectedEof {
                TraceError::Truncated
            } else {
                TraceError::Io(err)
            }
        })
    }
    fn frame(&mut self) -> Result<(u8, Vec<u8>), TraceError> {
        let mut head = [0u8; 5];
        self.exact(&mut head)?;
        let len = u32::from_le_bytes(head[1..].try_into().unwrap()) as usize;
        limit(len, self.limits.max_frame_bytes, "frame bytes")?;
        if len as u64 + 4 > self.limits.max_total_bytes.saturating_sub(self.consumed) {
            return Err(TraceError::LimitExceeded("total bytes"));
        }
        let mut payload = reserve_vec(len)?;
        payload.resize(len, 0);
        self.exact(&mut payload)?;
        let mut checksum = [0u8; 4];
        self.exact(&mut checksum)?;
        if u32::from_le_bytes(checksum) != crc32(&head, &payload) {
            return Err(TraceError::ChecksumMismatch);
        }
        Ok((head[0], payload))
    }
    fn require_eof(&mut self) -> Result<(), TraceError> {
        let mut byte = [0u8; 1];
        loop {
            match self.reader.read(&mut byte) {
                Ok(0) => return Ok(()),
                Ok(_) => return Err(TraceError::InvalidData("trailing data after footer")),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(TraceError::Io(e)),
            }
        }
    }
}

fn write_frame(writer: &mut impl Write, tag: u8, payload: &[u8]) -> Result<(), TraceError> {
    let len = u32::try_from(payload.len()).map_err(|_| TraceError::LimitExceeded("frame bytes"))?;
    let mut head = [0u8; 5];
    head[0] = tag;
    head[1..].copy_from_slice(&len.to_le_bytes());
    writer.write_all(&head)?;
    writer.write_all(payload)?;
    writer.write_all(&crc32(&head, payload).to_le_bytes())?;
    Ok(())
}

const fn crc_tables() -> [[u32; 256]; 8] {
    let mut tables = [[0u32; 256]; 8];
    let mut i = 0;
    while i < 256 {
        let mut crc = i as u32;
        let mut bit = 0;
        while bit < 8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xedb88320
            } else {
                crc >> 1
            };
            bit += 1;
        }
        tables[0][i] = crc;
        i += 1;
    }
    let mut slice = 1;
    while slice < 8 {
        let mut i = 0;
        while i < 256 {
            let previous = tables[slice - 1][i];
            tables[slice][i] = (previous >> 8) ^ tables[0][(previous & 255) as usize];
            i += 1;
        }
        slice += 1;
    }
    tables
}

const CRC_TABLES: [[u32; 256]; 8] = crc_tables();

fn crc32_update(mut crc: u32, bytes: &[u8]) -> u32 {
    // Slicing by eight removes the byte-at-a-time dependency chain for large
    // frames. Explicit little-endian reads work with any alignment and target
    // endianness; no hardware-specific instructions or unsafe code are needed.
    let (chunks, remainder) = bytes.as_chunks::<8>();
    for chunk in chunks {
        let word = u64::from_le_bytes(*chunk) ^ u64::from(crc);
        crc = CRC_TABLES[7][(word & 255) as usize]
            ^ CRC_TABLES[6][((word >> 8) & 255) as usize]
            ^ CRC_TABLES[5][((word >> 16) & 255) as usize]
            ^ CRC_TABLES[4][((word >> 24) & 255) as usize]
            ^ CRC_TABLES[3][((word >> 32) & 255) as usize]
            ^ CRC_TABLES[2][((word >> 40) & 255) as usize]
            ^ CRC_TABLES[1][((word >> 48) & 255) as usize]
            ^ CRC_TABLES[0][(word >> 56) as usize];
    }
    for &byte in remainder {
        crc = (crc >> 8) ^ CRC_TABLES[0][((crc as u8) ^ byte) as usize];
    }
    crc
}

fn crc32(header: &[u8], payload: &[u8]) -> u32 {
    !crc32_update(crc32_update(u32::MAX, header), payload)
}

#[cfg(test)]
mod crc_tests {
    use super::crc32;

    fn bitwise(header: &[u8], payload: &[u8]) -> u32 {
        let mut crc = u32::MAX;
        for &byte in header.iter().chain(payload) {
            crc ^= u32::from(byte);
            for _ in 0..8 {
                crc = (crc >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(crc & 1));
            }
        }
        !crc
    }

    #[test]
    fn standard_crc_and_every_short_split_match_bitwise_reference() {
        assert_eq!(crc32(&[], &[]), 0);
        assert_eq!(crc32(b"1234", b"56789"), 0xcbf43926);
        let bytes: Vec<u8> = (0..80).map(|i| (i * 37 + 91) as u8).collect();
        for offset in 0..8 {
            for length in 0..=64 {
                let data = &bytes[offset..offset + length];
                for split in 0..=length {
                    let (header, payload) = data.split_at(split);
                    assert_eq!(crc32(header, payload), bitwise(header, payload));
                }
            }
        }
    }

    #[test]
    fn unaligned_random_frames_match_bitwise_reference() {
        let mut random = 0x6a09e667f3bcc909_u64;
        let bytes: Vec<u8> = (0..16_400)
            .map(|_| {
                random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
                (random >> 32) as u8
            })
            .collect();
        for length in [
            65, 127, 128, 129, 255, 256, 257, 1_023, 1_024, 1_025, 8_191, 8_192, 8_193, 16_385,
        ] {
            for offset in 0..8 {
                let data = &bytes[offset..offset + length];
                for split in [0, 1, 5, 7, 8, 9, length / 2, length] {
                    let (header, payload) = data.split_at(split);
                    assert_eq!(crc32(header, payload), bitwise(header, payload));
                }
            }
        }
    }
}
