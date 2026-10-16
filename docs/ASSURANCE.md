# Optional assurance manifests

`stateless::assurance` audits declared verification adequacy without changing
`Model::step`, input enumeration, exact state equality, or reducer results. Models
using the existing explorers keep their current API and behavior; their adequacy
is **not assessed** unless this optional layer is selected.

A manifest is a separately maintained requirements catalog. It declares stable
property IDs and phases, expected input families, explicit exclusions with
rationales, environment/domain assumptions, cover goals, a required search
outcome, and manifest/model/property provenance. Bounds are declarations reviewed
against the model; they do not automatically configure the model. Actual search
budgets and the executable's `ModelMetadata` are recorded independently.

The machine-readable formats are
[`assurance-v1.schema.json`](../spec/assurance-v1.schema.json),
[`assurance-report-v1.schema.json`](../spec/assurance-report-v1.schema.json), and
[`assurance-campaign-v1.schema.json`](../spec/assurance-campaign-v1.schema.json).
The Rust core has no JSON-parser dependency. Build a typed `Manifest` directly or
validate/deserialize the JSON in a host adapter. `Manifest::validate` also rejects
empty identities, duplicate property IDs within a phase, duplicate family/goal
IDs, and exclusions with empty reasons. Schema structure validation alone cannot
check every semantic uniqueness constraint.

## Connect a model

Implement `Coverage<M>` separately from `Enumerate` and `Generate`. Its `observe`
callback receives the actual before state, input, result, and (when requested)
concrete input prefix. Classify the input with `hits.family("name")` and mark a
scenario only when its predicate is satisfied with `hits.goal("id")`. No callback
is used to determine enabled transitions or select search candidates.

Use `assurance::enumerate(model, manifest, coverage, config, limits)` or
`assurance::fuzz` / `assurance::guided_fuzz`. The return has two layers:

- The outer `Result` rejects an invalid manifest or mismatched model identity.
- `AssuredRun.search` retains the ordinary engine result or model error.
- `AssuredRun.assurance` retains observations even if the engine returns an error.

`Manifest::require` allows manual property metadata additions. `add_domain`
copies existing `DomainDescriptor` assumptions and entry bounds. An independent
required-property catalog is deliberate: deriving requirements solely from
checks observed during a run would certify a deleted property by deleting it
from the expectation as well.

Observer hooks run after state and transition checks and before duplicate-state
or admission-budget handling. An edge to an already visited state is observed,
as is a checked edge that violates a property. Partially emitted property checks
before a callback error are retained, but an incompletely checked edge cannot
satisfy a cover goal. There is no reducer re-execution or second search engine.
Coverage callback errors are recorded separately and do not alter the search's
property results, state graph, or termination. Hits emitted by a callback that
then errors are discarded.

## Interpret the dimensions separately

| Dimension | Possible results |
| --- | --- |
| Required property (ID **and phase**) | Passed, failed, skipped, missing |
| Search | Graph exhausted, sampled cases completed, depth limited, budget limited, interrupted, timed out, failed, not started |
| Cover goal | Observed, unreachable under the declared model, not reached before stop |
| Input family | Observed, missing, explicitly excluded, exclusion violated |
| Callback diagnostics | Model/check/coverage errors, separate from property violations |

A callback error prevents a green assessment even when some properties passed.
Undeclared failed or skipped properties are retained and also prevent strict
success. Observing an excluded family is a policy contradiction; forgetting a
family is `Missing`, never automatically certified as covered. An unclassified
checked input or an undeclared coverage ID also blocks strict success.

Select `report.assess(AssurancePolicy::Strict)` explicitly. This requires all
expected properties to pass without skips, all required families and goals to
be observed, no callback errors, and the manifest's required search completion.
Each required property must appear in **every completed batch of its phase**;
state batches include the initial checkpoint. `counts.missing` records omissions,
including a property emitted initially but silently omitted on later edges.
Missing counts survive campaign merges and a later pass cannot erase them.
Conditional properties must emit an explicit passed or skipped observation,
rather than disappear. Duplicate property IDs within one phase/batch make the
assurance observation ambiguous and block strict success, even if both pass;
the same ID in state and transition phases remains distinct. Ordinary explorers
without assurance retain their existing ordered duplicate-check behavior.
An empty expected-property catalog or a catalog without required input families
cannot pass vacuously. The assessment includes reasons and a narrowly scoped
confidence statement. An unmet cover goal is an **adequacy failure**, not a new
application-invariant violation. `to_json()` emits deterministic report data;
it does not silently select an assurance policy or add a green flag.

Assessment and merge also validate that the public report's derived property,
family and goal catalogs agree with the manifest and retained observations.
`observed_families` and `observed_goals` retain the callback hit identities
separately from the derived result rows; unknown hit identities are rejected.
Clearing a required result or changing its derived status cannot manufacture a
passing assessment. This checks internal consistency, not authenticity: callers
who fabricate underlying observations or provenance can still fabricate evidence.
Persisted or externally supplied reports need source/receipt verification.

## Graph completion and histories

`GraphExhausted` describes only the exact declared finite graph. It cannot prove
that the environment omitted no real-world events or that an unencoded
requirement holds.

A `GoalScope::Transition` predicate must depend only on the actual before state,
input, and result. It may inspect history **encoded in exact model state**. If all
edges are exhausted without reaching it, its status is
`UnreachableUnderDeclaredModel`; this still fails the required adequacy goal.
Any budget/depth/deadline/cancellation stop leaves an unobserved goal incomplete.
Observation errors also prevent an unreachable classification.

A `GoalScope::History` predicate may inspect the actual input prefix. Fuzzing
provides its current case's prefix. BFS provides a representative shortest prefix
for each explored edge, reconstructed only when a history goal is declared.
BFS graph deduplication does not enumerate every path around cycles. Consequently
an unobserved history goal **never** becomes unreachable from graph exhaustion.
Encode a relevant finite monitor/history state in model equality if a complete
reachability claim is needed. Traversal order is never presented as a history.

## Campaigns and partial results

`assurance::run_campaign` uses the existing scheduler and job seeds. Each worker
gets independent coverage state; the original job sink still receives original
counterexamples, factory errors, model errors, and panics. Partial observations
survive model errors and unwinding panics. Factory failures and jobs never
started are explicit missing assurance reports. Reports already produced survive
a sink failure; the original campaign report preserves that sink error.
The merged assessment also incorporates campaign termination, delivery failures,
and incomplete accounting, even when every individual job finished successfully.

`merge_reports` sorts by job ID, rejects duplicates or different manifests/build
identities, unions observed scenarios, and retains every per-job report. Missing
or interrupted jobs remain visible and cannot be washed out by a completed
sibling. A merged report certifies graph exhaustion only when every expected job
reported graph exhaustion. Individual search budgets remain in the per-job
reports, and the aggregate records requested/reported job counts. For existing
custom/guided campaign hosts, use `Observer` with `explore::fuzz_observed` or
`guided::fuzz_observed`, then merge the explicitly expected job IDs.

Guided reports include the corpus algorithm, every guidance budget, and feedback
name/version/build alongside random seed and fuzz budgets. Campaign aggregates
record their master seed, job range, workers, buffer, stop policy and delivery
counts; per-job reports retain the derived seeds.

No cross-language assurance profile or cryptographic attestation is implied by
this Rust library/JSON format. Callback purity, correct scope declarations,
accurate provenance, and independent requirements review remain host obligations.
