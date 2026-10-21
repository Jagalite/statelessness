import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {compareEquivalence,qualificationParameters,accepted,check,metadata} from '../dist/index.js';
const equality={equalStates:(a,b)=>a===b,equalInputs:(a,b)=>a===b,equalOutputs:(a,b)=>a===b,equalProjections:(a,b)=>a===b};
const config=(extra={})=>({enabled:true,maxStates:256n,maxEdges:4096n,maxEquivalencePairs:65536n,...extra});
const proposal=(extra={})=>({name:'finite-example-projection',version:1,build:'fixture-proposal-v1',assumptions:{established:true,finiteDomain:'explicit closed fixture',equivalence:'equal observable behavior',effects:'ordered equality',transitionProperties:'exact named checks',queueOrder:'exact represented order',oracleHistory:'history represented in state',visibleTiming:'logical time represented'},requiredProperties:[{id:'state',phase:'state'},{id:'transition',phase:'transition'}],...extra});
function toggle(extra={}){let steps=0;const model={metadata:()=>metadata('toggle-research'),initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:()=>[null],step:s=>{steps++;return accepted(1-s,[1,2]);},checkState:()=>[check('state')],checkTransition:()=>[check('transition')],...extra};return {model,get steps(){return steps;}};}
const run=(model,states=[0,1],p=proposal(),cfg=config(),projection=()=>0,eq=equality)=>compareEquivalence(model,states,p,cfg,projection,eq);
test('research defaults disabled and unestablished assumptions execute no model graph callbacks',()=>{
 const fixture=toggle({initialState(){throw Error('initial executed');}});let projected=0;
 for(const [p,cfg,status]of [[proposal(),{},'Disabled'],[proposal({assumptions:{...proposal().assumptions,established:false}}),config(),'AssumptionsNotEstablished']]){const r=run(fixture.model,[0,1],p,cfg,()=>{projected++;return 0;});assert.equal(r.status,status);assert.equal(r.exact,null);assert.equal(r.reduced,null);assert.equal(r.materializedEdges,0n);}assert.equal(projected,0);
});
test('research equivalent toggle materializes once and records bounded qualification',()=>{
 const fixture=toggle(),r=run(fixture.model);assert.equal(r.status,'AgreesWithinDeclaredFixture');assert.equal(fixture.steps,2);assert.deepEqual(r.exact.states,[0,1]);assert.deepEqual(r.reduced.states,[0]);assert.equal(r.materializedEdges,2n);assert.equal(r.comparedPairs,1n);assert.equal(r.exact.edges,2n);assert.equal(r.reduced.edges,1n);
 const q=qualificationParameters(r);assert.equal(q['research.production_pruning_enabled'],'false');assert.equal(q['research.status'],'AgreesWithinDeclaredFixture');assert.equal(q['research.materialized_edges'],'2');assert.equal(q['research.proposal_build'],'fixture-proposal-v1');
});
test('research state, edge and equivalence budgets independently remain incomplete',()=>{
 for(const limit of [{maxStates:1n},{maxEdges:1n},{maxEquivalencePairs:0n}]){const fixture=toggle(),r=run(fixture.model,[0,1],proposal(),config(limit));assert.equal(r.status,'Incomplete');if(limit.maxStates)assert.equal(fixture.steps,0);if(limit.maxEdges){assert.equal(r.materializedEdges,1n);assert.equal(fixture.steps,1);assert.equal(r.exact,null);}if(Object.hasOwn(limit,'maxEquivalencePairs')){assert.equal(r.comparedPairs,0n);assert.notEqual(r.exact,null);}}
 assert.equal(run(toggle().model,[]).status,'Incomplete');assert.equal(run(toggle().model,[0,1],proposal(),config({maxEdges:2n,maxEquivalencePairs:1n,maxStates:2n})).status,'AgreesWithinDeclaredFixture');
});
test('research rejects invalid proposals and exact duplicate domains',()=>{
 for(const p of [proposal({name:' '}),proposal({build:''}),proposal({version:0}),proposal({assumptions:{...proposal().assumptions,effects:'\t'}}),proposal({requiredProperties:[{id:' ',phase:'state'}]}),proposal({requiredProperties:[{id:'state',phase:'state'},{id:'state',phase:'state'}]})])assert.throws(()=>run(toggle().model,[0,1],p));
 assert.throws(()=>run(toggle().model,[0,0,1]),/duplicate exact states/);
 const r=run(toggle().model,[0,1],proposal({requiredProperties:[{id:'state',phase:'state'},{id:'state',phase:'transition'}]}));assert.deepEqual(r.missingProperties,[{id:'state',phase:'transition'}]);
});
test('research domain closure and callback failures retain only completed edge counts',()=>{
 assert.match(run(toggle().model,[0]).limitation,/successor.*outside/);
 assert.match(run(toggle({initialState:()=>9}).model).limitation,/initial state.*outside/);
 const fixture=toggle({step:s=>{if(s===1)throw Error('fixture error');return accepted(1,[1,2]);}}),r=run(fixture.model);assert.equal(r.status,'Incomplete');assert.equal(r.materializedEdges,1n);assert.equal(r.exact,null);assert.equal(r.reduced,null);assert.match(r.limitation,/fixture error/);
 const checked=run(toggle({checkTransition(){throw Error('edge checker error');}}).model);assert.equal(checked.materializedEdges,1n);assert.equal(checked.exact,null);assert.match(checked.limitation,/edge checker error/);
});
test('research missing, empty and skipped requirements cannot qualify',()=>{
 const skipped=run(toggle({checkState:()=>[check('state','skipped','not evaluated')]}).model);assert.equal(skipped.status,'Incomplete');assert.equal(skipped.exact.skippedChecks,3n);assert.equal(skipped.reduced.skippedChecks,2n);
 const missing=run(toggle().model,[0,1],proposal({requiredProperties:[...proposal().requiredProperties,{id:'forgotten',phase:'state'}]}));assert.equal(missing.status,'Incomplete');assert.deepEqual(missing.missingProperties,[{id:'forgotten',phase:'state'}]);
 assert.equal(run(toggle().model,[0,1],proposal({requiredProperties:[]})).status,'Incomplete');
});
test('research compares every observation in Rust precedence order',()=>{
 const variants=[
  ['state checks differ',{checkState:s=>[check(s?'different':'state')]}],
  ['enabled input counts differ',{inputs:s=>s?[null,1]:[null]}],
  ['enabled input labels or order differ',{inputs:s=>s?[1,0]:[0,1]}],
  ['ordered effects differ',{step:s=>accepted(1-s,s?[2,1]:[1,2])}],
  ['transition disposition differs',{step:s=>({...accepted(1-s,[1,2]),disposition:{kind:s?'rejected':'accepted',reason:s?'different':''}})}],
  ['transition checks differ',{checkTransition:s=>[check('transition',s?'failed':'passed',s?'different':'')]}],
 ];
 for(const [reason,hooks]of variants){const r=run(toggle(hooks).model);assert.equal(r.status,'Counterexample');assert.equal(r.counterexamples[0].reason,reason);assert.deepEqual(r.counterexamples[0].leftPrefix,[]);assert.deepEqual(r.counterexamples[0].rightPrefix,[null]);}
});
function concurrent(){return toggle({metadata:()=>metadata('two-machines'),inputs:()=>['Left','Right','Observe'],step:(s,i)=>accepted(i==='Left'?s|1:i==='Right'?s|2:s),checkTransition:(s,i)=>[s===2&&i==='Observe'?check('transition','failed','right machine published before left dependency'):check('transition')] }).model;}
test('research lossy population projection exposes deterministic replayable lost failure',()=>{
 const model=concurrent(),r=run(model,[0,1,2,3],proposal(),config(),s=>(s&1)+((s>>1)&1));assert.equal(r.status,'Counterexample');assert.equal(r.missingViolations.length,1);const witness=r.missingViolations[0];assert.deepEqual(witness.property,{id:'transition',phase:'transition'});assert.deepEqual(witness.inputs,['Right','Observe']);assert.deepEqual(r.reduced.violations,[]);
 let state=0,failed=false;for(const input of witness.inputs){const transition=model.step(state,input);failed||=model.checkTransition(state,input,transition).some(c=>c.status==='failed');state=transition.state;}assert.equal(failed,true);
 const exact=run(model,[0,1,2,3],proposal(),config(),s=>s);assert.equal(exact.status,'AgreesWithinDeclaredFixture');assert.deepEqual(exact.exact.violations,exact.reduced.violations);assert.equal(exact.exact.violations.length,1);
});
test('research materializes disconnected declared states but compares only reachable pairs',()=>{
 let calls=0,projections=0;const model=toggle({step:s=>{calls++;return accepted(s,[1,2]);}}).model,r=run(model,[0,1],proposal(),config(),()=>{projections++;return 0;});assert.equal(calls,2);assert.equal(projections,2);assert.equal(r.materializedEdges,2n);assert.equal(r.comparedPairs,0n);assert.deepEqual(r.exact.states,[0]);assert.equal(r.status,'AgreesWithinDeclaredFixture');
});
test('research edge budget probes one candidate and never invokes its reducer',()=>{
 let pulls=0,steps=0;const model=toggle({inputs:function*(){pulls++;yield 0;pulls++;yield 1;},step:s=>{steps++;return accepted(1-s);}}).model;
 const r=run(model,[0,1],proposal(),config({maxEdges:1n}));assert.equal(r.status,'Incomplete');assert.equal(r.materializedEdges,1n);assert.equal(pulls,2);assert.equal(steps,1);
});
test('research uses preferred append check hooks exactly once per materialized state and edge',()=>{
 let states=0,edges=0;const model=toggle({checkState(){throw Error('legacy state');},checkTransition(){throw Error('legacy edge');},checkStateInto(_s,out){states++;out.push(check('state'));},checkTransitionInto(_b,_i,_t,out){edges++;out.push(check('transition'));}}).model;
 assert.equal(run(model).status,'AgreesWithinDeclaredFixture');assert.equal(states,2);assert.equal(edges,2);
});

