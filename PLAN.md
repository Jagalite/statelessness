# Design and roadmap

Stateless explores application-defined state transitions, checks properties,
and records replayable evidence. See [README.md](README.md) for the current API
and [VALIDATION.md](VALIDATION.md) for verification commands and limits.

## Design

A model supplies state, inputs, a transition function, and named properties:

```text
State + Input -> Next State + Outputs -> Check properties -> Record evidence
```

Use the same transition logic as production wherever practical. Check outputs
as well as state: a valid final state can accompany an incorrect effect request.
All data relevant to future modeled behavior belongs in state or input,
including logical time, pending work, callback identities, and random outcomes.
The application executes real effects outside the model and supplies their
outcomes as inputs.

The engine stays dependency-free, including development and build dependencies.
Application state organization remains application-owned. Checks, input order,
codecs, and transitions must be deterministic for exact replay.

Findings and search termination are separate: a run can find no violation yet
stop at a depth or resource limit. Adapter and checker errors are distinct from
application property failures. Check initial states and all explored edges,
including edges to previously visited states.

The campaign coordinator uses a shared decision core for production execution
and model exploration. Threads, clocks, channels, and report sinks remain in its
executor. This model covers the coordination protocol, not all engine internals.

## Remaining Rust work

- Add application-directed checking policies with recorded schedules and explicit
  skipped results, preserving exact replay after checkpoint advancement.
- Reduce recording costs through configurable checkpoints and lossless state
  sharing while retaining per-step evidence and exact replay.
- Extend representative measurements of larger states, branching graphs, delayed
  completions, long sessions, recording, and shrinking. Record build identity,
  configuration, repeated timings, memory accounting, and trace volume.

Existing byte budgets, reusable buffers, lazy input generation, cooperative
cancellation, and bounded shrinking do not eliminate full-state copying or
full-state property scans. State-byte estimates omit temporary allocations and
are not a process memory cap. Cooperative deadlines cannot interrupt arbitrary
application callbacks.

## Deferred work

Further foreign binding development follows stabilization of the Rust API.
General liveness checking, partial-order reduction, parallel exact enumeration,
state normalization, shared coverage corpora, GPU execution, distributed workers,
and a visual debugger are outside the current scope.

Finite exploration supports only the supplied properties within the modeled
bounds. Production integration requires separate validation of adapters and
external behavior.
