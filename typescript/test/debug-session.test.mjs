import test from 'node:test';
import assert from 'node:assert/strict';
import {DebugSession} from '../dist/debug_session.js';
import {accepted,check} from '../dist/index.js';
const rejected=(state,reason)=>({state,outputs:[],disposition:{kind:'rejected',reason}});
const ignored=(state,reason)=>({state,outputs:[],disposition:{kind:'ignored',reason}});
const limits={maxTurns:10n,maxContinue:3,maxBreakpoints:3,maxCandidates:3};
const policy={kind:'enumerated',label:'manual',candidates:()=>[1,2],equal:(a,b)=>a===b};
function model(){return {initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,step:(s,i)=>i===2?rejected(s,'no'):accepted(s+i,[s+i]),checkState:s=>[check('limit',s<3?'passed':'failed','')],checkTransition:()=>[]};}
test('typed no-codec session one-pass checking disposition counters and detached observers',()=>{let checks=0;const m=model(),old=m.checkState;m.checkState=s=>{checks++;return old(s);};const s=new DebugSession('manual',m,policy,limits);assert.equal(checks,1);assert.equal(s.epoch>0n,true);assert.equal(s.capabilities.effectsDispatched,false);assert.equal(s.step(0n,1).delivered,true);assert.equal(checks,2);assert.equal(s.state,1);assert.equal(s.revision,1n);assert.equal(s.step(1n,2).delivered,true);assert.equal(s.state,1);assert.equal(s.observation().transition.disposition.kind,'rejected');assert.throws(()=>s.step(0n,1),/stale/);assert.throws(()=>s.step(2n,99),/permitted/);assert.equal(s.revision,2n);s.step(2n,1,()=>{throw Error('sink');});assert.equal(s.state,2);assert.equal(s.revision,3n);assert.equal(s.stopReason.kind,'observer_error');assert.equal(s.phase,'ended');});
test('pre breakpoint retains exact input once; post breakpoint commits before observation',()=>{const s=new DebugSession('breakpoints',model(),policy,limits);const pre=s.addPreBreakpoint(()=>true),post=s.addPostBreakpoint(t=>t.transition.state===1);assert.equal(s.step(0n,1).delivered,false);assert.deepEqual(s.stopReason,{kind:'pre_breakpoint',id:pre});assert.equal(s.revision,0n);assert.equal(s.step(0n,1).delivered,true);assert.equal(s.revision,1n);assert.deepEqual(s.stopReason,{kind:'post_breakpoint',id:post});assert.equal(s.removeBreakpoint(pre),true);s.step(1n,1);s.step(2n,1);assert.equal(s.stopReason.kind,'property_failure');assert.equal(s.phase,'ended');});
test('candidate authority is session/revision bound; stale/forged tokens reject',()=>{const s=new DebugSession('same',model(),policy,limits),other=new DebugSession('same',model(),policy,limits);const page=s.candidates(0n,0,1);assert.equal(page.complete,false);assert.equal(page.nextOffset,1);assert.equal(page.candidates[0].input,1);assert.throws(()=>other.stepCandidate(0n,page.candidates[0]),/foreign/);assert.throws(()=>s.stepCandidate(0n,{input:1,revision:0n}),/foreign/);s.stepCandidate(0n,page.candidates[0]);assert.throws(()=>s.stepCandidate(1n,page.candidates[0]),/stale/);assert.equal(s.candidates(1n,1,2).complete,true);});
test('checker failure commits transition; reducer failure does not; owner rejects reentrant calls',()=>{const m=model();m.checkTransition=()=>{throw Error('checker');};const s=new DebugSession('check',m,policy,limits);s.step(0n,1);assert.equal(s.state,1);assert.equal(s.revision,1n);assert.equal(s.observation().completeChecks,false);assert.equal(s.stopReason.kind,'checker_error');const n=model();n.step=()=>{throw Error('reduce');};const t=new DebugSession('reduce',n,policy,limits);assert.equal(t.step(0n,1).delivered,false);assert.equal(t.revision,0n);assert.equal(t.state,0);const u=new DebugSession('owner',model(),policy,limits);u.step(0n,1,()=>assert.throws(()=>u.step(1n,1),/owned/));assert.equal(u.phase,'paused');});
test('initial failures, bounded continue, cancellation, admission lookahead and ignored turns',()=>{const m=model();m.initialState=()=>3;const s=new DebugSession('initial',m,policy,limits);assert.equal(s.stopReason.kind,'property_failure');assert.throws(()=>s.step(0n,1),/ended/);const t=new DebugSession('run',model(),policy,limits);assert.equal(t.runBounded(0n,[1,2,2,1],2).length,2);assert.equal(t.stopReason.kind,'budget_exhausted');t.cancel(2n);assert.equal(t.stopReason.kind,'cancelled');const u=new DebugSession('bounded',model(),{...policy,candidates:()=>[2,3,4,1]},limits);assert.throws(()=>u.step(0n,1),/candidate limit/);assert.equal(u.revision,0n);const n=model();n.step=s=>ignored(s,'later');const v=new DebugSession('ignored',n,policy,limits);v.step(0n,1);assert.equal(v.sequence,1n);assert.equal(v.observation().transition.disposition.kind,'ignored');});

