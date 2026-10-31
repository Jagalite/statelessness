# Native Python Statelessness

Experimental, dependency-free **Python implementation**, not a Rust/FFI binding.
Python >=3.10. Applications retain their own state types, callbacks, and codecs.
This package implements the `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and
`trace-json-v1` profiles in `../spec/README.md`. Features beyond these four
profiles, including runtime observation, bounded recording, and binary interchange,
retain their separately documented experimental status. Full Rust feature parity
is not implied.

Candidate [guided exploration](GUIDED.md) adds `guided_fuzz`, custom `Feedback`,
`StateFeedback` and `exact_state`, with bounded feature/corpus counts and optional
observers and cooperative controls. It shares the ordinary fuzz execution/check
kernel. See [the runnable example](examples/guided.py). Portable profiles and
ordinary fuzz defaults are unchanged. Campaigns and assurance remain separate work.

`WithOracle`, `Paired`, and `Projection` are additive experimental APIs described
by the [`paired-v1` candidate](../spec/paired-v1.md). They keep independent reference
history in state and compare projected state, ordered effects, and full disposition
using the existing observer, BFS and recorder. They are listed in
`EXPERIMENTAL_PROFILES`, outside the four qualified `PROFILES`: runtime Rust parity
and independent specification review remain pending. See `tests/test_paired.py`
for separately implemented bounded-queue reducers and retained error observations.

## Install and test

From the repository root:

```sh
python -m pip install ./python
python -m unittest discover -s python/tests -v
python -m statelessness.conformance conformance/corpus.json
# The installed command is equivalent:
statelessness-conformance conformance/corpus.json
```

No Rust compiler or native library is needed. JSON is only used for fixture and
trace interchange; native application transitions are ordinary Python calls.
The corpus is a repository artifact, not implicitly downloaded by the library.
Supply its path when running the installed package outside the checkout.

The optional legacy and indexed lifecycle monitors accept `max_events` (default
100,000) for the combined input and output classifier results per transition.
An over-limit batch fails without applying any of its events; the bound is part
of the Python monitor build identity and is not a process-memory limit.
Rust has no such bound, so the `;events=N` build suffix is an accepted
Python-only divergence (build identities are engine-specific in any case).

`IndexedLifecycleMonitor` also offers opt-in Rust Debug-compatible check details
through `rust_debug_key`, `rust_debug_string`, and `rust_debug_identity`. The two
callbacks must synchronously return the exact Rust Debug text for the inner key
and a string respectively, including quotes and escapes. The nonempty identity
identifies both callbacks; it does not prove their compatibility with a Rust
toolchain. Default native diagnostics remain unchanged. Formatter failures are
model errors, and callback work and returned text are outside retention limits.

## Native application example

```python
from dataclasses import dataclass
from statelessness import Check, SearchConfig, Transition, enumerate_states

@dataclass(frozen=True)
class State:
    count: int = 0

class Counter:
    def initial_state(self):
        return State()

    def inputs(self, state):
        return ["increment"]

    def step(self, state, input):
        return Transition(State(state.count + 1), outputs=("changed",))

    def check_state(self, state):
        return [Check("count.bound", "passed") if state.count <= 2
                else Check("count.bound", "failed", "count exceeded two")]

