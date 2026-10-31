#!/usr/bin/env python3
"""Bounded installed N05 comparison; not a replacement for R3/final acceptance.

Invoke through Kyoto on a frozen source snapshot. Cargo build is a separate job;
this runner records and executes the existing source-bound Rust executable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate JSON key: '+key)
            result[key]=value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def exact(a,b):
    if type(a) is not type(b): return False
    if isinstance(a,dict): return a.keys()==b.keys() and all(exact(a[k],b[k]) for k in a)
    if isinstance(a,list): return len(a)==len(b) and all(exact(x,y) for x,y in zip(a,b))
    return a==b


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--source',type=Path,required=True)
    a.add_argument('--output',type=Path,required=True)
    a.add_argument('--manifest',type=Path,required=True)
    a.add_argument('--manifest-sha256',required=True)
    a.add_argument('--rust-binary',type=Path)
    a.add_argument('--rust-manifest',type=Path)
    a.add_argument('--rust-binary-sha256',required=True)
    a.add_argument('--rust-build-receipt',type=Path,required=True)
    args=a.parse_args(); source=args.source.resolve(); out=args.output.resolve(); out.mkdir(exist_ok=False)
    if sha(args.manifest)!=args.manifest_sha256: raise RuntimeError('manifest changed')
    manifest=json.loads(args.manifest.read_text())
    def verify():
        generated={'__pycache__','.build','target','node_modules','dist'}
        current={str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() and not set(p.relative_to(source).parts)&generated}
        if current!=set(manifest): raise RuntimeError('source membership changed')
        for name, digest in manifest.items():
            if sha(source/name)!=digest: raise RuntimeError('source changed: '+name)
    verify()
    report={'source_manifest_sha256':args.manifest_sha256,'commands':[],'comparisons':{},'scope':'installed guided 90-case slice; no full parity claim'}
    env=dict(os.environ,CGO_ENABLED='0');env.pop('PYTHONPATH',None)
    tools={k:shutil.which(k) for k in ['python3','go','npm','node','swift']}
    report['tools']={k:{'path':v,'sha256':sha(v)} for k,v in tools.items()}
    def save(): (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    def run(name,cmd,cwd=out):
        cmd=[str(v) for v in cmd];start=time.monotonic()
        with (out/(name+'.stdout')).open('wb') as stdout, (out/(name+'.stderr')).open('wb') as stderr:
            process=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            try: code=process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait();code='timeout'
        report['commands'].append({'name':name,'command':cmd,'exit_code':code,'seconds':time.monotonic()-start,
            'stdout_sha256':sha(out/(name+'.stdout')),'stderr_sha256':sha(out/(name+'.stderr'))});save()
        print(json.dumps(report['commands'][-1]),flush=True)
        if code: raise RuntimeError(name+' failed')
        return (out/(name+'.stdout')).read_text()
    def copy(src,dest):
        shutil.copytree(src,dest,ignore=shutil.ignore_patterns('__pycache__','target','node_modules','.build','dist','receipts'))
    packages=out/'packages';packages.mkdir()
    consumers=source/'scripts/native-guided-consumers'; runners={}
    try:
        copy(source/'python',out/'python-build')
        run('builder',[tools['python3'],'-m','venv',out/'builder'])
        builder=out/'builder/bin/python'
        run('builder-tools',[builder,'-I','-m','pip','install','setuptools>=77','wheel'])
        run('wheel',[builder,'-I','-m','pip','wheel','--no-build-isolation','--no-deps','--wheel-dir',packages,out/'python-build'])
        run('venv',[tools['python3'],'-m','venv',out/'venv']);py=out/'venv/bin/python'
        wheel=next(packages.glob('*.whl'));run('wheel-install',[py,'-I','-m','pip','install','--no-deps',wheel])
        copy(consumers/'python',out/'python-consumer')
        runners['python']=[py,'-I','-B',out/'python-consumer/consumer.py']
        copy(source/'typescript',out/'typescript-build')
        run('npm-ci',[tools['npm'],'ci','--ignore-scripts','--no-audit','--no-fund'],out/'typescript-build')
        run('ts-build',[tools['npm'],'run','build'],out/'typescript-build')
        run('npm-pack',[tools['npm'],'pack','--ignore-scripts','--pack-destination',packages],out/'typescript-build')
        copy(consumers/'typescript',out/'typescript-consumer');tar=next(packages.glob('*.tgz'))
        run('npm-install',[tools['npm'],'install','--ignore-scripts','--no-audit','--no-fund',tar],out/'typescript-consumer')
        runners['typescript']=[tools['node'],out/'typescript-consumer/run.mjs']
        copy(source/'go',out/'go-package');copy(consumers/'go',out/'go-consumer')
        run('go-build',[tools['go'],'build','-o',out/'guided-go','.'],out/'go-consumer')
        runners['go']=[out/'guided-go']
        native=out/'swift-package';native.mkdir();shutil.copy2(source/'Package.swift',native/'Package.swift');copy(source/'swift',native/'swift')
        copy(consumers/'swift',out/'swift-consumer');env['STATELESS_SWIFT_PACKAGE']=str(native)
        run('swift-build',[tools['swift'],'build','-j','2'],out/'swift-consumer')
        bindir=run('swift-bin',[tools['swift'],'build','--show-bin-path'],out/'swift-consumer').strip()
        runners['swift']=[Path(bindir)/'GuidedConsumer']
        if args.rust_binary:
            if args.rust_manifest is None: raise RuntimeError('Rust source manifest required')
            rust_manifest=json.loads(args.rust_manifest.read_text())
            for name, value in manifest.items():
                if name.startswith(('src/','crates/','scripts/native-guided-consumers/rust/')) or name in ('Cargo.toml','Cargo.lock','build.rs'):
                    if rust_manifest.get(name)!=value: raise RuntimeError('Rust source differs: '+name)
            report['rust_source_manifest_sha256']=sha(args.rust_manifest)
        runners['rust']=[args.rust_binary or source/'scripts/native-guided-consumers/rust/target/debug/native-guided-rust']
        report['package_sha256']={p.name:sha(p) for p in packages.iterdir()}
        report['executable_sha256']={k:sha(v[0]) for k,v in runners.items()}
        if report['executable_sha256']['rust']!=args.rust_binary_sha256: raise RuntimeError('Rust executable changed')
        report['rust_build_receipt_sha256']=sha(args.rust_build_receipt)
        report['rust_build_receipt']=strict_json(args.rust_build_receipt.read_text())
        if report['rust_build_receipt']['status']!='succeeded': raise RuntimeError('Rust build did not succeed')
        corpus=source/'conformance/guided-v2/corpus.json';data=strict_json(corpus.read_text())
        report['corpus_sha256']=sha(corpus);report['freeze_sha256']=sha(corpus.with_name('FREEZE.json'))
        if report['corpus_sha256']!='12c762786c729e6e2030a2cb279184f23767695e15bc85afdb0ea74fecc21b35' or report['freeze_sha256']!='8a7f6bdc8e8ac335ec71ed6f6483c9ad470a1e72689d85f56961f757180e8547': raise RuntimeError('unreviewed corpus/seal')
        expected={c['id']:{k:v for k,v in c['expected'].items() if k!='rng_audit'} for c in data['cases']}
        if len(data['cases'])!=90 or len(expected)!=90: raise RuntimeError('expected exactly 90 distinct cases')
        for engine, runner in runners.items():
            raw=run('guided-'+engine,[*runner,'--corpus',corpus]);actual=strict_json(raw)
            if actual.get('schema')!='stateless-guided-v2-results/1' or actual.get('engine')!=engine: raise RuntimeError('result envelope')
            rows=actual['cases']; ids=[r['id'] for r in rows]
            if ids != list(expected):raise RuntimeError(engine+' case membership/order mismatch')
            failures=[r['id'] for r in rows if not exact(r['actual'],expected[r['id']])]
            report['comparisons'][engine]={'cases':len(rows),'failures':failures}
            save()
        report['resource_policy']='Three fresh-process corpus repetitions after one comparison run; includes startup/JIT/GC, no throughput/capacity or budget qualification.'
        for engine, runner in runners.items():
            if report['comparisons'][engine]['failures']: continue
            for index in range(3):
                raw=run(f'resource-{engine}-{index}', ['/usr/bin/time','-l',*runner,'--corpus',corpus])
                repeated=strict_json(raw)
                if not exact(repeated, strict_json((out/('guided-'+engine+'.stdout')).read_text())):
                    raise RuntimeError(engine+' nondeterministic resource replay')
        # Installed Python campaign reference exercises the independent 49-row inventory too.
        run('campaign-python',[py,'-I','-B',source/'examples/native-campaign/python/consumer.py','--all-reference'])
        report['campaign_python']='smoke only; raw 49 rows retained, no N06 semantic comparison'
        if sha(runners['rust'][0])!=args.rust_binary_sha256: raise RuntimeError('Rust executable drift')
        for name,tool in report['tools'].items():
            if sha(tool['path'])!=tool['sha256']: raise RuntimeError('tool changed: '+name)
        verify();report['source_unchanged']=True
        report['status']='passed' if all(not r['failures'] for r in report['comparisons'].values()) else 'failed-comparison'
    except Exception as e:
        report['status']='failed';report['error']=repr(e)
    finally:save()
    return 0 if report['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
