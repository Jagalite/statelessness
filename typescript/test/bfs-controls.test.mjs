import test from 'node:test';
import assert from 'node:assert/strict';
import {enumerateStates,enumerateStatesObserved,accepted,check,ModelError,ConfigError,WithOracle,Paired,metadata} from '../dist/index.js';
const cfg=(x={})=>({maxStates:100,maxTransitions:100,maxDepth:10,...x});
const base=(x={})=>({initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>s<2?[0,1]:[],step:(s,i)=>accepted(s+i),checkState:()=>[],equalStates:(a,b)=>a===b,hashState:()=>0,estimatedStateBytes:()=>5n,...x});
test('BFS estimates exact fit, initial and successor rejection; state cap precedes estimate',()=>{
 const m=base(),r=enumerateStates(m,cfg());assert.equal(r.termination,'graph_exhausted');assert.equal(r.states,3);assert.equal(r.transitions,4);assert.equal(r.estimated_retained_state_bytes,15n);
 assert.equal(enumerateStates(m,cfg(),{maxEstimatedStateBytes:15n}).termination,'graph_exhausted');
 const cap=enumerateStates(m,cfg(),{maxEstimatedStateBytes:14n});assert.equal(cap.termination,'estimated_state_byte_limit');assert.equal(cap.states,2);assert.equal(cap.transitions,4);assert.equal(cap.estimated_retained_state_bytes,10n);
 const initial=enumerateStates(m,cfg(),{maxEstimatedStateBytes:4n});assert.equal(initial.states,0);assert.equal(initial.estimated_retained_state_bytes,0n);
 let estimates=0;const state=enumerateStates(base({estimatedStateBytes:()=>{estimates++;return 5n;}}),cfg({maxStates:1}),{maxEstimatedStateBytes:5n});assert.equal(state.termination,'state_limit');assert.equal(estimates,1);
});
test('BFS unknown/overflow estimates and duplicate suppression',()=>{
 for(const hook of [undefined,()=>null]){const m=base({estimatedStateBytes:hook});assert.equal(enumerateStates(m,cfg()).estimated_retained_state_bytes,null);assert.throws(()=>enumerateStates(m,cfg(),{maxEstimatedStateBytes:100n}),/requires estimated_state_bytes/);}
 const m=base({estimatedStateBytes:s=>s===0?((1n<<64n)-1n):1n});assert.equal(enumerateStates(m,cfg()).estimated_retained_state_bytes,null);const capped=enumerateStates(m,cfg(),{maxEstimatedStateBytes:(1n<<64n)-1n});assert.equal(capped.termination,'estimated_state_byte_limit');assert.equal(capped.states,1);
 const calls=[];enumerateStates(base({estimatedStateBytes:s=>{calls.push(s);return 0n;}}),cfg(),{maxEstimatedStateBytes:0n});assert.deepEqual(calls,[0,1,2]);
 for(const n of [-1n,1n<<64n,1,undefined])assert.throws(()=>enumerateStates(base({estimatedStateBytes:()=>n}),cfg()),ModelError);
 for(const n of [-1n,1n<<64n,1])assert.throws(()=>enumerateStates(base(),cfg(),{maxEstimatedStateBytes:n}),ConfigError);
});
test('BFS pre-start stop has zero callbacks, cancellation priority and no clock absent deadline',()=>{
 let called=0;const m=base({initialState:()=>{called++;return 0;}});
 for(const control of [{cancellation:()=>true},{maxDurationMs:0},{cancellation:()=>true,maxDurationMs:0}]){const r=enumerateStates(m,cfg(),{control});assert.equal(r.states,0);assert.equal(r.transitions,0);assert.equal(r.estimated_retained_state_bytes,null);assert.equal(r.termination,control.cancellation?'cancelled':'deadline');}assert.equal(called,0);
 const old=globalThis.performance;Object.defineProperty(globalThis,'performance',{configurable:true,value:{now(){throw Error('clock accessed');}}});try{assert.equal(enumerateStates(base(),cfg()).termination,'graph_exhausted');}finally{Object.defineProperty(globalThis,'performance',{configurable:true,value:old});}
});
test('BFS polls after iterator creation/advancement, hashing, equality and estimates',()=>{
 for(const target of ['estimate','hash','inputs','next','equal']){
  let cancel=false,steps=0,checks=0;const m=base({estimatedStateBytes:()=>{if(target==='estimate')cancel=true;return 5n;},hashState:()=>{if(target==='hash')cancel=true;return 0;},inputs:()=>{if(target==='inputs')cancel=true;return {[Symbol.iterator](){return {next(){if(target==='next')cancel=true;return {done:false,value:0};}};}};},equalStates:(a,b)=>{if(target==='equal')cancel=true;return a===b;},step:(s,i)=>{steps++;return accepted(s+i);},checkState:()=>{checks++;return [];}});
  const r=enumerateStates(m,cfg(),{control:{cancellation:()=>cancel}});assert.equal(r.termination,'cancelled');assert.equal(steps,target==='equal'?1:0);assert.equal(checks,target==='equal'?2:1);
 }
});
test('BFS completed checking failure and checker error beat simultaneous cancellation',()=>{
 let cancel=false,edge=0,observed=0;const m=base({inputs:()=>[1],step:s=>{cancel=true;return accepted(s+1);},checkState:s=>s?[check('bad','failed','bad')]:[],checkTransition:()=>{edge++;return [];}});
 const r=enumerateStates(m,cfg(),{control:{cancellation:()=>cancel}},{transition(){observed++;}});assert.equal(r.termination,'failure_found');assert.equal(edge,1);assert.equal(observed,1);assert.deepEqual(r.failure.inputs,[1]);
 cancel=false;assert.throws(()=>enumerateStates({...m,checkState:s=>{if(s)throw Error('primary');return []; }},cfg(),{control:{cancellation:()=>cancel}}),/primary/);
 let estimates=0;assert.equal(enumerateStates(base({checkState:()=>[check('bad','failed','bad')],estimatedStateBytes:()=>{estimates++;return 1n;}}),cfg()).termination,'failure_found');assert.equal(estimates,0);
});
test('BFS single exhaustion probes and latest-first hash collision comparisons',()=>{
 for(const [config,reason]of [[cfg({maxDepth:0}),'depth_bound'],[cfg({maxTransitions:0}),'transition_limit']]){let pulls=0;const m=base({inputs:function*(){pulls++;yield 1;pulls++;yield 2;}});assert.equal(enumerateStates(m,config).termination,reason);assert.equal(pulls,1);}
 assert.equal(enumerateStates(base({inputs:()=>[]}),cfg({maxTransitions:0})).termination,'graph_exhausted');
 const pairs=[];enumerateStates(base({inputs:s=>s===0?[1,2,3]:[],equalStates:(a,b)=>{pairs.push([a,b]);return a===b;}}),cfg());assert.deepEqual(pairs,[[0,1],[1,2],[0,2],[2,3],[1,3],[0,3]]);
});
test('BFS observes duplicate and failing edges with detached rooted history',()=>{
 const events=[];const m=base({initialState:()=>({n:0}),cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),inputs:s=>s.n<2?[{n:0},{n:1}]:[],step:(s,i)=>accepted({n:s.n+i.n},[{n:7}]),equalStates:(a,b)=>a.n===b.n,checkState:s=>s.n===2?[check('bad','failed','bad')]:[]});
 const r=enumerateStatesObserved(m,cfg(),{needsHistory:()=>true,initial(s){s.n=99;},transition(before,i,t,sc,ec,h){events.push([before.n,i.n,t.state.n,h.map(i=>i.n)]);before.n=99;i.n=99;t.state.n=99;t.outputs[0].n=99;h[0].n=99;if(sc[0])sc[0].id='changed';}});
 assert.equal(r.termination,'failure_found');assert.deepEqual(r.failure.inputs,[{n:1},{n:1}]);assert.equal(r.failure.violations[0].check.id,'bad');assert.deepEqual(events,[[0,0,0,[0]],[0,1,1,[1]],[1,0,1,[1,0]],[1,1,2,[1,1]]]);
 const lengths=[];enumerateStates(base(),cfg(),{},{transition(_b,_i,_t,_sc,_ec,h){lengths.push(h.length);}});assert.ok(lengths.every(n=>n===0));
});
test('BFS check_error partial batches retain primary checker errors',()=>{
 for(const kind of ['initial','state','transition']){const batches=[];const m=base({inputs:()=>[1],checkState:function*(s){yield check('state');if(kind==='initial'||kind==='state'&&s)throw Error('primary state');},checkTransition:function*(){yield check('edge');if(kind==='transition')throw Error('primary edge');}});
 assert.throws(()=>enumerateStates(m,cfg(),{},{checkError(phase,checks,complete){batches.push([phase,checks.map(c=>c.id),complete]);throw Error('secondary');}}),kind==='transition'?/transition check: primary edge; observer check_error: secondary; observer check_error: secondary/:/primary state; observer check_error: secondary/);
 assert.deepEqual(batches,kind==='initial'?[['initial_state',['state'],false]]:kind==='state'?[['state',['state'],false]]:[['state',['state'],true],['transition',['edge'],false]]);}
});
test('Oracle estimate overhead explicit, checked, optional; Paired delegates reference estimate',()=>{
 const model=base(),oracle={metadata:()=>metadata('oracle'),initialState:()=>0,advance:h=>h,checkState:()=>[],cloneHistory:h=>h,equalHistory:(a,b)=>a===b,estimatedStateBytes:()=>7n},state={model:0,oracle:0};
 assert.equal(new WithOracle(model,oracle).estimatedStateBytes(state),null);assert.equal(new WithOracle(model,oracle,0n).estimatedStateBytes(state),12n);assert.equal(new WithOracle(model,oracle,3n).estimatedStateBytes(state),15n);assert.equal(new WithOracle(model,oracle,(1n<<64n)-1n).estimatedStateBytes(state),null);
 const projection={metadata:()=>metadata('projection'),actualState:s=>s,referenceState:s=>s,actualEffect:o=>o,referenceEffect:o=>o,equalState:(a,b)=>a===b,equalEffect:(a,b)=>a===b};assert.equal(new Paired(model,projection).estimatedStateBytes(0),5n);assert.equal(new Paired({...model,estimatedStateBytes:undefined},projection).estimatedStateBytes(0),null);
 for(const fn of [()=>new WithOracle({...model,estimatedStateBytes:()=>undefined},oracle,0n).estimatedStateBytes(state),()=>new WithOracle(model,{...oracle,estimatedStateBytes:()=>undefined},0n).estimatedStateBytes(state),()=>new Paired({...model,estimatedStateBytes:()=>undefined},projection).estimatedStateBytes(0)])assert.throws(fn,ModelError);
});
test('BFS fake-clock deadline triggered inside a turn respects complete check boundary',()=>{
 const old=globalThis.performance;
 for(const fail of [false,true]){
  let now=0,edge=0,observed=0;Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>now}});
  try{
   const m=base({inputs:()=>[1],step:s=>{now=2;return accepted(s+1);},checkState:s=>fail&&s?[check('bad','failed','broken')]:[],checkTransition:()=>{edge++;return [];}});
   const r=enumerateStates(m,cfg(),{control:{maxDurationMs:1}},{transition(){observed++;}});
   assert.equal(r.termination,fail?'failure_found':'deadline');assert.equal(r.transitions,1);assert.equal(edge,1);assert.equal(observed,1);
  }finally{Object.defineProperty(globalThis,'performance',{configurable:true,value:old});}
 }
});
test('wide BFS accepts exact full-u64 transition budgets and returns bigint counters',async()=>{
 const {enumerateStatesExact}=await import('../dist/index.js');
 for(const budget of [0n,BigInt(Number.MAX_SAFE_INTEGER)+1n,(1n<<64n)-1n]){const r=enumerateStatesExact(base({inputs:()=>[],checkState:()=>[check('skip','skipped','not run')]}),{maxStates:10,maxTransitions:budget,maxDepth:10});assert.equal(r.termination,'graph_exhausted');assert.equal(r.transitions,0n);assert.equal(r.skipped_checks,1n);}
 const r=enumerateStatesExact(base(),{maxStates:10,maxTransitions:1n,maxDepth:10});assert.equal(r.termination,'transition_limit');assert.equal(r.transitions,1n);
 for(const budget of [-1n,1n<<64n,Number.MAX_SAFE_INTEGER])assert.throws(()=>enumerateStatesExact(base(),{maxStates:10,maxTransitions:budget,maxDepth:10}),ConfigError);
});
test('shared skipped-check accounting saturates at u64 maximum',async()=>{
 const {addSkippedChecks}=await import('../dist/search_support.js');const max=(1n<<64n)-1n;
 assert.equal(addSkippedChecks(0n,0n),0n);assert.equal(addSkippedChecks(max-1n,1n),max);assert.equal(addSkippedChecks(max-1n,2n),max);assert.equal(addSkippedChecks(max,1n),max);
});
test('BFS captures validated config fields once and ignores callback budget mutation',async()=>{
 const {enumerateStatesExact}=await import('../dist/index.js');
 for(const wide of [false,true]){
  const reads={maxStates:0,maxTransitions:0,maxDepth:0},config={};
  for(const [key,value]of Object.entries({maxStates:10,maxTransitions:wide?0n:0,maxDepth:10}))Object.defineProperty(config,key,{get(){reads[key]++;if(reads[key]!==1)throw Error('config reread');return value;}});
  const r=(wide?enumerateStatesExact:enumerateStates)(base(),config);assert.equal(r.termination,'transition_limit');assert.equal(r.transitions,wide?0n:0);assert.deepEqual(reads,{maxStates:1,maxTransitions:1,maxDepth:1});
  const mutable={maxStates:10,maxTransitions:wide?0n:0,maxDepth:10};
  const m=base({initialState(){mutable.maxStates=1;mutable.maxTransitions=wide?100n:100;mutable.maxDepth=0;return 0;}});
  assert.equal((wide?enumerateStatesExact:enumerateStates)(m,mutable).termination,'transition_limit');
 }
 let reads=0;const unsafeGetter={maxStates:10,maxDepth:10,get maxTransitions(){return ++reads===1?0:2**53;}};
 assert.equal(enumerateStates(base(),unsafeGetter).transitions,0);assert.equal(reads,1);
});
test('run controls snapshot duration/callback once, preserve receiver and keep external flags live',()=>{
 const old=globalThis.performance;Object.defineProperty(globalThis,'performance',{configurable:true,value:{now(){throw Error('unexpected clock');}}});
 try{
  let durationReads=0,cancelReads=0,calls=0;
  const control={get maxDurationMs(){durationReads++;return durationReads===1?undefined:0;},get cancellation(){cancelReads++;return function(){assert.equal(this,control);calls++;return false;};}};
  assert.equal(enumerateStates(base(),cfg(),{control}).termination,'graph_exhausted');assert.equal(durationReads,1);assert.equal(cancelReads,1);assert.ok(calls>1);
  const mutable={maxDurationMs:undefined,cancellation(){assert.equal(this,mutable);mutable.maxDurationMs=0;mutable.cancellation=()=>true;return false;}};
  assert.equal(enumerateStates(base(),cfg(),{control:mutable}).termination,'graph_exhausted');
  let flag=false;const live={cancellation:()=>flag};const r=enumerateStates(base({inputs(){flag=true;return [1];}}),cfg(),{control:live});assert.equal(r.termination,'cancelled');
 }finally{Object.defineProperty(globalThis,'performance',{configurable:true,value:old});}
 let now=0;Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>now}});
 try{
  const control={maxDurationMs:1,cancellation(){this.maxDurationMs=100;return false;}};
  const r=enumerateStates(base({step(s,i){now=2;return accepted(s+i);}}),cfg(),{control});assert.equal(r.termination,'deadline');assert.equal(r.transitions,1);
 }finally{Object.defineProperty(globalThis,'performance',{configurable:true,value:old});}
});
