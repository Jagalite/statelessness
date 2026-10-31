# N06 Rust reference r1: queued, not executed

Base `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`, branch
`t3/native-campaign-runtime`. All work is confined to the assigned checkout.
No production Rust/N05 source edits, commits, staging or promotion.

`source-manifest.json` pins 135 source/dependency-input files by path, type, mode,
size and SHA-256. Manifest SHA-256:
`b9d4dcdcfc003c046e505ac1de94b4adad2fb37ec34c951f33ee24298317cc18`.
The private snapshot is ignored by Git; each copied input was verified identical
to its captured source bytes and set read-only. Preserve this snapshot and target
until every queued job and its later runtime evidence are reconciled. Do not
recreate this directory in place or point the queued commands at mutable source.

Owner UUID: `998c92d7-4b7a-4c1f-9028-6686060d0661`.
Shared Kyoto: `/Volumes/seed1/Projects/kyoto/kyoto.py`, default state/profile.
Every submission used `--cwd` equal to the absolute snapshot path and
`--resource` equal to the absolute target path. `CARGO_TARGET_DIR` is that same
target, `CARGO_BUILD_JOBS=1`, `CARGO_INCREMENTAL=0`.

Requested commands, executed from `snapshot`:

```text
cargo run --locked --offline --manifest-path conformance/fixtures/native-campaign-rust/Cargo.toml --bin native-campaign-public
cargo test --locked --offline --manifest-path conformance/fixtures/native-campaign-rust/Cargo.toml --lib --message-format=json
cargo test --locked --offline --test campaign --test native_campaign_reference --message-format=json
```

The public run additionally captures
`N06_EXPECTED_AUTHOR=<absolute snapshot>/conformance/fixtures/native-campaign-golden.py`.
That calls the independent strict JSON comparator, preserving exact types, all
fields, row/array order, callbacks and job identities. No expected result is
derived from runtime output. The emitted 49 public rows and panic diagnostics
will remain in the Kyoto log; expected caught panic hooks on stderr alone do not
establish a failed run or a pass. Terminal exit status and comparator checks do.

| Purpose | Job ID | Effective behavior |
| --- | --- | --- |
| Public reference runtime | `6ac33655baf9486895c626676a9740ff` | Cargo run, pending |
| Private adapter compile | `5ea2d04832ab4cc0acb08d05e8e1384c` | `--no-run`, pending |
| Public/host tests compile | `c47fa958741f4502beac1404b613ad1d` | `--no-run`, pending |

Exact submit and last show responses are retained as `*-submit.json` and
`*-show.json`. Kyoto logs:

```text
/Users/jagatranvo/.local/state/kyoto/6ac33655baf9486895c626676a9740ff.log
/Users/jagatranvo/.local/state/kyoto/5ea2d04832ab4cc0acb08d05e8e1384c.log
/Users/jagatranvo/.local/state/kyoto/c47fa958741f4502beac1404b613ad1d.log
```

After each compile succeeds, parse its Cargo JSON compiler-artifact messages for
the exact test executable path. Hash that artifact and record toolchain identity,
then submit that exact binary with `--nocapture --test-threads=1` through Kyoto.
Private full run should set `STATELESS_SELF_MODEL_ARTIFACTS` to a fresh existing
directory; the original unchanged fault test writes original/minimized traces
create-new. Submit a second fresh-process invocation filtered to
`persisted_fault_trace_replays_when_supplied`, with `STATELESS_SELF_MODEL_REPLAY`
pointing to the emitted minimized trace. Keep source/package/binary/trace identities
and terminal statuses distinct. Do not rerun or claim any of these as already done.

Pending: compilation, actual public reference, new and baseline host tests,
private codec/self-model/fault execution and fresh-process trace replay. Final
installed cross-language evidence belongs to the root. Swift's known N05 feedback-
metadata exception classification differs from the retained Rust Panicked row;
this is an open dependency, not an accepted normalization or pass.
