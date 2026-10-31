import {EnumerateModel,Transition,Check,ModelError} from './core.js';
import {CheckSink,withCheckSink,appendStateChecks,appendTransitionChecks,requireSynchronousCheckReturn} from './checks.js';
export type PairAddress<L,R>={readonly side:'left';readonly value:L}|{readonly side:'right';readonly value:R};
export type PairInput<L,R>={readonly kind:'local';readonly address:PairAddress<L,R>}|{readonly kind:'deliver';readonly index:number};
export interface PairState<L,R,LI,RI>{readonly left:L;readonly right:R;readonly pending:readonly PairAddress<LI,RI>[]}
export interface PairWiring<L,R,LI,RI,LO,RO>{messages(o:PairAddress<LO,RO>):Iterable<PairAddress<LI,RI>>;checkState(s:PairState<L,R,LI,RI>):Iterable<Check>;checkStateInto?(s:PairState<L,R,LI,RI>,sink:CheckSink):void;checkTransitionInto?(s:PairState<L,R,LI,RI>,i:PairInput<LI,RI>,t:Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,sink:CheckSink):void;checkTransition?(s:PairState<L,R,LI,RI>,i:PairInput<LI,RI>,t:Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>):Iterable<Check>}
function ns(prefix:string,sink:CheckSink,fn:(s:CheckSink)=>void){const cs:Check[]=[];try{withCheckSink(cs,fn);}finally{for(const c of cs){const id=prefix+'::'+c.id;if(sink.snapshot().some(v=>v.id===id))throw new ModelError('duplicate composed property id');sink.push({...c,id});}}}
function equality(value:unknown):boolean{if(typeof value==='boolean')return value;if(value instanceof Promise)void value.catch(()=>{});throw new ModelError('pair equality must return boolean synchronously');}
export class Pair<L,R,LI,RI,LO,RO>{
 constructor(readonly left:EnumerateModel<L,LI,LO>,readonly right:EnumerateModel<R,RI,RO>,readonly wiring:PairWiring<L,R,LI,RI,LO,RO>,readonly maxPending=32,readonly equalLeftInput?: (a:LI,b:LI)=>boolean,readonly equalRightInput?: (a:RI,b:RI)=>boolean){if(!Number.isSafeInteger(maxPending)||maxPending<0)throw new ModelError('invalid pending bound');}
 equalStates(a:PairState<L,R,LI,RI>,b:PairState<L,R,LI,RI>):boolean{
  const left=this.left,right=this.right,eqLeftInput=this.equalLeftInput,eqRightInput=this.equalRightInput;
  if(!eqLeftInput||!eqRightInput)throw new ModelError('pair equality requires input equality');
  const cloneLeft=left.cloneState,cloneRight=right.cloneState,cloneLI=left.cloneInput,cloneRI=right.cloneInput,eqLeft=left.equalStates,eqRight=right.equalStates;
  // Freeze both observations' structure before any application clone callback.
  const raw=(s:PairState<L,R,LI,RI>):PairState<L,R,LI,RI>=>({left:s.left,right:s.right,pending:s.pending.map(m=>({side:m.side,value:m.value} as PairAddress<LI,RI>))});
  const rawA=raw(a),rawB=raw(b);
  const capture=(s:PairState<L,R,LI,RI>):PairState<L,R,LI,RI>=>({left:cloneLeft.call(left,s.left),right:cloneRight.call(right,s.right),pending:s.pending.map(m=>{
   if(m.side==='left')return {side:'left',value:cloneLI.call(left,m.value)};
   if(m.side==='right')return {side:'right',value:cloneRI.call(right,m.value)};
   throw new ModelError('invalid pair address tag');
  })});
  const beforeA=capture(rawA),beforeB=capture(rawB);
  return equality(eqLeft.call(left,cloneLeft.call(left,beforeA.left),cloneLeft.call(left,beforeB.left)))&&equality(eqRight.call(right,cloneRight.call(right,beforeA.right),cloneRight.call(right,beforeB.right)))&&beforeA.pending.length===beforeB.pending.length&&beforeA.pending.every((m,j)=>{const n=beforeB.pending[j];return m.side==='left'&&n.side==='left'?equality(eqLeftInput.call(this,cloneLI.call(left,m.value),cloneLI.call(left,n.value))):m.side==='right'&&n.side==='right'?equality(eqRightInput.call(this,cloneRI.call(right,m.value),cloneRI.call(right,n.value))):false;});
 }
 private message(a:PairAddress<LI,RI>):PairAddress<LI,RI>{if(a.side!=='left'&&a.side!=='right')throw new ModelError('invalid pair address tag');return a.side==='left'?{side:'left',value:this.left.cloneInput(a.value)}:{side:'right',value:this.right.cloneInput(a.value)};}
 cloneState(s:PairState<L,R,LI,RI>):PairState<L,R,LI,RI>{
  const left=this.left,right=this.right,cloneLeft=left.cloneState,cloneRight=right.cloneState,cloneLI=left.cloneInput,cloneRI=right.cloneInput;
  const rawLeft=s.left,rawRight=s.right,rawPending=s.pending.map(a=>({side:a.side,value:a.value} as PairAddress<LI,RI>));
  const pending=rawPending.map(a=>{if(a.side==='left')return {side:'left' as const,value:cloneLI.call(left,a.value)};if(a.side==='right')return {side:'right' as const,value:cloneRI.call(right,a.value)};throw new ModelError('invalid pair address tag');});
  return {left:cloneLeft.call(left,rawLeft),right:cloneRight.call(right,rawRight),pending};
 }
 cloneInput(i:PairInput<LI,RI>):PairInput<LI,RI>{if(i.kind==='local')return {kind:'local',address:this.message(i.address)};if(i.kind!=='deliver')throw new ModelError('invalid composition input tag');return {...i};}
 cloneOutput(o:PairAddress<LO,RO>):PairAddress<LO,RO>{if(o.side!=='left'&&o.side!=='right')throw new ModelError('invalid pair address tag');return o.side==='left'?{side:'left',value:this.left.cloneOutput(o.value)}:{side:'right',value:this.right.cloneOutput(o.value)};}
 initialState():PairState<L,R,LI,RI>{return {left:this.left.cloneState(this.left.initialState()),right:this.right.cloneState(this.right.initialState()),pending:[]};}
 private addressed(s:PairState<L,R,LI,RI>,i:PairInput<LI,RI>){if(i.kind==='local'){if(i.address.side!=='left'&&i.address.side!=='right')throw new ModelError('invalid pair address tag');return i.address;}if(i.kind!=='deliver')throw new ModelError('invalid composition input tag');if(!Number.isInteger(i.index)||i.index<0||i.index>0xffffffff||i.index>=s.pending.length)throw new ModelError('pending message index out of range');const a=s.pending[i.index];if(a.side!=='left'&&a.side!=='right')throw new ModelError('invalid pair address tag');return a;}
 step(s:PairState<L,R,LI,RI>,i:PairInput<LI,RI>):Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>{
  if(s.pending.length>this.maxPending)throw new ModelError('pending bound exceeded; verification incomplete');
  this.addressed(s,i);
  const leftModel=this.left,rightModel=this.right,wiring=this.wiring,maxPending=this.maxPending;
  const cloneLS=leftModel.cloneState,cloneRS=rightModel.cloneState,cloneLI=leftModel.cloneInput,cloneRI=rightModel.cloneInput,cloneLO=leftModel.cloneOutput,cloneRO=rightModel.cloneOutput,leftStep=leftModel.step,rightStep=rightModel.step,messages=wiring.messages;
  const message=(m:PairAddress<LI,RI>):PairAddress<LI,RI>=>{if(m.side==='left')return {side:'left',value:cloneLI.call(leftModel,m.value)};if(m.side==='right')return {side:'right',value:cloneRI.call(rightModel,m.value)};throw new ModelError('invalid pair address tag');};
  const output=(o:PairAddress<LO,RO>):PairAddress<LO,RO>=>o.side==='left'?{side:'left',value:cloneLO.call(leftModel,o.value)}:{side:'right',value:cloneRO.call(rightModel,o.value)};
  // Preserve queue tags and payload references before any application clone hook.
  const rawPending=s.pending.map(m=>({side:m.side,value:m.value} as PairAddress<LI,RI>));
  const rawLeft=s.left,rawRight=s.right,input:PairInput<LI,RI>=i.kind==='local'?{kind:'local',address:message(i.address)}:{kind:'deliver',index:i.index};
  const pendingSnapshot=rawPending.map(message);
  const snapshot:PairState<L,R,LI,RI>={left:cloneLS.call(leftModel,rawLeft),right:cloneRS.call(rightModel,rawRight),pending:pendingSnapshot};
  const a=this.addressed(snapshot,input);let left=snapshot.left,right=snapshot.right,outputs:PairAddress<LO,RO>[];let disposition;
  const pending=[...snapshot.pending];if(input.kind==='deliver')pending.splice(input.index,1);
  if(a.side==='left'){const t=leftStep.call(leftModel,cloneLS.call(leftModel,left),cloneLI.call(leftModel,a.value));left=cloneLS.call(leftModel,t.state);outputs=t.outputs.map(value=>({side:'left',value:cloneLO.call(leftModel,value)}));disposition={...t.disposition};}
  else{const t=rightStep.call(rightModel,cloneRS.call(rightModel,right),cloneRI.call(rightModel,a.value));right=cloneRS.call(rightModel,t.state);outputs=t.outputs.map(value=>({side:'right',value:cloneRO.call(rightModel,value)}));disposition={...t.disposition};}
  for(const o of outputs)for(const m of messages.call(wiring,output(o))){if(pending.length>=maxPending)throw new ModelError('pending bound exceeded; verification incomplete');pending.push(message(m));}return {state:{left,right,pending},outputs,disposition};
 }
 inputs(s:PairState<L,R,LI,RI>):Iterable<PairInput<LI,RI>>{return this.inputsOwned(this.cloneState(s));}
 private *inputsOwned(s:PairState<L,R,LI,RI>):Iterable<PairInput<LI,RI>>{for(const value of this.left.inputs(this.left.cloneState(s.left)))yield {kind:'local',address:{side:'left',value:this.left.cloneInput(value)}};for(const value of this.right.inputs(this.right.cloneState(s.right)))yield {kind:'local',address:{side:'right',value:this.right.cloneInput(value)}};for(let index=0;index<s.pending.length;index++)yield {kind:'deliver',index};}
 checkStateInto(s:PairState<L,R,LI,RI>,sink:CheckSink){s=this.cloneState(s);ns('left',sink,c=>appendStateChecks(this.left,this.left.cloneState(s.left),c));ns('right',sink,c=>appendStateChecks(this.right,this.right.cloneState(s.right),c));ns('global',sink,c=>{if(this.wiring.checkStateInto)requireSynchronousCheckReturn(this.wiring.checkStateInto(this.cloneState(s),c));else c.extend(this.wiring.checkState(this.cloneState(s)));});}
 checkState(s:PairState<L,R,LI,RI>){const c:Check[]=[];withCheckSink(c,sink=>this.checkStateInto(s,sink));return c;}
 checkTransitionInto(s:PairState<L,R,LI,RI>,i:PairInput<LI,RI>,t:Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,sink:CheckSink){const target=this.addressed(s,i).side;i=this.cloneInput(i);s=this.cloneState(s);t={state:this.cloneState(t.state),outputs:t.outputs.map(o=>{if(o.side!==target)throw new ModelError('wrong child output address');return this.cloneOutput(o);}),disposition:{...t.disposition}};const a=this.addressed(s,i);
  if(a.side==='left'){const outputs:LO[]=[];for(const o of t.outputs){if(o.side!=='left')throw new ModelError('wrong child output address');outputs.push(this.left.cloneOutput(o.value));}ns('left',sink,c=>appendTransitionChecks(this.left,this.left.cloneState(s.left),this.left.cloneInput(a.value),{state:this.left.cloneState(t.state.left),outputs,disposition:{...t.disposition}},c));}
  else {const outputs:RO[]=[];for(const o of t.outputs){if(o.side!=='right')throw new ModelError('wrong child output address');outputs.push(this.right.cloneOutput(o.value));}ns('right',sink,c=>appendTransitionChecks(this.right,this.right.cloneState(s.right),this.right.cloneInput(a.value),{state:this.right.cloneState(t.state.right),outputs,disposition:{...t.disposition}},c));}
  if(this.wiring.checkTransitionInto||this.wiring.checkTransition)ns('global',sink,c=>{const before=this.cloneState(s),input=this.cloneInput(i),tr={state:this.cloneState(t.state),outputs:t.outputs.map(o=>this.cloneOutput(o)),disposition:{...t.disposition}};if(this.wiring.checkTransitionInto)requireSynchronousCheckReturn(this.wiring.checkTransitionInto(before,input,tr,c));else c.extend(this.wiring.checkTransition!(before,input,tr));});
 }
}

