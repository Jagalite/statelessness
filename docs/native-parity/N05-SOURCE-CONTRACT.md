# N05 source contract and independent golden expectations

Authority is commit `a4cc11eea8db62115b452eee3c6610c04fa06b96`. This contract was
derived from Git objects while the isolated checkout was materializing that
commit. No candidate implementation/output supplied an expected value. The
existing [audit](GUIDED-CAMPAIGN-AUDIT.md) and
[review](GUIDED-CAMPAIGN-REVIEW.md) were source-only navigation inputs, checked
against the actual Rust implementation. This document covers bare N05 and the
ordinary fuzz behavior its shared kernel must preserve. It does not close N05,
N06, N10, M4, installed-package qualification, or platform execution.

| Authority file | SHA-256 |
| --- | --- |
| `src/guided.rs` | `bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7` |
| `src/explore.rs` | `496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb` |
| `src/model.rs` | `e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb` |
| `src/execution.rs` | `acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0` |
| `src/execution/replay_kernel.rs` | `a7a4c8242408531e10453b0314c11b26c8c469ce2c8eca56e81c2e748d783cd1` |
| `tests/guided.rs` | `02624f83c5a6d310817fc69f0d3c9897ed96669dde75ead554fac17066fccd15` |

Line references below refer to these pinned objects. Tests were read for authored
regression intent, not treated as executed evidence. Source defaults and API
names are facts about this commit, not assertions about subsequent revisions.

## Public feedback and ownership

`Feedback<M>` has an application-owned `Feature: Eq + Hash`, metadata, default
empty initial feedback and required transition feedback. Initial absence means
success with no key; it must not silently become exact-state feedback.
`StateFeedback::new(metadata, project)` emits one projected key for initial and
successor state. `exact_state` uses a full state clone with metadata
`exact-state / 1 / clone-eq-hash-v1`. Model state identity and feature identity
are independent. Equal compact features must never merge application states,
omit checks, suppress transitions or replace concrete replay history. Hashes
locate keys; equality resolves collisions (`guided.rs:18-112`).

The FeatureBuffer borrows engine scratch for one callback. Every successful push
occupies one slot, including duplicates. At `len >= maximum`, a push fails and
sets a sticky failed bit; every later push in the same callback also fails.
Ignoring errors cannot bypass the limit. A callback's returned error wins over
the sticky error because `?` precedes the failed-bit check. On callback success,
the engine detects an ignored overrun before learning **any** emitted key.
Scratch is cleared for each callback (`guided.rs:21-37,323-362`).

An error is present by its Result variant, not by message truthiness.
`ModelError::new("")` is still Err. In the JSON fixtures only null means no error;
an empty checker message produces contextual error `"check_state: "` (or the
corresponding phase prefix), and an empty feedback callback error still wins
over ignored buffer overrun. The final space after the colon is significant.

The returned corpus owns cloned input sequences; candidate selection clones the
retained sequence before mutation. Rust's Clone does not promise recursively
deep snapshots of application interior mutability. Stable equality/hash and
valid callback references remain application obligations. FeatureBuffer bounds
counts, not key size, callback allocations, output size, or RSS. The public Rust
feedback does not require feature Clone, Send, Sync, serialization, canonical
encoding, or a state hash shared with the model.

## Defaults, validation and reporting

Guidance defaults are 256 entries, 25,600 total retained input values, 100,000
tracked keys and 256 emitted keys per observation. All four must be positive;
failure text is `guidance limits must be positive`. Ordinary fuzz defaults remain
seed0, cases1000, max_steps100, max_transitions100000, mutation_percent50.
Guided `fuzz`, `fuzz_with_limits`, and `fuzz_observed` retain those fuzz defaults.

Bare guided admission order is guidance validation, mutation-percent validation,
outer Control construction, exactly one feedback metadata query, then shared
kernel entry with the remaining duration. The kernel constructs its own Control,
validates mutation percentage, initializes RNG/report, and polls control before
the case loop. No model metadata query belongs to this API. Metadata is queried
even for zero cases, pre-cancellation, and a supported zero-duration run.
Invalid guidance or mutation prevents metadata. On unknown-OS Wasm, requested
wall-clock control is rejected before metadata; this target-specific branch is
not a general rule about JavaScript/browser clocks (`guided.rs:165-232`,
`explore.rs:127-181,569-612,731-756`).

