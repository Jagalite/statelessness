# Installed Go guided-v2 consumer

This external module imports only the public
`github.com/Jagalite/statelessness/go` package and Go's standard library. Its
fixture follows `conformance/guided-v2/README.md`. It does not import conformance
internals, implement a search algorithm, read expected reports, or compare or
regenerate goldens.

The qualification harness copies this directory beside an immutable installed
module at `../go-package`, records package identity, and builds the executable.
The manifest's replacement names that external installed package; it does not
point into the repository's `go/` source tree. The harness may rewrite this
staged manifest to its equivalent installed-package path.

Invocation after the caller has built the consumer:

```text
guided-go-consumer --corpus /absolute/path/to/corpus.json
guided-go-consumer --corpus /absolute/path/to/corpus.json --case zero-cases
```

A positional corpus filename is also accepted, and `--corpus -` reads a whole
corpus from stdin. Standard output is one JSON object:

```json
{"schema":"stateless-guided-v2-results/1","engine":"go","cases":[{"id":"case-id","actual":{"report":null,"error":"observed error","trace":[],"generator_rng":[],"witness_replays":[],"shrink":null}}]}
```

`--case` executes exactly the selected case. Without it, cases execute in corpus
order with independent fixture instances. Invalid fixture descriptors, unknown
case selectors, or a failed witness/replay/shrink operation fail the process and
write diagnostics to stderr. Public fuzz errors are observations in `actual`,
with a null report. Optional fields remain JSON null; empty arrays remain arrays.
The input decoder intentionally has no `expected` field.

The consumer uses `FuzzObserved` and `GuidedFuzzObserved` with full-history
observers. The model returns its configured disposition through the public
transition, defaulting to Accepted. Observer-transition and transition-feedback
trace entries append the actual disposition, preserving empty rejection/ignore
reasons. Custom feedback preserves optional initial hooks, attempted pushes,
duplicates, exact JSON-number equality, constant-hash collisions, and callback
error precedence. State and exact feedback use `StateFeedback` and
`ExactStateFeedback` through transparent tracing wrappers; helper pushes do not
appear in the trace. Append-only check sinks retain partial batches for observer
check-error callbacks. Cancellation occurs after the selected callback, including
failed callbacks; zero duration uses the public run limit.

Every retained prefix is recorded with public `Record`, decoded from the actual
recording, checked against the emitted check batches, and replayed with public
`Replay` on another fresh fixture. Witnesses include the recorded dispositions
and the returned replay outcome, failure-reproduced flag, and verified-step count.
Replay must be exact, complete, and build-matching. Retained entries must complete
and replay without a failure; no shrinking is run for them.

Only input `shrink_config` selects a separate public `Shrink` run, using the
observed failure, fresh callbacks, reset controls, and no simplifications. Its
original failure from `FuzzReport` and minimized failure from `ShrinkReport` are
recorded and replayed after the retained entries, in that order, even when their
inputs are equal. Both must terminate with PropertyFailed and reproduce the
failure, with the failing transition included in the verified-step count.
Recording and replay callbacks are separate from fuzz observations. Replay
starts its checkpoint's case selector at one because the public API loads the
checkpoint without calling `InitialState`; invocation counters remain reset.

Normalization translates Go's snake_case enums and callback context spellings
to the shared Rust-shaped protocol. It preserves callback error messages,
including empty messages and contextual trailing spaces, and all observed counts.
Only null denotes absence of a callback error. The Go `invalid fuzz configuration`
error maps to the schema's mutation-range message only when the mutation
percentage is the invalid field.
No normalization is based on case IDs or expected values.

Status: this schema revision has source review and gofmt only. No build, test,
consumer execution, or golden comparison was performed for this revision. The
parent owns qualification after library stabilization and corpus freeze. Do not
compare candidate output until the current `FREEZE.json` exists and its hashes
match. All executable qualification must be submitted through the default shared
queue with
`python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- ...`.