import {Metadata,CodecModel} from './core.js';
import {ValueSchema,customValueSchema,valueCodecs as vc,logicalLayout,bindModelCodec} from './value_codec.js';
export function pairSchemas<L,R,LI,RI,LO,RO>(left:ValueSchema<L>,right:ValueSchema<R>,li:ValueSchema<LI>,ri:ValueSchema<RI>,lo:ValueSchema<LO>,ro:ValueSchema<RO>){
 const address=<A,B>(a:ValueSchema<A>,b:ValueSchema<B>):ValueSchema<PairAddress<A,B>>=>{if(!a.layout||!b.layout)throw new ModelError('payload allocation layouts required');return customValueSchema({name:'PairAddress',layout:logicalLayout(a.layout.bytes+b.layout.bytes+64n,'pair-address'),encodeInto:(v,out)=>{if(v.side!=='left'&&v.side!=='right')throw new ModelError('invalid pair address tag');if(v.side==='left'){vc.u8.encodeRaw(0,out);a.encodeRaw(v.value,out);}else{vc.u8.encodeRaw(1,out);b.encodeRaw(v.value,out);}},decodeFrom:r=>{const tag=r.value(vc.u8);if(tag===0)return {side:'left',value:r.value(a)};if(tag===1)return {side:'right',value:r.value(b)};throw new ModelError('invalid pair address tag');}});};
 const msg=address(li,ri),queue=vc.vector(msg);
 const state=customValueSchema<PairState<L,R,LI,RI>>({name:'PairState',encodeInto:(s,b)=>{left.encodeRaw(s.left,b);right.encodeRaw(s.right,b);queue.encodeRaw([...s.pending],b);},decodeFrom:r=>({left:r.value(left),right:r.value(right),pending:r.value(queue)})});
 const input=customValueSchema<PairInput<LI,RI>>({name:'PairInput',encodeInto:(i,b)=>{if(i.kind!=='local'&&i.kind!=='deliver')throw new ModelError('invalid composition input tag');vc.u8.encodeRaw(i.kind==='local'?0:1,b);if(i.kind==='local')msg.encodeRaw(i.address,b);else vc.u32.encodeRaw(i.index,b);},decodeFrom:r=>{const tag=r.value(vc.u8);if(tag===0)return {kind:'local',address:r.value(msg)};if(tag===1)return {kind:'deliver',index:r.value(vc.u32)};throw new ModelError('invalid composition input tag');}});
 return {state,input,output:address(lo,ro)};
}
export function withPairCodec<L,R,LI,RI,LO,RO>(p:Pair<L,R,LI,RI,LO,RO>,schemas:ReturnType<typeof pairSchemas<L,R,LI,RI,LO,RO>>,wiringMetadata:()=>Metadata){
 const metadata=():Metadata=>{const l=p.left as unknown as Partial<CodecModel<L,LI,LO>>,r=p.right as unknown as Partial<CodecModel<R,RI,RO>>;if(typeof l.metadata!=='function'||typeof r.metadata!=='function')throw new ModelError('pair child metadata required');const ms=[l.metadata(),r.metadata(),wiringMetadata()],len=(s:string)=>new TextEncoder().encode(s).length,identities=ms.map(m=>`${len(m.name)}:${m.name}:${m.model_version}/${m.properties_version}/${m.codec_version}`);return {name:`pair-v1:${identities.map(s=>`${len(s)}:${s}`).join('')};pending=${p.maxPending}`,model_version:1,properties_version:1,codec_version:1,build:ms.map(m=>`${len(m.build)}:${m.build}`).join('')+';native'};};
 return bindModelCodec(Object.assign(p,{metadata}),schemas);
}
