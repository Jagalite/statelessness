# Optional native diagnostics

Import `github.com/Jagalite/statelessness/go/debug` explicitly. The core engine
has no dependency on this package. There are no package initializer clocks,
watchers, goroutines, transports or global model instrumentation.

The offline APIs own their evidence. `NewEvidenceViewer` validates recording
semantics beyond structural wire reads. Boundary zero is the retained checkpoint;
full-width local/host sequences and phase provenance remain distinct. Exact bytes
can contain secrets. `Preview` truncates bytes without an output decoder.
`Failure` rejects unknown noninitial v1 phases; `MinimizeWithPhase` is the explicit
legacy assertion API. Persisted v2 phase contradictions reject.

`Verify` uses explicit finite transition limits (the artifact's finite step quota
when omitted), optional cooperative cancellation/deadline, actual observations and
all replay difference flags. It dispatches no outputs. V2 minimization performs
phase verification, then exact workbench verification, before checkpoint-based
shrinking; all passes charge one transition/time budget. Declared provenance needs
an explicit original policy implementation. Admission and Generate causality are
independent. A smaller tested reproducer is not proof of global minimality.

Typed `Session` owns simulation state under the application's clone contract.
The host serializes calls; reentrant mutation rejects. Pending pre-breakpoint
inputs bypass once at the same revision. Checks and observers cannot undo an
already produced transition. Opaque candidate tokens bind session+revision+input.
Recording sessions hand canonical checked tokens to the recorder without repeated
checking. `ForkWithOptions` verifies the selected prefix before checkpoint decode,
rejects failed boundaries, and creates a recorded independent child with typed
parent/checkpoint FNV+size sidecar bindings. Those bindings detect accidental
mismatch; they are not authentication.

Inspection is an explicit display adapter, independent of codecs and equality.
Queries bound work/depth/nodes/bytes/value previews/pages. Redacted fields never
run their getters. Map ordering is declared stable or unstable; incomplete,
redacted and unstable projections compare Unknown before equality.
`AddValidated` resolves a permitted watch path under its schema before ack.
Watches retain independent bounded baselines and queues, revisioned generations,
evaluation/delivery sampling, TTLs, source/schema/gap resets, typed thresholds and
explicit queue-loss/health records. Scoped probes skip unselected getters and
share one selected getter across sinks. Configuration uses expected revision and
explicit ordered-producer acknowledgement at an effective boundary. Unknown
property status never matches known successful checks. Exporters run explicitly;
failed exports preserve queue data and health independently of the model.

Protocol, cooperative live ownership and host telemetry have separate APIs and
proof boundaries; see `TELEMETRY.md`. No API promises callback preemption, process
RSS limits, remote authentication, LLDB/GDB integration or a new engine default.
The finite regression suite and frozen-source installed-consumer qualifier are
candidates for root review. Broad N12/profile/platform closure is not claimed.
