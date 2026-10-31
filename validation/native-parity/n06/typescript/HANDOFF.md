# TypeScript N06 source-ready handoff

**Post-r1 addendum:** the mutable standalone qualifier has newer, unexecuted Q2
cleanup/receipt corrections and executing-driver identity guards. See
[POST-R1-Q2.md](POST-R1-Q2.md). The original frozen inputs,
manifest, submission and show receipts below are unchanged; their queued command
still executes the old driver. A future successful r1 receipt does not qualify the
new driver or close its cleanup/provenance execution gaps. No replacement job was submitted.

The last source review returned no actionable findings for qualifier SHA-256
`70c05fcafac2a2c37d52bcb0d7cfd838af27f1fdbbdcbb19890cfeacabd93882`, observed at
root HEAD `faa5baa6c9ddafbf0ff40bfd28f054709d65313e`. Current owned paths and hashes
are in [post-r1-source-identity.json](post-r1-source-identity.json). This is a source
handoff, not a new freeze or an execution receipt.

Original r1 candidate on `t3/native-campaign-runtime`, then base/HEAD `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. No staging, commits, N05 foundation edits, pushes or final aggregate submission by this implementation agent.

## Frozen identity and queued execution

- Freeze: `snapshots/focused-r1/source` (117 files; payload tree ignored by local `.gitignore`, preserved on disk).
- Manifest: [source-manifest.json](snapshots/focused-r1/source-manifest.json). SHA-256: `d5fa4f4fe098aaca7389518f542e859e578257777579ee088868255c8af21c41`.
- Live focused Kyoto job: `84048d5f31b4404ba8a6c07e1af98f6a`, queued, `started=null`, `exit_code=null` at the retained [show receipt](snapshots/focused-r1/show.json). Queue admission is not execution.
- [Submission receipt](snapshots/focused-r1/submission.json) retains identical requested/effective argv, cwd, resource path, no policy substitution and the Kyoto log path.
- [Exact commands](snapshots/focused-r1/commands.txt) includes preparation, submission and status commands. The command runs the frozen `qualify.py` with pinned source-manifest hash and output `runs/focused-r1`.
- [Owned file identities](snapshots/focused-r1/owned-files.json), [freeze receipt](snapshots/focused-r1/freeze-receipt.json), and [origin recheck](snapshots/focused-r1/origin-after-freeze.json) are compact review artifacts.

No compiler, test, package or example execution result exists yet. One focused job was submitted after an empty matching-live-job check; no retry, cancellation or watcher was started.

## Exact owned implementation paths

- `typescript/package.json`
- `typescript/src/index.ts`
- `typescript/src/campaign.ts`
- `typescript/src/campaign_lifecycle.ts`
- `typescript/src/campaign_wire.ts`
- `typescript/src/campaign_test_support.ts`
- `typescript/src/node_campaign.ts`
- `typescript/src/node_campaign_worker.ts`
- `typescript/test/campaign.test.mjs`
- `typescript/test/campaign-lifecycle.test.mjs`
- `typescript/test/fixtures/campaign-worker.mjs`
- `typescript/test/fixtures/campaign-lifecycle.mjs`
- `docs/native-parity/N06-TYPESCRIPT.md`
- `validation/native-parity/n06/typescript/qualify.py`
- `validation/native-parity/n06/typescript/freeze.py`
- `validation/native-parity/n06/typescript/.gitignore`
- `examples/native-campaign/typescript/README.md`
- `examples/native-campaign/typescript/browser.html`
- `examples/native-campaign/typescript/browser.mjs`
- `examples/native-campaign/typescript/common.mjs`
- `examples/native-campaign/typescript/node.mjs`
- `examples/native-campaign/typescript/package.json`
- `examples/native-campaign/typescript/queue-model.mjs`
- `examples/native-campaign/typescript/serve.mjs`
- `examples/native-campaign/typescript/worker-model.mjs`

The browser-safe root and Node subpath use the actual public N05 fuzz/guided APIs. The private lifecycle drives caller and dedicated-coordinator execution; real job workers use explicit application modules and input transfer, Complete before send, Receive before sink completion, bounded queue/rendezvous, drain/join and first-sink-error precedence. See [the API and examples contract](../../../../docs/native-parity/N06-TYPESCRIPT.md).

## Review dispositions

| Finding | Source disposition | Execution |
| --- | --- | --- |
| Mutable sink report acknowledgement | Capture original job ID before decode/sink; wrong ID and throwing getter return/throw cases | Pending |
| Cancellation receiver | Original limits receiver retained | Pending |
| R1 hostile sink diagnostics | Owned primitive diagnostic; guarded category inspection; getter, revoked proxy and prototype trap; synchronous throw and rejected Promise with drain/first-error tests | Pending |
| R3 Node caller token composition | Context token OR original cancellation control; original receiver and absolute deadline preserved; factory-token-cancel test with false function | Pending |
| T2 full-u64 test codec | Bigint wire DTO with all seven counters; valid 2^53/2^53+1/u64-max states; separate native representability error | Pending |
| T3 capacity evidence | Fixed private SAB telemetry records actual Complete/send/admission; full queue and all workers blocked with B+W+1; no quiet-interval proof | Pending |
| T4 private replay payloads | Every failure and per-corpus-entry input value compared for full/range/maxID/worker references | Pending |
| Fabricated worker loss / premature Finish | Existing ledger directly compares lost flags and independently derives Finish; new corruption witness after ModelError and pending sibling | Pending |
| Additional review coverage | Complete-before-encoding property stop barrier; genuine initial coordinator construction failure; inconsistent-summary BFS/fuzz alphabet | Pending |
| Qualification provenance | Full source/work/consumer membership/type/hash checks, installed after-manifests in failure finalizers, executable and compiler hashes, allowlisted env, owned process-group timeout cleanup | Pending |

Final delegated executor source review (round 3) returned no concrete defects or type risks. The full-u64 codec author reports an independent source review with no findings; parent additionally inspected the bigint mapping and ledger witness. All reviews are static. The parent added inconsistent-summary alphabet coverage after that codec review and read the final qualification runner. Root second-round review and independent seal remain separate.

Authored campaign suites: 61 runtime tests (including loop-generated cases) and 28 lifecycle/codec/self-model tests. Static whitespace checks passed. No runtime pass is inferred from authored tests or source review.

## Pending steps and limits

1. Let the existing focused job execute. Inspect `runs/focused-r1/receipt.json`, complete command logs, source/work/consumer/installed integrity checks and actual tool/package hashes, retaining failures. Do not submit this identity again while it is live.
2. The focused command builds, executes all baseline and campaign test files, packs the package, installs it freshly and executes ordinary/guided Node examples. The construction-failure witness covers initial coordinator startup, not forced partial job-worker startup failure.
3. Root completes actual public Rust golden and cross-language installed comparisons, reconciliation of any native-domain differences, second-round source review and independent seal before final integration/commit.
4. Public job/case/step counts remain safe-number bounded. Test-only full-u64 wire acceptance does not extend the runtime to full Rust usize. Browser caller source/example exists, but no browser execution or browser multiworker adapter is qualified.
5. Preserve N05 public callback normalization. N05 author90 qualification/FREEZE and the explicit Swift N05 feedback-metadata provenance gap remain pending root gates. This TypeScript job does not close them, N10 or 144-cell promotion.

No fatal trap/abort recovery, hard RSS cap, callback preemption, durable delivery, queue resume or generic CampaignReport merge is claimed.
