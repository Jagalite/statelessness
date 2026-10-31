import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {Readable,Writable} from 'node:stream';
import {DebugSession} from '../dist/debug_session.js';
import {DiagnosticHub} from '../dist/debug_diagnostic.js';
import {ProtocolSession,encodeRequest,decodeRequest,encodeResponse,decodeResponse,writeFrame,FrameReader,DEFAULT_PROTOCOL_LIMITS} from '../dist/debug_protocol.js';
import {serveNodeStdio} from '../dist/node_debug_protocol.js';
const model={initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,step:(s,i)=>({state:s+i,outputs:[],disposition:{kind:'accepted'}}),checkState:()=>[],checkTransition:()=>[]};
function fixture(authority={},limits={}){const s=new DebugSession('a\t<é>',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:10n,maxContinue:10,maxCandidates:10,maxBreakpoints:10});const p=new ProtocolSession(s,b=>{if(b.length!==1)throw Error();return b[0];},authority,limits);let id=0n;return {s,p,r:(command,extra={})=>({version:1,session:s.id,epoch:p.epoch,requestId:++id,revision:s.revision,configuration:0n,command,...extra})};}
test('Node protocol transport disconnects on failed framed output',async()=>{
 const {s,p,r}=fixture(),request=r({kind:'status'}),input=Readable.from([writeFrame(encodeRequest(request))]);
 const output=new Writable({write(_chunk,_encoding,callback){callback(Error('output failed'));}});
 await assert.rejects(()=>serveNodeStdio(p,input,output),/output failed/);
 assert.equal(p.handle(r({kind:'status'})).error,'disconnected');assert.equal(s.sequence,0n);
});
test('Node protocol transport reports a late output error after write callback',async()=>{
 const {p,r}=fixture(),input=Readable.from([writeFrame(encodeRequest(r({kind:'status'})))]);
 const output=new Writable({write(_chunk,_encoding,callback){callback();setTimeout(()=>this.emit('error',Error('late output failure')),0);}});
 await assert.rejects(()=>serveNodeStdio(p,input,output),/late output failure/);
 assert.equal(p.handle(r({kind:'status'})).error,'disconnected');
});
test('built-in inspection attaches an explicit projection with handle and budget checks',async()=>{
 const {projection}=await import('../dist/debug_inspect.js'),{s,p,r}=fixture({deliverInputs:true});let calls=0;
 p.enableInspection(state=>projection({name:'counter',version:1},(path,context)=>{calls++;assert.equal(p.handle(r({kind:'cancel'})).error,'busy');assert.throws(()=>s.step(s.revision,1),/owned/);return context.scalar(path,{kind:'integer',type:'u64',decimal:String(state)});}));
 assert.equal(Object.fromEntries(p.handle(r({kind:'status'})).fields)['capability.inspection'],'bool:true');
 const handle=Object.fromEntries(p.handle(r({kind:'snapshot'})).fields).handle;
 const request=(schema='u64:1',offset='u64:0',limit='u64:1')=>r({kind:'host',name:'inspect',arguments:[handle,schema,offset,limit]});
 const result=p.handle(request());assert.equal(result.error,null);const fields=Object.fromEntries(result.fields);
 assert.equal(fields['schema.name'],'string:counter');assert.equal(fields['node.kind'],'enum:integer');assert.equal(fields['node.value'],'u64:0');assert.equal(fields['snapshot.sequence'],'u64:0');
 assert.equal(p.handle(request('u64:2')).error,'invalid_input');assert.equal(p.handle(request('u64:1','u64:0','u64:0')).error,'limit');assert.equal(calls,1);
 assert.equal(p.handle(r({kind:'step',bytes:Uint8Array.of(1)})).error,null);assert.equal(p.handle(request()).error,'stale_handle');assert.throws(()=>p.enableInspection(()=>null),{code:'invalid_input'});
});
test('built-in inspection admits schema field independently of node budget',async()=>{
 const {projection}=await import('../dist/debug_inspect.js'),{p,r}=fixture();
 p.enableInspection(()=>projection({name:'s'.repeat(8185),version:1},(path,context)=>context.scalar(path,{kind:'unit'})));
 const handle=Object.fromEntries(p.handle(r({kind:'snapshot'})).fields).handle;
 const result=p.handle(r({kind:'host',name:'inspect',arguments:[handle,'u64:1','u64:0','u64:1']}));
 assert.equal(result.error,null);assert.equal(Object.fromEntries(result.fields)['schema.name'].length,8192);
});
test('built-in inspection bounds schema UTF-8 before wire formatting',async()=>{
 const {projection}=await import('../dist/debug_inspect.js');
 for(const [name,expected] of [['x'.repeat(1_000_000),'limit'],['é'.repeat(4093),'limit'],['\ud800','invalid_input']]){
  const {p,r}=fixture();p.enableInspection(()=>projection({name,version:1},(path,c)=>c.scalar(path,{kind:'unit'})));
  const handle=Object.fromEntries(p.handle(r({kind:'snapshot'})).fields).handle;
  assert.equal(p.handle(r({kind:'host',name:'inspect',arguments:[handle,'u64:1','u64:0','u64:1']})).error,expected);
 }
});
test('wire fields place page metadata before node kind at every depth',async()=>{
 const {nodeProtocolFields}=await import('../dist/debug_protocol_services.js');
 const make=children=>({kind:{kind:'object',name:'x'},children,childCount:children.length,page:{offset:0,returned:children.length,total:children.length,nextOffset:null},completeness:{kind:'complete'}});
 const fields=nodeProtocolFields('node',make([{segment:{kind:'field',id:'a'},label:'a',node:make([])}]));
 const names=fields.map(([name])=>name);
 for(const p of ['node','node.children.0.node'])assert.deepEqual(names.slice(names.indexOf(p+'.child_count'),names.indexOf(p+'.kind')+1),[p+'.child_count',p+'.page.offset',p+'.page.returned',p+'.page.total',p+'.page.next',p+'.kind']);
});
test('independent manually specified wire golden and full width values',()=>{const r={version:1,session:'x\t<é>',epoch:18446744073709551615n,requestId:9007199254740993n,revision:18446744073709551615n,configuration:0n,command:{kind:'status'}};const golden='DDBG\tu32:1\trequest\tstring:x%09%3C%C3%A9%3E\tu64:18446744073709551615\tu64:9007199254740993\tu64:18446744073709551615\tu64:0\tstatus';assert.equal(new TextDecoder().decode(encodeRequest(r)),golden);assert.deepEqual(decodeRequest(new TextEncoder().encode(golden)),r);const response={...r,error:null,fields:[['quantity','u128:340282366920938463463374607431768211455']],command:undefined};const decoded=decodeResponse(encodeResponse(response));assert.equal(decoded.fields[0][1],'u128:340282366920938463463374607431768211455');assert.equal(decoded.revision,r.revision);});
test('framing fragmented, partial, oversized and strict escaping',()=>{const f=writeFrame(new Uint8Array([7,8]));const parser=new FrameReader();let out=[];for(const b of f)out.push(...parser.feed(Uint8Array.of(b)));parser.finish();assert.deepEqual(out,[Uint8Array.of(7,8)]);const short=new FrameReader();[...short.feed(f.slice(0,5))];assert.throws(()=>short.finish(),/malformed/);assert.throws(()=>[...new FrameReader(2).feed(Uint8Array.of(0,0,0,3))],/limit/);assert.throws(()=>decodeRequest(new TextEncoder().encode('DDBG\tu32:1\trequest\tstring:%FF\tu64:1\tu64:1\tu64:0\tu64:0\tstatus')),/malformed/);});
test('actual session authority, handles, stale step, cancellation and independent adapters',()=>{const {s,p,r}=fixture();assert.equal(p.handle(r({kind:'step',bytes:Uint8Array.of(1)})).error,'unauthorized');assert.equal(p.handle(r({kind:'unsupported',name:'watch',arguments:[]})).error,'unsupported');assert.equal(s.revision,0n);const f=fixture({deliverInputs:true});const snap=f.p.handle(f.r({kind:'snapshot'}));const handle=BigInt(snap.fields[0][1].slice(4));assert.equal(f.p.handle(f.r({kind:'step',bytes:Uint8Array.of(2)})).error,null);assert.equal(f.s.state,2);assert.equal(f.p.handle(f.r({kind:'checks',handle})).error,'stale_handle');assert.equal(f.p.handle(f.r({kind:'step',bytes:Uint8Array.of(2)},{revision:0n})).error,'stale_revision');assert.equal(f.p.handle(f.r({kind:'cancel'})).error,null);assert.equal(f.s.stopReason.kind,'cancelled');});
test('bounded dedup original responses, conflict, skipped retired IDs, discovery and reconnect',()=>{const {s,p,r}=fixture({deliverInputs:true},{maxCacheEntries:1});const a=r({kind:'step',bytes:Uint8Array.of(1)});const first=p.handle(a);assert.deepEqual(p.handle(a),first);assert.equal(s.state,1);assert.equal(p.handle({...a,command:{kind:'cancel'}}).error,'duplicate_conflict');p.handle(r({kind:'status'},{requestId:5n}));assert.equal(p.handle(a).error,'retired_request');assert.equal(p.handle(r({kind:'status'},{requestId:4n})).error,'retired_request');const old=p.epoch;p.disconnect();p.reconnect();assert.notEqual(p.epoch,old);assert.equal(p.handle(a).error,'wrong_epoch');assert.equal(p.handle(r({kind:'status'},{epoch:0n,requestId:1n})).epoch,p.epoch);assert.equal(p.handle(r({kind:'cancel'},{epoch:0n})).error,'wrong_epoch');});

