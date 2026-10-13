# Indexed lifecycle and finite networks

`lifecycle::indexed` and `network` are additive experimental Rust APIs. They do
not change the legacy `History`, `LifecycleMonitor`, `Pair`, or their encodings.
Regression suites are in `tests/lifecycle_indexed.rs` and `tests/network.rs`.
The local task permits compilation only: test execution and performance
qualification remain pending. No speedup or new qualified portable profile is
claimed here.

## Lifecycle identity and retirement protocol

`ScopedKey { epoch, generation, key }` is the full identity. Indexed obligations
and resources use deterministic ordered maps. Their codec emits strict key order
and rejects duplicate/out-of-order entries, details below the retirement fence,
and published-but-unsettled records. Histories include logical time, the fence,
all retained records, and structured failures; restart never reconstructs history
from application state.

`IndexedEvent::Fence { before }` declares a **model assumption**: no future
legitimate event may reference an epoch less than `before`. A protocol can justify
that assumption with a barrier acknowledged by every possible sender, or an
explicitly modeled transport generation fence. Merely finishing, cancelling,
waiting, or receiving one worker's acknowledgment does not establish it.

The monitor validates the fence is monotonic, affected obligations are settled
or cancelled, and affected resources are released. It then removes their detailed
records and retains a single monotonic watermark. Every subsequent old-epoch
reference is recorded as `retired_epoch_reference`, even a key never previously
observed. Thus late duplicate settlements and stale publications cannot become
apparently fresh operations. Detailed pre-fence duplicate classifications are
replaced by the stronger protocol rejection. The monitor cannot determine whether
the application honestly justified its declared fence.

Violations retain key, epoch, generation, event kind, logical time, and the prior
obligation including deadline. They survive retirement. This bounds retained
history on failure-free, regularly fenced executions with bounded live work;
it does not bound an arbitrary failing or unfenced execution indefinitely.
Quota exhaustion returns `ModelError` with `verification incomplete`; it never
evicts evidence. Retention diagnostics distinguish live and historical records
and expose the oldest unresolved deadline. A cancelled operation with a still
acquired resource blocks retirement, covering remote work continuing after local
cancellation.

Within non-retired epochs, the new monitor preserves legacy safety violations and
deadline inclusivity. It additionally prohibits duplicate publication for one
logical operation. The differential suite checks legacy violation preservation,
not byte equality or identical failure messages between different history types.
The fenced long-history case compares against an unretired baseline only within
the declared protocol; below-fence events are deliberately outside that scope.

## Network semantics

`Network::new` accepts a finite registry of stable numeric `MachineId`s, a typed
`Model` family, application wiring, and an immutable `QueuePolicy`. The shared
family may be an enum whose reducer validates role/state/input combinations for
heterogeneous components. Registry insertion order is irrelevant; sorted IDs,
child semantic identities, wiring identity, and every queue option appear in
model identity. Renumbering IDs or changing queue semantics invalidates replay
identity even when a caller permits build mismatch.

Every modeled action has one atomic boundary:

1. `Local` executes one addressed child.
2. `Deliver(index)` removes one chosen message and executes its destination child.
3. Child outputs remain observable in their original order. Wiring appends routes
   in output order, then route order. Newly queued messages wait for future inputs.
4. `Drop(index)` removes one message without executing a child.
5. `Duplicate(index)` appends one exact copy without executing a child.

Drop and duplicate are disabled unless explicitly enabled. FIFO constrains each
`(source, destination, channel)` independently; unordered mode exposes every
pending index. Dropping a later message is still allowed under FIFO when loss is
modeled. Duplication appends to its channel. Retry, timeout, cancellation, and
external completion are application input variants delivered with `Local` or
routed messages; there is no hidden clock or retry loop.

Global capacity, per-channel capacity, and route fanout are explicit bounds.
Exceeding a bound returns an incomplete error and publishes no transition;
the borrowed starting state remains intact. A bound is not silently interpreted
as a dropped output, rejected child input, or successful backpressure handshake.
Backpressure protocols must model request/credit/acknowledgment messages in their
children. Callback allocation before a route vector returns is application-owned;
these bounds constrain accepted model state, not arbitrary callback memory.

Checks run in stable machine-ID order, followed by global wiring checks, with
`machine/<id>::` and `global::` namespaces. The selected child's transition checks
run before global transition checks; queue-only actions run global checks. An
outer `WithOracle` adds independent cross-machine history. Pending queue order is
part of state equality and exact encoding. No scheduling reduction or automatic
queue draining is used.

The included three-machine handoff has a coordinator, current owner, and next
owner. The faulty coordinator queues a revoke and grant together. Delivering the
grant first produces two owners despite all local state checks passing. A separate
oracle derives ownership from release/claim effects and detects the same defect.
The corrected protocol waits for the previous owner's release acknowledgment
before granting ownership. Its finite graph test explores all declared inputs and
round-trips every reached state. Other network protocols need their own fixtures,
assumptions, and qualification; this fixture is not a distributed-systems proof.
