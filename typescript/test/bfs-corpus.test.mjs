import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {enumerateStates,accepted,check} from '../dist/index.js';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/bfs-controls-v1.json',import.meta.url),'utf8'));
for(const row of corpus.cases)test(`bfs-controls-v1 ${row.case}`,()=>{
 const events=[];let cancelled=['cancelled','both'].includes(row.entry_control);
 const log=e=>{events.push(e);if(e===row.cancel_at)cancelled=true;};
 const model={initialState(){log('initial');return 0;},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
  estimatedStateBytes(s){log(`estimate:${s}`);return row.estimates[s]===null?null:BigInt(row.estimates[s]);},
  hashState(s){log(`hash:${s}`);return 0;},equalStates(a,b){log(`equal:${a}:${b}`);return a===b;},
  inputs(s){log(`inputs:${s}`);const values=row.fixture==='empty'?[]:row.fixture==='loop'?[0,0]:row.fixture==='diamond'?(s===0?[1,2,1]:s===1||s===2?[3]:[]):s<2?[1]:[];let n=0;return {[Symbol.iterator](){return {next(){if(n===values.length){log(`next:${s}:end`);return {done:true};}const value=values[n++];log(`next:${s}:${value}`);return {done:false,value};}};}};},
  step(s,i){log(`step:${s}:${i}`);return accepted(row.fixture==='diamond'?i:s+i);},
  *checkState(s){const e=`state:${s}`;log(e);if(e===row.error_at){if(row.error_prefix)yield check('prefix');throw Error('fixture check error');}yield check('ok');if(row.skipped)yield check('skip','skipped','not run');if(row.fail_initial&&s===0||row.fail_state===s)yield check('bad','failed','broken');},
  *checkTransition(s,i,t){const e=`transition:${s}:${i}:${t.state}`;log(e);if(e===row.error_at){if(row.error_prefix)yield check('prefix');throw Error('fixture check error');}}
 };
 const observer={needsHistory:()=>row.needs_history,initial(s){log(`observe_initial:${s}`);},transition(s,i,t,_sc,_ec,h){log(`observe:${s}:${i}:${t.state}:${h.length?h.join(','):'-'}`);},checkError(phase,checks,complete){log(`check_error:${phase}:${complete}:${checks.length}`);}};
 const config={maxStates:row.max_states,maxTransitions:row.max_transitions,maxDepth:row.max_depth};
 const limits={control:{cancellation:()=>cancelled,...(['deadline','both'].includes(row.entry_control)?{maxDurationMs:0}:{})},...(row.max_estimated_state_bytes===null?{}:{maxEstimatedStateBytes:BigInt(row.max_estimated_state_bytes)})};
 const run=()=>enumerateStates(model,config,limits,observer);
 if(row.error_contains){assert.throws(run,e=>e.message.includes(row.error_contains)||(row.error_contains.includes('max_states')&&e.message.includes('maxStates')));}
 else{const r=run();assert.deepEqual({termination:r.termination,states:r.states,transitions:r.transitions,max_depth_reached:r.max_depth_reached,skipped_checks:r.skipped_checks,estimated_retained_state_bytes:r.estimated_retained_state_bytes?.toString()??null,witness:r.failure?.inputs??null,failed_phase:r.failure?.violations[0]?.phase??null,failed_id:r.failure?.violations[0]?.check.id??null},row.expected);}
 assert.deepEqual(events,row.events);
});
