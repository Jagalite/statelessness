import test from 'node:test';
import assert from 'node:assert/strict';
import {recordContinued,validateContinuedRecording,replayContinued,encodeContinuedTrace,parseContinuedTrace,continuedFailingSequences,record,replay,validateRecording,parseTrace,stringifyTrace,FormatError,
 shrink,shrinkContinued,exploreGraph,metadata,accepted,check,NO_INPUT} from '../dist/index.js';
// continued-failure-evidence-v1. Counter: state += input, outputs [input, state];
// state check `high` fails at state >= 3; transition check `big_step` fails for input >= 5.
const bytes=v=>{const b=new Uint8Array(8);new DataView(b.buffer).setBigInt64(0,BigInt(v),true);return b;};
const int=b=>{if(b.length!==8)throw Error('bad integer');return Number(new DataView(b.buffer,b.byteOffset,8).getBigInt64(0,true));};
const codec=(name,version=1)=>({metadata:()=>({...metadata(name),model_version:version}),encodeState:bytes,decodeState:int,encodeInput:bytes,decodeInput:int,encodeOutput:bytes});
const counter=({version=1,throwAt}={})=>({...codec('counter',version),initialState:()=>0,step:(s,i)=>accepted(s+i,[i,s+i]),
 checkState:s=>{if(s===throwAt)throw Error('checker exploded');return [s>=3?check('high','failed',String(s)):check('high')];},
 checkTransition:(_s,i)=>i>=5?[check('big_step','failed',String(i))]:[],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
 inputs:()=>[1,5],equalStates:(a,b)=>a===b,hashState:s=>s,generate:()=>NO_INPUT,equalInputs:(a,b)=>a===b});
// 0 unarmed, 1 armed, 2 fired. Input 0 waits, 1 arms (warning), 2 fires and is enabled only when armed.
const trigger=()=>({...codec('trigger'),initialState:()=>0,step:(s,i)=>accepted(i===1&&s===0?1:i===2&&s===1?2:s),
 checkState:s=>[s===2?check('fired','failed','boom'):check('fired')],checkTransition:(s,i)=>i===1&&s===0?[check('armed_warning','failed','armed')]:[],
 cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>s===0?[0,1]:s===1?[0,2]:[],equalStates:(a,b)=>a===b,hashState:s=>s,
 generate:()=>NO_INPUT,isEnabled:(s,i)=>i!==2||s===1,equalInputs:(a,b)=>a===b});
