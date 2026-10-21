// Execute through Kyoto. --qualify rebuilds/tests before packaging, after other writers settle.
import {execFileSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,readFileSync,readdirSync,mkdirSync,copyFileSync,statSync,rmSync} from 'node:fs';
import {createHash,randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,resolve,relative} from 'node:path';
const root=resolve('typescript'),output=resolve('validation/native-parity/debugger-diagnostics/typescript/installed',`${Date.now()}-${randomUUID()}`),dir=mkdtempSync(join(tmpdir(),'stateless-debug-installed-'));
const hash=b=>createHash('sha256').update(b).digest('hex');
function manifest(paths){const files=[];const visit=p=>{const st=statSync(p);if(st.isDirectory()){for(const entry of readdirSync(p,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)))if(!entry.isSymbolicLink())visit(join(p,entry.name));}else{const b=readFileSync(p);files.push({path:relative(root,p),bytes:b.length,sha256:hash(b)});}};for(const p of paths)visit(join(root,p));return files;}
const sourceBefore=manifest(['src','test','package.json','package-lock.json','tsconfig.json']);const commands=[];const run=(cmd,args,options={})=>{commands.push({cmd,args,cwd:options.cwd??process.cwd()});return execFileSync(cmd,args,options);};
mkdirSync(output,{recursive:true});
const consumer=`import assert from 'node:assert/strict';
const core=await import('@jagalite/statelessness');assert.equal('EvidenceViewer' in core,false);assert.equal('DiagnosticHub' in core,false);
const {EvidenceViewer}=await import('@jagalite/statelessness/debug');
const {DebugSession}=await import('@jagalite/statelessness/debug/session');
const {forkVerified,validateBranchParent}=await import('@jagalite/statelessness/debug/fork');
const {projection,inspect,DEFAULT_INSPECT_LIMITS}=await import('@jagalite/statelessness/debug/inspect');
const {DiagnosticHub}=await import('@jagalite/statelessness/debug/diagnostic');
const {WatchRegistry,WatchAuthorization}=await import('@jagalite/statelessness/debug/watches');
for(const name of ['protocol','metrics','dashboard','live','effects'])assert.ok(Object.keys(await import('@jagalite/statelessness/debug/'+name)).length>0);
const bytes=n=>Uint8Array.of(n);let checked=0;
const model={metadata:()=>core.metadata('installed-counter','installed'),initialState:()=>0,cloneState:x=>x,cloneInput:x=>x,cloneOutput:x=>x,checkState:s=>{checked++;return [core.check('limit',s<3?'passed':'failed','')];},checkTransition:()=>[],step:(s,i)=>core.accepted(s+i,[s+i]),encodeState:bytes,decodeState:b=>b[0],encodeInput:bytes,decodeInput:b=>b[0],encodeOutput:bytes};
const policy={kind:'unrestricted',label:'out-of-domain-injection'},limits={maxTurns:5n,maxContinue:3,maxCandidates:2,maxBreakpoints:1};
const session=DebugSession.recording('installed',model,policy,limits,{strategy:'installed',seed:18446744073709551615n,parameters:[]});
assert.equal(session.step(0n,1).delivered,true);assert.equal(session.state,1);assert.equal(checked,2);
const viewer=new EvidenceViewer({kind:'v1',trace:session.exportTrace()});const before=checked;assert.equal(viewer.boundary(1).after[0],1);assert.equal(checked,before);assert.equal(viewer.verify(model,{maxTransitions:1n}).outcome,'exact');
const branch=forkVerified(viewer,'installed-branch',model,1,policy,limits,{}, {maxTransitions:1n});branch.session.step(0n,1);assert.equal(branch.session.state,2);assert.equal(session.state,1);validateBranchParent(branch.provenance,viewer.artifact());
const value=n=>projection({name:'installed',version:1},(p,c)=>c.scalar(p,{kind:'integer',type:'u64',decimal:String(n)}));
const result=inspect(value(9007199254740993n),{snapshot:{session:1n,revision:9007199254740993n,sequence:1n},path:[],schemaVersion:1,page:{offset:0,limit:1},limits:DEFAULT_INSPECT_LIMITS});assert.equal(result.node.kind.value.decimal,'9007199254740993');
const watches=new WatchRegistry(undefined,WatchAuthorization.allowAll());const ack=watches.add({selector:{path:[],schemaVersion:1},trigger:{kind:'changed'},enabled:true,evaluationInterval:1n,deliveryInterval:1n,limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:32768},page:{offset:0,limit:1}});watches.observe(()=>value(1),{snapshot:{session:1n,revision:0n,sequence:0n},origin:'simulation'});assert.equal(watches.current(ack.watchId).kind.kind,'baseline');
const hub=new DiagnosticHub();const sink=hub.addSink({sites:['counter'],paths:[[]]},{records:2,bytes:10000,policy:'newest'});hub.registerProducer(1n,18446744073709551615n);hub.configure(0n,[{sink,site:'counter',kind:'probe',path:[],trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'debug'}]);hub.acknowledge(1n,1n,0n);hub.withTurn(1n,1n,0n,'test',scope=>scope.probe('counter',()=>value(1)));assert.equal(hub.pop(sink).epoch,18446744073709551615n);
console.log('installed fresh-process viewer/verification/native recording/fork/inspection/watch/probe proof passed');`;
try{
 if(process.argv.includes('--qualify'))run('npm',['run','build'],{cwd:root,stdio:'inherit'});
 if(process.argv.includes('--qualify'))run(process.execPath,['--test',...readdirSync(join(root,'test')).filter(n=>n.endsWith('.test.mjs')).sort().map(n=>join(root,'test',n))],{cwd:root,stdio:'inherit'});
 const dist=manifest(['dist']);
 const packed=JSON.parse(run('npm',['pack','--json','--ignore-scripts','--pack-destination',output],{cwd:root,encoding:'utf8'}));
 if(!packed[0].files.some(f=>f.path==='DEBUGGER.md'))throw Error('optional debugger guide absent from package');
 const tarball=join(output,packed[0].filename),tarBytes=readFileSync(tarball);writeFileSync(join(output,'consumer.mjs'),consumer,{flag:'wx'});
 writeFileSync(join(dir,'package.json'),'{"type":"module","private":true}');run('npm',['install','--ignore-scripts','--no-audit','--no-fund',tarball],{cwd:dir,stdio:'inherit'});copyFileSync(join(output,'consumer.mjs'),join(dir,'proof.mjs'));run(process.execPath,[join(dir,'proof.mjs')],{cwd:dir,stdio:'inherit'});
 const sourceAfter=manifest(['src','test','package.json','package-lock.json','tsconfig.json']);if(JSON.stringify(sourceBefore)!==JSON.stringify(sourceAfter))throw Error('source changed during qualification; receipt cannot qualify one candidate');
 const receipt={schema:'statelessness.debugger-installed-proof/1',sourceBefore,sourceAfter,dist,sourceStable:true,buildAndFullTestsExecuted:process.argv.includes('--qualify'),packageArtifact:{path:tarball,bytes:tarBytes.length,sha256:hash(tarBytes)},consumer:{bytes:Buffer.byteLength(consumer),sha256:hash(consumer)},runtime:{node:process.version,platform:process.platform,arch:process.arch,npm:run('npm',['--version'],{encoding:'utf8'}).trim(),typescript:JSON.parse(readFileSync(join(root,'node_modules/typescript/package.json'))).version},commands};
 writeFileSync(join(output,'receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({installedProof:output,packageSha256:hash(tarBytes),packageBytes:tarBytes.length,sourceStable:true,buildAndFullTestsExecuted:receipt.buildAndFullTestsExecuted}));
}catch(error){writeFileSync(join(output,'failure.json'),JSON.stringify({error:String(error),sourceBefore,commands},null,2)+'\n',{flag:'wx'});throw error;}finally{rmSync(dir,{recursive:true,force:true});}
