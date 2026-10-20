import {accepted,check,metadata,enumerateStates,record,replay,parseTrace,stringifyTrace,
 type CodecModel,type EnumerateModel,type SearchLimits,type SearchObserver} from '@jagalite/statelessness';
declare const process:{argv:string[]};
// @ts-ignore Node runtime built-in; no development dependency is required.
import {readFileSync,writeFileSync} from 'node:fs';
const text=(v:string)=>new TextEncoder().encode(v),decode=(v:Uint8Array)=>new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(v);
function model(counter:{calls:number}):CodecModel<number,number,string>&EnumerateModel<number,number,string>{return{
 initialState:()=>{counter.calls++;return 0;},inputs:()=>{counter.calls++;return [0,1];},equalStates:(a,b)=>{counter.calls++;return a===b;},
 step:(state,input)=>{counter.calls++;if(input!==0&&input!==1)throw Error('expected zero or one');return accepted(state+input,input===1?['changed']:[]);},
 checkState:state=>{counter.calls++;return[state>=3?check('threshold.bound','failed','at least three'):check('threshold.bound')];},
 estimatedStateBytes:()=>{counter.calls++;return 5n;},cloneState:v=>{counter.calls++;return v;},cloneInput:v=>{counter.calls++;return v;},cloneOutput:v=>{counter.calls++;return v;},
 metadata:()=>metadata('native-bfs-threshold','application-v1'),encodeState:v=>text(String(v)),decodeState:v=>Number(decode(v)),
 encodeInput:v=>text(String(v)),decodeInput:v=>{const s=decode(v);if(s!=='0'&&s!=='1')throw Error('expected zero or one');return Number(s);},encodeOutput:text,
};}
function search(cap?:bigint,cancelled=false){
 const counter={calls:0};let initials=0,errors=0;const histories:number[][]=[];
 const observer:SearchObserver<number,number,string>={needsHistory:()=>true,initial:()=>{initials++;},checkError:()=>{errors++;},
  transition:(_b,_i,_t,_s,_e,history)=>{histories.push([...history]);}};
 const limits:SearchLimits={control:{cancellation:()=>cancelled},...(cap===undefined?{}:{maxEstimatedStateBytes:cap})};
 const result=enumerateStates(model(counter),{maxStates:100,maxTransitions:100,maxDepth:10},limits,observer);
 const violation=result.failure?.violations[0];
 return{failure:result.failure,receipt:{termination:result.termination,states:result.states,transitions:result.transitions,
  estimated_retained_state_bytes:result.estimated_retained_state_bytes===null?null:Number(result.estimated_retained_state_bytes),
  witness:result.failure?.inputs??null,failure_phase:violation?.phase??null,failure_property:violation?.check.id??null,
  observed_histories:histories,initial_observations:initials,check_errors:errors,model_callbacks:counter.calls,observer_callbacks:initials+histories.length+errors}};
}
const[mode,path]=process.argv.slice(2);let receipt:Record<string,unknown>;
if(mode==='record'){
 const base=search(),cap=search(14n),precancel=search(undefined,true);
 if(!base.failure)throw Error('expected BFS witness');
 writeFileSync(path,stringifyTrace(record(model({calls:0}),base.failure.inputs)));
 receipt={base:base.receipt,cap:cap.receipt,precancel:precancel.receipt};
}else if(mode==='replay'){
 const trace=parseTrace(readFileSync(path,'utf8')),result=replay(model({calls:0}),trace);
 receipt={outcome:result.outcome,failure_reproduced:result.failure_reproduced,steps:trace.steps.length};
}else throw Error('expected record or replay');
console.log(JSON.stringify(receipt));