result = enumerate_states(Counter(), SearchConfig(max_states=20, max_depth=5))
assert result.termination == "failure_found"
assert result.failure.inputs == ("increment",) * 3
```

`examples/counter.py` adds application-owned codecs, JSON recording, and replay.
[`examples/background_jobs.py`](examples/background_jobs.py) explores a finite
job reducer and uses the same reducer in an asyncio host, including a controlled
late completion after cancellation. Its gates simulate external work; they do not
qualify a real service or an independently installed consumer.

The optional `check_transition(before, input, transition)` callback returns
additional `Check` values. Checks and outputs are ordered. A `ModelError` is a
broken callback/adapter, not a failed property. Rejected and ignored transitions
are still checked; rejection-preserves-state is application policy, not imposed
by the framework.

`check_observed` checks a transition already executed by the application and
never calls `step`. The caller checks its initial state separately. Periodic
checking marks omitted checks as skipped rather than passing.

## Experimental assurance observation

The `AssuranceManifest` declares required state or transition checks, input
families, coverage goals, provenance, and a required completion outcome. An
`AssuranceObserver` records actual search check batches and coverage callback
hits. After search, pass its termination through `assurance_search_outcome`,
finish the observer, and call `report.assess()`. The strict assessment rejects
missing, skipped or failed checks, missing coverage, unclassified inputs, callback
errors, and incomplete search. See [examples/assurance.py](examples/assurance.py)
for a complete finite BFS example.
`manifest.require(id, phase)` and `manifest.add_domain(descriptor)` return new
manifests with explicit requirements or declared domain assumptions. A domain
declaration does not establish that its inputs were exercised.
Coverage callbacks receive a temporary hit collector. It permits at most 4,096
distinct family/goal hits per observation, with identifiers up to 4,096 UTF-8
bytes; rejected writes fail that observation even if caught by the callback.
At most 1,024 distinct observation errors are retained, plus an overflow marker.
The observer retains at most 4,096 distinct phase/property identities and accepts
property IDs up to 4,096 UTF-8 bytes. Exceeding either limit records an error and
prevents a successful assurance assessment.
The 4,096 coverage-identity limit also applies across an entire observation
report and its merged summary. A merged summary that exceeds its property,
coverage, or error limit records an error and retains the individual job reports.
Manifest catalogs admit at most 8,192 entries per collection, 4,096 UTF-8 bytes
per text field, and 1 MiB of total text before snapshots are made. Coverage hits
are restricted to the callback thread, and rejected observer reentry and partial
check batches prevent an adequate assessment.
Finishing an observer consumes it; later observation or finish calls fail.

`assured_enumerate`, `assured_fuzz`, and `assured_guided_fuzz` run search with an
observer and return an `AssuredRun` containing the search result or model error
alongside the assurance report. The wrappers capture configured budgets and
retain partial observations when a search fails.
Guided reports include the algorithm, configured corpus limits and feedback
identity once feedback metadata has been obtained; metadata is called once.

This is an experimental observer slice, not N10 completion or an attestation of
the model or manifest. `merge_assurance_reports` combines reports with matching
manifest and executable metadata, preserving individual and missing job IDs.
`assured_campaign` and `assured_guided_campaign` add evidence to campaigns, preserving
job evidence before input encoding and sink delivery. Multiworker runs require an
explicit `ProcessWorkers` policy and spawn-picklable factories, as for ordinary campaigns.
Assured campaigns and `merge_assurance_reports` reject more than 100,000 expected
jobs by default before creating the job catalog. Set `max_jobs` explicitly to a
different nonnegative limit when the caller can support the retained evidence.
Their `max_evidence_bytes` budget defaults to 64 MiB of serialized job reports.
Direct merges reject an over-budget input; campaigns continue delivery, mark
unretained job reports missing, and report the evidence limit failure.
This logical serialized-byte budget is not a process-memory limit.
`report.properties`, `report.families`, and `report.coverage` derive sorted
statuses from the retained evidence; a history goal is never called unreachable
solely because a graph was exhausted. Broad parity qualification remains open. If a search raises,
record the error in `observer.finish(..., error=error)` and use a non-completed
outcome; do not claim an exhausted search. The unprefixed names are also available
from `statelessness.assurance`.

Manifest, report and merged report `to_json()` exports use the Rust assurance
JSON field and status names. They validate before export; this is an evidence
export and does not authenticate the underlying observations.
Strings use the Rust writer's escaping (only `"`, `\`, `\n`, `\r`, `\t` are
short escapes; other controls are `\u00XX`, never `\b`/`\f`). Report
`termination` uses Rust Debug spelling (`GraphExhausted`, `MergedJobs`,
`Campaign::JobsCompleted`, `Campaign::CallbackError(ModelError("..."))`); snake-case
terminations passed to `Observer.finish` are normalized. `assess()` reasons use
Rust wording (for example `property edge Transition: Missing`,
`cover goal two: NotReachedBeforeStop`); non-ASCII Debug escaping of a family
exclusion reason approximates Rust's printable tables. Budgets contain only Rust
keys; the Python-only `assured_*_auto` APIs do not record their candidate limit,
matching Go and Swift (Rust has no assured automatic entry point). Campaign
evidence-byte limits are not recorded in budgets.
`AssuranceManifest.from_json`, `AssuranceReport.from_json`, and
`MergedAssuranceReport.from_json` import these shapes
with a configurable `max_bytes` limit (default 1 MiB), reject duplicate keys and
verify report catalogs against retained evidence. Merged import also recomputes
job aggregates and enforces `max_jobs`. An imported report is not proof that its
observations occurred.

`statelessness.reference_controller` provides a partial finite controller
fixture: the reducer, bounded input enumeration, independent packaged requirements,
coverage, codec, checks and named fault controls. It does not establish N10
controller qualification or an executed cross-engine differential.
Its build identity fingerprints the packaged controller source and independent
requirements at module load, together with the bounds and selected mutation.
Its packaged witness corpus can be checked with `check_regressions()`, and
`statelessness.reference_host.Host` provides a single-owner worker example with
ordered effects, cooperative cancellation and optional recording. The host does
not provide durable exactly-once execution across crashes.
`statelessness.reference_suite.write_mutation_suite(directory)` writes exclusive
binary original and minimized witnesses plus a report. A fresh process can replay
one with `python -m statelessness.reference_suite MUTATION PATH`.
`verify_mutation_suite(directory)` validates a complete same-build suite and
replays both labeled witnesses for every named control; incomplete suites cannot
pass that check. Its `max_total_bytes` bounds summary and witness bytes together.
For foreign builds, supply both `expected_build` for the summary and
`expected_mutation_builds` with a build pin for every named control; model,
codec and target checks still apply. Witnesses carry exactly Rust's
`write_mutation_suite` run parameters (no witness-kind parameter), and the
summary is written in Rust `suite_json` layout (`"search":"Some(FailureFound)"`),
so Rust- and Go-produced suites verify. A witness-kind parameter recorded by an
older Python suite must still match its label.
`write_mutation_suite_with_pins(directory, pins_path)` creates a complete suite
and publishes those producer pins separately without overwriting existing files.
Both producer APIs accept `max_evidence_bytes` to bound the combined serialized
witness bytes; a control whose pair cannot be retained is not evaluated. This
limit excludes the JSON summary, producer pins, callback allocations and process
memory. A zero-byte witness limit can still produce an incomplete JSON summary.
The corresponding fresh-process producer is
`python -m statelessness.reference_suite --write-suite DIRECTORY --write-pins PINS.json`.
Use `--max-evidence-bytes` in that mode to set the combined witness-byte cap.
With `return_report=True`, verification also reports total bytes read and whether
the producer and all mutation builds match the local controller builds.
For a fresh-process local suite check, use
`python -m statelessness.reference_suite --suite DIRECTORY`.
Add `--pins PINS.json` for foreign suites, using a separate JSON object with
`report_build` and a complete `mutation_builds` mapping. The pins file has a
separate one-mebibyte limit and is not counted in the suite byte budget.
Use an empty destination with a single writer; directory checks and ownership
checks do not lock out concurrent replacements. If a file descriptor cannot be
identified immediately after creation, the descriptor is closed and its path is
left for inspection rather than deleting a possible replacement.

