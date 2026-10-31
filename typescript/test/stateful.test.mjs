import {test} from 'node:test';import assert from 'node:assert/strict';
import {LifecycleMonitor,IndexedLifecycleMonitor} from '../dist/lifecycle.js';
import {Network,DEFAULT_QUEUE,networkMachine} from '../dist/network.js';
import {Pair,pairSchemas} from '../dist/composition.js';
import {withCheckSink} from '../dist/checks.js';
const spec={metadata:()=>({name:'fixture',model_version:1,properties_version:1,codec_version:1,build:'fixture'}),inputEvents:i=>i,outputEvents:o=>o};
const limits={obligations:2,resources:2,violations:8},d={kind:'accepted',reason:''};
test('network machine lookup uses canonical machine ID ordering',()=>{
 const value={count:2},state={machines:[{machine:0,value:{count:0}},{machine:4,value},{machine:0xffffffff,value:{count:9}}],pending:[]};
 assert.equal(networkMachine(state,4),value);
 assert.equal(networkMachine(state,0).count,0);
 assert.equal(networkMachine(state,0xffffffff).count,9);
 assert.equal(networkMachine(state,3),undefined);
 assert.equal(networkMachine({machines:[],pending:[]},0),undefined);
 assert.throws(()=>networkMachine(state,-1),/machine id/);
});
test('lifecycle retains input classifier events before output classification',()=>{
 const event={kind:'begin',key:{id:1},time:10n};
 const callbacks={...spec,inputEvents:()=>[event],outputEvents:()=>{event.key.id=2;event.time=0n;return [{kind:'settle',key:{id:1}}];}};
 const monitor=new LifecycleMonitor(callbacks,limits,(a,b)=>a.id-b.id,k=>({...k}));
 const history=monitor.advance(monitor.initialState(),null,[],d);
 assert.equal(history.violations.length,0);assert.equal(history.obligations[0][0].id,1);assert.equal(history.obligations[0][1].deadline,10n);assert.equal(history.obligations[0][1].settled,true);
});
test('lifecycle retains output slots before input classifier mutation',()=>{
 const outputs=[{kind:'settle',key:1}];
 const callbacks={...spec,inputEvents:()=>{outputs[0]={kind:'cancel',key:1};return [{kind:'begin',key:1,time:10n}];},outputEvents:o=>o};
 const monitor=new LifecycleMonitor(callbacks,limits,(a,b)=>a-b,k=>k);
 const h=monitor.advance(monitor.initialState(),null,outputs,d);
 assert.equal(h.obligations[0][1].settled,true);assert.equal(h.obligations[0][1].cancelled,false);
});
test('lifecycle defers output slot snapshot errors until after input classification',()=>{
 const outputError=new Error('output copy'),inputError=new Error('input classifier');
 const outputs=new Proxy([1],{get(target,key,receiver){if(key==='0')throw outputError;return Reflect.get(target,key,receiver);}});
 let monitor=new LifecycleMonitor({...spec,inputEvents:()=>{throw inputError;}},limits,(a,b)=>a-b,k=>k);
 assert.throws(()=>monitor.advance(monitor.initialState(),null,outputs,d),e=>e===inputError);
 monitor=new LifecycleMonitor({...spec,inputEvents:()=>[]},limits,(a,b)=>a-b,k=>k);
 assert.throws(()=>monitor.advance(monitor.initialState(),null,outputs,d),e=>e===outputError);
});
test('lifecycle snapshots output slots into a plain array without invoking species',()=>{
 class Outputs extends Array {static get [Symbol.species](){throw new Error('species invoked');}}
 const outputs=new Outputs({kind:'settle',key:1});let observed;
 const callbacks={...spec,inputEvents:()=>[{kind:'begin',key:1,time:10n}],outputEvents:o=>{observed=o;return o;}};
 const monitor=new LifecycleMonitor(callbacks,limits,(a,b)=>a-b,k=>k);
 const h=monitor.advance(monitor.initialState(),null,outputs,d);
 assert.equal(Object.getPrototypeOf(observed),Array.prototype);assert.equal(h.obligations[0][1].settled,true);
});
test('lifecycle retains configured limits across classifier callbacks',()=>{
 for(const [field,events] of [['obligations',[{kind:'begin',key:1,time:10n},{kind:'begin',key:2,time:10n}]],['resources',[{kind:'acquire',key:1},{kind:'acquire',key:2}]],['violations',[{kind:'cancel',key:1},{kind:'cancel',key:2}]]]){
  const configured={obligations:1,resources:1,violations:1};let monitor;
  const callbacks={...spec,inputEvents:()=>{configured[field]=100;return events;},outputEvents:()=>[]};
  monitor=new LifecycleMonitor(callbacks,configured,(a,b)=>a-b,k=>k);
  assert.throws(()=>monitor.advance(monitor.initialState(),null,[],d),/limit/);assert.equal(monitor.limits[field],1);
  assert.throws(()=>{monitor.limits[field]=100;},TypeError);assert.throws(()=>{monitor.limits=configured;},TypeError);
 }
});
test('lifecycle enforcement does not trust a shadowed public limits getter',()=>{
 let monitor;const callbacks={...spec,inputEvents:()=>{Object.defineProperty(monitor,'limits',{value:{obligations:100,resources:100,violations:100}});return [{kind:'begin',key:1,time:10n},{kind:'begin',key:2,time:10n}];},outputEvents:()=>[]};
 monitor=new LifecycleMonitor(callbacks,{obligations:1,resources:1,violations:1},(a,b)=>a-b,k=>k);
 assert.throws(()=>monitor.advance(monitor.initialState(),null,[],d),/obligation limit/);
});
test('lifecycle rejects nonnumeric or asynchronous key comparators',()=>{
 const events=[{kind:'begin',key:1,time:10n},{kind:'begin',key:1,time:10n}],callbacks={...spec,inputEvents:()=>events,outputEvents:()=>[]};
 for(const result of [true,NaN,Infinity,Promise.resolve(0),Promise.reject(Error('comparison failed'))]){
  const monitor=new LifecycleMonitor(callbacks,limits,()=>result,k=>k);
  assert.throws(()=>monitor.advance(monitor.initialState(),null,[],d),/finite number synchronously/);
 }
 const indexedEvents=[{kind:'begin',key:{epoch:1n,generation:1n,key:1},time:10n},{kind:'begin',key:{epoch:1n,generation:1n,key:1},time:10n}];
 const indexed=new IndexedLifecycleMonitor({...callbacks,inputEvents:()=>indexedEvents},limits,()=>Promise.resolve(0),k=>k);
 assert.throws(()=>indexed.advance(indexed.initialState(),null,[],d),/finite number synchronously/);
});
test('lifecycle uses its validated comparator despite a shadowed public property',()=>{
 let monitor;const callbacks={...spec,inputEvents:()=>{Object.defineProperty(monitor,'compare',{value:()=>NaN});return [{kind:'begin',key:1,time:10n},{kind:'begin',key:1,time:10n}];},outputEvents:()=>[]};
 monitor=new LifecycleMonitor(callbacks,limits,(a,b)=>a-b,k=>k);
 const history=monitor.advance(monitor.initialState(),null,[],d);
 assert.equal(history.obligations.length,1);assert.equal(history.violations[0].code,'key_reused');
});
test('deadline crossing cannot be erased by cancellation; inclusive settlement',()=>{const m=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);let h=m.advance(m.initialState(),[{kind:'begin',key:7,time:10n},{kind:'tick',time:11n},{kind:'cancel',key:7}],[],d);assert.equal(h.violations[0].code,'missing_before_advance');h=m.advance(m.initialState(),[{kind:'begin',key:7,time:10n},{kind:'tick',time:10n}],[{kind:'settle',key:7}],d);assert.equal(m.checkState(h)[0].status,'passed');});
test('1000 explicit epochs bound details and reject delayed references',()=>{const m=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);let h=m.initialState();for(let epoch=0n;epoch<1000n;epoch++){const key={epoch,generation:0n,key:7};h=m.advance(h,[{kind:'begin',key,time:10n},{kind:'acquire',key}],[{kind:'settle',key},{kind:'release',key},{kind:'fence',time:epoch+1n}],d);assert.equal(h.obligations.length,0);}const copy=m.cloneHistory(h);assert(m.equalHistory(h,copy));h=m.advance(h,[{kind:'settle',key:{epoch:0n,generation:0n,key:7}}],[],d);assert.equal(h.violations[0].code,'retired_epoch_reference');assert.equal(copy.violations.length,0);});
const child={initialState:()=>0,step:(s,i)=>({state:s+i,outputs:[i],disposition:{kind:'ignored',reason:'observed'}}),checkState:()=>[{id:'safe',status:'passed',details:''}],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:()=>[1],equalStates:(a,b)=>a===b};
test('network input enumeration pins child callbacks before snapshot hooks',()=>{
 const later={...child,inputs(){return [7]},cloneInput(value){return value}};
 let mutate=false;
 const first={...child,cloneState(value){if(mutate){later.inputs=()=>[99];later.cloneInput=()=>88;}return value;}};
 const network=new Network([[0,first],[1,later]],{routes:()=>[],checkState:()=>[]});
 const state=network.initialState();mutate=true;
 const inputs=[...network.inputs(state)];
 assert.deepEqual(inputs.filter(i=>i.kind==='local').map(i=>[i.address.machine,i.address.value]),[[0,1],[1,7]]);
});
test('network input enumeration snapshots mutable policy before clone hooks',()=>{
 let mutate=false;const policy={...DEFAULT_QUEUE,allowDrop:false,allowDuplicate:false};
 const model={...child,cloneState(value){if(mutate){policy.allowDrop=true;policy.allowDuplicate=true;}return value;}};
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state=network.initialState();state.pending.push({source:0,route:{destination:0,channel:0,input:1}});
 network.policy=policy;mutate=true;
 assert.deepEqual([...network.inputs(state)].filter(i=>i.kind!=='local').map(i=>i.kind),['deliver']);
 assert.equal(policy.allowDrop,true);
});
test('network topology is not exposed through a JavaScript property',()=>{
 const network=new Network([[2,child],[0,child]],{routes:()=>[],checkState:()=>[]});
 assert.equal(network.machines,undefined);
 network.machines=new Map([[9,child]]);
 assert.deepEqual(network.machineIDs(),[0,2]);
 assert.deepEqual(network.initialState().machines.map(entry=>entry.machine),[0,2]);
 assert.throws(()=>network.step(network.initialState(),{kind:'local',address:{machine:9,value:1}}),/unknown network machine/);
});
test('network retains child disposition before route callbacks',()=>{
 let emitted;
 const model={...child,step:(s,i)=>emitted={state:s+i,outputs:[i],disposition:{kind:'ignored',reason:'original'}}};
 const network=new Network([[0,model]],{routes:()=>{emitted.disposition.kind='rejected';emitted.disposition.reason='changed';return [];},checkState:()=>[]});
 const result=network.step(network.initialState(),{kind:'local',address:{machine:0,value:1}});
 assert.deepEqual(result.disposition,{kind:'ignored',reason:'original'});
});
test('network step retains admitted queue bounds across routing callbacks',()=>{
 for(const policy of [{...DEFAULT_QUEUE,maxRoutesPerOutput:0},{...DEFAULT_QUEUE,maxPending:0}]){
  let network;network=new Network([[0,child]],{routes:()=>{network.policy={...DEFAULT_QUEUE,maxRoutesPerOutput:100,maxPending:100};return [{destination:0,channel:0,input:1}];},checkState:()=>[]},policy);
  assert.throws(()=>network.step(network.initialState(),{kind:'local',address:{machine:0,value:1}}),/bound exceeded/);
 }
});
test('network inputs snapshot state when requested, before first pull',()=>{
 const model={...child,inputs:s=>[s]},network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state=network.initialState();state.machines[0].value=7;state.pending.push({source:0,route:{destination:0,channel:0,input:2}});
 const inputs=network.inputs(state);state.machines[0].value=99;state.pending.length=0;
 assert.deepEqual([...inputs],[{kind:'local',address:{machine:0,value:7}},{kind:'deliver',index:0}]);
 const invalid=network.initialState();invalid.pending.push({source:1,route:{destination:0,channel:0,input:2}});
 assert.throws(()=>network.inputs(invalid),/unknown network machine/);
});
test('network input iteration retains admitted policy through child callbacks',()=>{
 let network;const model={...child,inputs:()=>{network.policy={...DEFAULT_QUEUE,allowDrop:false};return [1];}};
 network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]},{...DEFAULT_QUEUE,allowDrop:true});
 const state=network.initialState();state.pending.push({source:0,route:{destination:0,channel:0,input:2}});
 assert.deepEqual([...network.inputs(state)],[{kind:'local',address:{machine:0,value:1}},{kind:'deliver',index:0},{kind:'drop',index:0}]);
});
test('three-machine FIFO cycle, duplicate saturation, stable IDs',()=>{const n=new Network([[2,child],[0,child],[1,child]],{routes:o=>[{destination:(o.machine+1)%3,channel:7,input:o.value}],checkState:()=>[{id:'sum',status:'passed',details:''}]},{...DEFAULT_QUEUE,maxPending:2,maxPerChannel:2,allowDrop:true,allowDuplicate:true});const initial=n.initialState();let t=n.step(initial,{kind:'local',address:{machine:0,value:1}});assert.equal(initial.pending.length,0);assert.equal(t.disposition.kind,'ignored');assert.deepEqual(n.checkState(t.state).map(c=>c.id),['machine/0::safe','machine/1::safe','machine/2::safe','global::sum']);const dup=n.step(t.state,{kind:'duplicate',index:0});assert.throws(()=>n.step(dup.state,{kind:'deliver',index:1}),/FIFO/);assert.throws(()=>n.step(dup.state,{kind:'duplicate',index:0}),/incomplete/);for(let i=0;i<100;i++){t=n.step(t.state,{kind:'deliver',index:0});assert.equal(t.state.pending.length,1);}});
test('network snapshots policy and enumerates FIFO actions from an owned queue',()=>{
 let reads=0;const policy={...DEFAULT_QUEUE,maxPending:5,maxPerChannel:2,allowDrop:true,allowDuplicate:true,get maxRoutesPerOutput(){return ++reads===1?2:-1;}};
 const n=new Network([[0,child],[1,child]],{routes:()=>[],checkState:()=>[]},policy);assert.equal(reads,1);assert.equal(n.policy.maxRoutesPerOutput,2);
 const state=n.initialState();state.pending.push({source:0,route:{destination:1,channel:3,input:1}},{source:0,route:{destination:1,channel:3,input:2}},{source:1,route:{destination:0,channel:3,input:3}});
 const iterator=n.inputs(state)[Symbol.iterator](),first=iterator.next().value;state.pending.length=0;
 const actions=[first,...iterator];assert.deepEqual(actions.map(a=>a.kind),['local','local','deliver','drop','drop','deliver','drop','duplicate']);
 assert.deepEqual(actions.filter(a=>a.kind==='deliver').map(a=>a.index),[0,2]);assert.deepEqual(actions.filter(a=>a.kind==='duplicate').map(a=>a.index),[2]);
 const over=n.initialState();over.pending.push(...Array.from({length:3},()=>({source:0,route:{destination:1,channel:3,input:1}})));assert.throws(()=>n.validate(over),/channel bound/);
});
test('network queue actions are owned before child snapshot callbacks',()=>{
 let action,armed=false;const model={...child,cloneState:s=>{if(armed){action.kind='drop';action.index=1;}return s;},step:(s,i)=>({state:s+i,outputs:[],disposition:d})};
 const network=new Network([[0,model],[1,model]],{routes:()=>[],checkState:()=>[]},{...DEFAULT_QUEUE,maxPending:4,maxPerChannel:4,allowDrop:true,allowDuplicate:true});
 const state=network.initialState();state.pending.push({source:0,route:{destination:1,channel:1,input:7}},{source:1,route:{destination:0,channel:2,input:9}});
 action={kind:'deliver',index:0};armed=true;const delivered=network.step(state,action);armed=false;
 assert.equal(delivered.state.machines[1].value,7);assert.deepEqual(delivered.state.pending,[state.pending[1]]);assert.equal(action.kind,'drop');
 action={kind:'duplicate',index:0};armed=true;const duplicated=network.step(state,action);armed=false;
 assert.deepEqual(duplicated.state.pending,[state.pending[0],state.pending[1],state.pending[0]]);assert.equal(duplicated.outputs.length,0);
 assert.throws(()=>network.cloneInput({kind:'unknown',index:0}),/invalid network action/);
});
test('network transition checks use a stable snapshot across child and global callbacks',()=>{
 let state,input,transition,observed;
 const model={...child,checkTransitionInto:()=>{state.machines[0].value=99;input.index=1;transition.state.machines[0].value=88;transition.outputs.length=0;}};
 const network=new Network([[0,model],[1,model]],{routes:()=>[],checkState:()=>[],checkTransition:(s,i,t)=>{observed={s,i,t};return [];}},{...DEFAULT_QUEUE,maxPending:4});
 state=network.initialState();state.machines[0].value=1;state.pending.push({source:1,route:{destination:0,channel:0,input:3}},{source:0,route:{destination:1,channel:1,input:4}});input={kind:'deliver',index:0};
 transition={state:{machines:[{machine:0,value:4},{machine:1,value:0}],pending:[state.pending[1]]},outputs:[{machine:0,value:5}],disposition:d};
 withCheckSink([],sink=>network.checkTransitionInto(state,input,transition,sink));
 assert.equal(observed.s.machines[0].value,1);assert.equal(observed.i.index,0);assert.equal(observed.t.state.machines[0].value,4);assert.deepEqual(observed.t.outputs,[{machine:0,value:5}]);
});
test('pair retains routed effects until explicit delivery',()=>{const p=new Pair(child,child,{messages:o=>[{side:o.side==='left'?'right':'left',value:o.value}],checkState:()=>[]},1);let t=p.step(p.initialState(),{kind:'local',address:{side:'left',value:1}});assert.equal(t.state.right,0);t=p.step(t.state,{kind:'deliver',index:0});assert.equal(t.state.right,1);assert.equal(t.state.pending.length,1);});
test('pair step captures pending messages before child state clone callbacks',()=>{
 for(const action of [{kind:'local',address:{side:'left',value:1}},{kind:'deliver',index:0}]){
  let original;const left={...child,cloneState:s=>{original.pending[0].value=99;return s;}};
  const pair=new Pair(left,child,{messages:()=>[],checkState:()=>[]},4);
  original={left:0,right:0,pending:[{side:'left',value:7}]};
  const result=pair.step(original,action);
  if(action.kind==='local')assert.equal(result.state.pending[0].value,7);
  else assert.equal(result.state.left,7);
 }
 let copies=0;const pair=new Pair({...child,cloneState:s=>{copies++;return s;}},child,{messages:()=>[],checkState:()=>[]});
 assert.throws(()=>pair.step({left:0,right:0,pending:[]},{kind:'deliver',index:0}),/index/);assert.equal(copies,0);
});
test('pair step pins child and wiring callbacks before cloning',()=>{
 let pair;const replacement={...child,step:()=>{throw new Error('replacement child');}};
 const left={...child,cloneInput:i=>{pair.left=replacement;pair.wiring={messages:()=>{throw new Error('replacement wiring');},checkState:()=>[]};return i;},step:(s,i)=>({state:s+i,outputs:[i],disposition:d})};
 pair=new Pair(left,child,{messages:()=>[],checkState:()=>[]});
 const t=pair.step({left:0,right:0,pending:[]},{kind:'local',address:{side:'left',value:2}});
 assert.equal(t.state.left,2);assert.equal(t.outputs[0].value,2);
});
test('pair step and lazy input enumeration own state and action snapshots',()=>{
 let state,input;const right={...child,step:(s,i)=>{state.pending.push({side:'left',value:99});input.index=1;return {state:s+i,outputs:[],disposition:d};}};
 const pair=new Pair(child,right,{messages:()=>[],checkState:()=>[]},4);
 state={left:0,right:1,pending:[{side:'right',value:7}]};input={kind:'deliver',index:0};
 const transition=pair.step(state,input);assert.equal(transition.state.right,8);assert.deepEqual(transition.state.pending,[]);assert.equal(state.pending.length,2);assert.equal(input.index,1);
 const enumerating={...child,inputs:s=>[s]};const other=new Pair(enumerating,enumerating,{messages:()=>[],checkState:()=>[]},4);
 state={left:2,right:3,pending:[{side:'left',value:4}]};const iterator=other.inputs(state)[Symbol.iterator](),first=iterator.next().value;state.right=99;state.pending.length=0;
 assert.deepEqual([first,...iterator],[{kind:'local',address:{side:'left',value:2}},{kind:'local',address:{side:'right',value:3}},{kind:'deliver',index:0}]);
});
test('pair inputs snapshot state when requested, before first pull',()=>{
 const enumerating={...child,inputs:s=>[s]};
 const pair=new Pair(enumerating,enumerating,{messages:()=>[],checkState:()=>[]},4);
 const state={left:2,right:3,pending:[{side:'left',value:4}]};
 const inputs=pair.inputs(state);state.left=90;state.right=99;state.pending.length=0;
 assert.deepEqual([...inputs],[{kind:'local',address:{side:'left',value:2}},{kind:'local',address:{side:'right',value:3}},{kind:'deliver',index:0}]);
});
test('pair owns a delivery action before child snapshot callbacks',()=>{
 let action,armed=false;const model={...child,cloneState:s=>{if(armed)action.index=1;return s;},step:(s,i)=>({state:s+i,outputs:[],disposition:d})};
 const pair=new Pair(model,model,{messages:()=>[],checkState:()=>[]},4);
 const state={left:0,right:0,pending:[{side:'right',value:7},{side:'left',value:9}]};action={kind:'deliver',index:0};armed=true;
 const result=pair.step(state,action);armed=false;
 assert.equal(result.state.right,7);assert.equal(result.state.left,0);assert.deepEqual(result.state.pending,[state.pending[1]]);assert.equal(action.index,1);
});
test('pair transition checks use a stable snapshot across child and global callbacks',()=>{
 let state,input,transition,observed;
 const model={...child,checkTransitionInto:(_s,_i,_t,_sink)=>{state.left=99;input.index=1;transition.state.left=88;transition.outputs.length=0;}};
 const pair=new Pair(model,model,{messages:()=>[],checkState:()=>[],checkTransition: (s,i,t)=>{observed={s,i,t};return [];}},4);
 state={left:1,right:2,pending:[{side:'left',value:3},{side:'right',value:4}]};input={kind:'deliver',index:0};
 transition={state:{left:4,right:2,pending:[state.pending[1]]},outputs:[{side:'left',value:5}],disposition:d};
 withCheckSink([],sink=>pair.checkTransitionInto(state,input,transition,sink));
 assert.equal(observed.s.left,1);assert.equal(observed.i.index,0);assert.equal(observed.t.state.left,4);assert.deepEqual(observed.t.outputs,[{side:'left',value:5}]);
});
test('pair input codec rejects unknown action tags instead of encoding a delivery',()=>{
 const schema=pairSchemas(vc.u8,vc.u8,vc.u8,vc.u8,vc.u8,vc.u8).input;
 assert.throws(()=>encodeValue(schema,{kind:'drop',index:0},1024),/invalid composition input tag/);
 assert.deepEqual(decodeValue(schema,encodeValue(schema,{kind:'deliver',index:7},1024)),{kind:'deliver',index:7});
});
test('pair and network equality isolate snapshots and reject non-boolean callbacks',()=>{
 const m={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:(a,b)=>{a.value=99;b.value=99;return true;}};
 const eq=(a,b)=>{a.value=99;b.value=99;return true;};
 const a={left:{value:1},right:{value:2},pending:[{side:'left',value:{value:3}}]},b=structuredClone(a);
 const pair=new Pair(m,m,{messages:()=>[],checkState:()=>[]},2,eq,eq);assert.equal(pair.equalStates(a,b),true);assert.equal(a.left.value,1);assert.equal(b.pending[0].value.value,3);
 const invalid=new Pair({...m,equalStates:()=>1},m,{messages:()=>[],checkState:()=>[]},2,eq,eq);assert.throws(()=>invalid.equalStates(a,b),/boolean synchronously/);
 const n=new Network([[1,m]],{routes:()=>[],checkState:()=>[]},DEFAULT_QUEUE,eq);
 const na={machines:[{machine:1,value:{value:1}}],pending:[{source:1,route:{destination:1,channel:0,input:{value:3}}}]},nb=structuredClone(na);
 assert.equal(n.equalStates(na,nb),true);assert.equal(na.machines[0].value.value,1);assert.equal(nb.pending[0].route.input.value,3);
 const bad=new Network([[1,m]],{routes:()=>[],checkState:()=>[]},DEFAULT_QUEUE,()=>Promise.resolve(true));assert.throws(()=>bad.equalStates(na,nb),/boolean synchronously/);
});
test('pair equality captures both sides and pending messages before callbacks',()=>{
 const a={left:{value:1},right:{value:2},pending:[{side:'left',value:{value:3}}]},b=structuredClone(a);
 const left={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:()=>{b.right.value=99;b.pending[0].value.value=99;return true;}};
 const right={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:(x,y)=>x.value===y.value};
 const eq=(x,y)=>x.value===y.value;
 const pair=new Pair(left,right,{messages:()=>[],checkState:()=>[]},2,eq,eq);
 assert.equal(pair.equalStates(a,b),true);
 assert.equal(b.right.value,99);assert.equal(b.pending[0].value.value,99);
});
test('pair equality pins child and input comparators for the operation',()=>{
 const state={left:{value:1},right:{value:2},pending:[{side:'left',value:{value:3}}]};let pair;
 const left={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:()=>{pair.right={...right,equalStates:()=>false};pair.equalLeftInput=()=>false;return true;}};
 const right={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:(x,y)=>x.value===y.value};
 pair=new Pair(left,right,{messages:()=>[],checkState:()=>[]},2,(x,y)=>x.value===y.value,()=>true);
 assert.equal(pair.equalStates(state,structuredClone(state)),true);
});
test('pair equality freezes both structures before clone hooks',()=>{
 const a={left:{value:1},right:{value:2},pending:[{side:'left',value:{value:3}}]},b=structuredClone(a);
 let first=true;
 const left={...child,cloneState:s=>{if(first){first=false;b.right={value:99};b.pending[0]={side:'right',value:{value:99}};}return {...s};},cloneInput:i=>({...i}),equalStates:(x,y)=>x.value===y.value};
 const right={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:(x,y)=>x.value===y.value};
 const eq=(x,y)=>x.value===y.value;
 const pair=new Pair(left,right,{messages:()=>[],checkState:()=>[]},2,eq,eq);
 assert.equal(pair.equalStates(a,b),true);
 assert.equal(b.pending[0].side,'right');
});
test('network equality captures all observations and pins callbacks before comparison',()=>{
 const a={machines:[{machine:0,value:{value:1}},{machine:1,value:{value:2}}],pending:[{source:0,route:{destination:1,channel:3,input:{value:4}}}]},b=structuredClone(a);
 const second={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:(x,y)=>x.value===y.value};
 let network;
 const first={...child,cloneState:s=>({...s}),cloneInput:i=>({...i}),equalStates:()=>{b.machines[1].value.value=90;b.pending[0].route.input.value=90;second.equalStates=()=>false;network.equalInput=()=>false;return true;}};
 network=new Network([[0,first],[1,second]],{routes:()=>[],checkState:()=>[]},DEFAULT_QUEUE,(x,y)=>x.value===y.value);
 assert.equal(network.equalStates(a,b),true);
 assert.equal(b.machines[1].value.value,90);assert.equal(b.pending[0].route.input.value,90);
});
test('pair and network state checks share an owned pre-callback snapshot',()=>{
 let pairState,networkState,pairSeen,networkSeen,rightSeen;
 const mutating={...child,checkState:()=>{if(pairState)pairState.right=90;if(networkState)networkState.machines[1].value=90;return [];}};
 const reading={...child,checkState:s=>{rightSeen=s;return [];}};
 const pair=new Pair(mutating,reading,{messages:()=>[],checkState:s=>{pairSeen=s;return [];}});
 pairState={left:1,right:2,pending:[]};pair.checkState(pairState);assert.equal(rightSeen,2);assert.equal(pairSeen.right,2);pairState=null;
 const network=new Network([[0,mutating],[1,reading]],{routes:()=>[],checkState:s=>{networkSeen=s;return [];}},DEFAULT_QUEUE);
 networkState=network.initialState();networkState.machines[1].value=2;network.checkState(networkState);assert.equal(rightSeen,2);assert.equal(networkSeen.machines[1].value,2);
});
test('pair and network reject asynchronous global check hooks',()=>{
 const wiring={messages:()=>[],routes:()=>[],checkState:()=>[],checkStateInto:()=>Promise.resolve(),checkTransitionInto:()=>Promise.resolve()};
 const pair=new Pair(child,child,wiring),ps=pair.initialState(),pi={kind:'local',address:{side:'left',value:1}},pt=pair.step(ps,pi);
 assert.throws(()=>pair.checkState(ps),/synchronously/);assert.throws(()=>withCheckSink([],sink=>pair.checkTransitionInto(ps,pi,pt,sink)),/synchronously/);
 const network=new Network([[0,child]],wiring),ns=network.initialState(),ni={kind:'local',address:{machine:0,value:1}},nt=network.step(ns,ni);
 assert.throws(()=>network.checkState(ns),/synchronously/);assert.throws(()=>withCheckSink([],sink=>network.checkTransitionInto(ns,ni,nt,sink)),/synchronously/);
});
import {scopedKeySchema,lifecycleHistorySchema} from '../dist/lifecycle.js';import {valueCodecs as vc,encodeValue,decodeValue} from '../dist/value_codec.js';
test('historical and indexed codec golden fields and restart',()=>{const m=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k),schema=lifecycleHistorySchema(m,vc.u8);assert.deepEqual(encodeValue(schema,m.initialState(),1024),new Uint8Array(20));const indexed=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k),is=lifecycleHistorySchema(indexed,scopedKeySchema(vc.u8));assert.deepEqual(encodeValue(is,indexed.initialState(),1024),new Uint8Array([1,...Array(28).fill(0)]));const h=indexed.advance(indexed.initialState(),[{kind:'begin',key:{epoch:2n,generation:3n,key:7},time:10n}],[],d),bytes=encodeValue(is,h,1024);assert.equal(bytes.length,57);assert(indexed.equalHistory(h,decodeValue(is,bytes)));assert.throws(()=>decodeValue(is,new Uint8Array([...bytes,0])));});
test('raw legacy strings survive restart without code collisions',()=>{const m=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k),c=lifecycleHistorySchema(m,vc.u8),h=m.initialState();h.violations.push({legacy:'time_backwards',code:'time_backwards',key:undefined,event:'',observedAt:0n,prior:undefined});const bytes=encodeValue(c,h,1024),restored=decodeValue(c,bytes);assert.deepEqual(encodeValue(c,restored,1024),bytes);assert(m.equalHistory(h,restored));});
test('legacy lifecycle rejects state its codec cannot represent',()=>{
 const m=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k),c=lifecycleHistorySchema(m,vc.u8);
 const fenced=m.initialState();fenced.retiredBefore=1n;
 const published=m.initialState();published.obligations.push([1,{deadline:10n,cancelled:false,settled:true,published:true}]);
 for(const h of [fenced,published]){
  assert.throws(()=>m.validate(h),/legacy/);
  assert.throws(()=>m.advance(h,[],[],d),/legacy/);
  assert.throws(()=>encodeValue(c,h,1024),/legacy/);
 }
 // Legacy Rust encoding is independent of the monitor's retention limits.
 const limited=new LifecycleMonitor(spec,{...limits,obligations:0},(a,b)=>a-b,k=>k);
 const representable=limited.initialState();representable.obligations.push([1,{deadline:10n,cancelled:false,settled:true,published:false}]);
 assert.throws(()=>limited.validate(representable),/retention/);
 assert.doesNotThrow(()=>encodeValue(lifecycleHistorySchema(limited,vc.u8),representable,1024));
});
test('legacy lifecycle publish and release inspect every restored duplicate obligation',()=>{
 const m=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k),h=m.initialState();
 h.obligations.push([1,{deadline:100n,cancelled:true,settled:false,published:false}],[1,{deadline:100n,cancelled:false,settled:true,published:false}]);
 const published=m.advance(h,[{kind:'publish',key:1}],[],d);
 assert.equal(published.violations.length,0);
 const pending=m.initialState();pending.obligations.push([1,{deadline:100n,cancelled:true,settled:false,published:false}],[1,{deadline:100n,cancelled:false,settled:false,published:false}]);pending.resources.push([1,true]);
 const released=m.advance(pending,[{kind:'release',key:1}],[],d);
 assert(released.violations.some(v=>v.code==='release_before_settlement'));
});
test('acknowledged ownership transfer versus early grant',()=>{const role=(id,fault)=>({...child,initialState:()=>id===20?1:0,checkState:()=>[],step:(s,i)=>{let state=s,outputs=[];if(id===10&&i===1){state=1;outputs=fault?[10,11]:[10];}else if(id===10&&i===4){state=2;outputs=[11];}else if(id===20&&i===2){state=0;outputs=[12];}else if(id===30&&i===3)state=1;return {state,outputs,disposition:d};}}),wiring={routes:o=>{const [destination,input]={10:[20,2],11:[30,3],12:[10,4]}[o.value];return [{destination,channel:0,input}];},checkState:s=>{const get=id=>s.machines.find(m=>m.machine===id).value;return [get(20)+get(30)>1?{id:'unique-owner',status:'failed',details:'two owners'}:{id:'unique-owner',status:'passed',details:''}];}},make=fault=>new Network([10,20,30].map(id=>[id,role(id,fault)]),wiring);const safe=make(false);let s=safe.step(safe.initialState(),{kind:'local',address:{machine:10,value:1}}).state;for(let i=0;i<3;i++){s=safe.step(s,{kind:'deliver',index:0}).state;assert.equal(safe.checkState(s).at(-1).status,'passed');}const bad=make(true);s=bad.step(bad.initialState(),{kind:'local',address:{machine:10,value:1}}).state;s=bad.step(s,{kind:'deliver',index:1}).state;assert.equal(bad.checkState(s).at(-1).status,'failed');});

