import fs from 'node:fs';
import {Network,withNetworkCodec,valueCodecs as vc,record,replay,stringifyTrace,parseTrace,recordAudit,writeAuditTrace} from '@jagalite/statelessness';
const metadata=name=>({name,build:'fixture-v1',model_version:1,properties_version:1,codec_version:1});
const child={metadata:()=>metadata('ring-child'),initialState:()=>0,step:(s,i)=>({state:s+i,outputs:[i],disposition:{kind:'accepted',reason:''}}),checkState:()=>[{id:'safe',status:'passed',details:''}],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,inputs:()=>[1],equalStates:(a,b)=>a===b};
const model=weak=>withNetworkCodec(new Network([[0,child],[1,child],[2,child]],{metadata:()=>metadata('ring-global'),routes:o=>[{destination:(o.machine+1)%3,channel:7,input:o.value}],checkState:s=>[s.machines.reduce((n,a)=>n+a.value,0)>(weak?3:2)?{id:'total',status:'failed',details:'total exceeds two'}:{id:'total',status:'passed',details:''}]}),vc.u8,vc.u8,vc.u8);
const inputs=()=>[{kind:'local',address:{machine:0,value:1}},{kind:'deliver',index:0},{kind:'deliver',index:0}];
// An independent requirement for this fixed schedule, checked on both recording
// and replay. The weak control must complete normally with the same final state.
function expected(m,t,weak){
 if(t.steps.length!==3||t.termination!==(weak?'completed':'property_failed'))return false;
 const last=t.steps[2],s=m.decodeState(last.postState);
 return s.machines.length===3&&s.machines.every((a,i)=>a.machine===i&&a.value===1)&&s.pending.length===1&&
   last.checks.some(c=>c.id==='global::total'&&c.status===(weak?'passed':'failed')&&c.details===(weak?'':'total exceeds two'));
}
const m=model(false),[mode,path]=process.argv.slice(2);
if(mode==='record-audit'){const t=recordAudit(m,inputs(),{strategy:'stateful-fixture',seed:null,parameters:[]});if(!expected(m,t,false))throw Error('independent expected failure not observed');fs.writeFileSync(path,writeAuditTrace(t));console.log('audit recorded');}
else if(mode==='record'){const t=record(m,inputs());if(!expected(m,t,false))throw Error('independent expected failure not observed');fs.writeFileSync(path,stringifyTrace(t));console.log('recorded');}
else if(mode==='control'){const weak=model(true),t=record(weak,inputs());if(!expected(weak,t,true)||expected(weak,t,false))throw Error('weakened-property control did not produce expected missed failure');console.log('network-control-detected');}
else if(mode==='compare-cross-build'){const t=parseTrace(fs.readFileSync(path)), own=m.metadata();if(!expected(m,t,false))throw Error('trace does not contain independent expected failure'); if(t.metadata.build!==process.env.STATEFUL_PRODUCER_BUILD||own.build!==process.env.STATEFUL_CONSUMER_BUILD||t.metadata.build===own.build)throw Error('build identity mismatch'); const strict=replay(m,t); if(strict.outcome!=='incompatible'||strict.build_matches||strict.steps_verified!==0||strict.failure_reproduced)throw Error(JSON.stringify(strict)); const r=replay(m,t,true);if(r.outcome!=='exact'||r.build_matches||r.steps_verified!==3||!r.failure_reproduced)throw Error(JSON.stringify(r));console.log('STATEFUL_COMPARISON '+JSON.stringify({strict_outcome:strict.outcome,strict_build_matches:strict.build_matches,strict_steps_verified:strict.steps_verified,strict_failure_reproduced:strict.failure_reproduced,observation_outcome:r.outcome,observation_build_matches:r.build_matches,observation_steps_verified:r.steps_verified,observation_failure_reproduced:r.failure_reproduced}));}
else if(mode==='replay'){const t=parseTrace(fs.readFileSync(path));if(!expected(m,t,false))throw Error('trace does not contain independent expected failure');const r=replay(m,t);if(r.outcome!=='exact'||r.steps_verified!==3||!r.failure_reproduced)throw Error(JSON.stringify(r));console.log('exact failure reproduced');}else throw Error('unknown mode');
