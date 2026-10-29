#!/usr/bin/env python3
"""Run only under Kyoto against an immutable copied source tree.

No scheduler submission occurs inside this runner. Output must be outside source.
All attempts, including compilation failure, remain in the receipt directory.
"""
import argparse,hashlib,json,os,pathlib,shutil,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);a=p.parse_args()
root=pathlib.Path(a.source).resolve();out=pathlib.Path(a.output).resolve()
if out==root or root in out.parents:raise SystemExit('receipts must be outside frozen source')
out.mkdir(parents=True,exist_ok=False)
manifest={}
for folder in ('python','go','typescript','swift','scripts','conformance','spec','examples'):
 for path in sorted((root/folder).rglob('*')):
  if path.is_file() and not any(v in path.parts for v in ('node_modules','dist','__pycache__','.build')):manifest[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
for name in ('Package.swift','Package.resolved'):
 path=root/name
 if path.exists():manifest[name]=hashlib.sha256(path.read_bytes()).hexdigest()
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
for tool,args in [('python3',['--version']),('go',['version']),('node',['--version']),('npm',['--version']),('swift',['--version'])]:run('tool-'+tool,[tool]+args)
run('python',['python3','-m','unittest','discover','-s','python/tests'],env={'PYTHONPATH':str(root/'python/src')})
run('go',['go','test','./...'],root/'go',{'CGO_ENABLED':'0'})
if run('npm-ci',['npm','ci'],root/'typescript'):run('typescript',['npm','test'],root/'typescript')
run('swift',['swift','test'],root)
# A clean wheel install uses no source import path and no dependency resolution.
venv=out/'venv'
if run('venv',['python3','-m','venv',str(venv)]):
 py=str(venv/'bin/python')
 if run('wheel',[py,'-m','pip','wheel','--no-deps','--wheel-dir',str(out/'packages'),str(root/'python')],out):
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
commands={
 'python':[str(venv/'bin/python'),str(root/'examples/native-stateful/python/network.py')],
 'go':[str(goapp/'consumer')],
 'typescript':['node',str(out/'ts-consumer/network.mjs')],
 'swift':[str(swiftapp/'.build/debug/StatefulConsumer')],
}
for producer,command in commands.items():
 trace=out/(producer+'-network.json')
 if run('record-'+producer,command+['record',str(trace)],out,{'PYTHONPATH':''}):
  for consumer,consume in commands.items():run('replay-'+producer+'-'+consumer,consume+['replay',str(trace)],out,{'PYTHONPATH':''})
# Explicit source mutants: successful compilation is not semantic detection.
mutant=out/'mutant-python';shutil.copytree(root/'python',mutant,ignore=shutil.ignore_patterns('__pycache__'))
f=mutant/'src/statelessness/lifecycle.py';text=f.read_text();needle='if key.epoch<h.retired_before:'
if text.count(needle)!=1:raise SystemExit('mutation site identity changed')
f.write_text(text.replace(needle,'if False and key.epoch<h.retired_before:'))
start=time.monotonic()
with (out/'mutant-python.log').open('wb') as log:
 r=subprocess.run(['python3','-m','unittest','test_stateful.StatefulTests.test_fences_reuse_retention_restart'],cwd=mutant/'tests',env={**os.environ,'PYTHONPATH':str(mutant/'src')},stdout=log,stderr=subprocess.STDOUT)
text=(out/'mutant-python.log').read_text();detected=r.returncode!=0 and 'AssertionError' in text and 'retired_epoch_reference' in text and 'SyntaxError' not in text
(out/'mutation.json').write_text(json.dumps({'name':'accept-retired-reference','detected_semantically':detected,'exit_code':r.returncode,'seconds':time.monotonic()-start,'source_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'log_sha256':hashlib.sha256((out/'mutant-python.log').read_bytes()).hexdigest()},indent=2)+'\n')
if not detected:results.append({'name':'mutation-not-detected','exit_code':1})
# The same retired-reference semantic fault is independently compiled/executed
# in each other native engine; compile failures never count as detections.
mutation_results=[]
controls=[
 ('go','go/lifecycle.go','m.Epoch(k) < h.RetiredBefore','false && m.Epoch(k) < h.RetiredBefore',['go','test','-run','^TestLifecycleFenceAndDeadline$','-count=1','.'],'go','--- FAIL: TestLifecycleFenceAndDeadline'),
 ('typescript','typescript/src/lifecycle.ts','u64(this.epoch(k!))<h.retiredBefore','false&&u64(this.epoch(k!))<h.retiredBefore',['node','--test','--test-name-pattern=1000 explicit epochs','test/stateful.test.mjs'],'typescript','ERR_ASSERTION'),
 ('swift','swift/Sources/Statelessness/Lifecycle.swift','if let k = key, let epoch = epoch, epoch(k) < h.retiredBefore','if let k = key, let epoch = epoch, false && epoch(k) < h.retiredBefore',['swift','test','--filter','StatefulTests.testFencesAndDeadline'],'','XCTAssertEqual failed'),
]
for language,relative,needle,replacement,command,working,marker in controls:
 mutant_root=out/('mutant-'+language)
 folders=['go'] if language=='go' else ['typescript'] if language=='typescript' else ['swift']
 mutant_root.mkdir()
 for folder in folders:shutil.copytree(root/folder,mutant_root/folder,ignore=shutil.ignore_patterns('node_modules','dist','.build'))
 if language=='swift':shutil.copy2(root/'Package.swift',mutant_root/'Package.swift')
 target=mutant_root/relative;content=target.read_text()
 if content.count(needle)!=1:raise SystemExit('mutation site identity changed: '+language)
 target.write_text(content.replace(needle,replacement));ready=True
 if language=='typescript':ready=run('mutant-ts-install',['npm','ci'],mutant_root/'typescript') and run('mutant-ts-compile',['npm','run','build'],mutant_root/'typescript')
 if ready:
  # Expected test failures are recorded separately from qualification commands.
  log=out/('mutant-'+language+'.log')
  with log.open('wb') as sink:result=subprocess.run(command,cwd=mutant_root/working,env={**os.environ,'CGO_ENABLED':'0'},stdout=sink,stderr=subprocess.STDOUT)
  output=log.read_text();detected=result.returncode!=0 and marker in output
  mutation_results.append({'language':language,'command':command,'exit_code':result.returncode,'detected_semantically':detected,'source_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()})
  if not detected:results.append({'name':'mutant-'+language+'-not-detected','exit_code':1})
 else:mutation_results.append({'language':language,'detected_semantically':False,'reason':'compile/install failed'})
(out/'native-mutations.json').write_text(json.dumps(mutation_results,indent=2)+'\n')
artifacts={}
for folder in [packages,goapp,swiftapp/'.build/debug']:
 for path in folder.rglob('*'):
  if path.is_file() and (path.suffix in ('.whl','.tgz') or path.name in ('consumer','StatefulConsumer')):artifacts[str(path.relative_to(out))]=hashlib.sha256(path.read_bytes()).hexdigest()
for path in out.glob('*-network.json'):artifacts[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
matrix={producer:{consumer:next((r['exit_code']==0 for r in results if r['name']=='replay-'+producer+'-'+consumer),None) for consumer in commands} for producer in commands}
(out/'replay-matrix.json').write_text(json.dumps(matrix,indent=2)+'\n')
(out/'artifacts.json').write_text(json.dumps(artifacts,indent=2)+'\n')
# Verify the source files read by this receipt have not changed while running.
for rel,digest in manifest.items():
 if hashlib.sha256((root/rel).read_bytes()).hexdigest()!=digest:raise SystemExit('source changed during qualification: '+rel)
(out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
(out/'manifest.sha256').write_text(hashlib.sha256((out/'source.json').read_bytes()).hexdigest()+'\n')
raise SystemExit(0 if all(r['exit_code']==0 for r in results) else 1)
