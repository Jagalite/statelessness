# Native Python guided exploration

`guided_fuzz(model, feedback, config=FuzzConfig(), guidance=GuidanceConfig(),
limits=RunLimits(), observer=None)` runs the same checked search kernel as
ordinary `fuzz`, with the `feature-corpus-v2` candidate policy. `limits` and
`observer` are keyword-only. The result is a `FuzzReport` with a `GuidanceReport`
in `guidance`; ordinary fuzz keeps `guidance=None`. This candidate API does not
promote portable profiles or implement campaigns, assurance or corpus resume.

```python
from statelessness import FeedbackMetadata, FuzzConfig, StateFeedback, guided_fuzz

feedback = StateFeedback(FeedbackMetadata("phase", 1, "application-v1"),
                         lambda state: state.phase)
report = guided_fuzz(model, feedback, FuzzConfig(seed=42, cases=100))
for entry in report.guidance.corpus:
    print(entry.discovered_case, entry.discovered_transition, entry.inputs)
```

See [the runnable counter example](examples/guided.py). No codec is required for
search. Public `record`/`replay` can validate passing witnesses when the model
provides codecs. Public `shrink` accepts property failures, not passing corpus
entries. Feature novelty is neither exhaustive coverage nor state equivalence.

## Feedback and ownership

Implement `metadata()` and `transition_features(before, input, after, out)`.
`after` is the ordinary `Transition`, including outputs and disposition. An
optional `initial_features(state, out)` defaults to no emissions, both for
subclasses of `Feedback` and structural implementations. All emission callbacks
return `None` synchronously. `StateFeedback(metadata, project)` emits the
projection of every initial/successor state. `exact_state()` emits full cloned
states, including represented history, with identity
`exact-state/1/clone-eq-hash-v1`.

Feature keys use Python hashing and exact native equality, including equality
resolution of hash collisions. Keys must be hashable, deterministic and lawful:
equal keys have equal stable hashes, and equality is an equivalence relation.
Nonreflexive keys (such as NaN) and asynchronous hashing/equality are outside this
contract; the engine does not try to prove arbitrary key laws.
`exact_state()` therefore needs hashable states, as the Rust API needs `Hash`.
For lists or dictionaries, provide an explicit immutable application projection
with `StateFeedback`; the engine does not invent serialization-based equality.

Each callback receives detached `deepcopy` snapshots of model values.
`FeatureBuffer.push(key)` snapshots the key before retaining it, so later
mutation of a caller's key does not change its identity. Corpus admission and
candidate reuse copy inputs. Reports own their retained input values; modifying
one corpus entry does not modify another entry. Arbitrary custom copy/hash/equals
code must be faithful and pure. Python cannot enforce that contract, prevent
private reflection, bound callback allocations, or make concurrent mutation safe.

Feature sinks are append-only leases. Each push, including duplicates, counts
against the observation emission limit. The sink checks capacity before copying
an excess key. Overrun and ordinary snapshot/hash write errors remain sticky if
the callback catches them. A callback's own exception takes precedence over the
sticky write error; accepted keys from an unsuccessful callback never teach.
Copy and hash callbacks may not reenter the same sink: a nested push records a
sticky error, and the outer push rechecks that error and capacity before appending.
Hash/equality exceptions during feature lookup are contextual feedback errors and
produce no report. BaseException propagation and diagnostic-error handling below
also apply to these application hooks.

Every callback sink expires on success, `Exception` and `BaseException`, releasing
its reference to scratch storage. A retained sink cannot mutate later observations
or keep that reused storage alive. It exposes no mutable backing list or snapshot
accessor. Standalone `FeatureBuffer(maximum=256)` creates its own private storage;
search itself supplies callback-scoped instances.

## Python execution and errors

