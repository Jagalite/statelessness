# Isolated N13 + research integration candidate

Worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-ec9c0a88`.
Branch: `t3/integrate-native-parity`. No parent merge, push, PR or profile promotion.

## Source and independent review

The new worktree fast-forwarded to research proof parent
`ac52c17570522411642b575b243a9fd4636dc8eb`, then merged N13 source/proof
`9daee75e4dd93ce746956954120d7d3e2f95d1ec` with `--no-ff` at
`0fb93be273b2c257536c084d519d2882e0e288d9`. Six additive conflicts (four language
READMEs and Python/TypeScript exports) retained both branches' APIs and prose.
`merge-resolution.json` and `merge-source-reconciliation.json` retain identities.
Historical receipts, including failure logs and patch whitespace, are unchanged.

Independent review covered exports, preferred codec dispatch, declaration
directions, error ownership, limits, receiver preservation, phase-aware recording,
research and baseline behavior. Go/Swift review found no concrete defect.
Python/TypeScript review identified two P2 defects corrected in
`1e3d8417e7f471da49b6756938517c63e4416e1f`:

- Preserve the existing generic public Python `ModelCodec` protocol; expose the
  declaration configuration as `ModelCodecBinding`. Unit and installed-consumer
  assertions cover protocol identity and generic subscription.
- Resolve TypeScript metadata on every codec hook, retaining the original receiver,
  instead of calling a stale captured method while the facade exposes a replacement.
  New tests cover eight hostile-input gates, unchanged sink prefixes, no decode-limit
  or encoder calls on rejection, and record/Recorder/checked/causal capture rejection.

The exact correction received static clearance in `review-python-typescript-r2.json`.
Static clearance is not execution proof. The provenance runner's separate reviews
and corrections are retained as `review-runner-r*.json`.

## Qualification boundaries

At this handoff all four named jobs are **queued**, with no start time. Kyoto
reports `OS memory pressure: warning`, effective start limit zero, and an unpaused
scheduler. **No integrated build or test has executed.** The combined native
suite job is `5f4c49e2e3eb43e499bcff0c04abe9ed`. Keep its sealed source unchanged;
do not submit a duplicate. No runtime test count is claimed for this candidate.

The frozen executable-source commit is
`800a591aa69aa213a17632520a3d5fc864fa2fae`; the 4,706-entry source manifest is
`558c93519c417fe193fd55495ba36282c39ff12d36838b48e70fb8313d4df481`.
Later receipt-only commits are excluded from that source manifest.

No prior-source pass is counted as integrated runtime qualification.
`historical-evidence.json` links unchanged N13 failures and 644 actual workspace
passes, and parent research proof (five engines × 67 shared cases, 717 baseline
cases per engine, 550 fresh replays). That research proof covers source `3232590`.
The two old installed attempts reached zero qualifying producers/pairs.

The Rust audit found 146 actual root-workspace inputs unchanged, but could not
establish complete prior compiler/executable identities. A fresh direct Cargo
compile is therefore queued as `34122687ff5b49fa9c716e69b1c852d0`. Shared Kyoto
policy added `--no-run`; even success is compilation only. The broader 263-entry
pre-submit closure and 64 recursively consumed Rust source files are preserved.
Actual workspace runtime, including doctests, must follow successful compilation
in a separate Kyoto job. Do not resubmit this compile while live.

The combined native job and exact current admission state are recorded in
`current-state.json` and `jobs/`. The runner retains raw per-command logs, complete
source membership/type/mode/size/hash before and after, current tool/configuration
identities, TypeScript dependency identities, Go test and Rust doctest executable
identities, and shared content-addressed manifests. Rustdoc's stable
[run-tool options](https://doc.rust-lang.org/rustdoc/command-line-arguments.html#--test-runtool---test-runtool-arg-program-to-run-tests-with-args-to-pass-to-it)
provide the temporary doctest executable capture; no compiler checking is relaxed.
The five 90-row ledgers remain source accounting, not 450 runtime cases.

## Protected handles and remaining actions

The parent tree remains pinned for N05 job `4baa668b525940e080684b11abc6680f`.
The old N13 tree remains sealed for these exact handles:

- `b4822433e1d7413a83d77efab4ecd25c`: ten final driver methods.
- `7daea984bfc5457083db9f4451a6ca35`: five installed producers and 25 ordered pairs.

Read the existing jobs, never restart or duplicate them. Preserve terminal logs,
source comparisons, and driver/application results when they finish. If either
fails, archive it and diagnose/fix only in this candidate before requalification.
If both pass, archive their evidence and run the integrated installed model and
research consumers into fresh output directories, including the full existing
portable baseline. Old-tree success alone cannot qualify this candidate.

`qualification-plan.json` retains commands and scope. Root workspace tests do not
compile the separate `validation/corpus` research workspace: build its
`native-research` and `stateless-corpus` tools separately through Kyoto before
installed research execution. Require all five engines, 67 shared research cases,
717 baseline cases per engine and 550 fresh replays; model qualification separately
requires five producers/all 25 ordered pairs with independent bytes, configuration,
phase/provenance and exact callback counters.

Keep the sealed candidate's source, docs and scripts unchanged while jobs are live;
only this excluded `derive-model-v2` receipt family may receive updates. Preserve
failed attempts before corrections. Parent/old-tree guard manifests and final
comparisons are in this directory. Do not modify shared queue policy or other jobs.

Full N13 modeling/domain/inspection helpers, N05/N06, N12 direct wire, broad144,
platform/resource/performance qualification and profile promotion remain open.
This candidate does not silently redefine overall parity as codec integration.