test('network step captures pending messages before machine clone hooks',()=>{
 for(const action of [{kind:'local',address:{machine:1,value:{n:2}}},{kind:'duplicate',index:0}]){
  let state,armed=false;
  const model={...child,cloneInput:i=>({...i}),cloneState:s=>{if(armed)state.pending[0].route.input.n=99;return s;},step:(s,i)=>({state:s+i.n,outputs:[],disposition:d})};
  const network=new Network([[1,model]],{routes:()=>[],checkState:()=>[]},{...DEFAULT_QUEUE,allowDuplicate:true});
  state={machines:[{machine:1,value:0}],pending:[{source:1,route:{destination:1,channel:0,input:{n:7}}}]};armed=true;
  const result=network.step(state,action);
  assert.equal(result.state.pending[0].route.input.n,7);
  if(action.kind==='duplicate')assert.equal(result.state.pending[1].route.input.n,7);
  else assert.equal(result.state.machines[0].value,2);
 }
});

test('network state checks capture pending payloads before machine clone hooks',()=>{
 let state,armed=false,seen;
 const model={...child,cloneInput:i=>({...i}),cloneState:s=>{if(armed)state.pending[0].route.input.n=99;return s;}};
 const network=new Network([[1,model]],{routes:()=>[],checkState:s=>{seen=s.pending[0].route.input.n;return [];}});
 state={machines:[{machine:1,value:0}],pending:[{source:1,route:{destination:1,channel:0,input:{n:7}}}]};
 armed=true;network.checkState(state);
 assert.equal(seen,7);assert.equal(state.pending[0].route.input.n,99);
});

