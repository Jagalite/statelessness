# N06 harness review, round 2

Source-only review, 2026-10-22. **Four P2 findings remain: H6–H9 below.**
H1 and the named compiler/dependency portion of H2 are corrected in source.
H4 is corrected for Python, TypeScript, Go and Swift; Rust retains H9.
H5 is corrected within the reachable public API domains described below.
H3/Q5's causal transport qualification remains a separate open gate.
The new N06 guardian corrects Q2's ordinary/nonzero leader-exit cleanup bypass,
subject to its explicit ownership boundary and H7's finalization problem.
None of these source dispositions establishes a runtime pass.

Only this review document was written. No repository code, build, test,
qualification, Kyoto command, staging, commit, policy change or job operation
was performed by this review or its two read-only source cross-checks.
Source inspection used reads, Git metadata and stdlib-only filesystem/hash
inspection. The Kyoto skill was read; executable qualification remains the
coordinator's work through shareddefault and
`/Volumes/seed1/Projects/kyoto/kyoto.py`.

## Source identity and concurrent drift

The authoritative readiness manifest is
`validation/native-parity/n06/qualification/source-readiness-r2.manifest.json`,
SHA-256 `09f65e46bdd5393e7847004910dfa90cf17749879de9cbbd4c658482d238fee5`.
It and `source-readiness-r2.md` were read fully. The readiness Markdown hash is
`c612000f5a2c071bde99b90acd1c4099dd201678ed85879a84c322303b5d8108`.

