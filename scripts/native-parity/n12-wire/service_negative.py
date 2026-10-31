#!/usr/bin/env python3
"""Run inside Kyoto against an immutable source/package copy, never in-place.

An automatic producer acknowledgement must fail the independently authored
pending-producer assertion. Import/compile failures are NOT detections.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('--source',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
results=[]
for name in ('control','automatic-producer-ack'):
    root=a.output/name
    shutil.copytree(a.source/'python',root,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.venv'))
    module=root/'src/statelessness/debugger_protocol_services.py'
    before=hashlib.sha256(module.read_bytes()).hexdigest()
    if name!='control':
        source=module.read_text()
        needle="            fields[0]=('probes.capture_revision',n(ack.capture_revision));self.selected="
        if source.count(needle)!=1: raise RuntimeError('mutant target not uniquely identified')
        source=source.replace(needle,"            for producer in h.pending_producers: h.acknowledge(producer,h.revision,1)\n"+needle)
        module.write_text(source)
    cmd=[sys.executable,'-B','-m','unittest','test_debugger_protocol_services.ServicesTests.test_probe_configuration_does_not_acknowledge_host','-v']
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'src')}
    run=subprocess.run(cmd,cwd=root/'tests',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (root/'test.log').write_bytes(run.stdout)
    passed=(run.returncode==0 and b'OK' in run.stdout) if name=='control' else (run.returncode==1 and b'AssertionError: Tuples differ: () != (3,)' in run.stdout and b'FAILED (failures=1)' in run.stdout and b'ERROR:' not in run.stdout)
    results.append({'name':name,'command':cmd,'module_before_sha256':before,'module_after_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'exit':run.returncode,'matched_semantic_assertion':passed,'log_sha256':hashlib.sha256(run.stdout).hexdigest()})
    if not passed: break
(a.output/'receipt.json').write_text(json.dumps({'passed':len(results)==2 and all(r['matched_semantic_assertion'] for r in results),'results':results},indent=2)+'\n')
raise SystemExit(0 if len(results)==2 and all(r['matched_semantic_assertion'] for r in results) else 1)
