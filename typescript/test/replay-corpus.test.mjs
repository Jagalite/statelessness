import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {check,metadata,disposition,ReplayError,replayWithObservations,replayWithObservationsBounded} from '../dist/index.js';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/replay-evidence-v1.json',import.meta.url),'utf8'));
const unhex=h=>Uint8Array.from(Buffer.from(h,'hex')),hex=b=>Buffer.from(b).toString('hex');
const report=r=>({outcome:r.outcome,divergence_step:r.step,divergence_field:r.field,steps_verified:r.steps_verified,failure_reproduced:r.failure_reproduced,build_matches:r.build_matches});
const ck=c=>check(c.id,c.status,c.reason??'');
const disp=d=>disposition(d.kind,d.reason??'');
const trace=t=>({metadata:t.metadata,config:{...t.config,seed:t.config.seed===null?null:BigInt(t.config.seed)},initialState:unhex(t.initial_state),initialChecks:t.initial_checks.map(ck),steps:t.steps.map(s=>({input:unhex(s.input),disposition:disp(s.disposition),outputs:s.outputs.map(unhex),postState:unhex(s.post_state),checks:s.checks.map(ck)})),termination:t.termination.kind,error:t.termination.reason??''});
const normalizedStep=s=>({input:hex(s.input),disposition:s.disposition,outputs:s.outputs.map(hex),post_state:hex(s.postState),checks:s.checks});
function observation(o,r){
 if(o.kind==='error')return {kind:'error',sequence:o.sequence,actual:o.actual,report:report(r)};
 if(o.kind==='initial')return {kind:'initial',sequence:0,state:o.state,checks:o.checks,expected_state:hex(o.expected_state),actual_state:hex(o.actual_state),expected_checks:o.expected_checks,differences:o.differences,report:report(r)};
 const {kind,...actual}=o.actual;return {kind:'turn',...actual,expected:normalizedStep(o.expected),actual_input:hex(o.actual_input),actual_outputs:o.actual_outputs.map(hex),actual_state:hex(o.actual_state),differences:o.differences,report:report(r)};
}
function fixture(c,events){
 const v=c.model,encoder=new TextEncoder(),decoder=new TextDecoder();
 const event=s=>{events.push(s);if(v.error_at===s)throw Error('fixture codec replay error');};
 const enc=(kind,n,b)=>{event(`encode_${kind}:${n}`);b.extend(encoder.encode(String(n)+(kind==='output'?'x'.repeat(v.output_padding):'')));};
 return {
 metadata(){event('metadata');return metadata(v.name,v.build);},initialState(){throw Error('must not initialize');},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
 decodeState(b){event(`decode_state:${hex(b)}`);return Number(decoder.decode(b));},decodeInput(b){event(`decode_input:${hex(b)}`);return Number(decoder.decode(b));},
 encodeState(){throw Error('legacy state');},encodeInput(){throw Error('legacy input');},encodeOutput(){throw Error('legacy output');},
 encodeStateInto(s,b){enc('state',s,b);},encodeInputInto(i,b){enc('input',i,b);},encodeOutputInto(o,b){enc('output',o,b);},
 step(s,i){event(`step:${s}:${i}`);const base=s+i;return {state:base+v.state_delta,outputs:Array.from({length:v.output_count},()=>base+v.output_delta),disposition:disposition(v.disposition,v.disposition==='accepted'?'':v.disposition_reason??'why')};},
 checkState(s){event(`check_state:${s}`);return [ck((s===0?v.initial_check:v.turn_state_check)??{id:'ok',status:'passed',reason:null})];},
 checkTransition(s,i,t){event(`check_transition:${s}:${i}:${t.state}`);return [ck(v.turn_transition_check??{id:'edge',status:'passed',reason:null})];}
 };
}
for(const c of corpus.cases)test(`shared replay-evidence-v1 ${c.case}`,()=>{
 const events=[],observations=[],m=fixture(c,events),t=trace(c.trace),options={allowBuildMismatch:c.allow_build_mismatch};
 const observer=(o,r)=>{events.push(o.kind==='turn'?`observe_turn:${o.actual.sequence}`:o.kind==='error'?`observe_error:${o.sequence}`:'observe_initial');observations.push(observation(o,r));const label=o.kind==='turn'?`turn:${o.actual.sequence}`:o.kind;if(c.observer_error_at===label)throw Error('observer fixture error');};
 let result;
 try{const r=c.bounded?replayWithObservationsBounded(m,t,options,c.limits,observer):replayWithObservations(m,t,options,observer);result={report:report(r),observations,error:false,actual:null,observer_error:false};}
 catch(e){assert.ok(e instanceof ReplayError,String(e));if(c.error_contains)assert.ok(e.message.includes(c.error_contains),e.message);result={report:report(e.report),observations,error:true,actual:e.actual,observer_error:e.observer_error!==null};}
 assert.deepEqual(result,c.expected);assert.deepEqual(events,c.events);
});
