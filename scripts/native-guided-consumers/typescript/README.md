# Installed TypeScript guided-v2 consumer

`run.mjs` implements the fixture callbacks and normalization from the independent
[guided-v2 schema](../../../conformance/guided-v2/README.md). It imports only the
installed public `@jagalite/statelessness` entry point. It does not implement
guided search, mutation, corpus admission, RNG, replay, or shrinking, and it
discards `expected` and `rng_audit` before constructing fixtures.

Status: authored and statically reviewed; no install, build, test, or comparison
was run by the consumer implementation agent. Parent-owned installed execution
and comparison remain required. A producer exit of zero is not a conformance
pass. This consumer makes no profile or release qualification claim.

## Invocation

The parent qualification harness in `scripts/native-guided-qualification.py`
copies this directory into its scratch consumer, installs the built tarball
there, and invokes `run.mjs`. Prefer that existing workflow; do not duplicate an
active package build or comparison job.

For an independently authorized standalone run, first have the parent stage
`package.json`, `run.mjs`, and `seal.mjs` together in an external scratch directory. Use the
already built package tarball and a frozen corpus snapshot. Substitute absolute
paths in these commands and run each executable operation through the default
shared Kyoto queue:

```sh
consumer_scratch=/absolute/path/to/typescript-consumer
package_tarball=/absolute/path/to/jagalite-statelessness-0.1.0.tgz
frozen_corpus=/absolute/path/to/frozen-source/conformance/guided-v2/corpus.json
cd "$consumer_scratch"
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  npm install --offline --ignore-scripts --no-audit --no-fund --omit=dev "$package_tarball"
```

Wait for that submitted job and inspect its effective command and result before
submitting execution. No TypeScript compiler is needed for this `.mjs` consumer;
the tarball must already contain the parent's built public package.

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show INSTALL_JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs INSTALL_JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py wait INSTALL_JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  node "$consumer_scratch/run.mjs" --corpus "$frozen_corpus" \
  --output "$consumer_scratch/typescript-results.json"
```

Keep raw submission, show, logs, terminal status, package identity and exact
command evidence in the parent run's receipts. No queue policies need changing.
Use `--case CASE_ID` for a selected case in a fresh process; the shared harness
already does this for each case. Omitting `--output` prints JSON on stdout.
`--output` creates a new file and refuses to overwrite an existing receipt.

```text
node run.mjs --corpus PATH [--case ID] [--output PATH] [--freeze PATH]
  [--corpus-sha256 HASH] [--freeze-sha256 HASH]
```

Node 20 or newer is required. The installed package must be under
`node_modules/@jagalite/statelessness` beside `run.mjs`; package symlinks and a
resolved entry point outside that installation are rejected. This guards local
resolution; tarball hashes, dependency-lock integrity, frozen package contents
and complete source identity remain the parent harness's responsibility.

## Freeze and result contract

Before importing or executing the candidate, the consumer requires `FREEZE.json`
beside the corpus, or at `--freeze PATH`. Its schema must be
`stateless-guided-v2-freeze/1`, its `source_commit` must equal
`a4cc11eea8db62115b452eee3c6610c04fa06b96`, and its
`files["conformance/guided-v2/corpus.json"]` must match the exact corpus bytes.
The seal must declare `candidate_output_used: false`, a positive integer revision,
and a positive case count matching the corpus. The published seal has no
top-level `corpus_sha256`; that field exists only in the author's command summary.
Optional digest flags also bind the corpus and freeze to
externally recorded identities. Do not manufacture or modify a freeze to admit
candidate output. The consumer does not read the independent oracle or compare
candidate output with expectations; the parent owns that comparison.
`seal.test.mjs` tests synthetic admission records without importing a candidate or
performing any corpus comparison.

Output is
`{schema:"stateless-guided-v2-results/1",engine:"typescript",cases:[{id,actual}]}`.
Each `actual` has exactly `report`, `error`, `trace`, `generator_rng`,
`witness_replays`, and `shrink`. Native `ModelError` and `ConfigError` from fuzz
become the case's error result. Unexpected exceptions, missing public APIs,
invalid recordings and replay mismatches terminate the process with exit 2.
Diagnostics go to stderr; successful production exits 0.

## API mapping and normalization

- Guided runs call `fuzzGuidedObserved(model, config, guidance, feedback,
  observer, limits)`; ordinary runs call `fuzzObserved(model, config, observer,
  limits)`. Limits use `cancellation` and an actual `maxDurationMs: 0` timeout.
- Config fields are translated to camelCase. `seed` and `maxTransitions` become
  bigint. Guidance reports translate their camelCase `config` back to snake_case.
  Ordinary reports without `guidance` emit explicit null.
- Lowercase enum values map explicitly to the schema's PascalCase values.
  Checks retain order; `details` becomes `message`, with null for Passed.
  Seeds and generator draws are decimal strings. Bigint report counters are
  serialized as exact JSON numeric tokens without converting through Number.
  Numeric fixture inputs must be safe integers; rounded input is rejected.
- Checker errors with the exact native outer prefix `state check: ` or
  `transition check: `, optionally repeated by the public append-check boundary,
  map to `initial check_state: `, `check_state: `, or `check_transition: ` using
  the callback phase that actually threw. The remaining text must match that
  callback's error exactly. Other errors are preserved unchanged. This mapping
  normalizes context spelling; it does not replace errors with corpus values.
- Custom feedback uses owned keys and exact scalar/array equality, with constant
  or value hashes. A null initial hook is truly absent. Each push is traced,
  including duplicates and exceptions. Swallowed push failures remain the
  public buffer's responsibility; callback errors are thrown after attempted
  pushes. Cancellation is set when the callback finishes, including error exits.
- State and exact feedback use public `StateFeedback` and `exactStateFeedback`
  behind transparent wrappers. Only callback entry is traced; internal helper
  pushes remain opaque. Full-history observers preserve partial error batches.
- `model.disposition` is returned through public `disposition`, with Accepted as
  the default. Rejected and Ignored retain their exact reason, including empty
  strings, and still execute the fixture's state/output calculation. Observer and
  feedback trace entries append the actual normalized transition disposition.

Every retained corpus entry is recorded with public `record` on a fresh model.
The little-endian fixture codec decodes recorded inputs, states and outputs.
Public `replay` and `replayWithObservations`, each with fresh counters and reset
controls, must return Exact, matching builds and verified step counts, without
reproducing failure. Replay observations provide the separate state/transition
check batches, including empty batches; their values are checked against the
recorded evidence. Every replayed transition's disposition is also checked
against the recording. Returned witnesses include those recorded `dispositions`
and the actual observed `replay` outcome, failure flag, and verified-step count.
These callbacks never enter the original fuzz trace.

Only a non-null input `shrink_config` triggers public `shrink`, using the actual
fuzz failure and a fresh model with no simplification hook. The full shrink
report, including ordered history and operations, is normalized. Original and
minimized failures additionally require fresh public recording/replay to
reproduce failure, including the failing transition in the verified-step count.
Their full witness records are appended after the retained passing witnesses,
original first and minimized second, even if the two input sequences are equal.
Passing corpus witnesses are never shrink targets. Only the input `shrink_config`
selects this work; expected corpus fields are never consulted.

No source-level API questions remain for the supplied signatures. The
independent freeze, installed package execution and parent comparison are still
unverified gates; any runtime mismatch must be investigated without changing
the independent expectations.
