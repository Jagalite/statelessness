use statelessness_debug::inspect::*;
use std::cell::Cell;
use std::collections::BTreeMap;

struct Counted<'a>(&'a Cell<usize>, u128);
impl Inspect for Counted<'_> {
    fn inspect(
        &self,
        p: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        self.0.set(self.0.get() + 1);
        self.1.inspect(p, cx)
    }
}
struct State<'a> {
    value: Counted<'a>,
    secret: &'a str,
    items: Vec<Counted<'a>>,
}
impl Inspect for State<'_> {
    fn inspect(
        &self,
        p: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            p,
            "State",
            &[
                FieldView::new("value", "Value", &self.value),
                FieldView::redacted("secret", "Secret"),
                FieldView::new("items", "Items", &self.items),
            ],
        )
    }
}
fn query(path: Vec<PathSegment>) -> InspectQuery {
    InspectQuery {
        path,
        ..InspectQuery::default()
    }
}
#[test]
fn large_integers_keep_type_and_exact_decimal() {
    let view = inspect(&u128::MAX, &query(vec![])).unwrap();
    assert_eq!(
        view.node.kind,
        NodeKind::Scalar(Scalar::Integer {
            kind: IntegerType::U128,
            decimal: "340282366920938463463374607431768211455".into()
        })
    );
    let other = inspect(&i128::MIN, &query(vec![])).unwrap();
    assert!(format!("{:?}", other.node).contains("-170141183460469231731687303715884105728"));
}
#[test]
fn nested_path_is_structural_and_does_not_traverse_siblings() {
    let count = Cell::new(0);
    let s = State {
        value: Counted(&count, 17),
        secret: "token-do-not-log",
        items: (0..1000).map(|i| Counted(&count, i)).collect(),
    };
    let view = inspect(
        &s,
        &query(vec![
            PathSegment::Field("items".into()),
            PathSegment::Index(900),
        ]),
    )
    .unwrap();
    assert_eq!(count.get(), 1);
    assert!(view.node.is_complete());
    assert!(!format!("{view:?}").contains(s.secret));
    let redacted = inspect(&s, &query(vec![PathSegment::Field("secret".into())])).unwrap();
    assert_eq!(count.get(), 1);
    assert_eq!(redacted.node.kind, NodeKind::Redacted);
    assert_eq!(
        compare_nodes(Some(&redacted.node), Some(&redacted.node)),
        DiffKind::Unknown
    );
}
#[test]
fn paging_allocates_and_evaluates_only_requested_values() {
    let count = Cell::new(0);
    let list: Vec<_> = (0..10_000).map(|i| Counted(&count, i)).collect();
    let q = InspectQuery {
        page: PageRequest {
            offset: 501,
            limit: 3,
        },
        ..query(vec![])
    };
    let view = inspect(&list, &q).unwrap();
    assert_eq!(count.get(), 3);
    assert_eq!(view.node.children.len(), 3);
    assert_eq!(view.node.children[0].segment, PathSegment::Index(501));
    assert_eq!(view.node.page.unwrap().next_offset, Some(504));
    assert!(!view.node.is_complete());
    assert_eq!(
        compare_nodes(Some(&view.node), Some(&view.node)),
        DiffKind::Unknown
    );
}
#[test]
fn utf8_bytes_depth_and_aggregate_budgets_are_honest() {
    let mut q = query(vec![]);
    q.limits.max_value_bytes = 3;
    let s = inspect(&"💚hello", &q).unwrap();
    assert_eq!(
        s.node.kind,
        NodeKind::String {
            preview: "".into(),
            total_bytes: 9
        }
    );
    assert!(!s.node.is_complete());
    let b = inspect(&Bytes(&[0, 1, 2, 3, 4]), &q).unwrap();
    assert_eq!(
        b.node.kind,
        NodeKind::Bytes {
            preview: vec![0, 1, 2],
            total_bytes: 5
        }
    );
    q.limits.max_depth = 0;
    let nested = inspect(&vec![vec![1u8]], &q).unwrap();
    assert_eq!(
        nested.node.completeness,
        Completeness::Partial(IncompleteReason::Depth)
    );
    q.limits.max_depth = 32;
    q.limits.max_nodes = 2;
    let nodes = inspect(&vec![1u8, 2, 3], &q).unwrap();
    assert_eq!(nodes.node.children.len(), 1);
    assert!(!nodes.node.is_complete());
    q.limits.max_bytes = 1;
    assert_eq!(inspect(&1u8, &q), Err(InspectError::BudgetExceeded));
}
#[test]
fn maps_preserve_typed_keys_order_and_selection() {
    let mut values = BTreeMap::new();
    values.insert(u128::MAX, "last".to_owned());
    values.insert(1u128, "first".to_owned());
    let all = inspect(&values, &query(vec![])).unwrap();
    assert_eq!(
        all.node.kind,
        NodeKind::Map {
            ordering: MapOrdering::Stable
        }
    );
    assert!(all.node.is_complete());
    let selected = inspect(
        &values,
        &query(vec![PathSegment::MapKey(MapKey::Integer {
            kind: IntegerType::U128,
            decimal: u128::MAX.to_string(),
        })]),
    )
    .unwrap();
    assert_eq!(
        selected.node.kind,
        NodeKind::String {
            preview: "last".into(),
            total_bytes: 4
        }
    );
}
#[test]
fn opaque_unavailable_truncated_and_schema_mismatch_never_equal() {
    let a = inspect(&Opaque("socket"), &query(vec![])).unwrap();
    assert_eq!(diff(&a, &a).kind, DiffKind::Unknown);
    let mut c = InspectContext::new(InspectLimits::default(), PageRequest::default());
    let unknown = c.unavailable(&[]).unwrap();
    assert_eq!(
        compare_nodes(Some(&unknown), Some(&unknown)),
        DiffKind::Unknown
    );
    let truncated = c.truncated(IncompleteReason::Bytes).unwrap();
    assert_eq!(
        compare_nodes(Some(&truncated), Some(&truncated)),
        DiffKind::Unknown
    );
    let mut wrong = query(vec![]);
    wrong.schema_version = 999;
    assert!(matches!(
        inspect(&1u8, &wrong),
        Err(InspectError::SchemaMismatch { .. })
    ));
}
#[test]
fn float_payload_bits_prevent_false_nan_equality() {
    let a = inspect(&f64::from_bits(0x7ff8_0000_0000_0001), &query(vec![])).unwrap();
    let b = inspect(&f64::from_bits(0x7ff8_0000_0000_0002), &query(vec![])).unwrap();
    assert_eq!(diff(&a, &b).kind, DiffKind::Changed);
}

