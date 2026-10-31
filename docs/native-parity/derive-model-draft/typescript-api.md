# TypeScript N13 API draft

Design only. This document proposes additions to the current `valueCodecs`,
`customValueSchema`, `withLength`, `Decoder`, `EncodeBuffer` and `CodecModel`
surfaces. It changes no runtime, package exports, profiles or qualification
claims. Sources are the [audit](../DERIVE-MODEL-CODEC-AUDIT.md),
[Rust derive expansion](../../../crates/statelessness-macros/src/codec.rs),
[ModelCodec expansion](../../../crates/statelessness-macros/src/model_codec.rs),
and [current TypeScript descriptors](../../../typescript/src/value_codec.ts).
All names below are proposed, not callable exports.

## Directional types and runtime authority

Keep the current unified `ValueSchema<T>` runtime interface for compatibility.
Add public directional views and private capability metadata, rather than
inferring availability from the presence of its always-present methods:

```ts
interface ValueEncoder<T> {
  encodeRaw(value: T, out: EncodeBuffer): void;
  schema(): string;
  variant(value: T): string | null;
}
interface ValueDecoder<T> { decodeFrom(reader: Decoder): T }
interface LengthEncoder<T> {
  encodeLengthRaw(value: T, width: number, out: EncodeBuffer): void;
}
interface LengthDecoder<T> { decodeLength(width: number, reader: Decoder): T }
```

Factory-produced values retain independent ordinary encode/decode and length
encode/decode capabilities. Directional views are compile-time guidance, not
runtime authenticity: public builders validate and capture the required methods
and consult private factory metadata when available. A structural custom adapter
must explicitly provide its directional callbacks; the mere presence of unified
unsupported-method stubs does not establish a capability. No registry, decoder
for output, or ordinary capability inferred from a length capability is needed.

A descriptor exposing unavailable unified methods continues to fail clearly at
use. Builders infer the actual encode/decode intersection across **every** field and
enum variant by default. They may honestly publish neither direction when, for
example, one field is encode-only and another is decode-only. An optional
`require: 'encode' | 'decode' | 'both'` eagerly rejects a missing requested
capability; it never fabricates a capability or filters an independently available
one. Omitting `require` is not shorthand for requiring both. Encode-only output
builders therefore work, and an empty record value or an unselected variant
cannot bypass the declaration's actual missing capability. No wire bytes are
published on a declaration error.

## Ordered fields and records

Propose `codecField<T>()`, `orderedRecord<T>()`, `tupleRecord<T>()` and
`unitRecord<T>()`. An explicit ordered array is the wire declaration; object key
insertion order, reflection and property enumeration never determine it.

```ts
type Pair = { code: number; note: string };
const field = codecField<Pair>();
const pair = orderedRecord<Pair>({
  name: 'app.Pair',
  require: 'both',
  fields: [
    field.value('code', valueCodecs.u8),
    field.length('note', valueCodecs.string, 1),
  ] as const,
  construct: fields => ({ code: fields.code, note: fields.note }),
});
```

`field.value<K extends keyof T>(key: K, codec: ValueSchema<T[K]>)` uses
`codec.encodeRaw` and `reader.value(codec)`. `field.length` instead requires the
selected length directions and directly calls `encodeLengthRaw` or
`decodeLength`; it inserts no ordinary field guard. Its declaration width must
be exactly 1, 2 or 4, matching the derive's field annotation. General custom
length methods retain their existing native u8 domain, including width 3.

`field.adapter<K>(key, adapter)` has independently optional synchronous
`encode(value: T[K], out): void` and `decode(reader): T[K]` callbacks and calls
them directly. It adds no ordinary field guard or inferred ordinary/length
trait requirement. The adapter and length field alternatives are a discriminated
union; they cannot coexist. Unknown options, missing keys, duplicate keys,
unsupported modes and malformed callbacks reject when declaring the descriptor.
No skip, default, implicit omission or unknown-field serialization is supported.

