# Guided exploration in Go

`GuidedFuzz`, `GuidedFuzzWithLimits`, and `GuidedFuzzObserved` add
`feature-corpus-v2` feedback to the ordinary fuzz execution kernel. Use explicit
`DefaultFuzzConfig()` and `DefaultGuidanceConfig()` values when defaults are wanted;
zero fuzz budgets retain their ordinary meanings, and zero guidance limits are
invalid. The result is `FuzzReport[I]` with a non-nil `Guidance` on every successful
guided return, including cancellation, deadline, and property failure. Ordinary
fuzz leaves that field nil. Callback errors return a nil report and an error.

See [the runnable example](examples/guided/main.go). The package requires no
codec, assurance object, worker, or campaign to use guidance.

`Feedback[S,I,O,K]` supplies `Metadata`, optional `InitialFeatures`, and mandatory
`TransitionFeatures`. An absent initial hook emits nothing. `StateFeedback`
creates both initial and transition hooks from a state projection;
`ExactStateFeedback(model)` uses full cloned state, `EqualStates`, and `HashState`.
Its identity is `exact-state`, version 1, build `clone-eq-hash-v1`. History included
in the model state is included in exact feedback. A projection can deliberately
omit history for novelty while all checks still run against the complete state.
Feedback equality never changes model equality, replay, or state pruning.

`FeatureKey[K]` explicitly defines `Clone`, `Equal`, and optional `Hash`. `K` is
unconstrained: slices, maps, and reference-bearing records work with faithful
deep copies and lawful equality. Nil Hash uses a single equality bucket; hash
collisions always resolve through Equal. Equal must be an equivalence relation,
and equal keys must hash equally. Clone must preserve identity while detaching
all mutable storage. `ValueCopy` is appropriate only for immutable/value types.
The package cannot verify that an application clone is faithful or detached.

Every Push snapshots its key immediately. Hash and equality receive detached
copies of keys; feedback receives detached state, input, successor, and outputs.
Retained prefixes, selected candidates, and returned corpus entries own separate
input snapshots. Hooks must remain deterministic, with stable emission order and
bounded application work. Hook side effects outside those snapshots are the
application's responsibility.

`FeatureBuffer.Push` is the only emission operation. Duplicate pushes count
toward the per-observation limit. A sink overrun or failed snapshot stays sticky
if ignored. A callback's own returned error or recovered panic takes precedence;
no partial keys from a failed callback are learned. Each callback has a fresh
lease, revoked on return, error, or panic, before equality/hash evaluation.
Copying the public buffer copies the lease, not its lifetime. Expired and zero
buffers reject writes. Reentrant Push from a key clone is rejected. Callbacks
and buffers are synchronous and exclusive: do not use a buffer concurrently or
start asynchronous work with it. Go's static callback signatures have no awaitable
return type. Recoverable panics use the existing ModelError boundary, including
observer callbacks; this Go policy differs from Rust's bare observer unwind.
Fatal runtime/process failures are outside that boundary. No host destructor or
async cleanup protocol is invoked.

Admission validates guidance, fuzz numeric configuration, required model/key/
feedback hooks, and control configuration before metadata. These structural hook
checks are Go API requirements. Metadata runs once even for a pre-cancelled or
zero-duration call; its time counts against the deadline. Empty metadata name,
version zero, and empty build are accepted, matching the bare Rust surface. No
model metadata callback is invoked. Cancellation wins over deadline. Blocking
callbacks are cooperative and cannot be preempted.

Initial checks and observers precede initial feedback. State checks, transition
checks, and the transition observer precede failure extraction and transition
feedback. A failed property teaches no feature. A checker error supersedes
earlier failed checks and preserves the existing partial `CheckError` observations.
Successful feedback learns before the following control poll, so cancellation
inside it retains its discovery. Feedback errors return no report even if the
callback also cancelled. Transition limits are tested at step entry, after case
initialization and candidate selection. `MaxSteps=0` still performs initial work.

Novel features admit whole prefixes in observation order. Empty initial prefixes
mark keys witnessed but never enter the corpus. Retention evicts oldest prefixes
until both entry and total-input counts fit; eviction leaves the keys witnessed.
Overlong novel prefixes increase `DroppedPrefixes` and leave their keys known
but unwitnessed. A later retainable witness can recover those keys with
`NewFeatures=0`, including when the feature limit is full. Saturation flags mean
an attempted excess, not merely filling a limit. Exhausting retention never stops
the search. Report counters are exact native int/uint64 counts; the existing
skipped-check counter saturates at uint64 maximum.

The corpus mutation policy and generation share the existing SplitMix64 stream,
including rejection sampling and singleton index draws. Empty corpus consumes no
mutation draws. A nonempty corpus consumes the percentage draw even at 0 percent.
Selection supports preserving a prefix, contiguous deletion plus one replacement,
or one replacement without deletion. Only that position is regenerated; later
enabled inputs can still be reused. Reused inputs undergo causal validation, and
generated inputs are validated once. Guided search polls controls after a
successful reused-input enabled result at the Rust boundary, including the
short-circuited result after generation selection. Ordinary Go retains its
existing polling boundaries. Generation exhaustion ends the current case.
Guided search does not clone a completed case into an unused previous-sequence
slot; its owned corpus is the only mutation source. Ordinary fuzz retains its
existing case-end snapshot and its snapshot-error boundary.

Guidance limits default to 256 corpus entries, 25,600 retained inputs, 100,000
known features, and 256 emissions per observation. They bound independent counts,
not bytes, allocator capacity, application callbacks, or RSS. Reports are evidence
of this finite run, not exhaustive coverage. A retained passing witness can be
replayed; only a property failure is a shrink target. There is no seeded-corpus,
checkpoint, or resume API. Returned corpus entries do not expose the hidden
witnessed bits or authorize reconstructing a resumed search.
