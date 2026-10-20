# Native Python Statelessness

Experimental, dependency-free **Python implementation**, not a Rust/FFI binding.
Python >=3.10. Applications retain their own state types, callbacks, and codecs.
This package implements the `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and
`trace-json-v1` profiles in `../spec/README.md`. It does not imply full Rust feature
parity: guided search, runtime ring recording,
campaigns, composition helpers, and the binary `.sttrace` format are not provided.

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
The optional `check_transition(before, input, transition)` callback returns
additional `Check` values. Checks and outputs are ordered. A `ModelError` is a
broken callback/adapter, not a failed property. Rejected and ignored transitions
are still checked; rejection-preserves-state is application policy, not imposed
by the framework.

`check_observed` checks a transition already executed by the application and
never calls `step`. The caller checks its initial state separately. Periodic
checking marks omitted checks as skipped rather than passing.

## State and cost contract

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
callback allocations. Callback errors raise `ModelError`, never count as property
reproduction. Search completion does not prove exhaustive coverage or global
minimality. These APIs implement the experimental [`search-v1` candidate](../spec/search-v1.md)
and consume its hand-authored TSV corpus in tests; they do not expand `PROFILES`.

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
