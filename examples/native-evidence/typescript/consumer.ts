import {accepted,check,metadata,checkInitial,checkTurnExact,EvidenceRecorder,readArtifact,artifactTrace,writeTraceV2,writeAuditTrace,
 replayTraceV2,verifyUpgrade,checkPhase,diagnosticJSON,auditTraceToPortable,stringifyTrace,
 type CodecModel,type BinaryLimits,type EvidenceLimits,type TraceV2,type CausalContext,type Provenance,type AuditTrace,type ReplayReport} from '@jagalite/statelessness';
import {writeRawEvidenceNew,exportDiagnosticNew} from '@jagalite/statelessness/node-evidence';
declare const process:{argv:string[]};
// @ts-ignore Node runtime built-in; no development dependency is required.
import {readFileSync,writeFileSync} from 'node:fs';
function require(condition:unknown,message:string):asserts condition {if(!condition)throw Error(message);}
const ascii=(value:string)=>new TextEncoder().encode(value);
function integer(bytes:Uint8Array):number {
 require(bytes.length>0&&bytes.every(b=>b>=48&&b<=57),'invalid ASCII integer');
 const value=Number(new TextDecoder().decode(bytes));require(Number.isSafeInteger(value),'invalid ASCII integer');return value;
}
function application(overrun=false,divergent=false){
 const stats={state:0,input:0,output:0,legacy:0,decode:0,step:0,initial:0,stateChecks:0,edgeChecks:0};
 const legacy=():never=>{stats.legacy++;throw Error('legacy encoder called');};
 const model:CodecModel<number,number,string>={
  initialState:()=>{stats.initial++;return 0;},step:(state,input)=>{stats.step++;require(input===0||input===1,'expected zero or one');return accepted(state+input,input===1?[overrun?'x'.repeat(65):divergent?'different':'changed']:[]);},
  checkState:()=>{throw Error('legacy state check');},checkTransition:()=>{throw Error('legacy edge check');},
  checkStateInto:(state,sink)=>{stats.stateChecks++;sink.push(state>=3?check('threshold.bound','failed','at least three'):check('threshold.bound'));sink.push(check('state.appended'));},
  checkTransitionInto:(_before,_input,_turn,sink)=>{stats.edgeChecks++;sink.push(check('edge.checked'));},
  cloneState:v=>v,cloneInput:v=>v,cloneOutput:v=>v,metadata:()=>metadata('native-evidence-threshold','application-v1'),
  encodeState:legacy,encodeInput:legacy,encodeOutput:legacy,
  decodeState:bytes=>{stats.decode++;return integer(bytes);},decodeInput:bytes=>{stats.decode++;const value=integer(bytes);require(value===0||value===1,'expected zero or one');return value;},
  encodeStateInto:(value,sink)=>{stats.state++;sink.extend(ascii(String(value)));},
  encodeInputInto:(value,sink)=>{stats.input++;sink.extend(ascii(String(value)));},
  encodeOutputInto:(value,sink)=>{stats.output++;sink.extend(ascii(value));},
 };
 return {model,stats};
}
const limits:BinaryLimits={max_total_bytes:16384,max_frame_bytes:4096,max_blob_bytes:64,max_string_bytes:256,
 max_steps:16,max_checks_per_step:16,max_outputs_per_step:16,max_parameters:32,max_items:256};

