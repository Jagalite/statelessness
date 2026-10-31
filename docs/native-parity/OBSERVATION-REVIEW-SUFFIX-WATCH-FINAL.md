# Final suffix output-watch review

Source-only focused review, 2026-10-22, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, with HEAD verified as
`c5ab108940254d60a840ac47616d7819c1a4af79`. The helper and test are untracked
working-tree inputs; this HEAD alone does not identify their reviewed bytes.

**No actionable findings within the requested scope.** The three immediate
watches resolve the P1 source issue recorded in
`docs/native-parity/OBSERVATION-FIXES-DRIVER-FINAL.md:163`: an intervening
persistent change to a completed suffix output can no longer become its first
accepted inventory baseline. This is source-review closure, not executed
qualification or whole-native-parity completion.

## Exact correction and detection paths

- **Go:** `scripts/observation_suffix.py:177` watches `suffix-go-bin` as
  `suffix-runner:go` immediately after the build returns at line 176, before
  TypeScript staging/configuration or its build. The final executable inventory
  at line 214 reuses that label and path.
- **TypeScript:** `scripts/observation_suffix.py:188` watches the complete
  `suffix-build` tree as `suffix-application:typescript` immediately after the
  build at line 187, before Swift tool resolution, staging and build. The final
  application inventory at lines 216-220 reuses that label and directory.
- **Swift:** `scripts/observation_suffix.py:200` watches `CheckedConsumer` as
  `suffix-runner:swift` once the reported binary path is identified and checked
  to remain inside the staged application at lines 195-199. It precedes Rust
  artifact identity reads/copying and the final executable inventory at line 214,
  which reuses the same label and path.

`Attempt.watch` compares an existing label's path and current identity before
assigning it (`scripts/check-native-observation-consumers.py:414-420`), so a
repeat inventory cannot reset a changed baseline. `check_inputs` rejects changes
under the original labels at lines 407-412. Actual `Attempt.run` calls it before
launch and after command completion, including failure, at lines 484 and 540.
These additions retain that existing behavior and the final inventories.

As a read-only text/hash check, excluding exactly the three lines above from
the current helper produces SHA-256
`d69007d2b44dbc1c5d283a8eb59dc2bd5c5c39f07fc87af779afe46cb8c1873d`, matching
the previous helper recorded in
`docs/native-parity/OBSERVATION-REVIEW-SUFFIX-FINAL.md:304`. Thus this helper
delta consists of precisely those three watch calls. No candidate code was
evaluated to make this comparison.

## Fixture boundaries and sensitivity

`scripts/test_observation_suffix_inputs.py:17` contains one test method with
three targets, each tested unchanged and mutated (lines 20-29). It calls the
actual suffix helper with real staging, generated-input registration, watch,
identity and staged-snapshot functions. Tool discovery and command execution
are replaced by inert fixtures (lines 68-91). The following are static control
flow conclusions, not observed test results:

- **Go positive/mutation:** The stub produces the Go output at lines 80-81.
  The unchanged case reaches `suffix-typescript-build` and raises the intended
  `BoundaryReached` sentinel after both input checks. The mutation case changes
  that already-produced file before those checks (lines 58-61, 69-74), causing
  `input changed before/after command: suffix-runner:go`.
- **TypeScript positive/mutation:** The stub produces the TypeScript output at
  lines 82-83. The unchanged case reaches `suffix-swift-build`. Mutation before
  its input checks changes the watched tree and causes
  `input changed before/after command: suffix-application:typescript`.
- **Swift positive/mutation:** The stub produces the executable and returns its
  location at lines 84-87. The unchanged case passes the Rust identity/copy
  checks, all final inventories, and the first runtime-input check, reaching
  `suffix-interrupted-python-record`. Mutation is injected by the patched
  executable-identity function on the first Rust identity read, after the Swift
  watch and before inventory (lines 62-67; helper line 205). It persists through
  subsequent Rust identity reads; final executable inventory rejects it with
  `watched input changed before reuse: suffix-runner:swift`.

The positive paths have the required minimal contract fields, source files and
installed-package placeholders (`scripts/test_observation_suffix_inputs.py:30-56`).
Resolving the temporary root makes generated input paths compatible with the
nonsymlink-path check. Go and TypeScript generated configurations are registered
before consumption (helper lines 162 and 186); registered Go `go.mod` is included
in the staged baseline. Swift `.build` is an allowed staged output directory
(`scripts/check-native-observation-consumers.py:681-682`). Consequently these
source checks do not prematurely reject the unchanged controls. Swift's positive
sentinel occurs before receipt/session fields absent from the minimal contract
would be consumed (helper lines 253-258).

Removing each corresponding early watch would let its mutation reach
`BoundaryReached` rather than the required `ValueError` at test lines 92-97.
Go's output is outside its staged source directory, TypeScript's generated tree
is not registered as source by this fixture, and Swift `.build` is excluded from
staged-source membership. Those unrelated checks therefore cannot substitute
for the intended watches and falsely satisfy the mutation assertions. The
synthetic controls establish coverage of these three ordering defects only;
they do not execute a compiler, native consumer, or the real command runner.

An independent delegated source trace agreed with these six boundary outcomes
and reported no findings. Its returned selection preserved `codex`,
`gpt-6-astra`, `reasoningEffort=ultra`, `serviceTier=default`.

## Inspected source identities and qualification boundary

The following SHA-256 values were read and rechecked during this review; both
task-supplied hashes matched:

```text
1f2eea73dbca6dd0450dec66e83efd1ba8160180734680d17c2dc224ec9797b7  scripts/observation_suffix.py
54e25021329a6492d15c4b6346f660ee6884d48dcd2eb9a328b32bab85b6d8e9  scripts/test_observation_suffix_inputs.py
1b6f5db4ba98cc6e296a52a18535f61f784894e14e02ae1069e42ef16c877602  scripts/check-native-observation-consumers.py
```

The required `/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was
read. No candidate code, import, helper, syntax check, test, compiler or build
was executed; no jobs were submitted; nothing was staged or committed. Only
this new report was written. Prior F1-F3/nested/GODEBUG conclusions and full
suffix literals/native consumers were not reopened as a new audit.

The parent retains the integrated source freeze and all subsequent Kyoto
build/test/compilation/executable qualification. The one method/six subcases
remain unexecuted here; a successful compilation-only job would not establish
their runtime outcomes or any native parity result.
