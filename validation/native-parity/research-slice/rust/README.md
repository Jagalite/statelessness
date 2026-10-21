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
