# N07 qualification driver final source review

Reviewed on 2026-10-22 in the shared isolated worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, at base/HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This review is bound to the
SHA-256 snapshot below, including the qualifier's final unconditional, watched
rustup resolution trio. It is not a source freeze or qualification receipt.

**Disposition: three findings accepted by the parent for correction before
qualification.** Separate implementation tasks and independent correction
reviews own those changes. This document preserves the original findings and
examined hashes; it does not review or approve later corrections. The separately
reviewed suffix/import/cache/installed-watch findings are not superseded here.

Only this new document was authored by this reviewer. No other worktree or
implementation was edited, and nothing was staged or committed. No syntax check,
build, test, implementation execution, fixture generation, Kyoto submission,
push, or PR occurred. No applicable ancestor/in-tree `AGENTS.md` was found.
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read; the user's
stricter instruction leaves every executable qualification to the parent after
the integrated source freeze.

## Accepted findings

### D1 — P1: macOS identity lookup rejects an already-exited, unreaped command

`scripts/observation_process.py:113` calls
`proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ...)`. The zero argument excludes zombie
lookup. If a normal foreground command exits before `_Owner` makes its initial
identity read, the child is still owned and unreaped, but the backend returns
`None` for `ESRCH`; `_Owner.__init__` rejects it at lines 143-145. Fast version or
resolution commands can therefore fail a correct qualification attempt merely
because the supervisor was scheduled later than its child.

Apple's [XNU implementation, proc_info.c lines 2046-2059](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c#L2046-L2059)
enables zombie lookup for this flavor only when `arg` is nonzero. This was checked
by reading the source, without invoking the backend. The existing mocked test
at `scripts/test_observation_process.py:53` explicitly requires the incorrect
zero argument, so its synthetic zombie result cannot detect the real API issue.

Request zombie-inclusive lookup, correct that mock, and add an already-exited,
unreaped-child regression. Preserve the start-time fingerprint and inherited
PGID checks; neither a missing identity nor a name/PGID match is a substitute.
The correction and its eventual macOS execution remain outside this review.

### D2 — P2: aggregate finalization hashes nested Go logs marked unsealed

`scripts/check-native-observation-consumers.py:569-574` checks
`raw_logs_stable` only on the outer attempt's commands, then recursively hashes
every nested `stdout.bin` and `stderr.bin`. The nested Go wrapper at
`scripts/qualify-native-observation.py:131-159` can retain its own command with
`raw_logs_stable=False` after an ownership/cleanup failure, while the outer Go
command subsequently terminates with its separately observed cohort confirmed.
The finalizer does not read that nested uncertainty before hashing its files.

This can publish an immutable aggregate identity for logs the inner owner
explicitly left unsealed, and can hash a file still growing through a writer
whose cleanup was unconfirmed. The aggregate attempt may correctly remain
failed; that does not make its immutable raw-log claim valid. The outer sampled
cohort does not automatically supersede missing inner ownership evidence.

Reconcile nested command journals and available wrapper receipts before hashing.
Preserve paths without immutable hashes for nested unconfirmed, incomplete, or
inconsistent evidence, and retain the failed attempt. Do not convert an absent
nested finish record into success. Add a finalizer regression combining stable
outer command metadata with explicitly unsealed nested logs; the current
`test_observation_process.py:420-455` covers only immediate command finalization.

### D3 — P2: the declared Go panic profile is neither enforced nor recorded

`scripts/check-native-observation-consumers.py:337-349` copies the host
environment without resetting `GODEBUG`, and the command environment inventory
at lines 484-489 omits it. `go/README.md:562-565` explicitly limits panic
containment to the module's default Go 1.23 behavior and excludes
`GODEBUG=panicnil=1`, under which an existing raw codec boundary can swallow a
nil panic before the observer sees it.

An inherited setting therefore changes the qualified behavior without appearing
in command identity. Even if a native regression rejects that configuration,
the driver has not selected the declared profile; an installed fixture subset
which does not exercise that branch cannot establish it.

