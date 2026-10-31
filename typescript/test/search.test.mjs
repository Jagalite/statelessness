import test from 'node:test';
import assert from 'node:assert/strict';
import { accepted,check,Rng,automatic,sampleEnumerated,containsEnumerated,NO_INPUT,record,replay,enumerateStates,metadata,fuzz,shrink,ModelError } from '../dist/index.js';
const base=()=>({initialState:()=>0,step:(s,i)=>accepted(s+i),checkState:s=>[check('limit',s>=5?'failed':'passed',s>=5?'too high':'')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalInputs:(a,b)=>a===b,generate:(_s,r)=>Number(r.index(3n))+1,simplerInputs:i=>i>1?[1,i-1]:[],isEnabled:(_s,i)=>i>0});
const config=(overrides={})=>({seed:17n,cases:20,maxSteps:8,maxTransitions:100n,mutationPercent:50,...overrides});
test('BFS retains enumerated input before reducer mutates its source',()=>{
 const query={n:1}, seen=[];
 const m={...base(),initialState:()=>0,inputs:s=>s===0?[query]:[],cloneInput:i=>({...i}),
  step:(s,i)=>{query.n=99;return accepted(s+i.n);},
  checkState:()=>[],checkTransition:(_s,i)=>{seen.push(i.n);return [check('edge','failed','stop')];},
  equalInputs:(a,b)=>a.n===b.n};
 const result=enumerateStates(m);
 assert.equal(result.termination,'failure_found');assert.deepEqual(seen,[1]);assert.equal(result.failure.inputs[0].n,1);
});
test('BFS retains initial and successor nodes across check callbacks',()=>{
 let source; const visited=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{source=s;return {...s};},
  inputs:s=>s.n<2?[1]:[],step:(s,i)=>accepted({n:s.n+i}),
  checkState:s=>{visited.push(s.n);source.n=99;return [];},
  equalStates:(a,b)=>a.n===b.n,hashState:s=>s.n};
 const result=enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:3});
 assert.equal(result.termination,'graph_exhausted');assert.deepEqual(visited,[0,1,2]);
});
test('BFS checks the initial snapshot before an observer getter mutates its source',()=>{
 let source; const seen=[], observed=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{source=s;return {...s};},inputs:()=>[],
  checkState:s=>{seen.push(s.n);return [];},equalStates:(a,b)=>a.n===b.n,hashState:s=>s.n};
 const observer={get initial(){source.n=99;return s=>observed.push(s.n);}};
 const result=enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:2},{},observer);
 assert.equal(result.termination,'graph_exhausted');assert.deepEqual(seen,[0]);assert.deepEqual(observed,[0]);
});
test('BFS captures check edge before observer history callback',()=>{
 let latest; const seen=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{latest=s;return {...s};},
  inputs:s=>s.n===0?[1]:[],step:(s,i)=>accepted({n:s.n+i}),
  checkState:s=>{seen.push(s.n);return [];},equalStates:(a,b)=>a.n===b.n,hashState:s=>s.n};
 const result=enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:2},{},{needsHistory:()=>{latest.n=99;return false;}});
 assert.equal(result.termination,'graph_exhausted');assert.deepEqual(seen,[0,1]);
});
test('BFS pins transition consumers before observer history callback',()=>{
 const seen=[];
 const m={...base(),inputs:s=>s===0?[1]:[],step:(s,i)=>accepted(s+i),checkState:()=>[],
  checkTransition:()=>{seen.push('check');return [];},equalStates:(a,b)=>a===b};
 const observer={transition:()=>seen.push('observer'),needsHistory:()=>{m.checkTransition=()=>{throw Error('replaced check');};observer.transition=()=>{throw Error('replaced observer');};return false;}};
 assert.equal(enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:2},{},observer).termination,'graph_exhausted');
 assert.deepEqual(seen,['check','observer']);
});
test('BFS captures successor before transition observer getter mutates it',()=>{
 let successor; const checked=[],observed=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>({...s}),inputs:s=>s.n===0?[1]:[],
  step:()=>accepted(successor={n:1}),checkState:s=>{checked.push(s.n);return [];},
  equalStates:(a,b)=>a.n===b.n,hashState:s=>s.n};
 const observer={get transition(){successor.n=99;return (_s,_i,t)=>observed.push(t.state.n);}};
 assert.equal(enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:2},{},observer).termination,'graph_exhausted');
 assert.deepEqual(checked,[0,1]);assert.deepEqual(observed,[1]);
});
test('fuzz retains admitted input when reducer mutates its argument source',()=>{
 let source; const seen=[];
 const m={...base(),cloneInput:i=>{source=i;return {...i};},generate:()=>({n:1}),
  isEnabled:()=>true,step:(s,i)=>{source.n=99;return accepted(s+i.n);},
  checkState:()=>[],checkTransition:(_s,i)=>{seen.push(i.n);return [check('edge','failed','stop')];},
  equalInputs:(a,b)=>a.n===b.n};
 const result=fuzz(m,config({cases:1,maxSteps:1,maxTransitions:1n}));
 assert.equal(result.termination,'failure_found');assert.deepEqual(seen,[1]);assert.equal(result.failure.inputs[0].n,1);
});
test('fuzz admission uses the captured generated input',()=>{
 let copies=0;const seen=[];
 const m={...base(),cloneInput:i=>{const v={...i};if(++copies===2)i.n=99;return v;},
  generate:()=>({n:1}),isEnabled:(_s,i)=>{seen.push(i.n);return true;},
  step:s=>accepted(s),checkState:()=>[],equalInputs:(a,b)=>a.n===b.n};
 fuzz(m,config({cases:1,maxSteps:1,maxTransitions:1n}));
 assert.deepEqual(seen,[1]);
});
test('fuzz retains initial state before state checks mutate an alias',()=>{
 let source;const seen=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{source=s;return {...s};},
  cloneInput:i=>i,generate:()=>1,checkState:s=>{if(s.n===0)source.n=99;return [];},
  step:(s,i)=>{seen.push(s.n);return accepted({n:s.n+i});}};
 const result=fuzz(m,config({cases:1,maxSteps:1,maxTransitions:1n}));
 assert.equal(result.transitions,1n);assert.deepEqual(seen,[0]);
});
test('fuzz initial checks precede continuation-copy alias mutation',()=>{
 let calls=0, source; const seen=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{calls++; if(calls===1) { source={...s}; return source; } if(calls===3) source.n=99; return {...s};},
  checkState:s=>{seen.push(s.n);return [];},generate:()=>NO_INPUT};
 fuzz(m,config({cases:1,maxSteps:1,maxTransitions:1n}));
 assert.deepEqual(seen,[0]);
});
test('fuzz retains execution state across enabled callback alias mutation',()=>{
 let stateSource;const seen=[];
 const m={...base(),initialState:()=>({n:0}),cloneState:s=>{stateSource=s;return {...s};},
  generate:()=>1,isEnabled:()=>{stateSource.n=99;return true;},checkState:()=>[],
  step:(s,i)=>{seen.push(s.n);return accepted({n:s.n+i});}};
 fuzz(m,config({cases:1,maxSteps:1,maxTransitions:1n}));
 assert.deepEqual(seen,[0]);
});
test('BFS reports a retained input copy failure before the reducer',()=>{
 let copies=0,steps=0;
 const m={...base(),inputs:s=>s===0?[1]:[],cloneInput:i=>{if(++copies===2)throw Error('second input copy');return i;},
  checkState:()=>[],step:s=>{steps++;return accepted(s+1);}};
 assert.throws(()=>enumerateStates(m),/second input copy/);
 assert.equal(copies,2);assert.equal(steps,0);
});
test('shrink enabled check captures input before state clone callbacks',()=>{
 let lastInput,armed=false;const seen=[];
 const m={...base(),initialState:()=>0,cloneInput:i=>{lastInput=i;return {...i};},cloneState:s=>{if(armed&&lastInput)lastInput.n=99;return s;},
  checkState:s=>{armed=true;return s===1?[check('target','failed','bad')]:[];},isEnabled:(_s,i)=>{seen.push(i.n);return true;},
  step:(s,_i)=>accepted(s+1),simplerInputs:()=>[],equalInputs:(a,b)=>a.n===b.n};
 shrink(m,{inputs:[{n:1}],violations:[{phase:'state',check:check('target','failed','bad')}]});
 assert.equal(seen[0],1);
});
test('shrink preserves replay input for reducer, checks, and witness across state clone callbacks',()=>{
 let armed=false, lastSource; const steps=[],checks=[];
 const m={...base(),initialState:()=>0,
  cloneInput:i=>{lastSource=i;return {...i};},
  cloneState:s=>{if(armed&&lastSource)lastSource.n=99;return s;},
  checkState:s=>{armed=true;return [];},
  checkTransition:(_s,i)=>{checks.push(i.n);return [check('target','failed','bad')];},
  isEnabled:()=>true,step:(s,i)=>{steps.push(i.n);return accepted(s+1);},
  simplerInputs:()=>[],equalInputs:(a,b)=>a.n===b.n};
 const result=shrink(m,{inputs:[{n:1}],violations:[{phase:'transition',check:check('target','failed','bad')}]},{maxAttempts:1});
 assert.equal(steps[0],1);assert.equal(checks[0],1);assert.equal(result.minimized.inputs[0].n,1);
});
test('shrink captures later candidate inputs before initial and step callbacks',()=>{
 const sources=[], seen=[];
 const m={...base(),cloneInput:i=>{if(i.n===2)sources.push(i);return {...i};},
  initialState:()=>{for(const source of sources)source.n=99;return 0;},
  checkState:()=>[],isEnabled:()=>true,step:(s,i)=>{seen.push(i.n);return accepted(s+1);},
  checkTransition:(s)=>s===1?[check('target','failed','bad')]:[],simplerInputs:()=>[],equalInputs:(a,b)=>a.n===b.n};
 const result=shrink(m,{inputs:[{n:1},{n:2}],violations:[{phase:'transition',check:check('target','failed','bad')}]},{maxAttempts:1});
 assert.deepEqual(seen,[1,2]);assert.deepEqual(result.minimized.inputs.map(i=>i.n),[1,2]);
});
test('shrink replay retains candidate length when initial callback resizes it',()=>{
 for(const grow of [false,true]){
  const originalSlice=Array.prototype.slice;let replayInputs;const seen=[];
  Array.prototype.slice=function(...args){
   if(this.length===2 && this[0]?.marker==='length-control' && this[1]?.n===2)replayInputs=this;
   return originalSlice.apply(this,args);
  };
  try{
   const m={...base(),cloneInput:i=>({...i}),initialState:()=>{
    assert.ok(replayInputs);if(grow)replayInputs.push({n:3});else replayInputs.length=0;return 0;
   },checkState:()=>[],isEnabled:()=>true,step:(s,i)=>{seen.push(i.n);return accepted(s+1);},
    checkTransition:s=>!grow&&s===1?[check('target','failed','bad')]:[],simplerInputs:()=>[],equalInputs:(a,b)=>a.n===b.n};
   const failure={inputs:[{n:1,marker:'length-control'},{n:2}],violations:[{phase:'transition',check:check('target','failed','bad')}]};
   if(grow)assert.throws(()=>shrink(m,failure,{maxAttempts:1}),/does not reproduce/);
   else {const result=shrink(m,failure,{maxAttempts:1});assert.deepEqual(result.minimized.inputs.map(i=>i.n),[1,2]);}
   assert.deepEqual(seen,[1,2]);
  }finally{Array.prototype.slice=originalSlice;}
 }
});
test('shrink defers an unused transition input copy past state checks',()=>{
 for (const stateThrows of [false,true]) {
  let armed=false,copies=0;
  const m={...base(),initialState:()=>0,cloneInput:i=>{if(armed&&++copies===4)throw Error('unused observation copy');return i;},
   checkState:s=>{if(s===0){armed=true;return [];}if(stateThrows)throw Error('state sentinel');return [check('target','failed','bad')];},
   isEnabled:()=>true,step:s=>accepted(s+1),simplerInputs:()=>[],checkTransition:undefined};
  const failure={inputs:[1],violations:[{phase:'state',check:check('target','failed','bad')}]};
  if(stateThrows) assert.throws(()=>shrink(m,failure,{maxAttempts:1}),/state sentinel/);
  else assert.equal(shrink(m,failure,{maxAttempts:1}).validated_original,true);
 }
});
test('shrink defers unused replay copies when enabled rejects a candidate',()=>{
 let armed=false,copies=0;
 const m={...base(),initialState:()=>0,cloneInput:i=>{if(armed&&++copies===2)throw Error('unused witness copy');return i;},
  checkState:()=>{armed=true;return [];},isEnabled:()=>false,step:s=>accepted(s+1)};
 const failure={inputs:[1],violations:[{phase:'state',check:check('target','failed','bad')}]};
 assert.throws(()=>shrink(m,failure,{maxAttempts:1}),/does not reproduce with valid causality/);
});
test('fuzz enabled check captures input before state clone callbacks',()=>{
 let lastInput;const seen=[];
 const m={...base(),initialState:()=>0,cloneInput:i=>{lastInput=i;return {...i};},cloneState:s=>{if(lastInput)lastInput.n=99;return s;},
  generate:()=>({n:1}),checkState:()=>[],isEnabled:(_s,i)=>{seen.push(i.n);return true;},step:(s,_i)=>accepted(s+1),equalInputs:(a,b)=>a.n===b.n};
 fuzz(m,config({cases:1,maxSteps:1}));
 assert.equal(seen[0],1);
});
test('automatic reservoir consumes no random draw for first, uses full domain and rolls back errors',()=>{
 const m={...base(),inputs:()=>[10,20,30],equalStates:(a,b)=>a===b};const rng=new Rng(9n),reference=new Rng(9n);let wanted=10;if(reference.index(2n)===0n)wanted=20;if(reference.index(3n)===0n)wanted=30;assert.equal(sampleEnumerated(m,0,rng),wanted);assert.equal(rng.nextU64(),reference.nextU64());
 for(const inputs of [()=>[1,2,3],function*(){yield 1;yield 2;throw Error('iterator failed');}]){const r=new Rng(4n),prior=r.clone();assert.throws(()=>sampleEnumerated({...m,inputs},0,r,{maxCandidates:2}),ModelError);assert.equal(r.nextU64(),prior.nextU64());}
 const empty=new Rng(8n),prior=empty.clone();assert.equal(sampleEnumerated({...m,inputs:()=>[]},0,empty),NO_INPUT);assert.equal(empty.nextU64(),prior.nextU64());
 assert.equal(containsEnumerated(m,0,10,{maxCandidates:1}),true);assert.throws(()=>containsEnumerated(m,0,30,{maxCandidates:1}),/exceeds/);
});
test('automatic enumeration closes early iterators and preserves primary failures',()=>{
 let closed=0;const m={...base(),inputs:function*(){try{yield 1;yield 2;yield 3;}finally{closed++;}}};
 assert.equal(containsEnumerated(m,0,1),true);assert.equal(closed,1);
 const rng=new Rng(13n),before=rng.clone();assert.throws(()=>sampleEnumerated(m,0,rng,{maxCandidates:1}),/exceeds/);assert.equal(closed,2);assert.equal(rng.nextU64(),before.nextU64());
 const broken={...base(),inputs:()=>({[Symbol.iterator](){return {next(){throw Error('primary failure');},return(){throw Error('cleanup failure');}};}})};
 assert.throws(()=>sampleEnumerated(broken,0,new Rng()),/primary failure/);
 assert.throws(()=>containsEnumerated(broken,0,1),/primary failure/);
 const cleanup={...base(),inputs:()=>({[Symbol.iterator](){return {next(){return {done:false,value:1};},return(){throw Error('cleanup failure');}};}})};
 assert.throws(()=>containsEnumerated(cleanup,0,1),/cleanup failure/);
});
test('automatic membership snapshots query before domain callbacks and defers copy errors',()=>{
 const query={value:7};
 const m={...base(),cloneInput:i=>({...i}),cloneState:s=>{query.value=9;return s;},inputs:()=>[{value:7}],equalInputs:(a,b)=>a.value===b.value};
 assert.equal(containsEnumerated(m,0,query),true);
 const changing={...base(),cloneInput:i=>({...i}),equalInputs:(a,b)=>a.value===b.value,inputs(){this.cloneInput=()=>{throw Error('replaced clone');};this.equalInputs=()=>false;return [{value:3}];}};
 assert.equal(containsEnumerated(changing,0,{value:3}),true);
 const broken={...m,cloneState:s=>s,cloneInput:()=>{throw Error('copy failure');}};
 assert.equal(containsEnumerated({...broken,inputs:()=>[]},0,{value:1}),false);
 assert.throws(()=>containsEnumerated({...broken,inputs:()=>{throw Error('domain failure');}},0,{value:1}),/domain failure/);
 assert.throws(()=>containsEnumerated({...broken,inputs:()=>[{value:1}]},0,{value:1}),/copy failure/);
});
test('automatic scans maxCandidates plus one and preserves receiver',()=>{
 let pulled=0;const m={...base(),inputs:function*(){for(let i=0;i<100;i++){pulled++;yield i;}},equalStates:(a,b)=>a===b};assert.throws(()=>sampleEnumerated(m,0,new Rng(),{maxCandidates:3}),/exceeds/);assert.equal(pulled,4);
 class Finite {#items=[2];initialState(){return 0;}step(s,i){return accepted(s+i);}checkState(){return [];}cloneState(s){return s;}cloneInput(i){return i;}cloneOutput(o){return o;}equalInputs(a,b){return a===b;}equalStates(a,b){return a===b;}inputs(){return this.#items;}}
 assert.equal(automatic(new Finite()).generate(0,new Rng()),2);
});
test('seeded fuzz is deterministic, checks initial failures and rejects disabled generation',()=>{
 const first=fuzz(base(),config()),second=fuzz(base(),config());assert.deepEqual(first,second);assert.equal(first.termination,'failure_found');assert.ok(first.failure.inputs.reduce((a,b)=>a+b,0)>=5);assert.equal(first.transitions,BigInt(first.failure.inputs.length));
 const initial=fuzz({...base(),initialState:()=>5},config());assert.deepEqual(initial.failure.inputs,[]);assert.equal(initial.transitions,0n);
 assert.throws(()=>fuzz({...base(),isEnabled:()=>false},config()),/disabled/);
});
test('fuzz generation is state aware; cancellation, deadline and transition bounds are explicit',()=>{
 const seen=[];const m={...base(),checkState:()=>[],generate:s=>{seen.push(s);return 1;}};const r=fuzz(m,config({cases:2,maxSteps:3,maxTransitions:4n,mutationPercent:0}));assert.equal(r.termination,'transition_limit');assert.equal(r.transitions,4n);assert.deepEqual(seen,[0,1,2,0]);
 assert.equal(fuzz(m,config(),{cancellation:()=>true}).cases,0);assert.equal(fuzz(m,config(),{maxDurationMs:0}).termination,'deadline');
 let cancel=false;const during=fuzz({...m,generate:()=>{cancel=true;return 1;}},config(),{cancellation:()=>cancel});assert.equal(during.termination,'cancelled');assert.equal(during.transitions,0n);
});
test('shrink deletes chunks then simplifies, with explicit validation and budgets',()=>{
 const m=base(),original={inputs:[1,3,3],violations:[{phase:'state',check:check('limit','failed','too high')}]};const r=shrink(m,original);assert.equal(r.validated_original,true);assert.equal(r.termination,'search_complete');assert.deepEqual(r.minimized.inputs,[2,3]);assert.equal(r.history[0].operation.kind,'validate_original');assert.equal(r.attempts,r.history.length);assert.ok(r.history.some(a=>a.operation.kind==='simplify'));
 const budget=shrink(m,original,{maxAttempts:20},{maxReplayedTransitions:1n});assert.equal(budget.validated_original,false);assert.equal(budget.termination,'transition_limit');assert.deepEqual(budget.minimized,original);assert.equal(budget.replayed_transitions,1n);
 const attempt=shrink(m,original,{maxAttempts:1});assert.equal(attempt.termination,'attempt_limit');assert.equal(attempt.attempts,1);
 assert.throws(()=>shrink(m,{...original,inputs:[1]}),/does not reproduce/);
});
test('shrink preserves target phase and id, causality and terminates cyclic simplification',()=>{
 const target={phase:'transition',check:check('target','failed','bad')};const m={...base(),checkState:()=>[],checkTransition:(_s,i)=>i>=2?[check('target','failed','bad')]:[],simplerInputs:i=>i===2?[3]:[2]};const r=shrink(m,{inputs:[2],violations:[target]});assert.equal(r.termination,'search_complete');assert.ok(r.attempts<10);assert.equal(r.minimized.violations[0].phase,'transition');
 const wrong={...m,checkState:()=>[check('target','failed','bad')]};assert.throws(()=>shrink(wrong,{inputs:[2],violations:[target]}),/target check and phase/);
 const causal={...base(),isEnabled:(s,i)=>s>0||i===1};const result=shrink(causal,{inputs:[1,4],violations:[{phase:'state',check:check('limit','failed','bad')}]});assert.ok(result.history.some(a=>a.outcome==='invalid_causality'));
});
test('shrink closes lazy simplifiers after retention, cancellation, and errors',()=>{
 const failure={inputs:[5],violations:[{phase:'state',check:check('limit','failed','too high')}]};let opened=0,closed=0;
 const m={...base(),simplerInputs:function*(value){opened++;try{if(value===5)yield 6;}finally{closed++;}}};
 const result=shrink(m,failure);assert.deepEqual(result.minimized.inputs,[6]);assert.equal(opened,closed);
 let cancel=false;const cancelled={...base(),simplerInputs:function*(){try{cancel=true;yield 6;}finally{closed++;}}};const before=closed;
 const interrupted=shrink(cancelled,failure,undefined,{cancellation:()=>cancel});assert.equal(interrupted.termination,'cancelled');assert.equal(closed,before+1);
 const broken={...base(),simplerInputs:()=>({[Symbol.iterator](){return {next(){throw Error('primary simplifier error');},return(){throw Error('cleanup error');}};}})};
 assert.throws(()=>shrink(broken,failure),/primary simplifier error/);
 let stop=false;
 const badClose={...base(),simplerInputs:()=>({[Symbol.iterator](){return {next(){stop=true;return {done:false,value:6};},return(){throw Error('cleanup error');}};}})};
 assert.throws(()=>shrink(badClose,failure,undefined,{cancellation:()=>stop}),e=>e instanceof ModelError && /simpler_inputs close: cleanup error/.test(e.message));
 stop=false;const revoked=Proxy.revocable({},{});revoked.revoke();
 const hostileClose={...base(),simplerInputs:()=>({[Symbol.iterator](){return {next(){stop=true;return {done:false,value:6};},return(){throw revoked.proxy;}};}})};
 assert.throws(()=>shrink(hostileClose,failure,undefined,{cancellation:()=>stop}),e=>e instanceof ModelError && /simpler_inputs close: unknown error/.test(e.message));
 stop=false;const odd=new Error();odd.message=Symbol('hostile');
 const oddClose={...base(),simplerInputs:()=>({[Symbol.iterator](){return {next(){stop=true;return {done:false,value:6};},return(){throw odd;}};}})};
 assert.throws(()=>shrink(oddClose,failure,undefined,{cancellation:()=>stop}),e=>e instanceof ModelError && /simpler_inputs close: unknown error/.test(e.message));
});
test('shrink bounds repeated known hints and closes the lazy simplifier',()=>{
 const failure={inputs:[5],violations:[{phase:'state',check:check('limit','failed','too high')}]};let closed=0;
 const finite={...base(),simplerInputs:function*(v){yield v;yield v;yield v;}};
 const looping={...base(),simplerInputs:function*(v){try{while(true)yield v;}finally{closed++;}}};
 const a=shrink(finite,failure,{maxAttempts:3}),b=shrink(looping,failure,{maxAttempts:3});
 assert.equal(a.termination,'search_complete');assert.equal(b.termination,'attempt_limit');assert.equal(closed,1);
 assert.equal(a.attempts,2);assert.equal(b.attempts,a.attempts);assert.deepEqual(b.history,a.history);
});
test('skipped hint bound is per iterator not per run',()=>{
 // Mirrors Rust tests/exploration.rs skipped_hint_bound_is_per_iterator_not_per_run.
 const m={initialState:()=>[],step:(s,i)=>accepted([...s,i]),checkState:s=>s.length>=6?[check('long','failed','long')]:[],cloneState:s=>[...s],cloneInput:i=>i,cloneOutput:o=>o,equalInputs:(a,b)=>a===b,generate:()=>1,isEnabled:()=>true,simplerInputs:i=>i===0?Array(10).fill(0):[0]};
 const failure=fuzz(m,config()).failure;assert.ok(failure);
 const r=shrink(m,failure,{maxAttempts:150});
 assert.equal(r.termination,'search_complete');assert.ok(Number(r.attempts)<150);
 assert.ok(r.minimized.inputs.every(i=>i===0));
});
test('snapshots isolate state/input callbacks and errors remain errors',()=>{
 const m={initialState:()=>({n:0}),cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),equalInputs:(a,b)=>a.n===b.n,generate:s=>{s.n=999;return {n:1};},isEnabled:(s,i)=>{s.n=777;i.n=888;return true;},step:(s,i)=>{s.n+=i.n;i.n=999;return accepted(s);},checkState:s=>[check('bound',s.n===2?'failed':'passed',s.n===2?'bad':'')]};const r=fuzz(m,config());assert.deepEqual(r.failure.inputs,[{n:1},{n:1}]);
 assert.throws(()=>fuzz({...base(),generate:()=>{throw Error('generator exploded');}},config()),/generate: generator exploded/);
});
test('mutation consumes the specified deletion/replacement draws and replaces without insertion',()=>{
 // Each fresh input records its draw; callbacks also reveal reused input positions.
 const reference=new Rng(37n),expected=[];let previous=[];const generation=[];
 for(let c=0;c<3;c++){
   let candidate=[],replacement=null;
   if(previous.length&&reference.index(100n)<100n){candidate=[...previous];const start=Number(reference.index(BigInt(candidate.length))),length=1+Number(reference.index(BigInt(candidate.length-start)));candidate.splice(start,length);replacement=Number(reference.index(BigInt(candidate.length+1)));}
   const values=[];for(let n=0;n<5;n++){if(n!==replacement&&n<candidate.length)values.push(candidate[n]);else{const value=reference.nextU64();values.push(value);generation.push(value);}}expected.push(values);previous=values;
 }
 const actual=[],draws=[];let values;
 const m={...base(),initialState:()=>{values=[];actual.push(values);return 0;},generate:(_s,r)=>{const v=r.nextU64();draws.push(v);return v;},step:(s,i)=>{values.push(i);return accepted(s+1);},checkState:()=>[],isEnabled:()=>true};
 assert.equal(fuzz(m,config({seed:37n,cases:3,maxSteps:5,mutationPercent:100})).termination,'cases_completed');assert.deepEqual(actual,expected);assert.deepEqual(draws,generation);
});
test('a disabled mutation reuse falls back to generation against current state',()=>{
 let cases=0,generated=0;const m={...base(),initialState:()=>{cases++;return 0;},checkState:()=>[],generate:s=>{generated++;return s+cases*10;},isEnabled:(s,i)=>i===s+cases*10,step:s=>accepted(s+1)};
 const r=fuzz(m,config({seed:0n,cases:3,maxSteps:5,mutationPercent:100}));assert.equal(r.transitions,15n);assert.equal(generated,15);
});
test('nonboolean control/equality results fail explicitly',()=>{
 const m={...base(),inputs:()=>[1],equalStates:(a,b)=>a===b};
 assert.throws(()=>fuzz(base(),config(),{cancellation:()=>1}),/cancellation must return boolean/);
 assert.throws(()=>containsEnumerated({...m,inputs:()=>[1],equalInputs:()=>1},0,1),/equalInputs must return boolean/);
});

