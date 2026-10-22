#!/usr/bin/env python3
"""Finite installed five-engine N13 binder/Recorder/causal-evidence application.

Execute only through Kyoto, after source freeze. Each process is independent.
This finite macOS arm64 scenario does not close the 90-row corpus or broad144.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, platform, re, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import importlib.util
_provenance_spec=importlib.util.spec_from_file_location('native_consumer_provenance',Path(__file__).with_name('native_consumer_provenance.py'))
provenance=importlib.util.module_from_spec(_provenance_spec);_provenance_spec.loader.exec_module(provenance)
_spec=importlib.util.spec_from_file_location('n13_expected',ROOT/'examples/native-model/expected.py')
_oracle_inputs={}
provenance.freeze_input_tree(_oracle_inputs,'model-expected',ROOT/'examples/native-model/expected.py')
# Execute the bound source bytes directly, without consulting an unbound .pyc.
_oracle=importlib.util.module_from_spec(_spec);exec(compile(Path(_spec.origin).read_bytes(),_spec.origin,'exec'),_oracle.__dict__)
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
def runner_snapshot(runners, path_env=None):
 # Retain every available identity even if another file disappeared or cannot be
 # read. These fixed runner arguments contain only flags and application paths.
 identities={}
 for name,argv in runners.items():
  files=[]
  for index,arg in enumerate(argv):
   if index and arg.startswith('-'):continue
   item=dict(argument_index=index,path=str(arg))
   try:
    path=Path(shutil.which(arg,path=path_env) or arg) if index==0 else Path(arg)
    item['path']=str(path)
    resolved=path.resolve(strict=True);item['resolved_path']=str(resolved)
    item.update(sha256=sha(resolved),size_bytes=resolved.stat().st_size)
   except BaseException as error:item['identity_error']=repr(error)
   files.append(item)
  identities[name]=files
 return identities
def record_runner_snapshot(report, phase, path_env=None):
 if phase not in ('before','after'):raise ValueError('unknown runner identity phase')
 if phase=='before' and 'runner_files_before' in report:raise RuntimeError('runner baseline must not be replaced')
 before=report.get('runner_files_before')
 try:current=runner_snapshot(report['runners'],path_env)
 except BaseException as error:
  report.setdefault('runner_snapshot_failures',[]).append({'phase':phase,'snapshot_error':repr(error)})
  report['runner_snapshot_unchanged']=False
  raise
 report['runner_files_'+phase]=current
 def valid(value):
  return bool(value) and all(files and all('identity_error' not in item for item in files) for files in value.values())
 if not valid(current) or phase=='after' and (not valid(before) or before!=current):
  report.setdefault('runner_snapshot_failures',[]).append({'phase':phase,'files':copy.deepcopy(current)})
 report['runner_snapshot_unchanged']=not report.get('runner_snapshot_failures')
 return valid(current) and report['runner_snapshot_unchanged']
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--typescript-compiler',type=Path,required=True,help='Prepared packages/typescript/node_modules/typescript directory')
 p.add_argument('--typescript-compiler-manifest',type=Path,required=True,help='Preparation typescript-compiler-before.json; full tree must match')
 p.add_argument('--typescript-compiler-manifest-sha256',required=True,help='Literal pre-submit SHA-256 of the preparation compiler manifest')
 a=p.parse_args();out=a.output.resolve()
 if out.exists() and any(out.iterdir()):raise RuntimeError('output must be new or empty; preserve prior receipts')
 out.mkdir(parents=True,exist_ok=True);logs=out/'logs';logs.mkdir();packages=out/'packages';packages.mkdir();values=out/'values';values.mkdir()
 work=Path(tempfile.mkdtemp(prefix='stateless-native-model-')).resolve();env=dict(os.environ,PYTHONPATH='',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',GOPROXY='off',GOTOOLCHAIN='local',GOWORK='off',CGO_ENABLED='0')
 report=dict(status='running',platform=platform.platform(),machine=platform.machine(),workspace=str(work),scope='one actual State/Input binder application, custom encode-only Output, dual checked Recorder/EvidenceRecorder; 5 installed producers, 25 ordered fresh-process read/replay/rewrite pairs; finite macOS arm64 only, no broad144/profile promotion',commands=[],pairs=[],producers={})
 report.update(copy.deepcopy(_oracle_inputs))
 def save():
  artifacts={}
  for f in out.rglob('*'):
   if f.name=='qualification.json' or f.is_dir():continue
   if not f.is_file():
    artifacts[str(f.relative_to(out))]={'identity_error':'artifact is not a readable regular file'};continue
   try:artifacts[str(f.relative_to(out))]={'sha256':sha(f),'size_bytes':f.stat().st_size}
   except (OSError,RuntimeError) as error:artifacts[str(f.relative_to(out))]={'identity_error':repr(error)}
  report['artifacts']=artifacts
  (out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
 def run(name,argv,cwd=work,*,produced=None):
  provenance.check_input_trees(report)
  if name.startswith('typescript'):provenance.check_typescript(ROOT,report['typescript_compiler'])
  argv=list(map(str,argv));print(f'[{name}] '+ ' '.join(argv),flush=True)
  try:r=subprocess.run(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900)
  except subprocess.TimeoutExpired as exc:
   log=logs/(name+'.log');log.write_bytes(exc.stdout or b'');report['commands'].append(dict(name=name,argv=argv,cwd=str(cwd),exit_code=None,timed_out=True,log_sha256=sha(log),log_size_bytes=log.stat().st_size));save();raise
  log=logs/(name+'.log');log.write_bytes(r.stdout);report['commands'].append(dict(name=name,argv=argv,cwd=str(cwd),exit_code=r.returncode,log_sha256=sha(log),log_size_bytes=log.stat().st_size));save()
  # Bind build outputs before any later command or preparation can replace them.
  if r.returncode==0:
   for output_name,path in (produced or {}).items():freeze(output_name,path)
  provenance.check_input_trees(report)
  if name.startswith('typescript'):provenance.check_typescript(ROOT,report['typescript_compiler'])
  if r.returncode:raise RuntimeError(f'{name} failed ({r.returncode}): '+r.stdout.decode(errors='replace')[-4000:])
  return r.stdout.decode().strip()
 def stage(src,dst):shutil.copytree(src,dst,ignore=shutil.ignore_patterns('.build','.swiftpm','node_modules','dist','__pycache__','*.egg-info','build','*.tgz'))
 def freeze(name,path,**options):
  provenance.freeze_input_tree(report,name,path,**options);save()
 try:
  report['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
  def snapshot():
   names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
   return {s:{'sha256':sha(ROOT/s),'size_bytes':(ROOT/s).stat().st_size}for s in sorted(set(names)) if s and (ROOT/s).is_file() and not s.startswith(('validation/native-parity/derive-model-v2/','target/'))}
  report['source_files']=snapshot();save()
  report['typescript_compiler']={}
  compiler=provenance.select_typescript(ROOT,a.typescript_compiler,a.typescript_compiler_manifest,a.typescript_compiler_manifest_sha256,report['typescript_compiler']);save()
  report['typescript_version']=run('typescript-version',compiler+['--version'])
  provenance.require_typescript_version(report['typescript_version'],report['typescript_compiler'])
  stage(ROOT/'python',work/'python-package')
  freeze('python-package',work/'python-package',excluded=('build','src/statelessness.egg-info'))
  run('python-builder-venv',[sys.executable,'-B','-m','venv',work/'builder']);builder=work/'builder/bin/python'
  run('python-builder-tools',[builder,'-B','-m','pip','install','setuptools>=77','wheel']);run('python-wheel',[builder,'-B','-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',packages,work/'python-package'])
  wheel,=packages.glob('*.whl');freeze('python-wheel',wheel)
  run('python-venv',[sys.executable,'-B','-m','venv',work/'venv']);py=work/'venv/bin/python';run('python-install',[py,'-B','-m','pip','install','--no-index','--no-deps',wheel])
  provenance.freeze_installed_python(report,work/'venv');save()
  shutil.copy2(ROOT/'examples/native-model/python/consumer.py',work/'python-consumer.py');freeze('python-consumer',work/'python-consumer.py')
  runners={'python':[str(py),'-I','-B',str(work/'python-consumer.py')]}
  stage(ROOT/'go',work/'go-package');stage(ROOT/'examples/native-model/go',work/'go-consumer');(work/'go-consumer/go.mod').write_text('module model-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
  freeze('go-package',work/'go-package');freeze('go-consumer',work/'go-consumer')
  run('go-build',['go','build','-trimpath','-o',work/'go-consumer-bin','.'],work/'go-consumer',produced={'go-executable':work/'go-consumer-bin'});runners['go']=[str(work/'go-consumer-bin')]
  stage(ROOT/'typescript',work/'typescript-package')
  freeze('typescript-package',work/'typescript-package',excluded=('dist',))
  run('typescript-package-build',compiler+['-p','tsconfig.json'],work/'typescript-package')
  freeze('typescript-dist',work/'typescript-package/dist')
  run('typescript-pack',['npm','pack','--ignore-scripts','--pack-destination',packages],work/'typescript-package');tarball,=packages.glob('*.tgz');freeze('typescript-tarball',tarball)
  stage(ROOT/'examples/native-model/typescript',work/'typescript-consumer');ts=work/'typescript-consumer';(ts/'package.json').write_text('{"name":"model-consumer","private":true,"type":"module"}\n')
  (ts/'tsconfig.json').write_text(json.dumps({'compilerOptions':{'strict':True,'target':'ES2022','module':'NodeNext','moduleResolution':'NodeNext','outDir':'build','lib':['ES2022','DOM']},'files':['consumer.ts']}))
  freeze('typescript-consumer',ts,excluded=('node_modules','build'))
  # Keep the authored consumer manifest immutable across installation.
  run('typescript-install',['npm','install','--offline','--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--no-save','--omit=dev',tarball],ts)
  freeze('installed-typescript',ts/'node_modules')
  run('typescript-declarations',compiler+['-p','tsconfig.json'],ts);freeze('typescript-consumer-build',ts/'build');runners['typescript']=['node',str(ts/'build/consumer.js')]
  (work/'swift-package').mkdir();shutil.copy2(ROOT/'Package.swift',work/'swift-package/Package.swift');stage(ROOT/'swift',work/'swift-package/swift');stage(ROOT/'examples/native-model/swift',work/'swift-consumer')
  freeze('swift-package',work/'swift-package',excluded=('.build',));freeze('swift-consumer',work/'swift-consumer',excluded=('.build',))
  bindir=run('swift-bin-path',['swift','build','--package-path',work/'swift-consumer','--show-bin-path']);swift_bin=Path(bindir)/'ModelConsumer'
  run('swift-build',['swift','build','--package-path',work/'swift-consumer','-j','2'],produced={'swift-executable':swift_bin});runners['swift']=[str(swift_bin)]
  stage(ROOT/'src',work/'rust-source/src');stage(ROOT/'crates',work/'rust-source/crates');stage(ROOT/'docs/requirements',work/'rust-source/docs/requirements')
  # Preserve declared Cargo targets even when this install selects one binary.
  for directory in ('benches','tests','examples'):
   stage(ROOT/directory,work/'rust-source'/directory)
  for f in ('Cargo.toml','Cargo.lock','build.rs','README.md','LICENSE'):shutil.copy2(ROOT/f,work/'rust-source'/f)
  freeze('rust-source',work/'rust-source')
  rustc_version=run('rustc-version',['rustc','--version'])+'\n'
  rustc_verbose=run('rustc-host',['rustc','-vV']);target=next(line.split(': ',1)[1]for line in rustc_verbose.splitlines()if line.startswith('host: '))
  # Pin install flags so the independent build identity has explicit inputs.
  env['CARGO_ENCODED_RUSTFLAGS']='';env['RUSTFLAGS']=''
  env['CARGO_TARGET_DIR']=str(work/'rust-target');env['CARGO_INCREMENTAL']='0'
  independently_expected_build=_oracle.rust_build_id(work/'rust-source',rustc_version.encode(),target,flags='')
  rust_build_output=run('rust-install',['cargo','install','-vv','--path',work/'rust-source/crates/macro-qualification','--bin','n13-model-interchange','--root',work/'rust-install','--locked','--offline'],work/'rust-source',produced={'rust-executable':work/'rust-install/bin/n13-model-interchange'})
  rust_build=rust_engine_build_identity(rust_build_output)
  if rust_build!=independently_expected_build:raise RuntimeError('compiler build identity differs from independent source/compiler-input hash')
  report['rust_build_identity']=rust_build;report['rust_build_inputs']={'rustc_version':rustc_version,'target':target,'profile':'release','opt_level':'3','debug':'false','encoded_rustflags':''}
  runners['rust']=[str(work/'rust-install/bin/n13-model-interchange')]
  report['staged_source_files']={str(f.relative_to(work)):{'sha256':sha(f),'size_bytes':f.stat().st_size}for d in ('python-package','go-package','typescript-package','swift-package','go-consumer','typescript-consumer','swift-consumer','rust-source')for f in (work/d).rglob('*')if f.is_file()and not any(x in f.parts for x in ('.build','node_modules','dist','__pycache__','build','target'))};report['staged_source_files']['python-consumer.py']={'sha256':sha(work/'python-consumer.py'),'size_bytes':(work/'python-consumer.py').stat().st_size};report['runners']=runners
  identities_valid=record_runner_snapshot(report,'before',env.get('PATH'));save()
  if not identities_valid:raise RuntimeError('executed runner identity unavailable before execution')
  for name,runner in runners.items():
   dest=values/(name+'.json');receipt=json.loads(run(name+'-produce',runner+['produce',str(dest)]).splitlines()[-1]);actual=json.loads(dest.read_text());expected=_oracle.expected(name,rust_build=rust_build)
   if actual!=expected:raise AssertionError(f'{name}: literal independent artifact oracle mismatch')
   validate_receipt(name,'produce',receipt)
   report['producers'][name]=receipt;freeze('producer-'+name,dest)
  for producer in runners:
   for consumer,runner in runners.items():
    dest=values/(producer+'-to-'+consumer+'.json');receipt=json.loads(run(producer+'-to-'+consumer,runner+['transcode',str(values/(producer+'.json')),str(dest)]).splitlines()[-1])
    if json.loads(dest.read_text())!=_oracle.expected(producer,rust_build=rust_build):raise AssertionError(f'{producer}->{consumer}: exact rewrite mismatch')
    validate_receipt(consumer,'transcode',receipt)
    report['pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt));save()
  record_runner_snapshot(report,'after',env.get('PATH'))
  if not report['runner_snapshot_unchanged']:raise RuntimeError('executed runner identity changed during qualification')
  report['source_files_after']=snapshot();report['source_snapshot_unchanged']=report['source_files_after']==report['source_files']
  if not report['source_snapshot_unchanged']:raise RuntimeError('source additions/removals/hash drift during qualification; preserve and requalify')
  provenance.check_typescript(ROOT,report['typescript_compiler'])
  provenance.check_input_trees(report)
  report['status']='passed';save();print(f'Finite installed N13 passed: 5 producers, 25 fresh-process pairs; {out}',flush=True)
 except BaseException as e:
  report['status']='failed';report['error']=str(e)
  try:provenance.check_input_trees(report)
  except BaseException as input_error:report['input_tree_error']=repr(input_error)
  if 'before' in report.get('typescript_compiler',{}):
   try:provenance.check_typescript(ROOT,report['typescript_compiler'])
   except BaseException as compiler_error:report['typescript_compiler_error']=repr(compiler_error)
  if 'runner_files_before' in report:
   try:
    record_runner_snapshot(report,'after',env.get('PATH'))
   except BaseException as identity_error:report['runner_identity_error']=repr(identity_error)
  try:
   report['source_files_after']=snapshot();report['source_snapshot_unchanged']=report['source_files_after']==report.get('source_files')
  except BaseException as snapshot_error:report['after_snapshot_error']=repr(snapshot_error)
  save();raise
if __name__=='__main__':main()