test('network cloneState pins child callbacks before cloning the queue',()=>{
 let armed=false;
 const model={...child,cloneInput:i=>{if(armed){model.cloneInput=()=>({n:90});model.cloneState=()=>90;}return {...i};},cloneState:s=>s};
 const network=new Network([[1,model]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:1,value:3}],pending:[{source:1,route:{destination:1,channel:0,input:{n:7}}},{source:1,route:{destination:1,channel:1,input:{n:8}}}]};
 armed=true;const copy=network.cloneState(state);
 assert.equal(copy.machines[0].value,3);assert.deepEqual(copy.pending.map(m=>m.route.input.n),[7,8]);
});

test('network step captures topology before local input clone callback',()=>{
 let state,armed=false;
 const model={...child,cloneInput:i=>{if(armed){state.machines[0].machine=7;state.pending[0].route.channel=9;}return {...i};},cloneState:s=>s,step:(s,i)=>({state:s+i.n,outputs:[],disposition:d})};
 const network=new Network([[1,model]],{routes:()=>[],checkState:()=>[]});
 state={machines:[{machine:1,value:0}],pending:[{source:1,route:{destination:1,channel:0,input:{n:7}}}]};armed=true;
 const result=network.step(state,{kind:'local',address:{machine:1,value:{n:2}}});
 assert.equal(result.state.machines[0].machine,1);assert.equal(result.state.pending[0].route.channel,0);assert.equal(result.state.machines[0].value,2);
});