Feedback methods, metadata and state projections are synchronous. Awaitables,
coroutines and generators returned in place of their required values are rejected
without awaiting or iterating them. Returned native coroutines/generators are
closed; other awaitables and async generators are not scheduled or driven by the
engine. On an invalid emission callback return, the sink is revoked before lazy
result cleanup. Cleanup failure is secondary, available as `cleanup_error` on the
underlying error (`ModelError.__cause__` after contextual wrapping).

Ordinary Python `Exception` failures become contextual `ModelError`, with initial
or transition feedback context. `BaseException` such as `KeyboardInterrupt` and
`SystemExit` propagates after sink cleanup, even following a swallowed overrun.
There is no successful partial report on a callback error. This is a native Python
policy, not a claim that Rust `Result` provides Python exception handling.
Feedback-boundary errors whose `__str__` throws retain the original exception as
their cause and expose the formatting failure as `diagnostic_error`, without
replacing the primary failure. A callback's sink is revoked before formatting
its error; copy/hash diagnostics during a push remain under the reentrancy guard.
Existing
model, checker and search observer behavior is shared with ordinary fuzz: partial
checker batches reach `check_error`, and checker errors remain primary over an
ordinary observer error while reporting those batches. No async execution API,
thread safety or process-failure recovery is supplied.

## Limits, order and retention

`GuidanceConfig` defaults to 256 entries, 25,600 retained inputs, 100,000 tracked
features and 256 emissions per observation. Each must be a positive integer in
the Python engine's unsigned 64-bit count domain. These are four independent
count budgets, not byte or process-memory caps. Application callbacks, keys,
copies and Python container capacity are outside them. Discovery/drop/eviction
counters are exact within admitted budgets; skipped checks retain the ordinary
engine's saturating u64 accounting.

Guided admission captures `GuidanceConfig`, `FuzzConfig` and `RunLimits` into base
frozen dataclasses before calling feedback metadata. Counts, seed and duration are
validated scalar copies; the cancellation callable is retained by reference, as
intended for live cooperative cancellation. Rebinding a caller configuration does
not change the admitted run or its reported guidance limits. No application
`deepcopy` hook runs while capturing configurations. Ordinary fuzz retains its
existing configuration policy. Concurrent mutation and private reflection remain
outside the supported configuration API.

Guidance admission precedes fuzz configuration admission, then control timing and
one feedback metadata call. Metadata allows empty strings and version zero, and
its time counts against the deadline. Bare guided search never queries model
metadata. A pre-cancelled valid run still queries feedback metadata but executes
no model or feature callback. Cancellation/deadlines are cooperative and cannot
interrupt a callback.

Each case checks and observes its initial state before feedback. Each transition
executes state and transition checks, then observation, then failure extraction.
Property failures precede feature feedback; a transition checker error supersedes
an already emitted state property failure. Rejected/ignored transitions consume
the transition budget and undergo the same checks and feedback. Successful
feedback learns before the next cancellation poll; its own error wins over a
cancellation triggered inside it. Zero cases/steps/transitions retain the shared
kernel's ordering, including case setup before a step checks its transition cap.

An initial feature is witnessed without a corpus entry. Novel retainable prefixes
enter FIFO order, evicting oldest entries until both entry and total-input budgets
fit. Eviction leaves the tracked feature witnessed; merely rediscovering it does
not reinsert its old prefix. Novel overlong prefixes are dropped once per
observation and leave their tracked features unwitnessed. A later retainable
observation can recover such a feature at capacity, admitting `new_features=0`.
Repeated overlong observations of known keys add no drops. Saturation flags mean
attempted excess, not merely filling a limit, and saturation does not stop search.

Mutation uses the same SplitMix64/rejection-sampled index stream as generation.
Empty corpora consume no percent draw; nonempty corpora draw even at zero percent.
Selection chooses a corpus entry then preserves, deletes a contiguous segment, or
replaces one position. Replacement does not discard later candidate positions.
Every reuse is causally checked, and disabled reuse falls back to generation.
Generated `EXHAUSTED` ends the case; Python `None` remains a valid input.