test('connected reconnect rejects without changing epoch, cached replies, admission or model state',()=>{
 const {s,p,r}=fixture({deliverInputs:true});
 const firstRequest=r({kind:'step',bytes:Uint8Array.of(1)},{requestId:5n});
 const firstResponse=p.handle(firstRequest);
 assert.equal(firstResponse.error,null);
 assert.equal(p.handle(r({kind:'step',bytes:Uint8Array.of(2)},{requestId:6n})).error,null);
 const originalEpoch=p.epoch;
 const originalState={state:s.state,revision:s.revision,sequence:s.sequence,phase:s.phase,stop:s.stopReason};
 assert.deepEqual(originalState,{state:3,revision:2n,sequence:2n,phase:'paused',stop:null});
 assert.throws(()=>p.reconnect(),{code:'invalid_input'});
 assert.equal(p.epoch,originalEpoch);
 // This response has revision 1 although the current session is at revision 2.
 assert.deepEqual(p.handle(firstRequest),firstResponse);
 assert.equal(p.handle({...firstRequest,command:{kind:'cancel'}}).error,'duplicate_conflict');
 assert.equal(p.handle(r({kind:'status'},{requestId:4n})).error,'retired_request');
 assert.deepEqual({state:s.state,revision:s.revision,sequence:s.sequence,phase:s.phase,stop:s.stopReason},originalState);
 p.disconnect();
 assert.equal(p.reconnect(),originalEpoch+1n);
 assert.equal(s.state,originalState.state);
 assert.equal(s.revision,originalState.revision);
 assert.equal(s.sequence,originalState.sequence);
});
test('callback reentry rejected and raw transport errors do not deliver',()=>{const f=fixture({deliverInputs:true});const p=new ProtocolSession(f.s,()=>{assert.equal(p.handle({...f.r({kind:'cancel'}),epoch:p.epoch}).error,'busy');return 1;},{deliverInputs:true});assert.equal(p.handle({...f.r({kind:'step',bytes:Uint8Array.of(1)}),epoch:p.epoch}).error,null);assert.equal(f.s.sequence,1n);const response=decodeResponse(p.handlePayload(Uint8Array.of(255)));assert.equal(response.requestId,0n);assert.equal(response.error,'malformed');});
test('real OS pipe framed session with duplicate delivery and clean EOF',async()=>{const source=`import {DebugSession} from './dist/debug_session.js';import {ProtocolSession} from './dist/debug_protocol.js';import {serveNodeStdio} from './dist/node_debug_protocol.js';const m=${JSON.stringify({})};const s=new DebugSession('pipe',{initialState:()=>0,cloneState:x=>x,cloneInput:x=>x,cloneOutput:x=>x,step:(s,i)=>({state:s+i,outputs:[],disposition:{kind:'accepted'}}),checkState:()=>[],checkTransition:()=>[]},{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:10n,maxContinue:1,maxCandidates:1,maxBreakpoints:1});const p=new ProtocolSession(s,b=>b[0],{deliverInputs:true});await serveNodeStdio(p);`;
 const child=spawn(process.execPath,['--input-type=module','-e',source],{cwd:new URL('..',import.meta.url),stdio:['pipe','pipe','pipe']});const chunks=[],errors=[];child.stdout.on('data',b=>chunks.push(b));child.stderr.on('data',b=>errors.push(b));const request={version:1,session:'pipe',epoch:1n,requestId:1n,revision:0n,configuration:0n,command:{kind:'step',bytes:Uint8Array.of(2)}};child.stdin.write(writeFrame(encodeRequest(request)));child.stdin.write(writeFrame(encodeRequest(request)));child.stdin.end(writeFrame(encodeRequest({...request,requestId:2n,revision:1n,command:{kind:'status'}})));const exit=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve);});assert.equal(exit,0,Buffer.concat(errors).toString());const f=new FrameReader(),responses=[...f.feed(Buffer.concat(chunks))].map(b=>decodeResponse(b));f.finish();assert.equal(responses.length,3);assert.deepEqual(responses[0],responses[1]);assert.equal(responses[2].revision,1n);});
