import test from 'node:test';
import assert from 'node:assert/strict';
import {exploreGraph,compareReduction,reductionEquivalent,validateDiamonds,analyzeProgress,ProgressGraph,accepted,check,disposition} from '../dist/index.js';
// reduction-v1 (S4). Inputs 10..19 are "late result" inputs; the class is
// input/10. Unless a test overrides step, input k leads to state k%10.
const model=({step=(_s,i)=>accepted(i%10,[i%10]),failEdge=[],inputs={0:[10,11,12,1],1:[2],2:[]}}={})=>({
 initialState:()=>0,step,checkState:()=>[check('ok')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,
 checkTransition:(_s,i)=>failEdge.includes(i)?[check('late','failed',String(i))]:[],inputs:s=>inputs[s]??[],equalStates:(a,b)=>a===b,hashState:s=>s});
const byDecade=()=>({eligibleClass:(_s,i)=>i>=10?'late':null,projection:(_i,t)=>Uint8Array.from(t.outputs)});
const collect={kind:'collect',maxRetainedFindings:10,expandFailingSuccessors:true};
const explore=(m,reduction,failurePolicy=collect)=>exploreGraph(m,undefined,{},{failurePolicy,reduction});
const same=extra=>model({step:(_s,i)=>accepted(i>=10?1:i,[7]),...extra});
test('equivalent edges collapse into a representative',()=>{
 const distinct=explore(model(),byDecade());
 assert.equal(distinct.reducedEdges,0n);assert.deepEqual(distinct.reductionDecisions.map(d=>d.rejected.length),[0,1,2]);
 assert.deepEqual(distinct.reductionDecisions[1].rejected,[{representative:0,reason:'projection_differs'}]);
 const m=same(),unreduced=explore(m),reduced=explore(m,byDecade());
 assert.deepEqual(reduced.edges.map(e=>e.target),[{kind:'admitted',node:1},{kind:'reduced',edge:0},{kind:'reduced',edge:0},{kind:'duplicate',node:1},{kind:'admitted',node:2}]);
 assert.equal(reduced.reducedEdges,2n);assert.equal(reduced.transitions,unreduced.transitions,'reduction never skips execution');
 assert.deepEqual(reduced.reductionDecisions,[{edge:0,source:0,equivalenceClass:'late',reducedTo:null,rejected:[]},{edge:1,source:0,equivalenceClass:'late',reducedTo:0,rejected:[]},{edge:2,source:0,equivalenceClass:'late',reducedTo:0,rejected:[]}]);
 const events=[];exploreGraph(m,undefined,{},{failurePolicy:collect,reduction:byDecade(),events:e=>{if(e.kind==='edgeReduced')events.push(`${e.edge}->${e.representative}`);}});
 assert.deepEqual(events,['1->0','2->0']);
 const comparison=compareReduction(m,unreduced,reduced);
 assert.ok(reductionEquivalent(comparison),JSON.stringify(comparison));
 // Review regression: progress is compared only when predicates are supplied.
 assert.equal(comparison.progressMatch,null,'progress is not compared without predicates');
 const progress=r=>analyzeProgress(ProgressGraph.fromReport(r,()=>true),v=>r.nodes[v].state===2?'terminal':'transaction');
 assert.deepEqual(progress(reduced),progress(unreduced));
 const withProgress=compareReduction(m,unreduced,reduced,{fair:()=>true,kind:s=>s===2?'terminal':'transaction'});
 assert.equal(withProgress.progressMatch,true);assert.ok(reductionEquivalent(withProgress));
});
test('a progress difference is not equivalent',()=>{
 // Same states 0 -> 1, same dispositions and checks; only the input differs, and fairness depends on it.
 const oneEdge=input=>model({step:()=>accepted(1),inputs:{0:[input]}});
 const a=explore(oneEdge(5)),b=explore(oneEdge(6));
 assert.ok(reductionEquivalent(compareReduction(oneEdge(5),a,b)),'structure alone matches');
 const c=compareReduction(oneEdge(5),a,b,{fair:e=>e.input===5,kind:s=>s===1?'terminal':'transaction'});
 assert.equal(c.progressMatch,false);assert.deepEqual(c.progressMismatches,[0]);assert.equal(reductionEquivalent(c),false);
});
test('rejected equivalences are inspectable',()=>{
 const successor=model({step:(_s,i)=>accepted(i%10,[7])});
 assert.deepEqual(explore(successor,byDecade()).reductionDecisions[1].rejected,[{representative:0,reason:'successor_differs'}]);
 const failing=same({failEdge:[11]}),checks=explore(failing,byDecade());
 assert.deepEqual(checks.reductionDecisions[1].rejected,[{representative:0,reason:'checks_differ'}]);
 assert.equal(checks.reductionDecisions[2].reducedTo,0);assert.equal(checks.findingCount,1n);
 const rejected=model({step:(_s,i)=>({state:i>=10?1:i,outputs:[7],disposition:i===11?disposition('rejected','stale'):disposition()})});
 assert.deepEqual(explore(rejected,byDecade()).reductionDecisions[1].rejected.map(r=>r.reason),['disposition_differs']);
 assert.ok(reductionEquivalent(compareReduction(failing,explore(failing),checks)));
});
test('negative control: incorrect reduction is detected',()=>{
 const m=same({failEdge:[12]}),unreduced=explore(m),reduced=explore(m,byDecade());
 assert.ok(reductionEquivalent(compareReduction(m,unreduced,reduced)));
 const missingState={...reduced,nodes:reduced.nodes.slice(0,-1),edges:reduced.edges.map(e=>({...e}))};missingState.edges[4].target={kind:'rejected',reason:'tampered'};
 const a=compareReduction(m,unreduced,missingState);
 assert.equal(reductionEquivalent(a),false);assert.deepEqual(a.missingInReduced,[2]);assert.deepEqual(a.unrepresentedEdges,[4]);
 assert.equal(compareReduction(m,unreduced,{...reduced,findingCount:0n}).findingsMatch,false);
 const wrongTarget={...reduced,edges:reduced.edges.map(e=>({...e}))};wrongTarget.edges[3].target={kind:'duplicate',node:2};
 const c=compareReduction(m,unreduced,wrongTarget);
 // Unreduced 0->1 edges remain represented by edge 0; the 0->2 edge has no unreduced counterpart.
 assert.equal(reductionEquivalent(c),false);assert.deepEqual(c.unrepresentedEdges,[]);assert.deepEqual(c.extraReducedEdges,[3]);
 assert.throws(()=>compareReduction(m,explore(m,undefined,{kind:'stopAtFirst'}),reduced),/finished searches/);
});
test('diamond validation',()=>{
 const counters={initialState:()=>[0,0],step:(s,i)=>{const n=[...s];n[i]++;return accepted(n);},checkState:()=>[],cloneState:s=>[...s],cloneInput:i=>i,cloneOutput:o=>o,
  inputs:s=>[0,1].filter(i=>s[i]<2),equalStates:(a,b)=>a[0]===b[0]&&a[1]===b[1],hashState:s=>s[0]*3+s[1]};
 const report=exploreGraph(counters),eq=(a,b)=>a===b,none=()=>new Uint8Array();
 const ok=validateDiamonds(counters,report,()=>true,eq,none);
 assert.equal(ok.termination,'search_complete');assert.equal(ok.validated,4n);assert.deepEqual(ok.nonCommuting,[]);
 const arithmetic={initialState:()=>1,step:(s,i)=>accepted(i===0?s*2:s+1,[i]),checkState:()=>[],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>s<3?[0,1]:[],equalStates:eq,hashState:s=>s};
 const bad=validateDiamonds(arithmetic,exploreGraph(arithmetic,{maxStates:100,maxTransitions:100,maxDepth:0}),()=>true,eq,none);
 assert.equal(bad.checkedNodes,0);assert.equal(bad.validated,0n);
 const deep=validateDiamonds(arithmetic,exploreGraph(arithmetic),()=>true,eq,none);
 assert.equal(deep.validated,1n);assert.equal(deep.nonCommuting.length,1);
 assert.deepEqual(deep.nonCommuting[0],{node:0,first:0,second:1,reason:'state_differs'});
 const observed=validateDiamonds(counters,report,()=>true,eq,(i,t)=>Uint8Array.of(i,t.state[0]));
 assert.deepEqual(observed.nonCommuting.map(w=>w.reason),['observations_differ','observations_differ','observations_differ','observations_differ']);
 assert.equal(validateDiamonds(counters,report,()=>true,eq,none,{cancellation:()=>true}).termination,'cancelled');
});
// MigrationNegativeControl: each control introduces one defect and requires the evidence to expose it.
const control=({edges={0:[1,2],1:[3],2:[3]},equal=(a,b)=>a===b,hash=s=>s,failing=[3]}={})=>({
 initialState:()=>0,step:(_s,i)=>accepted(i),checkState:s=>[failing.includes(s)?check('bad','failed',String(s)):check('bad')],
 cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>edges[s]??[],equalStates:equal,hashState:hash});
const collectAll={kind:'collect',maxRetainedFindings:8,expandFailingSuccessors:true};
const run=(m,config)=>exploreGraph(m,config,{},{failurePolicy:collectAll});
test('negative control: missing transition',()=>{
 const c=compareReduction(control(),run(control()),run(control({edges:{0:[1,2],1:[3]}})));
 assert.equal(reductionEquivalent(c),false);assert.deepEqual(c.unrepresentedEdges,[3]);assert.equal(c.findingsMatch,false);
});
test('negative control: incorrect identity merging',()=>{
 const merged=control({equal:(a,b)=>a===b||(a>0&&a<3&&b>0&&b<3),hash:s=>s===2?1:s});
 const lossy=run(merged);assert.equal(lossy.nodes.length,3);
 const c=compareReduction(control(),run(control()),lossy);
 assert.equal(reductionEquivalent(c),false);assert.deepEqual(c.missingInReduced,[2]);
});
test('negative control: suppressed check',()=>{
 const checked=run(control()),suppressed=run(control({failing:[]}));
 assert.equal(checked.findingCount,2n);assert.equal(suppressed.findingCount,0n);
 const c=compareReduction(control(),checked,suppressed);
 assert.equal(c.findingsMatch,false);assert.deepEqual(c.unrepresentedEdges,[2,3],'edges carry their check batches');
});
test('negative control: false progress',()=>{
 const truth=analyzeProgress(new ProgressGraph(2,[{source:0,target:1,fair:true},{source:1,target:0,fair:true}],[true,true]),()=>'transaction');
 assert.deepEqual(truth.closedNonterminalComponents,[[0,1]]);
 const truncated=analyzeProgress(new ProgressGraph(2,[{source:0,target:1,fair:true}],[true,false]),()=>'transaction');
 assert.deepEqual(truncated.closedNonterminalComponents,[]);assert.equal(truncated.graphComplete,false,'a cutoff is not reported as progress');
 assert.notDeepEqual(truth,truncated);
 const bounded=run(control(),{maxStates:100,maxTransitions:100,maxDepth:1});
 assert.equal(analyzeProgress(ProgressGraph.fromReport(bounded,()=>true),()=>'transaction').graphComplete,false);
});
