# Independent pre-PR review and requalification

The user requested a second independent full review and proper testing before a
pull request on 2026-10-10. Four non-author reviewers checked the full relevant
diff from `f3e00e79471b928d67ddb530c113d6d51af6f277` through `461a369`, read the
current repository v1.1 acceptance criteria (including M3a/M3b), wrote independent
regressions, and reviewed their fixes. Prior passing results were treated as
historical evidence, not as proof that these defects could not exist.

## Review coverage and resolved findings

- [Core/replay/sessions/workbench](REVIEW-CORE.md): three findings fixed, including
  environmental-policy-preserving minimization, pre-execution command validation,
  and sequence-zero failure budgets. Eighteen new independent tests
- [Inspection/watches/probes/macros](REVIEW-INSPECTION.md): four defect classes
  fixed, including uncertainty preservation, source changes and intrinsically
  incomplete inspection nodes. Fifteen independent tests, with eight failures
  reproduced against the clean prior source
- [Live control/effects/metrics](REVIEW-LIVE-EFFECTS.md): nine owned findings plus
  the shared transactional-snapshot fix. Covers committed host results, pause
  attribution, admission/retry/lost-token accounting, health merging, clock
  monotonicity/races and foreign-observer isolation
- [Protocol/integration](REVIEW-PROTOCOL.md): rejected snapshots preserve both
  acknowledged handles and retained metric history; unknown startup options fail
  closed; first-failure identity survives truncated check lists. Real OS pipes
  exercise malformed/truncated frames, stale handles, authority and restart

The reports retain resolved findings, observed red assertions, exact test
commands and explicit limitations. No unresolved blocking finding remains in
these scoped reviews. This is not a proof of arbitrary host applications,
unbounded interleavings, hard memory limits, or callback preemption.

## Integration review findings

The integration owner reproduced and fixed four additional delivery/tooling
problems, with independent review by the protocol reviewer:

1. The delivered source ZIP had no `.git`, but qualification scripts required Git
   metadata. Optional exact-root provenance now reports unknown metadata instead
   of failing or borrowing an enclosing repository's identity. Content hashes
   still bind all checked sources. Missing Git and nested archives have tests
2. External Inspect consumer manifests interpolated unescaped Windows paths into
   TOML. JSON-quoted basic strings now round-trip Windows drive/UNC paths, spaces,
   POSIX quote characters, and astral Unicode through a TOML parser
3. Rust 1.90 Clippy rejected three safety comments placed after a previous closing
   brace. The comments now precede their unsafe blocks on separate lines, with no
   semantic change
4. Cargo’s explicit `scripts/**` package inclusion bypassed Git’s cache ignore
   and shipped generated Python bytecode, breaking the subsequent clean package
   check. Source-only script globs retain every tracked script and exclude caches;
   archive inspection and the full packaged external consumer guard this boundary

The qualification runner now includes Rustdoc, provenance/path regressions,
Windows executable suffixes, and all qualification scripts/workflow files in its
source binding. The existing manual, read-only CI matrix now invokes the same
integration runner; it was not remotely dispatched during local review.

## Requalification gate

The final integration gate runs the reviewed source on Linux/x86_64 with official
Rust 1.95 and the declared Rust 1.90 minimum, repeats installed Windows GNU/macOS/
Wasm compile checks, and exercises a fresh exported source archive without Git
metadata. Cross-target checks are compilation only, never native execution.
Final source identity and outcomes are recorded in [ACCEPTANCE.md](ACCEPTANCE.md)
and the source-bound JSON evidence. The historical first-run qualification must
not be substituted for those post-review results.
