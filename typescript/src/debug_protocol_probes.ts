/** Explicit host-approved diagnostic profiles and sinks for the local protocol. */
import {DiagnosticHub,type Subscription,type SinkHealth} from './debug_diagnostic.js';
import {ProtocolError,type ProtocolLimits,type Response} from './debug_protocol.js';
import {nodeProtocolFields,protocolSegment,protocolVariant} from './debug_protocol_services.js';
export interface ProbeProfile {readonly id:bigint;readonly label:string;readonly subscription:Subscription}
const U64=(1n<<64n)-1n,n=(v:number|bigint)=>`u64:${v}`,b=(v:boolean)=>`bool:${v}`;
const number=(v:string)=>BigInt(v.slice(4));
const boundedOffset=(offset:bigint,total:number)=>offset>=BigInt(total)?total:Number(offset);
// Copy plain profile data under a logical byte/depth budget before invoking any
// serializer. Accessors, custom prototypes and cycles are not profile data.
function boundedProfiles(value:unknown,maximum:number):ProbeProfile[]{
 let remaining=maximum;const active=new WeakSet<object>();
 const charge=(amount:number)=>{if(!Number.isSafeInteger(amount)||amount>remaining)throw new ProtocolError('limit');remaining-=amount;};
 const copy=(item:unknown,depth:number):unknown=>{
  if(depth>16)throw new ProtocolError('limit');
  if(typeof item==='string'){charge(item.length*3+16);return item;}
  if(typeof item==='bigint'||typeof item==='number'||typeof item==='boolean'||item===null||item===undefined){charge(32);return item;}
  if(typeof item!=='object')throw new ProtocolError('invalid_input');
  const object=item as object;if(active.has(object))throw new ProtocolError('invalid_input');active.add(object);
  try{
   if(Array.isArray(item)){if(item.length>64)throw new ProtocolError('limit');charge(32+item.length*8);const out:unknown[]=[];for(let i=0;i<item.length;i++){const descriptor=Object.getOwnPropertyDescriptor(item,String(i));if(!descriptor||!('value' in descriptor))throw new ProtocolError('invalid_input');out.push(copy(descriptor.value,depth+1));}return out;}
   const proto=Object.getPrototypeOf(item);if(proto!==Object.prototype&&proto!==null)throw new ProtocolError('invalid_input');
   const out:Record<string,unknown>=Object.create(null);let count=0;
   for(const key in item as Record<string,unknown>){if(!Object.prototype.hasOwnProperty.call(item,key))continue;if(++count>64)throw new ProtocolError('limit');charge(key.length*3+32);const descriptor=Object.getOwnPropertyDescriptor(item,key);if(!descriptor||!('value' in descriptor))throw new ProtocolError('invalid_input');out[key]=copy(descriptor.value,depth+1);}
   return out;
  }finally{active.delete(object);}
 };
 const result=copy(value,0);if(!Array.isArray(result))throw new ProtocolError('invalid_input');return result as ProbeProfile[];
}
function boundedSinks(value:unknown):bigint[]{
 if(!Array.isArray(value))throw new ProtocolError('invalid_input');
 if(value.length>64)throw new ProtocolError('limit');
 const sinks:bigint[]=[],seen=new Set<bigint>();
 for(let i=0;i<value.length;i++){
  const descriptor=Object.getOwnPropertyDescriptor(value,String(i));
  if(!descriptor||!('value' in descriptor))throw new ProtocolError('invalid_input');
  const sink:unknown=descriptor.value;
  if(typeof sink!=='bigint'||sink<0n||sink>U64||seen.has(sink))throw new ProtocolError('invalid_input');
  seen.add(sink);sinks.push(sink);
 }
 return sinks;
}
export class ProbeAttachment {
 readonly #profiles:ProbeProfile[];readonly #sinks:bigint[];#selected?:{revision:bigint;ids:bigint[]};
 constructor(readonly hub:DiagnosticHub,profiles:readonly ProbeProfile[],sinks:readonly bigint[],readonly limits:ProtocolLimits){
  hub.assertAvailable();if(!Array.isArray(profiles))throw new ProtocolError('invalid_input');if(profiles.length>64)throw new ProtocolError('limit');
  const ownedSinks=boundedSinks(sinks);
  const copies=boundedProfiles(profiles,limits.maxCandidateBytes-ownedSinks.length*8);
  const seen=new Set<bigint>();for(const profile of copies){if(typeof profile.id!=='bigint'||profile.id<=0n||profile.id>U64||seen.has(profile.id)||typeof profile.label!=='string'||profile.label.length>128||new TextEncoder().encode(profile.label).length>Math.min(128,limits.maxField-7))throw new ProtocolError('invalid_input');seen.add(profile.id);}
  for(const sink of ownedSinks){try{hub.queued(sink);}catch{throw new ProtocolError('invalid_input');}}
  this.#profiles=copies;this.#sinks=ownedSinks;
 }
 execute(name:string,args:readonly string[],reserve:(fields:Response['fields'])=>unknown):Response['fields']{
  try{return this.#execute(name,args,reserve);}catch(e){if(e instanceof ProtocolError)throw e;const message=e instanceof Error?e.message:'';if(message.includes('owned')||message.includes('owns'))throw new ProtocolError('busy');if(message.includes('stale'))throw new ProtocolError('stale_configuration');if(message.includes('unauthorized'))throw new ProtocolError('unauthorized');if(message.includes('capacity')||message.includes('limit'))throw new ProtocolError('limit');if(message.includes('exhausted'))throw new ProtocolError('exhausted');throw new ProtocolError('invalid_input');}
 }
 #execute(name:string,args:readonly string[],reserve:(fields:Response['fields'])=>unknown):Response['fields']{
  const h=this.hub;h.assertAvailable();let fields:[string,string][]=[];const put=(key:string,value:string)=>fields.push([key,value]);
  if(name==='probe_configure'){
   const expected=number(args[0]),ids=args.slice(1).map(number);if(new Set(ids).size!==ids.length)throw new ProtocolError('invalid_input');
   const selected=ids.map(id=>{const p=this.#profiles.find(v=>v.id===id);if(!p)throw new ProtocolError('unauthorized');return p.subscription;});if(expected!==h.revision)throw new ProtocolError('stale_configuration');
   const producers=h.producerStatus().map(p=>p.producer).sort((a,b)=>a<b?-1:a>b?1:0);put('probes.capture_revision',n(U64));put('probes.selected_count',n(ids.length));put('probes.pending_producers.total',n(producers.length));put('probes.pending_producers.truncated',b(producers.length>this.limits.maxPageSize));reserve(fields);for(const [i,id] of producers.slice(0,this.limits.maxPageSize).entries()){put(`probes.pending_producers.${i}`,n(id));try{reserve(fields);}catch(error){if(!(error instanceof ProtocolError)||error.code!=='limit')throw error;fields.pop();fields[3][1]='bool:true';break;}}
   const ack=h.configure(expected,selected);fields[0][1]=n(ack.captureRevision);this.#selected={revision:ack.captureRevision,ids:[...ids]};return fields;
  }
  if(name==='probe_status'){
   const offset=number(args[0]),limit=Number(number(args[1]));const producers=h.producerStatus().sort((a,b)=>a.producer<b.producer?-1:a.producer>b.producer?1:0),pending=h.pendingProducers();const po=boundedOffset(offset,producers.length),qo=boundedOffset(offset,this.#profiles.length),so=boundedOffset(offset,this.#sinks.length);
   put('probes.capture_revision',n(h.revision));put('probes.producers.total',n(producers.length));put('probes.producers.truncated',b(offset+BigInt(limit)<BigInt(producers.length)));put('probes.pending_producers.total',n(pending.length));
   producers.slice(po,po+limit).forEach((p,i)=>{const k=`probes.producers.${i}`;put(k+'.id',n(p.producer));put(k+'.epoch',n(p.epoch));put(k+'.capture_revision',n(p.captureRevision));put(k+'.effective_transition',n(p.effectiveTransition));put(k+'.pending',b(pending.includes(p.producer)));});
   const total=Math.max(producers.length,this.#profiles.length,this.#sinks.length);put('probes.status_live','bool:true');put('probes.offset',n(offset));put('probes.next_offset',offset+BigInt(limit)<BigInt(total)?n(offset+BigInt(limit)):'none');put('probes.profiles.total',n(this.#profiles.length));put('probes.profiles.truncated',b(offset+BigInt(limit)<BigInt(this.#profiles.length)));
   this.#profiles.slice(qo,qo+limit).forEach((p,i)=>{put(`probes.profiles.${i}.id`,n(p.id));put(`probes.profiles.${i}.label`,`string:${p.label}`);});const known=this.#selected?.revision===h.revision;put('probes.selected_profiles_known',b(known));if(known){const ids=this.#selected!.ids;ids.slice(boundedOffset(offset,ids.length),boundedOffset(offset,ids.length)+limit).forEach((id,i)=>put(`probes.selected.${i}`,n(id)));}
   put('probes.sinks.total',n(this.#sinks.length));this.#sinks.slice(so,so+limit).forEach((id,i)=>{const k=`probes.sinks.${i}`,q=h.queued(id);put(k+'.id',n(id));put(k+'.queued_records',n(q.records));put(k+'.queued_bytes',n(q.bytes));this.#health(fields,k,h.health(id));});return fields;
  }
  const sink=number(args[0]),limit=Number(number(args[1])),payload=args[2]==='bool:true';if(!this.#sinks.includes(sink))throw new ProtocolError('unauthorized');const health=h.health(sink);put('probes.sink',n(sink));put('probes.capture_revision',n(h.revision));put('probes.sink.health.dropped',n(health.dropped));put('probes.sink.health.exporter_failed',n(health.exporterFailed));
  const events=h.peekMany(sink,limit);events.forEach((e,i)=>{const k=`probes.events.${i}`;for(const [key,value] of Object.entries({schema_version:1,run:e.runId,producer:e.producer,epoch:e.epoch,producer_sequence:e.producerSequence,capture_revision:e.captureRevision,transition:e.transition}))put(`${k}.${key}`,n(value));for(const key of ['origin','severity','kind'] as const)put(`${k}.${key}`,`enum:${protocolVariant(e[key])}`);put(k+'.baseline',b(e.baseline));put(k+'.baseline_reason',e.baselineReason===null?'none':`enum:${protocolVariant(e.baselineReason)}`);put(k+'.change_unknown',b(e.changeUnknown));put(k+'.incomplete_turn',b(e.incompleteTurn));if(payload){put(k+'.site',`string:${e.site}`);e.path.forEach((p,j)=>put(`${k}.path.${j}`,protocolSegment(p)));if(e.displaySchema){put(k+'.display_schema',`string:${e.displaySchema[0]}`);put(k+'.display_schema_version',n(e.displaySchema[1]));}if(e.payload)fields.push(...nodeProtocolFields(k+'.payload',e.payload));else put(k+'.payload','none');}else{put(k+'.metadata_omitted','bool:true');put(k+'.payload','omitted:by_request');}});
  put('probes.events.returned',n(events.length));reserve(fields);for(let i=0;i<events.length;i++)h.pop(sink);return fields;
 }
 #health(fields:[string,string][],prefix:string,h:SinkHealth){for(const [wire,key] of [['captured','captured'],['filtered','filtered'],['sampled','sampled'],['dropped','dropped'],['truncated','truncated'],['exporter_failed','exporterFailed'],['abandoned','abandoned'],['inspection_failed','inspectionFailed'],['baseline_evicted','baselineEvicted'],['observation_gaps','observationGaps']] as const)fields.push([`${prefix}.health.${wire}`,n(h[key])]);}
}
