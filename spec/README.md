# Statelessness portable specification 1.0.0

Status: **experimental v1 profiles**. Protocol and fixture version: 1. This is a
language-independent contract for a deliberately bounded set of capabilities,
not a claim that every Rust feature has been ported. The specification, reviewed
examples, and independent properties are authoritative; Rust is an implementation,
not an oracle that automatically blesses new expectations.

## Profiles and compatibility

**PROFILE-001.** Implementations MUST advertise supported specification/protocol
versions and profiles. A claimed profile MUST pass every applicable required case.
Unsupported operations MUST be explicit; missing tests or unsupported capabilities
MUST NOT be reported as successful qualification.

| Profile | Required capabilities |
| --- | --- |
| `core-v1` | Native model callbacks, ordered checks, observation, recording semantics. |
| `bfs-v1` | Deterministic bounded breadth-first exploration and exact equality. |
| `rng-splitmix64-v1` | The specified 64-bit generator and bounded-choice operation. |
| `trace-json-v1` | JSON observation-envelope decoding/encoding and exact replay. |

The Rust runner and independent native Python, Go, TypeScript, and Swift packages
implement these four profiles.
The native ports do **not** provide fuzzing, shrinking, guided corpora, oracle
composition, campaign scheduling, runtime ring recording, composition helpers,
or Rust's binary `.sttrace` reader/writer. Those require additional profiles and
independent qualification; exposing an operation name alone is not parity.

Native APIs, ownership, allocation, internal hashes, error prose, and performance
may differ. Normative observations, ordering, identity checks, deterministic work
counts, and termination classifications within a profile may not. Human callback
error prose is diagnostic except the controlled fixture's injected messages.
No equivalence of wall-clock limits, actual RSS, or concurrent completion order is
asserted. Default limits outside an explicitly configured profile are native API
choices, not portable algorithm guarantees.

## Model contract

The conceptual transition is `State + Input -> (State, ordered Outputs, Disposition)`.
Models provide initial state, state checks, and optionally transition checks;
finite exploration additionally supplies an ordered input domain. Persistence
additionally supplies model identity and canonical state/input/output codecs.
Native programs need not serialize through JSON or use the table representation.

**MODEL-001.** Callbacks MUST be deterministic. Clocks, nondeterministic choices,
property history, pending work, and identities affecting future behavior belong
in state or explicit inputs. Real effects and external I/O are not executed by
this engine. Retained snapshots MUST not change as another transition evolves.
Equality MUST preserve future permitted inputs, transition observations, and
properties. Implementations MUST resolve hash collisions with equality rather
than equating hashes. Application hash/copy implementations must honor their
language contracts; callbacks are not sandboxed.

**MODEL-002.** Inputs, outputs, and checks are ordered sequences. Duplicate input
entries are separate explored edges. Duplicate check IDs remain ordered distinct
observations; consumers must not silently collapse them. In a deterministic
table, repeated `(state,input)` entries MUST have identical transition semantics.
The table's state storage order is not traversal order: only each state's edge
list determines input order.

**MODEL-003.** Disposition is `accepted`, `rejected(reason)`, or `ignored(reason)`.
Reasons are exact scalar strings. Rejection does not inherently force state
preservation or empty outputs; applications impose that through their properties.
All completed transitions, including rejected/ignored ones, receive normal checks.

## Checking and execution

**CHECK-001.** A check has a textual ID and `passed`, `failed(details)`, or
`skipped(reason)` status. A skip is not a pass and MUST remain visible. A failed
check is a property finding, distinct from a callback error. Passing checks carry
no details. Empty textual IDs/reasons are allowed, matching the existing model
contract; projects should use meaningful stable names.

**CHECK-002.** Initial observations consist of state checks. Transition
observations concatenate successor-state checks, then transition checks. Preserve
order, duplicates, and all failures in the completed check batch. Enumeration
additionally preserves each failure's phase: `initial_state`, `state`, or
`transition`.

**CHECK-003.** Observation policies may run state checks every positive Nth
one-based sequence number and may disable transition checks. An omitted state
batch becomes `skipped` with ID `stateless.state_checks` and reason
`periodic checking policy`. Disabled transition checks become `skipped` with ID
`stateless.transition_checks` and reason `disabled by policy`. Record/replay and
v1 BFS always use full checks; the policy applies to observation only.

**EXEC-001.** Recording and enumeration check the initial state before consuming
inputs or executing a transition. An initial failure has an empty witness and no
transition index. It is not attributed to a fabricated first input.

**OBSERVE-001.** Checking an already performed transition MUST NOT call the model
reducer or execute effects. The caller supplies before-state, input, observed
transition, and a positive one-based sequence. Initial checking is separate.

**ERROR-001.** A callback, codec, or invalid-adapter failure is an engine/model
error, never a passing run or a property failure. A checker error invalidates the
partial observation batch, even if an earlier callback produced a finding.
Enumeration errors produce no completed search report. Python uses `ModelError`;
Rust uses `Result::Err`. Process interruptions, interpreter exits, and crashes are
not normal completed engine reports.