const witness=[1,1,1,1,5,0,7];
const rebuilt=(t,steps)=>({trace:{...t,steps}});
const failed=(id,details='')=>check(id,'failed',details);
test('record continues past failures; old readers reject',()=>{
 const t=recordContinued(counter(),witness);
 assert.equal(t.trace.steps.length,7);assert.equal(t.trace.termination,'completed');assert.deepEqual(continuedFailingSequences(t),[3,4,5,6,7]);
 validateContinuedRecording(t);
 assert.throws(()=>validateRecording(t.trace),/continues after property failure/);
 assert.throws(()=>replay(counter(),t.trace),/continues after property failure/);
 const legacy=record(counter(),witness);
 assert.equal(legacy.steps.length,3);assert.equal(legacy.termination,'property_failed');
 const encoded=encodeContinuedTrace(t);
 assert.deepEqual(parseContinuedTrace(encoded),t);
 assert.throws(()=>parseTrace(encoded),FormatError);
 assert.throws(()=>parseContinuedTrace(stringifyTrace(legacy)),FormatError);
});
test('replay compares every step',()=>{
 const t=recordContinued(counter(),witness);
 const exact=replayContinued(counter(),t);
 assert.equal(exact.completeReproduction,true);assert.deepEqual(exact.reproducedFailures,[3,4,5,6,7]);assert.equal(exact.replay.steps_verified,7);
 let steps=t.trace.steps.slice();steps[4]={...steps[4],outputs:[bytes(5),bytes(0)]};
 const corrupt=replayContinued(counter(),rebuilt(t.trace,steps));
 assert.equal(corrupt.completeReproduction,false);assert.equal(corrupt.replay.outcome,'diverged');assert.equal(corrupt.replay.step,5);assert.equal(corrupt.replay.field,'outputs');
 steps=t.trace.steps.slice();steps[1]={...steps[1],outputs:[...steps[1].outputs].reverse()};
 assert.equal(replayContinued(counter(),rebuilt(t.trace,steps)).replay.field,'outputs');
 steps=t.trace.steps.slice();steps[6]={...steps[6],checks:[check('high')]};
 const suppressed=replayContinued(counter(),rebuilt(t.trace,steps));
 assert.equal(suppressed.completeReproduction,false);assert.equal(suppressed.replay.field,'checks');assert.deepEqual(suppressed.recordedFailures,[3,4,5,6]);
 const incompatible=replayContinued(counter({version:2}),t);
 assert.equal(incompatible.replay.outcome,'incompatible');assert.equal(incompatible.completeReproduction,false);
});
test('corrupted and truncated artifacts are rejected',()=>{
 const encoded=encodeContinuedTrace(recordContinued(counter(),witness));
 assert.throws(()=>parseContinuedTrace(encoded.slice(0,encoded.length>>1)),FormatError);
 assert.ok(encoded.includes('"failing_sequences":[3,4,5,6,7]'));
 assert.throws(()=>parseContinuedTrace(encoded.replace('"failing_sequences":[3,4,5,6,7]','"failing_sequences":[3,4]')),/failing_sequences disagree/);
 assert.throws(()=>parseContinuedTrace(encoded.replace('"version":1','"version":2')),/unsupported continued trace format/);
 assert.throws(()=>parseContinuedTrace(encoded.replace('"format":"stateless.continued-trace-json"','"format":"stateless.trace-json"')),FormatError);
 const failedTrace={trace:{metadata:metadata('counter'),initialState:bytes(0),initialChecks:[],steps:[],termination:'property_failed',error:''}};
 assert.throws(()=>encodeContinuedTrace(failedTrace),/do not terminate on property failure/);
});
test('incomplete checks after an earlier failure keep completed steps',()=>{
 const t=recordContinued(counter({throwAt:4}),[1,1,1,1,1]);
 assert.equal(t.trace.termination,'model_error');assert.equal(t.trace.steps.length,3);assert.deepEqual(continuedFailingSequences(t),[3]);
 assert.match(t.trace.error,/checker exploded/);
 assert.deepEqual(parseContinuedTrace(encodeContinuedTrace(t)),t);
});
test('two failures on one turn',()=>{
 const target={phase:'transition',check:failed('big_step')};
 const r=shrinkContinued(counter(),witness,target);
 assert.equal(r.base.validated_original,true);assert.equal(r.base.termination,'search_complete');
 assert.deepEqual(r.base.original.inputs,witness);assert.deepEqual(r.base.minimized.inputs,[7]);
 assert.deepEqual(r.originalFindings.map(f=>f.sequence),[3,4,5,6,7]);
 assert.deepEqual(r.minimizedFindings,[{sequence:1,violations:[{phase:'state',check:failed('high','7')},{phase:'transition',check:failed('big_step','7')}]}]);
 // Stop-at-failure shrinking cannot reach the later target.
 assert.throws(()=>shrink(counter(),{inputs:witness,violations:[target]}),/does not reproduce/);
});
test('selected later target on successive turns',()=>{
 const r=shrinkContinued(trigger(),[0,1,0,2],{phase:'state',check:failed('fired')});
 assert.deepEqual(r.base.minimized.inputs,[1,2]);
 assert.deepEqual(r.minimizedFindings,[{sequence:1,violations:[{phase:'transition',check:failed('armed_warning','armed')}]},{sequence:2,violations:[{phase:'state',check:failed('fired','boom')}]}]);
 const outcomes=r.base.history.map(h=>h.outcome);
 assert.ok(outcomes.includes('invalid_causality'));assert.ok(outcomes.includes('different_failure'));
 assert.throws(()=>shrinkContinued(trigger(),[0,1,0,2],{phase:'transition',check:failed('armed_warning')}),/does not reproduce/);
 assert.throws(()=>shrinkContinued(trigger(),[0,1,0,2],{phase:'state',check:check('fired')}),/without a target violation/);
});
test('budget keeps original and minimized witnesses; interrupted validation keeps no findings',()=>{
 const r=shrinkContinued(counter(),witness,{phase:'transition',check:failed('big_step')},{maxAttempts:2});
 assert.equal(r.base.termination,'attempt_limit');assert.deepEqual(r.base.original.inputs,witness);assert.deepEqual(r.base.minimized.inputs,[5,0,7]);
 assert.equal(r.minimizedFindings.at(-1).sequence,3);
 const stopped=shrinkContinued(counter(),witness,{phase:'transition',check:failed('big_step')},{maxAttempts:10n},{cancellation:()=>true});
 assert.equal(stopped.base.validated_original,false);assert.deepEqual(stopped.base.minimized.inputs,witness);
 assert.deepEqual(stopped.originalFindings,[]);assert.deepEqual(stopped.minimizedFindings,[]);assert.equal(stopped.base.attempts,1n);
});
test('graph findings flow into continued evidence',()=>{
 const g=trigger();
 const report=exploreGraph(g,undefined,{},{failurePolicy:{kind:'collect',maxRetainedFindings:10,expandFailingSuccessors:true}});
 assert.deepEqual(report.findings.map(f=>f.witness),[[1],[1,2]]);assert.deepEqual(report.findings.map(f=>f.requiresContinuedEvidence),[false,true]);
 const later=report.findings[1];
 assert.deepEqual(shrinkContinued(g,later.witness,later.violations[0]).base.minimized.inputs,[1,2]);
 const trace=recordContinued(g,later.witness);
 assert.equal(replayContinued(g,trace).completeReproduction,true);assert.deepEqual(continuedFailingSequences(trace),[1,2]);
});
