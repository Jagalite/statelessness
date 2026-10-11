# Live observation and cooperative host control

The `statelessness_debug::live` module implements the v1.1 M5/M6 boundaries.
These are application-linked APIs, not a network listener or a process debugger.
The host continues to own reducers, checking, oracle history, queues, effects and
monotonic clocks. Pausing input delivery does not pause workers or the outside world.

## Observation integration

1. At a complete application boundary, create an engine-sealed `CheckedInitial`
   using `execution::check_initial`. Its state must include the application's
   composite state, pending modeled work and property/oracle history.
2. Call `LiveBridge::attach` with the last completed host sequence, a nonzero
   host epoch, recorder options and `LiveLimits`.
3. Execute each actual reducer once. Create one `CheckedTurn` with `check_turn`
   and pass that same sealed token to `LiveBridge::observe`. Rejected and ignored
   inputs still advance host sequence. Neither attachment nor observation reruns
   the reducer or checker.
4. Keep the actual host result regardless of recording/diagnostic errors.
   A missing sequence freezes the recorder with its normal bounded error footer,
   including omissions whose state changes cancel out. Recorder/codec failures
   preserve the coherent prefix. `stop_recording` handles external checker/oracle
   errors for which no complete checked token exists.
5. `export` returns a raw, potentially sensitive exact replay artifact. A property
   failure freezes normal capture at its failing turn; the host may continue.
   Later diagnostic errors cannot overwrite that property-failure footer.

Model, property, codec and build identity changes are rejected even after capture
has frozen. Caller-supplied `stateless.debug.` parameters cannot overwrite live
provenance. Recorder indices are local to the attached checkpoint; the original
host sequence is retained in configuration metadata.

The metadata queue contains only counts, disposition classes, sequence/epoch IDs,
gap/freeze notifications and explicit dispatch facts. It contains no model values
or callback error strings. `pop_event` drains it; `health` reports event drops,
retention evictions, origin evictions, observed host position and exact-capture
health. Stream sequence advances even when an event is dropped. Diagnostic loss
never removes exact evidence or establishes that an effect ran.

`effect_dispatched(origin)` records a host fact. Requested outputs alone do not
establish dispatch, completion or settlement. Unknown/evicted origins and duplicate
dispatch facts are rejected. Origins are scoped to their attached host run/model;
this module does not infer idempotency from payload equality.

## Cooperative control integration

Construct `LiveController<I>` only through explicit host runtime opt-in. Its
immutable `ControllerOptions` separate pause/step and injection permissions. Both
are disabled by default. Host dispatch methods are not client command authority.
There is no live state replacement API.

The normal handshake is:

- `request_pause(epoch, revision)` requests a boundary
- Host `safe_point()` acknowledges a completed boundary with `PauseAck`
- `authorize_one(epoch, revision)` grants one input delivery, or `resume` removes
  the pause gate
- `begin_next()` removes at most one buffered input and returns its non-Clone,
  controller-bound `DeliveryPermit`
- Host reports the actual complete turn with `delivered(permit, output_count)`
- One-turn authorization returns to pause-requested after that delivery

A request racing an already executing turn takes effect after the real turn.
`terminate()` prevents further admission but allows an already admitted turn to
report its result. Host code uses this for checker/property stop policy; it never
retries the reducer to recover diagnostics. `delivery_failed` terminates an
unsuccessful in-flight host execution without manufacturing a completed turn.

The host chooses either a pre-dispatch or post-dispatch safe point. Pre-dispatch
acknowledgements report `EffectPhase::Staged`. `take_dispatch(index)` reserves an
output and returns a non-Clone, controller-bound `DispatchPermit`; reservation
reports `Dispatching`, not `Dispatched`. After the actual host action, call
`dispatched(permit)`. On uncertain/failed dispatch, call `dispatch_failed` to
terminate control. Dropping a reservation leaves work uncertain, blocks another
turn/safe-point acknowledgement/reconnect, and never authorizes a retry.

A client reconnect increments the command epoch, invalidating old commands. It
cannot increase authority, replace in-flight permits or redispatch consumed
outputs. `ControllerStatus::origin_epoch` and `DispatchPermit::origin()` keep the
stable host epoch so the same observation bridge continues correlating effects.
Commands additionally validate the current execution revision. Permit ownership
is bound to the individual controller, not just matching numeric IDs.

