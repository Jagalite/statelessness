// Violates the NodeAutoCampaignModule contract at module level: a non-function hook export.
import { accepted, metadata } from '../../dist/index.js';
export function createModel() {
 return { metadata:()=>metadata('auto-bad-module','1'), initialState:()=>0, step:s=>accepted(s+1), checkState:()=>[], cloneState:s=>s, cloneInput:i=>i, cloneOutput:o=>o, equalInputs:(a,b)=>a===b, inputs:()=>[1] };
}
export function encodeInput(x){return x;}
export const disposeModel = 5;
