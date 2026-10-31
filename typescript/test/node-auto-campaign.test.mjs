import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CAMPAIGN, DEFAULT_GUIDANCE, CampaignCancellation } from '../dist/index.js';
import { runNodeAutoCampaign, runNodeGuidedAutoCampaign } from '../dist/node_campaign.js';
const module = new URL('./fixtures/node-auto-worker.mjs',import.meta.url);
const config={...DEFAULT_CAMPAIGN,jobs:1,workers:2,fuzz:{seed:42n,cases:1,maxSteps:1,maxTransitions:4n,mutationPercent:0}};
for(const guided of [false,true]) for(const workers of [1,2]) test(`node auto campaign ${guided?'guided':'ordinary'} workers=${workers} stops in scan`,async()=>{
 const counts=new Int32Array(new SharedArrayBuffer(16)), token=new CampaignCancellation(), jobs=[];
 const descriptor={module,data:{counts:counts.buffer,cancel:true},decodeInput:x=>x-100};
 const args=[{...config,workers},{cancellation:token},{maxCandidates:30}];
 const report=guided?await runNodeGuidedAutoCampaign(...args,DEFAULT_GUIDANCE,descriptor,j=>{jobs.push(j);}):await runNodeAutoCampaign(...args,descriptor,j=>{jobs.push(j);});
 assert.equal(jobs.length,1);assert.equal(jobs[0].outcome.kind,'Completed');assert.equal(jobs[0].outcome.report.termination,'cancelled');
 assert.equal(Atomics.load(counts,0),3);assert.equal(Atomics.load(counts,1),1);assert.equal(report.finished,1);
});
for(const guided of [false,true]) test(`node auto campaign ${guided?'guided':'ordinary'} completes across two workers`,async()=>{
 const counts=new Int32Array(new SharedArrayBuffer(16)), jobs=[];
 const descriptor={module,data:{counts:counts.buffer,cancel:false},decodeInput:x=>x-100};
 const args=[{...config,jobs:2,workers:2},{},{maxCandidates:30}];
 const report=guided?await runNodeGuidedAutoCampaign(...args,DEFAULT_GUIDANCE,descriptor,j=>{jobs.push(j);}):await runNodeAutoCampaign(...args,descriptor,j=>{jobs.push(j);});
 assert.equal(report.finished,2, JSON.stringify(report,(_k,v)=>typeof v==='bigint'?String(v):v));assert.equal(report.delivered,2, JSON.stringify(report,(_k,v)=>typeof v==='bigint'?String(v):v));assert.equal(jobs.length,2, JSON.stringify(report,(_k,v)=>typeof v==='bigint'?String(v):v));
 for(const job of jobs){assert.equal(job.outcome.kind,'Completed');assert.notEqual(job.outcome.report.termination,'cancelled');}
 assert.ok(Atomics.load(counts,0)>0);assert.ok(Atomics.load(counts,1)>0);
});

for(const guided of [false,true]) test(`node auto ${guided?'guided':'ordinary'} transfers failure and disposes`,async()=>{
 const counts=new Int32Array(new SharedArrayBuffer(16)), jobs=[];
 const descriptor={module,data:{counts:counts.buffer,witness:true},decodeInput:x=>x-100};
 const args=[{...config,jobs:2,workers:2},{},{maxCandidates:30}];
 const report=guided?await runNodeGuidedAutoCampaign(...args,DEFAULT_GUIDANCE,descriptor,j=>{jobs.push(j);}):await runNodeAutoCampaign(...args,descriptor,j=>{jobs.push(j);});
 assert.equal(report.finished,2, JSON.stringify(report,(_k,v)=>typeof v==='bigint'?String(v):v));assert.equal(jobs.length,2, JSON.stringify(report,(_k,v)=>typeof v==='bigint'?String(v):v));
 for(const job of jobs){assert.equal(job.outcome.kind,'Completed');assert.ok(job.outcome.report.failure);assert.ok(job.outcome.report.failure.inputs.every(x=>x>=0 && x<10));}
 assert.equal(Atomics.load(counts,2),2);assert.equal(Atomics.load(counts,3),guided?2:0);
});

for(const workers of [1,2]) test(`node auto campaign enforces the automatic model contract at runtime workers=${workers}`,async()=>{
 const counts=new Int32Array(new SharedArrayBuffer(16)), jobs=[];
 const descriptor={module,data:{counts:counts.buffer,missingInputs:true},decodeInput:x=>x-100};
 const report=await runNodeAutoCampaign({...config,jobs:1,workers},{},{maxCandidates:30},descriptor,j=>{jobs.push(j);});
 assert.equal(jobs.length,1);assert.equal(jobs[0].outcome.kind,'FactoryError');
 assert.match(jobs[0].outcome.error.message,/automatic campaign model requires inputs and equalInputs/);
 assert.equal(report.finished,1);
});
for(const workers of [1,2]) test(`node auto campaign rejects a module violating the automatic contract workers=${workers}`,async()=>{
 const jobs=[];const descriptor={module:new URL('./fixtures/node-auto-bad-module.mjs',import.meta.url),decodeInput:x=>x};
 const report=await runNodeAutoCampaign({...config,jobs:1,workers},{},{maxCandidates:30},descriptor,j=>{jobs.push(j);});
 assert.equal(jobs.length,0);assert.equal(report.termination.kind,'WorkerError');
 assert.match(report.termination.error.message,/disposeModel must be a function/);
});
