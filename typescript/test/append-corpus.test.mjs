import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {check,accepted,metadata,withCheckSink,checkObservedInto,enumerateStates,fuzz,shrink,record,replay,WithOracle,Paired} from '../dist/index.js';
import {appendStateChecks,appendTransitionChecks} from '../dist/checks.js';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/append-checks-v1.json',import.meta.url),'utf8'));
for(const row of corpus.cases)test(`append-checks-v1 ${row.case}`,()=>{
 const events=[],observations=[],out=[check('sentinel')];let outcome='ok',checks=null,witness=null,attempts=null,error;
 const stateChecks=s=>[check('same'),check('skip','skipped','not run'),...(row.fail_state===s?[check('bad','failed','broken')]:[])];
 const make=(prefix='')=>({
  initialState(){events.push(prefix+'initial');return 0;},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalStates:(a,b)=>a===b,inputs:s=>s===0?[1]:[],generate:()=>1,isEnabled:()=>true,simplerInputs:()=>[],
  step(s,i){events.push(`${prefix}step:${s}:${i}`);return accepted(s+i);},metadata:()=>metadata('append-fixture','append-v1'),encodeState:s=>new TextEncoder().encode(String(s)),decodeState:b=>Number(new TextDecoder().decode(b)),encodeInput:i=>new TextEncoder().encode(String(i)),decodeInput:b=>Number(new TextDecoder().decode(b)),encodeOutput:o=>new TextEncoder().encode(String(o)),
  checkState(s){events.push(`${prefix}legacy_state:${s}`);if(row.mode==='append')throw Error('legacy state called');if(row.error_at===`state:${s}`)throw Error('fixture append error');return stateChecks(s);},
  checkTransition(_s,_i,t){events.push(`${prefix}legacy_transition:${t.state}`);if(row.mode==='append')throw Error('legacy transition called');if(row.error_at===`transition:${t.state}`)throw Error('fixture append error');return [check('same')];},
  ...(row.mode==='append'?{
   checkStateInto(s,sink){events.push(`${prefix}append_state:${s}:${sink.length}`);sink.push(check('same'));if(row.error_at===`state:${s}`||prefix&&row.wrapper_error==='reference_state')throw Error('fixture append error');sink.extend(stateChecks(s).slice(1));},
   checkTransitionInto(_s,_i,t,sink){events.push(`${prefix}append_transition:${t.state}:${sink.length}`);sink.push(check('same'));if(row.error_at===`transition:${t.state}`)throw Error('fixture append error');}
  }:{})
 });
 const m=make(),observer={initial(_s,cs){events.push('observe_initial');observations.push({phase:'initial_state',complete:true,checks:[...cs]});},transition(_s,_i,_t,sc,ec){events.push('observe_transition');observations.push({phase:'state',complete:true,checks:[...sc]},{phase:'transition',complete:true,checks:[...ec]});},checkError(phase,cs,complete){events.push(`check_error:${phase}:${complete}`);observations.push({phase,complete,checks:[...cs]});}};
 try{
  if(row.operation==='direct_state'){withCheckSink(out,sink=>appendStateChecks(m,1,sink));checks=out;}
  else if(row.operation==='direct_transition'){withCheckSink(out,sink=>appendTransitionChecks(m,0,1,accepted(1),sink));checks=out;}
  else if(row.operation==='observed'){checkObservedInto(m,0,1,accepted(1),row.sequence,out,{stateEvery:row.state_every,transitionChecks:row.transition_checks});checks=out;}
  else if(row.operation==='bfs'){const r=enumerateStates(m,{maxStates:10,maxTransitions:10,maxDepth:10},{},observer);outcome=r.termination;witness=r.failure?.inputs??null;}
  else if(row.operation==='fuzz'){const r=fuzz(m,{seed:0n,cases:1,maxSteps:1,maxTransitions:10n,mutationPercent:0},{},observer);outcome=r.termination;witness=r.failure?.inputs??null;}
  else if(row.operation==='shrink'){const r=shrink(m,{inputs:[1],violations:[{phase:'state',check:check('bad','failed','broken')}]});outcome=r.termination;witness=r.minimized.inputs;attempts=r.attempts;}
  else if(row.operation==='record'){const r=record(m,[1]);outcome=r.termination;checks=[...r.initialChecks,...r.steps.flatMap(s=>s.checks)];}
  else if(row.operation==='replay'){const trace={metadata:m.metadata(),initialState:new TextEncoder().encode('0'),initialChecks:stateChecks(0),steps:[{input:new TextEncoder().encode('1'),disposition:{kind:'accepted',reason:''},outputs:[],postState:new TextEncoder().encode('1'),checks:[...stateChecks(1),check('same')]}],termination:'completed',error:''};outcome=replay(m,trace).outcome;}
  else {
   const oracle={metadata:()=>metadata('oracle'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState(){throw Error('legacy oracle');},checkStateInto(h,_s,sink){events.push(`oracle_state:${h}:${sink.length}`);sink.push(check('oracle.state'));if(row.wrapper_error==='oracle_state')throw Error('fixture append error');sink.push(check('oracle.skip','skipped','not run'));},checkTransitionInto(_s,_i,t,sink){events.push(`oracle_transition:${t.state.oracle}:${sink.length}`);sink.push(check('oracle.transition'));if(row.wrapper_error==='oracle_transition')throw Error('fixture append error');sink.push(check('oracle.skip','skipped','not run'));}};
   const projection={metadata:()=>metadata('projection'),actualState(s){events.push('projection_actual_state');if(row.wrapper_error==='projection_actual_state')throw Error('fixture append error');return s;},referenceState(s){events.push('projection_reference_state');return s;},actualEffect:o=>o,referenceEffect:o=>o,equalState:(a,b)=>a===b,equalEffect:(a,b)=>a===b};
   const wrapped=new WithOracle(m,row.operation.startsWith('paired')?new Paired(make('reference_'),projection):oracle);
   if(row.operation.endsWith('state'))withCheckSink(out,sink=>wrapped.checkStateInto({model:1,oracle:1},sink));
   else withCheckSink(out,sink=>wrapped.checkTransitionInto({model:0,oracle:0},1,accepted({model:1,oracle:1}),sink));checks=out;
  }
 }catch(e){error=e;outcome='error';if(['direct_state','direct_transition','observed','oracle_state','oracle_transition','paired_state','paired_transition'].includes(row.operation))checks=out;}
 if(row.error_contains)assert.ok(error?.message.includes(row.error_contains),`${row.case}: ${error?.message}`);else assert.equal(error,undefined);
 assert.deepEqual({outcome,checks,observations,witness,attempts},row.expected);assert.deepEqual(events,row.events);
});
