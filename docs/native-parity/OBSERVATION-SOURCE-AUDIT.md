# N07 observation and existing recorder source audit

Static source audit, 2026-10-22. Worktree:
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`; base:
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.
No build, compilation, test, implementation execution, or Kyoto submission was
performed. All integrated execution belongs to the parent's later frozen-source
qualification. A declaration or fixture is not runtime evidence.

## Source identity

SHA-256 values were read from the inspected files. Rust sources and existing
token/recorder sources are the base reference; concurrently authored native
observation files are draft integration inputs and need a new final source seal.

| Source | SHA-256 |
|---|---|
| `src/observation.rs` | `c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870` |
| `src/monitor.rs` | `d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac` |
| `src/execution.rs` | `acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0` |
| `src/model.rs` | `e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb` |
| `src/trace.rs` | `157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786` |
| `tests/observation.rs` | `842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d` |
| `tests/monitor.rs` | `c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f` |
| `tests/monitor_limits.rs` | `4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165` |
| `tests/monitor_review.rs` | `f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc` |
| `docs/NATIVE-PARITY-PLAN.md` | `8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301` |
| `docs/native-parity/CHECKED-RESULTS-AUDIT.md` | `03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec` |
| `docs/native-parity/semantic-symbols.jsonl` | `a400ebd6aaac0a2bad40c72b0586b5df614fa2dec8687427767e250e15469a12` |
| `python/src/statelessness/monitor.py` | `034e6f479504860d727a5354245ced7230675f04d10592530eba047eb9120493` |
| `python/src/statelessness/checked.py` | `5a450a24632d2f304d3714cccd1723654d568550d92d47f7ff38d7f8568e0b70` |
| `go/recorder.go` | `910f44bbc0e63830a4867efc57fa7cfc022a8140a050c5b9be5abfea4bee19d7` |
| `go/checked_results.go` | `9f9172b1587a490afb8bcf867b11a2789bc3ae0eb07899d0a4cb5f61d15832c5` |
| `typescript/src/runtime_recorder.ts` | `5aeda96d9d961d04632361b017d1ab841a1a3f260cf63c36c1a8be88bab7e732` |
| `typescript/src/checked.ts` | `ee3df47d3b03c09489343184aa2ce2917f29348119dd605b55003aee727ce45d` |
| `swift/Sources/Statelessness/Recorder.swift` | `459c44a770785540e60b0280e3a3f212a683fcbfccd41f6193b8bad790602ec9` |
| `swift/Sources/Statelessness/CheckedResults.swift` | `d2f92206250ddbc1368364c8c6cce4100074ce599e26bb722125e1d8c7dc2ccb` |

The historical [checked audit](CHECKED-RESULTS-AUDIT.md) predates native checked
tokens and recorder implementations. Its claims that those APIs are missing are
historical, not current. Preserve their actual implementations and consumers.

## Exact diagnostic behavior

[Observation](../../src/observation.rs) implements synchronous optional diagnostics:

- `TextObserver::observe` (line 371) checks sink health before Off and severity
  filtering. Off or filtered events invoke no payload formatter, codec, reducer,
  check, or sink write. Explicit `flush` is still a sink operation at Off.
- `check_level` (line 357) prioritizes Failed over Skipped, otherwise uses Info for
  lifecycle, Debug for transitions, Trace for checkpoints. A disposition alone
  does not change severity. The supplied check order and output order are retained.
- Configured Trace controls payloads and passed-check details, even when the
  emitted severity is Error or Warn. Input precedes outputs then selected post-state;
  before-state is not rendered. Explicit checkpoints ignore the periodic interval
  when snapshots are non-None. Sequence is application supplied; gaps, duplicates,
  and decreasing diagnostic sequence values confer no continuity guarantee.
- `NoPayloads` needs neither ModelCodec nor Debug. `EncodedPayloads` dispatches
  `encode_state_into/input_into/output_into`, reuses bounded storage, checks finish,
  and renders lowercase hex. Legacy codec temporary allocations remain outside
  its bound. Native declaration wrappers must forward the actual codec dispatch.
- The record byte bound includes LF and UTF-8 bytes. The private BoundedRecord
  failure is sticky; a swallowed rejected write is caught by the final LF or a
  subsequent write. Formatting/codec errors publish no current record and do not
  poison the sink. Sink write/flush errors may expose a prefix and poison forever.
- `get_ref` borrows the writer; `into_inner` transfers without flushing. Native
  ownership mechanisms must be explicit. Reference writers may remain caller
  aliases; no observer may claim exclusive native ownership it does not enforce.
- Rust uses Debug for strings, disposition, checks, metadata, config and optional
  sequence. The shared fixture freezes an explicit ASCII/common UTF-8 profile and
  the finite lifecycle layout. It does not assert stable arbitrary Rust Debug
  spelling or sanitize arbitrary custom formatter output. Custom output can
  contain newlines or non-UTF-8 bytes; single-line rendering is the formatter's job.
- Events and `TurnObservation` are diagnostics, not checked authority.
  `CheckedTurn::observation` is only a view; no logging path may rerun checking,
  reducer execution or application effects to enrich its diagnostics.

The [contract](../../spec/observation-v1.md) and
[36 hand-authored cases](../../conformance/observation-v1.json) cover the above
finite profile. Expected record text was independently transcribed from source
format strings, then encoded to UTF-8 hex. No Rust/native implementation produced
any golden. The [public Rust consumer](../../validation/corpus/src/bin/native-observation.rs)
implements `observe fixturePath` and `self-test fixturePath`. It calls the actual
TextObserver, payload formats and bounded codec override APIs, supplies executed
transition values directly, and traps any initializer/reducer/check/decoder call.
It checks per-record failure atomicity, public Off health probing, and extraction
without implicit flush. No expected bytes are recomputed from implementation.
An independent static consumer/fixture review identified a borrowed JSON check-ID
lifetime error; the consumer now owns those IDs before constructing Check values.
No other byte/callback/protocol mismatch was identified in that source review.
This correction has not been compiled or executed.

## Existing checks, recorder and proof reuse

Use the current public checking APIs: Python `check_initial/check_turn`, Go
`CheckInitial/CheckTurn`, TypeScript `checkInitial/checkTurnExact`, Swift
`checkInitial/checkTurn`. Preserve observed-check/into APIs and periodic policy;
TypeScript's full-u64 path uses bigint, while compatibility number APIs reject
unsafe integers. These functions are tagged N01 in the inventory but are N07
integration dependencies.

CheckedInitial binds a model, checkpoint and complete state-check batch.
CheckedTurn also binds the positive sequence, before/input/actual transition,
ordered checks, state-check boundary and coverage. Tokens certify checking
occurred, not that properties passed. State checks precede transition checks,
including after a failed property. Checker error yields no partial token.
Full coverage requires period exactly one and transition checks enabled; matching
a sampled sequence does not change that coverage claim.

The actual Rust [Recorder](../../src/monitor.rs) and all four native recorders exist.
They initialize from a supplied checkpoint, never execute reducers/effects, and
can reuse a sealed initial/turn batch without checking again. Ordinary observe
checks the supplied actual transition once. Diagnostic events must not substitute
for these tokens. One token may feed independent recorders; sequence enforcement
belongs to each recorder, not a global token-consumption bit.

Admission validates positive step/byte budgets, reserved parameter names, metadata
identity, canonical before-state continuity, and checked sequence
`origin + observed_steps + 1` without overflow. Identity is metadata/canonical
bytes, not necessarily model pointer or raw representation identity. Candidate
encoding and planned eviction are atomic. Evicted checkpoint checks include only
that turn's state-check prefix. Local observed/evicted/checkpoint counters remain
separate from absolute sequence origin. Retained-suffix replay makes no claim
about evicted history.

First committed property failure or recording error freezes the artifact.
Capture failure on a property-failing candidate is recording ModelError, not
evidence that the uncaptured failure was recorded. `stop_with_error` cannot
overwrite prior termination. Active export means Interrupted. Retained-byte
accounting measures serialized evidence plus admission reserves, not process RSS,
callback allocation or backing capacity.

Reuse the existing [checked-results spec](../../spec/checked-results-v1.md),
shared token/recorder cases, installed consumers and fresh-process trace replay.
The three monitor test files contain 24 inspected tests, not executed here:
long retention, byte/multiple eviction, continuity, actual-output divergence,
failure freezing, encode quotas, counter growth and UTF-8 footer truncation.
The concrete existing sources are [observation tests](../../tests/observation.rs),
[monitor tests](../../tests/monitor.rs), [limit tests](../../tests/monitor_limits.rs),
and [monitor review regressions](../../tests/monitor_review.rs). Their existing
check-once/logging and no-codec/no-Debug coverage is reused rather than duplicated.

Preserve native ownership differences: Python/TS reject after trace transfer;
Swift prohibits another transfer but allows later read-only inspection; Go
returns detached trace evidence and freezes capture while remaining inspectable.
Preserve EvidenceRecorder's existing retention coordination, including Python
`_observe_checked_retaining/_retained_output_count`, Go
`observeCheckedRecorderRetaining/retainedOutputCount`, TypeScript
`observeCheckedRetaining/retainedOutputCount`, and Swift equivalents.

## Native API mapping families

Rows in the complete symbol ledger below resolve through these families. Rust
trait glue maps to language mechanisms with explicit semantic obligations; it is
not an independent new subsystem. Native observation names refer to concurrent
draft source inspected on 2026-10-22 and still need the parent's final export,
ownership and runtime qualification. No draft API presence closes N07.

**Modules:** Rust `monitor/observation` map to existing recorder/checked sources
and new observation source. Native public roots are `statelessness` (Python),
`statelessness` (Go), `typescript/src/index.ts`, and `Statelessness` (Swift).

**Recorder:** Python [monitor.py](../../python/src/statelessness/monitor.py)
`Recorder` uses snake-case names. Go [recorder.go](../../go/recorder.go)
`Recorder` uses exported CamelCase names; construction is `NewRecorder`,
`NewRecorderWithOptions`, `NewRecorderWithCheckedInitial`,
`NewRecorderWithCheckedInitialAt`; observing uses free functions
`ObserveRecorder/ObserveCheckedRecorder`. TypeScript
[runtime_recorder.ts](../../typescript/src/runtime_recorder.ts) uses `Recorder`,
constructor options, `withCheckedInitial/withCheckedInitialAt` and lower-camel
methods. Swift [Recorder.swift](../../swift/Sources/Statelessness/Recorder.swift)
uses model/checkedInitial initializers, `sequenceOrigin:`, lower-camel methods
and properties. `write_with_limits` maps to `writeWithLimits(_:to:)`.
RecorderOptions fields map snake_case -> Go UpperCamel -> TS/Swift lowerCamel.
Defaults are Python/Swift `RecorderOptions()`, Go `DefaultRecorderOptions()`,
TS constructor options `{}`. Clone uses frozen/value/copy mechanisms, with
explicit nested limits copies where needed. Rust Debug formatting of options
has no promised byte-equivalent TS/Swift API and remains an explicit disposition.

**CheckedInitial/CheckedTurn/TurnObservation:** Python
[checked.py](../../python/src/statelessness/checked.py), Go
[checked_results.go](../../go/checked_results.go), TypeScript
[checked.ts](../../typescript/src/checked.ts), Swift
[CheckedResults.swift](../../swift/Sources/Statelessness/CheckedResults.swift)
already expose the corresponding named types. Python accessors are snake-case
properties; Go exported CamelCase methods; TS/Swift lower-camel properties.
`into_checks/observation` map to Python same-name methods, Go
`IntoChecks/Observation`, TS/Swift `intoChecks/observation`.
TurnObservation fields follow the same case conversions; these are constructible
diagnostics only. Native payload getters detach mutable data and may fail
(Go/Swift); Rust borrows under Model's determinism contract. Native intoChecks
does not globally consume a token. Preserve the existing integrity conventions.

**DebugObservation:** Dedicated public initial/turn sum: Python
`DebugObservation.Initial(state, checks)` / `.Turn(observation)` (also
`DebugInitial` / `DebugTurn`); Go `DebugObservation[S,I,O]` with exported
`DebugInitial.State/Checks` and `DebugTurn.Observation`; TypeScript discriminated
`DebugObservation<S,I,O>` (`kind: initial` with `state/checks`, `kind: turn` with
`observation`); Swift `DebugObservation.initial(state:checks:)` /
`.turn(TurnObservation)`. Payloads follow native ownership; no model callback,
checking or effect is executed. These values are not checked Recorder tokens.
The sum, Initial, its state/check members, Turn and its payload map all six rows.

**LogLevel:** New Python `LogLevel.OFF/ERROR/WARN/INFO/DEBUG/TRACE` (title aliases),
Go `LogOff/LogError/LogWarn/LogInfo/LogDebug/LogTrace`, TS
`LogLevel.Off/Error/Warn/Info/Debug/Trace`, Swift
`LogLevel.off/error/warn/info/debug/trace`. Ordered 0..5 values implement
comparison/equality; Copy/Clone use native value/immutable enum semantics.
Default is Off. Rust Debug labels used by the fixture are exact profile text;
native arbitrary enum repr/debug strings are not an equality claim.

**SnapshotPolicy:** Python `NONE/EVERY_TRANSITION/every`; Go
`SnapshotNone/SnapshotEveryTransition/SnapshotEvery`; TS
`None/EveryTransition/Every`; Swift `none/everyTransition/every`.
Every carries a positive exact u64, compared structurally including kind.
Every(1) and EveryTransition render the same selected transitions but are
distinct policy variants. Default is None; native Copy/Clone/value equality
must preserve the immutable policy. Rust Debug of this enum is outside the
shared record profile; native formatting remains a language disposition.

**ObservationOptions:** Python frozen dataclass, Go struct plus
`DefaultObservationOptions()`, TS interface plus `DEFAULT_OBSERVATION_OPTIONS`,
Swift struct initializer. Fields preserve level/snapshots/byte-bound semantics
with native naming. Clone/copy must preserve values without shared mutable
configuration surprises. General Debug equivalence is explicitly open/incidental.

**Event:** Python `Event.Started/Checkpoint/Transition/Message/Finished`
(classes `Started/Checkpoint/TransitionEvent/Message/Finished`), Go
`Event` plus `EventKind`, TS discriminated `Event`, Swift `Event` cases.
Every member maps to its corresponding native field with native casing; supplied
metadata/config/checks/input/before/transition/sequence/count/reason remain
application data. Positive transition sequences are represented by native
validation (Swift `ObservationSequence`); optional sequence preserves None vs zero.

**Observer/PayloadFormat:** Python protocols, Go interfaces, TS interfaces, Swift
protocols with associated types. Observe and state/input/output callbacks map to
snake-case Python, UpperCamel Go, lower-camel TS/Swift. Public formatting accepts
callback-scoped writable bytes, independently of codec availability.

**NoPayloads/EncodedPayloads:** Existing Rust implementations and new same-named
native formatters. Go encoded constructor is `NewEncodedPayloads`; other native
constructors use the type name. NoPayloads defaults/initializers have no payload
requirements. EncodedPayloads must use real bounded codec dispatch, including
declaration wrappers, rather than encode supplied fixture bytes itself.

**TextObserver:** New same-named type in
[Python](../../python/src/statelessness/observation.py),
[Go](../../go/observation.go), [TypeScript](../../typescript/src/observation.ts),
[Swift](../../swift/Sources/Statelessness/Observation.swift).
Python methods match Rust snake-case; Go constructor is `NewTextObserver` and
methods use UpperCamel; TS/Swift constructors and methods use lower-camel.
`set_level/set_snapshots/get_ref/into_inner/flush/observe` map accordingly.
Rust consuming ownership is enforced by native consumed-state guards where
needed. getRef of a Swift value writer returns a value copy, a reference writer
can remain aliased. intoInner never flushes/closes. Native reentrancy, escaped
callback leases and exception handling need focused tests beyond Rust Result
semantics; they are native extensions, not a claim that Rust catches panics.

## Complete N07 semantic-symbol ledger

All 162 records tagged N07 in the pinned semantic inventory are enumerated below.
IDs retain their exact inventory identity. Family names resolve to the concrete
public APIs or explicit open behavior above. This is an API disposition ledger,
not 162 passing tests. Parent may refine final per-language mappings without
discarding the open distinctions.

| ID | Rust exported semantic symbol | Mapping / disposition |
|---|---|---|
| `statelessness:4550` | `<stateless::monitor::RecorderOptions as Clone>::clone` | Recorder (options); native value mechanism |
| `statelessness:4551` | `<stateless::monitor::RecorderOptions as Clone>::{impl}` | Recorder (options); native value mechanism |
| `statelessness:4552` | `<stateless::monitor::RecorderOptions as Debug>::fmt` | Recorder (options); Debug disposition |
| `statelessness:4553` | `<stateless::monitor::RecorderOptions as Debug>::{impl}` | Recorder (options); Debug disposition |
| `statelessness:4554` | `<stateless::monitor::RecorderOptions as Default>::default` | Recorder (options) |
| `statelessness:4555` | `<stateless::monitor::RecorderOptions as Default>::{impl}` | Recorder (options) |
| `statelessness:5192` | `<stateless::observation::EncodedPayloads as PayloadFormat>::input` | EncodedPayloads |
| `statelessness:5193` | `<stateless::observation::EncodedPayloads as PayloadFormat>::output` | EncodedPayloads |
| `statelessness:5191` | `<stateless::observation::EncodedPayloads as PayloadFormat>::state` | EncodedPayloads |
| `statelessness:5194` | `<stateless::observation::EncodedPayloads as PayloadFormat>::{impl}` | EncodedPayloads |
| `statelessness:5066` | `<stateless::observation::LogLevel as Clone>::clone` | LogLevel; native value mechanism |
| `statelessness:5067` | `<stateless::observation::LogLevel as Clone>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5068` | `<stateless::observation::LogLevel as Copy>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5069` | `<stateless::observation::LogLevel as Debug>::fmt` | LogLevel; Debug disposition |
| `statelessness:5070` | `<stateless::observation::LogLevel as Debug>::{impl}` | LogLevel; Debug disposition |
| `statelessness:5071` | `<stateless::observation::LogLevel as Default>::default` | LogLevel |
| `statelessness:5072` | `<stateless::observation::LogLevel as Default>::{impl}` | LogLevel |
| `statelessness:5076` | `<stateless::observation::LogLevel as Eq>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5079` | `<stateless::observation::LogLevel as Ord>::cmp` | LogLevel; native value mechanism |
| `statelessness:5080` | `<stateless::observation::LogLevel as Ord>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5074` | `<stateless::observation::LogLevel as PartialEq>::eq` | LogLevel; native value mechanism |
| `statelessness:5075` | `<stateless::observation::LogLevel as PartialEq>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5077` | `<stateless::observation::LogLevel as PartialOrd>::partial_cmp` | LogLevel; native value mechanism |
| `statelessness:5078` | `<stateless::observation::LogLevel as PartialOrd>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5073` | `<stateless::observation::LogLevel as StructuralPartialEq>::{impl}` | LogLevel; native value mechanism |
| `statelessness:5210` | `<stateless::observation::NoPayloads as Default>::default` | NoPayloads |
| `statelessness:5211` | `<stateless::observation::NoPayloads as Default>::{impl}` | NoPayloads |
| `statelessness:5187` | `<stateless::observation::NoPayloads as PayloadFormat>::input` | NoPayloads |
| `statelessness:5188` | `<stateless::observation::NoPayloads as PayloadFormat>::output` | NoPayloads |
| `statelessness:5186` | `<stateless::observation::NoPayloads as PayloadFormat>::state` | NoPayloads |
| `statelessness:5189` | `<stateless::observation::NoPayloads as PayloadFormat>::{impl}` | NoPayloads |
| `statelessness:5136` | `<stateless::observation::ObservationOptions as Clone>::clone` | ObservationOptions; native value mechanism |
| `statelessness:5137` | `<stateless::observation::ObservationOptions as Clone>::{impl}` | ObservationOptions; native value mechanism |
| `statelessness:5138` | `<stateless::observation::ObservationOptions as Debug>::fmt` | ObservationOptions; Debug disposition |
| `statelessness:5139` | `<stateless::observation::ObservationOptions as Debug>::{impl}` | ObservationOptions; Debug disposition |
| `statelessness:5140` | `<stateless::observation::ObservationOptions as Default>::default` | ObservationOptions |
| `statelessness:5141` | `<stateless::observation::ObservationOptions as Default>::{impl}` | ObservationOptions |
| `statelessness:5105` | `<stateless::observation::SnapshotPolicy as Clone>::clone` | SnapshotPolicy; native value mechanism |
| `statelessness:5106` | `<stateless::observation::SnapshotPolicy as Clone>::{impl}` | SnapshotPolicy; native value mechanism |
| `statelessness:5107` | `<stateless::observation::SnapshotPolicy as Copy>::{impl}` | SnapshotPolicy; native value mechanism |
| `statelessness:5108` | `<stateless::observation::SnapshotPolicy as Debug>::fmt` | SnapshotPolicy; Debug disposition |
| `statelessness:5109` | `<stateless::observation::SnapshotPolicy as Debug>::{impl}` | SnapshotPolicy; Debug disposition |
| `statelessness:5110` | `<stateless::observation::SnapshotPolicy as Default>::default` | SnapshotPolicy |
| `statelessness:5111` | `<stateless::observation::SnapshotPolicy as Default>::{impl}` | SnapshotPolicy |
| `statelessness:5115` | `<stateless::observation::SnapshotPolicy as Eq>::{impl}` | SnapshotPolicy; native value mechanism |
| `statelessness:5113` | `<stateless::observation::SnapshotPolicy as PartialEq>::eq` | SnapshotPolicy; native value mechanism |
| `statelessness:5114` | `<stateless::observation::SnapshotPolicy as PartialEq>::{impl}` | SnapshotPolicy; native value mechanism |
| `statelessness:5112` | `<stateless::observation::SnapshotPolicy as StructuralPartialEq>::{impl}` | SnapshotPolicy; native value mechanism |
| `statelessness:5179` | `<stateless::observation::TextObserver as Observer>::observe` | TextObserver |
| `statelessness:5180` | `<stateless::observation::TextObserver as Observer>::{impl}` | TextObserver |
| `statelessness:4530` | `stateless::monitor` | Modules |
| `statelessness:1881` | `stateless::monitor::Recorder` | Recorder |
| `statelessness:4617` | `stateless::monitor::Recorder::evicted_steps` | Recorder |
| `statelessness:4609` | `stateless::monitor::Recorder::into_trace` | Recorder |
| `statelessness:4618` | `stateless::monitor::Recorder::is_frozen` | Recorder |
| `statelessness:4592` | `stateless::monitor::Recorder::new` | Recorder |
| `statelessness:4598` | `stateless::monitor::Recorder::observe` | Recorder |
| `statelessness:4600` | `stateless::monitor::Recorder::observe_checked` | Recorder |
| `statelessness:4616` | `stateless::monitor::Recorder::observed_steps` | Recorder |
| `statelessness:4613` | `stateless::monitor::Recorder::retained_bytes` | Recorder |
| `statelessness:4614` | `stateless::monitor::Recorder::retained_steps` | Recorder |
| `statelessness:4608` | `stateless::monitor::Recorder::snapshot` | Recorder |
| `statelessness:4606` | `stateless::monitor::Recorder::stop_with_error` | Recorder |
| `statelessness:4595` | `stateless::monitor::Recorder::with_checked_initial` | Recorder |
| `statelessness:4596` | `stateless::monitor::Recorder::with_checked_initial_at` | Recorder |
| `statelessness:4593` | `stateless::monitor::Recorder::with_options` | Recorder |
| `statelessness:4610` | `stateless::monitor::Recorder::write_to` | Recorder |
| `statelessness:4612` | `stateless::monitor::Recorder::write_with_limits` | Recorder |
| `statelessness:4619` | `stateless::monitor::Recorder::{impl}` | Recorder |
| `statelessness:1892` | `stateless::monitor::RecorderOptions` | Recorder (options) |
| `statelessness:4533` | `stateless::monitor::RecorderOptions::limits` | Recorder (options) |
| `statelessness:4532` | `stateless::monitor::RecorderOptions::max_retained_bytes` | Recorder (options) |
| `statelessness:4531` | `stateless::monitor::RecorderOptions::max_steps` | Recorder (options) |
| `statelessness:5087` | `stateless::observation` | Modules |
| `statelessness:1890` | `stateless::observation::CheckedInitial` | CheckedInitial |
| `statelessness:4952` | `stateless::observation::CheckedInitial::checks` | CheckedInitial |
| `statelessness:4954` | `stateless::observation::CheckedInitial::into_checks` | CheckedInitial |
| `statelessness:4950` | `stateless::observation::CheckedInitial::model` | CheckedInitial |
| `statelessness:4951` | `stateless::observation::CheckedInitial::state` | CheckedInitial |
| `statelessness:4953` | `stateless::observation::CheckedInitial::state_check_count` | CheckedInitial |
| `statelessness:4955` | `stateless::observation::CheckedInitial::{impl}` | CheckedInitial |
| `statelessness:1894` | `stateless::observation::CheckedTurn` | CheckedTurn |
| `statelessness:4979` | `stateless::observation::CheckedTurn::before` | CheckedTurn |
| `statelessness:4983` | `stateless::observation::CheckedTurn::checks` | CheckedTurn |
| `statelessness:4985` | `stateless::observation::CheckedTurn::full_checking` | CheckedTurn |
| `statelessness:4980` | `stateless::observation::CheckedTurn::input` | CheckedTurn |
| `statelessness:4986` | `stateless::observation::CheckedTurn::into_checks` | CheckedTurn |
| `statelessness:4978` | `stateless::observation::CheckedTurn::model` | CheckedTurn |
| `statelessness:4987` | `stateless::observation::CheckedTurn::observation` | CheckedTurn |
| `statelessness:4982` | `stateless::observation::CheckedTurn::sequence` | CheckedTurn |
| `statelessness:4984` | `stateless::observation::CheckedTurn::state_check_count` | CheckedTurn |
| `statelessness:4981` | `stateless::observation::CheckedTurn::transition` | CheckedTurn |
| `statelessness:4988` | `stateless::observation::CheckedTurn::{impl}` | CheckedTurn |
| `statelessness:5028` | `stateless::observation::DebugObservation` | DebugObservation |
| `statelessness:5025` | `stateless::observation::DebugObservation::Initial` | DebugObservation |
| `statelessness:5024` | `stateless::observation::DebugObservation::Initial::checks` | DebugObservation |
| `statelessness:5023` | `stateless::observation::DebugObservation::Initial::state` | DebugObservation |
| `statelessness:5027` | `stateless::observation::DebugObservation::Turn` | DebugObservation |
| `statelessness:5026` | `stateless::observation::DebugObservation::Turn::0` | DebugObservation |
| `statelessness:5195` | `stateless::observation::EncodedPayloads` | EncodedPayloads |
| `statelessness:5214` | `stateless::observation::EncodedPayloads::new` | EncodedPayloads |
| `statelessness:5216` | `stateless::observation::EncodedPayloads::{impl}` | EncodedPayloads |
| `statelessness:5162` | `stateless::observation::Event` | Event |
| `statelessness:5148` | `stateless::observation::Event::Checkpoint` | Event |
| `statelessness:5147` | `stateless::observation::Event::Checkpoint::checks` | Event |
| `statelessness:5145` | `stateless::observation::Event::Checkpoint::sequence` | Event |
| `statelessness:5146` | `stateless::observation::Event::Checkpoint::state` | Event |
| `statelessness:5161` | `stateless::observation::Event::Finished` | Event |
| `statelessness:5160` | `stateless::observation::Event::Finished::reason` | Event |
| `statelessness:5159` | `stateless::observation::Event::Finished::transitions` | Event |
| `statelessness:5158` | `stateless::observation::Event::Message` | Event |
| `statelessness:5155` | `stateless::observation::Event::Message::level` | Event |
| `statelessness:5157` | `stateless::observation::Event::Message::message` | Event |
| `statelessness:5156` | `stateless::observation::Event::Message::sequence` | Event |
| `statelessness:5144` | `stateless::observation::Event::Started` | Event |
| `statelessness:5143` | `stateless::observation::Event::Started::config` | Event |
| `statelessness:5142` | `stateless::observation::Event::Started::metadata` | Event |
| `statelessness:5154` | `stateless::observation::Event::Transition` | Event |
| `statelessness:5150` | `stateless::observation::Event::Transition::before` | Event |
| `statelessness:5153` | `stateless::observation::Event::Transition::checks` | Event |
| `statelessness:5151` | `stateless::observation::Event::Transition::input` | Event |
| `statelessness:5149` | `stateless::observation::Event::Transition::sequence` | Event |
| `statelessness:5152` | `stateless::observation::Event::Transition::transition` | Event |
| `statelessness:5049` | `stateless::observation::LogLevel` | LogLevel |
| `statelessness:5047` | `stateless::observation::LogLevel::Debug` | LogLevel |
| `statelessness:5044` | `stateless::observation::LogLevel::Error` | LogLevel |
| `statelessness:5046` | `stateless::observation::LogLevel::Info` | LogLevel |
| `statelessness:5043` | `stateless::observation::LogLevel::Off` | LogLevel |
| `statelessness:5048` | `stateless::observation::LogLevel::Trace` | LogLevel |
| `statelessness:5045` | `stateless::observation::LogLevel::Warn` | LogLevel |
| `statelessness:5190` | `stateless::observation::NoPayloads` | NoPayloads |
| `statelessness:5119` | `stateless::observation::ObservationOptions` | ObservationOptions |
| `statelessness:5116` | `stateless::observation::ObservationOptions::level` | ObservationOptions |
| `statelessness:5118` | `stateless::observation::ObservationOptions::max_record_bytes` | ObservationOptions |
| `statelessness:5117` | `stateless::observation::ObservationOptions::snapshots` | ObservationOptions |
| `statelessness:5178` | `stateless::observation::Observer` | Observer |
| `statelessness:5177` | `stateless::observation::Observer::observe` | Observer |
| `statelessness:5181` | `stateless::observation::PayloadFormat` | PayloadFormat |
| `statelessness:5184` | `stateless::observation::PayloadFormat::input` | PayloadFormat |
| `statelessness:5185` | `stateless::observation::PayloadFormat::output` | PayloadFormat |
| `statelessness:5183` | `stateless::observation::PayloadFormat::state` | PayloadFormat |
| `statelessness:5085` | `stateless::observation::SnapshotPolicy` | SnapshotPolicy |
| `statelessness:5083` | `stateless::observation::SnapshotPolicy::Every` | SnapshotPolicy |
| `statelessness:5082` | `stateless::observation::SnapshotPolicy::Every::0` | SnapshotPolicy |
| `statelessness:5084` | `stateless::observation::SnapshotPolicy::EveryTransition` | SnapshotPolicy |
| `statelessness:5081` | `stateless::observation::SnapshotPolicy::None` | SnapshotPolicy |
| `statelessness:5088` | `stateless::observation::SnapshotPolicy::{impl}` | SnapshotPolicy |
| `statelessness:5182` | `stateless::observation::TextObserver` | TextObserver |
| `statelessness:5242` | `stateless::observation::TextObserver::flush` | TextObserver |
| `statelessness:5240` | `stateless::observation::TextObserver::get_ref` | TextObserver |
| `statelessness:5241` | `stateless::observation::TextObserver::into_inner` | TextObserver |
| `statelessness:5237` | `stateless::observation::TextObserver::new` | TextObserver |
| `statelessness:5238` | `stateless::observation::TextObserver::set_level` | TextObserver |
| `statelessness:5239` | `stateless::observation::TextObserver::set_snapshots` | TextObserver |
| `statelessness:5244` | `stateless::observation::TextObserver::{impl}` | TextObserver |
| `statelessness:2349` | `stateless::observation::TurnObservation` | TurnObservation |
| `statelessness:5004` | `stateless::observation::TurnObservation::before` | TurnObservation |
| `statelessness:5007` | `stateless::observation::TurnObservation::checks` | TurnObservation |
| `statelessness:5005` | `stateless::observation::TurnObservation::input` | TurnObservation |
| `statelessness:5003` | `stateless::observation::TurnObservation::sequence` | TurnObservation |
| `statelessness:5008` | `stateless::observation::TurnObservation::state_check_count` | TurnObservation |
| `statelessness:5006` | `stateless::observation::TurnObservation::transition` | TurnObservation |

## Review concerns and remaining qualification gates

The first two findings below describe the original audit snapshot. Parent
integration corrected both in `Recorder.swift`, and corrected the adjacent
STEP-item admission order in `ExecutionBudget.swift`; see
[Swift correction disposition](OBSERVATION-FIXES-SWIFT.md). Later review also
separated monitor wire-order admission from replay's isolated check-batch
preflight and recording's cumulative-item preflight; see the independently
reviewed [caller correction](OBSERVATION-FIXES-SWIFT-CALLERS.md). Their regression
sources are present. Source review and executable qualification are separate
from this disposition; no passing result is implied.

1. **Historical Swift recorder footer ordering: source-resolved.** Current
   `Recorder.swift` checks the reserved footer after metadata and before checkpoint
   encoding/checks. Native clone hooks remain an explicit ownership adaptation.
2. **Historical Swift output-limit attribution: source-resolved.** Current
   `Recorder.swift` separately checks outputs-per-step then aggregate items.
   `ObservationBudgetPrecedenceTests` exercises conflicting-limit precedence.
3. Compile and execute the new public Rust consumer through Kyoto only after
   integrated source freeze. Compare all five languages' normalized case entries;
   the language discriminator belongs only at the result root. Keep returned
   record_hex, callback order, first error stage and final poison independent.
4. Run current installed-package observation tests and existing checked/monitor
   suites, including no codec/Debug model, defaults, all level thresholds,
   positive/u64 boundaries, writer short/interrupted/zero progress, extraction,
   declaration binding and native callback failure/ownership conventions.
   The shared fixtures do not contain every native-only invalid-construction,
   reentrancy, alias, escaped-lease or UTF-8 invalid-byte case.
5. Compose logging with the existing real Recorder/check-once token path over a
   session exceeding retention. Assert unchanged checked batches/artifacts and
   no extra reducer/check/effect calls across log levels. Replay retained suffixes
   in fresh installed processes; cover failure freeze, zero/small capacity,
   checkpoint phase boundaries and absolute origins. Prior checked-slice receipts
   are historical reuse evidence, not qualification of this source integration.
6. Keep exact command/source/artifact hashes and requested/effective Kyoto commands.
   Compilation-only is not runtime passing. Scope any finite green result to the
   profile exercised; neither record fixtures nor existing recorder tests alone
   close full N07. The DebugObservation and incidental Debug dispositions above
   remain visible until resolved by the parent.
