/** Explicit lifecycle history. No wall clock, implicit retirement or effect host. */
import {Check, Disposition, Metadata, ModelError, check} from './core.js';
export interface ScopedKey<K> {readonly epoch:bigint;readonly generation:bigint;readonly key:K}
export interface LifecycleEvent<K> {readonly kind:'begin'|'cancel'|'settle'|'publish'|'acquire'|'release'|'tick'|'fence';readonly key?:K;readonly time?:bigint}
export interface LifecycleObligation {deadline:bigint;cancelled:boolean;settled:boolean;published:boolean}
export interface LifecycleViolation<K> {legacy?:string;key:K|undefined;code:string;event:string;observedAt:bigint;prior:LifecycleObligation|undefined}
export interface LifecycleHistory<K> {now:bigint;retiredBefore:bigint;obligations:[K,LifecycleObligation][];resources:[K,boolean][];violations:LifecycleViolation<K>[]}
export interface RetentionLimits {obligations:number;resources:number;violations:number}
export interface LifecycleSpec<K,I,O> {metadata():Metadata;inputEvents(input:I,disposition:Disposition):Iterable<LifecycleEvent<K>>;outputEvents(outputs:readonly O[]):Iterable<LifecycleEvent<K>>}
const pending=(v:LifecycleObligation)=>!v.cancelled&&!v.settled;
function u64(v:bigint):bigint {if(typeof v!=='bigint'||v<0n||v>0xffffffffffffffffn)throw new ModelError('logical time must be u64');return v;}
/** Key equality/order and copying are application contracts, as with model states. */
export class LifecycleMonitor<K,I,O> {
 constructor(readonly spec:LifecycleSpec<K,I,O>,readonly limits:RetentionLimits,readonly compare:(a:K,b:K)=>number,readonly cloneKey:(k:K)=>K,readonly epoch?: (k:K)=>bigint) {
  for(const v of Object.values(limits))if(!Number.isSafeInteger(v)||v<0)throw new ModelError('invalid retention limit');
 }
 metadata():Metadata {const m=this.spec.metadata();return {...m,name:this.epoch?`indexed-lifecycle-v1:${new TextEncoder().encode(m.name).length}:${m.name};limits=${this.limits.obligations},${this.limits.resources},${this.limits.violations};diagnostics=native-codes-v1`:`${m.name};history-limit=${this.limits.obligations}`,build:m.build+';lifecycle-native'};}
 equalHistory(a:LifecycleHistory<K>,b:LifecycleHistory<K>):boolean {
  const ob=(x:LifecycleObligation|undefined,y:LifecycleObligation|undefined)=>x===undefined?y===undefined:y!==undefined&&x.deadline===y.deadline&&x.cancelled===y.cancelled&&x.settled===y.settled&&x.published===y.published;
  return a.now===b.now&&a.retiredBefore===b.retiredBefore&&a.obligations.length===b.obligations.length&&a.resources.length===b.resources.length&&a.violations.length===b.violations.length&&a.obligations.every(([k,v],i)=>this.compare(k,b.obligations[i][0])===0&&ob(v,b.obligations[i][1]))&&a.resources.every(([k,v],i)=>this.compare(k,b.resources[i][0])===0&&v===b.resources[i][1])&&a.violations.every((v,i)=>{const w=b.violations[i];if(!this.epoch)return (v.legacy??legacyLifecycleMessages[v.code]??v.code)===(w.legacy??legacyLifecycleMessages[w.code]??w.code);return (v.key===undefined?w.key===undefined:w.key!==undefined&&this.compare(v.key,w.key)===0)&&v.code===w.code&&v.event===w.event&&v.observedAt===w.observedAt&&ob(v.prior,w.prior);});
 }
 initialState():LifecycleHistory<K>{return {now:0n,retiredBefore:0n,obligations:[],resources:[],violations:[]};}
 cloneHistory(h:LifecycleHistory<K>):LifecycleHistory<K>{return {...h,obligations:h.obligations.map(([k,v])=>[this.cloneKey(k),{...v}]),resources:h.resources.map(([k,v])=>[this.cloneKey(k),v]),violations:h.violations.map(v=>({...v,key:v.key===undefined?undefined:this.cloneKey(v.key),prior:v.prior&&{...v.prior}}))};}
 validate(h:LifecycleHistory<K>):void {
  u64(h.now);u64(h.retiredBefore);
  for(const n of ['obligations','resources','violations'] as const)if(h[n].length>this.limits[n])throw new ModelError('lifecycle retention limit; verification incomplete');
  if(this.epoch){for(const entries of [h.obligations,h.resources])for(let i=0;i<entries.length;i++){if(u64(this.epoch(entries[i][0]))<h.retiredBefore||i>0&&this.compare(entries[i-1][0],entries[i][0])>=0)throw new ModelError('noncanonical indexed history');}if(h.obligations.some(([,v])=>v.published&&!v.settled))throw new ModelError('invalid publication history');}
 }
 retention(h:LifecycleHistory<K>){const active=h.obligations.filter(([,v])=>pending(v));let oldest:[K,bigint]|undefined;for(const [k,v]of active)if(!oldest||v.deadline<oldest[1])oldest=[this.cloneKey(k),v.deadline];const acquired=h.resources.filter(([,v])=>v).length;return {activeObligations:active.length,historicalObligations:h.obligations.length-active.length,acquiredResources:acquired,historicalResources:h.resources.length-acquired,oldestUnresolved:oldest,retiredBefore:h.retiredBefore};}
 private find<V>(entries:readonly [K,V][],key:K):[K,V]|undefined {if(!this.epoch)return entries.find(([k])=>this.compare(k,key)===0);let low=0,high=entries.length;while(low<high){const mid=Math.floor((low+high)/2);if(this.compare(entries[mid][0],key)<0)low=mid+1;else high=mid;}return low<entries.length&&this.compare(entries[low][0],key)===0?entries[low]:undefined;}
 private overdue(h:LifecycleHistory<K>,t:bigint,strict=false){return h.obligations.find(([,v])=>pending(v)&&(strict?v.deadline<t:v.deadline<=t));}
 private violation(h:LifecycleHistory<K>,key:K|undefined,code:string,event:string){if(h.violations.length>=this.limits.violations)throw new ModelError('lifecycle violation limit; verification incomplete');const prior=key===undefined?undefined:this.find(h.obligations,key)?.[1];h.violations.push({legacy:this.epoch?undefined:legacyLifecycleMessages[code]??code,key:key===undefined?undefined:this.cloneKey(key),code,event,observedAt:h.now,prior:prior&&{...prior}});}
 advance(before:LifecycleHistory<K>,input:I,outputs:readonly O[],disposition:Disposition):LifecycleHistory<K>{
  this.validate(before);const h=this.cloneHistory(before);let overdue=this.overdue(h,h.now);if(overdue)this.violation(h,overdue[0],'missing_in_prior_history','observation');
  const events=[...this.spec.inputEvents(input,disposition),...this.spec.outputEvents(outputs)];
  for(const e of events){const kind=e.kind,k=e.key,t=u64(e.time??0n);const fail=(code:string,key:K|undefined=k)=>this.violation(h,key,code,kind);
   if(kind==='fence'){
    if(!this.epoch)throw new ModelError('legacy history has no fence');
    if(t<h.retiredBefore){fail('fence_backwards',undefined);continue;}
    const o=h.obligations.find(([k,v])=>this.epoch!(k)<t&&pending(v));if(o){fail('fence_pending_obligation',o[0]);continue;}
    const r=h.resources.find(([k,v])=>this.epoch!(k)<t&&v);if(r){fail('fence_acquired_resource',r[0]);continue;}
    h.obligations=h.obligations.filter(([k])=>this.epoch!(k)>=t);h.resources=h.resources.filter(([k])=>this.epoch!(k)>=t);h.retiredBefore=t;continue;
   }
   if(kind!=='tick'&&k===undefined)throw new ModelError('missing lifecycle key');
   if(kind!=='tick'&&this.epoch&&u64(this.epoch(k!))<h.retiredBefore){fail('retired_epoch_reference');continue;}
   const v=(k===undefined?undefined:this.find(h.obligations,k)?.[1]);const r=(k===undefined?undefined:this.find(h.resources,k));
   switch(kind){
    case 'tick':if(t<h.now)fail('time_backwards',undefined);else {const o=this.overdue(h,t,true);if(o)fail('missing_before_advance',o[0]);h.now=t;}break;
    case 'begin':if(v)fail('key_reused');else {if(h.obligations.length>=this.limits.obligations)throw new ModelError('obligation limit; explicit fence required; verification incomplete');h.obligations.push([this.cloneKey(k!),{deadline:t,cancelled:false,settled:false,published:false}]);if(this.epoch)h.obligations.sort((a,b)=>this.compare(a[0],b[0]));if(t<h.now)fail('began_after_deadline');}break;
    case 'cancel':if(v)v.cancelled=true;else fail('cancel_without_obligation');break;
    case 'settle':if(!v)fail('settlement_without_obligation');else if(v.cancelled)fail('settlement_after_cancel');else if(v.settled)fail('duplicate_settlement');else {if(!this.epoch)v.settled=true;if(v.deadline<h.now)fail('settlement_after_deadline');v.settled=true;}break;
    case 'publish':if(!v||v.cancelled||!v.settled)fail('stale_or_premature_publication');else if(this.epoch){if(v.published)fail('duplicate_publication');else v.published=true;}break;
    case 'acquire':if(r){if(r[1])fail('resource_already_acquired');else r[1]=true;}else {if(h.resources.length>=this.limits.resources)throw new ModelError('resource limit; verification incomplete');h.resources.push([this.cloneKey(k!),true]);if(this.epoch)h.resources.sort((a,b)=>this.compare(a[0],b[0]));}break;
    case 'release':if(v&&pending(v))fail('release_before_settlement');if(!r)fail('release_without_acquisition');else if(!r[1])fail('duplicate_release');else r[1]=false;break;
    default:throw new ModelError('invalid lifecycle event');
   }
  }
  overdue=this.overdue(h,h.now);if(overdue)this.violation(h,overdue[0],'missing_at_deadline','observation');return h;
 }
 checkState(h:LifecycleHistory<K>,_actual?:unknown):Check[]{this.validate(h);const p=this.epoch?'lifecycle.indexed':'lifecycle',n=this.retention(h).activeObligations;return [h.violations.length||this.overdue(h,h.now)?check(p+'.safety','failed',h.violations.map(v=>this.epoch?v.code:v.legacy??legacyLifecycleMessages[v.code]??v.code).join('; ')||(this.epoch?'missing_at_deadline':'required settlement missing at deadline')):check(p+'.safety'),n?check(p+'.obligations','skipped',`${n} pending obligations in finite history`):check(p+'.obligations')];}
}
export class IndexedLifecycleMonitor<K,I,O> extends LifecycleMonitor<ScopedKey<K>,I,O>{
 constructor(spec:LifecycleSpec<ScopedKey<K>,I,O>,limits:RetentionLimits,compare:(a:K,b:K)=>number,clone:(k:K)=>K){super(spec,limits,(a,b)=>a.epoch<b.epoch?-1:a.epoch>b.epoch?1:a.generation<b.generation?-1:a.generation>b.generation?1:compare(a.key,b.key),k=>({epoch:u64(k.epoch),generation:u64(k.generation),key:clone(k.key)}),k=>k.epoch);}
}

