# N05 implementation review and qualification boundaries

Work is isolated in `t3/cb476da7`, based on
`a4cc11eea8db62115b452eee3c6610c04fa06b96`. The parent checkout and its N14
qualification sources are outside the edit scope. No push, pull request,
publication, profile promotion or default change is authorized by this work.

The [source contract](N05-SOURCE-CONTRACT.md) and
[independent corpus](../../conformance/guided-v2/README.md) derive expectations
from the actual Rust sources. The previous guided/campaign audit is source-only
input, not executed evidence or N05 completion. Bare N05 does not require N06
campaigns or N10 assurance.

## Review findings

These findings came from source inspection before final installed qualification.
Executed corrections require their own source-bound receipts; an authored test
alone does not close that gate.

| Finding | Required correction | Disposition |
| --- | --- | --- |
| Python and Swift feature cloning can recursively push through a captured sink after the outer capacity check | Guard reentrant writes, retain sticky failure, and check failure before the outer append | Owners added guards and native regressions; final-source execution tracked separately |
| Python callback exception formatting can use a captured sink before outer cleanup | Revoke inside callback dispatch before formatting the error; preserve the callback's own error over sticky failure | Independently reproduced by Go/Python reviewer; owner added formatting-time lifetime tests |
| Go ordinary fuzz lacks Rust's second reused-input control poll | Add that boundary to guided search while preserving the existing ordinary path | Independent reviewer confirmed the guided-only correction and regression source |
| Guided Go needlessly clones the previous ordinary sequence at case completion | Restrict that existing clone to ordinary fuzz; guided selection owns its feature corpus | Independent reviewer confirmed correction |
| TypeScript extraction changed ordinary exception formatting for throwing getters/coercions | Preserve the existing shared callback expression and scope defensive formatting to new feedback hooks | Owner restored ordinary behavior and authored secondary-exception identity regressions |
| Swift expired sinks retain the feature-clone closure and its captures | Clear both the storage owner and clone closure during revocation | Owner added weak-capture release tests for successful and throwing callbacks; focused execution pending |
| TypeScript guided formatting returns a non-string `Error.message` before protected coercion | Convert the selected diagnostic with `String` inside the guarded formatter | Owner corrected guided formatting and added focused hostile-message tests; ordinary dispatch is unchanged; execution pending |
| Installed harness and TypeScript/Python consumers expect a nonexistent top-level corpus hash in the independent seal | Validate the author's exact `files` map and pinned source authority | Owners corrected readers; the harness also verifies all four independently sealed files; no comparison is authorized by a fabricated or rewritten seal |
| Independent oracle treats an empty callback error as false | Test nullable presence rather than truthiness, including empty callback error after ignored overrun | Old 78-case seal archived; independently authored revision required before further comparison |
| Initial corpus cannot distinguish missing final feedback poll | Add final-step feedback cancellation, generator-None cancellation and zero-case control cases | Added to the requested independent revision |
| Initial corpus models only Accepted transitions | Add parameterized Rejected/Ignored outcomes and compare actual dispositions | Consumer/schema extension requested before final freeze |
| Initial expected replay evidence covers only passing corpus prefixes | Record/replay original and minimized selected property failures; compare actual replay fields | Consumer/schema extension requested; passing prefixes remain unshrunk |
| The original disabled-reuse fixture performs only successful enabled checks | Add a source-derived case that actually rejects retained candidates and regenerates inputs, with explicit trace anchors | Independent reviewer identified the gap before comparison; additive fixture requested while preserving historical expectations |

The review also requested an `index(0)` boundary assertion. This is explicitly
covered by the authored Rust public host checks and the existing portable RNG
qualification lane, rather than being inferred from empty-corpus selection.
Those lanes still need actual final execution.

Independent round `n05-final-corpus-review-r2` confirmed all five semantic corpus
corrections against Rust source and found no additional mismatch in the 90-case
derivation, ordering, RNG use, FIFO/recovery or shrink/replay anchors. It found
one remaining seal-check issue: the oracle's `--check` path accepted incomplete
`files` maps and ignored revision/count metadata. The author added strict
four-path/schema/count/provenance admission; root inspected that narrow
correction. This affects receipt verification, not the independently derived
expected values. The active seal was absent at review time, so no revised
materialization or comparison was claimed.