A generic `construct` receives a detached typed field map assembled from the
ordered entries after successful decoding. Its availability participates in the inferred decode capability. It is
required when `require` includes decoding and is otherwise optional. Invoke it
exactly once only after every field has decoded successfully; a partial field
failure invokes no constructor. Its result is application-owned; the library cannot prove
that user construction preserves values. The type helper should enforce that
all required fields appear exactly once; runtime duplicate detection remains
mandatory for JavaScript, casts and dynamic declarations. Optional application
fields still need an explicit field codec such as a tagged option.

For class/private-field use, offer an explicit field accessor alternative
`field.access(name, codec, get)` with a positional decode constructor. It avoids
pretending private members can be reflected. Capture accessors once; invoke them
synchronously with documented receiver and declaration order. The basic key
form reads each selected field once during that encode call. Values themselves
are not deep-cloned or frozen by a declaration.

`tupleRecord` accepts an ordered readonly tuple of equivalent field bindings,
encodes without a count, and decodes to a detached tuple or an explicit
constructor result. `unitRecord` takes a zero-argument decode constructor and
writes nothing. Empty named/tuple records are legal. None adds a field count,
name, frame or schema bytes. Ordinary record decode is raw; only the caller's
`reader.value(record)` contributes the record guard.

No composite allocation weight is inferred. `layout?: LogicalLayout` is an
explicit cooperative declaration. An unweighted record works at top level and
as an encoded collection element; collection decoding requires an admitted
explicit weight. Field wire sizes are not a Rust layout calculation.

## Explicit tagged enums

Propose `taggedEnum<T>()` with an explicit variant discriminator key and ordered
case declarations. A typed case helper relates each discriminator literal to
`Extract<T, Record<K, V>>`, so case fields and constructors stay correctly typed.

```ts
type Event = { kind: 'Idle' } | { kind: 'Note'; text: string };
const cases = enumCases<Event, 'kind'>('kind');
const event = taggedEnum<Event>({
  name: 'app.Event', require: 'both', tagType: 'u16',
  discriminator: 'kind',
  cases: [
    cases.unit('Idle', 7, () => ({ kind: 'Idle' })),
    cases.record('Note', 400, {
      fields: [codecField<Extract<Event, {kind: 'Note'}>>()
        .length('text', valueCodecs.string, 1)] as const,
      construct: f => ({ kind: 'Note', text: f.text }),
    }),
  ] as const,
});
```

`tagType` defaults to u8 and admits only u8/u16/u32. Tags are explicit safe
Number integers within u32 and the selected width; tags and discriminator labels
must be unique. Missing tags, sparse-array declarations, duplicates, unknown
options, implicit indexes and discriminants reject at declaration. Reordering
cases does not change tags. The type helper checks exhaustiveness of a finite
union where possible; runtime handling still rejects an unknown discriminator.

Encoding reads the discriminator once, writes the selected fixed-width
little-endian tag through its **raw** scalar encoder, and then writes that
case's fields with the same dispatch as records. Decoding reads the tag through
`reader.value(tagCodec)` and rejects `unknown enum tag` before any payload
callback. It then decodes fields directly; there is no extra guard for a hidden
record wrapper. Unit, tuple and named case forms share this mechanism.
The available directions are the intersection over all cases even when the
current value is unit or belongs to another case. An optional `require` checks
this intersection without hiding extra capabilities. A mixed-direction enum may
expose neither direction by default. Constructors/accessors participate only in
the corresponding direction; a selected decode constructor runs exactly once
after all of its fields succeed, never after an unknown tag or partial failure.

`schema()` returns the captured explicit diagnostic name and a detached ordered
description may be exposed for tooling. `variant(value)` returns the selected
source label. Labels do not determine tags, hashes, versions or compatibility;
no Rust token-stream spelling is promised. Invalid diagnostics reject without
turning a label into a wire feature.

