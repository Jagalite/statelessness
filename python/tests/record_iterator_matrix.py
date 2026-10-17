"""Compare old/new record results using identical model objects and helpers.

Usage: PYTHONPATH=python/src python python/tests/record_iterator_matrix.py BEFORE_CORE
"""
import ast
from itertools import product
from pathlib import Path
import sys

import statelessness.core as core
from test_record_iterator import Counter, NextError

source = Path(sys.argv[1]).read_text()
node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'record')
scope = vars(core).copy()
exec(compile(ast.get_source_segment(source, node), str(sys.argv[1]), 'exec'), scope)
before = scope['record']

class Bounded(Counter):
    def __init__(self, bound):
        self.bound = bound
    def check_state(self, state):
        return (core.Check('bound') if state <= self.bound else core.Check('bound', 'failed', 'above bound'),)


def capture(fn, model, inputs, max_steps, limits):
    try:
        return ('trace', fn(model, inputs, max_steps=max_steps, limits=limits))
    except Exception as error:
        return ('error', type(error).__name__, str(error))

counts = {}
total = 0
for length, ending, cap, payload, bound in product(
    range(7), ('complete', 'next_error'), (0, 1, 2, 4, 7, 20), (0, 1, 3, 12, 10000), (6, 9, 100)
):
    values = tuple(range(1, length + 1))
    def inputs():
        return iter(values) if ending == 'complete' else NextError(RuntimeError('source failed'), values)
    limits = core.TraceLimits(max_payload_bytes=payload)
    old = capture(before, Bounded(bound), inputs(), cap, limits)
    new = capture(core.record, Bounded(bound), inputs(), cap, limits)
    assert old == new, (length, ending, cap, payload, bound, old, new)
    total += 1
    kind = new[1].termination if new[0] == 'trace' else 'pre_checkpoint_error'
    counts[kind] = counts.get(kind, 0) + 1
    if new[0] == 'trace':
        core.validate_recording(new[1])
        report = core.replay(Bounded(bound), new[1])
        assert report.outcome == 'exact', report
print(f'{total} unchanged-behavior comparisons passed; all returned prefixes replayed exactly')
print(dict(sorted(counts.items())))
