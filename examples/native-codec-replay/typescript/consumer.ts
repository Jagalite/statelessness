import {accepted,check,metadata,EncodeStorage,withEncodeBuffer,recordAudit,replay,replayWithObservations,replayWithObservationsBounded,ReplayError,
 readAuditTrace,writeAuditTrace,auditTraceToPortable,parseTrace,stringifyTrace,
 type EncodeBuffer,type CodecModel,type BinaryLimits,type AuditTrace,type ReplayReport,type TraceStep,type ReplayObserver} from '@jagalite/statelessness';
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
 const stats={state:0,input:0,output:0,legacy:0,decode:0,step:0};
 const legacy=():never=>{stats.legacy++;throw Error('legacy encoder called');};
 const model:CodecModel<number,number,string>={
  initialState:()=>0,step:(state,input)=>{stats.step++;require(input===0||input===1,'expected zero or one');return accepted(state+input,input===1?[overrun?'x'.repeat(65):divergent?'different':'changed']:[]);},
  checkState:state=>[state>=3?check('threshold.bound','failed','at least three'):check('threshold.bound')],
  cloneState:v=>v,cloneInput:v=>v,cloneOutput:v=>v,metadata:()=>metadata('native-codec-replay-threshold','application-v1'),
  encodeState:legacy,encodeInput:legacy,encodeOutput:legacy,
  decodeState:bytes=>{stats.decode++;return integer(bytes);},decodeInput:bytes=>{stats.decode++;const value=integer(bytes);require(value===0||value===1,'expected zero or one');return value;},
  encodeStateInto:(value,sink)=>{stats.state++;sink.extend(ascii(String(value)));},
  encodeInputInto:(value,sink)=>{stats.input++;sink.extend(ascii(String(value)));},
  encodeOutputInto:(value,sink)=>{stats.output++;sink.extend(ascii(value));},
 };
 return {model,stats};
}
const limits:BinaryLimits={max_total_bytes:4096,max_frame_bytes:1024,max_blob_bytes:64,max_string_bytes:128,
 max_steps:16,max_checks_per_step:16,max_outputs_per_step:16,max_parameters:16,max_items:128};
