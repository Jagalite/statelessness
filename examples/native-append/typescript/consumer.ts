import {accepted,check,metadata,checkObservedInto,enumerateStates,fuzz,shrink,record,replay,parseTrace,stringifyTrace,
 type Check,type CheckSink,type CodecModel,type GenerateModel,type EnumerateModel} from '@jagalite/statelessness';
declare const process:{argv:string[]};
// @ts-ignore Node runtime built-in; no development dependency is required.
import {readFileSync,writeFileSync} from 'node:fs';
const stateIds=['threshold.bound','state.appended'],allIds=[...stateIds,'edge.checked','edge.appended'];
const ids=(sink:CheckSink)=>sink.snapshot().map(c=>c.id);
const size=(sink:CheckSink):number=>sink.length;
function requireEqual(a:unknown,b:unknown,message:string){if(JSON.stringify(a)!==JSON.stringify(b))throw Error(message);}
function expired(sink:CheckSink){
 const operations:{[key:string]:()=>unknown}={push:()=>sink.push(check('expired')),extend:()=>sink.extend([check('expired')]),snapshot:()=>sink.snapshot(),length:()=>size(sink),reserve:()=>sink.reserve(1)};
 for(const[name,operation]of Object.entries(operations)){let rejected=false;try{operation();}catch(error){if(!(error instanceof Error)||!error.message.toLowerCase().includes('expired'))throw error;rejected=true;}if(!rejected)throw Error(`escaped sink ${name} succeeded`);}
}
const encode=(v:string)=>new TextEncoder().encode(v),decode=(v:Uint8Array)=>new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(v);
function application(failTransition=false,nested=false){
 const stats={stateCalls:0,transitionCalls:0,stepCalls:0,legacyCalls:0,leases:[] as CheckSink[],nestedExpired:false,outerActive:false};
 const model:CodecModel<number,number,string>&GenerateModel<number,number,string>&EnumerateModel<number,number,string>={
  initialState:()=>0,inputs:()=>[0,1],equalStates:(a,b)=>a===b,
  step:(state,input)=>{stats.stepCalls++;if(input!==0&&input!==1)throw Error('expected zero or one');return accepted(state+input,input===1?['changed']:[]);},
  checkState:()=>{stats.legacyCalls++;throw Error('legacy state checker invoked');},
  checkTransition:()=>{stats.legacyCalls++;throw Error('legacy transition checker invoked');},
  checkStateInto:(state,sink)=>{
   stats.stateCalls++;stats.leases.push(sink);requireEqual(ids(sink),[],'state sink not empty');if(size(sink)!==0)throw Error('state sink length');sink.reserve(2);
   sink.push(state>=3?check('threshold.bound','failed','at least three'):check('threshold.bound'));
   requireEqual(ids(sink),['threshold.bound'],'state inspection');if(size(sink)!==1)throw Error('state inspection length');
   if(nested){const child=application(),storage:Check[]=[];checkObservedInto(child.model,0,1,accepted(1,['changed']),1,storage);child.stats.leases.forEach(expired);
    stats.nestedExpired=true;stats.outerActive=size(sink)===1&&ids(sink)[0]==='threshold.bound';}
   sink.extend([check('state.appended')]);requireEqual(ids(sink),stateIds,'state append order');
  },
  checkTransitionInto:(_b,_i,_t,sink)=>{
   stats.transitionCalls++;stats.leases.push(sink);requireEqual(ids(sink),stateIds,'transition lost state prefix');if(size(sink)!==2)throw Error('transition prefix length');sink.reserve(2);
   sink.push(check('edge.checked'));requireEqual(ids(sink),allIds.slice(0,3),'transition inspection');if(size(sink)!==3)throw Error('transition length');
   if(failTransition)throw Error('application checker failure');sink.extend([check('edge.appended')]);requireEqual(ids(sink),allIds,'transition append order');
  },
  cloneState:v=>v,cloneInput:v=>v,cloneOutput:v=>v,generate:(_s,rng)=>1-Number(rng.index(2n)),isEnabled:(_s,i)=>i===0||i===1,equalInputs:(a,b)=>a===b,
  metadata:()=>metadata('native-append-threshold','application-v1'),encodeState:v=>encode(String(v)),decodeState:v=>Number(decode(v)),
  encodeInput:v=>encode(String(v)),decodeInput:v=>{const s=decode(v);if(s!=='0'&&s!=='1')throw Error('expected zero or one');return Number(s);},encodeOutput:encode,
 };
 return{model,stats};
}
function controls(){
 const app=application(),storage=[check('stale')];checkObservedInto(app.model,0,1,accepted(1,['changed']),1,storage);const actualIds=storage.map(c=>c.id);app.stats.leases.forEach(expired);
 const failed=application(true),failedStorage=[check('stale')];let threw=false;
 try{checkObservedInto(failed.model,0,1,accepted(1,['changed']),1,failedStorage);}catch(e){if(!(e instanceof Error)||!e.message.includes('application checker failure'))throw e;threw=true;}
 if(!threw)throw Error('checker error missing');failed.stats.leases.forEach(expired);
 const nested=application(false,true),nestedStorage:Check[]=[];checkObservedInto(nested.model,0,1,accepted(1,['changed']),1,nestedStorage);
 return{ids:actualIds,step_calls:app.stats.stepCalls,state_calls:app.stats.stateCalls,transition_calls:app.stats.transitionCalls,
  error_cleared:failedStorage.length===0,error_step_calls:failed.stats.stepCalls,error_state_calls:failed.stats.stateCalls,error_transition_calls:failed.stats.transitionCalls,
  escaped_sink_expired:true,escaped_operations:['push','extend','snapshot','length','reserve'],nested_child_expired:nested.stats.nestedExpired,
  outer_active_after_nested:nested.stats.outerActive,legacy_calls:app.stats.legacyCalls+failed.stats.legacyCalls+nested.stats.legacyCalls};
}
const[mode,path]=process.argv.slice(2);let receipt:Record<string,unknown>;
if(mode==='record'){
 const control=controls(),app=application(),bfs=enumerateStates(app.model),found=fuzz(app.model,{seed:0n,cases:4,maxSteps:32,maxTransitions:128n,mutationPercent:0});
 if(!bfs.failure||!found.failure)throw Error('search witness missing');const reduced=shrink(app.model,found.failure);
 requireEqual(reduced.minimized.inputs,[1,1,1],'shrink witness');if(!reduced.validated_original)throw Error('original validation missing');
 const trace=record(app.model,reduced.minimized.inputs),result=replay(app.model,parseTrace(stringifyTrace(trace)));
 if(result.outcome!=='exact'||!result.failure_reproduced)throw Error('local witness replay mismatch');writeFileSync(path,stringifyTrace(trace));
 receipt={controls:control,bfs:{states:bfs.states,transitions:bfs.transitions,witness:bfs.failure.inputs},
  fuzz:{original_steps:found.failure.inputs.length,minimized_steps:reduced.minimized.inputs.length,shrink_attempts:reduced.attempts,validated_original:reduced.validated_original},
  legacy_calls:app.stats.legacyCalls,append_state_calls:app.stats.stateCalls,append_transition_calls:app.stats.transitionCalls};
}else if(mode==='replay'){
 const app=application(),trace=parseTrace(readFileSync(path,'utf8')),result=replay(app.model,trace);
 receipt={outcome:result.outcome,failure_reproduced:result.failure_reproduced,steps:trace.steps.length,legacy_calls:app.stats.legacyCalls,
  append_state_calls:app.stats.stateCalls,append_transition_calls:app.stats.transitionCalls};
}else throw Error('expected record or replay');
console.log(JSON.stringify(receipt));
