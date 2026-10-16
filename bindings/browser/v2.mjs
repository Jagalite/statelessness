/** ABI 2 host-value adapter. Explicit async initialization, synchronous sessions.
 * No reducer/search/replay implementation lives here. Session roots and copied
 * snapshots belong to one WebAssembly instance; there is no backend fallback.
 */
import {check,disposition,metadata,natural,scalar,validateTraceLimits,DEFAULT_TRACE_LIMITS} from './v2-values.mjs';
import {toPacket,fromPacket} from './v2-packet.mjs';
export {parseTrace,stringifyTrace} from './v2-json.mjs';
export {check,disposition,DEFAULT_TRACE_LIMITS};
const MAX=(1n<<64n)-1n,MAX_BYTES=64*1024*1024;
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true});
const kinds=['accepted','rejected','ignored'],statuses=['passed','failed','skipped'];
function diagnostic(error){try{return String(error instanceof Error ? error.message : error).slice(0,16000);}catch{return 'callback failed (diagnostic unavailable)';}}
function u64(value,name='u64') {
  if(typeof value==='number'){natural(value,name);value=BigInt(value);}
  if(typeof value!=='bigint'||value<0n||value>MAX)throw new RangeError(`invalid ${name}`);return value;
}
function size(value,name='size') {const n=u64(value,name);if(n>BigInt(Number.MAX_SAFE_INTEGER))throw new RangeError(`${name} exceeds JS integer precision`);return Number(n);}
function bytes(value) {if(!(value instanceof Uint8Array)||value.length>MAX_BYTES)throw new TypeError('expected bounded Uint8Array');return value;}
function text(value) {scalar(value);return encoder.encode(value);}
export class NativeError extends Error {
  constructor(message,status=11){super(message);this.name='NativeError';this.status=status;}
}

/** Use the feature-built stateless_v2.wasm. An optional digest verifies bytes
 * before instantiation; a precompiled Module has no recoverable byte identity. */
