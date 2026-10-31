# N06 harness source readiness for independent R2

Status: **source stable, ready for root's fresh independent harness R2 review**.
This handoff is source inspection, not compilation, execution or qualification.
No ownership path is still being edited by a consumer/compiler/recorder child.

The selected-source manifest is
[`source-readiness-r2.manifest.json`](source-readiness-r2.manifest.json), SHA-256
`09f65e46bdd5393e7847004910dfa90cf17749879de9cbbd4c658482d238fee5`.
It records 53 owned files, 63 total file/directory entries and seven dependency
pins, with membership/type/mode/hash rescanned before and after capture. There
are no exclusions within its selected roots. The metadata handoff files
themselves are outside that manifest, avoiding a self-referential hash.

Observed HEAD was `188a1e4a69938cb4c433a7df8d53f16ab6044b50` on
`t3/native-campaign-runtime`. HEAD is supplemental: the manifest identifies the
uncommitted owned source. Root's later commits do not retroactively identify a
different payload. No staging or commits were performed by this owner.

## Review dispositions

| Finding | Source disposition | Execution/review boundary |
| --- | --- | --- |
| H1 | `environment.py` sets `GOENV=off`, empty `GOFLAGS`, local toolchain and disabled workspace. `toolchains.py` captures and validates effective owned Go configuration. The N06 test authors a persisted overlay control. | The negative control queries actual Go configuration; it does not claim an overlay-substituted application was compiled. Unexecuted. N05 helper unchanged. |
| H2 | `toolchains.py` and `installed.py` capture full declared compiler/dependency/SDK installations before first use, including modes, alias chains and referents. Incremental journals retain partial installs and success/failure after-identities. TypeScript dependencies are captured before compiler use. | Unsupported/unbounded layouts fail closed. Kernel, dynamic loader, dyld shared cache and arbitrary transitive system libraries are outside the declared installation scope. No hermetic OS claim. Unexecuted. |
| H3 / Q5 | Installed Python has 20-job/two-worker held-sink checks for buffers 0/2, release/rejection, with mandatory labels. TypeScript's label is `slow-sink-progress-and-transfer` and explicitly denies causal capacity proof. All hosts document finite observational limits. | `transport.py` makes separately source-bound actual transport probes/fault controls mandatory. Installed encode/teardown counters and quiet intervals cannot close that gate. Root owns matching execution receipts. |
| H4 | All installed hosts author ordinary/guided checks with workers 1/3, multiple cases, RNG-dependent inputs and mutation 100%, comparing full per-job reports and ordered callbacks to fresh public bare calls. Host inventories require the new labels. | Same-host references are distinct from the independent 49-row cross-language protocol. Actual delivery order is retained separately. Compilation/runtime pending. |
| H5 | All four hosts author all four invalid guidance limits combined with invalid campaign settings and preset controls, exact first-error/zero-callback assertions. | Python's public constructor rejects zero guidance first: 16 combinations report constructor-only scope; four reachable valid-guidance cases separately test campaign validation before controls. No unchecked configuration construction. |
| Q2 | New N06 `recorder.py` / `recorder_guard.py` use a live private session/group guardian. Only that living guardian signals its own group once; the parent never signals a saved numeric PGID. All target exit paths run bounded group checks and preserve target/wrapper logs, primary errors and cleanup errors. | Lost ownership fails closed. Remaining zombies are not absent; group observations are not atomic and cannot contain `setsid`/`setpgid` escapes. Fourteen adversarial tests are authored, unexecuted. |
| Earlier Rust artifact finding | Binary and depfile manifests bind regular file type, executable mode and bytes; alias substitution is rejected. | Adversarial tests authored, unexecuted. |
| Earlier failed-Cargo after-capture finding | Every terminal direct-Cargo readback retains original status/exit plus best-effort after-identities, even on failed build or provenance error. | Original failure and verification errors remain separate. Authored test, unexecuted. |
| Earlier invalid-receipt-as-pending finding | Missing/live evidence and invalid terminal evidence have distinct exception/status paths. Damaged logs/locks cannot become pending. | Authored tampering test, unexecuted. |

The exact paths and hashes for all implementations, consumers and documentation
are in the manifest. Entry point:
`scripts/check-native-campaign-consumers.py`; harness:
`scripts/native-campaign-support/`; public applications:
`scripts/native-campaign-consumers/{rust,python,typescript,go,swift}/`.
`docs/native-parity/N06-QUALIFICATION.md` describes invocation and acceptance.

## Scoped checks and pending execution

The owner inspected current imports, recorder/guardian ownership and evidence
paths, compiler capture/failure hooks, terminal receipt status handling, host
labels and consumer handoffs. Source metadata capture rechecked every selected
entry and confirmed the reused N05 `evidence.py` equals HEAD. No repository
module, test suite, compiler or product was executed for those checks.

`focused_run.py` now invokes both `test_harness.py` (16 authored tests) and
`test_recorder.py` (14 authored tests), continuing to the second suite if the
first fails. It requires an immutable snapshot, exclusive execution marker,
complete declared Python installation identity and retained success/failure
after-manifests. Harness and guardian invocations use `-I -S -B`; installed wheel
applications retain their fresh venv site directory.

**New focused job ID: none; requested/effective command: not yet submitted.**
Root requested source-stable readiness so independent R2 can start before a
fresh freeze. After that review, one immutable focused harness snapshot/job is
still required through shared-default Kyoto. No duplicate or replacement job,
watcher or aggregate was submitted during these fixes. Existing live r1 jobs
and their frozen payloads were not changed; even a later successful r1 result
cannot qualify this replacement recorder or these later source changes.

Aggregate preparation/execution remains blocked on the author artifacts,
independent review, root's final source seal and the standalone integration
marker in `docs/native-parity/N06-INTEGRATION.md`. The existing independent
author job `1be30f3a8bea47758dd063d93a8970cb` is retained; its reported queued
state is not execution and was not polled by this handoff.

The 49 exact rows for each of five engines remain mandatory. Source-derived
diagnostic differences, Python constructor ordering and Swift's N05
feedback-metadata `ModelError` / Some / incomplete versus Rust `Panicked`
remain visible. No normalization, expected rewrite or unsupported-domain
reclassification was added. Counts above the shared safe-integer domain stay
separate, with full-width seed/job-ID/transition values preserved. N05 final
qualification, actual transport/lifecycle/fault-control receipts and full N06
closure remain separate pending gates.
