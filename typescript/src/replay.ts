import {ModelError,ConfigError} from './errors.js';
/** Native replay evidence. Exact describes the recorded prefix, not a terminal model error. */
import {check,disposition,validateMetadata,validateRecording,bytesEqual,checkObservedCountInto,type CodecModel,type Check,type Metadata,type Transition,type Trace,type TraceStep,type ReplayReport} from './core.js';
import {withCheckSink,appendStateChecks} from './checks.js';
import {EncodeStorage,encodeModel} from './encoding.js';
import {AuditBudget,effectiveBlobLimit,resolveBinaryLimits,snapshotAuditTrace} from './audit_budget.js';
import {type AuditTrace,type BinaryLimits} from './binary.js';
export interface ReplayOptions {readonly allowBuildMismatch?:boolean}
export interface ReplayDifferences {readonly disposition:boolean;readonly outputs:boolean;readonly state:boolean;readonly checks:boolean}
export type ReplayActual<S,I,O> = {kind:'initial';sequence:0;state:S;checks:readonly Check[]|null} | {kind:'turn';sequence:number;before:S;input:I;transition:Transition<S,O>;checks:readonly Check[]|null;state_check_count:number|null};
export type ReplayObservation<S,I,O> =
 {kind:'initial';sequence:0;state:S;checks:readonly Check[];expected_state:Uint8Array;actual_state:Uint8Array;expected_checks:readonly Check[];differences:ReplayDifferences} |
 {kind:'turn';actual:Extract<ReplayActual<S,I,O>,{kind:'turn'}>;expected:TraceStep;actual_input:Uint8Array;actual_outputs:readonly Uint8Array[];actual_state:Uint8Array;differences:ReplayDifferences} |
 {kind:'error';sequence:number;error:ModelError;actual:ReplayActual<S,I,O>|null;actual_available:boolean;snapshot_unavailable:boolean};
