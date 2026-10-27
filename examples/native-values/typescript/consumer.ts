import {accepted,check,metadata,checkInitial,checkTurnExact,EvidenceRecorder,readArtifact,writeAuditTrace,replayTraceV2,auditTraceToPortable,stringifyTrace,valueCodecs as vc,logicalLayout,decodeValue,some,NONE,customValueSchema,
 type CodecModel,type BinaryLimits,type EvidenceLimits,type TraceV2,type Provenance,type CausalContext,type AuditTrace,type ReplayReport,type Option} from '@jagalite/statelessness';
import {writeRawEvidenceNew} from '@jagalite/statelessness/node-evidence';
import {contract,runValues,hex} from './values.js';
// @ts-ignore Node runtime builtin; no development dependency is needed.
import {readFileSync,writeFileSync} from 'node:fs';
declare const process:{argv:string[]};
function require(v:unknown,why:string):asserts v{if(!v)throw Error(why);}
type State=Option<bigint>[];interface EncodeOnlyOutput{code:number;message:string}
const inputCodec=vc.option(vc.u64,logicalLayout(16,'measured Option<u64>'));
const stateCodec=vc.vector(inputCodec),outputFields=vc.tuple([vc.u8,vc.string] as const);
const outputCodec=customValueSchema<EncodeOnlyOutput>({name:'application-output',encodeInto:(v,out)=>outputFields.encodeRaw([v.code,v.message],out)});
const decodeLimits=Object.fromEntries(Object.entries(contract.model_decode_limits).map(([k,v])=>[k,BigInt(v as string)]));
const limits:BinaryLimits={max_total_bytes:16384,max_frame_bytes:4096,max_blob_bytes:64,max_string_bytes:256,max_steps:16,max_checks_per_step:16,max_outputs_per_step:16,max_parameters:32,max_items:256};
const el:EvidenceLimits={trace:limits,maxMetadataBytes:1024,maxLabels:8,maxLabelBytes:128,maxCausesPerStep:4,maxTotalCauses:8};
const provenance:Provenance={...contract.provenance,epoch:BigInt(contract.provenance.epoch)};
const config={strategy:'manual',seed:null,parameters:[]};
function application(){const stats={initial_state:0,reducer:0,state:0,transition:0,legacy:0,into:0};const legacy=():never=>{stats.legacy++;throw Error('legacy encoder');};
 const model:CodecModel<State,Option<bigint>,EncodeOnlyOutput>={initialState:()=>{stats.initial_state++;return [];},step:(state,input)=>{stats.reducer++;return accepted([...state,input],[{code:7,message:'\ufeffchanged'}]);},checkState:()=>{throw Error('legacy state checker');},checkStateInto:(state,out)=>{stats.state++;const total=state.reduce((n,v)=>n+(v.some?v.value:0n),0n);out.push(total>=3n?check('threshold.bound','failed','at least three'):check('threshold.bound'));out.push(check('state.appended'));},checkTransitionInto:(_s,_i,_t,out)=>{stats.transition++;out.push(check('edge.checked'));},cloneState:s=>s.map(v=>v.some?some(v.value):NONE),cloneInput:v=>v.some?some(v.value):NONE,cloneOutput:v=>({...v}),metadata:()=>metadata('native-values-threshold','application-v1'),encodeState:legacy,encodeInput:legacy,encodeOutput:legacy,
 encodeStateInto:(v,out)=>{stats.into++;stateCodec.encodeInto(v,out);},encodeInputInto:(v,out)=>{stats.into++;inputCodec.encodeInto(v,out);},encodeOutputInto:(v,out)=>{stats.into++;outputCodec.encodeInto(v,out);},decodeState:b=>decodeValue(stateCodec,b,decodeLimits),decodeInput:b=>decodeValue(inputCodec,b,decodeLimits)};return {model,stats};}
