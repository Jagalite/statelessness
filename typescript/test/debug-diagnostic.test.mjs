import test from 'node:test';
import assert from 'node:assert/strict';
import {DiagnosticHub,DEFAULT_DIAGNOSTIC_LIMITS} from '../dist/debug_diagnostic.js';
import {projection} from '../dist/debug_inspect.js';
const value=n=>projection({name:'manual',version:1},(p,c)=>c.scalar(p,{kind:'integer',type:'u128',decimal:String(n)}));
test('diagnostic authorization matches typed path identity independent of object key order',()=>{
 const h=new DiagnosticHub(),allowed=[{kind:'field',id:'items'},{kind:'map_key',key:{kind:'string',value:'x'}}];
 const sink=h.addSink({sites:['queue'],paths:[allowed]},{records:2,bytes:65536,policy:'newest'});
 const base={sink,site:'queue',kind:'probe',trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'debug'};
 const candidate=[{id:'items',kind:'field'},{key:{value:'x',kind:'string'},kind:'map_key'}];
 assert.throws(()=>h.configure(0n,[{...base,path:[candidate[0],{kind:'map_key',key:{kind:'char',value:'x'}}]}]),/unauthorized/);
 assert.doesNotThrow(()=>h.configure(0n,[{...base,path:candidate}]));
});
const subscription=(sink,extra={})=>({sink,site:'queue',kind:'probe',path:[],trigger:{kind:'every'},sampleEvery:1n,minimumSeverity:'debug',...extra});
function hub(limits=DEFAULT_DIAGNOSTIC_LIMITS,sinkLimits={records:10,bytes:65536,policy:'newest'}){const h=new DiagnosticHub(limits);const sink=h.addSink({sites:['queue','mark'],paths:[[]]},sinkLimits);h.registerProducer(9007199254740993n,18446744073709551615n);return {h,sink,p:9007199254740993n};}
test('diagnostic scalar equality uses typed fields rather than object layout',()=>{
 const {h,sink,p}=hub();
 h.configure(0n,[subscription(sink,{trigger:{kind:'equals',value:{decimal:'not numeric',type:'u8',kind:'integer',extra:'ignored'}}})]);h.acknowledge(p,1n,0n);
 const proj=(decimal,type='u8')=>projection({name:'manual',version:1},(path,c)=>c.scalar(path,{kind:'integer',type,decimal}));
 h.withTurn(p,1n,0n,'test',s=>s.probe('queue',()=>proj('not numeric')));assert.equal(h.queued(sink).records,1);h.pop(sink);
 h.withTurn(p,1n,1n,'test',s=>s.probe('queue',()=>proj('not numeric','u16')));assert.equal(h.queued(sink).records,0);
});
test('probe no-interest path avoids thunk/schema/projection and explicit context selectors AND',()=>{const {h,sink,p}=hub();let calls=0;h.withTurn(p,1n,0n,'test',scope=>{assert.equal(scope.interested('queue'),false);scope.probe('queue',()=>{calls++;return value(1);});});const ack=h.configure(0n,[subscription(sink,{selector:{model:'counter',propertyFailed:true}})]);assert.deepEqual(ack.pendingProducers,[p]);h.acknowledge(p,1n,1n);h.withTurn(p,1n,1n,'test',scope=>scope.probe('queue',()=>{calls++;return value(1);}),{model:'counter',propertyFailed:false});assert.equal(calls,0);h.withTurn(p,1n,2n,'test',scope=>scope.probe('queue',()=>{calls++;return value(1);}),{model:'counter',propertyFailed:true});assert.equal(calls,1);assert.equal(h.queued(sink).records,1);assert.equal(h.peek(sink).epoch,18446744073709551615n);});
test('turn context is owned and invalid runtime context does not advance the producer',()=>{
 const {h,sink,p}=hub();h.configure(0n,[subscription(sink,{selector:{model:'counter',propertyFailed:true}})]);h.acknowledge(p,1n,0n);
 for(const [origin,context] of [['invalid',{}],['test',{model:'é'.repeat(129)}],['test',{propertyFailed:'yes'}],['test',null]])assert.throws(()=>h.beginTurn(p,1n,0n,origin,context));
 const context={model:'counter',propertyFailed:true};let calls=0;
 const scope=h.beginTurn(p,1n,0n,'test',context);context.model='different';context.propertyFailed=false;
 assert.equal(scope.interested('queue'),true);scope.probe('queue',()=>{calls++;context.model='other';return value(1);});scope.finish(true);
 assert.equal(calls,1);assert.equal(h.queued(sink).records,1);assert.equal(h.pop(sink).baselineReason,'initial');
 h.withTurn(p,1n,1n,'test',s=>s.probe('queue',()=>value(2)),{model:'counter',propertyFailed:true});
 assert.equal(h.pop(sink).baselineReason,null);
});
test('selector admission rejects invalid runtime fields atomically and keeps owned validated values',()=>{
 const {h,sink,p}=hub();
 const bad=[null,[],{producer:-1n},{producer:1},{producer:1n<<64n},{origin:'unknown'},{propertyFailed:1},{model:42},{model:'é'.repeat(129)},{unknown:'x'}];
 for(const selector of bad){assert.throws(()=>h.configure(0n,[subscription(sink,{selector})]));assert.equal(h.revision,0n);assert.deepEqual(h.producerStatus()[0].captureRevision,0n);}
 let reads=0;const changing={get model(){return ++reads<3?'valid':42;}};
 assert.throws(()=>h.configure(0n,[subscription(sink,{selector:changing})]));assert.equal(h.revision,0n);
 const selector={producer:p,origin:'test',model:'é'.repeat(128),propertyFailed:true};
 h.configure(0n,[subscription(sink,{selector})]);selector.model='changed';h.acknowledge(p,1n,0n);
 h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>value(1)),{model:'é'.repeat(128),propertyFailed:true});
 assert.equal(h.queued(sink).records,1);
});
test('diagnostic site limits count UTF-8 bytes at configuration and capture',()=>{
 const h=new DiagnosticHub();const sink=h.addSink({sites:['*'],paths:[[]]},{records:10,bytes:65536,policy:'newest'});h.registerProducer(1n,1n);
 const fits='é'.repeat(128),tooLong='é'.repeat(129);
 assert.throws(()=>h.configure(0n,[subscription(sink,{site:tooLong})]),/invalid subscription/);assert.equal(h.revision,0n);
 assert.throws(()=>h.configure(0n,[subscription(sink,{site:123})]),/invalid subscription/);assert.equal(h.revision,0n);
 let reads=0;const changing={...subscription(sink),get site(){return ++reads<3?'valid':tooLong;}};
 assert.throws(()=>h.configure(0n,[changing]),/invalid subscription/);assert.equal(h.revision,0n);
 h.configure(0n,[subscription(sink,{site:fits})]);h.acknowledge(1n,1n,0n);let calls=0;
 h.withTurn(1n,1n,0n,'test',scope=>{assert.throws(()=>scope.probe(tooLong,()=>{calls++;return value(1);}),/invalid diagnostic site/);assert.throws(()=>scope.marker(tooLong),/invalid diagnostic site/);scope.probe(fits,()=>{calls++;return value(2);});});
 assert.equal(calls,1);const event=h.pop(sink);assert.equal(event.site,fits);assert.equal(event.producerSequence,1n);
});
test('sink permission admission bounds UTF-8 bytes and rejects invalid capacities atomically',()=>{
 const h=new DiagnosticHub({...DEFAULT_DIAGNOSTIC_LIMITS,configBytes:92,subscriptions:1});
 const limits={records:1,bytes:4096,policy:'newest'};
 for(const permissions of [
  {sites:['ééé'],paths:[]},
  {sites:['a','b'],paths:[]},
  {sites:[],paths:[Array.from({length:33},()=>({kind:'field',id:'x'}))]},
  {sites:Array(1),paths:[]},
 ])assert.throws(()=>h.addSink(permissions,limits));
 assert.throws(()=>h.addSink({sites:[],paths:[]},{...limits,records:0}));
 assert.throws(()=>h.addSink({sites:[],paths:[]},{...limits,bytes:0}));
 let reads=0;const sites=[];Object.defineProperty(sites,0,{enumerable:true,get(){return ++reads===1?'a':'ééé';}});sites.length=1;
 assert.throws(()=>h.addSink({sites,paths:[]},limits),/permission byte limit/);
 const permissions={sites:['éé'],paths:[]};const id=h.addSink(permissions,limits);assert.equal(id,1n);
 permissions.sites[0]='mutated';assert.deepEqual(h.queued(id),{records:0,bytes:0});
});
test('host getters cannot reenter diagnostic admission or bypass owned authorization',()=>{
 const h=new DiagnosticHub();let sinkReads=0;
 const limits={get records(){sinkReads++;assert.throws(()=>h.addSink({sites:[],paths:[]},{records:1,bytes:1,policy:'newest'}),/owns/);return 2;},bytes:4096,policy:'newest'};
 const sink=h.addSink({sites:['queue'],paths:[[]]},limits);assert.equal(sink,1n);assert.equal(sinkReads,1);
 h.registerProducer(1n,1n);let reads=0;
 const changing={...subscription(sink),get site(){assert.throws(()=>h.configure(0n,[]),/owns/);return ++reads<3?'queue':'secret';}};
 assert.throws(()=>h.configure(0n,[changing]),/unauthorized/);assert.equal(h.revision,0n);
 h.configure(0n,[subscription(sink,{selector:{model:'counter'}})]);h.acknowledge(1n,1n,0n);
 const bad={get model(){assert.throws(()=>h.beginTurn(1n,1n,0n,'test'),/owns/);throw Error('context failed');}};
 assert.throws(()=>h.beginTurn(1n,1n,0n,'test',bad),/context failed/);
 h.withTurn(1n,1n,0n,'test',scope=>scope.probe('queue',()=>value(1)),{model:'counter'});
 assert.equal(h.queued(sink).records,1);
});
test('diagnostic triggers validate runtime variants and equality scalar payloads atomically',()=>{
 const {h,sink,p}=hub();
 const invalid=[null,[],{}, {kind:'unknown'}, {kind:'equals'}, {kind:'equals',value:{kind:'bool',value:1}}, {kind:'equals',value:{kind:'char',value:'ab'}}, {kind:'equals',value:{kind:'char',value:'\ud800'}}, {kind:'equals',value:{kind:'integer',type:'u7',decimal:'1'}}, {kind:'equals',value:{kind:'integer',type:'u64',decimal:3}}, {kind:'equals',value:{kind:'float',type:'f64',decimal:'1',bits:1}}, {kind:'above_u128',threshold:-1n,hysteresis:0n,cooldownTurns:0n}, {kind:'above_u128',threshold:0n,hysteresis:0n,cooldownTurns:1n<<64n}];
 for(const trigger of invalid){assert.throws(()=>h.configure(0n,[subscription(sink,{trigger})]));assert.equal(h.revision,0n);}
 let reads=0;const changing={get kind(){return ++reads<3?'every':'unknown';}};
 assert.throws(()=>h.configure(0n,[subscription(sink,{trigger:changing})]));assert.equal(h.revision,0n);
 h.configure(0n,[subscription(sink,{trigger:{kind:'equals',value:{kind:'integer',type:'u128',decimal:'7'}}})]);h.acknowledge(p,1n,0n);
 h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>value(7)));assert.equal(h.queued(sink).records,1);
});
test('diagnostic paths reject malformed host segments before installing subscriptions',()=>{
 const {h,sink,p}=hub();
 const bad=[null,{},Array(1),[{kind:'unknown'}],[{kind:'field',id:3}],[{kind:'field',id:'é'.repeat(129)}],[{kind:'variant',id:'\ud800'}],[{kind:'index',index:-1}],[{kind:'index',index:1.5}],[{kind:'map_key',key:{kind:'bool',value:1}}],[{kind:'map_key',key:{kind:'char',value:'ab'}}],[{kind:'map_key',key:{kind:'integer',type:'i7',decimal:'1'}}]];
 for(const path of bad){assert.throws(()=>h.configure(0n,[subscription(sink,{path})]));assert.equal(h.revision,0n);}
 const badPermissions=[{kind:'map_key',key:{kind:'integer',type:'u7',decimal:'256'}}];assert.throws(()=>h.addSink({sites:['queue'],paths:[badPermissions]},{records:1,bytes:4096,policy:'newest'}));
 const path=[{kind:'map_key',key:{kind:'integer',type:'u8',decimal:'not-a-number'}}];h.configure(0n,[subscription(sink,{path})]);path[0].key.decimal='0';h.acknowledge(p,1n,0n);
 h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>{throw Error('unavailable');}));
 const event=h.pop(sink);assert.equal(event.path[0].key.decimal,'not-a-number');assert.equal(event.changeUnknown,true);
});
test('revision and producer effective acknowledgement guard capture boundaries',()=>{const {h,sink,p}=hub();h.configure(0n,[subscription(sink)]);assert.throws(()=>h.configure(0n,[]),/stale/);assert.throws(()=>h.acknowledge(p,0n,0n),/stale/);h.acknowledge(p,1n,5n);assert.throws(()=>h.beginTurn(p,1n,4n,'test'),/pending/);h.withTurn(p,1n,5n,'test',scope=>scope.probe('queue',()=>value(1)));assert.throws(()=>h.beginTurn(p,1n,5n,'test'),/stale/);h.configure(1n,[subscription(sink)]);assert.equal(h.pendingProducers().length,1);h.acknowledge(p,2n,6n);assert.equal(h.pendingProducers().length,0);});
test('changed baseline gap source schema and incomplete scope are explicit',()=>{const {h,sink,p}=hub();h.configure(0n,[subscription(sink,{trigger:{kind:'changed'}})]);h.acknowledge(p,1n,0n);h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>value(1)));assert.equal(h.pop(sink).baselineReason,'initial');h.withTurn(p,1n,1n,'test',scope=>scope.probe('queue',()=>value(1)));assert.equal(h.queued(sink).records,0);assert.throws(()=>h.withTurn(p,1n,3n,'test',scope=>{scope.probe('queue',()=>value(2));throw Error('early');}),/early/);const event=h.pop(sink);assert.equal(event.incompleteTurn,true);assert.equal(event.baselineReason,'observation_gap');assert.equal(h.health(sink).observationGaps,1n);h.withTurn(p,2n,4n,'test',scope=>scope.probe('queue',()=>value(2)));assert.equal(h.pop(sink).baselineReason,'source_changed');h.withTurn(p,2n,5n,'test',scope=>scope.probe('queue',()=>projection({name:'other',version:1},(p,c)=>c.scalar(p,{kind:'bool',value:true}))));assert.equal(h.pop(sink).baselineReason,'schema_changed');});
test('threshold hysteresis cooldown and u128 width do not use Number',()=>{const {h,sink,p}=hub();const threshold=1n<<100n;h.configure(0n,[subscription(sink,{trigger:{kind:'above_u128',threshold,hysteresis:2n,cooldownTurns:3n}})]);h.acknowledge(p,1n,0n);for(const [i,n]of [threshold+1n,threshold+2n,threshold-2n,threshold+1n].entries())h.withTurn(p,1n,BigInt(i),'test',scope=>scope.probe('queue',()=>value(n)));assert.equal(h.queued(sink).records,2);assert.equal(h.pop(sink).payload.kind.value.decimal,String(threshold+1n));assert.equal(h.pop(sink).transition,3n);});
test('sink count byte drops exporter failure and abandon are independent',()=>{const {h,sink,p}=hub(DEFAULT_DIAGNOSTIC_LIMITS,{records:1,bytes:65536,policy:'newest'});const other=h.addSink({sites:['queue'],paths:[[]]},{records:10,bytes:65536,policy:'oldest'});h.configure(0n,[subscription(sink),subscription(other)]);h.acknowledge(p,1n,0n);for(let n=0;n<3;n++)h.withTurn(p,1n,BigInt(n),'test',scope=>scope.probe('queue',()=>value(n)));assert.equal(h.queued(sink).records,1);assert.equal(h.queued(other).records,3);assert.equal(h.health(sink).dropped,2n);assert.equal(h.drainTo(sink,()=>{throw Error('exporter');},1),0);assert.equal(h.queued(sink).records,0);assert.equal(h.health(sink).exporterFailed,1n);assert.equal(h.abandon(other),3);assert.equal(h.health(other).abandoned,3n);});
test('scope ownership denies configuration/reentry and sink permission rejects before capture',()=>{const {h,sink,p}=hub();assert.throws(()=>h.configure(0n,[subscription(sink,{site:'secret'})]),/unauthorized/);h.configure(0n,[subscription(sink)]);h.acknowledge(p,1n,0n);h.withTurn(p,1n,0n,'test',scope=>{assert.throws(()=>h.configure(1n,[]),/owns/);assert.throws(()=>h.beginTurn(p,1n,1n,'test'),/owns/);scope.probe('queue',()=>value(1));});assert.equal(h.peek(sink).incompleteTurn,false);});
test('selected thunk executes once across different sink paths even when it throws',()=>{const {h,sink,p}=hub();const other=h.addSink({sites:['queue'],paths:[[]]},{records:10,bytes:65536,policy:'newest'});h.configure(0n,[subscription(sink),subscription(other,{path:[{kind:'field',id:'other'}]})]);h.acknowledge(p,1n,0n);let calls=0;h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>{calls++;throw Error('projection failed');}));assert.equal(calls,1);assert.equal(h.health(sink).inspectionFailed,1n);assert.equal(h.health(other).inspectionFailed,1n);});
test('complete schema reset stays change-unknown and above-u128 overflow is unavailable',()=>{const {h,sink,p}=hub();h.configure(0n,[subscription(sink)]);h.acknowledge(p,1n,0n);h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>value(1)));h.pop(sink);h.withTurn(p,1n,1n,'test',scope=>scope.probe('queue',()=>projection({name:'new-schema',version:1},(p,c)=>c.scalar(p,{kind:'integer',type:'u128',decimal:'2'}))));assert.equal(h.pop(sink).changeUnknown,true);
 h.configure(1n,[subscription(sink,{trigger:{kind:'above_u128',threshold:0n,hysteresis:0n,cooldownTurns:0n}})]);h.acknowledge(p,2n,2n);const overflow={schema:()=>({name:'raw',version:1}),inspect:()=>({kind:{kind:'scalar',value:{kind:'integer',type:'u128',decimal:String(1n<<128n)}},children:[],childCount:0,page:null,completeness:{kind:'complete'}})};h.withTurn(p,1n,2n,'test',scope=>scope.probe('queue',()=>overflow));assert.equal(h.queued(sink).records,0);});
