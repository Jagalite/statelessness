import test from 'node:test';
import assert from 'node:assert/strict';
import {DebugSession} from '../dist/debug_session.js';
import {ProtocolSession,encodeRequest,decodeResponse} from '../dist/debug_protocol.js';
import {projection,inspect,DEFAULT_INSPECT_LIMITS} from '../dist/debug_inspect.js';
import {metricCatalog} from '../dist/debug_metrics.js';
import {accepted} from '../dist/index.js';
test('real inspection and metric catalog execute through typed host protocol bindings',()=>{
 const m={initialState:()=>9007199254740993n,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,checkState:()=>[],step:(s,i)=>accepted(s+i,[])};
 const s=new DebugSession('host-binding',m,{kind:'unrestricted',label:'out-of-domain-injection'},{maxTurns:3n,maxContinue:3,maxCandidates:1,maxBreakpoints:0});
 const p=new ProtocolSession(s,()=>1n,{readExactValues:true},{},{inspect:(args,ctx)=>{ctx.requireHandle(BigInt(args[0].slice(4)));const value=projection({name:'counter',version:1},(path,cx)=>cx.scalar(path,{kind:'integer',type:'u64',decimal:ctx.session.state.toString()}));const result=inspect(value,{snapshot:{session:ctx.session.epoch,revision:ctx.session.revision,sequence:ctx.session.sequence},path:[],schemaVersion:Number(args[1].slice(4)),page:{offset:Number(args[2].slice(4)),limit:Number(args[3].slice(4))},limits:DEFAULT_INSPECT_LIMITS});return {fields:[['schema.name',result.schema.name],['value.type',result.node.kind.value.type],['value.decimal',result.node.kind.value.decimal]]};},metric_catalog:args=>{const offset=Number(args[0].slice(4)),limit=Number(args[1].slice(4)),catalog=metricCatalog();return {fields:[['total',`u64:${catalog.length}`],...catalog.slice(offset,offset+limit).map((m,i)=>[`metric.${offset+i}.name`,m.name])]};}});
 let requestId=0n;const request=command=>({version:1,session:s.id,epoch:p.epoch,requestId:++requestId,revision:s.revision,configuration:0n,command});
 const snap=p.handle(request({kind:'snapshot'}));const handle=snap.fields.find(([name])=>name==='handle')[1];
 const r=p.handle(request({kind:'host',name:'inspect',arguments:[handle,'u64:1','u64:0','u64:1']}));assert.equal(r.error,null);assert.equal(r.fields.find(([name])=>name==='value.decimal')[1],'9007199254740993');
 const catalog=decodeResponse(p.handlePayload(encodeRequest(request({kind:'host',name:'metric_catalog',arguments:['u64:0','u64:2']}))));assert.equal(catalog.error,null);assert.equal(catalog.fields.find(([name])=>name==='metric.0.name')[1],'effect_requests_total');assert.equal(s.revision,0n);
});
