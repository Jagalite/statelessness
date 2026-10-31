import {Model,EnumerateModel,Transition,Check,ModelError,accepted,natural} from './core.js';
import {CheckSink,withCheckSink,appendStateChecks,appendTransitionChecks,requireSynchronousCheckReturn} from './checks.js';
export interface NetworkAddress<T>{readonly machine:number;readonly value:T}
export interface NetworkRoute<I>{readonly destination:number;readonly channel:number;readonly input:I}
export interface NetworkMessage<I>{readonly source:number;readonly route:NetworkRoute<I>}
export type NetworkInput<I>={readonly kind:'local';readonly address:NetworkAddress<I>}|{readonly kind:'deliver'|'drop'|'duplicate';readonly index:number};
export interface NetworkState<S,I>{readonly machines:readonly NetworkAddress<S>[];readonly pending:readonly NetworkMessage<I>[]}
/** Find a machine state in canonical, strictly increasing machine-ID order.
 * Like Rust's State.machine, this returns the stored value by reference; an
 * undefined result can also be a stored undefined value. */
export function networkMachine<S,I>(state:NetworkState<S,I>,id:number):S|undefined {
 natural(id,'machine id',0,0xffffffff);
 let lo=0,hi=state.machines.length;
 while(lo<hi){const mid=lo+Math.floor((hi-lo)/2),entry=state.machines[mid];if(entry.machine<id)lo=mid+1;else hi=mid;}
 return lo<state.machines.length&&state.machines[lo].machine===id?state.machines[lo].value:undefined;
}
export interface QueuePolicy{readonly delivery:'Unordered'|'FifoPerChannel';readonly maxPending:number;readonly maxPerChannel:number;readonly maxRoutesPerOutput:number;readonly allowDrop:boolean;readonly allowDuplicate:boolean}
export const DEFAULT_QUEUE:QueuePolicy=Object.freeze({delivery:'FifoPerChannel',maxPending:32,maxPerChannel:32,maxRoutesPerOutput:8,allowDrop:false,allowDuplicate:false});
export interface NetworkWiring<S,I,O>{metadata?():Metadata;routes(o:NetworkAddress<O>):Iterable<NetworkRoute<I>>;checkState(s:NetworkState<S,I>):Iterable<Check>;checkStateInto?(s:NetworkState<S,I>,sink:CheckSink):void;checkTransition?(s:NetworkState<S,I>,i:NetworkInput<I>,t:Transition<NetworkState<S,I>,NetworkAddress<O>>):Iterable<Check>;checkTransitionInto?(s:NetworkState<S,I>,i:NetworkInput<I>,t:Transition<NetworkState<S,I>,NetworkAddress<O>>,sink:CheckSink):void}
function sameChannel<I>(a:NetworkMessage<I>,b:NetworkMessage<I>):boolean{return a.source===b.source&&a.route.destination===b.route.destination&&a.route.channel===b.route.channel;}
function channelKey<I>(message:NetworkMessage<I>):string{return `${message.source}:${message.route.destination}:${message.route.channel}`;}
function equality(value:unknown):boolean{if(typeof value==='boolean')return value;if(value instanceof Promise)void value.catch(()=>{});throw new ModelError('network equality must return boolean synchronously');}
function namespace(prefix:string,sink:CheckSink,body:(child:CheckSink)=>void):void{const checks:Check[]=[];try{withCheckSink(checks,body);}finally{for(const c of checks){const id=prefix+'::'+c.id;if(sink.snapshot().some(p=>p.id===id))throw new ModelError('duplicate network property id: '+id);sink.push({...c,id});}}}
export class Network<S,I,O> implements Model<NetworkState<S,I>,NetworkInput<I>,NetworkAddress<O>> {
 readonly #machines:ReadonlyMap<number,EnumerateModel<S,I,O>>;
 readonly policy:QueuePolicy;
 constructor(machines:Iterable<readonly [number,EnumerateModel<S,I,O>]>,readonly wiring:NetworkWiring<S,I,O>,policy:QueuePolicy=DEFAULT_QUEUE,readonly equalInput?: (a:I,b:I)=>boolean){const entries=[...machines];for(const [id]of entries)natural(id,'machine id',0,0xffffffff);this.#machines=new Map(entries.sort((a,b)=>a[0]-b[0]));if(!entries.length||entries.length!==this.#machines.size)throw new ModelError('empty or duplicate network machine id');const owned={delivery:policy.delivery,maxPending:policy.maxPending,maxPerChannel:policy.maxPerChannel,maxRoutesPerOutput:policy.maxRoutesPerOutput,allowDrop:policy.allowDrop,allowDuplicate:policy.allowDuplicate};for(const n of ['maxPending','maxPerChannel','maxRoutesPerOutput'] as const)natural(owned[n],n);natural(owned.maxPending,'pending',0,0xffffffff);if(typeof owned.allowDrop!=='boolean'||typeof owned.allowDuplicate!=='boolean')throw new ModelError('queue flags must be boolean');if(!['Unordered','FifoPerChannel'].includes(owned.delivery))throw new ModelError('invalid delivery order');this.policy=Object.freeze(owned);}
 equalStates(a:NetworkState<S,I>,b:NetworkState<S,I>):boolean {
  this.validate(a); this.validate(b);
  const equalInput=this.equalInput;
  if(!equalInput)throw new ModelError('network equality requires input equality');
  // Pin child callbacks before invoking any of them; JS callers can mutate
  // otherwise-readonly model objects from inside a callback.
  const callbacks=new Map([...this.#machines].map(([id,owner])=>[id,{owner,cloneState:owner.cloneState,cloneInput:owner.cloneInput,equalStates:owner.equalStates}] as const));
  const snapshot=(s:NetworkState<S,I>):NetworkState<S,I>=>{
   const pending=s.pending.map(m=>{const c=callbacks.get(m.route.destination)!;return {source:m.source,route:{destination:m.route.destination,channel:m.route.channel,input:c.cloneInput.call(c.owner,m.route.input)}};});
   const machines=s.machines.map(m=>{const c=callbacks.get(m.machine)!;return {machine:m.machine,value:c.cloneState.call(c.owner,m.value)};});
   return {machines,pending};
  };
  const left=snapshot(a),right=snapshot(b);
  return left.machines.every((m,j)=>{const c=callbacks.get(m.machine)!;return equality(c.equalStates.call(c.owner,m.value,right.machines[j].value));})&&left.pending.length===right.pending.length&&left.pending.every((m,j)=>sameChannel(m,right.pending[j])&&equality(equalInput.call(this,m.route.input,right.pending[j].route.input)));
 }
 metadata():Metadata {
  const identity=(m:Metadata)=>`${new TextEncoder().encode(m.name).length}:${m.name}/${m.model_version}/${m.properties_version}/${m.codec_version}`;let names='',builds='';
  for(const [id,child]of this.#machines){const c=child as unknown as Partial<CodecModel<S,I,O>>;if(typeof c.metadata!=='function')throw new ModelError('network child metadata required');const m=c.metadata(),n=identity(m);names+=`${id}=${new TextEncoder().encode(n).length}:${n};`;builds+=`${new TextEncoder().encode(m.build).length}:${m.build}`;}
  if(!this.wiring.metadata)throw new ModelError('network wiring metadata required');const w=this.wiring.metadata(),n=identity(w);names+=`w=${new TextEncoder().encode(n).length}:${n};`;builds+=`${new TextEncoder().encode(w.build).length}:${w.build}`;const p=this.policy;return {name:`network-v1:${names}policy=${p.delivery},${p.maxPending},${p.maxPerChannel},${p.maxRoutesPerOutput},${p.allowDrop},${p.allowDuplicate}`,model_version:1,properties_version:1,codec_version:1,build:builds+';native'};
 }
 machineIDs():readonly number[]{return [...this.#machines.keys()];}
 private child(id:number){const m=this.#machines.get(id);if(!m)throw new ModelError('unknown network machine');return m;}
 initialState():NetworkState<S,I>{return {machines:[...this.#machines].map(([machine,m])=>({machine,value:m.cloneState(m.initialState())})),pending:[]};}
 cloneState(s:NetworkState<S,I>):NetworkState<S,I>{
  // Capture structural fields first, and queue payloads before machine hooks:
  // a child clone can otherwise mutate the caller's queue through an alias.
  const machines=s.machines.map(({machine,value})=>({machine,value}));
  const pending=s.pending.map(m=>({source:m.source,route:{destination:m.route.destination,channel:m.route.channel,input:m.route.input}}));
  const callbacks=new Map([...this.#machines].map(([id,owner])=>[id,{owner,cloneInput:owner.cloneInput,cloneState:owner.cloneState}] as const));
  const callback=(id:number)=>{const c=callbacks.get(id);if(!c)throw new ModelError('unknown network machine');return c;};
  const copiedPending=pending.map(m=>{const c=callback(m.route.destination);return {source:m.source,route:{...m.route,input:c.cloneInput.call(c.owner,m.route.input)}};});
  return {machines:machines.map(a=>{const c=callback(a.machine);return {machine:a.machine,value:c.cloneState.call(c.owner,a.value)};}),pending:copiedPending};
 }
 cloneInput(i:NetworkInput<I>):NetworkInput<I>{if(i.kind==='local'){const address=i.address,machine=address.machine;return {kind:'local',address:{machine,value:this.child(machine).cloneInput(address.value)}};}if(i.kind!=='deliver'&&i.kind!=='drop'&&i.kind!=='duplicate')throw new ModelError('invalid network action');natural(i.index,'pending index',0,0xffffffff);return {kind:i.kind,index:i.index};}
 cloneOutput(o:NetworkAddress<O>):NetworkAddress<O>{return {machine:o.machine,value:this.child(o.machine).cloneOutput(o.value)};}
 validate(s:NetworkState<S,I>,policy:QueuePolicy=this.policy):void{const ids=[...this.#machines.keys()];if(ids.length!==s.machines.length||ids.some((id,j)=>s.machines[j].machine!==id))throw new ModelError('network state machine identity mismatch');if(s.pending.length>policy.maxPending)throw new ModelError('network pending bound exceeded; verification incomplete');const counts=new Map<string,number>();for(const m of s.pending){this.child(m.source);this.child(m.route.destination);natural(m.route.channel,'channel',0,0xffffffff);const key=channelKey(m),count=(counts.get(key)??0)+1;if(count>policy.maxPerChannel)throw new ModelError('network channel bound exceeded; verification incomplete');counts.set(key,count);}}
 private deliverable(s:NetworkState<S,I>,n:number,policy:QueuePolicy){return policy.delivery==='Unordered'||!s.pending.slice(0,n).some(m=>sameChannel(m,s.pending[n]));}
 private addressed(s:NetworkState<S,I>,i:NetworkInput<I>,policy:QueuePolicy=this.policy):NetworkAddress<I>|undefined{
  if(i.kind==='local'){this.child(i.address.machine);return i.address;}natural(i.index,'pending index',0,0xffffffff);if(i.index>=s.pending.length)throw new ModelError('network pending index out of range');
  if(i.kind==='deliver'){if(!this.deliverable(s,i.index,policy))throw new ModelError('network FIFO channel order violation');const m=s.pending[i.index];return {machine:m.route.destination,value:m.route.input};}
  if(i.kind==='drop'&&!policy.allowDrop||i.kind==='duplicate'&&!policy.allowDuplicate)throw new ModelError('network action disabled');if(i.kind!=='drop'&&i.kind!=='duplicate')throw new ModelError('invalid network action');return undefined;
 }
 private enqueue(q:NetworkMessage<I>[],m:NetworkMessage<I>,policy:QueuePolicy){const child=this.child(m.route.destination);natural(m.route.channel,'channel',0,0xffffffff);if(q.length>=policy.maxPending||q.filter(v=>sameChannel(m,v)).length>=policy.maxPerChannel)throw new ModelError('network queue bound exceeded; verification incomplete');q.push({source:m.source,route:{...m.route,input:child.cloneInput(m.route.input)}});}
 step(s:NetworkState<S,I>,i:NetworkInput<I>):Transition<NetworkState<S,I>,NetworkAddress<O>>{
  const policy=this.policy;this.validate(s,policy);this.addressed(s,i,policy);
  // Freeze address metadata and pending payloads before machine clone hooks can
  // mutate caller-owned queue entries. Hooks must still cooperate across copies.
  const rawMachines=s.machines.map(({machine,value})=>({machine,value}));
  const rawPending=s.pending.map(m=>({source:m.source,route:{destination:m.route.destination,channel:m.route.channel,input:m.route.input}}));
  const input=this.cloneInput(i);
  const pendingSnapshot=rawPending.map(m=>({source:m.source,route:{...m.route,input:this.child(m.route.destination).cloneInput(m.route.input)}}));
  const snapshot:NetworkState<S,I>={machines:rawMachines.map(a=>({machine:a.machine,value:this.child(a.machine).cloneState(a.value)})),pending:pendingSnapshot};
  this.validate(snapshot,policy);const a=this.addressed(snapshot,input,policy),machines=[...snapshot.machines],pending=[...snapshot.pending];if(input.kind==='deliver'||input.kind==='drop')pending.splice(input.index,1);if(input.kind==='duplicate')this.enqueue(pending,snapshot.pending[input.index],policy);if(!a)return accepted({machines,pending});
  const child=this.child(a.machine),n=machines.findIndex(v=>v.machine===a.machine),t=child.step(child.cloneState(machines[n].value),child.cloneInput(a.value)),disposition={...t.disposition};machines[n]={machine:a.machine,value:child.cloneState(t.state)};const outputs=t.outputs.map(value=>this.cloneOutput({machine:a.machine,value}));
  for(const o of outputs){let count=0;for(const route of this.wiring.routes(this.cloneOutput(o))){if(++count>policy.maxRoutesPerOutput)throw new ModelError('network fanout bound exceeded; verification incomplete');this.enqueue(pending,{source:a.machine,route},policy);}}return {state:{machines,pending},outputs,disposition};
 }
 inputs(s:NetworkState<S,I>):Iterable<NetworkInput<I>>{
  const current=this.policy,policy:QueuePolicy={delivery:current.delivery,maxPending:current.maxPending,maxPerChannel:current.maxPerChannel,maxRoutesPerOutput:current.maxRoutesPerOutput,allowDrop:current.allowDrop,allowDuplicate:current.allowDuplicate};this.validate(s,policy);
  // Scheduling depends only on channel identities, not queued payloads. Capture
  // metadata and machine addresses before application clone hooks run.
  const channels=s.pending.map(channelKey),raw=s.machines.map(({machine,value})=>({machine,value}));
  const callbacks=new Map([...this.#machines].map(([id,owner])=>[id,{owner,cloneState:owner.cloneState,cloneInput:owner.cloneInput,inputs:owner.inputs}] as const));
  const machines=raw.map(a=>{const c=callbacks.get(a.machine)!;return {machine:a.machine,value:c.cloneState.call(c.owner,a.value)};});
  return this.inputsOwned(machines,channels,policy,callbacks);
 }
 private *inputsOwned(machines:readonly NetworkAddress<S>[],channels:readonly string[],policy:QueuePolicy,callbacks:ReadonlyMap<number,{owner:EnumerateModel<S,I,O>;cloneState:EnumerateModel<S,I,O>['cloneState'];cloneInput:EnumerateModel<S,I,O>['cloneInput'];inputs:EnumerateModel<S,I,O>['inputs']}>):Iterable<NetworkInput<I>>{for(const a of machines){const c=callbacks.get(a.machine)!;for(const value of c.inputs.call(c.owner,c.cloneState.call(c.owner,a.value)))yield {kind:'local',address:{machine:a.machine,value:c.cloneInput.call(c.owner,value)}};}const counts=new Map<string,number>(),seen=new Set<string>();for(const key of channels)counts.set(key,(counts.get(key)??0)+1);for(let n=0;n<channels.length;n++){const key=channels[n];if(policy.delivery==='Unordered'||!seen.has(key))yield {kind:'deliver',index:n};seen.add(key);if(policy.allowDrop)yield {kind:'drop',index:n};if(policy.allowDuplicate&&channels.length<policy.maxPending&&counts.get(key)!<policy.maxPerChannel)yield {kind:'duplicate',index:n};}}
 checkStateInto(s:NetworkState<S,I>,sink:CheckSink):void{this.validate(s);s=this.cloneState(s);for(const a of s.machines)namespace(`machine/${a.machine}`,sink,c=>appendStateChecks(this.child(a.machine),this.child(a.machine).cloneState(a.value),c));namespace('global',sink,c=>{const state=this.cloneState(s);if(this.wiring.checkStateInto)requireSynchronousCheckReturn(this.wiring.checkStateInto(state,c));else c.extend(this.wiring.checkState(state));});}
 checkState(s:NetworkState<S,I>):Check[]{const c:Check[]=[];withCheckSink(c,sink=>this.checkStateInto(s,sink));return c;}
 checkTransitionInto(s:NetworkState<S,I>,i:NetworkInput<I>,t:Transition<NetworkState<S,I>,NetworkAddress<O>>,sink:CheckSink):void{
  // A queue-only action has no child check. When no global transition check is
  // configured, validate its observation without invoking application clone hooks.
  this.validate(s);this.validate(t.state);
  const addressed=this.addressed(s,i),target=addressed?.machine;
  const wiring=this.wiring,globalInto=wiring.checkTransitionInto,globalLegacy=wiring.checkTransition;
  if(target===undefined){if(t.outputs.length)throw new ModelError('queue action emitted child output');if(!globalInto&&!globalLegacy)return;}
  // Freeze output slots before any user hook. Map each address and output in
  // order, as Rust does: an earlier output copy failure wins over a later bad
  // address. Other snapshots happen first for isolation, but their failures are
  // observed only after output mapping succeeds.
  const rawOutputs=t.outputs.map(o=>({machine:o.machine,value:o.value})),afterState=t.state,disposition={...t.disposition};
  if(target!==undefined&&rawOutputs.length&&rawOutputs[0].machine!==target)throw new ModelError('wrong network child output address');
  const callbacks=new Map([...this.#machines].map(([id,owner])=>[id,{owner,cloneState:owner.cloneState,cloneInput:owner.cloneInput,cloneOutput:owner.cloneOutput}] as const));
  const callback=(id:number)=>{const c=callbacks.get(id);if(!c)throw new ModelError('unknown network machine');return c;};
  const targetOwner=target===undefined?undefined:callback(target).owner,targetInto=targetOwner?.checkTransitionInto,targetLegacy=targetInto===undefined?targetOwner?.checkTransition:undefined;
  const rawAction:NetworkInput<I>=i.kind==='local'?{kind:'local',address:{machine:i.address.machine,value:i.address.value}}:{kind:i.kind,index:i.index};
  const rawState=(state:NetworkState<S,I>):NetworkState<S,I>=>({machines:state.machines.map(({machine,value})=>({machine,value})),pending:state.pending.map(m=>({source:m.source,route:{destination:m.route.destination,channel:m.route.channel,input:m.route.input}}))});
  const rawBefore=rawState(s),rawAfter=rawState(afterState);
  const copyInput=(input:NetworkInput<I>):NetworkInput<I>=>{if(input.kind!=='local')return {...input};const c=callback(input.address.machine);return {kind:'local',address:{machine:input.address.machine,value:c.cloneInput.call(c.owner,input.address.value)}};};
  const copyState=(state:NetworkState<S,I>):NetworkState<S,I>=>({pending:state.pending.map(m=>{const c=callback(m.route.destination);return {source:m.source,route:{...m.route,input:c.cloneInput.call(c.owner,m.route.input)}};}),machines:state.machines.map(m=>{const c=callback(m.machine);return {machine:m.machine,value:c.cloneState.call(c.owner,m.value)};})});
  const copyOutput=(o:NetworkAddress<O>):NetworkAddress<O>=>{const c=callback(o.machine);return {machine:o.machine,value:c.cloneOutput.call(c.owner,o.value)};};
  type Capture<T>={ok:true;value:T}|{ok:false;error:unknown};
  const capture=<T>(f:()=>T):Capture<T>=>{try{return {ok:true,value:f()};}catch(error){return {ok:false,error};}};
  const get=<T>(v:Capture<T>):T=>{if(v.ok)return v.value;throw v.error;};
  const capturedInput=capture(()=>copyInput(rawAction)),capturedBefore=capture(()=>copyState(rawBefore)),capturedAfter=capture(()=>copyState(rawAfter));
  const outputs:NetworkAddress<O>[]=[];
  if(target!==undefined)for(const o of rawOutputs){if(o.machine!==target)throw new ModelError('wrong network child output address');outputs.push(copyOutput(o));}
  i=get(capturedInput);s=get(capturedBefore);t={state:get(capturedAfter),outputs,disposition};const a=target===undefined?undefined:{machine:target,value:i.kind==='local'?i.address.value:s.pending[(i as {index:number}).index].route.input};if(a){const c=callback(target!),child=c.owner;const facade={cloneState:c.cloneState.bind(child),cloneInput:c.cloneInput.bind(child),cloneOutput:c.cloneOutput.bind(child),checkTransition:typeof targetLegacy==='function'?targetLegacy.bind(child):targetLegacy,checkTransitionInto:typeof targetInto==='function'?targetInto.bind(child):targetInto} as unknown as Model<S,I,O>;const before=c.cloneState.call(child,s.machines.find(v=>v.machine===target)!.value),after=c.cloneState.call(child,t.state.machines.find(v=>v.machine===target)!.value);namespace(`machine/${target}`,sink,sinkChild=>appendTransitionChecks(facade,before,c.cloneInput.call(child,a.value),{state:after,outputs:t.outputs.map(o=>o.value),disposition:{...t.disposition}},sinkChild));}else if(t.outputs.length)throw new ModelError('queue action emitted child output');
  if(globalInto||globalLegacy)namespace('global',sink,c=>{const before=copyState(s),input=copyInput(i),tr={state:copyState(t.state),outputs:t.outputs.map(copyOutput),disposition:{...t.disposition}};if(globalInto)requireSynchronousCheckReturn(globalInto.call(wiring,before,input,tr,c));else if(globalLegacy)c.extend(globalLegacy.call(wiring,before,input,tr));});
 }
}

import {Metadata,CodecModel} from './core.js';
import {ValueSchema,customValueSchema,valueCodecs as vc,logicalLayout,bindModelCodec} from './value_codec.js';
export function networkSchemas<S,I,O>(state:ValueSchema<S>,input:ValueSchema<I>,output:ValueSchema<O>){
 const record=<T>(name:string,size:bigint,encode:ValueSchema<T>['encodeInto'],decode:ValueSchema<T>['decodeFrom'])=>customValueSchema<T>({name,layout:logicalLayout(size,'network-conservative'),encodeInto:encode,decodeFrom:decode});
 const weight=(c:ValueSchema<unknown>)=>{if(!c.layout)throw new ModelError('explicit payload allocation layout required');return c.layout.bytes+64n;};
 const address=<T>(c:ValueSchema<T>)=>record<NetworkAddress<T>>('NetworkAddress',weight(c),(a,b)=>{vc.u32.encodeRaw(a.machine,b);c.encodeRaw(a.value,b);},r=>({machine:r.value(vc.u32),value:r.value(c)}));
 const route=record<NetworkRoute<I>>('NetworkRoute',weight(input),(v,b)=>{vc.u32.encodeRaw(v.destination,b);vc.u32.encodeRaw(v.channel,b);input.encodeRaw(v.input,b);},r=>({destination:r.value(vc.u32),channel:r.value(vc.u32),input:r.value(input)}));
 const message=record<NetworkMessage<I>>('NetworkMessage',weight(route),(v,b)=>{vc.u32.encodeRaw(v.source,b);route.encodeRaw(v.route,b);},r=>({source:r.value(vc.u32),route:r.value(route)}));
 const machines=vc.vector(address(state)),messages=vc.vector(message),local=address(input);
 const s=record<NetworkState<S,I>>('NetworkState',64n,(s,b)=>{vc.u8.encodeRaw(1,b);machines.encodeRaw([...s.machines],b);messages.encodeRaw([...s.pending],b);},r=>{if(r.value(vc.u8)!==1)throw new ModelError('network state codec version');const ms=r.value(machines),pending=r.value(messages);if(ms.some((m,i)=>i>0&&ms[i-1].machine>=m.machine))throw new ModelError('noncanonical network machine order');const ids=new Set(ms.map(m=>m.machine));if(pending.some(m=>!ids.has(m.source)||!ids.has(m.route.destination)))throw new ModelError('unknown route machine');return {machines:ms,pending};});
 const i=record<NetworkInput<I>>('NetworkInput',weight(input),(i,b)=>{const tag={local:0,deliver:1,drop:2,duplicate:3}[i.kind];if(tag===undefined)throw new ModelError('network action tag');vc.u8.encodeRaw(tag,b);if(i.kind==='local')local.encodeRaw(i.address,b);else vc.u32.encodeRaw(i.index,b);},r=>{const tag=r.value(vc.u8);if(tag===0)return {kind:'local',address:r.value(local)};if(tag>3)throw new ModelError('network action tag');return {kind:tag===1?'deliver':tag===2?'drop':'duplicate',index:r.value(vc.u32)};});
 return {state:s,input:i,output:address(output)};
}
export function withNetworkCodec<S,I,O>(model:Network<S,I,O>,state:ValueSchema<S>,input:ValueSchema<I>,output:ValueSchema<O>){const schemas=networkSchemas(state,input,output),s=schemas.state,i=schemas.input;return bindModelCodec(model,{state:customValueSchema<NetworkState<S,I>>({name:s.schema(),layout:s.layout,encodeInto:(v,b)=>{model.validate(v);s.encodeRaw(v,b);},decodeFrom:r=>{const v=r.value(s);model.validate(v);return v;}}),input:customValueSchema<NetworkInput<I>>({name:i.schema(),layout:i.layout,encodeInto:(v,b)=>i.encodeRaw(v,b),decodeFrom:r=>{const v=r.value(i);if(v.kind==='local'&&!model.machineIDs().includes(v.address.machine))throw new ModelError('unknown local machine');if(v.kind==='drop'&&!model.policy.allowDrop||v.kind==='duplicate'&&!model.policy.allowDuplicate)throw new ModelError('network action disabled');return v;}}),output:schemas.output});}
