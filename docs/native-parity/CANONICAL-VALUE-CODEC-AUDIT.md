# Canonical value codecs: next implementation audit

Static source audit, 2026-10-20, worktree HEAD
`7f37c2f2dadb4d251f75762ac60d0976a5198ab2` plus the current uncommitted native
candidates. This is a proposed next slice under N11 and N13 of the
[native parity plan](../NATIVE-PARITY-PLAN.md). It does not qualify the current
causal-evidence slice or declare that slice complete. No builds or tests were run
for this audit; a symbol or source inspection is not runtime evidence.

## Scope and existing boundary

Expose reusable native equivalents of Rust's public
[`value_codec`](../../src/value_codec.rs) module. These codecs encode typed model
values into deterministic bytes; they are separate from binary-v1 framing,
portable JSON, causal-envelope metadata, and application model identity. An
artifact reader's ability to parse its fixed metadata schema does not provide an
application with a general value decoder.

All four native candidates already have incremental `EncodeBuffer` hooks and
fixed-schema causal metadata codecs. The metadata implementations in
[Python](../../python/src/statelessness/evidence.py),
[Go](../../go/evidence_metadata.go),
[TypeScript](../../typescript/src/evidence.ts), and
[Swift](../../swift/Sources/Statelessness/EvidenceCodec.swift) supply useful
strict-integer/string/option and budget-accounting machinery. Their current
metadata schema and limits are specialized. They do not expose the full generic
scalar/container/custom-decoder contract described below.

The inspected Python package exports `EncodeBuffer` and application `ModelCodec`
callbacks; its metadata reader is private. Go's `evidenceMetadataDecoder` is
package-private. TypeScript's `MetadataReader` is internal and its package entry
point exports artifact APIs rather than a reusable value schema. Swift's
`EvidenceDecoder` and `EvidenceDecodeLimits` have internal visibility. These are
concrete public-surface gaps in this inspected revision, not assertions that no
codec code exists elsewhere or that the metadata candidates are qualified.

## Rust public API contract

Rust exports `pub mod value_codec` through [lib.rs](../../src/lib.rs). The public
surfaces are:

- `TraceEncode::trace_encode(&self, &mut EncodeBuffer)`: append canonical bytes to
  an existing sink. `trace_bytes(maximum)` creates a bounded sink, invokes the
  encoder, and checks `finish` before returning bytes. `trace_schema()` defaults
  to a diagnostic Rust type spelling; it is explicitly not a compatibility proof.
  `trace_variant()` defaults to no variant label.
- `TraceDecode::trace_decode(&mut Decoder)`: decode one value inside an existing
  reader. `from_trace(bytes, limits)` creates a reader, enters one `value`, and
  requires `finish`; successful decoding of a prefix is insufficient.
- `Decoder::new`, `take`, `value`, `collection`, and `finish`: cooperate with
  application decoders while preserving sticky structural failures and budgets.
- `LengthEncode::encode_length(width, sink)` and
  `LengthDecode::decode_length(width, reader)`: override vector/string length
  widths with 1, 2, or 4 bytes. Unsupported widths fail.
- `DecodeLimits { bytes, allocation, elements, depth, work }`: independent
  inclusive limits, with defaults 16 MiB, 32 MiB, 1,000,000, 64, and 2,000,000.
  These defaults differ from the fixed causal metadata decoder's byte-derived
  limits. They must not silently inherit `BinaryLimits`, `TraceLimits`, or
  `EvidenceLimits`.

A native equivalent should support typed schema objects or protocols, explicit
`encode_into`/`decode(reader)` implementations, and top-level bounded encode/decode
helpers. Exact exported names can follow each language's conventions. Wire
semantics, nested budget accounting, and failure propagation must remain shared.
Existing application codec callbacks remain usable; adoption is explicit.

## Exact built-in wire shapes

These are not tagged dynamic values. A decoder needs the expected schema; the
same bytes can be valid under several different schemas.