Pin the narrow observation qualification profile to `GODEBUG=panicnil=0`, record
the effective value in command metadata, and preserve it through the Go wrapper.
Author a regression showing that an inherited `panicnil=1` cannot survive this
environment preparation. This recommendation is confined to the documented
panic behavior, not an additional general environment-hardening requirement.

## Earlier findings and evidence-chain inspection

- **Prior R1, compile admission:** no actionable defect found in the reviewed
  correction. `observation_compile_proof.py` checks the retained attempt,
  start/finish journals, recursive content-addressed manifests, exact raw logs,
  source/fixture identity, toolchain resolution, Cargo arguments, full artifact
  records, distinct executables and complete target inventory. Source tracing
  supports compatibility with the current producer's eight artifacts and
  `load_compile` namespace. This is static compatibility, not successful
  admission of an executed producer receipt.
- **Prior R2, package source staging:** the earlier unsealed-copy interval is
  addressed for registered sources by staged origin mappings, directory
  membership checks, before/after comparisons and explicit generated-input
  registration. Wheel, tarball and Python builder tooling watches precede their
  consumption. This disposition does not clear distinct installed-output or
  cache issues identified by the separate suffix reviewer.
- **Prior R3, receipt-only HEAD:** `Attempt.check_head` compares HEAD's committed
  nonreceipt tree with the original pin, separately from actual working inputs.
  Receipt-only advancement retains the original pin; a committed source change
  hidden behind restored working bytes fails. Authored tests cover both cases.
- **Prior R4, timeout descendants:** shared Kyoto PGID retention, exact observed
  fingerprints, bounded TERM/KILL phases, descendant drain and conditional log
  hashing replace leader-only cleanup. D1 and D2 remain concrete defects in the
  inspected integration; the earlier R4 must not be called fully qualified.
- **Prior R5, validator admission:** the native scope discovers every
  `test_observation_*.py` module and records test IDs, loader errors, executed
  count, skips and result categories. Empty execution, omitted modules, skips,
  expected failures and ordinary failures reject validator success. These
  validator counts remain separate from engine test and corpus counts.

All three script regression files were inspected, with disjoint read-only
compile-proof and process subreviews using the inherited
`codex/gpt-6-astra`, `reasoningEffort=ultra`, `serviceTier=default` selection.
The compile-proof tests author their temporary Cargo manifests and Rust source
stubs; their `ROOT` reads import staged scripts. They do not require the native
staging list to include the real root Cargo/source trees. Synthetic binary rows
and inert fixture executables are admission tests, not compilation evidence.

The installed driver invokes authority controls after package compilation,
registers their generated sources before consumption, and calls the suffix
helper before the final broad runtime-tree watch. Suffix inputs, generated
configuration, binaries, packages and exported traces have individual watches;
producer/replay invocations compare those watches around commands. These hook
order observations do not replace the independent suffix/authority review.

## Explicit limits and remaining execution gates

**SIGTERM publication remains open, never a pass.** Neither qualification entry
path installs a SIGTERM handler. Ordinary Kyoto cancellation sends SIGTERM and
escalates after three seconds (`/Volumes/seed1/Projects/kyoto/kyoto.py:377-399`).
The default signal action bypasses Python `BaseException` cleanup and final
receipt publication. Already-created raw files and start journals remain, but
their termination, final hashes and complete failed receipts cannot be claimed.
`OBSERVATION-FIXES-PROCESS.md` already discloses this limitation. Because this
path does not publish successful qualification and was explicitly scoped, it is
recorded here as an operational evidence limit, rather than an additional
successful-run blocker. Full cancellation receipt support would require guarded
handling in both `main` and `record_go_exec`, including repeated TERM delivery
and Kyoto's escalation deadline; KeyboardInterrupt/SystemExit tests do not prove it.

