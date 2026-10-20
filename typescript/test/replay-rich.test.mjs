import test from 'node:test';
import assert from 'node:assert/strict';
import {accepted,check,metadata,record,automatic} from '../dist/index.js';
import {ReplayError,replayWithObservations,replayWithObservationsBounded} from '../dist/replay.js';
function model(extra={}) {return {initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,metadata:()=>metadata('replay','build'),step:(s,i)=>accepted(s+i,[s]),checkState:()=>[check('state')],checkTransition:()=>[check('edge')],encodeState:s=>Uint8Array.of(s),decodeState:b=>b[0],encodeInput:i=>Uint8Array.of(i),decodeInput:b=>b[0],encodeOutput:o=>Uint8Array.of(o),...extra};}
function caught(f){try{f();assert.fail('expected replay error');}catch(e){assert.ok(e instanceof ReplayError,String(e));return e;}}
test('rich replay reports every difference with disposition priority and stops at first divergence',()=>{
 const source=record(model(),[1,2]);let executions=0;const observations=[];
 const actual=model({step(s,i){executions++;return {state:s+i+1,outputs:[9],disposition:{kind:'rejected',reason:'why'}};},checkState:()=>[check('different')]});
 // Keep initial checks exact, then change the turn batch.
 actual.checkState=s=>[check(s===0?'state':'different')];
 const report=replayWithObservations(actual,source,{},(o,r)=>{observations.push([o,r]);});
 assert.equal(executions,1);assert.equal(report.field,'disposition');assert.equal(report.steps_verified,0);
 assert.deepEqual(observations[1][0].differences,{disposition:true,outputs:true,state:true,checks:true});
 assert.equal(observations[1][0].actual.state_check_count,1);assert.equal(observations[1][0].actual.checks.length,2);
});
test('checker failure retains one executed turn with unknown checks and preserves primary observer error',()=>{
 const source=record(model(),[1]);let calls=0;const observations=[];
 const m=model({step(s,i){calls++;return accepted(s+i,[s]);},checkTransition(){throw Error('checker exploded');}});
 const error=caught(()=>replayWithObservations(m,source,{},o=>{observations.push(o);if(o.kind==='error')throw Error('secondary');}));
 assert.equal(calls,1);assert.match(error.error.message,/checker exploded/);assert.match(error.observer_error.message,/secondary/);
 assert.equal(error.actual.kind,'turn');assert.equal(error.actual.transition.state,1);assert.equal(error.actual.checks,null);assert.equal(error.actual.state_check_count,null);
 assert.equal(observations.at(-1).actual.checks,null);
});
test('normal observer failure retains updated report and does not recursively notify error',()=>{
 const source=record(model(),[1]);const kinds=[];
 const error=caught(()=>replayWithObservations(model(),source,{},o=>{kinds.push(o.kind);if(o.kind==='turn')throw Error('observer failed');}));
 assert.deepEqual(kinds,['initial','turn']);assert.equal(error.report.steps_verified,1);assert.equal(error.actual.transition.state,1);assert.match(error.message,/replay observer/);
});
test('observer mutations are detached from later replay execution and stored evidence',()=>{
 const m={...model(),cloneState:s=>({...s}),initialState:()=>({n:0}),step:(s,i)=>accepted({n:s.n+i},[{n:s.n}]),cloneOutput:o=>({...o}),encodeState:s=>Uint8Array.of(s.n),decodeState:b=>({n:b[0]}),encodeOutput:o=>Uint8Array.of(o.n)};
 const source=record(m,[1,2]);const report=replayWithObservations(m,source,{},o=>{if(o.kind==='initial'){o.state.n=200;o.actual_state[0]=200;}if(o.kind==='turn'){o.actual.transition.state.n=200;o.actual.transition.outputs[0].n=200;o.expected.postState[0]=200;}});
 assert.equal(report.outcome,'exact');assert.equal(report.steps_verified,2);assert.equal(source.steps[0].postState[0],1);
});
test('post reducer snapshot failure transfers actual without reexecution or retry',()=>{
 const source=record(model(),[1]);let calls=0,failedClones=0;const events=[];
 const m=model({step(s,i){calls++;return accepted({n:s+i});},cloneState(s){if(typeof s==='object'){failedClones++;throw Error('cannot snapshot result');}return s;}});
 const error=caught(()=>replayWithObservations(m,source,{},o=>{events.push(o);}));
 assert.equal(calls,1);assert.equal(failedClones,1);assert.deepEqual(error.actual.transition.state,{n:1});assert.ok(error.snapshot_error);
 assert.equal(events.at(-1).actual,null);assert.equal(events.at(-1).actual_available,true);assert.equal(events.at(-1).snapshot_unavailable,true);
});
test('encoding failure retains complete checks and exact state-check boundary',()=>{
 const source=record(model(),[1]);const m=model({encodeOutput(){throw Error('output codec');}});
 const error=caught(()=>replayWithObservations(m,source));assert.equal(error.actual.checks.length,2);assert.equal(error.actual.state_check_count,1);assert.equal(error.actual.transition.state,1);
});
test('identity mismatch wins over malformed bounded artifact and does not decode',()=>{
 let decoded=0;const source={...record(model(),[1]),metadata:metadata('other'),config:{strategy:'test',seed:null,parameters:[]}};
 const m=model({decodeState(){decoded++;throw Error('decode');}});
 assert.equal(replayWithObservationsBounded(m,source,{}, {max_total_bytes:0}).outcome,'incompatible');assert.equal(decoded,0);
});
test('bounded artifact validates total/frame/items before decode while actual expansion retains completed evidence',()=>{
 const trace={...record(model(),[1]),config:{strategy:'test',seed:null,parameters:[]}};let decoded=0;
 const m=model({decodeState(b){decoded++;return b[0];}});
 caught(()=>replayWithObservationsBounded(m,trace,{}, {max_total_bytes:1}));assert.equal(decoded,0);
 const error=caught(()=>replayWithObservationsBounded(model({step:(s,i)=>accepted(s+i,[1,2,3])}),trace,{}, {max_outputs_per_step:1}));
 assert.equal(error.actual.kind,'turn');assert.equal(error.actual.checks.length,2);assert.equal(error.report.failure_reproduced,false);
});
test('frozen private receiver and automatic wrapper remain usable for rich replay',()=>{
 class Private {#n=1;initialState(){return 0;}cloneState(s){return s;}cloneInput(i){return i;}cloneOutput(o){return o;}metadata(){return metadata('private','same');}step(s,i){return accepted(s+i*this.#n);}checkState(){return [];}inputs(){return [1];}encodeState(s){return Uint8Array.of(s);}decodeState(b){return b[0];}encodeInput(i){return Uint8Array.of(i);}decodeInput(b){return b[0];}encodeOutput(o){return Uint8Array.of(o);}}
 const m=automatic(Object.freeze(new Private()));assert.equal(replayWithObservations(m,record(m,[1])).outcome,'exact');
});
test('noncallback snapshot failure does not retry the failed transition snapshot',()=>{
 const source=record(model(),[1]);let clones=0,afterFailure=0,failed=false;const observations=[];
 class BadOutputs extends Array {[Symbol.iterator](){failed=true;throw Error('iterator snapshot failure');}}
 const raw=accepted(1,new BadOutputs(0));
 const m=model({cloneState(s){clones++;if(failed)afterFailure++;return s;},step(){return raw;}});
 const error=caught(()=>replayWithObservations(m,source,{},o=>{observations.push(o);}));
 assert.equal(afterFailure,0);assert.ok(clones>0);assert.equal(error.actual.transition,raw);assert.equal(observations.at(-1).snapshot_unavailable,true);assert.equal(observations.at(-1).actual,null);
});
test('bounded limits are captured once before metadata and validated after identity',()=>{
 const trace={...record(model(),[1]),config:{strategy:'test',seed:null,parameters:[]}};
 let reads=0,cap=1;const limits={get max_total_bytes(){reads++;return cap;}};let decoded=0;
 const m=model({metadata(){cap=100000;return metadata('replay','build');},decodeState(b){decoded++;return b[0];}});
 caught(()=>replayWithObservationsBounded(m,trace,{},limits));assert.equal(reads,1);assert.equal(decoded,0);
 const incompatible={...trace,metadata:metadata('other')};assert.equal(replayWithObservationsBounded(m,incompatible,{}, {max_total_bytes:-1}).outcome,'incompatible');
});