function rejects(operation:()=>unknown):boolean {try{operation();return false;}catch{return true;}}
function bufferControls(){
 const storage=new EncodeStorage();withEncodeBuffer(storage,2,sink=>sink.extend(Uint8Array.of(99,99)));
 let escaped:EncodeBuffer|undefined,nested:EncodeBuffer|undefined,cleared=false,nestedExpired=false,parentActive=false;
 withEncodeBuffer(storage,10,sink=>{
  cleared=sink.length===0;escaped=sink;sink.extend(Uint8Array.of(7));
  sink.framed(child=>{nested=child;child.extend(Uint8Array.of(8));});
  nestedExpired=rejects(()=>nested!.extend(new Uint8Array()));parentActive=sink.length===10;sink.finish();
 });
 const expired=rejects(()=>escaped!.extend(new Uint8Array()))&&rejects(()=>escaped!.length)&&rejects(()=>escaped!.snapshot())&&rejects(()=>escaped!.finish());
 const framing=JSON.stringify([...storage.snapshot()])===JSON.stringify([7,1,0,0,0,0,0,0,0,8]);
 let sticky=false;const rejected=rejects(()=>withEncodeBuffer(storage,1,sink=>{
  rejects(()=>sink.extend(Uint8Array.of(1,2)));sticky=rejects(()=>sink.extend(new Uint8Array()))&&rejects(()=>sink.finish());
 }));
 require(cleared&&framing&&expired&&nestedExpired&&parentActive&&sticky&&rejected,'buffer controls failed');
 const nestedStorage=new EncodeStorage();withEncodeBuffer(nestedStorage,17,sink=>sink.framed(child=>child.framed(inner=>inner.extend(Uint8Array.of(9)))));
 const nestedFraming=JSON.stringify([...nestedStorage.snapshot()])===JSON.stringify([9,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,9]);
 const sharedBudget=rejects(()=>withEncodeBuffer(new EncodeStorage(),8,sink=>sink.framed(child=>child.extend(Uint8Array.of(1)))));
 require(nestedFraming&&sharedBudget,'framing controls failed');
 return {clear_entry:cleared,sticky_finish:sticky&&rejected,framed_shared_budget:framing&&sharedBudget,nested_framing:nestedFraming,escaped_sink_expired:expired,nested_child_expired:nestedExpired,outer_active_after_nested:parentActive};
}
const hex=(bytes:Uint8Array)=>[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
function reportJSON(report:ReplayReport){return {outcome:report.outcome,divergence_step:report.step,divergence_field:report.field,steps_verified:report.steps_verified,failure_reproduced:report.failure_reproduced,build_matches:report.build_matches};}
function stepJSON(step:TraceStep){return {input:hex(step.input),disposition:step.disposition,outputs:step.outputs.map(hex),post_state:hex(step.postState),checks:step.checks};}
const config={strategy:'manual',seed:null,parameters:[]};
const encodes=(stats:ReturnType<typeof application>['stats'])=>stats.state+stats.input+stats.output;
function controls(audit:AuditTrace){
 const buffer=bufferControls(),preflight=application();
 const preflightRejected=rejects(()=>replayWithObservationsBounded(preflight.model,audit,{}, {...limits,max_steps:2},()=>{}));
 require(preflightRejected&&preflight.stats.decode===0&&preflight.stats.step===0&&preflight.stats.legacy===0&&encodes(preflight.stats)===0,'bounded preflight called decoder/reducer/encoder');
 const oversize=application(true);let retained=false;
 try{replayWithObservationsBounded(oversize.model,audit,{},limits,()=>{});throw Error('oversize replay succeeded');}
 catch(error){
  require(error instanceof ReplayError,'expected ReplayError');const actual=error.actual;
  retained=actual?.kind==='turn'&&actual.sequence===1&&actual.before===0&&actual.input===1&&actual.transition.state===1&&JSON.stringify(actual.transition.outputs)===JSON.stringify(['x'.repeat(65)])&&JSON.stringify(actual.checks)===JSON.stringify([check('threshold.bound')])&&actual.state_check_count===1;
 }
 require(oversize.stats.step===1&&retained,'oversize actual missing');
 const last=audit.steps[audit.steps.length-1],changed:AuditTrace={...audit,steps:[...audit.steps.slice(0,-1),{...last,checks:[check('threshold.bound','failed','different expected detail')]}]};
 const divergent=application(),divergence=replayWithObservations(divergent.model,auditTraceToPortable(changed).trace,{},()=>{});
 const richDivergence=divergence.outcome==='diverged'&&divergence.field==='checks'&&divergence.step===3&&divergence.failure_reproduced;
 require(richDivergence,'rich divergence missing');
 return {...buffer,preflight_decode_calls:preflight.stats.decode,oversize_reducer_calls:oversize.stats.step,oversize_actual_retained:retained,
  legacy_encoder_calls:preflight.stats.legacy+oversize.stats.legacy+divergent.stats.legacy,codec_into_calls:encodes(preflight.stats)+encodes(oversize.stats)+encodes(divergent.stats),rich_divergence:richDivergence};
}
function observed(model:CodecModel<number,number,string>,audit:AuditTrace,bounded:boolean){
 const observations:Record<string,unknown>[]=[];
 const observer:ReplayObserver<number,number,string>=(observation,report)=>{
  if(observation.kind==='initial')observations.push({kind:'initial',sequence:0,state:observation.state,checks:observation.checks,expected_state:hex(observation.expected_state),actual_state:hex(observation.actual_state),expected_checks:observation.expected_checks,differences:observation.differences,report:reportJSON(report)});
  else if(observation.kind==='turn'){
   const actual=observation.actual;
   observations.push({kind:'turn',sequence:actual.sequence,before:actual.before,input:actual.input,transition:actual.transition,checks:actual.checks,state_check_count:actual.state_check_count,
    expected:stepJSON(observation.expected),actual_input:hex(observation.actual_input),actual_outputs:observation.actual_outputs.map(hex),actual_state:hex(observation.actual_state),differences:observation.differences,report:reportJSON(report)});
  }else throw Error('unexpected replay error observation');
 };
 const report=bounded?replayWithObservationsBounded(model,audit,{},limits,observer):replayWithObservations(model,auditTraceToPortable(audit).trace,{},observer);
 return {report:reportJSON(report),observations};
}
const [mode,path]=process.argv.slice(2);require(path&&process.argv.length===4,'expected record PREFIX, replay BIN, or replay-json JSON');
const {model,stats}=application();let receipt:Record<string,unknown>;
const retainedConfig=(audit:AuditTrace)=>({strategy:audit.config.strategy,seed:audit.config.seed===null?null:String(audit.config.seed),parameters:audit.config.parameters});
if(mode==='record'){
 const audit=recordAudit(model,[1,1,1],config,100000,limits);
 writeFileSync(path+'.bin',writeAuditTrace(audit,limits));writeFileSync(path+'.json',stringifyTrace(auditTraceToPortable(audit).trace));
 receipt={steps:audit.steps.length,termination:audit.termination,rich:observed(model,audit,false),bounded:observed(model,audit,true),retained_config:retainedConfig(audit),controls:controls(audit)};
}else if(mode==='replay'){
 const audit=readAuditTrace(readFileSync(path),limits);
 receipt={rich:observed(model,audit,false),bounded:observed(model,audit,true),retained_config:retainedConfig(audit)};
}else if(mode==='replay-json')receipt=reportJSON(replay(model,parseTrace(readFileSync(path,'utf8'))));
else throw Error('unknown mode');
receipt.producer='typescript';receipt.mode=mode;receipt.legacy_encoder_calls=stats.legacy;receipt.codec_into_calls=encodes(stats);
console.log(JSON.stringify(receipt));
