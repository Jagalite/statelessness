// Fixture adapter only: every engine operation dispatches to the supplied Wasm.
import {NativeError} from './v2.mjs';
import {ModelError} from '../../typescript/dist/core.js';
export function conformanceBackend(engine) {
  const run=(model,work)=>{
    let session;
    try {session=engine.createSession(model);return work(session);}
    catch(error){if(error instanceof NativeError)throw new ModelError(error.message);throw error;}
    finally {session?.close();}
  };
  return {
    identity:{implementation:'typescript-rust-abi2',engine_family:'rust-shared',abi_version:2,artifact_sha256:engine.sha256},
    rng:seed=>engine.rng(seed),
    enumerateStates:(model,config)=>run(model,s=>s.enumerateStates(config)),
    record:(model,inputs,maxSteps)=>run(model,s=>s.record(inputs,maxSteps)),
    checkObserved:(model,before,input,transition,sequence,policy)=>run(model,s=>s.checkObserved(before,input,transition,sequence,policy)),
    replay:(model,trace,allowBuildMismatch)=>run(model,s=>s.replay(trace,{allowBuildMismatch})),
  };
}
