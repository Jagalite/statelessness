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
```

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

## Binding prototypes

See [bindings/README.md](bindings/README.md) for native Swift and browser/Wasm
fixture commands. Native, Node, and actual browser execution are separate checks.
A successful Rust test run alone does not qualify foreign runtime integration.

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