test('host adapter whitelist, independent authority, watch CAS and bounded staged commits',()=>{const f=fixture();let committed=0,prepared=0;const bindings={watch:(a,ctx)=>{prepared++;assert.equal(ctx.configuration,0n);return {fields:[['watch.id','u64:1']],commit:()=>{committed++;}};},probe_configure:()=>({fields:[],commit:()=>{committed++;}}),metric_catalog:()=>({fields:[['quantity','u128:340282366920938463463374607431768211455']]})};const p=new ProtocolSession(f.s,b=>b[0],{configureWatches:true},{},bindings);let id=0n;const r=(name,args=[],extra={})=>({...f.r({kind:'host',name,arguments:args}),epoch:p.epoch,requestId:++id,...extra});assert.equal(p.handle(r('watch',['u64:1','u64:0'],{configuration:1n})).error,'stale_configuration');assert.equal(prepared,0);assert.equal(p.handle(r('watch',['u64:1','u64:0'])).configuration,1n);assert.equal(committed,1);assert.equal(f.s.sequence,0n);assert.equal(p.handle(r('watch',['u64:1','u64:0'])).error,'stale_configuration');assert.equal(p.handle(r('probe_configure',['u64:0'])).error,'unauthorized');assert.equal(p.handle(r('metric_catalog',['u64:0','u64:1'])).fields[0][1],'u128:340282366920938463463374607431768211455');assert.equal(p.handle(r('metric_catalog',['i64:0','u64:1'])).error,'malformed');const huge=new ProtocolSession(f.s,b=>b[0],{configureWatches:true},{},{watch:()=>({fields:[['huge','x'.repeat(9000)]],commit:()=>{committed++;}})});assert.equal(huge.handle({...r('watch',['u64:1','u64:0']),epoch:huge.epoch}).error,'limit');assert.equal(committed,1);assert.equal(huge.configuration,0n);});

