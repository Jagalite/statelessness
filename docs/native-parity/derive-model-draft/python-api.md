# Python N13 declaration and ModelCodec API draft

Status: design only. The names and examples below are proposed APIs, not imports
available in the package. This document changes no runtime, package, profile or
qualification result. It follows [the derive audit](../DERIVE-MODEL-CODEC-AUDIT.md)
and the current [value codec surface](../../../python/src/statelessness/value_codec.py).
Execution, negative-declaration qualification and installed consumers are future
work through Kyoto.

## Explicit declarations

Use an opt-in declaration module, provisionally `statelessness.codec_declarations`.
Do not infer codecs from annotations, field reflection, JSON, attribute discovery
or the spelling of Python types. Initial scope is explicit declarations; a future
dataclass decorator can translate to these same declarations without becoming the
wire specification.

```python
@dataclass(frozen=True)
class Header:
    kind: int
    live: bool
    count: int

HEADER = ordered_record(
    "Header",
    value_type=Header,
    fields=(
        field("kind", U8, get=lambda value: value.kind),
        field("live", BOOL, get=lambda value: value.live),
        field("count", U16, get=lambda value: value.count),
    ),
    construct=lambda values: Header(*values),
)
# Header(7, True, 0x1234) -> 07013412
```

`construct` receives one declaration-order tuple, including for named records.
Decode invokes the constructor exactly once only after every field has decoded
successfully; a field error prevents construction entirely. Field names are
diagnostic labels; no names, field count, delimiter or schema text enter the wire.
An explicit callback avoids interpreting a field name as an attribute path. `value_type` is checked by identity (`type(value) is value_type`)
before extraction. It is an application type declaration, not a reflection source.
A constructor must return that exact type. Extraction and construction callbacks
are synchronous, deterministic and must not mutate supplied values.

`tuple_record(name, value_type=..., fields=(positional_field(schema, get=...),
...), construct=...)` preserves positional order and uses the same tuple-based
constructor. `unit_record(name, value_type=..., construct=lambda values: Tick())`
is a zero-field record. Empty named and tuple records are also legal and emit no
field bytes. Their forms remain distinct diagnostic declarations despite equal
wire bytes. No implicit constructor, field default or skipped field is synthesized.

Each field has exactly one route:

| Declaration | Encode | Decode |
| --- | --- | --- |
| `field(name, schema, get=...)` | `encode_raw(schema, value, sink)` | `reader.value(schema)` |
| `field(name, schema, get=..., length=1)` | `encode_length_raw(schema, value, 1, sink)` | `decode_length_raw(schema, 1, reader)` |
| `adapted_field(name, adapter, get=...)` | adapter raw encoder | adapter raw decoder |

Length declarations accept exact integer widths 1, 2 or 4, corresponding to the
Rust derive's u8/u16/u32 annotations. The public custom raw length API can accept
other u8 widths, but that does not broaden this declaration option. An adapter
may explicitly choose another representation.

`adapter(encode=callback_or_none, decode=callback_or_none, name="big-endian-u16")`
captures the supplied directions independently. Its encoder receives `(value,
sink)` and returns None; its decoder receives `reader` and returns the value.
Adapter decoding is a direct call, without `reader.value`. Adapter encoding uses
a fresh raw child sink lease, revoked on every exit; it neither finishes the
shared writer nor adds whole-callback poisoning. A rejected writer operation
remains sticky, but a returned adapter error alone does not poison accepted
prefix bytes. A directly caught raw decoder validation error likewise retains
the existing raw/guarded distinction. Async callbacks and non-None encoder
returns are errors. Arbitrary callback execution/allocation is not bounded by the
codec's logical budgets.

A field cannot combine length and adapter routes. Unknown/duplicate options,
missing extractors for a requested encoding direction, conflicting options,
invalid widths, duplicate names and unsupported defaults/skip policies are
rejected before a descriptor is returned. A mapping of keyword options must not
be used to conceal duplicates; the builder API accepts explicit options, and any
future source generator must diagnose duplicate syntax before building it.

