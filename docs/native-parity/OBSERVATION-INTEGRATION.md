# N07 observation integration candidate

This work extends committed candidate
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83` in the isolated
`t3/n07-observation-parity` worktree. It adds synchronous diagnostic observation
to the independent Python, Go, TypeScript and Swift packages. It does not change
the pinned Rust observation or monitor implementation.

Read the [source contract](../../spec/observation-v1.md),
[source audit](OBSERVATION-SOURCE-AUDIT.md), and
[installed example protocol](../../examples/native-observation/README.md)
together. Source implementation, source review, compilation, executable tests,
installed consumers and platform/resource qualification are separate gates.
Full N07 qualification remains open until its relevant executable gates pass.

## Existing checked and Recorder slice

The historical [checked-result audit](CHECKED-RESULTS-AUDIT.md) describes missing
APIs at its original snapshot. The current base already supplies the following:

| Contract | Python | Go | TypeScript | Swift |
| --- | --- | --- | --- | --- |
| Supplied checkpoint checked once | `check_initial` | `CheckInitial` | `checkInitial` | `checkInitial` |
| Supplied turn checked once | `check_turn` | `CheckTurn` | `checkTurnExact` | `checkTurn` |
| Sealed result types | `CheckedInitial`, `CheckedTurn` | `CheckedInitial`, `CheckedTurn` | `CheckedInitial`, `CheckedTurn` | `CheckedInitial`, `CheckedTurn` |
| Diagnostic turn view | `TurnObservation` | `TurnObservation` | `TurnObservation` | `TurnObservation` |
| Bounded runtime evidence | `Recorder` | `Recorder` | `Recorder` | `Recorder` |
| Checked handoff | `observe_checked` | `ObserveCheckedRecorder` | `observeChecked` | `observeChecked` |

The implementations retain ordered checks and their state/transition boundary,
full-checking coverage, exact u64 host sequences, attachment origins, metadata
identity and canonical before-state continuity. Recorder eviction promotes only
the evicted state-check prefix into the retained checkpoint. First property or
recording failure freezes the coherent prefix. Sampling produces explicit skipped
checks; an empty check batch remains distinct from that evidence.

The [checked-slice receipts](../../validation/native-parity/checked-slice/README.md)
are historical evidence for their pinned source. Their 27 token/36 Recorder
vectors and public installed consumers remain useful regression inputs. The
historical language suite counts and replay-pair counts are not results for this
changed source tree. The same applies to earlier N13 installed research and
native proof, including any jobs still queued against other worktrees.

## Diagnostic and evidence ownership

An application executes each reducer and check operation, then chooses whether
to send diagnostics and whether to record durable evidence. TextObserver consumes
supplied event data. It does not run reducers, checks or external effects and
does not check event continuity. Even a complete-looking diagnostic event stream
does not certify a replayable history.

Passing an event or a public turn observation to a checked Recorder handoff must
not grant token authority. Existing private-token guards remain in force. The
caller may send the authentic checked batch to Recorder and separately use its
diagnostic view for logging. A logging failure does not itself stop application
execution or freeze Recorder: that decision belongs to the caller.

The text sink buffers one record before publishing. Formatting, encoding and
record-bound failures publish zero bytes for that record and permit a later
event. A destination write or flush failure can expose a prefix and permanently
poisons that observer, including after verbosity is changed to Off. Extracting
the writer performs no implicit flush. Custom formatters own their escaping and
side effects; the library cannot promise single-line output from arbitrary custom
callbacks.

Public ownership and error choices are native to each package:

| Package | Diagnostic error | Destination contract | Ownership |
| --- | --- | --- | --- |
| Python | `TextObserverError` with stage/cause | Synchronous binary `write` counts; retry short/interrupted writes | Application references; extraction consumes the observer handle without flushing |
| Go | `TextObserverError` with stage/cause | `io.Writer`; a short nil-error return fails with `io.ErrShortWrite`; optional `Flush() error` | Application-owned writer interface, explicit access/extraction |
| TypeScript | `TextObservationError` with stage and rendered message | Synchronous consumed-byte counts; retry short/interrupted writes | Browser-safe references and typed arrays; no Node imports in the library |
| Swift | `TextObservationError` with stage and rendered detail | Synchronous `ObservationWriter` consumed-byte counts | Native value/reference behavior; observer is not Sendable; extraction does not flush |

The pre-existing oracle `ObservationError` remains a distinct API in Python,
Go and TypeScript. Formatter errors are contextualized to the active payload;
a nested observer's failure does not poison the outer destination. Logical
storage bounds cover retained record/codec bytes. They do not bound caller-owned
writers, model cloning, legacy allocating codecs or arbitrary callbacks.

## Retained suffix integration

The additive `record-suffix PREFIX` mode in the existing checked examples uses
the real checked token and Recorder path, with one retained turn after two
application turns. The supplied checkpoint starts at absolute origin 40; eviction
promotes state 1 and its two state checks to the retained checkpoint at absolute
41. Replay verifies the one retained turn to state 2. Audit configuration retains
the original origin, two observed turns, one eviction and local checkpoint offset
1. Diagnostic events at sequences 40, 41 and 42 do not certify evicted history.

The independent [interrupted contract](../../examples/native-observation/suffix-contract.json)
and [failure contract](../../examples/native-observation/suffix-failure-contract.json)
fix callback counts, complete replay observations, checkpoint phase, outputs,
checks, configuration and termination. The failing case adds the third turn,
retains checkpoint state 2 and the first failing turn to state 3, and verifies
that later diagnostics and a repeated authentic token cannot replace the frozen
artifact. Capture counts are 0/2/3/2 or 0/3/4/3 for initial-state/reducer/state/
transition callbacks. Each separate replay must report 0/1/2/1.

Each case uses five producer processes and 25 binary plus 25 JSON consumer
processes: 10/50/50 in total. These are planned execution units, not passing
results. Both formats compare complete rich and bounded observations. Portable
JSON omits audit configuration: native rich replay accepts the portable trace;
bounded replay uses the public conversion with a default execution configuration.
No recovered configuration is claimed in JSON receipts. Binary framing/footer
validation separately checks what a replay report does not expose. Existing
checked example modes remain available alongside the new suffix modes.

## Qualification gates

The new corpus is independently authored from the pinned Rust source; consumers
must never generate expected records from implementation execution. Exact text
comparison covers the declared shared representation. It must not normalize away
differences in severity, escaping, sequence values, check details, payload order,
snapshot selection, partial writes or failure stages.

Required changed-tree evidence includes:

- All four language suites, including existing checked/Recorder cases and the
  added observation/Recorder integration tests.
- Actual public Rust observation/monitor/checked-result execution, separately
  from any Cargo compilation-only receipt.
- Installed Python wheel and TypeScript tarball consumers, staged Go with
  `CGO_ENABLED=0`, staged Swift and the public Rust consumer against the same
  independent corpus.
- Executed callback counts, negative controls, exact UTF-8 record bounds,
  bounded encoded payloads, swallowed-write failures, formatting recovery and
  permanent writer/flush poisoning.
- Long sessions beyond retention, zero/small capacities, absolute checkpoints,
  skipped versus absent checks, first-failure preservation and fresh-process
  retained-suffix replay through the existing checked consumer protocol.
- Positive checked-token compilation and expected diagnostic-type rejection in
  installed Go and Swift consumers, with unrelated compiler failures rejected.
- Source membership/type/mode/hash identity, corpus/package/toolchain/executable
  identities, and raw logs for every failed, superseded or successful attempt.

Logical record, encoded-blob and retained-wire bounds do not establish an RSS or
allocator-capacity bound. Legacy allocating callbacks, model clone hooks, custom
formatters and application code can allocate outside those limits. Other native
platforms, browser-host runtime qualification and resource campaigns remain
separate acceptance gates until executed and documented.

The source audit maps all 162 N07 inventory rows. Six `DebugObservation` sum/
member rows remain explicitly open as a dedicated public native surface; the
new logging Event is not relabeled as that API. Incidental Rust Debug/trait glue
uses the native dispositions stated in the audit. This candidate does not close
those gaps by narrowing the definition of full N07 or full native parity.
