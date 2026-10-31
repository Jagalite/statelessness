#!/usr/bin/env python3
"""Run through Kyoto. Freeze packages, install clean consumers, retain every attempt.

Finite lane evidence only. This intentionally does not invoke the integration
qualifier or write into its excluded receipt family.
"""
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path):
    if path.is_file(): return {path.name:digest(path)}
    return {str(p.relative_to(path)):digest(p) for p in sorted(path.rglob('*')) if p.is_file() and not any(x in p.parts for x in ('.build','.swiftpm','node_modules','__pycache__','dist','build','.git'))}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--compiler',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve()
    if out.exists(): raise RuntimeError('preserve previous attempt: output must not exist')
    if out.is_relative_to(ROOT): raise RuntimeError('receipts must stay outside tracked source')
    out.mkdir(parents=True);work=out/'work';work.mkdir();logs=out/'logs';logs.mkdir()
    report={'status':'running','platform':platform.platform(),'scope':'finite native-modeling lane; current host only','commands':[],'sources':{},'artifacts':{}}
    env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',GOPROXY='off',GOTOOLCHAIN='local',GOWORK='off',CGO_ENABLED='0')
    def save(): (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    def run(name,argv,cwd=work):
        argv=list(map(str,argv));print(name,flush=True);start=time.monotonic()
        # macOS time retains peak RSS and CPU separately from wall duration.
        timed=['/usr/bin/time','-l']+argv if sys.platform=='darwin' else argv
        identities={str(p.resolve()):digest(p.resolve()) for arg in argv for p in [Path(shutil.which(arg) or arg)] if p.is_file()}
        try: result=subprocess.run(timed,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=1200)
        except subprocess.TimeoutExpired as error:
            (logs/(name+'.log')).write_bytes(error.stdout or b'')
            report['commands'].append({'name':name,'argv':argv,'exit_code':None,'timeout':True,'runner_before':identities});save();return False
        after={path:digest(Path(path)) for path in identities}
        if after!=identities: raise RuntimeError('executed tool or consumer changed: '+name)
        log=logs/(name+'.log');log.write_bytes(result.stdout)
        report['commands'].append({'name':name,'argv':argv,'cwd':str(cwd),'exit_code':result.returncode,'wall_seconds':time.monotonic()-start,'log_sha256':digest(log),'runner_before':identities,'runner_after':after})
        save()
        if result.returncode: print(result.stdout.decode(errors='replace')[-5000:],flush=True)
        return result.returncode==0
    def stage(src,dst): shutil.copytree(src,dst,ignore=shutil.ignore_patterns('.build','.swiftpm','node_modules','dist','__pycache__','*.egg-info','build','.git'))
    # Source identity includes all added lane files, examples and harness; no pyc reuse.
    source_names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    report['sources']={n:digest(ROOT/n) for n in sorted(set(source_names)) if n and (ROOT/n).is_file()}
    report['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    try:
        for name,cmd in [('python-version',[sys.executable,'--version']),('go-version',['go','version']),('node-version',['node','--version']),('swift-version',['swift','--version'])]: run(name,cmd)
        stage(args.compiler,work/'compiler');report['compiler']=tree(work/'compiler')
        if json.loads((work/'compiler/package.json').read_text())['version']!='5.8.3':raise RuntimeError('TypeScript compiler must be 5.8.3')
        tsc=['node',work/'compiler/bin/tsc']
        for lang in ('python','go','typescript'):stage(ROOT/lang,work/(lang+'-package'))
        (work/'swift-package').mkdir();shutil.copy2(ROOT/'Package.swift',work/'swift-package/Package.swift');stage(ROOT/'swift',work/'swift-package/swift')
        stage(ROOT/'conformance',work/'conformance');stage(ROOT/'conformance',work/'swift-package/conformance')
        report['corpus_before']=tree(work/'conformance')
        stage(ROOT/'examples/native-modeling',work/'examples')
        report['staged_before']={lang:tree(work/(lang+'-package')) for lang in ('python','go','typescript','swift')};save()
        # Independent suites exercise existing canonical codec, inspection and research controls too.
        suite_env=env.copy();env['PYTHONPATH']=str(work/'python-package/src');run('python-suite',[sys.executable,'-B','-m','unittest','discover','-s','tests'],work/'python-package');env.clear();env.update(suite_env)
        run('go-suite',['go','test','./...'],work/'go-package');run('go-vet',['go','vet','./...'],work/'go-package')
        env['CGO_ENABLED']='1';run('go-race',['go','test','-race','./...'],work/'go-package');env['CGO_ENABLED']='0'
        if run('typescript-build',tsc+['-p','tsconfig.json'],work/'typescript-package'):
            run('typescript-suite',['node','--test']+sorted((work/'typescript-package/test').glob('*.test.mjs')),work/'typescript-package')
        run('swift-suite',['swift','test','--package-path',work/'swift-package','-j','2'])
        run('venv',[sys.executable,'-m','venv',work/'venv']);py=work/'venv/bin/python'
        run('python-build-tools',[py,'-m','pip','install','build'])
        run('python-sdist',[py,'-m','build','--sdist','--outdir',out/'packages',work/'python-package'])
        run('python-wheel',[py,'-m','pip','wheel','--no-deps','--wheel-dir',out/'packages',work/'python-package'])
        wheels=list((out/'packages').glob('*.whl'))
        if wheels:
            run('python-install',[py,'-m','pip','install','--no-index','--no-deps',wheels[0]])
            run('python-consumer',[py,'-I','-B',work/'examples/python/consumer.py'])
        archives=list((out/'packages').glob('*.tar.gz'))
        if archives:
            run('sdist-venv',[sys.executable,'-m','venv',work/'sdist-venv']);sdpy=work/'sdist-venv/bin/python'
            run('sdist-install',[sdpy,'-m','pip','install','--no-deps',archives[0]])
            run('sdist-consumer',[sdpy,'-I','-B',work/'examples/python/consumer.py'])
        stage(work/'examples/go',work/'go-consumer');(work/'go-consumer/go.mod').write_text('module modeling-consumer\n\ngo 1.23.0\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
        if run('go-consumer-build',['go','build','-trimpath','-buildvcs=false','-o',work/'go-consumer-bin','.'],work/'go-consumer'):run('go-consumer',[work/'go-consumer-bin'])
        run('typescript-pack',['npm','pack','--ignore-scripts','--pack-destination',out/'packages'],work/'typescript-package')
        stage(work/'examples/typescript',work/'typescript-consumer');ts=work/'typescript-consumer';(ts/'package.json').write_text('{"private":true,"type":"module"}');(ts/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'target':'ES2022','module':'NodeNext','moduleResolution':'NodeNext','outDir':'build'},'files':['consumer.ts']}))
        tarballs=list((out/'packages').glob('*.tgz'))
        if tarballs:
            run('typescript-install',['npm','install','--offline','--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--no-save',tarballs[0]],ts)
            if run('typescript-consumer-build',tsc+['-p','tsconfig.json'],ts):run('typescript-consumer',['node',ts/'build/consumer.js'])
        stage(work/'examples/swift',work/'swift-consumer')
        if run('swift-consumer-build',['swift','build','--package-path',work/'swift-consumer','-j','2']):
            binary=next((work/'swift-consumer/.build').glob('*/debug/ModelingConsumer'));run('swift-consumer',[binary]);report['artifacts']['swift-executable']=digest(binary)
        for name,path in [('go-executable',work/'go-consumer-bin'),('typescript-installed',ts/'node_modules'),('python-installed',work/'venv/lib'),('sdist-installed',work/'sdist-venv/lib')]:
            if path.exists():report['artifacts'][name]=tree(path)
        report['artifacts']['packages']=tree(out/'packages');report['artifacts']['typescript-executable']=tree(ts/'build')
        report['staged_after']={lang:tree(work/(lang+'-package')) for lang in ('python','go','typescript','swift')}
        # Build-generated package metadata is an artifact, not a source change.
        for phase in ('staged_before','staged_after'):
            report[phase]['python']={k:v for k,v in report[phase]['python'].items() if '.egg-info/' not in k}
        current_names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
        report['source_unchanged']=report['sources']=={n:digest(ROOT/n) for n in sorted(set(current_names)) if n and (ROOT/n).is_file()}
        report['staged_unchanged']=report['staged_before']==report['staged_after'];report['compiler_unchanged']=report['compiler']==tree(work/'compiler');report['corpus_unchanged']=report['corpus_before']==tree(work/'conformance')
        report['status']='passed' if all(c['exit_code']==0 for c in report['commands']) and report['source_unchanged'] and report['staged_unchanged'] and report['compiler_unchanged'] and report['corpus_unchanged'] else 'failed'
    except BaseException as error:
        report['status']='failed';report['error']=repr(error);raise
    finally:save()
    print(report['status'],out,flush=True)
    if report['status']!='passed':sys.exit(1)
if __name__=='__main__':main()
