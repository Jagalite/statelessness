# ABI v2 workload sources

No workloads or benchmarks in this document have been executed for this change.
Numeric regression budgets remain pending repeated baseline measurements. Sources
can be compiled through Kyoto, but test/benchmark executables must not be run in
the current workflow.

The matched model is a 32-state modulo counter with ordered inputs `[1,2]`, one
output carrying the prior counter value, a state-validity check and a transition
check. Enumeration must exhaust 32 states and 64 transitions. Codecs encode the
counter as little-endian u32, input as u8, output as little-endian u32 and the
payload as trailing state bytes. Metadata is `abi-v2-matched-counter`, build
`benchmark-v1`, with all semantic versions 1.

| Fixture | State payload | Additional state-check work |
| --- | --- | --- |
| cheap | Empty | None |
| large | 65,536 zero bytes | None |
| expensive | Empty | 4,096 wrapping-u64 digest rounds |

Each digest starts at the counter and computes
`((digest XOR i) * 0x9e3779b97f4a7c15 + 1) mod 2^64`. This is consumed only to
retain deterministic work. It never changes the model's checks or transitions.
Mutable payloads require real snapshots; language ownership/copy representations
must be stated in receipts when comparing their costs.

Separate operation timings cover:

- Observation of `0 + 2 -> 2`, output `0`; two passing checks, no reducer call.
- BFS with state/transition/depth bounds `64/256/64`; expected `32/64` counts.
- Record 64 alternating `[1,2]` inputs; replay verifies those 64 steps exactly.
- Fuzz with seed `0`, one case, 64 steps, 256 transitions and no mutation. Enable
  the failing property at state `>=24`; generation chooses `rng.index(2)+1`.
- Shrink that prepared failure with 256 attempts and 4,096 transitions; input 2
  simplifies to 1. Failure validation must remain explicit.
- Ring observation of 64 alternating inputs, retaining 16 steps within 16 MiB.

Registration, fixture construction, preparation of replay artifacts/original
shrink witnesses and session teardown are outside the timed operation. Result
release belongs to each operation. Root counts must return to their prepared
baseline; observation and ring paths do not invoke a reducer.

Workload sources are in `benches/abi_v2.rs`,
`bindings/go/v2/benchmark_test.go`, `bindings/swift-v2/Benchmarks/main.swift`,
`python/benchmarks/abi_v2.py` and `bindings/browser/benchmark-v2.mjs`. The Python
and JS drivers emit raw timing/callback samples with the explicit engine hash.
Those raw documents are deliberately not accepted as full performance receipts.
Go's allocation counters measure Go allocations, not Rust allocation or RSS.
Equivalent limits apply to other language-local profiling tools. Fixture callback
counters also differ in whether iterator advancement is represented as an explicit
closure; they are diagnostic counters, not uniformly instrumented ABI dispatch
counts. Qualification must capture a consistently defined callback metric.

Future qualification must attach whole-process/native allocation and peak RSS
measurements, retained-memory evidence, repeated timing samples, exact source and
package identity, runtime/target identity and reviewed budgets. Use
`compare-abi-v2-benchmarks.py` only with completed measurements. Portable baseline
engines expose observation/BFS/record/replay; advanced operation baselines need a
measured native Rust or prior-v2 reference. Unsupported cells are never zero-cost
observations. ABI 1 byte models and host-value models should have separate cells.

The independent-engine corpus remains the semantic baseline. Workload assertions
and a successful compile cannot substitute for executing that corpus or proving
historical/installed-consumer behavior.
