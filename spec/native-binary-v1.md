# native-binary-v1 (candidate)

Status: experimental candidate for M2/N11. This is the existing Rust audit format
in `src/trace.rs`, not a new portable encoding. Qualification requires lossless
native read/write interoperability, shared finite goldens, adversarial limits,
and exact semantic replay with compatible application codecs. This document and
corpus alone do not promote a profile or qualify replay, performance, durable
publication, binary v2 evidence, or debugger integration.

## Lossless native representation

Use a separate `AuditTrace` (or an additive lossless extension) containing model
metadata, RunConfig, initial bytes/checks, ordered TraceSteps, and termination
including ModelError text. The present independent native Trace types already
have an error string but lack RunConfig; do not silently default strategy,
seed, or parameters when importing binary evidence. Preserve explicit seed=None
versus seed=0, ordered duplicate parameter pairs, every UTF-8 string, all u32
metadata versions, and all u64 seeds. TypeScript must use BigInt or validated
unsigned decimal text for u64 values, never rounded Number.

Converting portable Trace to audit form requires explicit RunConfig or a clearly
named caller-selected default. Converting AuditTrace to the narrower portable
shape must return the omitted fields alongside it or reject loss; a plain
conversion that discards configuration is not interoperability. A model_error
termination carries its stored reason exactly, including empty text. Reject a
nonempty portable error string paired with another termination unless the
conversion explicitly retains it separately; binary v1 has no such field.
Checks and disposition reasons are variant data: passed/accepted have no reason
field, while failed/skipped/rejected/ignored carry a string even when empty.
Do not drop or invent these distinctions.

## Primitives and framing

All integers are unsigned little endian. u8/u16/u32/u64 mean fixed widths of
1/2/4/8 bytes. Blob is u32 byte length followed by uninterpreted bytes. String is
u32 **UTF-8 byte length** followed by strict valid UTF-8; never normalize Unicode
or substitute replacement characters. Vector is u32 count followed by its ordered
items. Empty strings, empty check IDs, empty blobs, duplicate parameter keys,
and duplicate check IDs are structurally permitted. Model meaning is separate.

The exact 12-byte header is hex `53544c4553531a0a01000000`: eight-byte magic
`STLESS` + 1a 0a, u16 version=1, u16 flags=0. There is no header checksum.
Each subsequent frame is u8 tag, u32 payload length, payload, u32 CRC32. CRC32
covers the five frame-header bytes and payload, excluding its stored checksum.
Use reflected IEEE CRC32 polynomial 0xedb88320, initial accumulator 0xffffffff,
final xor 0xffffffff; bytes processed in stream order. The standard check vector
`123456789` is 0xcbf43926. Store checksum little endian. CRC detects accidental
corruption and authenticates nothing.

Exactly one RUN(tag1), zero or more STEP(tag2), and one END(tag3) must occur in
that order. A footer is mandatory even for zero steps. After END require EOF;
concatenated artifacts, trailing padding, and second footers are errors.

RUN payload, in order:

1. String model name; u32 model_version; u32 properties_version;
   u32 codec_version; String build.
2. String RunConfig.strategy; u8 seed-presence (0 absent, 1 followed by u64).
3. Vector parameter pairs, each String key then String value, preserving order.
4. Blob initial_state; check vector initial_checks.

A check is String id then u8 status: 0 passed (no following reason), 1 failed
(String reason), 2 skipped (String reason). The check vector is u32 count plus
checks in emitted order. Binary v1 has **no successor-state/transition check
boundary**: initial checks have known initial-state phase, subsequent phases
are unknown_legacy. Do not infer phases from ID/order; verified explicit replay
upgrade or binary v2 is required to establish boundaries.

STEP payload, in order:

1. u64 zero-based index, exactly equal to preceding STEP count.
2. Blob input; u8 disposition: 0 accepted, 1 rejected + String reason,
   2 ignored + String reason.
3. Vector outputs, each Blob; Blob post_state; check vector checks.

END payload: u64 total STEP count followed by u8 termination: 0 completed,
1 property_failed, 2 step_limit, 3 interrupted, 4 model_error + String reason.
No other tag values, optional fields, or implicit trailing fields are allowed.
Completed means supplied inputs ended, not graph exhaustion. ModelError means
only the stored prefix precedes an adapter error; replay of that prefix does
not verify the terminal error. Structural readers do not rerun checks, enforce
model compatibility, or require termination/check consistency; those belong to
semantic replay. A structurally valid but semantically unusual trace must not be
silently rewritten by the codec.

## Limits and strictness

Independent binary limits must match Rust ReadLimits, on writing and reading;
portable JSON/payload quotas are different and cannot substitute for them.
Raising one field does not raise another. Inclusive defaults:

| Field | Default | Counted scope |
| --- | ---: | --- |
| max_total_bytes | 67108864 | Header, all frame headers/payloads/CRCs, footer |
| max_frame_bytes | 8388608 | One frame payload, excluding nine framing bytes |
| max_blob_bytes | 4194304 | Each opaque blob |
| max_string_bytes | 65536 | Each UTF-8 string's bytes |
| max_steps | 100000 | STEP records |
| max_checks_per_step | 4096 | Each check vector, **including initial_checks** |
| max_outputs_per_step | 4096 | Each STEP output vector |
| max_parameters | 1024 | Parameter pairs |
| max_items | 250000 | Sum of STEP records, all checks, all outputs, all parameter pairs |

All length/count additions must be checked, never wrapping or using lossy float
math. Frame payload lengths, blob/string byte lengths, and vector counts must
fit their u32 wire fields even if a caller raises limits beyond them. STEP
indices and the END total STEP count retain their explicitly specified u64
width; do not impose an additional u32 step-count limit.
Before reserving a frame, validate payload length against max_frame_bytes and
remaining max_total_bytes including the four checksum bytes. Before collection
allocation validate count against its individual quota and aggregate items,
then against remaining payload's minimum item width: parameter pair>=8 bytes,
output>=4, check>=5. Before copying a blob/string check its length quota and that
its bytes fit the frame. These checks bound encoded bytes and retained collection
items, not allocator overhead, application codec allocations, or process RSS.

Reader validates header magic, version, flags; then frames/checksums and fields.
Wrong checksum is rejected before payload interpretation. Unknown/misplaced
records, missing footer, noncontiguous index, wrong footer count, bad enum tags,
invalid UTF-8, any field beyond its frame, unconsumed payload fields, and trailing
file bytes are errors. No coercion or prefix acceptance on invalid artifacts.
An I/O short read is truncated; a length field beyond an otherwise complete
frame is invalid_data. Oversized declared frame may be rejected by quota before
its missing payload is read. EOF probing after footer may block on an unbounded
socket; provide bounded input and host I/O deadlines. No decoder callback can
make a blocking reader cooperatively preemptible.

Writer validates the entire trace and total lengths/items before publishing its
first byte. Validate all frames, not only a prefix. One reusable frame buffer is
sufficient; the native implementation need not clone every payload simultaneously.
I/O failure can still leave a partial stream. Writing does not flush, close,
fsync, atomically replace, or authorize exporting the artifact; durable/private
publication is a separate host operation.

Rust recorder's reserved model-error footer uses a UTF-8-boundary-truncated reason
up to min(256, max_string_bytes, max_frame_bytes.saturating_sub(13)) bytes and may
append `...` when truncating with room>=3. This is recorder capture policy, not
a codec rule: the binary codec must preserve any already-supplied ModelError
reason within its limits and must not truncate reasons during read/write.

Rust's recorder also appends reserved `stateless.engine.version` and
`stateless.engine.build` parameters. The structural codec preserves supplied
configuration without adding these fields. Static format goldens and freshly
recorded artifacts therefore have separate configuration expectations. Cross-engine
tests must retain each producer's actual provenance rather than remove stamps
or invent another engine's build identity to obtain equal bytes.

## Candidate finite corpus

`conformance/native-binary-v1.json` stores fixed hand-constructed bytes_hex and
normalized trace values. Blob values are lowercase hex; u64 seed is absent/null
or unsigned decimal text; versions remain exact u32 integers; parameters are
ordered two-element arrays. Termination is {kind,reason}, reason null except
model_error; disposition and check status use corresponding kind/status and
nullable reason. Error fixtures use expected_error {kind,detail} and optional
limit overrides over defaults. Do not derive expected bytes from an engine.

The independent builder in `conformance/test_native_binary_profile.py` implements
only format arithmetic. It generates fixed corpus bytes using Python's standard
CRC, and tests framing with a separate bitwise CRC implementation and the
standard check vector. This is not a native encoder under test. Valid cases pin
empty/single-step/property-failure, all dispositions and terminations, arbitrary
blob octets, strict Unicode, ordered duplicate configuration, absent/zero/full
u64 seeds, and maximum u32 metadata versions. Negative cases cover malformed
headers, CRC, byte-prefix truncation examples, trailing bytes/fields, unknown or
misplaced records, bad tags, index/count errors, field/count bounds, and each
limit family. Qualification additionally requires every-prefix truncation,
reader chunking/I/O failures, writer no-publication on invalid data, unsafe
integer rejection, and native↔Rust exact cross-read/re-encode/replay tests.
