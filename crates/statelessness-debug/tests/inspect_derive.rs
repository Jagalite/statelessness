//! Compile-and-run qualification of the optional dependency-free derive.
#![allow(dead_code, clippy::non_minimal_cfg)]
use statelessness_debug::inspect::{
    Inspect, InspectContext, InspectError, InspectNode, InspectQuery, IntegerType, NodeKind,
    PathSegment, Scalar, inspect,
};
use statelessness_macros::Inspect;

fn project(value: &dyn Inspect, path: Vec<PathSegment>) -> InspectNode {
    inspect(
        value,
        &InspectQuery {
            path,
            schema_version: value.schema().version,
            ..InspectQuery::default()
        },
    )
    .unwrap()
    .node
}
fn field(id: &str) -> PathSegment {
    PathSegment::Field(id.to_owned())
}
fn integer(kind: IntegerType, value: impl ToString) -> NodeKind {
    NodeKind::Scalar(Scalar::Integer {
        kind,
        decimal: value.to_string(),
    })
}

struct SecretWithoutInspect;
struct PanicInspector;
impl Inspect for PanicInspector {
    fn inspect(
        &self,
        _: &[PathSegment],
        _: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        panic!("redacted data must never reach an inspector")
    }
}

#[derive(Inspect)]
#[inspect(version = 7, label = "Account display")]
struct Account<T> {
    #[inspect(id = "account-number", label = "Account number")]
    number: u128,
    #[inspect(redact, label = "Credential")]
    secret: T,
    #[inspect(redact)]
    dangerous: PanicInspector,
    r#type: bool,
}

#[test]
fn named_fields_have_stable_ids_labels_version_and_source() {
    let state = Account {
        number: u128::MAX,
        secret: SecretWithoutInspect,
        dangerous: PanicInspector,
        r#type: true,
    };
    let schema = state.schema();
    assert_eq!(schema.name, "Account display");
    assert_eq!(schema.version, 7);
    let source = schema.source.unwrap();
    assert!(source.file.ends_with("inspect_derive.rs"));
    assert!(source.line > 0 && source.column > 0);
    let node = project(&state, vec![]);
    assert_eq!(
        node.kind,
        NodeKind::Object {
            name: "Account display".into()
        }
    );
    assert_eq!(node.child_count, Some(4));
    assert_eq!(node.children[0].segment, field("account-number"));
    assert_eq!(node.children[0].label, "Account number");
    assert!(node.children.iter().all(|f| f.source.is_some()));
    assert_eq!(
        node.children[0].node.kind,
        integer(IntegerType::U128, u128::MAX)
    );
    assert_eq!(node.children[1].node.kind, NodeKind::Redacted);
    assert_eq!(node.children[2].node.kind, NodeKind::Redacted);
    assert_eq!(node.children[3].segment, field("type"));
    assert_eq!(
        project(&state, vec![field("account-number")]).kind,
        integer(IntegerType::U128, u128::MAX)
    );
}

#[test]
fn redacted_paths_never_invoke_or_require_an_inspector() {
    let state = Account {
        number: 1,
        secret: SecretWithoutInspect,
        dangerous: PanicInspector,
        r#type: false,
    };
    assert_eq!(
        project(&state, vec![field("secret")]).kind,
        NodeKind::Redacted
    );
    assert_eq!(
        project(&state, vec![field("dangerous")]).kind,
        NodeKind::Redacted
    );
}

#[derive(Inspect)]
struct Tuple<T: Clone = u16>(
    #[inspect(id = "value")] T,
    #[inspect(redact)] SecretWithoutInspect,
)
where
    T: PartialEq;
#[derive(Inspect)]
struct Unit;
#[derive(Inspect)]
struct EmptyNamed {}
#[derive(Inspect)]
struct EmptyTuple();
#[derive(Inspect)]
struct ConstUnit<const N: usize = 2>;
#[derive(Inspect)]
enum Never {}