test('host exact export handles and independent grants; structural paths retain typed keys',()=>{const f=fixture();let calls=0;const p=new ProtocolSession(f.s,b=>b[0],{readExactValues:true},{},{export_trace:()=>{calls++;return {fields:[]};}});const request={...f.r({kind:'host',name:'export_trace',arguments:['u64:1','u64:0','u64:1']}),epoch:p.epoch};assert.equal(p.handle(request).error,'unauthorized');assert.equal(calls,0);const base={...request,command:{kind:'host',name:'inspect',arguments:['u64:1','u64:1','u64:0','u64:1','field:a.b','key.u128:340282366920938463463374607431768211455','key.string:a.b']}};assert.deepEqual(decodeRequest(encodeRequest(base)).command,base.command);assert.throws(()=>encodeRequest({...base,command:{...base.command,arguments:[...base.command.arguments,'key.u8:256']}}),/malformed/);});

test('u32 versions reject out of range and lossy integers structurally',()=>{const f=fixture(),r=f.r({kind:'status'});for(const version of [-1,1.5,4294967296,9007199254740992])assert.throws(()=>encodeRequest({...r,version}),/malformed/);const raw=new TextDecoder().decode(encodeRequest(r));for(const v of ['4294967296','9007199254740993'])assert.throws(()=>decodeRequest(new TextEncoder().encode(raw.replace('u32:1',`u32:${v}`))),/malformed/);});
test('exact outputs require independent authority and bounded optional encoder with expired lease',()=>{let calls=0,lease,session;const m={...model,step:(s,i)=>({state:s+i,outputs:[i],disposition:{kind:'accepted'}}),encodeOutputInto(o,b){calls++;lease=b;assert.throws(()=>session.cancel(session.revision),/owned/);b.extend(Uint8Array.of(o));}};session=new DebugSession('outputs',m,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:10n,maxContinue:1,maxCandidates:1,maxBreakpoints:1});session.step(0n,7);const run=authority=>{const p=new ProtocolSession(session,b=>b[0],authority),r=(requestId,command)=>({version:1,session:session.id,epoch:p.epoch,requestId,revision:1n,configuration:0n,command});const snap=p.handle(r(1n,{kind:'snapshot'})),handle=BigInt(snap.fields[0][1].slice(4));return p.handle(r(2n,{kind:'outputs',handle}));};assert.equal(run({}).fields.find(([k])=>k==='outputs.payloads')[1],'enum:unavailable');assert.equal(calls,0);const response=run({readExactValues:true});assert.equal(response.error,null);assert.equal(response.fields.find(([k])=>k==='outputs.0.encoded')[1],'bytes:07');assert.equal(calls,1);assert.throws(()=>lease.extend(Uint8Array.of(1)),/expired/);});

