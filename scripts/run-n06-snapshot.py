#!/usr/bin/env python3
"""Stage and compare N06 consumers from a prepared source snapshot.

Submit this script through Kyoto after separately submitting the Rust build
directly to Kyoto from the snapshot. This script never runs Cargo. Its receipt
is caller-attested: hashes and job identity do not prove the executable was built
from the snapshot or that installed consumers loaded only declared artifacts.
It does not qualify multiworker, fault, mutation, performance or platform gates.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import shutil
import subprocess
import sys
import platform
import math
import time
import traceback

KYOTO = '/Volumes/seed1/Projects/kyoto/kyoto.py'
SETUPTOOLS_WHEEL = 'setuptools-80.9.0-py3-none-any.whl'
SETUPTOOLS_SHA256 = '062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922'

def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def validate_backend(path):
    """Require the exact wheel used for the offline Python build backend."""
    path = path.resolve(strict=True)
    if not path.is_file() or path.name != SETUPTOOLS_WHEEL or digest(path) != SETUPTOOLS_SHA256:
        raise ValueError('setuptools wheel does not match the pinned filename and SHA-256')
    return path

def module(path):
    spec = importlib.util.spec_from_file_location('n06_snapshot', path)
    obj = importlib.util.module_from_spec(spec); spec.loader.exec_module(obj)
    return obj

def inventory(root):
    return sorted(p.resolve() for p in root.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts)

def validate_build_window(checkpoint, window, snapshot, rust, job_id, job):
    """Check caller-attested build bracket against the verified source and Kyoto job."""
    before = json.loads(checkpoint.read_text())
    after = json.loads(window.read_text())
    def finite(value): return type(value) in (int, float) and math.isfinite(value)
    source_manifest = digest(snapshot / 'source-manifest.json')
    helper = snapshot / 'source/scripts/n06-build-window.py'
    if (before.get('schema') != 'n06-build-window-before/1' or after.get('schema') != 'n06-build-window/1'
            or before.get('snapshot') != str(snapshot) or after.get('snapshot') != str(snapshot)
            or before.get('manifest_sha256') != source_manifest or after.get('manifest_sha256') != source_manifest
            or before.get('helper_sha256') != digest(helper) or after.get('checkpoint_sha256') != digest(checkpoint)
            or after.get('kyoto_job') != job_id or after.get('requested_command') != job.get('requested_command')
            or after.get('binary') != str(rust) or after.get('binary_sha256') != digest(rust)
            or after.get('log_sha256') != digest(Path(job['log']).resolve(strict=True))
            or not all(finite(v) for v in (before.get('verified_at'), after.get('verified_before'),
                                           after.get('verified_after'), job.get('submitted'), job.get('finished')))
            or before['verified_at'] != after['verified_before'] or after.get('submitted') != job['submitted']
            or after.get('finished') != job['finished']
            or not (before['verified_at'] <= job['submitted'] <= job['finished'] <= after['verified_after'])):
        raise ValueError('build-window receipt does not match snapshot, job and executable')
    return {'checkpoint_sha256':digest(checkpoint), 'window_sha256':digest(window),
            'scope':'caller-attested bracket; transient source drift and post-build artifact replacement are not excluded'}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--rust-executable', type=Path, required=True)
    ap.add_argument('--rust-kyoto-job', required=True)
    ap.add_argument('--rust-build-checkpoint', type=Path, required=True)
    ap.add_argument('--rust-build-window', type=Path, required=True)
    ap.add_argument('--setuptools-wheel', type=Path, required=True,
                    help='offline setuptools 80.9.0 wheel matching the pinned SHA-256')
    a = ap.parse_args()
    snap = a.snapshot.resolve(strict=True); src = snap / 'source'
    manifest = json.loads((snap / 'source-manifest.json').read_text())
    original = Path(manifest['source_root']).resolve(strict=True)
    out = a.output.resolve(); rust = a.rust_executable.resolve(strict=True)
    try: backend = validate_backend(a.setuptools_wheel)
    except (OSError, ValueError) as error: ap.error(str(error))
    for path in (out, rust, backend):
        if path == original or path.is_relative_to(original) or path == snap or path.is_relative_to(snap):
            ap.error('output, Rust executable, and backend wheel must be outside original checkout and snapshot')
    if out.exists(): ap.error('output must not exist')
    out.mkdir(parents=True); logs = out / 'logs'; logs.mkdir()
    receipt = {'schema':'n06-snapshot-execution/1', 'status':'failed', 'snapshot':str(snap),
               'snapshot_manifest_sha256':digest(snap / 'source-manifest.json'),
               'rust_executable':str(rust), 'rust_sha256':digest(rust), 'rust_kyoto_job':a.rust_kyoto_job,
               'orchestrator_sha256':digest(Path(__file__).resolve()),
               'host':platform.platform(), 'python':sys.version,
               'python_build_backend':{'name':'setuptools','version':'80.9.0','sha256':SETUPTOOLS_SHA256},
               'scope':'caller-attested source-to-artifact linkage; pinned offline Python build backend; not full N06 qualification', 'commands':[]}
    def run(label, argv, cwd, env=None):
        entry = {'label':label, 'argv':[str(x) for x in argv], 'cwd':str(cwd)}
        receipt['commands'].append(entry)
        try:
            execute(label,entry,cwd,env)
        except Exception as error:
            entry['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            for stream in ('stdout','stderr'):
                path=logs/(label+'.'+stream)
                if path.is_file(): entry[stream+'_sha256']=digest(path)
    def execute(label,entry,cwd,env):
        with (logs / (label+'.stdout')).open('wb') as so, (logs / (label+'.stderr')).open('wb') as se, selectors.DefaultSelector() as sel:
            proc = subprocess.Popen(entry['argv'], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            started=time.monotonic(); sizes={so:0,se:0}
            for pipe,target in ((proc.stdout,so),(proc.stderr,se)):
                os.set_blocking(pipe.fileno(),False); sel.register(pipe,selectors.EVENT_READ,target)
            try:
                while sel.get_map():
                    if time.monotonic()-started > 900: raise RuntimeError(label+': timeout')
                    for key,_ in sel.select(0.1):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk: sel.unregister(key.fileobj); key.fileobj.close(); continue
                        target=key.data; sizes[target]+=len(chunk)
                        if sizes[target]>16*1024*1024: raise RuntimeError(label+': output limit exceeded')
                        target.write(chunk)
                code=proc.wait(timeout=max(.001,900-(time.monotonic()-started)))
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                else: raise RuntimeError(label+': descendant remained')
            finally:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait()
                for pipe in (proc.stdout,proc.stderr):
                    if not pipe.closed: pipe.close()
        entry['exit']=code
        if code: raise RuntimeError(label+' exited '+str(code))
    verify = module(src / 'scripts/prepare-n06-snapshot.py').verify
    try:
        verify(snap)
        if Path(__file__).resolve() != (src/'scripts/run-n06-snapshot.py').resolve():
            raise ValueError('orchestrator must execute from the verified snapshot')
        job = json.loads(subprocess.check_output([sys.executable,KYOTO,'show',a.rust_kyoto_job]))
        expected = ['cargo','build','--manifest-path','conformance/fixtures/native-campaign-rust/Cargo.toml','--target-dir']
        requested = job.get('requested_command', [])
        if (job.get('status') != 'succeeded' or job.get('exit_code') != 0 or Path(job.get('cwd','')).resolve() != src
                or requested[:5] != expected or len(requested) != 8 or requested[6:] != ['--bin','native-campaign-public']
                or job.get('command') != requested
                or rust != (Path(requested[5]).resolve()/'debug/native-campaign-public').resolve()):
            raise ValueError('Rust Kyoto job does not match declared snapshot and executable')
        receipt['rust_job_requested'] = requested
        checkpoint=a.rust_build_checkpoint.resolve(strict=True); window=a.rust_build_window.resolve(strict=True)
        for path in (checkpoint,window):
            if path.is_relative_to(original) or path.is_relative_to(snap):
                raise ValueError('build-window evidence must be outside checkout and snapshot')
        receipt['rust_build_window']=validate_build_window(checkpoint,window,snap,rust,a.rust_kyoto_job,job)
        for source,name,key in ((checkpoint,'rust-build-checkpoint.json','checkpoint_sha256'),
                                (window,'rust-build-window.json','window_sha256')):
            target=out/name; shutil.copyfile(source,target)
            if digest(target) != receipt['rust_build_window'][key]:
                raise ValueError('build-window evidence changed during copy')
        stage = out/'stage'; stage.mkdir()
        for name in ('python','typescript','go','swift'):
            shutil.copytree(src/name, stage/name, symlinks=False)
        shutil.copy2(src/'Package.swift',stage/'Package.swift')
        shutil.copytree(src/'docs/requirements', stage/'docs/requirements')
        consumers = out/'consumers'; consumers.mkdir()
        for name in ('python','go','typescript','swift'):
            shutil.copytree(src/'examples/native-campaign'/name, consumers/name)
        env = os.environ.copy()
        for key in list(env):
            if key in ('PYTHONPATH','PYTHONHOME','NODE_PATH','NODE_OPTIONS','GOFLAGS','GOWORK','GOTOOLCHAIN','SWIFTPM_MODULECACHE_OVERRIDE') or key.upper().startswith(('NPM_CONFIG_','PIP_')): env.pop(key,None)
        env.update(PYTHONNOUSERSITE='1',PYTHONOPTIMIZE='0',PIP_CONFIG_FILE=os.devnull,PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
        run('python-backend-pin-tests',[sys.executable,src/'scripts/test_n06_backend_pin.py'],out,env)
        run('build-window-tests',[sys.executable,src/'scripts/test_n06_build_window.py'],out,env)
        run('build-window-integration-tests',[sys.executable,src/'scripts/test_n06_build_window_integration.py'],out,env)
        receipt['build_tools']={name:str(Path(shutil.which(name)).resolve(strict=True)) for name in ('go','node','npm','swift')}
        for name,argv in (('go-version',['go','version']),('node-version',['node','--version']),('npm-version',['npm','--version']),('swift-version',['swift','--version'])):
            run(name,argv,out,env)
        venv=out/'venv'; run('venv',[sys.executable,'-m','venv',venv],out,env)
        py=venv/'bin/python'; wheels=out/'wheels'; wheels.mkdir()
        receipt['python_interpreter']={'path':str(Path(sys.executable).resolve()),'sha256':digest(Path(sys.executable).resolve())}
        run('python-pip-version',[py,'-m','pip','--version'],out,env)
        backend_copy=out/SETUPTOOLS_WHEEL; shutil.copyfile(backend,backend_copy)
        if digest(backend_copy) != SETUPTOOLS_SHA256: raise ValueError('copied setuptools wheel changed')
        run('python-backend-install',[py,'-m','pip','install','--no-index','--no-deps',backend_copy],out,env)
        run('python-wheel',[py,'-m','pip','wheel','--no-build-isolation','--no-index','--no-deps','--wheel-dir',wheels,stage/'python'],out,env)
        wh=list(wheels.glob('*.whl'))
        if len(wh)!=1: raise ValueError('expected one wheel')
        run('python-install',[py,'-m','pip','install','--no-deps',wh[0]],out,env)
        run('typescript-ci',['npm','ci','--ignore-scripts'],stage/'typescript',env)
        run('typescript-build',['npm','run','build'],stage/'typescript',env)
        packs=out/'packs'; packs.mkdir()
        run('typescript-pack',['npm','pack','--pack-destination',packs],stage/'typescript',env)
        tgz=list(packs.glob('*.tgz'))
        if len(tgz)!=1: raise ValueError('expected one npm tarball')
        tc=consumers/'typescript'
        (tc/'package.json').write_text(json.dumps({'name':'n06-consumer','version':'0.0.0','private':True,'type':'module'})+'\n')
        run('typescript-install',['npm','install','--ignore-scripts','--offline','--no-audit','--no-fund',tgz[0]],tc,env)
        gc=consumers/'go'; run('go-replace',['go','mod','edit','-replace','github.com/Jagalite/statelessness/go='+str(stage/'go')],gc,env)
        gobin=out/'n06-go'; run('go-build',['go','build','-o',gobin,'.'],gc,env)
        sc=consumers/'swift'; swift_env=env.copy(); swift_env['STATELESS_SWIFT_PACKAGE_PATH']=str(stage)
        scratch=out/'swift-build'; run('swift-build',['swift','build','--package-path',sc,'--scratch-path',scratch],out,swift_env)
        swift=(scratch/'debug/CampaignConsumer').resolve(strict=True)
        site=next((venv/'lib').glob('python*/site-packages'))
        node=Path(shutil.which('node')).resolve(strict=True)
        def engine(argv, consumer, artifacts):
            paths=set(p.resolve(strict=True) for p in artifacts); paths.update((Path(argv[0]).resolve(strict=True),consumer.resolve(strict=True)))
            if any(p.is_relative_to(original) or p.is_relative_to(snap) for p in paths): raise ValueError('runtime artifact inside source')
            return {'argv':[str(x) for x in argv], 'cwd':str(out), 'consumer':str(consumer.resolve()), 'artifacts':{str(p):digest(p) for p in sorted(paths)}}
        pc=(consumers/'python/consumer.py').resolve(); tr=(tc/'reference.mjs').resolve()
        entries={
            'rust':engine([rust],rust,[]),
            'python':engine([py,'-I','-B',pc,'--all-reference'],pc,inventory(site/'statelessness')+inventory(wheels)),
            'go':engine([gobin,'--all-reference'],gobin,inventory(stage/'go')+inventory(gc)),
            'typescript':engine([node,tr,'--all-reference'],tr,inventory(tc/'node_modules/@jagalite/statelessness')+inventory(packs)),
            'swift':engine([swift],swift,inventory(stage/'swift')+inventory(sc)),
        }
        command_file=out/'commands.json'; command_file.write_text(json.dumps({'schema':'n06-installed-commands/1','engines':entries},sort_keys=True,indent=2)+'\n')
        run('compare',[sys.executable,src/'scripts/qualify-n06-matrix.py','--commands',command_file,'--commands-sha256',digest(command_file),'--output',out/'comparison'],out,env)
        verify(snap)
        receipt['commands_sha256']=digest(command_file); receipt['status']='passed_49_case_comparison'
    except Exception as e:
        receipt['error']=type(e).__name__+': '+str(e)
        (out/'error.txt').write_text(traceback.format_exc())
        try: verify(snap); receipt['snapshot_after']='verified'
        except Exception as ve: receipt['snapshot_after']='failed: '+str(ve)
    else: receipt['snapshot_after']='verified'
    (out/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps(receipt,sort_keys=True))
    return 0 if receipt['status']=='passed_49_case_comparison' else 1

if __name__=='__main__': sys.exit(main())
