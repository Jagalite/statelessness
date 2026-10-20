# Swift ordered records, tagged enums and ModelCodec binding: API proposal

Static design only, 2026-10-20. This document changes no runtime, package,
deployment minimum, profile or qualification result. The signatures below are
proposed interfaces, not compiled examples. The existing N11 implementation and
its Kyoto receipts remain separate evidence.

References: [N13 audit](../DERIVE-MODEL-CODEC-AUDIT.md),
[Rust derive expansion](../../../crates/statelessness-macros/src/codec.rs),
[ModelCodec expansion](../../../crates/statelessness-macros/src/model_codec.rs),
[current Swift value descriptors](../../../swift/Sources/Statelessness/ValueCodec.swift),
[reader](../../../swift/Sources/Statelessness/ValueCodecDecoder.swift), and
[application Codec hooks](../../../swift/Sources/Statelessness/Model.swift).

## Declaration model

Use explicit generic builders, without reflection, Codable, source parsing or a
Swift compiler macro dependency. Preserve tools-version6.0 and the current
macOS11/iOS14/tvOS14/watchOS7 minimums. These builders need no newer runtime
Int128, parameter-pack facility, compiler plugin or external dependency.

The result is the existing `TraceValueCodec<Value>`. Its four independent
capabilities remain independent; a record or enum does not acquire LengthEncode
or LengthDecode simply because its fields have length overrides. Those field
traits select an encoding mode, rather than a new record-wide length prefix.
Users can separately wrap the resulting codec in a custom length adapter.

An immutable field declaration has one of three modes:

```swift
TraceField<Root, Field>.ordinary(label, codec, get: projection?)
TraceField<Root, Field>.length(label, codec, width: .u8, get: projection?)
TraceField<Root, Field>.adapter(label, adapterCodec, get: projection?)
```

`TraceLengthWidth` is a closed `.u8/.u16/.u32` declaration type, corresponding to
1/2/4 bytes. Field labels are diagnostic only. A projection has type
`(Root) throws -> Field`; omission disables field encoding without requiring a
fake getter for a decode-only declaration. The adapter is another typed
`TraceValueCodec<Field>` with independent ordinary encoding/decoding callbacks;
there is no implicit conversion from String spelling to an adapter.

Separate factories make length plus adapter conflicts unrepresentable. Do not
accept a bag of options that silently ignores skip/default/unknown options.
Do not synthesize missing values or omit zero/empty fields.

| Field mode | Encoding | Decoding | Required direction |
| --- | --- | --- | --- |
| ordinary | `codec.encodeRaw(value, into: sink)` | `reader.value(codec)` | ordinary encoding / ordinary decoding |
| length | `codec.encodeLengthRaw(value, width:, into:)` | `codec.decodeLength(width:, from:)` | length encoding / length decoding only |
| adapter | `adapter.encodeRaw(value, into:)` | `adapter.decode(from:)` | adapter's ordinary encoding / decoding only |

This table is the call graph, not just a wire-format equivalence. In particular,
length and adapter fields add no generic field value guard, no temporary blob,
no frame, no automatic finish and no strong whole-callback poison. Existing raw
leases still revoke callback access. Actual writer rejection remains sticky.

## Typed ordered products and record construction

Use a typed ordered product rather than `[Any]`, a dictionary of decoded values,
mutable partially initialized roots or forced casts:

```swift
TraceFields<Root, Field>.first(field)
TraceFields<Root, Decoded>.appending(nextField)
    -> TraceFields<Root, TraceProduct<Decoded, Next>>

TraceProduct<Prefix, Last> { let prefix: Prefix; let last: Last }

TraceCodecRequirement.none / .encoding / .decoding / .both

TraceRecords.codec(schema:, fields:, construct: ((Decoded) throws -> Root)? = nil,
                   require: TraceCodecRequirement = .none,
                   layout: Rust64ValueLayout? = nil) throws
TraceRecords.unit(schema:, construct: (() throws -> Root)? = nil,
                  require: TraceCodecRequirement = .none,
                  layout: Rust64ValueLayout? = nil) throws
```

`first` followed by `appending` preserves declaration order with arbitrary field
count using ordinary generic nesting. A caller supplies a typed constructor once,
after all fields decode successfully. Nested products are less concise than a
macro but avoid an arity cap and deployment/toolchain expansion. Convenience
builders for named/tuple record arities1–4 can flatten this same implementation
later; they must not introduce a different wire or guard path.

