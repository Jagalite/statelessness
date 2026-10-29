import fs from 'node:fs';
import {Network,withNetworkCodec,valueCodecs as vc,record,replay,stringifyTrace,parseTrace} from '@jagalite/statelessness';
const metadata=name=>({name,build:'fixture-v1',model_version:1,properties_version:1,codec_version:1});
const child={metadata:()=>metadata('ring-child'),initialState:()=>0,step:(s,i)=>({state:s+i,outputs:[i],disposition:{kind:'accepted',reason:''}}),checkState:()=>[{id:'safe',status:'passed',details:''}],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:()=>[1],equalStates:(a,b)=>a===b};
const wiring={metadata:()=>metadata('ring-global'),routes:o=>[{destination:(o.machine+1)%3,channel:7,input:o.value}],checkState:s=>[s.machines.reduce((n,a)=>n+a.value,0)>2?{id:'total',status:'failed',details:'total exceeds two'}:{id:'total',status:'passed',details:''}]};
const m=withNetworkCodec(new Network([[0,child],[1,child],[2,child]],wiring),vc.u8,vc.u8,vc.u8),[mode,path]=process.argv.slice(2);
if(mode==='record'){fs.writeFileSync(path,stringifyTrace(record(m,[{kind:'local',address:{machine:0,value:1}},{kind:'deliver',index:0},{kind:'deliver',index:0}])));console.log('recorded');}
else{const r=replay(m,parseTrace(fs.readFileSync(path)));if(r.outcome!=='exact'||!r.failureReproduced)throw Error(JSON.stringify(r));console.log('exact failure reproduced');}