| Value | Canonical bytes | Admission details |
| --- | --- | --- |
| Unsigned u8/u16/u32/u64/u128 | Exactly 1/2/4/8/16 little-endian bytes | Reject out-of-range native values; never truncate or round |
| Signed i8/i16/i32/i64/i128 | Same fixed widths, two's-complement little-endian | Preserve the full signed range |
| bool | One byte, 0 or 1 | Reject every other byte and inappropriate native coercions |
| unit `()` | Zero bytes | Still costs one `value` work/depth entry when decoded through `value` |
| String | u32 UTF-8 byte length, then exact UTF-8 bytes | Strict UTF-8; no normalization, BOM removal, or replacement characters |
| vector/list of T | u32 element count, then each T in order | Count elements, not encoded bytes; duplicate values remain |
| Option<T> | u8 tag 0 for absent; tag 1 then T for present | Reject other tags; distinguish absent from present unit/empty values |
| fixed array `[T; N]` | N consecutive T values, no length prefix | Decode charges N elements and its requested allocation |
| tuple arities 1 through 4 | Consecutive fields, no prefix/tag | Preserve field order, schema, and arity |
| `OracleState<S,H>` | S then H, no extra framing | General value codec shape; it is not the application `ORC1` checkpoint wrapper |

Vector and String default lengths are u32. Length overrides change only those
prefix widths; integer widths and nested element schemas remain unchanged.
Arrays have no encoded count. There is no built-in float, char, usize/isize,
map/set, arbitrary object, pointer/reference graph, or dynamic type-tag format in
this module. Supporting a custom application schema does not make those types
built-in portable formats.

## Decode budgets and sticky errors

Implement these transitions in source order, including simultaneous failures:

1. Construction rejects input length greater than `bytes`. `take(n)` checks a
   prior sticky failure, then remaining input; truncation becomes sticky. Taking
   raw bytes does not itself charge a per-byte work unit.
2. Every `value(schema)` first rejects an existing failure, subtracts one work
   unit, then rejects when current depth is already at its maximum. It increments
   depth for the callback and decrements it on normal success/error return.
   Rust does not catch a callback panic or provide a depth-restoration guard for
   that unwind. A returned callback failure is sticky. A callback returning success after swallowing a nested reader error
   must still fail.
3. `collection(n, element_size)` first subtracts n from the aggregate element
   allowance, then checks n times element_size for overflow, subtracts the
   allocation request, and finally requires n no greater than remaining work.
   This last check does not subtract n: subsequent `value` entries consume their
   own work. Collections charge before their storage allocation.
4. A String reads its length through `value<u32>`, performs collection accounting
   with one-byte elements, subtracts its byte length from work, then takes and
   validates UTF-8 before creating the owned string.
5. A vector reads its length through `value<length-integer>`, charges its element
   allocation, reserves storage, then calls `value<T>` for every element. Fixed
   arrays charge their known N and decode each element through `value` too.
6. `finish` rejects a sticky failure before checking trailing bytes. Rust's
   trailing-bytes failure is a terminal check, not a mutation of its reader's
   failure field. Native APIs need not expose a way to clear terminal failure.

Do not use input length as a substitute for element/work budgets. `Vec<()>` has
zero encoded bytes per element, and nested vectors have their own aggregate
collection charges. A generic minimum encoded-width guard must allow zero-width
schemas; the fixed evidence decoder's positive-width assumptions cannot be
copied wholesale. Preserve quota-error precedence before any extra truncation
shortcuts. Native ownership checks may impose separately documented structural
validation, but must not disguise a different budget algorithm as Rust parity.

Work follows actual `value` calls, not a uniform per-wire-field formula. A
standalone integer or unit costs one value entry; a bool costs two because its
implementation enters `value<u8>` for the tag. An option costs its outer entry,
one tag entry, and the contained value's entries only when present. Tuple and
OracleState decoding enter each field through `value`. A normal vector/string
has its outer entry plus a length-integer entry; a direct
`LengthDecode::decode_length` call does not add its own outer entry. Derive field
length overrides use that direct call. Native schema composition must preserve
this distinction rather than inserting an extra guarded entry for convenience.

The sticky guarantee applies to errors recorded by `take`, `collection`, or a
`value` guard. A custom decoder can call `LengthDecode::decode_length` or a
concrete `trace_decode` implementation directly; returned validation/allocation
errors become sticky only if propagated into an enclosing `value`. For example,
a direct length override's unsupported-width error does not itself call
`Decoder::reject`. Do not promise that arbitrary application code bypassing the
guarded API cannot suppress every possible direct trait error. Conversely, an
already recorded reader failure must survive a custom decoder's successful
return, including when that decoder called a nested guarded value.