## State and cost contract

`runtime_build_id()` computes an on-demand SHA-256 identity for packaged Python
sources and bundled JSON/text data as they exist on disk when called. Keep those
files stable during the walk: concurrent edits can produce a mixed digest, and
already-imported code may differ from files edited since import. The identity is
not an authenticity guarantee and excludes the interpreter, native extensions,
environment and bytecode.

Callbacks must be deterministic and free of real I/O, clocks, hidden mutable
history, and effect execution. The library defensively deep-copies snapshots and
callback arguments. States must support correct `deepcopy` behavior; overriding
it to alias mutable storage violates the contract. This is not a code sandbox.

Equality must preserve future behavior, input choices, and checked properties.
Input equality also defines shrink sequence identity; use value equality that
survives defensive copying, rather than identity-only mutable input objects.
Hash collisions are resolved by equality. Lists/dictionaries are supported via
linear equality lookup; hashable states are generally faster. Equal hashable
states must have equal hashes. Large states incur copying costs; no parity of
runtime, memory consumption, or throughput with Rust is claimed.

Search count limits are not process-memory limits. `TraceLimits` bound retained
encoded payloads, blob sizes, and item counts; callbacks can allocate before
returning. JSON serialization has a separate byte limit. Exact replay verifies
only the recorded prefix and identities, not graph exhaustion or a callback error
after that prefix. Build mismatch requires explicit opt-in and never bypasses
model/property/codec version checks.

## Build distributions

```sh
python -m pip install build
python -m build python
# Or with a provisioned setuptools >=77 backend and no dependency downloads:
python -m pip wheel --no-build-isolation --no-deps ./python -w dist
```

The wheel is pure Python (`py3-none-any`). Nothing here publishes to PyPI.
The source distribution is also supported; release qualification should test an
installed wheel in a fresh environment, not just imports from `src`.

## Experimental native generation, fuzzing and shrinking

`Auto(model, AutoOptions(max_candidates=100_000))` adds reservoir generation
from the complete finite `inputs(state)` domain. It validates the full domain,
uses one extra pull to detect overflow, and rolls RNG state back on errors.
`Generated(model, generate, is_enabled=..., simpler_inputs=...)` instead adds
application-owned callbacks. `generate(state, rng)` returns an input or the exported `EXHAUSTED`
sentinel for exhaustion. Every ordinary Python value, including `None`, is a
present input. Callbacks use defensive snapshots; the RNG is intentionally mutable.

```python
from statelessness import Auto, FuzzConfig, ShrinkConfig, fuzz, shrink

report = fuzz(Auto(Counter()), FuzzConfig(seed=42, cases=100, max_steps=20))
if report.failure is not None:
    minimized = shrink(Auto(Counter()), report.failure, ShrinkConfig(max_attempts=1000))
    assert minimized.validated_original
```

`fuzz` retains only the previous passing sequence for deterministic mutation and
checks enabled inputs before execution. `shrink` validates original evidence,
then tries chunk deletion and application simplification while preserving the
first violation's check ID and phase. Reports include attempts, operation history,
aggregate replay transitions, and whether original validation completed.
`RunLimits(max_duration=seconds, cancellation=callable)` provides cooperative
control; `ShrinkLimits` also caps aggregate replay transitions including original
validation. A callback or iterator pull cannot be interrupted; limits do not cap
callback allocations. On early exit, shrinking closes a simplifier iterator that
provides `close`; a cleanup failure does not replace an existing search error.
Callback errors raise `ModelError`, never count as property reproduction. Search
completion does not prove exhaustive coverage or global
minimality. These APIs implement the experimental [`search-v1` candidate](../spec/search-v1.md)
and consume its hand-authored TSV corpus in tests; they do not expand `PROFILES`.

`fuzz_auto(model, config, options=..., limits=...)` is an opt-in finite-domain
path that also checks its run cancellation and deadline between automatic
generation and membership iterator pulls. Interrupted sampling leaves its
tentative RNG state uncommitted. `guided_fuzz_auto`, `run_auto_campaign`, and
`run_guided_auto_campaign` extend the same behavior to guided and campaign
search. Existing `fuzz(Auto(...))` and other existing entry points retain their
callback-boundary controls; a feedback factory itself cannot be preempted.

`fuzz(..., observer=observer)` accepts optional `SearchObserver` hooks:
`initial(state, checks)`, `transition(before, input, transition, state_checks,
transition_checks, history)`, and `check_error(phase, checks, complete)`.
Each hook receives defensive snapshots; history is the actual input prefix from
that case's initial state. Failing transitions are observed. If a check iterable
fails after yielding values, `check_error` receives its incomplete batch; an edge
checker error also exposes the completed state batch separately. Observer errors
raise `ModelError` and cannot manufacture a passing search report.
If a `check_error` hook itself fails, the original checker `ModelError` remains
primary and secondary errors are attached in its `observer_errors` tuple.

`WithOracle` forwards generation, enabled-input checks and simplification to the
actual model while retaining independent oracle history. Wrap the actual model
in `Generated` or `Auto` before attaching the oracle when it lacks a generator;
requesting fuzzing without generation raises an explicit `ModelError`.
`EXPERIMENTAL_PROFILES` lists `paired-v1`, `search-v1`, `native-binary-v1`, and `bfs-controls-v1`; qualified `PROFILES`
remain unchanged.

## Experimental lossless Rust audit binary v1

