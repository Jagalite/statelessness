# N05 Go implementation checks

Base source: `a4cc11eea8db62115b452eee3c6610c04fa06b96` in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7`.
No parent checkout edits, staging, commits, profile promotion, or policy changes.

Initial full native package test submission:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit \
  --cwd /Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7/go \
  -- go test -count=1 ./...
```

Job: `29ebddcc9c834023b6b0fc4c57d4881c`. `unit-submit.json` preserves admission
and effective command; `unit-show.json`, `unit-wait.json`, and `unit.log` hold
the observed status and raw log. `unit-source-sha256.txt` pins the package sources
sealed before execution. The job succeeded with exit code 0: the package and
portable JSON tests executed, and the commands/examples compiled. Final source
hash verification passed. No compilation-only result is treated as executed evidence.

While that job was still queued (no start time), source review found that error
or recovered-panic formatting could run an application method before the feature
lease was revoked. Revocation now happens in a defer inside the callback boundary,
before contextual error formatting. Added adversarial returned-error/panic
formatting tests plus observer-to-feedback mutation isolation coverage.
`unit-source-sha256-before-lifetime-fix.txt` preserves the original source identity.
The original pre-submission source seal is superseded. After correction and
resealing, `unit-prestart-after-lifetime-fix-show.json` confirms this same job was
still queued, with no start time. Thus no execution used the earlier source;
the job may qualify the corrected source if its final hashes match this seal.
No duplicate live job or replacement was submitted. This was a source-review
correction, not a runtime failure result.

Root's later source review identified a Rust guided poll after the
short-circuited reused-input enabled result. Added that poll only for guided
search, plus a private deterministic control probe proving cancellation precedes
the reused reducer. A companion probe preserves ordinary Go's original reducer/
check boundary. Removed only guided search's unused case-end previous-sequence
clone; a public regression keeps ordinary's corresponding snapshot error.
`unit-source-sha256-before-kernel-review.txt` preserves the prior seal, and
`unit-prestart-after-kernel-review-show.json` confirms the same Kyoto job was
still unstarted after the new source seal. These are source-review corrections,
with no runtime pass or failure claimed before job execution.

The public consumer is a separate module under
`scripts/native-guided-consumers/go`. Independent `guided-v2` expectations must
be frozen before comparing its observations. The package tests do not write
or generate that corpus. Installed cross-engine qualification remains separate
from the native package test job.

Separate public consumer compilation job: `145cd1af6dd945c28c83fdfbcb294509`.
`consumer-build-submit.json` retains the exact Python staging/build command and
Kyoto's effective command. It copies the package and consumer into a fresh
temporary external module layout, prints every source SHA-256, then runs
`go version`, `go list -m -json all`, `go build -trimpath`, and `go version -m`.
Its raw log and status are `consumer-build.log` and `consumer-build-show.json`.
This job is compilation only, never a golden comparison or executed-consumer
claim. The copied source manifest and binary remain in the printed temporary
stage for inspection. No fixture is executed before the independent freeze.

Both jobs initially queued under shared OS memory-pressure admission, then
succeeded with exit code 0. Final show/log receipts are retained above. The
external consumer compiled using Go 1.24.2; its binary SHA-256 was
`85658d0aa907fbec3765ff7ec249f05913871158a237ebcdd30931f01c41d81e`.
The copied module paths and complete source manifest are in the compilation log.
The pre-execution package and consumer source seals both verified unchanged when
the final receipts were collected. The local `kyoto.py wait` client had been
interrupted before admission to avoid leaving an idle local watch process;
neither job was cancelled. `unit-wait.json` is empty, not a completion receipt.

The independent corpus subsequently added disposition and failing-witness replay
fields. The original successful consumer compilation and source seal remain
preserved as evidence for that earlier source. Consumer alignment to the revised
schema requires a new focused build; no golden comparison has run, and comparison
must wait for the current independent `FREEZE.json`. No further broad native
package run is needed for consumer-only schema changes.

README revision 3 consumer alignment is source-complete. It returns actual
configured dispositions and public Record/Replay results for retained entries,
then original and minimized failing witnesses selected by `shrink_config`.
Focused external consumer build job `b6c08b43052544cea6f8592ae77d0ac7` reuses the
same copy/hash/module/build command, with exact submission in
`consumer-build-r2-submit.json` and source seal in
`consumer-source-r2-sha256.txt`. Its submission was queued. It does not execute
the corpus or compare expectations. The original compilation receipts above
remain unchanged.