#[test]
fn tuple_unit_and_empty_shapes_compile_and_inspect() {
    let node = project(&Tuple(42u16, SecretWithoutInspect), vec![]);
    assert_eq!(node.children[0].segment, field("value"));
    assert_eq!(node.children[0].node.kind, integer(IntegerType::U16, 42));
    assert_eq!(node.children[1].segment, field("1"));
    assert_eq!(node.children[1].node.kind, NodeKind::Redacted);
    for value in [
        &Unit as &dyn Inspect,
        &EmptyNamed {},
        &EmptyTuple(),
        &ConstUnit::<3>,
    ] {
        let node = project(value, vec![]);
        assert_eq!(node.child_count, Some(0));
        assert!(node.children.is_empty());
    }
}

#[derive(Inspect)]
#[inspect(label = "Operation")]
enum Operation<T> {
    #[inspect(id = "idle-state", label = "Idle")]
    Idle,
    #[inspect(id = "sending", label = "Send value")]
    Send(#[inspect(label = "Payload")] u64, #[inspect(redact)] T),
    #[inspect(id = "failed")]
    Failed {
        #[inspect(id = "status", label = "Status code")]
        code: u32,
        #[inspect(redact)]
        secret: T,
    },
    EmptyTuple(),
    EmptyNamed {},
}

#[test]
fn variants_support_each_shape_and_structural_paths() {
    let idle = project(&Operation::<SecretWithoutInspect>::Idle, vec![]);
    assert!(
        matches!(idle.kind, NodeKind::Enum { ref variant_id, ref variant_label, .. } if variant_id == "idle-state" && variant_label == "Idle")
    );
    let send = Operation::Send(999, SecretWithoutInspect);
    let send_node = project(&send, vec![]);
    assert!(
        matches!(send_node.kind, NodeKind::Enum { ref variant_id, .. } if variant_id == "sending")
    );
    assert_eq!(
        project(
            &send,
            vec![PathSegment::Variant("sending".into()), field("0")]
        )
        .kind,
        integer(IntegerType::U64, 999)
    );
    assert_eq!(
        project(
            &send,
            vec![PathSegment::Variant("sending".into()), field("1")]
        )
        .kind,
        NodeKind::Redacted
    );
    let failed = Operation::Failed {
        code: 500,
        secret: SecretWithoutInspect,
    };
    assert_eq!(
        project(
            &failed,
            vec![PathSegment::Variant("failed".into()), field("status")]
        )
        .kind,
        integer(IntegerType::U32, 500)
    );
    for state in [
        Operation::<SecretWithoutInspect>::EmptyTuple(),
        Operation::EmptyNamed {},
    ] {
        assert!(matches!(
            project(&state, vec![]).kind,
            NodeKind::Enum { .. }
        ));
    }
}

trait HasValue {
    type Value;
}
struct Provider;
impl HasValue for Provider {
    type Value = u64;
}
#[derive(Inspect)]
struct Qualified<'a, T: HasValue, const N: usize = 2>
where
    <T as HasValue>::Value: Clone,
{
    value: &'a <T as HasValue>::Value,
    array: [u16; N],
    nested: std::collections::BTreeMap<String, std::vec::Vec<u8>>,
    #[inspect(redact)]
    marker: std::marker::PhantomData<T>,
}

#[test]
fn generics_lifetimes_consts_qualified_types_and_where_clauses() {
    let number = 5;
    let state = Qualified::<Provider> {
        value: &number,
        array: [3, 7],
        nested: [("items".to_owned(), vec![8, 9])].into(),
        marker: std::marker::PhantomData,
    };
    assert_eq!(
        project(&state, vec![field("value")]).kind,
        integer(IntegerType::U64, 5)
    );
    assert_eq!(
        project(&state, vec![field("array"), PathSegment::Index(1)]).kind,
        integer(IntegerType::U16, 7)
    );
    assert_eq!(project(&state, vec![]).child_count, Some(4));
}

mod renamed {
    use statelessness_debug as display_runtime;
    #[derive(statelessness_macros::Inspect)]
    #[inspect(crate = "self::display_runtime", label = r#"Display "view""#)]
    pub struct Renamed {
        #[inspect(id = "escaped\x2did", label = "line\n\u{2764}")]
        pub value: u8,
    }
}

#[test]
fn runtime_paths_and_escaped_or_raw_metadata_are_supported() {
    let state = renamed::Renamed { value: 1 };
    assert_eq!(state.schema().name, "Display \"view\"");
    let node = project(&state, vec![]);
    assert_eq!(node.children[0].segment, field("escaped-id"));
    assert_eq!(node.children[0].label, "line\n❤");
}

#[derive(Inspect)]
struct Conditional {
    #[cfg(any())]
    missing: NotDefined,
    #[cfg(all())]
    present: u8,
}
#[derive(Inspect)]
enum ConditionalEnum {
    #[cfg(any())]
    Missing(NotDefined),
    Present(u8),
}

#[test]
fn rust_cfg_is_respected() {
    assert_eq!(
        project(&Conditional { present: 4 }, vec![]).child_count,
        Some(1)
    );
    assert!(
        matches!(project(&ConditionalEnum::Present(3), vec![]).kind, NodeKind::Enum { ref variant_id, .. } if variant_id == "Present")
    );
}

#[derive(Inspect)]
#[allow(non_camel_case_types)]
struct Hygiene<__inspect_path, __inspect_cx> {
    value: __inspect_path,
    #[inspect(redact)]
    secret: __inspect_cx,
}
#[derive(Inspect)]
#[allow(non_camel_case_types)]
enum VariantHygiene<__inspect_field_0_0> {
    Value(__inspect_field_0_0),
}

#[test]
fn generated_bindings_do_not_shadow_caller_identifiers() {
    let state = Hygiene {
        value: 1u8,
        secret: SecretWithoutInspect,
    };
    assert_eq!(
        project(&state, vec![field("value")]).kind,
        integer(IntegerType::U8, 1)
    );
    assert!(matches!(
        project(&VariantHygiene::Value(2u8), vec![]).kind,
        NodeKind::Enum { .. }
    ));
}

#[derive(Inspect)]
struct Unsized<T: ?Sized> {
    value: T,
}

#[test]
fn unsized_generic_tail_uses_reference_inspection() {
    let sized = Unsized { value: [1u8, 2, 3] };
    let state: &Unsized<[u8]> = &sized;
    assert_eq!(
        project(&state, vec![field("value"), PathSegment::Index(2)]).kind,
        integer(IntegerType::U8, 3)
    );
}

#[derive(Inspect, statelessness_macros::TraceEncode, statelessness_macros::TraceDecode)]
struct ExactAndDisplay {
    #[inspect(redact)]
    secret: u64,
    #[inspect(id = "display-value")]
    value: u8,
}
#[derive(Inspect, statelessness_macros::TraceEncode, statelessness_macros::TraceDecode)]
enum ExactAndDisplayEnum {
    #[trace(tag = 4)]
    #[inspect(id = "value", label = "Value")]
    Value(#[inspect(redact)] u8),
    #[trace(tag = 5)]
    #[inspect(id = "empty")]
    Empty,
}

#[test]
fn derive_metadata_coexists_with_exact_codecs_without_redacting_trace_bytes() {
    use stateless::value_codec::{TraceDecode as _, TraceEncode as _};
    let state = ExactAndDisplay {
        secret: 123,
        value: 9,
    };
    let exact = state.trace_bytes(32).unwrap();
    assert_eq!(&exact[..8], &123u64.to_le_bytes());
    let restored = ExactAndDisplay::from_trace(&exact, Default::default()).unwrap();
    assert_eq!(restored.secret, 123);
    assert_eq!(
        project(&restored, vec![field("secret")]).kind,
        NodeKind::Redacted
    );
    let state = ExactAndDisplayEnum::Value(42);
    assert_eq!(state.trace_bytes(32).unwrap(), [4, 42]);
    let restored = ExactAndDisplayEnum::from_trace(&[4, 42], Default::default()).unwrap();
    assert_eq!(
        project(
            &restored,
            vec![PathSegment::Variant("value".into()), field("0")]
        )
        .kind,
        NodeKind::Redacted
    );
}

#[test]
fn default_schema_names_distinguish_qualified_types_and_generics() {
    mod first {
        #[derive(statelessness_macros::Inspect)]
        pub struct Same(pub u8);
    }
    mod second {
        #[derive(statelessness_macros::Inspect)]
        pub struct Same(pub u8);
    }
    #[derive(statelessness_macros::Inspect)]
    struct Generic<T>(T);
    assert_ne!(first::Same(1).schema().name, second::Same(1).schema().name);
    assert_ne!(Generic(1u8).schema().name, Generic(1u64).schema().name);
}
