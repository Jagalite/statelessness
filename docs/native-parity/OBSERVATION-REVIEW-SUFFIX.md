# N07 retained-suffix source review, round 2

Reviewed on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, HEAD/base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. The hashes below identify the
uncommitted source reviewed. This is source inspection, not a frozen integrated
revision, compilation result, execution receipt, or whole-N07 approval.

Only this new document was written by this reviewer. No implementation or fixture
was changed; no build, test, compile, implementation execution, Kyoto job,
staging, commit, push, or PR was performed. Ancestor and in-tree searches found
no applicable `AGENTS.md`. The required
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read; the user's
stronger source-only restriction applies. An additional read-only reviewer
completed inspection of the four native applications and their actual public APIs.

## Findings requiring disposition

### S1 — P1: staged build inputs are not fully sealed before compilation

The original snapshot had the timing gap below. A late parent revision fixes
modification of registered source files, but leaves added source membership
unchecked; see the disposition at the end of this finding.

`scripts/observation_suffix.py:130-147,151-162` copies the Go, TypeScript, and Swift
applications and invokes their compilers. Their first persistent input watches
are at lines 183-187, after compilation. Generated `go.mod` and
`suffix-tsconfig.json` are also consumed before being watched. Go/Swift package
trees are first watched by this helper at lines 188-192.

`stage` verifies each copy immediately (`scripts/check-native-observation-consumers.py:500-515`),
but `Attempt.run:368-369,447-450` checks the checkout, not those external copies.
Consequently a persistent change to a staged consumer/configuration between copy
and compilation can compile successfully and become the accepted post-build
baseline. The resulting receipt cannot establish that the compiled application
consumed the sealed source. This extends the earlier proof review's R2 to the
new suffix applications; watching only the final runtime tree does not close it.

**Fix:** before the first command consumes them, seal the immutable staged source
files and generated build configurations, with their mapping to frozen input
identities. Compare them after the consuming command and on failure. Keep
generated compiler output/cache directories outside that immutable subset. Do
the same for the reused package inputs before their initial build, as required
by R2. Add a control that changes a staged application/configuration after copy
but before compilation and requires a failed receipt. Do not generate new goldens.

**Late source disposition:** driver hash
`13b8c93fc7bea4a6f27dcf3f9abee9606e49bd45cd88078f57eb0c28cbf8f204`
adds source registration in `stage:549-569`, generated-file registration before
commands at `run:395-406`, and before/after/failure checks of registered files.
This addresses the original existing-file modification scenario in source.
However, `staged_snapshot:379-388` iterates only previously registered paths;
it does not detect new source paths. Adding a new `.go` file to
`suffix-go-consumer` after staging but before `go build .` leaves all registered
identities unchanged. Go consumes that file, and the helper's first full
application-tree watch at line 183 accepts it after compilation. The same
membership issue applies to discovered Swift sources. This is an unclosed
source-provenance scenario, not a runtime-observed failure. Seal immutable source
directory membership as well as existing bytes, allowing only explicitly named
generated outputs, and add a new-source-file mutation control. The late driver
and compile-admission changes have not received a complete integrated review.

### S2 — P2: JSON pair success does not validate complete replay observations

`scripts/observation_suffix.py:250-257` checks only the final six report fields
for JSON pairs. The five applications invoke plain replay: Python
`consumer.py:194`, Go `main.go:406`, TypeScript `consumer.ts:123`, Swift
`main.swift:149`, and Rust `checked_reference.rs:1284`. A regression in callback
delivery, contents, phase boundaries, or intermediate reports is invisible on
this path, despite the full independent session already present in the contract.

**Fix for the requested full-observation coverage:** add an observation-bearing
JSON replay path and compare its full session to that literal contract. Portable
JSON still has no audit configuration; do not invent one. Until then describe
these 25 pairs as report-only portable replay, separately from the 25 binary
pairs' complete rich/bounded observations.

### S3 — P2: fresh replay work counts are not checked

`scripts/observation_suffix.py:32-35` compares sessions and codec-use indicators;
`main_counts` is checked only for producers at line 217. Consumer replay receipts
discard reducer/check counts (`checked_reference.rs:986-990,1280-1289` and the
native session wrappers). An extra reducer or checker invocation returning the
same values can leave every compared observation/report unchanged. This gate
therefore establishes check-once counts for capture, not for fresh replay.