test('cooldown-suppressed crossover does not consume future crossing alert',()=>{const {h,sink,p}=hub();h.configure(0n,[subscription(sink,{trigger:{kind:'above_u128',threshold:10n,hysteresis:1n,cooldownTurns:3n}})]);h.acknowledge(p,1n,0n);for(const [n,v]of [11,9,11,11].entries())h.withTurn(p,1n,BigInt(n),'test',scope=>scope.probe('queue',()=>value(v)));assert.equal(h.queued(sink).records,2);assert.equal(h.pop(sink).transition,0n);assert.equal(h.pop(sink).transition,3n);});

test('probe delivery sampling follows trigger evaluation and preserves baseline updates',()=>{const {h,sink,p}=hub();h.configure(0n,[subscription(sink,{trigger:{kind:'changed'},sampleEvery:2n})]);h.acknowledge(p,1n,0n);let evaluated=0;for(const [n,v]of [1,2,2,3].entries())h.withTurn(p,1n,BigInt(n),'test',scope=>scope.probe('queue',()=>{evaluated++;return value(v);}));assert.equal(evaluated,4);assert.equal(h.queued(sink).records,2);assert.equal(h.pop(sink).payload.kind.value.decimal,'1');assert.equal(h.pop(sink).payload.kind.value.decimal,'3');assert.equal(h.health(sink).sampled,1n);});

