import {ModelError,scalar,validateMetadata,enumerateStatesExact,type Check,type Transition,type Metadata,type EnumerateModel,type ExactSearchConfig,type ExactSearchReport,type SearchLimits} from './core.js';
import {fuzz,fuzzAuto,type AutoOptions,type GenerateModel,type FuzzConfig,type FuzzReport} from './search.js';
import {fuzzGuided,fuzzGuidedAuto,validateGuidanceConfig,GUIDANCE_ALGORITHM,type GuidanceConfig,type Feedback,type GuidedFuzzReport} from './guided.js';
import type {Count} from './counts.js';
import type {SearchObserver,RunLimits} from './search_support.js';
import {strictParse,fields,array,string} from './json.js';
import {validateCampaignConfig,CampaignControl,runCallerCampaign,type CampaignConfig,type CampaignLimits,type CampaignFactory,type CampaignModel,type AutoCampaignModel,type CampaignFeedback,type CampaignSink,type CampaignReport} from './campaign.js';

/** Independent declared requirements; these do not themselves establish coverage. */
export const ASSURANCE_VERSION = 1;
export type AssurancePhase = 'State' | 'Transition';
export interface AssuranceProperty {readonly id:string; readonly phase:AssurancePhase}
export type AssuranceFamilyCoverage = {readonly kind:'Required'} | {readonly kind:'Excluded'; readonly reason:string};
export interface AssuranceFamily {readonly name:string; readonly coverage:AssuranceFamilyCoverage}
export interface AssuranceGoal {readonly id:string; readonly scope:'Transition'|'History'}
export interface AssuranceManifest {
 readonly assuranceVersion:number; readonly model:string;
 readonly requiredProperties:readonly AssuranceProperty[]; readonly inputFamilies:readonly AssuranceFamily[];
 readonly bounds:Readonly<Record<string,string>>; readonly coverGoals:readonly AssuranceGoal[];
 readonly requiredSearchOutcome:'GraphExhausted'|'Completed';
 readonly provenance:{readonly manifest:string;readonly model:string;readonly properties:string};
}

