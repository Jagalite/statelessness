import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {checkInitial,checkTurnExact,checkTurn,CheckedInitial,CheckedTurn,check,accepted,checkObservedCountIntoExact} from '../dist/index.js';
const corpus=JSON.parse(fs.readFileSync(new URL('../../conformance/checked-results-v1.json',import.meta.url)));
for(const row of corpus.cases)test(`checked corpus ${row.case}`,()=>{
 const events=[];let initial=0,steps=0;
 const m={initialState(){initial++;throw Error('unexpected initial');},step(){steps++;throw Error('unexpected step');},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
  checkState(){throw Error('legacy state');},checkStateInto(s,sink){events.push(`state:${s}`);sink.extend(row.model.state_checks);if(row.model.state_error)throw Error(row.model.state_error);},
  checkTransitionInto(b,i,t,sink){events.push(`transition:${b}:${i}:${t.state}`);sink.extend(row.model.transition_checks);if(row.model.transition_error)throw Error(row.model.transition_error);}};
 let result,error;
 try{const t=row.kind==='initial'?checkInitial(m,row.state):checkTurnExact(m,row.before,row.input,row.transition,BigInt(row.sequence),{stateEvery:BigInt(row.policy.state_every),transitionChecks:row.policy.transition_checks});result=row.kind==='initial'?{kind:'initial',state:t.state,checks:t.checks,state_check_count:t.stateCheckCount}:{kind:'turn',before:t.before,input:t.input,transition:t.transition,sequence:String(t.sequence),checks:t.checks,state_check_count:t.stateCheckCount,full_checking:t.fullChecking};}catch(e){error=e;}
 if(row.expected.error_contains){assert.ok(error);assert.ok(error.message.includes(row.expected.error_contains),error.message);}else{assert.ifError(error);assert.deepEqual(result,row.expected.token);}
 assert.deepEqual(events,row.expected.events);assert.equal(initial,0);assert.equal(steps,0);
});
function mutableModel(){return {initialState(){throw Error('unused');},step(){throw Error('unused');},cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),checkState:()=>[check('ok')],checkTransition:()=>[check('edge')]};}
test('checked tokens authenticate and detach all context and diagnostic accessors',()=>{
 const m=mutableModel(),before={n:7},input={n:2},transition=accepted({n:9},[{n:90}]);const t=checkTurnExact(m,before,input,transition,1n);
 before.n=100;input.n=100;transition.state.n=100;transition.outputs[0].n=100;
 assert.equal(t.before.n,7);assert.equal(t.input.n,2);assert.equal(t.transition.state.n,9);assert.equal(t.transition.outputs[0].n,90);
 const view=t.observation();view.before.n=33;view.transition.state.n=33;assert.equal(t.before.n,7);assert.equal(t.transition.state.n,9);
 assert.throws(()=>t.sequence=4n,TypeError);assert.throws(()=>t.checks.push(check('forged')),TypeError);assert.throws(()=>new CheckedTurn());assert.throws(()=>new CheckedInitial());
 assert.throws(()=>Object.getOwnPropertyDescriptor(CheckedTurn.prototype,'checks').get.call({}));
 assert.throws(()=>Object.getOwnPropertyDescriptor(CheckedTurn.prototype,'checks').get.call(Object.create(CheckedTurn.prototype)));
 assert.deepEqual(t.intoChecks(),t.checks);assert.equal(t.fullChecking,true);
});
test('exact sequence and policy preserve full u64 and compatibility rejects unsafe numbers',()=>{
 const m=mutableModel(),t=accepted({n:9});assert.equal(checkTurnExact(m,{n:7},{n:2},t,(1n<<64n)-1n).sequence,(1n<<64n)-1n);
 for(const n of [0n,-1n,1n<<64n])assert.throws(()=>checkTurnExact(m,{},{},t,n));
 assert.throws(()=>checkTurn(m,{},{},t,2**53));assert.throws(()=>checkTurnExact(m,{},{},t,1n,{stateEvery:0n,transitionChecks:true}));
 const p={get stateEvery(){return 2n;},transitionChecks:true};assert.equal(checkTurnExact(m,{},{},t,2n,p).fullChecking,false);
});
test('snapshot/check failures never mint tokens or rerun callbacks and supplied turn survives',()=>{
 const m=mutableModel(),actual=accepted({n:9});let calls=0;m.checkState=()=>{calls++;throw Error('checker failed');};assert.throws(()=>checkTurnExact(m,{n:7},{n:2},actual,1n),/checker failed/);assert.equal(calls,1);assert.equal(actual.state.n,9);
 m.cloneState=()=>{throw Error('clone failed');};assert.throws(()=>checkInitial(m,{n:4}),/clone failed/);assert.equal(calls,1);
});
test('checker-retained context cannot later mutate the sealed snapshot',()=>{
 const m=mutableModel();let escaped;m.checkState=s=>{escaped=s;return [check('ok')];};const t=checkInitial(m,{n:7});escaped.n=99;assert.equal(t.state.n,7);
 let transitionContext;m.checkTransition=(b,i,t)=>{transitionContext=t;return [];};const turn=checkTurnExact(m,{n:7},{n:2},accepted({n:9}),1n);transitionContext.state.n=55;assert.equal(turn.transition.state.n,9);
});
test('policy accessors are captured once before callbacks',()=>{
 const m=mutableModel();let reads=0;const policy={get stateEvery(){reads++;if(reads>1)throw Error('reread');return 1n;},transitionChecks:true};const t=checkTurnExact(m,{n:1},{n:1},accepted({n:2}),1n,policy);assert.equal(reads,1);assert.equal(t.fullChecking,true);
});
test('exact observed checking preserves u64 policy arithmetic and clears all errors',()=>{
 const m=mutableModel(),out=[check('old')],t=accepted({n:2});const max=(1n<<64n)-1n;
 assert.equal(checkObservedCountIntoExact(m,{n:1},{n:1},t,max,out,{stateEvery:max,transitionChecks:true}),1);assert.deepEqual(out.map(c=>c.id),['ok','edge']);
 assert.equal(checkObservedCountIntoExact(m,{n:1},{n:1},t,max-1n,out,{stateEvery:max,transitionChecks:false}),1);assert.deepEqual(out.map(c=>c.status),['skipped','skipped']);
 assert.throws(()=>checkObservedCountIntoExact(m,{},{},t,0n,out));assert.deepEqual(out,[]);
 m.checkTransition=()=>{throw Error('edge failed');};assert.throws(()=>checkObservedCountIntoExact(m,{},{},t,1n,out),/edge failed/);assert.deepEqual(out,[]);
});
