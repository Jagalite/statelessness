import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {enumerateStates,exploreGraph,GraphSearchError,graphFailure,findingsTruncated,accepted,check} from '../dist/index.js';
// graph-search-v1 (S1/S2). Shared fixtures use the spellings of spec/graph-search-v1.md "Fixture spellings".
const load=name=>JSON.parse(readFileSync(new URL(`../../conformance/${name}`,import.meta.url),'utf8'));
const spellEvent=e=>{switch(e.kind){
 case 'rootAdmitted':return `root:${e.node}`;case 'expansionStarted':return `expand:${e.node}`;
 case 'edgeEvaluated':return `edge:${e.edge.id}:${e.edge.source}:${e.edge.input}`;
 case 'finding':return `finding:${e.index}:${e.edge??'-'}:${e.violations.map(v=>`${v.phase}/${v.check.id}`).join(',')}`;
 case 'successorAdmitted':return `admit:${e.edge}:${e.node}`;case 'duplicateEdge':return `dup:${e.edge}:${e.existing}`;
 case 'edgeReduced':return `reduced:${e.edge}:${e.representative}`;case 'successorRejected':return `reject:${e.edge}:${e.reason}`;
 case 'expansionFinished':return `finish:${e.node}:${e.status.kind}`;case 'layerCompleted':return `layer:${e.depth}`;
 case 'searchFinished':return `end:${e.termination}`;default:throw Error(`unknown event ${e.kind}`);}};
const spellTarget=t=>t.kind==='admitted'||t.kind==='duplicate'?`${t.kind}:${t.node}`:t.kind==='reduced'?`reduced:${t.edge}`:t.kind==='rejected'?`rejected:${t.reason}`:'pending';
const spellCoverage=c=>c.kind==='exhaustive'?'exhaustive':c.kind==='bounded'?`bounded:${[c.depthCutoff?'depth_cutoff':null,c.failurePruned?'failure_pruned':null].filter(Boolean).join('+')}`:`incomplete:${c.reason}`;
const expansion=(kind,reason)=>reason===undefined?{kind}:{kind,reason};

// Default options must execute the bfs-controls-v1 callback sequence and report
// the same counters and witness as the portable kernel.
const bfs=load('bfs-controls-v1.json');
for(const row of bfs.cases)test(`exploreGraph default options reproduce bfs-controls-v1 ${row.case}`,()=>{
 const execute=graph=>{
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
  try{
   if(graph){const r=exploreGraph(model,config,limits,{observer});const f=graphFailure(r);
    return {result:{termination:r.termination,states:r.states,transitions:r.transitions,max_depth_reached:r.maxDepthReached,skipped_checks:r.skippedChecks,estimated_retained_state_bytes:r.estimatedRetainedStateBytes,inputs:f?.inputs??null,violations:f?.violations??null},events};}
   const r=enumerateStates(model,config,limits,observer);
   return {result:{termination:r.termination,states:r.states,transitions:BigInt(r.transitions),max_depth_reached:r.max_depth_reached,skipped_checks:BigInt(r.skipped_checks),estimated_retained_state_bytes:r.estimated_retained_state_bytes,inputs:r.failure?.inputs??null,violations:r.failure?.violations??null},events};
  }catch(e){return {result:`error ${e.message}`,events};}
 };
 const portable=execute(false),extended=execute(true);
 assert.deepEqual(extended.result,portable.result);assert.deepEqual(extended.events,portable.events);
 // The comparison is meaningful only if the portable kernel still matches the corpus.
 assert.deepEqual(portable.events,row.events);
});

const intModel=(edges,{hash,failState=[],failEdge=[]}={})=>({
 initialState:()=>0,step:(_s,i)=>accepted(i,[i*10]),
 checkState:s=>failState.includes(s)?[check('ok'),check('bad_state','failed',`state ${s}`)]:[check('ok')],
 checkTransition:(b,i)=>failEdge.some(([x,y])=>x===b&&y===i)?[check('bad_edge','failed',`${b}->${i}`)]:[],
 cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:s=>edges[s]??[],equalStates:(a,b)=>a===b,hashState:s=>hash?hash(s):s});