## Tagged enums

```python
COMMAND = tagged_enum(
    "Command",
    tag_type=U16,
    select=lambda value: value.variant,
    cases=(
        case("Reset", tag=9, fields=(),
             construct=lambda values: Command.reset()),
        case("Set", tag=400,
             fields=(field("amount", U16, get=lambda value: value.amount),),
             construct=lambda values: Command.set(values[0])),
    ),
)
# Command.set(0x1234) -> 90013412
```

`select` runs once per encoding and returns an exact string case label. An
unknown label is an encoding error. Labels are unique, immutable diagnostics;
explicit tags determine the wire independently of labels and declaration order.
The selected case's fixed-width tag is written before its fields. Payload fields
use precisely the record routes above. Extractor/selector exceptions are native
callback errors; purity is required rather than treating these callbacks as
Rust's infallible pattern matching.

The default tag schema is U8. Only the builtin U8/U16/U32 identities are accepted
for `tag_type`; a diagnostic name cannot impersonate one. Every case requires an
explicit exact-int tag, with bool and integer subclasses rejected. Tags must be
unique and fit the chosen width, including its maximum. No source-order tags,
discriminants or automatic tag assignment exist. Unit, positional and named
payload forms may use separate case constructors sharing the same recipe.

Raw enum decoding reads its tag through `reader.value(tag_type)`, looks it up,
then decodes fields and invokes that case's constructor once. An unknown tag
returns `unknown enum tag` before payload decoders or constructors run. It is
recorded as sticky only if propagated through a guarded value entry. A guarded
enum has its own outer entry; raw enum decoding does not invent that entry.

## Directions, layouts and immutable recipes

The returned descriptors implement the existing Schema contract. Ordinary
encoding, ordinary decoding, length encoding and length decoding remain four
independent capabilities. Record/enum declarations do not automatically gain
length capabilities. Each direction is inferred as the actual intersection of
its required callbacks and field routes. For enums this includes all cases, not just the selected case.
The default builder may honestly publish an encode-only, decode-only,
bidirectional or neither-direction descriptor; it does not fabricate callbacks or
reject a declaration merely because neither ordinary direction is available.
Unit/empty records do not supply a missing constructor, and absent/empty values
do not bypass unavailable capabilities. An encode-only recipe may omit
constructors; a decode-only recipe may omit extractors and enum selection.

Optional `require=("encode",)` or `require=("decode",)` assertions eagerly reject
missing requested capabilities before publication. Requiring both is also legal.
`require` is an assertion, not a direction selection or filter: requiring encoding
on a bidirectional declaration preserves decoding, and no requirement can create
an unavailable capability. Unavailable operations always fail before
consuming/emitting bytes at their public boundary. The same assertion policy may
name independently available length capabilities, without supplying a default
length implementation for records or enums.

Length-only fields require only the respective length capability. Adapter fields
require only their explicitly supplied adapter direction. No fake ordinary field
encoder/decoder is installed to satisfy a convenience API. An ordinary field adds
a guarded decode entry, while length and adapter fields do not. These choices
are part of the descriptor and cannot change according to the current value.

Declarations are frozen, slotted objects. Builders accept exact strings and
explicit ordered field/case sequences, copy their outer configuration to tuples,
validate tags/options and resolve directional callbacks before publication. They
capture callback bindings, capability flags, route widths and optional allocation
weights once; rebinding a caller's field list, adapter method, custom schema layout
or label container cannot change the recipe. They do not deepcopy application
values, callable closures or arbitrary descriptor objects. A normalized sealed
callback holder can feed the existing raw helpers without re-reading a mutable
custom object's method binding on each operation. Builtin schemas are
already immutable; custom schema/adapter callbacks remain cooperative and must
preserve their declared behavior. This is API integrity, not a Python sandbox.

