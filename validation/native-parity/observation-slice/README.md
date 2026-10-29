# N07 observation qualification receipts

Source pin: **`21490bd5092a18b45416c0a42bed249af63a7d18`**.
Worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`.
Branch: `t3/n07-observation-parity`; base:
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.

The reviewed source is committed in two coherent changes:

- `c5ab108940254d60a840ac47616d7819c1a4af79`: four native observation APIs,
  bounded sinks, reviewed Swift budget corrections, fixtures, tests and consumers.
- `21490bd5092a18b45416c0a42bed249af63a7d18`: installed/suffix qualification,
  evidence controls, independent review reports and explicit parity dispositions.

Only this receipt subtree may advance while these jobs consume the pin. The
drivers accept later receipt-only HEADs only if all committed nonreceipt inputs
and the working sources still match the original pin. Keep this checkout frozen;
do not rewrite it for a retry while a queued or running job can consume it.

## Actual evidence and queued work

At the retained 2026-10-22 17:19:37 UTC snapshot, **both jobs are queued, neither
has started, and no changed-tree compilation, test or consumer execution has
occurred**. Kyoto's fresh health sample reports `OS memory pressure: warning` and
`effective_max_running: 0`. No queue setting was changed, no workload cancelled,
and no duplicate job submitted.

| Scope | Kyoto job | Retained state | Planned evidence |
| --- | --- | --- | --- |
| native | `b940924f138148d8b5b3c59a6d8da818` | queued; started/finished/exit_code null | Full four-language suites and all `test_observation_*.py` proof controls |
| rust-compile | `737a58a322c54e95b2f43809e88c3d96` | queued; started/finished/exit_code null | Six Rust test binaries and two public consumers; compilation only |
| rust-runtime | not submitted | depends on successful compile receipt | Actual execution of the six selected binaries (57 source-audited tests), then 36 Rust observation cases |
| installed | not submitted | depends on successful compile receipt | 36 observation cases per engine, authority controls and both suffix matrices |

Both actual submissions are opaque Python commands executed as requested;
`command` equals `requested_command`, with `policy_message: null`. The `native`
wrapper intentionally runs builds and tests. The `rust-compile` wrapper uses
explicit Cargo `--no-run` and does not execute its products. The separately
retained direct-Cargo policy preview shows that shared policy appends `--no-run`
to the requested selected test command. That preview did not enqueue a third job.
Opaque-command classification fields are not runtime evidence.

The installed suffix plan is **10 producers / 50 binary pairs / 50 JSON pairs**,
split equally across interrupted and failing cases. These are planned units;
executed producer/pair and language-case counts are currently zero. Validator
mutation counts and authority compilation controls remain separate bookkeeping.
There are no failed executable attempts to archive at this snapshot. Submission
and metadata reads succeeded; their raw stdout and stderr are retained below.

Source review and Git whitespace checks completed. Read the
[review resolution](../../../docs/native-parity/OBSERVATION-REVIEW-RESOLUTION.md),
[source audit](../../../docs/native-parity/OBSERVATION-SOURCE-AUDIT.md), and
[integration contract](../../../docs/native-parity/OBSERVATION-INTEGRATION.md).
Historical checked-slice, installed research and N13 results retain their original
source identities and are not passes for this candidate.

## Retained metadata identities

These are exact raw metadata bytes, not executable qualification receipts.
Each accompanying `.stderr` file is empty, with SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

| File | SHA-256 |
| --- | --- |
| [Cargo policy preview](kyoto-cargo-policy-20261022.json) | `9fedfd7f66d6fca848c42748d8ca025edbbb4fdf74189693312edfa8c5e4c88f` |
| [Native submit](kyoto-native-a1-submit.json) | `a33007a071f75b004cc39ae97ce13e81b6d3594819817e80bf49a8596ad87eb5` |
| [Native status](kyoto-native-a1-show-20261022.json) | `5fc8491cf9d239da732ff296c15342833ae605041895a0fd974b7b1b04317592` |
| [Rust compile submit](kyoto-rust-compile-a1-submit.json) | `d33457bd08b8ad45cee885e1f46268985855b4a91ce0a6f8c6c5d0dd60164d81` |
| [Rust compile status](kyoto-rust-compile-a1-show-20261022.json) | `2ea5d6ed8c6efb29ac3d2f3437add7e47df8d94b49c803429f97a3b3ae520c4b` |
| [Scheduler health](kyoto-health-20261022.json) | `5659698a744b7dc4bbb06e6ec65bad8c8f7c6ad1acb4cbc391ebe507464f304f` |

The independently authored observation fixture SHA-256 is
`20d7002957eb05f1d22c25f4a0be613105323fabcd2a49382588e6bc9ae7e8a8`.
Interrupted/failing suffix fixture hashes are respectively
`3c3bcd168a162754ddb1536551551716320ade95fec2d2cd188cc4783c9849c4`
and `10124e42e1e377a3b4e907e3800b14c6c4f711cf2595805c9a56d72978e9d865`.

## Exact continuation

Run from the frozen worktree. **Do not resubmit the two live jobs.** Their exact
original commands are preserved in the submit JSON above. Read their terminal
metadata and raw logs, using fresh filenames if retaining later snapshots:

```sh
cd /Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show b940924f138148d8b5b3c59a6d8da818
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs b940924f138148d8b5b3c59a6d8da818
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show 737a58a322c54e95b2f43809e88c3d96
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs 737a58a322c54e95b2f43809e88c3d96
```

Only after inspecting a successful source-bound
`rust-compile-a1/receipt.json`, submit each dependent stage once:

```sh
OBS_COMMIT=21490bd5092a18b45416c0a42bed249af63a7d18
OBS_RECEIPTS=/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca/validation/native-parity/observation-slice
OBS_TARGET=/tmp/stateless-observation-21490bd5092a18b45416c0a42bed249af63a7d18-a1

python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope rust-runtime \
  --expected-commit "$OBS_COMMIT" \
  --compile-receipt "$OBS_RECEIPTS/rust-compile-a1/receipt.json" \
  --output "$OBS_RECEIPTS/rust-runtime-a1"

python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/check-native-observation-consumers.py \
  --expected-commit "$OBS_COMMIT" \
  --compile-receipt "$OBS_RECEIPTS/rust-compile-a1/receipt.json" \
  --rust "$OBS_TARGET/debug/native-observation" \
  --output "$OBS_RECEIPTS/installed-a1"
```

Every failure requires retention of original raw stdout/stderr, journals and
receipt before a fresh attempt. Never overwrite evidence or treat queued or
compile-only work as runtime success. Uncertain writers leave unsealed raw paths;
the driver must not manufacture final hashes while bytes may still change.

## Remaining gates

All changed-tree builds and executable gates above remain open. Full N07 also
retains six explicitly unmapped dedicated `DebugObservation` sum/member entries;
diagnostic Event is not a substitute for that API. The finite shared text profile
does not establish universal Rust Debug/Unicode formatting. Browser-host runtime,
other native platforms, Go race, full Rust workspace/doctests and
`macro-qualification/monitor_release` execution are not covered by the selected
stages. Logical record/blob/retained-wire bounds do not establish allocator/RSS
bounds for legacy codecs, model cloning or arbitrary callbacks. Retained suffixes
do not certify evicted history. Sampled process ownership is not containment;
default-action SIGTERM/SIGKILL or supervisor death may leave incomplete failed
attempt evidence. No graceful cancellation publication claim is made.

No push, PR, release, profile promotion or full-parity completion occurred.