test('null and undefined inputs are distinct from explicit exhaustion',()=>{
 for(const value of [null,undefined]){
  const m={...base(),inputs:()=>[value],isEnabled:(_s,i)=>i===value,equalStates:(a,b)=>a===b,step:s=>accepted(s+1),checkState:s=>s===1?[check('nullable','failed','bad')]:[]};
  assert.equal(sampleEnumerated(m,0,new Rng()),value);assert.equal(containsEnumerated(m,0,value),true);
  for(const gen of [automatic(m),{...m,generate:()=>value}]){const r=fuzz(gen,config());assert.equal(r.transitions,1n);assert.deepEqual(r.failure.inputs,[value]);}
 }
 assert.equal(fuzz({...base(),generate:()=>NO_INPUT},config()).transitions,0n);
});
test('fuzz observers receive initial and actual failing prefixes without mutating search',()=>{
 const observed=[],m={...base(),initialState:()=>({n:0}),cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),generate:()=>({n:1}),isEnabled:()=>true,step:(s,i)=>accepted({n:s.n+i.n},[{n:2}]),checkState:s=>s.n===2?[check('bad','failed','bad')]:[]};
 const r=fuzz(m,config(),{}, {initial:(s,checks)=>{observed.push(['initial',s.n,checks.length]);s.n=100;},transition:(before,i,t,sc,ec,history)=>{observed.push(['edge',before.n,t.state.n,history.map(i=>i.n),sc.map(c=>c.id)]);before.n=500;i.n=500;t.state.n=500;t.outputs[0].n=500;history[0].n=500;if(sc.length)sc[0].id='changed';}});
 assert.deepEqual(observed,[['initial',0,0],['edge',0,1,[1],[]],['edge',1,2,[1,1],['bad']]]);assert.deepEqual(r.failure.inputs,[{n:1},{n:1}]);assert.equal(r.failure.violations[0].check.id,'bad');
});
test('observer errors and partial checker failures are explicit and phase ordered',()=>{
 const errors=[],observer={checkError:(phase,checks,complete)=>errors.push([phase,checks.map(c=>c.id),complete])};
 const initial={...base(),checkState:function*(){yield check('initial');throw Error('state exploded');}};assert.throws(()=>fuzz(initial,config(),{},observer),/state exploded/);assert.deepEqual(errors,[['initial_state',['initial'],false]]);
 errors.length=0;const edge={...base(),checkState:s=>s===0?[]:[check('state')],checkTransition:function*(){yield check('partial');throw Error('edge exploded');}};assert.throws(()=>fuzz(edge,config(),{},observer),/edge exploded/);assert.deepEqual(errors,[['state',['state'],true],['transition',['partial'],false]]);
 errors.length=0;const stateError={...base(),checkState:function*(s){if(s){yield check('state-partial');throw Error('state edge exploded');}}};assert.throws(()=>fuzz(stateError,config(),{},observer),/state edge exploded/);assert.deepEqual(errors,[['state',['state-partial'],false]]);
 assert.throws(()=>fuzz(base(),config(),{},{initial:()=>{throw Error('observer exploded');}}),/observer initial: observer exploded/);
});
test('fuzz reports an initial check snapshot failure to the observer',()=>{
 let copies=0; const notices=[];
 const m={...base(),cloneState:s=>{if(++copies===2)throw Error('check copy failed');return s;}};
 assert.throws(()=>fuzz(m,config(),{},{checkError:(phase,checks,complete)=>notices.push([phase,checks,complete])}),/check copy failed/);
 assert.deepEqual(notices,[['initial_state',[],false]]);
});
test('automatic preserves enumeration and codec capabilities with private class receivers',()=>{
 class Counter {
  #name='automatic-codec';#domain=[1];
  initialState(){return 0;}step(s,i){return accepted(Math.min(2,s+i));}checkState(){return [];}cloneState(s){return s;}cloneInput(i){return i;}cloneOutput(o){return o;}
  inputs(){return this.#domain;}equalInputs(a,b){return a===b;}equalStates(a,b){return a===b;}hashState(s){return `${this.#name}:${s}`;}
  metadata(){return metadata(this.#name,'test');}encodeState(s){return new Uint8Array([s]);}decodeState(b){return b[0];}encodeInput(i){return new Uint8Array([i]);}decodeInput(b){return b[0];}encodeOutput(o){return new Uint8Array([o]);}
 }
 const wrapped=automatic(new Counter());assert.equal(enumerateStates(wrapped).termination,'graph_exhausted');const trace=record(wrapped,[1,1]);assert.equal(trace.metadata.name,'automatic-codec');assert.equal(replay(wrapped,trace).outcome,'exact');
});
test('checker error remains primary when checkError observer also throws',()=>{
 const m={...base(),checkState:function*(){yield check('partial');throw Error('primary checker');}};
 assert.throws(()=>fuzz(m,config(),{},{checkError:()=>{throw Error('secondary observer');}}),/initial check_state: primary checker; observer check_error: secondary observer/);
});
test('completed property failure wins cancellation; callback errors beat simultaneous stop',()=>{
 let cancel=false;const r=fuzz({...base(),checkState:s=>{if(s){cancel=true;return [check('bad','failed','bad')];}return [];}},config(),{cancellation:()=>cancel});assert.equal(r.termination,'failure_found');
 cancel=false;assert.throws(()=>fuzz({...base(),generate:()=>{cancel=true;throw Error('generation failed');}},config(),{cancellation:()=>cancel}),/generation failed/);
});
test('automatic supports frozen models with own nonconfigurable functions',()=>{
 const m=Object.freeze({...base(),inputs:()=>[1],equalStates:(a,b)=>a===b});const gen=automatic(m);assert.equal(gen.initialState(),0);assert.equal(gen.generate(0,new Rng()),1);assert.equal(fuzz(gen,config()).termination,'failure_found');
});
test('automatic generator overrides stay on the facade and are honored',()=>{
 const m={...base(),inputs:()=>[1],equalStates:(a,b)=>a===b},original=m.generate;const gen=automatic(m);gen.generate=()=>9;gen.isEnabled=()=>true;assert.equal(gen.generate(0,new Rng()),9);assert.equal(m.generate,original);assert.equal(fuzz(gen,config()).failure.inputs[0],9);
});
test('fuzz, shrink and automatic sampling snapshot validated budget fields once',()=>{
 const oneRead=values=>{const reads={},config={};for(const [key,value]of Object.entries(values)){reads[key]=0;Object.defineProperty(config,key,{get(){assert.equal(++reads[key],1,`${key} reread`);return value;}});}return config;};
 const m={...base(),checkState:()=>[],inputs:()=>[1,2],equalStates:(a,b)=>a===b};
 const fuzzConfig=oneRead(config({cases:1,maxSteps:1,maxTransitions:1n}));assert.equal(fuzz(m,fuzzConfig).transitions,1n);
 const opts=()=>oneRead({maxCandidates:2});assert.ok([1,2].includes(sampleEnumerated(m,0,new Rng(),opts())));assert.equal(containsEnumerated(m,0,2,opts()),true);assert.ok([1,2].includes(automatic(m,opts()).generate(0,new Rng())));
 const original={inputs:[3,3],violations:[{phase:'state',check:check('limit','failed','too high')}]};
 const limited=shrink(base(),original,oneRead({maxAttempts:1}),oneRead({maxReplayedTransitions:10n}));assert.equal(limited.termination,'attempt_limit');assert.equal(limited.attempts,1);
 const mutable=config({cases:1,maxSteps:1,maxTransitions:1n});const f=fuzz({...m,initialState(){mutable.cases=0;mutable.maxSteps=0;mutable.maxTransitions=0n;return 0;}},mutable);assert.equal(f.transitions,1n);
 const domainOptions={maxCandidates:1};const domain={...m,inputs(){domainOptions.maxCandidates=100;return [1,2];}};assert.throws(()=>sampleEnumerated(domain,0,new Rng(),domainOptions),/exceeds max_candidates/);
 const attempts={maxAttempts:1},replays={maxReplayedTransitions:10n};const sh=shrink({...base(),initialState(){attempts.maxAttempts=100;replays.maxReplayedTransitions=0n;return 0;}},original,attempts,replays);assert.equal(sh.termination,'attempt_limit');assert.equal(sh.attempts,1);
});

test('initial observer retains state before checks and defers snapshot errors',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 const original={n:1};let seen,reads=0;const observer={};
 Object.defineProperty(observer,'initial',{get(){reads++;return function(value){assert.equal(this,observer);seen=value.n;};}});
 const m={...base(),cloneState:s=>({...s}),checkState(){original.n=2;this.cloneState=()=>{throw Error('replacement clone');};return [];}};
 searchChecks(m,original,undefined,undefined,undefined,observer);
 assert.equal(seen,1);assert.equal(reads,1);
 let copies=0;const bad={...base(),cloneState(s){if(++copies===1)throw Error('copy failed');return s;},checkState(){throw Error('state first');}};
 assert.throws(()=>searchChecks(bad,1,undefined,undefined,undefined,{initial(){}}),/state first/);
 let cloned=0;const absent={...base(),cloneState:s=>{cloned++;return s;},checkState:()=>[]};
 searchChecks(absent,1);assert.equal(cloned,1);
});

test('search edge snapshots precede state-check alias mutation', async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 const before={n:1}, query={n:2}, after={n:3}, output={n:4}, history=[{n:5}];
 const seen=[]; const m={...base(), cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),
  checkState:()=>{before.n=11;query.n=12;after.n=13;output.n=14;history[0].n=15;return [];},
  checkTransition:(b,i,t)=>{seen.push([b.n,i.n,t.state.n,t.outputs[0].n]);return [];}};
 const observer={transition:(b,i,t,_s,_e,h)=>seen.push([b.n,i.n,t.state.n,t.outputs[0].n,h[0].n])};
 searchChecks(m,after,before,query,accepted(after,[output]),observer,history);
 assert.deepEqual(seen,[[1,2,3,4],[1,2,3,4,5]]);
});
test('search edge copy failures retain checker precedence and unused paths',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 const m={...base(),cloneState:s=>s,cloneInput:()=>{throw Error('edge copy');},checkState:()=>{throw Error('state first');},checkTransition:()=>[]};
 assert.throws(()=>searchChecks(m,1,0,2,accepted(1)),/state first/);
 const noEdge={...m,checkState:()=>[],checkTransition:undefined};
 assert.doesNotThrow(()=>searchChecks(noEdge,1,0,2,accepted(1)));
 const calls=[];const observer={transition:()=>{},checkError:(...a)=>calls.push(a)};
 assert.throws(()=>searchChecks(noEdge,1,0,2,accepted(1),observer),/edge copy/);
 assert.equal(calls.length,0);
});

