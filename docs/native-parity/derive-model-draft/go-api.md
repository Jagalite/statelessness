# Go N13 API proposal

Design only. This document proposes additive builders over the current `ValueCodec`, `LengthValueCodec`, `Decoder`, `EncodeBuffer`, and model `Codec` APIs. No runtime implementation, build, fixture execution, package change, or parity qualification accompanies it. The reference is [the audit](../DERIVE-MODEL-CODEC-AUDIT.md), `crates/statelessness-macros/src/{codec,model_codec}.rs`, and `src/value_codec.rs`.

## Typed ordered fields

Use generic free constructors because Go methods cannot introduce a new field type parameter. The public descriptors have private fields; no exported mutable callback list or untyped value map is needed.

```go
type ValueDirections struct { Encode, Decode bool }
type RecordOptions struct {
    Require *ValueDirections // nil infers available intersection; optional explicit requirements
    Layout *ValueLayout        // nil means unknown, never inferred
}
type RecordField[F any] struct { /* private directional callbacks */ }
type FieldProduct[H,T any] struct { Head H; Tail T }
type RecordFields[P any] struct { /* private ordered product codec */ }

func OrdinaryField[F any](label string, codec ValueCodec[F]) (RecordField[F], error)
func LengthField[F any](label string, codec LengthValueCodec[F], width uint8) (RecordField[F], error)
func AdapterField[F any](label string,
    encode func(F, *EncodeBuffer) error, decode func(*Decoder) (F, error)) (RecordField[F], error)
func NoRecordFields() RecordFields[Unit]
func PrependRecordField[H,T any](head RecordField[H], tail RecordFields[T]) RecordFields[FieldProduct[H,T]]
func OrderedRecord[R,P any](schema string, fields RecordFields[P],
    project func(R) (P,error), construct func(P) (R,error),
    options RecordOptions) (ValueCodec[R], error)
```

The typed product is a heterogeneous field list, independent of the destination record. `PrependRecordField(a, PrependRecordField(b, NoRecordFields()))` declares `a` then `b`; it is not a mutation sequence. A small set of named `Fields1` through `Fields4` convenience constructors may return the same typed products without changing execution. Applications with more fields compose the recursive product. No reflection, exported `any` payload, or untyped field map is needed.

`project` is encoding-only and `construct` decoding-only. Each field may carry either direction independently. By default the record exposes the intersection of all field capabilities and the applicable application callbacks: encode requires `project` and every encoder; decode requires `construct` and every decoder. Mixed encode-only/decode-only fields therefore produce a valid metadata-only descriptor with neither operation. No callback stub fabricates a direction, and unavailable operations fail without entering projection, construction, fields, sink or reader. An encode-only output record needs no constructor or field decoders; a decode-only record needs no projection or encoders.

`Require`, when nonnil, is an additional eager declaration check: any requested-but-unavailable direction rejects construction; it does not remove inferred capabilities. The options capture a copy of that requirement value. A default `Require == nil` never converts mixed-neither into a declaration error. Implementing metadata-only descriptors requires a narrow internal builder constructor rather than routing through today's public `NewValueCodec`, which rejects two nil callbacks; the existing public constructor contract need not change. Raw value operation errors should distinguish an unavailable direction from an uninitialized zero descriptor without invoking callback code.

Decoding collects typed field locals/product values without invoking application construction callbacks. Only after **all** fields decode successfully does `construct` run once. A truncated field, invalid bool, unknown tag, or budget error runs no record constructor, no setters, and publishes no partial destination. Nested field records necessarily construct after their own successful fields, in source order; an outer constructor still waits for every outer field. The product itself is an internal value carrier, not an application constructor hook. Project/construct callback allocations remain outside structural decoder budget guarantees. Configuration is privately copied; there is no caller-owned mutable field slice or layout pointer retained.

Fields execute in supplied order, including duplicate diagnostic labels; labels are not bytes or selectors. Project/construct callbacks must respect application ownership and purity requirements. No reflection, exported `any` payload, JSON conversion, field omission, skip, default synthesis, or source-name inference is introduced.

