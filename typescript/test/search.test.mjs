import test from 'node:test';
import assert from 'node:assert/strict';
import { accepted,check,Rng,automatic,sampleEnumerated,containsEnumerated,NO_INPUT,record,replay,enumerateStates,metadata,fuzz,shrink,ModelError } from '../dist/index.js';
const base=()=>({initialState:()=>0,step:(s,i)=>accepted(s+i),checkState:s=>[check('limit',s>=5?'failed':'passed',s>=5?'too high':'')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalInputs:(a,b)=>a===b,generate:(_s,r)=>Number(r.index(3n))+1,simplerInputs:i=>i>1?[1,i-1]:[],isEnabled:(_s,i)=>i>0});
const config=(overrides={})=>({seed:17n,cases:20,maxSteps:8,maxTransitions:100n,mutationPercent:50,...overrides});
test('automatic reservoir consumes no random draw for first, uses full domain and rolls back errors',()=>{
 const m={...base(),inputs:()=>[10,20,30],equalStates:(a,b)=>a===b};const rng=new Rng(9n),reference=new Rng(9n);let wanted=10;if(reference.index(2n)===0n)wanted=20;if(reference.index(3n)===0n)wanted=30;assert.equal(sampleEnumerated(m,0,rng),wanted);assert.equal(rng.nextU64(),reference.nextU64());
 for(const inputs of [()=>[1,2,3],function*(){yield 1;yield 2;throw Error('iterator failed');}]){const r=new Rng(4n),prior=r.clone();assert.throws(()=>sampleEnumerated({...m,inputs},0,r,{maxCandidates:2}),ModelError);assert.equal(r.nextU64(),prior.nextU64());}
 const empty=new Rng(8n),prior=empty.clone();assert.equal(sampleEnumerated({...m,inputs:()=>[]},0,empty),NO_INPUT);assert.equal(empty.nextU64(),prior.nextU64());
 assert.equal(containsEnumerated(m,0,10,{maxCandidates:1}),true);assert.throws(()=>containsEnumerated(m,0,30,{maxCandidates:1}),/exceeds/);
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
