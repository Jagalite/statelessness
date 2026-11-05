# Lean 4 assurance proof of concept

This is a development-only verification layer for `src/assurance.rs`. It does not
change the Rust engine, its API, workspace, dependencies, or normal build. It is
not a claim that the Rust implementation, a user's application, or an exhaustive
search has been formally verified.

## Run

Install Rust/Cargo (CI uses 1.90.0), Python 3.10+, and Elan/Lake. The checked-in
`lean-toolchain` pins Lean **4.24.0** for reproducibility; it is not a claim to use
the latest release. Initial toolchain installation needs network access. There
are no Mathlib or other third-party Lean package dependencies.

From the repository root:

```sh
# Formal proofs, axiom audit, and differential conformance:
python3 formal/lean4/check.py

# Also compile four deliberately broken Rust variants and detect their behavior:
python3 formal/lean4/check.py --mutations

# Existing Rust regression suite, independently of Lean:
cargo test --offline --workspace
```

For just the theorem kernel:

```sh
cd formal/lean4
lake build Assurance
```

The dedicated GitHub Actions workflow runs the existing Rust tests and the full
POC. Successful runs write `results/receipt.json` and `results/axioms.txt`, which
are retained as workflow artifacts. Generated files are not source-controlled.
The mutation runner only edits disposable copies, never the working tree.
The first successful full run is recorded in [VALIDATION.md](VALIDATION.md).

## What is actually proved

`Assurance.lean` contains 16 kernel-checked theorems. Some are direct consequences
of the main strict-assessment characterization, not independent algorithms.
The principal results are:

- A property's status is `Passed` exactly when it has a positive pass count and
  zero failed, skipped, and missing counts. Failure/skip/missing precedence is
  modeled explicitly.
- Saturating addition at `u64::MAX` is zero exactly when both operands are zero.
  Merging clean counters is equivalent to both inputs being clean. The proof
  does not assume that counters cannot overflow.
- Once failed/skipped/missing evidence exists, **any finite sequence** of later
  counter merges preserves it; the accumulated property cannot become `Passed`.
- The Boolean strict assessment is equivalent to explicit obligations on every
  required property, all observed evidence (including undeclared checks), input
  families, goals, callback errors, classification, and search outcome.
- Empty required-property catalogs, no required input family, an invalid report,
  callback errors, unclassified transitions, and missing required checks cannot
  produce strict success.
- If a graph-exhaustion requirement passes, the supplied outcome must be
  `GraphExhausted`, not merely `CasesCompleted`.

The audit asks Lean for the transitive axiom dependencies of **every theorem in
this file** and permits only Lean's standard `propext`, `Classical.choice`, and
`Quot.sound`. Proof holes, custom assumptions, and native-evaluation proof
shortcuts are not accepted as substitutes for proofs. Warnings are errors.

## The bridge to actual Rust

`Main.lean` implements an executable fixture model using the proved definitions.
`rust/` is an isolated Cargo package depending on the actual library by local
path. It calls the real public `Observer`, `AssuranceReport::validate`,
`AssuranceReport::assess`, and `merge_reports` APIs. It does not copy the Rust
assessment algorithm into a test oracle or expose new library internals.

`check.py` sends identical deterministic inputs to both executables and compares
all returned fields, not only a final pass/fail bit:

| Corpus | Cases | Scope |
| --- | ---: | --- |
| Counter/status validation | 2,500 | All four candidate statuses for counts from `0, 1, 2, MAX-1, MAX` |
| Two-report merge | 6,561 | All eight counter fields over `0, 1, MAX`; saturated counts, status, assessment |
| Observer/strict assessment | 35,374 | Policy/completion/coverage matrix, batch sequences, tampering, seeded longer histories |
| **Total** | **44,435** | Finite conformance evidence, not a universal equivalence proof |

Eight additional malformed-protocol checks require nonzero exits. Within the
observer corpus, both phases deliberately use the **same property ID** to test
phase separation. Cases include omitted checks after earlier passes, duplicate
IDs, undeclared failures/skips, excluded families, unknown coverage IDs, errors
after partial coverage emission, transition/history goals, all eight search
outcomes, and twelve corruptions of an otherwise consistent report. Random
histories use fixed seed `20261021` and at most ten transitions.