For example, two fields decode to `TraceProduct<UInt8, UInt16>`; construction
receives `values.prefix` and `values.last`. Encode-only construction supplies no
constructor and can explicitly request `require: .encoding`. Decode-only fields
supply no projections. A unit/empty record emits
no bytes; decoding still needs an explicit constructor, and an enclosing `value`
still charges its work/depth entry. Named, tuple and unit declaration labels
change diagnostics/construction, not the field wire format.

By default, `codec` infers the actual direction intersection. Encoding requires
every selected field mode's encoding capability and its projection; decoding
requires every selected decode capability plus the supplied constructor. A
metadata-only result with neither ordinary direction is valid and advertises
both false, analogous to a type implementing neither Rust trait. Unsupported
operations reject at the native boundary before callbacks, bytes or guard work.
There are no placeholder callbacks.

Optional `require` eagerly rejects a missing requested capability at builder
construction. It does not generate a missing direction or filter an available
one: `require: .encoding` still returns both directions if both are inferred.
The default `.none` means no additional requirement, not no capabilities. For a
unit/empty record, encoding is the empty-field intersection (true); decoding
additionally needs an explicit constructor. Capability checks cover every
declared field, even fields holding empty values. Independent structural errors
(such as invalid field modes) still reject construction before publication.

A record constructor runs exactly once, only after all field decodes succeed.
No constructor, partially initialized Root, field setter or placeholder value is
used before decoding the fields; any failed field prevents construction entirely.
Projection and construction closures are application callbacks; mutable-reference fidelity,
purity and synchronous use remain caller responsibilities. No universal clone,
Sendable or callback-memory guarantee is added.

## Explicit tagged enum cases

Propose typed case payload declarations plus private type erasure for the case
array. Do not erase payloads through public `Any` casts:

```swift
TraceEnumCase<Enum, Payload>(tag: UInt32, label: String,
                            payload: TraceValueCodec<Payload>,
                            construct: ((Payload) throws -> Enum)?)
caseDeclaration.erased() -> AnyTraceEnumCase<Enum>
caseDeclaration.select(payload) -> TraceEnumSelection<Enum>

TraceEnums.codec(schema:, tagWidth: .u8, cases: [AnyTraceEnumCase<Enum>],
                 select: ((Enum) -> TraceEnumSelection<Enum>)? = nil,
                 require: TraceCodecRequirement = .none,
                 layout: Rust64ValueLayout? = nil) throws
```

Use a record payload built above for named/tuple variants and `TraceUnit` for unit
variants. `TraceEnumSelection` has no public initializer: `select(payload)` ties
the correctly typed payload to its case declaration. A native exhaustive switch
in the selector returns the appropriate typed selection. The enum builder checks
that a selection belongs to one of its local case declarations before writing a
tag. No global registry or cross-instance single-use token is needed. Selections
are ordinary reusable values; this identity check validates a declaration, not a
security boundary or application model identity.

The closed tag-width type admits only u8/u16/u32. Every case requires an explicit
UInt32 tag; construction rejects duplicates and tags exceeding the chosen width.
Sparse and reordered tags remain legal. Tags are never array indices, Swift
hashes, labels or Swift enum ABI discriminants. Above-u32 declaration values are
excluded by the typed boundary, rather than narrowed into a valid tag.

The enum builder applies the same inference/require rule as records. Encoding
requires a selector and every case payload's encoding capability; decoding
requires every case payload's decoding capability and constructor. Missing
capabilities in an unselected case still affect the intersection. A neither
result remains an honest metadata-only declaration; an explicit requirement can
reject it eagerly. Requirements never filter existing capabilities. A selected
case constructor runs only after its entire payload has decoded successfully.

Encoding selects once, checks membership, writes the raw little-endian tag, and
calls the selected payload's raw encode. It must check encoding availability
across **all** cases, not just the current selection. Decoding enters one guarded
tag integer, rejects an unknown tag with `unknown enum tag` before any payload
callback, then calls the selected payload's raw decode and constructor. The
payload record's individual fields supply their own guards; there is no payload
record guard merely because its implementation uses a record codec. Unit payload
raw decoding adds no unit value guard. An enclosing enum `fromTrace` supplies the
single outer enum entry.

