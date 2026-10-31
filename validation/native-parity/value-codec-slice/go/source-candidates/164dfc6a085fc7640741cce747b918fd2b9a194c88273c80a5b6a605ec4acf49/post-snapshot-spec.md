# canonical-value-codec-v1 (design candidate)

This is a normative design draft from Rust `src/value_codec.rs`, not an
implementation or qualification result. The companion corpus is independently
hand-authored wire arithmetic and source-derived budget expectations. No Rust or
native execution has qualified this draft, no target/layout is newly qualified,
and no existing codec, default, portable profile or causal-evidence qualification
is changed. General value codecs are an N11 building block; derive/model glue
remains a separate N13 gate.

## Schema and API boundary

A schema determines the type; bytes carry no dynamic type tag. Expose composable
native descriptors/protocols, bounded encode/decode helpers and a public reader.
Names may follow each language's conventions. In particular, this design does
not require Go to reuse its existing unrelated Codec name, Swift to raise its OS
deployment target, or TypeScript to encode exact 64/128-bit values as Number.
Retain existing application ModelCodec hooks; adopting descriptors is explicit.

Encoding and decoding are independent capabilities, as Rust's `TraceEncode` and
`TraceDecode` traits are independent. A custom descriptor may provide either or
both; a custom factory rejects a declaration providing neither. Composition
preserves each capability separately: options, vectors, arrays, tuples,
OracleState and length overrides support a direction only when every required
component supports it. This applies even to None, empty vectors and zero-length
arrays; their data cannot bypass a missing component capability. A mixed
composition may exist as a descriptor with neither direction, but both
operations reject it. Capability metadata is immutable and unavailable
operations fail before emitting or consuming bytes. Encoding never requires a
decoder or collection allocation descriptor; an unknown element layout is
required only when decoding its collection storage request.

Provide distinct operations corresponding to guarded `Decoder.value`, concrete
raw `TraceDecode::trace_decode`, and direct `LengthDecode::decode_length`. They
are observably different. Top-level `from_trace` constructs a reader, enters one
guarded value and requires `finish`; decoding a prefix alone is insufficient.
`trace_bytes(maximum)` creates a bounded EncodeBuffer, calls encoding and checks
finish before returning. Encoders append to an existing sink rather than clearing
it; sink construction controls clearing. Existing encode-buffers-v1 sticky finish,
shared framing and native lease guarantees continue to apply. Diagnostic schema
spellings and optional variant labels are not compatibility identities, schema
hashes, wire tags or executable registration.

## Exact built-ins

| Schema | Wire |
| --- | --- |
| u8/u16/u32/u64/u128 | 1/2/4/8/16 little-endian bytes |
| i8/i16/i32/i64/i128 | Same widths, two's-complement little-endian |
| bool | One byte: zero or one; other values invalid |
| unit | No bytes |
| String | u32 UTF-8 byte length followed by exact strict UTF-8 |
| Vec<T> | u32 element count followed by each T in order |
| Option<T> | Byte zero absent, byte one followed by T present; other tags invalid |
| [T;N] | Exactly N consecutive values, with no prefix |
| Tuple arity1..4 | Fields in declared order, no prefix or tag |
| OracleState<S,H> | S then H, no prefix or ORC1 framing |

Integer encoding never truncates, rounds or implicitly coerces. String lengths
count bytes, not characters/code units. Preserve leading BOM, embedded NUL,
non-ASCII and canonically equivalent but byte-distinct strings. Reject malformed
UTF-8 and unpaired native UTF-16 surrogates; no replacement, normalization or BOM
removal. Preserve duplicate vector/tuple values and absent versus present unit or
empty values. There are no built-in float/char/usize/isize, map/set, arbitrary
object, reference-graph or dynamic serialization schemas.

String/vector LengthEncode/LengthDecode overrides accept prefix widths1,2,4.
Only that container's length prefix changes; nested containers keep their own
explicit/default schemas. Fixed arrays have no length override. Values/counts
outside the selected prefix range fail `length overflow`; unsupported widths fail
`unsupported length width`. Bounds are inclusive. Changing length width does not
change integer element widths or turn encoded byte count into element count.

