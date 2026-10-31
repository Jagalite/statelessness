# N07 retained-suffix final source review

Review date: 2026-10-22. Worktree:
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`; base/HEAD:
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.

This document identifies uncommitted sources inspected, not a frozen integrated
revision or an execution receipt. This reviewer owns only this new document.
No implementation or fixture was edited; no build, test, compiler, executable
check, implementation, or Kyoto job was run or submitted. Nothing was staged,
committed, pushed, or opened as a PR. Unrelated edits were preserved. Ancestor
and in-tree searches found no applicable `AGENTS.md`. The requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read; the explicit
source-only restriction governs this pass.

## Findings requiring disposition before qualification

**Final source verdict: three P1 provenance blockers remain.** The suffix
consumer changes and literal S2-S4 coverage have no actionable findings in this
review. The specific staged Go/Swift membership defect from S1 is addressed,
but the integrated qualification path still has the following source-to-code
identity gaps. These are concrete source-derived failure scenarios, not
executed exploit demonstrations or failing runtime receipts.

### F1 — P1: Python test runners import from an unsealed directory

`scripts/qualify-native-observation.py:179`, `:197`, `:199`, `:212`, `:223`,
and `:233` create and individually register `validator-suite.py` and
`python-suite.py` directly under `attempt.work`, then invoke them with `python
-B`. Both immediately import `unittest`. Python puts the script directory on
the import path; the Python-suite `PYTHONPATH` overlay does not remove that
entry. `stage_native_sources:162` registers/watches copied child trees, and
`Attempt.staged_snapshot:431` enumerates only those registered directory roots.
Nothing protects top-level workspace membership before these invocations.

An added `attempt.work/unittest.py` can therefore be imported in place of the
standard library while every registered source and runner hash still matches.
Its fake loader/runner can emit positive test counts and the expected module
names without running the sealed tests. The validator result checks at
`:202` constrain reported names/counts, not the imported testing implementation.
The native stage has no later whole-workspace watch that rejects this added
import. This breaks the intended sealed-source execution evidence.

**Fix:** isolate generated runners from unregistered import locations and
enforce membership for the runner directory before/after execution. Use a
safe/isolated Python import bootstrap that explicitly adds only the sealed
test/source directories needed by discovery; do not rely on `-B` or
`PYTHONPATH` alone. Add source-authored controls for top-level import shadowing
and reject them before interpreting a passing summary. No control was run here.

### F2 — P1: installed TypeScript code is first watched after being consumed

`scripts/check-native-observation-consumers.py:759` installs the packaged
library into `typescript-consumer/node_modules`. Its declarations are consumed
by `typescript-strict-declarations:766` and its JavaScript by the browser import
graph check at `:787`; the first installed-package watch is only at `:813`.
`staged_build_output:593` excludes `node_modules` from membership checks. The
watched tarball and separately watched compiler dependencies do not cover these
installed package files.

A persistent change to an installed `.d.ts` or `.js` file after installation
but before either check is therefore consumed without a prior expected identity.
The later watch accepts the changed tree as its baseline, and the suffix helper
reuses it. A successful receipt cannot then establish that the checked/runtime
package corresponds to the sealed build artifact.

**Fix:** register/watch the installed package immediately after installation,
before the first declaration or import-graph consumer, and maintain that identity
through later suffix execution. Add a control mutating an installed input in
that interval and require rejection. This is a remaining timing gap analogous
to prior S1/R2, not a claim that the narrower added-Go/Swift-source fix failed.

### F3 — P1: unsealed Python bytecode can replace the hashed helper source

`scripts/check-native-observation-consumers.py:54` loads helpers with
`spec_from_file_location` and cache-aware `exec_module`. The same mechanism
bootstraps the driver at `scripts/qualify-native-observation.py:24` and the
reverse entry point at `check-native-observation-consumers.py:846`.
`ignored_build_output:160` explicitly omits ignored `__pycache__/*.pyc` from
the source snapshot, and `.gitignore:11` ignores that directory. Thus the source
hash used as evidence need not identify the code actually loaded.

A valid old timestamp/size cache, or an unchecked-hash cache, can supply different
authority, suffix or compile-proof helper code while the `.py` files and all
recorded source identities remain unchanged. `-B` and
`PYTHONDONTWRITEBYTECODE=1` prevent cache writes, not reads. This mechanism was
confirmed by reading local CPython's `SourceLoader.get_code`: it may return
cached code before reaching the `sys.dont_write_bytecode` write guard
(`/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/_bootstrap_external.py:1093`,
`:1155`, `:1162`). No Python loader or cache was executed or modified.

**Fix:** load qualification drivers/helpers directly from verified source bytes,
or enforce a cache-free import policy before any such module is loaded. Include
the initial driver bootstrap and dynamically loaded helpers, not just the
suffix invocation. Add controls with otherwise valid stale/unchecked cache
contents and require source execution or rejection. A late cache inventory
after importing cannot establish which code already ran.

## Prior findings and current source disposition

The prior [S1-S4 review](OBSERVATION-REVIEW-SUFFIX.md),
[suffix fixes](OBSERVATION-FIXES-SUFFIX.md),
[suffix corpus derivation](OBSERVATION-FIXES-SUFFIX-CORPUS.md), and
[general corpus fixes](OBSERVATION-FIXES-CORPUS.md) were read against actual
sources. The consumer subreview completed with no actionable findings or
obvious public API/signature mismatch. The authority/provenance subreview found
F1-F3 above; this reviewer independently reread the cited paths and the local
Python loader source. Delegation preserved the live parent selection:
`codex`, `gpt-6-astra`, `reasoningEffort=ultra`, `serviceTier=default`.

**S1, staged input bytes and membership:** the helper registers generated Go
and TypeScript build configurations before their first consuming command
(`scripts/observation_suffix.py:158`, `:180`). `stage` registers copied bytes
and source directory roots; `staged_snapshot` now detects unexpected sibling
files as well as changed/missing registered files. `Attempt.run` checks these
before a command, after it, and on command failure
(`scripts/check-native-observation-consumers.py:409`, `:431`, `:467`, `:600`).
Thus the prior added `.go`/`.swift` source scenario no longer becomes a silently
accepted post-compilation baseline. Generated input registration rejects changed
bytes at an already registered path and moving an existing label to another
path. Layout-specific output exclusions are explicit at `:585`; the special
`suffix-build` exclusion is limited to the installed observation TypeScript
consumer. Source-authored mutation/registration controls exist in
`scripts/test_observation_receipts.py:258`; none was executed here.

**S2, complete JSON replay observations:** both pair matrices call `_session`
and compare the full rich and bounded session with the independent literal
`session`, including typed initial/turn values, expected/actual bytes, ordered
checks, state-check count, differences, intermediate reports and final report
(`scripts/observation_suffix.py:35`, `:284`). They now invoke `replay-suffix`
and `replay-suffix-json`; the prior report-only `replay-json` mode remains an
additive legacy path, not the JSON suffix proof.

**S3, independent replay work counts:** every suffix session uses separate
model instances for rich and bounded work and returns `rich_counts` and
`bounded_counts`. The helper compares each with `replay_counts` using canonical
JSON, preserving numeric versus Boolean kinds. The literal counts are
**0/1/2/1** in initial-state/reducer/state-check/transition-check order. Replay
decodes the retained checkpoint, checks it once, executes one retained input,
and checks that turn's state and transition once
(`src/execution/replay_kernel.rs:275`, `:291`, `:375`, `:392`). Producer-local
replays have separate models and are not counted as fresh-process pairs.

**S4, first failure after eviction:** the interrupted case remains intact and
the helper separately runs `record-failing-suffix`. It compares the full failure
session, failure artifact, actual primary observer text, separate diagnostic
text, and strict Boolean `freeze_verified: true`
(`scripts/observation_suffix.py:250`). All five producers set that field only
after comparing frozen binary/portable artifacts and tracked callback counts
around a supplied diagnostic and rejected reuse of the authentic final token.
No fourth reducer/check operation manufactures the rejection. The diagnostic
is sequence 44, state 999, zero checks: it cannot certify continuity or alter
the retained checkpoint. TypeScript saves primary text before the diagnostic
and slices the added writer bytes into the separate field; the other consumers
use a separate observer/writer. A separate field does not require a new observer
instance. See Python `consumer.py:195`, Go `main.go:401`, TypeScript
`consumer.ts:138`, Swift `main.swift:159`, and Rust `checked_reference.rs:1267`.

## Public APIs and authority boundary

Native rich JSON replay accepts portable `Trace` directly. Native bounded replay
requires `AuditTrace`; the applications use public conversion with default
manual configuration, absent seed and empty parameters: Python
`AuditTrace.from_portable`, Go `AuditFromPortable`, TypeScript
`auditTraceFromPortable`, and Swift `AuditTrace(portable:config:)`. These defaults
are replay inputs, not recovered provenance. Rust's two replay APIs accept
public `Trace`, constructed by the existing portable parser with
`RunConfig::default()`. All JSON suffix receipts omit `retained_config`, and
the helper explicitly rejects its presence. Binary receipts retain the complete
actual producer configuration and each consumer must return it unchanged.

The actual additions are in Python `consumer.py:224`, Go `main.go:438`,
TypeScript `consumer.ts:76`, Swift `main.swift:75`, and Rust
`checked_reference.rs:995`, `:1350`. Their observations come from public replay
callbacks, not substituted fixtures. Each application performs its own reducer
and checks, supplies the existing checked token to Recorder, and supplies only
already available values/checks to logging. `NoPayloads` formats unavailable
markers without invoking model codecs. No inspected diagnostic path runs a
reducer, check or effect, or supplies an event/view as checked Recorder authority.

The historical [checked-results audit](CHECKED-RESULTS-AUDIT.md) describes an
older snapshot. Existing native checked tokens, periodic checking and bounded
Recorders already exist and remain in use. Rust's `observe_checked` still
requires full checking and the next absolute host sequence, then verifies
metadata and encoded before-state continuity (`src/monitor.rs:308`, `:398`).
Events explicitly provide no continuity or checked authority
(`src/observation.rs:100`, `:179`).

`scripts/observation_authority.py:21` defines six external-package compiler
controls: one checked-token acceptance and two diagnostic/event rejections
for each of Go and Swift. Positive controls must compile successfully. Negative
controls require a nonzero exit and diagnostics naming both the actual
`Event`/`TurnObservation` type and `CheckedTurn`, with a type-mismatch marker
(`:64`). The source tree is watched and its generated files registered before
compilation (`:48`); the Swift module directory is watched at `:19`. Intended
counts are **2 positive compilations and 4 expected rejections**, with
`runtime_executed: false`. These are public type-boundary controls, not runtime
token-forgery tests or evidence of executing the generated functions. Their
actual compiler acceptance/rejection remains unqualified.

## Independent literal verification

The literals were inspected without invoking any implementation or validator.
`Threshold` supplies state 0 and input 1, increments the state and emits
`changed`; append checks are `threshold.bound`, `state.appended`, then
`edge.checked`. Only `threshold.bound` fails at state >= 3, with details
`at least three` (`validation/corpus/src/checked_reference.rs:868`). These
model rules are also present in the base, independent of suffix receipt code.

Both captures attach at origin 40 with capacity 1. The first token has sequence
41, two state checks and full coverage. Interrupted capture runs two turns and
has counts **0/2/3/2**; failure capture runs three and has **0/3/4/3**. Eviction
promotes only the evicted turn's state-check prefix (`src/monitor.rs:368`), so
the retained checkpoints are states 1 and 2, with two passing checks and no
transition check. Retained turns are respectively state 1 -> 2 passing and
state 2 -> 3 failing. The failure turn is retained before Recorder freezes.

The configuration has two engine stamps followed by seven ordered parameters.
Observed/evicted/checkpoint counters are `2/1/1` or `3/2/2` respectively;
`checkpoint_after_sequence` is local, not absolute (`src/monitor.rs:600`).
Together with origin 40, these locate absolute checkpoints 41 and 42. Replay
reports local sequence 0 for the checkpoint and 1 for its sole turn. Each
initial report has zero verified steps and no reproduced failure; the turn and
final reports have one verified step. `failure_reproduced` becomes true only
for the failing case. Exact session comparison fixes the failed property's
identity, details and batch order, beyond the weaker same-ID failure predicate
alone (`src/execution.rs:771`). Every difference flag is false and application
build metadata is `application-v1`.

Decimal ASCII state bytes 1/2/3 are `31`/`32`/`33`, input 1 is `31`, and output
`changed` is `6368616e676564`. Portable metadata names
`native-checked-threshold`, with model/properties/codec versions all 1. Portable
JSON intentionally has no audit configuration. The helper compares the complete
portable artifact structurally, preserving array order and scalar kinds.

The binary framing reader independently checks header
`53544c4553531a0a01000000`, frame lengths and CRC over header plus payload,
tag order `[1,2,3]`, single local step index `[0]`, and exact footer payload.
The one-step footer is `010000000000000003` for Interrupted and
`010000000000000001` for PropertyFailed (`src/trace.rs:278`, `:304`, `:826`).
Thus an otherwise exact replay cannot hide a wrong terminal encoding. This is
finite framing validation, not a second complete model/config/check decoder.

TextObserver gives checkpoints Trace severity, passing turns Debug, and the
failed turn Error. Configured Trace still includes every ordered check on the
Error line. EveryTransition with NoPayloads emits the literal unavailable
payload markers (`src/observation.rs:231`, `:376`, `:410`, `:461`). Primary text
retains `suffix-observer\n`; every record includes its final LF. No recorder
eviction implies a separate promotion event. The separate frozen diagnostic is
exactly `Trace stream="suffix" event=checkpoint sequence=44 checks=0 state=unavailable\n`.

Supporting tests were read, not executed: observation filtering/severity and
explicit checkpoints (`tests/observation.rs:103`, `:129`, `:172`), later
diagnostics after Recorder failure (`:308`), retained checkpoint replay and
first-failure freeze (`tests/monitor.rs:125`, `:162`), variable checkpoint phase
counts and failure after repeated eviction (`tests/monitor_review.rs:114`,
`:189`). The pinned Rust files remain unchanged from the base.

## Process counts, provenance and qualification limits

The source loops actually plan **10 producer processes, 50 ordered binary
consumer processes and 50 ordered JSON consumer processes**: two distinct
cases, each with five producers and 5 x 5 pairs per format, including fresh
same-language processes (`scripts/observation_suffix.py:245`, `:286`). Each
pair process performs both rich and bounded replay internally. Producer-local
replay is additional; it does not increase the fresh-process pair count.

Rows are inserted before execution, carry completed/matched flags, and only
successful completion of every comparison publishes executed totals. Failure
retains partial rows and raw Attempt command logs and propagates to the parent;
later cases remain pending. Runtime input identities are checked before and
after each consumer invocation, including failure, and once more before the
aggregate passes (`scripts/observation_suffix.py:42`, `:226`, `:309`). The
installed driver invokes the suffix helper before sealing its final runtime
tree (`scripts/check-native-observation-consumers.py:820`).

Binary configuration equality preserves actual engine stamps but does not
independently authenticate them: `_config` accepts any nonempty engine version
and any nonempty Rust build stamp, with fixed native build-label checks.
`build_matches: true` concerns application metadata. Source/tool/package and
compile receipts must provide provenance; these strings alone do not. This
limitation from the prior review remains explicit and is not a claim of
authenticated engine stamps.

All compile controls, validator controls, native/Rust suites and installed
matrices remain for the parent's single frozen-source Kyoto qualification.
Do not credit compile-only success as runtime. The additional failure case
closes the prior missing first-failure *source coverage*, but the finite
capacity-one scenarios do not establish long-session, zero/small capacity,
negative resource-limit, variable-check, callback, codec or sink-failure gates
required by [N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording).
They certify no evicted-prefix execution. The previously suggested malformed
suffix framing mutation controls are not part of this helper's current planned
counts; no such negative execution is implied. Parent owns the integrated
source freeze, exact staged commits and final qualification decision.

## Inspected identities

SHA-256 metadata readings, not validator or runtime results. The initial
qualifier identity is retained below and explicitly superseded afterward;
the other listed task inputs were unchanged on final reread.

```text
d69007d2b44dbc1c5d283a8eb59dc2bd5c5c39f07fc87af779afe46cb8c1873d  scripts/observation_suffix.py
096ffeb57d8c6bb5cc71553e963d33bf33c6fc6d686b24bc93e8cd9a4d7ea8f8  scripts/observation_authority.py
dfa1d2217c770750933dcee1c42c3ff8426e438b31fab3af81b0d87170d9b061  scripts/check-native-observation-consumers.py
c7ae91c400da292f03e2826e1632f57fd23c3953bf484fe6efd8a2323f947c1e  scripts/qualify-native-observation.py
3b78d0f6226f0dd54dfdb36713a3c78d7fc0ece17aa67b29f38897f2bf1579a9  scripts/observation_compile_proof.py
3c3bcd168a162754ddb1536551551716320ade95fec2d2cd188cc4783c9849c4  examples/native-observation/suffix-contract.json
10124e42e1e377a3b4e907e3800b14c6c4f711cf2595805c9a56d72978e9d865  examples/native-observation/suffix-failure-contract.json
09d64fff242ef1a6430f26dd9a1f7b7944d06449a605986be872f0a947115a5f  examples/native-checked/python/consumer.py
7990e2fc69e4c38d363eecce2ad300d68967f9b3e4c40a8657844eec5a8d85cd  examples/native-checked/go/main.go
9ed3ead9272b0e0bf4eacdf3888811ce2e1fc2d2760d83c76ff8f52ebb4501ab  examples/native-checked/typescript/consumer.ts
594ca9a3f0a97c43ccf54310cf2d314c3e30656d7f9e3968cfec6ddf49dd6a7d  examples/native-checked/swift/Sources/CheckedConsumer/main.swift
28dedc791694586169c7d8a0975fbfd285511d10a04707a534994d9061314f5c  validation/corpus/src/bin/native-checked.rs
37534c25506a3078c0f403b76679ac52e7e5cbf94f882ddcfb45182b4f11fdf7  validation/corpus/src/checked_reference.rs
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0  src/execution.rs
a7a4c8242408531e10453b0314c11b26c8c469ce2c8eca56e81c2e748d783cd1  src/execution/replay_kernel.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
```

The qualifier changed concurrently after the initial hash above. The final
inspected `compile_rust` source still compiles both public consumers, records
their complete Cargo artifact rows and marks compilation non-runtime. Current
identity (superseding only the initial qualifier hash):

```text
80e86133843bb9ff34380fc25d60f993a3f6ed08d6cf56494e533f69b37e8fc4  scripts/qualify-native-observation.py
```

The source-only Python loader reference used for F3 has SHA-256:

```text
bf2a271ee85fc78a28c08e919894785c04b7da3f6a6dca9534470144f6b34a4e  /Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/importlib/_bootstrap_external.py
```

Resolve F1-F3 and review the resulting source identities before freezing and
qualifying the integrated candidate. No whole-N07 completion is claimed.