The guided report identifies `feature-corpus-v2`, preserves config/feedback
identity, and includes all counters, both sticky limit flags and concrete FIFO
corpus entries. `discovered_case` is one-based; `discovered_transition` is the
global executed-transition count, not case depth. `cases` counts successfully
initialized cases, including ones ending in initial failure or a step budget.
`transitions` counts successful reducer returns before property checking.
Skipped checks saturate at u64::MAX; no separate saturation flag is exposed.
The other guided counts are ordinary bounded arithmetic, not saturating metrics.
`corpus_inputs` is exactly the sum of retained sequence lengths. Callback errors
return Err and expose no successful partial FuzzReport. An observer may already
have seen completed/partial work (`guided.rs:114-163`,
`explore.rs:602-612,763-881,1381-1407`).

## Case and callback sequence

For each case, after a control poll:

1. Call initial_state; on success increment cases. Run initial state checks.
   On checker Err, call `check_error(InitialState, partial, false)` then return
   `initial check_state: ...`. Otherwise call normal initial observer before
   extracting failures/skips. Initial property failure wins before control or
   feedback.
2. Poll control, run initial feedback and learn, poll control, choose candidate.
   Initial learning can track/recover keys but never admits an empty prefix.
3. At each step entry, check transition equality with the cap **before** polling
   control. There is no step-entry probe for max_steps0.
4. If this is not the one replacement position and a candidate input exists,
   call is_enabled. Reuse only if true. Poll control. Otherwise generate a fresh
   input. Poll control. Generation None ends the case. Reused enabled inputs are
   not checked twice; generated input calls is_enabled, then polls control, then
   rejects a false result with `generate returned a causally disabled input`.
5. Step, count transition, append input. Run successor state checks then
   transition checks with **no control polls** between successful reducer/check
   callbacks. State-check Err yields only `check_error(State, partial, false)`.
   Transition-check Err yields `check_error(State, completed, true)` followed by
   `check_error(Transition, partial, false)`, even if completed state checks
   already contain a failure. Checker Err wins over property/control outcomes.
6. On successful check callbacks, invoke normal transition observer with state
   and transition batches and the actual input prefix. Only then extract skips
   and failures. All failed state checks precede all failed transition checks.
   Fuzz passes full prefix history regardless of observer.needs_history.
7. Property failure returns before feedback/control. Otherwise poll control,
   invoke transition feedback and learn, then poll control and adopt new state.

Callback Err wins over cancellation set inside the same callback. Cancellation
from successful step/check/observer allows all checks and normal observer to
finish, but prevents transition feedback unless a property failure already won.
Cancellation from feedback success still retains its learned keys/prefix before
the next poll. The control poll checks cancellation before deadline. Rust normal
observer callbacks have no Result channel and cannot replace property outcomes
through a return value (`explore.rs:17-39,763-890,1410-1462`).

Finishing the last allowed transition on the last configured step can report
CasesCompleted. A later case may initialize/check/learn and consume mutation draws
before its first step reports TransitionLimit. No extra terminal cap test is
allowed. Generation None is a case end, not a transition or global termination.
The post-feedback poll still runs at the final case/step boundary: cancellation
from that successful callback produces Cancelled and retains its learned prefix.
The post-generation poll precedes interpretation of None, so a generator that
returns None and sets cancellation yields Cancelled. Preflight control is polled
even for cases0, after guided metadata: pre-cancelled yields Cancelled and a
supported zero-duration budget yields Deadline.

Disposition is part of the actual borrowed transition (`model.rs:284-289,329-340`).
The shared fuzz kernel does not branch on Accepted/Rejected/Ignored: a successful
step still counts, both property phases and observer run, and successful feedback
can learn from it. The fixture's parameterized non-Accepted transitions retain
their declared state and outputs. Their exact reason, including an empty string,
must survive both observer and feedback traces and public recording/replay.

## Feature learning and FIFO admission

Track each equal key once with a `witnessed` bit. A prefix is retainable exactly
when its length is <= max_corpus_inputs; the empty initial prefix is retainable.
Process emissions in order, resolving equality even at full capacity:

* An existing unwitnessed key becomes witnessed on a retainable observation and
  marks the observation recovered. It does not increment new_features.
* A vacant key at feature capacity is rejected and sets feature_limit_reached.
  A vacant key below capacity is inserted with witnessed=retainable and increments
  the observation's novel count.
