import { runAutoCampaign, DEFAULT_CAMPAIGN, accepted, metadata } from '../dist/index.js';
const factory = () => ({initialState:()=>0,step:(s:number,_i:number)=>accepted(s+1),checkState:()=>[],cloneState:(s:number)=>s,cloneInput:(i:number)=>i,cloneOutput:(o:never)=>o,equalInputs:(a:number,b:number)=>a===b,equalStates:(a:number,b:number)=>a===b,inputs:()=>[1],metadata:()=>metadata('typecheck')});
void runAutoCampaign(DEFAULT_CAMPAIGN, {}, {maxCandidates:3}, factory, () => {});
import type { CampaignJobHooks } from '../dist/campaign.js';
// Internal runners are not part of the hook type callers can construct.
// @ts-expect-error runJob is a package-internal runner, not a public campaign hook.
const hooks: CampaignJobHooks<number, number, never, never> = { runJob: () => { throw new Error('unreachable'); } };
void hooks;