test('constructor initial callback precedes configuration rejection; explicit state avoids initialization',()=>{let calls=0;const m=model();m.initialState=()=>{calls++;return 0;};assert.throws(()=>new DebugSession('',m,policy,limits));assert.equal(calls,1);new DebugSession('restored',m,policy,limits,{state:1});assert.equal(calls,1);});
test('bounded iterator callbacks cannot reenter session',()=>{const s=new DebugSession('iteration',model(),policy,limits);let next=0;const inputs={[Symbol.iterator](){assert.throws(()=>s.cancel(0n),/owned/);return {next(){assert.throws(()=>s.step(s.revision,1),/owned/);return next++?{done:true}:{done:false,value:1};},return(){assert.throws(()=>s.cancel(s.revision),/owned/);return {done:true};}};}};assert.equal(s.runBounded(0n,inputs,2).length,1);assert.equal(s.revision,1n);});
test('post breakpoint exception leaves terminal precommit state and diagnostic',()=>{const s=new DebugSession('post-error',model(),policy,limits);s.addPostBreakpoint(()=>{throw Error('post callback');});assert.throws(()=>s.step(0n,1),/post callback/);assert.equal(s.state,0);assert.equal(s.sequence,1n);assert.equal(s.phase,'ended');assert.equal(s.observation(),null);assert.equal(s.diagnosticErrors.length,1);});

test('declared predicate policy needs neither enumeration nor codec',()=>{const s=new DebugSession('predicate',model(),{kind:'declared',label:'ones',permits:(_s,i)=>i===1},limits);assert.equal(s.capabilities.enumerateInputs,false);assert.throws(()=>s.candidates(0n,0,1),/unavailable/);assert.throws(()=>s.step(0n,2),/not permitted/);assert.equal(s.step(0n,1).delivered,true);});

test('post predicate sees prior session state and completed detached turn before commit',()=>{const s=new DebugSession('post-order',model(),policy,limits);s.addPostBreakpoint(t=>{assert.equal(s.state,0);assert.equal(t.transition.state,1);return false;});s.step(0n,1);assert.equal(s.state,1);});

test('snapshot clone callbacks cannot reenter session mutation',()=>{const m=model();let session;const clone=m.cloneState;m.cloneState=s=>{if(session)assert.throws(()=>session.cancel(session.revision),/owned/);return clone(s);};session=new DebugSession('snapshot-owner',m,policy,limits);assert.equal(session.state,0);session.step(0n,1);assert.equal(session.observation().transition.state,1);});
test('model input domain remains independent of unrestricted environmental injection',()=>{const m=model();let available=true;m.inputs=()=>available?[1]:[];m.equalInputs=(a,b)=>a===b;const s=new DebugSession('domain',m,{kind:'unrestricted',label:'out-of-domain-injection'},limits);assert.equal(s.capabilities.enumerateInputs,true);const token=s.candidates(0n,0,1).candidates[0];available=false;assert.throws(()=>s.stepCandidate(0n,token),/current domain/);assert.equal(s.sequence,0n);assert.equal(s.step(0n,99).delivered,true);assert.equal(s.state,99);});

