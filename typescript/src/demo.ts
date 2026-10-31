/** Finite request-lifecycle fixture; not a qualified production application. */
import {accepted,check,disposition,metadata,ModelError,natural,type CodecModel,type EnumerateModel,type Transition,type Rng} from './core.js';
import {NO_INPUT,type GenerateModel} from './search.js';
import type {EncodeBuffer} from './encoding.js';
export interface RequestState {generation:number;active:boolean;ready:boolean;pending:number[]}
export type RequestInput={kind:'start'}|{kind:'cancel'}|{kind:'complete';generation:number};
export type RequestOutput={kind:'request'|'publish'|'release';generation:number};
const byte=(v:number)=>natural(v,'generation',0,255);
function validInput(i:RequestInput):void{if(!i||!['start','cancel','complete'].includes(i.kind))throw new ModelError('invalid request input');if(i.kind==='complete')byte(i.generation);}
function validOutput(o:RequestOutput):void{if(!o||!['request','publish','release'].includes(o.kind))throw new ModelError('invalid request output');byte(o.generation);}
export class RequestModel implements CodecModel<RequestState,RequestInput,RequestOutput>,EnumerateModel<RequestState,RequestInput,RequestOutput>,GenerateModel<RequestState,RequestInput,RequestOutput>{
 constructor(readonly injectBug:boolean,readonly build='unqualified'){}
 static buggy(build='unqualified'){return new RequestModel(true,build);}
 static fixed(build='unqualified'){return new RequestModel(false,build);}
 metadata(){return metadata('request-lifecycle',`${this.build}:${this.injectBug?'injected':'fixed'}`);}
 initialState():RequestState{return {generation:0,active:false,ready:false,pending:[]};}
 /** Logical accounting only; JavaScript object/array overhead is host-dependent. */
 estimatedStateBytes(s:RequestState):bigint{return 32n+BigInt(s.pending.length);}
 cloneState(s:RequestState):RequestState{return {...s,pending:[...s.pending]};}
 cloneInput(i:RequestInput):RequestInput{validInput(i);return {...i};}
 cloneOutput(o:RequestOutput):RequestOutput{validOutput(o);return {...o};}
 equalStates(a:RequestState,b:RequestState){return a.generation===b.generation&&a.active===b.active&&a.ready===b.ready&&a.pending.length===b.pending.length&&a.pending.every((v,i)=>v===b.pending[i]);}
 equalInputs(a:RequestInput,b:RequestInput){validInput(a);validInput(b);return a.kind===b.kind&&(a.kind!=='complete'||b.kind==='complete'&&a.generation===b.generation);}
 step(before:RequestState,input:RequestInput):Transition<RequestState,RequestOutput>{validInput(input);const s=this.cloneState(before),outputs:RequestOutput[]=[];
  if(input.kind==='start'&&s.generation<2){s.generation++;s.active=true;s.ready=false;s.pending.push(s.generation);outputs.push({kind:'request',generation:s.generation});}
  else if(input.kind==='cancel'&&s.active){s.active=false;s.ready=false;}
  else if(input.kind==='complete'&&s.pending.includes(input.generation)){s.pending=s.pending.filter(g=>g!==input.generation);if(this.injectBug||(s.active&&input.generation===s.generation)){s.ready=true;outputs.push({kind:'publish',generation:input.generation});}else outputs.push({kind:'release',generation:input.generation});}
  else return {state:s,outputs,disposition:disposition('rejected','input is not enabled')};return accepted(s,outputs);
 }
 checkState(s:RequestState){const ready=!s.ready||s.active,bounded=s.pending.length<=2&&s.generation<=2&&s.pending.every((g,i)=>g>0&&g<=s.generation&&(i===0||s.pending[i-1]<g));return [check('ready_requires_active',ready?'passed':'failed',ready?'':'retired request became ready'),check('bounded_pending',bounded?'passed':'failed',bounded?'':'invalid pending ledger')];}
 checkTransition(before:RequestState,input:RequestInput,t:Transition<RequestState,RequestOutput>){validInput(input);for(const o of t.outputs)validOutput(o);const stale=input.kind==='complete'&&before.pending.includes(input.generation)&&(!before.active||input.generation!==before.generation);const ok=!stale||(input.kind==='complete'&&t.outputs.length===1&&t.outputs[0].kind==='release'&&t.outputs[0].generation===input.generation);return [check('stale_completion_is_cleanup_only',ok?'passed':'failed',ok?'':'stale completion published instead of releasing')];}
 inputs(s:RequestState):RequestInput[]{return [...(s.generation<2?[{kind:'start' as const}]:[]),...(s.active?[{kind:'cancel' as const}]:[]),...s.pending.map(generation=>({kind:'complete' as const,generation}))];}
 generate(s:RequestState,rng:Rng):RequestInput|typeof NO_INPUT{const inputs=this.inputs(s),i=rng.index(BigInt(inputs.length));return i===null?NO_INPUT:inputs[Number(i)];}
 isEnabled(s:RequestState,i:RequestInput){validInput(i);return i.kind==='start'?s.generation<2:i.kind==='cancel'?s.active:s.pending.includes(i.generation);}
 encodeState(s:RequestState){byte(s.generation);if(typeof s.active!=='boolean'||typeof s.ready!=='boolean'||s.pending.length>255)throw new ModelError('invalid request state');return Uint8Array.of(s.generation,+s.active,+s.ready,s.pending.length,...s.pending.map(byte));}
 decodeState(b:Uint8Array):RequestState{if(b.length<4||b.length!==4+b[3]||b[1]>1||b[2]>1)throw new ModelError('invalid request state encoding');return {generation:b[0],active:!!b[1],ready:!!b[2],pending:[...b.slice(4)]};}
 encodeInput(i:RequestInput){validInput(i);return i.kind==='start'?Uint8Array.of(0):i.kind==='cancel'?Uint8Array.of(1):Uint8Array.of(2,byte(i.generation));}
 decodeInput(b:Uint8Array):RequestInput{if(b.length===1&&b[0]===0)return {kind:'start'};if(b.length===1&&b[0]===1)return {kind:'cancel'};if(b.length===2&&b[0]===2)return {kind:'complete',generation:b[1]};throw new ModelError('invalid request input encoding');}
 encodeOutput(o:RequestOutput){validOutput(o);return Uint8Array.of(o.kind==='request'?0:o.kind==='publish'?1:2,byte(o.generation));}
 encodeStateInto(s:RequestState,b:EncodeBuffer){b.extend(this.encodeState(s));}
 encodeInputInto(i:RequestInput,b:EncodeBuffer){b.extend(this.encodeInput(i));}
 encodeOutputInto(o:RequestOutput,b:EncodeBuffer){b.extend(this.encodeOutput(o));}
}