const policyOf=p=>p.kind==='collect'?{kind:'collect',maxRetainedFindings:p.max_retained_findings,expandFailingSuccessors:p.expand_failing_successors}:{kind:'stopAtFirst'};
const graphFixtures=load('graph-search-v1.json');
test('graph-search-v1 fixture document',()=>{assert.equal(graphFixtures.version,1);assert.equal(graphFixtures.cases.length,10);});
for(const c of graphFixtures.cases)test(`graph-search-v1 fixture ${c.case}`,()=>{
 const m=intModel(c.edges,{hash:c.hash==='constant'?()=>0:undefined,failState:c.fail_state,failEdge:c.fail_edge});
 const events=[];
 const r=exploreGraph(m,{maxStates:c.config.max_states,maxTransitions:BigInt(c.config.max_transitions),maxDepth:c.config.max_depth},{},{failurePolicy:policyOf(c.policy),events:e=>{events.push(spellEvent(e));}});
 const x=c.expected;
 assert.deepEqual(events,x.events);
 assert.deepEqual(r.edges.map(e=>spellTarget(e.target)),x.targets);
 assert.equal(r.termination,x.termination);assert.equal(spellCoverage(r.coverage),x.coverage);
 assert.equal(r.states,x.states);assert.equal(r.transitions,BigInt(x.transitions));
 assert.deepEqual(r.findings.map(f=>f.witness),x.finding_witnesses);
 assert.deepEqual(r.findings.map(f=>f.requiresContinuedEvidence),x.requires_continued_evidence);
 assert.equal(r.findingCount,BigInt(x.finding_count));
});