#[test]
fn handwritten_and_generated_inspectors_have_the_same_projection() {
    #[derive(statelessness_macros::Inspect)]
    struct Generated {
        count: u128,
        items: Vec<String>,
        #[inspect(redact)]
        secret: String,
    }
    struct Handwritten {
        count: u128,
        items: Vec<String>,
    }
    impl Inspect for Handwritten {
        fn inspect(
            &self,
            p: &[PathSegment],
            cx: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            cx.object(
                p,
                "Generated",
                &[
                    FieldView::new("count", "count", &self.count),
                    FieldView::new("items", "items", &self.items),
                    FieldView::redacted("secret", "secret"),
                ],
            )
        }
    }
    fn strip_sources(node: &mut InspectNode) {
        for child in &mut node.children {
            child.source = None;
            strip_sources(&mut child.node);
        }
    }
    let generated = Generated {
        count: u128::MAX,
        items: vec!["a".into(), "b".into()],
        secret: "do-not-read".into(),
    };
    let handwritten = Handwritten {
        count: generated.count,
        items: generated.items.clone(),
    };
    let mut a = inspect(&generated, &query(vec![])).unwrap().node;
    let mut b = inspect(&handwritten, &query(vec![])).unwrap().node;
    strip_sources(&mut a);
    strip_sources(&mut b);
    assert_eq!(a, b);
    assert_eq!(generated.secret, "do-not-read");
}
#[test]
fn huge_map_key_is_bounded_before_owned_key_capture() {
    let values: BTreeMap<String, u8> = [("x".repeat(1_000_000), 1)].into();
    let view = inspect(&values, &query(vec![])).unwrap();
    assert!(view.node.children.is_empty());
    assert!(!view.node.is_complete());
    assert!(view.node.retained_bytes() < 1024);
}

#[test]
// The Cell counts inspections only; key ordering/equality depend exclusively on
// immutable `index`, so instrumentation cannot invalidate BTreeMap ordering.
#[allow(clippy::mutable_key_type)]
fn missing_map_keys_and_far_pages_have_explicit_work_bounds() {
    use std::rc::Rc;
    #[derive(Clone)]
    struct Key {
        index: usize,
        calls: Rc<Cell<usize>>,
    }
    impl PartialEq for Key {
        fn eq(&self, b: &Self) -> bool {
            self.index == b.index
        }
    }
    impl Eq for Key {}
    impl PartialOrd for Key {
        fn partial_cmp(&self, b: &Self) -> Option<std::cmp::Ordering> {
            Some(self.cmp(b))
        }
    }
    impl Ord for Key {
        fn cmp(&self, b: &Self) -> std::cmp::Ordering {
            self.index.cmp(&b.index)
        }
    }
    impl InspectKey for Key {
        fn inspect_key(&self) -> MapKey {
            MapKey::Integer {
                kind: IntegerType::Usize,
                decimal: self.index.to_string(),
            }
        }
        fn matches_inspect_key(&self, _: &MapKey) -> bool {
            self.calls.set(self.calls.get() + 1);
            false
        }
    }
    let calls = Rc::new(Cell::new(0));
    let map: BTreeMap<_, _> = (0..1000)
        .map(|index| {
            (
                Key {
                    index,
                    calls: calls.clone(),
                },
                index,
            )
        })
        .collect();
    let q = InspectQuery {
        path: vec![PathSegment::MapKey(MapKey::Bool(true))],
        limits: InspectLimits {
            max_work: 7,
            ..InspectLimits::default()
        },
        ..InspectQuery::default()
    };
    assert_eq!(inspect(&map, &q), Err(InspectError::WorkLimit));
    assert_eq!(calls.get(), 7);
    let q = InspectQuery {
        path: vec![],
        page: PageRequest {
            offset: 999,
            limit: 1,
        },
        ..q
    };
    let view = inspect(&map, &q).unwrap();
    assert_eq!(
        view.node.completeness,
        Completeness::Partial(IncompleteReason::Work)
    );
    assert_eq!(view.node.children.len(), 0);
}
