use stateless::{conformance::*, execution::*, explore::*, ffi::*, trace::*, *};
use std::panic::{AssertUnwindSafe, catch_unwind};
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Job {
    phase: u8,
    generation: u8,
}
pub struct Reference;
fn decode(b: &[u8]) -> Result<Job, ModelError> {
    if b.len() != 2 || b[0] > 4 || b[1] > 2 || (b[0] == 0) != (b[1] == 0) {
        return Err(ModelError::new("invalid job state v1"));
    };
    Ok(Job {
        phase: b[0],
        generation: b[1],
    })
}
impl Model for Reference {
    type State = Job;
    type Input = Vec<u8>;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "rust-jobs/event-v1".into(),
            build: env!("JOBS_BUILD").into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
    fn initial_state(&self) -> Result<Job, ModelError> {
        Ok(Job {
            phase: 0,
            generation: 0,
        })
    }
    fn step(&self, s: &Job, i: &Vec<u8>) -> Result<Transition<Job, u8>, ModelError> {
        if i.len() != 2 || i[0] > 4 || i[1] > 2 {
            return Err(ModelError::new("invalid event v1"));
        }
        let mut n = s.clone();
        let mut effects = vec![];
        let mut disposition = Disposition::Rejected("invalid-phase".into());
        match i[0] {
            0 if (s.phase == 0 || s.phase >= 3) && s.generation < 2 => {
                n.generation += 1;
                n.phase = 1;
                disposition = Disposition::Accepted;
                effects.push(0)
            }
            1 if s.phase == 1 => {
                n.phase = 2;
                disposition = Disposition::Accepted;
                effects.push(1)
            }
            2 if s.phase == 1 || s.phase == 2 => {
                n.phase = 3;
                disposition = Disposition::Accepted;
                effects.push(2)
            }
            3 | 4 if i[1] != s.generation => disposition = Disposition::Rejected("stale".into()),
            3 | 4 if s.phase == 2 => {
                n.phase = 4;
                disposition = Disposition::Accepted;
                effects.push(3)
            }
            _ => {}
        }
        Ok(Transition {
            state: n,
            outputs: effects,
            disposition,
        })
    }
    fn check_state(&self, s: &Job) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if s.phase <= 4 && s.generation <= 2 {
            Check::passed("reference.bounds")
        } else {
            Check::failed("reference.bounds", "invalid state")
        }])
    }
    fn check_transition(
        &self,
        b: &Job,
        _: &Vec<u8>,
        t: &TransitionRef<'_, Job, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![
            if !matches!(t.disposition, Disposition::Accepted) && t.state != b {
                Check::failed("reference.rejection-preserves-state", "mutated")
            } else {
                Check::passed("reference.rejection-preserves-state")
            },
        ])
    }
}
impl ModelCodec for Reference {
    fn encode_state(&self, s: &Job) -> Result<Vec<u8>, ModelError> {
        Ok(vec![s.phase, s.generation])
    }
    fn decode_state(&self, b: &[u8]) -> Result<Job, ModelError> {
        decode(b)
    }
    fn encode_input(&self, b: &Vec<u8>) -> Result<Vec<u8>, ModelError> {
        self.decode_input(b)
    }
    fn decode_input(&self, b: &[u8]) -> Result<Vec<u8>, ModelError> {
        if b.len() != 2 || b[0] > 4 || b[1] > 2 {
            Err(ModelError::new("invalid event"))
        } else {
            Ok(b.to_vec())
        }
    }
    fn encode_output(&self, o: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*o])
    }
}
pub struct JobProjection;
impl Projection<&HostModel, Reference> for JobProjection {
    type State = (u8, u8);
    type Effect = u8;
    fn actual_state(&self, b: &Vec<u8>) -> Result<(u8, u8), ModelError> {
        let s = decode(b)?;
        Ok((s.phase, s.generation))
    }
    fn reference_state(&self, s: &Job) -> Result<(u8, u8), ModelError> {
        Ok((s.phase, s.generation))
    }
    fn actual_effect(&self, b: &Vec<u8>) -> Result<u8, ModelError> {
        if b.len() != 1 || b[0] > 3 {
            Err(ModelError::new("invalid effect"))
        } else {
            Ok(b[0])
        }
    }
    fn reference_effect(&self, b: &u8) -> Result<u8, ModelError> {
        Ok(*b)
    }
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "job-projection-v1".into(),
            build: env!("JOBS_BUILD").into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
}
/// Example-only additive bridge for the paired job fixture.
///
/// # Safety
/// All handles must be live and thread confined, the model context must outlive
/// the call, and no operation may reenter the same model. Output handles must be
/// distinct and exclusively accessed. The artifact span must be readable for len
/// bytes (null is allowed only for zero length). Nothing is retained or freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jobs_run(
    model: *const StatelessModel,
    artifact: *const u8,
    len: usize,
    replay_mode: bool,
    report: *mut StatelessBuffer,
    original: *mut StatelessBuffer,
    reduced: *mut StatelessBuffer,
) -> i32 {
    let result = catch_unwind(AssertUnwindSafe(
        || -> Result<i32, Box<dyn std::error::Error>> {
            if model.is_null()
                || report.is_null()
                || original.is_null()
                || reduced.is_null()
                || report == original
                || report == reduced
                || original == reduced
                || len > 64 * 1024 * 1024
                || (len > 0 && artifact.is_null())
            {
                return Err("invalid bridge argument".into());
            }
            // SAFETY: Caller keeps model/context live and confined for this operation.
            let m = stateless::automatic::Auto::new(WithOracle::new(
                unsafe { (&*model).host() },
                Paired {
                    reference: Reference,
                    projection: JobProjection,
                },
            ));
            let identity = m.metadata();
            let mut text = format!(
                "identities={identity:?}\nseed=none (deterministic BFS)\nmax_states=10000 max_transitions=100000 max_depth=100\n"
            );
            if replay_mode {
                // SAFETY: Span validated above; caller supplies readable bytes.
                let bytes = if len == 0 {
                    &[][..]
                } else {
                    unsafe { std::slice::from_raw_parts(artifact, len) }
                };
                let t = Trace::read_from(bytes, &ReadLimits::default())?;
                let r = replay(
                    &m,
                    &t,
                    ReplayOptions {
                        allow_build_mismatch: false,
                    },
                )?;
                text.push_str(&format!("replay={r:?}\n"));
                assign(report, text.as_bytes())?;
                return Ok(match r.outcome {
                    ReplayOutcome::Exact if r.failure_reproduced => failure_status(&t),
                    ReplayOutcome::Exact => 0,
                    ReplayOutcome::Incompatible { .. } => 3,
                    _ => 2,
                });
            }
            let r = enumerate(
                &m,
                SearchConfig {
                    max_states: 10000,
                    max_transitions: 100000,
                    max_depth: 100,
                },
            )?;
            text.push_str(&format!(
                "termination={:?} states={} transitions={} skipped={}\n",
                r.termination, r.states, r.transitions, r.skipped_checks
            ));
            if let Some(f) = r.failure {
                // Add harmless rejected events to demonstrate reduction of valid longer evidence.
                let mut f = f;
                f.inputs.splice(0..0, [vec![1, 0], vec![2, 0]]);
                let t = record(&m, f.inputs.clone(), config(), f.inputs.len() + 1)?;
                let shrunk = shrink(&m, &f, ShrinkConfig::default())?;
                let rt = record(
                    &m,
                    shrunk.minimized.inputs.clone(),
                    config(),
                    shrunk.minimized.inputs.len() + 1,
                )?;
                let mut bytes = vec![];
                t.write_to(&mut bytes)?;
                assign(original, &bytes)?;
                bytes.clear();
                rt.write_to(&mut bytes)?;
                assign(reduced, &bytes)?;
                text.push_str(&format!(
                    "shrink={:?} validated={} original={} reduced={}\n",
                    shrunk.termination,
                    shrunk.validated_original,
                    t.steps.len(),
                    rt.steps.len()
                ));
                diagnostics(&m, &t, &mut text, "original");
                diagnostics(&m, &rt, &mut text, "reduced");
                assign(report, text.as_bytes())?;
                Ok(failure_status(&rt))
            } else {
                assign(report, text.as_bytes())?;
                Ok(
                    if r.termination == SearchTermination::GraphExhausted && r.skipped_checks == 0 {
                        0
                    } else {
                        4
                    },
                )
            }
        },
    ));
    match result {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            let _ = assign(report, format!("paired error: {e}").as_bytes());
            11
        }
        Err(_) => {
            let _ = assign(report, b"paired Rust panic");
            13
        }
    }
}
fn config() -> RunConfig {
    RunConfig {
        strategy: "paired-bfs-v1".into(),
        seed: None,
        parameters: vec![
            ("max_states".into(), "10000".into()),
            ("max_transitions".into(), "100000".into()),
            ("max_depth".into(), "100".into()),
        ],
    }
}
fn diagnostics<M: ModelCodec>(m: &M, t: &Trace, out: &mut String, label: &str)
where
    M::State: std::fmt::Debug,
{
    if t.initial_checks.iter().any(Check::is_failure) {
        out.push_str(&format!(
            "{label} {} input_index=none (initial state) state={:?} named_checks={:?}\n",
            failure_label(&t.initial_checks),
            m.decode_state(&t.initial_state),
            t.initial_checks
        ));
        return;
    }
    for (i, s) in t.steps.iter().enumerate() {
        if s.checks.iter().any(|c| c.is_failure()) {
            let before = m.decode_state(if i == 0 {
                &t.initial_state
            } else {
                &t.steps[i - 1].post_state
            });
            let after = m.decode_state(&s.post_state);
            out.push_str(&format!("{label} {} input_index={i} event={:?} before={before:?} after={after:?} actual_effects={:?} actual_outcome={:?} named_checks={:?}\n",failure_label(&s.checks),s.input,s.outputs,s.disposition,s.checks));
            break;
        }
    }
}

