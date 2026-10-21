/** Optional offline debugger. Stored views never execute model callbacks. */
import { ModelError, ConfigError, natural, validateRecording, type CodecModel, type Check, type Violation } from './core.js';
import { type Artifact, type EvidenceLimitOptions, type StepEvidence, artifactTrace, readArtifact, writeTraceV2, checkPhase, resolveEvidenceLimits } from './evidence.js';
import { writeAuditTrace } from './binary.js';
import { replayWithObservationsBounded, type ReplayOptions, type ReplayObserver } from './replay.js';
import { shrink, type GenerateModel, type ShrinkConfig, type ShrinkLimits, type ShrinkReport } from './search.js';
import { runControl } from './search_support.js';

export interface EncodedPreview { readonly bytes: Uint8Array; readonly totalBytes: number; readonly complete: boolean; readonly provenance: 'stored-exact-encoding' }
export interface EvidenceBoundary {
 readonly sequence: number; readonly hostSequence: bigint; readonly before: Uint8Array; readonly after: Uint8Array;
 readonly input: Uint8Array | null; readonly outputs: readonly Uint8Array[]; readonly checks: readonly Check[]; readonly causal: StepEvidence | null;
}
/** Policy labels bind provenance only; the host must provide the original implementation. */
export type InputPolicy<S,I> = { readonly label: string; readonly kind: 'unrestricted' } | {
 readonly label: string; readonly kind: 'declared'; readonly permits:(state:S,input:I)=>boolean;
} | {
 readonly label: string; readonly kind: 'enumerated'; readonly candidates: (state:S)=>Iterable<I>; readonly equal: (left:I,right:I)=>boolean;
};
const U64=(1n<<64n)-1n;
export class EvidenceViewer {
 readonly #bytes: Uint8Array;
 readonly #limits: ReturnType<typeof resolveEvidenceLimits>;
 readonly #artifact: Artifact;
 #position=0;
 readonly checkpointAfter: bigint;
 constructor(artifact:Artifact, limits:EvidenceLimitOptions={}) {
  this.#limits=resolveEvidenceLimits(limits);
  this.#bytes=artifact.kind==='v1'?writeAuditTrace(artifact.trace,this.#limits.trace):writeTraceV2(artifact.evidence,this.#limits);
  this.#artifact=readArtifact(this.#bytes,this.#limits);
  const trace=artifactTrace(this.#artifact); validateRecording(trace);
  if(this.#artifact.kind==='v2')this.checkpointAfter=this.#artifact.evidence.checkpointAfter;else {
   const text=trace.config.parameters.find(([k])=>k==='stateless.monitor.checkpoint_after_sequence')?.[1]??'0';
   if(!/^\+?[0-9]+$/.test(text))throw new ModelError('invalid retained checkpoint sequence');this.checkpointAfter=BigInt(text);
  }
  if(this.checkpointAfter<0n||this.checkpointAfter+BigInt(trace.steps.length)>U64)throw new ModelError('retained sequence overflow');
 }
 static read(bytes:Uint8Array,limits:EvidenceLimitOptions={}):EvidenceViewer {return new EvidenceViewer(readArtifact(bytes,limits),limits);}
 /** Detached artifact: mutation cannot alter the viewer or later verification. */
 artifact():Artifact {return readArtifact(this.#bytes,this.#limits);}
 evidenceLimits():ReturnType<typeof resolveEvidenceLimits>{return {...this.#limits,trace:{...this.#limits.trace}};}
 get position():number{return this.#position;}
 get retainedRange():readonly [bigint,bigint]{return [this.checkpointAfter,this.checkpointAfter+BigInt(artifactTrace(this.#artifact).steps.length)];}
 seek(sequence:number):EvidenceBoundary {const b=this.boundary(sequence);if(!b)throw new ModelError('snapshot outside retained history');this.#position=sequence;return b;}
 boundary(sequence:number=this.#position):EvidenceBoundary|null {
  natural(sequence,'sequence');const a=this.artifact(),trace=artifactTrace(a);if(sequence>trace.steps.length)return null;
  const step=sequence===0?null:trace.steps[sequence-1];
  const before=sequence<=1?trace.initialState:trace.steps[sequence-2].postState;
  return {sequence,hostSequence:this.checkpointAfter+BigInt(sequence),before,after:step?.postState??trace.initialState,input:step?.input??null,outputs:step?.outputs??[],checks:step?.checks??trace.initialChecks,causal:a.kind==='v2'&&sequence>0?a.evidence.steps[sequence-1]:null};
 }
 failure(sequence:number,index:number):Violation {
  natural(index,'check index');const selected=this.boundary(sequence)?.checks[index];
  if(!selected)throw new ModelError('unknown evidence check');if(selected.status!=='failed')throw new ModelError('selected check is not a failure');
  const phase=checkPhase(this.#artifact,sequence,index);if(phase===null)throw new ModelError('v1 phase unknown; explicitly verify and upgrade or supply a phase');
  return {phase,check:{...selected}};
 }
 preview(bytes:Uint8Array,maximum:number):EncodedPreview {natural(maximum,'preview maximum');return {bytes:bytes.slice(0,maximum),totalBytes:bytes.length,complete:bytes.length<=maximum,provenance:'stored-exact-encoding'};}
 /** Cooperative bounds apply between callbacks; no output decoder or dispatch is required. */
 verify<S,I,O>(model:CodecModel<S,I,O>,limits:ShrinkLimits & {readonly maxTransitions:bigint},options:ReplayOptions={},observer?:ReplayObserver<S,I,O>) {
  const maximum=limits.maxTransitions;if(typeof maximum!=='bigint'||maximum<0n||maximum>U64)throw new ConfigError('maxTransitions must be u64 bigint');
  const stop=runControl(limits);const stopped=()=>{const reason=stop();if(reason)throw new ModelError(`verification ${reason}`);};stopped();
  const trace=artifactTrace(this.#artifact);if(BigInt(trace.steps.length)>maximum)throw new ModelError('verification transition budget cannot cover retained trace');
  return replayWithObservationsBounded(model,trace,options,this.#limits.trace,(o,r)=>{
   stopped();if(o.kind==='turn'&&r.outcome==='exact'&&this.#artifact.kind==='v2'&&o.actual.state_check_count!==this.#artifact.evidence.steps[o.actual.sequence-1].stateCheckCount)throw new ModelError('persisted check phase differs from replay');
   observer?.(o,r);stopped();
  });
 }
 minimize<S,I,O>(model:CodecModel<S,I,O>&GenerateModel<S,I,O>,sequence:number,index:number,config:ShrinkConfig,limits:ShrinkLimits & {readonly maxReplayedTransitions:bigint},policy?:InputPolicy<S,I>,maxAdmissionCandidates=100000,legacyPhase?:Violation['phase']):ShrinkReport<I> {
  let target:Violation;
  if(this.#artifact.kind==='v1'&&sequence>0&&legacyPhase!==undefined){if(legacyPhase!=='state'&&legacyPhase!=='transition')throw new ModelError('invalid legacy noninitial phase');const selected=this.boundary(sequence)?.checks[index];if(!selected||selected.status!=='failed')throw new ModelError('selected check is not a failure');target={phase:legacyPhase,check:{...selected}};}else target=this.failure(sequence,index);
  const trace=artifactTrace(this.#artifact);natural(config.maxAttempts,'maxAttempts',1);
  if(typeof limits.maxReplayedTransitions!=='bigint'||limits.maxReplayedTransitions<0n||limits.maxReplayedTransitions>U64)throw new ConfigError('maxReplayedTransitions must be u64 bigint');
  natural(maxAdmissionCandidates,'maxAdmissionCandidates',1);
  const unique=(name:string)=>{const v=trace.config.parameters.filter(([k])=>k===name);if(v.length>1)throw new ModelError('ambiguous recorded environmental policy');return v[0]?.[1];};
  const environment=unique('stateless.debug.environment'),origin=unique('stateless.debug.origin');
  if((environment===undefined)!==(origin===undefined))throw new ModelError('incomplete recorded environmental policy provenance');
  if(origin!==undefined){if(origin!=='simulation'&&origin!=='out-of-domain-injection')throw new ModelError('unknown recorded environmental policy origin');
   if(policy?(policy.label!==environment||(policy.kind==='unrestricted')!==(origin==='out-of-domain-injection')):!(origin==='out-of-domain-injection'&&environment==='out-of-domain-injection'))throw new ModelError('recorded environmental policy requires matching explicit policy');}
  const started=limits.maxDurationMs===undefined?0:performance.now();const stop=runControl(limits);const stopped=()=>{const reason=stop();if(reason)throw new ModelError(`minimization ${reason}`);};stopped();
  let charged=0n;
  if(this.#artifact.kind==='v2'){
   const verified=this.verify(model,{...limits,maxTransitions:limits.maxReplayedTransitions}, {},()=>stopped());
   if(verified.outcome!=='exact'||!verified.failure_reproduced)throw new ModelError('minimization requires exact v2 phase and failure replay');charged=BigInt(trace.steps.length);
  }
  stopped();
  const count=BigInt(trace.steps.length);
  if(count>0n&&count>=limits.maxReplayedTransitions-charged)throw new ModelError('minimization budget cannot cover verification and original validation');
  const verified=this.verify(model,{...limits,maxTransitions:limits.maxReplayedTransitions-charged},{},()=>stopped());
  if(verified.outcome!=='exact'||!verified.failure_reproduced)throw new ModelError('minimization requires an exactly reproduced failure');
  charged+=count;stopped();
  const remaining:ShrinkLimits={...limits,maxReplayedTransitions:limits.maxReplayedTransitions-charged};
  if(limits.maxDurationMs!==undefined){const ms=limits.maxDurationMs-(performance.now()-started);if(ms<=0)throw new ModelError('minimization deadline');Object.assign(remaining,{maxDurationMs:ms});}
  const adapted=new Proxy(model,{get(target,key){
   if(key==='initialState')return ()=>model.decodeState(trace.initialState.slice());
   if(key==='isEnabled')return (s:S,i:I)=>{stopped();const causal=()=>model.isEnabled?model.isEnabled(model.cloneState(s),model.cloneInput(i)):true;
    if(!policy||policy.kind==='unrestricted')return causal();
    if(policy.kind==='declared'){const value=policy.permits(model.cloneState(s),model.cloneInput(i));if(typeof value!=='boolean')throw new ModelError('policy admission must return boolean');return value&&causal();}
    const iterator=policy.candidates(model.cloneState(s))[Symbol.iterator]();let found=false;
    try{for(let count=0;;count++){stopped();const next=iterator.next();if(next.done)return found;if(count>=maxAdmissionCandidates)throw new ModelError('environmental admission candidate limit');const equal=policy.equal(model.cloneInput(next.value),model.cloneInput(i));if(typeof equal!=='boolean')throw new ModelError('policy equality must return boolean');if(equal)return causal();}}finally{iterator.return?.();}
   };
   const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
  }});
  const inputs=trace.steps.slice(0,sequence).map(s=>model.decodeInput(s.input.slice()));
  const report=shrink(adapted,{inputs,violations:[target]},config,remaining);report.replayed_transitions+=charged;return report;
 }
}