const el:EvidenceLimits={trace:limits,maxMetadataBytes:1024,maxLabels:8,maxLabelBytes:128,maxCausesPerStep:4,maxTotalCauses:8};
const provenance:Provenance={run:'installed-evidence-run',epoch:7n,manifest:'manifest-v1',source:'consumer-source',environment:[['region','test'],['note','é']]};
const emptyProvenance:Provenance={run:'',epoch:0n,manifest:null,source:null,environment:[]};
const empty=():CausalContext=>({machine:null,generation:null,correlation:null,logicalTime:null,causes:[]});
const context=(seq:number,full=true):CausalContext=>({...empty(),machine:full?'worker-a':null,generation:full?9n:null,correlation:full?'request-1':null,logicalTime:full?BigInt(10+seq):null,causes:seq===1?[]:[{sequence:BigInt(seq-1),output:0}]});
const options=(maxSteps=3)=>({maxSteps,maxRetainedBytes:16384n,limits});
const config={strategy:'manual',seed:null,parameters:[]};
const count=(s:ReturnType<typeof application>['stats'])=>({initial_state:s.initial,reducer:s.step,state:s.stateChecks,transition:s.edgeChecks});
const attach=(m:CodecModel<number,number,string>,n=3,l=el,p=provenance)=>new EvidenceRecorder(checkInitial(m,0),0n,config,options(n),p,l);
const token=(m:CodecModel<number,number,string>,before:number,seq:number)=>checkTurnExact(m,before,1,accepted(before+1,['changed']),BigInt(seq));
const hex=(bytes:Uint8Array)=>[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
const configJSON=(t:AuditTrace)=>({strategy:t.config.strategy,seed:t.config.seed===null?null:String(t.config.seed),parameters:t.config.parameters});
const checkJSON=(cs:AuditTrace['initialChecks'])=>cs.map(c=>({id:c.id,status:c.status,reason:c.details||null}));
function normalize(v:TraceV2){const t=v.trace;return {trace:{metadata:t.metadata,config:configJSON(t),initial_state:hex(t.initialState),initial_checks:checkJSON(t.initialChecks),steps:t.steps.map(s=>({input:hex(s.input),disposition:{kind:s.disposition.kind,reason:s.disposition.reason||null},outputs:s.outputs.map(hex),post_state:hex(s.postState),checks:checkJSON(s.checks)})),termination:{kind:t.termination,reason:t.error||null}},checkpoint_after:String(v.checkpointAfter),provenance:{...v.provenance,epoch:String(v.provenance.epoch)},steps:v.steps.map(s=>({sequence:String(s.sequence),state_check_count:s.stateCheckCount,causal:{machine:s.causal.machine,generation:s.causal.generation===null?null:String(s.causal.generation),correlation:s.causal.correlation,logical_time:s.causal.logicalTime===null?null:String(s.causal.logicalTime),causes:s.causal.causes.map(o=>({sequence:String(o.sequence),output:o.output}))}}))};}
function reportJSON(r:ReplayReport){return {outcome:r.outcome,divergence_step:r.step,divergence_field:r.field,steps_verified:r.steps_verified,failure_reproduced:r.failure_reproduced,build_matches:r.build_matches};}
function rejected(f:()=>unknown,contains?:string){try{f();}catch(e){if(contains)require(String(e).toLowerCase().includes(contains),'wrong rejection: '+String(e));return;}throw Error('expected rejection');}
const policy={maxSteps:2,maxBytes:4096,propertyIds:['threshold.bound']};
const text=(b:Uint8Array)=>new TextDecoder().decode(b);
function controls(v:TraceV2,prefix:string){
 for(const kind of ['metadata','cause','trace']){const {model}=application(),l={...el,maxMetadataBytes:kind==='metadata'?70:256,maxTotalCauses:kind==='cause'?1:8},r=attach(model,kind==='trace'?1:3,l,emptyProvenance);
  for(let seq=1;seq<=(kind==='trace'?2:3);seq++)r.observe(token(model,seq-1,seq),kind==='cause'?context(seq,false):empty());const x=r.snapshot();
  require(x.checkpointAfter===(kind==='cause'?2n:1n)&&x.steps.length===x.trace.steps.length&&x.trace.initialChecks.length===2&&x.trace.initialChecks.every(c=>c.id!=='edge.checked')&&replayTraceV2(x,model,{},l).outcome==='exact','coordinated eviction failed');
 }
 const {model,stats}=application(),l={...el,maxMetadataBytes:62},r=attach(model,3,l,emptyProvenance);r.observe(token(model,0,1),empty());rejected(()=>r.observe(token(model,1,2),{...empty(),causes:[{sequence:1n,output:99}]}),'output');const prefixEvidence=r.snapshot();writeTraceV2(prefixEvidence,l);require(r.isFrozen()&&prefixEvidence.checkpointAfter===0n&&prefixEvidence.steps.length===1,'invalid cause corrupted prefix');
 const changed={...v,steps:[{...v.steps[0],stateCheckCount:1},...v.steps.slice(1)]},negative=replayTraceV2(changed,model,{},el);require(negative.outcome==='diverged'&&negative.field==='check_phase'&&negative.step===1&&negative.steps_verified===0&&!negative.failure_reproduced,'phase comparison bypassed');
 const up=verifyUpgrade(model,v.trace,provenance,0n,{},el);require(replayTraceV2(up,model,{},el).outcome==='exact'&&up.steps.every(s=>s.causal.machine===null&&s.causal.causes.length===0),'upgrade fabricated labels');
 const a={kind:'v2' as const,evidence:v},bytes=diagnosticJSON(a,policy);require(hex(diagnosticJSON(a,{...policy,maxBytes:bytes.length}))===hex(bytes),'diagnostic fit');rejected(()=>diagnosticJSON(a,{...policy,maxBytes:bytes.length-1}));const zero=JSON.parse(text(diagnosticJSON(a,{...policy,maxSteps:0})));require(zero.observations.length===1&&zero.truncated,'diagnostic omitted initial');
 exportDiagnosticNew(prefix+'.diagnostic',a,policy);rejected(()=>exportDiagnosticNew(prefix+'.diagnostic',a,policy));require(hex(readFileSync(prefix+'.diagnostic'))===hex(bytes),'diagnostic overwritten');rejected(()=>writeRawEvidenceNew(prefix+'.v2',v,el));const before=JSON.stringify(count(stats)),legacy=readArtifact(writeAuditTrace(v.trace,limits),el);require(legacy.kind==='v1'&&checkPhase(legacy,0,0)==='initial_state'&&checkPhase(legacy,1,0)===null&&JSON.stringify(count(stats))===before,'passive v1 phases invented');
 return {controls:{check_once:true,coordinated_metadata_eviction:true,coordinated_cause_eviction:true,coordinated_trace_eviction:true,source_index_before_eviction:true,phase_divergence:true,verified_upgrade:true,diagnostic_exact:true,diagnostic_caps:true,exclusive_private_files:true,no_overwrite:true,passive_v1_no_execution:true},diagnostic_text:text(bytes)};
}
const [mode,path,out]=process.argv.slice(2);require(path,'missing path');const {model,stats}=application();let receipt:Record<string,unknown>;
function destinationWrite(f:()=>void):void { try { f(); } catch (error) { console.error(`N11_DESTINATION_CREATE_ERROR ${String(error)}`); throw error; } }
if(mode==='record'){const r=attach(model);for(const seq of [1,2,3]){const actual=model.step(seq-1,1),checked=checkTurnExact(model,seq-1,1,actual,BigInt(seq)),before=JSON.stringify(count(stats));r.observe(checked,context(seq));require(JSON.stringify(count(stats))===before,'handoff reexecuted');}const v=r.snapshot(),main_counts=count(stats);require(JSON.stringify(main_counts)===JSON.stringify({initial_state:0,reducer:3,state:4,transition:3}),'check-once count');writeRawEvidenceNew(path+'.v2',v,el);writeFileSync(path+'.v1',writeAuditTrace(v.trace,limits));writeFileSync(path+'.json',stringifyTrace(auditTraceToPortable(v.trace).trace));receipt={evidence:normalize(v),report:reportJSON(replayTraceV2(v,model,{},el)),retained_config:configJSON(v.trace),main_counts,...controls(v,path)};
}else if(mode==='read-v1'){const a=readArtifact(readFileSync(path),el);require(a.kind==='v1','expected v1');receipt={version:1,initial_phase:checkPhase(a,0,0),turn_phase:checkPhase(a,1,0),execution_calls:stats.initial+stats.step+stats.stateChecks+stats.edgeChecks,retained_config:configJSON(artifactTrace(a))};
}else if(mode==='replay'||mode==='upgrade'){const a=readArtifact(readFileSync(path),el);let v:TraceV2;if(mode==='replay'){require(a.kind==='v2','expected v2');v=a.evidence;}else v=verifyUpgrade(model,artifactTrace(a),provenance,0n,{},el);destinationWrite(()=>writeRawEvidenceNew(out,v,el));receipt={version:2,evidence:normalize(v),report:reportJSON(replayTraceV2(v,model,{},el)),retained_config:configJSON(v.trace)};
}else if(mode==='diagnostic'){const a=readArtifact(readFileSync(path),el),bytes=diagnosticJSON(a,policy);destinationWrite(()=>exportDiagnosticNew(out,a,policy));rejected(()=>exportDiagnosticNew(out,a,policy));require(hex(readFileSync(out))===hex(bytes),'diagnostic overwritten');receipt={diagnostic_text:text(bytes),execution_calls:stats.initial+stats.step+stats.stateChecks+stats.edgeChecks,no_overwrite:true};
}else throw Error('unknown mode');
receipt.producer='typescript';receipt.mode=mode;receipt.legacy_encoder_calls=stats.legacy;receipt.codec_into_calls=stats.state+stats.input+stats.output;console.log(JSON.stringify(receipt));
