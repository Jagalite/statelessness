// Compile-only consumer of the actual .mjs entry point and typed declarations.
import {createEngine, check, disposition, type Model, type Trace} from './v2.mjs';
const model: Model<{count:number}, number, string> = {
  initialState:()=>({count:0}),
  cloneState:s=>({...s}), cloneInput:i=>i, cloneOutput:o=>o,
  step:(s,i)=>({state:{count:s.count+i},outputs:['changed'],disposition:disposition()}),
  checkState:s=>[check('bound',s.count<3?'passed':'failed')],
  inputs:()=>[1], equalStates:(a,b)=>a.count===b.count,
};
export async function consumer(wasm:Uint8Array) {
  const engine=await createEngine(wasm), session=engine.createSession(model);
  try {
    const report=session.enumerateStates({maxStates:10});
    const inputs:readonly number[]|undefined=report.failure?.inputs;
    const draw:bigint=engine.rng(0n).nextU64();
    const observed=session.checkObserved({count:0},1,model.step({count:0},1),1);
    return {inputs,draw,observed};
  } finally {session.close();}
}
export type PortableArtifact=Trace;