**Fix:** return and compare counts separately for each rich, bounded, and JSON
replay invocation. For this literal one-turn suffix the expected
initial-state/reducer/state-check/transition-check counts are **0/1/2/1**:
replay checks the decoded checkpoint, executes one input, and checks that turn.
This follows `src/execution/replay_kernel.rs:291-300,375-377,392-402`.
Use separate model instances or count deltas so producer and replay work cannot
hide one another. Keep these counts distinct from producer counts **0/2/3/2**.

### S4 — P2: the prior first-failure suffix requirement remains open

`docs/native-parity/OBSERVATION-REVIEW-PROOF.md:229-233` required independently
expected first-failure identity on the fresh suffix path. The new contract
requires `failure_reproduced: false` (`suffix-contract.json:35`) and the producers
stop at state 2, before Threshold fails at state 3. This is a valid interrupted
suffix case but cannot discharge that earlier requirement.

**Disposition:** add a separate failing retained suffix with source-authored
failure identity, checkpoint phase, and preserved terminal checks, or explicitly
retain that part of the N07 gap. Preserve the interrupted case and its footer
proof; do not substitute a newly generated expected result for either case.

## Source conclusions

The suffix producers are additive branches of the existing checked applications.
They call the real checked-initial/checked-turn and bounded Recorder APIs rather
than fabricating checked objects or replacing the existing implementations.
The historical `CHECKED-RESULTS-AUDIT.md` statements that native tokens/Recorder
were missing do not describe this base. Existing periodic checking and Recorder
coverage/continuity checks remain in force.

The Rust branch at `validation/corpus/src/checked_reference.rs:1192-1267` uses
the public `Recorder::with_checked_initial_at`, `observe_checked`,
`CheckedTurn::observation`, `TextObserver`, `Event`, `NoPayloads`, and trace APIs.
Its event's `TransitionRef`, positive `NonZeroU64` sequence, borrowed state/checks,
and observer trait invocation match the declarations in
`src/observation.rs:100-120,183-219,307-338`. The loop's borrow lifetimes end before
the next iteration changes its state. No Rust API/signature mismatch was found
in this inspection. The independent native reviewer likewise found no obvious
API/signature/type mismatch in the four application additions; compilation
remains unperformed.

Application code performs each reducer/checking operation once, then passes the
existing result/checks to diagnostics and the actual checked token to Recorder.
Counters are captured before the separate producer-local replay passes. Each
logger call is surrounded by application/codec count comparisons; `NoPayloads`
formats unavailable markers without invoking model codecs. Actual writer output
is returned as `observer_text`, including the preexisting writer prefix. No
implementation path inspected executes a reducer/check/effect for logging, and
no diagnostic event is supplied as Recorder authority. Reducer/check executions
inside explicit replay are separate application verification, not logging.

## Independent literal derivation

The contract was read as authored data; no implementation was executed to
generate or refresh expectations. The following values follow from the pinned
Rust source and the existing Threshold model:

- `Threshold::step` advances by one and emits `changed`; its state checks are
  `threshold.bound` then `state.appended`, both passed below state 3; the
  transition check is `edge.checked`
  (`checked_reference.rs:886-923`). Supplied checkpoint 0 plus two turns yields
  initial-state/reducer/state-check/transition-check counts **0/2/3/2**. The first
  checked token has host sequence **41**, two state checks, and full coverage.
- Recorder attaches at origin **40** with capacity **1**. After sequences 41/42,
  it has observed **2**, evicted **1**, and retained **1** turn. Eviction promotes
  state **1** and only the first **2** checks; `edge.checked` remains a transition
  check (`src/monitor.rs:368-373`). No failed check occurred, so termination stays
  `Interrupted` (`src/monitor.rs:255-259,376-378`).
- `checkpoint_after_sequence` is the **local eviction count 1**, not absolute 41
  (`src/monitor.rs:600-605`). The independent configuration contract correctly
  retains origin 40 separately. Their sum identifies absolute checkpoint 41;
  replay starts from that checkpoint and reports local initial/turn sequences
  **0/1**. It proves nothing about the evicted turn.