test('network inputs capture channel metadata without cloning queued payloads',()=>{
 let state,armed=false;
 const model={...child,cloneState:s=>{if(armed)state.pending[1].route.channel=9;return s;},cloneInput:()=>{throw new Error('queued payload must not be cloned');},inputs:()=>[]};
 const network=new Network([[1,model]],{routes:()=>[],checkState:()=>[]});
 state={machines:[{machine:1,value:0}],pending:[{source:1,route:{destination:1,channel:0,input:{}}},{source:1,route:{destination:1,channel:0,input:{}}}]};
 armed=true;
 assert.deepEqual([...network.inputs(state)],[{kind:'deliver',index:0}]);
 assert.equal(state.pending[1].route.channel,9);
});

test('pair cloneState captures pending before state hooks and pins clone callbacks',()=>{
 let state, armed=false;
 const left={...child,cloneInput:i=>{if(armed){left.cloneInput=()=>({n:90});left.cloneState=()=>90;}return {...i};},cloneState:s=>{if(armed)state.pending[0].value.n=99;return s;}};
 const pair=new Pair(left,child,{messages:()=>[],checkState:()=>[]});
 state={left:1,right:2,pending:[{side:'left',value:{n:3}},{side:'left',value:{n:4}}]};
 armed=true;
 const copy=pair.cloneState(state);
 assert.deepEqual(copy,{left:1,right:2,pending:[{side:'left',value:{n:3}},{side:'left',value:{n:4}}]});
});

