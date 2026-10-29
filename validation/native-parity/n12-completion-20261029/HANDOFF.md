# M0/N12 partial implementation handoff — NOT complete

Parent baseline: `4e84cf1a8bf15b2e6b0698d6801a92dc94a45d00`.
Pinned Rust behavior: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
PR12 sources are preserved; Rust source was not edited. No push, PR or merge.

## Reviewed changes

- Correct Python read/candidate clone ownership, Swift candidate revalidation,
  TypeScript bounded candidate lookahead, Swift candidate wire keys and Go
  default metric labels. Focused regressions are authored in each native suite.
- Add explicit N12 sealed validation-corpus inputs and a mutation-of-corpus
  identity regression. Overall integration receipt-family guard is unchanged.
- Reconcile five stale debugger/derive feature groups as partial with current
  source paths/hashes. All 144 language qualification cells remain pending.
- Add requirements ledger/checker and negative admission tests. Four debugger
  type correspondences are source-only. All 5,864 behavioral equivalence reviews
  remain open; 5,860 records also lack an individual source correspondence.
- The gate lists 4,032 capability/language/platform/gate evidence obligations and
  explicit mandatory whole-plan blockers. It cannot promote source mappings to
  parity or reduce the required platform matrix using JSON edits.

Independent static review: [REVIEW.md](REVIEW.md). Parent retains acceptance.

## Exact validation state

No current-source native execution pass is available at handoff. Shared Kyoto
reports `OS memory pressure: warning`; new jobs are queued. Shared policy was not
changed. Do not duplicate these source-frozen jobs:

| Job | Scope | Last observed state |
| --- | --- | --- |
| `67fc2ea72c9041fc83228aa6b0f6f88c` | sealed corpus identity regression | queued |
| `0cee3de256f0474fb16995e18b6de877` | final completion-gate tests | queued |
| `6b80f419e40d4554bb0bf634dc18e1b3` | five fresh installed/copied hosts + four native suites | queued |

The commands/effective policy, timestamps and log identities are in `jobs.json`.
These are explicit Python runners with `policy_message: null`; native suite
execution is inside the preparation runner, not inferred from Cargo compilation.
Rust is built as a host; its execution requires the subsequent comparison.

The initial gate attempt `bb49163638154cdfabcc6067024ac3c4` executed seven tests
and FAILED (4 failures, 1 error): trusted-root traversal incorrectly rejected
macOS `/var` symlinks in temporary fixtures. The source was corrected and review
added further negative controls. This failure is retained verbatim and is not a
semantic mutant detection. Five queued superseded submissions were cancelled
before starting for review corrections; all are retained. The first cancellation
request lacked Kyoto owner identity and was rejected; retry supplied the exact
matching owner ID, without changing policy or cancelling another owner's job.

Preparation artifacts: `/private/tmp/n12-completion-20261029-artifacts-4`.
Raw receipts: `/private/tmp/n12-completion-20261029-receipts/prepare-4`.
`source-identity.json` binds the frozen source inventory and scoped edited files.
Large generated output remains outside tracked source. Receipt markdown/JSON/text
under this directory are excluded from the source inventory by the existing runner.

After preparation succeeds, submit (do not nest waiting queue jobs):

```
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env PYTHONDONTWRITEBYTECODE=1 python3 scripts/native-parity/n12-wire/compare.py --hosts /private/tmp/n12-completion-20261029-receipts/prepare-4/hosts.json --output /private/tmp/n12-completion-20261029-receipts/compare-4
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 /private/tmp/n12-read-ownership-negative.py /private/tmp/n12-completion-20261029-artifacts-4 /private/tmp/n12-completion-20261029-negative-4
```

The second runner is preserved in `negative-control-runner.txt`; it copies the
frozen Python package, requires the control test to pass, removes only the state
read ownership guard, and requires the specific `DebugError not raised` assertion.
It is authored but NOT RUN. Compile/setup failures cannot qualify as detection.
The direct wire comparison is also NOT RUN for this source.

## Required remaining work

Concrete missing optional integrations are listed in REVIEW.md: Go/TypeScript
candidate commands, Go/TypeScript/Swift exact-export command, four watch/probe
service bindings, and Go/Swift telemetry bindings. Those are this lane's remaining
implementation work, not external/platform blockers. Therefore this handoff does
not fulfill the original entire-lane request or authorize N12 closure.

External execution blocker is precisely Kyoto admission under OS memory pressure.
Other platforms/runtimes, browser/mobile claims, installed cross-engine interchange,
source-specific mutation coverage, resource budgets and final integrated suites
remain parent gates. M0 still needs individual reviewed semantic dispositions and
additional target/feature inventory evidence. Other implementation lanes remain
pending dependencies. Do not merge PR12 on this partial evidence.