test('policy callbacks preserve prototype and own method receivers and capture declarations once',()=>{
 class Declared {
  kind='declared';label='private';#allowed=1;reads=0;
  get permits(){this.reads++;return this.admit;}
  admit(_state,input){return input===this.#allowed;}
 }
 const p=new Declared(),s=new DebugSession('prototype-policy',model(),p,limits);
 assert.equal(p.reads,1);p.kind='unrestricted';p.label='changed';p.admit=()=>false;
 assert.equal(s.inputPolicy,'private');assert.throws(()=>s.step(0n,99),/not permitted/);
 assert.equal(s.step(0n,1).delivered,true);assert.equal(p.reads,1);
 const own={kind:'declared',label:'own',allowed:1,permits(_state,input){assert.equal(this,own);return input===this.allowed;}};
 const t=new DebugSession('own-policy',model(),own,limits);own.permits=()=>false;
 assert.equal(t.step(0n,1).delivered,true);
});

test('enumerated policy and explicit domain retain receivers for admission candidates and breakpoint equality',()=>{
 class Enumerated {
  kind='enumerated';label='private-domain';#value=1;
  candidates(){return [this.#value];}
  equal(a,b){return a===this.#value&&a===b;}
 }
 const p=new Enumerated(),s=new DebugSession('enumerated-receiver',model(),p,limits);
 p.candidates=()=>[];p.equal=()=>false;
 const token=s.candidates(0n,0,1).candidates[0];
 s.addPreBreakpoint(()=>true);assert.equal(s.stepCandidate(0n,token).delivered,false);
 assert.equal(s.stepCandidate(0n,token).delivered,true);
 class Domain {#value=1;inputs(){return [this.#value];}equal(a,b){return a===this.#value&&a===b;}}
 const domain=new Domain(),t=new DebugSession('explicit-domain',model(),{kind:'unrestricted',label:'all'},limits,{inputDomain:domain});
 domain.inputs=()=>[];domain.equal=()=>false;
 assert.equal(t.stepCandidate(0n,t.candidates(0n,0,1).candidates[0]).delivered,true);
 const options={value:1,equalInputs(a,b){assert.equal(this,options);return a===this.value&&a===b;}};
 const u=new DebugSession('option-equality',model(),{kind:'unrestricted',label:'all'},limits,options);
 options.equalInputs=()=>false;u.addPreBreakpoint(()=>true);
 assert.equal(u.step(0n,1).delivered,false);assert.equal(u.step(0n,1).delivered,true);
});

test('pending breakpoint equality gets detached inputs and requires a synchronous boolean',()=>{
 for(const kind of ['declared','enumerated']){
  const m={...model(),cloneInput:i=>({...i}),step:(s,i)=>accepted(s+i.value,[])};
  let session;
  const equal=(a,b)=>{assert.throws(()=>session.cancel(0n),/owned/);a.value=99;b.value=99;return true;};
  const p=kind==='declared'?{kind,label:'ones',permits:(_s,i)=>i.value===1}:{kind,label:'ones',candidates:()=>[{value:1}],equal};
  session=new DebugSession(`detached-${kind}`,m,p,limits,{equalInputs:equal});session.addPreBreakpoint(()=>true);
  assert.equal(session.step(0n,{value:1}).delivered,false);
  assert.equal(session.step(0n,{value:1}).delivered,true);assert.equal(session.state,1);
  assert.equal(session.observation().input.value,1);
 }
 for(const invalid of [1,{},Promise.resolve(true)]){
  const s=new DebugSession('invalid-equality',model(),{kind:'unrestricted',label:'all'},limits,{equalInputs:()=>invalid});s.addPreBreakpoint(()=>true);
  s.step(0n,1);assert.throws(()=>s.step(0n,1),/equality must return boolean/);assert.equal(s.state,0);assert.equal(s.sequence,0n);
 }
});

test('candidate page permits one lookahead at exact work limit',()=>{
 let reads=0, enumerations=0;
 const m=model();m.inputs=function*(){enumerations++;for(const n of [1,2]){reads++;yield n;}};
 const s=new DebugSession('lookahead',m,{kind:'unrestricted',label:'out-of-domain-injection'},{...limits,maxCandidates:1});
 const page=s.candidates(0n,0,1);
 assert.equal(page.complete,false);assert.equal(page.nextOffset,1);assert.equal(page.candidates[0].input,1);assert.equal(reads,2);
 assert.throws(()=>s.candidates(0n,1,1),/budget/);assert.equal(enumerations,1);
});

test('admission retains query before state copy callbacks and defers unused copy failure',()=>{
 for(const kind of ['declared','enumerated']){
  let active=false, first;
  const m={...model(),cloneInput(i){const copy={...i};if(active&&!first)first=copy;return copy;},cloneState(s){if(active&&first)first.value=9;return s;},step:(s,i)=>accepted(s+i.value,[])};
  const p=kind==='declared'?{kind,label:'query',permits:(_s,i)=>i.value===1}:{kind,label:'query',candidates:()=>[{value:1}],equal:(a,b)=>a.value===b.value};
  const session=new DebugSession(`query-${kind}`,m,p,limits);active=true;
  assert.equal(session.step(0n,{value:1}).delivered,true);
 }
 let calls=0;const m={...model(),cloneInput(i){if(++calls===2)throw Error('copy');return i;}};
 const session=new DebugSession('empty-query',m,{kind:'enumerated',label:'empty',candidates:()=>[],equal:(a,b)=>a===b},limits);
 assert.throws(()=>session.step(0n,1),/not permitted/);assert.equal(session.revision,0n);
});

test('input copy failure leaves session terminal',()=>{
 const m=model();m.cloneInput=()=>{throw Error('input clone');};
 const session=new DebugSession('copy-terminal',m,{kind:'unrestricted',label:'all'},limits);
 assert.throws(()=>session.step(0n,1),/input clone/);assert.equal(session.phase,'ended');assert.equal(session.sequence,0n);
});
