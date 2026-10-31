# Installed Python guided-v2 consumer

`consumer.py` exercises the installed `statelessness` public API against the
independently authored [guided-v2 schema](../../../conformance/guided-v2/README.md).
The adapter follows specification revision 3 (boundary, disposition and
failing-replay extension), retaining the six top-level actual-result fields.
It implements the table model, feedback callbacks, collision keys and observer;
it does not implement guided search or derive expectations from candidate output.
Implementation alone is not qualification. No build or execution receipt is
included here; the parent qualification harness owns those runs.

Run from outside the source checkout with a fresh virtualenv, a noneditable wheel
installation and Python `-I`. The consumer checks import/distribution provenance
and rejects source-tree or editable imports. It imports only the public
`statelessness` package; it never changes `sys.path`, reads private implementation
modules, or imports the independent oracle.

## Freeze and command contract

`--corpus PATH` is required. `FREEZE.json` must exist beside the corpus (or at
`--freeze PATH`) **before candidate import or execution**. JSON must have no
duplicate keys or nonfinite values. The freeze must contain `source_commit`
equal to `a4cc11eea8db62115b452eee3c6610c04fa06b96` and a `files` map whose
`conformance/guided-v2/corpus.json` entry equals the SHA-256 of the exact corpus
bytes. The independent seal has no top-level `corpus_sha256` field. Unknown
freeze formats fail closed. Optional
`--corpus-sha256 HASH` and `--freeze-sha256 HASH` additionally bind the run to
externally recorded revisions. The parent harness also seals source, oracle,
contract, package and consumer identities; this local admission check does not
replace that provenance record. Never create or change a freeze to admit a
candidate mismatch. The superseded 78-case seal under
`conformance/guided-v2/receipts/superseded-78-cases/` is historical evidence;
preserve it and all prior receipts. Do not compare this revised consumer until
the independent author publishes the current `conformance/guided-v2/FREEZE.json`.

The default command produces
`{schema:"stateless-guided-v2-results/1",engine:"python",cases:[{id,actual}]}`
for the parent harness to compare. `--compare` compares **all six** required
fields (`report`, `error`, `trace`, `generator_rng`, `witness_replays`, `shrink`)
after production. It ignores only explanatory `rng_audit`; dictionary keys,
scalar types and array order must agree. Expected values never reach the model,
feedback, observer or result-generation functions. `shrink_config` selects a
shrink run; `expected.shrink` does not.

`--case ID` selects one case for diagnosis. `--output PATH` creates a new result
file and refuses to overwrite it; omit it for JSON on stdout. Comparison
diagnostics go to stderr. Exit status is 0 for production/a successful comparison,
1 for comparison differences, and 2 for admission/API/consumer errors. Keep every
failed result and the raw queue receipts. A producer exit of 0 without
`--compare` is not a conformance pass.

## Wheel build, install and execution outside the checkout

The shared harness in `scripts/native-guided-qualification.py` performs these
steps with its own immutable snapshot and receipts. Prefer that harness for the
five-engine qualification. The following standalone recipe is an alternative
for a parent-authorized Python-only run, not an additional job to submit while
the harness is active. Use a complete frozen source snapshot with independently
published `FREEZE.json`; do not execute against a changing shared checkout.

Prepare copies outside the checkout (substitute the frozen snapshot path):

```sh
source_root=/absolute/path/to/frozen-source
run_root="$(mktemp -d /tmp/stateless-guided-python.XXXXXX)"
mkdir "$run_root/consumer" "$run_root/wheels"
cp -R "$source_root/python" "$run_root/package-source"
cp "$source_root/scripts/native-guided-consumers/python/consumer.py" "$run_root/consumer/"
cp -R "$source_root/conformance/guided-v2" "$run_root/guided-v2"
cd "$run_root"
```

Create a workflow script there, then submit the **whole** workflow to the default
shared Kyoto queue. This installs build tools in a builder environment and only
the resulting wheel in a separate runtime environment:

```sh
cat > install-and-run.sh <<'SH'
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv builder
builder/bin/python -I -m pip install 'setuptools>=77' wheel
builder/bin/python -I -m pip freeze --all > builder-freeze.txt
builder/bin/python -I -m pip wheel --no-deps --no-build-isolation \
  --wheel-dir wheels ./package-source
wheels=(wheels/*.whl)
test "${#wheels[@]}" -eq 1
shasum -a 256 "${wheels[0]}" guided-v2/corpus.json guided-v2/FREEZE.json > input-sha256.txt
python3 -m venv runtime
runtime/bin/python -I -m pip install --no-index --no-deps "${wheels[0]}"
runtime/bin/python -I -m pip freeze --all > runtime-freeze.txt
runtime/bin/python -I -B consumer/consumer.py \
  --corpus guided-v2/corpus.json --compare --output python-results.json
SH
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- bash "$run_root/install-and-run.sh"
```

