import test from 'node:test';
import assert from 'node:assert/strict';
import {RequestModel,enumerateStates,Rng,NO_INPUT} from '../dist/index.js';
test('request lifecycle discovers the injected stale publication and fixed model is safe',()=>{
 const fixed=enumerateStates(RequestModel.fixed()),buggy=enumerateStates(RequestModel.buggy());
 assert.equal(fixed.failure,null);assert.ok(buggy.failure);
 assert.ok(buggy.failure.violations.some(v=>v.check.id==='stale_completion_is_cleanup_only'));
});
test('request lifecycle codec matches Rust byte vectors and rejects malformed input',()=>{
 const m=RequestModel.fixed('shared');const s={generation:2,active:true,ready:false,pending:[1,2]};
 assert.deepEqual([...m.encodeState(s)],[2,1,0,2,1,2]);assert.deepEqual(m.decodeState(m.encodeState(s)),s);
 for(const [i,bytes] of [[{kind:'start'},[0]],[{kind:'cancel'},[1]],[{kind:'complete',generation:2},[2,2]]]){assert.deepEqual([...m.encodeInput(i)],bytes);assert.deepEqual(m.decodeInput(Uint8Array.from(bytes)),i);}
 assert.deepEqual([...m.encodeOutput({kind:'release',generation:2})],[2,2]);
 for(const b of [[0,2,0,0],[0,0,0,1],[0,0,0,0,1]])assert.throws(()=>m.decodeState(Uint8Array.from(b)));
 for(const b of [[],[0,0],[2],[3,0]])assert.throws(()=>m.decodeInput(Uint8Array.from(b)));
 assert.equal(m.metadata().build,'shared:fixed');
});
test('request generation draws from enabled ordered inputs',()=>{
 const m=RequestModel.fixed(),s={generation:2,active:false,ready:false,pending:[]};assert.equal(m.generate(s,new Rng(1n)),NO_INPUT);
 const active={generation:1,active:true,ready:false,pending:[1]};assert.deepEqual(m.inputs(active),[{kind:'start'},{kind:'cancel'},{kind:'complete',generation:1}]);for(let n=0n;n<20n;n++)assert.equal(m.isEnabled(active,m.generate(active,new Rng(n))),true);
});
test('request fixture participates in bounded estimated-state admission',()=>{
 const m=RequestModel.fixed();
 const capped=enumerateStates(m,undefined,{maxEstimatedStateBytes:32n});
 assert.equal(capped.termination,'estimated_state_byte_limit');assert.equal(capped.states,1);assert.equal(capped.estimated_retained_state_bytes,32n);
 const complete=enumerateStates(m,undefined,{maxEstimatedStateBytes:10000n});assert.equal(complete.termination,'graph_exhausted');assert.ok(complete.estimated_retained_state_bytes>32n);
});
test('request model rejects invalid runtime variant tags',()=>{
 const m=RequestModel.fixed(),s=m.initialState(),badInput={kind:'unknown',generation:1},badOutput={kind:'unknown',generation:1};
 for(const fn of [()=>m.step(s,badInput),()=>m.isEnabled(s,badInput),()=>m.encodeInput(badInput),()=>m.cloneInput(badInput),()=>m.equalInputs(badInput,{kind:'start'}),()=>m.checkTransition(s,badInput,{state:s,outputs:[],disposition:{kind:'accepted',reason:''}}),()=>m.encodeOutput(badOutput),()=>m.cloneOutput(badOutput),()=>m.checkTransition(s,{kind:'start'},{state:s,outputs:[badOutput],disposition:{kind:'accepted',reason:''}})])assert.throws(fn,/invalid request/);
 assert.throws(()=>m.encodeInput({kind:'complete',generation:256}));
 assert.throws(()=>m.encodeOutput({kind:'request',generation:-1}));
});