## Reader transitions and first error

Defaults are independent `bytes=16777216`, `allocation=33554432`,
`elements=1000000`, `depth=64`, `work=2000000`. They do not inherit artifact,
causal-metadata, model or portable-JSON limits. Counters/requests use checked exact
Rust64 unsigned arithmetic in the intended first target convention; native APIs
must reject unsupported/unsafe representations rather than silently round.

Construction checks input length against bytes before invoking a decoder or
copying an unadmitted input. `take(n)` first returns a prior sticky failure, then
checks remaining input. Insufficient input records `truncated value`. Taking raw
bytes consumes input but does not consume per-byte work or increase depth.

Guarded `value(schema)` first returns an existing failure, then subtracts one work
unit, then checks current depth against its maximum. Work failure therefore wins
over simultaneous depth exhaustion. Increment depth for the callback and decrement
on normal success/error return. A callback error is recorded as the reader's first
failure. A callback returning success after swallowing a reader failure still
fails. The first failure wins over a different subsequent callback error, later
reads and finish. Rust does not catch callback panic or restore depth for that
unwind; native exception handling is described separately below.

`collection(n, element_size)` does these checks in order:

1. Return the reader's prior sticky failure.
2. Subtract n from aggregate elements, or record `decode element limit`.
3. Checked-multiply n by element_size at target usize width, or record
   `decode allocation overflow`.
4. Subtract the request from allocation, or record `decode allocation limit`.
5. Require n no greater than remaining work, or record `decode work limit`.

The final check does NOT subtract n: subsequent guarded entries do that. Charge
before collection storage allocation. Zero count times the largest size is zero,
not overflow. Zero-sized elements consume elements and guarded work even when
allocation and encoded bodies are zero. In particular a huge Vec<()> count must
fail its element/work admission without a positive-element-wire-width assumption,
large allocation request or multi-billion-iteration test run.

Strings enter the length integer through guarded value, call collection(n,1),
subtract n byte-work, take bytes, validate UTF-8 and then acquire owned text.
Vectors enter a guarded length integer, call collection(n,sizeof(T)), reserve
storage and enter every T through guarded value. Fixed arrays call
collection(N,sizeof(T)) and enter every element through value; their temporary
Vec storage is charged even when the final array is inline. Tuples and OracleState
enter each declared field through value, but do not charge their own top-level
result storage. Integers and unit each consume one guarded entry. Bool consumes
two (bool then nested u8). Option consumes its own entry, one tag entry and the
contained value's entries only when present.

A standard string/vector thus has an outer guarded entry plus its guarded length.
Direct LengthDecode has only the guarded length entry, not an added outer entry.
A handwritten field adapter calling LengthDecode directly must not insert a
convenience value guard. A separately guarded custom wrapper does add its own
entry. Schema composition must preserve the chosen call graph rather than apply
one fixed per-byte or per-field budget formula.

`finish` returns a sticky reader failure first, then checks complete consumption.
A trailing-bytes error does not mutate Rust's failure field. A native top-level
helper may close its private reader after completion; a public reader's explicit
terminal lifecycle must be documented rather than claiming that trailing bytes
are a recorded structural failure. The corpus marks the reusable-Rust-reader
witness separately from mandatory guarded-reader failures.

## Raw/custom encode boundary

A direct TraceEncode/LengthEncode call returning Err does not automatically set
EncodeBuffer failure. Unsupported width and length overflow can fail before any
write. A custom encoder may append a prefix and return an error; if an outer
encoder explicitly catches that returned error, it may continue writing and
finish successfully. A raw callback that ignores a rejected write and returns
Ok must still return Ok from that RAW call; only a later finish reports the
sticky buffer failure. If that callback instead returns a different error, its
returned error stays primary for the direct call while the write failure remains
for later finish. Raw operations must not automatically finish on success or
replace callback errors with stored writer errors. An actual rejected buffer
write remains sticky even if its returned error was ignored. Explicit framed callbacks also poison on their
returned error, as specified by encode-buffers-v1.

