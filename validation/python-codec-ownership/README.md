# Python decoder ownership audit

Date: 2026-10-17. Base: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
Scope: the native Python replay engine and paired checkpoint decoder. No
TypeScript, Rust, ABI, format-version, or CI changes are included. The separately
published TypeScript PRs #7 and #8 were checked for overlap, not used as the test
oracle or modified by this work.

## Reproduced failures

`WithOracle.decode_state` kept the actual decoder's returned object while calling
the history decoder. When both return the same scratch object, an independently
framed checkpoint containing actual state **7** and history **42** restored as
**42 / 42**. Distinct outer dictionaries can still share a nested mutable list,
so a shallow copy does not solve the problem. A later decode could also change
an earlier returned checkpoint, even when the two component decoders use separate
workspaces.

Core `replay` similarly retained decoded initial state and input objects across
later callbacks. This can produce either false divergence or a false `exact`
result. The smallest false-exact regression records state 7 and input 2 under an
addition reducer, yielding state 9. During replay, a changed reducer adds an
extra 5. A shared decoder workspace changes the saved initial state from 7 to 2
when decoding the input, so the changed reducer computes `2 + 2 + 5 = 9` and the
old replay incorrectly accepts it. With the snapshot fix, it computes
`7 + 2 + 5 = 14`, and replay correctly reports divergence at step 1, field `state`.
The fixture deliberately keeps metadata unchanged to test observation comparison,
not identity/version rejection.

Reusing a decoder workspace during canonical re-encoding can also change the
input subsequently sent to the reducer. Reusing it during the reducer can change
the input subsequently seen by transition checks. Both boundaries are covered.

## Fix and compatibility

Snapshot each decoded initial state and input immediately with the existing
`_copy` helper. In `WithOracle.decode_state`, snapshot the actual component before
invoking the history decoder, then snapshot the history before returning it.
Complete frame validation still happens before any component callback.

The public signatures, ORC1 framing, strict immutable-`bytes` encoder contract,
canonical re-encoding checks, metadata comparisons, and trace comparison order
are unchanged. A component that cannot be snapshotted now fails at its ownership
boundary; a failed actual-component snapshot prevents the history decoder from
running. Existing decoder exception behavior is otherwise unchanged.

This applies the library's existing defensive-copy policy to retained decoder
results. It does not permit nondeterministic logical history or turn callbacks
into a sandbox. Values still need correct `deepcopy` behavior; custom copy hooks
that deliberately retain mutable aliases remain outside the documented contract.
Python's [copy documentation](https://docs.python.org/3.10/library/copy.html)
explains the distinction between shallow/deep copies and user-defined hooks.

The cost is one extra initial-state snapshot, one per replay input, and two
component snapshots per paired checkpoint decode. When those APIs compose, so do
the copies. No throughput or memory-reduction claim is made. Ordinary recording
and BFS code are unchanged, and `paired-v1` remains experimental.

## Validation performed

Local environment: Linux, Python 3.13.5. Because direct Git checkout/download was
unavailable, selected files were fetched through the GitHub connector and their
complete original byte contents checked against Git blob SHA-1s before testing.
The native source modules, existing test files, and paired TSV fixture were used
without unrelated edits. This was a source-based test workspace, not an installed
wheel or a full-repository checkout.

| Check | Result |
| --- | --- |
| Existing native behavior tests, excluding the corpus CLI test | 26 passed before and after |
| Existing paired tests, including all 11 TSV golden cases | 7 passed before and after |
| New ownership tests | 11 failed / 5 passed before; 16 passed after |
| Combined selected unit suite | 49 passed after |
| Valid trace grid: 16 initial values x 8 input values x 5 lengths | 640 exact replays |
| Tailored changed-reducer grid, unequal initial/input values | 120 false-exact results before; 120 divergences after |
| Shared checkpoint grid: 16 actual x 16 history values | 256 passed, including subsequent decodes and mutation |
| Fresh-process JSON replay | 8 unchanged reducers exact; 8 changed reducers diverged |
| Deliberately weakened fixes | All 6 detected, without test infrastructure errors |
| Python compilation and patch whitespace check | Passed |

The six mutation controls remove the initial, input, actual-component, or
history-component snapshot individually, copy the composite too late, or use
shallow decoded snapshots. They cause 3, 2, 5, 2, 4, and 11 regression failures
respectively. These controls establish sensitivity to the placement and depth of
the fix; the finite grids are not a universal correctness proof.

The main portable corpus CLI/harness, optional Rust ABI suite, installed-package
qualification, other Python versions, and other operating systems were **not**
run locally in this continuation. Earlier agents' test counts are not reused as
qualification evidence for this patch. No independent second-reviewer claim is
made; the change was reviewed with regression, mutation, and compatibility tests.

## Reproduce

From a complete checkout with this change:

```sh
PYTHONPATH=python/src python -m unittest discover -s python/tests -p test_codec_ownership.py -v
python validation/python-codec-ownership/check.py --output /tmp/python-codec-audit
```

The second command checks the grids and fresh processes, then creates isolated
temporary source trees for the six mutations. It never changes the checked-out
source. It writes `extended-results.json` and `fresh-process-traces.jsonl` to the
explicit output directory.

The exact selected native/paired unit run used locally was:

```sh
PYTHONPATH=python/src:python/tests python - <<'PY'
import sys, unittest
import test_native, test_paired, test_codec_ownership
loader = unittest.TestLoader()
suite = unittest.TestSuite(
    test_native.NativeTests(name)
    for name in loader.getTestCaseNames(test_native.NativeTests)
    if name != "test_corpus_cli"
)
suite.addTests(loader.loadTestsFromModule(test_paired))
suite.addTests(loader.loadTestsFromModule(test_codec_ownership))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
PY
```

The repository's ordinary full Python and portable-conformance commands remain
additional qualification gates; the explicit exclusion above must not be
mistaken for a full-suite pass.
