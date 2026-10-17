# Python recorder: input iterator initialization errors

## Scope and finding

Investigated the independent Python engine at main commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`, separately from TypeScript codec work.
The confirmed defect is in `python/src/statelessness/core.py::record`, not BFS
state equality or oracle checkpoint encoding.

`record` finishes and validates its initial checkpoint before opening the input
iterator. It already retains a `model_error` trace if advancing that iterator
fails, but iterator creation was outside the coherent-prefix error handler.
Consequently, two equivalent input-source failures had different evidence:

| Input failure | Before | After |
| --- | --- | --- |
| `__iter__` raises `RuntimeError("source failed")` | Raises `ModelError`; no trace returned | Initial checkpoint retained with `model_error` termination |
| First `__next__` raises the same error | Initial checkpoint retained | Unchanged |
| A later `__next__` raises | Completed prefix retained | Unchanged |

A caller expecting recording to return an exportable error trace could lose its
already-valid initial state, initial checks, and model/build identity. This is a
loss of diagnostic evidence, not a false property failure or silent successful
search. Exact replay of the retained prefix still does **not** reproduce or
validate the unrecorded input-source error.

Python's iterator protocol distinguishes returning an iterator from requesting
its next item. Normal exhaustion is `StopIteration` from `__next__`, not failure
to construct an iterator. See the [iterator protocol documentation](https://docs.python.org/3/library/stdtypes.html#iterator-types).

## Change and boundaries

Catch the existing `ModelError` wrapper around `iter(inputs)` and return the
already-built checkpoint with `model_error` termination and its error text.
No transition executes, and no checkpoint callback is repeated on this path.
The per-transition loop, public signatures, trace format, identities, and search
algorithms are unchanged.

Initial state/check/codec/metadata failures and initial checkpoint budget errors
still raise: no valid checkpoint exists to retain. An initial property failure
still takes precedence without touching inputs. `KeyboardInterrupt`, `SystemExit`,
and `GeneratorExit` still propagate at both iterator stages; this patch does not
catch `BaseException`. See Python's [exception hierarchy](https://docs.python.org/3/library/exceptions.html).

Malformed iterables are handled consistently with other adapter errors. This is
an observable change for callers that previously caught `ModelError` specifically
from input iterator creation: inspect the returned trace's `termination` and
`error` instead. No performance improvement is claimed.

## Executed validation

Environment: Linux, CPython 3.13.5. Source modules and the existing native test
file were retrieved through GitHub and checked against their Git blob hashes.
The local source subset was not a full repository checkout.

- All 26 existing native behavior tests, excluding the corpus CLI integration
  test, passed before and after the change.
- The new 16-test iterator suite produced 10 error outcomes on the original
  recorder (nine affected test methods, including two zero-budget subtests).
  All 16 passed with the fix.
- Combined behavior run: 42 tests passed.
- JSON round trips and a fresh-process replay of the retained initial checkpoint
  passed. Replay reported an exact prefix and did not report a reproduced
  property failure.
- A deterministic 1,260-case before/after matrix produced identical outcomes for
  previously working paths. It varied sequence length, normal versus failing
  `__next__`, step caps, payload budgets, and initial/later property failures.
  The matrix contained 86 completed traces, 350 model-error traces, 416 property
  failures, 156 step-limited traces, and 252 pre-checkpoint errors. All 1,008
  returned traces replayed exactly.
- Python compilation and whitespace/diff checks passed.

The full corpus runner, paired-profile suite, native ABI, Rust suites, other
Python versions, and GitHub CI were not executed as part of this local result.
This report does not claim full portable-profile or Rust qualification.

## Reproduce

From the repository root:

```sh
PYTHONPATH=python/src python -m unittest discover \
  -s python/tests -p test_record_iterator.py -v

# Compare the original recorder against this branch using identical helpers/types.
git show 0876717bee85e335449c2c99ada41f6c6e6112d1:python/src/statelessness/core.py \
  > /tmp/statelessness-core-before.py
PYTHONPATH=python/src python python/tests/record_iterator_matrix.py \
  /tmp/statelessness-core-before.py

# Full native test file, including corpus CLI, for a complete checkout.
PYTHONPATH=python/src python -m unittest discover \
  -s python/tests -p test_native.py -v
```

The matrix script is a comparison against a known baseline, not an independent
specification oracle. The focused unit tests directly assert retained evidence,
error classification, callback counts, control-exception propagation, and replay
behavior. Together they demonstrate this fix's boundary without claiming a proof
of the entire recorder.
