import {WatchRegistry,type WatchConfig} from './debug_watches.js';
import {ProbeAttachment,type ProbeProfile} from './debug_protocol_probes.js';
import type {DiagnosticHub} from './debug_diagnostic.js';
import {DEFAULT_INSPECT_LIMITS,inspectForProtocol,type Inspect} from './debug_inspect.js';
import {decodeProtocolPath,nodeProtocolFields,protocolVariant,protocolInspectionError} from './debug_protocol_services.js';
/** Optional local transport. No clocks, filesystem, sockets or effects are loaded. */
import type {DebugSession} from './debug_session.js';
const U64=(1n<<64n)-1n;
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true});
export class ProtocolError extends Error {constructor(readonly code:string){super(code);}}
export interface ProtocolLimits {maxFrame:number;maxField:number;maxRequestFields:number;maxResponseFields:number;maxInput:number;maxCacheEntries:number;maxCacheBytes:number;maxHandles:number;maxPageSize:number;maxCandidates:number;maxCandidateBytes:number;maxTelemetrySnapshotBytes:number;maxEventEntries:number;maxEventBytes:number}
export const DEFAULT_PROTOCOL_LIMITS:Readonly<ProtocolLimits>=Object.freeze({maxFrame:65536,maxField:8192,maxRequestFields:64,maxResponseFields:4096,maxInput:8192,maxCacheEntries:64,maxCacheBytes:4194304,maxHandles:64,maxPageSize:100,maxCandidates:256,maxCandidateBytes:262144,maxTelemetrySnapshotBytes:8388608,maxEventEntries:1024,maxEventBytes:262144});
export interface ProtocolAuthority {deliverInputs:boolean;configureWatches:boolean;configureDiagnostics:boolean;readExactValues:boolean;exportExactTrace:boolean}
export type HostCommandName='export_trace'|'inspect'|'inputs'|'select'|'events'|'watch'|'unwatch'|'watch_status'|'watch_current'|'metric_catalog'|'metric_snapshot'|'metric_window'|'effect_details'|'effect_timeline'|'telemetry_health'|'probe_status'|'probe_configure'|'probe_events';
export type Command={kind:'status'|'snapshot'|'cancel'}|{kind:'step';bytes:Uint8Array}|{kind:'checks'|'outputs';handle:bigint}|{kind:'host';name:HostCommandName;arguments:readonly string[]}|{kind:'unsupported';name:string;arguments:readonly string[]};
/** Adapters are explicit host code. Prepare must be passive; commit is invoked only
 * after the acknowledgement fits the wire budget. Callbacks remain cooperative. */