## Bounded BFS algorithm

Configuration explicitly supplies positive `max_states` and nonnegative
`max_transitions`, `max_depth`. The adapter's finite domain is part of the model;
exhaustion makes no claim about omitted events or unmodeled behavior.

**BFS-001.** Use a FIFO frontier beginning with the checked initial state. Expand
inputs in the declared order and retain the first predecessor of each newly
admitted state. Failure witnesses follow those predecessors and append the
failing input. These rules define deterministic breadth-first tie-breaking.

**BFS-002.** `states` counts unique **admitted** states, initially one. `transitions`
counts every completed step including visits to known states. `max_depth_reached`
is the maximum source depth plus one among executed edges, initially zero.
`skipped_checks` counts initial and all executed-edge skips, including revisits.

**BFS-003.** Initial failures return `failure_found`, one admitted state, zero
transitions, and all ordered initial violations. Initial skips are counted even
with zero work budgets.

**BFS-004.** After each step, increment executed-edge statistics and fully check
the successor and transition **before** deduplicating state or applying the
state-admission cap. A finding returns `failure_found` without admitting its
successor. A failure on an already-visited state or at the state cap cannot vanish.

**BFS-005.** After checking, a duplicate successor is not enqueued. A novel passing
successor at `max_states` returns `state_limit`; its checked edge is counted, but
its state is not. Otherwise retain and enqueue the successor.

**BFS-006.** At source depth `max_depth`, probe at most one input and execute none.
Remember `depth_bound` if such an input exists; a terminal state at that boundary
does not cause a cutoff. After the frontier is exhausted, return `depth_bound` if
any depth probe found an unexamined input, otherwise `graph_exhausted`.

**BFS-007.** Below the depth boundary, probe the next input even at the transition
cap. Return `transition_limit` only when another input exists but cannot execute.
An exactly exhausted domain can return `graph_exhausted` at the cap. Findings from
an already executed checked edge take precedence over later admission limits.

**BFS-008.** Hash values are internal indexing aids. Exact equality resolves
collisions. A language may use a slower equality-only implementation and still
conform. The v1 table adapters deliberately hash every state identically to
exercise collision chains, not to prescribe production hashing performance.

## Recording

**RECORD-001.** Record an initial checkpoint and its checks, then each completed
transition's encoded input, disposition, ordered encoded outputs, encoded
post-state, and ordered checks. Stop after the first failing initial or transition
batch. Preserve the entire failing batch and completed failing transition.

**RECORD-002.** Record at most the requested step budget. If input ends exactly
at the budget, termination is `completed`; if another input remains, it is
`step_limit`. `completed` means the supplied sequence ended, not graph exhaustion
or application completion. The implementation may probe one extra input to make
this distinction. At a zero budget an empty sequence completes; a nonempty one
produces an empty bounded prefix.

**RECORD-003.** Errors before a coherent initial checkpoint raise/return an engine
error. A later step/codec/check error keeps only the earlier coherent prefix with
`model_error` termination and diagnostic text; do not invent observations for the
failed callback. Replaying that prefix alone does not reproduce its terminal
error. Native retention limits are not hard process-memory limits.

## Exact replay and codecs

**CODEC-001.** Codecs are canonical: decoding and re-encoding a checkpoint or input
MUST reproduce exactly its original bytes. Malformed, trailing, or noncanonical
application payloads MUST not be silently normalized for exact replay. The table
codec uses exact UTF-8 state IDs, inputs, and outputs; invalid UTF-8 or unknown
state IDs are errors. Envelope bytes are lowercase, even-length hexadecimal;
JSON object-key order is irrelevant, array order is not. Floating point, implicit
Unicode normalization, and language-specific native serialization are not used.

**REPLAY-001.** Replay compares every recorded observation, not just the final
state. `exact` verifies the stored prefix only. `steps_verified` counts fully
matching transitions, excluding a divergent attempted edge.

**REPLAY-002.** `failure_reproduced` means a recorded failing check ID failed again
at its corresponding observation. Details may differ, so this flag can be true
alongside `diverged`. It is not proof that all model behavior is correct.

**REPLAY-003.** Restore the saved initial checkpoint and check it; do not call
`initial_state()` to reconstruct history. Validate canonical initial and input
encodings before using them. For divergent initial checks, `step` is null; other
divergence indices are one-based.

**REPLAY-004.** Compare model name, model version, properties version, codec
version, and build identity before semantic replay. Mismatch is `incompatible`.
An explicit `allow_build_mismatch` may bypass **only** build equality, while
retaining `build_matches=false`. It never bypasses model/property/codec identity.
Shared table fixture identities permit cross-language replay; arbitrary native
application layouts or builds do not become compatible automatically.

**REPLAY-005.** Compare actual and stored disposition, then outputs, post-state,
and checks in that precedence. The first mismatch sets `field` to `disposition`,
`outputs`, `state`, or `checks`. Initial mismatch uses `initial checks`.

