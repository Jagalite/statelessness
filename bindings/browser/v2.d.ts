export type Check = {readonly id:string;readonly status:'passed'|'failed'|'skipped';readonly details:string};
export type Disposition = {readonly kind:'accepted'|'rejected'|'ignored';readonly reason:string};
export interface Transition<S,O>{readonly state:S;readonly outputs:readonly O[];readonly disposition:Disposition}
export interface Metadata{readonly name:string;readonly build:string;readonly model_version:number;readonly properties_version:number;readonly codec_version:number}
export interface Model<S,I,O>{
  initialState():S;step(state:S,input:I):Transition<S,O>;checkState(state:S):Iterable<Check>;
  checkTransition?(before:S,input:I,transition:Transition<S,O>):Iterable<Check>;
  cloneState(state:S):S;cloneInput(input:I):I;cloneOutput(output:O):O;
  inputs?(state:S):Iterable<I>;equalStates?(left:S,right:S):boolean;
  hashState?(state:S):string|number|bigint;estimatedStateBytes?(state:S):number|bigint|null;
  metadata?():Metadata;encodeState?(state:S):Uint8Array;decodeState?(bytes:Uint8Array):S;
  encodeInput?(input:I):Uint8Array;decodeInput?(bytes:Uint8Array):I;encodeOutput?(output:O):Uint8Array;
  generate?(state:S,rng:CallbackRng):I|null;isEnabled?(state:S,input:I):boolean;
  equalInputs?(left:I,right:I):boolean;simplerInputs?(input:I):Iterable<I>;
}
export interface CallbackRng{nextU64():bigint;index(upper:bigint|number):bigint|null}
export interface NativeRng extends CallbackRng{readonly state:bigint}
export interface TraceLimits{readonly maxSteps:number;readonly maxBlobBytes:number;readonly maxItems:number;readonly maxPayloadBytes:number;readonly maxJSONBytes:number}
export interface TraceStep{readonly input:Uint8Array;readonly disposition:Disposition;readonly outputs:readonly Uint8Array[];readonly postState:Uint8Array;readonly checks:readonly Check[]}
export interface Trace{readonly metadata:Metadata;readonly initialState:Uint8Array;readonly initialChecks:readonly Check[];readonly steps:readonly TraceStep[];readonly termination:'completed'|'property_failed'|'step_limit'|'interrupted'|'model_error';readonly error:string}
export interface Violation{readonly phase:'unspecified'|'initial_state'|'state'|'transition';readonly check:Check}
export interface Failure<I>{readonly inputs:readonly I[];readonly violations:readonly Violation[]}
export interface SearchReport<I>{readonly termination:'graph_exhausted'|'depth_bound'|'state_limit'|'transition_limit'|'failure_found'|'estimated_state_byte_limit'|'deadline'|'cancelled';readonly states:number;readonly transitions:number;readonly max_depth_reached:number;readonly skipped_checks:number;readonly failure:Failure<I>|null}
export interface ReplayReport{readonly outcome:'exact'|'diverged'|'incompatible';readonly steps_verified:number;readonly failure_reproduced:boolean;readonly build_matches:boolean;readonly step:number|null;readonly field:string|null}
export interface SearchConfig{maxStates?:number|bigint;maxTransitions?:number|bigint;maxDepth?:number|bigint;maxStateBytes?:number|bigint;maxDurationMillis?:number|bigint}
export interface NativeResult<I>{readonly status:number;close():void;scalar(field:number):bigint;bytes(field?:number,index?:number|bigint):Uint8Array;text(field:number,index?:number|bigint):string;checks():readonly Violation[];inputs():readonly I[];searchReport():SearchReport<I>}
export interface Ring<S,I,O>{close():void;observe(before:S,input:I,transition:Transition<S,O>):readonly Check[];snapshot():Uint8Array}
export interface Session<S,I,O>{
 readonly retainedRoots:number;readonly cleanupErrors:readonly string[];readonly capabilities:bigint;readonly metadata:Metadata;
 close():void;cancel():void;resetCancellation():void;
 search(config?:SearchConfig):NativeResult<I>;enumerateStates(config?:SearchConfig):SearchReport<I>;
 checkInitial(state:S):readonly Check[];
 checkObserved(before:S,input:I,transition:Transition<S,O>,sequence:number|bigint,policy?:{stateEvery?:number|bigint;transitionChecks?:boolean}):readonly Check[];
 recordBinary(inputs:Iterable<I>,maxSteps?:number|bigint):Uint8Array;
 record(inputs:Iterable<I>,maxSteps?:number|bigint,limits?:TraceLimits):Trace;
 replay(trace:Trace|Uint8Array,options?:{allowBuildMismatch?:boolean}):ReplayReport;
 fuzz(config?:{seed?:bigint;cases?:number|bigint;maxSteps?:number|bigint;maxTransitions?:number|bigint;mutationPercent?:number;maxDurationMillis?:number|bigint}):NativeResult<I>;
 shrink(failure:NativeResult<I>,config?:{maxAttempts?:number|bigint;maxTransitions?:number|bigint;maxDurationMillis?:number|bigint}):NativeResult<I>;
 ring(initialState:S,options?:{maxSteps?:number|bigint;maxRetainedBytes?:number|bigint}):Ring<S,I,O>;
}
export interface Engine{readonly sha256:string|null;createSession<S,I,O>(model:Model<S,I,O>,limits?:{maxValues?:number;maxItems?:number;maxBytes?:number;automaticGeneration?:boolean}):Session<S,I,O>;rng(seed:bigint):NativeRng}
export class NativeError extends Error{readonly status:number;constructor(message:string,status?:number)}
export function createEngine(source:Uint8Array|WebAssembly.Module,options?:{expectedSHA256?:string}):Promise<Engine>;
export function check(id:string,status?:Check['status'],details?:string):Check;
export function disposition(kind?:Disposition['kind'],reason?:string):Disposition;
export const DEFAULT_TRACE_LIMITS:Readonly<TraceLimits>;
export function parseTrace(data:string|Uint8Array,limits?:TraceLimits):Trace;
export function stringifyTrace(trace:Trace,limits?:TraceLimits):string;
