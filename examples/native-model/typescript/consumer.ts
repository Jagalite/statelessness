import {accepted,check,metadata,codecField,orderedRecord,enumCases,taggedEnum,valueCodecs,customValueSchema,bindModelCodec,Recorder,EvidenceRecorder,checkInitial,checkTurnExact,checkPhase,ModelError,readArtifact,writeAuditTrace,writeTraceV2,replayTraceV2,replayWithObservationsBounded,withEncodeBuffer,EncodeStorage,type DecodeLimits,type AuditTrace,type TraceV2,type CheckedTurn,type CausalContext} from '@jagalite/statelessness';
// @ts-ignore Node builtins supplied by the independently installed host.
import {readFileSync,writeFileSync} from 'node:fs';
// @ts-ignore Node builtins supplied by the independently installed host.
import {Buffer} from 'node:buffer';
declare const process:{argv:string[]};
function require(v:unknown,m:string):asserts v{if(!v)throw Error(m);}
const hex=(b:Uint8Array)=>Buffer.from(b).toString('hex');
const unhex=(s:string)=>{require(/^(?:[0-9a-f]{2})*$/.test(s),'invalid artifact hex');return new Uint8Array(Buffer.from(s,'hex'));};
const json=(v:unknown)=>JSON.stringify(v,(_k,x)=>typeof x==='bigint'?x.toString():x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const eq=(a:unknown,b:unknown)=>require(json(a)===json(b),'assertion mismatch: '+json([a,b]));
function rejects(f:()=>unknown,part?:string){try{f();}catch(e){if(part)require(String(e).includes(part),'wrong rejection '+String(e));return;}throw Error('expected rejection');}
type State={count:number};type Input={kind:'Add';amount:number};
const outputFailure='n13 controlled Output encoder failure';
function application(provider?:()=>Partial<DecodeLimits>){
 const stats={initial:0,reducer:0,state:0,transition:0,state_encode:0,input_encode:0,output_encode:0,state_decode:0,input_decode:0,limits:0};
 const sf=codecField<State>(), inf=codecField<Input>(),ec=enumCases<Input,'kind'>('kind');
 const state=orderedRecord<State>({name:'State',fields:[sf.value('count',valueCodecs.u32)],construct:v=>({count:v.count}),require:'both'});
 const input=taggedEnum<Input>({name:'Input',discriminator:'kind',cases:[ec.record('Add',1,{fields:[inf.value('amount',valueCodecs.u8)],construct:v=>({kind:'Add',amount:v.amount})})],require:'both'});
 const counted=<T>(schema:{encodeRaw:(v:T,o:any)=>void;decodeFrom:(r:any)=>T},kind:'state'|'input')=>customValueSchema<T>({name:kind,encodeInto(v,o){stats[`${kind}_encode`]++;schema.encodeRaw(v,o);},decodeFrom(r){stats[`${kind}_decode`]++;return schema.decodeFrom(r);}});
 let failOutput=false;
 const output=customValueSchema<number>({name:'OnlyOutput',encodeInto(v,o){stats.output_encode++;if(failOutput){o.extend(Uint8Array.of(0xee));throw new ModelError(outputFailure);}valueCodecs.u32.encodeRaw(v,o);}});
 let version=1;
 class Model {
  #marker='private-receiver';
  metadata(){require(this.#marker==='private-receiver','receiver');return {...metadata('n13-installed-model','application-v1'),codec_version:version};}
  initialState(){stats.initial++;return {count:0};}
  step(s:State,i:Input){stats.reducer++;const count=s.count+i.amount;return accepted({count},[count]);}
  checkState(){throw Error('preferred append checker bypassed');}
  checkStateInto(s:State,sink:any){stats.state++;sink.push(s.count>=3?check('threshold.bound','failed','at least three'):check('threshold.bound'));}
  checkTransitionInto(_s:State,_i:Input,_t:unknown,sink:any){stats.transition++;sink.push(check('edge.checked'));}
  cloneState(s:State){return {...s};}cloneInput(i:Input){return {...i};}cloneOutput(o:number){return o;}
 }
 const model=bindModelCodec(new Model(),{state:counted(state,'state'),input:counted(input,'input'),output,...(provider?{decodeLimits:()=>{stats.limits++;return provider();}}:{})});
 return {model,stats,setVersion:(v:number)=>{version=v;},setOutputFailure:(v:boolean)=>{failOutput=v;}};
}
const config={strategy:'manual',seed:7n,parameters:[['region','test']] as [string,string][]};
const provenance={run:'n13-installed-run',epoch:7n,manifest:'manifest-v1',source:'consumer-source',environment:[['region','test']] as [string,string][]};
const limits={max_total_bytes:16384,max_frame_bytes:4096,max_blob_bytes:64,max_string_bytes:256,max_steps:16,max_checks_per_step:16,max_outputs_per_step:16,max_parameters:32,max_items:256};
const options={maxSteps:3,maxRetainedBytes:16384n,limits};const evidenceLimits={trace:limits,maxMetadataBytes:1024,maxLabels:8,maxLabelBytes:128,maxCausesPerStep:4,maxTotalCauses:8};
const semantic=(s:ReturnType<typeof application>['stats'])=>({initial:s.initial,reducer:s.reducer,state:s.state,transition:s.transition});
const counts=(s:ReturnType<typeof application>['stats'])=>({...semantic(s),encode_state:s.state_encode,encode_input:s.input_encode,encode_output:s.output_encode,decode_state:s.state_decode,decode_input:s.input_decode,limits:s.limits});
function gates(){
 let budget=100,providerCalls=0;const a=application(()=>{providerCalls++;return {bytes:budget,work:100};});
 const {model:m,stats}=a;
 const state=unhex('01000000'),input=unhex('0101');
 eq(m.decodeState(state),{count:1});eq(m.decodeInput(input),{kind:'Add',amount:1});
 budget=4;eq(m.decodeState(state),{count:1});budget=3;rejects(()=>m.decodeState(state));budget=2;eq(m.decodeInput(input),{kind:'Add',amount:1});budget=1;rejects(()=>m.decodeInput(input));budget=100;eq(m.decodeState(state),{count:1});eq(m.decodeInput(input),{kind:'Add',amount:1});
 for(const [fn,good] of [[m.decodeState,state],[m.decodeInput,input]] as const){rejects(()=>fn(good.slice(0,-1)));rejects(()=>fn(Uint8Array.of(...good,0)));}
 const before=providerCalls;
 m.encodeState({count:1});m.encodeInput({kind:'Add',amount:1});m.encodeOutput(1);
 for(const [fn,v] of [[m.encodeStateInto,{count:1}],[m.encodeInputInto,{kind:'Add',amount:1}],[m.encodeOutputInto,1]] as const)withEncodeBuffer(new EncodeStorage(),100,o=>fn!(v as never,o));
 require(providerCalls===before,'encoder consulted limits');
 a.setVersion(2);const counters=JSON.stringify(stats);const hostile=new Proxy({}, {get(){throw Error('hostile value read');}});
 for(const name of ['encodeState','encodeInput','encodeOutput','decodeState','decodeInput'] as const)rejects(()=>m[name](hostile as never),'version');
 for(const name of ['encodeStateInto','encodeInputInto','encodeOutputInto'] as const)withEncodeBuffer(new EncodeStorage(),100,o=>{o.extend(Uint8Array.of(99));rejects(()=>m[name]!(hostile as never,o),'version');require(o.length===1,'sink prefix changed');});
 require(JSON.stringify(stats)===counters&&providerCalls===before,'version gate touched callbacks');
 const defaults=application();eq(defaults.model.decodeState(state),{count:1});eq(defaults.model.decodeInput(input),{kind:'Add',amount:1});
 require(defaults.stats.limits===0&&defaults.stats.state_decode===1&&defaults.stats.input_decode===1,'omitted-provider control');
 require(!('decodeOutput' in m),'output decoder fabricated');
 return {version_hooks:8,fresh_limits:true,default_limits:true,malformed_trailing:true,encoders_ignore_limits:true};
}
function rejectExact(f:()=>unknown,message:string){try{f();}catch(e){require(e instanceof ModelError&&e.message===message,'wrong capture rejection: '+String(e));return;}throw Error('expected ModelError: '+message);}
function captureFailureControls(){
 const receipts:Record<string,Record<string,ReturnType<typeof counts>>>={};
 const diagnostic='runtime encode output: '+outputFailure;
 const negativeOptions={...options,maxSteps:1};
 const firstCausal:CausalContext={machine:'worker-a',generation:9n,correlation:'request-1',logicalTime:11n,causes:[]};
 const secondCausal:CausalContext={...firstCausal,logicalTime:12n,causes:[{sequence:1n,output:0}]};
 for(const kind of ['recorder','evidence']){
  const app=application(()=>({})),{model,stats}=app,initial=checkInitial(model,{count:0});
  const evidence=kind==='evidence'?new EvidenceRecorder(initial,0n,config,negativeOptions,provenance,evidenceLimits):null;
  const recorder=evidence?null:Recorder.withCheckedInitial(initial,config,negativeOptions);
  const observe=(token:CheckedTurn<State,Input,number>,causal:CausalContext)=>{if(evidence)evidence.observe(token,causal);else recorder!.observeChecked(token);};
  const snapshot=()=>evidence?evidence.snapshot().trace:recorder!.snapshot();
  const want={initial:0,reducer:0,state:1,transition:0,encode_state:1,encode_input:0,encode_output:0,decode_state:0,decode_input:0,limits:0};
  eq(counts(stats),want);
  const stages:Record<string,ReturnType<typeof counts>>={after_initial:counts(stats)};
  const input:Input={kind:'Add',amount:1};
  const first=checkTurnExact(model,{count:0},input,model.step({count:0},input),1n);
  Object.assign(want,{reducer:1,state:2,transition:1});eq(counts(stats),want);
  observe(first,firstCausal);
  Object.assign(want,{encode_state:3,encode_input:1,encode_output:1});eq(counts(stats),want);
  stages.after_first_capture=counts(stats);
  const prefix=snapshot(),journal=evidence?.snapshot();
  require(prefix.termination==='interrupted'&&prefix.error==='','successful prefix termination');
  eq(prefix.metadata,metadata('n13-installed-model','application-v1'));require(hex(prefix.initialState)==='00000000','checkpoint');
  eq(prefix.initialChecks,[check('threshold.bound')]);require(prefix.steps.length===1,'retained prefix length');
  const step=prefix.steps[0];require(hex(step.input)==='0101'&&hex(step.postState)==='01000000','retained input/state');
  eq(step.outputs.map(hex),['01000000']);eq(step.disposition,{kind:'accepted',reason:''});eq(step.checks,[check('threshold.bound'),check('edge.checked')]);
  require(prefix.config.strategy===config.strategy&&prefix.config.seed===config.seed,'producer config');eq(prefix.config.parameters[0],['region','test']);
  const retained=new Map(prefix.config.parameters);
  require(retained.get('stateless.monitor.max_steps')==='1'&&retained.get('stateless.monitor.observed_steps')==='1'&&retained.get('stateless.monitor.evicted_steps')==='0'&&retained.get('stateless.monitor.checkpoint_after_sequence')==='0','retained counters');
  const second=checkTurnExact(model,{count:1},input,model.step({count:1},input),2n);
  Object.assign(want,{reducer:2,state:3,transition:2});eq(counts(stats),want);
  stages.before_failed_capture=counts(stats);app.setOutputFailure(true);
  rejectExact(()=>observe(second,secondCausal),diagnostic);
  Object.assign(want,{encode_state:4,encode_input:2,encode_output:2});eq(counts(stats),want);
  stages.after_failed_capture=counts(stats);
  const frozen=snapshot();require(evidence?evidence.isFrozen():recorder!.isFrozen(),'capture must freeze');
  eq(frozen,{...prefix,termination:'model_error',error:diagnostic});
  if(evidence){
   const value=evidence.snapshot();eq(value,{...journal,trace:frozen});eq(value.provenance,provenance);require(value.checkpointAfter===0n,'retained checkpoint');
   eq(value.steps,[{sequence:1n,stateCheckCount:1,causal:firstCausal}]);
   const artifact=readArtifact(writeTraceV2(value,evidenceLimits),evidenceLimits);require(artifact.kind==='v2','negative evidence kind');eq(artifact.evidence,value);
   require(checkPhase(artifact,1,0)==='state'&&checkPhase(artifact,1,1)==='transition'&&checkPhase(artifact,2,0)===null,'retained phase journal');
  }else{
   require(recorder!.observedSteps()===1n&&recorder!.evictedSteps()===0n&&recorder!.retainedSteps()===1,'retained recorder counters');
   const artifact=readArtifact(writeAuditTrace(frozen,limits),evidenceLimits);require(artifact.kind==='v1','negative audit kind');eq(artifact.trace,frozen);
  }
  // Reuse the sealed token; even disabling the fault cannot unfreeze capture.
  app.setOutputFailure(false);
  rejectExact(()=>observe(second,secondCausal),'runtime '+(evidence?'evidence recorder':'recorder')+' is frozen; export its retained trace');
  eq(snapshot(),frozen);eq(counts(stats),want);
  if(evidence)eq(evidence.intoTrace(),{...journal,trace:frozen});else eq(recorder!.intoTrace(),frozen);
  eq(counts(stats),want);receipts[kind]=stages;
 }
 return receipts;
}
function produce(){
 const {model,stats}=application(()=>({})),initial=checkInitial(model,{count:0});
 const recorder=Recorder.withCheckedInitial(initial,config,options),evidence=new EvidenceRecorder(initial,0n,config,options,provenance,evidenceLimits);
 for(let sequence=1;sequence<=3;sequence++){
  const before={count:sequence-1},input:Input={kind:'Add',amount:1},turn=model.step(before,input),token=checkTurnExact(model,before,input,turn,BigInt(sequence));
  const checked=semantic(stats);recorder.observeChecked(token);evidence.observe(token,{machine:'worker-a',generation:9n,correlation:'request-1',logicalTime:BigInt(10+sequence),causes:sequence===1?[]:[{sequence:BigInt(sequence-1),output:0}]});eq(semantic(stats),checked);
 }
 eq(semantic(stats),{initial:0,reducer:3,state:4,transition:3});
 eq(counts(stats),{initial:0,reducer:3,state:4,transition:3,encode_state:14,encode_input:6,encode_output:6,decode_state:0,decode_input:0,limits:0});
 const v2=evidence.snapshot(),v1=recorder.snapshot();
 // Each recorder stamps its own retained budget; both carry the application config.
 return {envelope:{v1:hex(writeAuditTrace(v1)),v2:hex(writeTraceV2(v2,evidenceLimits))},receipt:{capture_counts:counts(stats),checked_handoff:true}};
}
function inspect(v1:AuditTrace,v2:TraceV2){
 for(const [index,t] of [v1,v2.trace].entries()){
  eq(t.metadata,metadata('n13-installed-model','application-v1'));require(t.config.strategy==='manual'&&t.config.seed===7n&&t.config.parameters.some(([k,v])=>k==='region'&&v==='test'),'producer config lost');
  const parameters=new Map(t.config.parameters);require(parameters.get('stateless.monitor.max_retained_bytes')===(index===0?'16384':'15328')&&parameters.get('stateless.monitor.max_steps')==='3'&&parameters.get('stateless.monitor.observed_steps')==='3'&&parameters.get('stateless.monitor.evicted_steps')==='0','retained recorder config');
  require(t.steps.length===3&&hex(t.initialState)==='00000000','state/step count');
  for(let i=0;i<3;i++){const s=t.steps[i];require(hex(s.input)==='0101'&&hex(s.outputs[0])===hex(Uint8Array.of(i+1,0,0,0))&&hex(s.postState)===hex(s.outputs[0]),'model bytes');eq(s.checks.map(c=>[c.id,c.status,c.details]),[['threshold.bound',i===2?'failed':'passed',i===2?'at least three':''],['edge.checked','passed','']]);}
 }
 eq(v2.provenance,provenance);require(v2.checkpointAfter===0n,'checkpoint');
 v2.steps.forEach((s,i)=>{require(s.sequence===BigInt(i+1)&&s.stateCheckCount===1,'phase');eq(s.causal,{machine:'worker-a',generation:9n,correlation:'request-1',logicalTime:BigInt(11+i),causes:i===0?[]:[{sequence:BigInt(i),output:0}]});});
}
const [mode,source,destination]=process.argv.slice(2);require(mode==='produce'||mode==='transcode','mode');
const captureFailureCounts=captureFailureControls();
const produced=mode==='produce'?produce():null;
const envelope=produced?.envelope??JSON.parse(readFileSync(source,'utf8'));
const a=readArtifact(unhex(envelope.v1)),b=readArtifact(unhex(envelope.v2),evidenceLimits);require(a.kind==='v1'&&b.kind==='v2','artifact versions');inspect(a.trace,b.evidence);
const replayReceipts=[];
for(const version of [1,2]){const {model,stats}=application(()=>({}));let observations=0;const report=version===1?replayWithObservationsBounded(model,a.trace,{}, {},()=>{observations++;}):replayTraceV2(b.evidence,model,{},evidenceLimits);
 require(report.outcome==='exact'&&report.steps_verified===3&&report.failure_reproduced&&report.build_matches,'replay report');eq(counts(stats),{initial:0,reducer:3,state:4,transition:3,encode_state:4,encode_input:3,encode_output:3,decode_state:1,decode_input:3,limits:4});if(version===1)require(observations===4,'rich observations');replayReceipts.push({version,counts:counts(stats),observations});}
const rewritten={v1:hex(writeAuditTrace(a.trace)),v2:hex(writeTraceV2(b.evidence,evidenceLimits))};eq(rewritten,envelope);
writeFileSync(mode==='produce'?source:destination,JSON.stringify(rewritten)+'\n');
console.log(JSON.stringify({producer:'typescript',mode,...produced?.receipt,replays:replayReceipts,replay_counts:Object.fromEntries(replayReceipts.map(r=>['v'+r.version,r.counts])),capture_failure_controls:true,capture_failure_counts:captureFailureCounts,gates:gates(),exact_artifact_rewrite:true}));
