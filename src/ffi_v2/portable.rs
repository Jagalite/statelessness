//! Bounded transport between the native trace codec and portable JSON adapters.
//! This is a structural codec, not a recording or replay implementation. The
//! packet preserves binary run configuration; JSON adapters explicitly omit it.
use crate::model::{Check, CheckStatus, Disposition, ModelError, ModelMetadata};
use crate::trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep};

const MAGIC: &[u8; 8] = b"STV2P001";
fn error(message: impl Into<String>) -> ModelError {
    ModelError::new(message)
}

struct Writer {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Writer {
    fn raw(&mut self, data: &[u8]) -> Result<(), ModelError> {
        if data.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(error("portable packet byte limit"));
        }
        self.bytes
            .try_reserve(data.len())
            .map_err(|_| error("portable packet allocation"))?;
        self.bytes.extend_from_slice(data);
        Ok(())
    }
    fn byte(&mut self, value: u8) -> Result<(), ModelError> {
        self.raw(&[value])
    }
    fn count(&mut self, value: usize) -> Result<(), ModelError> {
        self.raw(
            &u32::try_from(value)
                .map_err(|_| error("portable count overflow"))?
                .to_le_bytes(),
        )
    }
    fn blob(&mut self, value: &[u8]) -> Result<(), ModelError> {
        self.count(value.len())?;
        self.raw(value)
    }
    fn text(&mut self, value: &str) -> Result<(), ModelError> {
        self.blob(value.as_bytes())
    }
    fn checks(&mut self, values: &[Check]) -> Result<(), ModelError> {
        self.count(values.len())?;
        for check in values {
            self.text(check.id.as_str())?;
            let (tag, details) = match &check.status {
                CheckStatus::Passed => (0, ""),
                CheckStatus::Failed(s) => (1, s.as_str()),
                CheckStatus::Skipped(s) => (2, s.as_str()),
            };
            self.byte(tag)?;
            self.text(details)?;
        }
        Ok(())
    }
}

struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
    items: usize,
    limits: &'a ReadLimits,
}
impl<'a> Reader<'a> {
    fn raw(&mut self, len: usize) -> Result<&'a [u8], ModelError> {
        if len > self.data.len().saturating_sub(self.offset) {
            return Err(error("truncated portable packet"));
        }
        let value = &self.data[self.offset..self.offset + len];
        self.offset += len;
        Ok(value)
    }
    fn byte(&mut self) -> Result<u8, ModelError> {
        Ok(self.raw(1)?[0])
    }
    fn u32(&mut self) -> Result<u32, ModelError> {
        Ok(u32::from_le_bytes(
            self.raw(4)?.try_into().expect("four bytes"),
        ))
    }
    fn count(&mut self, maximum: usize) -> Result<usize, ModelError> {
        let value = self.u32()? as usize;
        if value > maximum || value > self.limits.max_items.saturating_sub(self.items) {
            return Err(error("portable packet item limit"));
        }
        self.items += value;
        Ok(value)
    }
    fn blob(&mut self, maximum: usize) -> Result<Vec<u8>, ModelError> {
        let len = self.u32()? as usize;
        if len > maximum {
            return Err(error("portable packet blob limit"));
        }
        let raw = self.raw(len)?;
        let mut value = Vec::new();
        value
            .try_reserve_exact(len)
            .map_err(|_| error("portable packet allocation"))?;
        value.extend_from_slice(raw);
        Ok(value)
    }
    fn text(&mut self) -> Result<String, ModelError> {
        String::from_utf8(self.blob(self.limits.max_string_bytes)?)
            .map_err(|_| error("portable packet invalid UTF-8"))
    }
    fn checks(&mut self) -> Result<Vec<Check>, ModelError> {
        let count = self.count(self.limits.max_checks_per_step)?;
        let mut checks = Vec::new();
        // Do not reserve an untrusted count before proving its bytes exist.
        for _ in 0..count {
            let id = self.text()?;
            let tag = self.byte()?;
            let details = self.text()?;
            let check = match tag {
                0 if details.is_empty() => Check::passed(id),
                1 => Check::failed(id, details),
                2 => Check::skipped(id, details),
                _ => return Err(error("portable packet invalid check")),
            };
            checks.push(check);
        }
        Ok(checks)
    }
}

