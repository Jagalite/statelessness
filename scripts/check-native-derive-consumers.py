#!/usr/bin/env python3
"""Finite installed N13 record/enum interchange. Execute only through Kyoto.

Three concrete fixtures, four native packages, fresh producer/consumer processes.
This is candidate macOS arm64 evidence, not the 90-class corpus or broad parity.
"""
from __future__ import annotations
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXPECTED=[{'id':'point','hex':'07000000020000006869'},{'id':'empty','hex':'00'},{'id':'variant','hex':'0107000000020000006869'}]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
 if out.exists() and any(out.iterdir()):raise RuntimeError('output must be new or empty; preserve prior receipts')
 out.mkdir(parents=True,exist_ok=True);logs=out/'logs';logs.mkdir();packages=out/'packages';packages.mkdir();values=out/'values';values.mkdir()
 work=Path(tempfile.mkdtemp(prefix='stateless-native-derive-')).resolve();env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',GOPROXY='off',GOTOOLCHAIN='local',GOWORK='off',CGO_ENABLED='0')
 report=dict(status='running',platform=platform.platform(),machine=platform.machine(),workspace=str(work),scope='3 concrete ordered-record/tagged-enum fixtures; four native installed packages; no ModelCodec binder claim; no broad parity/profile promotion',commands=[],pairs=[],producers={})
 def save():
  report['artifacts']={str(f.relative_to(out)):{'sha256':sha(f),'size_bytes':f.stat().st_size}for f in out.rglob('*')if f.is_file()and f.name!='qualification.json'}
  (out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
 def run(name,argv,cwd=work):
  argv=list(map(str,argv));print(f'[{name}] '+ ' '.join(argv),flush=True)
  try:r=subprocess.run(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900)
  except subprocess.TimeoutExpired as exc:
   log=logs/(name+'.log');log.write_bytes(exc.stdout or b'');report['commands'].append(dict(name=name,argv=argv,cwd=str(cwd),exit_code=None,timed_out=True,log_sha256=sha(log),log_size_bytes=log.stat().st_size));save();raise
  log=logs/(name+'.log');log.write_bytes(r.stdout);report['commands'].append(dict(name=name,argv=argv,cwd=str(cwd),exit_code=r.returncode,log_sha256=sha(log),log_size_bytes=log.stat().st_size));save()
  if r.returncode:raise RuntimeError(f'{name} failed ({r.returncode}): '+r.stdout.decode(errors='replace')[-4000:])
  return r.stdout.decode().strip()
 def stage(src,dst):shutil.copytree(src,dst,ignore=shutil.ignore_patterns('.build','.swiftpm','node_modules','dist','__pycache__','*.egg-info','build','*.tgz'))
 try:
  report['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
  files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
  report['source_files']={s:{'sha256':sha(ROOT/s),'size_bytes':(ROOT/s).stat().st_size}for s in files if s and (ROOT/s).is_file()and not s.startswith(('validation/','target/'))};save()
  stage(ROOT/'python',work/'python-package');run('python-builder-venv',[sys.executable,'-m','venv',work/'builder']);builder=work/'builder/bin/python'
  run('python-builder-tools',[builder,'-m','pip','install','setuptools>=77','wheel']);run('python-wheel',[builder,'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',packages,work/'python-package'])
  run('python-venv',[sys.executable,'-m','venv',work/'venv']);py=work/'venv/bin/python';wheel,=packages.glob('*.whl');run('python-install',[py,'-m','pip','install','--no-index','--no-deps',wheel]);shutil.copy2(ROOT/'examples/native-derive/python/consumer.py',work/'python-consumer.py')
  runners={'python':[str(py),'-I',str(work/'python-consumer.py')]}
  stage(ROOT/'go',work/'go-package');stage(ROOT/'examples/native-derive/go',work/'go-consumer');(work/'go-consumer/go.mod').write_text('module derive-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
  run('go-build',['go','build','-trimpath','-o',work/'go-consumer-bin','.'],work/'go-consumer');runners['go']=[str(work/'go-consumer-bin')]
  stage(ROOT/'typescript',work/'typescript-package');compiler=['node',str(ROOT/'typescript/node_modules/typescript/bin/tsc')]if(ROOT/'typescript/node_modules/typescript/bin/tsc').exists()else['tsc']
  run('typescript-package-build',compiler+['-p','tsconfig.json'],work/'typescript-package');run('typescript-pack',['npm','pack','--ignore-scripts','--pack-destination',packages],work/'typescript-package');tarball,=packages.glob('*.tgz')
  stage(ROOT/'examples/native-derive/typescript',work/'typescript-consumer');ts=work/'typescript-consumer';(ts/'package.json').write_text('{"name":"derive-consumer","private":true,"type":"module"}\n')
  run('typescript-install',['npm','install','--offline','--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--omit=dev',tarball],ts)
  (ts/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'target':'ES2022','module':'NodeNext','moduleResolution':'NodeNext','outDir':'build','lib':['ES2022','DOM']},'files':['consumer.ts']}));run('typescript-declarations',compiler+['-p','tsconfig.json'],ts);runners['typescript']=['node',str(ts/'build/consumer.js')]
  (work/'swift-package').mkdir();shutil.copy2(ROOT/'Package.swift',work/'swift-package/Package.swift');stage(ROOT/'swift',work/'swift-package/swift');stage(ROOT/'examples/native-derive/swift',work/'swift-consumer')
  run('swift-build',['swift','build','--package-path',work/'swift-consumer','-j','2']);bindir=run('swift-bin-path',['swift','build','--package-path',work/'swift-consumer','--show-bin-path']);runners['swift']=[str(Path(bindir)/'DeriveConsumer')]
  report['staged_source_files']={str(f.relative_to(work)):{'sha256':sha(f),'size_bytes':f.stat().st_size}for d in ('python-package','go-package','typescript-package','swift-package','go-consumer','typescript-consumer','swift-consumer')for f in (work/d).rglob('*')if f.is_file()and not any(x in f.parts for x in ('.build','node_modules','dist','__pycache__','build'))};report['staged_source_files']['python-consumer.py']={'sha256':sha(work/'python-consumer.py'),'size_bytes':(work/'python-consumer.py').stat().st_size};report['runners']=runners
  for name,runner in runners.items():
   dest=values/(name+'.json');receipt=json.loads(run(name+'-produce',runner+['produce',str(dest)]));assert json.loads(dest.read_text())==EXPECTED,name;assert receipt['case_count']==3,name;report['producers'][name]=receipt;save()
  for producer in runners:
   for consumer,runner in runners.items():
    dest=values/(producer+'-to-'+consumer+'.json');receipt=json.loads(run(producer+'-to-'+consumer,runner+['transcode',str(values/(producer+'.json')),str(dest)]));assert json.loads(dest.read_text())==EXPECTED,(producer,consumer);assert receipt['case_count']==3;report['pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt));save()
  report['source_files_after']={s:{'sha256':sha(ROOT/s),'size_bytes':(ROOT/s).stat().st_size}for s in report['source_files'] if (ROOT/s).is_file()};report['source_snapshot_unchanged']=report['source_files_after']==report['source_files'];
  if not report['source_snapshot_unchanged']:raise RuntimeError('source snapshot changed during qualification; preserve and requalify')
  report['status']='passed';save();print(f'Finite installed N13 passed: 4 producers, 16 fresh-process pairs; {out}',flush=True)
 except BaseException as e:report['status']='failed';report['error']=str(e);save();raise
if __name__=='__main__':main()
