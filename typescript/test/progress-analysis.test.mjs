import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ProgressGraph,analyzeProgress,stronglyConnectedComponents,reverseReachable,exploreGraph,accepted,ConfigError} from '../dist/index.js';
// progress-analysis-v1 (S3): hand-authored graphs independent of any application.
const fixtures=JSON.parse(readFileSync(new URL('../../conformance/progress-analysis-v1.json',import.meta.url),'utf8'));
const graph=(n,edges,unmeasured=[])=>new ProgressGraph(n,edges.map(([source,target,fair])=>({source,target,fair})),Array.from({length:n},(_,v)=>!unmeasured.includes(v)));
test('progress-analysis-v1 fixture document',()=>{assert.equal(fixtures.version,1);assert.equal(fixtures.cases.length,9);});
for(const c of fixtures.cases)test(`progress-analysis-v1 fixture ${c.case}`,()=>{
 const r=analyzeProgress(graph(c.nodes,c.edges,c.unmeasured),v=>c.kinds[v],c.ranks?v=>BigInt(c.ranks[v]):undefined);
 const x=c.expected;
 assert.deepEqual(r.classes,x.classes);assert.deepEqual(r.closedNonterminalComponents,x.closed);assert.deepEqual(r.unsettledTransactionStates,x.unsettled);
 assert.equal(r.settledTransactionStates,x.settled_transactions);assert.equal(r.unmeasuredComponents,x.unmeasured_components);assert.equal(r.graphComplete,x.graph_complete);
});
test('class counts, stuck states and maximum rank',()=>{
 const waits=analyzeProgress(graph(3,[[0,1,true],[0,2,true],[1,1,false]]),v=>['transaction','intentionalWait','externalWait'][v]);
 assert.equal(waits.stableIntentionalWaitStates,1);assert.equal(waits.externalWaitStates,1);assert.equal(waits.transactionStates,1);
 assert.deepEqual(waits.closedNonterminalComponents,[],'a self-looping wait is settled');
 const cycle=analyzeProgress(graph(3,[[0,1,true],[1,0,true],[2,0,true]]),()=>'transaction',()=>4n);
 assert.equal(cycle.maximumRank,4n);
 const only=analyzeProgress(graph(2,[[0,1,false]]),v=>['transaction','terminal'][v]);
 assert.deepEqual(only.stuckStates,[0]);
 const cut=analyzeProgress(graph(2,[[0,1,true]],[1]),()=>'transaction');
 assert.equal(cut.unmeasuredStates,1);assert.equal(cut.graphComplete,false);
});
test('iterative SCC handles deep graphs; reverse reachability filters edges',()=>{
 const n=200_000,chain=Array.from({length:n-1},(_,v)=>[v,v+1]);chain.push([n-1,0]);
 const components=stronglyConnectedComponents(n,chain);
 assert.equal(components.length,1);assert.equal(components[0].length,n);
 assert.deepEqual(stronglyConnectedComponents(4,[[0,1],[1,0],[2,3]]),[[0,1],[2],[3]]);
 assert.deepEqual(reverseReachable(4,[{source:0,target:1,fair:true},{source:2,target:1,fair:false}],[1],e=>e.fair),[true,true,false,false]);
});
test('graph from search report',()=>{
 const edges={0:[1,2],1:[3],2:[2]};
 const m={initialState:()=>0,step:(_s,i)=>accepted(i),checkState:()=>[],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>edges[s]??[],equalStates:(a,b)=>a===b,hashState:s=>s};
 const report=exploreGraph(m);
 const g=ProgressGraph.fromReport(report,e=>e.input!==2);
 assert.deepEqual(g.edges,[{source:0,target:1,fair:true},{source:0,target:2,fair:false},{source:1,target:3,fair:true},{source:2,target:2,fair:false}]);
 const r=analyzeProgress(g,v=>report.nodes[v].state===3?'terminal':'transaction');
 assert.deepEqual(r.classes,['transactionProgress','transactionProgress','stuck','terminal']);assert.deepEqual(r.closedNonterminalComponents,[[2]]);
 const bounded=exploreGraph(m,{maxStates:100,maxTransitions:100,maxDepth:1});
 assert.deepEqual(ProgressGraph.fromReport(bounded,()=>true).measured,[true,false,false]);
 assert.throws(()=>new ProgressGraph(1,[{source:0,target:1,fair:true}],[true]),ConfigError);
 const streamed=exploreGraph(m,undefined,{},{retention:{retainEdges:false}});
 assert.throws(()=>ProgressGraph.fromReport(streamed,()=>true),/retained edges/);
});