pub(super) fn binary_to_packet(
    artifact: &[u8],
    limits: &ReadLimits,
) -> Result<Vec<u8>, ModelError> {
    let trace = Trace::read_from(artifact, limits).map_err(|e| error(e.to_string()))?;
    let mut w = Writer {
        bytes: Vec::new(),
        maximum: usize::try_from(limits.max_total_bytes).unwrap_or(usize::MAX),
    };
    w.raw(MAGIC)?;
    w.text(&trace.metadata.name)?;
    w.text(&trace.metadata.build)?;
    for version in [
        trace.metadata.model_version,
        trace.metadata.properties_version,
        trace.metadata.codec_version,
    ] {
        w.raw(&version.to_le_bytes())?;
    }
    w.text(&trace.config.strategy)?;
    match trace.config.seed {
        None => w.byte(0)?,
        Some(seed) => {
            w.byte(1)?;
            w.raw(&seed.to_le_bytes())?;
        }
    }
    w.count(trace.config.parameters.len())?;
    for (key, value) in &trace.config.parameters {
        w.text(key)?;
        w.text(value)?;
    }
    w.blob(&trace.initial_state)?;
    w.checks(&trace.initial_checks)?;
    w.count(trace.steps.len())?;
    for step in &trace.steps {
        w.blob(&step.input)?;
        let (tag, reason) = match &step.disposition {
            Disposition::Accepted => (0, ""),
            Disposition::Rejected(s) => (1, s.as_str()),
            Disposition::Ignored(s) => (2, s.as_str()),
        };
        w.byte(tag)?;
        w.text(reason)?;
        w.count(step.outputs.len())?;
        for output in &step.outputs {
            w.blob(output)?;
        }
        w.blob(&step.post_state)?;
        w.checks(&step.checks)?;
    }
    let (tag, message) = match &trace.termination {
        Termination::Completed => (0, ""),
        Termination::PropertyFailed => (1, ""),
        Termination::StepLimit => (2, ""),
        Termination::Interrupted => (3, ""),
        Termination::ModelError(s) => (4, s.as_str()),
    };
    w.byte(tag)?;
    w.text(message)?;
    Ok(w.bytes)
}

