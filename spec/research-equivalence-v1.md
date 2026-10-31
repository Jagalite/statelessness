# research-equivalence-v1 (candidate)

This opt-in finite-fixture research contract mirrors public
`stateless::research::compare_equivalence` and `qualification_parameters` from
[research.rs](../src/research.rs). It never changes production exploration,
equality, replay, generation or pruning. Agreement concerns only the explicit
fixture and caller-declared assumptions, including any property failures; it
does not mean that application properties passed or a broader reduction is sound.
No source syntax or Rust internal layout is a native API requirement.

## Inputs and admission order

Inputs are a model with finite ordered enumeration, explicit exact state list,
projection, Proposal and ExperimentConfig. Metadata is required even for a
non-codec model; native bindings may accept an explicit metadata supplier. Never
invent a codec requirement. Model, input, output and projected-key equality must
be explicit faithful native equality; JSON serialization, display labels and
object identity are not replacements. Custom callbacks are synchronous and
cooperative, not a process resource sandbox.

Validate all seven assumption statements first, then nonblank proposal name and
build and positive u32 version, then required properties for nonblank IDs and
duplicate exact `(id,phase)` pairs. A property can use the same ID in both State
and Transition phases. Assumptions.established is a caller/reviewer declaration,
not a proof. Blank means Rust str.trim with Unicode White_Space: U+0009..000D,
0020,0085,00A0,1680,2000..200A,2028,2029,202F,205F,3000. U+001C..001F and FEFF
are not blank characters. Validation checks emptiness without normalizing or
trimming stored strings. IDs and metadata retain exact UTF-8 identity, including
distinct NFC and NFD spellings.

Invalid declarations return an error before metadata or model callbacks, even
when disabled. After declaration validation, call metadata exactly once, construct
the report, then return Disabled if config.enabled=false. Otherwise return
AssumptionsNotEstablished if assumptions.established=false. Neither executes an
initial state, checks, enumeration or projection. Only enabled, established runs
check empty/overbudget state count and exact duplicates. State-budget refusal is
an Incomplete report before duplicate equality; duplicate exact states are an
error. Limits are exact unsigned Rust64 counters; proposal version is u32. Native
host argument/type failures are separately classified, not invented Rust calls.

## One immutable finite graph

Call initial_state once, locate it in the declared exact list, then process every
declared state in list order, including unreachable states. For each: invoke its
preferred append state checker once, obtain input_iter once, and consume ordered
inputs. Every iterator pull precedes the edge-limit check, including one that
discovers exhaustion or a limit. At executed_edges==max_edges, a yielded input
ends materialization before step; exhausted iteration at an exactly full budget
can complete. No extra loop or reduction arm reruns those callbacks.

For each yielded input below the limit, step once. A successful reducer return
increments materialized_edges before closed-domain target lookup and transition
checking. Thus outside-domain successors or transition-check errors include that
edge; reducer errors do not. Preserve ordered outputs, disposition including
reason, and ordered complete transition-check batch. State and transition check
batches remain separate. Noninitial target state checks are not rerun on edges;
their stored batch is used later by traversal.

Initial, state-check, iterator-construction, step and transition-check Result
errors return Incomplete with their exact message as limitation, the completed
edge count, and no exact/reduced reachability, projection or pair comparisons.
No partially materialized graph becomes a completed experiment. Metadata,
projection, Eq/Hash and iterator next are infallible Rust signatures: native
exceptions or malformed values there need an explicitly documented fail-closed
host policy and separate native tests, not fabricated shared Rust Result errors.
Rust panic containment is not promised.

Only a completed graph computes projection once for every declared state in
list order. Both traversals and all comparisons then use the same graph and
captured keys; no reducer, checker, enumerator or projection re-execution.

## Traversal and witnesses

Both traversals are FIFO BFS beginning at the declared index of initial_state.
Exact admission compares state indexes. Reduced admission keeps the first
encountered representative of each projected key. States arrays are discovery
order of those representative exact indexes, not a sorted set of declared IDs.
Every outgoing edge of a dequeued representative counts, including duplicate
targets and self loops; traversals continue through property failures.

Observe the initial stored state checks first. On each traversed edge, append its
input to the source's shortest prefix, observe target state checks, then transition
checks, before deciding whether to enqueue the target. Already visited/collapsed
targets still contribute check observations. This is why a reduced traversal may
observe a state violation on a collapsed target whose outgoing edges it never
explores.