Rust allocation accounting uses target `size_of::<T>()`, not wire widths or
process RSS. The causal metadata convention of fixed Rust64 logical weights is
only a fixed-schema precedent. Before implementing general native schemas, choose
and document a logical allocation-weight descriptor for every supported schema,
including zero-sized unit, String, vector, option, tuple and array layouts. Confirm
these against a pinned Rust target. Do not infer generic Rust layouts from Go,
Swift, Python or JavaScript heap sizes, or silently reuse the metadata scale of
16 times bytes. Custom decoders using `collection` declare requests cooperatively;
they cannot prevent arbitrary allocation or work outside the reader.

Allocation is charged at collections, not at every value entry. Top-level scalar,
option and tuple decoding does not independently charge the size of the result.
A vector of tuples/options charges the full in-memory element layout once for
its element storage; decoding nested strings/vectors/arrays can then charge their
own storage. Fixed arrays also charge N times the element layout because Rust's
array decoder builds a temporary vector. For `Vec<[u16; 3]>`, outer element
storage and each inner array's six-byte temporary request are both charged.
For `Vec<Vec<u8>>`, the outer elements use vector-header layout, then each inner
vector charges its own byte storage. Unit elements have zero allocation weight
but retain element and work costs.

Useful **source-derived expectations to measure**, not newly qualified layout
claims, for the intended Rust64 target are integer weights equal to their fixed
widths, bool1, unit0, String24, Vec<T>24, Option<()>1, Option<bool>1, Option<u8>2,
Option<u64>16 and Option<String>24. These examples deliberately distinguish enum
niches from an assumed universal tag-plus-payload formula. Arrays use N times the
measured T layout. Tuple layout includes target alignment/padding and may reorder
physical fields independently of wire order; `(u8,u64)` is expected16, not9.
Nested options, 128-bit alignment, tuple combinations and custom record layouts
need explicit reference measurement, not recursive sums of wire widths. Preserve
checked target-width multiplication and subtraction, including overflow before
allocation-limit comparison. No general Rust32 layout equivalence is implied.

The core observable error categories are byte limit, work limit, nesting limit,
element limit, allocation overflow/limit/failure, truncation, invalid boolean,
invalid UTF-8, invalid option tag, unsupported length width, length overflow, and
trailing bytes. Preserve the first reader failure even if a custom decoder catches
it and returns a value. Native allocation failure handling follows what the host
can actually catch; Swift traps and process OOM are not recoverable model errors.

## Language API gaps and proposed first slice

- **Python:** introduce explicit fixed-width signed/unsigned schemas, strict bool
  and unit, composable option/vector/array/tuple schemas, a public budgeted reader,
  and encode/decode helpers. Python integers can represent all required ranges,
  but `bool` is an `int` subclass: numeric admission must explicitly reject bool.
  Reject malformed Unicode strings containing lone surrogates before encoding.
- **Go:** introduce generic codec descriptors and a public reader whose guarded
  decode entry point contains callback panics consistently with existing engine
  conventions. Built-in Go types cover widths through 64 bits; use explicit
  two-limb 128-bit value types or a deliberately scoped standard-library big-int
  adapter, with range and ownership checks. Native `int` is not a portable width.
  Options require a representation that preserves present zero/unit values.
- **TypeScript:** introduce explicit schema objects, a public reader and exact
  arithmetic admission. Use `bigint` for 64/128-bit values; reject fractional,
  nonfinite and unsafe numeric inputs rather than rounding. Preserve u8/u16/u32
  boundaries and typed-array subview offsets. Strict UTF-8 encoding must reject
  unpaired UTF-16 surrogates instead of `TextEncoder` replacement; strict decoding
  must preserve a leading BOM.
- **Swift:** introduce public protocols or typed descriptors and an independently
  configurable reader. Cover fixed-width values through 64 bits and an explicit
  portable 128-bit representation when required by the supported toolchain.
  Preserve exact UTF-8 bytes, avoiding normalization-sensitive equality when
  comparing encoded identity. Ordinary Swift enum/optional ergonomics need
  explicit unit and present-empty tests. Catchable throws do not cover traps.

