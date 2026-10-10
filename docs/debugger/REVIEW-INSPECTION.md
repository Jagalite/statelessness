# Independent pre-PR review: inspection, watches, macros and logging

Reviewed 2026-10-10 against implementation commit
`461a369eb161d3924e9e9dd1e72633e122f144f7`, including the full relevant diff from
`f3e00e79471b928d67ddb530c113d6d51af6f277`. This review did not treat the existing
qualification report or earlier PASS logs as evidence of current correctness.

The authoritative requirements are the repository's
[v1.1 design](../Statelessness-Rust-Debugger-Design-v1.1.md), especially sections
4, 6, 9, 13, 14, 17 and M3/M3a. Compatible Library-derived state-watch requirements
were checked against the reconciliation in [CONTRACTS.md](CONTRACTS.md) and the
implemented [inspection/watch contract](../DEBUGGER-INSPECTION.md).

## Scope and outcome

Reviewed `inspect.rs`, `watches.rs`, `diagnostic.rs`, macro `inspect.rs`, and the
macro `codec.rs`/`lib.rs` changes. Added the independently written integration
suite `crates/statelessness-debug/tests/pr_review_inspection.rs`. No PR, commit,
push, dependency installation or external-service change was performed.

Four classes of correctness defect were found and resolved. Eight independent
regressions fail against a clean archive of the pre-review commit; all fifteen
tests pass with the fixes. The larger focused suite passes 87 tests. There are no
known unresolved correctness findings in the owned code after this review.

## Resolved findings

### I01: Trigger labels erased watch comparison uncertainty

`PropertyFailure` and `ValueMatched` replaced the initial/gap/sampled event kind
before `difference` and completeness were computed. A first value incorrectly
became `Added`; a failure after sampled-away changes could become `Unchanged` with
complete status. The comparison provenance is now computed before applying the
trigger label. Forced failure capture remains selected and unsampled, without
inventing observations for intervening boundaries.

Regressions cover initial failure, a sequence gap, a sampled change-and-restore,
and a first equality-trigger match.

### I02: State watches compared different origins

A watch reset on model/machine changes but reused a baseline across simulation,
live or replay origin changes. Origin is now part of the source-continuity check.
The next value has `Baseline(SourceChanged)` and `Unknown` difference.

### I03: Local probes compared unrelated source identities

Probe baselines and threshold state survived changes to run/origin and to
model/machine/entity/correlation metadata when the selector was unscoped. An equal
first value from a different instance could be silently suppressed. The producer
now tracks bounded selected-source identity and resets baseline, hysteresis and
cooldown with `ProbeBaselineReason::SourceChanged` on an identity change.

Identity retains at most four already validated 256-byte labels plus fixed
headers per selected producer. Unselected/disabled producers allocate no identity,
unchanged identities are not recloned, and labels are not newly exported. Source
changes do not increment the missing-observation health counter unless there is
also an actual transition gap. Reconfiguration still establishes its own baseline.

### I04: An inconsistent completeness tag could certify incomplete values

Public handwritten nodes marked `Complete` could previously make redacted,
opaque, unavailable or truncated nodes compare equal. The same applied to explicit
unstable map ordering, shortened string/byte previews, inconsistent child counts,
partial-page metadata and incomplete descendants.

`InspectNode::is_complete` now checks these intrinsic semantics as well as the
completeness tag. Container counts must describe all returned children; page
metadata must describe the whole container; intrinsic incomplete kinds remain
unknown. This is conservative display equality, not model or replay equality.

## New and repeated evidence

The saved logs are in `validation/debugger/`:

- `pr-review-inspection-before.log`: clean `git archive` of `461a369` plus the
  independent test file, **7 passed / 8 failed**, expected exit status 101
- `pr-review-inspection-after.log`: **87 passed**, including the independent
  fifteen, existing inspection/derive/watch/sink suites, the two-producer
  controller model and previous review regressions
- `pr-review-inspection-compiled-out.log`: **40 passed** across the independent,
  diagnostic and derive suites with `compiled-out-diagnostics`
- `pr-review-inspection-macros.log`: cold-offline renamed dependency consumer
  plus **32 expected compile failures**, each with its expected diagnostic and no
  proc-macro panic