observed_properties is sorted by exact ID bytes then State before Transition on
ties. Violation witnesses are first-discovery order, one per exact ID+phase,
retaining the first details and shortest BFS input prefix. They are not globally
sorted by phase or ID. Skipped checks count on every observation, saturating at
u64::MAX, and still count as observed properties. Passing/failed duplicates do
not erase earlier failures; ordered batch identity matters in pair comparison.

missing_violations preserves exact violation order when its ID+phase is absent
from reduced violations. missing_properties preserves proposal requirement order
for properties absent from exact traversal, not merely absent materialized rows.
Checks on unreachable declared states are materialized but not observed by BFS.

## Pair comparison and result precedence

Compare each reachable same-key pair in exact BFS state order: outer position
then later positions. Capture both exact shortest prefixes and stop each pair at
its first mismatch in this strict order:

1. Ordered state check batches: `state checks differ`.
2. Outgoing counts: `enabled input counts differ`.
3. For each corresponding edge in order: input equality/order (`enabled input
   labels or order differ`), ordered outputs (`ordered effects differ`), full
   disposition (`transition disposition differs`), ordered transition checks
   (`transition checks differ`), then target keys (`successor equivalence classes
   differ`).

Edges compare sequentially; a later edge's output mismatch does not outrank an
earlier edge's successor mismatch. No input/output symmetry renaming, queue
reordering, history compaction or timing abstraction is inferred.

The pair limit is charged only for reachable equal-key pairs. Reaching an exact
limit is not incomplete unless another same-key pair remains. Zero can qualify
if no such pairs exist. Both reachabilities and missing-property/failure sets
are computed even when pair comparison is cut short.

Status priority after completed materialization is Counterexample for any pair
mismatch, lost violation, lost projected reachable class or reduced-only failure;
then Incomplete for untested pairs; then Incomplete for missing required
properties, any skipped checks, or an empty requirements list; otherwise
AgreesWithinDeclaredFixture. A concrete counterexample outranks simultaneous
pair exhaustion, skipped checks or missing requirements. Agreement retains
reachable failures; it never enables pruning or certifies those properties.

## Reports and qualification strings

Preserve model metadata, proposal and config, counts, optional reachabilities,
counterexamples, missing lists and the exact limitation. Early Disabled or
AssumptionsNotEstablished reports retain the initial no-production limitation;
later completed agreement retains it too. Runtime limits/errors replace it with
the corresponding source message. Reports from partial graph materialization
must not publish an exact/reduced traversal from a previous invocation.

qualification_parameters returns the exact 17 `research.*` strings authored in
the corpus: proposal/name/version/build, model build, Rust status spelling,
declared/materialized/compared counts, seven unchanged assumptions, limitation
and `research.production_pruning_enabled=false`. This map summarizes evidence,
not an executable configuration or hidden toggle. Preserve strings exactly;
native naming conventions do not change status or numeric string values.

## Shared fixture wire and proof boundary

The independently authored JSON corpus has defaults for metadata/proposal/config,
and cases with ordered finite `scenario.states`, initial ID, rows of state checks
and input edges, and keys parallel to states. IDs are portable u32 integers;
input/output values are exact strings; u64 config/count fields are decimal strings.
Scenario annotations explaining history/queues are not a projection algorithm.
Per-case overrides deep-merge assumptions; requirements lists replace whole lists.
Only listed error_at events inject the ModelError `fixture error at EVENT`.

Expected normalization is `{error,report,callback_log,qualification_parameters}`.
Callbacks log `metadata`, `initial`, `state:N`, `inputs:N`, `next:N:INDEX`,
`step:N:LABEL`, `transition:N:LABEL`, `projection:N`. Pull indexes start at zero
and include exhausted None/edge-budget lookahead. Log strings are fixture hook
evidence, not a required product tracing API. Error declarations have null report
and qualification map; Incomplete graph reports are ordinary successful results.

The Rust reference must implement only table-backed model callbacks and call
real public compare_equivalence/qualification_parameters, never mirror the BFS,
comparison or verdict algorithm as its oracle. Expected reports/callback logs
are independently authored before execution. Native host-negative cases are
reported as public rejection or explicit static exclusion by each language; they
do not inflate Rust runtime counts. Builds and executions require Kyoto after
fixture review approval. Candidate evidence does not promote any profile or
change the existing broad qualification matrix.