Record the returned job ID and retain the effective command, terminal status and
full stdout/stderr:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py wait JOB_ID
```

Do not claim success from submission alone. No positive-duration timing test or
independent watcher is needed for this corpus.

## Public API requirements and normalization

The installed wheel must export `FeedbackMetadata`, `Feedback`, `StateFeedback`,
`FeatureBuffer`, `GuidanceConfig`, `CorpusEntry`, `GuidanceReport`, `guided_fuzz`
and `exact_state`, plus the existing `FuzzConfig`, `fuzz`, `RunLimits`, `Check`,
`Disposition`, `Transition`, `Metadata`, `ModelError`, `EXHAUSTED`, `record`, `replay`,
`ShrinkConfig` and `shrink`. The consumer calls
`guided_fuzz(model, feedback, config, guidance, limits=..., observer=...)` and
`StateFeedback(FeedbackMetadata(...), projection)`.

Reports must expose every Rust snake_case field, including `FuzzReport.guidance`
(`None` for ordinary fuzz). Corpus entries and their input sequences must be
tuples. State is `(depth,total)`, so public `exact_state()` receives a hashable
full state. Custom/state keys are immutable value wrappers; constant hashing
forces equal-hash/unequal-value collision handling. A null initial hook really
omits `initial_features`; an explicit empty hook still runs and is traced.
`FeatureBuffer.push` must own a deep copy, count duplicates against its cap,
retain a swallowed overrun, and preserve the callback's own error as primary.
This consumer exercises shared schema cases; host-specific ownership/lifetime
tests remain the implementation owner's separate responsibility.

`model.disposition` is returned through public `Disposition` and `Transition`.
An absent disposition defaults to `{kind:"Accepted",reason:null}`. Rejected and
ignored reasons retain their exact strings, including empty strings, and leave
the fixture's state/output computation unchanged. Observer transitions and both
custom and helper feedback wrappers append the actual callback transition's
disposition to their trace entries.

All witnesses use fresh models with public `record` then `replay`. Their states,
outputs, inputs and per-turn dispositions come from the recorded public trace.
Per-phase checker batches come from callbacks invoked by `record` and are checked
against its retained initial/turn check batches. Empty batches are preserved.
Each witness includes the actual normalized replay result
`{outcome:"Exact",failure_reproduced:boolean,steps_verified:N}`. Replay must return
`exact`, verify every recorded input and match the build. Retained corpus entries
are emitted first in FIFO order, with `Completed` termination and no reproduced
failure. A non-null input `shrink_config` appends the original fuzz failure and
then the actual shrink report's minimized failure, even if their inputs are
identical. Both must record `PropertyFailed` termination and replay with a
reproduced failure; the verified count includes the failing transition.
No passing witness is passed to `shrink`; only the actual fuzz failure is used.
The codec is exactly the published little-endian u64/i64 fixture codec, with
strict fixed-width decoding. Recording/replay callback traces are kept separate
from fuzz callbacks, and all controls/counters reset.

Empty callback errors remain errors: only `None` denotes their absence, and
context normalization preserves trailing spaces. Terminal cancellation uses the
existing model/configuration and callback controls without consumer-side
termination overrides.

Normalization is limited to representation and context spelling:

- Public lowercase enum strings map explicitly to schema PascalCase values.
  Unknown values are errors. Check `details` becomes `message`; a passed check
  has a null message. An accepted disposition's native empty reason becomes null;
  rejected/ignored reasons retain their strings. u64 seeds/draws become decimal
  strings, tuples become JSON arrays, and absent optional values remain null.
- `initial check: ...`, `state check: ...`, `transition check: ...`, and
  `transition: ...` become `initial check_state: ...`, `check_state: ...`,
  `check_transition: ...`, and `step: ...`, respectively. Only the outer context
  prefix changes; callback text is preserved.
- The exact public integer-range `ValueError` for the four guidance limits maps
  to `guidance limits must be positive`; the exact mutation-percent range error
  maps to `mutation_percent must be in 0..=100`. Those constructors run in Rust
  admission order (guidance, then fuzz configuration), before callbacks.
- All other native error messages pass through unchanged. Unexpected exceptions,
  missing exports/report fields, bad recordings and replay mismatches terminate
  the consumer; they never become a fabricated passing report.

The source-level public API supports this revision-3 adapter. Qualification still
requires the current independent freeze and the parent's focused/combined Kyoto
runs, including actual installed-wheel execution and comparison of all six fields.
No build, test, execution or comparison was performed for this schema adaptation;
no private API fallback is provided.