The selector is a pure, total view of an enum value, analogous to a Swift switch.
`traceVariant` can obtain the selected declaration's diagnostic label separately;
calling a diagnostic accessor is not another encode operation. An unregistered
selection has no variant label and fails encoding before tag publication. Labels
are not compatibility identities and never derive tags. Decode-only enum
construction omits the selector; encode-only cases omit constructors. Case
payloads preserve the required ordinary direction independently. A custom
length-only field belongs inside a record payload's length mode, not an invented
ordinary payload callback.

## Opt-in model-codec binder

Propose an immutable binding specification that returns the existing mutable
application `Codec<State, Input, Output>` value:

```swift
ModelCodecBinding<State, Input, Output>(
    metadata: @escaping () throws -> Metadata,
    version: UInt32 = 1,
    state: TraceValueCodec<State>,
    input: TraceValueCodec<Input>,
    output: TraceValueCodec<Output>,
    decodeLimits: @escaping () throws -> DecodeLimits = { DecodeLimits() }
) throws
binding.codec() -> Codec<State, Input, Output>
```

Construction validates positive version, State/Input ordinary encode+decode and
Output ordinary encode only. Length-only descriptors can participate explicitly
through `withLength`; no output decoder is introduced or required. Construction
must not evaluate metadata, decode limits, getters, reducers, checkers or codecs.
Unknown State/Input/Output layouts do not block encoding or top-level decoding;
a collection requests its element's explicitly declared layout when decoding.

For each of the eight generated operations (three legacy encodes, three into
encodes, State/Input decodes):

1. Evaluate the original metadata closure exactly once.
2. Compare its current codecVersion with the declared version.
3. On mismatch return `ModelCodec derive version does not match Model metadata`
   before touching the value, sink, reader, decode-limits factory or codec callback.
4. On match dispatch as below.

| Hook | Delegate |
| --- | --- |
| legacy encodeState/Input/Output | corresponding `traceBytes(value, maximum: Int.max)` |
| preferred encodeState/Input/OutputInto | corresponding `encodeRaw(value, into: callerSink)` |
| decodeState/Input | evaluate fresh limits once, then corresponding `fromTrace(bytes, limits:)` |

Preferred hooks append directly and do not clear the caller destination, add
framing, invoke strong `encode`, finish, allocate a temporary encoded blob, or
evaluate decode limits. Existing engine-owned strong helper leases remain their
own boundary. Legacy encodes own a new buffer, finish before returning, and have
no bounded-temporary guarantee; Int.max is the native array-index ceiling, not a
claim that Swift represents every Rust64 usize allocation.

The returned Codec.metadata remains the original metadata provider: merely
querying metadata is not itself a generated codec-method version check. Identity,
producer stamps, model initialization, checks, reducers and replay compatibility
remain existing engine responsibilities. The limits factory is evaluated per
decode call, after version admission; encoding never evaluates it. Capability
validation and declaration storage are snapshots of descriptor configuration,
not deep immutability of arbitrary closure-captured application objects.

Adoption is explicit when constructing a model's codec. Do not automatically
replace existing model callbacks. Since Codec is already publicly mutable,
subsequent user overrides are outside the binder's generated-method guarantee;
preferred-into precedence continues to apply when editing adapter hooks.

## Layout and qualification gates

No field sum, tuple estimate, Swift MemoryLayout or observed encoded length may
silently become a record/enum allocation descriptor. Custom record/enum layouts
are absent unless explicitly supplied; collections require a truthful measured
exact-shape Rust64 descriptor or an explicitly documented caller declaration.
Standalone records/enums can encode and decode without unused result-layout
weights. Runtime allocation weights remain structural requests, not heap/RSS.

A future finite corpus should cover all declaration shapes; maximum/sparse tags;
every prefix/trailing byte; unknown tags before payload work; direct adapter and
length-only fields at exact work/depth; unavailable directions in unselected
cases and empty values; raw errors versus writer failures; explicit collection
layouts; diagnostics independent of tags; and no callback repeats. The binder
needs mismatch tests for all eight hooks with untouched sink/reader/limits,
per-call limit factories, encode-only outputs through real Recorder/evidence
capture and replay, plus installed renamed-dependency/package consumers.

Negative declarations must fail at the actual native builder/type boundary;
fixture-only rejection is not runtime evidence. Rust syntax/attribute parsing,
source cfg selection, lifetime/const-generic syntax and compiler error text are
not claimed by these explicit Swift builders. Snapshot selected declarations in
application codec version/provenance. Future builds/tests must use Kyoto; none
were run for this document.