test('producer sequence counts selected sites rather than turns and nested capture is denied',()=>{const {h,sink,p}=hub();h.withTurn(p,1n,0n,'test',()=>{});h.configure(0n,[subscription(sink)]);h.acknowledge(p,1n,1n);h.withTurn(p,1n,1n,'test',scope=>{scope.probe('queue',()=>{assert.throws(()=>scope.probe('queue',()=>value(99)),/already evaluating/);assert.throws(()=>scope.finish(true),/already evaluating/);return value(1);});scope.probe('queue',()=>value(2));});assert.equal(h.pop(sink).producerSequence,1n);assert.equal(h.pop(sink).producerSequence,2n);});


test('bounded diagnostic preview owns events and does not drain them',()=>{
 const {h,sink,p}=hub();h.configure(0n,[subscription(sink)]);h.acknowledge(p,1n,0n);
 h.withTurn(p,1n,0n,'test',scope=>scope.probe('queue',()=>value(1)));
 h.withTurn(p,1n,1n,'test',scope=>scope.probe('queue',()=>value(2)));
 const events=h.peekMany(sink,1);assert.equal(events.length,1);assert.equal(h.queued(sink).records,2);
 events[0].site='altered';assert.equal(h.peek(sink).site,'queue');
 assert.equal(h.peekMany(sink,0).length,0);assert.throws(()=>h.peekMany(sink,-1));
 h.withTurn(p,1n,2n,'test',()=>assert.throws(()=>h.peekMany(sink,1),/owns/));
 assert.equal(h.queued(sink).records,2);
});