## Arrivals, detach and limits

- External arrivals remain host-owned on rejection: `ArrivalRejected<I>` returns
  both the error and input. Bounded queues enforce count and declared owned-byte
  estimates. Backpressure means the host must retry admission; no rejected input
  is silently delivered or discarded by the gate
- Overflow policy is explicit backpressure or disconnect. Detach policy is
  remain paused, resume, or terminate. Terminating during a turn still accounts
  for that already admitted result before shutdown
- `max_outputs` bounds dispatch bookkeeping. Oversized actual output batches
  terminate control; the host still owns its result
- Live event count/bytes and effect-origin count are bounded. A huge output batch
  retains only its bounded origin tail in O(origin capacity) diagnostic work
- Check summaries inspect at most the recorder's configured check count and
  label omitted checks/coverage. Exact evidence uses separate recorder limits
- Byte budgets count retained records or host-declared payload estimates, not
  spare allocator capacity, arbitrary input internals or callback allocations.
  A blocking codec/reducer/checker cannot be preempted by these APIs

The bridge's exact-capture completeness flag is independent of event loss and
recorder freeze: a terminal property failure can freeze a complete valid prefix.
Applications must review raw trace privacy independently of metadata-only viewing.

## Pause timing provenance

`PauseAck` carries host execution revision, command epoch, effect phase and a
monotonic acknowledgement generation. Associate host-owned monotonic timestamps
with that generation and end the interval when `resume` or `authorize_one`
succeeds. Repeated inspection of an existing pause does not create another
interval. A cancelled one-turn authorization requires a fresh safe point.

The controller does not read a clock or synthesize durations. Replaying its trace
cannot produce fresh production effect measurements. Host telemetry should flag
pause-affected measurements rather than treating them as ordinary production
latency or subtracting overlapping intervals blindly.

## Qualification and runnable example

Run with the repository's qualified Rust toolchain:

```sh
cargo test -p statelessness-debug --test live --offline -- --nocapture
cargo run -p statelessness-debug --example live_host --offline
```

The 23 integration tests cover coherent mid-run/replayed suffixes, one reducer and
checker pass, omitted same-state roundtrips, first-failure freeze with continued
host execution, retention/event/origin evictions, sampled-check rejection,
identity/provenance rejection, bounded huge zero-sized output batches, bounded
error footers, stale epochs/revisions, cross-controller permits, every detach and
overflow policy, in-flight races, dispatch ownership across reconnect, authority
non-escalation, counter exhaustion and pause correlation.

One test uses Statelessness exhaustive exploration to compare every edge against
a separately represented reference controller. All **44,100 reachable states and
732,924 edges** exhaust across **24 configurations**: three detach policies, two
overflow policies and four authority combinations. Its finite scope allows two
completed deliveries, one reconnect, generation-bounded explicit pause admission,
and all enumerated arrival, overflow, stepping, dispatch, failure, termination and
stale-command interleavings. Witness histories are excluded from state identity;
cycles close on exact reference state. This is bounded model evidence, not proof
of arbitrary host callbacks or operating-system scheduling.

The `live_host` example executes three real synthetic host turns and two explicit
host dispatches, then verifies all three trace turns without dispatching effects.
It shows how to preserve an actual result when checking/recording fails and how
to separate the host's execution from capture and cooperative control.

### Combined host telemetry example

`live_host` also wires the independent `EffectObserver` into the synthetic host.
It observes requested outputs immediately after the actual reducer, maps the
controller's stable `DispatchPermit::origin()` to a `RequestOrigin`, and reports
admission, ready/start/finish, logical resolution, synchronized completion
publication, delivery immediately before/after the reducer, and explicit cleanup
settlement. Diagnostic hook errors do not gate or retry the synthetic host action.

The host calls `debugger_pause(true)` after safe-point acknowledgement and clears
it when authorizing progress. Completion latency spanning pauses is retained with
`debugger_affected`; the example asserts that controlled-origin samples are absent
from the ordinary production view. It also asserts bridge/effect origin agreement
and that exact replay leaves the already recorded host metrics unchanged. These
are synthetic local effects and local monotonic elapsed measurements, not a claim
of production timing or unchanged real-world scheduling.
