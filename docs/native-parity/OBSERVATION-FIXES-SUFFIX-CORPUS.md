# Independently authored suffix corpus refinements

Source-only work on 2026-10-22 in the shared isolated worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This task owns only
[`suffix-contract.json`](../../examples/native-observation/suffix-contract.json),
[`suffix-failure-contract.json`](../../examples/native-observation/suffix-failure-contract.json),
and this note. All expected values were written from source inspection; no
implementation output was used to generate or refresh fixtures. No build, test,
compile, executable check, validator, or Kyoto job was run or submitted. Nothing
was staged or committed. No applicable ancestor or in-tree `AGENTS.md` was found;
the requested `rust-builds-kyoto/SKILL.md` was read. The parent owns the integrated
freeze and subsequent shared Kyoto qualification.

The pinned [`Threshold`](../../validation/corpus/src/checked_reference.rs) supplies
the model semantics: supplied checkpoint `0`, input `1`, accepted state increment
and output `changed`; `check_state_into` appends `threshold.bound` followed by
`state.appended`, and `check_transition_into` appends `edge.checked`. Only the
threshold check fails, at state >= 3, with details `at least three`. Metadata is
`native-checked-threshold`, model/properties/codec version `1`, build
`application-v1`. Its preferred codecs write decimal ASCII state/input and UTF-8
output, giving state 1/2/3 bytes `31`/`32`/`33`, input `31`, and output
`6368616e676564`. These declarations were also inspected directly in the base
commit, independently of the concurrently edited consumer helper. The relevant
Rust observation, monitor, replay-kernel, trace and test sources were unchanged
from that base during this inspection.

The interrupted contract is preserved in full, with only top-level
`replay_counts` added. Its two application turns retain checkpoint state `1` and
turn `1 -> 2`; termination remains `interrupted`, footer payload remains
`010000000000000003`, and every original session, configuration and observer
literal remains unchanged. Capture counts stay `0/2/3/2` in the order
initial-state/reducer/state-check/transition-check. Each separate replay has
counts `0/1/2/1`.

