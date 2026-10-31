#!/usr/bin/env python3
"""Finite installed five-engine N13 binder/Recorder/causal-evidence application.

Execute only through Kyoto, after source freeze. Each process is independent.
This finite macOS arm64 scenario does not close the 90-row corpus or broad144.
"""
from __future__ import annotations
import argparse, hashlib, json, os, platform, re, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import importlib.util
_spec=importlib.util.spec_from_file_location('n13_expected',ROOT/'examples/native-model/expected.py')
_oracle=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_oracle)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rust_engine_build_identity(output):
 # The proc-macro crate emits its own STATELESS_BUILD_ID. Select the engine's
 # package-prefixed build-script line, preserving the full log for both crates.
 ids=set(re.findall(r'^\[statelessness [^\]\r\n]+\] cargo:rustc-env=STATELESS_BUILD_ID=(source-fnv1a64:[0-9a-f]{16})\s*$',output,re.MULTILINE))
 if len(ids)!=1:raise RuntimeError('expected exactly one retained statelessness engine build identity')
 return ids.pop()
def validate_receipt(engine, mode, receipt):
 if not isinstance(receipt,dict) or receipt.get('engine',receipt.get('producer'))!=engine:
  raise AssertionError(f'{engine}: missing or incorrect receipt engine')
 if receipt.get('capture_failure_controls') is not True:
  raise AssertionError(f'{engine}: missing executed capture failure controls')
 def counts(actual,expected,label):
  if (not isinstance(actual,dict) or set(actual)!=set(expected)
      or any(type(value) is not int for value in actual.values()) or actual!=expected):
   raise AssertionError(f'{engine}: {label} measured callback counts differ: {actual!r}')
 if mode=='produce':counts(receipt.get('capture_counts'),_oracle.CAPTURE_COUNTS,'capture')
 elif receipt.get('capture_counts') is not None:
  raise AssertionError(f'{engine}: transcode unexpectedly reports capture counts')
 replays=receipt.get('replay_counts')
 if not isinstance(replays,dict):raise AssertionError(f'{engine}: missing replay count object')
 if mode=='transcode' and not {'v1','v2'}<=set(replays):
  raise AssertionError(f'{engine}: missing v1/v2 measured replay counts')
 for version,actual in replays.items():
  if version not in ('v1','v2','v2_causal'):raise AssertionError(f'{engine}: unknown replay {version}')
  counts(actual,_oracle.REPLAY_COUNTS,version+' replay')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
 if out.exists() and any(out.iterdir()):raise RuntimeError('output must be new or empty; preserve prior receipts')
 out.mkdir(parents=True,exist_ok=True);logs=out/'logs';logs.mkdir();packages=out/'packages';packages.mkdir();values=out/'values';values.mkdir()
 work=Path(tempfile.mkdtemp(prefix='stateless-native-model-')).resolve();env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',GOPROXY='off',GOTOOLCHAIN='local',GOWORK='off',CGO_ENABLED='0')
 report=dict(status='running',platform=platform.platform(),machine=platform.machine(),workspace=str(work),scope='one actual State/Input binder application, custom encode-only Output, dual checked Recorder/EvidenceRecorder; 5 installed producers, 25 ordered fresh-process read/replay/rewrite pairs; finite macOS arm64 only, no broad144/profile promotion',commands=[],pairs=[],producers={})
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
  def snapshot():
   names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
   return {s:{'sha256':sha(ROOT/s),'size_bytes':(ROOT/s).stat().st_size}for s in sorted(set(names)) if s and (ROOT/s).is_file() and not s.startswith(('validation/native-parity/derive-model-v2/','target/'))}
  report['source_files']=snapshot();save()
  stage(ROOT/'python',work/'python-package');run('python-builder-venv',[sys.executable,'-m','venv',work/'builder']);builder=work/'builder/bin/python'
  run('python-builder-tools',[builder,'-m','pip','install','setuptools>=77','wheel']);run('python-wheel',[builder,'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',packages,work/'python-package'])
  run('python-venv',[sys.executable,'-m','venv',work/'venv']);py=work/'venv/bin/python';wheel,=packages.glob('*.whl');run('python-install',[py,'-m','pip','install','--no-index','--no-deps',wheel]);shutil.copy2(ROOT/'examples/native-model/python/consumer.py',work/'python-consumer.py')
  runners={'python':[str(py),'-I',str(work/'python-consumer.py')]}
  stage(ROOT/'go',work/'go-package');stage(ROOT/'examples/native-model/go',work/'go-consumer');(work/'go-consumer/go.mod').write_text('module model-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
  run('go-build',['go','build','-trimpath','-o',work/'go-consumer-bin','.'],work/'go-consumer');runners['go']=[str(work/'go-consumer-bin')]
  stage(ROOT/'typescript',work/'typescript-package');compiler=['node',str(ROOT/'typescript/node_modules/typescript/bin/tsc')]if(ROOT/'typescript/node_modules/typescript/bin/tsc').exists()else['tsc']
  run('typescript-package-build',compiler+['-p','tsconfig.json'],work/'typescript-package');run('typescript-pack',['npm','pack','--ignore-scripts','--pack-destination',packages],work/'typescript-package');tarball,=packages.glob('*.tgz')
  stage(ROOT/'examples/native-model/typescript',work/'typescript-consumer');ts=work/'typescript-consumer';(ts/'package.json').write_text('{"name":"model-consumer","private":true,"type":"module"}\n')
  run('typescript-install',['npm','install','--offline','--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--omit=dev',tarball],ts)
  (ts/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'target':'ES2022','module':'NodeNext','moduleResolution':'NodeNext','outDir':'build','lib':['ES2022','DOM']},'files':['consumer.ts']}));run('typescript-declarations',compiler+['-p','tsconfig.json'],ts);runners['typescript']=['node',str(ts/'build/consumer.js')]
  (work/'swift-package').mkdir();shutil.copy2(ROOT/'Package.swift',work/'swift-package/Package.swift');stage(ROOT/'swift',work/'swift-package/swift');stage(ROOT/'examples/native-model/swift',work/'swift-consumer')
  run('swift-build',['swift','build','--package-path',work/'swift-consumer','-j','2']);bindir=run('swift-bin-path',['swift','build','--package-path',work/'swift-consumer','--show-bin-path']);runners['swift']=[str(Path(bindir)/'ModelConsumer')]
  stage(ROOT/'src',work/'rust-source/src');stage(ROOT/'crates',work/'rust-source/crates');stage(ROOT/'docs/requirements',work/'rust-source/docs/requirements')
  # Preserve declared Cargo targets even when this install selects one binary.
  for directory in ('benches','tests','examples'):
   stage(ROOT/directory,work/'rust-source'/directory)
  for f in ('Cargo.toml','Cargo.lock','build.rs','README.md','LICENSE'):shutil.copy2(ROOT/f,work/'rust-source'/f)
  rustc_version=run('rustc-version',['rustc','--version'])+'\n'
  rustc_verbose=run('rustc-host',['rustc','-vV']);target=next(line.split(': ',1)[1]for line in rustc_verbose.splitlines()if line.startswith('host: '))
  # Pin install flags so the independent build identity has explicit inputs.
  env['CARGO_ENCODED_RUSTFLAGS']='';env['RUSTFLAGS']=''
  env['CARGO_TARGET_DIR']=str(work/'rust-target');env['CARGO_INCREMENTAL']='0'
  independently_expected_build=_oracle.rust_build_id(work/'rust-source',rustc_version.encode(),target,flags='')
  rust_build_output=run('rust-install',['cargo','install','-vv','--path',work/'rust-source/crates/macro-qualification','--bin','n13-model-interchange','--root',work/'rust-install','--locked','--offline'],work/'rust-source')
  rust_build=rust_engine_build_identity(rust_build_output)
  if rust_build!=independently_expected_build:raise RuntimeError('compiler build identity differs from independent source/compiler-input hash')
  report['rust_build_identity']=rust_build;report['rust_build_inputs']={'rustc_version':rustc_version,'target':target,'profile':'release','opt_level':'3','debug':'false','encoded_rustflags':''}
  runners['rust']=[str(work/'rust-install/bin/n13-model-interchange')]
  report['staged_source_files']={str(f.relative_to(work)):{'sha256':sha(f),'size_bytes':f.stat().st_size}for d in ('python-package','go-package','typescript-package','swift-package','go-consumer','typescript-consumer','swift-consumer','rust-source')for f in (work/d).rglob('*')if f.is_file()and not any(x in f.parts for x in ('.build','node_modules','dist','__pycache__','build','target'))};report['staged_source_files']['python-consumer.py']={'sha256':sha(work/'python-consumer.py'),'size_bytes':(work/'python-consumer.py').stat().st_size};report['runners']=runners
  def runner_snapshot():
   identities={}
   for name,argv in runners.items():
    files=[]
    for index,arg in enumerate(argv):
     path=Path(shutil.which(arg,path=env.get('PATH')) or arg) if index==0 else Path(arg)
     if index and not path.is_file():continue
     resolved=path.resolve(strict=True)
     files.append(dict(argument_index=index,path=str(path),resolved_path=str(resolved),sha256=sha(resolved),size_bytes=resolved.stat().st_size))
    identities[name]=files
   return identities
  report['runner_files_before']=runner_snapshot();save()
  for name,runner in runners.items():
   dest=values/(name+'.json');receipt=json.loads(run(name+'-produce',runner+['produce',str(dest)]).splitlines()[-1]);actual=json.loads(dest.read_text());expected=_oracle.expected(name,rust_build=rust_build)
   if actual!=expected:raise AssertionError(f'{name}: literal independent artifact oracle mismatch')
   validate_receipt(name,'produce',receipt)
   report['producers'][name]=receipt;save()
  for producer in runners:
   for consumer,runner in runners.items():
    dest=values/(producer+'-to-'+consumer+'.json');receipt=json.loads(run(producer+'-to-'+consumer,runner+['transcode',str(values/(producer+'.json')),str(dest)]).splitlines()[-1])
    if json.loads(dest.read_text())!=_oracle.expected(producer,rust_build=rust_build):raise AssertionError(f'{producer}->{consumer}: exact rewrite mismatch')
    validate_receipt(consumer,'transcode',receipt)
    report['pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt));save()
  report['runner_files_after']=runner_snapshot();report['runner_snapshot_unchanged']=report['runner_files_after']==report['runner_files_before']
  if not report['runner_snapshot_unchanged']:raise RuntimeError('executed runner identity changed during qualification')
  report['source_files_after']=snapshot();report['source_snapshot_unchanged']=report['source_files_after']==report['source_files']
  if not report['source_snapshot_unchanged']:raise RuntimeError('source additions/removals/hash drift during qualification; preserve and requalify')
  report['status']='passed';save();print(f'Finite installed N13 passed: 5 producers, 25 fresh-process pairs; {out}',flush=True)
 except BaseException as e:
  report['status']='failed';report['error']=str(e)
  if 'runner_files_before' in report:
   try:
    report['runner_files_after']=runner_snapshot();report['runner_snapshot_unchanged']=report['runner_files_after']==report['runner_files_before']
   except BaseException as identity_error:report['runner_identity_error']=repr(identity_error)
  try:
   report['source_files_after']=snapshot();report['source_snapshot_unchanged']=report['source_files_after']==report.get('source_files')
  except BaseException as snapshot_error:report['after_snapshot_error']=repr(snapshot_error)
  save();raise
if __name__=='__main__':main()