`AuditTrace` retains `RunConfig(strategy, seed, parameters)` alongside all portable
trace fields. Seeds preserve absence versus zero and all 64 bits; parameter pairs
preserve order and duplicate keys. `AuditTrace.from_portable(trace, config)`
requires explicit configuration. `audit.to_portable()` returns `(trace, config)`
to retain fields omitted by portable traces.

`audit_to_bytes(audit)` / `audit_from_bytes(data)` and stream counterparts
`write_audit(audit, writer)` / `read_audit(reader)` implement the existing Rust
binary v1 format with strict UTF-8, CRC32, framing, indices and EOF validation.
`BinaryLimits` independently bounds total/frame/blob/string bytes, steps, checks,
outputs, parameters and aggregate items on reading and writing. Writers preflight
all fields before publishing bytes; I/O failure can still leave a partial stream.
Streams remain caller-owned: no implicit flush, close or durable publication.
Reader EOF probing requires a bounded stream and host-controlled I/O deadlines.
`BinaryTraceError.kind` distinguishes structural, quota, checksum, truncation,
version and I/O errors. Binary parsing preserves structurally valid unusual
termination/check combinations; semantic consistency belongs to `replay`.

Binary v1 records no successor/transition check boundary, so step check phases
remain `unknown_legacy`; never infer phases from IDs or order. `native-binary-v1`
is an experimental candidate, outside qualified `PROFILES`. Shared finite goldens
and unit tests do not alone establish cross-language replay or publication safety.

## Candidate BFS controls, state estimates and observations

`enumerate_states(model, config, limits=SearchLimits(...), observer=...)` preserves
ordinary FIFO exploration and accepts the same `RunLimits` controls as fuzzing.
Cancellation/deadline polls surround enumeration, hashing, collision comparisons
and estimation. Completed check boundaries and their failures precede stops.
Depth/transition caps still probe one input to distinguish exhaustion from cutoff.
An admitted enumerated input is copied before the reducer and copied again for
the reducer call, so copy hooks can run or fail before the reducer. They must
provide detached values; concurrent mutation through external aliases is not atomic.

An optional `estimated_state_bytes(state)` model hook returns an unsigned 64-bit
logical retained-state estimate or `None`. `SearchLimits` optionally caps their
sum with `max_estimated_state_bytes`. Exact fits are admitted. Duplicates are not
charged or estimated again; state-count limits precede successor estimation.
Missing estimates under a cap raise `ModelError`; overflowing a capped total
stops admission. `SearchReport.estimated_retained_state_bytes` is `None` when an
uncapped total is unknown or overflows. These are application estimates, excluding
incoming successors, predecessor inputs, maps/frontier storage, callback
allocations and process RSS.

BFS uses the existing `SearchObserver` hooks. `needs_history()` defaults false;
return true to receive each edge's detached root-to-edge input prefix. Duplicate
and failing edges are observed. Partial check prefixes on errors retain the same
primary-error/secondary-observer-error semantics as fuzzing.

`WithOracle(..., estimated_wrapper_overhead=bytes)` combines actual and history
estimates with checked unsigned 64-bit addition. Python has no portable Rust-like
inline layout, so callers must explicitly supply their chosen logical wrapper
representation overhead; the default is unknown, never silently zero. Missing
components or sum overflow also produce unknown. `Paired` delegates history
estimation to its reference model. This convention does not imply equal byte
counts across languages or a runtime memory guarantee. Promoted `bfs-v1` JSON
interchange remains unchanged; these additive APIs need separate qualification.

The BFS extension is the experimental [`bfs-controls-v1` candidate](../spec/bfs-controls-v1.md); tests consume its shared callback/report vectors.

## Candidate append-only checking

Optional `check_state_into(state, sink)` and
`check_transition_into(before, input, transition, sink)` hooks take precedence
over legacy iterable-returning checks. Hooks are synchronous and return `None`;
non-None results, including coroutine objects, are errors. An append-only model
may omit legacy callbacks entirely; it must supply either the state append hook
or legacy state checking. Absent transition hooks remain empty.

`CheckSink` exposes `append` (`push` alias), `extend`, `reserve`, `snapshot`,
indexing, iteration and length. Inspection returns detached immutable tuples and
fresh Check values. There is no clear/remove/replace or public mutable backing
list. Each callback gets a fresh revocable lease. Every operation after callback
return/error fails before accessing storage; nested wrapper callbacks expire
individually while a legitimate outer lease stays usable. Invalid append/extend/
reserve is sticky even if the checker catches it: the batch still fails, retaining
its valid prefix for search diagnostics. Expired old views cannot poison a new
batch. This is a synchronous ownership API, not thread safety or a security
sandbox against private implementation access.

`check_observed_into(model, before, input, transition, sequence, checks, policy)`
reuses a caller-owned list, clearing it at entry and on every error (including
invalid sequence, snapshot failure, and interruption). It never invokes step.
The tuple-returning convenience API shares the same dispatch. BFS/fuzz/shrink
reuse check storage and preserve actual partial batches for observers. Record,
replay and oracle/paired/generated/automatic wrappers use the same append-first
path, without rerunning checkers. Sink callbacks see all preceding checks,
including the completed state batch and preceding actual/reference observations.
Python list capacity reuse and zero-allocation performance are not promised.
`append-checks-v1` remains an experimental candidate outside qualified `PROFILES`.

## Candidate bounded codecs and replay evidence

