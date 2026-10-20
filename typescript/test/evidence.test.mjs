import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {readArtifact,writeTraceV2,writeTraceV2To,artifactTrace,checkPhase,diagnosticJSON,snapshotTraceV2,resolveEvidenceLimits,emptyCausalContext,writeAuditTrace,replayTraceV2,verifyUpgrade,accepted,check,metadata} from '../dist/index.js';
import {writeRawEvidenceNew,exportDiagnosticNew} from '../dist/node_evidence.js';
const corpus=JSON.parse(fs.readFileSync(new URL('../../conformance/causal-evidence-v2.json',import.meta.url)));
const hex=b=>Buffer.from(b).toString('hex');
const bytes=s=>Uint8Array.from(Buffer.from(s,'hex'));
function trace(t){return {metadata:t.metadata,config:{...t.config,seed:t.config.seed===null?null:BigInt(t.config.seed)},initialState:bytes(t.initial_state),initialChecks:t.initial_checks.map(c=>({...c,details:c.reason??c.details??''})).map(({id,status,details})=>({id,status,details})),steps:t.steps.map(s=>({input:bytes(s.input),disposition:{...s.disposition,reason:s.disposition.reason??''},outputs:s.outputs.map(bytes),postState:bytes(s.post_state),checks:s.checks.map(c=>({id:c.id,status:c.status,details:c.reason??c.details??''}))})),termination:t.termination.kind,error:t.termination.reason??''};}
function evidence(e){return {trace:trace(e.trace),checkpointAfter:BigInt(e.checkpoint_after),provenance:{...e.provenance,epoch:BigInt(e.provenance.epoch)},steps:e.steps.map(s=>({sequence:BigInt(s.sequence),stateCheckCount:s.state_check_count,causal:{machine:s.causal.machine,correlation:s.causal.correlation,logicalTime:s.causal.logical_time===null?null:BigInt(s.causal.logical_time),generation:s.causal.generation===null?null:BigInt(s.causal.generation),causes:s.causal.causes.map(o=>({...o,sequence:BigInt(o.sequence)}))}}))};}
function limits(l={}){const out={};for(const [k,v]of Object.entries(l)){const camel=k.replace(/_([a-z])/g,(_,c)=>c.toUpperCase());out[camel]=v;}return out;}
for(const r of corpus.wire_cases)test(`causal wire ${r.case}`,()=>{
 const l=limits(r.limits);if(!r.valid){assert.throws(()=>readArtifact(bytes(r.bytes_hex),l),e=>e.message.includes(r.error_contains));if(r.evidence)assert.throws(()=>writeTraceV2(evidence(r.evidence),l));return;}
 const a=readArtifact(bytes(r.bytes_hex),l);assert.equal(a.kind,r.artifact_kind);
 if(a.kind==='v2'){assert.deepEqual(a.evidence,evidence(r.evidence));assert.equal(hex(writeTraceV2(evidence(r.evidence),l)),r.bytes_hex);}else assert.deepEqual(a.trace,trace(r.trace));
});
test('causal envelope rejects every strict prefix and preserves source ownership',()=>{
 const r=corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v2'),b=bytes(r.bytes_hex);for(let i=0;i<b.length;i++)assert.throws(()=>readArtifact(b.subarray(0,i)));
 const a=readArtifact(b);b.fill(0);assert.equal(hex(writeTraceV2(a.evidence)),r.bytes_hex);
});
test('v2 writer preflights before publishing and snapshots detach mutable evidence',()=>{
 const e=evidence(corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v2').evidence);let writes=0;assert.throws(()=>writeTraceV2To(e,{write(){writes++;}},{maxMetadataBytes:0}));assert.equal(writes,0);
 const owned=snapshotTraceV2(e);e.provenance.environment.push(['new','value']);assert.equal(owned.provenance.environment.length,0);
});
test('passive v1 phases remain unknown and diagnostics include initial at zero step allowance',()=>{
 const r=corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v1'),a=readArtifact(bytes(r.bytes_hex));assert.equal(checkPhase(a,1,0),null);
 const d=JSON.parse(new TextDecoder().decode(diagnosticJSON(a,{maxSteps:0})));assert.equal(d.replayable,false);assert.equal(d.observations.length,1);assert.equal(d.checkpoint_after,null);assert.throws(()=>diagnosticJSON(a,{maxBytes:1}));
});
test('Node private exports create exclusively without overwrite and use private permissions',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'evidence-'));try{const e=evidence(corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v2').evidence),p=path.join(dir,'raw.bin');writeRawEvidenceNew(p,e);const previous=fs.readFileSync(p);assert.throws(()=>writeRawEvidenceNew(p,e));assert.deepEqual(fs.readFileSync(p),previous);if(process.platform!=='win32')assert.equal(fs.statSync(p).mode&0o777,0o600);const q=path.join(dir,'diagnostic.json');exportDiagnosticNew(q,{kind:'v2',evidence:e});assert.equal(JSON.parse(fs.readFileSync(q,'utf8')).replayable,false);}finally{fs.rmSync(dir,{recursive:true,force:true});}
});
function fixture(v){const ascii=n=>new TextEncoder().encode(String(n));return {metadata:()=>metadata(v.name,v.build),initialState(){throw Error('must not initialize');},step(s,i){if(v.error_at==='step')throw Error('step boom');const next=s+i;return accepted(next+v.state_delta,[next+v.output_delta]);},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,checkState:s=>v.phase_shift&&s!==0?[]:[check('s')],checkTransition:()=>v.phase_shift?[check('s'),check('t')]:[check('t')],encodeState:ascii,decodeState:b=>Number(new TextDecoder().decode(b)),encodeInput:ascii,decodeInput:b=>Number(new TextDecoder().decode(b)),encodeOutput:ascii};}
const reportJSON=r=>({outcome:r.outcome,divergence_step:r.step,divergence_field:r.field,steps_verified:r.steps_verified,failure_reproduced:r.failure_reproduced,build_matches:r.build_matches});
const stepJSON=s=>({sequence:String(s.sequence),state_check_count:s.stateCheckCount,causal:{machine:s.causal.machine,generation:s.causal.generation===null?null:String(s.causal.generation),correlation:s.causal.correlation,logical_time:s.causal.logicalTime===null?null:String(s.causal.logicalTime),causes:s.causal.causes.map(c=>({sequence:String(c.sequence),output:c.output}))}});
for(const r of corpus.replay_cases??[])test(`causal ${r.operation} ${r.case}`,()=>{
 const e=evidence(r.evidence),m=fixture(r.model),l=limits(r.limits),options={allowBuildMismatch:r.allow_build_mismatch??false};let result;
 const run=()=>r.operation==='upgrade'?verifyUpgrade(m,e.trace,e.provenance,e.checkpointAfter,options,l):replayTraceV2(e,m,options,l);
 if(r.expected.error_contains){assert.throws(run,e=>e.message.includes(r.expected.error_contains));return;}
 result=run();if(r.operation==='replay')assert.deepEqual(reportJSON(result),r.expected.report);else{assert.deepEqual(result.steps.map(stepJSON),r.expected.steps);assert.deepEqual(result.trace,e.trace);assert.deepEqual(result.provenance,e.provenance);assert.equal(result.checkpointAfter,e.checkpointAfter);}
});
for(const r of corpus.export_cases??[])test(`causal diagnostic ${r.case}`,()=>{
 const a=r.artifact_kind==='v2'?{kind:'v2',evidence:evidence(r.evidence)}:{kind:'v1',trace:trace(r.trace??r.evidence.trace)},policy={maxSteps:r.policy.max_steps,maxBytes:r.policy.max_bytes,propertyIds:r.policy.property_ids};
 const run=()=>new TextDecoder().decode(diagnosticJSON(a,policy));if(r.expected.error_contains)assert.throws(run);else assert.equal(run(),r.expected.json_text);
});
test('diagnostics reject malformed native enums and checkpoint without inspecting omitted payloads',()=>{
 const t=trace(corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v1').trace);t.initialChecks=[{id:'safe',status:'passed\",\"payload\":\"secret',details:''}];assert.throws(()=>diagnosticJSON({kind:'v1',trace:t}));
 t.initialChecks=[];t.termination='completed\",\"secret\":true';assert.throws(()=>diagnosticJSON({kind:'v1',trace:t}));t.termination='completed';
 const e={trace:t,checkpointAfter:'0,\"secret\":true',steps:[],provenance:{}};assert.throws(()=>diagnosticJSON({kind:'v2',evidence:e}));
 let reads=0;t.initialChecks=[{get id(){if(++reads>1)throw Error('reread');return 'safe';},status:'passed',get details(){throw Error('sensitive');}}];t.steps=[{get checks(){throw Error('unselected');},get input(){throw Error('payload');}}];
 const d=JSON.parse(new TextDecoder().decode(diagnosticJSON({kind:'v1',trace:t},{maxSteps:0,propertyIds:['safe']})));assert.equal(reads,1);assert.equal(d.observations[0].checks[0].id,'safe');assert.equal(d.truncated,true);
});
test('evidence streams preserve receiver and enforce synchronous interfaces',()=>{
 const row=corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v2'),b=bytes(row.bytes_hex),reader={offset:0,read(out){const n=Math.min(3,out.length,b.length-this.offset);out.set(b.subarray(this.offset,this.offset+n));this.offset+=n;return n;}};
 assert.equal(hex(writeTraceV2(readArtifact(reader).evidence)),row.bytes_hex);
 assert.throws(()=>readArtifact({...reader,read:()=>-1}));assert.throws(()=>readArtifact({...reader,read:()=>9000}));
 assert.throws(()=>writeTraceV2To(evidence(row.evidence),{write:async()=>{}}),/synchronously/);
});
test('Node sync failure reports incomplete exclusive file without overwriting it',async()=>{
 const {syncBuiltinESMExports}=await import('node:module'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'evidence-sync-')),old=fs.fsyncSync;
 try{fs.fsyncSync=()=>{throw Error('sync failed');};syncBuiltinESMExports();const e=evidence(corpus.wire_cases.find(r=>r.valid&&r.artifact_kind==='v2').evidence),p=path.join(dir,'new.bin');assert.throws(()=>writeRawEvidenceNew(p,e),/incomplete newly created evidence file: sync failed/);assert.ok(fs.existsSync(p));assert.throws(()=>writeRawEvidenceNew(p,e));}finally{fs.fsyncSync=old;syncBuiltinESMExports();fs.rmSync(dir,{recursive:true,force:true});}
});
test('upgrade owns admitted fields once and defers unfit artifacts until after identity',()=>{
 const row=corpus.replay_cases.find(r=>r.case==='phase-aware-exact'),e=evidence(row.evidence),m=fixture(row.model);let metadataCalls=0,stepCalls=0;const originalStep=m.step;m.step=(...a)=>{stepCalls++;return originalStep(...a);};
 const bad={...e.trace,initialState:new Uint8Array(65)};m.metadata=()=>{metadataCalls++;return metadata('foreign','candidate-v1');};assert.throws(()=>verifyUpgrade(m,bad,e.provenance,0n,{}, {trace:{max_blob_bytes:64}}),/exact compatible replay/);assert.equal(metadataCalls,1);assert.equal(stepCalls,0);
 m.metadata=()=>{metadataCalls++;return metadata('causal-fixture','candidate-v1');};assert.throws(()=>verifyUpgrade(m,bad,e.provenance,0n,{}, {trace:{max_blob_bytes:64}}),/blob bytes/);assert.equal(metadataCalls,2);assert.equal(stepCalls,0);
 const invalid={...e.provenance,environment:[['same','a'],['same','b']]};assert.throws(()=>verifyUpgrade(m,e.trace,invalid,0n),/duplicate evidence environment/);assert.equal(stepCalls,2);
 let reads=0;const source={...e.trace,get metadata(){if(++reads>1)throw Error('metadata reread');return e.trace.metadata;}};m.metadata=()=>{source.initialState.fill(88);return metadata('causal-fixture','candidate-v1');};const upgraded=verifyUpgrade(m,source,e.provenance,0n);assert.equal(reads,1);assert.equal(hex(upgraded.trace.initialState),'30');
});
test('v2 validation reports embedded semantic failure before phase origin errors',()=>{
 const row=corpus.replay_cases.find(r=>r.case==='phase-aware-exact'),e=evidence(row.evidence);e.trace.termination='property_failed';e.steps[1].causal.causes[0].sequence=0n;assert.throws(()=>writeTraceV2(e),/termination disagrees/);
 const other=evidence(row.evidence);other.steps.pop();other.checkpointAfter=(1n<<64n)-1n;assert.throws(()=>writeTraceV2(other),/does not cover every recorded step/);
});
test('borrowed v2 admission preserves Rust competing-error order before copying',()=>{
 const row=corpus.replay_cases.find(r=>r.case==='phase-aware-exact');
 const semantic=evidence(row.evidence);semantic.trace.termination='property_failed';assert.throws(()=>writeTraceV2(semantic,{trace:{max_blob_bytes:0}}),/termination disagrees/);assert.throws(()=>writeTraceV2(semantic,{maxMetadataBytes:17}),/termination disagrees/);
 const coverage=evidence(row.evidence);coverage.trace.steps.pop();assert.throws(()=>writeTraceV2(coverage,{maxCausesPerStep:0}),/does not cover every recorded step/);
 const boundary=evidence(row.evidence);boundary.steps[0].stateCheckCount=3;assert.throws(()=>writeTraceV2(boundary,{maxCausesPerStep:0}),/invalid phase evidence sequence or check boundary/);
 const independent=evidence(row.evidence);independent.trace.steps=[];independent.steps=[...independent.steps,...independent.steps];assert.throws(()=>writeTraceV2(independent,{trace:{max_steps:0}}),/does not cover every recorded step/);
});
test('borrowed validation snapshots nested accessors once without copying rejected payloads',()=>{
 const e=evidence(corpus.replay_cases.find(r=>r.case==='phase-aware-exact').evidence);let statuses=0,initialReads=0;const status=e.trace.initialChecks[0].status;Object.defineProperty(e.trace.initialChecks[0],'status',{get(){statuses++;if(statuses>1)throw Error('status reread');return status;}});
 const initial=e.trace.initialState;Object.defineProperty(e.trace,'initialState',{get(){initialReads++;if(initialReads>1)throw Error('initial reread');return initial;}});writeTraceV2(e);assert.equal(statuses,1);assert.equal(initialReads,1);
});
test('known unfit traces scan semantic precedence without retaining an accessor journal',()=>{
 const e=evidence(corpus.replay_cases.find(r=>r.case==='phase-aware-exact').evidence);let reads=0;const c={id:'s',get status(){reads++;return 'passed';},details:''},step={checks:[c]};e.trace.initialChecks=[];e.trace.steps=new Array(20000).fill(step);assert.throws(()=>writeTraceV2(e,{trace:{max_steps:0}}),/trace exceeds steps limit/);assert.equal(reads,20000);
 e.trace.termination='property_failed';reads=0;assert.throws(()=>writeTraceV2(e,{trace:{max_steps:0}}),/termination disagrees/);assert.equal(reads,20000);
});
test('causal structural label work is bounded by remaining metadata allowance',()=>{
 const e=evidence(corpus.replay_cases.find(r=>r.case==='phase-aware-exact').evidence);e.steps[0].causal.machine='é'.repeat(1000000);assert.throws(()=>writeTraceV2(e,{maxMetadataBytes:100}),/encoded byte limit/);
});
test('cache discard preserves ordered v1 run quota before later step quota',()=>{
 const e=evidence(corpus.replay_cases.find(r=>r.case==='phase-aware-exact').evidence);assert.throws(()=>writeTraceV2(e,{trace:{max_blob_bytes:0,max_steps:0}}),/trace exceeds blob bytes limit/);
});