* After all keys, publish seen size. Stop if novel=0 and no recovery, or if the
  prefix is empty. Rejected untracked keys alone never admit or drop a prefix.
* A novel overlong prefix sets corpus_limit_reached and increments dropped_prefixes
  once for the observation, regardless of how many new keys. Repeating an
  overlong observation with only existing unwitnessed keys does not drop again.
* Otherwise evict oldest prefixes until both entry count and total retained-input
  budget permit admission. Each eviction decrements corpus_inputs by its old
  length and increments evicted_prefixes; it sets corpus_limit_reached. Never
  clear witness bits or delete tracked features on eviction.
* Append the entire concrete prefix, novel count (possibly zero for recovery),
  current case and global transition. Filling a limit exactly sets no flag;
  flags describe attempted rejection/eviction, not occupancy.

These laws allow shorter recovery at full feature capacity, multiple FIFO
evictions on one admission, witnessed features with no currently retained
representative, and feature sets that permanently exceed active corpus coverage.
Novelty is not exhaustive state coverage (`guided.rs:233-291`).

## RNG call graph and candidate selection

SplitMix64 starts at seed; next advances by 0x9e3779b97f4a7c15 with u64 wrapping,
then applies the two source mix multipliers 0xbf58476d1ce4e5b9 and
0x94d049bb133111eb and final xor shifts. `index(upper)` returns None without a
draw when upper0. For upper>0 it repeatedly draws until value >=
`wrapping_neg(upper) % upper`, then returns value%upper. `index(1)` consumes a
draw. Modulo without rejection or skipping index1 changes subsequent generation
(`model.rs:477-510`).

Guided mutation draws, in exact order:

1. If corpus empty, return fresh candidate without a draw. Otherwise index100,
   including mutation_percent0 and100. If result >= mutation_percent, fresh.
2. Increment mutation_cases, index(corpus length), clone that FIFO entry, index3.
3. Arm0 reuses prefix with no replacement. Arm1 draws start index(length), then
   delete count `1 + index(length-start)`, removes that span, and draws replacement
   index(new length+1). Arm2 draws replacement index(length).

Only the selected position is replaced. Later original positions remain eligible
for is_enabled/reuse. Arm1 can select replacement at candidate end; generation
beyond candidate length still follows normal behavior. A failed reuse regenerates
only the current position; later retained positions remain eligible. Generation
None ends the entire case, including any later retained positions. A generated
replacement may alter later causality, which is checked on reuse.

The additive seed42, one-corpus-entry fallback fixture keeps `[1,2,4,8]` as the
only retained prefix after case1. The source mutation draw sequence in case2
deletes its leading `[1,2]`; retained 4 is disabled at total0, and retained 8 is
disabled at total1. The fixture generator returns 1 and 2 at those depths. Each
rejected reuse must therefore be followed by generation, a successful enabled
check on the generated input, and reduction. Manual trace anchors assert these
exact two fallback sequences and the eventual case2 inputs `[1,2,4,8]`.
The old `mutation-disabled-reuse` fixture is preserved for regression identity;
despite its historical description all its 20 enabled checks are true, so it
does not prove fallback coverage.

Ordinary fuzz uses only the previous passing case's full sequence. If empty,
no draw; otherwise index100. A selected mutation then draws deletion start,
deletion length and replacement index(length after deletion+1); it never draws
corpus-index or arm. The shared kernel saves previous only on normal case end
(`guided.rs:294-320`, `explore.rs:653-672,793-888`).

## Independent corpus and replay/shrink expectations

The [shared schema](../../conformance/guided-v2/README.md) defines one deterministic
table model, feedback policies, injected errors/cancellation and normalized trace.
The independent [oracle](../../conformance/guided-v2/oracle.py) implements these
source-derived rules using only Python stdlib. It reads no Rust driver output,
native package or candidate output. Linear key lookup deliberately provides a
separate collision-independent specification of Eq behavior. Generated expected
values are authored before cross-engine comparison and sealed by FREEZE.json.
The oracle is an independent specification implementation, not qualification of
the candidate or a substitute for the Rust/public consumers.

Every retained corpus entry has a fresh replay expectation: full initial and
successor states, outputs, dispositions, ordered check batches and Completed
termination, with replay Exact, failure_reproduced=false and steps_verified equal
to the number of recorded transitions.
Passing witnesses are not sent to shrinking; Rust rejects absent target violation
(`explore.rs:1006-1010`). The two property-failure cases use state-based total>=5,
first inputs `[1,1,5]`, phase State, check ID `limit`. The full shrink expectation
is hand-derived:

1. Validate `[1,1,5]`: three transitions, same failure, attempt1 Retained.
2. Delete range `[0,2)`: `[5]` reproduces after one transition; attempt2 Retained.
3. Delete `[0,1)` from `[5]`: empty passes, zero transitions; attempt3 NoFailure.
4. No simpler inputs exist. SearchComplete, minimized `[5]`, four transitions.

With max_attempts1, validation still executes three transitions and reproduces
the original; the next attempted deletion is blocked, giving AttemptLimit with
one history entry. This is bounded algorithmic reduction, not global minimality.
The first original violation supplies phase+ID target; a different phase with
the same ID does not qualify (`explore.rs:995-1051,1057-1135,1184-1234,1369-1374`).

For both shrink-selected cases, append original and minimized failing witness
recordings after the retained passing FIFO entries, even when original and
minimized inputs are equal. Original `[1,1,5]` records states
`[[0,0],[1,1],[2,2],[3,7]]`, outputs `[[1],[2],[7]]`, and the final state check
`limit / Failed / total reached limit`; its following transition batch is empty.
Minimized `[5]` records `[[0,0],[1,5]]` and `[[5]]` with the same failure identity.
Both terminate PropertyFailed. Exact replay verifies all corresponding
transitions, including the failing transition, so their steps_verified are 3
and 1 and failure_reproduced is true. The attempt-limited case appends two copies
of the original witness, each with steps_verified3. This does not broaden N04
shrinking scope.

The recorder appends the successfully checked failing step before terminating
PropertyFailed (`execution.rs:363-398,475-488`). Replay initializes zero verified
steps, checks recorded/actual failure identities, and increments steps_verified
for every fully matching transition (`execution/replay_kernel.rs:219-224,317,
436,495-511`); property failure itself is not a divergence. Recorded dispositions
come from the actual transition (`execution.rs:396-401`). All these expectations
were derived from source without executing a candidate or reference driver.

## Native-only controls and explicit gaps

The shared corpus has mathematical integer inputs and owned JSON-compatible
states/keys. Each native host separately needs explicit tests and API decisions
for the following; none can be claimed solely from cross-Rust normalized output:

* Stable feature equality and collision handling for native compound keys; NaN,
  reference identity, hash errors and mutable hash keys need explicit policy.
* Input/state/output/key ownership at callback, corpus and report boundaries,
  including aliasing/mutation attempts and exact-state cloned snapshots.
* Retaining a FeatureBuffer or check sink after callback return, reentrancy and
  revocation. Rust borrowing is not a proof of dynamic-language lease behavior.
* Awaitable/Promise/task/async-generator rejection where APIs are synchronous;
  typed throwable/exception containment and native observer error policy. Rust
  feedback returns ModelError; its ordinary observers return unit and may panic.
* Native clock availability, real cancellation primitives and positive-duration
  deadline accounting, including metadata time. Shared zero-duration cases do
  not qualify timer precision, preemption, browser workers or async execution.
* Count width and boundary behavior: exact u64 seed/RNG/skipped accounting,
  skipped saturation with no flag, integer overflow rejection/representation,
  and usize/host limits. Exhausting u64 skips through public callbacks is not a
  practical corpus test; an explicit bounded internal arithmetic test is needed.
* Direct `Rng.index(0)` no-draw behavior is a separate Rust host/portable RNG
  baseline gate. Guided empty-corpus short-circuit coverage does not itself call
  index0; no artificial fixture algorithm is added to imply that coverage.
* Huge keys/prefix inputs and allocation pressure. All four guidance limits are
  count caps; no hard memory/RSS or callback allocation bound is provided.
* StateFeedback/exact-state use through real public constructors, default-empty
  Feedback initial implementation, and ordinary public API defaults. Transparent
  tracing wrappers may delegate; adapters must not replace these public features
  with private fixture algorithms.

Cross-engine run results, installed clean-process consumers, native package
ownership/lifetime tests and actual public replay/record integration remain for
the implementation/qualification owners. This source work never edits runtime
implementations or reference drivers, stages/commits, pushes, changes profiles,
promotes defaults, changes Kyoto policy or touches the frozen parent checkout.
