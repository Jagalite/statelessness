import {ModelError,type Transition} from './core.js';
import {ReferenceController,type ReferenceBounds,type ReferenceState,type ReferenceInput,type ReferenceEffect,type ReferenceOutcome,type ReferenceSettlement,type ReferenceMutation} from './reference_controller.js';
import {Recorder} from './runtime_recorder.js';
import type {AuditTrace} from './binary.js';

/** Rust src/reference/host.rs `Host::new(bounds, work, record)` options. */
export interface ReferenceHostOptions {
 /** Record each delivered turn (Rust `record: true`): checks run and failures become `property failure: <id>` diagnostics. */
 readonly record?:boolean;
 /** TypeScript-only fault-injection seam for demonstrations and tests; Rust's host always runs the unmutated controller. */
 readonly mutation?:ReferenceMutation|null;
}
const HOST_RUN_CONFIG=Object.freeze({strategy:'reference-live-host',seed:null,parameters:Object.freeze([]) as readonly (readonly [string,string])[]});
const errorMessage=(error:unknown,fallback:string):string=>{try{return error instanceof Error?String(error.message):String(error);}catch{return fallback;}};

/** Browser-safe cooperative host. Work is asynchronous; a host chooses whether it uses a real worker. */
export class ReferenceHost {
 readonly #model:ReferenceController;readonly #work:(generation:bigint,attempt:number,signal:AbortSignal)=>ReferenceOutcome|Promise<ReferenceOutcome>;
 readonly #observe?: (before:ReferenceState,input:ReferenceInput,transition:Transition<ReferenceState,ReferenceEffect>)=>void;
 readonly #recorder:Recorder<ReferenceState,ReferenceInput,ReferenceEffect>|null;
 #state:ReferenceState;#busy=false;#receiving=false;#waiting=false;#closed=false;#wake:(()=>void)|null=null;readonly #events:ReferenceInput[]=[];
 readonly #workers=new Map<string,{generation:bigint;cancelled:boolean;abort:AbortController;task:Promise<void>}>();
 readonly #published:bigint[]=[];readonly #settled=new Map<bigint,ReferenceSettlement>();readonly #diagnostics:string[]=[];#executedEffects=0;
 constructor(bounds:ReferenceBounds,work:(generation:bigint,attempt:number,signal:AbortSignal)=>ReferenceOutcome|Promise<ReferenceOutcome>,observe?: (before:ReferenceState,input:ReferenceInput,transition:Transition<ReferenceState,ReferenceEffect>)=>void,options:ReferenceHostOptions={}){if(typeof work!=='function')throw new ModelError('reference host requires work callback');const record=options.record===true,mutation=options.mutation??null;this.#model=new ReferenceController(bounds,mutation);this.#state=this.#model.initialState();this.#work=work;this.#observe=observe;
  // Rust Host::new: Recorder::new(model, state, RunConfig { strategy: "reference-live-host", .. }, 1024).
  this.#recorder=record?new Recorder(this.#model,this.#state,HOST_RUN_CONFIG,{maxSteps:1024}):null;}
 /** Rust Host::recording: a snapshot of the bounded live recording, or null when recording is off. */
 recording():AuditTrace|null{return this.#recorder?this.#recorder.snapshot():null;}
 get state():ReferenceState{return this.#model.cloneState(this.#state);}get pendingWorkers():number{return this.#workers.size;}get published():readonly bigint[]{return [...this.#published];}get settled():ReadonlyMap<bigint,ReferenceSettlement>{return new Map(this.#settled);}get diagnostics():readonly string[]{return [...this.#diagnostics];}get executedEffects():number{return this.#executedEffects;}
 #push(input:ReferenceInput){if(this.#closed)return;this.#events.push(input);this.#wake?.();}
 async deliver(input:ReferenceInput):Promise<readonly ReferenceEffect[]>{if(this.#closed)throw new ModelError('reference host closed');if(this.#busy||this.#receiving)throw new ModelError('reference host busy');this.#busy=true;try{
  const owned=this.#model.cloneInput(input),before=this.#model.cloneState(this.#state),transition=this.#model.step(before,owned);
  // Rust Host::deliver: the recorder runs state and transition checks once; failures are diagnostics, never a rerun or suppression of effects.
  if(this.#recorder)try{for(const c of this.#recorder.observe(this.#model,this.#model.cloneState(before),this.#model.cloneInput(owned),{state:this.#model.cloneState(transition.state),outputs:transition.outputs.map(e=>this.#model.cloneOutput(e)),disposition:{...transition.disposition}}))if(c.status==='failed')this.#diagnostics.push(`property failure: ${c.id}`);}catch(error){this.#diagnostics.push(`recording unavailable: ${errorMessage(error,'unknown recorder error')}`);}
  if(this.#observe)try{const result:unknown=this.#observe(this.#model.cloneState(before),this.#model.cloneInput(owned),{state:this.#model.cloneState(transition.state),outputs:transition.outputs.map(e=>this.#model.cloneOutput(e)),disposition:{...transition.disposition}});if(result!==undefined){if(result instanceof Promise)void result.catch(()=>{});throw new ModelError('host observer must be synchronous');}}catch(error){let message='unknown observer error';try{message=error instanceof Error?String(error.message):String(error);}catch{}this.#diagnostics.push(`recording unavailable: ${message}`);}
  this.#state=this.#model.cloneState(transition.state);for(const effect of transition.outputs)await this.#execute(effect);return transition.outputs.map(e=>this.#model.cloneOutput(e));
 }finally{this.#busy=false;}}
 async #execute(effect:ReferenceEffect):Promise<void>{this.#executedEffects++;if(effect.kind==='Start'){
  const key=`${effect.generation}:${effect.attempt}`;if(this.#workers.has(key))throw new ModelError('duplicate effect dispatch');const worker={generation:effect.generation,cancelled:false,abort:new AbortController(),task:Promise.resolve()};this.#workers.set(key,worker);
  worker.task=Promise.resolve().then(()=>this.#work(effect.generation,effect.attempt,worker.abort.signal)).then(outcome=>outcome==='Success'||outcome==='RetryableFailure'?outcome:'RetryableFailure',()=> 'RetryableFailure' as const).then(outcome=>{this.#push(worker.cancelled?{kind:'Cancelled',generation:effect.generation,attempt:effect.attempt}:{kind:'Complete',generation:effect.generation,attempt:effect.attempt,outcome});});
 }else if(effect.kind==='Cancel'){const worker=this.#workers.get(`${effect.generation}:${effect.attempt}`);if(worker){worker.cancelled=true;worker.abort.abort();}}
 else if(effect.kind==='Publish')this.#published.push(effect.generation);
 else if(effect.kind==='Settle'){if(this.#settled.has(effect.generation))throw new ModelError('host saw duplicate settlement');this.#settled.set(effect.generation,effect.result);}
 else{if(!this.#settled.has(effect.generation))throw new ModelError('host saw release before settlement');for(const [key,worker] of this.#workers)if(worker.generation===effect.generation){await worker.task;this.#workers.delete(key);}}
 }
 /** Rust Host::tick: the caller supplies logical time through the same input boundary. */
 async tick(now:bigint):Promise<void>{await this.deliver({kind:'Tick',now});}
 async receive(timeoutMs:number):Promise<boolean>{if(!Number.isFinite(timeoutMs)||timeoutMs<0)throw new ModelError('invalid receive timeout');if(this.#busy||this.#receiving)throw new ModelError('reference host busy');this.#receiving=true;try{if(!this.#events.length&&!this.#closed){this.#waiting=true;try{await new Promise<void>(resolve=>{let timer:ReturnType<typeof setTimeout>;const done=()=>{clearTimeout(timer);this.#wake=null;resolve();};this.#wake=done;timer=setTimeout(done,timeoutMs);});}finally{this.#waiting=false;}}if(this.#closed)return false;const input=this.#events.shift();if(!input)return false;this.#receiving=false;await this.deliver(input);return true;}finally{this.#receiving=false;}}
 /** Like Rust Drop: requests cancellation and detaches workers. A receive that is only waiting is woken and returns false. */
 async close():Promise<void>{if(this.#busy||(this.#receiving&&!this.#waiting))throw new ModelError('reference host busy');this.#closed=true;this.#wake?.();for(const worker of this.#workers.values()){worker.cancelled=true;worker.abort.abort();}this.#workers.clear();this.#events.length=0;}
}
