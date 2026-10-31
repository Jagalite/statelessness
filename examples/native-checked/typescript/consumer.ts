import {accepted,check,metadata,EncodeStorage,withEncodeBuffer,Recorder,checkInitial,checkTurnExact,replay,replayWithObservations,replayWithObservationsBounded,ReplayError,
 LogLevel,SnapshotPolicy,TextObserver,NoPayloads,
 readAuditTrace,writeAuditTrace,auditTraceFromPortable,auditTraceToPortable,parseTrace,stringifyTrace,
 type EncodeBuffer,type CodecModel,type BinaryLimits,type AuditTrace,type Trace,type ReplayReport,type TraceStep,type ReplayObserver} from '@jagalite/statelessness';
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
  cloneState:v=>v,cloneInput:v=>v,cloneOutput:v=>v,metadata:()=>metadata('native-checked-threshold','application-v1'),
  encodeState:legacy,encodeInput:legacy,encodeOutput:legacy,
  decodeState:bytes=>{stats.decode++;return integer(bytes);},decodeInput:bytes=>{stats.decode++;const value=integer(bytes);require(value===0||value===1,'expected zero or one');return value;},
  encodeStateInto:(value,sink)=>{stats.state++;sink.extend(ascii(String(value)));},
  encodeInputInto:(value,sink)=>{stats.input++;sink.extend(ascii(String(value)));},
  encodeOutputInto:(value,sink)=>{stats.output++;sink.extend(ascii(value));},
 };
 return {model,stats};
}
const limits:BinaryLimits={max_total_bytes:8192,max_frame_bytes:2048,max_blob_bytes:64,max_string_bytes:256,
 max_steps:16,max_checks_per_step:16,max_outputs_per_step:16,max_parameters:32,max_items:256};