test('exact output codec receives detached output graph',()=>{const m={...model,cloneOutput:o=>({nested:{...o.nested}}),step:s=>({state:s+1,outputs:[{nested:{value:7}}],disposition:{kind:'accepted'}}),encodeOutputInto(o,b){o.nested.value=99;b.extend(Uint8Array.of(o.nested.value));}};const s=new DebugSession('detached-output',m,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:2n,maxContinue:1,maxCandidates:1,maxBreakpoints:1});s.step(0n,1);assert.deepEqual(s.encodeObservedOutput(0,1),Uint8Array.of(99));assert.equal(s.observation().transition.outputs[0].nested.value,7);});

test('host commit reserves full revision/configuration wire width before mutation',()=>{const f=fixture();let commits=0,p;const limits={...DEFAULT_PROTOCOL_LIMITS,maxFrame:2048};const fields=[['padding','']];p=new ProtocolSession(f.s,b=>b[0],{deliverInputs:true},limits,{select:()=>({fields,commit:()=>{commits++;f.s.step(f.s.revision,1);}})});const response={version:1,session:f.s.id,epoch:p.epoch,requestId:1n,revision:0n,configuration:0n,error:null,fields};while(encodeResponse(response,limits).length<2048)fields[0][1]+='x';assert.equal(encodeResponse(response,limits).length,2048);const request={version:1,session:f.s.id,epoch:p.epoch,requestId:1n,revision:0n,configuration:0n,command:{kind:'host',name:'select',arguments:['u64:1']}};assert.equal(p.handle(request).error,'limit');assert.equal(commits,0);assert.equal(f.s.revision,0n);});
test('host inspection and watch schemas are bounded u32 identities',()=>{const f=fixture();for(const [name,args]of [['inspect',['u64:1','u64:4294967296','u64:0','u64:1']],['watch',['u64:4294967296','u64:0']]])assert.throws(()=>encodeRequest(f.r({kind:'host',name,arguments:args})),/malformed/);});

test('external decoder cannot directly mutate borrowed session before intended input delivery',()=>{const f=fixture({deliverInputs:true}),p=new ProtocolSession(f.s,b=>{assert.throws(()=>f.s.step(f.s.revision,99),/owned/);assert.throws(()=>f.s.cancel(f.s.revision),/owned/);return b[0];},{deliverInputs:true});const response=p.handle({...f.r({kind:'step',bytes:Uint8Array.of(2)}),epoch:p.epoch});assert.equal(response.error,null);assert.equal(f.s.sequence,1n);assert.equal(f.s.state,2);});

test('concrete candidate cache uses canonical bytes, revalidates, redacts and retires on reconnect',()=>{
 let decoded=0,allowed=[1,2];
 const m={...model,inputs:()=>allowed,encodeInput:i=>Uint8Array.of(i),decodeInput:b=>b[0]};
 const s=new DebugSession('candidates',m,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:10n,maxContinue:10,maxCandidates:10,maxBreakpoints:0});
 const p=new ProtocolSession(s,b=>{decoded++;return b[0];},{deliverInputs:true});let id=0n;
 const send=(name,a,revision=s.revision)=>p.handle({version:1,session:s.id,epoch:p.epoch,requestId:++id,revision,configuration:0n,command:{kind:'host',name,arguments:a}});
 let response=send('inputs',['u64:0','u64:1']);assert.equal(response.error,null);assert.deepEqual(Object.fromEntries(response.fields),{complete:'bool:false',next_offset:'u64:1','candidates.0.token':'u64:1','candidates.returned':'u64:1'});
 assert.equal(send('inputs',['u64:10','u64:1']).error,'invalid_input');assert.equal(send('inputs',['u64:0','u64:11']).error,'invalid_input');
 allowed=[2];assert.equal(send('select',['u64:1']).error,'invalid_input');assert.equal(s.sequence,0n);assert.equal(decoded,1);
 allowed=[1,2];assert.equal(send('select',['u64:1']).error,null);assert.equal(s.sequence,1n);assert.equal(decoded,2);
 assert.equal(send('select',['u64:1']).error,'stale_handle');
 response=send('inputs',['u64:0','u64:1']);const token=Object.fromEntries(response.fields)['candidates.0.token'];p.disconnect();p.reconnect();assert.equal(send('select',[token]).error,'stale_handle');
 assert.throws(()=>s.traceWindow(-1n,1));assert.throws(()=>s.traceWindow(0n,-1));
});