For arbitrary counter fixtures, the Rust bridge tries all four candidate status
rows and requires exactly one to pass the actual report validator. This reaches
the private classification logic through public APIs without reimplementing it.
Synthetic reports are test inputs, not authenticated evidence.

### Mutation controls

With `--mutations`, each temporary mutant must **compile and run** before a
behavioral disagreement counts as detection. A failed build is a test error,
not a successful negative control. The four regressions are:

1. Erase missing-check evidence instead of recording it.
2. Ignore undeclared failed/skipped checks during strict assessment.
3. Accept sampled-case completion as graph exhaustion.
4. Wrap the missing counter instead of saturating it during report merges.

The receipt records mismatch counts and a concrete first witness. Mutation
anchors must match exactly once; source drift fails loudly rather than silently
skipping a control.

## Trust boundary and explicit exclusions

`Evidence.valid` is an **input assumption** standing for Rust report validation.
This POC does not prove the implementation of `AssuranceReport::validate` or the
adapter that constructs this input. Twelve invalid-report fixtures exercise that
boundary, but they do not close it mathematically. Coverage and classification
flags similarly summarize observations; the executable fixture adapter is not
itself covered by a refinement proof.

The observer tests supply completed check batches and a chosen search-outcome
tag. They do not execute BFS or establish that the chosen tag is truthful.
The arbitrary-history theorem concerns the Lean counter accumulator, not an
unbounded proof of Rust's observer implementation. The merge fixtures cover two
present jobs with matching identities, not the whole campaign scheduler or all
missing-job/accounting behavior.

The Lean kernel checks theorem proofs. The compiled Lean executable, compiler,
Rust implementation, protocol adapters, Python driver, toolchain installation,
and CI runner remain part of the testing/trust boundary. Agreement on this
finite corpus is not an Aeneas-style refinement proof. This POC proves neither
report authenticity nor callback honesty, model adequacy, actual input-domain
completeness, application correctness, liveness, real effect execution, FFI
safety, serialization safety, nor correctness of the production search engine.

The useful next step is to verify a small Rust-to-Lean refinement boundary or a
finite-graph traversal kernel, not to require Lean for library consumers.

## Fixture protocol (internal, version 1)

Each line contains a command and space-separated unsigned integers; one result
line is returned. Inputs exceeding `u64::MAX`, invalid arities, enum values, or
fixture combinations are rejected. This is a test protocol, not a public API.

- `C p f s m candidate`: status candidate `0=Passed, 1=Failed, 2=Skipped,
  3=Missing`; returns `valid adequate`.
- `M p f s m p2 f2 s2 m2`: merges two consistent report fixtures; returns
  `passed failed skipped missing status adequate`.
- `R mask family goal requirement outcome hits error tamper initial [state transition ...]`:
  bit 0/1 of `mask` requires state/transition property `p`; `family` declares the
  required input family; `goal` is `0=none, 1=transition, 2=history`;
  `requirement` is `0=graph, 1=completed`. Outcome codes follow the order in
  `SearchOutcome`. Hit bits select input family, excluded family, goal, unknown
  family, and unknown goal. The error flag makes the coverage callback fail
  **after** emitting hits. Tamper modes `1..12` require the documented all-good
  fixture. Results contain state counts, transition counts, validation and
  adequacy bits, callback-error presence, and unclassified-transition count.
  Counts/error/classification fields are sampled **before** deliberate report
  tampering; the validation and adequacy bits are computed afterward.

Batch codes: `0=omitted`, `1=pass`, `2=fail`, `3=skip`, `4=duplicate pass`,
`5=other pass`, `6=other fail`, `7=other skip`, `8=pass + other fail`, and
`9=pass + other skip`. The first batch is the initial state; subsequent batches
come in state/transition pairs. Supplying batches after a failing observation
intentionally tests sticky evidence; the production explorer's early-stop
behavior is outside this fixture.
