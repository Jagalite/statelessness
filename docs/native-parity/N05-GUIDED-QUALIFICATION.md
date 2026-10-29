# N05 native guided exploration qualification

Status: implementation and qualification in progress. The current public APIs
are candidates; no installed five-engine comparison has passed yet. This work
does not promote a portable profile or close N06 campaigns, N10 assurance, M4,
the broad 144 platform/feature cells, or the full native parity goal.

## Public implementation

All four independent native engines share their ordinary checked fuzz kernel
with a guided candidate policy. Feedback metadata identifies application feature
semantics separately from the `feature-corpus-v2` admission/mutation algorithm.
Feedback equality resolves hash collisions and does not change model checking,
replay equality, or state pruning. No Rust ABI or fixture interpreter implements
the native search algorithm.

| Engine | Public search | Feedback and examples |
| --- | --- | --- |
| Go | `GuidedFuzz`, `GuidedFuzzWithLimits`, `GuidedFuzzObserved` | [`Feedback`, `FeatureKey`, `StateFeedback`, `ExactStateFeedback`](../../go/GUIDED.md); [example](../../go/examples/guided/main.go) |
| Python | `guided_fuzz` with keyword controls/observer | [`Feedback`, `StateFeedback`, `exact_state`](../../python/GUIDED.md); [example](../../python/examples/guided.py) |
| TypeScript | `fuzzGuided`, `fuzzGuidedObserved` | [`Feedback`, `FeatureIdentity`, `StateFeedback`, `exactStateFeedback`](../../typescript/README.md); [example](../../typescript/examples/guided.ts) |
| Swift | `guidedFuzz` with limits/observer | [`Feedback`, `StateFeedback`, `exactStateFeedback`](../../swift/README.md); [example](../../swift/Examples/Guided) |

The public bounded feature sink counts duplicate emissions. Callback errors
precede swallowed sink errors; failed observations teach no features. Retained
passing prefixes use FIFO eviction and remain replayable. An overlong discovery
can later acquire a retainable witness with zero newly tracked features. No
passing witness is claimed to have been shrunk. Reports expose finite-run
novelty and retention counts, not exhaustive state coverage or hard RSS bounds.

Native cloning, sink revocation, reentrancy, synchronous return types, exceptions
and callback panic/throw handling are documented in each language. Those host
contracts are explicit native choices, separately tested from Rust semantics.
The [source contract](N05-SOURCE-CONTRACT.md) documents the actual Rust authority;
the earlier [audit](GUIDED-CAMPAIGN-AUDIT.md) remains source-only input.

## Evidence gates

The [independent corpus](../../conformance/guided-v2/README.md) is authored from
pinned Rust source before candidate comparisons. Superseded expectations and
authoring receipts remain preserved. The [installed harness](N05-QUALIFICATION-HARNESS.md)
requires fresh packaged public consumers for actual Rust, Go, Python, TypeScript
and Swift, exact callback/RNG/report comparisons, passing and selected failing
witness replay, and the existing portable baseline. Native boundary suites have
separate scope. Compilation alone is never reported as test execution.

Current native evidence is retained at [Go](../../go/receipts/n05/README.md),
[Python](../../python/receipts/n05-guided/README.md),
[TypeScript](../../typescript/receipts/n05/README.md), and
[Swift](../../swift/validation/n05/README.md). Their snapshot identities and
individual terminal results govern each claim; earlier passing revisions do not
qualify later source changes. [Rust reference status](N05-RUST-REFERENCE.md)
distinguishes its source-path build from installed-package execution.

Review findings and corrections are tracked in the
[implementation review](N05-IMPLEMENTATION-REVIEW.md). Parent runtime isolation
is checked read-only against the pinned Git objects. Source manifests record
membership, file types, modes and hashes; immutable manifest references are
reused. Exact commands/toolchains, source/package/corpus identities and raw logs
must accompany the final executed results. No parent edits, push, pull request,
publication or profile/default promotion are part of this work.