test('research checks successor equivalence and gives concrete counterexamples priority over exhausted pair budget',()=>{
 const model=toggle({inputs:s=>s===0?[1,2]:[0],step:(s,i)=>accepted(s===0?i:s===1?3:2,[1,2])}).model;
 const report=run(model,[0,1,2,3],proposal(),config(),s=>s===1||s===2?'shared':String(s));assert.equal(report.status,'Counterexample');assert.ok(report.counterexamples.some(c=>c.reason==='successor equivalence classes differ'));
 const limited=run(model,[0,1,2,3],proposal(),config({maxEquivalencePairs:1n}));assert.equal(limited.status,'Counterexample');
});
test('research observes successor state checks even when quotient suppresses enqueue and phase identities stay separate',()=>{
 const model=toggle({checkState:s=>[s?check('same','failed','state failure'):check('same')],checkTransition:()=>[check('same','failed','edge failure')]}).model;
 const r=run(model,[0,1],proposal({requiredProperties:[{id:'same',phase:'state'},{id:'same',phase:'transition'}]}));assert.equal(r.exact.violations.length,2);assert.equal(r.reduced.violations.length,2);assert.deepEqual(r.reduced.violations.map(v=>v.property.phase),['state','transition']);assert.deepEqual(r.reduced.violations.map(v=>v.inputs),[[null],[null]]);
});
test('research accepts explicit metadata without codec and preserves private frozen model receivers',()=>{
 let calls=0;const model={initialState(){throw Error('disabled callback');}};
 const r=compareEquivalence(model,[0],proposal(),{},()=>0,equality,function(){assert.equal(this,model);calls++;return metadata('explicit-no-codec');});assert.equal(r.status,'Disabled');assert.equal(calls,1);assert.equal(r.model.name,'explicit-no-codec');
 class PrivateModel{#identity='private';metadata(){return metadata(this.#identity);}initialState(){return 0;}cloneState(s){assert.equal(this.#identity,'private');return s;}cloneInput(i){return i;}cloneOutput(o){return o;}inputs(){return [null];}step(s){return accepted(1-s);}checkStateInto(_s,out){assert.equal(this.#identity,'private');out.push(check('state'));}checkTransitionInto(_s,_i,_t,out){assert.equal(this.#identity,'private');out.push(check('transition'));}}
 assert.equal(run(Object.freeze(new PrivateModel())).status,'AgreesWithinDeclaredFixture');
});
test('research config is read once and metadata mutations cannot change captured controls or proposal',()=>{
 const reads={},raw={enabled:true,maxStates:2n,maxEdges:2n,maxEquivalencePairs:1n},cfg={};for(const key of Object.keys(raw))Object.defineProperty(cfg,key,{get(){reads[key]=(reads[key]??0)+1;return raw[key];}});
 const p=proposal(),model=toggle({metadata(){raw.enabled=false;raw.maxEdges=0n;p.name='changed';p.assumptions.effects='changed';p.requiredProperties.length=0;return metadata('snapshot');}}).model;
 const r=run(model,[0,1],p,cfg);assert.equal(r.status,'AgreesWithinDeclaredFixture');assert.equal(r.proposal.name,'finite-example-projection');assert.equal(r.proposal.assumptions.effects,'ordered equality');assert.equal(r.proposal.requiredProperties.length,2);assert.deepEqual(reads,{enabled:1,maxStates:1,maxEdges:1,maxEquivalencePairs:1});assert.equal(r.config.maxEdges,2n);
});
test('research callbacks receive detached model data and mutable projection keys need explicit cloning',()=>{
 const stateObjects=[{n:0},{n:1}],inputs=[{n:0}],outputs=[];
 const model={metadata:()=>metadata('owned'),initialState:()=>stateObjects[0],cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),inputs:()=>inputs,step(s,i){const n=s.n;s.n=99;i.n=99;const output={n};outputs.push(output);return accepted({n:1-n},[output]);},checkState:()=>[check('state')],checkTransition:()=>[check('transition')]};
 const savedKeys=[],eq={equalStates(a,b){const same=a.n===b.n;a.n=b.n=123;return same;},equalInputs(a,b){const same=a.n===b.n;a.n=b.n=123;return same;},equalOutputs(a,b){const same=a.n===b.n;a.n=b.n=123;return same;},equalProjections(a,b){const same=a.n===b.n;a.n=b.n=123;return same;},cloneProjection:k=>({...k})};
 const r=run(model,stateObjects,proposal(),config(),s=>{const key={n:0};savedKeys.push(key);s.n=123;return key;},eq);assert.equal(r.status,'Counterexample');assert.equal(r.counterexamples[0].reason,'ordered effects differ');assert.deepEqual(r.counterexamples[0].rightPrefix,[{n:0}]);assert.deepEqual(stateObjects,[{n:0},{n:1}]);assert.deepEqual(inputs,[{n:0}]);assert.deepEqual(savedKeys,[{n:0},{n:0}]);
 inputs[0].n=88;outputs.forEach(o=>{o.n=88;});savedKeys.forEach(k=>{k.n=88;});assert.deepEqual(r.counterexamples[0].rightPrefix,[{n:0}]);
 const withoutClone={...eq,cloneProjection:undefined};const rejected=run(model,stateObjects,proposal(),config(),()=>({n:0}),withoutClone);assert.equal(rejected.status,'Incomplete');assert.match(rejected.limitation,/cloneProjection/);
});
test('research projection and equality errors remain partial unqualified evidence',()=>{
 for(const [projection,eq,message]of [[()=>{throw Error('projection failed');},equality,'projection failed'],[()=>0,{...equality,equalProjections(){throw Error('key comparison failed');}},'key comparison failed'],[()=>0,{...equality,equalOutputs(){return 'yes';}},'return boolean']]){const r=run(toggle().model,[0,1],proposal(),config(),projection,eq);assert.equal(r.status,'Incomplete');assert.equal(r.materializedEdges,2n);assert.match(r.limitation,new RegExp(message));}
});
test('research rejects non-reflexive projection relations before traversing a cyclic quotient',()=>{
 for(const [project,eq]of [[()=>NaN,equality],[()=>0,{...equality,equalProjections:()=>false}]]){const r=run(toggle().model,[0,1],proposal(),config(),project,eq);assert.equal(r.status,'Incomplete');assert.match(r.limitation,/reflexive/);assert.equal(r.materializedEdges,2n);}
});
test('research blank declarations use Rust Unicode White_Space rather than JavaScript trim',()=>{
 for(const p of [proposal({name:'\u0085'}),proposal({build:'\u0085'}),proposal({assumptions:{...proposal().assumptions,effects:'\u0085'}}),proposal({requiredProperties:[{id:'\u0085',phase:'state'}]})])assert.throws(()=>run(toggle().model,[0,1],p,{}));
 let called=0;const p=proposal({name:'\ufeff',build:'\ufeff',assumptions:Object.fromEntries(Object.entries(proposal().assumptions).map(([k,v])=>[k,typeof v==='string'?'\ufeff':v])),requiredProperties:[{id:'\ufeff',phase:'state'}]});const model=toggle({metadata(){called++;return metadata('bom');},initialState(){throw Error('disabled initial');}}).model;assert.equal(run(model,[0,1],p,{}).status,'Disabled');assert.equal(called,1);
});
test('research captures iterator next once and callback mutation cannot replace an active materialization',()=>{
 let nextReads=0,pulls=0,steps=0;const originalStep=s=>{steps++;return accepted(1-s);};
 const model=toggle({inputs(){let done=false;return {[Symbol.iterator](){return {get next(){nextReads++;return function(){pulls++;if(done)return {done:true};done=true;model.step=()=>{throw Error('replaced reducer');};return {done:false,value:null};};}};}};},step:originalStep}).model;
 const r=run(model);assert.equal(r.status,'AgreesWithinDeclaredFixture');assert.equal(steps,2);assert.equal(nextReads,2);assert.equal(pulls,4);
});