Existing native whole-callback poisoning helpers are intentionally stronger
policies and must not be weakened. Expose a separate clearly distinguished raw
encode/length call, with revocable callback access, when claiming equivalence to
these direct public Rust traits. Do not silently route raw calls through a
helper that poisons on every returned callback error. Top-level encode helpers
still propagate a callback failure and check finish; this distinction matters
when a handwritten outer encoder catches the error. Native exceptions and
malformed native-only arguments can have separately documented stronger policy;
they must not be mislabeled as identical raw Rust Result behavior.

## Raw/custom decode boundary

Raw concrete decoding and direct length overrides are cooperative APIs. Only
take, collection and value guards record errors. For example, raw bool decoding
enters guarded u8 successfully, then may return invalid boolean outside a guard.
If custom code catches that returned validation error and returns success, no
reader failure was recorded. Likewise direct string UTF-8 validation or an
unsupported direct length width can return an error without recording one. If
propagated through an enclosing value guard, that returned error becomes sticky.
If a failed take/collection/nested guarded value was swallowed, the existing
reader failure remains sticky regardless of the callback's final return.

Do not present direct trait methods as an authenticated sandbox, or promise they
prevent arbitrary user allocation, loops or work outside the cooperative reader.
Native custom adapters must expose raw versus guarded calls explicitly. They may
contain catchable callback exceptions, restore depth in finally/defer and revoke
helper-owned callback leases; those are stronger native ownership policies than
Rust panic behavior. Preserve an already recorded first failure while containing
an exception. Public user-owned readers may remain usable across multiple calls;
reentrant guarded composition is how nested schemas work, not an invalid call.
Top-level helper inputs must not be concurrently mutated during admission/copy.
Decoded arrays, byte-backed custom values and containers must not alias mutable
input buffers unless an explicit separately named borrowed API documents it.

## Explicit allocation descriptors

Wire width is not allocation weight. The intended first convention uses explicit
logical Rust64 layout descriptors, pinned to a measured Rust target/compiler.
These are structural request weights, not Go/Swift physical layout or
Python/JavaScript heap/RSS measurements. A schema needs its allocation descriptor
when it is the element of a collection (or an array element whose decoder makes
temporary storage). Top-level scalar/option/tuple results have no independent
allocation charge; they can have well-defined wire decoding without requiring an
otherwise unused compound layout guess.

The corpus ledger is SOURCE-DERIVED EXPECTATION ONLY pending measurement. Examples
include fixed-width integers, bool1, unit0, String24, vector header24, Option<()>1,
Option<bool>1, Option<u8>2, Option<u64>16, Option<String>24, [u16;3]6 and
(u8,u64)16. These are explicit finite descriptors, not a generic inference formula.
In particular niches, nested options, tuple field alignment/reordering, u128
alignment and custom records require exact-shape measurement. Never fall back to
zero, tag-plus-payload, recursive wire sums or a host container's heap size.
Unknown compound layouts require an explicit validated descriptor before they can
serve as collection elements. No arbitrary Rust-layout algorithm or Rust32
qualification is implied.

Allocation is charged only at collection requests, and nested requests accumulate.
Vec<Vec<u8>> requests outer header storage (24 per element), then inner bytes.
Vec<[u16;3]> requests outer array-element storage and each inner array's six-byte
temporary storage. Vec<Option<u64>> requests its measured 16-byte option element
layout even for absent values; the option's scalar decoding adds no further own
storage charge. Unit weights zero but does not bypass work/element limits.
Custom decoders declare collection requests cooperatively; truthful descriptors
and allocation failure boundaries remain part of their documented contract.

## Native admission policies

