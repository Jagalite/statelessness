/** Optional typed simulation: host callbacks remain synchronous and cooperative. */
import {type Model,type CodecModel,type Check,type Transition,ModelError,ConfigError,natural,disposition} from './core.js';
import {checkInitial,checkTurnExact,type CheckedInitial,type CheckedTurn,type ExactCheckPolicy,FULL_CHECKS_EXACT} from './checked.js';
import {EncodeStorage,encodeModel} from './encoding.js';
import {BRANCH_FACTORY} from './debug_internal.js';
import {Recorder,type RecorderOptions} from './runtime_recorder.js';
import type {RunConfig,AuditTrace} from './binary.js';
import type {InputPolicy} from './debug.js';
const U64=(1n<<64n)-1n;let epoch=0n;
export type SessionPhase='ready'|'paused'|'ended'|'disconnected';
export type SessionStop={kind:'pre_breakpoint'|'post_breakpoint';id:bigint}|{kind:'property_failure'|'budget_exhausted'|'cancelled'}|{kind:'initial_check_error'|'model_error'|'checker_error'|'observer_error'|'recording_error';message:string};
export interface SessionLimits {readonly maxTurns:bigint;readonly maxContinue:number;readonly maxBreakpoints:number;readonly maxCandidates:number}
export interface DebugTurn<S,I,O>{readonly sequence:bigint;readonly before:S;readonly input:I;readonly transition:Transition<S,O>;readonly checks:readonly Check[];readonly stateCheckCount:number;readonly completeChecks:boolean}
export interface StepResult {readonly sequence:bigint;readonly delivered:boolean;readonly stop:SessionStop|null}
export interface SessionRecorder<S,I,O>{recordCheckedTurn(turn:CheckedTurn<S,I,O>):void}
function synchronous(value:unknown):void{if(value!==undefined){if(value instanceof Promise)void value.catch(()=>{});throw new ModelError('callback must return undefined synchronously');}}
const message=(e:unknown)=>e instanceof Error?e.message:String(e);
const candidates=new WeakMap<object,{owner:object;epoch:bigint;revision:bigint;input:unknown}>();
export interface CandidateToken<I>{readonly revision:bigint;readonly input:I}
export class DebugSession<S,I,O>{
 readonly epoch:bigint;readonly #owner={};readonly #model:Model<S,I,O>;readonly #policy:InputPolicy<S,I>;readonly #limits:SessionLimits;readonly id:string;
 #state:S;#initialChecks:readonly Check[]=[];#sequence=0n;#revision=0n;#phase:SessionPhase='ready';#stop:SessionStop|null=null;#last:DebugTurn<S,I,O>|null=null;#busy=false;#nextBreakpoint=1n;
 #pending:{revision:bigint;input:I}|null=null;#pre=new Map<bigint,(state:S,input:I)=>boolean>();#post=new Map<bigint,(turn:DebugTurn<S,I,O>)=>boolean>();#issues:string[]=[];
 readonly #domain?:{inputs:(state:S)=>Iterable<I>;equal:(left:I,right:I)=>boolean};readonly #equalInputs:(left:I,right:I)=>boolean;readonly #checks:ExactCheckPolicy;#recorder?:SessionRecorder<S,I,O>;#nativeRecorder?:Recorder<S,I,O>;
 constructor(id:string,model:Model<S,I,O>,policy:InputPolicy<S,I>,limits:SessionLimits,options:{state?:S;checkPolicy?:ExactCheckPolicy;recorder?:SessionRecorder<S,I,O>;equalInputs?:(left:I,right:I)=>boolean;makeRecorder?:(initial:CheckedInitial<S,I,O>)=>SessionRecorder<S,I,O>;inputDomain?:{inputs:(state:S)=>Iterable<I>;equal:(left:I,right:I)=>boolean}}={}){
  const initial=Object.hasOwn(options,'state')?options.state as S:model.initialState();
  if(!id)throw new ConfigError('nonempty session identity required');if(typeof limits.maxTurns!=='bigint'||limits.maxTurns<1n||limits.maxTurns>U64)throw new ConfigError('maxTurns must be positive u64 bigint');
  natural(limits.maxContinue,'maxContinue',1);natural(limits.maxBreakpoints,'maxBreakpoints');natural(limits.maxCandidates,'maxCandidates',1);
  if(epoch===U64-1n)throw new ModelError('session epoch exhausted');this.epoch=++epoch;this.id=id;this.#model=model;this.#policy={...policy};this.#limits={...limits};this.#checks={...(options.checkPolicy??FULL_CHECKS_EXACT)};this.#recorder=options.recorder;const modelInputEquality=(model as Model<S,I,O>&{equalInputs?:(left:I,right:I)=>boolean}).equalInputs;this.#equalInputs=options.equalInputs??(modelInputEquality?modelInputEquality.bind(model):policy.kind==='enumerated'?policy.equal:Object.is);const modelInputs=(model as Model<S,I,O>&{inputs?:(state:S)=>Iterable<I>}).inputs;this.#domain=options.inputDomain??(modelInputs?{inputs:modelInputs.bind(model),equal:this.#equalInputs}:policy.kind==='enumerated'?{inputs:policy.candidates,equal:policy.equal}:undefined);
  this.#state=model.cloneState(initial);
  this.#busy=true;try{const initial=checkInitial(model,this.#state);this.#initialChecks=initial.checks;if(options.makeRecorder)try{this.#recorder=options.makeRecorder(initial);}catch(e){this.#issues.push(`exact recording: ${message(e)}`);this.#phase='ended';this.#stop={kind:'recording_error',message:message(e)};}if(this.#initialChecks.some(c=>c.status==='failed')){this.#phase='ended';this.#stop={kind:'property_failure'};}}catch(e){this.#phase='ended';this.#stop={kind:'initial_check_error',message:message(e)};}finally{this.#busy=false;}
 }
 static recording<S,I,O>(id:string,model:CodecModel<S,I,O>,policy:InputPolicy<S,I>,limits:SessionLimits,config:RunConfig,options:RecorderOptions={},state?:{value:S}):DebugSession<S,I,O>{
  if(config.parameters.some(([key])=>key.startsWith('stateless.debug.')))throw new ModelError('stateless.debug. parameters are reserved');return DebugSession.#recording(id,model,policy,limits,config,options,state);
 }
 static [BRANCH_FACTORY]<S,I,O>(id:string,model:CodecModel<S,I,O>,policy:InputPolicy<S,I>,limits:SessionLimits,config:RunConfig,options:RecorderOptions={},state?:{value:S}):DebugSession<S,I,O>{return DebugSession.#recording(id,model,policy,limits,config,options,state);}
 static #recording<S,I,O>(id:string,model:CodecModel<S,I,O>,policy:InputPolicy<S,I>,limits:SessionLimits,config:RunConfig,options:RecorderOptions={},state?:{value:S}):DebugSession<S,I,O>{
  let native:Recorder<S,I,O>|undefined;
  const parameters=config.parameters.map(([k,v])=>[k,v] as [string,string]);parameters.push(['stateless.debug.environment',policy.label],['stateless.debug.origin',policy.kind==='unrestricted'?'out-of-domain-injection':'simulation']);
  const session=new DebugSession(id,model,policy,limits,{...(state?{state:state.value}:{}),makeRecorder:initial=>{native=Recorder.withCheckedInitial(initial,{...config,parameters},options);return {recordCheckedTurn:turn=>{native!.observeChecked(turn);}};}});session.#nativeRecorder=native;return session;
 }
 exportTrace():AuditTrace{if(!this.#nativeRecorder)throw new ModelError('exact recording unavailable');return this.#read(()=>this.#nativeRecorder!.snapshot());}
 #read<T>(callback:()=>T):T{const old=this.#busy;this.#busy=true;try{return callback();}finally{this.#busy=old;}}
 get state():S{return this.#read(()=>this.#model.cloneState(this.#state));}get sequence():bigint{return this.#sequence;}get revision():bigint{return this.#revision;}get phase():SessionPhase{return this.#phase;}get stopReason():SessionStop|null{return this.#stop?{...this.#stop}:null;}get initialChecks():readonly Check[]{return this.#initialChecks.map(c=>({...c}));}get diagnosticErrors():readonly string[]{return [...this.#issues];}
 get inputPolicy():string{return this.#policy.label??(this.#policy.kind==='unrestricted'?'out-of-domain-injection':this.#policy.kind==='enumerated'?'enumerated-domain':'declared');}
 get checksComplete():boolean{return this.#last?.completeChecks??this.#stop?.kind!=='initial_check_error';}
 get capabilities(){return {mode:'simulation' as const,typedInput:true,exactRecording:this.#nativeRecorder!==undefined,enumerateInputs:this.#domain!==undefined,effectsDispatched:false,liveRestore:false};}
 /** External protocol decoders borrow session ownership before input delivery. */
 decodeInputWith(bytes:Uint8Array,decode:(bytes:Uint8Array)=>I):I{return this.#read(()=>decode(bytes.slice()));}
 /** Exact output encoding is optional and never requires an output decoder. */
 encodeObservedOutput(index:number,maximum:number):Uint8Array{return this.#read(()=>{natural(index,'output index');natural(maximum,'output bytes');const output=this.#last?.transition.outputs[index];if(!this.#last||index>=this.#last.transition.outputs.length)throw new ModelError('unknown output');const model=this.#model as CodecModel<S,I,O>;if(typeof model.encodeOutputInto!=='function'&&typeof model.encodeOutput!=='function')throw new ModelError('exact output encoding unavailable');return encodeModel(model,'output',this.#model.cloneOutput(output as O),new EncodeStorage(),maximum);});}
 observation():DebugTurn<S,I,O>|null{return this.#read(()=>{const t=this.#last;if(!t)return null;const m=this.#model;return {...t,before:m.cloneState(t.before),input:m.cloneInput(t.input),transition:{state:m.cloneState(t.transition.state),outputs:t.transition.outputs.map(o=>m.cloneOutput(o)),disposition:{...t.transition.disposition}},checks:t.checks.map(c=>({...c}))};});}
 #guard(expected:bigint){if(this.#busy)throw new ModelError('session already owned by a callback');if(typeof expected!=='bigint'||expected!==this.#revision)throw new ModelError('stale session revision');}
 #breakpoint():bigint{if(this.#pre.size+this.#post.size>=this.#limits.maxBreakpoints||this.#nextBreakpoint===U64)throw new ModelError('breakpoint budget exhausted');return this.#nextBreakpoint++;}
 addPreBreakpoint(predicate:(state:S,input:I)=>boolean):bigint{if(this.#busy)throw new ModelError('session busy');const id=this.#breakpoint();this.#pre.set(id,predicate);return id;}
 addPostBreakpoint(predicate:(turn:DebugTurn<S,I,O>)=>boolean):bigint{if(this.#busy)throw new ModelError('session busy');const id=this.#breakpoint();this.#post.set(id,predicate);return id;}
 removeBreakpoint(id:bigint):boolean{if(this.#busy)throw new ModelError('session busy');return this.#pre.delete(id)||this.#post.delete(id);}
 #permits(input:I):boolean{const p=this.#policy;if(p.kind==='unrestricted')return true;const m=this.#model;if(p.kind==='declared'){const value=p.permits(m.cloneState(this.#state),m.cloneInput(input));if(typeof value!=='boolean')throw new ModelError('policy admission must return boolean');return value;}const it=p.candidates(m.cloneState(this.#state))[Symbol.iterator]();let found=false;
  try{for(let n=0;;n++){const next=it.next();if(next.done)return found;if(n>=this.#limits.maxCandidates)throw new ModelError('environmental admission candidate limit');const equal=p.equal(m.cloneInput(next.value),m.cloneInput(input));if(typeof equal!=='boolean')throw new ModelError('policy equality must return boolean');if(equal)return true;}}finally{it.return?.();}}
 step(expected:bigint,input:I,observer?:(turn:DebugTurn<S,I,O>)=>void):StepResult{
  this.#guard(expected);if(this.#phase==='ended'||this.#phase==='disconnected')throw new ModelError('session ended');const result=(delivered:boolean):StepResult=>({sequence:this.#sequence,delivered,stop:this.stopReason});
  if(this.#sequence>=this.#limits.maxTurns||this.#revision===U64){this.#phase='ended';this.#stop={kind:'budget_exhausted'};return result(false);}
  const m=this.#model;this.#busy=true;
  try{input=m.cloneInput(input);const oldPhase=this.#phase;this.#phase='ended';if(!this.#permits(input)){this.#phase=oldPhase;throw new ModelError('input not permitted');}
   const bypass=this.#pending!==null&&this.#pending.revision===this.#revision&&(this.#policy.kind==='enumerated'?this.#policy.equal(this.#pending.input,input):this.#equalInputs(this.#pending.input,input));this.#pending=null;
   if(!bypass)for(const [id,predicate]of this.#pre){const yes=predicate(m.cloneState(this.#state),m.cloneInput(input));if(typeof yes!=='boolean')throw new ModelError('breakpoint must return boolean');if(yes){this.#pending={revision:this.#revision,input:m.cloneInput(input)};this.#phase='paused';this.#stop={kind:'pre_breakpoint',id};return result(false);}}
   this.#phase='ended';this.#stop=null;let t:Transition<S,O>;
   try{const raw=m.step(m.cloneState(this.#state),m.cloneInput(input));t={state:m.cloneState(raw.state),outputs:Array.from(raw.outputs,o=>m.cloneOutput(o)),disposition:disposition(raw.disposition.kind,raw.disposition.reason)};}catch(e){this.#stop={kind:'model_error',message:message(e)};this.#nativeRecorder?.stopWithError(new ModelError(message(e)));return result(false);}
   this.#sequence++;this.#revision++;let checks:readonly Check[]=[],count=0,complete=true;
   try{const checked=checkTurnExact(m,this.#state,input,t,this.#sequence,this.#checks);checks=checked.checks;count=checked.stateCheckCount;if(checks.some(c=>c.status==='failed'))this.#stop={kind:'property_failure'};
    if(this.#recorder)try{synchronous(this.#recorder.recordCheckedTurn(checked));}catch(e){this.#issues.push(`exact recording: ${message(e)}`);this.#stop??={kind:'recording_error',message:message(e)};}
   }catch(e){complete=false;this.#stop={kind:'checker_error',message:message(e)};this.#nativeRecorder?.stopWithError(new ModelError(message(e)));}
   const actual:DebugTurn<S,I,O>={sequence:this.#sequence,before:this.#state,input:m.cloneInput(input),transition:t,checks,stateCheckCount:count,completeChecks:complete};
   if(!this.#stop)for(const [id,predicate]of this.#post){const view={...actual,before:m.cloneState(actual.before),input:m.cloneInput(actual.input),transition:{state:m.cloneState(t.state),outputs:t.outputs.map(o=>m.cloneOutput(o)),disposition:{...t.disposition}},checks:checks.map(c=>({...c}))};const yes=predicate(view);if(typeof yes!=='boolean')throw new ModelError('breakpoint must return boolean');if(yes){this.#stop={kind:'post_breakpoint',id};break;}}
   this.#last=actual;this.#state=t.state;
   this.#phase=this.#stop&&this.#stop.kind!=='post_breakpoint'?'ended':'paused';
   if(observer)try{synchronous(observer(this.observation()!));}catch(e){this.#issues.push(`observer: ${message(e)}`);this.#stop??={kind:'observer_error',message:message(e)};this.#phase='ended';}
   return result(true);
  }catch(e){if(this.#phase==='ended'&&this.#stop===null){this.#issues.push(`callback: ${message(e)}`);}throw e;}finally{this.#busy=false;}
 }
 runBounded(expected:bigint,inputs:Iterable<I>,maximum:number):readonly StepResult[]{this.#guard(expected);natural(maximum,'continue count',1,this.#limits.maxContinue);const results:StepResult[]=[];this.#busy=true;let iterator:Iterator<I>;try{iterator=inputs[Symbol.iterator]();}finally{this.#busy=false;}try{for(let n=0;n<maximum;n++){this.#busy=true;let next:IteratorResult<I>;try{next=iterator.next();}finally{this.#busy=false;}if(next.done)return results;const r=this.step(this.#revision,next.value);results.push(r);if(r.stop)return results;}this.#phase='paused';this.#stop={kind:'budget_exhausted'};return results;}finally{this.#busy=true;try{iterator.return?.();}finally{this.#busy=false;}}}
 cancel(expected:bigint):void{this.#guard(expected);if(this.#phase==='ended'||this.#phase==='disconnected')throw new ModelError('session ended');this.#phase='ended';this.#stop={kind:'cancelled'};}
 /** Snapshot-bound candidates have private authority, independent of list indices. */
 candidates(expected:bigint,offset:number,maximum:number):{candidates:readonly CandidateToken<I>[];nextOffset:number|null;complete:boolean}{this.#guard(expected);natural(offset,'offset');natural(maximum,'maximum',1,this.#limits.maxCandidates);if(!this.#domain)throw new ModelError('candidate enumeration unavailable');this.#busy=true;const output:CandidateToken<I>[]=[];try{const all=this.#domain!.inputs(this.state)[Symbol.iterator]();try{let total=0;for(;;){const n=all.next();if(n.done)return {candidates:output,nextOffset:null,complete:true};if(total>=this.#limits.maxCandidates)throw new ModelError('candidate enumeration budget');if(total>=offset){if(output.length===maximum)return {candidates:output,nextOffset:total,complete:false};const input=this.#model.cloneInput(n.value),snapshot=()=>this.#read(()=>this.#model.cloneInput(input)),token=Object.freeze({revision:this.#revision,get input(){return snapshot();}});candidates.set(token,{owner:this.#owner,epoch:this.epoch,revision:this.#revision,input:this.#model.cloneInput(input)});output.push(token);}total++;}}finally{all.return?.();}}finally{this.#busy=false;}}
 stepCandidate(expected:bigint,token:CandidateToken<I>):StepResult{this.#guard(expected);const c=candidates.get(token);if(!c||c.owner!==this.#owner||c.epoch!==this.epoch||c.revision!==this.#revision)throw new ModelError('stale or foreign candidate');const input=this.#read(()=>{const input=this.#model.cloneInput(c.input as I);if(!this.#domain)throw new ModelError('candidate domain unavailable');const iterator=this.#domain.inputs(this.state)[Symbol.iterator]();try{for(let n=0;;n++){const next=iterator.next();if(next.done)throw new ModelError('candidate no longer in current domain');if(n>=this.#limits.maxCandidates)throw new ModelError('candidate admission budget');const same=this.#domain.equal(this.#model.cloneInput(next.value),this.#model.cloneInput(input));if(typeof same!=='boolean')throw new ModelError('domain equality must return boolean');if(same)return input;}}finally{iterator.return?.();}});return this.step(expected,input);}
}
