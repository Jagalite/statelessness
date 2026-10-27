import { accepted, check, metadata, fuzz, shrink, record, replay, stringifyTrace, parseTrace,
 type CodecModel, type GenerateModel } from '@jagalite/statelessness';
// Keep this application dependency-free; Node built-ins are declared locally.
declare const process: { argv: string[] };
// @ts-ignore Node's runtime built-in; no @types/node dependency is required.
import { readFileSync, writeFileSync } from 'node:fs';
const encode = (v: string): Uint8Array => new TextEncoder().encode(v);
const decode = (v: Uint8Array): string => new TextDecoder('utf-8', {fatal:true, ignoreBOM:true}).decode(v);
const decodeInput = (v: Uint8Array): number => { const text=decode(v); if(text!=='0'&&text!=='1') throw Error('expected zero or one'); return Number(text); };
const model: CodecModel<number,number,string> & GenerateModel<number,number,string> = {
 initialState:()=>0,
 step:(state,input)=> { if(input!==0&&input!==1) throw Error('expected zero or one'); return accepted(state+input,input===1?['changed']:[]); },
 checkState:state=>[state>=3 ? check('threshold.bound','failed','at least three') : check('threshold.bound')],
 cloneState:v=>v, cloneInput:v=>v, cloneOutput:v=>v,
 generate:(_s,rng)=>1-Number(rng.index(2n)), isEnabled:(_s,i)=>i===0||i===1, equalInputs:(a,b)=>a===b,
 metadata:()=>({...metadata('native-search-threshold','application-v2'),model_version:2}),
 encodeState:v=>encode(String(v)),decodeState:v=>Number(decode(v)),
 encodeInput:v=>encode(String(v)),decodeInput,encodeOutput:encode,
};
const [mode,path]=process.argv.slice(2);
let searchReceipt: Record<string,number|string|boolean> = {};
if(mode==='record') {
 const found=fuzz(model,{seed:0n,cases:4,maxSteps:32,maxTransitions:128n,mutationPercent:0});
 if(found.termination!=='failure_found'||!found.failure) throw Error('failure missing');
 writeFileSync(path+'.original.json',stringifyTrace(record(model,found.failure.inputs)));
 const reduced=shrink(model,found.failure);
 const target=(f:typeof found.failure)=>f?.violations.map(v=>`${v.phase}:${v.check.id}`).join(',');
 if(!reduced.validated_original||reduced.termination!=='search_complete'||reduced.minimized.inputs.length!==3
  ||!reduced.minimized.inputs.every(i=>i===1)||found.failure.inputs.length<=3||reduced.attempts<=0
  ||target(found.failure)!=='state:threshold.bound'||target(reduced.minimized)!==target(found.failure)) throw Error('shrink incomplete');
 searchReceipt={original_steps:found.failure.inputs.length,minimized_steps:reduced.minimized.inputs.length,shrink_attempts:reduced.attempts,
  validated_original:reduced.validated_original,phase:'state',property:'threshold.bound'};
 writeFileSync(path,stringifyTrace(record(model,reduced.minimized.inputs)));
} else if(mode!=='replay') throw Error('expected record or replay');
const trace=parseTrace(readFileSync(path,'utf8'));
const result=replay(model,trace);
if(result.outcome!=='exact'||!result.failure_reproduced) throw Error('failure did not replay');
console.log(JSON.stringify({mode,outcome:result.outcome,failure_reproduced:result.failure_reproduced,steps:trace.steps.length,...searchReceipt}));