test('attached watch owns projection callbacks and preserves its installation boundary',async()=>{
 const {WatchRegistry,WatchAuthorization,DEFAULT_WATCH_LIMITS}=await import('../dist/debug_watches.js');
 const {projection}=await import('../dist/debug_inspect.js');
 const s=new DebugSession('watch',model,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:10n,maxContinue:10,maxCandidates:10,maxBreakpoints:0});
 const p=new ProtocolSession(s,b=>b[0],{deliverInputs:true,configureWatches:true});let calls=0,id=0n;
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 p.enableWatches(registry,state=>{calls++;return projection({name:'counter',version:1},(path,cx)=>{assert.throws(()=>s.step(s.revision,1),/owned/);return cx.scalar(path,{kind:'integer',type:'u64',decimal:String(state)});});});
 assert.equal(calls,0);
 const send=(command)=>p.handle({version:1,session:s.id,epoch:p.epoch,requestId:++id,revision:s.revision,configuration:p.configuration,command});
 const host=(name,arguments_)=>send({kind:'host',name,arguments:arguments_});
 let r=host('watch',['u64:1','u64:0']);assert.equal(r.error,null);let fields=Object.fromEntries(r.fields);assert.equal(fields['watch.effective_sequence'],'u64:1');assert.equal(fields['watch.resolved_type'],'enum:u64');assert.equal(fields['watch.metadata_complete'],'bool:true');
 assert.equal(send({kind:'step',bytes:Uint8Array.of(1)}).error,null);
 registry.list=()=>{throw Error('watch_current must not enumerate configurations');};
 fields=Object.fromEntries(host('watch_current',['u64:999']).fields);assert.equal(fields['watch.value'],'enum:unavailable');
 fields=Object.fromEntries(host('watch_current',['u64:1']).fields);assert.equal(fields['watch.kind'],'enum:Baseline(Initial)');assert.equal(fields['watch.origin'],'enum:OutOfDomainInjection');assert.equal(fields['watch.node.value'],'u64:1');
 assert.equal(send({kind:'step',bytes:Uint8Array.of(0)}).error,null);
 fields=Object.fromEntries(host('watch_current',['u64:1']).fields);assert.equal(fields['watch.snapshot_revision'],'u64:2');assert.equal(fields['watch.kind'],'enum:Unchanged');
 const status=host('watch_status',[]);fields=Object.fromEntries(status.fields);assert.equal(fields['watches.0.effective_sequence'],'u64:1');
 // Exact Rust crates/statelessness-debug/src/protocol.rs Command::WatchStatus field sequence.
 assert.deepEqual(status.fields.map(([k])=>k),['watches.total','watches.truncated','watches.dropped_events','watches.observation_gaps','watches.inspection_errors','watches.0.id','watches.0.generation','watches.0.enabled','watches.0.has_baseline','watches.0.effective_sequence']);
 assert.equal(fields['watches.total'],'u64:1');assert.equal(fields['watches.truncated'],'bool:false');
 fields=Object.fromEntries(host('unwatch',['u64:1']).fields);assert.equal(fields['watch.generation'],'u64:2');assert.equal(fields['watch.effective_sequence'],'u64:3');
});