test('projection errors emit unknown evidence only for eligible triggers and respect sampling',()=>{
 const {h,sink,p}=hub();const other=h.addSink({sites:['queue'],paths:[[]]},{records:10,bytes:65536,policy:'newest'});
 h.configure(0n,[subscription(sink,{trigger:{kind:'changed'},sampleEvery:2n}),subscription(other,{trigger:{kind:'equals',value:{kind:'integer',type:'u128',decimal:'1'}}})]);h.acknowledge(p,1n,0n);
 for(let n=0;n<2;n++)h.withTurn(p,1n,BigInt(n),'test',scope=>scope.probe('queue',()=>{throw Error('unavailable');}));
 const e=h.pop(sink);assert.equal(e.payload,null);assert.equal(e.changeUnknown,true);assert.equal(e.baselineReason,'initial');assert.equal(h.queued(sink).records,0);
 assert.equal(h.health(sink).inspectionFailed,2n);assert.equal(h.health(sink).sampled,1n);assert.equal(h.health(sink).truncated,1n);
 assert.equal(h.queued(other).records,0);assert.equal(h.health(other).filtered,2n);assert.equal(h.health(other).inspectionFailed,2n);
 h.withTurn(p,1n,2n,'test',scope=>scope.probe('queue',()=>value(1)));const recovery=h.pop(sink);assert.equal(recovery.baselineReason,'inspection_incomplete');assert.equal(recovery.changeUnknown,true);
});