Start with built-ins and handwritten composition; then add optional record/enum
builders and model-codec glue as a separately reviewed extension. Keep the public
reader independent of model execution: decoding values must not invoke reducers,
checkers, model initialization, or artifact replay. Integrate with existing into
hooks, honoring their sticky finish and revoked-sink ownership guarantees.

Rust [derive expansion](../../crates/statelessness-macros/src/codec.rs) additionally
supports record/tuple/unit shapes, enum tags, field length overrides and custom
adapters. [ModelCodec glue](../../crates/statelessness-macros/src/model_codec.rs)
checks the declared codec version and connects State/Input encode+decode and
Output encode only. Native builders or optional generation can provide equivalent
behavior without copying procedural macro syntax. Do not require a historical
output decoder merely to adopt generic codecs or replay.

## Suggested finite shared corpus

Author expected bytes and failures independently, then execute them through public
Rust APIs and installed native packages. Record schema descriptions explicitly;
the bytes are not self-describing. Include:

- All integer widths at zero, min/max, adjacent overflow, and mixed signed tuples.
  Include `(0x1234u16, -2i32, true, Some(9u8))` with bytes
  `3412feffffff010109` as an independently checkable small witness.
- Unit, empty/nonempty arrays and vectors, one-element tuples, nested options,
  absent versus present unit, and vectors of unit. Prove a small work allowance
  rejects a huge count even with zero required body bytes.
- Empty, ASCII, multibyte Unicode, leading BOM, embedded NUL, and canonically
  equivalent but byte-distinct strings; malformed/truncated UTF-8 and native lone
  surrogate rejection. Length tests use UTF-8 byte counts rather than characters.
- u8/u16/u32 length overrides at inclusive boundaries and one over, unsupported
  width, fixed-array missing/extra elements, and nested tuple/vector order.
- Each independent limit at zero, exact fit and one under; nested aggregate
  elements and allocations; multiplication overflow; work-before-depth and
  element-before-allocation-before-work precedence. Use explicit measured logical
  schema weights in allocation fixtures, with the qualified Rust target recorded.
- Every prefix truncation of nonzero-width goldens and trailing-byte rejection.
  Unit decoding from empty input succeeds when its work/depth budget permits;
  arbitrary truncation rules must account for zero-width complete values.
- Custom decoders swallowing `take`, `value`, invalid bool and `collection`
  errors; later reads and finish remain failed. Callback errors after partial
  decoding preserve the original failure. Custom nesting cannot bypass `value`
  accounting in the documented cooperative API.
- Exact encode cap and sticky failure through nested schemas, ignored rejected
  writes, caller-owned mutable input aliases and retained sink revocation. Prove
  decode returns owned values and cannot be changed by input-buffer mutation.
- OracleState field ordering and explicit model-codec integration: State/Input
  canonical decode, Output encode-only, metadata/codec-version policy, and no
  legacy allocating encoder call when a preferred into hook is present.

Use schema-tagged JSON fixtures with exact decimal text for 64/128-bit values and
hex encoded bytes. Record requested limits, qualified target/layout convention,
result/error category and, for callback witnesses, ordered events. Static goldens,
Rust reference execution, native conformance and installed-consumer receipts are
separate gates. A package test importing source is not installed-package evidence.

## Exclusions and completion gates

This slice does not publish registries, change engine defaults, replace existing
application codecs, add ABI wrappers, complete campaigns/network/lifecycle APIs,
or qualify debugger UI, all-platform builds or performance. It does not promise
schema migration, dynamic object serialization, canonical maps, a schema hash,
cryptographic authenticity, hard RSS bounds, callback preemption, zero allocation,
or parity of compiler diagnostics. Schema spelling and enum labels remain
informational unless an independently defined compatibility format says otherwise.

Close the built-in value-codec gap per language only after the public API,
full supported-type ledger, exact bytes, strict errors, independent limits,
custom-reader sticky behavior and ownership have reviewed tests; Rust reference
and installed native consumer executions pass through Kyoto with source/corpus/
toolchain/target receipts; and unsupported types and allocation conventions are
explicit. Model/derive convenience closure is a separate N13 gate. Existing
causal-evidence qualification and its remaining receipts retain their own status.