export type AssuranceOutcome='GraphExhausted'|'CasesCompleted'|'DepthLimited'|'BudgetLimited'|'Interrupted'|'TimedOut'|'Failed'|'NotStarted';
export interface AssuranceCounts {passed:bigint;failed:bigint;skipped:bigint;missing:bigint}
export interface AssuranceReport {
 manifest:AssuranceManifest;metadata:Metadata;outcome:AssuranceOutcome;termination:string;budgets:Record<string,string>;checkedTransitions:bigint;unclassifiedTransitions:bigint;
 properties:{property:AssuranceProperty;status:'Passed'|'Failed'|'Skipped'|'Missing';counts:AssuranceCounts}[];
 families:{name:string;status:'Observed'|'Missing'|'Excluded'|'ExclusionViolated';reason?:string}[];
 goals:{goal:AssuranceGoal;status:'Observed'|'UnreachableUnderDeclaredModel'|'NotReachedBeforeStop'}[];
 observedProperties:{property:AssuranceProperty;counts:AssuranceCounts}[];observedFamilies:string[];observedGoals:string[];callbackErrors:string[];
}
export interface AssuranceHits {family(name:string):void;goal(id:string):void}
const MAX=(1n<<64n)-1n;
const plus=(n:bigint)=>n<MAX?n+1n:MAX;
const propertyKey=(phase:AssurancePhase,id:string)=>JSON.stringify([phase,id]);
const emptyCounts=():AssuranceCounts=>({passed:0n,failed:0n,skipped:0n,missing:0n});
const utf8=new TextEncoder();
function rustDebugString(value:string):string{scalar(value);let result='"';for(const c of value){const n=c.codePointAt(0)!;result+=c==='"'?'\\"':c==='\\'?'\\\\':c==='\n'?'\\n':c==='\r'?'\\r':c==='\t'?'\\t':n===0?'\\0':n<32||n===127?`\\u{${n.toString(16)}}`:c;}return result+'"';}
function campaignTerminationKind(termination:string):string|null{if(!termination.startsWith('Campaign::'))return null;const rest=termination.slice(10);if(['JobsCompleted','FailureFound','Cancelled','Deadline','CallbackError','WorkerError'].includes(rest))return rest;const match=/^(CallbackError|WorkerError)\(ModelError\("(?:[^"\\\x00-\x1f\x7f]|\\(?:["\\nrt0]|u\{[0-9a-f]{1,6}\}))*"\)\)$/.exec(rest);return match?.[1]??null;}
function rustStringOrder(a:string,b:string):number{const x=utf8.encode(a),y=utf8.encode(b),n=Math.min(x.length,y.length);for(let i=0;i<n;i++)if(x[i]!==y[i])return x[i]-y[i];return x.length-y.length;}
function propertyOrder(a:AssuranceProperty,b:AssuranceProperty):number{return rustStringOrder(a.id,b.id)||(a.phase===b.phase?0:a.phase==='State'?-1:1);}
export interface AssuranceObservationLimits{readonly maxProperties?:number;readonly maxErrors?:number;readonly maxHitsPerTransition?:number;readonly maxTextBytes?:number;readonly maxManifestEntries?:number;readonly maxManifestBytes?:number}
const DEFAULT_ASSURANCE_OBSERVATION_LIMITS=Object.freeze({maxProperties:10000,maxErrors:1000,maxHitsPerTransition:1000,maxTextBytes:4096,maxManifestEntries:10000,maxManifestBytes:4194304});
export function snapshotAssuranceObservationLimits(limits:AssuranceObservationLimits={}):Required<AssuranceObservationLimits>{const owned={maxProperties:limits.maxProperties??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxProperties,maxErrors:limits.maxErrors??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxErrors,maxHitsPerTransition:limits.maxHitsPerTransition??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxHitsPerTransition,maxTextBytes:limits.maxTextBytes??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxTextBytes,maxManifestEntries:limits.maxManifestEntries??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxManifestEntries,maxManifestBytes:limits.maxManifestBytes??DEFAULT_ASSURANCE_OBSERVATION_LIMITS.maxManifestBytes};for(const n of Object.values(owned))if(!Number.isSafeInteger(n)||n<0)throw new ModelError('invalid assurance observation limits');return Object.freeze(owned);}
export function admitAssuranceManifest(manifest:AssuranceManifest,limits:AssuranceObservationLimits={}):AssuranceManifest{
 const cap=snapshotAssuranceObservationLimits(limits);if(!manifest||typeof manifest!=='object')throw new ModelError('invalid assurance manifest catalog');const {assuranceVersion,model,requiredProperties:properties,inputFamilies:families,bounds:sourceBounds,coverGoals:goals,requiredSearchOutcome,provenance:sourceProvenance}=manifest;
 if(!Array.isArray(properties)||!Array.isArray(families)||!Array.isArray(goals)||!sourceBounds||typeof sourceBounds!=='object'||Array.isArray(sourceBounds)||!sourceProvenance)throw new ModelError('invalid assurance manifest catalog');
 const pn=properties.length,fn=families.length,gn=goals.length;let entries=pn+fn+gn,bytes=0;if(entries>cap.maxManifestEntries)throw new ModelError('assurance manifest entry limit');
 const text=<T>(v:T):T=>{if(typeof v==='string'){if(v.length>cap.maxManifestBytes-bytes)throw new ModelError('assurance manifest byte limit');bytes+=utf8.encode(v).length;if(bytes>cap.maxManifestBytes)throw new ModelError('assurance manifest byte limit');}return v;};
 const provenance={manifest:text(sourceProvenance.manifest),model:text(sourceProvenance.model),properties:text(sourceProvenance.properties)},requiredProperties:AssuranceProperty[]=[],inputFamilies:AssuranceFamily[]=[],coverGoals:AssuranceGoal[]=[];
 for(let i=0;i<pn;i++){const p=properties[i];if(!p||typeof p!=='object')throw new ModelError('invalid assurance property');const id=text(p.id),phase=p.phase;requiredProperties.push({id,phase});}
 for(let i=0;i<fn;i++){const f=families[i];if(!f||typeof f!=='object')throw new ModelError('invalid assurance family');const name=text(f.name),coverage=f.coverage;if(!coverage||typeof coverage!=='object')throw new ModelError('invalid input family coverage');const kind=coverage.kind;if(kind==='Required')inputFamilies.push({name,coverage:{kind}});else if(kind==='Excluded')inputFamilies.push({name,coverage:{kind,reason:text(coverage.reason)}});else throw new ModelError('invalid input family coverage');}
 for(let i=0;i<gn;i++){const g=goals[i];if(!g||typeof g!=='object')throw new ModelError('invalid assurance goal');const id=text(g.id),scope=g.scope;coverGoals.push({id,scope});}
 const bounds:Record<string,string>=Object.create(null);for(const key in sourceBounds)if(Object.hasOwn(sourceBounds,key)){if(++entries>cap.maxManifestEntries)throw new ModelError('assurance manifest entry limit');text(key);bounds[key]=text(sourceBounds[key]);}
 return validateAssuranceManifestOwned({assuranceVersion,model:text(model),requiredProperties,inputFamilies,bounds,coverGoals,requiredSearchOutcome,provenance});
}
function safeMessage(error:unknown):string{try{const result=error instanceof Error?String(error.message):String(error);scalar(result);return result;}catch{return 'unprintable callback error';}}

/** Search observer counts actual checks and coverage; callback failure cannot certify partial hits. */
export class AssuranceObserver<S,I,O> implements SearchObserver<S,I,O> {
 readonly #manifest:AssuranceManifest;readonly #metadata:Metadata;
 readonly #coverage:(before:S,input:I,after:Transition<S,O>,history:readonly I[],hits:AssuranceHits)=>void;
 readonly #properties=new Map<string,{property:AssuranceProperty;counts:AssuranceCounts}>();
 readonly #families=new Set<string>();readonly #goals=new Set<string>();readonly #errors=new Set<string>();
 readonly #limits:Required<AssuranceObservationLimits>;#busy=false;#overflow=false;#reentered=false;#transitions=0n;#unclassified=0n;
 constructor(manifest:AssuranceManifest,metadata:Metadata,coverage:(before:S,input:I,after:Transition<S,O>,history:readonly I[],hits:AssuranceHits)=>void,limits:AssuranceObservationLimits={}){
  this.#limits=snapshotAssuranceObservationLimits(limits);
  this.#manifest=admitAssuranceManifest(manifest,this.#limits);const identity=validateMetadata(metadata);if(identity.name!==this.#manifest.model)throw new ModelError('assurance manifest model does not match actual model metadata');if(typeof coverage!=='function')throw new ModelError('assurance coverage callback required');this.#metadata=identity;this.#coverage=coverage;
 }
 #text(value:unknown):value is string{try{scalar(value);return value.length<=this.#limits.maxTextBytes&&utf8.encode(value).length<=this.#limits.maxTextBytes;}catch{return false;}}
 #error(value:string){if(!this.#text(value)||(!this.#errors.has(value)&&this.#errors.size>=this.#limits.maxErrors)){this.#overflow=true;return;}this.#errors.add(value);}
 #enter(){if(this.#busy){this.#overflow=true;this.#reentered=true;throw new ModelError('assurance observer reentry');}this.#busy=true;}
 needsHistory(){if(this.#busy){this.#overflow=true;this.#reentered=true;throw new ModelError('assurance observer reentry');}return this.#manifest.coverGoals.some(g=>g.scope==='History');}
 #checks(phase:AssurancePhase,checks:readonly Check[],complete:boolean){const emitted=new Set<string>();for(const c of checks){if(!this.#text(c.id)){this.#overflow=true;continue;}
  // Rust Observer callbacks are infallible; an unknown status is recorded as a callback error (never certifying) instead of throwing into the search.
  if(c.status!=='passed'&&c.status!=='failed'&&c.status!=='skipped'){this.#error(`invalid check status in ${phase} batch: ${c.id}`);continue;}
  if(emitted.has(c.id))this.#error(`ambiguous duplicate property ID in ${phase} batch: ${c.id}`);if(!emitted.has(c.id)&&emitted.size>=this.#limits.maxProperties){this.#overflow=true;continue;}emitted.add(c.id);const key=propertyKey(phase,c.id);if(!this.#properties.has(key)&&this.#properties.size>=this.#limits.maxProperties){this.#overflow=true;continue;}const entry=this.#properties.get(key)??{property:{phase,id:c.id},counts:emptyCounts()};entry.counts[c.status]=plus(entry.counts[c.status]);this.#properties.set(key,entry);}if(complete)for(const p of this.#manifest.requiredProperties)if(p.phase===phase&&!emitted.has(p.id)){const key=propertyKey(phase,p.id);if(!this.#text(p.id)||!this.#properties.has(key)&&this.#properties.size>=this.#limits.maxProperties){this.#overflow=true;continue;}const entry=this.#properties.get(key)??{property:{...p},counts:emptyCounts()};entry.counts.missing=plus(entry.counts.missing);this.#properties.set(key,entry);}}
 initial(_state:S,checks:readonly Check[]){this.#enter();try{this.#checks('State',checks,true);}finally{this.#busy=false;}}
 checkError(phase:'initial_state'|'state'|'transition',checks:readonly Check[],complete:boolean){this.#enter();try{this.#checks(phase==='transition'?'Transition':'State',checks,complete);}finally{this.#busy=false;}}
 transition(before:S,input:I,after:Transition<S,O>,stateChecks:readonly Check[],transitionChecks:readonly Check[],history:readonly I[]){
  this.#enter();try{this.#checks('State',stateChecks,true);this.#checks('Transition',transitionChecks,true);this.#transitions=plus(this.#transitions);const families=new Set<string>(),goals=new Set<string>();let failed=false;
  let active=true;const hit=(set:Set<string>,value:string)=>{if(!active)throw new ModelError('assurance coverage lease expired');if(!this.#text(value)||!set.has(value)&&families.size+goals.size>=this.#limits.maxHitsPerTransition){this.#overflow=true;failed=true;throw new ModelError('assurance coverage hit limit');}set.add(value);};
  try{const value:unknown=this.#coverage(before,input,after,history,{family:name=>hit(families,name),goal:id=>hit(goals,id)});if(value!==undefined){if(value instanceof Promise)void value.catch(()=>{});throw new ModelError('coverage callback must return synchronously');}}catch(e){failed=true;this.#error(`coverage callback: ${safeMessage(e)}`);}finally{active=false;}if(failed||this.#reentered){this.#unclassified=plus(this.#unclassified);return;}
  let classified=false;for(const name of families){if(this.#manifest.inputFamilies.some(f=>f.name===name)){classified=true;this.#families.add(name);}else this.#error(`undeclared input family: ${name}`);}if(!classified)this.#unclassified=plus(this.#unclassified);
  for(const id of goals){if(this.#manifest.coverGoals.some(g=>g.id===id))this.#goals.add(id);else this.#error(`undeclared cover goal: ${id}`);}
  }finally{this.#busy=false;}
 }
 finish(outcome:AssuranceOutcome,termination:string,error?:unknown):AssuranceReport {
  if(this.#busy){this.#overflow=true;this.#reentered=true;throw new ModelError('assurance observer reentry');}const allowed:Record<AssuranceOutcome,readonly string[]>={GraphExhausted:['graph_exhausted'],CasesCompleted:['cases_completed'],DepthLimited:['depth_bound'],BudgetLimited:['state_limit','transition_limit','estimated_state_byte_limit'],Interrupted:['cancelled'],TimedOut:['deadline'],Failed:['failure_found','ModelError','Panicked'],NotStarted:['not_started']};
  if(!allowed[outcome]?.includes(termination))throw new ModelError('inconsistent assurance outcome and termination');
  if(error!==undefined)this.#error(safeMessage(error));const errors=new Set(this.#errors);if(this.#overflow)errors.add('assurance observation incomplete: limit or reentry');
  const manifest=validateAssuranceManifestOwned(this.#manifest),observedProperties=[...this.#properties.values()].map(x=>({property:{...x.property},counts:{...x.counts}})).sort((a,b)=>propertyOrder(a.property,b.property));
  const {properties,families,goals}=derived(manifest,observedProperties,[...this.#families],[...this.#goals],outcome==='GraphExhausted'&&!errors.size);
  return {manifest,metadata:{...this.#metadata},outcome,termination,budgets:{},checkedTransitions:this.#transitions,unclassifiedTransitions:this.#unclassified,properties,families,goals,observedProperties,observedFamilies:[...this.#families].sort(rustStringOrder),observedGoals:[...this.#goals].sort(rustStringOrder),callbackErrors:[...errors].sort(rustStringOrder)};
 }
 /** Assess internally derived evidence, never a caller-modifiable report. */
 assess(outcome:AssuranceOutcome,termination:string,error?:unknown):{adequate:boolean;reasons:string[];confidence:string}{
  return assessDerived(this.finish(outcome,termination,error));
 }
}
export interface AssuranceAssessment{adequate:boolean;reasons:string[];confidence:string}
export interface AssuranceSnapshotLimits{readonly maxEntries?:number;readonly maxTextBytes?:number;readonly maxDepth?:number}
/** Snapshot caller-owned report structures once, with aggregate structural and UTF-8 budgets. */
function boundedSnapshotter(limits:AssuranceSnapshotLimits={}){
 const maxEntries=limits.maxEntries??100000,maxTextBytes=limits.maxTextBytes??8*1024*1024,maxDepth=limits.maxDepth??64;for(const n of [maxEntries,maxTextBytes,maxDepth])if(!Number.isSafeInteger(n)||n<0)throw new ModelError('invalid assurance snapshot limits');let entries=0,bytes=0;const active=new WeakSet<object>();
 const text=(v:string)=>{scalar(v);if(v.length>maxTextBytes-bytes)throw new ModelError('assurance snapshot byte limit');bytes+=utf8.encode(v).length;if(bytes>maxTextBytes)throw new ModelError('assurance snapshot byte limit');};
 const copy=(value:any,depth:number):any=>{if(depth>maxDepth)throw new ModelError('assurance snapshot depth limit');if(++entries>maxEntries)throw new ModelError('assurance snapshot entry limit');if(typeof value==='string'){text(value);return value;}if(value===null||typeof value==='boolean'||typeof value==='number'||typeof value==='bigint'||value===undefined)return value;if(typeof value!=='object')throw new ModelError('invalid assurance snapshot value');if(active.has(value))throw new ModelError('cyclic assurance snapshot');active.add(value);try{
  if(Array.isArray(value)){const n=value.length;if(n>maxEntries-entries)throw new ModelError('assurance snapshot entry limit');const result:any[]=[];for(let i=0;i<n;i++)result.push(copy(value[i],depth+1));return result;}
  if(value instanceof Map){if(value.size>maxEntries-entries)throw new ModelError('assurance snapshot entry limit');const result=new Map();for(const [k,v] of value)result.set(copy(k,depth+1),copy(v,depth+1));return result;}
  const prototype=Object.getPrototypeOf(value);if(prototype!==Object.prototype&&prototype!==null)throw new ModelError('invalid assurance snapshot object');const result:Record<string,unknown>=Object.create(null);for(const key in value)if(Object.hasOwn(value,key)){text(key);result[key]=copy(value[key],depth+1);}return result;
 }finally{active.delete(value);}};return <T>(value:T):T=>copy(value,0) as T;
}
function assessDerived(r:AssuranceReport):AssuranceAssessment{
  const outcome=r.outcome,reasons:string[]=[];
  if(!r.manifest.requiredProperties.length)reasons.push('no expected properties declared');
  if(!r.manifest.inputFamilies.some(f=>f.coverage.kind==='Required'))reasons.push('no required input families declared');
  for(const p of r.properties)if(p.status!=='Passed')reasons.push(`property ${p.property.id} ${p.property.phase}: ${p.status}`);
  if(r.observedProperties.some(p=>p.counts.failed||p.counts.skipped||p.counts.missing))reasons.push('observed failed, skipped, or omitted required checks');
  for(const f of r.families)if(f.status==='Missing'||f.status==='ExclusionViolated')reasons.push(`input family ${f.name}: ${f.status}`);
  for(const g of r.goals)if(g.status!=='Observed')reasons.push(`cover goal ${g.goal.id}: ${g.status}`);
  if(r.unclassifiedTransitions)reasons.push('some checked inputs had no declared family classification');
  if(r.callbackErrors.length)reasons.push('model or observation callback error');
  const complete=r.manifest.requiredSearchOutcome==='GraphExhausted'?outcome==='GraphExhausted':outcome==='GraphExhausted'||outcome==='CasesCompleted';
  if(!complete)reasons.push(`required search outcome not met: ${outcome}`);
  const adequate=!reasons.length,confidence=adequate?(outcome==='GraphExhausted'?'The declared properties held for the exhausted finite model and declared adequacy goals were observed.':'The declared properties held for the completed sampled cases and declared adequacy goals were observed.'):'Declared assurance requirements are not fully satisfied; no broader adequacy claim is established.';
  return {adequate,reasons,confidence};
}
function invalidAssessment(error:unknown):AssuranceAssessment{return {adequate:false,reasons:[`inconsistent assurance report: ${safeMessage(error)}`],confidence:'The report is internally inconsistent; no adequacy claim is established.'};}
/** Assessment of one job rejects aggregate outcomes without their constituent proof. */
export function assessAssuranceReport(report:AssuranceReport,limits:AssuranceSnapshotLimits={}):AssuranceAssessment{try{const owned=boundedSnapshotter(limits)(report);validateAssuranceReport(owned);requireIndividualReport(owned);return assessDerived(owned);}catch(error){return invalidAssessment(error);}}
/** Assessment of merged evidence reconstructs and validates all job and campaign accounting first. */
export function assessMergedAssurance(merged:MergedAssurance,limits:AssuranceJSONLimits={}):AssuranceAssessment{try{const budget=wireSnapshotLimits(limits.maxBytes??8*1024*1024,limits.maxEntries??100000);return assessDerived(parseMergedAssuranceJSON(mergedAssuranceJSON(merged,budget),limits).report);}catch(error){return invalidAssessment(error);}}

export type AssuranceCoverage<S,I,O>=(before:S,input:I,after:Transition<S,O>,history:readonly I[],hits:AssuranceHits)=>void;
export type AssuredRun<R>={search:{ok:true;value:R}|{ok:false;error:unknown};assurance:AssuranceReport;assessment:{adequate:boolean;reasons:string[];confidence:string}};
/** Internal worker adapter; the model metadata is the coordinator-validated snapshot. */
export function runWorkerAssured<S,I,O,C extends Count>(model:CampaignModel<S,I,O>,actual:Metadata,expected:Metadata,manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,limits:RunLimits,observationLimits:AssuranceObservationLimits={}):AssuredRun<FuzzReport<I,C>>{
 if(canonical(actual)!==canonical(expected))throw new ModelError('campaign assurance executable identity mismatch');
 const observer=new AssuranceObserver(manifest,actual,coverage,observationLimits);return runAssured(observer,()=>fuzz(model,config,limits,observer),r=>fuzzOutcome(r.termination),r=>r.termination,fuzzBudgets(config,limits),true);
}
export function runWorkerAssuredGuided<S,I,O,K,C extends Count>(model:CampaignModel<S,I,O>,actual:Metadata,expected:Metadata,manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,limits:RunLimits,guidance:GuidanceConfig<C>,feedback:Feedback<S,I,O,K>,observationLimits:AssuranceObservationLimits={}):AssuredRun<GuidedFuzzReport<I,C>>{
 if(canonical(actual)!==canonical(expected))throw new ModelError('campaign assurance executable identity mismatch');
 const observer=new AssuranceObserver(manifest,actual,coverage,observationLimits),budgets={...fuzzBudgets(config,limits),...guidanceBudgets(guidance)};
 const result=runAssured(observer,()=>fuzzGuided(model,config,guidance,feedback,limits,observer),r=>fuzzOutcome(r.termination),r=>r.termination,budgets,true);
 if(result.search.ok){const identity=result.search.value.guidance.feedback;result.assurance.budgets['guidance.feedback.name']=identity.name;result.assurance.budgets['guidance.feedback.version']=String(identity.version);result.assurance.budgets['guidance.feedback.build']=identity.build;}return result;
}
const fuzzOutcome=(t:FuzzReport<unknown,Count>['termination']):AssuranceOutcome=>({cases_completed:'CasesCompleted',transition_limit:'BudgetLimited',failure_found:'Failed',deadline:'TimedOut',cancelled:'Interrupted'} as const)[t];
const searchOutcome=(t:ExactSearchReport<unknown>['termination']):AssuranceOutcome=>({graph_exhausted:'GraphExhausted',depth_bound:'DepthLimited',state_limit:'BudgetLimited',transition_limit:'BudgetLimited',estimated_state_byte_limit:'BudgetLimited',failure_found:'Failed',deadline:'TimedOut',cancelled:'Interrupted'} as const)[t];
const isModelError=(value:unknown):boolean=>{try{return value instanceof ModelError;}catch{return false;}};
/** Rust run_campaign: a job that panics (any non-ModelError throw here) retains partial observations as "Panicked". */
const JOB_PANICKED='job panicked; partial observations retained';
function runAssured<S,I,O,R>(observer:AssuranceObserver<S,I,O>,run:()=>R,outcome:(r:R)=>AssuranceOutcome,termination:(r:R)=>string,budgets:Record<string,string>,campaignJob=false):AssuredRun<R>{
 let result:AssuredRun<R>['search'];try{result={ok:true,value:run()};}catch(error){result={ok:false,error};}
 const panicked=!result.ok&&campaignJob&&!isModelError(result.error);
 const state=result.ok?outcome(result.value):'Failed',name=result.ok?termination(result.value):panicked?'Panicked':'ModelError',error=result.ok?undefined:panicked?new ModelError(JOB_PANICKED):result.error;
 const assurance=observer.finish(state,name,error);assurance.budgets={...assurance.budgets,...budgets};return {search:result,assurance,assessment:observer.assess(state,name,error)};
}
/** Rust control_budgets records Duration::as_nanos(); TypeScript limits are milliseconds. */
function durationNanos(ms:number):string{if(typeof ms!=='number'||!Number.isFinite(ms)||ms<0)return String(ms);return (Number.isInteger(ms)?BigInt(ms)*1000000n:BigInt(Math.round(ms*1e6))).toString();}
function controlBudgets(limits:RunLimits):Record<string,string>{const result:Record<string,string>={cancellation_enabled:String(limits.cancellation!==undefined)};if(limits.maxDurationMs!==undefined)result.max_duration_ns=durationNanos(limits.maxDurationMs);return result;}
/** Rust assurance::guided_fuzz budget keys, spelled exactly. */
function guidanceBudgets<C extends Count>(g:GuidanceConfig<C>):Record<string,string>{return {'guidance.algorithm':GUIDANCE_ALGORITHM,'guidance.max_corpus_entries':String(g.maxCorpusEntries),'guidance.max_corpus_inputs':String(g.maxCorpusInputs),'guidance.max_features':String(g.maxFeatures),'guidance.max_features_per_observation':String(g.maxFeaturesPerObservation)};}
function snapshotRunLimits(limits:RunLimits):RunLimits{const duration=limits.maxDurationMs,cancellation=limits.cancellation;return {...(duration===undefined?{}:{maxDurationMs:duration}),...(cancellation===undefined?{}:{cancellation:()=>cancellation.call(limits)})};}
function fuzzBudgets<C extends Count>(c:FuzzConfig<C>,limits:RunLimits):Record<string,string>{return {seed:String(c.seed),cases:String(c.cases),max_steps:String(c.maxSteps),max_transitions:String(c.maxTransitions),mutation_percent:String(c.mutationPercent),...controlBudgets(limits)};}
export function assuredEnumerate<S,I,O>(model:EnumerateModel<S,I,O>&{metadata():Metadata},manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:ExactSearchConfig,limits:SearchLimits={},observationLimits:AssuranceObservationLimits={}):AssuredRun<ExactSearchReport<I>>{
 const observer=new AssuranceObserver(manifest,model.metadata(),coverage,observationLimits),c={maxStates:config.maxStates,maxTransitions:config.maxTransitions,maxDepth:config.maxDepth},control=snapshotRunLimits(limits.control??{}),owned={control,maxEstimatedStateBytes:limits.maxEstimatedStateBytes};
 const budgets:Record<string,string>={max_states:String(c.maxStates),max_transitions:String(c.maxTransitions),max_depth:String(c.maxDepth),...controlBudgets(control)};if(owned.maxEstimatedStateBytes!==undefined)budgets.max_estimated_state_bytes=String(owned.maxEstimatedStateBytes);
 return runAssured(observer,()=>enumerateStatesExact(model,c,owned,observer),r=>searchOutcome(r.termination),r=>r.termination,budgets);
}
export function assuredFuzz<S,I,O,C extends Count>(model:GenerateModel<S,I,O>&{metadata():Metadata},manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,limits:RunLimits={},observationLimits:AssuranceObservationLimits={}):AssuredRun<FuzzReport<I,C>>{
 const observer=new AssuranceObserver(manifest,model.metadata(),coverage,observationLimits),c={seed:config.seed,cases:config.cases,maxSteps:config.maxSteps,maxTransitions:config.maxTransitions,mutationPercent:config.mutationPercent},control=snapshotRunLimits(limits);
 return runAssured(observer,()=>fuzz(model,c,control,observer),r=>fuzzOutcome(r.termination),r=>r.termination,fuzzBudgets(c,control));
}
export function assuredGuidedFuzz<S,I,O,K,C extends Count>(model:GenerateModel<S,I,O>&{metadata():Metadata},manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,guidance:GuidanceConfig<C>,feedback:Feedback<S,I,O,K>,limits:RunLimits={},observationLimits:AssuranceObservationLimits={}):AssuredRun<GuidedFuzzReport<I,C>>{
 const observer=new AssuranceObserver(manifest,model.metadata(),coverage,observationLimits),c={seed:config.seed,cases:config.cases,maxSteps:config.maxSteps,maxTransitions:config.maxTransitions,mutationPercent:config.mutationPercent},g={...guidance},control=snapshotRunLimits(limits);
 const result=runAssured(observer,()=>fuzzGuided(model,c,g,feedback,control,observer),r=>fuzzOutcome(r.termination),r=>r.termination,{...fuzzBudgets(c,control),...guidanceBudgets(g)});
 if(result.search.ok){const metadata=result.search.value.guidance.feedback;result.assurance.budgets['guidance.feedback.name']=metadata.name;result.assurance.budgets['guidance.feedback.version']=String(metadata.version);result.assurance.budgets['guidance.feedback.build']=metadata.build;}return result;
}

/** Assure automatic fuzzing with cooperative scan control. */
export function assuredAutoFuzz<S,I,O,C extends Count>(model:EnumerateModel<S,I,O>&{equalInputs(a:I,b:I):boolean;metadata():Metadata},auto:AutoOptions<Count>,manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,limits:RunLimits={},observationLimits:AssuranceObservationLimits={}):AssuredRun<FuzzReport<I,C>>{
 const observer=new AssuranceObserver(manifest,model.metadata(),coverage,observationLimits),c={seed:config.seed,cases:config.cases,maxSteps:config.maxSteps,maxTransitions:config.maxTransitions,mutationPercent:config.mutationPercent},control=snapshotRunLimits(limits),options={maxCandidates:auto.maxCandidates};
 return runAssured(observer,()=>fuzzAuto(model,options,c,control,observer),r=>fuzzOutcome(r.termination),r=>r.termination,fuzzBudgets(c,control));
}
/** Assure guided automatic fuzzing with cooperative scan control. */
export function assuredGuidedAutoFuzz<S,I,O,K,C extends Count>(model:EnumerateModel<S,I,O>&{equalInputs(a:I,b:I):boolean;metadata():Metadata},auto:AutoOptions<Count>,manifest:AssuranceManifest,coverage:AssuranceCoverage<S,I,O>,config:FuzzConfig<C>,guidance:GuidanceConfig<C>,feedback:Feedback<S,I,O,K>,limits:RunLimits={},observationLimits:AssuranceObservationLimits={}):AssuredRun<GuidedFuzzReport<I,C>>{
 const observer=new AssuranceObserver(manifest,model.metadata(),coverage,observationLimits),c={seed:config.seed,cases:config.cases,maxSteps:config.maxSteps,maxTransitions:config.maxTransitions,mutationPercent:config.mutationPercent},g={...guidance},control=snapshotRunLimits(limits),options={maxCandidates:auto.maxCandidates};
 const result=runAssured(observer,()=>fuzzGuidedAuto(model,options,c,g,feedback,control,observer),r=>fuzzOutcome(r.termination),r=>r.termination,{...fuzzBudgets(c,control),...guidanceBudgets(g)});
 if(result.search.ok){const metadata=result.search.value.guidance.feedback;result.assurance.budgets['guidance.feedback.name']=metadata.name;result.assurance.budgets['guidance.feedback.version']=String(metadata.version);result.assurance.budgets['guidance.feedback.build']=metadata.build;}return result;
}

const canonical=(value:unknown):string=>JSON.stringify(value,(_k,v)=>typeof v==='bigint'?`${v}n`:v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0)):v);
const add=(a:bigint,b:bigint)=>a>MAX-b?MAX:a+b;
function derived(manifest:AssuranceManifest,observed:AssuranceReport['observedProperties'],familyHits:readonly string[],goalHits:readonly string[],exhausted:boolean){
 const counts=new Map(observed.map(v=>[propertyKey(v.property.phase,v.property.id),v.counts]));
 const properties=manifest.requiredProperties.map(p=>{const c={...(counts.get(propertyKey(p.phase,p.id))??emptyCounts())};return {property:{...p},status:(c.failed?'Failed':c.skipped?'Skipped':c.missing?'Missing':c.passed?'Passed':'Missing') as 'Passed'|'Failed'|'Skipped'|'Missing',counts:c};}).sort((a,b)=>propertyOrder(a.property,b.property));
 const families=manifest.inputFamilies.map(f=>({name:f.name,status:(f.coverage.kind==='Required'?(familyHits.includes(f.name)?'Observed':'Missing'):(familyHits.includes(f.name)?'ExclusionViolated':'Excluded')) as 'Observed'|'Missing'|'Excluded'|'ExclusionViolated',...(f.coverage.kind==='Excluded'?{reason:f.coverage.reason}:{})})).sort((a,b)=>rustStringOrder(a.name,b.name));
 const goals=manifest.coverGoals.map(g=>({goal:{...g},status:(goalHits.includes(g.id)?'Observed':exhausted&&g.scope==='Transition'?'UnreachableUnderDeclaredModel':'NotReachedBeforeStop') as 'Observed'|'UnreachableUnderDeclaredModel'|'NotReachedBeforeStop'})).sort((a,b)=>rustStringOrder(a.goal.id,b.goal.id));
 return {properties,families,goals};
}
/** Check derived catalogs against retained observations; this is not attestation. */
export function validateAssuranceReport(report:AssuranceReport):void{
 const m=validateAssuranceManifestOwned(report.manifest);if(!report.metadata)throw new ModelError('assurance report identity or termination is inconsistent');const identity=validateMetadata(report.metadata);if(m.model!==identity.name||typeof report.termination!=='string'||!report.termination.trim())throw new ModelError('assurance report identity or termination is inconsistent');scalar(report.termination);
 const allowed:Record<AssuranceOutcome,readonly string[]>={GraphExhausted:['graph_exhausted','MergedJobs','Campaign::JobsCompleted'],CasesCompleted:['cases_completed','MergedJobs','Campaign::JobsCompleted'],DepthLimited:['depth_bound','MergedJobs','Campaign::JobsCompleted'],BudgetLimited:['state_limit','transition_limit','estimated_state_byte_limit','MergedJobs','Campaign::JobsCompleted'],Interrupted:['cancelled','MergedJobs','Campaign::Cancelled','Campaign::JobsCompleted'],TimedOut:['deadline','MergedJobs','Campaign::Deadline'],Failed:['failure_found','ModelError','Panicked','MergedJobs','Campaign::FailureFound','Campaign::CallbackError','Campaign::WorkerError','Campaign::Cancelled','Campaign::Deadline','Campaign::JobsCompleted'],NotStarted:['not_started','MergedJobs','Campaign::JobsCompleted']};
 const errorKind=campaignTerminationKind(report.termination);if(!allowed[report.outcome]?.includes(report.termination)&&!(report.outcome==='Failed'&&(errorKind==='CallbackError'||errorKind==='WorkerError')))throw new ModelError('inconsistent assurance outcome and termination');
 const uint=(n:unknown)=>typeof n==='bigint'&&n>=0n&&n<=MAX;
 if(!uint(report.checkedTransitions)||!uint(report.unclassifiedTransitions)||report.unclassifiedTransitions>report.checkedTransitions)throw new ModelError('invalid assurance transition counts');
 if(!report.budgets||typeof report.budgets!=='object'||Array.isArray(report.budgets))throw new ModelError('invalid assurance budgets');for(const [key,value] of Object.entries(report.budgets)){scalar(key);scalar(value);}
 if(!Array.isArray(report.observedProperties)||!Array.isArray(report.observedFamilies)||!Array.isArray(report.observedGoals)||!Array.isArray(report.callbackErrors))throw new ModelError('invalid assurance observations');for(const value of [...report.observedFamilies,...report.observedGoals,...report.callbackErrors])scalar(value);
 const keys=new Set<string>();for(const item of report.observedProperties){const p=item?.property,c=item?.counts;if(!p||typeof p.id!=='string'||!p.id.trim()||!['State','Transition'].includes(p.phase)||!c||![c.passed,c.failed,c.skipped,c.missing].every(uint))throw new ModelError('invalid assurance property observation');scalar(p.id);const key=propertyKey(p.phase,p.id);if(keys.has(key))throw new ModelError('duplicate assurance property observation');keys.add(key);}
 if(new Set(report.observedFamilies).size!==report.observedFamilies.length||new Set(report.observedGoals).size!==report.observedGoals.length||report.observedFamilies.some(n=>!m.inputFamilies.some(f=>f.name===n))||report.observedGoals.some(n=>!m.coverGoals.some(g=>g.id===n)))throw new ModelError('assurance coverage evidence contains an undeclared or duplicate identity');
 if(report.checkedTransitions===0n&&(report.observedFamilies.length||report.observedGoals.length))throw new ModelError('assurance coverage has no checked transition evidence');
 const d=derived(m,report.observedProperties,report.observedFamilies,report.observedGoals,report.outcome==='GraphExhausted'&&!report.callbackErrors.length);
 if(canonical(d.properties)!==canonical(report.properties)||canonical(d.families)!==canonical(report.families)||canonical(d.goals)!==canonical(report.goals))throw new ModelError('assurance derived catalog does not match observations');
}
export interface MergedAssurance {report:AssuranceReport;jobs:Map<bigint,AssuranceReport>;missingJobs:bigint[];
 /** TypeScript-only retained-evidence accounting for assured campaigns. Never serialized and never written into Rust-defined budgets. */
 retention?:AssuranceRetention}
export interface AssuranceRetention{readonly maxRetainedReports:number;readonly maxRetainedBytes:number;readonly retainedReports:number;readonly retainedBytes:number;readonly droppedReports:number}
export function mergeAssuranceReports(manifest:AssuranceManifest,metadata:Metadata,expectedJobs:Iterable<bigint>,reports:Iterable<readonly [bigint,AssuranceReport]>,limits:AssuranceSnapshotLimits={}):MergedAssurance{
 const ownedLimits={maxEntries:limits.maxEntries??100000,maxTextBytes:limits.maxTextBytes??8*1024*1024,maxDepth:limits.maxDepth??64},snapshot=boundedSnapshotter(ownedLimits),m=validateAssuranceManifestOwned(snapshot(manifest)),identity=validateMetadata(snapshot(metadata));if(m.model!==identity.name)throw new ModelError('merge model identity mismatch');
 const expected=new Set<bigint>();for(const id of expectedJobs){if(typeof id!=='bigint'||id<0n||id>MAX||expected.has(id))throw new ModelError('invalid or duplicate expected assurance job');if(expected.size>=ownedLimits.maxEntries)throw new ModelError('assurance expected job limit');expected.add(id);}
 const jobs=new Map<bigint,AssuranceReport>();for(const [id,report] of reports){const owned=snapshot(report);validateAssuranceReport(owned);if(!expected.has(id))throw new ModelError('unexpected assurance job');if(jobs.has(id))throw new ModelError('duplicate assurance job report');if(canonical(m)!==canonical(owned.manifest)||canonical(identity)!==canonical(owned.metadata))throw new ModelError('cannot merge different assurance manifests or executable identities');jobs.set(id,owned);}
 const ids=[...expected].sort((a,b)=>a<b?-1:a>b?1:0),missingJobs=ids.filter(id=>!jobs.has(id)),props=new Map<string,{property:AssuranceProperty;counts:AssuranceCounts}>(),families=new Set<string>(),goals=new Set<string>(),errors=new Set<string>();let transitions=0n,unclassified=0n;
 for(const [id,job] of jobs){for(const p of job.observedProperties){const key=propertyKey(p.property.phase,p.property.id),entry=props.get(key)??{property:{...p.property},counts:emptyCounts()};for(const k of ['passed','failed','skipped','missing'] as const)entry.counts[k]=add(entry.counts[k],p.counts[k]);props.set(key,entry);}for(const f of job.observedFamilies)families.add(f);for(const g of job.observedGoals)goals.add(g);for(const e of job.callbackErrors)errors.add(`job ${id}: ${e}`);transitions=add(transitions,job.checkedTransitions);unclassified=add(unclassified,job.unclassifiedTransitions);}
 const values=[...jobs.values()],outcome:AssuranceOutcome=!expected.size||!jobs.size?'NotStarted':values.some(j=>j.outcome==='Failed')?'Failed':missingJobs.length?'Interrupted':values.every(j=>j.outcome==='GraphExhausted')?'GraphExhausted':values.every(j=>j.outcome==='GraphExhausted'||j.outcome==='CasesCompleted')?'CasesCompleted':values.some(j=>j.outcome==='TimedOut')?'TimedOut':values.some(j=>j.outcome==='Interrupted'||j.outcome==='NotStarted')?'Interrupted':values.some(j=>j.outcome==='BudgetLimited')?'BudgetLimited':'DepthLimited';
 const observedProperties=[...props.values()].sort((a,b)=>propertyOrder(a.property,b.property)),observedFamilies=[...families].sort(rustStringOrder),observedGoals=[...goals].sort(rustStringOrder),callbackErrors=[...errors].sort(rustStringOrder),d=derived(m,observedProperties,observedFamilies,observedGoals,outcome==='GraphExhausted'&&!callbackErrors.length);
 const report:AssuranceReport={manifest:m,metadata:identity,outcome,termination:'MergedJobs',budgets:{requested_jobs:String(expected.size),reported_jobs:String(jobs.size)},checkedTransitions:transitions,unclassifiedTransitions:unclassified,...d,observedProperties,observedFamilies,observedGoals,callbackErrors};validateAssuranceReport(report);return {report,jobs,missingJobs};
}

export interface AssuranceCampaignOptions{readonly maxExpectedJobs?:number;readonly observationLimits?:AssuranceObservationLimits;readonly maxRetainedReports?:number;readonly maxRetainedBytes?:number}
/** Bounds serialized retained reports; temporary serialization and callback allocations are separate. */
export class AssuranceReportStore {
 readonly #reports=new Map<bigint,AssuranceReport>();readonly #maxReports:number;readonly #maxBytes:number;#bytes=0;#dropped=0;
 constructor(options:AssuranceCampaignOptions={}){this.#maxReports=options.maxRetainedReports??10000;this.#maxBytes=options.maxRetainedBytes??64*1024*1024;if(!Number.isSafeInteger(this.#maxReports)||this.#maxReports<0||!Number.isSafeInteger(this.#maxBytes)||this.#maxBytes<0)throw new ModelError('invalid retained assurance limits');}
 retain(id:bigint,report:AssuranceReport):void{if(this.#reports.has(id))throw new ModelError('duplicate worker assurance');const remaining=this.#maxBytes-this.#bytes;if(this.#reports.size>=this.#maxReports||remaining===0){this.#dropped++;return;}const doubled=Math.min(Number.MAX_SAFE_INTEGER,remaining*2),snapshotLimit=Math.min(Number.MAX_SAFE_INTEGER,doubled+1024);let json:string;try{json=assuranceReportJSON(report,{maxEntries:snapshotLimit,maxTextBytes:snapshotLimit});}catch(error){if(error instanceof ModelError&&/^assurance snapshot (byte|entry) limit$/.test(error.message)){this.#dropped++;return;}throw error;}const bytes=utf8.encode(json).length;if(bytes>remaining){this.#dropped++;return;}const owned=parseAssuranceReportJSON(json,{maxBytes:bytes,maxEntries:100000});this.#reports.set(id,owned);this.#bytes+=bytes;}
 *entries():Iterable<readonly [bigint,AssuranceReport]>{for(const [id,report] of this.#reports)yield [id,boundedSnapshotter(this.mergeLimits())(report)] as const;}
 mergeLimits(manifestBytes=4*1024*1024,manifestEntries=10000,expectedJobs=100000):AssuranceSnapshotLimits{const plus=(a:number,b:number)=>Math.min(Number.MAX_SAFE_INTEGER,a+b);return {maxEntries:Math.max(100000,expectedJobs,plus(plus(this.#maxBytes,manifestEntries),100000)),maxTextBytes:Math.max(8*1024*1024,plus(this.#maxBytes,manifestBytes)),maxDepth:64};}
 finish(assurance:MergedAssurance):void{const r=assurance.report;assurance.retention=Object.freeze({maxRetainedReports:this.#maxReports,maxRetainedBytes:this.#maxBytes,retainedReports:this.#reports.size,retainedBytes:this.#bytes,droppedReports:this.#dropped});if(this.#dropped){r.callbackErrors=[...new Set([...r.callbackErrors,'assurance retained evidence limit exceeded'])].sort(rustStringOrder);r.goals=derived(r.manifest,r.observedProperties,r.observedFamilies,r.observedGoals,false).goals;}validateAssuranceReport(r);}
}
export function checkAssuranceJobLimit(jobs:Count,options:AssuranceCampaignOptions={}):void{const limit=options.maxExpectedJobs??100000;if(!Number.isSafeInteger(limit)||limit<1||limit>0xffffffff)throw new ModelError('invalid assurance expected job limit');if(BigInt(jobs)>BigInt(limit))throw new ModelError('assurance expected job limit exceeded');}
/** Capture control fields while preserving the callback receiver used by CampaignControl. */
export function snapshotAssuranceCampaignLimits(limits:CampaignLimits):CampaignLimits{const duration=limits.maxDurationMs,cancellation=limits.cancellation;return {...(duration===undefined?{}:{maxDurationMs:duration}),...(cancellation===undefined?{}:{cancellation:typeof cancellation==='function'?()=>cancellation.call(limits):cancellation})};}
async function assuredCampaignInternal<S,I,O,K,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,factory:CampaignFactory<S,I,O>,coverageFactory:(id:bigint,model:CampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,sink:CampaignSink<I,C>,guided?:{guidance:GuidanceConfig<C>;feedbackFactory:(id:bigint,model:CampaignModel<S,I,O>)=>CampaignFeedback<S,I,O,K>},options:AssuranceCampaignOptions={},auto?:AutoOptions<Count>):Promise<{campaign:CampaignReport<C>;assurance:MergedAssurance}>{
 const config=validateCampaignConfig(value),observationLimits=snapshotAssuranceObservationLimits(options.observationLimits),m=admitAssuranceManifest(manifest,observationLimits),identity={...metadata};if(m.model!==identity.name)throw new ModelError('campaign model identity mismatch');
 if(BigInt(config.workers)!==1n)throw new ModelError('multiple campaign workers require an explicit host adapter');
 checkAssuranceJobLimit(config.jobs,options);
 const expected=Array.from({length:Number(config.jobs)},(_,i)=>config.firstJob+BigInt(i)),reports=new AssuranceReportStore(options),ownedLimits=snapshotAssuranceCampaignLimits(limits),control=new CampaignControl(ownedLimits);
 const prepare=(id:bigint,model:CampaignModel<S,I,O>,actual:Metadata)=>{
  if(canonical(actual)!==canonical(identity))throw new ModelError('campaign assurance executable identity mismatch');
  const coverage=coverageFactory(id,model);if(typeof coverage!=='function')throw new ModelError('campaign coverage factory must return a function');
  return new AssuranceObserver(m,actual,coverage,observationLimits);
 };
 const campaign=await runCallerCampaign(config,control,factory,sink,{...(guided?{guidance:guided.guidance,feedbackFactory:guided.feedbackFactory}:{})},{runJob:(id,model,jobConfig,jobLimits,actual)=>{
  const observer=prepare(id,model,actual),run=runAssured(observer,()=>auto?fuzzAuto(model as unknown as AutoCampaignModel<S,I,O>,auto,jobConfig,jobLimits,observer):fuzz(model,jobConfig,jobLimits,observer),r=>fuzzOutcome(r.termination),r=>r.termination,fuzzBudgets(jobConfig,jobLimits),true);reports.retain(id,run.assurance);if(!run.search.ok)throw run.search.error;return run.search.value;
 },runGuidedJob:(id,model,jobConfig,jobLimits,actual,feedback,guidance)=>{
  const observer=prepare(id,model,actual),budgets={...fuzzBudgets(jobConfig,jobLimits),...guidanceBudgets(guidance)};
  const run=runAssured(observer,()=>auto?fuzzGuidedAuto(model as unknown as AutoCampaignModel<S,I,O>,auto,jobConfig,guidance,feedback,jobLimits,observer):fuzzGuided(model,jobConfig,guidance,feedback,jobLimits,observer),r=>fuzzOutcome(r.termination),r=>r.termination,budgets,true);if(run.search.ok){const identity=run.search.value.guidance.feedback;run.assurance.budgets['guidance.feedback.name']=identity.name;run.assurance.budgets['guidance.feedback.version']=String(identity.version);run.assurance.budgets['guidance.feedback.build']=identity.build;}reports.retain(id,run.assurance);if(!run.search.ok)throw run.search.error;return run.search.value;
 }});
 const assurance=finishAssuredCampaign(config,m,identity,expected,reports.entries(),campaign,ownedLimits,reports.mergeLimits(observationLimits.maxManifestBytes,observationLimits.maxManifestEntries,expected.length));reports.finish(assurance);return {campaign,assurance};
}
/** Shared by caller and Node hosts after their coordinator has joined workers. */
export function finishAssuredCampaign<C extends Count>(config:CampaignConfig<C>,m:AssuranceManifest,metadata:Metadata,expected:Iterable<bigint>,reports:Iterable<readonly [bigint,AssuranceReport]>,campaign:CampaignReport<C>,limits:CampaignLimits={},snapshotLimits:AssuranceSnapshotLimits={}):MergedAssurance{
 const assurance=mergeAssuranceReports(m,metadata,expected,reports,snapshotLimits),r=assurance.report,t=campaign.termination;
 Object.assign(r.budgets,{'campaign.master_seed':String(config.masterSeed),'campaign.first_job':String(config.firstJob),'campaign.workers':String(config.workers),'campaign.result_buffer':String(config.resultBuffer),'campaign.stop_on_failure':String(config.stopOnFailure),'campaign.started':String(campaign.started),'campaign.finished':String(campaign.finished),'campaign.delivered':String(campaign.delivered),'campaign.accounting_complete':String(campaign.accountingComplete)});
 r.budgets.cancellation_enabled=String(limits.cancellation!==undefined);if(limits.maxDurationMs!==undefined)r.budgets.max_duration_ns=durationNanos(limits.maxDurationMs);
 if(t.kind==='FailureFound')r.outcome='Failed';else if(t.kind==='CallbackError'||t.kind==='WorkerError'){r.outcome='Failed';r.callbackErrors.push(`campaign completion/delivery error: ${t.error.message}`);}else if(t.kind==='Cancelled'&&r.outcome!=='Failed')r.outcome='Interrupted';else if(t.kind==='Deadline'&&r.outcome!=='Failed')r.outcome='TimedOut';
 if(!campaign.accountingComplete)r.callbackErrors.push('campaign accounting is incomplete');if(BigInt(campaign.delivered)!==BigInt(campaign.requested))r.callbackErrors.push('not every requested campaign report was delivered successfully');
 r.termination=t.kind==='CallbackError'||t.kind==='WorkerError'?`Campaign::${t.kind}(ModelError(${rustDebugString(t.error.message)}))`:`Campaign::${t.kind}`;
 r.callbackErrors=[...new Set(r.callbackErrors)].sort(rustStringOrder);r.goals=derived(r.manifest,r.observedProperties,r.observedFamilies,r.observedGoals,r.outcome==='GraphExhausted'&&!r.callbackErrors.length).goals;validateAssuranceReport(r);return assurance;
}
export function assuredCampaign<S,I,O,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,factory:CampaignFactory<S,I,O>,coverageFactory:(id:bigint,model:CampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}){return assuredCampaignInternal<S,I,O,never,C>(value,limits,manifest,metadata,factory,coverageFactory,sink,undefined,options);}
export function assuredGuidedCampaign<S,I,O,K,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,factory:CampaignFactory<S,I,O>,coverageFactory:(id:bigint,model:CampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,guidance:GuidanceConfig<C>,feedbackFactory:(id:bigint,model:CampaignModel<S,I,O>)=>CampaignFeedback<S,I,O,K>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}){return assuredCampaignInternal(value,limits,manifest,metadata,factory,coverageFactory,sink,{guidance:validateGuidanceConfig(guidance),feedbackFactory},options);}

/** Single-worker assured automatic campaign. */
export function assuredAutoCampaign<S,I,O,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,auto:AutoOptions<Count>,manifest:AssuranceManifest,metadata:Metadata,factory:(id:bigint)=>AutoCampaignModel<S,I,O>,coverageFactory:(id:bigint,model:AutoCampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}){return assuredCampaignInternal<S,I,O,never,C>(value,limits,manifest,metadata,factory as unknown as CampaignFactory<S,I,O>,coverageFactory as unknown as (id:bigint,model:CampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,sink,undefined,options,{maxCandidates:auto.maxCandidates});}
export function assuredGuidedAutoCampaign<S,I,O,K,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,auto:AutoOptions<Count>,manifest:AssuranceManifest,metadata:Metadata,factory:(id:bigint)=>AutoCampaignModel<S,I,O>,coverageFactory:(id:bigint,model:AutoCampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,guidance:GuidanceConfig<C>,feedbackFactory:(id:bigint,model:AutoCampaignModel<S,I,O>)=>CampaignFeedback<S,I,O,K>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}){return assuredCampaignInternal(value,limits,manifest,metadata,factory as unknown as CampaignFactory<S,I,O>,coverageFactory as unknown as (id:bigint,model:CampaignModel<S,I,O>)=>AssuranceCoverage<S,I,O>,sink,{guidance:validateGuidanceConfig(guidance),feedbackFactory:feedbackFactory as unknown as (id:bigint,model:CampaignModel<S,I,O>)=>CampaignFeedback<S,I,O,K>},options,{maxCandidates:auto.maxCandidates});}

// Rust-compatible JSON writer: bigint remains a JSON integer without Number rounding.
function assuranceJSON(value:unknown):string{
 if(typeof value==='bigint')return value.toString();if(value===null)return 'null';if(typeof value==='number'||typeof value==='boolean')return JSON.stringify(value);
 if(typeof value==='string'){let out='"';for(const c of value){const n=c.codePointAt(0)!;if(n>=0xd800&&n<=0xdfff)throw new ModelError('invalid Unicode scalar in assurance export');out+=c==='"'?'\\"':c==='\\'?'\\\\':c==='\n'?'\\n':c==='\r'?'\\r':c==='\t'?'\\t':n<32?`\\u${n.toString(16).padStart(4,'0')}`:c;}return out+'"';}
 if(Array.isArray(value))return `[${value.map(assuranceJSON).join(',')}]`;
 if(value&&typeof value==='object')return `{${Object.entries(value).map(([k,v])=>`${assuranceJSON(k)}:${assuranceJSON(v)}`).join(',')}}`;
 throw new ModelError('invalid assurance JSON value');
}
function manifestWire(m:AssuranceManifest){return {assurance_version:m.assuranceVersion,model:m.model,required_properties:m.requiredProperties.map(p=>({id:p.id,phase:p.phase.toLowerCase()})),input_families:m.inputFamilies.map(f=>({name:f.name,coverage:f.coverage.kind.toLowerCase(),...(f.coverage.kind==='Excluded'?{reason:f.coverage.reason}:{})})),bounds:Object.fromEntries(Object.entries(m.bounds).sort(([a],[b])=>rustStringOrder(a,b))),cover_goals:m.coverGoals.map(g=>({id:g.id,scope:g.scope.toLowerCase()})),required_search_outcome:m.requiredSearchOutcome==='GraphExhausted'?'exhausted_within_declared_bounds':'completed',provenance:{...m.provenance}};}
const wireProperty=(p:AssuranceProperty)=>({id:p.id,phase:p.phase.toLowerCase()});
const wireGoal=(g:AssuranceGoal)=>({id:g.id,scope:g.scope.toLowerCase()});
const wireTermination:Record<string,string>={graph_exhausted:'GraphExhausted',depth_bound:'DepthBound',state_limit:'StateLimit',transition_limit:'TransitionLimit',estimated_state_byte_limit:'EstimatedStateByteLimit',failure_found:'FailureFound',deadline:'Deadline',cancelled:'Cancelled',cases_completed:'CasesCompleted',not_started:'NotStarted',ModelError:'ModelError',MergedJobs:'MergedJobs'};
function reportWire(r:AssuranceReport){return {assurance_version:ASSURANCE_VERSION,manifest:manifestWire(r.manifest),metadata:{name:r.metadata.name,model_version:r.metadata.model_version,properties_version:r.metadata.properties_version,codec_version:r.metadata.codec_version,build:r.metadata.build},search:{outcome:r.outcome,termination:wireTermination[r.termination]??r.termination,budgets:Object.fromEntries(Object.entries(r.budgets).sort(([a],[b])=>rustStringOrder(a,b))),checked_transitions:r.checkedTransitions},properties:r.properties.map(p=>({property:wireProperty(p.property),status:p.status,counts:{...p.counts}})),families:r.families.map(f=>({name:f.name,status:f.status,reason:f.reason??null})),coverage:r.goals.map(g=>({goal:wireGoal(g.goal),status:g.status})),observed_properties:r.observedProperties.map(p=>({property:wireProperty(p.property),counts:{...p.counts}})),observed_families:r.observedFamilies,observed_goals:r.observedGoals,callback_errors:r.callbackErrors,unclassified_transitions:r.unclassifiedTransitions};}
export function assuranceManifestJSON(manifest:AssuranceManifest,limits:AssuranceObservationLimits={}):string{return assuranceJSON(manifestWire(admitAssuranceManifest(manifest,limits)));}
export function assuranceReportJSON(report:AssuranceReport,limits:AssuranceSnapshotLimits={}):string{const owned=boundedSnapshotter(limits)(report);validateAssuranceReport(owned);return assuranceJSON(reportWire(owned));}
export function mergedAssuranceJSON(merged:MergedAssurance,limits:AssuranceSnapshotLimits={}):string{
 const owned=boundedSnapshotter(limits)(merged);validateAssuranceReport(owned.report);const jobs=[...owned.jobs.entries()].sort(([a],[b])=>a<b?-1:a>b?1:0);for(const [,r] of jobs)validateAssuranceReport(r);
 return `{"assurance_version":1,"report":${assuranceJSON(reportWire(owned.report))},"jobs":{${jobs.map(([id,r])=>`${assuranceJSON(String(id))}:${assuranceJSON(reportWire(r))}`).join(',')}},"missing_jobs":${assuranceJSON(owned.missingJobs)}}`;
}
export interface AssuranceJSONLimits{readonly maxBytes?:number;readonly maxEntries?:number}
function wireSnapshotLimits(maxBytes:number,maxEntries:number):AssuranceSnapshotLimits{if(!Number.isSafeInteger(maxBytes)||maxBytes<1||!Number.isSafeInteger(maxEntries)||maxEntries<1)throw new ModelError('invalid assurance JSON limits');const doubled=Math.min(Number.MAX_SAFE_INTEGER,maxBytes*2),entries=Math.min(Number.MAX_SAFE_INTEGER,doubled+maxEntries);return {maxEntries:entries,maxTextBytes:doubled,maxDepth:64};}
function assuranceInput(data:string|Uint8Array,limits:AssuranceJSONLimits){const maxBytes=limits.maxBytes??8*1024*1024,maxEntries=limits.maxEntries??100000;wireSnapshotLimits(maxBytes,maxEntries);return {value:strictParse(data,maxBytes,true),maxEntries,maxBytes};}
const obj=(v:unknown,names:string[],optional:string[]=[])=>fields(v,names,optional);
const small=(v:unknown,max=0xffffffff)=>{if(typeof v!=='bigint'||v<0n||v>BigInt(max))throw new ModelError('invalid assurance integer');return Number(v);};
const wide=(v:unknown)=>{if(typeof v!=='bigint'||v<0n||v>MAX)throw new ModelError('invalid assurance u64');return v;};
const stringMap=(v:unknown,max:number)=>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ModelError('invalid assurance string map');const entries=Object.entries(v);if(entries.length>max)throw new ModelError('assurance entry limit');const out:Record<string,string>=Object.create(null);for(const [k,x] of entries)out[string(k)]=string(x);return out;};
function readProperty(v:unknown):AssuranceProperty{const o=obj(v,['id','phase']),phase=string(o.phase);if(phase!=='state'&&phase!=='transition')throw new ModelError('invalid assurance property phase');return {id:string(o.id),phase:phase==='state'?'State':'Transition'};}
function readGoal(v:unknown):AssuranceGoal{const o=obj(v,['id','scope']),scope=string(o.scope);if(scope!=='transition'&&scope!=='history')throw new ModelError('invalid assurance goal scope');return {id:string(o.id),scope:scope==='transition'?'Transition':'History'};}
function readCounts(v:unknown):AssuranceCounts{const o=obj(v,['passed','failed','skipped','missing']);return {passed:wide(o.passed),failed:wide(o.failed),skipped:wide(o.skipped),missing:wide(o.missing)};}
function readManifest(v:unknown,max:number):AssuranceManifest{
 const o=obj(v,['assurance_version','model','required_properties','input_families','bounds','cover_goals','required_search_outcome','provenance']),provenance=obj(o.provenance,['manifest','model','properties']),outcome=string(o.required_search_outcome);
 if(outcome!=='completed'&&outcome!=='exhausted_within_declared_bounds')throw new ModelError('invalid assurance required outcome');
 const inputFamilies=array(o.input_families,max).map(v=>{const f=obj(v,['name','coverage'],['reason']),kind=string(f.coverage);if(kind==='required'&&f.reason===undefined)return {name:string(f.name),coverage:{kind:'Required' as const}};if(kind==='excluded'&&f.reason!==undefined)return {name:string(f.name),coverage:{kind:'Excluded' as const,reason:string(f.reason)}};throw new ModelError('invalid assurance family coverage');});
 return validateAssuranceManifestOwned({assuranceVersion:small(o.assurance_version),model:string(o.model),requiredProperties:array(o.required_properties,max).map(readProperty),inputFamilies,bounds:stringMap(o.bounds,max),coverGoals:array(o.cover_goals,max).map(readGoal),requiredSearchOutcome:outcome==='completed'?'Completed':'GraphExhausted',provenance:{manifest:string(provenance.manifest),model:string(provenance.model),properties:string(provenance.properties)}});
}
export function parseAssuranceManifestJSON(data:string|Uint8Array,limits:AssuranceJSONLimits={}):AssuranceManifest{const parsed=assuranceInput(data,limits);return readManifest(parsed.value,parsed.maxEntries);}
function readReport(value:unknown,max:number):AssuranceReport{
 const o=obj(value,['assurance_version','manifest','metadata','search','properties','families','coverage','observed_properties','observed_families','observed_goals','callback_errors','unclassified_transitions']);if(small(o.assurance_version)!==ASSURANCE_VERSION)throw new ModelError('unsupported assurance report version');
 const metadata=obj(o.metadata,['name','model_version','properties_version','codec_version','build']),search=obj(o.search,['outcome','termination','budgets','checked_transitions']);
 const wire=string(search.termination),mapped=Object.entries(wireTermination).find(([,v])=>v===wire)?.[0]??wire;
 const r:AssuranceReport={manifest:readManifest(o.manifest,max),metadata:{name:string(metadata.name),model_version:small(metadata.model_version),properties_version:small(metadata.properties_version),codec_version:small(metadata.codec_version),build:string(metadata.build)},outcome:string(search.outcome) as AssuranceOutcome,termination:mapped,budgets:stringMap(search.budgets,max),checkedTransitions:wide(search.checked_transitions),unclassifiedTransitions:wide(o.unclassified_transitions),
 properties:array(o.properties,max).map(v=>{const x=obj(v,['property','status','counts']);return {property:readProperty(x.property),status:string(x.status) as AssuranceReport['properties'][number]['status'],counts:readCounts(x.counts)};}),
 families:array(o.families,max).map(v=>{const x=obj(v,['name','status','reason']);return {name:string(x.name),status:string(x.status) as AssuranceReport['families'][number]['status'],...(x.reason===null?{}:{reason:string(x.reason)})};}),
 goals:array(o.coverage,max).map(v=>{const x=obj(v,['goal','status']);return {goal:readGoal(x.goal),status:string(x.status) as AssuranceReport['goals'][number]['status']};}),
 observedProperties:array(o.observed_properties,max).map(v=>{const x=obj(v,['property','counts']);return {property:readProperty(x.property),counts:readCounts(x.counts)};}),observedFamilies:array(o.observed_families,max).map(string),observedGoals:array(o.observed_goals,max).map(string),callbackErrors:array(o.callback_errors,max).map(string)};
 validateAssuranceReport(r);return r;
}
function requireIndividualReport(report:AssuranceReport):void{if(report.termination==='MergedJobs'||report.termination.startsWith('Campaign::'))throw new ModelError('aggregate assurance requires constituent job proof');}
export function parseAssuranceReportJSON(data:string|Uint8Array,limits:AssuranceJSONLimits={}):AssuranceReport{const parsed=assuranceInput(data,limits),report=readReport(parsed.value,parsed.maxEntries);requireIndividualReport(report);return report;}
export function parseMergedAssuranceJSON(data:string|Uint8Array,limits:AssuranceJSONLimits={}):MergedAssurance{
 const parsed=assuranceInput(data,limits),max=parsed.maxEntries,o=obj(parsed.value,['assurance_version','report','jobs','missing_jobs']);if(small(o.assurance_version)!==ASSURANCE_VERSION)throw new ModelError('unsupported assurance report version');
 const report=readReport(o.report,max);if(!o.jobs||typeof o.jobs!=='object'||Array.isArray(o.jobs))throw new ModelError('invalid assurance jobs');const entries=Object.entries(o.jobs);if(entries.length>max)throw new ModelError('assurance entry limit');
 const jobs=new Map<bigint,AssuranceReport>();for(const [key,value] of entries){if(!/^(0|[1-9][0-9]*)$/.test(key))throw new ModelError('invalid assurance job id');const id=wide(BigInt(key));if(jobs.has(id))throw new ModelError('duplicate assurance job id');const job=readReport(value,max);requireIndividualReport(job);jobs.set(id,job);}
 const missingJobs=array(o.missing_jobs,max).map(wide);if(new Set(missingJobs).size!==missingJobs.length||missingJobs.some(id=>jobs.has(id)))throw new ModelError('duplicate assurance job id');
 const expected=[...jobs.keys(),...missingJobs];if(expected.length>max)throw new ModelError('assurance entry limit');const rebuilt=mergeAssuranceReports(report.manifest,report.metadata,expected,jobs,wireSnapshotLimits(parsed.maxBytes,max)),aggregate=rebuilt.report;
 for(const key of ['checkedTransitions','unclassifiedTransitions','observedProperties','observedFamilies','observedGoals'] as const)if(canonical(report[key])!==canonical(aggregate[key]))throw new ModelError('merged assurance does not match job evidence');
 const expectedMissing=[...missingJobs].sort((a,b)=>a<b?-1:a>b?1:0);if(canonical(missingJobs)!==canonical(expectedMissing))throw new ModelError('noncanonical missing assurance jobs');
 const budgetCount=(key:string):bigint=>{const text=report.budgets[key];if(typeof text!=='string'||!/^(0|[1-9][0-9]*)$/.test(text))throw new ModelError(`invalid assurance accounting: ${key}`);return wide(BigInt(text));};
 const requested=budgetCount('requested_jobs'),reported=budgetCount('reported_jobs');if(requested!==BigInt(expected.length)||reported!==BigInt(jobs.size))throw new ModelError('assurance job accounting does not match constituent jobs');
 if(aggregate.callbackErrors.some(e=>!report.callbackErrors.includes(e)))throw new ModelError('merged assurance omitted job errors');
 if(report.termination==='MergedJobs'){if(report.outcome!==aggregate.outcome||canonical(report.callbackErrors)!==canonical(aggregate.callbackErrors))throw new ModelError('merged assurance outcome does not match jobs');}
 else if(report.termination.startsWith('Campaign::')){const kind=campaignTerminationKind(report.termination),started=budgetCount('campaign.started'),finished=budgetCount('campaign.finished'),delivered=budgetCount('campaign.delivered'),accounting=report.budgets['campaign.accounting_complete'];if(kind===null)throw new ModelError('invalid campaign termination');if(accounting!=='true'&&accounting!=='false')throw new ModelError('invalid campaign accounting completeness');if(started>requested||finished>started||delivered>finished||reported>started)throw new ModelError('campaign accounting counts are inconsistent');if(kind==='JobsCompleted'&&(report.outcome==='CasesCompleted'||report.outcome==='GraphExhausted')&&(missingJobs.length!==0||started!==requested||finished!==requested||delivered!==requested||accounting!=='true'))throw new ModelError('completed campaign lacks complete job and delivery proof');if(accounting==='false'&&!report.callbackErrors.includes('campaign accounting is incomplete'))throw new ModelError('campaign omitted incomplete accounting error');if(delivered!==requested&&!report.callbackErrors.includes('not every requested campaign report was delivered successfully'))throw new ModelError('campaign omitted incomplete delivery error');let outcome=aggregate.outcome;if(kind==='FailureFound'||kind==='CallbackError'||kind==='WorkerError')outcome='Failed';else if(kind==='Cancelled'&&outcome!=='Failed')outcome='Interrupted';else if(kind==='Deadline'&&outcome!=='Failed')outcome='TimedOut';if(report.outcome!==outcome)throw new ModelError('campaign assurance outcome does not match jobs');}
 else throw new ModelError('invalid merged assurance termination');
 return {report,jobs,missingJobs};
}

/** Validate and own the catalog before it is used by a future assured run. */
export function validateAssuranceManifest(source:AssuranceManifest,limits:AssuranceObservationLimits={}):AssuranceManifest{return admitAssuranceManifest(source,limits);}
function validateAssuranceManifestOwned(source:AssuranceManifest):AssuranceManifest {
 if(!source || source.assuranceVersion!==ASSURANCE_VERSION)throw new ModelError('unsupported assurance manifest version');
 const nonempty=(value:unknown)=>{try{scalar(value);return value.trim().length>0;}catch{return false;}};
 const {model,provenance,requiredSearchOutcome}=source;
 if(!nonempty(model)||!provenance||![provenance.manifest,provenance.model,provenance.properties].every(nonempty))throw new ModelError('assurance identity/provenance must not be empty');
 if(requiredSearchOutcome!=='GraphExhausted'&&requiredSearchOutcome!=='Completed')throw new ModelError('invalid required search outcome');
 if(!Array.isArray(source.requiredProperties)||!Array.isArray(source.inputFamilies)||!Array.isArray(source.coverGoals)||!source.bounds||typeof source.bounds!=='object'||Array.isArray(source.bounds))throw new ModelError('invalid assurance manifest catalog');
 const properties=new Set<string>();
 const requiredProperties=source.requiredProperties.map(p=>{if(!p||!nonempty(p.id)||(p.phase!=='State'&&p.phase!=='Transition'))throw new ModelError('empty or invalid required property');const key=JSON.stringify([p.phase,p.id]);if(properties.has(key))throw new ModelError('empty or duplicate required property');properties.add(key);return {id:p.id,phase:p.phase};});
 const families=new Set<string>();
 const inputFamilies=source.inputFamilies.map(f=>{if(!f||!nonempty(f.name)||families.has(f.name))throw new ModelError('empty or duplicate input family');families.add(f.name);if(!f.coverage||(f.coverage.kind!=='Required'&&f.coverage.kind!=='Excluded'))throw new ModelError('invalid input family coverage');if(f.coverage.kind==='Excluded'){if(!nonempty(f.coverage.reason))throw new ModelError('excluded input family needs a rationale');return {name:f.name,coverage:{kind:'Excluded' as const,reason:f.coverage.reason}};}return {name:f.name,coverage:{kind:'Required' as const}};});
 const goals=new Set<string>();
 const coverGoals=source.coverGoals.map(g=>{if(!g||!nonempty(g.id)||goals.has(g.id))throw new ModelError('empty or duplicate cover goal');if(g.scope!=='History'&&g.scope!=='Transition')throw new ModelError('invalid cover goal scope');goals.add(g.id);return {id:g.id,scope:g.scope};});
 const bounds:Record<string,string>=Object.create(null);for(const [key,value] of Object.entries(source.bounds)){if(!nonempty(key)||!nonempty(value))throw new ModelError('environment bound names and assumptions must not be empty');bounds[key]=value;}
 return {assuranceVersion:ASSURANCE_VERSION,model,requiredProperties,inputFamilies,bounds,coverGoals,requiredSearchOutcome,provenance:{manifest:provenance.manifest,model:provenance.model,properties:provenance.properties}};
}
