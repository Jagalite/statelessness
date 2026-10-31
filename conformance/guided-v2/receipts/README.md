# Independent authoring receipts

All commands ran from
`/Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7` using the default shared
Kyoto queue. No Rust/native candidate execution or candidate output was used.

## First unsealed authoring pass (superseded before comparison)

Exact submission:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 conformance/guided-v2/oracle.py --author
```

Job `8e79fc006ad44ec995e3cda909bb16f4` succeeded and wrote 77 cases, corpus SHA-256
`e7bbb29bb795d06399a9625e094103e851bdd6c7c78486e5df29b35625e784e3`.
Raw submission/show/log are `author-submit.json`, `author-show.json`, `author.log`.
This was never frozen or compared to candidates. The oracle source was still
being authored and was not independently sealed for this preliminary receipt.

Before freeze, source review identified an observability issue: public
StateFeedback/exact-state helper pushes are opaque, so an actual public adapter
can log its forwarding callbacks but cannot instrument those pushes. Push traces
were restricted to the custom feedback implementation. A forced index100
rejection case and hand-calculated assertions were added. No runtime-derived
expectation correction occurred.

## Revised authoring pass

Same exact submission command; job `8dab80e7ba8d43f69e6a5588bc5753f7`.
Raw submission/show/log are `author-v2-submit.json`, `author-v2-show.json`,
`author-v2.log`. The queued job was delayed by shared scheduler admission reason
`OS memory pressure: warning`. No queue policy/resource changes or cancellation
were made. Completion and final freeze hashes are recorded by FREEZE.json when
available.

While this job was still queued, the unsealed author script was finalized so
`--author` also checks hand-derived anchors, checks the written JSON round trip,
detects oracle source changes during execution, and atomically writes FREEZE.json
with the hashes of corpus/oracle/README/source-contract. This avoids a second
dependent queued command and never reads candidate outputs. The earlier v2
draft oracle hash was `50eef9dc19b4eef4388492ea8d0754176b9845d701d88ea37889e389a20edd76`;
that draft was not executed or frozen. Use the final seal's oracle hash for
source identity, and the actual job log for completion.

The final v2 authoring job succeeded with 78 cases and corpus SHA-256
`6b9b18bc5c2056e2587691457d43693822257ae05848f433be304fc8ca47955e`.
Root's subsequent source-only review found nullable-message truthiness mistakes
before candidate comparison: empty ModelError messages must still be errors,
and empty callback errors must outrank ignored sticky overrun. The complete
78-case corpus, oracle, README, source contract and seal were preserved under
`superseded-78-cases/`; the active FREEZE.json was moved there before any edit.

## Nullable-error correction (specification revision 2)

Changed nullable-error branches to explicit `is not None`/`is None` and selected
the callback message by presence. Added five independent cases: empty
InitialState/State/Transition checker errors and empty initial/transition feedback
errors after ignored overrun. Exact contextual errors preserve their trailing
space. Hand-derived assertions cover all five. An additional guard verifies that
the existing 78 independent cases are unchanged; it reads the archived authored
expectations only for comparison, never to generate an expected result.

The author/seal command remains:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 conformance/guided-v2/oracle.py --author
```

This intermediate job was `6f624c723cc7431cbaaa4f518ebaffdb`; raw evidence is
`author-v3-submit.json`, `author-v3-show.json` and `author-v3.log`. It was cancelled
before execution when root requested the further source-only review extensions
below. Owner-qualified cancellation used its recorded submit owner, without
force; the initial missing-owner denial and correction are retained in
`author-v3-cancel-denied.txt` and `author-v3-cancel.json`. No candidate output was
inspected for this correction, and no 83-case seal was published.

## Boundary, disposition and failing-witness extension (revision 3)

The next authoring pass began as an unsealed 89-case draft: the prior 78, five nullable-error
cases, final feedback cancellation, generation-None cancellation, zero-case
pre-cancellation and zero-duration, and explicit Rejected/Ignored dispositions.
Both relevant transition callback traces now append actual disposition. Every
witness includes per-turn dispositions and an Exact replay report with verified
step count and failure-reproduced flag. The two shrink-selected cases append
original and minimized PropertyFailed witnesses after retained passing entries.
All expected states/checks/outputs/replay counts were derived from pinned Rust
source; no reference/native candidate output was inspected.

