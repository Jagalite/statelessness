import test from 'node:test';
import assert from 'node:assert/strict';
import { accepted, automatic, fuzz, fuzzAuto, fuzzGuided, fuzzGuidedAuto, DEFAULT_GUIDANCE } from '../dist/index.js';
const config = { seed: 42n, cases: 3, maxSteps: 2, maxTransitions: 8n, mutationPercent: 0 };
const options = { maxCandidates: 30 };
const model = inputs => ({ initialState:()=>0, step:s=>accepted(s+1), checkState:()=>[], cloneState:s=>s, cloneInput:i=>i, cloneOutput:o=>o, equalInputs:(a,b)=>a===b, inputs });
const feedback=()=>({metadata:()=>({name:'x',version:1,build:'x'}),cloneFeature:x=>x,equalFeatures:(a,b)=>a===b,transitionFeatures:()=>{}});
test('ordinary and guided scans stop while iterating and close the iterator',()=>{
 for (const guided of [false,true]) {
  let cancelled=false, count=0, closed=0;
  const m=model(function*(){try { for(let i=0;i<20;i++){count++;if(count===3)cancelled=true;yield i;} }finally{closed++;}});
  const limits={cancellation:()=>cancelled};
  const result=guided ? fuzzGuidedAuto(m,options,config,DEFAULT_GUIDANCE,feedback(),limits) : fuzzAuto(m,options,config,limits);
  assert.equal(result.termination,'cancelled');assert.equal(result.transitions,0n);assert.equal(count,3);assert.equal(closed,1);
 }
});
test('membership scan is interrupted and iterator callback errors retain precedence',()=>{
 let calls=0,cancelled=false;
 const m=model(function*(){calls++;for(let i=0;i<5;i++){if(calls===2&&i===0)cancelled=true;yield i;}});
 const report=fuzzAuto(m,options,config,{cancellation:()=>cancelled});
 assert.equal(report.termination,'cancelled');assert.equal(report.transitions,0n);assert.equal(calls,2);
 const bad=model(function*(){cancelled=true;throw new Error('domain broke');});cancelled=false;
 assert.throws(()=>fuzzAuto(bad,options,config,{cancellation:()=>cancelled}),/domain broke/);
});
test('completed automatic and guided runs match existing behavior',()=>{
 const m=model(()=>[1,2,3]);
 assert.deepEqual(fuzzAuto(m,options,config),fuzz(automatic(m,options),config));
 assert.deepEqual(fuzzGuidedAuto(m,options,config,DEFAULT_GUIDANCE,feedback()),fuzzGuided(automatic(m,options),config,DEFAULT_GUIDANCE,feedback()));
});
test('interrupted reservoir leaves RNG unchanged',async()=>{
 const {Rng,sampleEnumerated,NO_INPUT}=await import('../dist/index.js');
 let polls=0;const rng=new Rng(42n), control=new Rng(42n);
 assert.equal(sampleEnumerated(model(()=>[1,2,3,4]),0,rng,options,()=>++polls===4),NO_INPUT);
 assert.equal(rng.nextU64(),control.nextU64());
});
test('caller-thread automatic campaigns stop inside scans and dispose',async()=>{
 const {runAutoCampaign,runGuidedAutoCampaign,DEFAULT_CAMPAIGN,metadata}=await import('../dist/index.js');
 for(const guided of [false,true]) {
  let cancel=false,closed=0,disposed=0;const results=[];
  const m={...model(function*(){try{for(let i=0;i<10;i++){if(i===2)cancel=true;yield i;}}finally{closed++;}}),metadata:()=>metadata('x','1'),dispose:()=>{disposed++;}};
  const c={...DEFAULT_CAMPAIGN,jobs:1,workers:1,fuzz:config};
  const sink=job=>{results.push(job)};
  if(guided) await runGuidedAutoCampaign(c,{cancellation:()=>cancel},options,DEFAULT_GUIDANCE,()=>m,()=>feedback(),sink);
  else await runAutoCampaign(c,{cancellation:()=>cancel},options,()=>m,sink);
  assert.equal(results.length,1);assert.equal(results[0].outcome.kind,'Completed',results[0].outcome.message);assert.equal(results[0].outcome.report.termination,'cancelled');assert.equal(disposed,1);assert.equal(closed,1);
 }
});
