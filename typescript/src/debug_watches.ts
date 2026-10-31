/** Local single-producer watches. Configuration acknowledgements are not remote producer barriers. */
import {ConfigError,ModelError,natural} from './core.js';
import {inspect,compareNodes,isComplete,retainedBytes,samePathSegment,validatePath,validateDisplayScalar,type Inspect,type InspectNode,type InspectLimits,type PathSegment,type MapKey,type DisplayScalar,type SnapshotId,type PageRequest,type DisplaySchema,type Completeness,type DiffKind} from './debug_inspect.js';
const U64=(1n<<64n)-1n,saturate=(v:bigint)=>v>U64?U64:v;
const key=(v:unknown)=>JSON.stringify(v,(_k,x)=>typeof x==='bigint'?x.toString():x);
const clone=<T>(v:T):T=>structuredClone(v);
// Logical layout matching the measured 64-bit Rust fixture; JavaScript object
// headers and allocator overhead are not inferred from these values.
const CONFIG_HEADER_BYTES=296,SEGMENT_HEADER_BYTES=32;
function configBytes(c:WatchConfig,maximum:number):number{
 let total=CONFIG_HEADER_BYTES;const charge=(n:number)=>{total+=n;if(total>maximum)throw new ModelError('watch configuration byte limit');};
 if(total>maximum)throw new ModelError('watch configuration byte limit');
 const text=(s:unknown)=>{if(typeof s!=='string')throw new ConfigError('invalid watch text');for(const ch of s){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ConfigError('invalid watch text');charge(cp<128?1:cp<2048?2:cp<65536?3:4);}};
 for(const segment of c.selector.path){charge(SEGMENT_HEADER_BYTES);if(segment.kind==='field'||segment.kind==='variant')text(segment.id);else if(segment.kind==='map_key'&&(segment.key.kind==='string'||segment.key.kind==='integer'))text(segment.key.kind==='string'?segment.key.value:segment.key.decimal);}
 for(const label of [c.selector.model,c.selector.machine,c.selector.inputVariant,c.selector.outputVariant])if(label!==undefined)text(label);
 if(c.trigger.kind==='equals'&&(c.trigger.value.kind==='integer'||c.trigger.value.kind==='float')){text(c.trigger.value.decimal);if(c.trigger.value.kind==='float'&&c.trigger.value.type!=='f32'&&c.trigger.value.type!=='f64')text(c.trigger.value.type);}
 return total;
}
function canonicalScalar(v:DisplayScalar):DisplayScalar{switch(v.kind){case 'unit':return {kind:'unit'};case 'bool':return {kind:'bool',value:v.value};case 'char':return {kind:'char',value:v.value};case 'integer':return {kind:'integer',type:v.type,decimal:v.decimal};case 'float':return {kind:'float',type:v.type==='f32'?'f32':v.type==='f64'?'f64':v.type,decimal:v.decimal,bits:v.bits};}}
function canonicalSegment(v:PathSegment):PathSegment{switch(v.kind){case 'field':case 'variant':return {kind:v.kind,id:v.id};case 'index':return {kind:'index',index:v.index};case 'map_key':{const k=v.key;if(k.kind==='integer')return {kind:'map_key',key:{kind:'integer',type:k.type,decimal:k.decimal}};if(k.kind==='string'||k.kind==='char')return {kind:'map_key',key:{kind:k.kind,value:k.value}};return {kind:'map_key',key:{kind:'bool',value:k.value}};}}}
function canonicalConfig(c:WatchConfig):WatchConfig{return {selector:{path:c.selector.path.map(canonicalSegment),schemaVersion:c.selector.schemaVersion,...(c.selector.model===undefined?{}:{model:c.selector.model}),...(c.selector.machine===undefined?{}:{machine:c.selector.machine}),...(c.selector.inputVariant===undefined?{}:{inputVariant:c.selector.inputVariant}),...(c.selector.outputVariant===undefined?{}:{outputVariant:c.selector.outputVariant}),...(c.selector.origin===undefined?{}:{origin:c.selector.origin})},trigger:c.trigger.kind==='equals'?{kind:'equals',value:canonicalScalar(c.trigger.value)}:{kind:c.trigger.kind},enabled:c.enabled,evaluationInterval:c.evaluationInterval,deliveryInterval:c.deliveryInterval,...(c.baselineTtlSequences===undefined?{}:{baselineTtlSequences:c.baselineTtlSequences}),...(c.ttlObservations===undefined?{}:{ttlObservations:c.ttlObservations}),limits:{maxWork:c.limits.maxWork,maxDepth:c.limits.maxDepth,maxNodes:c.limits.maxNodes,maxBytes:c.limits.maxBytes,maxValueBytes:c.limits.maxValueBytes,maxPageSize:c.limits.maxPageSize},page:{offset:c.page.offset,limit:c.page.limit}};}
/** Read only declared fields once; never clone caller-owned unknown graphs. */
function captureKnownConfig(c:WatchConfig,maximum:number):WatchConfig{
 const object=(v:unknown):Record<string,any>=>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ConfigError('invalid watch configuration');return v as Record<string,any>;};
 const text=(v:unknown):string=>{if(typeof v!=='string'||v.length>maximum)throw new ModelError('watch configuration byte limit');let bytes=0;for(const ch of v){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ConfigError('invalid watch text');bytes+=cp<128?1:cp<2048?2:cp<65536?3:4;if(bytes>maximum)throw new ModelError('watch configuration byte limit');}return v;};
 const source=object(c),selector=object(source.selector),rawPath=selector.path;
 if(!Array.isArray(rawPath))throw new ConfigError('invalid watch path');
 const pathLength=rawPath.length;
 if(!Number.isSafeInteger(pathLength)||pathLength<0||pathLength>32)throw new ConfigError('invalid watch path');
 const path:PathSegment[]=[];
 for(let i=0;i<pathLength;i++){
  if(!Object.hasOwn(rawPath,i))throw new ConfigError('sparse watch path');const segment=object(rawPath[i]),kind=segment.kind;
  if(kind==='field'||kind==='variant')path.push({kind,id:text(segment.id)});
  else if(kind==='index')path.push({kind,index:segment.index});
  else if(kind==='map_key'){
   const key=object(segment.key),keyKind=key.kind;
   if(keyKind==='integer')path.push({kind,key:{kind:keyKind,type:text(key.type) as Extract<MapKey,{kind:'integer'}>['type'],decimal:text(key.decimal)}});
   else if(keyKind==='string'||keyKind==='char')path.push({kind,key:{kind:keyKind,value:text(key.value)}});
   else if(keyKind==='bool')path.push({kind,key:{kind:keyKind,value:key.value}});
   else throw new ConfigError('invalid watch path key');
  }else throw new ConfigError('invalid watch path segment');
 }
 const schemaVersion=selector.schemaVersion,model=selector.model,machine=selector.machine,inputVariant=selector.inputVariant,outputVariant=selector.outputVariant,origin=selector.origin;
 const trigger=object(source.trigger),triggerKind=trigger.kind;let capturedTrigger:WatchTrigger;
 if(triggerKind==='changed'||triggerKind==='every_observation'||triggerKind==='property_failure')capturedTrigger={kind:triggerKind};
 else if(triggerKind==='equals'){
  const v=object(trigger.value),kind=v.kind;let value:DisplayScalar;
  if(kind==='unit')value={kind};else if(kind==='bool')value={kind,value:v.value};else if(kind==='char')value={kind,value:text(v.value)};
  else if(kind==='integer')value={kind,type:text(v.type) as Extract<DisplayScalar,{kind:'integer'}>['type'],decimal:text(v.decimal)};
  else if(kind==='float')value={kind,type:text(v.type),decimal:text(v.decimal),bits:v.bits};else throw new ConfigError('invalid watch equality scalar');
  capturedTrigger={kind:triggerKind,value};
 }else throw new ConfigError('invalid watch trigger');
 const limits=object(source.limits),page=object(source.page);
 return {selector:{path,schemaVersion,...(model===undefined?{}:{model:text(model)}),...(machine===undefined?{}:{machine:text(machine)}),...(inputVariant===undefined?{}:{inputVariant:text(inputVariant)}),...(outputVariant===undefined?{}:{outputVariant:text(outputVariant)}),...(origin===undefined?{}:{origin})},trigger:capturedTrigger,enabled:source.enabled,evaluationInterval:source.evaluationInterval,deliveryInterval:source.deliveryInterval,baselineTtlSequences:source.baselineTtlSequences,ttlObservations:source.ttlObservations,limits:{maxWork:limits.maxWork,maxDepth:limits.maxDepth,maxNodes:limits.maxNodes,maxBytes:limits.maxBytes,maxValueBytes:limits.maxValueBytes,maxPageSize:limits.maxPageSize},page:{offset:page.offset,limit:page.limit}};
}
function u64(n:bigint,name:string,min=0n){if(typeof n!=='bigint'||n<min||n>U64)throw new ConfigError(`${name} must be u64 bigint`);}
export type OriginKind='diagnostic_control'|'recorded_live'|'exact_replay'|'simulation'|'out_of_domain_injection'|'counterfactual_restore'|'edited_state_experiment';
export interface WatchSelector{readonly path:readonly PathSegment[];readonly schemaVersion:number;readonly model?:string;readonly machine?:string;readonly inputVariant?:string;readonly outputVariant?:string;readonly origin?:OriginKind}
export type WatchTrigger={kind:'changed'|'every_observation'|'property_failure'}|{kind:'equals';value:DisplayScalar};
export interface WatchConfig{readonly selector:WatchSelector;readonly trigger:WatchTrigger;readonly enabled:boolean;readonly evaluationInterval:bigint;readonly deliveryInterval:bigint;readonly baselineTtlSequences?:bigint;readonly ttlObservations?:bigint;readonly limits:InspectLimits;readonly page:PageRequest}
export interface WatchLimits{readonly maxWatches:number;readonly maxRingRecords:number;readonly maxRingBytes:number;readonly maxEventBytes:number;readonly maxBaselineBytes:number;readonly maxConfigBytes:number}
export const DEFAULT_WATCH_LIMITS:WatchLimits=Object.freeze({maxWatches:64,maxRingRecords:1000,maxRingBytes:4194304,maxEventBytes:32768,maxBaselineBytes:8388608,maxConfigBytes:4096});
export interface WatchContext{readonly snapshot:SnapshotId;readonly origin:OriginKind;readonly model?:string;readonly machine?:string;readonly inputVariant?:string;readonly outputVariant?:string;readonly outputVariants?:readonly string[];readonly propertyFailure?:boolean;readonly failingProperties?:readonly string[]}
function captureOutputVariants(value:unknown):string[]|undefined{
 if(value===undefined)return undefined;
 if(!Array.isArray(value))throw new ConfigError('invalid watch output variants');
 const length=value.length;if(!Number.isSafeInteger(length)||length<0||length>4096)throw new ConfigError('watch output variants limit');
 const output:string[]=[];let bytes=0;
 for(let i=0;i<length;i++){
  if(!Object.hasOwn(value,i))throw new ConfigError('invalid watch output variants');
  const v:unknown=value[i];if(typeof v!=='string'||v.length>1048576-bytes)throw new ConfigError('invalid watch output variants');
  for(const ch of v){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ConfigError('invalid watch output variants');bytes+=cp<128?1:cp<2048?2:cp<65536?3:4;if(bytes>1048576)throw new ConfigError('watch output variants limit');}
  output.push(v);
 }
 return output;
}
type CapturedContext=WatchContext & {readonly propertyMetadataTruncated:boolean;readonly identityMetadataTruncated:boolean};
// Rust's bounded metadata is a UTF-8 prefix, not a UTF-16 code-unit slice.
function boundedMetadata(s:string,limit=1024):{prefix:string,truncated:boolean}{
 if(typeof s!=='string'||s.length>1048576)throw new ConfigError('invalid watch metadata');
 let bytes=0,prefix='';
 for(const ch of s){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ConfigError('invalid watch metadata');const n=cp<128?1:cp<2048?2:cp<65536?3:4;if(bytes+n<=limit)prefix+=ch;bytes+=n;}
 return {prefix,truncated:bytes>limit};
}
function captureProperties(value:unknown):{ids:string[]|undefined,truncated:boolean}{
 if(value===undefined)return {ids:undefined,truncated:false};
 if(!Array.isArray(value))throw new ConfigError('invalid watch property IDs');
 const length=value.length;if(!Number.isSafeInteger(length)||length<0)throw new ConfigError('invalid watch property IDs');
 const ids:string[]=[];let truncated=length>16;
 for(let i=0;i<Math.min(length,16);i++){
  if(!Object.hasOwn(value,i))throw new ConfigError('invalid watch property IDs');
  const id:unknown=value[i];if(typeof id!=='string'||id.length>1048576)throw new ConfigError('invalid watch property ID');
  let bytes=0,preview='';for(const ch of id){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ConfigError('invalid watch property ID');const n=cp<128?1:cp<2048?2:cp<65536?3:4;if(bytes+n<=256)preview+=ch;bytes+=n;}
  if(bytes>256)truncated=true;ids.push(preview);
 }
 return {ids,truncated};
}
function captureContext(c:WatchContext):CapturedContext{const properties=captureProperties(c.failingProperties);const model=c.model,machine=c.machine;for(const value of [model,machine,c.inputVariant,c.outputVariant])if(value!==undefined){if(typeof value!=='string')throw new ConfigError('invalid watch metadata');boundedMetadata(value,0);}const identityMetadataTruncated=(model!==undefined&&boundedMetadata(model).truncated)||(machine!==undefined&&boundedMetadata(machine).truncated);return {snapshot:{...c.snapshot},origin:c.origin,model,machine,inputVariant:c.inputVariant,outputVariant:c.outputVariant,outputVariants:captureOutputVariants(c.outputVariants),propertyFailure:c.propertyFailure,failingProperties:properties.ids,propertyMetadataTruncated:properties.truncated,identityMetadataTruncated};}
export type BaselineReason='initial'|'reenabled'|'observation_gap'|'expired'|'evicted'|'source_changed';
export type WatchEventKind={kind:'watch_enabled'|'watch_disabled'|'watch_paused'|'watch_expired'|'watch_removed'|'observation_gap'|'schema_changed'|'changed'|'unchanged'|'unknown'|'property_failure'|'value_matched'|'snapshot'}|{kind:'events_dropped';count:bigint}|{kind:'baseline';reason:BaselineReason}|{kind:'change_since_last_sample';changed:boolean}|{kind:'inspector_error';error:string};
export interface WatchEvent{readonly watchId:bigint;readonly generation:bigint;readonly captureRevision:bigint;readonly snapshot:SnapshotId;readonly origin:OriginKind;readonly model:string|null;readonly machine:string|null;readonly kind:WatchEventKind;readonly completeness:Completeness;readonly value:InspectNode|null;readonly path:readonly PathSegment[];readonly schemaName:string|null;readonly previousSnapshot:SnapshotId|null;readonly difference:DiffKind;readonly propertyFailure:boolean;readonly failingProperties:readonly string[];readonly trigger:WatchTrigger}
export interface WatchAck{readonly watchId:bigint;readonly generation:bigint;readonly captureRevision:bigint;readonly effectiveSequence:bigint;readonly displaySchema:DisplaySchema|null;readonly resolvedType?:string}
export interface WatchHealth{evaluated:bigint;filtered:bigint;evaluationSampled:bigint;deliverySampled:bigint;droppedEvents:bigint;observationGaps:bigint;baselineEvictions:bigint;inspectionErrors:bigint;truncated:bigint}
type Entry={id:bigint;generation:bigint;config:WatchConfig;state:'active'|'paused'|'expired'|'disabled';current:WatchEvent|null;reset:BaselineReason|null;selected:bigint;emissions:bigint;sampled:boolean;schema:DisplaySchema|null;effectiveSequence:bigint};
function eventBytes(e:WatchEvent):number{return 384+(e.value?retainedBytes(e.value):0)+key({...e,value:null}).length*3;}
// Reserve the serializer's conservative metadata charge before inspecting.
function propertyReserve(c:WatchContext):number{return key(c.failingProperties??[]).length*3;}
export class WatchAuthorization{
 readonly #paths:readonly (readonly PathSegment[])[];
 private constructor(paths:readonly (readonly PathSegment[])[]){this.#paths=clone(paths);}
 static denyAll(){return new WatchAuthorization([]);}static allowAll(){return new WatchAuthorization([[]]);}static allowPaths(paths:readonly (readonly PathSegment[])[]){return new WatchAuthorization(paths);}
 allows(path:readonly PathSegment[]):boolean{return this.#paths.some(prefix=>prefix.length<=path.length&&prefix.every((s,i)=>samePathSegment(s,path[i])));}
}
export class WatchRegistry{
 readonly #limits:WatchLimits;readonly #authorization:WatchAuthorization;readonly #schemaVersion:number;#entries=new Map<bigint,Entry>();#revision=0n;#nextId=1n;#last:WatchContext|null=null;#ring:WatchEvent[]=[];#ringBytes=0;#pendingDropped=0n;#failure:ReturnType<WatchRegistry['snapshot']>|null=null;#busy=false;
 #health:WatchHealth={evaluated:0n,filtered:0n,evaluationSampled:0n,deliverySampled:0n,droppedEvents:0n,observationGaps:0n,baselineEvictions:0n,inspectionErrors:0n,truncated:0n};
 constructor(limits:WatchLimits=DEFAULT_WATCH_LIMITS,authorization=WatchAuthorization.denyAll(),schemaVersion=1){for(const [k,v]of Object.entries(limits))natural(v,k);natural(schemaVersion,'schema version',1,0xffffffff);this.#limits={...limits};this.#authorization=authorization;this.#schemaVersion=schemaVersion;}
 get limits():Readonly<WatchLimits>{return {...this.#limits};}
 get captureRevision(){return this.#revision;}get health():WatchHealth{return {...this.#health};}interested():boolean{return [...this.#entries.values()].some(e=>e.state==='active');}
 #inc(name:keyof WatchHealth,n=1n){this.#health[name]=saturate(this.#health[name]+n);}
 #guard(){if(this.#busy)throw new ModelError('watch registry already owned by producer');}
 #revisionNext(){if(this.#revision===U64||this.#last?.snapshot.sequence===U64)throw new ModelError('watch revision exhausted');return ++this.#revision;}
 #boundary(){return this.#last?saturate(this.#last.snapshot.sequence+1n):0n;}
 #ack(e:Entry):WatchAck{return {watchId:e.id,generation:e.generation,captureRevision:this.#revision,effectiveSequence:e.effectiveSequence,displaySchema:e.schema?clone(e.schema):null};}
 #validate(c:WatchConfig){if(typeof c.enabled!=='boolean')throw new ConfigError('watch enabled must be boolean');u64(c.evaluationInterval,'evaluationInterval',1n);u64(c.deliveryInterval,'deliveryInterval',1n);if(c.ttlObservations!==undefined)u64(c.ttlObservations,'ttlObservations',1n);if(c.baselineTtlSequences!==undefined)u64(c.baselineTtlSequences,'baselineTtlSequences');if(c.selector.schemaVersion!==this.#schemaVersion)throw new ModelError('watch schema mismatch');if(c.limits.maxDepth>32||c.limits.maxBytes===0||c.limits.maxBytes>this.#limits.maxEventBytes)throw new ModelError('watch projection limit');validatePath(c.selector.path,this.#limits.maxConfigBytes);if(c.selector.path.length>c.limits.maxDepth)throw new ModelError('watch path too deep');if(!c.trigger||typeof c.trigger!=='object'||!['changed','every_observation','property_failure','equals'].includes(c.trigger.kind))throw new ConfigError('invalid watch trigger');if(c.trigger.kind==='equals'){const v=c.trigger.value;if(v&&typeof v==='object'&&(v.kind==='integer'||v.kind==='float')&&(typeof v.decimal==='string'&&v.decimal.length>this.#limits.maxConfigBytes||v.kind==='float'&&typeof v.type==='string'&&v.type.length>this.#limits.maxConfigBytes))throw new ModelError('watch configuration byte limit');validateDisplayScalar(v);}if(c.selector.origin!==undefined&&!['diagnostic_control','recorded_live','exact_replay','simulation','out_of_domain_injection','counterfactual_restore','edited_state_experiment'].includes(c.selector.origin))throw new ConfigError('invalid watch origin');if(!this.#authorization.allows(c.selector.path))throw new ModelError('watch unauthorized');configBytes(c,this.#limits.maxConfigBytes);for(const k of ['maxWork','maxDepth','maxNodes','maxBytes','maxValueBytes','maxPageSize'] as const)natural(c.limits[k],k);natural(c.page.offset,'page offset');natural(c.page.limit,'page limit',1,c.limits.maxPageSize);}
 #captureConfig(config:WatchConfig):WatchConfig{this.#busy=true;try{return captureKnownConfig(config,this.#limits.maxConfigBytes);}finally{this.#busy=false;}}
 #captureContext(context:WatchContext):CapturedContext{this.#busy=true;try{return captureContext(context);}finally{this.#busy=false;}}
 #entry(id:bigint):Entry{const e=this.#entries.get(id);if(!e)throw new ModelError('unknown watch');return e;}
 #lifecycle(e:Entry,kind:WatchEventKind){this.#push({watchId:e.id,generation:e.generation,captureRevision:this.#revision,snapshot:this.#last?clone(this.#last.snapshot):{session:0n,revision:0n,sequence:0n},origin:'diagnostic_control',model:null,machine:null,kind,completeness:{kind:'complete'},value:null,path:clone(e.config.selector.path),schemaName:e.schema?boundedMetadata(e.schema.name).prefix:null,previousSnapshot:null,difference:'unknown',propertyFailure:false,failingProperties:[],trigger:clone(e.config.trigger)});}
 add(config:WatchConfig):WatchAck{this.#guard();config=this.#captureConfig(config);this.#validate(config);config=canonicalConfig(config);if(this.#entries.size>=this.#limits.maxWatches||this.#nextId===U64)throw new ModelError('watch capacity');this.#revisionNext();const e:Entry={id:this.#nextId++,generation:1n,config,state:config.enabled?'active':'disabled',current:null,reset:'initial',selected:0n,emissions:0n,sampled:false,schema:null,effectiveSequence:this.#boundary()};this.#entries.set(e.id,e);this.#lifecycle(e,{kind:config.enabled?'watch_enabled':'watch_disabled'});return this.#ack(e);}
 addValidated(config:WatchConfig,value:Inspect,context:WatchContext):WatchAck{this.#guard();config=this.#captureConfig(config);this.#validate(config);config=canonicalConfig(config);context=this.#captureContext(context);if(this.#entries.size>=this.#limits.maxWatches||this.#nextId===U64)throw new ModelError('watch capacity');this.#busy=true;let result;try{result=inspect(value,{snapshot:context.snapshot,path:config.selector.path,schemaVersion:config.selector.schemaVersion,page:config.page,limits:config.limits});if(result.node.kind.kind==='redacted')throw new ModelError('redacted watch path');}finally{this.#busy=false;}const ack=this.add(config);const e=this.#entry(ack.watchId);e.schema=clone(result.schema);const k=result.node.kind;return {...this.#ack(e),resolvedType:k.kind==='scalar'?(k.value.kind==='integer'||k.value.kind==='float'?k.value.type:k.value.kind):k.kind};}
 updateValidated(id:bigint,config:WatchConfig,value:Inspect,context:WatchContext):WatchAck{this.#guard();config=this.#captureConfig(config);this.#validate(config);config=canonicalConfig(config);context=this.#captureContext(context);this.#entry(id);this.#busy=true;let result;try{result=inspect(value,{snapshot:context.snapshot,path:config.selector.path,schemaVersion:config.selector.schemaVersion,page:config.page,limits:config.limits});if(result.node.kind.kind==='redacted')throw new ModelError('redacted watch path');}finally{this.#busy=false;}this.update(id,config);const e=this.#entry(id);e.schema=clone(result.schema);const k=result.node.kind;return {...this.#ack(e),resolvedType:k.kind==='scalar'?(k.value.kind==='integer'||k.value.kind==='float'?k.value.type:k.value.kind):k.kind};}
 snapshotNow(id:bigint,value:Inspect,context:WatchContext):WatchEvent{this.#guard();context=this.#captureContext(context);const e=this.#entry(id);this.#validate(e.config);this.#busy=true;try{const result=inspect(value,{snapshot:context.snapshot,path:e.config.selector.path,schemaVersion:e.config.selector.schemaVersion,page:e.config.page,limits:{...e.config.limits,maxBytes:Math.min(e.config.limits.maxBytes,Math.max(0,this.#limits.maxEventBytes-4096-propertyReserve(context)))}});if(e.schema&&(e.schema.name!==result.schema.name||e.schema.version!==result.schema.version))throw new ModelError('watch schema mismatch');if(result.node.kind.kind==='redacted')throw new ModelError('redacted watch path');const event:WatchEvent={watchId:e.id,generation:e.generation,captureRevision:this.#revision,snapshot:clone(context.snapshot),origin:context.origin,model:context.model===undefined?null:boundedMetadata(context.model).prefix,machine:context.machine===undefined?null:boundedMetadata(context.machine).prefix,kind:{kind:'snapshot'},completeness:result.node.completeness.kind==='complete'&&((context as CapturedContext).propertyMetadataTruncated||(context as CapturedContext).identityMetadataTruncated||boundedMetadata(result.schema.name).truncated)?{kind:'partial',reason:'value_limit'}:clone(result.node.completeness),value:result.node,path:clone(e.config.selector.path),schemaName:boundedMetadata(result.schema.name).prefix,previousSnapshot:null,difference:'unknown',propertyFailure:!!context.propertyFailure,failingProperties:[...(context.failingProperties??[])],trigger:clone(e.config.trigger)};if(eventBytes(event)>this.#limits.maxEventBytes)throw new ModelError('watch event capacity');this.#push(event);return clone(event);}finally{this.#busy=false;}}
 update(id:bigint,config:WatchConfig):WatchAck{this.#guard();config=this.#captureConfig(config);this.#validate(config);config=canonicalConfig(config);const e=this.#entry(id);if(e.generation===U64)throw new ModelError('watch generation exhausted');this.#revisionNext();e.generation++;e.config=config;e.schema=null;e.state=config.enabled?'active':'disabled';e.current=null;e.reset='reenabled';e.selected=0n;e.emissions=0n;e.sampled=false;e.effectiveSequence=this.#boundary();this.#lifecycle(e,{kind:config.enabled?'watch_enabled':'watch_disabled'});return this.#ack(e);}
 #state(id:bigint,state:Entry['state'],kind:WatchEventKind):WatchAck{this.#guard();const e=this.#entry(id);if(e.generation===U64)throw new ModelError('watch generation exhausted');this.#revisionNext();e.generation++;e.selected=0n;e.emissions=0n;e.sampled=false;e.state=state;e.config={...e.config,enabled:state==='active'};e.current=null;e.reset='reenabled';e.effectiveSequence=this.#boundary();this.#lifecycle(e,kind);return this.#ack(e);}
 enable(id:bigint){return this.#state(id,'active',{kind:'watch_enabled'});}disable(id:bigint){return this.#state(id,'disabled',{kind:'watch_disabled'});}pause(id:bigint){return this.#state(id,'paused',{kind:'watch_paused'});}resume(id:bigint){return this.enable(id);}
 remove(id:bigint):WatchAck{this.#guard();const e=this.#entry(id);if(e.generation===U64)throw new ModelError('watch generation exhausted');this.#revisionNext();e.generation++;e.effectiveSequence=this.#boundary();this.#lifecycle(e,{kind:'watch_removed'});this.#entries.delete(id);return {...this.#ack(e),displaySchema:null};}
 list(){return [...this.#entries.values()].map(e=>({id:e.id,generation:e.generation,state:e.state,enabled:e.config.enabled,config:clone(e.config),hasBaseline:e.current!==null,effectiveSequence:e.effectiveSequence,displaySchema:e.schema?clone(e.schema):null}));}
 protocolStatusCount():number{return this.#entries.size;}
 *protocolStatuses(){for(const e of this.#entries.values())yield {id:e.id,generation:e.generation,enabled:e.config.enabled,hasBaseline:e.reset===null&&!!e.current?.value&&isComplete(e.current.value),effectiveSequence:e.effectiveSequence};}
 current(id:bigint):WatchEvent|null{return clone(this.#entry(id).current);}
 protocolCurrent(id:bigint):WatchEvent|null{const e=this.#entries.get(id);return e?.current?clone(e.current):null;}
 snapshot(){return {captureRevision:this.#revision,at:this.#last?clone(this.#last.snapshot):null,events:clone(this.#ring),retainedBytes:this.#ringBytes,health:this.health};}
 failureSnapshot(){return this.#failure?clone(this.#failure):null;}clearFailureSnapshot(){this.#guard();this.#failure=null;}clearRing(){this.#guard();this.#ring=[];this.#ringBytes=0;}
 #push(event:WatchEvent):boolean{const bytes=eventBytes(event);if(bytes>this.#limits.maxEventBytes||bytes>this.#limits.maxRingBytes||this.#limits.maxRingRecords===0){this.#inc('droppedEvents');this.#pendingDropped=saturate(this.#pendingDropped+1n);return false;}while(this.#ring.length>=this.#limits.maxRingRecords||this.#ringBytes+bytes>this.#limits.maxRingBytes){const removed=this.#ring.shift()!;this.#ringBytes-=eventBytes(removed);this.#inc('droppedEvents');this.#pendingDropped=saturate(this.#pendingDropped+1n);}this.#ring.push(clone(event));this.#ringBytes+=bytes;return true;}
 observationGap():void{this.#guard();this.#gap();}
 #gap(){this.#inc('observationGaps');for(const e of this.#entries.values()){e.current=null;e.reset='observation_gap';if(e.state==='active')this.#lifecycle(e,{kind:'observation_gap'});}}
 #enforceBaselines(){let bytes=[...this.#entries.values()].reduce((sum,e)=>sum+(e.current?eventBytes(e.current):0),0);for(const e of this.#entries.values()){if(bytes<=this.#limits.maxBaselineBytes)break;if(e.current){bytes-=eventBytes(e.current);e.current=null;e.reset='evicted';this.#inc('baselineEvictions');}}}
 observe(value:()=>Inspect,context:WatchContext){this.#guard();context=this.#captureContext(context);for(const n of Object.values(context.snapshot))u64(n,'snapshot identity');let evaluated=0,emitted=0,observationGap=false;this.#busy=true;try{
  if(this.#last){const p=this.#last.snapshot,n=context.snapshot;if(p.session===n.session&&n.sequence<=p.sequence)return {evaluated,emitted,stale:true,observationGap};if(this.interested()&&(p.session!==n.session||n.sequence!==saturate(p.sequence+1n))){this.#gap();observationGap=true;}}
  this.#last={snapshot:clone(context.snapshot),origin:context.origin};if(!this.interested())return {evaluated,emitted,stale:false,observationGap};let materialized:Inspect|undefined;const cache=new Map<string,ReturnType<typeof inspect>|Error>();
  for(const e of this.#entries.values()){if(e.state!=='active')continue;const c=e.config;if(c.ttlObservations!==undefined&&e.selected>=c.ttlObservations){e.state='expired';e.config={...c,enabled:false};this.#lifecycle(e,{kind:'watch_expired'});continue;}
   if((['model','machine','inputVariant','origin'] as const).some(k=>c.selector[k]!==undefined&&c.selector[k]!==context[k])||(c.selector.outputVariant!==undefined&&c.selector.outputVariant!==context.outputVariant&&!context.outputVariants?.includes(c.selector.outputVariant))){this.#inc('filtered');continue;}
   materialized??=value();const actualSchema=materialized.schema();if(typeof actualSchema.name!=='string')throw new ConfigError('invalid watch schema name');boundedMetadata(actualSchema.name,0);if(actualSchema.source)boundedMetadata(actualSchema.source.file,0);if(actualSchema.version!==this.#schemaVersion||e.schema&&(e.schema.name!==actualSchema.name||e.schema.version!==actualSchema.version)){e.state='paused';e.config={...c,enabled:false};e.current=null;this.#lifecycle(e,{kind:'schema_changed'});continue;}e.schema={name:actualSchema.name,version:actualSchema.version,source:actualSchema.source?{file:actualSchema.source.file,line:actualSchema.source.line,column:actualSchema.source.column}:undefined};
   if(e.current&&(e.current.model!==(context.model??null)||e.current.machine!==(context.machine??null)||e.current.origin!==context.origin))e.reset='source_changed';
   e.selected=saturate(e.selected+1n);if(!context.propertyFailure&&(e.selected-1n)%c.evaluationInterval!==0n){e.sampled=true;this.#inc('evaluationSampled');if(c.ttlObservations!==undefined&&e.selected>=c.ttlObservations){e.state='expired';e.config={...c,enabled:false};this.#lifecycle(e,{kind:'watch_expired'});}continue;}
   if(e.current&&c.baselineTtlSequences!==undefined&&context.snapshot.sequence-e.current.snapshot.sequence>c.baselineTtlSequences)e.reset='expired';
   const query={snapshot:context.snapshot,path:c.selector.path,schemaVersion:c.selector.schemaVersion,page:c.page,limits:{...c.limits,maxBytes:Math.min(c.limits.maxBytes,Math.max(0,this.#limits.maxEventBytes-2048-propertyReserve(context)))}},cacheKey=key(query);let result=cache.get(cacheKey);if(!result){try{materialized??=value();result=inspect(materialized,query);}catch(error){result=error instanceof Error?error:new ModelError(String(error));}cache.set(cacheKey,result);}evaluated++;this.#inc('evaluated');
   let kind:WatchEventKind,node:InspectNode|null=null,completeness:Completeness={kind:'partial',reason:'unavailable'},difference:DiffKind='unknown';
   if(result instanceof Error){kind={kind:'inspector_error',error:result.message.slice(0,1024)};e.reset='observation_gap';this.#inc('inspectionErrors');}
   else {if(e.schema&&(e.schema.name!==result.schema.name||e.schema.version!==result.schema.version)){e.state='paused';e.config={...c,enabled:false};e.current=null;this.#lifecycle(e,{kind:'schema_changed'});continue;}e.schema=clone(result.schema);node=result.node;completeness=node.completeness;
    const compared=compareNodes(e.current?.value??null,node);if(!isComplete(node)){kind={kind:'unknown'};e.reset='observation_gap';this.#inc('truncated');}else if(e.reset){kind={kind:'baseline',reason:e.reset};e.reset=null;}else if(!e.current||!e.current.value||!isComplete(e.current.value))kind={kind:'baseline',reason:'observation_gap'};else if(e.sampled){kind={kind:'change_since_last_sample',changed:compared==='changed'};completeness={kind:'partial',reason:'sampled'};}else{kind={kind:compared==='changed'?'changed':compared==='unchanged'?'unchanged':'unknown'};difference=compared;}}
   e.sampled=false;const shouldEmit=c.trigger.kind==='every_observation'||c.trigger.kind==='changed'&&kind.kind!=='unchanged'||c.trigger.kind==='property_failure'&&!!context.propertyFailure||c.trigger.kind==='equals'&&node!==null&&isComplete(node)&&node.kind.kind==='scalar'&&key(node.kind.value)===key(c.trigger.value)||!!context.propertyFailure;
   if(context.propertyFailure&&kind.kind!=='inspector_error')kind={kind:'property_failure'};else if(kind.kind!=='inspector_error'&&shouldEmit&&c.trigger.kind==='equals')kind={kind:'value_matched'};
   if(((context as CapturedContext).propertyMetadataTruncated||(context as CapturedContext).identityMetadataTruncated||(e.schema!==null&&boundedMetadata(e.schema.name).truncated))&&completeness.kind==='complete')completeness={kind:'partial',reason:'value_limit'};
   const event:WatchEvent={watchId:e.id,generation:e.generation,captureRevision:this.#revision,snapshot:clone(context.snapshot),origin:context.origin,model:context.model===undefined?null:boundedMetadata(context.model).prefix,machine:context.machine===undefined?null:boundedMetadata(context.machine).prefix,kind,completeness:clone(completeness),value:node?clone(node):null,path:clone(c.selector.path),schemaName:e.schema?boundedMetadata(e.schema.name).prefix:null,previousSnapshot:e.current?clone(e.current.snapshot):null,difference,propertyFailure:!!context.propertyFailure,failingProperties:[...(context.failingProperties??[])],trigger:clone(c.trigger)};
   let deliveryDue=false;if(shouldEmit){e.emissions=saturate(e.emissions+1n);deliveryDue=!!context.propertyFailure||(e.emissions-1n)%c.deliveryInterval===0n;if(!deliveryDue)this.#inc('deliverySampled');}
   if(eventBytes(event)>this.#limits.maxEventBytes){e.current=null;e.reset='observation_gap';if(deliveryDue){this.#inc('droppedEvents');this.#pendingDropped=saturate(this.#pendingDropped+1n);}if(c.ttlObservations!==undefined&&e.selected>=c.ttlObservations){e.state='expired';e.config={...c,enabled:false};this.#lifecycle(e,{kind:'watch_expired'});}continue;}
   e.current=event;
   if(deliveryDue&&this.#push(event))emitted++;this.#enforceBaselines();
   if(c.ttlObservations!==undefined&&e.selected>=c.ttlObservations){e.state='expired';e.config={...c,enabled:false};this.#lifecycle(e,{kind:'watch_expired'});}
  }
  if(this.#pendingDropped>0n){const e=this.#entries.values().next().value as Entry|undefined;if(e){const count=this.#pendingDropped;this.#pendingDropped=0n;this.#lifecycle(e,{kind:'events_dropped',count});}}
  if(context.propertyFailure)this.#failure=this.snapshot();return {evaluated,emitted,stale:false,observationGap};
 }finally{this.#busy=false;}}
}
export interface DiagnosticMarker{readonly id:string;readonly detail:string}
export interface MarkerBatch{readonly markers:readonly DiagnosticMarker[];readonly dropped:bigint;readonly truncated:bigint;readonly retainedBytes:number}
/** Optional cheap markers use a logical 48-byte header (Rust 64-bit layout); this does not bound JS heap or callback allocations. */
export class MarkerCollector{
 readonly #enabled:boolean;readonly #maxRecords:number;readonly #maxBytes:number;#markers:DiagnosticMarker[]=[];#dropped=0n;#truncated=0n;#bytes=0;#finished=false;#busy=false;
 constructor(enabled:boolean,maxRecords=256,maxBytes=32768){if(typeof enabled!=='boolean')throw new ConfigError('marker enabled must be boolean');natural(maxRecords,'marker records');natural(maxBytes,'marker bytes');this.#enabled=enabled;this.#maxRecords=maxRecords;this.#maxBytes=maxBytes;}
 static disabled(){return new MarkerCollector(false);}
 interested():boolean{return !this.#finished&&this.#enabled&&this.#markers.length<this.#maxRecords&&this.#bytes<this.#maxBytes;}
 marker(id:string,detail:()=>string):void{if(this.#finished||this.#busy)throw new ModelError('marker collector unavailable');if(!this.#enabled)return;let idBytes=0;const remaining=this.#maxBytes-this.#bytes;if(!this.interested()||48+id.length>remaining){this.#dropped=saturate(this.#dropped+1n);return;}for(const ch of id){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ModelError('marker invalid Unicode scalar');idBytes+=cp<128?1:cp<2048?2:cp<65536?3:4;if(48+idBytes>remaining){this.#dropped=saturate(this.#dropped+1n);return;}}
  this.#busy=true;let value:string;try{value=detail();if(typeof value!=='string')throw new ModelError('marker detail must return string');}finally{this.#busy=false;}const available=remaining-48-idBytes;let bytes=0,preview='';for(const ch of value){const cp=ch.codePointAt(0)!;if(cp>=0xd800&&cp<=0xdfff)throw new ModelError('marker invalid Unicode scalar');const n=cp<128?1:cp<2048?2:cp<65536?3:4;if(bytes+n>available)break;bytes+=n;preview+=ch;}if(preview.length<value.length)this.#truncated=saturate(this.#truncated+1n);this.#bytes+=48+idBytes+bytes;this.#markers.push({id,detail:preview});
 }
 finish():MarkerBatch{if(this.#finished||this.#busy)throw new ModelError('marker collector unavailable');this.#finished=true;return {markers:clone(this.#markers),dropped:this.#dropped,truncated:this.#truncated,retainedBytes:this.#bytes};}
}