import {customValueSchema,ValueSchema,valueCodecs as vc,logicalLayout,encodeValue,decodeValue} from './value_codec.js';
/** Canonical Rust field order; logical allocation includes conservative map-node overhead. */
export function scopedKeySchema<K>(key:ValueSchema<K>):ValueSchema<ScopedKey<K>>{
 if(!key.layout)throw new ModelError('key allocation layout required');
 return customValueSchema({name:`ScopedKey<${key.schema()}>`,layout:logicalLayout(key.layout.bytes+16n,'scoped-key'),encodeInto:(k,b)=>{vc.u64.encodeRaw(k.epoch,b);vc.u64.encodeRaw(k.generation,b);key.encodeRaw(k.key,b);},decodeFrom:r=>({epoch:r.value(vc.u64),generation:r.value(vc.u64),key:r.value(key)})});
}
export function lifecycleHistorySchema<K,I,O>(m:LifecycleMonitor<K,I,O>,key:ValueSchema<K>):ValueSchema<LifecycleHistory<K>>{
 if(!key.layout)throw new ModelError('key allocation layout required');const weight=key.layout.bytes+256n,indexed=!!m.epoch;
 return customValueSchema({name:indexed?'indexed-lifecycle-v1':'legacy-lifecycle',encodeInto:(h,b)=>{
  if(indexed){m.validate(h);vc.u8.encodeRaw(1,b);}vc.u64.encodeRaw(h.now,b);if(indexed)vc.u64.encodeRaw(h.retiredBefore,b);
  const ob=(v:LifecycleObligation,full:boolean)=>{vc.u64.encodeRaw(v.deadline,b);vc.bool.encodeRaw(v.cancelled,b);vc.bool.encodeRaw(v.settled,b);if(full)vc.bool.encodeRaw(v.published,b);};
  vc.u32.encodeRaw(h.obligations.length,b);for(const [k,v]of h.obligations){key.encodeRaw(k,b);ob(v,indexed);}vc.u32.encodeRaw(h.resources.length,b);for(const [k,v]of h.resources){key.encodeRaw(k,b);vc.bool.encodeRaw(v,b);}vc.u32.encodeRaw(h.violations.length,b);
  for(const v of h.violations){if(!indexed){vc.string.encodeRaw(v.legacy??legacyLifecycleMessages[v.code]??v.code,b);continue;}vc.u8.encodeRaw(v.key===undefined?0:1,b);if(v.key!==undefined)key.encodeRaw(v.key,b);vc.string.encodeRaw(v.code,b);vc.string.encodeRaw(v.event,b);vc.u64.encodeRaw(v.observedAt,b);vc.u8.encodeRaw(v.prior===undefined?0:1,b);if(v.prior)ob(v.prior,true);}
 },decodeFrom:r=>{
  if(indexed&&r.value(vc.u8)!==1)throw new ModelError('indexed lifecycle codec version');const h=m.initialState();h.now=r.value(vc.u64);if(indexed)h.retiredBefore=r.value(vc.u64);
  const count=()=>{const n=r.value(vc.u32);r.collection(n,weight);return n;};const ob=(full:boolean):LifecycleObligation=>({deadline:r.value(vc.u64),cancelled:r.value(vc.bool),settled:r.value(vc.bool),published:full?r.value(vc.bool):false});
  for(let n=count();n>0;n--)h.obligations.push([r.value(key),ob(indexed)]);for(let n=count();n>0;n--)h.resources.push([r.value(key),r.value(vc.bool)]);
  const optional=<T>(read:()=>T):T|undefined=>{const tag=r.value(vc.u8);if(tag>1)throw new ModelError('invalid option tag');return tag===1?read():undefined;};
  for(let n=count();n>0;n--){if(!indexed){const text=r.value(vc.string),code=Object.keys(legacyLifecycleMessages).find(k=>legacyLifecycleMessages[k]===text)??text;h.violations.push({legacy:text,key:undefined,code,event:'',observedAt:0n,prior:undefined});}else h.violations.push({key:optional(()=>r.value(key)),code:r.value(vc.string),event:r.value(vc.string),observedAt:r.value(vc.u64),prior:optional(()=>ob(true))});}m.validate(h);return h;
 }});
}
/** Adds history persistence to the existing Oracle-compatible monitor. */
export function withLifecycleCodec<K,I,O>(m:LifecycleMonitor<K,I,O>,key:ValueSchema<K>){const schema=lifecycleHistorySchema(m,key);return Object.assign(m,{encodeHistory:(h:LifecycleHistory<K>)=>encodeValue(schema,h,0xffffffffffffffffn),decodeHistory:(bytes:Uint8Array)=>decodeValue(schema,bytes)});}
const legacyLifecycleMessages:Record<string,string>={"time_backwards": "logical time moved backwards", "missing_before_advance": "required settlement missing before time advance", "key_reused": "correlation key reused", "began_after_deadline": "obligation began after its deadline", "cancel_without_obligation": "cancellation without obligation", "settlement_after_cancel": "settlement after cancellation", "duplicate_settlement": "duplicate settlement", "settlement_after_deadline": "settlement after deadline", "settlement_without_obligation": "settlement without obligation", "stale_or_premature_publication": "stale or premature publication", "resource_already_acquired": "resource already acquired", "release_before_settlement": "resource released before settlement", "duplicate_release": "duplicate resource release", "release_without_acquisition": "release without acquisition", "missing_in_prior_history": "required settlement missing in prior history", "missing_at_deadline": "required settlement missing at deadline"};