pub(super) fn packet_to_binary(packet: &[u8], limits: &ReadLimits) -> Result<Vec<u8>, ModelError> {
    if packet.len() as u64 > limits.max_total_bytes {
        return Err(error("portable packet byte limit"));
    }
    let mut r = Reader {
        data: packet,
        offset: 0,
        items: 0,
        limits,
    };
    if r.raw(MAGIC.len())? != MAGIC {
        return Err(error("unsupported portable packet version"));
    }
    let name = r.text()?;
    let build = r.text()?;
    let metadata = ModelMetadata {
        name,
        build,
        model_version: r.u32()?,
        properties_version: r.u32()?,
        codec_version: r.u32()?,
    };
    let strategy = r.text()?;
    let seed = match r.byte()? {
        0 => None,
        1 => Some(u64::from_le_bytes(
            r.raw(8)?.try_into().expect("eight bytes"),
        )),
        _ => return Err(error("invalid portable seed tag")),
    };
    let mut parameters = Vec::new();
    for _ in 0..r.count(limits.max_parameters)? {
        parameters.push((r.text()?, r.text()?));
    }
    let config = RunConfig {
        strategy,
        seed,
        parameters,
    };
    let initial_state = r.blob(limits.max_blob_bytes)?;
    let initial_checks = r.checks()?;
    let mut steps = Vec::new();
    for _ in 0..r.count(limits.max_steps)? {
        let input = r.blob(limits.max_blob_bytes)?;
        let tag = r.byte()?;
        let reason = r.text()?;
        let disposition = match tag {
            0 if reason.is_empty() => Disposition::Accepted,
            1 => Disposition::Rejected(reason),
            2 => Disposition::Ignored(reason),
            _ => return Err(error("invalid portable disposition")),
        };
        let mut outputs = Vec::new();
        for _ in 0..r.count(limits.max_outputs_per_step)? {
            outputs.push(r.blob(limits.max_blob_bytes)?);
        }
        let post_state = r.blob(limits.max_blob_bytes)?;
        let checks = r.checks()?;
        steps.push(TraceStep {
            input,
            disposition,
            outputs,
            post_state,
            checks,
        });
    }
    let tag = r.byte()?;
    let message = r.text()?;
    let termination = match tag {
        0 if message.is_empty() => Termination::Completed,
        1 if message.is_empty() => Termination::PropertyFailed,
        2 if message.is_empty() => Termination::StepLimit,
        3 if message.is_empty() => Termination::Interrupted,
        4 => Termination::ModelError(message),
        _ => return Err(error("invalid portable termination")),
    };
    if r.offset != packet.len() {
        return Err(error("trailing portable packet bytes"));
    }
    let trace = Trace {
        metadata,
        config,
        initial_state,
        initial_checks,
        steps,
        termination,
    };
    let mut bytes = Vec::new();
    trace
        .write_with_limits(&mut bytes, limits)
        .map_err(|e| error(e.to_string()))?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_roundtrip_includes_run_configuration_and_empty_unicode_checks() {
        let trace = Trace {
            metadata: ModelMetadata {
                name: "".into(),
                build: "é".into(),
                model_version: 0,
                properties_version: 2,
                codec_version: 3,
            },
            config: RunConfig {
                strategy: "custom".into(),
                seed: Some(u64::MAX),
                parameters: vec![("é".into(), "e\u{301}".into())],
            },
            initial_state: vec![0, 255],
            initial_checks: vec![Check::passed("")],
            steps: vec![],
            termination: Termination::Completed,
        };
        let limits = ReadLimits::default();
        let mut bytes = Vec::new();
        trace.write_to(&mut bytes).unwrap();
        let packet = binary_to_packet(&bytes, &limits).unwrap();
        assert_eq!(packet_to_binary(&packet, &limits).unwrap(), bytes);
        for end in 0..packet.len() {
            assert!(packet_to_binary(&packet[..end], &limits).is_err());
        }
        let mut trailing = packet.clone();
        trailing.push(0);
        assert!(packet_to_binary(&trailing, &limits).is_err());
        let mut limited = limits;
        limited.max_total_bytes = 8;
        assert!(packet_to_binary(&packet, &limited).is_err());
    }

    #[test]
    fn structural_conversion_preserves_invalid_semantics_for_replay_identity_order() {
        use crate::execution::{ReplayOptions, ReplayOutcome, record, replay, validate_recording};
        let model = crate::demo::RequestModel::fixed();
        let mut trace = record(&model, std::iter::empty(), RunConfig::default(), 0).unwrap();
        trace.termination = Termination::PropertyFailed;
        assert!(validate_recording(&trace).is_err());
        for incompatible in [false, true] {
            let mut candidate = trace.clone();
            if incompatible {
                candidate.metadata.name.push_str("-different-model");
            }
            let mut original = Vec::new();
            candidate.write_to(&mut original).unwrap();
            let limits = ReadLimits::default();
            let packet = binary_to_packet(&original, &limits).unwrap();
            let converted = packet_to_binary(&packet, &limits).unwrap();
            assert_eq!(converted, original);
            let decoded = Trace::read_from(converted.as_slice(), &limits).unwrap();
            let outcome = replay(&model, &decoded, ReplayOptions::default());
            if incompatible {
                assert!(matches!(
                    outcome.unwrap().outcome,
                    ReplayOutcome::Incompatible { .. }
                ));
            } else {
                assert!(outcome.is_err());
            }
        }
    }
}
