# Public Rust finite research reference

This slice calls `stateless::research::compare_equivalence` and
`qualification_parameters` with a table-backed `Model + Enumerate`. JSON parsing,
callback logs, and report projection are adapters. The reference does not
implement traversal, reduction, pair comparison, witness selection, or verdicts.

The independently authored frozen input has 67 shared public Rust cases and 10
native-only host-admission cases. Native-only cases are explicitly excluded from
Rust runtime counts. All source/input identities are recorded before and after
qualification. No agreement enables production pruning.

Build job: `c2def9cef475478e82fa2844cc78626a`.
Requested command:

```text
cargo build --manifest-path validation/corpus/Cargo.toml --bin native-research
```

The actual qualification is a separate Kyoto execution after a successful build.
Job receipts and losslessly compressed raw logs are archived alongside this file.
No installed consumer smoke is claimed by this slice.

## Terminal outcome

Build `c2def9cef475478e82fa2844cc78626a` succeeded with exit 0. Separate actual
execution `cdc81e17fe894f6084979e244f84823e` succeeded with exit 0 using:

```text
/Volumes/seed2/Projects/stateless-native-parity/validation/corpus/target/debug/native-research --corpus /Volumes/seed2/Projects/stateless-native-parity/conformance/research-equivalence-v1.json
```

All 67 shared cases compared the complete independently authored expected error,
report, callback log and qualification strings. The 10 host-negative native-only
cases were explicitly excluded from Rust runtime counts. No source or golden
corrections were required. There were no failed build or execution attempts in
this slice; `build-pending.json` preserves the earlier queued state.

Frozen corpus SHA256:
`a1dd99679878eae28bd1203a4dc51b2c8a307e058d043ce5567172db566f39aa`.
Spec SHA256:
`bbaffbcb09f24e5e85bfa3ca59ed7ff5b3d242f8c086b91af43f7373ca022649`.
Binary SHA256:
`bd206783f4cbe058241ceb228805528c601efdb038df902a6b3060323d204352`
(1,919,744 bytes).

`identity-before.json`, `identity-before-run.json`, and `identity-after.json`
record exact adapter/core/input identities, plus the executable before and after
execution. All overlapping source/input identities and all before/after runtime
identities match. `build.json` and `qualification.json` preserve requested and
effective commands and terminal scheduler evidence. `result.json` is the actual
program receipt. `log-ledger.json` records original and compressed raw byte
lengths and SHA256 values; `.log.gz` files are lossless, deterministic gzip
archives retaining the original carriage returns and trailing whitespace.
