# Derive and ModelCodec convenience audit

This is a read-only source audit and a proposed N13 work boundary. No builds,
tests, macro expansions or installed consumers were executed for this document.
It does not change any promoted or experimental profile, or establish parity of
the candidate native value codecs. Rust procedural macro syntax is not a native
language requirement: equivalent explicit builders, generators or idiomatic
declarations can satisfy the observable contract.

## Sources and existing test surfaces

The reference is the actual [value traits](../../src/value_codec.rs),
[derive expansion](../../crates/statelessness-macros/src/codec.rs),
[ModelCodec expansion](../../crates/statelessness-macros/src/model_codec.rs),
[generic parser](../../crates/statelessness-macros/src/parse.rs),
[public macro exports](../../crates/statelessness-macros/src/lib.rs), and
[runtime ModelCodec](../../src/model.rs).

The runtime exposes value traits under `stateless::value_codec` and ModelCodec
at the crate root. The derives are separate exports of `statelessness_macros`,
not automatically imported by using the runtime. Expansion defaults to runtime
path `::stateless`; explicit crate-path options support a renamed dependency.
Generated implementations reference `modeling::MACRO_API_V1` as an API guard.

Existing source tests include [codec behavior](../../crates/macro-qualification/tests/codecs.rs),
[ModelCodec ergonomics](../../crates/macro-qualification/tests/ergonomics.rs),
[hygiene regressions](../../crates/macro-qualification/tests/review_regressions.rs),
and the external, renamed-dependency, package and negative-source harness in
[check-macros.py](../../scripts/check-macros.py). These are relevant proposed
qualification inputs; their presence is not a new pass claim.

## Exact derived value behavior

| Declaration | Encoding and construction |
| --- | --- |
| Named record | Fields in declaration order; no field names, count, delimiter or schema bytes. Decode constructs the named fields. |
| Tuple record | Positional declaration order; no count or delimiter. Decode constructs the tuple record. |
| Unit record | No bytes; decode constructs the unit record. Empty named/tuple records likewise have no field bytes. |
| Enum | Explicit fixed-width little-endian tag followed by that variant's declaration-order fields. Unit, tuple and named variants are supported. |

Enum tag width defaults to u8; `tag_type` permits u8/u16/u32 only. Every variant
requires one explicit decimal u32-parsed tag. Tags must be unique and fit the
chosen width, including its maximum. Sparse tags and source reordering are legal;
the wire tag does not become the declaration index. Rust discriminants are
rejected. Decode reads the tag through `reader.value::<tag_type>()`; an unmapped
tag returns `unknown enum tag` before constructing or decoding variant fields.

Ordinary fields call the field's raw `TraceEncode::trace_encode` when encoding
and `reader.value::<FieldType>()` when decoding. There is no separate record
field frame or temporary encoded blob. A derived decoder itself does not add an
outer value guard: `from_trace` or an enclosing `reader.value` supplies that
guard. Thus derived field nesting, work accounting and first-error behavior must
retain the call graph, not merely produce identical successful bytes.

`#[trace(length = "u8"|"u16"|"u32")]` calls the field's independent
`LengthEncode`/`LengthDecode` directly with width 1/2/4. It does not infer a field
type from its source spelling and does not add an ordinary field value guard.
Custom length-only field types need no ordinary value traits. An outer derived
value still has its own guard when entered through `from_trace`.

`#[trace(with = "path::adapter")]` calls `adapter::encode(&field, out)` and
`adapter::decode(reader)` directly. It adds no generic field value guard and
no automatic field trait bound. Adapters can deliberately define another wire
representation, but must participate in the same sink/reader budgets; arbitrary
adapter allocations or execution are not bounded by the derive. Do not insert a
native whole-callback poison or automatic finish into these raw field calls;
retain the established raw versus stronger-helper distinction.

Field `length` and `with` options are mutually exclusive. Duplicate options,
unknown field/type options, unsupported widths, duplicate/out-of-range tags and
missing tags are rejected. `skip`, default synthesis, implicit enum tags and
automatic field omission are unsupported, not an alternative encoding policy.
The field/variant parser tolerates doc/cfg/cfg_attr/inspect attributes; other
field/variant attributes are rejected. Rust removes disabled fields and variants
before the derive sees them. A different selected declaration can change the
wire and must be represented by application codec version/provenance.

The supported generic path preserves type/lifetime/const parameters and existing
where clauses, removes defaults from implementation parameters, and adds bounds
on complete field types. Ordinary fields require only the selected direction's
trait; length fields require only its corresponding length trait; custom adapter
fields add no automatic trait bound. An enum implementation requires the selected
direction for all non-adapted variant fields, including variants absent from the
current value. Empty/absent data must not bypass unavailable capabilities.
The parser is a deliberately limited token parser; arbitrary Rust type syntax,
complex higher-ranked/associated-const syntax or borrowed-value decoding is not
an inferred qualification envelope.

Encoding derives provide `trace_schema()` from the derive input's token-stream
string and enum `trace_variant()` as the selected source variant identifier.
Non-enum variant labels use the trait default None; decode derives add neither
diagnostic method. Schema spelling is not canonical wire metadata, a schema hash,
a version, or a compatibility proof. Native builders should offer meaningful
stable diagnostic labels without promising Rust token spelling or Rust compiler
diagnostic text. Renaming a variant label need not change an explicitly tagged
wire value; consumers must not derive tags or compatibility from that label.

## ModelCodec delegation

The ModelCodec derive applies to a model struct, including named, tuple or unit
forms; it does not infer behavior from its model fields. It generates the existing
runtime trait and retains generics/where clauses, with these independent bounds:

| Associated value | Required capability |
| --- | --- |
| State | TraceEncode and TraceDecode |
| Input | TraceEncode and TraceDecode |
| Output | TraceEncode only |

There is no generated output decoder. An encode-only output must work through
real recording, replay of freshly produced outputs, Recorder and evidence paths;
it must not be given a fabricated decoder to fit a convenience API. Historical
output decoding remains a separate capability.

Options are `crate`, `version` and `decode_limits`. Duplicate/unknown options,
non-struct targets, and a version other than a positive u32 literal are rejected.
Version defaults to 1. Every generated encode/decode method checks that the
current model metadata's `codec_version` equals that declared version, returning
`ModelCodec derive version does not match Model metadata` before touching the
value, sink or reader when it differs. This is not a new model identity or a
substitute for replay's full metadata compatibility checks.

Legacy encode methods call `TraceEncode::trace_bytes` with usize::MAX; they are
not a bounded temporary-allocation guarantee. Preferred encode-into methods call
the raw trait with the caller's sink and its shared budget. State/input decoders
call `TraceDecode::from_trace` with the configured limits expression, or fresh
default DecodeLimits, for each call. They therefore enforce input admission,
one outer guard, aggregate budgets and trailing-byte rejection. The expression
is emitted at each decode call site; encoding does not evaluate it. The derive
does not add checks, reducers, model identity, engine stamps or output decoding.

The separate `#[model(..., codec)]` adapter is another convenience surface, not
proof that handwritten-model ModelCodec derivation is covered. Its broader model
generation features are outside this audit's proposed implementation slice.

## Native surface and proposed equivalent

Current candidates offer scalar/composite value descriptors and public custom
callbacks: Python Schema/OptionOf/VectorOf/ArrayOf/TupleOf/OracleStateOf,
TypeScript valueCodecs/customValueSchema/withLength, Go ValueCodec and composite
factories, and Swift TraceValueCodec with independent directional callbacks.
Their runtime ModelCodec/CodecModel or model codec hooks already exist. These
permit handwritten adapters, but do not by themselves supply a reusable named
record/enum declaration or model-codec binding helper with the above contracts.
No first-class equivalent of those convenience surfaces was identified in the
inspected value-builder files; this is a scoped surface gap, not inability to
implement the bytes manually.

Propose explicit ordered record fields, tuple/unit record constructors, and tagged
enum cases with explicit width/tag/construct/extract operations. Use native
dataclasses/decorators or descriptors in Python, typed descriptors or optional
code generation in TypeScript, generated/typed callbacks in Go, and typed builder
closures or separate opt-in generation in Swift. Reflection, JSON serialization
and exhaustive Rust syntax emulation are unnecessary. Keep custom adapters and
length overrides explicit and independently directional. Preserve immutable
configuration, detached diagnostic descriptions and native callback lease rules.

Add an opt-in model-codec binder accepting state/input codecs, an output encoder,
declared codec version and decode limits. Preserve current model methods and
metadata, reject missing directions before use, and wire both legacy and preferred
bounded methods consistently. Do not require output decoding, infer versions from
schema labels, introduce a mandatory registry, or silently supply record/enum
Rust64 allocation weights. Unknown custom record/enum collection element layouts
still require an explicit validated weight or measured exact-shape descriptor.

## Proposed finite acceptance evidence

Author independent static wire goldens before running a reference. Suggested
positive families are named/tuple/unit/empty records; nested records; unit/tuple/
named enum cases; sparse/reordered tags; maximum u8/u16/u32 tags; all three field
length widths; raw adapter bytes differing from the builtin representation;
generic and const-generic fields; and encode-only, decode-only and length-only
custom fields. Include conditional declaration snapshots without claiming that
changing cfg preserves a format.

Small independently calculated starting expectations, not executed fixtures:

| Declared value | Expected hex |
| --- | --- |
| Record fields u8=7, bool=true, u16=0x1234 | `07013412` |
| Unit record or an empty record | empty |
| u16-tag enum case tag=400, one u16 payload=0x1234 | `90013412` |
| Record field String="abc" with u8 length override | `03616263` |
| Record field u16=0x1234 through an explicit big-endian adapter | `1234` |

A guarded derived record with two ordinary scalar fields requires three value
entries and depth two. A guarded derived record with only a directly u8-length
encoded String="abc" field requires five work units (record entry, length entry,
three UTF-8 bytes) and depth two; inserting an extra ordinary field guard changes
those expectations. A raw derived call has no record entry of its own.

Reader tests should distinguish ordinary field guards from direct length and
adapter calls using exact work/depth budgets, partial bytes and callback logs.
Include every proper prefix and trailing-byte rejection, unknown tags before
payload callbacks, first-error stickiness, bounded output admission and prefix
retention, nested aggregate collection quotas, zero-size fields, and collection
storage with explicit record weights. Verify diagnostics separately from bytes.

Negative declarations should cover duplicate/missing/overflow tags, invalid tag
width and struct tag option, discriminants, skip/default/unknown/duplicate field
options, conflicting length/adapter, unknown/duplicate ModelCodec options, zero/
overflow versions, non-struct model derive, unavailable ordinary/length/custom
directions and unsupported default field types. Rust uses real compile-fail
fixtures; native languages may reject at type checking, generation or builder
construction, with meaningful errors and no partial publication. Do not require
identical compiler text or claim a runtime execution for a static-only rejection.

Finally exercise a real application model with version match/mismatch on every
codec method, per-call custom decode limits, bad/trailing input, and an encode-only
output. Record bounded evidence with the preferred hooks, then cross-read and
replay among all five implementations, retaining producer configuration and
checking actual reducer/checker/codec counts. Run installed-package and existing
profile regressions separately. Builds and executable qualification remain
future work through Kyoto; this audit changes no existing qualification result.