The previous independent corpus is an immutable regression guard. For its 78
cases the guard removes only the documented new default disposition and replay
fields, the new disposition trace elements, and appended failing witnesses; it
then requires every prior field to remain identical. New expected values are
always computed independently before this comparison.

The command remains the default shared Kyoto `submit -- python3
conformance/guided-v2/oracle.py --author`; raw receipts use `author-v4-*` and
`author-v4.log`. After the additive review correction below, the resulting FREEZE
must have revision3 and 90 cases. Both
superseded author runs and any failures remain separately recorded.

The revision-3 author job is `36c3552df326429fb9e76d872c80aafa`, with recorded
submit owner `9ff8248a-50aa-4f74-bf97-804323253a00`. After capacity recovery this
same queued job was collected; no replacement or duplicate author job was
submitted. Its effective command is exactly
`["python3","conformance/guided-v2/oracle.py","--author"]` in the checkout above.
While it was still queued and before a live seal existed, the README gained the
explicit files-map seal schema for consumer alignment. No expected value was
changed for that documentation correction.

Root then relayed an independent source review coverage finding: the historical
`mutation-disabled-reuse` case has 20 enabled checks, all true. Before editing,
`show` confirmed this same author job remained queued with `started:null`, and
no live FREEZE existed; `author-v4-before-fallback-show.json` records that state.
The new `mutation-disabled-reuse-fallback` case keeps seed42 and the successor
model but limits the corpus to one entry. Source-derived case2 traces must reject
retained 4 and 8 at `[0,0]` and `[1,1]`, then generate/check/step 1 and 2.
Manual assertions cover both adjacent fallback sequences, case2 inputs, retained
prefix and eviction count. The old case and immutable 78-case archive are
unchanged. The oracle/README now specify 90 cases. The separate direct index0
coverage gate is documented rather than implemented by a fixture-only algorithm.
The relayed review described an earlier draft oracle, not a review pass of this
final source; its finding was applied before execution, independent of engines.
No new author job was submitted. An initial receipts-only insertion patch failed
to match context without changing files and was reapplied with current context.

The seal uses `schema`, `revision`, `source_commit`, `derivation`,
`candidate_output_used`, `cases`, and `files`. The corpus digest is
`files["conformance/guided-v2/corpus.json"]`; the four pinned files are corpus,
oracle, schema README, and source contract. There is no top-level `corpus_sha256`
or `case_count`. The stdout receipt separately uses `corpus_sha256` and
`freeze_sha256`. Consumers were told this distinction before comparison.

The archived 78-case files were re-read only to collect their byte hashes. Their
corpus digest remains
`6b9b18bc5c2056e2587691457d43693822257ae05848f433be304fc8ca47955e` and their seal
digest remains
`88d9d2922c96864c87c17710fb32f8494a2dc982bf33dc242e9002297b0a4d86`.

During collection, the default scheduler reported `OS memory pressure: warning`
with `effective_max_running:0`; its raw health response is retained in
`author-v4-queued-health.json`. The author job remained queued with no start or
execution output. No scheduler policy, admission settings, competing job or
candidate output was changed or inspected to bypass this gate. Use the final
`author-v4-show.json`, `author-v4.log` and live FREEZE for completion evidence;
the queued health snapshot alone is not a successful authoring receipt.

