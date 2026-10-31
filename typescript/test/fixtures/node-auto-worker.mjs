import { accepted, metadata, check } from '../../dist/index.js';
export function createModel(_id,data,context) {
 const counts = new Int32Array(data.counts);
 // Violates the NodeAutoCampaignModule contract: an ordinary generate-only model.
 if (data.missingInputs) return { metadata:()=>metadata('auto-worker','1'), initialState:()=>0, step:s=>accepted(s+1), checkState:()=>[], cloneState:s=>s, cloneInput:i=>i, cloneOutput:o=>o, generate:()=>1, isEnabled:()=>true };
 return { metadata:()=>metadata('auto-worker','1'), initialState:()=>0, step:s=>accepted(s+1), checkState:s=>data.witness && s>0 ? [check('bad','failed')] : [], cloneState:s=>s, cloneInput:i=>i, cloneOutput:o=>o, equalInputs:(a,b)=>a===b, dispose:()=>{Atomics.add(counts,2,1);},
  *inputs(){ try {for(let i=0;i<10;i++){Atomics.add(counts,0,1); if(data.cancel && i===2)context.cancellation.cancel(); yield i;}} finally {Atomics.add(counts,1,1);} } };
}
export function createFeedback(_id,_model,data){const counts=new Int32Array(data.counts);return {metadata:()=>({name:'feedback',version:1,build:'x'}),cloneFeature:x=>x,equalFeatures:(a,b)=>a===b,transitionFeatures:()=>{}, dispose:()=>{Atomics.add(counts,3,1);}};}
export function encodeInput(x){return x+100;}
export function disposeModel(model){model.dispose();}

export function disposeFeedback(feedback){feedback.dispose();}