const diamond={0:[1,2,1],1:[3],2:[3]},chain={0:[1],1:[2],2:[3],3:[4]};
function search(m,{config,limits={},policy,retention,cancelAfter}={}){
 const events=[];let cancelled=false;
 const r=exploreGraph(m,config,{...limits,control:{...limits.control,cancellation:()=>cancelled}},{failurePolicy:policy,retention,events:e=>{const t=spellEvent(e);events.push(t);if(t===cancelAfter)cancelled=true;}});
 return [r,events];
}
const targets=r=>r.edges.map(e=>spellTarget(e.target));
const collect=(maxRetainedFindings,expandFailingSuccessors)=>({kind:'collect',maxRetainedFindings,expandFailingSuccessors});
test('diamond graph IDs, payloads and counters',()=>{
 const [r]=search(intModel(diamond));
 assert.deepEqual(r.nodes.map(n=>n.state),[0,1,2,3]);assert.deepEqual(r.nodes.map(n=>n.parent),[null,0,0,1]);assert.deepEqual(r.nodes.map(n=>n.depth),[0,1,1,2]);
 assert.deepEqual(r.nodes.map(n=>n.admittingEdge),[null,0,1,3]);assert.deepEqual(r.edges.map(e=>e.id),[0,1,2,3,4]);
 assert.deepEqual(r.edges.map(e=>e.outputs),[[10],[20],[10],[30],[30]]);
 assert.equal(r.transitions,5n);assert.equal(r.graphEdges,5n);assert.equal(r.duplicateEdges,2n);assert.equal(r.rejectedSuccessors,0n);
 assert.equal(r.frontierSize,0);assert.equal(r.completedLayers,3);assert.equal(graphFailure(r),null);assert.ok(r.estimatedGraphBytes>0n);
 const [loop]=search(intModel({0:[0,0]}));assert.deepEqual(targets(loop),['duplicate:0','duplicate:0']);assert.equal(loop.nodes.length,1);
});
test('duplicate failing edge keeps evidence; unresolved edge counts as rejected even when streamed',()=>{
 const [r,events]=search(intModel(diamond,{failEdge:[[2,3]]}),{policy:collect(10,false)});
 assert.ok(events.includes('finding:0:4:transition/bad_edge'));assert.deepEqual(r.edges[4].target,{kind:'duplicate',node:3});
 assert.deepEqual(r.findings.map(f=>f.witness),[[2,3]]);assert.equal(r.findings[0].edge,4);assert.equal(spellCoverage(r.coverage),'exhaustive');
 const [stop]=search(intModel(diamond,{failEdge:[[2,3]]}));
 assert.equal(stop.termination,'failure_found');assert.equal(spellCoverage(stop.coverage),'incomplete:failure_found');
 assert.deepEqual(stop.edges.at(-1).target,{kind:'rejected',reason:'failure_found'});assert.deepEqual(stop.nodes[2].expansion,expansion('interrupted','failure_found'));
 assert.deepEqual(graphFailure(stop).inputs,[2,3]);assert.equal(stop.rejectedSuccessors,1n);
 // Review regression: the unresolved edge is counted even when edges are only streamed.
 const [streamed]=search(intModel(diamond,{failEdge:[[2,3]]}),{retention:{retainEdges:false}});
 assert.equal(streamed.rejectedSuccessors,1n);assert.deepEqual(streamed.edges,[]);assert.equal(streamed.edgesRetained,false);
 const quiet=exploreGraph(intModel(diamond,{failEdge:[[2,3]]}),undefined,{},{retention:{retainEdges:false}});
 assert.equal(quiet.rejectedSuccessors,1n,'no sink and no retention still counts the unresolved edge');
});
test('state, transition caps and depth coverage',()=>{
 const [s,events]=search(intModel(diamond),{config:{maxStates:2,maxTransitions:1000000,maxDepth:100}});
 assert.deepEqual(events.slice(-3),['edge:1:0:2','reject:1:state_limit','end:state_limit']);
 assert.deepEqual(s.nodes.map(n=>n.expansion),[expansion('interrupted','state_limit'),expansion('unexpanded')]);assert.equal(s.frontierSize,2);assert.equal(s.completedLayers,0);assert.equal(s.rejectedSuccessors,1n);
 const [t]=search(intModel(diamond),{config:{maxStates:100,maxTransitions:2,maxDepth:100}});
 assert.equal(t.termination,'transition_limit');assert.equal(t.edges.length,2);assert.deepEqual(t.nodes[0].expansion,expansion('interrupted','transition_limit'));
 const [one]=search(intModel(diamond),{config:{maxStates:100,maxTransitions:100n,maxDepth:1}});
 assert.equal(spellCoverage(one.coverage),'bounded:depth_cutoff');assert.equal(one.frontierSize,2);
 const [leaf]=search(intModel(diamond),{config:{maxStates:100,maxTransitions:100n,maxDepth:2}});
 assert.equal(spellCoverage(leaf.coverage),'exhaustive','reaching max_depth at a leaf is not a cutoff');
});
test('cancellation and deadline',()=>{
 const [r,events]=search(intModel(diamond),{cancelAfter:'admit:3:3'});
 assert.equal(r.termination,'cancelled');assert.equal(events.at(-1),'end:cancelled');
 assert.deepEqual(r.nodes.map(n=>n.expansion),[expansion('complete'),expansion('interrupted','cancelled'),expansion('unexpanded'),expansion('unexpanded')]);assert.equal(r.frontierSize,3);
 const [late]=search(intModel(diamond),{cancelAfter:'edge:4:2:3'});
 assert.deepEqual(late.edges[4].target,{kind:'rejected',reason:'cancelled'});assert.equal(late.rejectedSuccessors,1n);
 const [expired,none]=search(intModel(diamond),{limits:{control:{maxDurationMs:0}}});
 assert.equal(expired.termination,'deadline');assert.equal(expired.states,0);assert.deepEqual(expired.nodes,[]);assert.deepEqual(none,['end:deadline']);
});
test('retention limits are visible and negative caps are configuration errors',()=>{
 const [capped]=search(intModel(diamond),{retention:{maxRetainedEdges:3}});
 assert.equal(capped.termination,'graph_retention_limit');assert.equal(capped.edges.length,3);assert.equal(capped.edgesRetained,false);assert.equal(spellCoverage(capped.coverage),'incomplete:graph_retention_limit');
 const [streamed,events]=search(intModel(diamond),{retention:{retainEdges:false}});
 assert.deepEqual(streamed.edges,[]);assert.equal(spellCoverage(streamed.coverage),'exhaustive');assert.equal(streamed.graphEdges,5n);assert.equal(events.filter(e=>e.startsWith('edge:')).length,5);
 const [tiny]=search(intModel(diamond),{retention:{maxGraphBytes:1n}});
 assert.equal(tiny.termination,'graph_retention_limit');assert.equal(tiny.states,0);
 assert.throws(()=>search(intModel(diamond),{retention:{maxRetainedEdges:-1}}),/maxRetainedEdges/);
 assert.throws(()=>search(intModel(diamond),{policy:collect(-1,true)}),/maxRetainedFindings/);
});
test('S2 collection: successive failures, pruning, phases, truncation',()=>{
 const [r]=search(intModel(chain,{failState:[2,3,4]}),{policy:collect(10,true)});
 assert.deepEqual(r.findings.map(f=>f.witness),[[1,2],[1,2,3],[1,2,3,4]]);assert.deepEqual(r.findings.map(f=>f.requiresContinuedEvidence),[false,true,true]);
 assert.deepEqual(r.findings.map(f=>f.index),[0n,1n,2n]);assert.equal(findingsTruncated(r),false);
 assert.deepEqual(r.failedProperties,[{phase:'state',id:'bad_state',count:3n,firstFinding:0n}]);assert.deepEqual(r.nodes.map(n=>n.pathFailed),[false,false,true,true,true]);
 const [pruned]=search(intModel(chain,{failState:[2,3,4]}),{policy:collect(10,false)});
 assert.equal(pruned.findingCount,1n);assert.equal(pruned.nodes.length,2);assert.equal(spellCoverage(pruned.coverage),'bounded:failure_pruned');
 const [both,events]=search(intModel(chain,{failState:[1],failEdge:[[0,1]]}),{policy:collect(10,true)});
 assert.equal(both.findings.length,1);assert.ok(events.includes('finding:0:0:state/bad_state,transition/bad_edge'));
 assert.deepEqual(both.failedProperties.map(p=>p.id),['bad_state','bad_edge']);
 assert.deepEqual(both.edges[0].stateChecks,[check('ok'),check('bad_state','failed','state 1')]);assert.deepEqual(both.edges[0].transitionChecks,[check('bad_edge','failed','0->1')]);
 const [expand]=search(intModel(chain,{failState:[0]}),{policy:collect(10,true)});
 assert.deepEqual(expand.findings.map(f=>f.witness),[[]]);assert.equal(expand.findings[0].edge,null);assert.equal(expand.nodes.length,5);assert.equal(expand.findings[0].violations[0].phase,'initial_state');
 const [capped]=search(intModel(chain,{failState:[2]}),{config:{maxStates:3,maxTransitions:100,maxDepth:100},policy:collect(10,true)});
 assert.equal(capped.findingCount,1n);assert.equal(spellCoverage(capped.coverage),'incomplete:state_limit');
 const [a,ea]=search(intModel(chain,{failState:[2,3,4]}),{policy:collect(1,true)}),[b,eb]=search(intModel(chain,{failState:[2,3,4]}),{policy:collect(1,true)});
 assert.deepEqual(a.findings.map(f=>f.witness),[[1,2]]);assert.equal(a.findingCount,3n);assert.equal(findingsTruncated(a),true);assert.equal(a.failedProperties[0].count,3n);assert.deepEqual(ea,eb);assert.deepEqual(b.findings.map(f=>f.witness),[[1,2]]);
 const [none]=search(intModel(chain,{failState:[2]}),{policy:collect(0,true)});
 assert.deepEqual(none.findings,[]);assert.equal(graphFailure(none),null);assert.equal(none.findingCount,1n);
});
test('checker error after partial evidence keeps a GraphSearchError report',()=>{
 const m=intModel(chain,{failState:[2]}),base=m.checkState;m.checkState=s=>{if(s===3)throw Error('checker exploded');return base(s);};
 assert.throws(()=>exploreGraph(m,undefined,{},{failurePolicy:collect(10,true)}),e=>{
  assert.ok(e instanceof GraphSearchError);assert.match(e.message,/checker exploded/);
  assert.equal(e.report.termination,'model_error');assert.equal(spellCoverage(e.report.coverage),'incomplete:model_error');
  assert.deepEqual(e.report.findings.map(f=>f.witness),[[1,2]]);assert.deepEqual(e.report.nodes[2].expansion,expansion('interrupted','model_error'));
  assert.equal(e.report.edges.length,2);assert.deepEqual(e.report.edges.at(-1).target,{kind:'admitted',node:2});assert.equal(e.report.rejectedSuccessors,0n);
  return true;});
});
test('annotations and detached event payloads',()=>{
 const box=n=>({n});
 const m={initialState:()=>box(0),step:(_s,i)=>accepted(box(i.n),[box(i.n)]),checkState:()=>[],cloneState:s=>box(s.n),cloneInput:i=>box(i.n),cloneOutput:o=>box(o.n),
  inputs:s=>(s.n===0?[1,2]:[]).map(box),equalStates:(a,b)=>a.n===b.n,hashState:s=>s.n};
 const r=exploreGraph(m,undefined,{},{events:e=>{if(e.kind==='edgeEvaluated'){e.edge.input.n=99;e.edge.outputs.forEach(o=>{o.n=99;});}},annotateEdge:(before,input,t)=>{input.n=77;return [`from ${before.n} to ${t.state.n}`];}});
 assert.deepEqual(r.edges.map(e=>e.input.n),[1,2]);assert.deepEqual(r.edges.map(e=>e.outputs.map(o=>o.n)),[[1],[2]]);
 assert.deepEqual(r.edges.map(e=>e.annotations),[['from 0 to 1'],['from 0 to 2']]);assert.deepEqual(r.nodes.map(n=>n.state.n),[0,1,2]);
});
test('sink error aborts with a partial report',()=>{
 assert.throws(()=>exploreGraph(intModel(diamond),undefined,{},{events:e=>{if(e.kind==='successorAdmitted'&&e.node===2)throw Error('sink full');}}),e=>{
  assert.ok(e instanceof GraphSearchError);assert.match(e.message,/sink full/);assert.equal(e.report.nodes.length,3);assert.equal(spellCoverage(e.report.coverage),'incomplete:model_error');return true;});
});

// The sink receives detached expansion statuses (Codex review on PR #19).
test('expansionFinished statuses are detached from the search state',()=>{
 const model=(fail)=>({initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalStates:(a,b)=>a===b,hashState:s=>s,
  inputs:s=>s<2?[s+1]:[],step:(s,i)=>accepted(i),checkState:s=>fail&&s===0?[check('bad','failed','root')]:[check('ok')]});
 const tamper=e=>{if(e.kind==='expansionFinished')e.status.kind='complete';};
 const frontier=exploreGraph(model(false),{maxStates:100000,maxTransitions:1000000n,maxDepth:0},undefined,{events:tamper});
 assert.equal(frontier.nodes[0].expansion.kind,'depthFrontier');assert.equal(frontier.frontierSize,1);
 const pruned=exploreGraph(model(true),undefined,undefined,{failurePolicy:{kind:'collect',maxRetainedFindings:4,expandFailingSuccessors:false},events:tamper});
 assert.equal(pruned.nodes[0].expansion.kind,'failurePruned');assert.equal(pruned.nodes.length,1);
});