test('initial attached watch projection failure records a gap and recovers',async()=>{
 const {WatchRegistry,WatchAuthorization,DEFAULT_WATCH_LIMITS}=await import('../dist/debug_watches.js');
 const {projection,DEFAULT_INSPECT_LIMITS}=await import('../dist/debug_inspect.js');
 const {s,p,r}=fixture({deliverInputs:true});
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 const ack=registry.add({selector:{path:[],schemaVersion:1},trigger:{kind:'changed'},enabled:true,evaluationInterval:1n,deliveryInterval:1n,limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:32768},page:{offset:0,limit:10}});
 let fail=true;
 p.enableWatches(registry,state=>{if(fail)throw Error('projection failed');return projection({name:'counter',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u64',decimal:String(state)}));});
 assert.equal(registry.health.observationGaps,1n);
 assert.equal(registry.current(ack.watchId),null);
 fail=false;
 assert.equal(p.handle(r({kind:'step',bytes:Uint8Array.of(1)})).error,null);
 assert.deepEqual(registry.current(ack.watchId).kind,{kind:'baseline',reason:'observation_gap'});
 assert.equal(s.state,1);
});

test('attached probes enforce host ownership, boundaries and event preflight',()=>{
 const {s,p,r}=fixture({configureDiagnostics:true},{maxResponseFields:32});
 const hub=new DiagnosticHub();const sink=hub.addSink({sites:['site'],paths:[[]]},{records:10,bytes:65536,policy:'newest'});hub.registerProducer(3n,7n);
 const profile={id:2n,label:'markers',subscription:{sink,site:'site',kind:'marker',path:[],trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'info'}};
 p.attachProbes(hub,[profile],[sink]);
 const send=(name,args)=>p.handle(r({kind:'host',name,arguments:args}));
 assert.equal(Object.fromEntries(p.handle(r({kind:'status'})).fields)['capability.probe_configuration'],'bool:true');
 assert.equal(send('probe_configure',['u64:0','u64:2']).error,null);assert.deepEqual(hub.pendingProducers(),[3n]);
 assert.equal(send('probe_configure',['u64:0','u64:2']).error,'stale_configuration');assert.equal(send('probe_configure',['u64:1','u64:99']).error,'unauthorized');
 hub.acknowledge(3n,1n,1n);const scope=hub.beginTurn(3n,1n,1n,'simulation');scope.marker('site','info');
 assert.equal(send('probe_events',[`u64:${sink}`,'u64:1','bool:false']).error,'busy');scope.finish(true);
 const polled=send('probe_events',[`u64:${sink}`,'u64:1','bool:false']);assert.equal(polled.error,null);
 assert.deepEqual(polled.fields.filter(([key])=>key.startsWith('probes.events.0.')).map(([key])=>key),[
  'probes.events.0.schema_version','probes.events.0.run','probes.events.0.producer','probes.events.0.epoch','probes.events.0.producer_sequence','probes.events.0.capture_revision','probes.events.0.transition','probes.events.0.origin','probes.events.0.severity','probes.events.0.kind','probes.events.0.baseline','probes.events.0.baseline_reason','probes.events.0.change_unknown','probes.events.0.incomplete_turn','probes.events.0.metadata_omitted','probes.events.0.payload'
 ]);assert.equal(hub.queued(sink).records,0);
 hub.withTurn(3n,1n,2n,'simulation',scope=>{scope.marker('site','info');scope.marker('site','info');});
 assert.equal(send('probe_events',[`u64:${sink}`,'u64:2','bool:true']).error,'limit');assert.equal(hub.queued(sink).records,2);
 assert.equal(send('probe_events',['u64:999','u64:1','bool:false']).error,'unauthorized');
});

test('probe configuration truncates producer list without acknowledging producers',()=>{
 const {p,r}=fixture({configureDiagnostics:true},{maxResponseFields:32});
 const hub=new DiagnosticHub();const sink=hub.addSink({sites:['site'],paths:[[]]},{records:1,bytes:65536,policy:'newest'});
 for(let i=0n;i<40n;i++)hub.registerProducer(i,1n);
 const profile={id:1n,label:'marker',subscription:{sink,site:'site',kind:'marker',path:[],trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'info'}};
 p.attachProbes(hub,[profile],[sink]);
 const response=p.handle(r({kind:'host',name:'probe_configure',arguments:['u64:0','u64:1']}));
 const fields=Object.fromEntries(response.fields);
 assert.equal(response.error,null);assert.equal(fields['probes.pending_producers.total'],'u64:40');assert.equal(fields['probes.pending_producers.truncated'],'bool:true');assert.equal(hub.pendingProducers().length,40);
});

test('probe attachment bounds and owns host profile data before callbacks',()=>{
 const {p}=fixture({configureDiagnostics:true},{maxCandidateBytes:512});
 const hub=new DiagnosticHub();const sink=hub.addSink({sites:['site'],paths:[[]]},{records:1,bytes:4096,policy:'newest'});
 let invoked=0;const hostile={id:1n,subscription:{}};Object.defineProperty(hostile,'label',{enumerable:true,get(){invoked++;throw Error('must not execute');}});
 assert.throws(()=>p.attachProbes(hub,[hostile],[sink]),/invalid_input/);assert.equal(invoked,0);
 assert.throws(()=>p.attachProbes(hub,[{id:1n,label:'x',subscription:{site:'x'.repeat(10000)}}],[sink]),/limit/);
 assert.doesNotThrow(()=>p.attachProbes(hub,[],[sink]));
});

test('probe attachment snapshots exposed sinks without invoking host iterators or accessors',()=>{
 const {p,r}=fixture({configureDiagnostics:true});const hub=new DiagnosticHub();
 const sink=hub.addSink({sites:['site'],paths:[[]]},{records:1,bytes:4096,policy:'newest'});
 let invoked=0;const accessor=[];Object.defineProperty(accessor,0,{enumerable:true,get(){invoked++;return sink;}});accessor.length=1;
 assert.throws(()=>p.attachProbes(hub,[],accessor),/invalid_input/);assert.equal(invoked,0);
 assert.throws(()=>p.attachProbes(hub,[],Array(1)),/invalid_input/);
 const sinks=[sink];sinks[Symbol.iterator]=function*(){invoked++;yield 999n;};
 assert.doesNotThrow(()=>p.attachProbes(hub,[],sinks));sinks[0]=999n;assert.equal(invoked,0);
 const response=p.handle(r({kind:'host',name:'probe_status',arguments:['u64:0','u64:1']}));
 assert.equal(response.error,null);assert.equal(Object.fromEntries(response.fields)['probes.sinks.0.id'],`u64:${sink}`);
});

test('watch status returns whole rows within field, page, and frame budgets',async()=>{
 const {WatchRegistry,WatchAuthorization,DEFAULT_WATCH_LIMITS}=await import('../dist/debug_watches.js');
 const {projection}=await import('../dist/debug_inspect.js');
 function setup(limits){const s=new DebugSession('w',model,{kind:'unrestricted',label:'test'},{maxTurns:10n,maxContinue:10,maxCandidates:10,maxBreakpoints:0});const p=new ProtocolSession(s,b=>b[0],{configureWatches:true},limits);const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());p.enableWatches(registry,state=>projection({name:'counter',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u64',decimal:String(state)})));let id=0n;const host=(name,args=[])=>p.handle({version:1,session:s.id,epoch:p.epoch,requestId:++id,revision:s.revision,configuration:p.configuration,command:{kind:'host',name,arguments:args}});for(let i=0;i<20;i++)assert.equal(host('watch',['u64:1','u64:0']).error,null);return {p,host};}
 const assertPrefix=(response,count)=>{assert.equal(response.error,null);const fields=Object.fromEntries(response.fields);assert.equal(fields['watches.total'],'u64:20');assert.equal(fields['watches.truncated'],'bool:true');assert.equal(response.fields.length,5+count*5);assert.equal(fields['watches.0.has_baseline'],count?'bool:false':undefined);assert.equal(fields[`watches.${count}.id`],undefined);};
 assertPrefix(setup({maxResponseFields:32}).host('watch_status'),5);
 assertPrefix(setup({maxPageSize:1}).host('watch_status'),1);
 const framed=setup({maxFrame:2048}).host('watch_status');const count=(framed.fields.length-5)/5;assert.ok(count>0&&count<20);assertPrefix(framed,count);
});

test('protocol encoding checks frame and field budgets before expansion',()=>{
 const f=fixture(),limits={...DEFAULT_PROTOCOL_LIMITS,maxFrame:256,maxField:100000,maxResponseFields:1000};
 const response={version:1,session:f.s.id,epoch:1n,requestId:1n,revision:0n,configuration:0n,error:null,fields:[['large','é'.repeat(10000)]]};
 const original=TextEncoder.prototype.encode;let largest=0;
 TextEncoder.prototype.encode=function(value){largest=Math.max(largest,value.length);return original.call(this,value);};
 try {
  assert.throws(()=>encodeResponse(response,limits),/limit/);
  assert.ok(largest<256,`unexpected oversized encoding: ${largest}`);
  assert.throws(()=>encodeResponse({...response,fields:Array.from({length:200},()=>['',''])},limits),/limit/);
  assert.throws(()=>encodeRequest(f.r({kind:'step',bytes:new Uint8Array(10000)}),limits),/limit/);
  const args=new Array(10000).fill('x');args[Symbol.iterator]=()=>{throw new Error('argument iterator used');};
  assert.throws(()=>encodeRequest(f.r({kind:'unsupported',name:'example',arguments:args}),limits),/limit/);
  const smallArgs=['x'];smallArgs[Symbol.iterator]=()=>{throw new Error('argument iterator used');};
  assert.doesNotThrow(()=>encodeRequest(f.r({kind:'unsupported',name:'example',arguments:smallArgs}),limits));
  const bytes=Uint8Array.of(1);bytes[Symbol.iterator]=()=>{throw new Error('byte iterator used');};
  assert.doesNotThrow(()=>encodeRequest(f.r({kind:'step',bytes}),limits));
  const fields=[['a','b']];fields.flatMap=()=>{throw new Error('flatMap used');};
  assert.doesNotThrow(()=>encodeResponse({...response,fields},limits));
  assert.throws(()=>encodeResponse({...response,fields:[['a','x'.repeat(500)],['b','\ud800']]},limits),/malformed/);
 } finally { TextEncoder.prototype.encode=original; }
});