The requested checkout and branch remained
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`,
`t3/native-campaign-runtime`. Supplied/opening HEAD was
`188a1e4a69938cb4c433a7df8d53f16ab6044b50`; concurrent work advanced HEAD.
At the closing identity observation, **2026-10-22 16:47:31 UTC**, HEAD was
`eab04e523c5870a7949345318874f670745a6d22`.
All **63 selected entries, including 53 files, and all seven dependency pins**
matched the readiness manifest at the opening and closing inspections:
membership, type, mode, file size and SHA-256. There were no extra or missing
selected entries. These are sequential observations, not an atomic freeze.
The review applies to those content identities, not every file at later HEAD.

Concurrent drift outside the readiness selection was observed and is
**unreviewed at the later identities**:

| Path | Initial SHA-256 | Closing SHA-256 |
| --- | --- | --- |
| `docs/native-parity/N06-INTEGRATION.md` | `efd5b50e9f9f67fba0554272cf23655ffd7383a0b9323d5c31b3cdce32465a9e` | `67a4d3363275759bafa71f7b7d1e889a5470a1c80417de385e38d944093dd61b` |
| `python/tests/qualify_campaign_install.py` | `90704f3156b8f343cb147094c969bd6040639e1b4298064bcc536404a127b974` | `f72e2ff676254172e56d6abd99a9c5be152eb6965a82ba002700c282e52633ac` |
| `validation/native-parity/n06/typescript/qualify.py` | `d9f39e09e22be7486fece006a3d4a87f49a55801d4d09ba76b04b2c221a5b6ed` | `70c05fcafac2a2c37d52bcb0d7cfd838af27f1fdbbdcbb19890cfeacabd93882` |

Other concurrent language examples, qualification drivers, documentation and
receipt trees outside the stated read scope receive no approval here. In
particular, aggregate-recorder findings do not close separately owned standalone
watchdog findings. N05's helper remains unchanged at
`e5d934535179ea9262ce164cf865b1ff4b6214bd1f66938d088c41a358e3eea0`
(`scripts/native-guided-support/evidence.py`).

## Open findings

### H6 — P2: focused execution does not bind its actual Python drivers or control file

**Source:** `scripts/native-campaign-support/focused_run.py:25`, `:35–43`,
`:51–56`; `recorder.py:244`; related aggregate paths
`qualification.py:240–241`, `:339–343`, `installed.py:67–73`,
`toolchains.py:424–450`. Exact file hashes are listed below.

`capture()` manifests the installation declared by
`identity["python_installation"]`, while the version and suite commands use the
independent string `identity["python"]`. Neither is checked against the
interpreter actually running the focused runner; the guardian independently
uses `sys.executable`. The runner's placement inside the snapshot is checked,
but `focused.json` itself is read once outside that snapshot and is neither
retained as an immutable input nor reverified at the end.

**Scenario:** installation A remains byte-identical while interpreter B runs
the suites or guardian. The declared before/after manifests still match.
A wrong/no-op executable that returns zero for the version and suite invocations
can produce `status: passed` without running the 30 authored tests: neither
version output nor an executed-suite inventory is checked. This is a source
acceptance/provenance gap, not an observed false pass.

The aggregate has the corresponding driver gap: it records `sys.executable`
and `sys.version`, plans native/run jobs with that executable, but later discovers
and captures a potentially different PATH `python3` as the native builder.
Full capture of that chosen builder does not identify the already-running
orchestrator or its guardian. H2's compiler-installation changes therefore do
not close this broader driver requirement.

**Remedy:** freeze and retain the control-file bytes/digest with the invocation
and result, recheck its final identity, and require the actual runner, suite and
guardian interpreters, alias chains and referents to match a declared captured
installation. Bind the aggregate driver before it is used. Retain the executed
suite/test selection and add mismatched-driver/control and zero-work negative
controls; successful process exit alone must not establish test execution.

### H7 — P2: substituted receipt files can block finalization beyond the watchdog

**Source:** `scripts/native-campaign-support/recorder.py:44–50`, `:288–311`,
`:326–332`; SHA-256
`ddfad0b3fbf5efd268fce41e36d59fff09364cee132bda5aa5c4ddacaeccfb5f`.

Initial exclusive creation of regular receipt files at `:223–228` does not bind
their later path identities. After group cleanup and descriptor closure,
journal recovery calls `read_bytes()` on the current `guard-events.jsonl` path;
subsequent receipt hashing uses ordinary blocking `open("rb")`. There is no
current type/identity validation. Exception handling cannot bound a blocked
open/read.

**Scenario:** a target replaces `guard-events.jsonl`, or another receipt file,
with a FIFO before exiting. The guardian can finish writing through its already
open original descriptor, and owned-group cleanup can succeed, but the parent
then blocks opening the replacement FIFO. The command timeout no longer covers
this finalization path. This is an unexecuted adversarial scenario.

**Remedy:** retain owned descriptors or use nonblocking, no-follow opens with
descriptor type and expected identity validation before recovery/hashing.
Reject substitutions without losing the original target error or retained raw
streams, and record the evidence failure. Add a bounded adversarial fixture
that substitutes a receipt path and verifies terminal failure and bounded return.
This request is about this concrete FIFO path, not hard filesystem/preemption
guarantees.

### H8 — P2: failed inventories can suppress the terminal receipt and independent after captures

**Source:** `scripts/native-campaign-support/focused_run.py:35–39`, `:68–73`;
`qualification.py:727–757`, also `:465–494` and `:403–415`.

The focused runner inventories command and temporary-artifact trees without
guards before writing `result.json`. A failed suite can leave an unreadable or
unsupported artifact; `tree()` then raises from `finally`, so the accumulated
primary failures and terminal result are not written. Raw command logs may
survive. This produces incomplete evidence, **not false success**.

The aggregate's `partial_install_identity()` similarly uses one fail-fast
comprehension for all install roots. An unsupported entry in `packages` or
`python-build` prevents observations of later venv, consumer, Go and Swift
roots. The outer after-capture guards preserve the original command error, but
cannot recover the skipped root captures. A dangling root symlink is also
reported as absent by `exists()`. The focused before/after capture computes
Python identity before snapshot traversal but does not persist it independently
if that traversal fails; `Session.verify()` has the same compound-capture shape.

**Scenario:** a failing compiler or suite leaves a special file in an early
artifact root while later installed package roots have changed. The primary
failure remains known, but some required after identities are lost; in the
focused case even the terminal result can be lost.

**Remedy:** capture roots/stages independently, persist successful observations
and per-root capture errors, and retain special/dangling types explicitly.
Guard focused command/artifact inventories separately and always attempt the
failed terminal receipt while keeping primary failures separate from evidence
failures. Author controls with a damaged early root and a valid changed later
root, plus failed-suite artifact inventory failure. The current fixtures do
not cover this path.

### H9 — P2: Rust's installed seeded check omits requested membership and the report envelope

**Source:** `scripts/native-campaign-consumers/rust/src/host.rs:120–136`;
SHA-256 `fee67eadfd7de4770e5f65629c7fc31accb6b9354ace4e2a150d5d2edc695bb7`.
Reference: `tests/native_campaign_reference.rs:84–122`, SHA-256
`49b64ecae6c0b30cb089e687423c11f0f391eb719afa0fce575fb7f19cb5260c`.

The installed check requires seven reports with unique IDs, derives each fresh
reference from the **returned** ID, and compares the complete inner `FuzzReport`
and selected ordered callbacks. It never asserts the requested ID set or checks
`JobReport.seed`, effective `config`, or `metadata` against independently
derived expectations. The public source reference explicitly checks requested
membership at `:122`; the installed block omits that assertion.

**Scenario:** a multiworker path corrupts the report envelope while preserving
the completed fuzz report/callbacks, or consistently executes seven different
IDs. This block can accept it. Other exact corpus rows do not replace coverage
for this seeded multiworker path. This is an acceptance gap, not an observed
runtime defect.

**Remedy:** require exactly `first_job..first_job+jobs` in a separate membership
index; compare the envelope with the independently derived wrapping seed,
effective configuration and fresh model metadata alongside the existing full
fuzz/callback comparison. Keep original receipt order unchanged. H4 remains
partially open until this Rust check is strengthened and qualified.

### Exact source pins for the findings

All hashes below matched the readiness selection at closing:

```text
94cae2c0fe602c0d970a2533a82bca2e95149ffba1ad5c9f7b7d0040f24fc439  scripts/native-campaign-support/focused_run.py
ddfad0b3fbf5efd268fce41e36d59fff09364cee132bda5aa5c4ddacaeccfb5f  scripts/native-campaign-support/recorder.py
e911423f48ce81ca8ab252ffac6ff64122f0bcf947ff924cdbad69dee45719f4  scripts/native-campaign-support/recorder_guard.py
9756001b2353c92dd36e8d533d117224a64b42b2509e32fd07c56b6ae50621e8  scripts/native-campaign-support/qualification.py
eea1891f3fa4497dc3222d7bced2be68f3ed2e84e5510750260ed6807adcc28d  scripts/native-campaign-support/installed.py
701b65c688764082e41e77b47840e1b6a4ed5489e6ee9bc1939a701d6be74d4d  scripts/native-campaign-support/toolchains.py
f79d75039db86179710f0c2fa07997f0e928c620a8b348d3cbcc771ebc3db230  scripts/native-campaign-support/test_harness.py
490463cb6aa6069175da2f03a41462eb27ed3a53b9cf6824109f7ccaf6fdd087  scripts/native-campaign-support/test_recorder.py
fee67eadfd7de4770e5f65629c7fc31accb6b9354ace4e2a150d5d2edc695bb7  scripts/native-campaign-consumers/rust/src/host.rs
```

## Prior finding dispositions and fixture assessment

**H1 — corrected in source, execution pending.** `environment.py:5–10` disables
persisted Go configuration (`GOENV=off`), empties GOFLAGS, selects the local
toolchain and disables workspace discovery. `toolchains.py:315–332` validates
effective GOENV, GOFLAGS, GOTOOLCHAIN, GOWORK, CGO_ENABLED, GOROOT and GOTOOLDIR;
`:415–422` captures the declared GOROOT before use. `installed.py:191–203`
records effective configuration for each external consumer/baseline module.
`test_harness.py:302–339` authors a persisted overlay control using actual
`go env -json`. It does not claim an overlay-substituted application was built.
The N05 helper was not edited to obtain this result.

**H2 — named compiler/SDK/dependency omissions corrected in source; H6/H8 remain.**
`toolchains.py:27–197` binds modes, file stability, aliases/referents and full
declared installations, rejecting unsupported/unbounded layouts.
`Toolchains.capture/install/verify` at `:225–298` retain incremental before and
partial-after observations. Discovery at `:355–464` binds Apple selectors,
developer/compiler/SDK/support roots, Go, Python and Node/npm before their
respective uses. `installed.py:93–104` captures builder dependencies before
wheel construction; `:135–142` captures TypeScript dependencies/tsc before
version/build. Scope explicitly excludes the kernel, dynamic loader, dyld shared
cache and arbitrary transitive system libraries. This is not hermetic OS
identity, and the actual harness-driver and failed-capture gaps are H6/H8.

**H3/Q5 — improved installed observations; causal gate still open.** Python
`consumer.py:782–913` adds 20-job/two-worker held-sink cases for buffers 0/2,
release/rejection, real factory barriers, counts, drain and worker liveness.
Four corresponding labels are mandatory in `host-requirements.json:21–48`.
TypeScript `host-checks.ts:179–213` now labels its observation
`slow-sink-progress-and-transfer` and emits
`causal_transport_bound_proven: false`. Encoding occurs before send in
`typescript/src/node_campaign_worker.ts:40–45`; the narrowed label is correct.
Rust, Go and Swift also document finite observation limits.

An encoding/teardown observation or quiet interval cannot distinguish an
actually blocked transport send from a sender descheduled before admission.
`transport.py:17–23` records source inputs and status
`required-separate-source-bound-execution`; it does **not** ingest/validate
execution receipts, installed package identities, driver identities, executed
test selections or fault-control results. `execution.py:39–40` rechecks that
requirement object. `qualification.py:839–840` carries it into the narrowly
scoped installed receipt. Thus an installed receipt is not causal transport
qualification. Even Rust's listed public bound test at `tests/campaign.rs:638–644`
uses factory progress followed by a 20 ms quiet interval. Root must reconcile
actual transport admission/wait and deliberate fault-control evidence with the
production/package bytes and exact commands/test/driver inputs. A source
manifest alone cannot close H3/Q5.

**H4 — four language fixes corrected in source; Rust retains H9.** Python
`consumer.py:916–966`, TypeScript `seeded-checks.ts:71–143` and
`seeded-fixture.ts`, Go `host_equivalence.go:170–321`, and Swift
`SeededHostChecks.swift:183–274` author ordinary/guided workers-1/3 checks with
seven jobs, three cases, four steps, RNG-dependent inputs and mutation 100%.
They compare fresh public bare-search reports and ordered callbacks, with
seed/configuration expectations and real worker barriers/ownership observations.
Rust `host.rs:105–147` adds the corresponding inner report/callback comparison,
with H9's remaining envelope/membership omissions. Worker-local models and
application report-transfer paths are used; these are not fabricated worker
receipts. Receipt order is retained. These same-engine orchestration checks
remain distinct from independently authored cross-language vectors.

**H5 — corrected within the stated public domains, execution pending.** Rust
`host.rs:148–173`, Go `host_validation.go:14–81`, Swift
`AdmissionHostChecks.swift:34–92` and TypeScript `precedence-checks.ts:14–63`
cover all four zero guidance limits combined with invalid campaign settings and
preset controls, checking exact first error and forbidden callbacks. Python
`consumer.py:969–1029` explicitly records 16 constructor-only guidance
rejections with `campaign_invoked: false`; four valid-guidance cases separately
exercise jobs-zero before controls. Its receipt states
`campaign_guidance_order_exercised: false`. That is honest coverage of a narrower
reachable domain, not proof of Rust's campaign-level guidance precedence for an
invalid Python object. No unchecked constructor bypass closes the N05 gap.

**Q2 — original N06 leader-exit bypass corrected in source, bounded by ownership.**
The aggregate imports the N06-owned Recorder. `_finish()` runs from `finally`
(`recorder.py:288–297`) after normal/nonzero exit, launch errors, timeout and
exceptions. A surviving descendant fails qualification even when the leader
returned zero or `check=False`. Primary target errors, raw streams, wrapper
events and cleanup failures are retained separately. The parent never signals
a saved numeric PGID. The living guardian validates current PID/PGID/SID and
makes one self-inclusive group SIGKILL attempt
(`recorder_guard.py:49–68`). Its exception/control-EOF path attempts cleanup
while ownership survives (`:140–149`).

If the guardian is externally terminated, ownership is lost and the parent
fails closed without a numeric retry; SIGTERM/SIGHUP have no guardian handlers,
so that case can leave targets alive. Remaining zombies are not relabeled
absent. Group observations are not atomic; `setsid`/`setpgid` escape is outside
containment. These are explicit limits, not a universal all-exit containment
claim. H7 separately prevents accepting the present finalization as bounded.

**Focused authored inventory:** exactly **16 harness tests + 14 recorder tests**
are present; both modules invoke `unittest.main()`. `focused_run.py:51–58`
launches both with `-I -S -B … -v` and a 180-second timeout per suite, attempting
the second after a first-suite failure. Preflight/version failure skips both.
The recorder fixtures cover normal/nonzero surviving descendants,
`check=False`, timeout, target/guardian launch failure, observation failure,
injected KeyboardInterrupt, unrelated groups, synthetic lost/reused ownership,
one-shot signaling and zombies. They do not exercise real guardian death or
control EOF end to end. Their sleeps/polls are bounded, but H7's substituted
receipt finalization is untested. Thirty authored methods and two launch paths
are source facts; no test has been executed by this review.

## Corpus, dependency and execution boundaries

`qualification.py:115–133` requires the exact ordered **49 rows: 15 seed,
15 validation and 19 campaign**. `execution.py:30–60` checks the ordered
engine/case inventory and exact expected/actual objects for all five engines
(245 comparisons), plus mandatory installed-host labels. Strict comparison
preserves types, keys, values and array order. `test_harness.py:225–267` authors
row-omission controls, including feedback-metadata panic and overflow rows;
`:341–347` covers type/field/array-order changes. There is no diagnostic alias,
row deletion or silent count-domain substitution in the inspected path.
Go `wire.go:153–164` and Swift `Projection.swift:84–96` preserve actual errors;
Python/TypeScript public diagnostics remain visible.

The shared safe-count domain is not full native-count parity. Full-width u64
seeds/job IDs remain intact, the shared count boundary remains 2^53−1, and
native wider-count coverage is separate. `N06-DEPENDENCY-GAPS.md` remains open:
Python constructor/validation precedence, Swift/TypeScript feedback-metadata
`ModelError` versus Rust `Panicked`, actual context/error-string differences,
wider native-count support and composed Go Auto-panic provenance are not
normalized away. Required N05 changes must be reported separately.

Earlier receipt fixes remain present in source: regular executable Rust binary
and depfile identity (`qualification.py:148–157`), damaged-success receipts
classified as failed rather than pending (`:429–462`, `:760–799`), and terminal
Cargo raw failure/after observations (`:465–494`, `:552–622`). The related
authored tests are substantive, but H8 limits the independence of nested
after inventories. A recorded command or queue admission is not execution.

Root still owns: resolving/reviewing H6–H9; preparing a new immutable focused
payload with the actual driver bound; executing both suites (currently 16+14
authored tests) through shareddefault; independent author/golden freeze; fresh installed builds and
public callback/worker checks; all 49 rows across five engines; baseline
regression; matching separate transport/self-model/fault receipts and final
source/package reconciliation. Existing r1 jobs/payloads stay immutable.
**No harness job was submitted here; runtime remains pending.**

The supplied N05 boundary is preserved without querying queue state:
**materialized78 / reviewed90 / author
`36c3552df326429fb9e76d872c80aafa` queued / FREEZE absent**.
This review neither restarts that job nor claims author90 passed. Existing N06
author job `1be30f3a8bea47758dd063d93a8970cb` was not polled. N10, generic Rust
CampaignReport merge, defaults/profiles/144-cell promotion, hard RSS/preemption,
durable delivery, queue resume and fatal abort/trap catchability remain outside
this review's approval.

## Read scope

Read fully: the N06 plan section, `GUIDED-CAMPAIGN-AUDIT.md`,
`GUIDED-CAMPAIGN-REVIEW.md`, readiness Markdown and manifest,
`N06-REVIEW-HARNESS-R1.md`, `N06-QUALIFICATION.md`, dependency gaps and review
checklist; Python/TypeScript R2 Q2/Q5; the entry point, N06 support modules and
both authored fixture modules. The initial integration document was read fully;
its later drift above was not rereviewed. All five installed consumer
implementations were covered through direct and independent delegated reads.
The two delegated source cross-checks wrote and executed nothing; their
findings were reconciled against the current cited source.

Runtime authority reads included full Rust `src/campaign.rs` and
`src/campaign/lifecycle.rs`, full `tests/native_campaign_reference.rs`, targeted
lifecycle codec/self-model/fault tests, and guided/explore/model/operations
regions relevant to validation, RNG, callbacks, error metadata and teardown.
Consumer cross-checks also inspected the relevant Python, TypeScript/Node,
Go and Swift runtime/public bare-search/worker/projection paths. This is not a
fresh complete runtime, baseline, standalone-driver or example qualification.

Principal authority pins outside the readiness selection:

```text
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005  src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a  src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d  src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a  src/campaign/lifecycle/tests.rs
49b64ecae6c0b30cb089e687423c11f0f391eb719afa0fce575fb7f19cb5260c  tests/native_campaign_reference.rs
4ef4844eb77de0f120acbeaefdaea372a8e08caf7fc3466d0ccee10452aca449  tests/campaign.rs
f4cd232dec531a9b013551dacb47b64636719ac9bc0ae3053a335683bdb664a6  docs/native-parity/N06-REVIEW-HARNESS-R1.md
e0a8c4088d8109ba635e62f21124c293cb415c0862afeb58a77109fbd8aad581  docs/native-parity/N06-REVIEW-PYTHON-TYPESCRIPT-R2.md
f8c744053eb7b8567cd313fca9865834fcda3c63ff608ccd96c370e60c4438fd  docs/native-parity/N06-DEPENDENCY-GAPS.md
```
