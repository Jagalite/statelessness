# Integrating a real controller

The [controller executable](../examples/controller.rs) connects one production
reducer to background OS workers, ordered effects, finite exploration, independent
properties, fault controls, recording and fresh-process replay. The application
state and [reducer](../src/reference.rs) contain generation and attempt identifiers,
logical time, pending jobs and tombstones. File buffers, network clients, thread
handles and cancellation flags stay in the [host](../src/reference/host.rs).
The host calls the reducer once and executes each returned effect once, in order.
The verifier and replay call the same reducer and never execute effects.

## Running the integration

These commands execute application/test code. They are documentation for an
execution-authorized environment. This implementation task is **compile-only**
under the existing Kyoto policy; no local runtime outcome is asserted.

```sh
cargo run --example controller -- live /tmp/controller-live.sttrace
cargo run --example controller -- replay fixed /tmp/controller-live.sttrace
cargo run --example controller -- check /tmp/controller-assurance.json
cargo run --example controller -- mutations /tmp/controller-fault-controls
cargo run --example controller -- regressions
python3 scripts/check_reference_mutations.py /tmp/controller-fault-controls \
  --controller target/debug/examples/controller
```

Each destination must be new. Raw traces are created with mode `0600` on Unix;
the containing directory and report visibility are the caller's responsibility.
The final gate invokes a fresh process for every original and minimized witness
and checks every independently declared mutation target. A removed result,
renamed target, missing witness, surviving mutant, search cutoff or replay error
fails qualification. A surviving mutant means the current properties failed to
detect that seeded fault, not that production is necessarily wrong. A shrunk
trace is causally reduced under the recorded budget, not a global minimum proof.

For this task, compilation uses the shared queue without changing its policy:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  cargo test --no-run --test reference_controller --example controller
```

## The application contract

A new request supersedes the active request: emit `Cancel(old)` before
`Start(new)`. Cancellation marks the old job pending cancellation; its resource
remains owned until the host delivers `Cancelled` or a racing `Complete`.
Only a matching successful completion of the current running generation may
publish. Emit `Settle` before `Release`. Duplicates and stale attempts have no
state or effect changes. A retryable failure waits until an explicit `Tick`
reaches its deadline before starting the next attempt.

The finite environment includes two generations, two attempts, three clock values,
unknown generation zero, obsolete completions, duplicates, cancellation and retry.
It abstracts payload bytes to success/retryable-failure classes. Exhaustion of this
model does not prove arbitrary payload processing, OS cleanup, real network
behavior, process crashes, infinite request streams or eventual completion under
an unfair environment. These remain integration and operational obligations.
Tombstones deliberately remain retained. Retire them only after an explicit
upstream delivery fence or a separately documented bounded-delivery contract.

The [requirement catalog](requirements/controller.json),
[property declarations](requirements/controller-properties.txt) and
[retained regression corpus](requirements/controller-witnesses.txt) are separate
from reducer code. The observation properties inspect actual inputs, before/after
state and ordered outputs. They do not call the reducer to manufacture an expected
result. The catalog is **awaiting independent human review**; separation of files
is not evidence that such review occurred.

## Host recipes

**Background jobs and external commands.** Replace the host's worker callback with
bounded file, command or service work. Translate failure into a typed completion.
Capture random choices, clock readings and generation identity at dispatch; deliver
all outcomes as inputs. A command timeout is an observed input and a cancellation
request; it is not proof the external process has exited. Join/reap before release.
The sample only acknowledges cooperative cancellation when its worker returns.
Dropping the host requests cancellation and detaches remaining workers; it never
fabricates acknowledgments or claims their completion. The sample is a bounded
reference, so production services should also bound their event queue and report
overflow explicitly.

**Tokio-style async services.** A task owns the reducer. It reads one `Input` from
an inbox, commits one transition, and dispatches ordered effects. `Start` spawns a
future carrying `(generation, attempt)`. The future posts `Complete`; cancellation
posts `Cancelled` only once the owned future/resource has actually stopped.
A timer task posts a logical `Tick`. Keep Tokio outside the dependency-free model
and use the same model adapter in tests. If effect dispatch is persisted across
crashes, add an application outbox/acknowledgment protocol: in-process execution
once does not promise durable exactly-once semantics.

**UI/TUI event loops.** The UI thread owns state. Key events become `Submit` or
`Cancel`, a timer produces `Tick`, and a completion mailbox produces terminal
inputs. Render from the resulting state; rendering is not a modeled I/O effect.
Dispatch the output vector once before the next reducer event. Preserve generation
checks even if a hidden tab/view was replaced while work continued. Do not model a
simple formatting helper or large frame buffer unless a relevant invariant
actually depends on it.

**Actor-message handlers and multiple machines.** Use the same mailbox boundary;
a handler executes exactly one transition. The network/composition scheduler
chooses explicit deliveries; it does not run another actor to quiescence inside a
check callback. IDs identify foreign resources; do not serialize live handles.

## Attaching diagnostics and oracles

The host optionally attaches `Recorder` at the initial checkpoint. `observe`
checks and records a transition that already happened; it does not execute the
reducer again. Recorder failures are retained as diagnostics while normal ordered
effect execution continues. Attach logging/debugger/metrics through the same
observation boundary and track dropped or incomplete samples independently.
A metrics callback failure must not trigger effect retries.

A history-sensitive oracle starts alongside the application or restores its own
history from a saved valid prefix. A current-state snapshot cannot fabricate past
settlements. When attaching midstream, either restore an exact complete oracle
checkpoint, replay the recorded history into the oracle without effects, or
explicitly report the historical checks as unavailable. A recorder's bounded
window is not automatically a complete application history.

## Reviewing requirement changes

Validate the current catalog and corpus with:

```sh
python3 scripts/review_requirements.py
```

For a proposed change, save the base catalog/corpus from the PR base and run:

```sh
python3 scripts/review_requirements.py --base /tmp/base-controller.json \
  --base-corpus /tmp/base-controller-witnesses.txt \
  --current-corpus docs/requirements/controller-witnesses.txt --base-ref origin/main
```

The JSON report separately exposes reducer, property/domain, codec and regression
file changes. Requirement text edits, removed properties or goals, any input-domain
change, altered versions or assumptions, raised bounds and removed/changed
counterexamples all require review. The gate fails without an external receipt
matching the exact base/current catalog and corpus SHA-256, changed categories,
reviewer identity, rationale and `decision: approve_requirements_change`.
It validates that receipt's scope; it cannot authenticate the person who wrote it.
Protect approval receipts and PR review with repository controls. The shared
qualification runner accepts an already reviewed receipt through
`--requirements-review-receipt`; it never writes or approves one. In PR CI the
base revision is read from the event and its catalog/corpus/property phases are
compared directly. An absent initial baseline is labeled awaiting review, while
unavailable base history fails the change-review gate. Never let an
agent approve its own requirement weakening by generating that receipt. Initial
catalog validation does not mark the baseline independently reviewed.

The optional macro companion can progressively replace handwritten adapter glue;
keep the reducer and the independent catalog unchanged. Macro metadata registers
observed implementation checks, while the assurance manifest declares required
ones. Those catalogs serve different purposes and must not be inferred from each
other during a run.