Process ownership remains sampled ancestry for ordinary foreground commands,
not containment against unseen daemonization, descriptor passing or PID-reuse
races. Identity/configuration watches are not a fully hermetic build proof.
Cargo metadata commands are optional in the current producer/admission contract;
external dependency watches are required when that metadata pair is supplied,
but this producer does not supply it. Do not claim independently sealed
transitive registry sources from its locked/offline compilation alone.

The pinned Rust observation/monitor sources and selected tests preserve the
existing distinction between diagnostics and checked authority. Actual native
checked-token APIs, periodic checks and bounded Recorders exist; the historical
`CHECKED-RESULTS-AUDIT.md` absence statements are not current API findings.
Logging cannot authorize continuity or add reducer/check/effect execution.

The parent must freeze corrected integrated sources, including review documents,
before shared Kyoto qualification. Preserve original failed/superseded attempts.
Record the Kyoto job and requested/effective command alongside driver receipts.
Rust compilation remains compilation only; the six selected suites total 57
source-audited tests, and neither their execution nor any public/installed
consumer execution occurred in this review. Validators, compiler authority
controls, native test skips, observation cases and suffix process pairs are
distinct units. N07 closure, full-workspace coverage and runtime success remain
unclaimed. Corrections require their own source identities and review; later
source changes cannot inherit a passing receipt from this snapshot.

## Examined identities

These are the original reviewed revisions, rehashed before handing findings to
the parent. Later corrections intentionally do not replace this record.

```text
80e86133843bb9ff34380fc25d60f993a3f6ed08d6cf56494e533f69b37e8fc4  scripts/qualify-native-observation.py
dfa1d2217c770750933dcee1c42c3ff8426e438b31fab3af81b0d87170d9b061  scripts/check-native-observation-consumers.py
3b78d0f6226f0dd54dfdb36713a3c78d7fc0ece17aa67b29f38897f2bf1579a9  scripts/observation_compile_proof.py
3f36fc5827ca3ef345979158c66184a17bbe107d6454b758c6508840a981ea40  scripts/observation_process.py
cc845357556fc7b04416523cb4adad047b7c30ba1a00098cee81dd6b44c30329  scripts/test_observation_compile_proof.py
18167a81347f71a0c0323d9618cf6a83eee9764d95138c4edbbee32f1e7d4ad2  scripts/test_observation_process.py
48a233dcde4b972dd0fa061caf5403e77305bfd62dd81e73d77b523092213cc3  scripts/test_observation_receipts.py
096ffeb57d8c6bb5cc71553e963d33bf33c6fc6d686b24bc93e8cd9a4d7ea8f8  scripts/observation_authority.py
d69007d2b44dbc1c5d283a8eb59dc2bd5c5c39f07fc87af779afe46cb8c1873d  scripts/observation_suffix.py
944b7a5e12e47be276bb3dc6f5010378537c40b8a109ef868144fbd350eee726  docs/native-parity/OBSERVATION-FIXES-PROVENANCE.md
3283af67de2f7d0dc22fa0e3eaabfff055248caf65d1daa2401e7b88f320c461  docs/native-parity/OBSERVATION-FIXES-PROCESS.md
16676c58c03d90bfc539f4b05b3291d5fb62b76a0c5debcbf4c3637b4eabdf94  docs/native-parity/OBSERVATION-REVIEW-PROOF.md
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
704e559b3d31c660464fb9412f69b0803f1fb41313343e21b9b88ec5adc3ea96  tests/checked_replay.rs
4ae56ac8563de4dd118638cff125e2a013534fb8baa33994bf7d7d970f148cce  tests/pr_review_core.rs
20d7002957eb05f1d22c25f4a0be613105323fabcd2a49382588e6bc9ae7e8a8  conformance/observation-v1.json
3c3bcd168a162754ddb1536551551716320ade95fec2d2cd188cc4783c9849c4  examples/native-observation/suffix-contract.json
10124e42e1e377a3b4e907e3800b14c6c4f711cf2595805c9a56d72978e9d865  examples/native-observation/suffix-failure-contract.json
```