Named records, positional records, and unit records use the same builder: labels can describe names or positions; the empty `Unit` product emits no bytes and calls the final constructor only after its successful empty decode. The user chooses the actual Go struct or tuple-shaped type. A generic application helper can instantiate `OrdinaryField[T]` without source parsing. A future generator could emit exactly these declarations, but generation and Rust syntax emulation are separate scope.

Call graph is part of the API:

| Field kind | Encode | Decode |
| --- | --- | --- |
| Ordinary | field codec `EncodeRaw` | `ReadValue(reader, fieldCodec)` |
| Length | `EncodeLengthRaw(value,width,out)` | `DecodeLengthRaw(reader,width)` |
| Adapter | direct raw callback under a revocable encoder child lease | direct decoder callback, no new value guard |

A record's own raw decoder adds no guard. `FromValueBytes` or an enclosing ordinary field supplies the record guard. Length widths are validated as 1, 2, or 4 at declaration construction; custom length-only types require neither ordinary direction. Adapter encode/decode are independent and add no automatic field codec requirement. Neither raw record encoding nor field adapters insert the stronger `EncodeInto` callback-poison policy or automatic finish. Failed writes remain sticky; returned raw callback errors become sticky only through an enclosing guard/framed operation as already specified. Panic containment uses the existing constant diagnostics and lease invalidation, without formatting panic payloads. These builders produce ordinary encode/decode capabilities only; they do not invent independent LengthEncode/LengthDecode capabilities for records.

## Explicit tagged enums

```go
type EnumCase[E any] struct { /* private tag, label, payload callbacks */ }
type EnumOptions struct {
    Require *ValueDirections // same inference/requirement policy as records
    TagWidth uint8              // 1, 2, or 4; default supplied by helper is 1
    Layout *ValueLayout         // unknown unless explicitly declared
}
func TaggedCase[E, P any](tag uint32, label string, payload ValueCodec[P],
    extract func(E) (P, error), construct func(P) (E, error)) (EnumCase[E], error)
func TaggedEnum[E any](schema string, selectTag func(E) (uint32, error),
    options EnumOptions, cases ...EnumCase[E]) (ValueCodec[E], error)
```

A case payload can be an ordered named/positional record or a unit descriptor. No payload discriminator or wrapper bytes are added. `selectTag` is required only for encoding and runs once, before emitting the explicit little-endian tag. Only the selected case extractor executes; the extractor must reject a value inconsistent with that tag. Case construction is decode-only. No scanning of potentially overlapping `isCase` predicates is proposed. Unknown selections reject before writing a tag; unknown decoded tags reject with `unknown enum tag` after the guarded tag read and before any payload callback.

The constructor copies cases, rejects duplicate/out-of-range tags and unsupported widths, and computes capability intersection across all cases and the enum selection/construction callbacks, even when a particular value is unit or absent. Optional `Require` rejects missing requested directions; the default permits a metadata-only neither descriptor. Explicit `uint32` tags are mandatory, including zero; declaration order never supplies a tag. Sparse/reordered declarations and maximum tags are supported. The tag uses raw scalar encoding and `ReadValue` scalar decoding. The selected payload decoder is called **raw**, because its ordinary fields supply their own guards; there is no extra variant-payload guard in Rust derive expansion. Case labels feed a value-aware `VariantFor` callback from the selected tag and never compatibility metadata. Schema and variant labels are diagnostics only.

`DefaultEnumOptions()` supplies width 1 and inferred capabilities; direct width zero is invalid. Typed constructor parameters replace string attribute parsing: mutually exclusive field modes are distinct constructors, and invalid Go types/directions may fail during compilation or builder construction. No claim of matching Rust compiler messages follows.

## Opt-in model codec binder

```go
type ModelCodecOptions struct {
    Version uint32 // positive; DefaultModelCodecOptions() supplies 1
    DecodeLimits func() (DecodeLimits, error) // nil means fresh defaults per decode
}
func BindModelCodec[S,I,O any](metadata func() (Metadata,error),
    state ValueCodec[S], input ValueCodec[I], output ValueCodec[O],
    options ModelCodecOptions) (*Codec[S,I,O], error)
```

