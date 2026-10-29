# N05 TypeScript local qualification receipts

Authority: `a4cc11eea8db62115b452eee3c6610c04fa06b96`. All commands used
the default shared Kyoto queue from this checkout's `typescript` directory.
These are local package checks, not installed cross-engine/profile qualification.

## Initial implementation

Job `73d5c99651344d699b1c6eb84a48c65c`:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  sh -c 'npm ci --ignore-scripts && npm test'
```

Executed successfully: package build and 949 tests, zero failures/skips.
`unit-submit.json` records the requested command. Terminal raw log and job receipt
are `unit-after-correction.log` and `unit-after-correction-show.json`: those names
describe when the receipts were retrieved, **not** which source was tested.
The job finished before the later ordinary-exception correction. Earlier
`unit-show.json`/`unit.log` refreshes encountered transient worker recovery; no
duplicate job or queue-policy change was made.

`source-before-unit.sha256` pins the initial implementation's reviewed source.
The code review subsequently identified that the extracted shared kernel had
changed ordinary fuzz's exception formatting for unprintable thrown values.
The shared wrapper was restored to the original expression, a regression now
checks secondary exception identity in initial/generate/enabled/step hooks, and
the docs restrict placeholder formatting to guided feedback/feature hooks.

## Ordinary exception correction

Job `e18721828a2e4212b4f21decfaf279ea`:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  sh -c 'npm test && node node_modules/typescript/bin/tsc --noEmit --strict --target ES2022 --module NodeNext examples/guided.ts && node --check ../scripts/native-guided-consumers/typescript/run.mjs'
```

`correction-submit.json` records the command; `correction-show.json` and
`correction.log` record its latest observed state/output. Do not infer completion
from submission. `source-after-correction.sha256` pins the corrected files and
the consumer as it existed before the independent specification requested
disposition and failing-witness extensions. The consumer syntax check is not an
installed-package or corpus comparison.

The root owns the final independent freeze and installed comparison. No
candidate-derived expected values, profile promotion, staging or commits are
part of these checks. Preserve prior logs when a later source revision requires
focused verification.

## Independent specification revision 3

After the root requested the source-derived disposition/failing-replay extension,
the consumer was aligned to the updated independent README. It now returns
actual dispositions through the public transition API, includes them in feedback
and observer traces and recorded/replayed witnesses, and appends original and
minimized failing witnesses after passing corpus entries. Every witness includes
the actual public replay outcome, failure-reproduced flag and verified-step count.
No expected data or freeze was rewritten. `disposition-consumer-source.sha256`
pins this consumer and the README used for alignment. The correction job above
was already queued before this extension; no additional broad test job was added.
Installed execution and comparison await the root's final frozen snapshot.

## Final guided formatting and seal admission correction

The final source review found that an Error's non-string `message` could throw
during interpolation after `thrownText` returned. Only the guided formatter now
uses `String(error instanceof Error ? error.message : error)` inside its protected
try. The ordinary wrapper is unchanged. A targeted regression checks hostile
message coercion, callback-error precedence over swallowed overrun, sink expiry,
ordinary exception identity, and printable non-string messages.

The consumer now admits the actual published independent seal schema and its
`files["conformance/guided-v2/corpus.json"]` hash, instead of the author's command
summary field `corpus_sha256`. The source-only oracle's writer was consulted and
the spec/harness owners notified. Synthetic seal-admission tests import no
candidate engine and perform no corpus comparison.

Scoped job `0f40d4b1a2e84dc598c409af74f657f7`:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- sh receipts/n05/final-focused.sh
```

The script verifies `final-focused-source.sha256` before and after execution,
compiles into isolated `dist/n05-final-focused/dist`, runs only the three
throwable/ordinary-error tests plus the two synthetic seal tests, then checks
consumer syntax. No duplicate broad suite was submitted. Raw submission and
latest job/log captures use `final-focused-*` names. Submission is not a pass.

The earlier correction job was still queued immediately before this last source
edit (`correction-before-final-show.json`); its live-checkout command is not a
frozen pre-final-source receipt. Preserve its own command, actual executed output
and times, and do not attribute an earlier source hash to a later execution.
