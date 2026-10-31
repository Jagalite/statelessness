/** Passive payload-free views. Missing observations stay unknown. */
import {Histogram,MetricSnapshot,metricCatalog,metricKind,uint,type Labels,type MetricFamily,type Quantile,type GaugeScope,type MetricValue,type SnapshotTime,type MetricHealth} from './debug_metrics.js';
import type {TraceV2} from './evidence.js';
export const dashboardFamilies:readonly MetricFamily[]=['Requests','Admissions','Attempts','QueueWait','AttemptElapsed','DeliveryLag','CompletionProcessing','Settlement','Unresolved','Running','ReadyDepth','CompletionDepth','ScheduledBackoff','InterAttemptGap','CancellationRequests','CancellationAcknowledgements','Dropped','InvalidMeasurements'];
export interface DashboardRow {family:MetricFamily;labels:Labels;value:MetricValue|null;gaugeScope:GaugeScope|null;p50:Quantile|null;p95:Quantile|null;p99:Quantile|null}
export interface DashboardHealth {metric?:Partial<MetricHealth>;pauseIntervalEvictions?:bigint;abandoned?:bigint;duplicateObservations?:bigint;callbackFaults?:bigint;clockFailures?:bigint;destructorHookLosses?:bigint;invalidLifecycle?:bigint;detailEvictions?:bigint;detailRejections?:bigint;detailEventDrops?:bigint;activeEffectHandles?:bigint;duplicateDeliveries?:bigint;debuggerAffectedMeasurements?:bigint;retainedDetailRecords?:bigint|number;retainedDetailBytes?:bigint|number;effectHandleBytes?:bigint;attemptHandleBytes?:bigint;deliveryHandleBytes?:bigint;unobservedNativeDebuggerInterferencePossible?:boolean}
const metricHealthKeys=['rejectedSeries','invalidLabels','arithmeticErrors','omittedMeasurements','invalidMeasurements','importedIgnored','snapshotsEvicted','snapshotRejections'] as const;
const healthKeys=['detailEventDrops','detailEvictions','detailRejections','clockFailures','callbackFaults','destructorHookLosses','invalidLifecycle','activeEffectHandles','duplicateObservations','duplicateDeliveries','abandoned','pauseIntervalEvictions','debuggerAffectedMeasurements','retainedDetailRecords','retainedDetailBytes','effectHandleBytes','attemptHandleBytes','deliveryHandleBytes'] as const;
const lossKeys=['pauseIntervalEvictions','abandoned','duplicateObservations','callbackFaults','clockFailures','destructorHookLosses','invalidLifecycle','detailEvictions','detailRejections','detailEventDrops'] as const;
const snake=(s:string)=>s.replace(/[A-Z]/g,c=>'_'+c.toLowerCase());
function metricHealth(h:Partial<MetricHealth>){return Object.fromEntries(metricHealthKeys.map(k=>[snake(k),h[k]??0n]));}
const time=(t:SnapshotTime|null)=>t?{clock_domain:t.domain,nanos:t.nanos}:null;
/** Bounded lossless JSON encoder; bigint emits integer tokens, never Number. */
export function boundedJson(value:unknown,maxBytes:number):string {
 if(!Number.isSafeInteger(maxBytes)||maxBytes<0)throw new Error('invalid byte budget');
 const chunks:string[]=[],encoder=new TextEncoder(),active=new Set<object>();let bytes=0;
 const fail=()=>{throw new Error('dashboard byte budget exceeded');};
 const emit=(s:string)=>{if(s.length>maxBytes-bytes)fail();bytes+=encoder.encode(s).length;if(bytes>maxBytes)fail();chunks.push(s);};
 const string=(v:string)=>{if(v.length+2>maxBytes-bytes)fail();emit(JSON.stringify(v));};
 const visit=(v:unknown):void=>{
  if(typeof v==='bigint'){if(v<0n)throw new Error('negative quantity');emit(v.toString());}
  else if(v===null||typeof v==='boolean')emit(JSON.stringify(v));
  else if(typeof v==='string')string(v);
  else if(typeof v==='number'){if(!Number.isSafeInteger(v))throw new Error('invalid numeric quantity');emit(v.toString());}
  else if(v&&typeof v==='object'){
   if(active.has(v))throw new Error('cyclic dashboard value');active.add(v);
   try{if(Array.isArray(v)){emit('[');for(let i=0;i<v.length;i++){if(i)emit(',');if(Object.hasOwn(v,i))visit(v[i]);else emit('null');}emit(']');}
    else{emit('{');let first=true;for(const k of Object.keys(v)){if(!first)emit(',');first=false;string(k);emit(':');visit((v as Record<string,unknown>)[k]);}emit('}');}}
   finally{active.delete(v);}
  }else throw new Error('unsupported JSON value');
 };
 visit(value);return chunks.join('');
}
export class Dashboard {
 readonly #health:DashboardHealth;readonly #metricHealth:MetricHealth;readonly #rows:readonly DashboardRow[];readonly #start:SnapshotTime|null;readonly #end:SnapshotTime|null;
 private constructor(readonly epoch:bigint,readonly revision:bigint,readonly temporality:'cumulative'|'delta',start:SnapshotTime|null,end:SnapshotTime|null,health:DashboardHealth,metricHealth:MetricHealth,rows:readonly DashboardRow[],readonly truncated:boolean,readonly observationComplete:boolean){this.#start=start;this.#end=end;this.#health=health;this.#metricHealth=metricHealth;this.#rows=rows;Object.freeze(this);}
 get start(){return this.#start?{...this.#start}:null;}get end(){return this.#end?{...this.#end}:null;}
 get health():DashboardHealth{return {...this.#health,metric:{...this.#health.metric}};}get metricHealth():MetricHealth{return {...this.#metricHealth};}
 get rows():readonly DashboardRow[]{return this.#rows.map(r=>({...r,labels:{...r.labels},value:r.value instanceof Histogram?r.value.clone():r.value,p50:r.p50?{...r.p50}:null,p95:r.p95?{...r.p95}:null,p99:r.p99?{...r.p99}:null}));}
 static fromSnapshot(s:MetricSnapshot,h:DashboardHealth,labels:Iterable<Labels>,maxRows:number){s=s.clone();const sourceMetric=h.metric,ownedHealth:DashboardHealth={metric:{}};for(const k of healthKeys)Object.assign(ownedHealth,{[k]:h[k]});for(const k of metricHealthKeys)Object.assign(ownedHealth.metric!,{[k]:sourceMetric?.[k]});ownedHealth.unobservedNativeDebuggerInterferencePossible=h.unobservedNativeDebuggerInterferencePossible;h=ownedHealth;for(const k of healthKeys){const n=h[k];if(n===undefined)continue;if(typeof n==='number'){if(!['retainedDetailRecords','retainedDetailBytes'].includes(k)||!Number.isSafeInteger(n)||n<0)throw new Error('invalid health quantity');}else uint(n);}for(const k of metricHealthKeys)uint(h.metric?.[k]??0n);if(h.unobservedNativeDebuggerInterferencePossible!==undefined&&typeof h.unobservedNativeDebuggerInterferencePossible!=='boolean')throw new Error('invalid interference flag');if(!Number.isSafeInteger(maxRows)||maxRows<0)throw new Error('invalid row limit');const rows:DashboardRow[]=[];let truncated=false;for(const l of labels){for(const f of dashboardFamilies){if(rows.length===maxRows){truncated=true;break;}const found=s.get(f,l),value=found instanceof Histogram?found.clone():found??null,q=(n:bigint)=>value instanceof Histogram?value.quantile(s.schema,n,100n):null;const series=[...s.series].find(([,v])=>v.family===f&&v.labels.operation===l.operation&&v.labels.component===l.component&&v.labels.workerPool===l.workerPool&&v.labels.outcome===l.outcome&&v.labels.origin===l.origin&&v.labels.debuggerAffected===l.debuggerAffected);rows.push({family:f,labels:{...l},value,gaugeScope:value!==null&&metricKind(f)==='gauge'?(series?s.gaugeScopes.get(series[0])??s.gaugeScope:s.gaugeScope):null,p50:q(50n),p95:q(95n),p99:q(99n)});}if(truncated)break;}const health:DashboardHealth={metric:{...h.metric}};for(const k of healthKeys)Object.assign(health,{[k]:h[k]??0n});health.unobservedNativeDebuggerInterferencePossible=h.unobservedNativeDebuggerInterferencePossible??true;const complete=s.complete&&!truncated&&metricHealthKeys.every(k=>s.health[k]===0n&&(h.metric?.[k]??0n)===0n)&&lossKeys.every(k=>(h[k]??0n)===0n);return new Dashboard(s.epoch,s.revision,s.temporality,s.start?{...s.start}:null,s.end?{...s.end}:null,health,{...s.health},rows,truncated,complete);}
 toJson(maxBytes:number){
  const catalog=new Map(metricCatalog().map(d=>[d.family,d.name])),health:Record<string,unknown>={};
  for(const k of healthKeys)health[k==='detailEventDrops'?'dropped_detail_events':snake(k)]=this.#health[k]??0n;
  Object.assign(health,metricHealth(this.#metricHealth));health.unobserved_native_debugger_interference_possible=this.#health.unobservedNativeDebuggerInterferencePossible??true;health.host_metric_health=metricHealth(this.#health.metric??{});
  const q=(v:Quantile|null)=>v?{upper_bound_ns:v.upperBoundNs,overflow_bucket:v.upperBoundNs===null,observations:v.observations,low_sample_warning:v.lowSampleWarning}:null;
  const header=boundedJson({schema:'statelessness.effect-dashboard/1',epoch:this.epoch,revision:this.revision,temporality:this.temporality,start:time(this.#start),end:time(this.#end),truncated:this.truncated,observation_complete:this.observationComplete,health},maxBytes);
  const chunks:string[]=[];let used=0;const emit=(v:string)=>{used+=new TextEncoder().encode(v).length;if(used>maxBytes)throw new Error('dashboard byte budget exceeded');chunks.push(v);};
  emit(header.slice(0,-1));emit(',"rows":[');
  for(let i=0;i<this.#rows.length;i++){if(i)emit(',');const r=this.#rows[i];emit(boundedJson({metric:catalog.get(r.family)!,operation:r.labels.operation,component:r.labels.component,worker_pool:r.labels.workerPool,outcome:r.labels.outcome,measurement_origin:r.labels.origin,debugger_affected:r.labels.debuggerAffected,value:r.value instanceof Histogram?{count:r.value.count,sum_ns:r.value.sumNs}:r.value,gauge_scope:r.gaugeScope,p50:q(r.p50),p95:q(r.p95),p99:q(r.p99)},maxBytes-used));}
  emit(']}');return chunks.join('');
 }
}
export interface RequestOrigin {run:bigint;epoch:bigint;machine:bigint;transitionSequence:bigint;outputIndex:number}
export interface CorrelatableTimeline {origin:RequestOrigin;effect:{run:bigint;epoch:bigint};complete:boolean}
export type Correlation={kind:'retained';localSequence:number;output:number;detailComplete:boolean}|{kind:'before_checkpoint';origin:RequestOrigin}|{kind:'missing_identity'}|{kind:'conflict';reason:string};
export function correlate(e:TraceV2,t:CorrelatableTimeline):Correlation {const o=t.origin;if(!e.provenance.run)return {kind:'missing_identity'};for(const n of [o.run,o.epoch,o.machine,o.transitionSequence,t.effect.run,t.effect.epoch])uint(n);if(!Number.isInteger(o.outputIndex)||o.outputIndex<0||o.outputIndex>0xffffffff)throw new Error('invalid output identity');if(t.effect.run!==o.run||t.effect.epoch!==o.epoch||e.provenance.run!==o.run.toString()||e.provenance.epoch!==o.epoch)return {kind:'conflict',reason:'run or epoch'};if(!o.transitionSequence)return {kind:'conflict',reason:'zero transition sequence'};if(o.transitionSequence<=e.checkpointAfter)return {kind:'before_checkpoint',origin:{...o}};const index=o.transitionSequence-e.checkpointAfter-1n;if(index>=BigInt(e.steps.length))return {kind:'conflict',reason:'sequence outside evidence'};const i=Number(index),step=e.steps[i];if(step.causal.machine===null)return {kind:'missing_identity'};if(step.causal.machine!==o.machine.toString())return {kind:'conflict',reason:'machine'};if(step.sequence!==o.transitionSequence||!e.trace.steps[i]||o.outputIndex>=e.trace.steps[i].outputs.length)return {kind:'conflict',reason:'output outside evidence'};return {kind:'retained',localSequence:i+1,output:o.outputIndex,detailComplete:t.complete};}