Record and enum layouts default to unknown. `layout=Rust64Layout(...)` explicitly
supplies a logical allocation weight or measured exact-shape descriptor, under
the existing profile distinction. Never infer Rust layout from payload length,
field count, tag width, Python object size or diagnostic names. A top-level value
can decode without an unused layout. When it becomes a collection element, the
collection captures its declared weight and applies existing element/allocation/
work admission before storage. Encoding requires no allocation descriptor.

A recipe's `trace_schema()` returns a stable, detached diagnostic description
based on its explicit label and ordered declaration. Enum `trace_variant(value)`
returns its selected label; non-enums return None. Labels and descriptions are
not wire tags, schema hashes, versions or replay compatibility identities.
A future generator should target an explicit declaration API guard such as
`DECLARATION_API_V1`; incompatible generator/runtime APIs fail at declaration
construction, independently of application codec versions. No Rust macro import
or Python macro-equivalent compiler text is promised.

## ModelCodec binding

```python
BINDING = model_codec(
    state=STATE,                 # ordinary encode + ordinary decode
    input=INPUT,                 # ordinary encode + ordinary decode
    output=ENCODE_ONLY_EVENT,    # ordinary encode only; no fabricated decoder
    version=1,
    decode_limits=lambda model: DecodeLimits(work=model.decode_work),
)
model = BINDING.bind(existing_model)
```

The binder validates state/input encode+decode and output encode directions at
construction. It does not require length traits or allocation layouts unless
those are used within a declared field/collection route. `version` is an exact
positive u32 integer, default 1. Unknown options, duplicate generated options,
zero/overflow/bool versions and unavailable required directions are declaration
errors. An encode-only output descriptor is a first-class positive case.

`bind` returns a wrapper; it does not mutate the model class or instance. The
wrapper replaces only the eight codec methods in its view: three legacy encoders,
three preferred encode-into hooks, and state/input decoders. It supplies no output
decoder. Existing metadata, reducer, checks, estimates, generation, simplification
and other optional model methods retain their actual receiver and availability;
an absent optional method must not become an inherited callable placeholder.
Explicit binding intentionally selects these codecs even if the underlying
model has handwritten codecs. The underlying model remains usable independently.

Every codec method first reads the current model metadata once and compares
`codec_version` with the binding version. A mismatch returns
`ModelCodec derive version does not match Model metadata` before value extraction,
byte admission/copy, sink access, limits-provider evaluation or codec callbacks.
The gate does not alter metadata, supply engine stamps or replace replay's full
metadata compatibility checks.

Preferred hooks invoke the descriptor's raw encoder on a revocable child of the
caller's sink: shared quota, existing prefix, no temporary blob, no added finish
or binder-level callback poisoning. The surrounding engine retains its own
callback/error policy. Legacy encoders allocate a local buffer with U64_MAX,
invoke that same raw implementation, finish and return immutable bytes. They
are not a bounded-allocation promise. A shared internal implementation avoids
calling a second public method and checking metadata twice.

Each state/input decode, after its version gate, evaluates the decode-limits
provider once, requires a synchronous exact DecodeLimits result, snapshots its
five primitives and calls `decode_canonical`. This admits input bytes before
owned copying, adds exactly one outer value entry, and checks trailing bytes.
A supplied DecodeLimits constant is also supported; omission creates fresh
defaults per decode. Encoding never evaluates the provider. Provider exceptions
are contextual model errors and do not trigger repeated metadata/model/codec
calls. No checks, reducers or initial states run during binding or codec calls.

## Qualification boundary

The audit's independently calculated record/enum bytes and work/depth examples
should become shared fixtures before implementing these builders. Add malformed
builder tests, all four directional custom-field combinations, raw prefix/error
retention, exact work/depth routes, unknown-tag-before-payload logs, declaration
mutation probes and explicit collection weights. Keep native declaration
rejection separate from executed Rust compile-fail evidence.

A real application must use the binding with matching/mismatching versions on
all eight methods, per-call limits, encode-only output, preferred bounded
recording, replay, Recorder and causal evidence. Verify model/checker/codec counts,
then installed cross-language consumers and existing profile regressions. This
draft neither authorizes changes to the currently frozen package nor claims
those future qualification gates have passed.
