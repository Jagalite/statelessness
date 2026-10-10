# Transition debugger v1.1 contracts

Implementation starts from `f3e00e79471b928d67ddb530c113d6d51af6f277`.
The design's reviewed code baseline is `cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de`;
these differ only by the checked-in v1.1 design. The original supplied document
is Library version 2 of `Statelessness-Rust-Debugger-Design.docx` (77,057 bytes).
The complete checked-in design is [here](../Statelessness-Rust-Debugger-Design-v1.1.md).

## Preserved decisions

* A turn delivers exactly one modeled input. Effects are data; neither simulation
  nor replay dispatches them. Composition queues are never implicitly drained.
* Initial state/checks are sequence zero. Rejected and ignored deliveries count.
* Admission, environmental validation, breakpoints, execution, checking, exact
  capture, and diagnostic publication have distinct failure outcomes.
* Execute a reducer and advance each oracle once. Share a checked result between
  consumers; do not trust a publicly forgeable collection of checks.
* No ordinary exact artifact continues beyond its first property failure.
* Inspection is bounded, read-only and separately redacted. It is never semantic
  equality, an exact codec, or proof that a raw trace can be safely shared.
* Browsing, verified replay, simulation, live observation, and cooperative live
  control have independent capabilities. Reconnect cannot grant capabilities.
* Watch configuration is diagnostic state, with a control generation and an
  effective boundary. It cannot change model state, logical time or trace bytes.
* A view's position, a replay's verified prefix and a branch's execution head
  are separate. Branches preserve parents and coherent composite checkpoints.
* Model, property and codec identities remain mandatory. Explicit build mismatch
  permission permits comparison; it does not establish same-build replay.
* All limits are cooperative. Application callbacks can block and can allocate
  outside debugger budgets. Clone is not proof of snapshot independence.

## Existing boundaries retained

The core Model trait remains Clone + Eq only. Codecs and Debug formatting remain
optional. ModelCodec cannot decode historical outputs. Trace format remains v1.
Legacy replay observers run after attempted transitions, including divergence,
but never gain a sequence-zero callback. Recorder freeze does not stop a host.
The stock CLI remains linked to its fixture, without a dynamic Rust plugin ABI.
Live observation is not a process pause. No live arbitrary state restoration,
remote listener, native debugger portability claim, or GUI is implicit.

## Implementation map

| Milestone | Ownership and verification |
|---|---|
| M0 | These contracts; baseline engine/macro tests; existing replay/recorder fixtures |
| M1 | Core checked observations/replay seam; companion session and linked harness |
| M2 | Companion trace cursor, verification, provenance, fork, regression/minimization |
| M3 | Companion Inspect contract, paging/redaction/diffs/watches; optional derive |
| M3a | Explicit lazy probes, revisioned subscriptions, independent privacy-bounded sinks |
| M3b | Host lifecycle timing, bounded metrics, fake-clock/async/overhead qualification |
| M4 | Versioned bounded stdio protocol, revisions, deduplication, query handles |
| M5 | Observation-only application bridge, checkpoint/gaps/effect telemetry |
| M6 | Opt-in host safe points, one-turn permits, arrivals/detach/dispatch policies |

Each milestone's checked-in tests and final qualification report are the gate,
not the presence of an API or the historical source design. Optional TUI, editor
integration, remote transport and arbitrary state editing are later extensions.
Post-turn markers remain diagnostic classifications; explicit in-reducer probes
are included by the expanded repository revision described below.

## Same-version source reconciliation

On implementation review, the current repository document was found to be an
expanded v1.1, not identical to the Library v1.1 document. The repository version
is authoritative for this repository implementation, with compatible Library
requirements preserved. Both exact inputs are identified here:

* Repository design commit `315fc57` (merged by `f3e00e7`), SHA-256
  `4557a92bc9689da26accf22b651cf0ca70f3e508242296c6f99c820a8dad8877`
* Library version 2 DOCX SHA-256
  `3bc4c763a33aa130447f6b809c7c6ce988597d06021cfb9b6e8ed609c6054281`
  (original preserved in the user's Library; not redistributed in this repository)

The expanded source adds M3a scoped lazy probes and independent sink routing and
M3b host effect timing/metrics. These are included in the implementation and
qualification. Function-local probes are therefore **included**. Ecosystem adapters remain optional; no
telemetry service, remote listener, credentials or account is provisioned.

M3b freezes local monotonic clock-domain identity; known versus unknown/censored
endpoints; request/admission/attempt/resolution/post/delivery/settlement boundaries;
independent log sampling and metric accounting; bounded metric label catalogs;
fixed cumulative histogram schemas, epochs, populations and retained windows.
Telemetry clocks never enter model equality, exact bytes or logical time.