test('network queue-only transition checks validate without clone hooks',()=>{
 const model={...child,cloneState:()=>{throw Error('unexpected state clone');},cloneInput:()=>{throw Error('unexpected input clone');},cloneOutput:()=>{throw Error('unexpected output clone');}};
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]},{...DEFAULT_QUEUE,allowDrop:true,allowDuplicate:true});
 const state={machines:[{machine:0,value:0}],pending:[{source:0,route:{destination:0,channel:0,input:1}}]};
 const transition={state,outputs:[],disposition:{kind:'accepted'}};
 for(const kind of ['drop','duplicate'])withCheckSink([],sink=>network.checkTransitionInto(state,{kind,index:0},transition,sink));
 assert.throws(()=>withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'drop',index:1},transition,sink)),/index out of range/);
 assert.throws(()=>withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'drop',index:0},{...transition,outputs:[{machine:0,value:1}]},sink)),/queue action emitted/);
});

test('network transition maps output addresses and copies sequentially',()=>{
 let copies=0;
 const model={...child,cloneOutput:v=>{copies++;if(v==='bad-copy')throw Error('output copy failed');return v;}};
 const network=new Network([[0,model],[1,child]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0},{machine:1,value:0}],pending:[]};
 const action={kind:'local',address:{machine:0,value:1}};
 const check=outputs=>withCheckSink([],sink=>network.checkTransitionInto(state,action,{state,outputs,disposition:{kind:'accepted'}},sink));
 assert.throws(()=>check([{machine:0,value:'bad-copy'},{machine:1,value:2}]),/output copy failed/);
 assert.equal(copies,1);
 copies=0;
 assert.throws(()=>check([{machine:1,value:2},{machine:0,value:'bad-copy'}]),/wrong network child output address/);
 assert.equal(copies,0);
 copies=0;check([{machine:0,value:2}]);assert.equal(copies,1);
});