Optional `encode_state_into(value, buffer)`, `encode_input_into(value, buffer)`
and `encode_output_into(value, buffer)` hooks take precedence over legacy byte
encoders exactly once. Hooks return `None` synchronously. `EncodeBuffer(bytearray,
maximum)` clears the destination and supports `append`/`extend`, `framed(callback)`
with a little-endian u64 payload length, `snapshot`, length and `finish`. Appends
accept contiguous bytes, bytearray and memoryview values, check their byte count
before copying and retain detached bytes. A rejected write poisons the shared
budget even if caught; an explicit callback exception remains primary. Callback
leases expire on every exit and release storage ownership. A framed child shares
its parent's budget and expires independently. `WithOracle` writes ORC1 and both
framed component encodings incrementally; `Paired`, `Generated` and `Auto` forward
preferred codecs. Logical byte budgets do not bound legacy allocations, backing
capacity, model callbacks, snapshots or process memory.

`replay_with_observations(model, trace, observer=None)` returns the existing
`ReplayReport`. Its synchronous observer receives `(observation, report)`, with
initial, turn and error observation variants. Initial/turn observations contain
detached typed values, encoded bytes, checks and four difference flags; the report
is updated before delivery. A `ReplayError` retains the report and available
`actual`, with unknown checks on checker errors and complete checks on later codec
errors. Error-observer failures remain secondary in `observer_error`; a normal
observer failure does not recursively invoke the error observer. Snapshot failures
never rerun a reducer or retry the failed snapshot: the error owns the retained
original actual, while the error observation has absent actual and an explicit
`actual_unavailable_reason`. This recovery transfer differs from ordinary detached
observations and should be treated as error-owned mutable evidence.

`replay_with_observations_bounded(model, audit_trace, observer=None,
limits=BinaryLimits())` requires lossless `AuditTrace`, admits metadata identity
before validating the stored artifact against all nine binary quotas, then charges
actual RUN/STEP frames, aggregate items and shared output/state bytes with the
exact 18-byte replay footer. Quota primitives are captured before model callbacks;
identity-incompatible replay does not validate those captured quotas. Basic
`replay` keeps its existing API and uses the same kernel.

`record_audit(model, inputs, config, max_steps=100000, limits=BinaryLimits())`
records incrementally under all nine binary quotas, preserving coherent prefixes
and reserved error-footer headroom. It preserves caller config and appends ordered
`stateless.engine.version` and `stateless.engine.build` provenance; caller keys
with that prefix are rejected. Python's build label `native-python-v1` identifies
the implementation, not a source fingerprint. Replay preserves producer stamps.
`encode-buffers-v1` and `replay-evidence-v1` are experimental candidates; the four
qualified profiles and portable wire projections remain unchanged.

## Candidate synchronous runtime diagnostics

`Event.Started(metadata, config)`, `Event.Checkpoint(sequence, state, checks)`,
`Event.Transition(sequence, before, input, transition, checks)`,
`Event.Message(level, sequence_or_none, message)`, and
`Event.Finished(transitions, reason)` describe work the application already owns.
Top-level `Started`, `Checkpoint`, `TransitionEvent`, `Message`, and `Finished`
are the same variants; the existing top-level `Transition` remains the model
result. `Observer.observe(model, event)` is a structural synchronous protocol.
Events borrow payloads and freeze only their wrapper/check collection. Keep
payloads stable during observation; they are not detached snapshots. Diagnostics
do not execute reducers, initializers, effects, or checks, and do not certify
continuity or supply checked-token authority. Use the existing Recorder for that.

```python
import io
from statelessness import (Event, TextObserver, ObservationOptions, LogLevel,
                          SnapshotPolicy, EncodedPayloads)

writer = io.BytesIO()
observer = TextObserver(writer, "application", ObservationOptions(
    level=LogLevel.TRACE, snapshots=SnapshotPolicy.every(10)), EncodedPayloads(4096))
# actual and checked are the application's already computed values:
observer.observe(model, Event.Transition(sequence, before, input, actual, checked.checks))
observer.flush()  # Explicit; errors are the application's policy decision.
assert observer.into_inner() is writer  # Transfer without flush or close.
```

`ObservationOptions()` defaults to `LogLevel.OFF`, `SnapshotPolicy.NONE`, and a
1 MiB record limit. The ascending levels are `OFF/ERROR/WARN/INFO/DEBUG/TRACE`
(Rust-style `Off/Error/Warn/Info/Debug/Trace` aliases are also available).
Failed checks take Error precedence over Skipped checks at Warn. Ordinary
checkpoints are Trace, transitions Debug even when rejected/ignored, and start/
finish Info. Messages use their own level; Off messages are always filtered.
Filtering precedes formatter and codec work. Only configured Trace includes
payloads and passing check details; check failures do not suppress Trace payloads.

Snapshots use `SnapshotPolicy.NONE`, `SnapshotPolicy.every(nonzero_u64)` (or
`Every`), and `SnapshotPolicy.EVERY_TRANSITION`. At Trace, explicit checkpoints
include state with any non-None policy regardless of interval. Transitions format
input, ordered actual outputs, and then the selected post-state using absolute
sequence divisibility. Before-state is unused. `set_level` and `set_snapshots`
affect future events only; emit an explicit checkpoint when enabling snapshots.
Python integers cover the full u64 sequence/period domain; invalid values and
booleans are rejected. Gaps, duplicates, and reordered diagnostic events remain
the application's responsibility.

`PayloadFormat` callbacks `state/input/output(model, value, out)` synchronously
write bytes and return `None`. The callback writer expires after each call; no
retained lease may alter later records. `NoPayloads` writes `unavailable` and
needs neither codecs, copying, nor repr on model values. `EncodedPayloads(limit)`
uses the existing bounded `EncodeBuffer` dispatch, preferring `encode_*_into`,
including explicit `model_codec(...).bind(model)` declarations. It reuses a
bytearray and writes lowercase `hex:` bytes. Existing dispatch copies values;
legacy return-bytes encoders can allocate before the bound is enforced. Buffer
reuse promises neither Python capacity retention nor zero allocation.

