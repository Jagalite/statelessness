# Swift public guided consumer

Standalone SwiftPM executable for the native `Statelessness` product from a
copied **root** `Package.swift` plus `swift/` tree. Set
`STATELESS_SWIFT_PACKAGE` to that package root when resolving or building this
consumer. The manifest has no implicit checkout fallback and never imports the
Rust-backed ABI package under `bindings/`.

Status: source ready; this implementation handoff has not compiled or executed
the consumer. The parent owns the immutable snapshot and installed qualification
run. Do not run duplicate Swift builds against the shared checkout.

Run every build and execution through the default shared Kyoto queue. For
example, from an external writable copy of this consumer directory, with the
native package separately installed at `/absolute/installation/swift-package`:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env STATELESS_SWIFT_PACKAGE=/absolute/installation/swift-package swift build --product GuidedConsumer -j 2
# After that job succeeds, and after the independent corpus FREEZE.json exists:
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- .build/debug/GuidedConsumer --corpus /absolute/frozen/conformance/guided-v2/corpus.json --output /absolute/receipts/swift-actual.json
```

Retain each submission's job ID, `kyoto.py show JOB_ID`, and `kyoto.py logs
JOB_ID` output with the parent's receipts. A build receipt alone is not runtime
or installed-package qualification.

The executable accepts a positional corpus path or `--corpus PATH`, optional
`--case ID`, and optional `--output PATH`. Without `--output`, stdout is exactly
one JSON document; adapter failures go to stderr with a nonzero exit code.
`--case` supports the harness's fresh-process-per-fixture convention. Output is:

```text
{schema:"stateless-guided-v2-results/1",engine:"swift",
 cases:[{id,actual:{report,error,trace,generator_rng,witness_replays,shrink}}]}
```

`CorpusSpec` and `CaseSpec` decode fixture inputs only. They have no `expected`
or `rng_audit` properties. The adapter neither compares nor generates goldens;
the independent frozen-corpus checker compares all six actual fields. It also
accepts the harness's input-only copy of the corpus. The parent must establish
and verify the freeze and package/source identities before comparison.

The fixture calls public `fuzz` or `guidedFuzz`, using the native RNG, real bounded
`FeatureBuffer`, and full-history `SearchObserver`. Custom feedback traces every
attempted push and propagates or ignores errors as specified. A null initial hook
uses the public `Feedback` default without a traced callback. Projected and exact
feedback use public `StateFeedback.feedback()` and `exactStateFeedback`, wrapped
only to trace callback boundaries. Value keys distinguish exact UTF-8 strings,
numbers, and integer arrays; constant hashing deliberately exercises collisions.
Exact-state feedback receives the full custom `TableState` (`depth`, `total`),
whose `Hashable` implementation also uses a constant hash when requested by the
fixture. The same hash policy carries into fresh recording, replay and shrink
models, including decoded checkpoints.
All state/input/output values are owned Swift value snapshots. No admission,
mutation, fuzz loop, RNG, replay, or shrink algorithm is reimplemented here.

Checkers use `checkStateInto`/`checkTransitionInto` so an appended batch remains
observable if its callback subsequently throws. Enum spellings and field names
are normalized explicitly to the published schema. The only error text mappings
are these leading native engine contexts, verified against `src/explore.rs` at
the corpus's pinned authority commit and Swift's `CheckSink.swift`/`Model.swift`:

| Native Swift prefix | Rust fixture prefix |
| --- | --- |
| `state check: ` with observer error phase `initial_state` | `initial check_state: ` |
| `state check: ` with observer error phase `state` | `check_state: ` |
| `transition check: ` | `check_transition: ` |
| `transition: ` | `step: ` |

All callback message text after those prefixes is preserved verbatim. Other
errors, including configuration errors, remain unchanged so a semantic or
diagnostic mismatch remains visible to qualification.

Guided invalid mutation uses the source diagnostic
`mutation_percent must be in 0..=100`. Ordinary Swift fuzz retains its existing
`invalid fuzz configuration` diagnostic. The adapter does not rewrite either.

Each retained corpus entry is independently recorded with public `record`, then
replayed on another fresh fixture using `replayWithObservations`. The adapter
requires completed recording, exact replay, matching build, the full verified
step count, and no reproduced failure. States, outputs and separate ordered check
batches come from replay observations; empty batches remain present. Failure
witnesses must reproduce their recorded failure too. Only `shrink_config` selects
the separate public `shrink` call; its controls and callback counters are fresh,
its complete report is normalized, and its minimized failure is replayed.
The revision 3 schema supplies actual Accepted/Rejected/Ignored dispositions
through `Transition`, appends them to observer/feedback traces, and records them
from public replay observations. Each witness includes the actual validated replay
outcome, failure flag and verified-step count. Selected original and minimized
failures follow the FIFO passing corpus witnesses, even when their inputs match.
Only null denotes no callback error; empty error messages remain errors, including
their contextual trailing space. No expectation field influences these paths.

The parent's installed harness may supply its own manifest with the explicit
copied package path. Keep the executable target name `GuidedConsumer` and source
directory `Sources/GuidedConsumer` when doing so. No native-only callback lifetime
or concurrency claims, N06 campaigns, or N10 assurance are made by this consumer.