test('network transition pins target and callbacks before clone hooks',()=>{
 let action, calls=[];
 const model={...child,cloneInput:v=>{action.address.machine=1;model.cloneOutput=()=>{throw Error('replacement output');};model.checkTransitionInto=()=>{throw Error('replacement check');};return v;},cloneOutput:v=>v,checkTransitionInto:()=>{calls.push('original');}};
 const network=new Network([[0,model],[1,child]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0},{machine:1,value:0}],pending:[]};
 action={kind:'local',address:{machine:0,value:1}};
 withCheckSink([],sink=>network.checkTransitionInto(state,action,{state,outputs:[{machine:0,value:2}],disposition:{kind:'accepted'}},sink));
 assert.deepEqual(calls,['original']);
});

test('network transition retains admitted delivery despite policy replacement',()=>{
 let network;
 const model={...child,cloneInput:v=>{network.policy={...DEFAULT_QUEUE,delivery:'FifoPerChannel'};return v;}};
 network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]},{...DEFAULT_QUEUE,delivery:'Unordered'});
 const state={machines:[{machine:0,value:0}],pending:[{source:0,route:{destination:0,channel:0,input:1}},{source:0,route:{destination:0,channel:0,input:2}}]};
 withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'deliver',index:1},{state,outputs:[],disposition:{kind:'accepted'}},sink));
});

