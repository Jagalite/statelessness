//! Reference layout for the opt-in native indexed lifecycle diagnostics candidate.
use stateless::lifecycle::indexed::{Obligation, RetentionStatus, ScopedKey, Violation};

#[test]
fn indexed_debug_layout_reference() {
    let key = ScopedKey {
        epoch: 2,
        generation: 3,
        key: "a\"\n",
    };
    let prior = Obligation {
        deadline: 9,
        cancelled: false,
        settled: true,
        published: true,
    };
    let violations = vec![
        Violation {
            key: None,
            code: "bad\n\"\\".to_owned(),
            event: "é\t".to_owned(),
            observed_at: 4,
            prior: None,
        },
        Violation {
            key: Some(key.clone()),
            code: "duplicate".to_owned(),
            event: "publish".to_owned(),
            observed_at: 5,
            prior: Some(prior),
        },
    ];
    let retention = RetentionStatus {
        active_obligations: 1,
        historical_obligations: 2,
        acquired_resources: 3,
        historical_resources: 4,
        oldest_unresolved: Some((key, 8)),
        retired_before: 1,
    };
    let detail = format!("violations={violations:?}; retention={retention:?}");
    assert_eq!(
        detail,
        "violations=[Violation { key: None, code: \"bad\\n\\\"\\\\\", event: \"é\\t\", observed_at: 4, prior: None }, Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: \"a\\\"\\n\" }), code: \"duplicate\", event: \"publish\", observed_at: 5, prior: Some(Obligation { deadline: 9, cancelled: false, settled: true, published: true }) }]; retention=RetentionStatus { active_obligations: 1, historical_obligations: 2, acquired_resources: 3, historical_resources: 4, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: \"a\\\"\\n\" }, 8)), retired_before: 1 }"
    );
    let empty: Vec<Violation<u8>> = vec![];
    let r = RetentionStatus::<u8> {
        active_obligations: 0,
        historical_obligations: 0,
        acquired_resources: 0,
        historical_resources: 0,
        oldest_unresolved: None,
        retired_before: 0,
    };
    assert_eq!(
        format!("violations={empty:?}; retention={r:?}"),
        "violations=[]; retention=RetentionStatus { active_obligations: 0, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: None, retired_before: 0 }"
    );
    assert_eq!(
        format!("finite history: {r:?}"),
        "finite history: RetentionStatus { active_obligations: 0, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: None, retired_before: 0 }"
    );
}