test('diagnostic probes use declared schema versions and preserve schema on projection failure',()=>{
 const {h,sink,p}=hub();h.configure(0n,[subscription(sink,{trigger:{kind:'changed'}})]);h.acknowledge(p,1n,0n);
 let schemas=0;
 const adapter={schema:()=>{schemas++;return {name:'versioned',version:7};},inspect:(path,c)=>c.scalar(path,{kind:'integer',type:'u128',decimal:'9'})};
 h.withTurn(p,9n,0n,'test',scope=>scope.probe('queue',()=>adapter));
 let event=h.pop(sink);assert.deepEqual(event.displaySchema,['versioned',7]);assert.equal(event.payload.kind.value.decimal,'9');assert.equal(schemas,1);
 const failed={schema:()=>({name:'versioned',version:8}),inspect:()=>{throw Error('projection failed');}};
 h.withTurn(p,9n,1n,'test',scope=>scope.probe('queue',()=>failed));
 event=h.pop(sink);assert.deepEqual(event.displaySchema,['versioned',8]);assert.equal(event.payload,null);assert.equal(event.baseline,true);assert.equal(event.baselineReason,'schema_changed');assert.equal(event.changeUnknown,true);
 h.withTurn(p,9n,2n,'test',scope=>scope.probe('queue',()=>adapter));
 event=h.pop(sink);assert.equal(event.baselineReason,'schema_changed');assert.equal(event.changeUnknown,true);
});

test('markers obey payload triggers and captured health counts sink admission',()=>{
 const {h,sink,p}=hub(DEFAULT_DIAGNOSTIC_LIMITS,{records:1,bytes:65536,policy:'newest'});
 h.configure(0n,[subscription(sink,{site:'mark',kind:'marker',trigger:{kind:'every'}}),subscription(sink,{site:'mark',kind:'marker',trigger:{kind:'equals',value:{kind:'integer',type:'u128',decimal:'1'}}})]);h.acknowledge(p,1n,0n);
 h.withTurn(p,1n,0n,'test',scope=>{scope.marker('mark');assert.equal(h.health(sink).captured,0n);});
 h.withTurn(p,1n,1n,'test',scope=>scope.marker('mark'));
 assert.equal(h.health(sink).captured,1n);assert.equal(h.health(sink).filtered,2n);assert.equal(h.health(sink).dropped,1n);assert.equal(h.queued(sink).records,1);
});
