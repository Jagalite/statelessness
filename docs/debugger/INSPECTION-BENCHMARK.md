# Inspection/watch diagnostic microbenchmark

Measured 2026-10-10, around 18:00 UTC, in the v1.1 feature work tree based on
`f3e00e79471b928d67ddb530c113d6d51af6f277`. This is an environment-specific sample,
not a universal overhead claim. Rerun the command at the final integration commit
when reproducing release qualification.

## Reproduction

```sh
# Select the installed Rust 1.95.0 qualification toolchain first.
cargo run --locked --offline -p statelessness-debug --release --example measure_watches
```

- rustc 1.95.0 (59807616e 2026-04-14), LLVM 22.1.2
- x86_64-unknown-linux-gnu, Linux 6.18.44, release/optimized profile
- No CPU model/performance portability claim; the final manifest records the
  visible logical CPU count
- One producer on one thread; no application reducer, real effects, exporter I/O,
  asynchronous scheduling, or network involved
- System allocator wrapper counts alloc/alloc_zeroed/realloc during each loop;
  setup and result formatting are excluded, deallocation is not counted
- Clock reads occur around the measured loop, not in watch/probe capture
- Values, registry/ring defaults, counts and saturation policies are specified in
  `crates/statelessness-debug/examples/measure_watches.rs`

## Sample results

| Configuration | Observations | ns / observation | Allocator calls | Calls / observation |
|---|---:|---:|---:|---:|
| 64 runtime-disabled watches | 100,000 | 89 | 0 | 0.000 |
| One changed scalar watch | 10,000 | 580 | 70,008 | 7.001 |
| 64 watches sharing one scalar query | 2,000 | 56,354 | 518,004 | 259.002 |
| 100,000-element subtree, page request 100 | 2,000 | 24,987 | 982,006 | 491.003 |
| Scalar watch plus failure ring snapshot every 100 observations | 2,000 | 1,846 | 43,048 | 21.524 |
| Runtime-disabled scoped probe | 100,000 | 22 | 0 | 0.000 |
| One scalar probe plus drain | 10,000 | 441 | 70,001 | 7.000 |
| 64 probe subscriptions plus drain | 2,000 | 21,966 | 526,005 | 263.002 |
| One saturated probe sink, drop-newest | 10,000 | 479 | 70,001 | 7.000 |

The saturated sink retained one event and counted 9,999 drops. Filtered, sampled,
truncated, exporter-failed, abandoned, inspection-failed, baseline-evicted and
observation-gap counts were zero. This demonstrates independent explicit drop
accounting, not lossless delivery.

Retained watch ring payload bytes at completion:

- Disabled: 29,696, consisting of configuration lifecycle events created before
  the measured loop, with no value capture during the loop
- One scalar: 631,000
- Many shared scalar watches: 631,000
- Large subtree: 4,194,192, below the default 4 MiB / 4,194,304-byte bound
- Failure scenario's current ring: 631,000, with an independent bounded failure
  copy retained separately

The large collection remains paged/partial; it is not fully serialized. Active
scalar watchers currently allocate a projection on each evaluation. Shared query
capture avoids repeated inspector evaluation but destination baselines/events
still own their bounded copies. No semantic speedup or production tail-latency
claim follows from this microbenchmark.

## Other gates

`inspection`, `inspect_derive`, and `watches` integration tests exercise exact
integer representation, paging, privacy, work budgets, disabled laziness,
concurrent configuration boundaries, lifecycle/TTL, sampled unknown comparisons,
independent drop/gap accounting and byte-for-byte off/on exact recording/replay.
The separate macro script includes 32 compile-fail fixtures and a cold-offline
renamed-dependency consumer. Full engine/macro/protocol/host qualification remains
an integration gate, not a conclusion from these timings.

## Final source-bound repetition

The initial full run at source `e80044c` is preserved under
`validation/debugger/historical/initial-qualification/`. The post-review source
`5ef7f6a` was rerun on Rust 1.95 and 1.90; current results are in
`validation/debugger/qualification.json`, `logs/watch-probe-benchmark.log`, and
`msrv/logs/watch-probe-benchmark.log`. These shared-host synthetic runs do not
establish portable timing or compiler-comparison claims.
Its disabled watch/probe cases again allocated zero times (100 ns/watch boundary,
46 ns/scoped probe on this run). Active scalar watch/probe cases measured 672/646
ns respectively, about seven allocations each. The saturated sink again retained
one event and reported 9,999 drops. Use the machine-readable log for all cases;
the earlier table remains a historical repetition, not a claimed universal bound.