**REPLAY-006.** Reject continuation after an initial failure or any failing
nonfinal step. Reject disagreement between recorded checks and
`property_failed` termination. Identity incompatibility is reported before these
semantic replay validations; structural JSON validation still occurs on input.

### JSON envelope, not a replacement audit format

`trace-json-v1` is a portable **observation/replay envelope**. It includes format,
version, model metadata, checkpoint bytes/checks, steps, termination, and error.
It deliberately does not claim binary `.sttrace` interoperability, preserve a
Rust `RunConfig`/campaign audit history, or authenticate evidence. There is no
binary-to-JSON audit conversion API. Keep original binary audit artifacts for
Rust runs. Producers of durable evidence should supply real content/toolchain
build identities; fixture identity `fixture-v1` is intentionally test-only.

## Deterministic random primitive

**RNG-001.** State is an unsigned 64-bit seed. `next_u64` first adds
`0x9e3779b97f4a7c15` modulo 2^64, then applies:

```text
z = state
z = ((z XOR (z >> 30)) * 0xbf58476d1ce4e5b9) mod 2^64
z = ((z XOR (z >> 27)) * 0x94d049bb133111eb) mod 2^64
return z XOR (z >> 31)
```

Shifts are logical. This is SplitMix64, not a cryptographic generator.

**RNG-002.** `index(0)` returns no choice and consumes no random state. Otherwise
let `threshold = (2^64 mod upper)`. Draw until `value >= threshold`, then return
`value mod upper`. `index(1)` does consume a draw. All ports in this profile accept
0..2^64-1 bounds; the current Rust runner therefore requires a 64-bit target.
Algorithm identity alone does not specify a full fuzzing algorithm or its random
consumption; fuzzing is outside this initial profile.

## Wire protocol and qualification scope

**PROTOCOL-001.** A runner is a finite-process JSON Lines interface: one request
per UTF-8 line and one JSON response per line, no banners on stdout. Diagnostics
go to stderr. `hello` advertises implementation, package/spec/protocol versions,
and supported profiles. An unsupported version/operation produces
`unsupported_version` / `unsupported_operation`. Every malformed line has its own
error response, and the next line must still be processable.

**PROTOCOL-002.** Known requests/objects reject missing and unknown fields,
incorrect types, duplicate keys, non-scalar strings, malformed UTF-8, non-finite
or floating numbers, invalid tags, and conflicting table transitions. Booleans
are not integers. Full-width seeds/bounds are canonical unsigned decimal strings,
not JSON numbers. Output comparison ignores only JSON object-key ordering;
`true`, `1`, and `1.0` are not interchangeable. Ordinary integer fields have their
specified bounds; version fields for model metadata are u32.

Protocol request maximum is 4 MiB including its newline, nesting depth 64, up to
1,000 table states and 10,000 edges total. Individual check/output lists have up
to 4,096 entries; configured work and explicit input batches have maximum 100,000.
Random raw draws and bound lists have maximum 10,000 entries. Unknown state
references and inconsistent repeated inputs are invalid. Zero max_states,
sequence, or check period produce `invalid_config`; other schema/range violations
produce `invalid_request`. Callback failures produce `model_error`.

The shared profile qualifies bounded small-model semantics. Implementations may
have additional native allocation/trace limits. Do not use huge-output fixtures
to infer identical Rust binary-framing and Python JSON retention budgets. Test
runner byte/work caps are defensive guardrails, not an untrusted execution
service. The orchestrator has a finite process timeout and verifies response
counts; crashes, timeouts, malformed outputs, missing profiles, and empty corpora
are failures, never successful partial qualification.

`request.schema.json` describes positive request shapes, and its `$defs/trace`
describes the observation envelope. Runtime validation adds duplicate-key,
UTF-8, cross-reference, aggregate-count, and deterministic-model constraints that
JSON Schema alone cannot establish. Negative corpus requests intentionally may
violate that schema. `corpus.schema.json` validates the test container instead.

## Extending and maintaining the contract

Every change to semantics needs a specification/rule decision, reviewed examples,
and implementations or explicit unsupported profiles. A new regression test may
correctly make an old implementation fail without changing the semantic version.
An intentional incompatible behavior change needs a new profile/specification
version. Corpus revisions are content-fingerprinted independently of package
versions. Never update a golden result solely because the Rust output changed.

To add a language: implement its native callbacks and engine, provide the JSONL
adapter calling that engine, advertise profiles, and pass `conformance/run.py`.
Run native ownership/error tests too: table agreement cannot establish correct
copying, language integration, or arbitrary user codecs. Qualification reports
must state the actual source revision/toolchains/platforms and corpus digest.

The shared corpus and generated tiny graphs are evidence, not a proof over every
application or every possible implementation. The eight deliberate Python engine
mutations verify that representative faults are detected, not complete mutation
coverage. Future profiles should separately specify shrinking, fuzzing, oracles,
composition/scheduling, and binary audit interoperability rather than weakening
this contract to hide missing behavior.