- `pr-review-inspection-parity-{enabled,disabled}.log`: normalized initial
  state/checks/steps/termination output is identical; assertions verify three
  reducer calls and seven checks per run, including saturated capture
- `pr-review-inspection-clippy.log`: strict all-target/all-feature Clippy passes
  for the debugger and macro crates
- `pr-review-inspection-costs.log`: fresh release microbenchmark; disabled watches
  and disabled scoped probes each execute 100,000 boundaries with **zero allocator
  calls**; the saturated sink retains one record and reports 9,999 drops
- `pr-review-inspection-environment.log`: Rust 1.95.0, LLVM 22.1.2,
  x86_64-unknown-linux-gnu, Linux 6.18.44
- `pr-review-inspection-source.sha256`: exact reviewed source/test/benchmark
  hashes, including the benchmark's MSRV-compatible safety-comment formatting

The independent tests also exercise:

- Root versus field-authorized consumers, one evaluation per identical query,
  and no unauthorized fields in text, JSONL, custom sinks or sidecar output
- Typed inspector errors and exporter I/O errors without retaining arbitrary
  exporter error text; one failing exporter does not consume another sink's event
- Probe, schema, inspector and exporter panics propagating to an explicit host
  boundary; already staged records from an unwound turn are marked incomplete
- Disabled, filtered and unauthorized paths skipping schema/value callbacks and
  lazy closures
- Failed projection invalidating the comparison baseline and stale boundaries
  remaining rejected
- Struct and enum display metadata/redaction changes preserving exact codec bytes

Existing suites were rerun for paging, UTF-8 truncation, exact large integers,
work/depth/node/byte budgets, redacted-field non-access, schema-change pausing,
baseline eviction, TTL, lifecycle generations, effective boundaries, independent
drop reporting, selectors and threshold resets. These assertions inspect actual
results rather than trust the presence of a test name in an older report.

## Reproduction

Select the qualification Rust toolchain, then run from the repository root:

```sh
cargo test --offline -p statelessness-debug --test pr_review_inspection \
  --test inspection --test inspect_derive --test watches --test diagnostic \
  --test diagnostic_selectors --test diagnostic_controller_model --test review_debugger
cargo test --offline -p statelessness-debug --features compiled-out-diagnostics \
  --test pr_review_inspection --test diagnostic --test inspect_derive
python3 scripts/check-inspect-derive.py
cargo clippy --offline -p statelessness-debug -p statelessness-macros \
  --all-targets --all-features -- -D warnings
cargo run --locked --offline -p statelessness-debug --release --example measure_watches
cargo run --locked --offline -p statelessness-debug --example instrumentation_parity
cargo run --locked --offline -p statelessness-debug --features compiled-out-diagnostics \
  --example instrumentation_parity
sha256sum --check validation/debugger/pr-review-inspection-source.sha256
```

For red/green reproduction, extract `git archive 461a369` to a separate directory,
copy only the independent test file into its companion `tests/` directory and run
that test there. The independent file deliberately compiles on both versions.

## Acceptance boundaries and remaining qualification gaps

- Inspection/formatting callbacks remain cooperative. This review does not claim
  a process-memory cap, preemption of blocking callbacks or sanitization of an
  arbitrary handwritten callback that directly discloses secrets. Explicitly
  redacted generated fields are never accessed; typed framework error paths and
  all implemented diagnostic sink forms were tested for disclosure.
- Arbitrary callback panics intentionally propagate. A host that catches one
  must define continuation and its panic-hook policy; the tests do not claim
  automatic panic recovery or redaction of text printed by application code.
- Exact trace bytes retain semantic data, including secrets. Display redaction
  is not authorization to share a raw trace.
- Snapshot/revision authenticity and protocol cursor invalidation belong to the
  owning session/protocol adapter. A bare `Inspect` call accepts a caller-supplied
  snapshot identity. Protocol, replay and live-control acceptance are separately
  reviewed; this report is not a substitute for those gates.
- The review's measurements cover Linux/x86_64 with Rust 1.95.0, one producer,
  no exporter I/O and no model execution. They do not establish a portable latency
  bound, scheduling equivalence, zero active-capture cost or production overhead.
  The integration owner separately owns Rust 1.90/MSRV and whole-workspace gates.
- Optional ecosystem adapters and native-debugger/platform interoperability are
  not qualified by these tests. No undocumented support claim is added.