test('search edge hook getters defer failure until their stage',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 for (const failing of ['check','observer']) {
  const errors=[]; const m={...base(),checkState:()=>{throw Error('state first');}};
  const observer={checkError:(phase,_checks,complete)=>errors.push([phase,complete])};
  if(failing==='check') Object.defineProperty(m,'checkTransitionInto',{get(){throw Error('hook getter');}});
  else Object.defineProperty(observer,'transition',{get(){throw Error('observer getter');}});
  assert.throws(()=>searchChecks(m,1,0,2,accepted(1),observer),/state first/);
  assert.deepEqual(errors,[['state',false]]);
 }
 const notices=[];const m={...base(),checkState:()=>[]};
 Object.defineProperty(m,'checkTransitionInto',{get(){throw Error('hook getter');}});
 assert.throws(()=>searchChecks(m,1,0,2,accepted(1),{checkError:(phase,_checks,complete)=>notices.push([phase,complete])}),/hook getter/);
 assert.deepEqual(notices,[['state',true],['transition',false]]);
});

test('search edge pins a one-shot transition hook and observer receiver',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 let reads=0, calls=0, observed=0;
 const m={...base(),checkState(){ this.checkTransition=()=>{throw Error('replacement');};return []; }};
 Object.defineProperty(m,'checkTransitionInto',{configurable:true,get(){reads++;return reads===1?function(_b,_i,_t,sink){assert.equal(this,m);calls++;sink.push(check('original'));}:undefined;}});
 const observer={transition(){assert.equal(this,observer);observed++;}};
 searchChecks(m,1,0,2,accepted(1),observer);
 assert.equal(reads,1);assert.equal(calls,1);assert.equal(observed,1);
});

