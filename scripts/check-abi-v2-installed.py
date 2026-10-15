#!/usr/bin/env python3
"""Exercise an installed ABI v2 wheel and npm archive outside the checkout.

Requires staged bindings and Python wheel build tools. Never invokes Rust or
changes defaults. Go/Swift compilation is a separate caller-managed step.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

from binding_evidence import sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
NODE_CONSUMER = r"""
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {createEngine, check, disposition} from '@jagalite/stateless/v2';
const wasm = await readFile(new URL(import.meta.resolve('@jagalite/stateless/stateless_v2.wasm')));
const model = {
  metadata: () => ({name:'installed-counter',build:'v1',model_version:1,properties_version:1,codec_version:1}),
  initialState: () => ({n:0}), cloneState: s => ({...s}),
  cloneInput: i => ({...i}), cloneOutput: o => ({...o}),
  equalStates: (a,b) => a.n === b.n, equalInputs: (a,b) => a.delta === b.delta,
  inputs: () => [{delta:1}][Symbol.iterator](),
  step: (s,i) => ({state:{n:s.n+i.delta},outputs:[],disposition:disposition()}),
  checkState: s => [check('bound',s.n >= 3 ? 'failed' : 'passed')],
  encodeState: s => Uint8Array.of(s.n), decodeState: b => ({n:b[0]}),
  encodeInput: i => Uint8Array.of(i.delta), decodeInput: b => ({delta:b[0]}),
  encodeOutput: o => Uint8Array.of(o.n),
};
const engine = await createEngine(wasm), session = engine.createSession(model);
try {
  if (process.argv[2] === 'record') {
    const result = session.search({maxStates:20,maxTransitions:20,maxDepth:10});
    try { assert.deepEqual(result.inputs(),[{delta:1},{delta:1},{delta:1}]); }
    finally { result.close(); }
    await writeFile('trace.bin',session.recordBinary([{delta:1},{delta:1},{delta:1}]));
  } else {
    const replay = session.replay(new Uint8Array(await readFile('trace.bin')));
    assert.equal(replay.outcome,'exact');
    assert.equal(replay.failure_reproduced,true);
  }
  assert.equal(session.retainedRoots,0);
} finally { session.close(); }
console.log('installed v2 ' + process.argv[2] + ' passed');
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', type=Path, default=ROOT / 'target/bindings')
    parser.add_argument('--output', type=Path, required=True,
                        help='new qualification directory outside the checkout')
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error('--output must be outside the checkout')
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema':'stateless.abi-v2-installed.v1', 'checks':[], 'status':'running'}
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)

    def run(name, command, cwd=out, env=environment):
        command = list(map(str, command))
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=300)
        log = out / (name + '.log'); log.write_bytes(result.stdout)
        report['checks'].append({'name':name,'command':command,'cwd':str(cwd),
                                'exit_code':result.returncode,'log_sha256':sha256(log)})
        write_json(out / 'report.json', report)
        if result.returncode:
            raise RuntimeError(name + ': ' + result.stdout.decode(errors='replace'))
        return result.stdout.decode()

    try:
        distribution = out / 'distribution'
        for name in ('native','python','browser','go-v2','swift-v2','portable-swift'):
            shutil.copytree(args.bindings.resolve() / name, distribution / name)
        libraries = list((distribution/'native/lib').glob('*'))
        library = next(p for p in libraries if p.suffix in ('.so','.dylib','.dll'))
        environment['STATELESS_ABI2_LIBRARY'] = str(library)
        report['native_library_sha256'] = sha256(library)
        run('wheel-build',[sys.executable,'-m','pip','wheel','--no-build-isolation','--no-deps',
                           distribution/'python','--wheel-dir',out/'wheels'])
        wheel = next((out/'wheels').glob('*.whl')); report['wheel_sha256'] = sha256(wheel)
        venv.EnvBuilder(with_pip=True).create(out/'venv')
        python = out/'venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run('wheel-install',[python,'-m','pip','install','--no-index','--no-deps',wheel])
        run('wheel-origin',[python,'-I','-c',
            'import statelessness; from pathlib import Path; '
            f'assert Path({str(out)!r}) in Path(statelessness.__file__).parents; '
            'print(statelessness.__file__)'])
        run('installed-python-tests',[python,'-I','-m','unittest','discover','-s',ROOT/'python/tests','-v'])
        npm = shutil.which('npm'); node = shutil.which('node')
        if not npm or not node:
            raise RuntimeError('npm and Node must be installed')
        packed = json.loads(run('npm-pack',[npm,'pack','--offline','--ignore-scripts','--json',
                                            '--pack-destination',out],distribution/'browser'))
        archive = out/packed[0]['filename']; report['npm_archive_sha256'] = sha256(archive)
        consumer = out/'node-consumer'; consumer.mkdir()
        (consumer/'package.json').write_text('{"name":"abi2-consumer","private":true,"type":"module"}')
        run('npm-install',[npm,'install','--offline','--ignore-scripts','--no-audit','--no-fund',archive],consumer)
        (consumer/'check.mjs').write_text(NODE_CONSUMER)
        run('installed-node-record',[node,'check.mjs','record'],consumer)
        run('installed-node-fresh-replay',[node,'check.mjs','replay'],consumer)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        write_json(out/'report.json',report)
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