Each record is bounded in UTF-8 bytes including its final LF, and staged before
any destination write. Rejected buffer writes stay sticky even if swallowed by
callbacks. Codec/formatter/record-limit errors publish nothing and the next event
can recover. `TextObserverError.stage` identifies `encode_input/output/state`,
`format_input/output/state`, `record_limit`, `sink_write`, `sink_flush`, or
`sink_poisoned` (generic invalid formatting/ownership has `format`/`sink`).
Sink short writes are completed; interrupted writes are retried. Zero/invalid
progress, write failure, and flush failure permanently poison the sink, including
when subsequently set Off. Writer failures can leave partial records. Publication
uses an additional bounded immutable byte snapshot so retained writer arguments
cannot alias reusable record storage. Limits do not bound callback allocations,
object copies, retained writers, process memory, or time spent in blocking I/O.

`get_ref()` exposes the caller-owned binary writer; `into_inner()` transfers it
and disables further observation without implicitly flushing/closing. Direct
writer use is the caller's responsibility. `sink_poisoned` reports permanent I/O
failure. Observers/formatters are synchronous and not thread-safe; nested observer
entry and reinitialization are rejected. Ordinary Python exceptions are contained
with stage attribution even if their string formatting fails; process interrupts
are re-raised, and I/O interrupts other than `InterruptedError` still poison.

Library text follows the pinned Rust field order, variant names, double-quoted
escaping, and common printable UTF-8 from the
[`observation-v1` contract](../spec/observation-v1.md). Python's Unicode printability
tables determine additional `\u{...}` escapes, so arbitrary Unicode/future Rust
Debug changes are outside exact byte compatibility. Library text stays on one
line; custom payload bytes are deliberately unsanitized and their formatter owns
escaping. Diagnostic text is not a replay artifact. The
[installed consumer](../examples/native-observation/python/README.md) reads shared
fixtures without importing source via path overrides. This remains an experimental
candidate; the source additions and tests await integrated Kyoto qualification.

## Candidate checked results and runtime recording

`check_initial(model, state)` checks the supplied checkpoint once, without calling
`initial_state`, `step`, metadata or codecs. `check_turn(model, before, input,
transition, sequence, policy=CheckPolicy())` checks an already executed turn once
and never calls the reducer. Both return a sealed complete result even when
properties fail. Checker or snapshot errors produce no token. Positive Python
integer sequences preserve the full u64 domain; zero, booleans, floats and overflow
are rejected. Sampling policies include explicit skipped checks and always yield
`full_checking=False`, even at a turn whose sampled state check runs.

`CheckedInitial` exposes read-only `model`, `state`, `checks`, and
`state_check_count`. `CheckedTurn` adds `before`, `input`, `transition`, `sequence`,
`full_checking` and `observation()`. `into_checks()` returns detached checks without
invalidating the Python token. Payloads are snapshotted together before checking;
check callbacks receive separate copies. Payload/check accessors and diagnostic
views return fresh detached values, while `model` retains the original model
reference. This requires faithful `deepcopy` behavior and deterministic, pure model
callbacks. It introduces no codec, hashing, equality or serialization requirement
for ordinary model values. These native ownership assumptions correspond to Rust's
borrowed-context determinism contract; Python cannot prove arbitrary application
clone fidelity or purity.

Public constructors, subclassing, dataclass replacement and serialization cannot
create authority. A caller-created `TurnObservation`, replay observation or check
collection is diagnostic evidence and cannot authorize checked recording. Sealing
protects the public API against accidental replacement; it is not a security
sandbox against hostile reflection or alteration of private engine code. Tokens
remain reusable across independent recorders; each recorder enforces ordering.

`Recorder(model, checkpoint, config=RunConfig(), max_steps=1024)` captures
application-owned transitions without executing them. `with_options` selects
`RecorderOptions(max_steps, max_retained_bytes, limits=BinaryLimits())`.
`with_checked_initial` and `with_checked_initial_at(initial, host_sequence, config,
options)` attach to sealed checks without rechecking. `observe(model, before,
input, transition)` performs full checking; `observe_checked(token)` uses the
bound complete batch with no checker calls. Sampled coverage, reordered/duplicate/
gapped sequences, metadata changes, encoded-before discontinuity and quota errors
freeze the previous coherent prefix. Metadata identity, rather than model object
identity, determines compatible handoffs.

The recorder retains a contiguous bounded ring, validating candidates before
eviction and promoting only evicted state-check prefixes into checkpoint checks.
Property failure freezes capture and cannot be overwritten by a later external
error; `stop_with_error(error)` also freezes a coherent prefix. `snapshot()` returns
a detached lossless `AuditTrace`; counters, retained size and binary export expose
the captured artifact. Retention budgets include complete binary framing, metadata,
checks, parameters and error-footer headroom, not process memory or callback
allocations. Engine and monitor provenance parameters are reserved. Host sequence
origins are recorded separately from local retained-step indexing.
`checked-results-v1` is experimental. This runtime recorder does not implement the
separate causal evidence/envelope recorder.

## Candidate causal evidence v2

`TraceV2(trace, checkpoint_after, provenance, steps)` wraps a lossless `AuditTrace`
with ordered `StepEvidence(sequence, state_check_count, causal)` rows. `Provenance`
contains run/epoch, optional manifest/source and ordered environment pairs.
`CausalContext` contains optional machine/generation/correlation/logical_time and
ordered `OutputOrigin(sequence, output)` references. All integers retain their
exact u64/u32 domains. Collection constructors take ordinary lists or tuples and
own tuple copies; closed-schema subclasses are rejected before field getters.
String operations use intrinsic UTF-8 semantics, with no application encode/copy
hooks. Host labels assert provenance; neither labels nor CRC authenticate it.