- ASCII checkpoint/input/post-state bytes are `31`/`31`/`32`; the output is
  `6368616e676564`. Rich and bounded observations include expected and actual
  bytes, typed state/input/output, ordered checks, phase count 2, all four false
  difference flags, and incremental reports with 0 then 1 verified steps.
  Exact replay has `failure_reproduced: false` and matching application metadata.
- Checkpoint text has Trace severity; passing transitions have Debug severity
  even with configured Trace. Configured Trace includes all passed checks.
  EveryTransition snapshots plus NoPayloads produce `state=unavailable` on the
  checkpoint and input/output/post-state unavailable markers on each turn
  (`src/observation.rs:231-242,376-379,410-447,461-465`). The three literal lines
  in `suffix-contract.json:29-31` match those rules and preserve the final LF.

`tests/observation.rs:103-127,172-201`, `tests/monitor.rs:125-159`, and
`tests/monitor_review.rs:114-144` support the corresponding filtering, checkpoint,
eviction and phase-boundary contracts. They were inspected, not executed.

## Footer, counts, limits, and claim boundaries

The new independent binary framing reader is necessary and appears correct for
this finite contract (`scripts/observation_suffix.py:57-90`). It checks the v1
header, each frame's payload length and CRC over header plus payload, exact tag
order `[1,2,3]`, local step index `[0]`, and final footer payload
`010000000000000003`. Rust writes a little-endian u64 retained count followed by
termination byte **3** for Interrupted (`src/trace.rs:304-314,491-508,826-833`).
Thus a producer writing Completed instead is rejected even if a consumer's
reported replay result loses that distinction. This authenticates the persisted
footer at the gate; it does not prove every native reader exposes/preserves that
termination in its returned receipt. Add controls for a recomputed-CRC wrong
termination/count, missing/duplicate footer, wrong index and corrupt CRC.

At the reviewed helper revision the intended success path starts **5 producer
processes**, **25 ordered binary consumer processes**, and **25 ordered JSON
consumer processes**, including same-language fresh-process pairs. Each binary
pair runs both rich and bounded replay internally; this is 25 process pairs,
not 50 independent pair processes. Producer-local replay is additional validation
and must not be counted as the fresh-process proof. Rows are inserted before
execution and carry completed/matched flags; failure preserves partial rows and
propagates to the parent attempt. Success counters at lines 259-261 are reached
only after every comparison succeeds. None of these counts was executed here.

Binary pairs compare the full literal `session` twice plus the complete retained
configuration (`observation_suffix.py:239-249`). JSON pairs deliberately use the
old report-only `replay-json` mode and compare six final report fields
(`observation_suffix.py:250-257`; Rust implementation at
`checked_reference.rs:1280-1289`). They do **not** deliver full replay observations,
state-check phase counts, audit configuration, or bounded-replay receipts. Keep
that narrower scope explicit in the integrated report. If full observation
parity is claimed for JSON pairs, extend the consumers/contract first.

All producers select capacity 1, retained-byte budget 8192, the same nine finite
format limits, and 1024-byte observer records. The gate checks each persisted
artifact's total size, binary frame bounds, and each literal observer record's
UTF-8 length. Successful bounded replay is a positive within-limit case. This
fixture does not exercise byte-driven eviction, limit rejection/failure freeze,
long sessions, variable phase counts, failed/skipped checks, or first-failure
identity; it has only two passing turns. JSON uses the existing unbounded replay
entry point. These cannot be credited as negative limit controls or full N07
closure. Preserve the existing Recorder suites, including
`tests/monitor_limits.rs:145-181,302-333`, and their separate qualified counts.

Engine stamp validation is also narrower than source authentication:
`observation_suffix.py:48-54` checks stamp names, nonempty values, and native build
labels; it accepts any nonempty version and any nonempty Rust build value.
Cross-consumer equality preserves those producer values but does not establish
their correctness against package/version/build identity. Bind them to independently
identified inputs before claiming authenticated engine stamps. The package/source
manifest and compile command chain must carry provenance; `build_matches: true`
here compares application metadata `application-v1`.

## Parent integration and qualification gates

