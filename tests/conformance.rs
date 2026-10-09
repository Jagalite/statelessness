use stateless::{conformance::*, *};
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct NativeState {
    total: u16,
}
struct Actual {
    bad_effect: bool,
    bad_outcome: bool,
}
struct Reference;
struct Semantic;
fn metadata(n: &str) -> ModelMetadata {
    ModelMetadata {
        name: n.into(),
        build: "test-build".into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
    }
}
impl Model for Actual {
    type State = u8;
    type Input = u8;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        metadata("actual")
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, b: &u8, i: &u8) -> Result<Transition<u8, String>, ModelError> {
        Ok(Transition {
            state: b + i,
            outputs: vec![if self.bad_effect {
                "99".into()
            } else {
                i.to_string()
            }],
            disposition: if self.bad_outcome {
                Disposition::Rejected("bad".into())
            } else {
                Disposition::Accepted
            },
        })
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("actual.independent")])
    }
}
impl Model for Reference {
    type State = NativeState;
    type Input = u8;
    type Output = u16;
    fn metadata(&self) -> ModelMetadata {
        metadata("reference")
    }
    fn initial_state(&self) -> Result<NativeState, ModelError> {
        Ok(NativeState { total: 0 })
    }
    fn step(&self, b: &NativeState, i: &u8) -> Result<Transition<NativeState, u16>, ModelError> {
        Ok(Transition {
            state: NativeState {
                total: b.total + u16::from(*i),
            },
            outputs: vec![u16::from(*i)],
            disposition: Disposition::Accepted,
        })
    }
    fn check_state(&self, _: &NativeState) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("reference.independent")])
    }
}
impl Projection<Actual, Reference> for Semantic {
    type State = u16;
    type Effect = u16;
    fn metadata(&self) -> ModelMetadata {
        metadata("projection-v1")
    }
    fn actual_state(&self, s: &u8) -> Result<u16, ModelError> {
        Ok(u16::from(*s))
    }
    fn reference_state(&self, s: &NativeState) -> Result<u16, ModelError> {
        Ok(s.total)
    }
    fn actual_effect(&self, e: &String) -> Result<u16, ModelError> {
        e.parse().map_err(|_| ModelError::new("malformed effect"))
    }
    fn reference_effect(&self, e: &u16) -> Result<u16, ModelError> {
        Ok(*e)
    }
}
#[test]
fn semantic_comparison_is_independent_of_layout_and_encoding() {
    for (bad_effect, bad_outcome, target) in [
        (false, false, None),
        (true, false, Some("pair.effects")),
        (false, true, Some("pair.outcome")),
    ] {
        let m = WithOracle::new(
            Actual {
                bad_effect,
                bad_outcome,
            },
            Paired {
                reference: Reference,
                projection: Semantic,
            },
        );
        let initial = m.initial_state().unwrap();
        let t = m.step(&initial, &2).unwrap();
        assert_eq!(t.state.model, 2);
        assert_eq!(t.state.oracle.total, 2);
        let checks = m.check_state(&t.state).unwrap();
        assert_eq!(
            checks.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
            ["actual.independent", "reference.independent", "pair.state"]
        );
        let checks = m
            .check_transition(
                &initial,
                &2,
                &TransitionRef {
                    state: &t.state,
                    outputs: &t.outputs,
                    disposition: &t.disposition,
                },
            )
            .unwrap();
        assert_eq!(
            checks
                .iter()
                .find(|c| c.is_failure())
                .map(|c| c.id.as_str()),
            target
        );
    }
}
#[test]
fn reference_history_is_not_reconstructed_from_actual() {
    let m = WithOracle::new(
        Actual {
            bad_effect: false,
            bad_outcome: false,
        },
        Paired {
            reference: Reference,
            projection: Semantic,
        },
    );
    let b = m.attach(9, NativeState { total: 2 });
    let t = m.step(&b, &1).unwrap();
    assert_eq!(t.state.model, 10);
    assert_eq!(t.state.oracle.total, 3);
    assert!(
        m.check_state(&t.state)
            .unwrap()
            .iter()
            .any(|c| c.id.as_str() == "pair.state" && c.is_failure())
    );
}

impl ModelCodec for Reference {
    fn encode_state(&self, _: &NativeState) -> Result<Vec<u8>, ModelError> {
        panic!("unbounded fallback must not be used")
    }
    fn encode_state_into(
        &self,
        s: &NativeState,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&s.total.to_le_bytes())
    }
    fn decode_state(&self, b: &[u8]) -> Result<NativeState, ModelError> {
        let bytes = b.try_into().map_err(|_| ModelError::new("invalid state"))?;
        Ok(NativeState {
            total: u16::from_le_bytes(bytes),
        })
    }
    fn encode_input(&self, i: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*i])
    }
    fn decode_input(&self, b: &[u8]) -> Result<u8, ModelError> {
        match b {
            [i] => Ok(*i),
            _ => Err(ModelError::new("invalid input")),
        }
    }
    fn encode_output(&self, o: &u16) -> Result<Vec<u8>, ModelError> {
        Ok(o.to_le_bytes().to_vec())
    }
}

#[test]
fn paired_history_preserves_bounded_codec_hook() {
    let p = Paired {
        reference: Reference,
        projection: Semantic,
    };
    let state = NativeState { total: 123 };
    let mut bytes = vec![];
    <_ as OracleCodec<Actual>>::encode_history_into(
        &p,
        &state,
        &mut EncodeBuffer::new(&mut bytes, 2),
    )
    .unwrap();
    assert_eq!(bytes, [123, 0]);
    bytes.clear();
    assert!(
        <_ as OracleCodec<Actual>>::encode_history_into(
            &p,
            &state,
            &mut EncodeBuffer::new(&mut bytes, 1)
        )
        .is_err()
    );
    assert!(bytes.is_empty());
}
