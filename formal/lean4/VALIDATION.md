# Initial Lean assurance POC validation

Validated on 2026-10-21 in GitHub Actions, Ubuntu x86_64.

- Implementation commit: `16a1a3069a5e760522b22aaabf2ef02fa07c11fc`.
- Base Rust commit: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
- [Completed workflow run](https://github.com/Jagalite/statelessness/actions/runs/39638999854).
- Job: `proofs-and-conformance` (`119357290107`), conclusion **success**.
- Lean pinned to `leanprover/lean4:v4.24.0`; Rust `1.90.0`.

## Observed results

| Check | Result |
| --- | --- |
| `cargo test --offline --workspace` | Passed, including core, macros, debugger and doctests |
| Lean theorem build and transitive axiom audit | 16 theorems passed |
| Rust/Lean differential conformance | 44,435 cases agreed |
| Malformed fixture protocol controls | 8 checks passed |
| Deliberately broken Rust variants | All 4 compiled, ran and were detected |

Mutation mismatch counts from the actual CI log:

| Mutation | Disagreeing corpus rows |
| --- | ---: |
| Erase missing-check observations | 6,719 |
| Hide undeclared bad checks | 57 |
| Treat sampled completion as graph exhaustion | 9 |
| Wrap the missing counter during merge | 2,187 |

These are intentionally introduced regressions, not defects found in the
unmodified Rust implementation. A compiled mutant must disagree with Lean;
a compilation failure does not qualify as detection.

The workflow retained three files in the `lean-assurance-receipts` artifact:
`receipt.json`, `axioms.txt`, and the generated `AxiomAudit.lean`. The JSON
includes mutation witnesses and toolchain versions. The initial artifact is
[attached to the run](https://github.com/Jagalite/statelessness/actions/runs/39638999854/artifacts/12365346200).
Artifacts are subject to the repository's retention policy; the workflow log
and this historical receipt identify the tested source commit independently.

This is **formal verification of the Lean evidence model plus finite
conformance evidence for Rust**, not a proof of Rust/Lean equivalence or of
search-engine completeness. See [README.md](README.md) for the complete scope
and trust boundary. Later documentation-only commits do not change the tested
proof or adapter code; future source changes require a fresh workflow result.