The binder returns a new codec object for explicit assignment to `model.Codec`. It never inspects or mutates the model, replaces existing methods, changes metadata, adds checks/reducers/stamps, or fabricates an output decoder. It eagerly requires both ordinary directions for state/input, only encode for output, nonnil metadata, and positive version. Length-only values can participate through an explicitly selected-width ordinary wrapper. Output decoding, if the descriptor happens to offer it, remains unused.

Every generated encode/decode method calls the retained metadata callback once and checks current `CodecVersion == options.Version` before touching a getter/value encoder, sink, decoder, input copy, or decode-limit callback. Mismatch returns `ModelCodec derive version does not match Model metadata`. Metadata errors propagate without formatting. Full replay identity validation remains separate. The metadata callback itself stays exposed unchanged as `Codec.Metadata` and is not cached across operations.

Preferred encode hooks call the descriptor's **raw** encoder against the caller sink and shared budget, under its normal revocable lease; they do not select stronger `EncodeInto`. Legacy encode hooks use `EncodeValue` with host `MaxInt`, the Go signed slice/index ceiling. This is a native ceiling rather than exact Rust `usize::MAX` equivalence, and is not a bounded temporary-allocation guarantee. State/input decode hooks evaluate `DecodeLimits` freshly once per call, then call `FromValueBytes` for owned input admission, one outer guard, aggregate budgets and EOF. Encoding and mismatch do not evaluate decode limits. The options value is copied at construction; callback closures remain dynamic by design.

A helper must not accept both a codec and another codec already attached to a model and silently choose one. Returning a new `*Codec` keeps replacement opt-in and reviewable. A later `WithModelCodec(model, ...)` convenience could copy the model and call this binder, but is unnecessary for the initial implementation.

## Layouts and API evolution

Records and enums never acquire an automatic layout from field sums, `unsafe.Sizeof`, schema spelling, or tag width. An explicit `ValueLayout` is copied and validated; nil still permits top-level encoding/decoding but collection storage rejects unknown layout. Existing builtin measured layouts remain unchanged. This declaration is a logical Rust64 allocation charge, not Go heap or RSS accounting.

These hand-written builders need no macro API sentinel. If code generation is added later, export a dedicated constant such as `DeriveModelAPIV1` and have generated source reference it; it is an API compatibility guard, not a codec version. Do not pretend a constant's presence alone checks arbitrary generated-code compatibility.

## Implementation and qualification breakdown

1. Add private ordered-field closures and directional validation in a new Go file; implement raw record callbacks over existing lease/reader mechanisms. Add named, positional, empty and unit cases, ordinary versus length versus adapter exact call logs, zero constructor calls on field failure, final-constructor errors, detached declaration configuration, and encode/decode-only capability failures.
2. Add tagged cases and enum declaration validation. Pin fixed wire tags, unknown-tag precedence, raw payload call graph, value-aware diagnostics and case-wide capabilities. Use explicit layouts only.
3. Add the opt-in binder in a separate file with no engine changes. Test all eight codec methods (three legacy encodes, three preferred encodes, two decodes), per-method version mismatch before side effects, fresh limits, metadata failures, encode-only outputs, and actual bounded recording/replay/Recorder/evidence use.
4. Consume independently authored shared Rust/native fixtures. Include every proper prefix, trailing bytes, bounded writes, sticky first errors, decoder-owned ModelError payloads, length-only/adapted fields, exact work/depth and zero-size collections. Ordinary two-scalar guarded record needs work 3/depth 2; direct u8-length String `abc` record needs work 5/depth 2. Verify enum payload nesting against actual expansion, not successful bytes alone.
5. Add installed-package consumers and renamed-import Go examples. Execute full regressions and focused consumers through Kyoto, archive exact source/requested/effective commands and honest static versus runtime rejection counts. Keep current profiles unchanged until qualification is explicitly accepted.

This proposal does not authorize runtime edits while the current installed-job source is frozen.