The independent Go/Python review found no further high-confidence Go defect in
the inspected corpus accounting, collision handling, mutation call graph or
shared execution loop. It inspected consumers without finding execution driven
by expected outputs. This is a bounded source-review result, not a general
equivalence proof.

Review identities at that read were:

```text
go/fuzz_kernel.go cb5eef46acefbb7f2da6b1bab20f8442ee2148277bc626d93f90a4d5ac5c0513
go/guided.go 3bbf382922729fc97102bd1bf449a01c8c5c45eb446d8b15d09dffe6fabfba6f
go/guided_test.go 564127a74780e24b94ddf2409e303c66a8cc73faae4e5f7a8b6ecd9aed12ccb0
python/src/statelessness/guided.py b8ec84517ec10b3b47dd851582b42ddda3817eb41c99666337002a825ebe0225
python/src/statelessness/search.py 4cef4f85ff012660764b473f4e8f30ca2df1384f51932af175d090cf58c92fb6
python/tests/test_guided.py e4958d9d2e17fefba49bd8cc3a81eab50caad6b5c6919783f33df0a3d538a0a2
```

Python's reviewed hashes precede its corrections. Final receipt manifests, rather
than these historical review hashes, identify the source actually executed.

The independent Swift/TypeScript review confirmed the outstanding Swift sink
issues in its inspected snapshot and identified the diagnostic and seal-reader
issues above. The new independent round `n05-final-boundary-review-r2` found no
remaining high-confidence defect in the corrected native boundaries. It
confirmed sink revocation before formatting, guarded reentry, callback-error
precedence, Swift capture release, native synchronous policies and preservation
of ordinary dispatch. Its [reviewed hashes](../../validation/native-parity/guided-slice/source-review/native-boundaries-r2.sha256)
identify this bounded source-review result.

Separate round `n05-harness-review-r2` found no additional concrete blocker in
the installed harness after the seal-reader correction. It inspected public
import provenance, actual Rust API use, pinned compiler paths, lock/source
checks, exact output comparison and execution gates. Its
[reviewed hashes](../../validation/native-parity/guided-slice/source-review/harness-r2.sha256)
are separate from future execution receipts. Neither review edited files, ran
builds/tests, compared candidate output, or established runtime parity.

The harness author's separate independent implementation review R2 identified
three additional evidence issues: ambient Cargo configuration could influence
the compiler, directory symlinks could hide toolchain referents, and final sealing
did not revalidate every referenced comparison object. The final corrections
freeze configuration absence and the actual sysroot, reject directory symlinks,
and verify each content-addressed actual/expected object before sealing. Root
inspected these corrections; [final inspected hashes](../../validation/native-parity/guided-slice/source-review/harness-final-root-inspected.sha256)
supersede the earlier harness source identity without rewriting that review.
No executable qualification is implied by this inspection. The author's review
handle is `node:delegated-task:command%3Amcp%3Af5fafc0e-8145-4028-b767-85f2dd929ae1%3Adelegate-task%3An05-harness-implementation-review-r2`.

## Parent isolation

The read-only [parent guard](../../scripts/check-n05-parent-source.py) compared
254 selected source/package files, including embedded Rust requirements, against
the pinned Git objects on 2026-10-22 at 14:11:06 UTC. It found no membership,
file-type, executable-mode or content differences. Its compact inventory is
`7618966292502f82fe8f9b964a49c0e60626062199e208d8e04ffafd2d595ff9`;
[raw observation](../../validation/native-parity/guided-slice/parent-guard/during-implementation.json)
and Kyoto job `8bb04540cca54a2686375a5c11dde2d9` are retained. This observation
does not qualify N14 or infer anything about unrelated parent artifacts.

## Execution scope

The [installed harness](N05-QUALIFICATION-HARNESS.md) requires fresh public
package consumers in all five engines, actual Rust execution, frozen expected
data, complete callback/RNG/report comparisons, witness replay, and the existing
portable baseline. Compile-only results do not count as tests. Raw failed,
superseded and corrected receipts remain separate.

At this stage implementations and consumers are still under qualification.
Only explicitly recorded native macOS executions can become qualified here.
The broad 144 platform/feature cells, profile/default promotion, N06, N10 and
universal equivalence remain open. Retention limits bound counts rather than
process RSS or application callback allocations.