`EvidenceLimits` combines `trace=BinaryLimits()` with independent
`max_metadata_bytes=4194304`, `max_labels=1024`, `max_label_bytes=4096`,
`max_causes_per_step=32` and `max_total_causes=100000` budgets. `validate(limits)`
checks semantic termination, exact v1 quotas, complete-envelope size, phase rows,
checked sequence addition, unique earlier causes and retained output indices.
Links at or before the checkpoint describe an omitted prefix whose outputs are
unavailable. `check_phase(local_sequence, check_index)` returns `initial_state`,
`state`, `transition`, or `None`; local zero denotes the checkpoint.

`to_bytes(limits=...)`, `write(writer, limits=...)` and `write_raw_new(path,
limits=...)` emit the exact version-two envelope around unchanged v1 bytes and
canonical structural metadata. Writers preflight before publication. `Artifact`
wraps v1 or v2; `Artifact.read(reader, limits=...)` and `from_bytes(data, limits=...)`
parse bounded artifacts without executing a model or closing the caller's reader.
V1 later-turn phases remain unknown. Mutable input bytes are detached after header
quota admission; callers must not mutate inputs concurrently. Metadata decoding
has independent byte/element/depth/work/allocation accounting, separate from v1
item limits. Allocation accounting uses the explicit Rust64 logical schema request
weights, not Python heap measurements or a process-memory guarantee.

`TraceV2.replay(model, limits=..., allow_build_mismatch=False)` performs bounded
semantic replay and compares persisted phase boundaries at otherwise exact turns.
`TraceV2.verify_upgrade(model, audit_trace, provenance, checkpoint_after,
limits=...)` explicitly executes exact compatible replay to derive phase rows;
passive reading never invents them. Upgrade/replay do not execute external effects.

`EvidenceRecorder(initial_token, origin, config, options, provenance, limits)`
reserves the entire selected metadata allowance plus 32 envelope bytes before
creating its inner runtime recorder. Small total budgets require an explicitly
smaller metadata allowance. `observe(checked_turn, causal)` uses sealed complete
checks, validates causal references against currently retained outputs before
planned eviction, and commits the trace and phase journal together. Metadata,
cause-count and trace budgets may each force eviction. Snapshot phase/checkpoint
rows follow the exact same retained window. Capture never reruns reducers or
checkers; preparation errors freeze the coherent prefix and preserve the first
terminal reason. `snapshot()` returns detached owned evidence; `into_trace()`
consumes the native handle. Metadata ownership is canonicalized only after quota
admission and never invokes application `deepcopy` hooks. These are synchronous
APIs, with no asynchronous thread-safety or hard RSS claim.

`diagnostic_json(artifact, ExportPolicy(max_steps, max_bytes, property_ids))` returns
nonreplayable UTF-8 JSON. Payloads, property details, model/build identity and causal
labels are omitted. IDs survive only an exact explicit allowlist; Unicode strings
are not normalized. Initial checks remain present at zero turn allowance. Whole
output bytes and turn count are bounded. `export_diagnostic_new(artifact, path,
policy)` and raw new-file export precompute bounded bytes, create exclusively
without overwriting, request Unix mode0600 and fsync the new file. An error after
creation may leave an incomplete file and reports that explicitly. There is no
rename-atomic publication or directory-fsync claim. `causal-evidence-v2` remains
experimental and does not promote the four qualified portable profiles.

Candidate `canonical-value-codec-v1` provides native canonical value codecs through
`statelessness.value_codec`: strict fixed-width integers, bool, unit `()`, UTF-8
strings, `Some(value)`/`None` options, vectors, arrays, tuples and `OracleState`.
`encode_canonical(schema, value, maximum=...)` uses bounded `EncodeBuffer` leases;
`encode_into(schema, value, sink)` composes with preferred model codec hooks.
`decode_canonical(schema, data, limits=DecodeLimits(...))` freezes mutable byte
inputs after checking their byte quota. `Decoder.value(schema_or_callback)` guards
work and nesting; raw `schema.decode(reader)` and direct string/vector
`decode_length(width, reader)` deliberately do not add an outer guard. Length
widths are 1, 2 or 4 bytes. `reader.finish()` rejects trailing bytes.