test('network transition does not enumerate unrelated child properties',()=>{
 const model={...child,checkTransitionInto:()=>{}};
 Object.defineProperty(model,'unrelated',{enumerable:true,get(){throw Error('unrelated getter');}});
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0}],pending:[]};
 withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'local',address:{machine:0,value:1}},{state,outputs:[],disposition:{kind:'accepted'}},sink));
});

test('network transition preserves invalid append hook error',()=>{
 const model={...child,checkTransitionInto:42};
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0}],pending:[]};
 assert.throws(()=>withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'local',address:{machine:0,value:1}},{state,outputs:[],disposition:{kind:'accepted'}},sink)),/append check hook must be a function/);
});

test('network transition ignores malformed legacy hook when append hook exists',()=>{
 let checks=0;const model={...child,checkTransition:42,checkTransitionInto:()=>{checks++;}};
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0}],pending:[]};
 withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'local',address:{machine:0,value:1}},{state,outputs:[],disposition:{kind:'accepted'}},sink));
 assert.equal(checks,1);
});

test('network transition does not read legacy getter when append hook exists',()=>{
 let checks=0;const model={...child,checkTransitionInto:()=>{checks++;}};
 Object.defineProperty(model,'checkTransition',{get(){throw Error('legacy getter');}});
 const network=new Network([[0,model]],{routes:()=>[],checkState:()=>[]});
 const state={machines:[{machine:0,value:0}],pending:[]};
 withCheckSink([],sink=>network.checkTransitionInto(state,{kind:'local',address:{machine:0,value:1}},{state,outputs:[],disposition:{kind:'accepted'}},sink));
 assert.equal(checks,1);
});

test('lifecycle rejects restored timestamps outside u64 before checking or advancing',()=>{
 const legacy=new LifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);
 const indexed=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);
 for(const monitor of [legacy,indexed]) {
  const key=monitor===legacy?1:{epoch:1n,generation:0n,key:1};
  for(const deadline of [-1n,0x10000000000000000n,1]) {
   const h=monitor.initialState();h.obligations.push([key,{deadline,cancelled:false,settled:true,published:false}]);
   assert.throws(()=>monitor.checkState(h),/u64/);
   assert.throws(()=>monitor.advance(h,[],[],d),/u64/);
  }
 }
 const h=indexed.initialState();h.violations.push({key:undefined,code:'x',event:'tick',observedAt:-1n,prior:undefined});
 assert.throws(()=>indexed.checkState(h),/u64/);
 h.violations[0].observedAt=0n;h.violations[0].prior={deadline:-1n,cancelled:false,settled:true,published:false};
 assert.throws(()=>indexed.checkState(h),/u64/);
});

test('indexed lifecycle rejects restored invalid scoped generations and violation keys',()=>{
 const monitor=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);
 for(const key of [{epoch:1n,generation:-1n,key:1},{epoch:1n,generation:0x10000000000000000n,key:1},{epoch:-1n,generation:0n,key:1}]){
  for(const entry of ['obligations','resources','violations']){
   const h=monitor.initialState();
   if(entry==='obligations')h.obligations.push([key,{deadline:1n,cancelled:false,settled:true,published:false}]);
   else if(entry==='resources')h.resources.push([key,false]);
   else h.violations.push({key,code:'x',event:'tick',observedAt:0n,prior:undefined});
   assert.throws(()=>monitor.checkState(h),/u64/);
  }
 }
});

