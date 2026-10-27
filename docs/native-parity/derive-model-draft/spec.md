# Derive/model-codec v1 independent design draft

Status: proposed N13 acceptance contract for peer review. No implementation,
execution, compiler expansion or parity claim accompanies this draft. The JSON
beside it contains independently calculated expectations, not engine output.
Existing runtime packages, tests, fixtures, promoted profiles and qualification
inputs remain unchanged. Rust source authority is the parent
[audit](../DERIVE-MODEL-CODEC-AUDIT.md), especially `codec.rs`, `model_codec.rs`
and the independent value/length traits.

## Observable scope

Provide native ordered named/tuple/unit-record and tagged-enum declarations plus
opt-in ModelCodec binding. Native builders, typed declarations or code generation
may replace Rust procedural macros. No Rust parser, identical source syntax or
compiler diagnostic wording is required. No JSON/reflection-based wire fallback,
implicit field default, skipped field, implicit enum ordinal or output decoder
may be invented to make an unsupported declaration pass.

Record encoding concatenates declared fields without names/count/framing. Unit
and empty records encode no bytes. Enum encoding writes one explicit u8/u16/u32
little-endian tag then the selected case's ordered fields. Tags are decimal,
unique, sparse/reorderable and inclusive of the chosen maximum; every case needs
an explicit tag. Unknown tags fail before payload decoding or construction.

An ordinary field uses raw TraceEncode and guarded `reader.value<Field>()`.
A length field uses direct LengthEncode/LengthDecode at declared width 1/2/4.
A custom adapter uses its direct encode/decode callback. Neither special field
adds an ordinary field guard or finishes a shared sink/reader. All nested work,
depth, element and requested-allocation budgets remain shared. The public
top-level decoder supplies exactly one outer value guard and trailing rejection.
Raw declaration decoding supplies no outer guard. Preserve first structural
error and partial encoded prefixes, including the established distinction
between returned raw callback errors and recorded sink failures.

Field options `length` and `adapter` are mutually exclusive. Reject duplicates,
unsupported/unknown options, invalid tag width, duplicate/missing/overflow tags,
skip/default requests and implicit discriminants. For native builder arguments,
validate exact finite integers before casts; do not truncate, wrap or infer tags
from labels. The selected declaration is the format: cfg/source filtering can
change it and is not a promise of feature-independent compatibility.

Each ordinary direction requires that direction of every non-adapted field in
the complete declaration, including unselected enum cases. Length fields require
only the matching length capability. Adapter fields require only their matching
adapter callback. Missing directions fail before bytes or constructor callbacks,
including unit variants, absent options, empty vectors and zero-sized arrays.
An explicitly requested direction is rejected at construction/type checking when
unavailable. A builder with an inferred-direction mode may instead expose the
intersection of its fields' capabilities, including neither direction; neither
operation then succeeds. Not every factory must offer that inferred mode or
publish a descriptor with neither direction. No fake callback is supplied.
Encoding requires neither a decoder nor an otherwise unused layout descriptor.
Generic/typed native declarations must preserve these directional requirements;
dynamic builders provide equivalent validated immutable capability metadata.

Field lists, tags, labels, adapter references, captured layout weights and
direction metadata become immutable once the descriptor is built. Constructors
and field projections participate in the directions that require them; missing
construction support must not publish a usable decoder. Constructors
run only after their fields decode successfully, in declaration order; a failed
tag/field never publishes a partially constructed value. Custom callbacks remain
cooperative: arbitrary callback allocations, external mutation and malicious
cloning are not bounded or sandboxed by these helpers.

## Diagnostics and layout

Separate wire identity from diagnostic schema and variant names. Rust derives
return source token spelling for trace_schema and the selected enum identifier
for trace_variant; non-enums use None. Native equivalents can expose explicit
stable labels. Test labels as diagnostic evidence independently of tag bytes;
renaming a label must not silently renumber a tag. No schema compatibility or
version is inferred from a label. Decode support does not require diagnostic
encoding support.

Top-level record/enum decoding does not charge an invented object size. When a
record/enum is a collection element, require its explicit validated logical
Rust64 element weight or an independently measured exact-shape descriptor.
Never infer generic Rust struct/enum layout from summed wire widths, field
alignment guesses or native heap measurements. The draft collection fixture
declares Pair's storage weight explicitly as 4; this is a cooperative fixture
weight, not a new measured layout qualification.

## ModelCodec binding

An explicit model binding supplies State encode+decode, Input encode+decode,
Output encode only, a positive u32 declared codec version and decode limits.
Version defaults to 1 and limits default to the canonical value defaults.
Every one of the eight generated methods checks current model metadata's
codec_version against that declaration before value/sink/reader callbacks.
Mismatch is an error, not an automatic migration. Preserve existing model
identity and full replay compatibility checks; binding does not stamp a new
model version or engine identity.

Legacy state/input/output encode methods use the same raw descriptor with an
unbounded logical maximum, then finish. Preferred encode-into methods use the
caller's existing shared bounded sink without clearing, framing or implicitly
finishing it. State/input decode methods construct a fresh reader per call,
evaluate the configured limits supplier per call, enter one outer guard and
require no trailing bytes. Encoding never evaluates decode limits. Do not add an
Output decoder. Binding must compose with an encode-only Output through real
recording, replay of actual outputs, Recorder and evidence capture.

## Proposed evidence protocol

`cases.json` is a draft fixture vocabulary, not a promoted runner wire protocol.
The finite schema registry names ordered fields/cases and explicit override
callbacks. Values are lists for tuple records, objects for named records, null
for unit records and `{case, fields}` for enums. Callback IDs resolve only to
hand-authored fixture callbacks described in the registry; they are not arbitrary
executable source. Exact errors below are Rust reference strings where a public
runtime path exists; native declarations may use category-equivalent diagnostics.

Each executable positive row should independently compare exact bytes, decode
the expected value, reject every proper prefix/trailing byte and verify owned
results. Zero-byte values have no proper prefix. A nonzero minimum work/depth
expectation belongs to guarded decoding unless entry explicitly says raw.
At exact budget success, one-under work/depth/allocation/element or byte-budget
rows must fail for the stated reason. Never derive expected bytes, counts or
limits by running the implementation under test.

Directional positive rows declare which operation is available; an encode-only
row is not sent through a fabricated round-trip decoder. Reader-script rows
separately distinguish a raw returned unknown-tag error that can be swallowed
from a guarded unknown-tag error and an earlier sticky failed tag read. Every
script action calls a real public raw/guarded reader operation, not a replacement
decoder algorithm. A reader script's `terminal_error` is the outcome of its final
explicit finish action, not an additional automatic finish. Missing-direction
declaration failures are not manufactured decoder callbacks in those scripts.

Declaration negatives are real Rust compile-fail cases or native public
construction/type-check/code-generation failures; report those separately from
runtime execution. Adapter exceptions, missing directions and constructor errors
need focused native ownership/lease regressions. The independent registry does
not require a dynamic Rust codec implementation: reference tests should instantiate
concrete derives and call real public traits, keeping JSON conversion outside the
codec algorithm.

After peer review and an explicit implementation slice, qualify installed native
packages plus the real Rust reference with fresh producer/consumer processes.
Use both finite record/enum byte interchange and a bound application model with
encode-only Output, explicit limits/version mismatch controls and exact callback
counts. Preserve producer configuration and existing baseline profiles. Build and
executable qualification will go through Kyoto; this draft authoring runs none.
