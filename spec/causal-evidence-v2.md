# causal-evidence-v2 (candidate)

This experimental extension describes actual Rust `evidence` and `value_codec`
behavior. It covers TraceV2, Artifact, explicit upgrade, phase-aware replay, the
real EvidenceRecorder, diagnostic policy and exclusive filesystem exports. It
does not change promoted portable profiles. Labels and CRC provide assertions
and corruption detection, not authentication or validation of host claims.

## Owned values and exact wire

Provenance fields in order are run String, epoch u64, manifest Option<String>,
source Option<String>, environment Vec<(String,String)>. Environment order is
preserved. Names must be nonempty and unique by exact UTF-8 identity; values and
other labels may be empty. NFC and NFD spellings remain distinct, without Unicode
normalization. OutputOrigin is sequence u64 then output u32. CausalContext fields
are machine Option<String>, generation Option<u64>, correlation Option<String>,
logical_time Option<u64>, causes Vec<OutputOrigin>. StepEvidence fields are
sequence u64, state_check_count u32, causal CausalContext. Logical time and
generation have no monotonicity requirement. Absent optional fields stay absent.

TraceV2 owns the unchanged complete binary-v1 trace, checkpoint_after u64,
Provenance and one StepEvidence per retained turn. Artifact distinguishes V1 from
V2; passively reading V1 never guesses phases or runs an application. Native
mutable provenance/context collections are copied before retention and snapshot
accessors return independent owned evidence, under the existing faithful-clone
and deterministic-model contracts. U64 values must remain exact, including in
TypeScript. Structural parsing requires no model or host application resource.

Canonical metadata consists of checkpoint_after, Provenance, then Vec<StepEvidence>.
Integers are fixed-width little-endian. String and vector lengths are u32
little-endian; strings contain exactly that many strict UTF-8 bytes. Option tags
are one byte: zero means absent, one means followed by the value; other tags are
invalid. Tuple/struct fields concatenate in declaration order with no extra tags,
padding, field names or JSON. Vector elements follow their length in order.

The envelope has eight bytes `53 54 4c 45 53 53 1a 0a`, version u16 little-endian
2, two zero reserved bytes, complete embedded-v1 byte length u64 little-endian,
metadata byte length u64 little-endian, unchanged v1 artifact, metadata, and u32
little-endian CRC32 over every preceding envelope byte. CRC32 uses reflected
polynomial 0xedb88320, initial 0xffffffff and final complement. Envelope overhead
is exactly32 bytes. Embedded-v1 framing/checksums remain unchanged.

Artifact.read accepts version1 through the structural v1 reader, including
structurally valid but semantically inconsistent v1 artifacts. Version2 validates
reserved bytes, exact checked lengths, metadata allowance, CRC, embedded v1,
complete metadata decoding and full TraceV2 semantics. Reject extra/truncated
bytes, unsupported versions, invalid tags/UTF-8 and trailing metadata. The entire
reader is bounded by trace.max_total_bytes; bytes over that limit fail before
further parsing. Writers validate/precompute the entire envelope before writing
any output; callback I/O failure can leave a partial externally written stream.

## Independent limits and validation

EvidenceLimits contains all nine binary-v1 quotas as trace, plus metadata bytes
(default4194304), environment pair count (1024), individual label UTF-8 bytes
(4096), causes per turn (32), and total retained causes (100000). Increasing one
allowance does not increase another. max_labels applies only to environment pairs.
max_label_bytes applies to run/manifest/source, environment keys/values and causal
machine/correlation. Metadata structural budgets are independent of trace.max_items.

Metadata decoding bounds bytes by max_metadata_bytes, elements by the same value,
depth by16, and work/allocation by saturated16 times metadata bytes. Every value
costs one work unit before depth checking; depth rejects entering when current
depth is already the maximum. Collections first charge element count, then checked
count-times-element-size allocation, then require count no greater than remaining
work; strings additionally charge their byte length against work before reading
and strict UTF-8 validation. Failure is sticky across subsequent decoder calls.
Native implementations use explicitly documented Rust64 logical schema allocation
weights (String24, String pair48, OutputOrigin16, StepEvidence120), whose
target64 layout is checked by the reference qualification: these are bounded structural
requests, not Python/JavaScript heap bytes or RSS. Non64 target layout equivalence
is not qualified. Preflight occurs before allocation/copy; host OOM/fatal traps and
arbitrary application allocations are outside these quotas.

TraceV2.validate checks provenance first, then v1 first-failure/termination
semantics, exact v1 wire quotas, metadata encoding cap and complete-envelope total
size. Require equal phase-row/turn count. Check checkpoint_after + retained count
for u64 overflow; row sequences equal checkpoint_after + local index +1 and each
boundary is at most that turn's check count. Each cause is a unique (sequence,
output) pair, has positive sequence and precedes its input's absolute sequence.
Different output indices of the same earlier source are permitted. Accumulate
retained causes against their independent total cap. A cause after the checkpoint
must address an actual output of the retained referenced turn. A cause at/before
the checkpoint may reference omitted-prefix evidence; do not invent its payload
or reject its u32 output merely because the prefix is absent.

check_phase uses LOCAL sequence0 for checkpoint checks (initial_state), followed
by local1-based retained turns and their saved state/transition boundaries.
Out-of-range positions return no phase. V1 knows checkpoint initial phases only;
all later check phases remain unknown_legacy. Passive phase lookup and diagnostic
export do not themselves validate manually constructed Artifact values.

## Replay and explicit upgrade

