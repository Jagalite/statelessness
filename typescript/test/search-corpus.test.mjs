import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {accepted,check,automatic,fuzz,shrink} from '../dist/index.js';
const lines=readFileSync(new URL('../../conformance/search-v1.tsv',import.meta.url),'utf8').trim().split('\n');
const columns=lines.shift().split('\t');
const histories=JSON.parse(readFileSync(new URL('../../conformance/search-v1-history.json',import.meta.url),'utf8'));
for(const line of lines){const row=Object.fromEntries(line.split('\t').map((v,i)=>[columns[i],v]));test(`search-v1 ${row.case}`,()=>{
 const trace=[];let current;const fixture=row.fixture;
 const m={initialState:()=>{current=[];trace.push(current);return 0;},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalStates:(a,b)=>a===b,equalInputs:(a,b)=>a===b,
 inputs:s=>fixture==='empty'?[]:fixture==='causal'?(s===0?[1]:s===1?[2]:[]):['binary','phase','cycle'].includes(fixture)?[1,2]:[1],
 step:(s,i)=>{current.push(i);return accepted(['causal','phase','cycle'].includes(fixture)?i:s+i);},
 checkState:s=>{const bad=fixture==='initial'||fixture==='threshold'&&s>=3||fixture==='causal'&&s===2||fixture==='phase'&&s===1||fixture==='cycle'&&s!==0;return bad?[check(fixture==='phase'?'same':'bad','failed','fixture')]:[];},
 checkTransition:(_s,_i,t)=>fixture==='phase'&&t.state===2?[check('same','failed','fixture')]:[]};
 if(row.error==='invalid_candidate_budget'){assert.throws(()=>automatic(m,{maxCandidates:Number(row.max_candidates)}));return;}
 const gen=automatic(m,{maxCandidates:row.max_candidates==='-'?100:Number(row.max_candidates)});
 if(fixture==='digits'){gen.generate=(_s,r)=>Number(r.nextU64()%10n);gen.isEnabled=(_s,i)=>Number.isInteger(i)&&i>=0&&i<=9;}
 if(fixture==='phase')gen.simplerInputs=i=>i===1?[2]:[];if(fixture==='cycle')gen.simplerInputs=i=>[i===1?2:1];
 const limits=row.control==='cancelled'?{cancellation:()=>true}:row.control==='deadline'?{maxDurationMs:0}:{};
 const run=()=>row.operation==='fuzz'?fuzz(gen,{seed:BigInt(row.seed),cases:Number(row.cases),maxSteps:Number(row.max_steps),maxTransitions:BigInt(row.max_transitions),mutationPercent:Number(row.mutation_percent)},limits):shrink(gen,{inputs:JSON.parse(row.original),violations:[{phase:row.target_phase,check:check(row.target_id,'failed','fixture')}]},{maxAttempts:Number(row.max_attempts)},{...limits,...(row.max_replayed_transitions==='-'?{}:{maxReplayedTransitions:BigInt(row.max_replayed_transitions)})});
 if(row.error!=='-'){assert.throws(run);return;}
 const r=run();assert.equal(r.termination,row.termination);assert.equal(r.transitions??r.replayed_transitions,BigInt(row.transitions));
 if(row.operation==='fuzz'){assert.equal(r.cases,Number(row.started_cases));assert.equal(r.skipped_checks,0n);if(row.trace!=='-')assert.deepEqual(trace,JSON.parse(row.trace));}
 else{assert.equal(r.attempts,Number(row.attempts));assert.equal(r.validated_original,row.validated_original==='true');if(histories[row.case])assert.deepEqual(r.history,histories[row.case]);}
 const failure=row.operation==='fuzz'?r.failure:r.minimized;if(row.witness==='-')assert.equal(failure,null);else{assert.deepEqual(failure.inputs,JSON.parse(row.witness));assert.equal(failure.violations[0].phase,row.failed_phase);assert.equal(failure.violations[0].check.id,row.failed_id);}
 });}
