#!/usr/bin/env python3
"""Execute a frozen N05/N06 source snapshot inside one shared Kyoto job.

This bounded lane runner does not replace the unrecovered historical R3 harness
or certify platform/performance parity. All failures and raw outputs are retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if digest(args.manifest) != args.manifest_sha256:
        raise RuntimeError("manifest identity mismatch")
    manifest = json.loads(args.manifest.read_text())
    root = args.source.resolve()
    def verify():
        for name, sha in manifest.items():
            if digest(root / name) != sha:
                raise RuntimeError('source drift: ' + name)
        generated = {'__pycache__', '.build', 'target', 'node_modules', 'dist'}
        current = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                   and not (set(p.relative_to(root).parts) & generated)}
        if current != set(manifest):
            raise RuntimeError('source membership drift: ' + repr(current.symmetric_difference(manifest)))
    verify()
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(platform=platform.platform(), source_manifest_sha256=args.manifest_sha256, commands=[])
    env = dict(os.environ, PYTHONPATH=str(root/'python/src'), CGO_ENABLED='0', PYTHONDONTWRITEBYTECODE='1')
    tools = {name: str(Path(shutil.which(name)).absolute()) for name in ['python3','go','node','npm','swift','rustc','cargo']}
    report['tools'] = {name: dict(path=path, realpath=str(Path(path).resolve()), sha256=digest(Path(path))) for name,path in tools.items()}
    report['driver'] = dict(path=sys.executable, sha256=digest(Path(sys.executable)))
    def run(name, command, cwd=root):
        command = [tools.get(command[0], command[0]), *command[1:]]
        start = time.monotonic()
        with (args.output/(name+'.log')).open('wb') as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                # The unreaped leader still owns this process group ID.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code = 'timeout'
        report['commands'].append(dict(name=name, command=command, cwd=str(cwd), exit_code=code,
            elapsed_seconds=time.monotonic()-start, log_sha256=digest(args.output/(name+'.log'))))
        print(json.dumps(report['commands'][-1]), flush=True)
        (args.output/'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
        return code == 0
    for tool in tools:
        run('version-'+tool, [tool, 'version' if tool=='go' else '--version'])
    run('python', [sys.executable,'-m','unittest','discover','-s','python/tests','-v'])
    if run('go-compile', ['go','test','-c','-o',str(args.output/'go.test')], root/'go'):
        run('go', [str(args.output/'go.test'),'-test.v','-test.timeout=180s'], root/'go')
        report['go_executable_sha256']=digest(args.output/'go.test')
    run('go-vet',['go','vet','./...'],root/'go')
    if run('npm-install',['npm','ci','--ignore-scripts','--no-audit','--no-fund'],root/'typescript'):
        run('typescript',['npm','test'],root/'typescript')
    run('swift',['swift','test','-j','2'])
    report['swift_executables']={str(p.relative_to(root)):digest(p) for p in (root/'.build').rglob('*') if p.is_file() and (p.suffix == '.xctest' or '.xctest/Contents/MacOS/' in str(p))}
    verify()
    for name, item in report['tools'].items():
        if digest(Path(item['path'])) != item['sha256']:
            raise RuntimeError('tool changed: '+name)
    report['source_unchanged']=True
    report['aggregate_child_peak_rss_bytes_not_workload_budget']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    report['status']='passed' if all(c['exit_code']==0 for c in report['commands']) else 'failed'
    (args.output/'receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0 if report['status']=='passed' else 1

if __name__ == '__main__':
    raise SystemExit(main())
