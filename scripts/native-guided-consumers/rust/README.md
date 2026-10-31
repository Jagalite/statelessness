# Rust public guided reference consumer

This is a separate Cargo project and workspace. Its normal dependency is the
public `statelessness` package, imported by its library name `stateless`. JSON
dependencies belong only to this qualification consumer. The production crate
remains dependency-free.

The declared consumer MSRV is Rust 1.90. The installed harness should use the real
toolchain root `/Users/jagatranvo/.rustup/toolchains/1.90.0-aarch64-apple-darwin`.
The initial source build used the active stable toolchain; its lockfile is retained.
Locked dependency MSRV declarations are recorded in `receipts/dependency-msrv.txt`;
those declarations are static compatibility evidence, not a successful MSRV build.

`src/main.rs` deserializes fixture inputs into types with **no expected fields**.
It calls `guided::fuzz_observed` or `explore::fuzz_observed` with actual public
feedback and observer implementations. `src/fixture.rs` implements the specified
table model. There is no implementation of corpus admission, mutation, the fuzz
loop, SplitMix, replay, or shrinking in this consumer.

Custom feedback uses the real bounded `FeatureBuffer` and records the result of
each attempted push. One implementation leaves `initial_features` entirely
unimplemented to exercise its default no-op. State and exact-state feedback
delegate to the public `StateFeedback` and `guided::exact_state` helpers through
a transparent tracing wrapper; opaque pushes inside those helpers are not traced.

All builds and execution must use the default shared Kyoto queue:

```sh
# From this directory. Preserve the returned job ID and show/log output.
CARGO_INCREMENTAL=0 python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- cargo build --manifest-path Cargo.toml
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- ./target/debug/native-guided-rust --host-checks --output receipts/host-actual.json
# Wait for conformance/guided-v2/FREEZE.json before candidate comparisons.
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- ./target/debug/native-guided-rust --corpus ../../../conformance/guided-v2/corpus.json --output receipts/corpus-actual.json
# Optional standalone comparison (not submitted during this work; combined
# installed harness owns host checks and corpus execution for final qualification):
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 verify.py --binary target/debug/native-guided-rust --corpus ../../../conformance/guided-v2/corpus.json --output receipts/runtime-01 --host-checks
```

`--case ID` selects one fixture. The corpus output is:

```text
{schema:"stateless-guided-v2-results/1",engine:"rust",
 cases:[{id,actual:{report,error,trace,generator_rng,witness_replays,shrink}}]}
```

The orchestrator compares every actual field with the independently frozen
expected field. The oracle's private `rng_audit` is explanatory and is not an
actual public callback output. Unknown fixture schema/source identities and
unknown modes fail; a missing selected case fails. Execution errors from the
engine become the fixture's `error`, while consumer/replay/normalization failures
exit nonzero. The consumer never writes the corpus or expected outputs.

`verify.py` is a focused Rust-only runner, separate from the installed multi-engine
orchestrator. It validates all freeze hashes before executing, captures commands
and raw process logs, compares all six normalized fields, and rechecks input and
binary hashes afterward. It refuses an existing output directory so failed or
superseded attempts remain reviewable. It never invokes the authoring oracle.

Each retained passing corpus prefix is recorded through `execution::record`
using a fresh model, then replayed through `execution::replay_with_observations`
using another fresh model. The normalized states, outputs, dispositions and checks come from
that replay, and exact matching, verified step count and passing termination are
asserted. Passing witnesses are never passed to the shrinker. Failure witnesses
are replayed too. When the fixture requests `shrink_config`, the public
`explore::shrink` result is normalized in full, and its minimized witness is
replayed. Original and minimized failure replay data follow retained passing
entries in `witness_replays`; every entry includes the actual replay outcome,
failure-reproduction flag and verified step count. Replay/shrink controls and
callback counters are independent of search.

`--host-checks` independently calls `guided::fuzz`, `fuzz_with_limits`,
`StateFeedback`, `exact_state`, and the public RNG. It checks defaults, default
initial feedback, non-Clone/non-Send/non-Sync collision keys, metadata boundaries,
owned report/prefix cloning, compact versus exact-state features, sticky overflow
with callback-error precedence, pre-cancellation/validation order, panic unwind,
and zero/singleton RNG domains. The expected caught panic appears in stderr as
`host boundary sentinel`; success is determined by the process exit and complete
host-check result. Safe Rust prevents a callback from retaining a writable
`FeatureBuffer` beyond its borrow; these callbacks are synchronous. Counts do not
bound callback allocations or process RSS. No N06/N10 qualification is implied.

For installed qualification, copy this consumer into an isolated installation
directory and replace only the `stateless.path` dependency in that staged
`Cargo.toml` with the absolute extracted package directory. Keep its `[workspace]`
and JSON dependencies. The installed harness owns package extraction, source and
artifact identity receipts, and multi-engine comparison. A source-path build is
only local reference evidence, not installed-package qualification.

Actual requested commands, job IDs, terminal statuses, and raw logs are retained
in `receipts/`. `docs/native-parity/N05-RUST-REFERENCE.md` records qualification
status and limitations. No build/run is claimed merely from these instructions.