TraceV2.replay first fully validates evidence, before model identity or decoding;
invalid evidence therefore wins over an incompatible model. Then run bounded
exact replay using the stored trace config and the current model metadata. At an
otherwise exact turn compare actual complete state-check boundary against its
persisted boundary. A mismatch returns Diverged at that local turn, field
check_phase, steps_verified before that turn, failure_reproduced false. Existing
semantic divergence takes precedence. Combined identical checks moved from state
to transition checking are a phase mismatch even though ordinary replay is exact.
No host causal labels are executed/authenticated. Ordinary replay error recovery
and model-error terminal limitations remain as specified by replay-evidence-v1.

verify_upgrade is an explicitly requested execution operation: bounded replay must
be Exact and compatible. Build metadata from actual completed turn boundaries,
checked-add the caller checkpoint origin, and use empty causal contexts. Preserve
the original trace and supplied provenance, then validate the resulting evidence.
Invalid provenance can therefore fail after application replay, whereas a passive
reader runs no model. Incompatible/divergent/noncanonical replay cannot upgrade.
An exact ModelError prefix can upgrade; that does not reproduce its terminal
external error. The operation returns evidence, not an invented replay report.

## Coordinated real recording

EvidenceRecorder takes a complete sealed CheckedInitial, absolute origin, config,
RecorderOptions, Provenance and EvidenceLimits. It reserves32 plus the ENTIRE
selected metadata allowance from the smaller retention/trace-total budget before
creating the underlying runtime Recorder. Replace RecorderOptions.limits with
EvidenceLimits.trace. The monitor's recorded max_retained_bytes is this reduced
available amount; preserve it truthfully. Small envelope budgets require an
explicitly smaller metadata allowance; never silently shrink defaults. Provenance
encoded bytes plus12 (checkpoint and vector length) must fit metadata allowance.

observe accepts only a sealed CheckedTurn and owned causal context. It never
executes reducers/checkers. Reject an already-frozen recorder before other work.
Preflight context first, checked-u32 phase boundary, encoded row size and aggregate
causes; these can precede inner coverage/sequence errors. Plan sufficient oldest
phase-row evictions to fit metadata/cause budgets. A candidate too large alone
fails. Resolve causal references against CURRENT retained outputs/checkpoint
before acceptance: planned eviction does not excuse an invalid current index.

Pass the minimum planned eviction count into the real runtime Recorder's private
admission path, which may require additional trace byte/step evictions. Commit
trace first. Only after success drop exactly the same phase prefix and append the
candidate. On preparation failure freeze the old coherent prefix through normal
stop-with-error; on inner commit failure keep the phase journal unchanged.
Snapshot checkpoint_after equals origin plus actual local evictions. Phase/trace
lengths stay aligned after every commit and failure. Initial/property failure and
first error cannot be overwritten. A separate metadata ring beside an unmodified
recorder does not implement these semantics. No public unchecked bypass is added.

## Diagnostic policy and filesystem exports

ExportPolicy selects max_steps (turns only), max_bytes for the WHOLE output and
an exact allowlist of property IDs. diagnostic_json emits the nonreplayable schema
statelessness.diagnostic-export/1, omits payloads/check details/model identity/build
and causal labels, retains ordered statuses/phases/local sequence, and renders
nonallowlisted IDs as null. NFC/NFD allowlist IDs remain distinct. Always include
the initial observation even at zero turn allowance. truncated reflects omitted
turns only; byte overflow is an error, not a silently shortened document. V1
checkpoint_after/omitted_prefix are null; V2 emits exact decimal checkpoint and
checkpoint!=0 boolean. Completed maps completed_sequence, PropertyFailed maps
property_failure; remaining kinds map step_limit/interrupted/model_error.

Exact diagnostic bytes use Rust escaping: quote/backslash are escaped; EVERY
U+0000..001f control is lowercase `\u00xx`, never short newline/tab escapes. Other
Unicode, HTML characters, slash and U+2028 remain literal UTF-8. Whole-output byte
caps apply to that spelling; generic host JSON serializers can produce different
bytes and are not interchangeable. Stream/count bounded output before copying
oversized allowlisted IDs or publishing partial output.

Raw-new and diagnostic-new filesystem exports precompute bounded bytes, create
exclusively without overwrite, request Unix0600 (umask may narrow), write all and
sync the file. Creation failure leaves the existing path untouched. Write/sync
failure after creation may leave an incomplete NEW file and must say so; this is
not rename-atomic publication or directory-fsync durability. Expose byte APIs in
browser cores and an explicit Node/filesystem adapter separately. Do not perform
implicit downloads or acquire host resources while merely parsing an artifact.

## Finite qualification

conformance/causal-evidence-v2.json contains independently authored static
wire_cases, replay_cases, recorder_cases and export_cases. Env
STATELESS_EVIDENCE_CORPUS selects it. Wire rows carry exact hex, expected artifact
kind/owned values or error substring, with optional partial EvidenceLimits nested
trace overrides. Metadata uses exact snake_case fields, u64 decimal strings,
u32 numeric boundaries/outputs, ordered environment pairs and explicit nulls.
Replay rows select replay or upgrade with a finite decimal-codec model; phase_shift
moves s out of turn state checks into transition [s,t], keeping combined checks.
Initial checks remain [s]. Successful upgrade compares returned steps and preserved
context, not a nonexistent report. Recorder rows supply already executed turns,
sealed checking policy and causal context; expected snapshots include canonical
numeric trace projection, phase rows, absolute checkpoint and local monitor counts.
Diagnostic rows assert exact UTF-8 JSON and byte caps. Native ownership, exception,
allocation failure and filesystem adapter tests supplement the typed Rust cases;
all execution/build qualification runs through Kyoto with source identities.
