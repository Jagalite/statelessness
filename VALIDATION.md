# Validation

This document describes the repository's verification entry points and their
limits. Historical experiment results are not evidence for the current source
snapshot.

For the initial `statelessness` 0.1.0 release preparation on 2026-10-07,
`cargo fmt --check`, `cargo test --locked --offline`, and
`cargo clippy --locked --offline --all-targets -- -D warnings` passed locally.
The tests used a fresh target directory. Cargo's package file listing contained
only the public source, fixtures, and documentation, with no local experiments.
Package verification and registry publication are separate release gates; see
[RELEASING.md](RELEASING.md).

## Rust checks

Use Rust 1.90 or later:

```sh
cargo test --offline
cargo clippy --offline --all-targets -- -D warnings
cargo fmt --check
cargo run --offline --example counter
cargo test --locked --offline --example application
cargo run --locked --offline --example application -- check
```

The [application starter](examples/README.md) additionally exercises seeded
failure discovery, original/minimized artifacts, and application-specific replay.
Its example unit tests are selected explicitly; they are not part of plain
`cargo test`.

The manually triggered [verification workflow](.github/workflows/verify.yml)
runs Rust 1.90.0 and stable on Linux, macOS, and Windows, including fresh-process
starter replay and offline package verification. It does not publish anything.
This is a configured validation matrix, not a claim that all jobs have passed.
Like the release workflow, it does not run automatically on pushes or pull requests.

The counter example intentionally detects a violated bound. The CLI's deliberate
failure and replay workflow is described in [README.md](README.md#try-it).

The [integration tests](tests/) cover execution and replay, graph exploration,
causal shrinking, automatic and feedback-guided generation, campaign accounting,
resource limits, runtime recording, trace corruption, CLI behavior, and the C ABI.
The [campaign lifecycle tests](src/campaign/lifecycle/tests.rs) explore bounded
coordination schedules with an independent ledger and deliberate fault controls.
They do not model operating-system thread or channel internals.

## Benchmarks

```sh
cargo bench --offline --bench engine
cargo bench --offline --bench scaling
cargo bench --offline --bench campaign
```

The [benchmarks](benches/) exercise small-model execution/checking/replay,
synthetic state scaling, and campaigns. Record the exact source revision,
toolchain, host, configuration, and repeated results when reporting measurements.
Separate reducer cost, checking, serialization, search, and I/O. Synthetic
results do not establish production performance; byte accounting is not RSS.

## Binding packages

See [bindings/README.md](bindings/README.md) for native C/Swift and browser/Wasm
package commands. Native, Node, and actual browser execution are separate checks.
A successful Rust test run alone does not qualify foreign runtime integration.
Build distributions with `python3 scripts/build-bindings.py`; run
`node scripts/check-js-package.mjs` to pack/install an independent npm consumer.
Run `python3 scripts/test_readiness.py` for bundle file selection, versioning,
replacement and rollback regressions. Node adapter tests also cover large input
batches, aggregate packet limits and invalid string identities.
The manual binding jobs compile a C header smoke, run Node adapter/consumer checks,
and run Swift package tests plus fresh-process fixtures on macOS.

## Adoption and long-session measurements

`python3 scripts/check-playscale.py /path/to/playscale` snapshots a separately
supplied application's core and this engine into a temporary directory, tests
those snapshots, then measures reducer, checking, bounded recording, export and replay.
Application dependencies must already be cached for offline Cargo use. The runner
does not modify the application checkout or redistribute its source. Each
measurement exports a bounded trace and replays it in a fresh process; reports
include snapshot hashes, the resolved application lockfile, toolchain, host,
timings and process resource output.
See [SUPPORT.md](SUPPORT.md) for the measured 2026-10-07 baseline and its limits.

## Interpreting results

- No violation found is distinct from exhausting the reachable graph. Inspect
  search termination, configured bounds, skipped checks, and callback errors.
- Exact replay establishes agreement for the recorded sequence and identities;
  it does not prove all possible behavior correct.
- Properties and input generation are application-defined. Missing properties,
  omitted events, or an incomplete model can hide bugs.
- Deadlines are cooperative, and modeled concurrency is distinct from live
  network, I/O, scheduler, or long-session qualification.

Release validation should record the final source revision and the checks
actually run against it. Do not carry forward test counts or performance numbers
from unrelated source snapshots or local experiments.