export interface PreparedHostCommand {readonly fields:Response['fields'];readonly commit?:()=>void}
export interface HostCommandContext<S,I,O> {readonly session:DebugSession<S,I,O>;readonly request:Request;readonly configuration:bigint;readonly authority:Readonly<ProtocolAuthority>;readonly limits:Readonly<ProtocolLimits>;readonly requireHandle:(handle:bigint)=>void}
export type HostProtocolBindings<S,I,O>=Partial<Record<HostCommandName,(arguments_:readonly string[],context:HostCommandContext<S,I,O>)=>PreparedHostCommand>>;
export interface Request {version:number;session:string;epoch:bigint;requestId:bigint;revision:bigint;configuration:bigint;command:Command}
export interface Response {version:number;session:string;epoch:bigint;requestId:bigint;revision:bigint;configuration:bigint;error:string|null;fields:readonly (readonly [string,string])[]}
function fail(code:string):never{throw new ProtocolError(code);}
function inspectSchemaField(name:string,maximum:number):string{
 if(typeof name!=='string')fail('invalid_input');let bytes=7;
 for(let i=0;i<name.length;i++){const c=name.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){const next=name.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))fail('invalid_input');bytes+=4;}else if(c>=0xdc00&&c<=0xdfff)fail('invalid_input');else bytes+=c<0x80?1:c<0x800?2:3;if(bytes>maximum)fail('limit');}
 return `string:${name}`;
}
function u64(v:bigint):string{if(typeof v!=='bigint'||v<0n||v>U64)fail('malformed');return `u64:${v}`;}
function version(v:string):number{if(!/^u32:\+?[0-9]+$/.test(v))fail('malformed');const n=BigInt(v.slice(4));if(n>0xffffffffn)fail('malformed');return Number(n);}
function versionField(v:number):string{if(!Number.isInteger(v)||v<0||v>0xffffffff)fail('malformed');return `u32:${v}`;}
function integer(v:string):bigint{if(!/^u64:[0-9]+$/.test(v))fail('malformed');const n=BigInt(v.slice(4));if(n>U64)fail('malformed');return n;}
export function escapeField(value:string):string{const encoded=encoder.encode(value);if(decoder.decode(encoded)!==value)fail('malformed');return Array.from(encoded,b=>b>=65&&b<=90||b>=97&&b<=122||b>=48&&b<=57||[45,95,46,58].includes(b)?String.fromCharCode(b):`%${b.toString(16).toUpperCase().padStart(2,'0')}`).join('');}
function unescape(value:string,max:number):string{if(encoder.encode(value).length>max*3)fail('limit');const bytes:number[]=[];for(let i=0;i<value.length;i++){const c=value[i];if(c==='%'){const h=value.slice(i+1,i+3);if(!/^[0-9a-fA-F]{2}$/.test(h))fail('malformed');bytes.push(parseInt(h,16));i+=2;}else{if(!/^[A-Za-z0-9_.:-]$/.test(c))fail('malformed');bytes.push(c.charCodeAt(0));}if(bytes.length>max)fail('limit');}try{return decoder.decode(new Uint8Array(bytes));}catch{return fail('malformed');}}
// Count without first allocating encoded or escaped copies of caller strings.
// The first pass mirrors TextEncoder's replacement of unpaired surrogates;
// the escape pass then rejects those surrogates, as escapeField does.
function fieldSize(s:string,escaped:boolean):number{let size=0;for(let i=0;i<s.length;i++){const c=s.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){const next=s.charCodeAt(i+1);if(next>=0xdc00&&next<=0xdfff){i++;size+=escaped?12:4;}else{if(escaped)fail('malformed');size+=3;}}else if(c>=0xdc00&&c<=0xdfff){if(escaped)fail('malformed');size+=3;}else if(c<128){size+=escaped&&!(c>=65&&c<=90||c>=97&&c<=122||c>=48&&c<=57||c===45||c===95||c===46||c===58)?3:1;}else size+=(c<2048?2:3)*(escaped?3:1);}return size;}
function pack(fields:readonly string[],limits:ProtocolLimits,max:number):Uint8Array{if(fields.length>max)fail('limit');for(const s of fields)if(fieldSize(s,false)>limits.maxField)fail('limit');let size=Math.max(0,fields.length-1);for(const s of fields)size+=fieldSize(s,true);if(!size||size>limits.maxFrame)fail('limit');return encoder.encode(fields.map(escapeField).join('\t'));}
function unpack(bytes:Uint8Array,limits:ProtocolLimits,max:number):string[]{if(!bytes.length||bytes.length>limits.maxFrame)fail('limit');let raw:string;try{raw=decoder.decode(bytes);}catch{return fail('malformed');}const fields:string[]=[];for(const part of raw.split('\t')){if(fields.length===max)fail('limit');fields.push(unescape(part,limits.maxField));}return fields;}
const hostSpecs:Record<HostCommandName,readonly [number,number]>={export_trace:[3,3],inspect:[4,Infinity],inputs:[2,2],select:[1,1],events:[1,1],watch:[2,Infinity],unwatch:[1,1],watch_status:[0,0],watch_current:[1,1],metric_catalog:[2,2],metric_snapshot:[3,3],metric_window:[3,3],effect_details:[3,3],effect_timeline:[4,4],telemetry_health:[0,0],probe_status:[2,2],probe_configure:[1,Infinity],probe_events:[3,3]};
function pathSegment(value:string):string{
 if(/^(field|variant|key\.string):/.test(value))return value;
 if(/^index:\+?[0-9]+$/.test(value)){const n=BigInt(value.slice(6));if(n<=U64)return `index:${n}`;}
 if(/^key\.bool:(true|false)$/.test(value))return value;
 if(value.startsWith('key.char:')&&[...value.slice(9)].length===1){escapeField(value);return value;}
 const m=/^key\.(u8|u16|u32|u64|u128|usize|i8|i16|i32|i64|i128|isize):([+-]?[0-9]+)$/.exec(value);
 if(m){
  const n=BigInt(m[2]),signed=m[1][0]==='i',bits=m[1].endsWith('size')?64n:BigInt(m[1].slice(1));
  const lo=signed?-(1n<<(bits-1n)):0n,hi=signed?(1n<<(bits-1n))-1n:(1n<<bits)-1n;
  if((signed||!m[2].startsWith('-'))&&n>=lo&&n<=hi)return `key.${m[1]}:${n}`;
 }
 return fail('malformed');
}
function hostArguments(name:HostCommandName,a:readonly string[]):string[]{
 const [min,max]=hostSpecs[name];if(a.length<min||a.length>max)fail('malformed');
 const result:string[]=[],pathStart=name==='inspect'?4:name==='watch'?2:Infinity;
 for(let i=0;i<a.length;i++){
  if(i>=pathStart){if(a.length-pathStart>32)fail('limit');result.push(pathSegment(a[i]));continue;}
  if(name==='metric_snapshot'&&i===0&&a[i]==='none'){result.push('none');continue;}
  if(name==='probe_events'&&i===2){if(!/^bool:(true|false)$/.test(a[i]))fail('malformed');result.push(a[i]);continue;}
  const n=integer(a[i]);if((name==='inspect'&&i===1||name==='watch'&&i===0)&&n>0xffffffffn)fail('malformed');result.push(u64(n));
 }
 return result;
}
function args(c:Command,argumentCount=0):string[]{switch(c.kind){case 'step':{const parts:string[]=[];for(let i=0;i<argumentCount;i++)parts.push(c.bytes[i].toString(16).padStart(2,'0'));return ['step',`bytes:${parts.join('')}`];}case 'checks':case 'outputs':return [c.kind,u64(c.handle)];case 'host':case 'unsupported':{const captured:string[]=[];for(let i=0;i<argumentCount;i++)captured.push(c.arguments[i]);return c.kind==='host'?[c.name,...hostArguments(c.name,captured)]:[c.name,...captured];}default:return [c.kind];}}
export function encodeRequest(r:Request,l:ProtocolLimits=DEFAULT_PROTOCOL_LIMITS):Uint8Array{const prefix=['DDBG',versionField(r.version),'request',`string:${r.session}`,u64(r.epoch),u64(r.requestId),u64(r.revision),u64(r.configuration)];const command=r.command,argumentCount=command.kind==='host'||command.kind==='unsupported'?command.arguments.length:command.kind==='step'?command.bytes.length:0,count=command.kind==='host'||command.kind==='unsupported'?9+argumentCount:command.kind==='step'||command.kind==='checks'||command.kind==='outputs'?10:9;if(count>l.maxRequestFields||count-1>l.maxFrame)fail('limit');if(command.kind==='step'&&argumentCount>Math.floor((Math.min(l.maxField,l.maxFrame)-6)/2))fail('limit');return pack([...prefix,...args(command,argumentCount)],l,l.maxRequestFields);}
export function decodeRequest(b:Uint8Array,l:ProtocolLimits=DEFAULT_PROTOCOL_LIMITS):Request{
 const f=unpack(b,l,l.maxRequestFields);
 if(f.length<9||f[0]!=='DDBG'||f[2]!=='request')fail('malformed');
 const v=version(f[1]);if(v!==1)fail('unsupported_version');
 // Command parsing precedes envelope admission. A malformed or unknown command
 // has no admitted request ID and must not retire an otherwise valid ID.
 let command:Command;const name=f[8],a=f.slice(9);
 if(['status','snapshot','cancel'].includes(name)){
  if(a.length)fail('malformed');command={kind:name as 'status'|'snapshot'|'cancel'};
 }else if(name==='step'){
  if(a.length!==1||!a[0].startsWith('bytes:')||(a[0].length-6)%2!==0)fail('malformed');
  if((a[0].length-6)/2>l.maxInput)fail('limit');
  if(!/^bytes:([0-9a-fA-F]{2})*$/.test(a[0]))fail('malformed');
  command={kind:'step',bytes:Uint8Array.from(a[0].slice(6).match(/../g)??[],h=>parseInt(h,16))};
 }else if(name==='checks'||name==='outputs'){
  if(a.length!==1)fail('malformed');command={kind:name,handle:integer(a[0])};
 }else if(Object.hasOwn(hostSpecs,name)){
  command={kind:'host',name:name as HostCommandName,arguments:hostArguments(name as HostCommandName,a)};
 }else fail('unsupported');
 if(!f[3].startsWith('string:'))fail('malformed');
 return {version:v,session:f[3].slice(7),epoch:integer(f[4]),requestId:integer(f[5]),revision:integer(f[6]),configuration:integer(f[7]),command};
}
export function encodeResponse(r:Response,l:ProtocolLimits=DEFAULT_PROTOCOL_LIMITS):Uint8Array{const count=r.fields.length;if(count>l.maxResponseFields||10+2*count>l.maxFrame)fail('limit');const fields=['DDBG',versionField(r.version),'response',`string:${r.session}`,u64(r.epoch),u64(r.requestId),u64(r.revision),u64(r.configuration),r.error===null?'ok':'error',r.error??'none',u64(BigInt(count))];for(let i=0;i<count;i++){const pair=r.fields[i];fields.push(pair[0],pair[1]);}return pack(fields,l,11+2*l.maxResponseFields);}
export function decodeResponse(b:Uint8Array,l:ProtocolLimits=DEFAULT_PROTOCOL_LIMITS):Response{const f=unpack(b,l,11+2*l.maxResponseFields);if(f.length<11||f[0]!=='DDBG'||!/^u32:[0-9]+$/.test(f[1])||f[2]!=='response'||!f[3].startsWith('string:')||!['ok','error'].includes(f[8])||(f[8]==='ok'&&f[9]!=='none'))fail('malformed');const count=integer(f[10]);if(count>BigInt(l.maxResponseFields)||BigInt(f.length)!==11n+count*2n)fail('malformed');const fields:[string,string][]=[];for(let i=11;i<f.length;i+=2)fields.push([f[i],f[i+1]]);return {version:version(f[1]),session:f[3].slice(7),epoch:integer(f[4]),requestId:integer(f[5]),revision:integer(f[6]),configuration:integer(f[7]),error:f[8]==='ok'?null:f[9],fields};}
export function writeFrame(payload:Uint8Array,maximum=65536):Uint8Array{if(!payload.length||payload.length>maximum||payload.length>0xffffffff)fail('limit');const frame=new Uint8Array(4+payload.length);new DataView(frame.buffer).setUint32(0,payload.length);frame.set(payload,4);return frame;}
/** Incremental bounded parser rejects length before allocating the payload. */
export class FrameReader {#header=new Uint8Array(4);#headerUsed=0;#payload:Uint8Array|null=null;#used=0;constructor(readonly maximum=65536){if(!Number.isSafeInteger(maximum)||maximum<1||maximum>0xffffffff)fail('limit');}*feed(chunk:Uint8Array):Generator<Uint8Array>{for(const b of chunk){if(!this.#payload){this.#header[this.#headerUsed++]=b;if(this.#headerUsed===4){const n=new DataView(this.#header.buffer).getUint32(0);if(!n||n>this.maximum)fail('limit');this.#payload=new Uint8Array(n);this.#used=0;}}else{this.#payload[this.#used++]=b;if(this.#used===this.#payload.length){const p=this.#payload;this.#payload=null;this.#headerUsed=0;yield p;}}}}finish():void{if(this.#headerUsed||this.#payload)fail('malformed');}}
let epoch=0n;
export class ProtocolSession<S,I,O>{readonly limits:ProtocolLimits;readonly authority:Readonly<ProtocolAuthority>;readonly #bindings:Readonly<HostProtocolBindings<S,I,O>>;#configuration=0n;#epoch:bigint;#connected=true;#busy=false;#high=0n;#nextHandle=1n;#handles=new Map<bigint,bigint>();#cache=new Map<bigint,{key:string;bytes:Uint8Array;cost:number}>();#cacheBytes=0;
 #watches?:{registry:WatchRegistry;project:(state:S)=>Inspect};
 #inspector?: (state:S)=>Inspect;
 #probes?:ProbeAttachment;
 attachProbes(hub:DiagnosticHub,profiles:readonly ProbeProfile[],sinks:readonly bigint[]):void{if(this.#busy)fail('busy');if(this.#probes)fail('invalid_input');this.#probes=new ProbeAttachment(hub,profiles,sinks,this.limits);}
 enableWatches(registry:WatchRegistry,project:(state:S)=>Inspect):void{if(this.#busy)fail('busy');if(this.#watches)fail('invalid_input');this.#watches={registry,project};this.#configuration=registry.captureRevision;try{this.#observeWatches();}catch{registry.observationGap();}}
 /** Attach an explicit display-only projection; no state codec or reflection is used. */
 enableInspection(project:(state:S)=>Inspect):void{if(this.#busy)fail('busy');if(typeof project!=='function'||this.#inspector||this.#bindings.inspect)fail('invalid_input');this.#inspector=project;}
 #observeWatches():void{const w=this.#watches;if(w)this.session.withReadOwnership(()=>w.registry.observe(()=>this.session.projectState(w.project),{snapshot:{session:this.session.epoch,revision:this.session.revision,sequence:this.session.sequence},origin:this.session.inputPolicy==='out-of-domain-injection'?'out_of_domain_injection':'simulation',propertyFailure:this.session.stopReason?.kind==='property_failure'}));}

 #candidates=new Map<bigint,{revision:bigint;bytes:Uint8Array;cost:number}>();#candidateBytes=0;#nextCandidate=0n;
 #eventSequence=0n;#eventDrops=0n;#eventBytes=0;#events:{sequence:bigint;revision:bigint;configuration:bigint;kind:string;cost:number}[]=[];
 constructor(readonly session:DebugSession<S,I,O>,readonly decodeInput:(bytes:Uint8Array)=>I,authority:Partial<ProtocolAuthority>={},limits:Partial<ProtocolLimits>={},bindings:HostProtocolBindings<S,I,O>={}){
  this.limits=Object.freeze({...DEFAULT_PROTOCOL_LIMITS,...limits});
  for(const n of Object.values(this.limits))if(!Number.isSafeInteger(n)||n<1)fail('limit');
  const idBytes=encoder.encode(session.id).length;escapeField(session.id);escapeField(session.inputPolicy);
  if(this.limits.maxFrame<2048||this.limits.maxFrame>0xffffffff||this.limits.maxField<64||this.limits.maxRequestFields<12||this.limits.maxResponseFields<32||this.limits.maxCacheBytes<this.limits.maxFrame*2||this.limits.maxEventBytes<512||idBytes>128||idBytes+7>this.limits.maxField||encoder.encode(session.inputPolicy).length+7>this.limits.maxField)fail('limit');
  this.authority=Object.freeze({deliverInputs:false,configureWatches:false,configureDiagnostics:false,readExactValues:false,exportExactTrace:false,...authority});
  this.#bindings=Object.freeze({...bindings});this.#epoch=this.#allocateEpoch();
 }
 #allocateEpoch(){if(epoch===U64)fail('exhausted');return ++epoch;}get epoch(){return this.#epoch;}disconnect(){if(this.#busy)fail('busy');this.#connected=false;}reconnect(){if(this.#busy)fail('busy');if(this.#connected)fail('invalid_input');this.#epoch=this.#allocateEpoch();this.#connected=true;this.#cache.clear();this.#cacheBytes=0;this.#high=0n;this.#handles.clear();this.#candidates.clear();this.#candidateBytes=0;this.#events=[];this.#eventBytes=0;this.#eventSequence=0n;this.#publish('reconnected');return this.#epoch;}
 get configuration(){return this.#configuration;}
 #response(id:bigint,error:string|null,fields:Response['fields']=[]):Response{return {version:1,session:this.session.id,epoch:this.#epoch,requestId:id,revision:this.session.revision,configuration:this.#configuration,error,fields};}
 handle(request:Request):Response{const failure=(code:string)=>this.#response(request.requestId,code);if(this.#busy)return failure('busy');if(request.version!==1)return failure('unsupported_version');if(request.session!==this.session.id)return failure('wrong_session');const r={...request,epoch:request.epoch===0n&&request.command.kind==='status'?this.#epoch:request.epoch};if(r.epoch!==this.#epoch)return failure('wrong_epoch');if(!this.#connected)return failure('disconnected');if(r.requestId===0n)return failure('malformed');let key:string;try{key=decoder.decode(encodeRequest(r,this.limits));}catch(e){return failure(e instanceof ProtocolError?e.code:'malformed');}const old=this.#cache.get(r.requestId);if(old)return old.key===key?decodeResponse(old.bytes,this.limits):failure('duplicate_conflict');if(r.requestId<=this.#high)return failure('retired_request');this.#high=r.requestId;this.#busy=true;let response:Response;try{response=this.#response(r.requestId,null,this.#execute(r));}catch(e){response=failure(e instanceof ProtocolError?e.code:'callback');}finally{this.#busy=false;}let bytes:Uint8Array;try{bytes=encodeResponse(response,this.limits);}catch{response=failure('limit');bytes=encodeResponse(response,this.limits);}const cost=encoder.encode(key).length+bytes.length+128+response.fields.length*32;while(this.#cache.size&&(this.#cache.size>=this.limits.maxCacheEntries||this.#cacheBytes+cost>this.limits.maxCacheBytes)){const id=this.#cache.keys().next().value!;this.#cacheBytes-=this.#cache.get(id)!.cost;this.#cache.delete(id);}if(cost<=this.limits.maxCacheBytes){this.#cache.set(r.requestId,{key,bytes,cost});this.#cacheBytes+=cost;}return decodeResponse(bytes,this.limits);}
 #execute(r:Request):Response['fields']{const s=this.session,c=r.command;if(c.kind==='status')return [
  ['mode','enum:simulation'],['phase',`enum:${s.phase[0].toUpperCase()+s.phase.slice(1)}`],['sequence',u64(s.sequence)],['environment',`string:${s.inputPolicy}`],
  ['capability.input_delivery',`bool:${this.authority.deliverInputs}`],['capability.inspection',`bool:${this.#bindings.inspect!==undefined||this.#inspector!==undefined}`],['capability.watch_configuration',`bool:${this.authority.configureWatches&&(this.#watches!==undefined||this.#bindings.watch!==undefined)}`],
  ['capability.exact_values',`bool:${this.authority.readExactValues}`],['capability.live_control','bool:false'],['capability.live_restore','bool:false'],['capability.effects','bool:false'],
  ['capability.probe_queries',`bool:${this.#probes!==undefined||this.#bindings.probe_status!==undefined}`],['capability.probe_configuration',`bool:${this.authority.configureDiagnostics&&(this.#probes!==undefined||this.#bindings.probe_configure!==undefined)}`],
  ['capability.trace_export',`bool:${this.authority.exportExactTrace&&s.capabilities.exactRecording}`],['capability.effect_telemetry',`bool:${this.#bindings.telemetry_health!==undefined}`],['capability.metric_queries',`bool:${this.#bindings.metric_snapshot!==undefined}`],
  ['limits.telemetry_snapshot_bytes',u64(BigInt(this.limits.maxTelemetrySnapshotBytes))],['limits.frame_bytes',u64(BigInt(this.limits.maxFrame))],['limits.dedup_entries',u64(BigInt(this.limits.maxCacheEntries))],['limits.dedup_bytes',u64(BigInt(this.limits.maxCacheBytes))],['limits.page_size',u64(BigInt(this.limits.maxPageSize))],
  ['events.dropped',u64(this.#eventDrops)],['stop',`enum:${s.stopReason?.kind??'none'}`]];
 if(c.kind==='unsupported')fail('unsupported');if(c.kind==='host')return this.#host(r,c);
 if(c.kind==='snapshot'){if(r.revision!==s.revision)fail('stale_revision');if(this.#nextHandle>U64)fail('exhausted');const id=this.#nextHandle++;while(this.#handles.size>=this.limits.maxHandles)this.#handles.delete(this.#handles.keys().next().value!);this.#handles.set(id,s.revision);return [['handle',u64(id)],['snapshot.revision',u64(s.revision)],['snapshot.sequence',u64(s.sequence)]];}
 if(c.kind==='checks'||c.kind==='outputs'){
  this.#requireHandle(c.handle);
  const t=s.observation();
  if(c.kind==='outputs'){
   if(!t)return [['outputs.total','u64:0']];
   const fields:[string,string][]=[['outputs.total',u64(BigInt(t.transition.outputs.length))],['disposition',`enum:${t.transition.disposition.kind}`],['outputs.provenance','enum:simulated'],['outputs.payloads',this.authority.readExactValues?'enum:exact_sensitive':'enum:unavailable']];
   if(this.authority.readExactValues){const count=Math.min(t.transition.outputs.length,this.limits.maxPageSize);for(let i=0;i<count;i++){const bytes=s.encodeObservedOutput(i,this.limits.maxInput);fields.push([`outputs.${i}.encoded`,`bytes:${Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')}`]);}fields.push(['outputs.truncated',`bool:${count<t.transition.outputs.length}`]);}
   return fields;
  }
  const checks=t?.checks??s.initialChecks,first=checks.findIndex(c=>c.status==='failed');
  const failure:[string,string][]=first<0?[['checks.first_failure','none']]:[
   ['checks.first_failure.id',`string:${checks[first].id}`],
   ['checks.first_failure.index',u64(BigInt(first))],
   ['checks.first_failure.sequence',u64(s.sequence)],
   ['checks.first_failure.phase',`enum:${t?(first<t.stateCheckCount?'post_state':'transition'):'initial_state'}`],
  ];
  const count=Math.min(checks.length,this.limits.maxPageSize);
  return [['checks.complete',`bool:${s.checksComplete}`],['checks.total',u64(BigInt(checks.length))],...failure,
   ['checks.returned',u64(BigInt(count))],['checks.truncated',`bool:${count<checks.length}`],
   ...checks.slice(0,count).flatMap((v,i):[string,string][]=>[[`checks.${i}.id`,`string:${v.id}`],[`checks.${i}.status`,`enum:${v.status}`]])];
 }
 if(!this.authority.deliverInputs)fail('unauthorized');if(r.revision!==s.revision)fail('stale_revision');if(c.kind==='cancel'){if(s.phase==='ended'||s.phase==='disconnected')fail('ended');s.cancel(r.revision);this.#publish('cancelled');return [];}
 if(c.kind!=='step')fail('unsupported');
 if(c.bytes.length>this.limits.maxInput)fail('limit');let input:I;try{input=s.decodeInputWith(c.bytes,this.decodeInput);}catch{fail('invalid_input');}if(s.phase==='ended'||s.phase==='disconnected')fail('ended');let result;try{result=s.step(r.revision,input);}catch(e){if(e instanceof Error&&e.message.includes('not permitted'))fail('invalid_input');throw e;}if(result.delivered)this.#publish('transition');return [['delivered',`bool:${result.delivered}`],['sequence',u64(result.sequence)]];
 }
 #host(r:Request,c:Extract<Command,{kind:'host'}>):Response['fields']{
  const name=c.name,a=hostArguments(name,c.arguments);
  if(name==='select'&&!this.authority.deliverInputs||name==='export_trace'&&!this.authority.exportExactTrace||(name==='watch'||name==='unwatch')&&!this.authority.configureWatches||name==='probe_configure'&&!this.authority.configureDiagnostics)fail('unauthorized');
  if(name==='export_trace'||name==='inspect')this.#requireHandle(integer(a[0]));
  const watch=name==='watch'||name==='unwatch';if(watch&&r.configuration!==this.#configuration)fail('stale_configuration');
  if(['export_trace','inspect','inputs','select','watch'].includes(name)&&r.revision!==this.session.revision)fail('stale_revision');
  if(name==='watch'&&integer(a[1])!==this.session.revision)fail('stale_revision');
  if(watch&&this.#configuration===U64)fail('exhausted');
  const pageLimitIndex:Partial<Record<HostCommandName,number>>={inspect:3,inputs:1,events:0,metric_catalog:1,metric_snapshot:2,metric_window:2,effect_timeline:3,probe_status:1,probe_events:1};
  const pageOffsetIndex:Partial<Record<HostCommandName,number>>={inspect:2,inputs:0,metric_catalog:0,metric_snapshot:1,probe_status:0};
  const limitIndex=pageLimitIndex[name];if(limitIndex!==undefined){const count=integer(a[limitIndex]),offsetIndex=pageOffsetIndex[name],offset=offsetIndex===undefined?0n:integer(a[offsetIndex]);if(count===0n||count>BigInt(this.limits.maxPageSize)||offset+count>U64)fail('limit');}
  if(name==='probe_configure'&&a.length>65)fail('limit');
  if(name==='export_trace'){const offset=integer(a[1]),limit=integer(a[2]);if(limit===0n||limit>BigInt(Math.floor((this.limits.maxField-6)/2))||offset+limit>U64)fail('limit');}
  if(name==='metric_snapshot'&&a[0]==='none'&&integer(a[1])!==0n)fail('invalid_input');
  if(name==='inspect'&&this.#inspector){
   const offset=integer(a[2]),limit=integer(a[3]);if(offset>BigInt(Number.MAX_SAFE_INTEGER))fail('limit');
   const snapshot={session:this.session.epoch,revision:this.session.revision,sequence:this.session.sequence},path=decodeProtocolPath(a.slice(4)),project=this.#inspector;
   const result=this.session.projectState(state=>{const value=project(state);try{return inspectForProtocol(value,{snapshot,path,schemaVersion:Number(integer(a[1])),page:{offset:Number(offset),limit:Number(limit)},limits:{maxWork:10000,maxDepth:16,maxNodes:128,maxBytes:Math.floor(this.limits.maxFrame/8),maxValueBytes:Math.floor(this.limits.maxField/4),maxPageSize:this.limits.maxPageSize}});}catch{fail('invalid_input');}});
   return [['schema.name',inspectSchemaField(result.schema.name,this.limits.maxField)],['schema.version',u64(BigInt(result.schema.version))],['snapshot.revision',u64(snapshot.revision)],['snapshot.sequence',u64(snapshot.sequence)],...nodeProtocolFields('node',result.node)];
  }
  if(this.#watches&&['watch','unwatch','watch_status','watch_current'].includes(name))return this.session.withReadOwnership(()=>{try{return this.#watch(r,name,a);}catch(e){if(e instanceof ProtocolError)throw e;const message=e instanceof Error?e.message:'';if(/unauthorized|redacted/.test(message))fail('unauthorized');if(/unknown watch/.test(message))fail('stale_handle');if(/capacity|budget/.test(message))fail('limit');if(/exhausted/.test(message))fail('exhausted');if(/owned/.test(message))fail('busy');fail('invalid_input');}});
  if(this.#probes&&['probe_status','probe_configure','probe_events'].includes(name))return this.#probes.execute(name,a,fields=>encodeResponse({...this.#response(r.requestId,null,fields),revision:U64,configuration:U64},this.limits));
  const binding=this.#bindings[name];if(name==='events'&&!binding)return this.#pollEvents(r,Number(integer(a[0])));if(!binding&&['inputs','select','export_trace'].includes(name))return this.#concrete(r,name,a);if(!binding)fail('unsupported');
  const prepared=binding(Object.freeze([...a]),Object.freeze({session:this.session,request:r,configuration:this.#configuration,authority:this.authority,limits:this.limits,requireHandle:(id:bigint)=>this.#requireHandle(id)}));
  // Validate the entire acknowledgement before any declared commit. Host prepare
  // must not mutate; arbitrary callback side effects cannot be rolled back.
  const fields=prepared.fields.map(([k,v])=>[k,v] as const),response=this.#response(r.requestId,null,fields);
  if(watch)response.configuration=this.#configuration+1n;
  // A host commit may advance the session revision; reserve its full u64 width.
  encodeResponse({...response,revision:U64,configuration:U64},this.limits);
  if(prepared.commit){const result:unknown=prepared.commit();if(result!==undefined){if(result instanceof Promise)void result.catch(()=>{});fail('callback');}}
  if(watch)this.#configuration++;
  return fields;
 }
 #watch(r:Request,name:string,a:readonly string[]):Response['fields']{
  const w=this.#watches!,registry=w.registry,s=this.session,n=(v:number|bigint)=>u64(BigInt(v));let fields:[string,string][]=[];
  const reserve=()=>encodeResponse({...this.#response(r.requestId,null,fields),revision:U64,configuration:U64},this.limits);
  if(name==='watch'){
   const config:WatchConfig={selector:{path:decodeProtocolPath(a.slice(2)),schemaVersion:Number(integer(a[0]))},trigger:{kind:'changed'},enabled:true,evaluationInterval:1n,deliveryInterval:1n,limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:Math.min(registry.limits.maxEventBytes,Math.floor(this.limits.maxFrame/8)),maxValueBytes:Math.floor(this.limits.maxField/4),maxNodes:128,maxDepth:16,maxPageSize:this.limits.maxPageSize},page:{offset:0,limit:this.limits.maxPageSize}};
   fields=[['watch.id',n(U64)],['watch.generation',n(U64)],['watch.effective_sequence',n(U64)],['watch.installed_at_revision',n(s.revision)],['watch.baseline','enum:pending_at_effective_boundary'],['watch.metadata_complete','bool:false']];reserve();
   const ack=s.projectState(state=>registry.addValidated(config,w.project(state),{snapshot:{session:s.epoch,revision:s.revision,sequence:s.sequence},origin:'simulation'}));this.#configuration=ack.captureRevision;
   fields[0]=['watch.id',n(ack.watchId)];fields[1]=['watch.generation',n(ack.generation)];fields[2]=['watch.effective_sequence',n(ack.effectiveSequence)];if(ack.displaySchema&&ack.resolvedType){const base=fields;fields=[...fields,['watch.display_schema_version',n(ack.displaySchema.version)],['watch.display_schema',`string:${ack.displaySchema.name}`],['watch.resolved_type',`enum:${ack.resolvedType}`]];try{reserve();fields[5]=['watch.metadata_complete','bool:true'];}catch{fields=base;}}this.#publish('watch_configured');return fields;
  }
  if(name==='unwatch'){fields=[['watch.id',n(U64)],['watch.generation',n(U64)],['watch.effective_sequence',n(U64)]];reserve();const ack=registry.remove(integer(a[0]));this.#configuration=ack.captureRevision;this.#publish('watch_removed');return [['watch.id',n(ack.watchId)],['watch.generation',n(ack.generation)],['watch.effective_sequence',n(ack.effectiveSequence)]];}
  if(name==='watch_status'){
   const total=registry.protocolStatusCount(),h=registry.health;
   fields=[['watches.total',n(total)],['watches.truncated','bool:false'],['watches.dropped_events',n(h.droppedEvents)],['watches.observation_gaps',n(h.observationGaps)],['watches.inspection_errors',n(h.inspectionErrors)]];
   const fits=()=>encodeResponse(this.#response(r.requestId,null,fields),this.limits);fits();
   let returned=0;
   for(const v of registry.protocolStatuses()){
    if(returned>=this.limits.maxPageSize)break;
    fields.push([`watches.${returned}.id`,n(v.id)],[`watches.${returned}.generation`,n(v.generation)],[`watches.${returned}.enabled`,`bool:${v.enabled}`],[`watches.${returned}.has_baseline`,`bool:${v.hasBaseline}`],[`watches.${returned}.effective_sequence`,n(v.effectiveSequence)]);
    try{fits();}catch(e){fields.length-=5;if(e instanceof ProtocolError&&e.code==='limit')break;throw e;}
    returned++;
   }
   fields[1]=['watches.truncated',`bool:${returned<total}`];return fields;
  }
  const watchId=integer(a[0]);const event=registry.protocolCurrent(watchId);if(!event)return [['watch.value','enum:unavailable']];fields=[['watch.id',n(event.watchId)],['watch.generation',n(event.generation)],['watch.capture_revision',n(event.captureRevision)],['watch.snapshot_revision',n(event.snapshot.revision)],['watch.snapshot_sequence',n(event.snapshot.sequence)],['watch.kind',`enum:${event.kind.kind==='baseline'?`Baseline(${protocolVariant(event.kind.reason)})`:event.kind.kind==='inspector_error'?`InspectorError(${protocolInspectionError(event.kind.error)})`:event.kind.kind==='change_since_last_sample'?`ChangeSinceLastSample { changed: ${event.kind.changed} }`:protocolVariant(event.kind.kind)}`],['watch.completeness',`enum:${event.completeness.kind==='complete'?'Complete':`Partial(${protocolVariant(event.completeness.reason)})`}`],['watch.origin',`enum:${protocolVariant(event.origin)}`]];if(event.value)fields.push(...nodeProtocolFields('watch.node',event.value));return fields;
 }
 #concrete(r:Request,name:string,a:readonly string[]):Response['fields']{
  const s=this.session,hex=(v:Uint8Array)=>'bytes:'+Array.from(v,b=>b.toString(16).padStart(2,'0')).join('');
  if(name==='inputs'){
   if(!s.capabilities.enumerateInputs)fail('unsupported');const offset=integer(a[0]),limit=integer(a[1]);if(offset>BigInt(Number.MAX_SAFE_INTEGER))fail('limit');if(offset+limit>BigInt(s.candidateWorkLimit))fail('invalid_input');
   const page=(()=>{try{return s.candidates(r.revision,Number(offset),Number(limit));}catch(error){return s.withReadOwnership(()=>{if(error instanceof Error&&/candidate work budget/.test(error.message))fail('invalid_input');throw error;});}})(),fields:[string,string][]=[['complete',`bool:${page.complete}`],['next_offset',page.nextOffset===null?'none':u64(BigInt(page.nextOffset))]],pending:{id:bigint;revision:bigint;bytes:Uint8Array;cost:number}[]=[];let cost=0;
   for(const [i,token]of page.candidates.entries()){const bytes=s.encodeCandidate(token,this.limits.maxInput),id=this.#nextCandidate+BigInt(i)+1n;if(id>U64)fail('exhausted');cost+=bytes.length+64;if(cost>this.limits.maxCandidateBytes||pending.length===this.limits.maxCandidates)fail('limit');pending.push({id,revision:s.revision,bytes:bytes.slice(),cost:bytes.length+64});fields.push([`candidates.${i}.token`,u64(id)]);if(this.authority.readExactValues)fields.push([`candidates.${i}.encoded`,hex(bytes)]);}
   fields.push(['candidates.returned',u64(BigInt(pending.length))]);encodeResponse(this.#response(r.requestId,null,fields),this.limits);
   while(this.#candidates.size&&(this.#candidates.size+pending.length>this.limits.maxCandidates||this.#candidateBytes+cost>this.limits.maxCandidateBytes)){const id=this.#candidates.keys().next().value!;this.#candidateBytes-=this.#candidates.get(id)!.cost;this.#candidates.delete(id);}
   for(const c of pending){this.#candidates.set(c.id,c);this.#nextCandidate=c.id;}this.#candidateBytes+=cost;return fields;
  }
  if(name==='select'){const c=this.#candidates.get(integer(a[0]));if(!c||c.revision!==s.revision)fail('stale_handle');if(s.phase==='ended'||s.phase==='disconnected')fail('ended');let result;try{result=s.stepCanonicalCandidate(r.revision,c.bytes,this.decodeInput);}catch(e){s.withReadOwnership(()=>{if(e instanceof Error&&/invalid candidate encoding|candidate no longer|not permitted/.test(e.message))fail('invalid_input');throw e;});throw e;}if(result.delivered)this.#publish('transition');return [['delivered',`bool:${result.delivered}`],['sequence',u64(result.sequence)]];}
  if(!s.capabilities.exactRecording)fail('unsupported');const offset=integer(a[1]),w=s.traceWindow(offset,Number(integer(a[2])));if(offset>w.total)fail('invalid_input');const next=offset+BigInt(w.bytes.length);return [['export.kind','enum:exact_trace_sensitive'],['export.redacted','bool:false'],['export.snapshot_revision',u64(s.revision)],['export.snapshot_sequence',u64(s.sequence)],['export.total_bytes',u64(w.total)],['export.offset',u64(offset)],['export.returned_bytes',u64(BigInt(w.bytes.length))],['export.next_offset',next===w.total?'none':u64(next)],['export.complete',`bool:${next===w.total}`],['export.retained_steps',u64(BigInt(w.retained))],['export.evicted_steps',u64(w.evicted)],['export.capture_outcome',`enum:${s.stopReason?.kind??'none'}`],['export.bytes',hex(w.bytes)]];
 }
 #requireHandle(id:bigint):void{if(this.#handles.get(id)!==this.session.revision)fail('stale_handle');}
 #publish(kind:string):void{
  if(kind==='transition'){try{this.#observeWatches();}catch{this.#watches?.registry.observationGap();}}
  const drop=()=>{if(this.#eventDrops<U64)this.#eventDrops++;};
  if(this.#eventSequence===U64){drop();return;}
  const sequence=++this.#eventSequence;
  // Fixed-width event metadata plus the retained UTF-8 session and kind.
  const cost=88+encoder.encode(this.session.id).length+encoder.encode(kind).length;
  if(this.#events.length>=this.limits.maxEventEntries||cost>this.limits.maxEventBytes-this.#eventBytes){drop();return;}
  this.#events.push({sequence,revision:this.session.revision,configuration:this.#configuration,kind,cost});this.#eventBytes+=cost;
 }
 #pollEvents(r:Request,maximum:number):Response['fields']{
  const events=this.#events.slice(0,maximum),fields:[string,string][]=[['events.dropped',u64(this.#eventDrops)]];
  for(const [i,event]of events.entries())fields.push([`events.${i}.version`,'u64:1'],[`events.${i}.session`,`string:${this.session.id}`],[`events.${i}.epoch`,u64(this.#epoch)],[`events.${i}.sequence`,u64(event.sequence)],[`events.${i}.revision`,u64(event.revision)],[`events.${i}.configuration`,u64(event.configuration)],[`events.${i}.kind`,`enum:${event.kind}`]);
  fields.push(['events.returned',u64(BigInt(events.length))]);
  encodeResponse(this.#response(r.requestId,null,fields),this.limits);
  this.#events.splice(0,events.length);for(const event of events)this.#eventBytes-=event.cost;return fields;
 }
 /** One complete payload. Malformed frames do not retire a request ID. */
 handlePayload(bytes:Uint8Array):Uint8Array{let r:Request;try{r=decodeRequest(bytes,this.limits);}catch(e){return encodeResponse(this.#response(0n,e instanceof ProtocolError?e.code:'malformed'),this.limits);}return encodeResponse(this.handle(r),this.limits);}
}
/** Serve one local byte stream. The write callback must finish writing the entire
 * supplied frame before resolving. Clean EOF and transport failures disconnect
 * without cancelling the model; a failed write may follow a committed turn. */
export async function serve<S,I,O>(session:ProtocolSession<S,I,O>,reader:AsyncIterable<Uint8Array>,write:(frame:Uint8Array)=>void|Promise<void>):Promise<void>{
 const frames=new FrameReader(session.limits.maxFrame);
 try{
  for await(const chunk of reader)for(const payload of frames.feed(chunk))await write(writeFrame(session.handlePayload(payload),session.limits.maxFrame));
  frames.finish();
 }finally{session.disconnect();}
}
