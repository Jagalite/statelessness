#!/usr/bin/env python3
"""Run only under Kyoto against an immutable copied source tree.

No scheduler submission occurs inside this runner. Output must be outside source.
All attempts, including compilation failure, remain in the receipt directory.
"""
import argparse,hashlib,json,os,pathlib,re,shutil,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
# A caller must compile this exact Rust test against the snapshot in a direct
# Kyoto cargo-test job. The shared policy may compile without executing tests;
# the runner executes the supplied binary and records the caller-attested job ID.
p.add_argument('--rust-spy-executable');p.add_argument('--rust-spy-kyoto-job');a=p.parse_args()
root=pathlib.Path(a.source).resolve();out=pathlib.Path(a.output).resolve()
if out==root or root in out.parents:raise SystemExit('receipts must be outside frozen source')
out.mkdir(parents=True,exist_ok=False)
def source_manifest():
 manifest={}
 for folder in ('src','crates','tests','python','go','typescript','swift','scripts','conformance','spec','examples','docs'):
  for path in sorted((root/folder).rglob('*')):
   if path.is_file() and not any(v in path.parts for v in ('node_modules','dist','__pycache__','.build','target')):manifest[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
 for name in ('Package.swift','Package.resolved','Cargo.toml','Cargo.lock','build.rs'):
  path=root/name
  if path.exists():manifest[name]=hashlib.sha256(path.read_bytes()).hexdigest()
 return manifest
manifest=source_manifest()
(out/'source.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
results=[]
def run(name,cmd,cwd=root,env=None):
 start=time.monotonic();e=os.environ.copy();e.update(env or {})
 with (out/(name+'.log')).open('wb') as log:
  try:
   completed=subprocess.run(cmd,cwd=cwd,env=e,stdout=log,stderr=subprocess.STDOUT)
   code=completed.returncode
  except OSError as error:
   log.write(("NOT_RUN: "+str(error)+"\n").encode());code=127
 result=dict(name=name,command=cmd,cwd=str(cwd),exit_code=code,seconds=time.monotonic()-start,log_sha256=hashlib.sha256((out/(name+'.log')).read_bytes()).hexdigest())
 results.append(result);(out/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(result),flush=True)
 return code==0
MUTANT_TIMEOUT=int(os.environ.get('STATELESS_MUTANT_TIMEOUT_SECONDS','1800'))
def run_mutant(cmd,cwd,env,sink):
 # A hung mutant is recorded as a timeout (exit 124), never as a detection.
 try:return subprocess.run(cmd,cwd=cwd,env=env,stdout=sink,stderr=subprocess.STDOUT,timeout=MUTANT_TIMEOUT)
 except subprocess.TimeoutExpired:
  sink.write(("TIMEOUT after %d seconds\n"%MUTANT_TIMEOUT).encode());sink.flush()
  return subprocess.CompletedProcess(cmd,124)
for tool,args in [('python3',['--version']),('go',['version']),('node',['--version']),('npm',['--version']),('swift',['--version']),('cargo',['--version']),('rustc',['--version'])]:run('tool-'+tool,[tool]+args)
run('stateful-matrix-accounting-tests',['python3','-m','unittest','discover','-s',str(root/'scripts'),'-p','test_stateful_matrix_accounting.py'])
run('python',['python3','-m','unittest','discover','-s','python/tests'],env={'PYTHONPATH':str(root/'python/src')})
run('go',['go','test','./...'],root/'go',{'CGO_ENABLED':'0'})
if run('npm-ci',['npm','ci'],root/'typescript'):run('typescript',['npm','test'],root/'typescript')
run('swift',['swift','test'],root)
# A clean wheel install uses no source import path and no dependency resolution.
venv=out/'venv'
python_package=out/'python-package-source'
# PEP 517 build backends may write build/ and egg-info into their input tree.
# Keep those generated files outside the source snapshot being verified.
shutil.copytree(root/'python',python_package,ignore=shutil.ignore_patterns('__pycache__','build','*.egg-info'))
if run('venv',['python3','-m','venv',str(venv)]):
 py=str(venv/'bin/python')
 if run('wheel',[py,'-m','pip','wheel','--no-deps','--wheel-dir',str(out/'packages'),str(python_package)],out):
  wheel=next((out/'packages').glob('*.whl'))
  if run('wheel-install',[py,'-m','pip','install','--no-deps',str(wheel)],out):
   run('installed-python',[py,str(root/'examples/native-stateful/python/consumer.py')],out,{'PYTHONPATH':''})
# Fresh ordinary native package consumers, never ABI/Rust linked.
goapp=out/'go-consumer';shutil.copytree(root/'examples/native-stateful/go',goapp)
(goapp/'go.mod').write_text('module statefulconsumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => '+str(root/'go')+'\n')
if run('installed-go-build',['go','build','-o',str(goapp/'consumer'),'.'],goapp,{'CGO_ENABLED':'0'}):run('installed-go',[str(goapp/'consumer')],out)
packages=out/'packages';packages.mkdir(exist_ok=True)
if run('npm-pack',['npm','pack','--pack-destination',str(packages)],root/'typescript'):
 tgz=next(packages.glob('*.tgz'));app=out/'ts-consumer';app.mkdir();(app/'package.json').write_text('{"type":"module","private":true}')
 shutil.copy2(root/'examples/native-stateful/typescript/consumer.mjs',app/'consumer.mjs')
 shutil.copy2(root/'examples/native-stateful/typescript/network.mjs',app/'network.mjs')
 if run('npm-install',['npm','install','--ignore-scripts','--no-audit','--no-fund',str(tgz)],app):run('installed-typescript',['node','consumer.mjs'],app)
swiftapp=out/'swift-consumer';shutil.copytree(root/'examples/native-stateful/swift',swiftapp);(out/'swift-package').symlink_to(root,target_is_directory=True)
if run('installed-swift-build',['swift','build'],swiftapp):run('installed-swift',[str(swiftapp/'.build/debug/StatefulConsumer')],out)
# Cross-language identical indexed failure HISTORY bytes, not a trace-replay claim.
names=['installed-python','installed-go','installed-typescript','installed-swift']
if all(any(r['name']==n and r['exit_code']==0 for r in results) for n in names):
 values=[(out/(n+'.log')).read_text().strip() for n in names]
 (out/'history-interchange.json').write_text(json.dumps({'producer_count':4,'exact_history_bytes':len(set(values))==1,'hex':values[0]},indent=2)+'\n')
 if len(set(values))!=1:results.append({'name':'history-interchange','exit_code':1})
# Exact application failure traces use existing native record/replay APIs.
# Separate processes for every producer/consumer pair; no bytes-only replay claim.
rust_target=out/'rust-target'
rust_binary=rust_target/'debug/examples/native-stateful-network'
run('rust-network-build',['cargo','build','--example','native-stateful-network','--target-dir',str(rust_target)])
run('rust-network-negative-control',[str(rust_binary),'control',str(out/'unused-control-trace')],out)
rust_spy=None
if not a.rust_spy_executable or not a.rust_spy_kyoto_job:
 results.append({'name':'rust-build-mismatch-spy-not-run','exit_code':1,'error':'supply snapshot-compiled executable and Kyoto job ID'})
else:
 rust_spy=pathlib.Path(a.rust_spy_executable).resolve()
 try: spy_hash=hashlib.sha256(rust_spy.read_bytes()).hexdigest()
 except OSError as error:
  results.append({'name':'rust-build-mismatch-spy-not-run','exit_code':1,'error':str(error)});rust_spy=None
 if rust_spy is not None:
  spy_name='strict_build_mismatch_rejects_before_model_or_observer_callbacks'
  # The recorded Kyoto job must be a successful direct Cargo compilation in
  # this exact snapshot, with a declared target directory containing the test
  # executable. The hash is retained, but linkage is still caller-attested:
  # Kyoto does not attest that these executable bytes were not replaced later.
  job_ok=run('rust-spy-kyoto-show',['python3','/Volumes/seed1/Projects/kyoto/kyoto.py','show',a.rust_spy_kyoto_job],out)
  try:
   if not job_ok: raise ValueError('cannot inspect Kyoto job')
   job=json.loads((out/'rust-spy-kyoto-show.log').read_text());request=job['requested_command']
   # Exact command shape excludes values smuggled after `--` as test arguments.
   prefix=['cargo','test','--test','execution',spy_name,'--target-dir']
   if (job['status']!='succeeded' or job['exit_code']!=0 or pathlib.Path(job['cwd']).resolve()!=root
       or len(request)!=7 or request[:6]!=prefix or job['command']!=request+['--no-run']):
    raise ValueError('Kyoto job does not compile this snapshot spy')
   target=pathlib.Path(request[6])
   if not target.is_absolute():raise ValueError('Kyoto target directory must be absolute')
   target=target.resolve()
   if rust_spy.parent!=(target/'debug/deps').resolve() or not rust_spy.name.startswith('execution-'):raise ValueError('spy binary not in Kyoto target directory')
   if not run('rust-spy-kyoto-logs',['python3','/Volumes/seed1/Projects/kyoto/kyoto.py','logs',a.rust_spy_kyoto_job],out):raise ValueError('cannot inspect Kyoto compile log')
   compile_log=(out/'rust-spy-kyoto-logs.log').read_text()
   matches=re.findall(r'Executable tests/execution\.rs \(([^\n]+)\)',compile_log)
   if len(matches)!=1 or pathlib.Path(matches[0]).resolve()!=rust_spy:raise ValueError('Kyoto compile log does not name spy executable')
  except (ValueError,KeyError,IndexError,TypeError) as error:
   results.append({'name':'rust-spy-invalid-kyoto-provenance','exit_code':1,'error':str(error)});job_ok=False
  (out/'rust-spy-provenance.json').write_text(json.dumps({'executable':str(rust_spy),'sha256':spy_hash,'kyoto_compile_job':a.rust_spy_kyoto_job,'source_manifest_sha256':hashlib.sha256((out/'source.json').read_bytes()).hexdigest(),'linkage':'caller-attested; source at compilation is not independently bound to this manifest and executable may be replaced after compilation'},indent=2)+'\n')
  ok=job_ok and run('rust-build-mismatch-spy',[str(rust_spy),'--exact',spy_name],out)
  if not job_ok: results.append({'name':'rust-build-mismatch-spy-not-run','exit_code':1})
  if ok:
   log=(out/'rust-build-mismatch-spy.log').read_text()
   if f'test {spy_name} ... ok' not in log or 'test result: ok. 1 passed; 0 failed; 0 ignored' not in log:
    results.append({'name':'rust-build-mismatch-spy-not-executed','exit_code':1})
run('rust-bridge-tests',['python3','-m','unittest','discover','-s',str(root/'examples/native-stateful/rust'),'-p','test_bridge.py'],root,{'PYTHONPATH':str(root/'python/src')})
commands={
 'python':[str(venv/'bin/python'),str(root/'examples/native-stateful/python/network.py')],
 'go':[str(goapp/'consumer')],
 'typescript':['node',str(out/'ts-consumer/network.mjs')],
 'swift':[str(swiftapp/'.build/debug/StatefulConsumer')],
 'rust':[str(venv/'bin/python'),str(root/'examples/native-stateful/rust/bridge.py')],
}
# Each native engine must execute the deliberately weakened network property and
# independently confirm the completed three-step outcome. A crash or early exit
# is never a detected control; require both successful execution and its marker.
for language in ('python','go','typescript','swift'):
 name='network-negative-control-'+language
 ok=run(name,commands[language]+['control',str(out/'unused-control-trace')],out,{'PYTHONPATH':''})
 if ok and (out/(name+'.log')).read_text().strip()!='network-control-detected':
  results.append({'name':name+'-missing-marker','exit_code':1})
  (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
for producer,command in commands.items():
 trace=out/(producer+'-network.json')
 if producer=='rust':
  recorded=run('record-'+producer,command+['record',str(trace)],out,{'PYTHONPATH':'','STATEFUL_RUST_BINARY':str(rust_binary)})
 else:
  recorded=run('record-audit-'+producer,command+['record-audit',str(trace)+'.audit.bin'],out,{'PYTHONPATH':''})
  if recorded:recorded=run('record-'+producer,[str(venv/'bin/python'),str(root/'examples/native-stateful/rust/bridge.py'),'from-binary',str(trace)],out,{'PYTHONPATH':''})
# Separately labeled REPLAY-004 observations, never substituted for strict replay.
# Both build strings come from actual recorded traces, and every consumer checks
# its live metadata against the pinned consumer string before either replay.
# Source and artifact hashes in this receipt are caller-attested; they do not
# independently establish that the executing models came from those artifacts.
recorded_metadata={}
for name in commands:
 trace=out/(name+'-network.json')
 if trace.exists() and any(r['name']=='record-'+name and r['exit_code']==0 for r in results):
  try:
   data=json.loads(trace.read_text()); meta=data['metadata']
   if set(meta)!={'name','model_version','properties_version','codec_version','build'}:raise ValueError('unexpected metadata')
   recorded_metadata[name]=meta
  except (ValueError,KeyError,TypeError) as error:
   results.append({'name':'metadata-'+name,'exit_code':1,'error':str(error)})
(out/'observation-identities.json').write_text(json.dumps(recorded_metadata,indent=2,sort_keys=True)+'\n')
from stateful_matrix_accounting import require_complete_matrix, verify_cross_build_report
observations={producer:{} for producer in commands}
matrix={producer:{} for producer in commands}
for producer in commands:
 for consumer,consume in commands.items():
  pmeta,cmeta=recorded_metadata.get(producer),recorded_metadata.get(consumer)
  if pmeta is None or cmeta is None:
   matrix[producer][consumer]=None;observations[producer][consumer]={'status':'not-run','reason':'missing recorded identity'};results.append({'name':'missing-cell-'+producer+'-'+consumer,'exit_code':1});continue
  semantic=('name','model_version','properties_version','codec_version')
  if any(pmeta[k]!=cmeta[k] for k in semantic):
   matrix[producer][consumer]=None;observations[producer][consumer]={'status':'not-run','reason':'semantic identity mismatch'};results.append({'name':'identity-mismatch-'+producer+'-'+consumer,'exit_code':1});continue
  if pmeta['build']==cmeta['build']:
   ok=run('replay-'+producer+'-'+consumer,consume+['replay',str(out/(producer+'-network.json'))],out,{'PYTHONPATH':'','STATEFUL_RUST_BINARY':str(rust_binary)})
   matrix[producer][consumer]=ok;observations[producer][consumer]={'status':'reported-build-match-strict','passed':ok};continue
  env={'PYTHONPATH':'','STATEFUL_RUST_BINARY':str(rust_binary),
       'STATEFUL_PRODUCER_BUILD':pmeta['build'],'STATEFUL_CONSUMER_BUILD':cmeta['build']}
  name='observe-cross-build-'+producer+'-'+consumer
  ok=run(name,consume+['compare-cross-build',str(out/(producer+'-network.json'))],out,env)
  try:
   if not ok:raise ValueError('comparison command did not succeed')
   report=verify_cross_build_report((out/(name+'.log')).read_text())
  except (ValueError,UnicodeError) as error:
   ok=False;report=None;results.append({'name':'invalid-structured-result-'+producer+'-'+consumer,'exit_code':1,'error':str(error)})
  # The strict result is intentionally an incompatible negative control,
  # never recorded as an exact replay success. The raw command log is retained.
  matrix[producer][consumer]=False
  observations[producer][consumer]={'status':'cross-build-observation','passed':ok,'report':report,
                                     'producer_build':pmeta['build'],'consumer_build':cmeta['build'],
                                     'strict_expected':'incompatible','build_matches':False}
(out/'observation-matrix.json').write_text(json.dumps(observations,indent=2,sort_keys=True)+'\n')
try: require_complete_matrix(tuple(commands), observations)
except ValueError as error: results.append({'name':'incomplete-observation-matrix','exit_code':1,'error':str(error)})
# Explicit source mutants: successful compilation is not semantic detection.
mutant=out/'mutant-python';shutil.copytree(root/'python',mutant,ignore=shutil.ignore_patterns('__pycache__'))
f=mutant/'src/statelessness/lifecycle.py';text=f.read_text();needle='if key.epoch<h.retired_before:'
if text.count(needle)!=1:raise SystemExit('mutation site identity changed')
f.write_text(text.replace(needle,'if False and key.epoch<h.retired_before:'))
start=time.monotonic()
with (out/'mutant-python.log').open('wb') as log:
 r=run_mutant(['python3','-m','unittest','test_stateful.StatefulTests.test_fences_reuse_retention_restart'],mutant/'tests',{**os.environ,'PYTHONPATH':str(mutant/'src')},log)
text=(out/'mutant-python.log').read_text();detected=r.returncode not in (0,124) and 'AssertionError' in text and 'retired_epoch_reference' in text and 'SyntaxError' not in text
(out/'mutation.json').write_text(json.dumps({'name':'accept-retired-reference','detected_semantically':detected,'exit_code':r.returncode,'seconds':time.monotonic()-start,'source_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'log_sha256':hashlib.sha256((out/'mutant-python.log').read_bytes()).hexdigest()},indent=2)+'\n')
if not detected:results.append({'name':'mutation-not-detected','exit_code':1})
# The same retired-reference semantic fault is independently compiled/executed
# in each other native engine; compile failures never count as detections.
mutation_results=[]
controls=[
 ('go','go/lifecycle.go','m.Epoch(k) < h.RetiredBefore','false && m.Epoch(k) < h.RetiredBefore',['go','test','-run','^TestLifecycleFenceAndDeadline$','-count=1','.'],'go','--- FAIL: TestLifecycleFenceAndDeadline'),
 ('typescript','typescript/src/lifecycle.ts','u64(this.epoch(k!))<h.retiredBefore','u64(this.epoch(k!))<0n',['node','--test','--test-name-pattern=1000 explicit epochs','test/stateful.test.mjs'],'typescript','ERR_ASSERTION'),
 ('swift','swift/Sources/Statelessness/Lifecycle.swift','if let k = key, let epoch = epoch, epoch(k) < h.retiredBefore','if let k = key, let epoch = epoch, false && epoch(k) < h.retiredBefore',['swift','test','--filter','StatefulTests.testFencesAndDeadline'],'','XCTAssertEqual failed'),
]
for language,relative,needle,replacement,command,working,marker in controls:
 mutant_root=out/('mutant-'+language)
 folders=['go'] if language=='go' else ['typescript'] if language=='typescript' else ['swift']
 mutant_root.mkdir()
 for folder in folders:shutil.copytree(root/folder,mutant_root/folder,ignore=shutil.ignore_patterns('node_modules','dist','.build'))
 if language=='typescript':shutil.copytree(root/'docs/requirements',mutant_root/'docs/requirements')
 if language=='swift':shutil.copy2(root/'Package.swift',mutant_root/'Package.swift')
 target=mutant_root/relative;content=target.read_text()
 if content.count(needle)!=1:raise SystemExit('mutation site identity changed: '+language)
 target.write_text(content.replace(needle,replacement));ready=True
 if language=='typescript':ready=run('mutant-ts-install',['npm','ci'],mutant_root/'typescript') and run('mutant-ts-compile',['npm','run','build'],mutant_root/'typescript')
 if ready:
  # Expected test failures are recorded separately from qualification commands.
  log=out/('mutant-'+language+'.log')
  with log.open('wb') as sink:result=run_mutant(command,mutant_root/working,{**os.environ,'CGO_ENABLED':'0'},sink)
  output=log.read_text();detected=result.returncode not in (0,124) and marker in output
  mutation_results.append({'language':language,'command':command,'exit_code':result.returncode,'detected_semantically':detected,'source_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()})
  if not detected:results.append({'name':'mutant-'+language+'-not-detected','exit_code':1})
 else:mutation_results.append({'language':language,'detected_semantically':False,'reason':'compile/install failed'})
(out/'native-mutations.json').write_text(json.dumps(mutation_results,indent=2)+'\n')
artifacts={}
if rust_spy is not None:artifacts['rust-spy-executable']=spy_hash
for folder in [packages,goapp,swiftapp/'.build/debug',rust_target/'debug/examples']:
 for path in folder.rglob('*'):
  if path.is_file() and (path.suffix in ('.whl','.tgz') or path.name in ('consumer','StatefulConsumer','native-stateful-network')):artifacts[str(path.relative_to(out))]=hashlib.sha256(path.read_bytes()).hexdigest()
for path in out.glob('*-network.json.audit.bin'):artifacts[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
for path in out.glob('*-network.json'):artifacts[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
for path in out.glob('*-network.json.run-config.json'):artifacts[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
(out/'replay-matrix.json').write_text(json.dumps(matrix,indent=2)+'\n')
(out/'artifacts.json').write_text(json.dumps(artifacts,indent=2)+'\n')
# Verify the source files read by this receipt have not changed while running.
if source_manifest()!=manifest:raise SystemExit('source file set or content changed during qualification')
(out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
(out/'manifest.sha256').write_text(hashlib.sha256((out/'source.json').read_bytes()).hexdigest()+'\n')
raise SystemExit(0 if all(r['exit_code']==0 for r in results) else 1)
