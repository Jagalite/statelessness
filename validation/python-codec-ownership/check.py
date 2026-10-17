"""Validate Python decoder ownership with grids, fresh processes, and mutations."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

if not __debug__:
    raise RuntimeError("Run this validator without -O or PYTHONOPTIMIZE")

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True, type=Path, help="Directory for JSON evidence")
EVIDENCE = parser.parse_args().output.resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT / "python/src"), str(ROOT / "python/tests")]
from statelessness import replay, record
from test_codec_ownership import Counter, ScratchCounter, History, WithOracle, OracleState, box, wire

# Deterministic value/length grids; all encoded values stay within one byte.
valid = 0
for initial in range(16):
    for delta in range(8):
        for length in range(5):
            trace = record(Counter(initial=initial), [box(delta) for _ in range(length)])
            report = replay(ScratchCounter(initial=initial), trace)
            assert trace.termination == 'completed'
            assert (report.outcome, report.steps_verified) == ('exact', length)
            valid += 1
changed = 0
for initial in range(16):
    for delta in range(8):
        if initial == delta:
            continue
        trace = record(Counter(initial=initial), [box(delta)])
        report = replay(ScratchCounter(initial=initial, bias=initial-delta), trace)
        assert (report.outcome, report.step, report.field) == ('diverged', 1, 'state')
        changed += 1
checkpoints = 0
for actual in range(16):
    for history in range(16):
        decoder = ScratchCounter()
        model = WithOracle(decoder, History(decoder))
        first = model.decode_state(wire(actual, history))
        second = model.decode_state(wire(50, 51))
        second.model['values'][0] = 52
        second.oracle['values'][0] = 53
        assert first == OracleState(box(actual), box(history))
        assert decoder.scratch == box(51)
        checkpoints += 1

# Producer and each consumer run in a different process using trace JSON.
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           PYTHONPATH=os.pathsep.join((str(ROOT/'python/src'), str(ROOT/'python/tests'))))
producer = '''
import json
from statelessness import record, trace_json
from test_codec_ownership import Counter, box
for initial in (3, 7, 10, 15):
    for delta in (1, 2):
        print(json.dumps({'initial': initial, 'delta': delta,
              'trace': trace_json.dumps(record(Counter(initial=initial), [box(delta)]))}))
'''
produced = subprocess.run([sys.executable, '-c', producer], env=env, check=True, capture_output=True, text=True, timeout=30)
(EVIDENCE/'fresh-process-traces.jsonl').write_text(produced.stdout)
consumer = '''
import json, sys
from statelessness import replay, trace_json
from test_codec_ownership import ScratchCounter
case = json.loads(sys.stdin.read())
report = replay(ScratchCounter(initial=case['initial'], bias=case['bias']), trace_json.loads(case['trace']))
print(report.outcome)
'''
fresh = []
for line in produced.stdout.splitlines():
    case = json.loads(line)
    for bias, expected in ((0, 'exact'), (case['initial']-case['delta'], 'diverged')):
        payload = dict(case, bias=bias)
        result = subprocess.run([sys.executable, '-c', consumer], env=env, input=json.dumps(payload), check=True, capture_output=True, text=True, timeout=30)
        assert result.stdout.strip() == expected
        fresh.append({'initial':case['initial'], 'delta':case['delta'], 'bias':bias, 'outcome':result.stdout.strip()})

# Fresh process for each deliberately broken ownership-boundary variant.
core = (ROOT/'python/src/statelessness/core.py').read_text()
oracle = (ROOT/'python/src/statelessness/oracle.py').read_text()
mutants = {
 'remove_initial_state_snapshot': (core.replace('_copy(_call("decode initial state", model.decode_state, trace.initial_state))', '_call("decode initial state", model.decode_state, trace.initial_state)'), oracle),
 'remove_input_snapshot': (core.replace('_copy(_call("decode input", model.decode_input, expected.input))', '_call("decode input", model.decode_input, expected.input)'), oracle),
 'remove_actual_component_snapshot': (core, oracle.replace('_copy(self.model.decode_state(parts[0]))', 'self.model.decode_state(parts[0])')),
 'remove_history_component_snapshot': (core, oracle.replace('_copy(self.oracle.decode_history(parts[1]))', 'self.oracle.decode_history(parts[1])')),
 'clone_composite_too_late': (core, oracle.replace('        model = _copy(self.model.decode_state(parts[0]))\n        history = _copy(self.oracle.decode_history(parts[1]))\n        return OracleState(model, history)', '        return _copy(OracleState(self.model.decode_state(parts[0]), self.oracle.decode_history(parts[1])))')),
 'shallow_decoded_snapshots': (core.replace('_copy(_call("decode initial state",', '__import__("copy").copy(_call("decode initial state",').replace('_copy(_call("decode input",', '__import__("copy").copy(_call("decode input",'), oracle.replace('_copy(self.model.decode_state(', '__import__("copy").copy(self.model.decode_state(').replace('_copy(self.oracle.decode_history(', '__import__("copy").copy(self.oracle.decode_history(')),
}
runner = '''
import io, json, sys, unittest
import test_codec_ownership
result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(test_codec_ownership))
print(json.dumps({'tests':result.testsRun, 'failures':[str(t) for t,_ in result.failures], 'errors':[str(t) for t,_ in result.errors]}))
sys.exit(not result.wasSuccessful())
'''
mutation_results=[]
for name, (core_variant, oracle_variant) in mutants.items():
    assert (core_variant, oracle_variant) != (core, oracle)
    with tempfile.TemporaryDirectory() as temp:
        package=Path(temp)/'statelessness'; package.mkdir()
        for module in ('__init__.py','trace_json.py'):
            shutil.copy(ROOT/'python/src/statelessness'/module, package/module)
        (package/'core.py').write_text(core_variant)
        (package/'oracle.py').write_text(oracle_variant)
        mut_env=dict(env, PYTHONPATH=os.pathsep.join((temp, str(ROOT/'python/tests'))))
        result=subprocess.run([sys.executable,'-c',runner],env=mut_env,capture_output=True,text=True,timeout=30)
        detail=json.loads(result.stdout)
        assert result.returncode == 1, (name,result.stdout,result.stderr)
        assert detail['failures'] and not detail['errors'], (name,detail)
        mutation_results.append({'mutation':name,**detail})

summary={'valid_trace_grid':valid, 'changed_reducer_grid':changed,
         'checkpoint_grid':checkpoints, 'fresh_process_replays':fresh,
         'mutation_controls':mutation_results,
         'python':sys.version, 'platform':sys.platform}
(EVIDENCE/'extended-results.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'valid_trace_grid':valid,'changed_reducer_grid':changed,'checkpoint_grid':checkpoints,
                 'fresh_process_replays':len(fresh),'mutations_detected':len(mutation_results),
                 'mutation_failure_counts':{r['mutation']:len(r['failures']) for r in mutation_results}},indent=2))
