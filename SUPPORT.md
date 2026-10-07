# Installation and support status

Stateless is pre-1.0: API stability is not yet promised across minor versions.
The Rust library is the complete interface; binding packages expose the ABI 1
subset. A release qualifies the documented operations on the tested platforms;
it does not require every roadmap feature. Test API and artifact compatibility when
upgrading. Report problems through the repository's GitHub Issues with the
version, toolchain, platform, configured limits and a minimal model. Review
application payloads for private data before attaching traces.

## Choose an entry point

| Consumer | Installation | Scope |
| --- | --- | --- |
| Rust application tests | `stateless = { package = "statelessness", version = "0.1" }` in dev-dependencies | Checking, exploration, fuzzing, shrinking, recording, replay, oracles and campaigns |
| Rust runtime observation | Same dependency in dependencies | Checking and bounded recording of already executed transitions |
| Custom replay command | Adapt the [application starter](examples/README.md) | Your model and codec; the bundled CLI only understands its request fixture |
| C | Build `target/bindings/native` with `scripts/build-bindings.py` | ABI 1 byte-model recording, replay and enumeration |
| Swift | Local SwiftPM `StatelessNative` product from the generated Swift package; link matching native library | Same ABI 1 subset; synchronous, thread-confined sessions |
| JavaScript | Local npm package/tarball from `target/bindings/browser` | Same ABI 1 subset via Wasm; no npm runtime dependencies |

Rust 1.90 is the minimum supported version. The crates.io `statelessness` 0.1.0
download was tested with the new application starter on 2026-10-07. The working
tree's new guides and binding packages are unreleased changes; they are not
included merely by installing the existing registry release.

## Validation status

Local qualification uses macOS arm64 and Rust 1.90.0. Node tests use Node 23.5.0;
real browser checks passed in T3's Chromium 152/Electron 44 preview. Swift package
validation targets macOS with Swift 6.3.3. The C smoke compiles the public header
and calls the actual native library.

The manual verification workflow configures Rust 1.90/stable on Linux, macOS and
Windows, plus C/Node checks on Linux/macOS and Swift on macOS. Configured jobs are
not evidence that remote runners have passed. Safari, Firefox, iOS, Android,
Swift/Linux and Swift/Windows are not qualified. There is no universal native
archive or XCFramework, and no npm/Swift registry release.

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

The next candidate is 0.1.1; 0.1.0 is already published. Run the remote verification
matrix against the committed candidate and follow [RELEASING.md](RELEASING.md).
Publication is a separate explicit release action. Configurable checkpoints,
recorded selective checking, foreign fuzz/shrink APIs and broader runtime and
performance qualification remain roadmap work.