export async function createEngine(source,{expectedSHA256}={}) {
  let digest=null;
  if(!(source instanceof WebAssembly.Module)) {
    bytes(source);
    if(globalThis.crypto?.subtle){digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',source)),b=>b.toString(16).padStart(2,'0')).join('');}
    if(expectedSHA256&&digest!==expectedSHA256)throw new NativeError('Wasm artifact hash mismatch or digest unavailable',3);
  } else if(expectedSHA256)throw new NativeError('verify bytes before compiling a WebAssembly.Module',3);
  const engine=new Engine(digest);
  const result=await WebAssembly.instantiate(source,{
    stateless_host:{dispatch:()=>17},
    stateless_v2:{dispatch:(context,...args)=>engine.sessions.get(context)?._dispatch(...args)??11,
                  release:(context,kind,token)=>engine.sessions.get(context)?._release(kind,token)}
  });
  engine.api=(result.instance??result).exports;
  if(engine.api.stateless_v2_abi_version?.()!==2)throw new NativeError('expected ABI 2 engine',3);
  for(const name of ['wasm_session_new','record_cursor','record_cursor_limited','replay','enumerate','trace_convert','rng_next','rng_index','ring_new','ring_observe','ring_snapshot','ring_release'])
    if(typeof engine.api['stateless_v2_'+name]!=='function')throw new NativeError(`engine missing ${name}`,3);
  return engine;
}
class Engine {
  constructor(sha256){this.sha256=sha256;this.sessions=new Map();this.nextContext=1n;}
  read(pointer,length){const n=size(length);if(n>MAX_BYTES)throw new NativeError('memory copy exceeds limit',18);return new Uint8Array(this.api.memory.buffer,pointer,n).slice();}
  scope(work){
    const buffers=[];
    const own=value=>{
      bytes(value);value=new Uint8Array(value);const handle=this.api.stateless_buffer_new(value.length);
      if(!handle)throw new NativeError('Wasm scratch allocation failed',18);
      buffers.push(handle);const pointer=this.api.stateless_buffer_data(handle);
      new Uint8Array(this.api.memory.buffer,pointer,value.length).set(value);
      return pointer;
    };
    try{return work(own);}finally{for(const b of buffers.reverse())this.api.stateless_buffer_free(b);}
  }
  view(){return new DataView(this.api.memory.buffer);}
  error(status){
    return this.scope(own=>{
      const code=own(new Uint8Array(4)),op=own(new Uint8Array(4)),phase=own(new Uint8Array(4)),seq=own(new Uint8Array(8)),written=own(new Uint8Array(8));
      const fn=this.api.stateless_v2_last_error;
      fn(code,op,phase,seq,0,0n,written);const length=this.view().getBigUint64(written,true);
      if(length>1048576n)return new NativeError(`native status ${status}; diagnostic too large`,status);
      const data=own(new Uint8Array(Number(length)));fn(code,op,phase,seq,data,length,written);
      return new NativeError(decoder.decode(this.read(data,length))||`native status ${status}`,status);
    });
  }
  ok(status){if(status!==0)throw this.error(status);}
  createSession(model,limits={}){return new Session(this,model,limits);}
  rng(seed){return new NativeRng(this,u64(seed,'seed'));}
}
class NativeRng {
  constructor(engine,seed){this.engine=engine;this.state=seed;}
  nextU64(){return this.engine.scope(own=>{const state=own(new Uint8Array(8)),out=own(new Uint8Array(8));this.engine.view().setBigUint64(state,this.state,true);this.engine.ok(this.engine.api.stateless_v2_rng_next(state,out));this.state=this.engine.view().getBigUint64(state,true);return this.engine.view().getBigUint64(out,true);});}
  index(upper){return this.engine.scope(own=>{const state=own(new Uint8Array(8)),out=own(new Uint8Array(8)),present=own(new Uint8Array(4));this.engine.view().setBigUint64(state,this.state,true);this.engine.ok(this.engine.api.stateless_v2_rng_index(state,u64(upper,'upper'),present,out));this.state=this.engine.view().getBigUint64(state,true);return this.engine.view().getUint32(present,true)?this.engine.view().getBigUint64(out,true):null;});}
}
class Session {
  constructor(engine,model,{maxValues=1_000_000,maxItems=250_000,maxBytes=MAX_BYTES,automaticGeneration=false}={}){
    this.engine=engine;this.api=engine.api;this.model=model;this.busy=false;this.handle=0n;
    this.maxValues=natural(maxValues,'maxValues',1);this.maxItems=natural(maxItems,'maxItems',1);this.maxBytes=natural(maxBytes,'maxBytes',1,MAX_BYTES);
    this.roots=new Map();this.nextRoot=1n;this.cleanupErrors=[];
    for(const name of ['initialState','step','checkState','cloneState','cloneInput','cloneOutput'])if(typeof model[name]!=='function')throw new TypeError(`model requires ${name}`);
    let caps=1n;
    if(typeof model.inputs==='function'&&typeof model.equalStates==='function')caps|=2n;
    if(['metadata','encodeState','decodeState','encodeInput','decodeInput','encodeOutput'].every(k=>typeof model[k]==='function'))caps|=4n|128n;
    if(typeof model.generate==='function'&&typeof model.isEnabled==='function')caps|=8n;
    if(typeof automaticGeneration!=='boolean')throw new TypeError('automaticGeneration must be boolean');
    if(automaticGeneration){if(!(caps&2n)||typeof model.equalInputs!=='function')throw new TypeError('automatic generation requires inputs and equalInputs');caps|=8n|256n;}
    if((caps&8n)&&typeof model.simplerInputs==='function'&&typeof model.equalInputs==='function')caps|=16n;
    if(typeof model.hashState==='function')caps|=32n;
    if(typeof model.estimatedStateBytes==='function')caps|=64n;
    this.capabilities=caps;
    this.metadata=metadata((caps&4n)?model.metadata():{name:'host-model',build:'unqualified',model_version:1,properties_version:1,codec_version:1});
    this.context=engine.nextContext++;engine.sessions.set(this.context,this);
    try{engine.scope(own=>{
      const name=text(this.metadata.name),build=text(this.metadata.build),np=own(name),bp=own(build),out=own(new Uint8Array(8));
      engine.ok(this.api.stateless_v2_wasm_session_new(caps,this.context,BigInt(maxValues),BigInt(maxItems),BigInt(maxBytes),np,BigInt(name.length),bp,BigInt(build.length),this.metadata.model_version,this.metadata.properties_version,this.metadata.codec_version,out));
      this.handle=engine.view().getBigUint64(out,true);
    });}catch(error){engine.sessions.delete(this.context);throw error;}
  }
  get retainedRoots(){return this.roots.size;}
  ready(){if(!this.handle)throw new NativeError('session is closed',14);if(this.busy)throw new NativeError('session operation cannot reenter a callback',16);}
  close(){if(!this.handle)return;this.ready();this.busy=true;try{this.engine.ok(this.api.stateless_v2_session_close(this.handle));this.handle=0n;this.engine.sessions.delete(this.context);}finally{this.busy=false;}}
  cancel(){if(!this.handle)throw new NativeError('session is closed',14);this.engine.ok(this.api.stateless_v2_cancel(this.handle));}
  resetCancellation(){this.ready();this.engine.ok(this.api.stateless_v2_cancel_reset(this.handle));}
  copy(kind,value){const busy=this.busy;this.busy=true;try{return this.model[{1:'cloneState',2:'cloneInput',3:'cloneOutput'}[kind]](value);}finally{this.busy=busy;}}
  root(kind,value){if(this.roots.size>=this.maxValues||this.nextRoot>MAX){if(kind===4&&typeof value.return==='function'){const busy=this.busy;this.busy=true;try{value.return();}catch(error){if(this.cleanupErrors.length<16)this.cleanupErrors.push(diagnostic(error));}finally{this.busy=busy;}}throw new NativeError('host root limit',18);}const token=this.nextRoot++;this.roots.set(token,{kind,value});return token;}
  _release(kind,token){
    const busy=this.busy;this.busy=true;
    try{const entry=this.roots.get(token);if(!entry||entry.kind!==kind)throw new NativeError('invalid host release',14);this.roots.delete(token);if(kind===4&&typeof entry.value.return==='function')entry.value.return();}
    catch(error){try{if(this.cleanupErrors.length<16)this.cleanupErrors.push(diagnostic(error));}catch{}}finally{this.busy=busy;}
  }
  value(token,kind,copy=true){const entry=this.roots.get(token);if(!entry||entry.kind!==kind)throw new NativeError('host token kind mismatch',14);return copy?this.copy(kind,entry.value):entry.value;}
  publish(invocation,kind,value){const token=this.root(kind,kind===4?value:this.copy(kind,value));const status=this.api.stateless_v2_callback_value(this.handle,invocation,kind,token);if(status){this._release(kind,token);throw this.engine.error(status);}}
  _dispatch(session,invocation,operation,pointer,count,data,length){
    try{
      if(session!==this.handle)throw new NativeError('callback session mismatch',14);
      if(count>this.maxItems+4)throw new NativeError('callback item limit',18);
      const view=this.engine.view(),args=Array.from({length:count},(_,i)=>view.getBigUint64(pointer+8*i,true));
      const raw=this.engine.read(data,length),value=(i,kind)=>this.value(args[i],kind);
      const scalarValue=value=>this.engine.ok(this.api.stateless_v2_callback_scalar(session,invocation,u64(value,'callback scalar')));
      const checks=values=>{for(const c of values){const checked=check(c.id,c.status,c.details);this.engine.scope(own=>{const id=text(checked.id),details=text(checked.details);this.engine.ok(this.api.stateless_v2_callback_check(session,invocation,statuses.indexOf(checked.status),own(id),BigInt(id.length),own(details),BigInt(details.length)));});}};
      const rng={nextU64:()=>this.engine.scope(own=>{const out=own(new Uint8Array(8));this.engine.ok(this.api.stateless_v2_callback_rng(session,invocation,out));return this.engine.view().getBigUint64(out,true);}),index:upper=>this.engine.scope(own=>{const out=own(new Uint8Array(8)),present=own(new Uint8Array(4));this.engine.ok(this.api.stateless_v2_callback_rng_index(session,invocation,u64(upper),present,out));return this.engine.view().getUint32(present,true)?this.engine.view().getBigUint64(out,true):null;})};
      switch(operation){
        case 1:this.publish(invocation,1,this.model.initialState());break;
        case 2:{const t=this.model.step(value(0,1),value(1,2)),d=disposition(t.disposition.kind,t.disposition.reason);this.publish(invocation,1,t.state);for(const o of t.outputs)this.publish(invocation,3,o);this.engine.scope(own=>{const reason=text(d.reason);this.engine.ok(this.api.stateless_v2_callback_disposition(session,invocation,kinds.indexOf(d.kind),own(reason),BigInt(reason.length)));});break;}
        case 3:checks(this.model.checkState(value(0,1)));break;
        case 4:if(this.model.checkTransition)checks(this.model.checkTransition(value(0,1),value(1,2),{state:value(2,1),outputs:args.slice(4).map(t=>this.value(t,3)),disposition:disposition(kinds[size(args[3])],decoder.decode(raw))}));break;
        case 5:case 17:{const kind=operation===5?1:2,name=operation===5?'equalStates':'equalInputs',equal=this.model[name](value(0,kind),value(1,kind));if(typeof equal!=='boolean')throw new TypeError('equality must return boolean');scalarValue(equal?1n:0n);break;}
        case 6:{const hash=this.model.hashState(value(0,1));if(!['string','number','bigint'].includes(typeof hash))throw new TypeError('invalid state hash');const key=typeof hash+':'+String(hash);let n=0xcbf29ce484222325n;for(let i=0;i<key.length;i++)n=((n^BigInt(key.charCodeAt(i)))*0x100000001b3n)&MAX;scalarValue(n);break;}
        case 7:{const n=this.model.estimatedStateBytes(value(0,1));if(n!==null&&n!==undefined)scalarValue(n);break;}
        case 8:this.publish(invocation,4,this.model.inputs(value(0,1))[Symbol.iterator]());break;
        case 9:{const next=this.value(args[0],4,false).next();if(next===null||typeof next!=='object')throw new TypeError('invalid iterator result');if(!next.done)this.publish(invocation,2,next.value);break;}
        case 10:case 12:case 14:{const [kind,name]={10:[1,'encodeState'],12:[2,'encodeInput'],14:[3,'encodeOutput']}[operation];const encoded=bytes(this.model[name](value(0,kind)));this.engine.scope(own=>this.engine.ok(this.api.stateless_v2_callback_bytes(session,invocation,own(encoded),BigInt(encoded.length))));break;}
        case 11:this.publish(invocation,1,this.model.decodeState(raw));break;
        case 13:this.publish(invocation,2,this.model.decodeInput(raw));break;
        case 15:{const next=this.model.generate(value(0,1),rng);if(next!==null&&next!==undefined)this.publish(invocation,2,next);break;}
        case 16:{const enabled=this.model.isEnabled(value(0,1),value(1,2));if(typeof enabled!=='boolean')throw new TypeError('isEnabled must return boolean');scalarValue(enabled?1n:0n);break;}
        case 18:this.publish(invocation,4,this.model.simplerInputs(value(0,2))[Symbol.iterator]());break;
        default:throw new NativeError(`unsupported callback ${operation}`,17);
      }
      return 0;
    }catch(error){try{this.engine.scope(own=>{const message=encoder.encode(diagnostic(error));this.api.stateless_v2_callback_error(session,invocation,own(message),BigInt(message.length));});}catch{}return 11;}
  }
  import(kind,value){const token=this.root(kind,kind===4?value:this.copy(kind,value));let transferred=false;try{return this.engine.scope(own=>{const out=own(new Uint8Array(8));const status=this.api.stateless_v2_value_import(this.handle,kind,token,out);if(status)throw this.engine.error(status);transferred=true;return this.engine.view().getBigUint64(out,true);});}finally{if(!transferred)this._release(kind,token);}}
  release(value){this.engine.ok(this.api.stateless_v2_value_release(this.handle,value));}
  result(name,...args){this.ready();this.busy=true;try{return this.engine.scope(own=>{const out=own(new Uint8Array(8)),status=this.api['stateless_v2_'+name](this.handle,...args,out),handle=this.engine.view().getBigUint64(out,true);if(![0,1,2,3].includes(status)&&!(status===11&&handle&&['record_cursor','record_cursor_limited','ring_snapshot'].includes(name))){const error=this.engine.error(status);if(handle)this.api.stateless_v2_result_release(this.handle,handle);throw error;}if(!handle)throw new NativeError('engine returned no completed result');return new Result(this,handle,status);});}finally{this.busy=false;}}
  search({maxStates=100_000,maxTransitions=1_000_000,maxDepth=100,maxStateBytes=MAX,maxDurationMillis=0}={}){return this.result('enumerate',u64(maxStates),u64(maxTransitions),u64(maxDepth),u64(maxStateBytes),u64(maxDurationMillis));}
  enumerateStates(config){const r=this.search(config);try{return r.searchReport();}finally{r.close();}}
  checkInitial(state){this.ready();const value=this.import(1,state);try{const r=this.result('check_initial',value);try{return r.checks().map(v=>v.check);}finally{r.close();}}finally{this.release(value);}}
  observation(before,input,t,work){this.ready();const handles=[];const own=(kind,value)=>{const h=this.import(kind,value);handles.push(h);return h;};try{const b=own(1,before),i=own(2,input),a=own(1,t.state),outputs=Array.from(t.outputs,o=>own(3,o)),d=disposition(t.disposition.kind,t.disposition.reason);return this.engine.scope(allocate=>{const out=new Uint8Array(outputs.length*8),view=new DataView(out.buffer);outputs.forEach((h,i)=>view.setBigUint64(i*8,h,true));const reason=text(d.reason);return work(b,i,a,allocate(out),BigInt(outputs.length),kinds.indexOf(d.kind),allocate(reason),BigInt(reason.length));});}finally{for(const h of handles.reverse())this.release(h);}}
  checkObserved(before,input,t,sequence,{stateEvery=1,transitionChecks=true}={}){if(typeof transitionChecks!=='boolean')throw new TypeError('transitionChecks must be boolean');if(u64(sequence)===0n||u64(stateEvery)===0n)throw new RangeError('positive sequence and stateEvery required');return this.observation(before,input,t,(...args)=>{const r=this.result('check_observed',...args,u64(sequence),u64(stateEvery),transitionChecks?1:0);try{return r.checks().map(v=>v.check);}finally{r.close();}});}
  require(capability){this.ready();if((this.capabilities&capability)!==capability)throw new NativeError('model lacks required capability',17);}
  recordBinary(inputs,maxSteps=100_000){this.require(4n);const cursor=this.import(4,inputs[Symbol.iterator]());try{const r=this.result('record_cursor',cursor,u64(maxSteps));try{return r.bytes();}finally{r.close();}}finally{this.release(cursor);}}
  convert(format,artifact){return this.engine.scope(own=>{bytes(artifact);const r=this.result('trace_convert',format,own(artifact),BigInt(artifact.length));try{return r.bytes();}finally{r.close();}});}
  record(inputs,maxSteps=100_000,limits=DEFAULT_TRACE_LIMITS){this.require(4n);validateTraceLimits(limits);const maximum=Math.min(size(maxSteps),limits.maxSteps),cursor=this.import(4,inputs[Symbol.iterator]());let artifact;try{const r=this.result('record_cursor_limited',cursor,BigInt(maximum),BigInt(limits.maxBlobBytes),BigInt(limits.maxItems),BigInt(limits.maxPayloadBytes));try{artifact=r.bytes();}finally{r.close();}}finally{this.release(cursor);}return fromPacket(this.convert(1,artifact),limits);}
  replay(trace,{allowBuildMismatch=false}={}){this.require(4n);if(typeof allowBuildMismatch!=='boolean')throw new TypeError('allowBuildMismatch must be boolean');const artifact=trace instanceof Uint8Array?trace:this.convert(2,toPacket(trace));return this.engine.scope(own=>{const r=this.result('replay',own(artifact),BigInt(artifact.length),allowBuildMismatch?1:0);try{return {outcome:[0,1].includes(r.status)?'exact':r.status===2?'diverged':'incompatible',steps_verified:size(r.scalar(14)),failure_reproduced:!!r.scalar(16),build_matches:!!r.scalar(15),step:r.scalar(20)?size(r.scalar(21)):null,field:r.text(5)||null};}finally{r.close();}});}
  fuzz({seed=0n,cases=100,maxSteps=100,maxTransitions=1_000_000,mutationPercent=0,maxDurationMillis=0}={}){return this.result('fuzz',u64(seed),u64(cases),u64(maxSteps),u64(maxTransitions),natural(mutationPercent,'mutationPercent',0,100),u64(maxDurationMillis));}
  shrink(failure,{maxAttempts=10_000,maxTransitions=MAX,maxDurationMillis=0}={}){if(!(failure instanceof Result)||failure.session!==this)throw new NativeError('result belongs to another session',14);failure.ready();return this.result('shrink',failure.handle,u64(maxAttempts),u64(maxTransitions),u64(maxDurationMillis));}
  ring(initialState,{maxSteps=100,maxRetainedBytes=32*1024*1024}={}){this.require(128n);const state=this.import(1,initialState);try{return this.engine.scope(own=>{const out=own(new Uint8Array(8));this.busy=true;try{this.engine.ok(this.api.stateless_v2_ring_new(this.handle,state,u64(maxSteps),u64(maxRetainedBytes),out));return new Ring(this,this.engine.view().getBigUint64(out,true));}finally{this.busy=false;}});}finally{this.release(state);}}
}
class Result {
  constructor(session,handle,status){this.session=session;this.handle=handle;this.status=status;}
  ready(){this.session.ready();if(!this.handle)throw new NativeError('result is closed',14);}
  close(){if(!this.handle)return;if(!this.session.handle){this.handle=0n;return;}this.ready();this.session.engine.ok(this.session.api.stateless_v2_result_release(this.session.handle,this.handle));this.handle=0n;}
  scalar(field){this.ready();const s=this.session;return s.engine.scope(own=>{const out=own(new Uint8Array(8));s.engine.ok(s.api.stateless_v2_result_scalar(s.handle,this.handle,field,out));return s.engine.view().getBigUint64(out,true);});}
  bytes(field=1,index=0n){this.ready();const s=this.session;return s.engine.scope(own=>{const count=own(new Uint8Array(8)),fn=s.api.stateless_v2_result_copy;const status=fn(s.handle,this.handle,field,u64(index),0,0n,count);if(status!==0&&status!==18)throw s.engine.error(status);const n=s.engine.view().getBigUint64(count,true);if(n>BigInt(s.maxBytes))throw new NativeError('result byte limit',18);const out=own(new Uint8Array(size(n)));s.engine.ok(fn(s.handle,this.handle,field,u64(index),out,n,count));return s.engine.read(out,n);});}
  text(field,index=0n){return decoder.decode(this.bytes(field,index));}
  checks(){const s=this.session;return Array.from({length:size(this.scalar(10))},(_,i)=>s.engine.scope(own=>{const status=own(new Uint8Array(4)),phase=own(new Uint8Array(4));s.engine.ok(s.api.stateless_v2_result_check(s.handle,this.handle,BigInt(i),status,phase));return {phase:['unspecified','initial_state','state','transition'][s.engine.view().getUint32(phase,true)],check:check(this.text(2,i),statuses[s.engine.view().getUint32(status,true)],this.text(3,i))};}));}
  inputs(){const s=this.session;return Array.from({length:size(this.scalar(11))},(_,i)=>s.engine.scope(own=>{const out=own(new Uint8Array(8)),kind=own(new Uint8Array(4)),token=own(new Uint8Array(8));s.engine.ok(s.api.stateless_v2_result_input(s.handle,this.handle,BigInt(i),out));const value=s.engine.view().getBigUint64(out,true);try{s.engine.ok(s.api.stateless_v2_value_host_token(s.handle,value,kind,token));return s.value(s.engine.view().getBigUint64(token,true),2);}finally{s.release(value);}}));}
  searchReport(){if(this.scalar(2)!==2n)throw new TypeError('not an enumeration result');const violations=this.checks().filter(v=>v.check.status==='failed');return {termination:['graph_exhausted','depth_bound','state_limit','transition_limit','failure_found','estimated_state_byte_limit','deadline','cancelled'][size(this.scalar(3))],states:size(this.scalar(4)),transitions:size(this.scalar(5)),max_depth_reached:size(this.scalar(6)),skipped_checks:size(this.scalar(7)),failure:violations.length?{inputs:this.inputs(),violations}:null};}
}
class Ring {
  constructor(session,handle){this.session=session;this.handle=handle;}
  ready(){this.session.ready();if(!this.handle)throw new NativeError('recorder is closed',14);}
  close(){if(!this.handle)return;if(!this.session.handle){this.handle=0n;return;}this.ready();this.session.engine.ok(this.session.api.stateless_v2_ring_release(this.session.handle,this.handle));this.handle=0n;}
  observe(before,input,transition){this.ready();const s=this.session;return s.observation(before,input,transition,(...args)=>{const r=s.result('ring_observe',this.handle,...args);try{return r.checks().map(v=>v.check);}finally{r.close();}});}
  snapshot(){this.ready();const r=this.session.result('ring_snapshot',this.handle);try{return r.bytes();}finally{r.close();}}
}
