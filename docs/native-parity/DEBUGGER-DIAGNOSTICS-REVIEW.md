# Independent N12 audit review

Static review only. No builds, tests, transport, model execution or native runtime
edits were performed. Reviewed DEBUGGER-DIAGNOSTICS-AUDIT against N12 in the plan,
the companion's public module exports and relevant actual implementations in
session, workbench, evidence, inspect, watches, diagnostic, protocol, live,
effects, metrics and dashboard. Searched current four native runtime source trees
for the corresponding public session/viewer/live/watch/inspection/metric APIs;
no matching companion surface was identified. This does not prohibit handwritten
application implementations or qualify the existing native foundations.

## Precision findings

- **P3, resolved clarification:** inspection supports explicit unstable map
  ordering, not only stable ordering (`inspect.rs`, MapOrdering and
  InspectContext::map). Preserve the stable/unstable declaration and incomplete
  comparison semantics; index positions are not persistent entity identities.
  A native contract should not silently require every cooperative map inspector
  to manufacture stable order.
- **P3, resolved clarification:** an overflow histogram quantile is a present
  Quantile with `upper_bound_ns: None`, observations and low-sample warning;
  it is not an absent quantile. For the four-sample example p95 retains four
  observations and the warning, while its upper bound is null. Empty/invalid
  quantile queries separately return None (`metrics.rs`, Histogram::quantile).

No other concrete contract misstatement or uncovered N12 family was found in
this scoped source review. The author incorporated both clarifications and the revised text was re-read. These are documentation precision findings, not
implementation defects or new runtime qualification.

## Important source checks

Confirmed constructor/check order and observer-after-commit session semantics;
raw historical output decoding remains independently optional. Forks replay the
selected prefix before checkpoint decode and preserve explicit environment policy
provenance. Inspection returns Unknown for incomplete projections before equality;
redacted fields skip evaluation. Watch/probe no-interest paths avoid projection,
and scoped diagnostics remain explicit, revisioned and independently bounded.

Protocol framing rejects zero/oversized lengths before payload allocation;
partial frames dispatch no command, whereas complete malformed payloads receive
bounded responses. Epoch-local request retirement, independent authorities and
reconnect-invalidated handles remain distinct from durable exactly-once I/O.
Live delivery and dispatch are host-owned cooperative capabilities; pause cannot
preempt actual worker execution or turn a reservation into confirmed dispatch.

EvidenceViewer validates v1 recording semantics beyond structural Artifact reads.
Initial versus persisted-v2 check phases remain distinct from unknown legacy
phases. Its v2 minimizer charges exact phase-verification transitions/time against
the same overall budget before legacy workbench minimization. Declared-policy
callbacks are not fabricated by that legacy entry point. The proposed offline
first milestone therefore needs explicit phase-preserving policy-aware wiring;
its existence is not inferred from current replay/shrink foundations.

The proposed staged acceptance keeps passive data, simulation, inspection,
protocol mutation, live ownership and telemetry as independent proof boundaries.
OS-pipe, installed-package, host lifecycle and clock/histogram tests are future
evidence. No full N12 closure, broad platform/browser support, process debugger,
authentication, hard RSS or performance claim follows from this audit.

Reviewed audit SHA-256: `54ee63902a6814ca96aaff631c10b305b6c9b3e9968f5b2e96505b54af85085a`.
