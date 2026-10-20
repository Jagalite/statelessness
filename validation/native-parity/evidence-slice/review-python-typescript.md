# Python and TypeScript causal evidence static review

2026-10-20. Independent review of the final source identities below against actual
Rust `evidence.rs`/`value_codec.rs`, the relevant execution/monitor/trace paths and
`spec/causal-evidence-v2.md`. No remaining actionable finding in this scoped static
pass. This is source review, not execution qualification: no builds or tests were
run by this review. Final native suite and installed-consumer receipts remain
separate evidence owned by their qualification agents.

The shared corpus remained unchanged: 52 wire, 16 replay/upgrade, 20 recorder and
13 diagnostic cases (101 rows). No source or fixture was edited by this reviewer;
only this requested report was written. Implementing authors applied fixes and
added native regressions; their test execution is not inferred from static reads.

## Findings and disposition

| Finding | Final disposition |
| --- | --- |
| Python EvidenceRecorder rebuilt the entire provenance environment container before structural/label admission. | Fixed: the closed immutable input remains borrowed through encoded-size and label checks; `_own_provenance` acquires ownership afterward. Causal context follows the same admission boundary. |
| Python TraceV2 shape checking applied selected step/check/output limits before recording semantics. | Fixed: closed-schema shape checking is separate from quota admission; continuation/termination semantics precede v1 wire quotas. |
| TypeScript upgrade performed artifact quota validation before model identity. | Fixed: admitted artifacts are detached before callbacks; unfit artifacts retain metadata plus a cached failure, allowing the shared replay kernel to decide identity before surfacing the deferred artifact error. No unadmitted payload copy is made. |
| TypeScript v2 capture could report metadata/causal limits before embedded recording semantics, envelope size or phase mapping. | Fixed: provenance semantics, recording semantics, v1 sizing, raw metadata sizing, envelope total, coverage/overflow, per-row boundary and causal validation now occur in their specified order. Metadata row counts are not substituted with trace step/item quotas. |
| TypeScript stable getter caching retained a journal proportional to a known-unfit source before its count quota rejected it. | Fixed: discovered count failures disable and clear the cache; the semantic scan continues without retaining per-item source data. A lazy indexed preflight handles rejected sources without payload/source-collection copies. Accepted-source getter caching remains bounded by admitted counts; rejected-source getters may be reread, as documented. |
| The first cache fix surfaced a deferred count error before an earlier RUN field error. | Fixed: count discovery only controls retention. Ordered v1 preflight now decides the error; for example, an initial blob failure precedes a competing zero-step allowance. |
| TypeScript raw metadata counting could scan an entire oversized causal string before checking remaining metadata bytes. | Fixed: count prefixes and remaining allowance are checked before UTF-8 scanning/copying; a code-unit lower bound rejects already-unfit strings. |
| TypeScript Recorder construction used provenance semantic-first validation, unlike Rust's encoded-provenance budget, base-size, then semantic order. | Fixed: a separate `captureProvenanceForRecorder` performs bounded raw sizing, the additional 12-byte base check, semantic validation and then owned capture. TraceV2 validation retains its distinct semantic-first order. |
| Python buffered new-file output could reach the kernel only when closing, after `fsync`. This was found by the implementing author. | Fixed and independently reread: `os.fdopen(..., buffering=0)` makes writes reach the descriptor before synchronization. The author added a synchronization-boundary regression. |
| Python closed-schema union membership could invoke a foreign metaclass equality hook. This was found by the implementing author. | Fixed and independently reread: exact-type alternatives use identity (`is`), including Artifact, collection, byte-input and export-policy boundaries. |

## Additional boundaries checked

Python upgrade retains supported-API immutable closed values: admitted blobs are
exact `bytes`, collections are owned tuples, and metadata/check/disposition and
provenance values are frozen closed dataclasses with immutable primitive fields.
No supported mutable-alias defect was found in that borrowing path. Hostile
reflection such as bypassing frozen dataclasses remains outside the documented
integrity convention. TypeScript captures admitted mutable bytes before model
callbacks and caches invalid-entry errors without bypassing identity ordering.

Both recorder paths resolve references against the current retained output
window before planned eviction, prepare replacement journal storage before trace
commit, and update the phase journal only after successful underlying commit.
Persisted phase boundaries remain separate from combined checks. Semantic replay
divergence is not overwritten by phase divergence. Passive v1 reading invents no
phase and executes no model; upgrade remains explicitly executable.

Diagnostics access selected status/ID/phase fields, omit payloads/details/model
identity/causal labels, retain exact allowlist identity and Rust's control/UTF-8
spelling, and cap the whole output. Node filesystem access remains in the explicit
subpath adapter. Python and Node precompute bytes, create exclusively, request
private Unix permissions, write and synchronize, preserve existing paths and
report incomplete new files on post-create failure. Neither claims rename-atomic
publication, directory synchronization, browser filesystem parity or a hard RSS
bound. Structural allocation accounting remains the explicit qualified Rust64
logical schema convention rather than managed-heap measurement.

## Reviewed source identities

Paths are relative to the repository root; values are SHA-256.

```text
bac44cf62db1c2c40c9f66fe7ad1d941fa33cda5badc4709374d0b930355391e  src/evidence.rs
9c8ec3107cc69f6febe24cb2969d66d344fc1f19cee3341484f7b0dda274baa3  src/value_codec.rs
acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0  src/execution.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
c4b22b63aebc8e94dfe83e0228f0bc0aeb7abce899eb18987154bd98e7a203e7  spec/causal-evidence-v2.md
f3e6d9068bf526b47822e355b2fadd2bf4472b305869fbaaba94e0a748a899f7  conformance/causal-evidence-v2.json
2a57bdbf280daa3a9f01efd8235f50c7b1310e4281dec25fdddcbd5997ad75b3  python/src/statelessness/evidence.py
5a5f2d1697c620a6a3f2dc88163901bbf3a52c050cb6505e2a69d8ee06d9142b  python/src/statelessness/evidence_recorder.py
68f7797071346d294ab8077c031c182973787d42371c2b951af46124c1e419d4  python/src/statelessness/trace_binary.py
3072b0a097cd2d7c1d774eb18388debebaef3086e78dcd6592e7ad19005f1055  python/src/statelessness/replay_evidence.py
5a450a24632d2f304d3714cccd1723654d568550d92d47f7ff38d7f8568e0b70  python/src/statelessness/checked.py
034e6f479504860d727a5354245ced7230675f04d10592530eba047eb9120493  python/src/statelessness/monitor.py
b4db937ad59a667fbaca66a2b9473461d72a7373b7fc7124637dfafcfd0f1a33  typescript/src/evidence.ts
10148303f4f88cb4d7433aacfdbbeff2d53c75a31153569fde29cc57da23e566  typescript/src/evidence_recorder.ts
c659fa34979b129591ec6b9789fdf9383867c931572226c59e26fe914ad7c846  typescript/src/node_evidence.ts
9da3c179525b336a779cd88afe396b90bf3ab06e16c9184c117d0b1d2cdb2747  typescript/src/binary.ts
207923eb14891e6e99e8199530ec4cf0fb9282e7c12a704545f0ba76dbfd588e  typescript/src/replay.ts
ee3df47d3b03c09489343184aa2ce2917f29348119dd605b55003aee727ce45d  typescript/src/checked.ts
5aeda96d9d961d04632361b017d1ab841a1a3f260cf63c36c1a8be88bab7e732  typescript/src/runtime_recorder.ts
```