The exact evidence-collection commands for this job are:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show 36c3552df326429fb9e76d872c80aafa
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs 36c3552df326429fb9e76d872c80aafa
python3 /Volumes/seed1/Projects/kyoto/kyoto.py wait 36c3552df326429fb9e76d872c80aafa
```

## Final validator correction and pending handoff

Root's final independent corpus review reported all five prior semantic findings
resolved with no additional RNG/FIFO/control/shrink mismatch at oracle SHA-256
`5fac2454d8ffd0009de8f8ca70fcb355b3cf752a9f8d6210a6e31253231188f9`, README
`7e4b438403c909d65d79f2b2b08128bca36f841d7a63eb3b75c67af74ad4f501`, and source
contract `ced199e47efa371dae0b5ad954aa288f771ed3f2add58e7775f3295e48df0cb7`.
It found that `--check` could accept an empty files map and did not validate the
other seal identity/provenance fields. Before the narrow correction, the same
author job was again confirmed queued with no execution start and no live seal;
the raw snapshot is `author-v4-before-validator-show.json`.

The final checker requires the exact seal schema, integer revision3, integer
cases90 equal to the derived case count, pinned source commit, literal false
candidate-output provenance, nonblank derivation, and exactly the four required
paths, then checks each full-file digest. Author and checker share the same
required path list. This changes validation only, not expected values.

Final source SHA-256 values handed to root:

| File | SHA-256 |
| --- | --- |
| `conformance/guided-v2/oracle.py` | `25727bd3e8eb2b328ea85e2d36e94896c4e7d7b326d8e2359d5cac0560d0c537` |
| `conformance/guided-v2/README.md` | `7e4b438403c909d65d79f2b2b08128bca36f841d7a63eb3b75c67af74ad4f501` |
| `docs/native-parity/N05-SOURCE-CONTRACT.md` | `ced199e47efa371dae0b5ad954aa288f771ed3f2add58e7775f3295e48df0cb7` |

At root's explicit handoff request, author job
`36c3552df326429fb9e76d872c80aafa` remained queued and untouched. No duplicate,
replacement, retirement or cancellation was issued. Only this author's local
`kyoto.py wait` CLI process (PID97280, started October22 10:28:23 local) was
terminated with `kill -TERM 97280` to avoid idle watching; this is not a job or
worker cancellation. Root owns future job show/log collection, final source
inspection, optional `--check` submission after author success, and comparisons.

The final filesystem handoff still has the original 78-case active corpus hash
`6b9b18bc5c2056e2587691457d43693822257ae05848f433be304fc8ca47955e` and no active
FREEZE. The archived 78-case seal remains
`88d9d2922c96864c87c17710fb32f8494a2dc982bf33dc242e9002297b0a4d86`.
The authored 90-case source and stricter validator are not yet executed; no
revision-3 corpus/seal hashes or runtime qualification are claimed. All source
corrections preceded candidate-output inspection; this author inspected none.

To retrieve retained job evidence:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show 8dab80e7ba8d43f69e6a5588bc5753f7
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs 8dab80e7ba8d43f69e6a5588bc5753f7
```

An initial README insertion patch failed to match its context and made no file
change. It was reapplied against the inspected current paragraph. This was a
documentation-edit correction, not a behavioral job failure.

## Independent source review

Read-only delegated task
`node:delegated-task:command%3Amcp%3Aec618953-dbad-436b-8a48-bd3e46449d03%3Adelegate-task%3An05-source-order-audit-r1`
read pinned Rust objects and returned source findings on 2026-10-22. It performed
no edits/builds/tests/candidate inspection. Its callback/control/error order,
FIFO/recovery rules, rejection-index call counts and shrink validation accounting
agree with this contract. It emphasized that ordinary/guided zero mutation does
not imply equal RNG streams when their eligible corpus availability differs.
After capturing its final result, this author requested termination of that
owned child because no further audit work remained. The durable final findings
were retained; no other agent or Kyoto job was cancelled.

Additional shrink cases suggested by that review (changed target details,
target second within an edge, wrong-phase target, cyclic hints and empty-failure
control nuance) are N04 regression extensions, not falsely claimed coverage of
the two selected N05 property-failure fixtures.

A separate read-only review of the independent oracle was requested as
`node:delegated-task:command%3Amcp%3Aec618953-dbad-436b-8a48-bd3e46449d03%3Adelegate-task%3An05-oracle-source-review-r1`.
It remained unstarted in orchestration; this author requested cancellation before
it produced a review, to avoid leaving redundant child work pending. The tool
acknowledged `cancel_requested`; its final task state was `interrupted`. This is not a
review pass. The completed independent source audit above remains separate from
the author's own static oracle review and the oracle's manual assertions.
