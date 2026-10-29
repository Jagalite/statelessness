# Independent static review, 2026-10-29

One permitted native read-only reviewer inspected pinned Rust companion contracts,
current native optional modules, tests and the new gate. It executed no tests and
created no children. Final source changes address its findings:

- Swift candidate tokens revalidate the current bounded domain before delivery.
- Python state/observation/candidate copy hooks hold the session's mutation guard.
- TypeScript candidate pagination permits one lookahead and prevalidates range.
- Swift candidate protocol fields use Rust's candidates.* names; unauthorized
  exact payload fields are omitted.
- Go default metric labels are unspecified/host/default.
- N12 runner accepts explicitly sealed validation corpus inputs.
- Completion gate rejects source/test/consumer/package drift, narrowed platform
  matrices, symlink inputs and unsupported automatic semantic promotion.
- Final packaging follow-up adds the shipped typescript/DEBUGGER.md input.

The reviewer found no further native-code blockers in the scoped corrections.
The fixes require executed qualification; review is not runtime evidence.

## Unimplemented N12 integration still required

These are source gaps, not environmental blockers or qualified exclusions:

1. Go and TypeScript concrete protocol inputs/select/export_trace handlers;
   Swift concrete export_trace handler. Generic callback slots are insufficient.
2. All four concrete watch-registry/probe-hub protocol attachment/configuration/
   query integrations corresponding to Rust enable_watches/attach_probes.
3. Go and Swift concrete observer-backed telemetry protocol adapters corresponding
   to Rust attach_telemetry. Python/TypeScript already have metric/effect adapters.
4. Independent current-source installed direct qualification of those successful
   attached-service command families; existing 73-case fixture excludes them.

Standalone native session/replay/fork/inspection/watch/probe/live/effect/metrics/
passive/minimization implementations and many tests exist. Their existence does
not close the missing integration paths, full symbol equivalence, platform/runtime
matrices or resource budgets. This lane is not full M0/N12 acceptance.