const researchCorpus=JSON.parse(readFileSync(process.env.STATELESS_RESEARCH_CORPUS??new URL('../../conformance/research-equivalence-v1.json',import.meta.url),'utf8'));
const camel=name=>name.replace(/_([a-z])/g,(_all,c)=>c.toUpperCase());
const camelObject=value=>Array.isArray(value)?value.map(camelObject):value!==null&&typeof value==='object'?Object.fromEntries(Object.entries(value).map(([k,v])=>[camel(k),camelObject(v)])):value;
function normalizedReport(value,key=''){
 if(typeof value==='bigint'||key==='declared_states')return String(value);
 if(Array.isArray(value))return value.map(v=>normalizedReport(v));
 if(value!==null&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>{const name=k.replace(/[A-Z]/g,c=>'_'+c.toLowerCase());return [name,normalizedReport(v,name)];}));
 return value;
}
for(const row of researchCorpus.cases)test(`research-equivalence-v1 shared ${row.case}`,()=>{
 const defaults=researchCorpus.defaults,wireProposal={...defaults.proposal,...row.proposal_overrides,assumptions:{...defaults.proposal.assumptions,...row.proposal_overrides?.assumptions}},p=camelObject(wireProposal),wireConfig={...defaults.config,...row.config_overrides},cfg={enabled:wireConfig.enabled,maxStates:BigInt(wireConfig.max_states),maxEdges:BigInt(wireConfig.max_edges),maxEquivalencePairs:BigInt(wireConfig.max_equivalence_pairs)};
 const scenario=row.scenario,events=[],event=name=>{events.push(name);if(row.error_at===name)throw Error(`fixture error at ${name}`);};
 const table=s=>{const result=scenario.rows.find(r=>r.state===s);assert.ok(result,`missing fixture row ${s}`);return result;},checks=values=>values.map(c=>check(c.id,c.status,c.details??''));
 const model={metadata(){event('metadata');return {...defaults.metadata,...row.metadata_overrides};},initialState(){event('initial');return scenario.initial;},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
  checkState(s){event(`state:${s}`);return checks(table(s).checks);},
  inputs(s){event(`inputs:${s}`);let index=0;return {[Symbol.iterator](){return {next(){event(`next:${s}:${index}`);const edge=table(s).inputs[index++];return edge?{done:false,value:edge.input}:{done:true};}};}};},
  step(s,i){event(`step:${s}:${i}`);const edge=table(s).inputs.find(e=>e.input===i);assert.ok(edge);return {state:edge.target,outputs:[...edge.outputs],disposition:{kind:edge.disposition.kind,reason:edge.disposition.reason??''}};},
  checkTransition(s,i){event(`transition:${s}:${i}`);return checks(table(s).inputs.find(e=>e.input===i).checks);}
 };
 let report=null,error=null;try{report=run(model,scenario.states,p,cfg,s=>{event(`projection:${s}`);return scenario.keys[scenario.states.indexOf(s)];});}catch(cause){error=cause.message;}
 assert.equal(error,row.expected.error);assert.deepEqual(events,row.expected.callback_log);assert.deepEqual(report===null?null:normalizedReport(report),row.expected.report);assert.deepEqual(report===null?null:qualificationParameters(report),row.expected.qualification_parameters);
});
for(const row of researchCorpus.native_admission_cases)test(`research-equivalence-v1 native ${row.case}`,()=>{
 let calls=0;const model=toggle({metadata(){calls++;if(row.kind==='metadata')throw Error('native metadata');return metadata('native');}}).model;
 if(row.kind==='argument'||row.kind==='typescript_number'){
  const p=proposal(),cfg=config();if(row.case==='proposal-version-over-u32')p.version=Number(row.value);else cfg.maxEdges=row.kind==='typescript_number'?Number(row.value):BigInt(row.value);
  assert.throws(()=>run(model,[0,1],p,cfg));assert.equal(calls,0);return;
 }
 if(row.kind==='metadata'){assert.throws(()=>run(model),/native metadata/);return;}
 const eq={...equality};let projection=()=>0;
 if(row.case==='projection-nonreflexive-key')projection=()=>NaN;
 if(row.case==='projection-callback-throws')projection=()=>{throw Error('native projection');};
 if(row.kind==='equality')eq.equalProjections=()=>{throw Error('native equality');};
 if(row.kind==='iterator_next')model.inputs=()=>({[Symbol.iterator](){return {next(){throw Error('native next');}};}});
 if(row.kind==='async')projection=async()=>0;
 const report=run(model,[0,1],proposal(),config(),projection,eq);assert.equal(report.status,'Incomplete');assert.notEqual(report.status,'AgreesWithinDeclaredFixture');
});
test('research retains admitted domain topology across metadata mutation',()=>{
 const states=[0,1],model=toggle({metadata(){states.splice(0,2,99,100,101);return metadata('captured topology');}}).model;
 const report=run(model,states,proposal(),config({maxStates:2n}));assert.equal(report.status,'AgreesWithinDeclaredFixture');assert.equal(report.declaredStates,2);assert.deepEqual(report.exact.states,[0,1]);
});
test('research iterator cleanup cannot rewrite or replace the primary materialization error',()=>{
 const primary=Error('original reducer failure');let cleanup=0;
 const model=toggle({inputs(){let yielded=false;return {[Symbol.iterator](){return {next(){if(yielded)return {done:true};yielded=true;return {done:false,value:null};},return(){cleanup++;primary.message='rewritten by cleanup';throw Error('secondary cleanup failure');}};}};},step(){throw primary;}}).model;
 const report=run(model);assert.equal(report.status,'Incomplete');assert.equal(report.limitation,'original reducer failure');assert.equal(report.materializedEdges,0n);assert.equal(report.exact,null);assert.equal(cleanup,1);
});
