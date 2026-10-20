# Append-only check storage: implementation audit and next scope

Static audit, 2026-10-20, worktree HEAD
`7ba10472cd084afcc763d0d044e4c00f677ac9cd` with concurrent BFS candidate changes.
This is an actionable implementation brief, not an implementation or qualification
claim. No builds or runtime tests were run for this document. The current BFS
work does not by itself close this gap or N01.

## Reference contract

Rust's [CheckSink](../../src/model.rs#L123) wraps existing caller storage without
clearing it. A checker may append, extend, reserve, count, or inspect immutable
observations; it cannot remove or replace preceding observations or obtain the
mutable backing vector. The borrow prevents retaining the sink past its owner.
The [Model append hooks](../../src/model.rs#L379) default to extending from the
existing collection-returning callbacks, preserving compatibility and emitted
order. Defaults append only after the legacy callback returns successfully;
internal values from a legacy `Err` are unavailable. Defaults may still allocate
their temporary result collections. Inspection and length include the entire
existing prefix: a transition checker can see earlier state checks and an oracle
checker can see the actual model's observations.

[WithOracle](../../src/oracle.rs#L194) forwards the same sink first to the actual
model and then to the oracle. [Paired](../../src/conformance.rs#L72) forwards
reference checks before appending projection comparisons. Therefore a later
oracle, reference, or projection error may follow already appended observations.
This order and evidence cannot be reproduced by running either checker again.

[check_observed_into](../../src/execution.rs#L106) clears the caller's logical
result at entry while retaining capacity. It checks an already executed turn and
does not invoke the reducer. Its [shared implementation](../../src/execution.rs#L129)
clears the entire result on a returned `Result::Err`, including invalid sequence
zero. Sequence zero fails without invoking any model callback. It does not catch
callback panics: a panic after an append can unwind with that prefix still in
caller storage. Rust's
`CheckPolicy.state_every` is `NonZeroU64`, so a zero period cannot reach this API.
Native invalid-configuration, snapshot-failure, and contained panic/exception
cleanup must be specified as explicit native extensions rather than inferred
from Rust's returned-error behavior.

Policy-skipped state and transition batches append their explicit skipped checks
in that order. A skipped state batch suppresses only state checking; it does not
prevent enabled transition checking. A failed state property also does not
suppress transition checking. These observations borrow an already executed
transition and do not rerun its reducer or validate an initial state implicitly.
Rust's separate `check_initial` owns a fresh private vector and returns no checked
token on error; it is not a public reusable initial-check-into operation.
By contrast, [search checking](../../src/explore.rs#L1410) preserves actual partial
prefixes for diagnostic observers and distinguishes incomplete state/transition
batches. These are separate public contracts, not inconsistent behavior.

## Native source findings

| Language | Current check path | Required integration points |
| --- | --- | --- |
| Python | [Model and core collectors](../../python/src/statelessness/core.py#L104), [search collectors](../../python/src/statelessness/search.py#L211) consume iterables; lazy yields can survive a later error in search diagnostics | `core.py` observed checking, initial record/replay direct collectors; `search.py` shared BFS/fuzz/shrink checks; [WithOracle/Paired](../../python/src/statelessness/oracle.py#L142); generated/automatic wrappers and package exports |
| Go | [stateChecks/edgeChecks](../../go/model.go#L185) use collection-returning callbacks; [partialChecks](../../go/search_observer.go#L24) already preserves a valid returned prefix alongside an error | `model.go` validation and observed checking, `search_observer.go`, BFS/fuzz and shrink paths, `execution.go` initial checks and calls to `CheckObserved`; [Oracle](../../go/oracle.go#L15), [Paired](../../go/paired.go#L19), generated/automatic wrappers |
| TypeScript | [core collectors](../../typescript/src/core.ts#L94) and [search support](../../typescript/src/search_support.ts) are distinct paths; lazy iterables retain partial search evidence | Both collector families, direct record/replay initial checks in `core.ts`, [oracle wrappers](../../typescript/src/oracle.ts#L92), automatic facade receiver forwarding and public index declarations |
| Swift | [Model collectors](../../swift/Sources/Statelessness/Model.swift#L139) return arrays; [searchCheckBatch](../../swift/Sources/Statelessness/Fuzz.swift#L59) preserves validated prefixes of returned arrays | Model initializer/fields, shared search helpers and shrink initial checks, execution record/replay initial checking, [Oracle/Paired](../../swift/Sources/Statelessness/Oracle.swift#L100), Generate wrappers and SwiftPM public API |

Go is not equivalent to Swift here: Go's callback can return `[]Check` **and** an
error, and its observer path already retains the valid prefix before the error.
A throwing Swift array-return callback provides no array at all on throw; Swift
currently retains only a validated prefix when a successfully returned array
contains an invalid later check. An append sink would make pre-throw emitted
observations available without fabricating evidence in either language.

Do not update only observed BFS: shrink's replay checking, fuzz without observers,
basic observed checking, and record/replay initial checks must also prefer append
hooks. Existing direct `check_state`/`checkState` calls need an explicit audit.
Binary/JSON serialization of existing check values is not itself a check-dispatch
path; it must remain compatible without calling model checkers.

## Coherent backward-compatible API scope

1. Add an append-only `CheckSink` surface and optional state/transition append
   callbacks: Python `check_state_into`/`check_transition_into`; Go
   `CheckStateInto`/`CheckTransitionInto`; TypeScript and Swift
   `checkStateInto`/`checkTransitionInto`. Names are proposals to settle against
   each package's style, not claims that these symbols exist today.
2. Prefer an append hook when present. Otherwise enumerate the legacy callback
   result exactly once and append its validated values in order. Never invoke
   both callbacks, rerun a callback to recover a prefix, or derive passing checks
   from a partial batch. Preserve optional-empty transition checking defaults.
3. Keep existing collection-returning models, constructors and engine entrypoints
   source compatible. Rust still requires legacy state checking, so compatibility
   does not require permitting an append-only model with no collection callback;
   if that extension is chosen, document its validation contract explicitly.
4. Expose observed-check-into entrypoints with caller-owned reusable storage.
   Clear at entry and on returned errors, matching Rust. Also define clearing for
   native validation, snapshot and contained panic/exception errors as explicit
   host-language extensions; Rust does not catch an unwinding checker panic.
   Append policy checks in the same order as existing APIs. Keep the
   collection-returning convenience APIs, implemented through the shared dispatch
   to prevent semantic drift.
5. Share dispatch and batch-boundary logic across execution, BFS, fuzz, shrink,
   record and replay. Keep context-specific ownership/error policy at the caller.
   Search observers receive completed state checks separately from an incomplete
   transition prefix; ordinary observed checking exposes no partial success.
6. Add matching oracle append hooks and route WithOracle/Paired through them in
   actual-model, oracle/reference, projection order. Preserve partial prefix
   evidence when a downstream callback fails. Public legacy wrapper methods may
   collect from the new implementation, but must avoid recursive default loops.
7. Preserve capabilities and bound receiver semantics through generated/automatic
   wrappers, especially TypeScript class-private methods and frozen source models.
   Optional hooks must not disappear when a wrapper forwards generation or codec
   behavior. Keep ABI adapters separate from this independent-native scope.

## Ownership and escaped sink lifetime

Rust's lifetime guarantee needs a deliberate native equivalent. A checker can
retain a Python/Go/TypeScript/Swift sink object even after returning or throwing.
Simply storing a pointer/reference to the engine's mutable array lets that old
sink corrupt another turn after the engine clears and reuses its buffer.

Give every callback invocation a revocable sink lease. Revoke it in a guaranteed
cleanup path on success, callback failure, validation failure, and panic/exception
containment. Every operation on an expired sink must fail without reading or
mutating current engine storage. A fresh invocation gets a fresh lease; reopening
an old sink or resetting its generation must not revive escaped aliases. Nested
wrapper dispatch must preserve the existing append position and invalidate each
sub-callback's escaped view when that callback exits. Do not invalidate an outer
callback's still-active legitimate lease accidentally.

Expose immutable snapshots or read-only values for inspection, never the mutable
backing array/slice or replaceable entries. Native checks may have mutable runtime
representations even when statically readonly: validate/copy appended values and
prevent later caller mutation from changing recorded evidence. An inspection view
that aliases a reusable buffer must not outlive its lease; detached snapshots are
the simpler safe contract. Preserve detached observer batches and traces before
reusing storage. Go slices need ownership tests, TypeScript `readonly` alone is
not runtime enforcement, and Swift copy-on-write is not proof for nested reference
payloads or escaped sink objects.

Callbacks remain synchronous. Retaining a sink for asynchronous or concurrent
use is invalid. Document confinement and make expired use fail deterministically;
do not advertise thread safety merely because lease checks exist. If supported
concurrency is added, its synchronization and race semantics need their own scope.

### Native validation failures must remain failures

Rust's typed `Check` and status variants cannot supply a malformed runtime check
to `CheckSink`; its `push` has no validation-error return. Native APIs accepting
runtime values need an explicit additional fail-closed contract. Retain the
first validation failure in shared invocation state, including across nested
wrapper leases. Ignoring a Go append error or catching a TypeScript/Python/Swift
append exception must not let the callback return a passing batch. Inspect that
failure state after the callback returns successfully as well as on its error
path; later valid appends cannot erase it or bless the invalid batch.

Retain only successfully accepted observations preceding the first malformed
value for search error diagnostics. Ordinary observed-check-into clears its
caller result on this native validation failure. A fresh callback invocation
may begin with fresh validation state, but an existing or expired lease must not
reset a failed invocation. This native guarantee is separate from Rust's typed
append behavior and from the bounded encoder's own error rules.

## Qualification requirements

- Run legacy-only models through every engine path and compare existing traces,
  failures, counters and callback order. Add models whose legacy methods fail if
  called, proving that present append hooks are actually selected in initial and
  transition checks for BFS, fuzz, shrink, record, replay and observed checking.
- Verify state-before-transition and actual-before-oracle/reference-before-
  projection order. Include absent hooks, empty batches, duplicate check IDs,
  passed/failed/skipped values, lazy iterable failure, malformed late values,
  Go prefix-plus-error results, Swift append-then-throw, and nested wrapper errors.
- Test search error notifications against real emitted prefixes and completeness
  flags. Confirm both notifications occur after a transition-check error even if
  the first notification fails; retain checker error precedence. Test observed-
  into clearing on invalid sequence and returned checker errors. Separately test
  the documented native extensions for invalid configuration, snapshot failure,
  malformed append and contained panic/exception. Include ignored append errors,
  caught append exceptions, and later valid appends after a malformed value;
  none may convert the invalid batch into success. Rust unwind cleanup is not
  claimed. Never repeat a reducer or checker.
- Capture a sink and attempt push/extend/inspection/reserve after normal return,
  exception, and subsequent buffer reuse. Capture nested oracle/reference sinks.
  All expired operations must fail without changing any prior or later evidence.
  Mutate original check objects, returned inspection snapshots, observer batches,
  legacy result arrays and caller buffers to verify the documented ownership.
- Exercise backing-storage reuse across repeated turns, changing batch sizes,
  skipped policies and errors. Demonstrate logical clearing and capacity reuse
  where the runtime exposes meaningful capacity; Python/JavaScript lists have no
  portable reserve guarantee. A sink API alone proves neither zero allocation nor
  a hard memory cap. Allocation/performance claims require measured evidence and
  must account for snapshots, validations, check strings and legacy defaults.
- Add Rust-reference fixtures for semantic ordering/error boundaries and qualify
  actual installed package consumers for the new public APIs. Run executable
  checks through Kyoto, retain exact source/command receipts, and distinguish
  compilation from test execution. Do not promote broader N01 or performance
  parity from this bounded slice alone.