## Model codec binder

Propose an additive `bindModelCodec(model, declaration)` returning a facade with
the original model's type and codec methods:

```ts
const ready = bindModelCodec(model, {
  version: 1,
  state: stateCodec,       // ordinary encode + decode
  input: inputCodec,       // ordinary encode + decode
  output: outputEncoder,   // ordinary encode only
  decodeLimits: () => ({ bytes: 4096n, work: 10000n }),
});
```

The model must already provide `metadata()`. The binder adds no identity,
checks, reducer, clones, engine stamps or output decoder. It rejects missing
state/input encode or decode capabilities and missing output encode capability
at construction, using actual directional metadata rather than method presence.
Version defaults to 1 and must be a positive u32 safe Number integer. Unknown
options reject. The limits option is either a captured immutable limits record
or a synchronous zero-argument provider evaluated afresh on **each decode**.
The provider receives no model `this`; callers can explicitly close over state.
Its return is captured/resolved once by the existing decoder admission path.
Encoding never evaluates it.

Each of all eight codec methods (three legacy encoders, three preferred
encoders, two decoders) first calls original `metadata()` with the original
receiver, captures and validates its codec version, and compares it with the
declared version. A mismatch returns the Rust-equivalent meaningful error
`ModelCodec derive version does not match Model metadata` **before reading the
value, touching the sink, admitting/copying input bytes or invoking the limits
provider**. Replay's full identity check remains separate. Metadata stays live
per call; the declaration's codec version stays immutable.

Preferred methods call the descriptor's raw encoder on the caller's shared
sink, without an extra whole-callback poisoning boundary, temporary blob or
implicit finish. Existing engine-issued outer leases still provide their usual
lifetime and synchronous-hook checks. Legacy methods use `encodeValue` with
u64::MAX as the logical maximum; host allocation/representation can fail before
that maximum. They do not promise bounded temporary allocation. State/input
decode calls `decodeValue` with per-call limits: input quota before ownership
copy, exactly one outer guard, aggregate budgets and trailing-byte rejection.

Use the existing extensible facade approach rather than `Object.assign(model)`
or spreading methods. This supports frozen models and private fields: delegate
all other methods/getters using the original receiver, preserve enumeration,
generation, append checks, estimates and codecs, and let the facade's eight codec
methods override existing hooks. Preserve the original type's extra model
capabilities in the return type. Do not expose a public bypass around checked
or recorder invariants. Existing original-model methods invoking `this` continue
to see their original receiver; the binder does not rewrite internal self-calls.

## Immutable declaration boundary and validation plan

Capture every options/entry/callback/width/tag/name/layout field once before
validation and before application callbacks. Copy arrays by validated captured
length and numeric indexing, ignoring custom iterators; reject holes. Own/freeze
the declaration tree and private lookup maps, but do not freeze caller objects
or application values. Exposed diagnostics are detached. Prototype keys such as
`__proto__` are handled as data using Map/null-prototype structures. Callbacks
remain cooperative application code; capture their function and receiver once,
reject Promise/non-void encoder results, and expire child capabilities on every
exit. Declaration validation is failure-atomic and performs no encoding.

Before implementation, settle constructor-map versus positional constructor
spelling and the minimum public directional-view type additions in a type-only
fixture. These are ergonomic choices, not permission to alter wire semantics.
Qualification must include exact raw/guarded callback logs, widths and byte
prefixes; independent field directions including length-only; empty/None and
unselected-case rejection; maximum/sparse/reordered enum tags; malformed dynamic
declarations; private/frozen model forwarding; getter-once and callback mutation;
per-call version/limits ordering; and a genuine custom encode-only Output through
recording, rich replay, Recorder and evidence. Run installed TypeScript and
cross-language consumers plus existing profiles through Kyoto after a future
implementation is authorized. This document supplies no such execution proof.
