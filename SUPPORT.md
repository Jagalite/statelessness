# Installation and support status

Stateless is pre-1.0: API stability is not yet promised across minor versions.
The Rust library and optional debugger expose the broadest interfaces. Independent
native ports implement four portable profiles; Rust-backed binding packages expose
the ABI 1 subset. The [capability/version matrix](docs/CAPABILITIES.md) separates
implemented source APIs, historical qualification and registry availability.
A release qualifies the documented operations on the tested platforms;
it does not require every roadmap feature. Test API and artifact compatibility when
upgrading. Report problems through the repository's GitHub Issues with the
version, toolchain, platform, configured limits and a minimal model. Review
application payloads for private data before attaching traces.

## Choose an entry point

| Consumer | Installation | Scope |
| --- | --- | --- |
| Rust application tests | Published `stateless = { package = "statelessness", version = "=0.2.0" }`, or local path for current source | Checking, exploration, fuzzing, shrinking, recording, replay, oracles and campaigns |
| Rust runtime observation | Same dependency in dependencies | Checking and bounded recording of already executed transitions |
| Rust debugger | Matching local core and `crates/statelessness-debug` path dependencies | Sessions, replay/forks, inspection, diagnostics and cooperative live control |
| Custom replay command | Adapt the [application starter](examples/README.md) | Your model and codec; the bundled CLI only understands its request fixture |
| Native Python, Go, TypeScript, Swift | Follow [PORTABLE.md](PORTABLE.md) for local package instructions | Four portable v1 profiles; independent implementations |
| C | Build `target/bindings/native` with `scripts/build-bindings.py` | ABI 1 byte-model recording, replay and enumeration |
| Rust-backed Swift | Local SwiftPM `StatelessNative` product from the generated Swift package; link matching native library | Same ABI 1 subset; synchronous, thread-confined sessions |
| Rust-backed JavaScript | Local npm package/tarball from `target/bindings/browser` | Same ABI 1 subset via Wasm; no npm runtime dependencies |
| Rust-backed Go | Local module `bindings/go` and matching native library | Same ABI 1 subset through scoped cgo sessions; [guide](bindings/go/README.md) |

Rust 1.90 is the minimum supported version. Versions 0.1.0 and 0.1.1 were
published previously. Core and macro 0.2.0 archives were verified as available
and unyanked on 2026-10-12. They name an older source commit than this checkout;
the shared version number does not make the APIs identical. Add
`statelessness-macros = "=0.2.0"` for the published companion, or use matching
local paths for current-source APIs. See [macro support and limits](docs/MACRO-STATUS.md).

## Validation status

The following local environment evidence predates 0.2.0; release notes
identify the hosted run and artifacts that qualify each release.

Local qualification uses macOS arm64 and Rust 1.90.0. Node tests use Node 23.5.0;
real browser checks passed in T3's Chromium 152/Electron 44 preview. Swift package
validation targets macOS with Swift 6.3.3. The C smoke compiles the public header
and calls the actual native library.

The verification workflow configures a representative pull-request/push gate and
a full nightly/manual/release matrix: Rust 1.90/stable on Linux, macOS and Windows,
C/Node bindings on Linux/macOS, Swift bindings on macOS, and separate portable and
native-port workflows. These new gates have not yet been remotely qualified for
M0; configured jobs are not evidence of passing runs. Swift/Linux evidence for the
independent native engine is separate from the Rust-backed Swift package. See
[native qualification](spec/NATIVE-PORTS-QUALIFICATION.md) and
[debugger acceptance](docs/debugger/ACCEPTANCE.md) for their historical scope.
Safari, Firefox, iOS, Android and Swift/Windows are not qualified by these records.
There is no universal native archive or XCFramework; npm/Swift registry availability
was not verified in this audit.

The helper scripts require Python 3.11+; binding checks also require a C compiler,
Node/npm, and Swift for the relevant runtime.

Run `python3 scripts/check-consumer.py --allow-dirty` while developing to verify
an unpacked crate in an isolated application. For a clean release omit
`--allow-dirty`. `--registry` instead downloads the exact manifest version and
checks that published package. None of these commands publish anything.

## Application adoption evidence

The [adoption measurement](validation/adoption-2026-10-07.json) records an isolated
snapshot of Playscale's actual core reducers and existing model adapters tested
against this engine. Its job graph exhausted 20 states and 274 edges within the
declared attempt/identity bounds; its seeded job run checked 100,000 transitions.
Processing/publication and viewing models also passed.

Five 100,000-transition sessions exercised cancellation, delayed/stale completions,
retry and bounded runtime recording. Each retained 1,024 transitions, evicted
98,976, exported 358,848 encoded bytes and replayed exactly in a fresh process.
No application source is redistributed here. Supply a separate checkout:

```sh
python3 scripts/check-playscale.py /path/to/playscale
```

| Operation | Median per transition | Observed sample range |
| --- | ---: | ---: |
| Production reducer | 18 ns | 18–26 ns |
| Reducer plus checking | 170 ns | 157–210 ns |
| Reducer plus bounded recording/checking | 12.6 µs | 1.54–24.7 µs |

These are five whole-session samples with no excluded warmup. The host was busy;
recording and disk-sync timings varied substantially. Export plus `sync_all`
took 0.39–11.55 seconds and was measured separately. Whole measurement-process
peak RSS was 2,932,736 bytes on this host; it is not incremental recorder memory.
Encoded bytes are not an allocator or RSS bound.

This validates the production reducer/adapter boundary under supplied schedules.
It does not qualify Playscale's HTTP server, SQLite transactions, FFmpeg, OS
scheduler, or live media workloads. Larger-state/branching synthetic benchmarks
remain separate from this small-state application baseline.

## Remaining release gates

The registry release is 0.2.0; subsequent source changes require their own
qualification and a new package version before publication. Run the full remote
verification matrix against the committed candidate and follow
[RELEASING.md](RELEASING.md). Publication is a separate explicit release action.
The source already supports bounded recorder checkpoints and check-only periodic
policies. Configurable checkpoint cadence, selective checks inside recorded
traces, generic foreign fuzz/shrink APIs, broader runtime qualification and the
new M0 measured performance baseline remain pending work.

## Current improvement checkout

The improvement branch implements optional assurance manifests/coverage, a real
background controller with seeded application fault controls, independent
requirement-change reports, explicit multi-machine scheduling, bounded lifecycle
retention, a causal v2 evidence envelope, diagnostic projections/reports and macro
adoption conveniences. Candidate benchmark/storage/reduction fixtures are opt-in.
See [implementation status](docs/improvement/STATUS.md) and the
[capability matrix](docs/CAPABILITIES.md) for exact source scope.

These APIs are not represented by the older registry 0.2.0 artifacts or historical
M0 receipts. The current local policy permits Rust compilation only; runtime
qualification, actual mutation kills, measurements, hosted CI and independent
review of requirements remain separate gates. Native `paired-v1` is a candidate
for Rust/Python/TypeScript, not a promoted portable profile; Go/Swift do not support
it. No new mandatory runtime dependency or production optimization is implied.