The separate failure contract uses the same `statelessness.observation-suffix.v1`
shape with the common `replay_counts` field and the agreed failure-only
`freeze_observer_text` field. `record-failing-suffix` starts at supplied state
`0`, attaches at origin `40`, retains at most one turn, and executes exactly
three application turns with host sequences `41`, `42`, `43`. Capture counts are
`0/3/4/3`: one initial state check, then one reducer, state check and transition
check per turn. [`check_initial` and `check_turn`](../../src/execution.rs#L28)
do not execute the reducer; full default coverage includes both check phases.
The `checked` object still describes the first token at sequence `"41"`, with
two initial checks, state-check count `2`, and `full_checking: true`.

[`Recorder::with_checked_initial_at`](../../src/monitor.rs#L145) reuses the
initial token's checks. [`observe_checked`](../../src/monitor.rs#L308) requires
the next absolute sequence and full checking, validates continuity, and reuses
the complete token checks. After the third turn, local observed/evicted counts
are `3`/`2`. [`commit`](../../src/monitor.rs#L354) promotes the evicted turn's
post-state and only its state-check prefix, so the retained checkpoint is state
`2` with exactly two passing checks; `edge.checked` is absent there. The retained
turn is application turn 3, state `2 -> 3`, with ordered checks
`threshold.bound` failed (`at least three`), `state.appended` passed, and
`edge.checked` passed. The failing turn is retained before recording freezes with
`PropertyFailed`; the portable spelling is `property_failed`, with empty error.

Both configurations retain strategy `manual`, null seed, and seven ordered
parameters after the two engine stamps: sequence origin `40`, maximum steps `1`,
maximum retained bytes `8192`, checking `full`, then observed/evicted/checkpoint
counters. The failing values are `3`, `2`, `2`; the interrupted values remain
`2`, `1`, `1`. [`update_counts`](../../src/monitor.rs#L600) uses the local eviction
count for `checkpoint_after_sequence`, so the failing checkpoint's absolute
identity is `40 + 2 = 42`. The first two keys are `stateless.engine.version` and
`stateless.engine.build`, from [`stamp_config`](../../src/execution.rs#L811).
Their values are producer/build dependent and are deliberately outside the
shared literal parameter tail; complete binary replay configuration must still
match the actual producer configuration. Portable JSON does not encode audit
configuration and must not acquire a fabricated origin or engine stamp.

[`src/trace.rs`](../../src/trace.rs#L14) defines the twelve-byte header
`53544c4553531a0a01000000` and RUN/STEP/END tags `[1,2,3]`.
[`encode_step`](../../src/trace.rs#L278) writes zero-based retained indices, so
the single retained application turn has binary index `0`.
[`encode_end`](../../src/trace.rs#L304) writes little-endian u64 retained count
`1` followed by termination tag `1` for PropertyFailed: the exact failing footer
payload is `010000000000000001`. Framing checks must include frame lengths and
CRC, tag order, local index, terminal count and tag. A replay outcome alone does
not establish exact terminal encoding. The contract intentionally does not
freeze a producer-dependent complete binary blob.

Replay starts from the decoded retained checkpoint, never `initial_state`.
[`replay_kernel`](../../src/execution/replay_kernel.rs#L275) checks that checkpoint
once, executes the one retained input once, then checks state and transition
once. Thus each rich and bounded replay, for either binary or portable JSON,
must return `rich_counts` and `bounded_counts` individually equal to the shared
`replay_counts` object `{initial_state:0,reducer:1,state:2,transition:1}`. Both
complete sessions compare against the literal `session` object. Additive modes
`replay-suffix` and `replay-suffix-json` supply these observations and counts;
older report-only JSON replay cannot satisfy this comparison. Producer-local
replay counts are separate from capture and from every fresh consumer process.

For the failure case, the initial observation has local sequence `0`, typed
state `2`, matching expected/actual bytes `32`, two passing checks, four false
differences, and report `exact`, zero verified steps, no divergence,
`failure_reproduced: false`, `build_matches: true`. The turn has local sequence
`1`, before `2`, input `1`, accepted state `3`, output `changed`, all three
ordered checks, and state-check count `2`. Expected/actual encodings and check
batches agree. The turn and final reports are `exact`, one verified step, no
divergence, `failure_reproduced: true`, and matching build. The kernel updates
the report before delivering each observation
([initial](../../src/execution/replay_kernel.rs#L317),
[turn](../../src/execution/replay_kernel.rs#L436)).
[`same_failure`](../../src/execution.rs#L771) matches a failed property ID;
the full batch equality and exact report additionally fix the details and order.
[`validate_recording`](../../src/execution.rs#L781) rejects continuation after
failure and termination inconsistent with checks. This fixture identifies the
first failure in the retained trace; it does not certify the evicted prefix.

Observation literals follow [`TextObserver`](../../src/observation.rs#L357)
with stream `suffix`, configured Trace, EveryTransition snapshots, NoPayloads,
and a per-record bound of `1024` including LF. The primary writer starts with
`suffix-observer\n`, followed by checkpoint `40` at Trace and passing turns
`41`/`42` at Debug. The failing turn `43` is Error severity. Configured Trace
still includes every passed/failed check and input/output/post-state markers on
that Error line; failure prints exactly `Failed("at least three")`. Checkpoint
state and turn payloads are literal `unavailable` because
[`NoPayloads`](../../src/observation.rs#L231) never invokes model codecs.
Every primary record ends with LF; no Started, Finished or checkpoint-promotion
record is implied by recorder eviction.

The agreed failure-only `freeze_observer_text` is exactly
`Trace stream="suffix" event=checkpoint sequence=44 checks=0 state=unavailable\n`.
It is a separately captured diagnostic checkpoint supplied with sequence `44`,
state `999`, and an empty check batch after the failure. It does not extend
primary `observer_text`. The producer must compare actual frozen artifact bytes
and callback counts before and after this logging and a repeated handoff of the
authentic final token; the frozen recorder rejects that handoff without another
reducer/check invocation. Trace logging of supplied state `999` must not perform
state validation, run the reducer, mint a checked token, or record a fourth
turn. Its zero checks and unavailable state are intentional. The literal text
alone is insufficient proof of unchanged bytes/counts; the producer must assert
those equalities and the helper must compare the separate text field.

Inspected supporting tests include observation filtering and severity in
[`tests/observation.rs`](../../tests/observation.rs#L103), shared checks and
post-freeze logging in [the same suite](../../tests/observation.rs#L308),
[eviction and failure replay](../../tests/monitor.rs#L125), and
[failure after repeated eviction](../../tests/monitor_review.rs#L189). They were
read, not executed. The historical
[`CHECKED-RESULTS-AUDIT.md`](CHECKED-RESULTS-AUDIT.md) describes an older snapshot:
current Python `checked.py`/`monitor.py`, Go `checked_results.go`/`recorder.go`,
TypeScript `checked.ts`/`runtime_recorder.ts`, and Swift
`CheckedResults.swift`/`Recorder.swift` already provide checked tokens and
bounded recorders. These fixtures reuse that architecture and require no
replacement of tokens, periodic checking or Recorder.

Qualification remains pending. The helper and consumer changes are concurrent,
separately owned work; authoring these literals does not establish that any mode
or comparison executed. The parent must freeze the integrated sources, preserve
failed attempts, run the shared suites and installed fresh-process matrices
through Kyoto, and distinguish compile-only results from executed tests. This
finite capacity-one corpus does not close all
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording)
long-session, capacity, callback, codec, sink or resource gates. Events provide
diagnostics only and confer neither continuity nor checked authority.