Integration was explicitly pending when review began. In the original inspected
revision, `load_compile` accepts only the six Rust suites plus
`native-observation` (`check-native-observation-consumers.py:559-561`), while the
helper requires `compile_binaries['native-checked']` at line 170. The compile
driver builds only `native-observation`
(`qualify-native-observation.py:64-87`). The installed driver has no helper call
and still describes the suffix gap at lines 714-715. Those references belong to
the original driver hashes below, not the late parent revision.

The late driver now includes `native-checked` in `RUST_CONSUMERS`, invokes the
helper at lines 879-888 before `installed-runtime-tree` at line 890, and replaces
the old gap text. The late qualifier admits both consumers at lines 71-96. This
is visible source integration, not execution proof or review of the entire new
compile-admission chain. Resolve S1's remaining membership issue and S2-S4, then
review the final command/artifact chain and nested failure publication together.

The earlier proof review's compile-receipt admission and package-build provenance
findings (R1/R2) require disposition against that late integrated revision; this
review does not claim they are all unchanged or all fixed. Retain the exact-HEAD
restriction described by R3 unless separately fixed.
The parent owns final integrated source review/freeze and the single shared
Kyoto qualification. Preserve failed/superseded receipts and actual command/log,
tool/package/artifact identities. No compile-only success can count as runtime.
Review the final changed hashes again after integration/fixes.

## Examined source identities

SHA-256 of the inspected revisions; these hashes are not a compilation or runtime
receipt. No fixture was generated by this review.

```text
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0  src/execution.rs
a7a4c8242408531e10453b0314c11b26c8c469ce2c8eca56e81c2e748d783cd1  src/execution/replay_kernel.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
704e559b3d31c660464fb9412f69b0803f1fb41313343e21b9b88ec5adc3ea96  tests/checked_replay.rs
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
16676c58c03d90bfc539f4b05b3291d5fb62b76a0c5debcbf4c3637b4eabdf94  docs/native-parity/OBSERVATION-REVIEW-PROOF.md
0a0fe932bc884a36e8aff63bc72116206a2918190fa540d2d22bf0e6ad78c2db  examples/native-checked/python/consumer.py
9f874f89db6a77322f563a5a221b7738b880d5a412f7065099c626f042c7bd2b  examples/native-checked/go/main.go
a8908ead940ed8c19252e1f94ce7d6945b88eb6ea3df6f13ff9d30a91a328f41  examples/native-checked/typescript/consumer.ts
92e83f7e17beae639a47f3a066baf595fea45337c2191321d4a42f4b05552570  examples/native-checked/swift/Sources/CheckedConsumer/main.swift
2cfc278f68919c481c48a9ffd3048bbd4f1edce17ada04826a2be9e4b0b80655  examples/native-checked/swift/Package.swift
853eb234184b8ce7f88f977694c7abb7a4bae7a2742e0c2cd852647e6c3bed78  validation/corpus/src/bin/native-checked.rs
b22e01e0d3a8040e11177d48eee2a6c7a0b05e3a7a8fa38d6318b7f617537c19  validation/corpus/src/checked_reference.rs
38dc23620a277c8afaf6f040bf9db8979165b4abefbab999fc4ba2720af1a5a8  scripts/observation_suffix.py
28fa35d88990b9e30b9b197d87b8116f922460491ac273a565b0c03dfd624b4c  examples/native-observation/suffix-contract.json
5235806b96c8ac30937abfde6aa147a630c9da5d653e4d69cfc07b8958011289  scripts/check-native-observation-consumers.py
d009723b7f209b6e53f8d6de037a4e571097138f26a92d6bbed208eb19c831fb  scripts/qualify-native-observation.py
```

Late parent source identities, examined only for the bounded follow-up described
above; the original hashes and findings are retained rather than overwritten:

```text
13b8c93fc7bea4a6f27dcf3f9abee9606e49bd45cd88078f57eb0c28cbf8f204  scripts/check-native-observation-consumers.py
685807818054f4cf56276faa55f6be75b7a96559b339e361cce6cdec67e4ed21  scripts/qualify-native-observation.py
```

The suffix helper, contract, Rust application, and four native application hashes
were unchanged on final reread. Other agents' concurrent native/source fixes were
preserved; this document is not an approval of those separate changes.
