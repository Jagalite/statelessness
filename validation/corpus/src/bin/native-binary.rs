//! Binary-v1 interoperability qualification against the public Rust codec.
//! JSON is restricted to this isolated validation crate, never the core crate.
use serde_json::{Value, json};
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::model::*;
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace, TraceError, TraceStep};
use std::io::{self, Read, Write};
use std::path::Path;

type Result<T> = std::result::Result<T, String>;
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
fn binary_file(path: impl AsRef<Path>) -> Result<Vec<u8>> {
    let maximum = ReadLimits::default().max_total_bytes;
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > maximum {
        return Err("qualification binary byte limit".into());
    }
    Ok(bytes)
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
fn limits(v: &Value) -> Result<ReadLimits> {
    let mut l = ReadLimits::default();
    if let Some(overrides) = v.get("limits") {
        for (key, value) in overrides.as_object().ok_or("limits must be object")? {
            let n = value.as_u64().ok_or("limit must be unsigned integer")?;
            if key == "max_total_bytes" {
                l.max_total_bytes = n;
                continue;
            }
            let n = usize::try_from(n).map_err(|_| "limit exceeds usize")?;
            match key.as_str() {
                "max_frame_bytes" => l.max_frame_bytes = n,
                "max_blob_bytes" => l.max_blob_bytes = n,
                "max_string_bytes" => l.max_string_bytes = n,
                "max_steps" => l.max_steps = n,
                "max_checks_per_step" => l.max_checks_per_step = n,
                "max_outputs_per_step" => l.max_outputs_per_step = n,
                "max_parameters" => l.max_parameters = n,
                "max_items" => l.max_items = n,
                _ => return Err(format!("unknown binary limit {key}")),
            }
        }
    }
    Ok(l)
}
fn error_json(e: &TraceError) -> Value {
    let (kind, detail) = match e {
        TraceError::Io(e) => ("io", Some(e.to_string())),
        TraceError::Truncated => ("truncated", None),
        TraceError::UnsupportedVersion(n) => ("unsupported_version", Some(n.to_string())),
        TraceError::ChecksumMismatch => ("checksum_mismatch", None),
        TraceError::InvalidData(s) => ("invalid_data", Some((*s).into())),
        TraceError::LimitExceeded(s) => ("limit_exceeded", Some((*s).into())),
        TraceError::AllocationFailed => ("allocation_failed", None),
    };
    json!({"kind":kind,"detail":detail})
}
fn encode(t: &Trace, limits: &ReadLimits) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    t.write_with_limits(&mut output, limits)
        .map_err(|e| e.to_string())?;
    Ok(output)
}
struct Chunked<'a> {
    data: &'a [u8],
    chunk: usize,
}
impl Read for Chunked<'_> {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let n = out.len().min(self.chunk).min(self.data.len());
        out[..n].copy_from_slice(&self.data[..n]);
        self.data = &self.data[n..];
        Ok(n)
    }
}
struct FailingReader<'a> {
    data: &'a [u8],
    remaining: usize,
}
impl Read for FailingReader<'_> {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Err(io::Error::other("qualification reader failure"));
        }
        let n = out.len().min(self.remaining).min(self.data.len());
        out[..n].copy_from_slice(&self.data[..n]);
        self.data = &self.data[n..];
        self.remaining -= n;
        Ok(n)
    }
}
struct FailingWriter {
    remaining: usize,
    written: Vec<u8>,
}
impl Write for FailingWriter {
    fn write(&mut self, input: &[u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Err(io::Error::other("qualification writer failure"));
        }
        let n = input.len().min(self.remaining);
        self.written.extend_from_slice(&input[..n]);
        self.remaining -= n;
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> {
        Err(io::Error::other("must not flush caller writer"))
    }
}
fn require(condition: bool, message: impl Into<String>) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn validate_corpus(path: &Path) -> Result<Value> {
    let corpus = json_file(path)?;
    let fixtures = array(&corpus, "fixtures")?;
    require(
        corpus["profile"] == "native-binary-v1",
        "wrong corpus profile",
    )?;
    let mut good = 0;
    let mut bad = 0;
    let mut prefixes = 0;
    let mut chunked = 0;
    let mut io_cases = 0;
    let mut preflight = 0;
    let mut semantic_checks = 0;
    for row in fixtures {
        let name = string(row, "case")?;
        let bytes = decode_hex(string(row, "bytes_hex")?)?;
        let l = limits(row)?;
        let result = Trace::read_from(bytes.as_slice(), &l);
        if row["valid"] == true {
            let actual = result.map_err(|e| format!("{name}: unexpected read error {e}"))?;
            require(
                to_json(&actual) == row["trace"],
                format!("{name}: normalized trace fields changed"),
            )?;
            let expected = from_json(&row["trace"])?;
            if name == "single-step" {
                let fresh = record(&SingleModel, [1], RunConfig::default(), 100000)
                    .map_err(|e| e.to_string())?;
                let mut stamped_expected = expected.clone();
                stamped_expected.config.parameters = engine_parameters();
                require(
                    fresh == stamped_expected,
                    "single-step: actual Rust recording differs from independently specified semantic payload and full stamped config",
                )?;
                require(
                    Trace::read_from(
                        encode(&fresh, &ReadLimits::default())?.as_slice(),
                        &ReadLimits::default(),
                    )
                    .map_err(|e| e.to_string())?
                        == fresh,
                    "fresh Rust record config lost in binary roundtrip",
                )?;
                let fresh_report = replay(&SingleModel, &fresh, ReplayOptions::default())
                    .map_err(|e| e.to_string())?;
                require(
                    fresh_report.outcome == ReplayOutcome::Exact && fresh_report.build_matches,
                    "fresh stamped Rust record did not replay",
                )?;
                let report = replay(&SingleModel, &actual, ReplayOptions::default())
                    .map_err(|e| e.to_string())?;
                require(
                    report.outcome == ReplayOutcome::Exact
                        && report.build_matches
                        && report.steps_verified == 1,
                    "single-step: semantic replay differs",
                )?;
            }
            if matches!(name, "single-step" | "property-failed" | "empty") {
                let r = replay(&SingleModel, &actual, ReplayOptions::default())
                    .map_err(|e| e.to_string())?;
                let correct = match name {
                    "single-step" => r.outcome == ReplayOutcome::Exact,
                    "property-failed" => matches!(r.outcome, ReplayOutcome::Diverged { .. }),
                    "empty" => matches!(r.outcome, ReplayOutcome::Incompatible { .. }),
                    _ => unreachable!(),
                };
                require(
                    correct && !r.failure_reproduced,
                    format!("{name}: semantic replay control failed: {:?}", r.outcome),
                )?;
                semantic_checks += 1;
            }
            require(actual == expected, format!("{name}: typed trace mismatch"))?;
            require(
                encode(&actual, &l)? == bytes,
                format!("{name}: byte roundtrip differs"),
            )?;
            require(
                encode(&expected, &l)? == bytes,
                format!("{name}: Rust producer differs from independent golden"),
            )?;
            for end in 0..bytes.len() {
                require(
                    matches!(
                        Trace::read_from(&bytes[..end], &ReadLimits::default()),
                        Err(TraceError::Truncated)
                    ),
                    format!("{name}: prefix {end} must be truncated"),
                )?;
                prefixes += 1;
            }
            for chunk in [1, 2, 3, 5, 7, 31] {
                let decoded = Trace::read_from(
                    Chunked {
                        data: &bytes,
                        chunk,
                    },
                    &l,
                )
                .map_err(|e| format!("{name}: chunk {chunk}: {e}"))?;
                require(
                    decoded == actual,
                    format!("{name}: read chunk changes trace"),
                )?;
                chunked += 1;
            }
            for position in [0, 1, 11, 12, bytes.len() / 2, bytes.len() - 1, bytes.len()] {
                let decoded = Trace::read_from(
                    FailingReader {
                        data: &bytes,
                        remaining: position,
                    },
                    &l,
                );
                require(
                    matches!(decoded, Err(TraceError::Io(_))),
                    format!("{name}: reader error hidden at {position}"),
                )?;
                let mut writer = FailingWriter {
                    remaining: position,
                    written: Vec::new(),
                };
                if position < bytes.len() {
                    require(
                        matches!(
                            actual.write_with_limits(&mut writer, &l),
                            Err(TraceError::Io(_))
                        ),
                        format!("{name}: writer error hidden at {position}"),
                    )?;
                    require(
                        writer.written == bytes[..position],
                        format!("{name}: partial write isn't exact prefix"),
                    )?;
                } else {
                    actual
                        .write_with_limits(&mut writer, &l)
                        .map_err(|e| format!("{name}: writer flush/close surprise: {e}"))?;
                    require(
                        writer.written == bytes,
                        format!("{name}: full write differs"),
                    )?;
                }
                io_cases += 2;
            }
            good += 1;
        } else {
            let error = result
                .err()
                .ok_or_else(|| format!("{name}: invalid artifact accepted"))?;
            require(
                error_json(&error) == row["expected_error"],
                format!(
                    "{name}: wrong error {} expected {}",
                    error_json(&error),
                    row["expected_error"]
                ),
            )?;
            if row.get("limits").is_some() {
                let valid = Trace::read_from(bytes.as_slice(), &ReadLimits::default())
                    .map_err(|e| format!("{name}: default-limit source must be valid: {e}"))?;
                let mut output = Vec::new();
                let error = valid
                    .write_with_limits(&mut output, &l)
                    .err()
                    .ok_or_else(|| format!("{name}: writer accepted invalid limit"))?;
                require(
                    error_json(&error) == row["expected_error"],
                    format!("{name}: writer quota error mismatch"),
                )?;
                require(
                    output.is_empty(),
                    format!("{name}: writer published before quota validation"),
                )?;
                preflight += 1;
            }
            bad += 1;
        }
    }
    // A tail-only defect must be detected before even the header is published.
    let row = fixtures
        .iter()
        .find(|v| v["case"] == "single-step")
        .ok_or("single-step fixture missing")?;
    let original = from_json(&row["trace"])?;
    for tail in [
        "string",
        "blob",
        "checks",
        "outputs",
        "items",
        "steps",
        "total",
        "frame",
        "parameters",
    ] {
        let mut trace = original.clone();
        let mut l = ReadLimits::default();
        match tail {
            "string" => {
                trace.termination = Termination::ModelError("x".repeat(65537));
            }
            "blob" => {
                l.max_blob_bytes = 2;
                let mut s = trace.steps[0].clone();
                s.post_state = vec![0; 3];
                trace.steps.push(s);
            }
            "checks" => {
                l.max_checks_per_step = 2;
                let mut s = trace.steps[0].clone();
                s.checks = vec![Check::passed("x"); 3];
                trace.steps.push(s);
            }
            "outputs" => {
                l.max_outputs_per_step = 2;
                let mut s = trace.steps[0].clone();
                s.outputs = vec![vec![]; 3];
                trace.steps.push(s);
            }
            "items" => {
                l.max_items = 7;
                trace.steps.push(trace.steps[0].clone());
            }
            "steps" => {
                l.max_steps = 1;
                trace.steps.push(trace.steps[0].clone());
            }
            "total" => {
                l.max_total_bytes = encode(&trace, &l)?.len() as u64;
                trace.steps.push(trace.steps[0].clone());
            }
            "frame" => {
                l.max_frame_bytes = 256;
                trace.termination = Termination::ModelError("x".repeat(257));
            }
            "parameters" => {
                l.max_parameters = 0;
                trace.config.parameters.push(("x".into(), "y".into()));
            }
            _ => unreachable!(),
        }
        let mut output = Vec::new();
        require(
            matches!(
                trace.write_with_limits(&mut output, &l),
                Err(TraceError::LimitExceeded(_))
            ),
            format!("tail {tail}: expected quota error"),
        )?;
        require(
            output.is_empty(),
            format!("tail {tail}: partial output published"),
        )?;
        preflight += 1;
    }
    Ok(
        json!({"producer":"rust","profile":"native-binary-v1","valid_cases":good,"error_cases":bad,"every_prefix_truncations":prefixes,"chunked_reads":chunked,"io_error_checks":io_cases,"writer_preflight_checks":preflight,"semantic_replay_checks":semantic_checks,"exact_byte_roundtrip":true,"semantic_replay":false}),
    )
}
struct SingleModel;
impl Model for SingleModel {
    type State = u8;
    type Input = u8;
    type Output = Vec<u8>;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "counter".into(),
            model_version: 1,
            properties_version: 2,
            codec_version: 3,
            build: "b".into(),
        }
    }
    fn initial_state(&self) -> std::result::Result<u8, ModelError> {
        Ok(0)
    }
    fn step(
        &self,
        state: &u8,
        input: &u8,
    ) -> std::result::Result<Transition<u8, Vec<u8>>, ModelError> {
        let next = input
            .checked_mul(2)
            .and_then(|n| state.checked_add(n))
            .ok_or_else(|| ModelError::new("counter overflow"))?;
        Ok(Transition::accepted(next, vec![vec![], vec![*state, 255]]))
    }
    fn check_state(&self, _state: &u8) -> std::result::Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("ok")])
    }
    fn check_transition(
        &self,
        _before: &u8,
        _input: &u8,
        _after: &TransitionRef<'_, u8, Vec<u8>>,
    ) -> std::result::Result<Vec<Check>, ModelError> {
        Ok(vec![Check::skipped("later", "not run")])
    }
}
impl ModelCodec for SingleModel {
    fn encode_state(&self, s: &u8) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(vec![*s])
    }
    fn decode_state(&self, b: &[u8]) -> std::result::Result<u8, ModelError> {
        if b.len() == 1 {
            Ok(b[0])
        } else {
            Err(ModelError::new("one state byte required"))
        }
    }
    fn encode_input(&self, i: &u8) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(vec![*i])
    }
    fn decode_input(&self, b: &[u8]) -> std::result::Result<u8, ModelError> {
        if b.len() == 1 {
            Ok(b[0])
        } else {
            Err(ModelError::new("one input byte required"))
        }
    }
    fn encode_output(&self, o: &Vec<u8>) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(o.clone())
    }
}
// The public recorder stamps truthful engine provenance. Derive its exact
// version from the compile-time core manifest and build from the runtime API;
// never strip these pairs to force agreement with a static manual-run golden.
fn engine_parameters() -> Vec<(String, String)> {
    let manifest = include_str!("../../../../Cargo.toml");
    let version = manifest
        .lines()
        .find_map(|line| {
            line.strip_prefix("version = \"")
                .and_then(|s| s.strip_suffix('"'))
        })
        .expect("core package version");
    vec![
        ("stateless.engine.version".into(), version.into()),
        (
            "stateless.engine.build".into(),
            stateless::modeling::RUNTIME_BUILD_ID.into(),
        ),
    ]
}
fn write_new(path: &str, bytes: &[u8]) -> Result<()> {
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(bytes).map_err(|e| e.to_string())
}
fn run() -> Result<Value> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let default_corpus =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../conformance/native-binary-v1.json");
    match args.as_slice() {
        [] => validate_corpus(&default_corpus),
        [mode,path] if mode=="--corpus" => validate_corpus(Path::new(path)),
        [mode,source,output] if mode=="roundtrip" => {
            let bytes=binary_file(source)?;let t=Trace::read_from(bytes.as_slice(),&ReadLimits::default()).map_err(|e|e.to_string())?;
            let encoded=encode(&t,&ReadLimits::default())?;require(encoded==bytes,"noncanonical roundtrip")?;write_new(output,&encoded)?;
            Ok(json!({"producer":"rust","operation":"roundtrip","exact_byte_roundtrip":true,"semantic_replay":false,"steps":t.steps.len()}))
        },
        [mode,name,output] if mode=="generate" => generate(&default_corpus,name,output),
        [mode,name,output,corpus] if mode=="generate" => generate(Path::new(corpus),name,output),
        [mode,path] if mode=="record-single" => {
            let t=record(&SingleModel,[1],RunConfig::default(),100000).map_err(|e|e.to_string())?;
            require(t.config==RunConfig{strategy:"manual".into(),seed:None,parameters:engine_parameters()},"fresh record engine stamp mismatch")?;
            let bytes=encode(&t,&ReadLimits::default())?;write_new(path,&bytes)?;
            Ok(json!({"producer":"rust","operation":"record-single","case":"single-step","native_record":true,"fresh_record":true,"steps":t.steps.len(),"config":to_json(&t)["config"],"semantic_replay":false}))
        },
        [mode,path] if mode=="replay-single" => {
            let bytes=binary_file(path)?;let t=Trace::read_from(bytes.as_slice(),&ReadLimits::default()).map_err(|e|e.to_string())?;
            require(!matches!(t.termination,Termination::ModelError(_)),"model_error prefix cannot verify terminal error")?;
            let r=replay(&SingleModel,&t,ReplayOptions::default()).map_err(|e|e.to_string())?;
            let outcome=match &r.outcome { ReplayOutcome::Exact=>"exact",ReplayOutcome::Diverged{..}=>"diverged",ReplayOutcome::Incompatible{..}=>"incompatible" };
            Ok(json!({"producer":"rust","operation":"replay-single","outcome":outcome,"semantic_replay":r.outcome==ReplayOutcome::Exact,"failure_reproduced":r.failure_reproduced,"steps":r.steps_verified,"build_matches":r.build_matches,"retained_config":to_json(&t)["config"]}))
        },
        [mode,h] if mode=="--decode-hex" => { let b=decode_hex(h)?;Ok(to_json(&Trace::read_from(b.as_slice(),&ReadLimits::default()).map_err(|e|e.to_string())?)) },
        [mode,h] if mode=="--transcode-hex" => {let b=decode_hex(h)?;let t=Trace::read_from(b.as_slice(),&ReadLimits::default()).map_err(|e|e.to_string())?;Ok(json!({"bytes_hex":hex(&encode(&t,&ReadLimits::default())?)}))},
        [mode,path] if mode=="--encode-json" => Ok(json!({"bytes_hex":hex(&encode(&from_json(&json_file(path)?)?,&ReadLimits::default())?)})),
        _ => Err("usage: native-binary [--corpus PATH | generate CASE OUTPUT [CORPUS] | roundtrip INPUT OUTPUT | record-single OUTPUT | replay-single INPUT | --decode-hex HEX | --transcode-hex HEX | --encode-json PATH]".into()),
    }
}
fn generate(corpus: &Path, name: &str, output: &str) -> Result<Value> {
    let data = json_file(corpus)?;
    let row = array(&data, "fixtures")?
        .iter()
        .find(|v| v["case"] == name)
        .ok_or("unknown fixture")?;
    require(row["valid"] == true, "cannot generate invalid fixture")?;
    // Fixed corpus generation exercises only the exact codec, preserving the
    // hand-authored configuration. Fresh public recording is record-single.
    let t = from_json(&row["trace"])?;
    let bytes = encode(&t, &limits(row)?)?;
    require(
        hex(&bytes) == string(row, "bytes_hex")?,
        "Rust producer differs from independent golden",
    )?;
    write_new(output, &bytes)?;
    Ok(
        json!({"producer":"rust","operation":"generate","case":name,"fresh_record":false,"native_record":false,"exact_byte_roundtrip":true,"semantic_replay":false,"steps":t.steps.len()}),
    )
}
fn main() {
    match run() {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("native-binary: {error}");
            std::process::exit(1);
        }
    }
}
