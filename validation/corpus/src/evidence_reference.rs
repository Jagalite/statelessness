//! Actual Rust causal-evidence v2 wire, replay, recorder and export reference.
use serde_json::{Value, json};
use stateless::{
    evidence::*,
    execution::{self, CheckPolicy, ReplayOptions, ReplayOutcome, ReplayReport},
    explore::CheckPhase,
    model::*,
    monitor::RecorderOptions,
    trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep},
};
use std::{
    cell::{Cell, RefCell},
    io::{self, Read, Write},
    path::Path,
};
type Result<T> = std::result::Result<T, String>;
type R<T> = std::result::Result<T, ModelError>;
const MAX_JSON: u64 = 4 * 1024 * 1024;
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing string {key}"))
}
fn number(v: &Value, key: &str) -> Result<u64> {
    v.get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("missing unsigned integer {key}"))
}
fn array<'a>(v: &'a Value, key: &str) -> Result<&'a [Value]> {
    v.get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing array {key}"))
}
fn decode_hex(s: &str) -> Result<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return Err("odd hex length".into());
    }
    s.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let digit = |b: u8| match b {
                b'0'..=b'9' => Ok(b - b'0'),
                b'a'..=b'f' => Ok(b - b'a' + 10),
                _ => Err("noncanonical hex".to_owned()),
            };
            Ok(digit(pair[0])? * 16 + digit(pair[1])?)
        })
        .collect()
}
fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}
fn json_file(path: impl AsRef<Path>) -> Result<Value> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(MAX_JSON + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_JSON {
        return Err("qualification JSON byte limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
fn no_reason(v: &Value) -> Result<()> {
    if !v.get("reason").is_some_and(Value::is_null) {
        return Err("unexpected reason on fieldless variant".into());
    }
    Ok(())
}
fn checks(v: &Value, key: &str) -> Result<Vec<Check>> {
    array(v, key)?
        .iter()
        .map(|c| {
            let id = string(c, "id")?.to_owned();
            let check = match string(c, "status")? {
                "passed" => {
                    no_reason(c)?;
                    Check::passed(id)
                }
                "failed" => Check::failed(id, string(c, "reason")?),
                "skipped" => Check::skipped(id, string(c, "reason")?),
                _ => return Err("unknown check status".into()),
            };
            Ok(check)
        })
        .collect()
}
fn from_json(v: &Value) -> Result<Trace> {
    let m = &v["metadata"];
    let c = &v["config"];
    let version = |key| -> Result<u32> {
        u32::try_from(number(m, key)?).map_err(|_| format!("metadata {key} exceeds u32"))
    };
    let seed = match c.get("seed") {
        Some(Value::Null) => None,
        Some(Value::String(s)) => {
            let n = s
                .parse::<u64>()
                .map_err(|_| "invalid seed decimal".to_owned())?;
            if n.to_string() != *s {
                return Err("noncanonical seed decimal".into());
            }
            Some(n)
        }
        _ => return Err("seed must be null or decimal string".into()),
    };
    let parameters = array(c, "parameters")?
        .iter()
        .map(|p| {
            let pair = p
                .as_array()
                .filter(|p| p.len() == 2)
                .ok_or_else(|| "invalid parameter pair".to_owned())?;
            Ok((
                pair[0]
                    .as_str()
                    .ok_or("parameter key must be string")?
                    .to_owned(),
                pair[1]
                    .as_str()
                    .ok_or("parameter value must be string")?
                    .to_owned(),
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    let steps = array(v, "steps")?
        .iter()
        .map(|s| {
            let d = &s["disposition"];
            let disposition = match string(d, "kind")? {
                "accepted" => {
                    no_reason(d)?;
                    Disposition::Accepted
                }
                "rejected" => Disposition::Rejected(string(d, "reason")?.into()),
                "ignored" => Disposition::Ignored(string(d, "reason")?.into()),
                _ => return Err("unknown disposition".into()),
            };
            Ok(TraceStep {
                input: decode_hex(string(s, "input")?)?,
                disposition,
                outputs: array(s, "outputs")?
                    .iter()
                    .map(|b| decode_hex(b.as_str().ok_or("output hex must be string")?))
                    .collect::<Result<Vec<_>>>()?,
                post_state: decode_hex(string(s, "post_state")?)?,
                checks: checks(s, "checks")?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let t = &v["termination"];
    let termination = match string(t, "kind")? {
        "completed" => {
            no_reason(t)?;
            Termination::Completed
        }
        "property_failed" => {
            no_reason(t)?;
            Termination::PropertyFailed
        }
        "step_limit" => {
            no_reason(t)?;
            Termination::StepLimit
        }
        "interrupted" => {
            no_reason(t)?;
            Termination::Interrupted
        }
        "model_error" => Termination::ModelError(string(t, "reason")?.into()),
        _ => return Err("unknown termination".into()),
    };
    Ok(Trace {
        metadata: ModelMetadata {
            name: string(m, "name")?.into(),
            model_version: version("model_version")?,
            properties_version: version("properties_version")?,
            codec_version: version("codec_version")?,
            build: string(m, "build")?.into(),
        },
        config: RunConfig {
            strategy: string(c, "strategy")?.into(),
            seed,
            parameters,
        },
        initial_state: decode_hex(string(v, "initial_state")?)?,
        initial_checks: checks(v, "initial_checks")?,
        steps,
        termination,
    })
}
fn checks_json(checks: &[Check]) -> Vec<Value> {
    checks
        .iter()
        .map(|c| {
            let (status, reason) = match &c.status {
                CheckStatus::Passed => ("passed", None),
                CheckStatus::Failed(s) => ("failed", Some(s)),
                CheckStatus::Skipped(s) => ("skipped", Some(s)),
            };
            json!({"id":c.id.as_str(),"status":status,"reason":reason})
        })
        .collect()
}
fn to_json(t: &Trace) -> Value {
    let (kind, reason) = match &t.termination {
        Termination::Completed => ("completed", None),
        Termination::PropertyFailed => ("property_failed", None),
        Termination::StepLimit => ("step_limit", None),
        Termination::Interrupted => ("interrupted", None),
        Termination::ModelError(s) => ("model_error", Some(s)),
    };
    let steps = t.steps.iter().map(|s| {
        let (kind,reason) = match &s.disposition { Disposition::Accepted => ("accepted",None), Disposition::Rejected(s) => ("rejected",Some(s)), Disposition::Ignored(s) => ("ignored",Some(s)) };
        json!({"input":hex(&s.input),"disposition":{"kind":kind,"reason":reason},"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"checks":checks_json(&s.checks)})
    }).collect::<Vec<_>>();
    json!({"metadata":{"name":t.metadata.name,"model_version":t.metadata.model_version,"properties_version":t.metadata.properties_version,"codec_version":t.metadata.codec_version,"build":t.metadata.build},
        "config":{"strategy":t.config.strategy,"seed":t.config.seed.map(|n|n.to_string()),"parameters":t.config.parameters},
        "initial_state":hex(&t.initial_state),"initial_checks":checks_json(&t.initial_checks),"steps":steps,"termination":{"kind":kind,"reason":reason}})
}
fn unsigned(v: &Value) -> Result<u64> {
    if let Some(s) = v.as_str() {
        let n = s.parse::<u64>().map_err(|_| "u64 decimal")?;
        if n.to_string() != s {
            return Err("noncanonical u64 decimal".into());
        }
        Ok(n)
    } else {
        v.as_u64().ok_or("unsigned integer".into())
    }
}
fn optional_string(v: &Value, k: &str) -> Result<Option<String>> {
    if v[k].is_null() {
        Ok(None)
    } else {
        Ok(Some(string(v, k)?.into()))
    }
}
fn optional_unsigned(v: &Value, k: &str) -> Result<Option<u64>> {
    if v[k].is_null() {
        Ok(None)
    } else {
        Ok(Some(unsigned(&v[k])?))
    }
}
fn provenance_from_json(v: &Value) -> Result<Provenance> {
    Ok(Provenance {
        run: string(v, "run")?.into(),
        epoch: unsigned(&v["epoch"])?,
        manifest: optional_string(v, "manifest")?,
        source: optional_string(v, "source")?,
        environment: array(v, "environment")?
            .iter()
            .map(|p| {
                let a = p
                    .as_array()
                    .filter(|a| a.len() == 2)
                    .ok_or("environment pair")?;
                Ok((
                    a[0].as_str().ok_or("environment name")?.into(),
                    a[1].as_str().ok_or("environment value")?.into(),
                ))
            })
            .collect::<Result<_>>()?,
    })
}
fn context_from_json(v: &Value) -> Result<CausalContext> {
    Ok(CausalContext {
        machine: optional_string(v, "machine")?,
        generation: optional_unsigned(v, "generation")?,
        correlation: optional_string(v, "correlation")?,
        logical_time: optional_unsigned(v, "logical_time")?,
        causes: array(v, "causes")?
            .iter()
            .map(|o| {
                Ok(OutputOrigin {
                    sequence: unsigned(&o["sequence"])?,
                    output: u32::try_from(unsigned(&o["output"])?).map_err(|_| "output u32")?,
                })
            })
            .collect::<Result<_>>()?,
    })
}
fn evidence_from_json(v: &Value) -> Result<TraceV2> {
    Ok(TraceV2 {
        trace: from_json(&v["trace"])?,
        checkpoint_after: unsigned(&v["checkpoint_after"])?,
        provenance: provenance_from_json(&v["provenance"])?,
        steps: array(v, "steps")?
            .iter()
            .map(|s| {
                Ok(StepEvidence {
                    sequence: unsigned(&s["sequence"])?,
                    state_check_count: u32::try_from(unsigned(&s["state_check_count"])?)
                        .map_err(|_| "state count u32")?,
                    causal: context_from_json(&s["causal"])?,
                })
            })
            .collect::<Result<_>>()?,
    })
}
fn provenance_json(v: &Provenance) -> Value {
    json!({"run":v.run,"epoch":v.epoch.to_string(),"manifest":v.manifest,"source":v.source,"environment":v.environment})
}
fn context_json(v: &CausalContext) -> Value {
    json!({"machine":v.machine,"generation":v.generation.map(|n|n.to_string()),"correlation":v.correlation,"logical_time":v.logical_time.map(|n|n.to_string()),"causes":v.causes.iter().map(|o|json!({"sequence":o.sequence.to_string(),"output":o.output})).collect::<Vec<_>>()})
}
fn evidence_json(v: &TraceV2) -> Value {
    json!({"trace":to_json(&v.trace),"checkpoint_after":v.checkpoint_after.to_string(),"provenance":provenance_json(&v.provenance),"steps":v.steps.iter().map(|s|json!({"sequence":s.sequence.to_string(),"state_check_count":s.state_check_count,"causal":context_json(&s.causal)})).collect::<Vec<_>>()})
}
fn evidence_limits(v: &Value) -> Result<EvidenceLimits> {
    let mut l = EvidenceLimits::default();
    if v.is_null() {
        return Ok(l);
    }
    for (k, v) in v.as_object().ok_or("limits object")? {
        if k == "trace" {
            for (k, v) in v.as_object().ok_or("trace limits object")? {
                let n = unsigned(v)?;
                if k == "max_total_bytes" {
                    l.trace.max_total_bytes = n;
                    continue;
                }
                let n = usize::try_from(n).map_err(|_| "usize limit")?;
                match k.as_str() {
                    "max_frame_bytes" => l.trace.max_frame_bytes = n,
                    "max_blob_bytes" => l.trace.max_blob_bytes = n,
                    "max_string_bytes" => l.trace.max_string_bytes = n,
                    "max_steps" => l.trace.max_steps = n,
                    "max_checks_per_step" | "max_checks_per_batch" => {
                        l.trace.max_checks_per_step = n
                    }
                    "max_outputs_per_step" => l.trace.max_outputs_per_step = n,
                    "max_parameters" => l.trace.max_parameters = n,
                    "max_items" => l.trace.max_items = n,
                    _ => return Err(format!("unknown trace limit {k}")),
                }
            }
            continue;
        }
        let n = usize::try_from(unsigned(v)?).map_err(|_| "usize limit")?;
        match k.as_str() {
            "max_metadata_bytes" => l.max_metadata_bytes = n,
            "max_labels" => l.max_labels = n,
            "max_label_bytes" => l.max_label_bytes = n,
            "max_causes_per_step" => l.max_causes_per_step = n,
            "max_total_causes" => l.max_total_causes = n,
            _ => return Err(format!("unknown evidence limit {k}")),
        }
    }
    Ok(l)
}
fn phase_name(p: Option<CheckPhase>) -> Value {
    match p {
        Some(CheckPhase::InitialState) => json!("initial_state"),
        Some(CheckPhase::State) => json!("state"),
        Some(CheckPhase::Transition) => json!("transition"),
        None => Value::Null,
    }
}
fn report_json(r: &ReplayReport) -> Value {
    let (outcome, step, field) = match &r.outcome {
        ReplayOutcome::Exact => ("exact", None, None),
        ReplayOutcome::Diverged { step, field } => ("diverged", *step, Some(*field)),
        ReplayOutcome::Incompatible { .. } => ("incompatible", None, None),
    };
    json!({"outcome":outcome,"divergence_step":step,"divergence_field":field,"steps_verified":r.steps_verified,"failure_reproduced":r.failure_reproduced,"build_matches":r.build_matches})
}
struct Chunked<'a> {
    bytes: &'a [u8],
    width: usize,
}
impl Read for Chunked<'_> {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let n = out.len().min(self.width).min(self.bytes.len());
        out[..n].copy_from_slice(&self.bytes[..n]);
        self.bytes = &self.bytes[n..];
        Ok(n)
    }
}
struct FailingReader;
impl Read for FailingReader {
    fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
        Err(io::Error::other("fixture evidence read error"))
    }
}
struct FailingWriter;
impl Write for FailingWriter {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        Err(io::Error::other("fixture evidence write error"))
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn wire_case(v: &Value) -> Result<(usize, usize)> {
    let limits = evidence_limits(&v["limits"])?;
    let bytes = decode_hex(string(v, "bytes_hex")?)?;
    let read = Artifact::read(bytes.as_slice(), &limits);
    if v["valid"] != true {
        if let Some(value) = v.get("evidence") {
            let evidence = evidence_from_json(value)?;
            let mut bytes = vec![];
            if evidence.write(&mut bytes, &limits).is_ok() || !bytes.is_empty() {
                return Err(
                    "invalid semantic evidence wrote bytes before preflight rejection".into(),
                );
            }
        }
        let error = read.err().ok_or("invalid artifact accepted")?;
        if let Some(needle) = v["error_contains"].as_str()
            && (!error.to_string().contains(needle))
        {
            return Err(format!("error {error} lacks {needle}"));
        }
        return Ok((0, 0));
    }
    let artifact = read.map_err(|e| e.to_string())?;
    let expected = match string(v, "artifact_kind")? {
        "v1" => Artifact::V1(from_json(&v["trace"])?),
        "v2" => Artifact::V2(evidence_from_json(&v["evidence"])?),
        _ => return Err("artifact kind".into()),
    };
    if artifact != expected {
        return Err("decoded evidence differs from independently authored value".into());
    }
    let mut encoded = vec![];
    match &artifact {
        Artifact::V1(t) => t
            .write_with_limits(&mut encoded, &limits.trace)
            .map_err(|e| e.to_string())?,
        Artifact::V2(t) => {
            t.validate(&limits).map_err(|e| e.to_string())?;
            t.write(&mut encoded, &limits).map_err(|e| e.to_string())?;
            if t.write(FailingWriter, &limits).is_ok() {
                return Err("writer error ignored".into());
            }
        }
    }
    if encoded != bytes {
        return Err("canonical wire differs from independent golden".into());
    }
    for width in [1, 3, 7, 8192] {
        let decoded = Artifact::read(
            Chunked {
                bytes: &bytes,
                width,
            },
            &limits,
        )
        .map_err(|e| e.to_string())?;
        if decoded != artifact {
            return Err("chunked reader differs".into());
        }
    }
    if Artifact::read(FailingReader, &limits).is_ok() {
        return Err("reader error ignored".into());
    }
    for end in 0..bytes.len() {
        if Artifact::read(&bytes[..end], &limits).is_ok() {
            return Err(format!("accepted truncated prefix {end}"));
        }
    }
    let trace = artifact.trace();
    for sequence in 0..=trace.steps.len() + 1 {
        let count = if sequence == 0 {
            trace.initial_checks.len()
        } else {
            trace.steps.get(sequence - 1).map_or(0, |s| s.checks.len())
        };
        for index in 0..=count {
            let got = artifact.check_phase(sequence, index);
            let expected = match &expected {
                Artifact::V1(_) => {
                    if sequence == 0 && index < count {
                        Some(CheckPhase::InitialState)
                    } else {
                        None
                    }
                }
                Artifact::V2(e) => e.check_phase(sequence, index),
            };
            if got != expected {
                return Err("phase access mismatch".into());
            }
        }
    }
    Ok((bytes.len(), 4))
}
pub fn qualify(path: &Path) -> Result<Value> {
    let root = json_file(path)?;
    if root["profile"] != "causal-evidence-v2" {
        return Err("profile".into());
    }
    let mut valid = 0;
    let mut invalid = 0;
    let mut truncations = 0;
    let mut chunked = 0;
    for v in array(&root, "wire_cases")? {
        let (t, c) = wire_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        truncations += t;
        chunked += c;
        if v["valid"] == true {
            valid += 1
        } else {
            invalid += 1
        }
    }
    let mut replay_count = 0;
    for v in array(&root, "replay_cases")? {
        replay_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        replay_count += 1;
    }
    let mut export_count = 0;
    for v in array(&root, "export_cases")? {
        export_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        export_count += 1;
    }
    let mut recorder_count = 0;
    for v in array(&root, "recorder_cases")? {
        recorder_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        recorder_count += 1;
    }
    Ok(
        json!({"profile":"causal-evidence-v2","valid_wire":valid,"invalid_wire":invalid,"truncations":truncations,"chunked_reads":chunked,"replay_cases":replay_count,"export_cases":export_count,"recorder_cases":recorder_count,"rust_layout":{"usize_bits":usize::BITS,"String":std::mem::size_of::<String>(),"StringPair":std::mem::size_of::<(String,String)>(),"OutputOrigin":std::mem::size_of::<OutputOrigin>(),"StepEvidence":std::mem::size_of::<StepEvidence>()}}),
    )
}
struct Fixture {
    config: Value,
    state_calls: Cell<usize>,
    events: RefCell<Vec<String>>,
}
impl Fixture {
    fn new(config: Value) -> Self {
        Self {
            config,
            state_calls: Cell::new(0),
            events: RefCell::new(vec![]),
        }
    }
    fn event(&self, e: String) -> R<()> {
        self.events.borrow_mut().push(e.clone());
        if self.config["error_at"].as_str() == Some(&e) {
            Err(ModelError::new("fixture causal error"))
        } else {
            Ok(())
        }
    }
    fn delta(&self, k: &str) -> i32 {
        self.config[k].as_i64().unwrap_or(0) as i32
    }
    fn decode(&self, k: &str, b: &[u8]) -> R<i32> {
        self.event(format!("decode_{k}:{}", hex(b)))?;
        std::str::from_utf8(b)
            .map_err(|e| ModelError::new(e.to_string()))?
            .parse()
            .map_err(|_| ModelError::new("invalid decimal"))
    }
    fn encode(&self, k: &str, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.event(format!("encode_{k}:{n}"))?;
        if self.config["encode_error"].as_str() == Some(k) {
            return Err(ModelError::new("fixture causal encode error"));
        }
        for b in n.to_string().as_bytes() {
            o.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
}
impl Model for Fixture {
    type State = i32;
    type Input = i32;
    type Output = i32;
    fn metadata(&self) -> ModelMetadata {
        self.events.borrow_mut().push("metadata".into());
        ModelMetadata {
            name: self.config["name"]
                .as_str()
                .unwrap_or("causal-fixture")
                .into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: self.config["build"]
                .as_str()
                .unwrap_or("candidate-v1")
                .into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        self.event("initial_state".into())?;
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, i32>> {
        self.event(format!("step:{s}:{i}"))?;
        let base = s + i;
        Ok(Transition::accepted(
            base + self.delta("state_delta"),
            vec![base + self.delta("output_delta")],
        ))
    }
    fn check_state(&self, _: &i32) -> R<Vec<Check>> {
        Err(ModelError::new("legacy checker called"))
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.event(format!("check_state:{s}"))?;
        let call = self.state_calls.get();
        self.state_calls.set(call + 1);
        if call == 0 || self.config["phase_shift"] != true {
            c.push(if self.config["fail_at"].as_i64() == Some(*s as i64) {
                Check::failed("s", "at threshold")
            } else {
                Check::passed("s")
            });
        }
        Ok(())
    }
    fn check_transition_into(
        &self,
        b: &i32,
        i: &i32,
        t: &TransitionRef<'_, i32, i32>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.event(format!("check_transition:{b}:{i}:{}", t.state))?;
        if self.config["phase_shift"] == true {
            c.push(Check::passed("s"));
        }
        c.push(Check::passed("t"));
        Ok(())
    }
}
impl ModelCodec for Fixture {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy state encoder called"))
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy input encoder called"))
    }
    fn encode_output(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy output encoder called"))
    }
    fn encode_state_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("state", n, o)
    }
    fn encode_input_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("input", n, o)
    }
    fn encode_output_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("output", n, o)
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        self.decode("state", b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        self.decode("input", b)
    }
}
fn phase_rows(v: &TraceV2) -> Value {
    json!(v.steps.iter().map(|s|json!({"sequence":s.sequence.to_string(),"state_check_count":s.state_check_count,"causal":context_json(&s.causal)})).collect::<Vec<_>>())
}
fn replay_case(v: &Value) -> Result<()> {
    let evidence = evidence_from_json(&v["evidence"])?;
    let limits = evidence_limits(&v["limits"])?;
    let model = Fixture::new(v["model"].clone());
    let expected = &v["expected"];
    let options = ReplayOptions {
        allow_build_mismatch: v["allow_build_mismatch"] == true,
    };
    let result = match string(v, "operation")? {
        "replay" => evidence
            .replay(&model, options, &limits)
            .map(|report| json!({"report":report_json(&report),"steps":null})),
        "upgrade" => match TraceV2::verify_upgrade(
            &model,
            evidence.trace.clone(),
            evidence.provenance.clone(),
            evidence.checkpoint_after,
            options,
            &limits,
        ) {
            Err(e) => Err(e),
            Ok(value) => {
                if value.trace != evidence.trace
                    || value.provenance != evidence.provenance
                    || value.checkpoint_after != evidence.checkpoint_after
                {
                    return Err("upgrade changed original retained evidence".into());
                }
                value.validate(&limits).map_err(|e| e.to_string())?;
                let mut bytes = vec![];
                value
                    .write(&mut bytes, &limits)
                    .map_err(|e| e.to_string())?;
                if Artifact::read(bytes.as_slice(), &limits).map_err(|e| e.to_string())?
                    != Artifact::V2(value.clone())
                {
                    return Err("upgrade read/write mismatch".into());
                }
                Ok(json!({"report":null,"steps":phase_rows(&value)}))
            }
        },
        _ => return Err("operation".into()),
    };
    if string(v, "operation")? == "replay"
        && evidence.validate(&limits).is_err()
        && !model.events.borrow().is_empty()
    {
        return Err("invalid evidence invoked model before validation".into());
    }
    if v["case"] == "upgrade-invalid-provenance-after-replay"
        && model
            .events
            .borrow()
            .iter()
            .filter(|e| e.starts_with("step:"))
            .count()
            != evidence.trace.steps.len()
    {
        return Err("upgrade validated provenance before actual replay".into());
    }
    if model.events.borrow().iter().any(|e| e == "initial_state") {
        return Err("replay called initial_state".into());
    }
    match result {
        Err(error) => {
            let needle = string(expected, "error_contains")?;
            if !error.to_string().contains(needle) {
                return Err(format!("error {error} lacks {needle}"));
            }
        }
        Ok(actual) => {
            if !expected["error_contains"].is_null() {
                return Err("expected replay error".into());
            }
            for key in ["report", "steps"] {
                if let Some(want) = expected.get(key)
                    && (actual[key] != *want)
                {
                    return Err(format!("{key}: expected {want}, actual {}", actual[key]));
                }
            }
        }
    }
    Ok(())
}
fn export_case(v: &Value) -> Result<()> {
    let evidence = evidence_from_json(&v["evidence"])?;
    let artifact = match string(v, "artifact_kind")? {
        "v1" => Artifact::V1(evidence.trace),
        "v2" => Artifact::V2(evidence),
        _ => return Err("export artifact kind".into()),
    };
    let p = &v["policy"];
    let ids = array(p, "property_ids")?
        .iter()
        .map(|v| v.as_str().ok_or("property ID string"))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let policy = ExportPolicy {
        max_steps: usize::try_from(unsigned(&p["max_steps"])?).map_err(|_| "steps usize")?,
        max_bytes: usize::try_from(unsigned(&p["max_bytes"])?).map_err(|_| "bytes usize")?,
        property_ids: &ids,
    };
    let result = diagnostic_json(&artifact, &policy);
    let expected = &v["expected"];
    match result {
        Err(error) => {
            let needle = string(expected, "error_contains")?;
            if !error.to_string().contains(needle) {
                return Err(format!("diagnostic error {error} lacks {needle}"));
            }
        }
        Ok(bytes) => {
            if !expected["error_contains"].is_null() {
                return Err("expected export error".into());
            }
            if bytes != string(expected, "json_text")?.as_bytes() {
                return Err(format!(
                    "diagnostic text differs: {}",
                    String::from_utf8_lossy(&bytes)
                ));
            }
            let parsed: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if parsed["replayable"] != false || parsed["raw_payloads_included"] != false {
                return Err("diagnostic claims replayable/raw payload".into());
            }
            filesystem_exports(&artifact, &policy, &bytes)?;
        }
    }
    Ok(())
}
fn filesystem_exports(
    artifact: &Artifact,
    policy: &ExportPolicy<'_>,
    expected_diagnostic: &[u8],
) -> Result<()> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static INDEX: AtomicU64 = AtomicU64::new(0);
    let directory = std::env::temp_dir().join(format!(
        "stateless-evidence-reference-{}-{}",
        std::process::id(),
        INDEX.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
    let diagnostic = directory.join("diagnostic.json");
    let raw = directory.join("raw.bin");
    let result = (|| {
        export_diagnostic_new(artifact, &diagnostic, policy).map_err(|e| e.to_string())?;
        if std::fs::read(&diagnostic).map_err(|e| e.to_string())? != expected_diagnostic {
            return Err("diagnostic file bytes".into());
        }
        if export_diagnostic_new(artifact, &diagnostic, policy).is_ok() {
            return Err("diagnostic overwrote existing file".into());
        }
        if std::fs::read(&diagnostic).map_err(|e| e.to_string())? != expected_diagnostic {
            return Err("diagnostic overwrite mutated existing bytes".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if std::fs::metadata(&diagnostic)
                .map_err(|e| e.to_string())?
                .permissions()
                .mode()
                & 0o777
                != 0o600
            {
                return Err("diagnostic permissions not0600".into());
            }
        }
        if let Artifact::V2(evidence) = artifact {
            let limits = EvidenceLimits::default();
            if evidence.validate(&limits).is_ok() {
                evidence
                    .write_raw_new(&raw, &limits)
                    .map_err(|e| e.to_string())?;
                let expected = std::fs::read(&raw).map_err(|e| e.to_string())?;
                if Artifact::read(expected.as_slice(), &limits).map_err(|e| e.to_string())?
                    != *artifact
                {
                    return Err("raw filesystem roundtrip".into());
                }
                if evidence.write_raw_new(&raw, &limits).is_ok() {
                    return Err("raw export overwrote existing file".into());
                }
                if std::fs::read(&raw).map_err(|e| e.to_string())? != expected {
                    return Err("raw export changed existing file".into());
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if std::fs::metadata(&raw)
                        .map_err(|e| e.to_string())?
                        .permissions()
                        .mode()
                        & 0o777
                        != 0o600
                    {
                        return Err("raw permissions not0600".into());
                    }
                }
            }
        }
        Ok(())
    })();
    for path in [&diagnostic, &raw] {
        if path.exists() {
            std::fs::remove_file(path).map_err(|e| e.to_string())?;
        }
    }
    std::fs::remove_dir(&directory).map_err(|e| e.to_string())?;
    result
}
fn number_i32(v: &Value, k: &str) -> Result<i32> {
    v[k].as_i64()
        .and_then(|n| i32::try_from(n).ok())
        .ok_or_else(|| format!("invalid integer {k}"))
}
fn supplied_transition(v: &Value) -> Result<Transition<i32, i32>> {
    let d = &v["disposition"];
    let disposition = match string(d, "kind")? {
        "accepted" => Disposition::Accepted,
        "rejected" => Disposition::Rejected(string(d, "reason")?.into()),
        "ignored" => Disposition::Ignored(string(d, "reason")?.into()),
        _ => return Err("disposition".into()),
    };
    Ok(Transition {
        state: number_i32(v, "state")?,
        outputs: array(v, "outputs")?
            .iter()
            .map(|n| {
                n.as_i64()
                    .and_then(|n| i32::try_from(n).ok())
                    .ok_or("output integer".into())
            })
            .collect::<Result<_>>()?,
        disposition,
    })
}
fn policy(v: &Value) -> Result<CheckPolicy> {
    Ok(CheckPolicy {
        state_every: std::num::NonZeroU64::new(unsigned(&v["state_every"])?)
            .ok_or("zero period")?,
        transition_checks: v["transition_checks"]
            .as_bool()
            .ok_or("transition policy")?,
    })
}
fn config_from_json(v: &Value) -> Result<RunConfig> {
    Ok(RunConfig {
        strategy: string(v, "strategy")?.into(),
        seed: optional_unsigned(v, "seed")?,
        parameters: array(v, "parameters")?
            .iter()
            .map(|p| {
                let a = p
                    .as_array()
                    .filter(|a| a.len() == 2)
                    .ok_or("parameter pair")?;
                Ok((
                    a[0].as_str().ok_or("parameter key")?.into(),
                    a[1].as_str().ok_or("parameter value")?.into(),
                ))
            })
            .collect::<Result<_>>()?,
    })
}
fn recorder_options(v: &Value) -> Result<RecorderOptions> {
    Ok(RecorderOptions {
        max_steps: usize::try_from(unsigned(&v["max_steps"])?).map_err(|_| "maxsteps usize")?,
        max_retained_bytes: unsigned(&v["max_retained_bytes"])?,
        limits: evidence_limits(&json!({"trace":v["limits"]}))?.trace,
    })
}
fn checks_details(v: &[Check]) -> Value {
    json!(
        v.iter()
            .map(|c| {
                let (status, details) = match &c.status {
                    CheckStatus::Passed => ("passed", ""),
                    CheckStatus::Failed(s) => ("failed", s.as_str()),
                    CheckStatus::Skipped(s) => ("skipped", s.as_str()),
                };
                json!({"id":c.id.as_str(),"status":status,"details":details})
            })
            .collect::<Vec<_>>()
    )
}
fn decimal(b: &[u8]) -> Result<i32> {
    std::str::from_utf8(b)
        .map_err(|e| e.to_string())?
        .parse()
        .map_err(|_| "integer".into())
}
fn term_name(t: &Termination) -> &'static str {
    match t {
        Termination::Completed => "completed",
        Termination::PropertyFailed => "property_failed",
        Termination::Interrupted => "interrupted",
        Termination::StepLimit => "step_limit",
        Termination::ModelError(_) => "model_error",
    }
}
fn monitor_parameter<'a>(t: &'a Trace, k: &str) -> Result<&'a str> {
    let key = format!("stateless.monitor.{k}");
    t.config
        .parameters
        .iter()
        .find(|(key2, _)| *key2 == key)
        .map(|(_, v)| v.as_str())
        .ok_or_else(|| format!("missing {key}"))
}
fn recorder_state(r: &EvidenceRecorder, error: &Value) -> Result<Value> {
    let e = r.snapshot();
    let t = &e.trace;
    Ok(
        json!({"error_contains":error,"checkpoint_after":e.checkpoint_after.to_string(),"initial_state":decimal(&t.initial_state)?,"initial_checks":checks_details(&t.initial_checks),"steps":t.steps.iter().map(|s|Ok(json!({"input":decimal(&s.input)?,"post_state":decimal(&s.post_state)?,"outputs":s.outputs.iter().map(|b|decimal(b)).collect::<Result<Vec<_>>>()?,"checks":checks_details(&s.checks)}))).collect::<Result<Vec<_>>>()?,"evidence_steps":phase_rows(&e),"observed_steps":monitor_parameter(t,"observed_steps")?,"evicted_steps":monitor_parameter(t,"evicted_steps")?,"frozen":r.is_frozen(),"termination":term_name(&t.termination)}),
    )
}
fn check_error(error: Option<&ModelError>, expected: &Value) -> Result<()> {
    match (error, expected.as_str()) {
        (None, None) => Ok(()),
        (Some(e), Some(needle)) if e.to_string().contains(needle) => Ok(()),
        _ => Err(format!(
            "error expected {expected}, actual {}",
            error.map(ToString::to_string).unwrap_or_default()
        )),
    }
}
fn verify_recorder(
    r: &EvidenceRecorder,
    limits: &EvidenceLimits,
    origin: u64,
    provenance: &Provenance,
) -> Result<()> {
    let e = r.snapshot();
    e.validate(limits).map_err(|e| e.to_string())?;
    if e.provenance != *provenance {
        return Err("recorder changed provenance".into());
    }
    let evicted = monitor_parameter(&e.trace, "evicted_steps")?
        .parse::<u64>()
        .map_err(|_| "evicted count")?;
    if origin.checked_add(evicted) != Some(e.checkpoint_after) {
        return Err("checkpoint and trace eviction counters disagree".into());
    }
    if e.steps.len() != e.trace.steps.len() {
        return Err("phase journal detached from trace".into());
    }
    let mut bytes = vec![];
    e.write(&mut bytes, limits).map_err(|e| e.to_string())?;
    if Artifact::read(bytes.as_slice(), limits).map_err(|e| e.to_string())?
        != Artifact::V2(e.clone())
    {
        return Err("recorder artifact roundtrip".into());
    }
    let mut independent = e.clone();
    independent.provenance.run.push_str("modified snapshot");
    independent.steps.clear();
    if r.snapshot() != e {
        return Err("snapshot aliases retained evidence".into());
    }
    Ok(())
}
fn recorder_case(v: &Value) -> Result<()> {
    let model = Fixture::new(v["model"].clone());
    let state = number_i32(v, "initial_state")?;
    let checked = execution::check_initial(&model, &state).map_err(|e| e.to_string())?;
    let origin = unsigned(&v["origin"])?;
    let config = config_from_json(&v["config"])?;
    let options = recorder_options(&v["options"])?;
    let provenance = provenance_from_json(&v["provenance"])?;
    let limits = evidence_limits(&v["limits"])?;
    let before = model
        .events
        .borrow()
        .iter()
        .filter(|e| e.starts_with("check_"))
        .count();
    let result = EvidenceRecorder::new(
        &checked,
        origin,
        config,
        options,
        provenance.clone(),
        limits.clone(),
    );
    if model
        .events
        .borrow()
        .iter()
        .filter(|e| e.starts_with("check_"))
        .count()
        != before
    {
        return Err("initial evidence attach rechecked".into());
    }
    let expected = &v["expected"];
    check_error(result.as_ref().err(), &expected["initial_error_contains"])?;
    let mut recorder = match result {
        Err(_) => return Ok(()),
        Ok(r) => r,
    };
    verify_recorder(&recorder, &limits, origin, &provenance)?;
    let actions = array(v, "actions")?;
    let states = array(expected, "states")?;
    if actions.len() != states.len() {
        return Err("action/state count".into());
    }
    for (action, expected) in actions.iter().zip(states) {
        let mut cfg = v["model"].clone();
        if let Some(k) = action["encode_error"].as_str() {
            cfg["encode_error"] = json!(k);
        }
        let m = Fixture::new(cfg);
        let before = number_i32(action, "before")?;
        let input = number_i32(action, "input")?;
        let turn = supplied_transition(&action["transition"])?;
        let token = execution::check_turn(
            &m,
            &before,
            &input,
            &turn,
            unsigned(&action["sequence"])?,
            policy(&action["policy"])?,
        )
        .map_err(|e| e.to_string())?;
        let context = context_from_json(&action["causal"])?;
        let before_checks = m
            .events
            .borrow()
            .iter()
            .filter(|e| e.starts_with("check_"))
            .count();
        let prior = recorder.snapshot();
        let frozen = recorder.is_frozen();
        let result = recorder.observe(&token, context);
        if m.events
            .borrow()
            .iter()
            .filter(|e| e.starts_with("check_"))
            .count()
            != before_checks
        {
            return Err("causal handoff rechecked".into());
        }
        if m.events
            .borrow()
            .iter()
            .any(|e| e.starts_with("step:") || e == "initial_state")
        {
            return Err("causal handoff executes model".into());
        }
        check_error(result.as_ref().err(), &expected["error_contains"])?;
        if frozen && recorder.snapshot().trace.termination != prior.trace.termination {
            return Err("frozen reason overwritten".into());
        }
        let actual = recorder_state(&recorder, &expected["error_contains"])?;
        if actual != *expected {
            return Err(format!("recorder expected {expected}, actual {actual}"));
        }
        verify_recorder(&recorder, &limits, origin, &provenance)?;
    }
    let last = recorder.snapshot();
    if recorder.into_trace() != last {
        return Err("consuming evidence export differs".into());
    }
    if model
        .events
        .borrow()
        .iter()
        .any(|e| e.starts_with("step:") || e == "initial_state")
    {
        return Err("evidence constructor executed model".into());
    }
    Ok(())
}

#[derive(Default)]
struct Threshold {
    initial: Cell<usize>,
    reducer: Cell<usize>,
    state: Cell<usize>,
    edge: Cell<usize>,
    legacy: Cell<usize>,
    into: Cell<usize>,
}
impl Threshold {
    fn legacy<T>(&self) -> R<T> {
        self.legacy.set(self.legacy.get() + 1);
        Err(ModelError::new("legacy callback called"))
    }
    fn encode(&self, s: &str, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        for b in s.as_bytes() {
            o.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
    fn counts(&self) -> Value {
        json!({"initial_state":self.initial.get(),"reducer":self.reducer.get(),"state":self.state.get(),"transition":self.edge.get()})
    }
}
impl Model for Threshold {
    type State = i32;
    type Input = i32;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "native-evidence-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        self.initial.set(self.initial.get() + 1);
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, String>> {
        self.reducer.set(self.reducer.get() + 1);
        if *i != 1 {
            return Err(ModelError::new("expected one"));
        }
        Ok(Transition::accepted(s + i, vec!["changed".into()]))
    }
    fn check_state(&self, _: &i32) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.state.set(self.state.get() + 1);
        c.push(if *s >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        });
        c.push(Check::passed("state.appended"));
        Ok(())
    }
    fn check_transition(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
    ) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_transition_into(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.edge.set(self.edge.get() + 1);
        c.push(Check::passed("edge.checked"));
        Ok(())
    }
}
impl ModelCodec for Threshold {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_output(&self, _: &String) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_state_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(&n.to_string(), o)
    }
    fn encode_input_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(&n.to_string(), o)
    }
    fn encode_output_into(&self, s: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(s, o)
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        decode_integer(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        let n = decode_integer(b)?;
        if n != 1 {
            return Err(ModelError::new("expected one"));
        }
        Ok(n)
    }
}

fn decode_integer(b: &[u8]) -> R<i32> {
    decimal(b).map_err(ModelError::new)
}
fn consumer_limits() -> EvidenceLimits {
    EvidenceLimits {
        trace: ReadLimits {
            max_total_bytes: 16384,
            max_frame_bytes: 4096,
            max_blob_bytes: 64,
            max_string_bytes: 256,
            max_steps: 16,
            max_checks_per_step: 16,
            max_outputs_per_step: 16,
            max_parameters: 32,
            max_items: 256,
        },
        max_metadata_bytes: 1024,
        max_labels: 8,
        max_label_bytes: 128,
        max_causes_per_step: 4,
        max_total_causes: 8,
    }
}
fn consumer_options(steps: usize) -> RecorderOptions {
    RecorderOptions {
        max_steps: steps,
        max_retained_bytes: 16384,
        limits: consumer_limits().trace,
    }
}
fn consumer_provenance() -> Provenance {
    Provenance {
        run: "installed-evidence-run".into(),
        epoch: 7,
        manifest: Some("manifest-v1".into()),
        source: Some("consumer-source".into()),
        environment: vec![
            ("region".into(), "test".into()),
            ("note".into(), "é".into()),
        ],
    }
}
fn manual() -> RunConfig {
    RunConfig {
        strategy: "manual".into(),
        seed: None,
        parameters: vec![],
    }
}
fn app_causal(sequence: u64) -> CausalContext {
    CausalContext {
        machine: Some("worker-a".into()),
        generation: Some(9),
        correlation: Some("request-1".into()),
        logical_time: Some(10 + sequence),
        causes: if sequence == 1 {
            vec![]
        } else {
            vec![OutputOrigin {
                sequence: sequence - 1,
                output: 0,
            }]
        },
    }
}
fn require(ok: bool, msg: &str) -> Result<()> {
    if ok { Ok(()) } else { Err(msg.into()) }
}
fn config_json(c: &RunConfig) -> Value {
    json!({"strategy":c.strategy,"seed":c.seed.map(|n|n.to_string()),"parameters":c.parameters})
}
fn app_report(e: &TraceV2) -> Result<Value> {
    let model = Threshold::default();
    let report = e
        .replay(&model, ReplayOptions::default(), &consumer_limits())
        .map_err(|e| e.to_string())?;
    require(
        model.legacy.get() == 0,
        "phase replay called legacy callbacks",
    )?;
    Ok(report_json(&report))
}
fn app_capture(
    turns: usize,
    steps: usize,
    limits: EvidenceLimits,
    causes: bool,
) -> Result<TraceV2> {
    let model = Threshold::default();
    let checkpoint = 0;
    let initial = execution::check_initial(&model, &checkpoint).map_err(|e| e.to_string())?;
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        manual(),
        consumer_options(steps),
        Provenance::default(),
        limits.clone(),
    )
    .map_err(|e| e.to_string())?;
    let mut state = 0;
    for seq in 1..=turns {
        let input = 1;
        let turn = model.step(&state, &input).map_err(|e| e.to_string())?;
        let checked = execution::check_turn(
            &model,
            &state,
            &input,
            &turn,
            seq as u64,
            CheckPolicy::default(),
        )
        .map_err(|e| e.to_string())?;
        let context = if causes && seq > 1 {
            CausalContext {
                causes: vec![OutputOrigin {
                    sequence: seq as u64 - 1,
                    output: 0,
                }],
                ..CausalContext::default()
            }
        } else {
            CausalContext::default()
        };
        let before = model.counts();
        recorder
            .observe(&checked, context)
            .map_err(|e| e.to_string())?;
        require(model.counts() == before, "control recorder rechecked")?;
        state = turn.state;
        let snapshot = recorder.snapshot();
        snapshot.validate(&limits).map_err(|e| e.to_string())?;
        let replayed = snapshot
            .replay(&Threshold::default(), ReplayOptions::default(), &limits)
            .map_err(|e| e.to_string())?;
        require(
            replayed.outcome == ReplayOutcome::Exact,
            "control retained artifact does not replay",
        )?;
    }
    Ok(recorder.into_trace())
}
fn diagnostic_policy<'a>(ids: &'a [&'a str]) -> ExportPolicy<'a> {
    ExportPolicy {
        max_steps: 2,
        max_bytes: 4096,
        property_ids: ids,
    }
}
fn app_controls(evidence: &TraceV2) -> Result<Value> {
    let mut metadata = consumer_limits();
    metadata.max_metadata_bytes = 70;
    let m = app_capture(3, 3, metadata, false)?;
    require(
        m.checkpoint_after == 1 && m.steps.len() == 2 && m.trace.initial_checks.len() == 2,
        "metadata eviction misaligned",
    )?;
    let t = app_capture(2, 1, consumer_limits(), false)?;
    require(
        t.checkpoint_after == 1 && t.steps.len() == 1 && t.trace.initial_checks.len() == 2,
        "trace eviction misaligned",
    )?;
    let mut causes = consumer_limits();
    causes.max_total_causes = 1;
    let c = app_capture(3, 3, causes, true)?;
    require(
        c.checkpoint_after == 2 && c.steps.len() == 1 && c.trace.initial_checks.len() == 2,
        "cause eviction misaligned",
    )?;
    let model = Threshold::default();
    let checkpoint = 0;
    let initial = execution::check_initial(&model, &checkpoint).map_err(|e| e.to_string())?;
    let mut limits = consumer_limits();
    limits.max_metadata_bytes = 62;
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        manual(),
        consumer_options(3),
        Provenance::default(),
        limits.clone(),
    )
    .map_err(|e| e.to_string())?;
    let input = 1;
    let turn1 = model.step(&checkpoint, &input).map_err(|e| e.to_string())?;
    let token1 = execution::check_turn(
        &model,
        &checkpoint,
        &input,
        &turn1,
        1,
        CheckPolicy::default(),
    )
    .map_err(|e| e.to_string())?;
    recorder
        .observe(&token1, CausalContext::default())
        .map_err(|e| e.to_string())?;
    let turn2 = model
        .step(&turn1.state, &input)
        .map_err(|e| e.to_string())?;
    let token2 = execution::check_turn(
        &model,
        &turn1.state,
        &input,
        &turn2,
        2,
        CheckPolicy::default(),
    )
    .map_err(|e| e.to_string())?;
    let error = recorder
        .observe(
            &token2,
            CausalContext {
                causes: vec![OutputOrigin {
                    sequence: 1,
                    output: 99,
                }],
                ..CausalContext::default()
            },
        )
        .err()
        .ok_or("invalid retained source accepted")?;
    require(
        error.to_string().contains("output")
            && recorder.is_frozen()
            && recorder.snapshot().steps.len() == 1
            && recorder.snapshot().checkpoint_after == 0,
        "source validation preceded by planned eviction",
    )?;
    recorder
        .snapshot()
        .validate(&limits)
        .map_err(|e| e.to_string())?;
    let mut changed = evidence.clone();
    changed.steps[0].state_check_count = 1;
    let report = changed
        .replay(
            &Threshold::default(),
            ReplayOptions::default(),
            &consumer_limits(),
        )
        .map_err(|e| e.to_string())?;
    require(
        matches!(
            report.outcome,
            ReplayOutcome::Diverged {
                step: Some(1),
                field: "check_phase"
            }
        ) && report.steps_verified == 0
            && !report.failure_reproduced,
        "phase mismatch not detected",
    )?;
    let upgraded = TraceV2::verify_upgrade(
        &Threshold::default(),
        evidence.trace.clone(),
        consumer_provenance(),
        0,
        ReplayOptions::default(),
        &consumer_limits(),
    )
    .map_err(|e| e.to_string())?;
    require(
        upgraded
            .steps
            .iter()
            .all(|s| s.state_check_count == 2 && s.causal == CausalContext::default())
            && app_report(&upgraded)?["outcome"] == "exact",
        "upgrade lost phase",
    )?;
    let artifact = Artifact::V2(evidence.clone());
    let ids = ["threshold.bound"];
    let policy = diagnostic_policy(&ids);
    let diagnostic = diagnostic_json(&artifact, &policy).map_err(|e| e.to_string())?;
    let value: Value = serde_json::from_slice(&diagnostic).map_err(|e| e.to_string())?;
    require(
        value["truncated"] == true
            && value["observations"]
                .as_array()
                .is_some_and(|a| a.len() == 3)
            && value["termination"] == "property_failure"
            && value["replayable"] == false,
        "diagnostic shape",
    )?;
    for observation in value["observations"].as_array().ok_or("observations")? {
        for check in observation["checks"].as_array().ok_or("checks")? {
            require(
                check["id"].is_null() || check["id"] == "threshold.bound",
                "diagnostic leaked unallowlisted ID",
            )?;
            require(check.get("details").is_none(), "diagnostic leaked details")?;
        }
    }
    let exact = ExportPolicy {
        max_bytes: diagnostic.len(),
        ..policy
    };
    require(
        diagnostic_json(&artifact, &exact).map_err(|e| e.to_string())? == diagnostic,
        "exact diagnostic cap",
    )?;
    let under = ExportPolicy {
        max_bytes: diagnostic.len() - 1,
        ..exact
    };
    require(
        diagnostic_json(&artifact, &under).is_err(),
        "diagnostic cap ignored",
    )?;
    let zero = ExportPolicy {
        max_steps: 0,
        max_bytes: 4096,
        property_ids: &[],
    };
    let zero: Value =
        serde_json::from_slice(&diagnostic_json(&artifact, &zero).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    require(
        zero["observations"]
            .as_array()
            .is_some_and(|a| a.len() == 1),
        "zero steps omitted initial",
    )?;
    filesystem_exports(&artifact, &diagnostic_policy(&ids), &diagnostic)?;
    let mut v1 = vec![];
    evidence
        .trace
        .write_with_limits(&mut v1, &consumer_limits().trace)
        .map_err(|e| e.to_string())?;
    let passive = Artifact::read(v1.as_slice(), &consumer_limits()).map_err(|e| e.to_string())?;
    require(
        matches!(passive, Artifact::V1(_))
            && passive.check_phase(0, 0) == Some(CheckPhase::InitialState)
            && passive.check_phase(1, 0).is_none(),
        "passive v1 invented phases",
    )?;
    Ok(
        json!({"check_once":true,"coordinated_metadata_eviction":true,"coordinated_cause_eviction":true,"coordinated_trace_eviction":true,"source_index_before_eviction":true,"phase_divergence":true,"verified_upgrade":true,"diagnostic_exact":true,"diagnostic_caps":true,"exclusive_private_files":true,"no_overwrite":true,"passive_v1_no_execution":true}),
    )
}
fn private_bytes_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path).map_err(|e| e.to_string())?;
    f.write_all(bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())
}
fn portable_projection(t: &Trace) -> Value {
    let (kind, error) = match &t.termination {
        Termination::Completed => ("completed", ""),
        Termination::PropertyFailed => ("property_failed", ""),
        Termination::Interrupted => ("interrupted", ""),
        Termination::StepLimit => ("step_limit", ""),
        Termination::ModelError(s) => ("model_error", s.as_str()),
    };
    json!({"format":"stateless.trace-json","version":1,"metadata":{"name":t.metadata.name,"model_version":t.metadata.model_version,"properties_version":t.metadata.properties_version,"codec_version":t.metadata.codec_version,"build":t.metadata.build},"initial_state":hex(&t.initial_state),"initial_checks":checks_details(&t.initial_checks),"steps":t.steps.iter().map(|s|{let(kind,reason)=match &s.disposition{Disposition::Accepted=>("accepted",""),Disposition::Rejected(s)=>("rejected",s.as_str()),Disposition::Ignored(s)=>("ignored",s.as_str())};json!({"input":hex(&s.input),"disposition":{"kind":kind,"reason":reason},"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"checks":checks_details(&s.checks)})}).collect::<Vec<_>>(),"termination":kind,"error":error})
}
pub fn record(prefix: &str) -> Result<Value> {
    let model = Threshold::default();
    let checkpoint = 0;
    let initial = execution::check_initial(&model, &checkpoint).map_err(|e| e.to_string())?;
    let before = model.counts();
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        manual(),
        consumer_options(3),
        consumer_provenance(),
        consumer_limits(),
    )
    .map_err(|e| e.to_string())?;
    require(model.counts() == before, "main attach rechecks")?;
    let mut state = 0;
    for seq in 1..=3 {
        let input = 1;
        let turn = model.step(&state, &input).map_err(|e| e.to_string())?;
        let token =
            execution::check_turn(&model, &state, &input, &turn, seq, CheckPolicy::default())
                .map_err(|e| e.to_string())?;
        let before = model.counts();
        recorder
            .observe(&token, app_causal(seq))
            .map_err(|e| e.to_string())?;
        require(before == model.counts(), "main handoff rechecks")?;
        state = turn.state;
    }
    let counts = model.counts();
    require(
        counts == json!({"initial_state":0,"reducer":3,"state":4,"transition":3}),
        "main execution count",
    )?;
    let evidence = recorder.into_trace();
    evidence
        .write_raw_new(Path::new(&format!("{prefix}.v2")), &consumer_limits())
        .map_err(|e| e.to_string())?;
    let mut v1 = vec![];
    evidence
        .trace
        .write_with_limits(&mut v1, &consumer_limits().trace)
        .map_err(|e| e.to_string())?;
    private_bytes_new(Path::new(&format!("{prefix}.v1")), &v1)?;
    private_bytes_new(
        Path::new(&format!("{prefix}.json")),
        &serde_json::to_vec(&portable_projection(&evidence.trace)).map_err(|e| e.to_string())?,
    )?;
    let ids = ["threshold.bound"];
    let text = String::from_utf8(
        diagnostic_json(&Artifact::V2(evidence.clone()), &diagnostic_policy(&ids))
            .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    Ok(
        json!({"producer":"rust","mode":"record","main_counts":counts,"controls":app_controls(&evidence)?,"evidence":evidence_json(&evidence),"report":app_report(&evidence)?,"retained_config":config_json(&evidence.trace.config),"diagnostic_text":text,"legacy_encoder_calls":model.legacy.get(),"codec_into_calls":model.into.get()}),
    )
}
fn read_artifact(path: &Path) -> Result<Artifact> {
    Artifact::read(
        std::fs::File::open(path).map_err(|e| e.to_string())?,
        &consumer_limits(),
    )
    .map_err(|e| e.to_string())
}
pub fn replay(input: &Path, output: &Path) -> Result<Value> {
    let evidence = match read_artifact(input)? {
        Artifact::V2(e) => e,
        Artifact::V1(_) => return Err("expected v2 artifact".into()),
    };
    let report = app_report(&evidence)?;
    evidence
        .write_raw_new(output, &consumer_limits())
        .map_err(|e| format!("N11_DESTINATION_CREATE_ERROR {e}"))?;
    Ok(
        json!({"producer":"rust","mode":"replay","version":2,"evidence":evidence_json(&evidence),"report":report,"retained_config":config_json(&evidence.trace.config)}),
    )
}
pub fn read_v1(input: &Path) -> Result<Value> {
    let artifact = read_artifact(input)?;
    require(matches!(artifact, Artifact::V1(_)), "expected v1 artifact")?;
    Ok(
        json!({"producer":"rust","mode":"read-v1","version":1,"initial_phase":phase_name(artifact.check_phase(0,0)),"turn_phase":phase_name(artifact.check_phase(1,0)),"execution_calls":0,"retained_config":config_json(&artifact.trace().config)}),
    )
}
pub fn upgrade(input: &Path, output: &Path) -> Result<Value> {
    let artifact = read_artifact(input)?;
    let trace = match artifact {
        Artifact::V1(t) => t,
        Artifact::V2(_) => return Err("expected v1 artifact".into()),
    };
    let evidence = TraceV2::verify_upgrade(
        &Threshold::default(),
        trace,
        consumer_provenance(),
        0,
        ReplayOptions::default(),
        &consumer_limits(),
    )
    .map_err(|e| e.to_string())?;
    let report = app_report(&evidence)?;
    evidence
        .write_raw_new(output, &consumer_limits())
        .map_err(|e| format!("N11_DESTINATION_CREATE_ERROR {e}"))?;
    Ok(
        json!({"producer":"rust","mode":"upgrade","version":2,"evidence":evidence_json(&evidence),"report":report,"retained_config":config_json(&evidence.trace.config)}),
    )
}
pub fn diagnostic(input: &Path, output: &Path) -> Result<Value> {
    let artifact = read_artifact(input)?;
    let ids = ["threshold.bound"];
    let policy = diagnostic_policy(&ids);
    let bytes = diagnostic_json(&artifact, &policy).map_err(|e| e.to_string())?;
    export_diagnostic_new(&artifact, output, &policy).map_err(|e| format!("N11_DESTINATION_CREATE_ERROR {e}"))?;
    require(
        export_diagnostic_new(&artifact, output, &policy).is_err()
            && std::fs::read(output).map_err(|e| e.to_string())? == bytes,
        "diagnostic overwrite changed bytes",
    )?;
    Ok(
        json!({"producer":"rust","mode":"diagnostic","diagnostic_text":String::from_utf8(bytes).map_err(|e|e.to_string())?,"execution_calls":0,"no_overwrite":true}),
    )
}
