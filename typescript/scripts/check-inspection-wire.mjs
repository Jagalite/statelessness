// Compare responses from independently constructed Rust and TypeScript hosts.
// Pass the freshly built Rust inspect_wire_fixture executable as the sole argument.
import {spawnSync} from 'node:child_process';
import {DebugSession} from '../dist/debug_session.js';
import {ProtocolSession,encodeResponse} from '../dist/debug_protocol.js';
import {projection} from '../dist/debug_inspect.js';
import {EffectObserver,effectProtocolBindings} from '../dist/debug_effects.js';
import {WatchRegistry,WatchAuthorization,DEFAULT_WATCH_LIMITS} from '../dist/debug_watches.js';
import {DiagnosticHub} from '../dist/debug_diagnostic.js';

if (process.argv.length!==3) throw new Error('usage: node scripts/check-inspection-wire.mjs RUST_FIXTURE');
const rust=spawnSync(process.argv[2],[],{encoding:'utf8',timeout:10000});
if(rust.error||rust.status!==0)throw new Error(`Rust fixture failed: ${rust.error ?? rust.stderr}`);
const model={initialState:()=>((1n<<128n)-11n),step:s=>({state:s,outputs:[],disposition:{kind:'accepted',reason:''}}),checkState:()=>[],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o};
const session=new DebugSession('wire',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:100000n,maxContinue:1000,maxCandidates:1000,maxBreakpoints:64});
const protocol=new ProtocolSession(session,()=>null);
protocol.enableInspection(value=>projection({name:'u128',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u128',decimal:String(value)})));
const request=(requestId,command)=>({version:1,session:'wire',epoch:protocol.epoch,requestId,revision:0n,configuration:0n,command});
const handle=Object.fromEntries(protocol.handle(request(1n,{kind:'snapshot'})).fields).handle;
const actual=[];
for(const [id,schema] of [[2n,1],[3n,2]]){
 const response=protocol.handle(request(id,{kind:'host',name:'inspect',arguments:[handle,`u64:${schema}`,'u64:0','u64:1']}));
 if(response.error!==(schema===1?null:'invalid_input'))throw new Error(`unexpected inspection outcome for schema ${schema}: ${response.error}`);
 if(schema===1&&Object.fromEntries(response.fields)['node.value']!==`u128:${(1n<<128n)-11n}`)throw new Error('inspection did not return the expected scalar');
 actual.push(new TextDecoder().decode(encodeResponse(response,protocol.limits)));
}
const treeModel={...model,initialState:()=>({first:7,child:{x:11,y:12}}),cloneState:s=>({first:s.first,child:{...s.child}})};
const treeSession=new DebugSession('tree',treeModel,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:100000n,maxContinue:1000,maxCandidates:1000,maxBreakpoints:64});
const treeProtocol=new ProtocolSession(treeSession,()=>null);
const integer=n=>projection({name:'u64',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u64',decimal:String(n)}));
treeProtocol.enableInspection(value=>projection({name:'fixture.tree',version:1},(path,cx)=>cx.object(path,'Tree',[
 {id:'first',label:'first',value:integer(value.first)},
 {id:'child',label:'child',value:projection({name:'Child',version:1},(p,c)=>c.object(p,'Child',[{id:'x',label:'x',value:integer(value.child.x)},{id:'y',label:'y',value:integer(value.child.y)}]))},
])));
const treeRequest=(requestId,command)=>({version:1,session:'tree',epoch:treeProtocol.epoch,requestId,revision:0n,configuration:0n,command});
const treeHandle=Object.fromEntries(treeProtocol.handle(treeRequest(1n,{kind:'snapshot'})).fields).handle;
for(const [id,offset,path] of [[2n,1,[]],[3n,0,['field:child']]]){
 const response=treeProtocol.handle(treeRequest(id,{kind:'host',name:'inspect',arguments:[treeHandle,'u64:1',`u64:${offset}`,'u64:1',...path]}));
 if(response.error!==null)throw new Error(`tree inspection failed: ${response.error}`);
 const fields=Object.fromEntries(response.fields);
 if(fields['node.page.returned']!=='u64:1'||fields['node.page.total']!=='u64:2')throw new Error('tree pagination not represented');
 if(id===2n&&fields['node.children.0.node.page.returned']!=='u64:1')throw new Error('nested page not represented');
 actual.push(new TextDecoder().decode(encodeResponse(response,treeProtocol.limits)));
}
const observer=new EffectObserver({origin:'TestClock'},{now:()=>({domain:7n,nanos:0n})});
const effect=observer.requested({run:1n,epoch:1n,machine:1n,transitionSequence:1n,outputIndex:0});
observer.debuggerPause(true);observer.admission(effect,'Accepted');const attempt=observer.attemptCreated(effect);observer.attemptStarted(attempt);
const effectSession=new DebugSession('effect',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:100000n,maxContinue:1000,maxCandidates:1000,maxBreakpoints:64});
const effectProtocol=new ProtocolSession(effectSession,()=>null,{},{},effectProtocolBindings(observer));
const effectArgs=[`u64:${effect.id.run}`,`u64:${effect.id.epoch}`,`u64:${effect.id.serial}`];
for(const [id,name,args] of [[1n,'effect_details',effectArgs],[2n,'effect_timeline',[...effectArgs,'u64:2']]]){
 const response=effectProtocol.handle({version:1,session:'effect',epoch:effectProtocol.epoch,requestId:id,revision:0n,configuration:0n,command:{kind:'host',name,arguments:args}});
 if(response.error!==null)throw new Error(`${name}: ${response.error}`);
 const fields=Object.fromEntries(response.fields);
 if(fields['effect.labels.debugger_affected']!=='bool:false'||fields['effect.debugger_affected']!=='bool:true')throw new Error('effect debugger-affected fields did not diverge');
 if(name==='effect_timeline'&&fields['timeline.truncated']!=='bool:true')throw new Error('timeline did not truncate');
 actual.push(new TextDecoder().decode(encodeResponse(response,effectProtocol.limits)));
}
const watchSession=new DebugSession('watches',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:100000n,maxContinue:1000,maxCandidates:1000,maxBreakpoints:64});
const watchProtocol=new ProtocolSession(watchSession,()=>null,{configureWatches:true,deliverInputs:true},{maxResponseFields:32});
watchProtocol.enableWatches(new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),value=>projection({name:'u128',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u128',decimal:String(value)})));
for(let i=0;i<7;i++){
 const response=watchProtocol.handle({version:1,session:'watches',epoch:watchProtocol.epoch,requestId:BigInt(i+1),revision:0n,configuration:BigInt(i),command:{kind:'host',name:'watch',arguments:['u64:1','u64:0']}});
 if(response.error!==null)throw new Error(`watch installation failed: ${response.error}`);
}
const watchStatus=watchProtocol.handle({version:1,session:'watches',epoch:watchProtocol.epoch,requestId:8n,revision:0n,configuration:7n,command:{kind:'host',name:'watch_status',arguments:[]}});
const watchFields=Object.fromEntries(watchStatus.fields);
if(watchStatus.error!==null||watchFields['watches.total']!=='u64:7'||watchFields['watches.truncated']!=='bool:true'||watchFields['watches.4.id']===undefined||watchFields['watches.5.id']!==undefined)throw new Error('watch bounded prefix was not returned');
actual.push(new TextDecoder().decode(encodeResponse(watchStatus,watchProtocol.limits)));
const watchStep=watchProtocol.handle({version:1,session:'watches',epoch:watchProtocol.epoch,requestId:9n,revision:0n,configuration:7n,command:{kind:'step',bytes:new Uint8Array()}});
if(watchStep.error!==null)throw new Error(`watch step: ${watchStep.error}`);
const watchCurrent=watchProtocol.handle({version:1,session:'watches',epoch:watchProtocol.epoch,requestId:10n,revision:1n,configuration:7n,command:{kind:'host',name:'watch_current',arguments:['u64:1']}});
if(watchCurrent.error!==null||Object.fromEntries(watchCurrent.fields)['watch.kind']!=='enum:Baseline(Initial)')throw new Error('watch current baseline missing');
actual.push(new TextDecoder().decode(encodeResponse(watchCurrent,watchProtocol.limits)));
const hub=new DiagnosticHub(),sink=hub.addSink({sites:['site'],paths:[[]]},{records:1,bytes:65536,policy:'newest'});
for(let id=0n;id<40n;id++)hub.registerProducer(id,1n);
const probeSession=new DebugSession('probes',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:100000n,maxContinue:1000,maxCandidates:1000,maxBreakpoints:64});
const probeProtocol=new ProtocolSession(probeSession,()=>null,{configureDiagnostics:true},{maxResponseFields:32});
probeProtocol.attachProbes(hub,[{id:1n,label:'marker',subscription:{sink,site:'site',kind:'marker',path:[],trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'info'}}],[sink]);
for(const [id,name,args] of [[1n,'probe_configure',['u64:0','u64:1']],[2n,'probe_status',['u64:0','u64:1']]]){
 const response=probeProtocol.handle({version:1,session:'probes',epoch:probeProtocol.epoch,requestId:id,revision:0n,configuration:0n,command:{kind:'host',name,arguments:args}});
 if(response.error!==null)throw new Error(`${name}: ${response.error}`);
 if(name==='probe_configure'){const fields=Object.fromEntries(response.fields);if(fields['probes.pending_producers.total']!=='u64:40'||fields['probes.pending_producers.truncated']!=='bool:true')throw new Error('producer prefix not bounded');}
 actual.push(new TextDecoder().decode(encodeResponse(response,probeProtocol.limits)));
}
const expected=rust.stdout.trimEnd().split('\n');
if(JSON.stringify(actual)!==JSON.stringify(expected))throw new Error(`inspection wire mismatch\nRust: ${JSON.stringify(expected)}\nTypeScript: ${JSON.stringify(actual)}`);
console.log('Rust and TypeScript wire responses match: inspection, effects, bounded watch status, probe configuration and status');
