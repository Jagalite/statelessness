# Evidence formats and privacy

`stateless::evidence` adds an opt-in v2 envelope. Existing binary v1 bytes,
application codecs and model equality are unchanged. `EvidenceRecorder` accepts
sealed `CheckedInitial` and `CheckedTurn` observations; it never runs the reducer
or a checker again. Each retained turn includes the absolute host sequence and
the state/transition check boundary. Ring eviction advances the stored checkpoint.

| Artifact | Reader | Check phases | Replay |
| --- | --- | --- | --- |
| Binary v1 `.sttrace` | `Trace::read_from`, `Artifact::read` | Initial checks known; subsequent phases unknown | Existing exact semantic replay |
| Binary v2 `.sttrace2` | `Artifact::read` | Per-turn state check count, then transition checks | `TraceV2::replay` also verifies phase boundaries |
| Portable `trace-json-v1` | Each qualified native implementation | Profile-specific, not inferred from binary | Only the declared portable profile |
| Diagnostic JSON export | JSON consumers; schema below | Stored phase or `unknown_legacy` | **Not replayable** |
| Debugger metrics/detail | Existing debugger APIs | Host observation provenance | Diagnostic data, not canonical model state |

The v2 header is `STLESS` + `1a 0a`, little-endian u16 version 2, u16 flags 0,
u64 embedded-v1 byte length, and u64 metadata byte length. An unchanged complete
v1 artifact follows, then bounded `value_codec` metadata, then CRC32 of all
preceding bytes. Metadata encodes checkpoint sequence, `Provenance`, and the
ordered `StepEvidence` vector. Unknown flags/versions, trailing bytes, CRC
mismatch, inconsistent lengths, invalid phase counts and invalid retained causal
references are rejected. `EvidenceLimits` bounds the entire input, metadata,
labels, check/step counts, decode work/depth and causal links. Synchronous readers
and application callbacks still require host I/O deadlines when facing a peer.

For `EvidenceRecorder`, the complete v2 artifact must fit the smaller of
`RecorderOptions.max_retained_bytes` and `EvidenceLimits.trace.max_total_bytes`.
The recorder reserves all `max_metadata_bytes` plus the 32-byte envelope framing
before assigning the remaining budget to the embedded exact trace, including its
error footer. Construction fails if that reservation and initial trace cannot
fit. A small capture budget therefore requires an explicitly smaller metadata
allowance; unused metadata reservation is not borrowed by exact trace capture.

Provenance includes run/epoch, optional source/manifest identity and explicit
environment bounds. Causal context includes machine, generation, correlation,
modeled logical time and originating output references. A reference before the
checkpoint is an explicitly missing retained prefix; the reader cannot validate
its output index. A retained reference must precede its input and name an actual
output. Labels are host assertions, never authentication. CRCs detect accidental
corruption; neither CRCs nor build hashes prove who produced evidence.

`TraceV2::verify_upgrade` is an explicit bounded replay operation for v1 evidence.
It derives phase boundaries only after exact compatible replay; it cannot recover
unrecorded causal context. `EvidenceViewer` reads and compares stored boundaries
without a model or effect executor and selects a failed check's recorded phase
for minimization. Legacy callers must supply a phase or explicitly upgrade.
Minimization continues to enforce the existing environmental admission policy.
Replay and forks do not roll back databases, OS resources or network effects.

## Classification and export

Treat raw artifacts as sensitive application data. State, input, output, rejected
disposition strings, check details, metadata, paths and correlation labels can all
contain secrets. An inspector's redaction does not sanitize the underlying trace.
Keep raw capture in a restricted local directory and define retention in the
consumer application. Make raw export an explicit host/user action. The library's
`write_raw_new` creates a new file, refuses overwrite and requests Unix mode 0600;
on other systems the host must provision directory/file ACLs. It does not promise
secure deletion, encryption or crash-atomic publication.

`diagnostic_json` and `export_diagnostic_new` produce the separate
`statelessness.diagnostic-export/1` schema with `replayable: false` and named
redaction policy `omit-payloads-v1`. Default exports contain only local sequence,
status, phase and a static termination category. Raw values, details, model/build
names and causal labels are omitted. Property IDs are null unless explicitly
allowlisted after classification. Output bytes and steps are bounded; truncation
is explicit. This export deliberately cannot be opened by the exact reader.
The numeric checkpoint origin and omitted-prefix flag distinguish a retained v2
suffix from run start; both are null when the legacy artifact's origin is unknown.

The negative suite includes every byte-prefix truncation of a valid envelope,
checksum/header/length corruption, phase mismatch, causal-link bounds, metadata
quotas, redaction sentinels and private create-new output. These tests are added;
local Rust execution is pending under the current compile-only policy.

The CLI's `stateless inspect TRACE` accepts v1 and v2 for explicit raw offline
inspection. `stateless export-diagnostic TRACE NEW_JSON` writes the default
redacted diagnostic export without executing a model. Optional `--max-steps` and
`--max-bytes` lower or raise the selected export bounds; the output must be new.

## External debugger transports

The local debugger protocol is not a network security boundary. A host exposing
it remotely must authenticate the peer before reading commands and enforce
authorization independently of trace/model identity. Use a platform-authenticated
local socket or authenticated encrypted transport, bounded request bodies, I/O
deadlines, rate limits, origin checks for browser clients and audit records that
omit raw values. Default roles should permit only selected read operations.

Resolve an authenticated principal into a read capability or an explicit live
control capability. Classify every parsed command before dispatch: unknown/new
commands are denied until assigned a role. A read capability must not authorize
pause, input injection, stepping, restore/fork, configuration changes or raw
export. Recheck control authorization for each request; possession of a trace,
session ID or checksum is not authority. Keep raw export consent separate from
inspection permission. This pattern is a host responsibility, not authentication
implemented by the library or a promise that arbitrary inspection callbacks are
bounded in CPU/allocation.