test('lifecycle rejects nonboolean restored state before classification',()=>{
 let calls=0;const observing={...spec,inputEvents:()=>{calls++;return [];}};
 for(const monitor of [new LifecycleMonitor(observing,limits,(a,b)=>a-b,k=>k),new IndexedLifecycleMonitor(observing,limits,(a,b)=>a-b,k=>k)]){
  const key=monitor instanceof IndexedLifecycleMonitor?{epoch:1n,generation:0n,key:1}:1;
  for(const field of ['cancelled','settled','published']){
   const h=monitor.initialState(),v={deadline:1n,cancelled:false,settled:true,published:false};v[field]='false';h.obligations.push([key,v]);
   assert.throws(()=>monitor.advance(h,[],[],d),/boolean/);assert.equal(calls,0);
  }
  const h=monitor.initialState();h.resources.push([key,'false']);assert.throws(()=>monitor.checkState(h),/boolean/);
  if(monitor instanceof IndexedLifecycleMonitor){const x=monitor.initialState(),schema=lifecycleHistorySchema(monitor,scopedKeySchema(vc.u8));x.violations.push({key:undefined,code:'x',event:'tick',observedAt:0n,prior:{deadline:1n,cancelled:0,settled:true,published:false}});assert.throws(()=>monitor.checkState(x),/boolean/);for(const prior of [null,false,0]){x.violations[0].prior=prior;assert.throws(()=>monitor.checkState(x),/prior obligation/);assert.throws(()=>encodeValue(schema,x,1024),/prior obligation/);}}
 }
});

test('pair rejects a wrong-side output before calling its clone hook',()=>{
 let wrongCopies=0;
 const right={...child,cloneOutput:()=>{wrongCopies++;throw Error('wrong-side clone called');}};
 const pair=new Pair(child,right,{messages:()=>[],checkState:()=>[]});
 const state={left:0,right:0,pending:[]};
 const action={kind:'local',address:{side:'left',value:1}};
 assert.throws(()=>withCheckSink([],sink=>pair.checkTransitionInto(state,action,{state,outputs:[{side:'right',value:1}],disposition:{kind:'accepted'}},sink)),/wrong child output address/);
 assert.equal(wrongCopies,0);
});

test('indexed Rust Debug diagnostics are explicit and preserve owned history',()=>{
 const defaultMonitor=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k);
 const formatter={identity:'fixture-key-v1',formatKey:k=>`${k}`,formatString:s=>JSON.stringify(s)};
 const m=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,formatter);
 const h=m.initialState(),k={epoch:2n,generation:3n,key:7};
 h.obligations.push([k,{deadline:4n,cancelled:false,settled:false,published:false}]);h.now=4n;
 h.violations.push({key:k,code:'bad "quote"',event:'tick',observedAt:4n,prior:{deadline:4n,cancelled:true,settled:false,published:false}});
 const status='RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 4)), retired_before: 0 }';
 const checks=m.checkState(h);
 assert.equal(checks[0].details,`violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: ${JSON.stringify('bad "quote"')}, event: "tick", observed_at: 4, prior: Some(Obligation { deadline: 4, cancelled: true, settled: false, published: false }) }]; retention=${status}`);
 assert.equal(checks[1].details,`finite history: ${status}`);
 assert.match(m.metadata().name,/;diagnostics=rust-debug-v1;formatter=14:fixture-key-v1$/);
 assert.match(defaultMonitor.metadata().name,/;diagnostics=native-codes-v1$/);
 assert.equal(defaultMonitor.checkState(h)[0].details,'bad "quote"');
 const empty=m.initialState();assert.deepEqual(m.checkState(empty),defaultMonitor.checkState(empty));
 const failing=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,{...formatter,formatKey:()=>{throw new Error('failed');}});
 assert.throws(()=>failing.checkState(h),/formatter failed/);
 assert.throws(()=>new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,{...formatter,identity:'\ud800'}),/invalid Rust diagnostics/);
});

test('Rust diagnostic identity is literal UTF-8 and methods retain their receiver',()=>{
 const diagnostics={identity:'$&é',prefix:'k',formatKey(key){return this.prefix+key;},formatString(value){return JSON.stringify(value);}};
 const m=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,diagnostics),h=m.initialState();
 h.obligations.push([{epoch:1n,generation:0n,key:2},{deadline:9n,cancelled:false,settled:false,published:false}]);
 assert.match(m.metadata().name,/;formatter=4:\$&é$/);
 assert.match(m.checkState(h)[1].details,/key: k2/);
 const bad=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,{...diagnostics,formatKey:()=>String.fromCharCode(0xd800)});
 assert.throws(()=>bad.checkState(h),/invalid Unicode/);
});

test('indexed Rust Debug diagnostic matches real Rust duplicate begin reference',()=>{
 const m=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,{identity:'rust-u8',formatKey:k=>String(k),formatString:s=>JSON.stringify(s)});
 const key={epoch:2n,generation:3n,key:7},begin={kind:'begin',key,time:9n};
 const h=m.advance(m.initialState(),[begin,begin],[],d);
 assert.deepEqual(m.checkState(h),[
  {id:'lifecycle.indexed.safety',status:'failed',details:'violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: "key_reused", event: "begin", observed_at: 0, prior: Some(Obligation { deadline: 9, cancelled: false, settled: false, published: false }) }]; retention=RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }'},
  {id:'lifecycle.indexed.obligations',status:'skipped',details:'finite history: RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }'}]);
});

test('Rust diagnostic formatters reject hostile non-string values as model errors',()=>{
 const history={now:0n,retiredBefore:0n,obligations:[],resources:[],violations:[{key:{epoch:1n,generation:0n,key:2},code:'bad',event:'begin',observedAt:0n,prior:undefined}]};
 for(const field of ['formatKey','formatString']){
  const revoked=Proxy.revocable({},{});revoked.revoke();
  let probes=0;const trapped=new Proxy({}, {getPrototypeOf(){probes++;throw Error('unexpected prototype probe');},get(){probes++;throw Error('unexpected property probe');}});
  for(const value of [revoked.proxy,trapped,Promise.reject(Error('async formatter'))]){
   const fmt={identity:'fixture',formatKey:k=>String(k),formatString:s=>JSON.stringify(s),[field]:()=>value};
   const m=new IndexedLifecycleMonitor(spec,limits,(a,b)=>a-b,k=>k,fmt);
   assert.throws(()=>m.checkState(history),error=>error.name==='ModelError'&&/must return a string synchronously/.test(error.message));
  }
  assert.equal(probes,0);
 }
});