// Independent correctness failures must not be labeled cross-language disagreement.
fn failure_label(checks: &[Check]) -> &'static str {
    if checks.iter().filter(|c| c.is_failure()).any(|c| {
        !matches!(
            c.id.as_str(),
            "pair.state" | "pair.effects" | "pair.outcome"
        )
    }) {
        "independent-property-failure"
    } else {
        "first-divergence"
    }
}
fn failure_status(t: &Trace) -> i32 {
    let checks = t
        .initial_checks
        .iter()
        .chain(t.steps.iter().flat_map(|s| &s.checks));
    if checks.filter(|c| c.is_failure()).any(|c| {
        !matches!(
            c.id.as_str(),
            "pair.state" | "pair.effects" | "pair.outcome"
        )
    }) {
        5
    } else {
        1
    }
}

fn assign(b: *mut StatelessBuffer, bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    // SAFETY: Caller supplies a live exclusive output buffer, bytes live for call.
    let s = unsafe { stateless_buffer_assign(b, bytes.as_ptr(), bytes.len()) };
    if s != 0 {
        Err("output assignment failed".into())
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stale_completion_and_generation_ceiling() {
        let m = Reference;
        let mut s = m.initial_state().unwrap();
        for event in [vec![0, 0], vec![1, 0], vec![2, 0], vec![0, 0], vec![1, 0]] {
            let t = m.step(&s, &event).unwrap();
            assert_eq!(t.disposition, Disposition::Accepted);
            s = t.state;
        }
        let t = m.step(&s, &vec![4, 1]).unwrap();
        assert_eq!(t.state, s);
        assert!(t.outputs.is_empty());
        assert_eq!(t.disposition, Disposition::Rejected("stale".into()));
        let t = m.step(&s, &vec![3, 2]).unwrap();
        assert_eq!(t.outputs, [3]);
        assert_eq!(t.state.phase, 4);
        let next = m.step(&t.state, &vec![0, 0]).unwrap();
        assert_eq!(next.state, t.state);
        assert!(matches!(next.disposition, Disposition::Rejected(_)));
    }
    #[test]
    fn independent_failures_are_not_disagreements() {
        assert_eq!(
            failure_label(&[Check::failed("rust.bounds", "bad")]),
            "independent-property-failure"
        );
        assert_eq!(
            failure_label(&[Check::failed("pair.state", "different")]),
            "first-divergence"
        );
        let t = Trace {
            metadata: Reference.metadata(),
            config: config(),
            initial_state: vec![0, 0],
            initial_checks: vec![Check::failed("rust.bounds", "bad")],
            steps: vec![],
            termination: Termination::PropertyFailed,
        };
        assert_eq!(failure_status(&t), 5);
        let mut report = String::new();
        diagnostics(&Reference, &t, &mut report, "original");
        assert!(report.contains("independent-property-failure input_index=none"));
        assert!(report.contains("rust.bounds"));
        assert!(!report.contains("first-divergence"));
        let mut t = t;
        t.initial_checks = vec![Check::failed("pair.state", "different")];
        assert_eq!(failure_status(&t), 1);
    }
    #[test]
    fn malformed_codecs_are_errors() {
        for b in [
            &[][..],
            &[0],
            &[5, 1],
            &[1, 3],
            &[0, 1],
            &[1, 0],
            &[0, 0, 0],
        ] {
            assert!(Reference.decode_state(b).is_err());
        }
        for b in [&[][..], &[0], &[5, 0], &[3, 3], &[0, 0, 0]] {
            assert!(Reference.decode_input(b).is_err());
        }
    }
}
