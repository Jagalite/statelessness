import test from 'node:test';
import assert from 'node:assert/strict';
import {CheckSink,check,accepted,metadata,checkObserved,checkObservedInto,enumerateStates,fuzz,shrink,record,replay,WithOracle,Paired,automatic,ModelError,Rng} from '../dist/index.js';
const config={maxStates:100,maxTransitions:100,maxDepth:10};
const fuzzConfig={seed:0n,cases:1,maxSteps:10,maxTransitions:100n,mutationPercent:0};
function model(extra={}){return {
 initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalStates:(a,b)=>a===b,equalInputs:(a,b)=>a===b,inputs:()=>[1],generate:()=>1,isEnabled:()=>true,simplerInputs:()=>[],step:(s,i)=>accepted(s+i),
 checkState(){throw Error('legacy state called');},checkTransition(){throw Error('legacy edge called');},
 checkStateInto(s,sink){sink.push(check('state'));if(s>=2)sink.push(check('bad','failed','broken'));},
 checkTransitionInto(_s,_i,_t,sink){sink.push(check('edge'));},
 metadata:()=>metadata('append','test'),encodeState:s=>Uint8Array.of(s),decodeState:b=>b[0],encodeInput:i=>Uint8Array.of(i),decodeInput:b=>b[0],encodeOutput:o=>Uint8Array.of(o),...extra
};}
const ids=checks=>checks.map(c=>c.id);
test('all native engine paths prefer append hooks exactly once over throwing legacy methods',()=>{
 const m=model();assert.deepEqual(ids(checkObserved(m,0,1,accepted(1),1)),['state','edge']);
 const bfs=enumerateStates(m,config),f=fuzz(m,fuzzConfig);assert.deepEqual(bfs.failure.inputs,[1,1]);assert.deepEqual(f.failure.inputs,[1,1]);
 assert.deepEqual(shrink(m,bfs.failure).minimized.inputs,[1,1]);const trace=record(m,[1,1]);assert.equal(trace.termination,'property_failed');assert.deepEqual(ids(trace.initialChecks),['state']);assert.deepEqual(ids(trace.steps[0].checks),['state','edge']);assert.deepEqual(ids(trace.steps[1].checks),['state','bad','edge']);assert.equal(replay(m,trace).outcome,'exact');
});
test('sink stores detached validated checks, immutable snapshots and logical caller-buffer reuse',()=>{
 const buffer=[check('stale')],counts=[],source={id:'new',status:'passed',details:''};let saved;
 const m=model({checkStateInto(_s,sink){saved=sink;sink.reserve(4);sink.push(source);source.id='mutated';const snap=sink.snapshot();assert.ok(Object.isFrozen(snap));assert.ok(Object.isFrozen(snap[0]));assert.throws(()=>{snap[0].id='bad';});counts.push(sink.length);},checkTransitionInto(_s,_i,_t,sink){counts.push(sink.length);sink.extend([check('edge'),check('edge')]);}});
 checkObservedInto(m,0,1,accepted(1),1,buffer);assert.deepEqual(ids(buffer),['new','edge','edge']);assert.deepEqual(counts,[1,1]);
 for(const operation of [()=>saved.push(check('late')),()=>saved.extend([]),()=>saved.length,()=>saved.snapshot(),()=>saved.reserve(0)])assert.throws(operation,/lease expired/);
 checkObservedInto(model(),0,1,accepted(1),2,buffer,{stateEvery:3,transitionChecks:false});assert.deepEqual(ids(buffer),['stateless.state_checks','stateless.transition_checks']);assert.throws(()=>saved.push(check('late')),/lease expired/);assert.deepEqual(ids(buffer),['stateless.state_checks','stateless.transition_checks']);
 assert.throws(()=>new CheckSink(),/issued by checker dispatch/);
});
test('observedInto clears entry and every failure, including config/snapshot/throwundefined and malformed late append',()=>{
 const buffer=[check('old')];let saved;
 for(const m of [model({cloneState(){throw Error('snapshot');}}),model({checkStateInto(_s,sink){saved=sink;sink.push(check('prefix'));throw undefined;}}),model({checkTransitionInto(_s,_i,_t,sink){saved=sink;sink.push(check('prefix'));sink.push({id:'invalid',status:'invalid',details:''});}})]){
  buffer.push(check('old'));assert.throws(()=>checkObservedInto(m,0,1,accepted(1),1,buffer));assert.deepEqual(buffer,[]);if(saved)assert.throws(()=>saved.snapshot(),/lease expired/);
 }
 buffer.push(check('old'));assert.throws(()=>checkObservedInto(model(),0,1,accepted(1),0,buffer));assert.deepEqual(buffer,[]);
});
test('validation errors are sticky even when caught and no expired lease can poison another batch',()=>{
 let old;const m=model({checkStateInto(_s,sink){old=sink;sink.push(check('prefix'));try{sink.push({id:'bad',status:'oops',details:''});}catch{}assert.throws(()=>sink.length,/lease expired/);}});
 const events=[];assert.throws(()=>enumerateStates(m,config,{},{checkError(phase,cs,complete){events.push([phase,ids(cs),complete]);}}),/invalid check/);assert.deepEqual(events,[['initial_state',['prefix'],false]]);
 const fresh=model({checkStateInto(_s,sink){assert.throws(()=>old.push(check('late')),/lease expired/);sink.push(check('fresh'));}});assert.deepEqual(ids(checkObserved(fresh,0,1,accepted(1),1)),['fresh','edge']);
 for(const operation of [sink=>sink.extend([check('ok'),{id:'bad',status:'oops',details:''}]),sink=>sink.reserve(-1)]){const output=[];assert.throws(()=>checkObservedInto(model({checkStateInto(_s,sink){try{operation(sink);}catch{}}}),0,1,accepted(1),1,output));assert.deepEqual(output,[]);}
});
test('search retains state and partial transition batches while diagnostics cannot replace checker error',()=>{
 const events=[];let escaped;const m=model({checkTransitionInto(_s,_i,_t,sink){escaped=sink;sink.push(check('partial'));throw Error('primary edge');}});
 assert.throws(()=>enumerateStates(m,config,{},{checkError(phase,cs,complete){events.push([phase,ids(cs),complete]);throw Error('secondary');}}),/primary edge.*secondary.*secondary/);
 assert.deepEqual(events,[['state',['state'],true],['transition',['partial'],false]]);assert.throws(()=>escaped.length,/lease expired/);
});
test('nested oracle leases expire independently, see full preceding prefix and preserve actual-oracle order',()=>{
 let actualLease,oracleLease;const order=[];const m=model({checkStateInto(_s,sink){actualLease=sink;sink.push(check('actual'));}});
 const oracle={metadata:()=>metadata('oracle'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState(){throw Error('legacy oracle');},checkStateInto(_h,_s,sink){oracleLease=sink;assert.throws(()=>actualLease.length,/lease expired/);order.push(ids(sink.snapshot()));sink.push(check('oracle'));}};
 const wrapped=new WithOracle(m,oracle);const output=checkObserved(wrapped,{model:0,oracle:0},1,accepted({model:1,oracle:0}),1);assert.deepEqual(ids(output),['actual','oracle','edge']);assert.deepEqual(order,[['actual']]);assert.throws(()=>oracleLease.snapshot(),/lease expired/);
 const outer=model({checkStateInto(s,sink){wrapped.checkStateInto({model:s,oracle:0},sink);assert.throws(()=>oracleLease.push(check('late')),/lease expired/);sink.push(check('outer'));}});assert.deepEqual(ids(checkObserved(outer,0,1,accepted(1),1)),['actual','oracle','outer','edge']);
});
test('nested invalid child cannot be swallowed into successful outer batch; expired old child is isolated',()=>{
 const inner=model({checkStateInto(_s,sink){sink.push(check('child'));try{sink.push({id:'bad',status:'bad',details:''});}catch{}}});
 const oracle={metadata:()=>metadata('oracle'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState:()=>[]};const wrapped=new WithOracle(inner,oracle);
 const outer=model({checkStateInto(s,sink){try{wrapped.checkStateInto({model:s,oracle:0},sink);}catch{}sink.push(check('outer'));}});
 const output=[];assert.throws(()=>checkObservedInto(outer,0,1,accepted(1),1,output),/invalid check/);assert.deepEqual(output,[]);
});
test('Paired forwards reference append hooks before projection checks',()=>{
 let saved;const reference=model({checkStateInto(_s,sink){saved=sink;sink.push(check('reference'));},checkTransitionInto(_s,_i,_t,sink){sink.push(check('reference.edge'));}});
 const projection={metadata:()=>metadata('projection'),actualState:s=>s,referenceState:s=>s,actualEffect:o=>o,referenceEffect:o=>o,equalState:(a,b)=>a===b,equalEffect:(a,b)=>a===b};const pair=new Paired(reference,projection);
 assert.deepEqual(ids(Array.from(pair.checkState(1,1))),['reference','pair.state']);assert.throws(()=>saved.length,/lease expired/);
 const wrapped=new WithOracle(model(),pair);const checks=checkObserved(wrapped,{model:0,oracle:0},1,accepted({model:1,oracle:1}),1);assert.deepEqual(ids(checks),['state','reference','pair.state','edge','reference.edge','pair.effects','pair.outcome']);
});
test('automatic forwards append capabilities from frozen models with private method receivers',()=>{
 class Private {#n=0;initialState(){return 0;}cloneState(s){return s;}cloneInput(i){return i;}cloneOutput(o){return o;}inputs(){return [1];}equalInputs(a,b){return a===b;}equalStates(a,b){return a===b;}step(s,i){return accepted(s+i);}checkState(){throw Error('legacy');}checkStateInto(s,sink){this.#n++;sink.push(check('private',s>=1?'failed':'passed',s>=1?'bad':''));}}
 const original=Object.freeze(new Private()),m=automatic(original);assert.deepEqual(enumerateStates(m,config).failure.inputs,[1]);assert.equal(fuzz(m,fuzzConfig).termination,'failure_found');
});
test('append hooks must synchronously return undefined, including promises and thenables',async()=>{
 let escaped,returned;
 for(const hook of [(_s,sink)=>{escaped=sink;return 1;},(_s,sink)=>{escaped=sink;return {then(){}};},async(_s,sink)=>{escaped=sink;await Promise.resolve();sink.push(check('late'));}]){
  const output=[];assert.throws(()=>checkObservedInto(model({checkStateInto(s,sink){returned=hook(s,sink);return returned;}}),0,1,accepted(1),1,output),/undefined synchronously/);assert.deepEqual(output,[]);assert.throws(()=>escaped.length,/lease expired/);if(returned instanceof Promise)await returned.catch(()=>{});
 }
});
test('nested malformed append freezes the valid prefix before any caught outer continuation',()=>{
 const inner=model({checkStateInto(_s,sink){sink.push(check('prefix'));try{sink.push({id:'bad',status:'bad',details:''});}catch{}}});
 const oracle={metadata:()=>metadata('o'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState:()=>[]};const wrapped=new WithOracle(inner,oracle);
 const outer=model({checkStateInto(s,sink){try{wrapped.checkStateInto({model:s,oracle:0},sink);}catch{}try{sink.push(check('after'));}catch{}}});
 const batches=[];assert.throws(()=>enumerateStates(outer,config,{},{checkError(_p,cs){batches.push(ids(cs));}}),/invalid check/);assert.deepEqual(batches,[['prefix']]);
});
test('expired root and nested leases release backing storage even while sink objects survive',async()=>{
 const {spawnSync}=await import('node:child_process');
 const core=new URL('../dist/index.js',import.meta.url).href,dispatch=new URL('../dist/checks.js',import.meta.url).href;
 const source=`import {withCheckSink,check} from ${JSON.stringify(core)};import {appendStateChecks} from ${JSON.stringify(dispatch)};
 let escapedRoot,escapedChild;
 function make(){const output=[check('retained')];const model={cloneState:s=>s,checkState:()=>[],checkStateInto(_s,sink){escapedChild=sink;sink.push(check('child'));}};withCheckSink(output,sink=>{escapedRoot=sink;appendStateChecks(model,0,sink);});return new WeakRef(output);}
 const ref=make();let collected=false;
 for(let i=0;i<50;i++){await new Promise(setImmediate);global.gc();await new Promise(setImmediate);if(ref.deref()===undefined){collected=true;break;}}
 if(!collected)throw Error('escaped sink retained its expired storage');
 for(const sink of [escapedRoot,escapedChild]){let failed=false;try{sink.snapshot();}catch{failed=true;}if(!failed)throw Error('expired sink reopened');}`;
 const result=spawnSync(process.execPath,['--expose-gc','--input-type=module','-e',source],{encoding:'utf8',timeout:20000});assert.equal(result.status,0,result.stderr||String(result.error));
});
test('present noncallable append hooks fail closed instead of falling back to legacy',()=>{
 for(const bad of [false,0,'',null]){
  for(const key of ['checkStateInto','checkTransitionInto']){let legacy=0;const m=model({[key]:bad,checkState(){legacy++;return [];},checkTransition(){legacy++;return [];}});assert.throws(()=>checkObserved(m,0,1,accepted(1),1),/must be a function/);assert.equal(legacy,0);}
  const oracle={metadata:()=>metadata('o'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState:()=>[],checkStateInto:bad};
  assert.throws(()=>checkObserved(new WithOracle(model(),oracle),{model:0,oracle:0},1,accepted({model:1,oracle:0}),1),/must be a function/);
 }
});
