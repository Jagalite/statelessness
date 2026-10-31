import { accepted, metadata } from '../dist/index.js';
import type { NodeAutoCampaignModule } from '../dist/node_campaign.js';
const module: NodeAutoCampaignModule<number, number, never, number, number, undefined> = {
 createModel: () => ({initialState:()=>0,step:(s:number,_i:number)=>accepted(s+1),checkState:()=>[],cloneState:(s:number)=>s,cloneInput:(i:number)=>i,cloneOutput:(o:never)=>o,equalInputs:(a:number,b:number)=>a===b,equalStates:(a:number,b:number)=>a===b,inputs:()=>[1],metadata:()=>metadata('typecheck')}),
 encodeInput: x => x,
};
void module;
