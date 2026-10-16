// Explicit workload driver; never invoked by compile-only qualification.
// node bindings/browser/benchmark-v2.mjs /absolute/stateless_v2.wasm [iterations]
// Raw timings/callbacks are not allocation, RSS or performance qualification.
import {readFile} from 'node:fs/promises';
import {createEngine,check,disposition} from './v2.mjs';
const MASK=(1n<<64n)-1n;
export const PARAMETERS={states:32,inputs:[1,2],trace_steps:64,large_payload_bytes:65536,expensive_iterations:4096,failure_state:24,seed:0};
export function counter(kind,fail=false) {
  const payload=kind==='large'?65536:0,iterations=kind==='expensive'?4096:0;
  const model={callbacks:0,sink:0n,payload,
    hit(){this.callbacks++;},
    metadata:()=>({name:'abi-v2-matched-counter',build:'benchmark-v1',model_version:1,properties_version:1,codec_version:1}),
    initialState(){this.hit();return {value:0,payload:new Uint8Array(payload)};},
    cloneState(s){this.hit();return {value:s.value,payload:s.payload.slice()};},
    cloneInput(i){this.hit();return i;},cloneOutput(o){this.hit();return o;},
    step(s,i){this.hit();return {state:{value:(s.value+i)%32,payload:s.payload},outputs:[s.value],disposition:disposition()};},
    checkState(s){this.hit();let digest=BigInt(s.value);for(let i=0;i<iterations;i++)digest=((digest^BigInt(i))*0x9e3779b97f4a7c15n+1n)&MASK;this.sink^=digest;
      if(s.value<0||s.value>=32||s.payload.length!==payload)return [check('valid','failed','invalid state')];
      return [fail&&s.value>=24?check('bound','failed','state reached failure region'):check('valid')];},
    checkTransition(b,i,t){this.hit();return [t.state.value===(b.value+i)%32&&t.outputs.length===1&&t.outputs[0]===b.value?check('transition'):check('transition','failed','inconsistent transition')];},
    inputs(){this.hit();return [1,2];},equalStates(a,b){this.hit();return a.value===b.value;},hashState(s){this.hit();return s.value;},
    estimatedStateBytes(s){this.hit();return 4+s.payload.length;},
    encodeState(s){this.hit();const b=new Uint8Array(4+s.payload.length);new DataView(b.buffer).setUint32(0,s.value,true);b.set(s.payload,4);return b;},
    decodeState(b){this.hit();if(b.length!==4+payload)throw new Error('state length');return {value:new DataView(b.buffer,b.byteOffset,b.byteLength).getUint32(0,true),payload:b.slice(4)};},
    encodeInput(i){this.hit();return Uint8Array.of(i);},decodeInput(b){this.hit();if(b.length!==1)throw new Error('input length');return b[0];},
    encodeOutput(o){this.hit();const b=new Uint8Array(4);new DataView(b.buffer).setUint32(0,o,true);return b;},
    generate(s,rng){this.hit();return Number(rng.index(2))+1;},isEnabled(s,i){this.hit();return i===1||i===2;},
    equalInputs(a,b){this.hit();return a===b;},simplerInputs(i){this.hit();return i===2?[1]:[];},
  };return model;
}
function expect(condition){if(!condition)throw new Error('benchmark semantic check failed');}
export function workload(session,model,operation) {
  const initial={value:0,payload:new Uint8Array(model.payload)},inputs=Array.from({length:64},(_,i)=>i%2+1);
  const artifact=operation==='replay'?session.recordBinary(inputs,64):null;
  const options={seed:0n,cases:1,maxSteps:64,maxTransitions:256,mutationPercent:0};
  const failure=operation==='shrink'?session.fuzz(options):null;
  if(failure)expect(failure.status===1);
  return {close:()=>failure?.close(),run:()=>{
    switch(operation) {
      case 'observation':{const checks=session.checkObserved(initial,2,{state:{value:2,payload:initial.payload},outputs:[0],disposition:disposition()},1);expect(checks.length===2&&checks.every(c=>c.status==='passed'));break;}
      case 'bfs':{const r=session.enumerateStates({maxStates:64,maxTransitions:256,maxDepth:64});expect(r.states===32&&r.transitions===64&&r.termination==='graph_exhausted');break;}
      case 'record':expect(session.recordBinary(inputs,64).length>0);break;
      case 'replay':{const r=session.replay(artifact);expect(r.outcome==='exact'&&r.steps_verified===64);break;}
      case 'fuzz':{const r=session.fuzz(options);try{expect(r.status===1);}finally{r.close();}break;}
      case 'shrink':{const r=session.shrink(failure,{maxAttempts:256,maxTransitions:4096});try{expect(r.scalar(19)===1n);}finally{r.close();}break;}
      case 'ring':{const ring=session.ring(initial,{maxSteps:16,maxRetainedBytes:16*1024*1024});try{let before=initial;for(const input of inputs){const after={value:(before.value+input)%32,payload:before.payload};ring.observe(before,input,{state:after,outputs:[before.value],disposition:disposition()});before=after;}expect(ring.snapshot().length>0);}finally{ring.close();}break;}
      default:throw new Error('unknown workload');
    }
  }};
}
async function main() {
  const path=process.argv[2],iterations=Number(process.argv[3]??5),samples=7;
  if(!path||!Number.isSafeInteger(iterations)||iterations<1)throw new Error('explicit Wasm path and positive iterations required');
  const engine=await createEngine(new Uint8Array(await readFile(path))),cells=[];
  for(const kind of ['cheap','large','expensive'])for(const operation of ['observation','bfs','record','replay','fuzz','shrink','ring']) {
    const model=counter(kind,['fuzz','shrink'].includes(operation)),session=engine.createSession(model);
    try{const work=workload(session,model,operation);try{const baseline=session.retainedRoots,times=[],callbacks=[];
      for(let sample=0;sample<samples;sample++){model.callbacks=0;const start=performance.now();for(let i=0;i<iterations;i++)work.run();times.push((performance.now()-start)*1e6/iterations);callbacks.push(model.callbacks/iterations);expect(session.retainedRoots===baseline);}
      cells.push({model:kind,workload:operation,samples_ns:times,callbacks_per_operation:callbacks,retained_roots_after_operation:baseline});
    }finally{work.close();}}finally{session.close();}
  }
  process.stdout.write(JSON.stringify({schema:'stateless.binding-raw-timing.v1',engine_family:'rust-shared',artifact_sha256:engine.sha256,runtime:process.version,parameters:PARAMETERS,qualification:'incomplete: external allocation/RSS evidence and baseline budgets required',cells},null,2)+'\n');
}
// This is a command-line workload, separate from the production adapter exports.
await main();
