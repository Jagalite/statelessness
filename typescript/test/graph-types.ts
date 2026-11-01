import { exploreGraph, analyzeProgress, ProgressGraph, compareReduction, reductionEquivalent, recordContinued, replayContinued, shrinkContinued, accepted, metadata, NO_INPUT } from '../dist/index.js';
import type { GraphSearchReport, GraphEvent, EquivalenceReduction, ProgressKind, ContinuedShrinkReport } from '../dist/index.js';
// Type-level use of the experimental graph-search, progress, reduction and continued-evidence API.
const model = {initialState:()=>0,step:(s:number,i:number)=>accepted(s+i,[i]),checkState:()=>[],cloneState:(s:number)=>s,cloneInput:(i:number)=>i,cloneOutput:(o:number)=>o,
  inputs:(s:number)=>s<2?[1]:[],equalStates:(a:number,b:number)=>a===b,hashState:(s:number)=>s,metadata:()=>metadata('typecheck'),
  encodeState:()=>new Uint8Array(),decodeState:()=>0,encodeInput:()=>new Uint8Array(),decodeInput:()=>0,encodeOutput:()=>new Uint8Array(),
  generate:():number|typeof NO_INPUT=>NO_INPUT,equalInputs:(a:number,b:number)=>a===b};
const reduction: EquivalenceReduction<number, number, number> = {eligibleClass:()=>null,projection:()=>new Uint8Array()};
const events: GraphEvent<number, number>[] = [];
const report: GraphSearchReport<number, number, number> = exploreGraph(model,{maxStates:10,maxTransitions:10n,maxDepth:3},{},{failurePolicy:{kind:'collect',maxRetainedFindings:4,expandFailingSuccessors:true},reduction,events:e=>{events.push(e);}});
const transitions: bigint = report.transitions;
const kind = (_: number): ProgressKind => 'terminal';
const rank: bigint = analyzeProgress(ProgressGraph.fromReport(report,()=>true),kind).maximumRank;
const equivalent: boolean = reductionEquivalent(compareReduction(model,report,report,{fair:()=>true,kind:()=>'transaction'}));
const complete: boolean = replayContinued(model,recordContinued(model,[1,1])).completeReproduction;
const shrunk: ContinuedShrinkReport<number, bigint> = shrinkContinued(model,[1,1],{phase:'state',check:{id:'x',status:'failed',details:''}},{maxAttempts:5n});
// @ts-expect-error findings are indexed by exact bigint, never a rounded Number.
const index: number = report.findings[0].index;
void [transitions, rank, equivalent, complete, shrunk, index];
