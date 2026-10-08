# Test your own application

For configurable runtime logging and full replay capture, see
[logging.rs](logging.rs): `cargo run --offline --example logging`.

[application.rs](application.rs) is a self-contained starter using the public
library API. Its bounded counter stands in for your application's pure reducer.
It includes a deliberately buggy reducer, a corrected reducer, a regression
gate, canonical codecs, seeded fuzzing, shrinking, and a replay executable.

From a checkout, using Rust 1.90 or later:

```sh
cargo test --locked --offline --example application
cargo run --locked --offline --example application -- check
cargo run --locked --offline --example application -- find counter-failure
```

`check` exits 0 only after exhausting the corrected model's graph without
violations or skipped checks. `find` intentionally exits **1** after saving a
failure. Run it separately from commands chained with `&&` or a shell that exits
on errors. It requires a new directory and saves `original.sttrace` before
shrinking, then `minimized.sttrace`. If a later operation fails, the directory
and any evidence already written remain; the example is not transactional.
The printed shrink termination describes a bounded heuristic, not a guarantee
of global minimality.

Replay in separate processes with the same application model:

```sh
cargo run --locked --offline --example application -- replay counter-failure/original.sttrace
cargo run --locked --offline --example application -- replay counter-failure/minimized.sttrace
cargo run --locked --offline --example application -- replay-fixed counter-failure/minimized.sttrace
```

Both original-model replays exit **0**, reporting exact agreement and
`failure reproduced: true`. Successful replay of a known bug is not a passing
application regression test. `replay-fixed` explicitly permits a different build
and exits **3** at the changed transition. Model/property/codec versions still
must match. Other errors exit 2; incompatible replay identities exit 4.
The installed `stateless` CLI only knows its bundled request-lifecycle model;
use this application-specific executable to replay these counter artifacts.

## Adapt the starter

1. Add `stateless = { package = "statelessness", version = "0.1" }` to your
   application's `[dev-dependencies]`. For unpublished/local development, replace
   `version` with `path = "/path/to/stateless"`. Copy `application.rs` into your
   application's `examples/` directory and run it with the commands above.
2. Replace `reduce` with your actual pure transition function. Put clocks,
   randomness, pending work and completion identities in state or inputs. Return
   effects as output data; don't perform network or filesystem effects in `step`.
3. Define independent named properties. This example has only a state bound;
   applications with effects should also implement `check_transition` to check
   required, missing, or incorrect outputs. Use `WithOracle` when checking needs
   independently maintained history.
4. Enumerate all permitted inputs within your declared finite model. `Auto`
   turns that domain into random generation and causal validation for shrinking.
   For large domains, implement `Generate` directly. Fuzzing samples behavior;
   even `CasesCompleted` does not establish graph exhaustion.
5. Keep `application_regression` as the regression gate, or move the model and
   test into your application's `tests/`. Ordinary `cargo test` does not run an
   example's unit tests: use `cargo test --example application` explicitly.
   The gate rejects failures, incomplete searches, and skipped checks. Choose
   explicit search budgets suitable for your application.
6. Replace the one-byte codecs with canonical encodings. Validate restored
   states and inputs, including tags, lengths and trailing bytes. Update semantic
   versions when their contracts change. Replace the starter's source fingerprint
   with a fingerprint covering your application's relevant code and configuration;
   the example fingerprint covers only this source file and the fixed/buggy flag.

The starter deliberately keeps orchestration visible. Persisted traces contain
concrete inputs and observations, so replay does not depend on rerunning the RNG.
Keep both artifacts when investigating a finding. To retain a full search audit,
also persist your search configuration/report and the shrink report/history.

For live observation, use `monitor::Recorder` on transitions already executed by
the application. This starter exercises simulation and replay, not live runtime
integration or production performance.