export type ReplayObserver<S,I,O>=(observation:ReplayObservation<S,I,O>,report:Readonly<ReplayReport>)=>void;
export class ReplayError<S=unknown,I=unknown,O=unknown> extends ModelError {
 readonly recovery_ownership='transferred' as const;
 constructor(readonly error:ModelError,readonly report:ReplayReport,readonly actual:ReplayActual<S,I,O>|null,readonly observer_error:ModelError|null=null,readonly snapshot_error:ModelError|null=null){super(error.message);this.name='ReplayError';}
}
const wrapped=(stage:string,e:unknown)=>new ModelError(`${stage}: ${e instanceof Error?e.message:String(e)}`);
function invoke<T>(stage:string,f:()=>T):T {try{return f();}catch(e){throw wrapped(stage,e);}}
const checksCopy=(v:readonly Check[])=>v.map(c=>check(c.id,c.status,c.details));
const equalChecks=(a:readonly Check[],b:readonly Check[])=>a.length===b.length&&a.every((c,i)=>c.id===b[i].id&&c.status===b[i].status&&c.details===b[i].details);
const reproduced=(a:readonly Check[],b:readonly Check[])=>a.some(x=>x.status==='failed'&&b.some(y=>y.status==='failed'&&x.id===y.id));
const typedPrototype=Object.getPrototypeOf(Uint8Array.prototype),typedBuffer=Object.getOwnPropertyDescriptor(typedPrototype,'buffer')!.get!,typedOffset=Object.getOwnPropertyDescriptor(typedPrototype,'byteOffset')!.get!,typedLength=Object.getOwnPropertyDescriptor(typedPrototype,'length')!.get!;
function blob(v:Uint8Array):Uint8Array {if(!(v instanceof Uint8Array))throw new TypeError('expected Uint8Array');const view=new Uint8Array(typedBuffer.call(v),typedOffset.call(v),typedLength.call(v));return new Uint8Array(view);}
function stepCopy(s:TraceStep):TraceStep {return {input:blob(s.input),postState:blob(s.postState),outputs:s.outputs.map(blob),checks:checksCopy(s.checks),disposition:disposition(s.disposition.kind,s.disposition.reason)};}
function traceCopy(t:Trace):Trace {return {metadata:validateMetadata(t.metadata),initialState:blob(t.initialState),initialChecks:checksCopy(t.initialChecks),steps:t.steps.map(stepCopy),termination:t.termination,error:t.error};}
function notify<S,I,O>(observer:ReplayObserver<S,I,O>|undefined,value:ReplayObservation<S,I,O>,report:ReplayReport):void {
 if(!observer)return;
 const result:unknown=observer(value,{...report});
 if(result!==undefined){if(result instanceof Promise)void result.catch(()=>{});throw new ModelError('observer must return undefined synchronously');}
}
/** All actual values in an error belong to the caller after the error is thrown. */
function kernel<S,I,O>(original:CodecModel<S,I,O>,source:Trace,options:ReplayOptions,observer:ReplayObserver<S,I,O>|undefined,overrides?:Partial<BinaryLimits>,validate:(trace:Trace)=>void=validateRecording):ReplayReport {
 const allowOption=options.allowBuildMismatch,allow=allowOption===undefined?false:allowOption;if(typeof allow!=='boolean')throw new ConfigError('allowBuildMismatch must be boolean');
 if(observer!==undefined&&typeof observer!=='function')throw new ConfigError('observer must be a function');
 const capturedLimits=overrides===undefined?undefined:overrides===null?null:{...overrides};
 const report:ReplayReport={outcome:'exact',steps_verified:0,failure_reproduced:false,build_matches:false,step:null,field:null};
 let actual:ReplayActual<S,I,O>|null=null,sequence=0,snapshotError:ModelError|null=null,normalObserverError=false;
 // A facade keeps private-field receivers and frozen application models intact.
 const model=new Proxy(Object.create(null) as CodecModel<S,I,O>,{get(_t,key){const value=Reflect.get(original,key,original);if(typeof value!=='function')return value;if(key==='cloneState'||key==='cloneInput'||key==='cloneOutput')return (...args:unknown[])=>{try{return value.apply(original,args);}catch(e){snapshotError=wrapped('snapshot',e);throw snapshotError;}};return value.bind(original);}});
 const snapshot=<T>(callback:()=>T):T=>{try{return callback();}catch(e){snapshotError=wrapped('snapshot',e);throw snapshotError;}};
 const cloneTransition=(t:Transition<S,O>):Transition<S,O>=>({state:model.cloneState(t.state),outputs:Array.from(t.outputs,o=>model.cloneOutput(o)),disposition:disposition(t.disposition.kind,t.disposition.reason)});
 const cloneActual=(a:ReplayActual<S,I,O>):ReplayActual<S,I,O>=>a.kind==='initial'?{kind:'initial',sequence:0,state:model.cloneState(a.state),checks:a.checks===null?null:checksCopy(a.checks)}:{kind:'turn',sequence:a.sequence,before:model.cloneState(a.before),input:model.cloneInput(a.input),transition:cloneTransition(a.transition),checks:a.checks===null?null:checksCopy(a.checks),state_check_count:a.state_check_count};
 const emit=(make:()=>ReplayObservation<S,I,O>)=>{if(!observer)return;const value=snapshot(make);try{notify(observer,value,report);}catch(e){normalObserverError=true;throw wrapped('replay observer',e);}};
 const storage=new EncodeStorage(),checkStorage:Check[]=[];
 try {
  const identity=invoke('metadata',()=>validateMetadata(model.metadata()));
  const expectedIdentity=invoke('trace metadata',()=>validateMetadata(source.metadata));
  report.build_matches=identity.build===expectedIdentity.build;
  if((['name','model_version','properties_version','codec_version'] as const).some(k=>identity[k]!==expectedIdentity[k])||(!report.build_matches&&!allow)){report.outcome='incompatible';return report;}
  const limits=capturedLimits===undefined?undefined:resolveBinaryLimits(capturedLimits as Partial<BinaryLimits>);
  const trace=invoke(limits?'bounded replay artifact':'recording',()=>limits?snapshotAuditTrace(source as AuditTrace,limits):traceCopy(source));
  invoke('recording',()=>validate(trace));
  let state=invoke('decode initial state',()=>model.decodeState(blob(trace.initialState)));
  actual={kind:'initial',sequence:0,state,checks:null};
  state=snapshot(()=>model.cloneState(state));actual={kind:'initial',sequence:0,state,checks:null};
  const maximum=limits?effectiveBlobLimit(limits):Number.MAX_SAFE_INTEGER;
  const initialBytes=invoke('encode initial state',()=>encodeModel(model,'state',state,storage,maximum));
  if(!bytesEqual(initialBytes,trace.initialState))throw new ModelError('initial state encoding is not canonical');
  invoke('initial check',()=>withCheckSink(checkStorage,sink=>appendStateChecks(model,state,sink)));
  const initial=checksCopy(checkStorage);actual={kind:'initial',sequence:0,state,checks:initial};
  const budget=limits?invoke('bounded replay initial',()=>new AuditBudget(identity,(trace as AuditTrace).config,initialBytes,initial,limits)):undefined;
  report.failure_reproduced=reproduced(trace.initialChecks,initial);
  const initialDiff:ReplayDifferences={disposition:false,outputs:false,state:false,checks:!equalChecks(initial,trace.initialChecks)};
  if(initialDiff.checks){report.outcome='diverged';report.field='initial checks';}
  emit(()=>({kind:'initial',sequence:0,state:model.cloneState(state),checks:checksCopy(initial),expected_state:blob(trace.initialState),actual_state:blob(initialBytes),expected_checks:checksCopy(trace.initialChecks),differences:{...initialDiff}}));
  if(initialDiff.checks)return report;
  for(let index=0;index<trace.steps.length;index++){
   sequence=index+1;actual=null;snapshotError=null;
   const expected=trace.steps[index];
   const input=invoke('decode input',()=>model.decodeInput(blob(expected.input)));
   const inputBytes=invoke('encode input',()=>encodeModel(model,'input',input,storage,maximum));
   if(!bytesEqual(inputBytes,expected.input))throw new ModelError(`input ${sequence} encoding is not canonical`);
   const before=snapshot(()=>model.cloneState(state)),stepInput=snapshot(()=>model.cloneInput(input));
   const raw=invoke('transition',()=>model.step(before,stepInput));
   actual={kind:'turn',sequence,before:state,input,transition:raw,checks:null,state_check_count:null};
   const transition=snapshot(()=>cloneTransition(raw));
   actual={kind:'turn',sequence,before:state,input,transition,checks:null,state_check_count:null};
   const boundary=checkObservedCountInto(model,state,input,transition,sequence,checkStorage);
   const checks=checksCopy(checkStorage);
   actual={kind:'turn',sequence,before:state,input,transition,checks,state_check_count:boundary};
   const plan=budget?.plan(inputBytes,transition.disposition,transition.outputs.length,checks);
   report.failure_reproduced ||=reproduced(expected.checks,checks);
   const outputs:Uint8Array[]=[];
   for(const output of transition.outputs){const bytes=invoke('encode output',()=>encodeModel(model,'output',output,storage,plan?.maximum??maximum));plan?.accept(bytes);outputs.push(bytes);}
   const postState=invoke('encode state',()=>encodeModel(model,'state',transition.state,storage,plan?.maximum??maximum));plan?.accept(postState);plan?.commit();
   const differences:ReplayDifferences={disposition:transition.disposition.kind!==expected.disposition.kind||transition.disposition.reason!==expected.disposition.reason,outputs:outputs.length!==expected.outputs.length||outputs.some((o,i)=>!bytesEqual(o,expected.outputs[i])),state:!bytesEqual(postState,expected.postState),checks:!equalChecks(checks,expected.checks)};
   const field=(['disposition','outputs','state','checks'] as const).find(k=>differences[k])??null;
   if(field){report.outcome='diverged';report.step=sequence;report.field=field;}else report.steps_verified++;
   const completed=actual;
   emit(()=>({kind:'turn',actual:cloneActual(completed) as Extract<ReplayActual<S,I,O>,{kind:'turn'}>,expected:stepCopy(expected),actual_input:blob(inputBytes),actual_outputs:outputs.map(blob),actual_state:blob(postState),differences:{...differences}}));
   if(field)return report;
   state=transition.state;
  }
  return report;
 }catch(cause){
  const error=cause instanceof ModelError?cause:wrapped('replay',cause);
  let observerError:ModelError|null=null;
  if(observer&&!normalObserverError){
   let snapshot:ReplayActual<S,I,O>|null=null;
   if(actual&&!snapshotError){try{snapshot=cloneActual(actual);}catch(e){snapshotError=e instanceof ModelError?e:wrapped('snapshot',e);}}
   try{notify(observer,{kind:'error',sequence,error:new ModelError(error.message),actual:snapshot,actual_available:actual!==null,snapshot_unavailable:actual!==null&&snapshot===null},report);}catch(e){observerError=wrapped('replay observer',e);}
  }
  throw new ReplayError(error,{...report},actual,observerError,snapshotError);
 }
}
export function replayWithObservations<S,I,O>(model:CodecModel<S,I,O>,trace:Trace,options:ReplayOptions={},observer?:ReplayObserver<S,I,O>):ReplayReport{return kernel(model,trace,options,observer);}
export function replayWithObservationsBounded<S,I,O>(model:CodecModel<S,I,O>,trace:AuditTrace,options:ReplayOptions={},limits:Partial<BinaryLimits>={},observer?:ReplayObserver<S,I,O>):ReplayReport{return kernel(model,trace,options,observer,limits);}
export function replayBasic<S,I,O>(model:CodecModel<S,I,O>,trace:Trace,allowBuildMismatch=false):ReplayReport {try{return replayWithObservations(model,trace,{allowBuildMismatch});}catch(e){if(e instanceof ReplayError)throw e.error;throw e;}}
/** continued-failure-evidence-v1: the standard kernel with continued-trace validation. Not a package entrypoint. */
export function replayContinuedKernel<S,I,O>(model:CodecModel<S,I,O>,trace:Trace,allowBuildMismatch:boolean,validate:(trace:Trace)=>void,observer:ReplayObserver<S,I,O>):ReplayReport {try{return kernel(model,trace,{allowBuildMismatch},observer,undefined,validate);}catch(e){if(e instanceof ReplayError)throw e.error;throw e;}}
