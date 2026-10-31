# Fresh integrated N07 qualification — blocked before execution

Source pin: `4eeac8ccd77a1efd4479af2e6cf0539c4c3bbe0a`.
Isolated checkout: `/Users/jagatranvo/.t3/worktrees/stateless/t3-353db699`.
Branch: `t3/n07-native-qualification`.
The inherited HEAD and parent branch matched the requested pin and the checkout
was initially clean. Only receipt files were added during preparation.

The existing parent regression job `43415fbf900d4d38bdbc01a97e8e3d56`
is the first prerequisite. Its inputs belong to the parent and were not changed.
It remains queued in the retained snapshots, with no start time or executed count.
Kyoto reports `OS memory pressure: warning` and `effective_max_running: 0`.
The parent job must reach terminal success before the following submissions.
No new qualification scope has been submitted. Do not duplicate the parent job.

Requested/effective parent command, cwd `/Volumes/seed2/Projects/stateless`:

```sh
python3 -I -B -m unittest discover -s scripts -p 'test_observation_*.py' -v
```

`policy_message` is null. An empty log is not a passing test result.
Root script hashes remain:

- `scripts/qualify-native-observation.py`: `70440d4de2beb6398259873121ae7336a228a5b944717da7e24e493f59d2b380`
- `scripts/test_observation_receipts.py`: `e4107808791ac0bb86bbe33249452114cdd7c8ff7b86f552f049de7403c1a9b3`

## Prepared sequence, not executed

From this checkout, inspect parent terminal metadata and full logs first. Retain
actual unittest counts. Use the following fresh output names only if absent.
Submit scopes one at a time and inspect each terminal receipt and raw logs.
Keep all nonreceipt sources frozen while any submitted job may consume them.

```sh
OBS_PIN=4eeac8ccd77a1efd4479af2e6cf0539c4c3bbe0a
OBS_BASE=validation/native-parity/observation-slice/integrated-4eeac8c-20261029
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope native \
  --expected-commit "$OBS_PIN" --output "$OBS_BASE/native-a1" --timeout 1800

# Only after inspecting native results; do not enqueue dependent work during pressure.
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope rust-compile \
  --expected-commit "$OBS_PIN" --output "$OBS_BASE/rust-compile-a1" --timeout 1800
```

Before runtime/installed submission, use production `load_compile` in a separate
Kyoto job: load `scripts/check-native-observation-consumers.py` directly from source
bytes with `-I -B`, construct a fresh `Attempt`, call `seal()`, then
`load_compile(attempt, compile_receipt)`, and finish the attempt retaining any error.
Do not weaken `observation_compile_proof.py`. Admission requires the original
source pin, exact source/fixture manifests, retained commands/logs, actual binaries
and matching rustup tooling. Compile success alone is not admission or execution.

After successful admission, obtain `rust_binaries["native-observation"]["path"]`
from the compile receipt for `OBS_RUST`, and submit sequentially:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope rust-runtime \
  --expected-commit "$OBS_PIN" --compile-receipt "$OBS_BASE/rust-compile-a1/receipt.json" \
  --output "$OBS_BASE/rust-runtime-a1" --timeout 1800

python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope installed \
  --expected-commit "$OBS_PIN" --compile-receipt "$OBS_BASE/rust-compile-a1/receipt.json" \
  --rust "$OBS_RUST" --output "$OBS_BASE/installed-a1" --timeout 1800

python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 -I -B scripts/qualify-native-observation.py --scope extended \
  --expected-commit "$OBS_PIN" --output "$OBS_BASE/extended-a1" --timeout 1800
```

TypeScript compiler provisioning is already supported: the runner executes
`npm ci --include=dev --ignore-scripts --no-audit --no-fund` in external staging
using the sealed lock (TypeScript 5.8.3). Do not mutate any parent node_modules.
Chrome and sibling rustup proxies were present at static inspection; their
existence is not browser/compiler execution or captured runtime identity proof.

## Open acceptance gates

All fresh execution is NOT RUN: mandatory validator regressions on sealed source;
full four-language native suites; identity-admitted Rust compilation; 57 selected
Rust runtime tests; 36 shared cases per engine; two positive checked-token
compilations and six exact expected diagnostic-type rejections; ten retained-suffix
producers; 50 binary and 50 JSON fresh-process replay pairs; actual Chromium;
five-engine equivalent-work measurements; current-source Go race, Rust doctests
and release-monitor execution. The static test-attribute counts are 13/10/11/3/14/6,
not executed results. See static-preflight.json for per-file hashes.

The frozen `t3-329ad6a6` checkout was read only. Its terminal jobs and old-source
extended results remain historical; no reinterpretation as current-source proof.
No implementation fix, scheduler/policy change, cancellation, parent edit, PR action,
push, publication or merge was performed. N07 and global parity remain unqualified.
Final combined-source acceptance is still required after other lanes integrate.

## Latest bounded wait snapshot

At 2026-10-29 14:03:28 UTC, the prerequisite was still queued, with null
start/finish/exit status, queue position 23, and no estimated start time.
The shared queue contained 41 jobs and admission capacity remained zero.
The parent regression log did not exist: `parent-regression-latest.log` is an
empty capture placeholder, explicitly identified by `log_existed: false` in
`queue-latest.json`; it is not an emitted job log or test result.
Three bounded 60-second reads of the existing Kyoto waiter returned no output.
Earlier source/preflight inspection also occurred while that waiter was active.
No parent job cancellation is performed; stopping the local read-only wait client
does not affect the scheduler job. Resume from its actual current state, not from
this historical queue snapshot. No scheduler setting was changed.
