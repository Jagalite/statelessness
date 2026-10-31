# Python N05 execution receipts

Authority checkout reached `a4cc11eea8db62115b452eee3c6610c04fa06b96` before any
existing file was edited. New guided files were created only at paths absent from
that target tree while Git materialized the checkout. No parent checkout edits,
staging, commits, pushes or profile/default promotions were performed.

First focused job: `468ec8e2ad0e498ba0ef797f56dfd5a8` — **passed**, 75 tests.
Review-hardening job: `302d0e9e29dd4bdfa4b5b18c0bbcfa96` — **cancelled before
execution** (`started: null`, no exit code), preserving the proposed 80-test
revision. Both used the exact command below. The owner cancelled this queued job
after the parent requested changed-boundary tests instead of another broad run;
no other owner's job was cancelled.

Exact submitted command, from the assigned checkout:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit \
  --cwd /Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7 \
  -- env PYTHONPATH=python/src:python/tests python3 -m unittest -v \
  test_guided test_search test_append_checks
```

`focused-r1-submit.json` is the original submission. `focused-r1-show.json` and
`focused-r1.log` retain terminal status and raw output; `focused-r1-source.sha256`
pins that implementation/test revision. The matching `focused-r2-*` files retain
the subsequent submission and its changed source identity. The first run reported
75 tests in 0.103 seconds, exit zero. That result does not qualify the later
revision. Effective commands execute unittest, not compilation only. Empty logs
are not a pass. `focused-r2-cancel.json`, `focused-r2-show.json`, and
`focused-r2-resumed-show.json` preserve cancellation and subsequent confirmation.

Changed-boundary job: `8c97dbd659674c739cffa1adcdd7127f` — **queued** at submission.
`focused-r3-command.txt` contains the exact submitted command;
`focused-r3-submit.json` and `focused-r3-source.sha256` preserve its queue receipt
and source identity. This runs ownership/lifetime checks, four reentrancy and
configuration-ownership regressions, four relevant control/admission checks, one
RNG boundary check, and synthetic seal-admission tests. It does not run the full
ordinary suite, installed consumer or independent corpus comparison. Do not
duplicate this job while it remains live.

The current suite includes 44 new guided tests plus existing ordinary-search and
append-check regression suites. Coverage includes source mutation draws, FIFO
retention, recovery with zero newly tracked features, attempted saturation,
property/checker/observer/control order, skipped-count saturation, detached mutable
keys and inputs, callback lease expiry, async return rejection, throwable cleanup
and callback-error priority over sticky feature overrun. A source-review correction
moved sink revocation before feature equality/learning; no execution failure or
qualification was hidden by that correction.

The independent source review confirmed the kernel shape and identified missing
native boundary coverage. The second revision adds custom-awaitable/async-generator,
retained bound-method, snapshot/hash/equality exception and rejection-index tests.
Its guided-only error wrapper preserves the original callback exception as cause
when application `__str__` throws; that secondary diagnostic is retained separately.
Ordinary search/checker exception dispatch is unchanged. The first pass remains
preserved; no failed execution result has been discarded.

The third revision rejects reentrant push from application copy/hash callbacks,
rechecks sticky state and capacity before append, and revokes a callback sink
before formatting its error. The callback's own exception remains primary. It
also captures guided-run configuration scalars before metadata. These changes
have dedicated regressions. Full pre-run source hashes remain available rather
than reusing the first run as evidence for changed source.

The public installed consumer is `scripts/native-guided-consumers/python/consumer.py`.
The parent owns combined installed qualification against the independently frozen
guided-v2 corpus. The optional `python/tests/qualify_guided_install.py` provides a
Python-only wheel/clean-venv path with full command/log/source/artifact receipts;
it has not been submitted and must not duplicate a live combined qualification.
Both consumers refuse a missing or nonmatching independent `FREEZE.json`.
Both read the author's `files["conformance/guided-v2/corpus.json"]` binding;
the independent manifest has no top-level `corpus_sha256` field. The consumer is
aligned with schema revision 3, including actual dispositions and checked public
record/replay for original and minimized failing witnesses. No current independent
seal or installed comparison is claimed here; the archived 78-case freeze remains
historical. Seal-admission unit fixtures contain no behavioral oracle and execute
no candidate model or comparison.
