# N01 controls, estimates, and check-storage audit

Status: static audit and proposed next implementation scope, 2026-10-20.
Inspected worktree HEAD `daaf76c15d1c04c8720e4710b9e746a7d72ddb97` plus
concurrent binary-codec candidate changes. No builds or runtime qualification were
performed for this audit. This document does not close N01 or promote a profile.
See [the parity plan](../NATIVE-PARITY-PLAN.md) and
[current status](../NATIVE-PARITY-STATUS.md) for the wider scope.

## Current gap and next coherent slice

All four independent engines have bounded FIFO exploration, and their newer
fuzz/shrink engines have cooperative run controls. Their BFS APIs still lack the
Rust controls, retained-state estimates, and search-observer integration listed
below. Adding those three together is the next coherent BFS implementation slice.
Append-check storage and rich replay observations need separate complete slices;
implementing only BFS does not establish full execution or N01 parity.

| Surface | Rust authority | Independent native source to extend |
| --- | --- | --- |
| BFS controls and admission limits | [RunLimits/SearchLimits](../../src/explore.rs#L103), [enumerate_with_limits](../../src/explore.rs#L276) | Python [enumerate_states](../../python/src/statelessness/core.py#L403), Go [Enumerate](../../go/explore.go#L59), TypeScript [enumerateStates](../../typescript/src/core.ts#L124), Swift [enumerateStates](../../swift/Sources/Statelessness/Explore.swift#L35) |
| Model estimate hook | [Model::estimated_state_bytes](../../src/model.rs#L407) | Python [Model](../../python/src/statelessness/core.py#L95), Go [Model](../../go/model.go#L114), TypeScript [Model](../../typescript/src/core.ts#L58), Swift [Model](../../swift/Sources/Statelessness/Model.swift#L102) |
| Reported retained-state bytes | [SearchReport](../../src/explore.rs#L232) | The four BFS modules above omit this field |
| BFS observer | [SearchObserver](../../src/explore.rs#L19), [enumerate_observed](../../src/explore.rs#L289) | Existing native observer definitions: Python [search.py](../../python/src/statelessness/search.py#L161), Go [search_observer.go](../../go/search_observer.go#L9), TypeScript [search.ts](../../typescript/src/search.ts#L31), Swift [Fuzz.swift](../../swift/Sources/Statelessness/Fuzz.swift#L38) |

Existing BFS entrypoints must retain their default behavior. Add optional limits
and observer entrypoints/arguments consistent with each language's public style;
reuse the existing run-control implementation rather than creating a second
deadline/cancellation interpretation. Preserve finite enumeration, collision
resolution by actual equality, FIFO predecessors, shortest witnesses, duplicate
edges, and the single input probe needed to distinguish exhaustion from a cutoff.

## Required BFS semantics

The reference is the complete [Rust BFS kernel](../../src/explore.rs#L319), not
only its public types. Implement and qualify all of these requirements together:

1. Pre-start cancellation or an expired deadline invokes no model callbacks and
   reports zero retained states. Cancellation takes precedence over deadline when
   both are observed. Controls remain cooperative and cannot interrupt callbacks.
2. Once initial-state or transition checking starts, finish that checking boundary
   before reporting a stop. A property failure wins over a cutoff raised during
   that boundary. A checker error remains an engine error.
3. Probe controls around input-iterator creation and advancement, state hashing,
   each collision-chain equality comparison, and estimation. Merely checking once
   per BFS node does not match Rust.
4. Call the initial estimate after its successful checks, even without a byte cap.
   An initial property failure returns before estimation. When the initial state
   exceeds the cap, return `estimated_state_byte_limit`, zero retained states and
   a known retained-byte total of zero.
5. Fully check and observe each executed successor before deduplication or state
   admission. Duplicate successors are not estimated or charged again. For a new
   successor, the state-count cap takes precedence over the byte-admission cap.
6. The report sums estimates for admitted states only. Missing estimates or sum
   overflow make the uncapped total unknown. With a byte cap, a missing estimate
   is an engine error; sum overflow or a sum above the cap returns the byte-limit
   termination without admitting the successor. Exact equality with the cap fits.
7. Byte admission does not bound temporary successor allocation, predecessor
   inputs, maps/frontier storage, callback allocation, or process RSS. Preserve
   this limitation in public documentation.
8. Emit the initial observation and complete checked transition observations,
   including duplicate edges and failing transitions. Requested histories are
   actual initial-rooted input prefixes including the current input, never FIFO
   traversal order. Observers must receive detached snapshots under the existing
   native ownership contracts.
9. On initial/state-check error, deliver the actual partial check prefix as
   incomplete. On transition-check error, deliver completed state checks and the
   incomplete transition-check prefix separately. Preserve the primary checker
   error if error-reporting observation also fails. The Rust reference helpers
   are [initial_checks_ops and transition_checks_ops](../../src/explore.rs#L1410).

### Reference boundary and counter clarifications

These examples clarify the current Rust kernel's ordering; they do not add a
new control policy. A stop means cancellation or an expired deadline observed
at the next existing poll, with cancellation first when both apply.

- There is no control poll between `initial_state`, initial checking, and the
  initial observer. Cancellation requested by `initial_state` still allows the
  check batch and observer to complete. A passing batch then returns the stop
  with `states=1`, `transitions=0`, and an unknown retained-byte estimate. An
  initial property failure wins and returns before any estimate callback, even
  with a zero byte cap.
- There is no control poll between a successful `step`, successor checking,
  transition checking, and the transition observer. The executed edge and its
  depth are counted before checking. A state-property failure does not suppress the
  transition checker; its error, if any, remains an engine error and takes
  precedence over that finding. After successful checking and observation, a
  property failure wins over a stop requested during this boundary.
- Input advancement is followed by a control poll before interpreting either
  exhaustion or the transition cap. An iterator that requests cancellation on
  its final `None` therefore returns `cancelled`, not graph exhaustion. At the
  depth boundary the same post-probe poll precedes remembering a depth cutoff.
- Estimation is followed by a control poll before checking missing estimates,
  overflow, or the byte cap. The initial estimate has already been stored at
  that poll: an initial estimate of 10 that requests cancellation with a byte
  cap of 0 reports `cancelled`, `states=1`, and retained bytes 10. Without that
  stop, the same estimate reports the byte limit, zero states, and zero retained
  bytes. A missing estimate that requests cancellation also returns the stop
  before the missing-estimate error would be raised.
- A successor estimate is committed to the report only when admission succeeds.
  Cancellation requested by that estimate preserves the previous state count
  and retained-byte total; the completed edge and reached depth remain counted.
  Duplicate successors and novel successors rejected by the state-count cap
  never invoke estimation. Once an uncapped admitted total becomes unknown
  through a missing estimate or checked overflow, later known estimates do not
  restore it.
- A checker error emits only error observations, never a normal initial/turn
  observation. A successor-state checker error emits its incomplete prefix;
  transition checking does not run. A transition-checker error emits the complete
  state batch first and the incomplete transition batch second, including empty
  batches. Any partial failed check remains diagnostic evidence, not a successful
  failure report that replaces the checker error.

### Estimate forwarding and representation convention

Extend both model and oracle estimate surfaces. Rust's
[WithOracle estimate](../../src/oracle.rs#L236) combines model and oracle history
estimates with checked addition and the actual wrapper-layout overhead. Rust's
[Paired estimate](../../src/conformance.rs#L56) delegates to the reference model.
Native wrapper locations are Go [Oracle/WithOracle](../../go/oracle.go#L15) and
[Paired](../../go/paired.go#L19), Python [WithOracle](../../python/src/statelessness/oracle.py#L62)
and [Paired](../../python/src/statelessness/oracle.py#L184), TypeScript
[Oracle/WithOracle](../../typescript/src/oracle.ts#L9) and
[Paired](../../typescript/src/oracle.ts#L120), and Swift
[Oracle](../../swift/Sources/Statelessness/Oracle.swift#L11).

Define the native wrapper-overhead convention before claiming estimate parity.
Managed-runtime object sizes are not Rust's inline layout, and runtime object-size
helpers generally do not count owned reachable data consistently. Document which
logical/native representation is estimated, propagate unknown values and checked
overflow, and expose enough information to test wrapper accounting. Silently
adding only the two application estimates is not an exact implementation of the
Rust wrapper rule. Nor should a language's object-size result be presented as a
cross-language RSS guarantee. Test Paired forwarding independently from WithOracle
aggregation and preserve generation/enumeration/codec forwarding already present.

## Append-only checking slice

Rust provides [CheckSink](../../src/model.rs#L123), default-compatible
[check_state_into/check_transition_into](../../src/model.rs#L379), and
[check_observed_into](../../src/execution.rs#L106). Native model APIs currently
return collections or iterables. Python/TypeScript lazy search checking can retain
partial observations, but this does not provide append-style caller-owned storage;
Go can already preserve valid returned checks alongside a non-nil callback error
in its search-observer path. A panic before returning still exposes no returned
prefix. Swift's throwing array callbacks cannot reveal checks accumulated inside
a callback that throws before returning. Neither return-array API is an
append-only caller-owned check sink.

A complete implementation needs optional append callbacks with backward-compatible
defaults, an append-only sink that cannot clear or replace prior checks, reusable
engine storage, and forwarding through WithOracle/Paired and other applicable
adapters. Integrate execution, BFS, fuzz, shrink, recording, and replay checking
consistently rather than adding unused public callback fields. Preserve emitted
order and state/transition batch boundaries.

The [already-executed check path](../../src/execution.rs#L129) deliberately clears
its caller-owned result on error; search observers deliberately retain partial
prefix evidence. These are distinct contracts. Do not generalize either behavior
to the other. Existing native `check_observed`/`checkObserved` operations are
already present; the gap is their append-storage counterpart and richer checked
evidence, not absence of basic observed checking. Rust's sealed
[check_initial/check_turn](../../src/execution.rs#L30) tokens also remain a distinct
requirement for later exact-recorder integration.

## Replay is a separate observation/recovery slice

Rust replay does **not** accept `RunLimits` directly. Its
[ReplayOptions](../../src/execution.rs#L495) and
[rich observation APIs](../../src/execution.rs#L668) support stopping at initial or
turn boundaries through the observer. Do not add a differently specified replay
control API and label that Rust parity.

The native basic replay APIs are Python [replay](../../python/src/statelessness/core.py#L317),
Go [Replay](../../go/execution.go#L250), TypeScript
[replay](../../typescript/src/core.ts#L230), and Swift
[replay](../../swift/Sources/Statelessness/Execution.swift#L102). A complete richer
slice must add initial and divergent-turn observations, every available difference
flag with disposition → outputs → state → checks primary precedence, typed actual
states/inputs/outputs, complete-versus-incomplete checks, and retained actual
results after execution errors. An error-reporting observer failure is secondary
and cannot replace the engine error. No transition may be repeated merely to
recover evidence. Follow [ReplayObservation/ReplayActual/ReplayError](../../src/execution.rs#L603).

The [bounded replay variant](../../src/execution.rs#L684) additionally validates the
artifact before decoding and shares frame/aggregate byte/item budgets with actual
encoded observations, including a divergent turn. Incremental encoding buffers
and allocating legacy codecs have different allocation guarantees. Basic replay,
rich observation, and bounded observation require separate evidence; a passing
basic trace replay does not establish all three.

## Qualification and closure requirements

Before marking these scopes implemented and qualified:

- Add shared contract vectors derived from real public Rust executions, covering
  callback order and result counters as well as termination names. Exercise each
  independent native engine against those vectors.
- Cover pre-start and callback-triggered cancellation/deadline; failure-versus-stop
  precedence; iterator/hash/equality/estimate stops; depth/transition exhaustion
  probes; zero, exact-fit, over-cap, missing and overflow estimates; duplicate
  suppression; state-cap precedence; and wrapper estimate propagation.
- Cover BFS observer initial/failing/duplicate edges, detached actual histories,
  partial batches, complete state batch followed by transition-check error, and
  primary-error preservation. Existing fuzz observer tests alone are insufficient.
- For append sinks, test order, inaccessible destructive mutation, default and
  overridden callbacks, wrapper composition, buffer reuse, and the distinct
  search-partial versus observed-check-cleared error contracts. Make allocation
  claims only with measurements appropriate to the runtime.
- For replay, cover sequence zero, simultaneous difference flags, first-divergence
  stopping, actual-result retention at each callback/codec failure, observer-stop
  behavior, secondary observer errors, and bounded encoding limits.
- Route executable qualification/build work through Kyoto. Record requested and
  effective commands, source identities, actual test execution, package-consumer
  coverage, and remaining platform/runtime scope. Compilation alone is not a test
  pass, and these slices do not close the remaining N01–N14 requirements.