const configJSON=(t:AuditTrace)=>({strategy:t.config.strategy,seed:t.config.seed===null?null:String(t.config.seed),parameters:t.config.parameters});
const checkJSON=(cs:AuditTrace['initialChecks'])=>cs.map(c=>({id:c.id,status:c.status,reason:c.details||null}));
function normalize(v:TraceV2){const t=v.trace;return {trace:{metadata:t.metadata,config:configJSON(t),initial_state:hex(t.initialState),initial_checks:checkJSON(t.initialChecks),steps:t.steps.map(s=>({input:hex(s.input),disposition:{kind:s.disposition.kind,reason:s.disposition.reason||null},outputs:s.outputs.map(hex),post_state:hex(s.postState),checks:checkJSON(s.checks)})),termination:{kind:t.termination,reason:t.error||null}},checkpoint_after:String(v.checkpointAfter),provenance:{...v.provenance,epoch:String(v.provenance.epoch)},steps:v.steps.map(s=>({sequence:String(s.sequence),state_check_count:s.stateCheckCount,causal:{machine:s.causal.machine,generation:s.causal.generation===null?null:String(s.causal.generation),correlation:s.causal.correlation,logical_time:s.causal.logicalTime===null?null:String(s.causal.logicalTime),causes:s.causal.causes.map(o=>({sequence:String(o.sequence),output:o.output}))}}))};}
function reportJSON(r:ReplayReport){return {outcome:r.outcome,divergence_step:r.step,divergence_field:r.field,steps_verified:r.steps_verified,failure_reproduced:r.failure_reproduced,build_matches:r.build_matches};}

const count=(s:ReturnType<typeof application>['stats'])=>({initial_state:s.initial_state,reducer:s.reducer,state:s.state,transition:s.transition});
const context=(seq:number):CausalContext=>({machine:'worker-a',generation:9n,correlation:'request-1',logicalTime:BigInt(10+seq),causes:seq===1?[]:[{sequence:BigInt(seq-1),output:0}]});
const [mode,...paths]=process.argv.slice(2);let receipt:Record<string,unknown>;
if(mode==='values'||mode==='transcode')receipt=runValues(mode,mode==='transcode'?paths[0]:undefined,mode==='values'?paths[0]:paths[1]);
else{const {model,stats}=application();
 if(mode==='record'){const r=new EvidenceRecorder(checkInitial(model,[]),0n,config,{maxSteps:3,maxRetainedBytes:16384n,limits},provenance,el);let state:State=[];
 for(const seq of [1,2,3]){const input=some(1n),actual=model.step(state,input),checked=checkTurnExact(model,state,input,actual,BigInt(seq)),before=JSON.stringify(count(stats));r.observe(checked,context(seq));require(JSON.stringify(count(stats))===before,'handoff reexecuted');state=actual.state;}
 const v=r.snapshot(),main_counts=count(stats);require(JSON.stringify(main_counts)===JSON.stringify({initial_state:0,reducer:3,state:4,transition:3}),'check-once counts');writeRawEvidenceNew(paths[0]+'.v2',v,el);writeFileSync(paths[0]+'.v1',writeAuditTrace(v.trace,limits));writeFileSync(paths[0]+'.json',stringifyTrace(auditTraceToPortable(v.trace).trace));receipt={evidence:normalize(v),report:reportJSON(replayTraceV2(v,model,{},el)),retained_config:configJSON(v.trace),main_counts,output_encode_only:true};
 }else if(mode==='replay'){const a=readArtifact(readFileSync(paths[0]),el);require(a.kind==='v2','expected v2');const v=a.evidence;writeRawEvidenceNew(paths[1],v,el);receipt={version:2,evidence:normalize(v),report:reportJSON(replayTraceV2(v,model,{},el)),retained_config:configJSON(v.trace)};}else throw Error('unknown operation');
 receipt.legacy_encoder_calls=stats.legacy;receipt.codec_into_calls=stats.into;}
receipt.producer='typescript';receipt.mode=mode;console.log(JSON.stringify(receipt));
