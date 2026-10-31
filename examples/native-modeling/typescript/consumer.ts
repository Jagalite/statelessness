import {NO_INPUT,Domain,DomainDescriptor,Rng,checkLazy,declaredProperties,adaptModel,metadata,accepted,disposition,automatic,bindModelCodec,valueCodecs,record,replay} from '@jagalite/statelessness';
function require(v:unknown){if(!v)throw Error('assertion failed');}
function rejects(f:()=>unknown,text:string){try{f();}catch(e){require(String(e).includes(text));return;}throw Error('expected '+text);}
const d=new Domain(new DomainDescriptor('actions','duplicates carry weight',4),(n:number)=>n);
d.extend([3,1,3,7]);rejects(()=>d.push(9),'limit');const a=new Rng(42n),b=new Rng(42n);
for(let i=0;i<100;i++)require(d.sampleIndexed(a)===d.entries[Number(b.index(4n))]);
require(JSON.stringify(Domain.product([1,2],[4,5],4,(x,y)=>x+y))==='[5,6,6,7]');
rejects(()=>Domain.product([1,2],[4,5],3,()=>{throw Error('map called');}),'limit');
const empty=new Domain(new DomainDescriptor('empty','',0),(x:number)=>x);const r=new Rng(1n),r2=r.clone();require(empty.sampleIndexed(r)===NO_INPUT&&r.nextU64()===r2.nextU64());
const owned=new Domain(new DomainDescriptor('owned','',2),(v:number[])=>[...v]);const value=[1];owned.push(value);value.push(2);owned.entries[0].push(3);require(owned.entries[0].length===1);
require(checkLazy('lazy',true,()=>{throw Error('eager');}).status==='passed');require(checkLazy('lazy',false,()=> 'detail').details==='detail');
rejects(()=>checkLazy('lazy',false,()=>{throw Error('details error');}),'details error');
rejects(()=>declaredProperties([{id:'p',phase:'state'},{id:'p',phase:'transition'}]),'duplicate');
rejects(()=>new DomainDescriptor('bad','',-1),'maxEntries');
const nullable=new Domain(new DomainDescriptor('null','',1),(v:undefined)=>v);nullable.push(undefined);require(nullable.sampleIndexed(new Rng())===undefined);
const right=[4,5];require(JSON.stringify(Domain.product([1,2],right,4,(x,y)=>{right.push(6);return x+y;}))==='[5,6,6,7]');
let reentrant=false;const nested:Domain<number>=new Domain(new DomainDescriptor('nested','',1),(v:number)=>{if(!reentrant){reentrant=true;nested.push(1);}return v;});rejects(()=>nested.push(2),'limit');require(nested.entries.length===1);
rejects(()=>Reflect.set(d,'descriptor',new DomainDescriptor('bad','',99))||(()=>{throw Error('readonly');})(),'readonly');
class Application {
 #marker=1;
 toString(){return 'application';}
 metadata(){require(this.#marker===1);return metadata('native-modeling-application','teaching-v1');}
 step(state:number,event:number){require(this.#marker===1);if(event===1)return accepted(1,[1]);if(event===3)return accepted(0,[3]);if(event===2&&state===1)return accepted(2,[2]);return {state,outputs:[],disposition:disposition('rejected','not busy')};}
 checkState(state:number){return [checkLazy('state.range',state>=0&&state<=2,()=>`invalid ${state}`)];}
 inputs(){return [1,2,3];}equalInputs(a:number,b:number){return a===b;}equalStates(a:number,b:number){return a===b;}
 cloneState(v:number){return v;}cloneInput(v:number){return v;}cloneOutput(v:number){return v;}
}
const raw=new Application();rejects(()=>adaptModel(raw,{properties:[]}), 'no properties');
const adapted=adaptModel<number,number,number,Application>(raw,{properties:[{id:'state.range',phase:'state'}],initialDefault:()=>0});
require(adapted.toString()==='application');
const generated=automatic(adapted,{maxCandidates:2});const rng=new Rng(42n),copy=rng.clone();rejects(()=>generated.generate(0,rng),'max_candidates');require(rng.nextU64()===copy.nextU64());
const m=bindModelCodec(adapted,{state:valueCodecs.u8,input:valueCodecs.u8,output:valueCodecs.u8});
require(m.encodeState(2)[0]===2&&m.decodeState(Uint8Array.of(2))===2);rejects(()=>m.decodeState(Uint8Array.of(2,0)),'trailing');
for(const events of [[1,3,1,2],[1,2]]){let state=m.initialState();const observed=[];for(const event of events){const turn=m.step(state,event);state=turn.state;observed.push(state);}const trace=record(m,events);require(replay(m,trace).outcome==='exact');require(JSON.stringify(trace.steps.map(s=>m.decodeState(s.postState)))===JSON.stringify(observed));}
const start=m.step(m.initialState(),1);require(start.outputs[0]===1);
const completion=await Promise.resolve(2);const finish=m.step(start.state,completion);require(finish.state===2&&replay(m,record(m,[1,completion])).outcome==='exact');
console.log(JSON.stringify({engine:'typescript',controls:'passed',hosts:['ui','background','async'],turns:8}));