test('search edge validates hook before clone failures and pins snapshot callbacks',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 const malformed={...base(),checkState:()=>[],checkTransitionInto:42,cloneInput:()=>{throw Error('copy should not win');}};
 assert.throws(()=>searchChecks(malformed,1,0,2,accepted(1)),/append check hook must be a function/);
 const seen=[];const m={...base(),checkState:()=>[],checkTransition:(b,i,t)=>{seen.push([b,i,t.state,t.outputs[0]]);return [];},cloneState:s=>s,cloneOutput:o=>o};
 m.cloneInput=i=>{m.cloneState=()=>99;m.cloneOutput=()=>99;return i;};
 searchChecks(m,3,1,2,accepted(3,[4]));
 assert.deepEqual(seen,[[1,2,3,4]]);
});

test('search edge reads the output clone callback only once',async()=>{
 const {searchChecks}=await import('../dist/search_support.js');
 let reads=0, seen;
 const m={...base(),checkState:()=>[],checkTransition:(_b,_i,t)=>{seen=t.outputs[0];return [];}};
 Object.defineProperty(m,'cloneOutput',{get(){reads++;return reads===1?(o=>o):undefined;}});
 searchChecks(m,1,0,2,accepted(1,[7]));
 assert.equal(reads,1);assert.equal(seen,7);
});