function rejects(operation:()=>unknown):boolean {try{operation();return false;}catch{return true;}}
const hex=(bytes:Uint8Array)=>[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
function reportJSON(report:ReplayReport){return {outcome:report.outcome,divergence_step:report.step,divergence_field:report.field,steps_verified:report.steps_verified,failure_reproduced:report.failure_reproduced,build_matches:report.build_matches};}
function stepJSON(step:TraceStep){return {input:hex(step.input),disposition:step.disposition,outputs:step.outputs.map(hex),post_state:hex(step.postState),checks:step.checks};}
const config={strategy:'manual',seed:null,parameters:[]};
const encodes=(stats:ReturnType<typeof application>['stats'])=>stats.state+stats.input+stats.output;
const options=(maxSteps=3)=>({maxSteps,maxRetainedBytes:8192n,limits});
const count=(s:ReturnType<typeof application>['stats'])=>({initial_state:s.initial,reducer:s.step,state:s.stateChecks,transition:s.edgeChecks});
function attach(model:CodecModel<number,number,string>,state=0,origin=0n,maxSteps=3){return Recorder.withCheckedInitialAt(checkInitial(model,state),origin,config,options(maxSteps));}
function token(model:CodecModel<number,number,string>,before:number,sequence:bigint,policy={stateEvery:1n,transitionChecks:true},output='changed'){return checkTurnExact(model,before,1,accepted(before+1,[output]),sequence,policy);}
function controls(){
 const {model,stats}=application(), initial=checkInitial(model,1), before=JSON.stringify(count(stats));
 const origin=Recorder.withCheckedInitialAt(initial,10n,config,options());require(JSON.stringify(count(stats))===before,'attach rechecked');
 for(const [sequence,state] of [[11n,1],[12n,2]] as const){const t=token(model,state,sequence),before=JSON.stringify(count(stats));origin.observeChecked(t);require(JSON.stringify(count(stats))===before,'handoff rechecked');}
 require(origin.isFrozen()&&hex(origin.snapshot().initialState)==='31'&&origin.snapshot().config.parameters.some(([k,v])=>k==='stateless.monitor.sequence_origin'&&v==='10'),'origin lost');
 const start=checkInitial(model,0),first=token(model,0,1n),left=Recorder.withCheckedInitial(start,config,options()),right=Recorder.withCheckedInitial(start,config,options());left.observeChecked(first);right.observeChecked(first);
 require(hex(writeAuditTrace(left.snapshot(),limits))===hex(writeAuditTrace(right.snapshot(),limits)),'token not reusable');
 for(const kind of ['sampled','duplicate','gap']){const rec=attach(model);if(kind==='duplicate')rec.observeChecked(first);const bad=kind==='duplicate'?first:token(model,0,kind==='gap'?2n:1n,kind==='sampled'?{stateEvery:2n,transitionChecks:true}:{stateEvery:1n,transitionChecks:true});
 require(rejects(()=>rec.observeChecked(bad))&&rec.isFrozen(),'invalid token not frozen');const prefix=hex(writeAuditTrace(rec.snapshot(),limits));require(rec.snapshot().steps.length===(kind==='duplicate'?1:0),'prefix changed');require(rejects(()=>rec.observeChecked(first))&&hex(writeAuditTrace(rec.snapshot(),limits))===prefix,'freeze overwritten');}
 const forged=attach(model);require(rejects(()=>forged.observeChecked(first.observation() as never)),'diagnostic accepted');
 require(rejects(()=>{(first as unknown as {sequence:bigint}).sequence=9n;})&&first.sequence===1n&&first.transition.state===1&&first.intoChecks().length===3,'token mutation accepted');
 const bounded=attach(model);bounded.observeChecked(first);const oversize=token(model,1,2n,undefined,'x'.repeat(65)),beforeError=JSON.stringify(count(stats));require(rejects(()=>bounded.observeChecked(oversize))&&JSON.stringify(count(stats))===beforeError,'bounded accepted or checks repeated');
 const prefix=bounded.snapshot(),bytes=writeAuditTrace(prefix,limits);require(prefix.termination==='model_error'&&prefix.steps.length===1&&hex(writeAuditTrace(readAuditTrace(bytes,limits),limits))===hex(bytes),'incoherent bounded prefix');
 const ring=attach(model,0,0n,1);ring.observeChecked(first);ring.observeChecked(token(model,1,2n));const audit=ring.snapshot();require(hex(audit.initialState)==='31'&&audit.initialChecks.length===2&&audit.initialChecks.every(c=>c.id!=='edge.checked')&&ring.evictedSteps()===1n,'ring promoted edge checks');
 return {controls:{check_once:true,attach_rechecks:false,handoff_rechecks:false,token_reused:true,sampled_rejected:true,duplicate_rejected:true,gap_rejected:true,origin_preserved:true,ring_state_prefix_only:true,bounded_error_prefix:true,forged_diagnostic_rejected:true,accessor_ownership:true},ring:{rich:observed(model,audit,false),bounded:observed(model,audit,true),retained_config:retainedConfig(audit)}};
}
function replaySession(run:(observer:ReplayObserver<number,number,string>)=>ReplayReport){
 const observations:Record<string,unknown>[]=[];
 const observer:ReplayObserver<number,number,string>=(observation,report)=>{
  if(observation.kind==='initial')observations.push({kind:'initial',sequence:0,state:observation.state,checks:observation.checks,expected_state:hex(observation.expected_state),actual_state:hex(observation.actual_state),expected_checks:observation.expected_checks,differences:observation.differences,report:reportJSON(report)});
  else if(observation.kind==='turn'){
   const actual=observation.actual;
   observations.push({kind:'turn',sequence:actual.sequence,before:actual.before,input:actual.input,transition:actual.transition,checks:actual.checks,state_check_count:actual.state_check_count,
    expected:stepJSON(observation.expected),actual_input:hex(observation.actual_input),actual_outputs:observation.actual_outputs.map(hex),actual_state:hex(observation.actual_state),differences:observation.differences,report:reportJSON(report)});
  }else throw Error('unexpected replay error observation');
 };
 const report=run(observer);
 return {report:reportJSON(report),observations};
}
function observed(model:CodecModel<number,number,string>,audit:AuditTrace,bounded:boolean){
 return replaySession(observer=>bounded?replayWithObservationsBounded(model,audit,{},limits,observer):replayWithObservations(model,auditTraceToPortable(audit).trace,{},observer));
}
let replayLegacy=0,replayEncodes=0;
function suffixReplays(source:Trace|AuditTrace){
 const rich=application(),bounded=application();
 const portable='config' in source?auditTraceToPortable(source).trace:source;
 const richSession=replaySession(observer=>replayWithObservations(rich.model,portable,{},observer));
 // The public TypeScript bounded API requires AuditTrace. Portable JSON has no
 // retained config; supply only the default execution config through its public converter.
 const audit='config' in source?source:auditTraceFromPortable(source,config,limits);
 const boundedSession=observed(bounded.model,audit,true);
 const rich_counts=count(rich.stats),bounded_counts=count(bounded.stats);
 const expected=JSON.stringify({initial_state:0,reducer:1,state:2,transition:1});
 require(JSON.stringify(rich_counts)===expected&&JSON.stringify(bounded_counts)===expected,'suffix replay work counts differ');
 replayLegacy+=rich.stats.legacy+bounded.stats.legacy;replayEncodes+=encodes(rich.stats)+encodes(bounded.stats);
 return {rich:richSession,bounded:boundedSession,rich_counts,bounded_counts};
}
const [mode,path]=process.argv.slice(2);require(path&&process.argv.length===4,'expected record PREFIX, record-suffix PREFIX, record-failing-suffix PREFIX, replay BIN, replay-json JSON, replay-suffix BIN, or replay-suffix-json JSON');
const {model,stats}=application();let receipt:Record<string,unknown>;
const retainedConfig=(audit:AuditTrace)=>({strategy:audit.config.strategy,seed:audit.config.seed===null?null:String(audit.config.seed),parameters:audit.config.parameters});
if(mode==='record'){
 const initial=checkInitial(model,0),rec=Recorder.withCheckedInitial(initial,config,options());
 const certificates=[];for(const sequence of [1,2,3]){const actual=model.step(sequence-1,1),checked=checkTurnExact(model,sequence-1,1,actual,BigInt(sequence)),before=JSON.stringify(count(stats));certificates.push(checked);rec.observeChecked(checked);require(JSON.stringify(count(stats))===before,'handoff repeated application');}
 const audit=rec.snapshot(),main_counts=count(stats);require(JSON.stringify(main_counts)===JSON.stringify({initial_state:0,reducer:3,state:4,transition:3}),'check-once counts differ');const extra=controls();
 writeFileSync(path+'.bin',writeAuditTrace(audit,limits));writeFileSync(path+'.json',stringifyTrace(auditTraceToPortable(audit).trace));
 receipt={steps:audit.steps.length,termination:audit.termination,rich:observed(model,audit,false),bounded:observed(model,audit,true),retained_config:retainedConfig(audit),...extra,main_counts,checked:{initial_checks:initial.stateCheckCount,state_check_count:certificates[0].stateCheckCount,full_checking:certificates[0].fullChecking,sequence:String(certificates[0].sequence)}};
}else if(mode==='record-suffix'||mode==='record-failing-suffix'){
 const failing=mode==='record-failing-suffix',turns=failing?3:2;
 const observer_prefix='suffix-observer\n',observerBytes=[...ascii(observer_prefix)];
 const observer=new TextObserver<number,number,string>({
  write(bytes){for(const byte of bytes)observerBytes.push(byte);return bytes.length;},flush(){},
 },'suffix',{level:LogLevel.Trace,snapshots:SnapshotPolicy.EveryTransition,maxRecordBytes:1024},new NoPayloads());
 let state=0;
 const initial=checkInitial(model,state),attachCounts=JSON.stringify(count(stats));
 const rec=Recorder.withCheckedInitialAt(initial,40n,config,options(1));
 require(JSON.stringify(count(stats))===attachCounts,'suffix attach repeated application work');
 const checkpointEncodes=encodes(stats);
 observer.observe(model,{kind:'checkpoint',sequence:40n,state:initial.state,checks:initial.checks});
 require(JSON.stringify(count(stats))===attachCounts&&encodes(stats)===checkpointEncodes,'suffix checkpoint observer repeated application work or encoded payloads');
 const certificates=[];
 for(const sequence of failing?[41n,42n,43n]:[41n,42n]){
  const before=state,actual=model.step(before,1),checked=checkTurnExact(model,before,1,actual,sequence);
  certificates.push(checked);
  const beforeCounts=JSON.stringify(count(stats));
  rec.observeChecked(checked);
  require(JSON.stringify(count(stats))===beforeCounts,'suffix recorder repeated application work');
  const beforeEncodes=encodes(stats);
  observer.observe(model,{kind:'transition',sequence,before,input:1,transition:actual,checks:checked.checks});
  require(JSON.stringify(count(stats))===beforeCounts&&encodes(stats)===beforeEncodes,'suffix turn observer repeated application work or encoded payloads');
  state=actual.state;
 }
 observer.flush();
 // Capture application work before the independent rich/bounded replay passes.
 const audit=rec.snapshot(),main_counts=count(stats);
 require(JSON.stringify(main_counts)===JSON.stringify({initial_state:0,reducer:turns,state:turns+1,transition:turns}),'suffix check-once counts differ');
 require(state===turns&&audit.steps.length===1&&audit.termination===(failing?'property_failed':'interrupted')&&rec.isFrozen()===failing,'suffix retention or termination differs');
 require(hex(audit.initialState)===hex(ascii(String(turns-1)))&&JSON.stringify(audit.initialChecks)===JSON.stringify([check('threshold.bound'),check('state.appended')]),'suffix checkpoint promoted non-state checks');
 const terminalChecks=[failing?check('threshold.bound','failed','at least three'):check('threshold.bound'),check('state.appended'),check('edge.checked')];
 require(hex(audit.steps[0].postState)===hex(ascii(String(turns)))&&JSON.stringify(audit.steps[0].checks)===JSON.stringify(terminalChecks),'suffix retained turn differs');
 require(rec.observedSteps()===BigInt(turns)&&rec.evictedSteps()===BigInt(turns-1),'suffix local counters differ');
 for(const [key,value] of [['sequence_origin','40'],['observed_steps',String(turns)],['evicted_steps',String(turns-1)],['checkpoint_after_sequence',String(turns-1)]]){
  require(audit.config.parameters.some(([k,v])=>k==='stateless.monitor.'+key&&v===value),'suffix retained metadata differs: '+key);
 }
 const observer_text=new TextDecoder().decode(Uint8Array.from(observerBytes));
 const freeze:Record<string,unknown>={};
 if(failing){
  const frozenBinary=hex(writeAuditTrace(audit,limits)),frozenPortable=stringifyTrace(auditTraceToPortable(audit).trace);
  const frozenCounts=JSON.stringify(stats),diagnosticOffset=observerBytes.length;
  // A supplied diagnostic is not a new checked turn or continuity evidence.
  observer.observe(model,{kind:'checkpoint',sequence:44n,state:999,checks:[]});
  observer.flush();
  const freeze_observer_text=new TextDecoder().decode(Uint8Array.from(observerBytes.slice(diagnosticOffset)));
  require(freeze_observer_text==='Trace stream="suffix" event=checkpoint sequence=44 checks=0 state=unavailable\n','suffix frozen diagnostic differs');
  require(JSON.stringify(stats)===frozenCounts,'frozen diagnostic repeated callbacks or codecs');
  const afterDiagnostic=rec.snapshot();
  require(hex(writeAuditTrace(afterDiagnostic,limits))===frozenBinary&&stringifyTrace(auditTraceToPortable(afterDiagnostic).trace)===frozenPortable,'first failure artifact changed after diagnostic');
  require(rejects(()=>rec.observeChecked(certificates[certificates.length-1])),'frozen recorder accepted repeated final token');
  const after=rec.snapshot();
  require(rec.isFrozen()&&hex(writeAuditTrace(after,limits))===frozenBinary&&stringifyTrace(auditTraceToPortable(after).trace)===frozenPortable,'first failure artifact changed after diagnostic or duplicate handoff');
  require(JSON.stringify(stats)===frozenCounts,'frozen diagnostic or duplicate handoff repeated callbacks or codecs');
  freeze.freeze_observer_text=freeze_observer_text;
  freeze.freeze_verified=true;
 }
 writeFileSync(path+'.bin',writeAuditTrace(audit,limits));writeFileSync(path+'.json',stringifyTrace(auditTraceToPortable(audit).trace));
 receipt={steps:audit.steps.length,termination:audit.termination,...suffixReplays(audit),retained_config:retainedConfig(audit),main_counts,
  checked:{initial_checks:initial.stateCheckCount,state_check_count:certificates[0].stateCheckCount,full_checking:certificates[0].fullChecking,sequence:String(certificates[0].sequence)},observer_prefix,observer_text,...freeze};
}else if(mode==='replay'){
 const audit=readAuditTrace(readFileSync(path),limits);
 receipt={rich:observed(model,audit,false),bounded:observed(model,audit,true),retained_config:retainedConfig(audit)};
}else if(mode==='replay-json')receipt=reportJSON(replay(model,parseTrace(readFileSync(path,'utf8'))));
else if(mode==='replay-suffix'){
 const audit=readAuditTrace(readFileSync(path),limits);
 receipt={...suffixReplays(audit),retained_config:retainedConfig(audit)};
}else if(mode==='replay-suffix-json')receipt=suffixReplays(parseTrace(readFileSync(path,'utf8')));
else throw Error('unknown mode');
receipt.producer='typescript';receipt.mode=mode;receipt.legacy_encoder_calls=stats.legacy+replayLegacy;receipt.codec_into_calls=encodes(stats)+replayEncodes;
console.log(JSON.stringify(receipt));