Python must reject bool as an integer, enforce exact declared integer ranges,
reject malformed scalar Unicode and use explicit option presence rather than
None ambiguities. Go can use explicit limb values or deliberately scoped owned
big-int adapters for 128-bit values; native int is not a wire width. Swift can use
portable 128-bit limb descriptors and explicit unit/one-tuple forms without raising
OS deployment requirements. TypeScript uses bigint for64/128-bit values and rejects
unsafe/fractional/nonfinite Number coercions; Option and unit need explicit tagged
representations. Preserve typed-array view offsets and lengths.

Schema/length/limit validation is an explicit native boundary for values that
Rust's types cannot represent. Native invalid-integer, wrong-array-length,
malformed-host-string or wrong-bool inputs must not be fabricated as successfully
constructed Rust typed inputs. If a native type system excludes such an input,
record that static type exclusion rather than introducing a narrowing cast. When
the input is representable, qualify rejection at the actual public native
boundary; fixture-adapter-only validation is not runtime codec evidence. Catchable allocation failure can become a decoder
error, but process OOM, Swift traps and other fatal failures are not recoverable
ModelError claims. No hard RSS, preemption, concurrency, zero-allocation or
performance guarantee is introduced.

## Companion corpus and open gates

`conformance/canonical-value-codec-v1.json` is design-only and unexecuted.
Schema objects use kind, element, fields, length and length_width. Integer JSON
values are exact decimal strings at every width; unit is null; tuples/arrays/
vectors are ordered arrays; options are `{some:false}` or `{some:true,value:...}`;
OracleState is `{model:...,oracle:...}`. These are adapter values, not an additional
wire format or mandatory public API names.

`value_cases` contain exact independent bytes and encode caps; `guarded_value`
means normal from_trace, `guarded_length` means a guarded handwritten wrapper
around the direct override, and `direct_length` omits that wrapper. All require
finish. `decode_error_cases` carry exact failure strings and selected independent
limits. `encode_error_cases` use one-under caps and the direct first encoder error;
ignored-error/sticky finish behavior remains governed by encode-buffers-v1.
`encoder_cases` exercise direct public encode/length calls against one buffer,
catch each returned error, continue with later actions and compare finish and
retained bytes. They distinguish nonsticky returned validation/custom errors
from sticky rejected writes, and direct callback return from later finish. Rust
reference checks retain exact error strings. Native assertions may normalize only
the encode-limit message family: existing helpers may retain the first byte-limit
wording while Rust finish says byte-or-allocation limit. This presentation rule
does not permit adapter-only semantic recovery or changes to callback result,
finish result, sticky state, failure category or bytes.
`native_admission_cases` are explicitly native-only, including adjacent128-bit
range failures, bool/integer distinction, unsafe Number, surrogate and array length.

`length_cases` include arithmetic-only maximal prefix/overflow witnesses because
Rust's length primitive is private. They are NOT public-Rust execution claims.
Do not run a multi-billion-element encoder loop to manufacture u32-max coverage;
a bounded public reference strategy requires separate review. Unsupported-width
empty containers do use the actual public length-override surface.

`reader_cases` are ordered public-reader scripts. Top-level actions log a result
and continue after errors; a custom value body propagates a failed operation unless
on_error is explicitly swallow. A body then returns its declared ok/error value.
Expected callback_events record enter, operation result, swallow/propagate and
return order. A construction_error prevents all scripted callbacks/results. Raw take returns hex; decoded integers return decimal strings.
`rust_reader_lifecycle_native_policy_declared` is explicitly a lifecycle witness;
other reader first-error/guard cases are required shared semantics.
The automatic_checks section specifies every proper nonempty-encoding prefix,
trailing-byte rejection under default (not exact-fit byte) limits, admitted
subview offsets and owned-result independence. Zero-width encodings have no
proper-prefix requirement; no positive-width truncation heuristic is allowed.

The next gates are independent Rust64 layout measurement; real public Rust
execution of guarded/raw/custom boundaries; native implementation and source
review; installed-package interoperability; runtime/ownership regressions and
exact Kyoto source/command/target receipts. Custom record/enum builders, derive
length-field integration and ModelCodec version glue need a separate design/review
rather than silently expanding this built-in module. No implementation, build,
execution or parity claim is made by this draft.