The five independent decoder budgets are bytes, allocation, elements, depth and
work. Allocation counts logical Rust64 requests, not Python heap usage or RSS.
Collections require the element schema's explicit `Rust64Layout(size,
alignment=None, profile='explicit-cooperative-weight')`; unknown compound layouts
are never guessed. An explicit size-only descriptor is a cooperative accounting
weight, not a measured Rust layout claim. Top-level tuples and options do not
require an otherwise unused layout. Custom schemas may implement encoding and
decoding independently, and diagnostics `trace_schema()`/`trace_variant()` do not
establish model compatibility. These candidate APIs do not alter qualified
`PROFILES` or generate model codecs automatically.

`encode_raw(schema, value, sink)` and `encode_length_raw(schema, value, width,
sink)` expose the raw trait boundary with revocable leases: a returned validation
or callback error leaves accepted prefix bytes usable if the caller catches it.
Rejected buffer writes remain sticky. Built-in composition uses this raw boundary.
The stronger `encode_into` helper additionally poisons the buffer on callback
failure; this is an explicit native callback-lifetime extension. Top-level
`encode_canonical` never returns partial bytes on error.

Schemas expose `supports_encode` and `supports_decode` independently; compositions
propagate each direction from every field even for absent options, empty vectors
and zero-length arrays. A missing direction fails before encoding/decoding bytes.
Plain custom objects infer availability from their callable methods and may
explicitly declare boolean direction metadata; inherited `Schema` placeholders
confer no support. Length overrides also expose independent
`supports_encode_length`/`supports_decode_length`. Encoding never requires an
unused decoder or allocation descriptor. Custom schema capabilities must remain
stable after a composition is constructed.
Vector and array descriptors snapshot the optional element allocation weight at
construction. Rebinding a custom element's layout later cannot weaken an existing
collection's quota; a missing or invalid captured weight fails only when decoding
that collection. Encoding remains available without allocation metadata.
`encode_length_raw` and `decode_length_raw(schema, width, reader)` admit an exact
unsigned-byte width before any custom callback. Custom length codecs can accept
width 3; built-in string/vector codecs continue to accept only 1, 2 and 4. Raw
decoding adds no outer value guard and does not call `finish` automatically.

Optional finite research experiments live only in `statelessness.research`; they
are not imported into the basic public namespace. `compare_equivalence(model,
states, proposal, config, projection)` is disabled by default. It validates explicit
assumptions and proposal identity, reads metadata once, and requires both an
explicit enabled config and established assumptions before executing the model.
Models need metadata, enumeration, transitions and checks; no value codecs are
required. Required properties are explicit `(id, phase)` declarations, with phase
`state` or `transition`.

An enabled experiment owns snapshots of the declared finite domain, materializes
every declared edge once (including unreachable states), and rejects an unclosed
domain. Exact and candidate traversals then use only that private table. Ordered
input labels, effects, dispositions, checks and projected successors must match.
`ResearchEquality` supplies optional state/input/output/projection equality hooks;
otherwise native equality is used. Hooks receive detached snapshots and must
return an actual bool. No serialization or hash digest stands in for equality.
Cloning and equality remain cooperative, faithful and deterministic; arbitrary
callback execution/memory and malicious private mutation are not sandboxed.
`compare_equivalence_bounded` accepts `ResearchComparisonLimits` to set
explicit projection and exact-state equality call limits and returns the limits
and consumed calls alongside the ordinary report. Reaching a limit returns an incomplete report;
the original API uses fixed limits of one million calls for each relation and its
shared report format remains unchanged.

The result retains shortest BFS failure witnesses, pair prefixes, missing
properties and separate state/edge/pair budgets. Observed properties sort by ID
then phase; violation witnesses retain first BFS/check discovery order and first
failure details. Limits, callback errors, skipped checks and absent required
properties prevent qualification. `AgreesWithinDeclaredFixture` can retain real
property failures; it is bounded fixture evidence, not proof of application
correctness or a general reduction. `qualification_parameters` records proposal,
model build, assumptions and status, always with production pruning disabled.
The module never changes production search/replay equality or enables pruning.
Projection equality must be reflexive; invalid native keys such as NaN return an
incomplete result before traversal. An exact-index visitation guard also bounds
traversal if a cooperative relation later changes. These guards do not prove
symmetry/transitivity or stable application behavior. Identity-based object
models generally need explicit equality hooks compatible with faithful snapshots.
Typed ModelError messages are retained in incomplete limitations; other Python
exceptions receive safe contextual diagnostics. Successful reducer returns count
as materialized edges before native result validation or snapshot errors.

Explicit N13 declarations are opt-in via `statelessness.codec_declarations` (also
exported at package root). `ordered_record`, `tuple_record`, and `unit_record`
concatenate explicit fields; `tagged_enum` requires explicit U8/U16/U32 tags.
Fields choose ordinary, `length=1/2/4`, or adapter routes. Builders capture their
configuration and infer encode/decode independently; `require=("encode",)` asserts
availability without filtering other capabilities. Record/enum layouts remain
unknown unless `layout=Rust64Layout(...)` supplies an explicit logical weight.
`model_codec(state=..., input=..., output=...).bind(model)` preserves model
methods while replacing its eight codec hooks; output requires encoding only.
These APIs do not promote a compatibility profile or infer Rust object layout.

The package-root `ModelCodec[S, I, O]` remains the existing generic protocol.
The explicit declaration object is exported as `ModelCodecBinding`; it is also
available as `codec_declarations.ModelCodec`, alongside the `model_codec` factory.

`statelessness.debugger_watches.MarkerCollector` collects optional post-turn
markers. Its detail callback runs only when the marker can be admitted; retained
text is truncated at UTF-8 boundaries. Its byte limit uses a 48-byte logical
per-marker charge matching the 64-bit Rust layout, not Python heap usage or memory
allocated by callbacks. Use a collector from one thread and avoid reentrant calls
from the detail callback; `finish()` consumes it and returns an immutable batch.

`Network.machine_ids()` returns an ordered tuple snapshot of registered machine
IDs. `NetworkState.machine(id)` searches the canonical increasing-ID state and
returns `None` when no ID exists (previously it raised `KeyError`). The returned
state is not copied. If an application uses `None` as a state, inspect the
`machines` entries to distinguish it from an absent ID.
For compatibility the network's `machines` mapping remains mutable. Each
operation captures its registration in increasing ID order before invoking
callbacks and rejects an empty registration. Unlike Rust's immutable network
registration, changes made between operations change the model's semantics;
callers must avoid concurrent mutation while a snapshot is captured.
Network state equality copies both queues before machine values and comparison,
so queue copy hooks may run even when machine values compare unequal. Queue copy
errors are deferred until after machine comparison and queue length checks.
Successful queue and machine copies share a deepcopy memo. If a queue copy fails,
its partial memo is discarded before the machine values are copied.
Isolation depends on cooperative `deepcopy` hooks and is not atomic against
concurrent or cross-alias mutation during capture.
