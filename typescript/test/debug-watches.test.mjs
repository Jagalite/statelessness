import test from 'node:test';
import assert from 'node:assert/strict';
import {WatchRegistry,WatchAuthorization,DEFAULT_WATCH_LIMITS} from '../dist/debug_watches.js';
import {projection,DEFAULT_INSPECT_LIMITS} from '../dist/debug_inspect.js';
const config=()=>({selector:{path:[],schemaVersion:1},trigger:{kind:'changed'},enabled:true,evaluationInterval:1n,deliveryInterval:1n,limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:32768},page:{offset:0,limit:10}});
const context=n=>({snapshot:{session:1n,revision:BigInt(n),sequence:BigInt(n)},origin:'simulation'});
const value=n=>projection({name:'manual',version:1},(p,c)=>c.scalar(p,{kind:'integer',type:'u64',decimal:String(n)}));
test('watch authorization matches typed path identity independently of object key order',()=>{
 const allowed=[{kind:'field',id:'items'},{kind:'map_key',key:{kind:'integer',type:'u64',decimal:'7'}}];
 const reordered=[{id:'items',kind:'field'},{key:{decimal:'7',type:'u64',kind:'integer'},kind:'map_key'}];
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowPaths([allowed]));
 assert.doesNotThrow(()=>registry.add({...config(),selector:{schemaVersion:1,path:reordered}}));
 assert.throws(()=>registry.add({...config(),selector:{schemaVersion:1,path:[reordered[0],{kind:'map_key',key:{kind:'integer',type:'u32',decimal:'7'}}]}}),/unauthorized/);
 assert.throws(()=>registry.add({...config(),selector:{schemaVersion:1,path:[{kind:'variant',id:'items'},reordered[1]]}}),/unauthorized/);
});
test('watch output selector matches any output variant and snapshots caller list',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 r.add({...config(),selector:{...config().selector,outputVariant:'publish'}});
 r.add({...config(),selector:{...config().selector,outputVariant:'release'}});
 const variants=['publish','release'];let calls=0;
 const result=r.observe(()=>{calls++;variants.length=0;return value(1);},{...context(0),outputVariants:variants});
 assert.equal(result.evaluated,2);assert.equal(calls,1);
 assert.equal(r.observe(()=>value(2),{...context(1),outputVariants:['other']}).evaluated,0);
 assert.equal(r.observe(()=>value(3),{...context(2),outputVariant:'publish'}).evaluated,1);
});
test('watch rejects malformed and oversized output variant collections atomically',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());r.add({...config(),selector:{...config().selector,outputVariant:'publish'}});
 const sparse=Array(1),invalid=['republish',sparse,['publish',42],Array(4097).fill('a'),['é'.repeat(524289)],['\ud800']];
 for(const outputVariants of invalid){assert.throws(()=>r.observe(()=>value(1),{...context(0),outputVariants}),/watch output variants/);assert.equal(r.snapshot().at,null);}
 assert.equal(r.observe(()=>value(1),{...context(0),outputVariants:['publish']}).evaluated,1);
});
test('watch add and update authorize the owned configuration atomically',()=>{
 const allowed=[{kind:'field',id:'safe'}],secret=[{kind:'field',id:'secret'}];
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowPaths([allowed]));let reads=0;
 const changing={...config(),selector:{schemaVersion:1,get path(){return ++reads===1?allowed:secret;}}};
 const ack=registry.add(changing);assert.equal(reads,1);assert.deepEqual(registry.list()[0].config.selector.path,allowed);
 const before=registry.snapshot(),beforeList=registry.list();
 assert.throws(()=>registry.update(ack.watchId,{...config(),selector:{schemaVersion:1,get path(){return secret;}}}),/unauthorized/);
 assert.deepEqual(registry.snapshot(),before);assert.deepEqual(registry.list(),beforeList);
 const throwing={...config(),selector:{schemaVersion:1,get path(){throw Error('snapshot failed');}}};
 assert.throws(()=>registry.add(throwing),/snapshot failed/);assert.deepEqual(registry.snapshot(),before);assert.deepEqual(registry.list(),beforeList);
});
test('watch paths reject malformed segments even with local allow-all authority',()=>{
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 const invalid=[[{kind:'unknown'}],[{kind:'index',index:-1}],[{kind:'field',id:'\ud800'}],[{kind:'map_key',key:{kind:'integer',type:'u7',decimal:'256'}}],[{kind:'map_key',key:{kind:'bool',value:'true'}}]];
 for(const path of invalid){assert.throws(()=>registry.add({...config(),selector:{schemaVersion:1,path}}));assert.equal(registry.captureRevision,0n);assert.equal(registry.list().length,0);}
});
test('watch path capture snapshots a proxy length before iterating',()=>{
 let lengthReads=0;
 const path=new Proxy([{kind:'field',id:'safe'}],{get(target,key,receiver){if(key==='length')return ++lengthReads===1?1:1000;return Reflect.get(target,key,receiver);}});
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 registry.add({...config(),selector:{schemaVersion:1,path}});
 assert.equal(lengthReads,1);assert.deepEqual(registry.list()[0].config.selector.path,[{kind:'field',id:'safe'}]);
 for(const length of [-1,1.5,33,NaN]){
  const malformed=new Proxy([],{get(target,key,receiver){return key==='length'?length:Reflect.get(target,key,receiver);}});
  assert.throws(()=>registry.add({...config(),selector:{schemaVersion:1,path:malformed}}),/invalid watch path/);
 }
 assert.equal(registry.captureRevision,1n);
});
test('context capture rejects reentrant registry mutation without side effects',()=>{
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=registry.add(config());
 const hostile={get snapshot(){registry.remove(ack.watchId);return {session:1n,revision:0n,sequence:0n};},origin:'simulation'};
 const before=registry.snapshot(),entries=registry.list();let evaluations=0;
 const view=projection({name:'manual',version:1},(path,c)=>{evaluations++;return c.scalar(path,{kind:'unit'});});
 for(const call of [()=>registry.addValidated(config(),view,hostile),()=>registry.updateValidated(ack.watchId,config(),view,hostile),()=>registry.snapshotNow(ack.watchId,view,hostile),()=>registry.observe(()=>view,hostile)]){
  assert.throws(call,/owned/);assert.deepEqual(registry.snapshot(),before);assert.deepEqual(registry.list(),entries);
 }
 assert.equal(evaluations,0);
});
test('watch configuration rejects malformed triggers, origins and projection limits atomically',()=>{
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=registry.add(config()),before=registry.snapshot(),entries=registry.list();
 const missingLimit={...config().limits};delete missingLimit.maxWork;
 const invalid=[{...config(),trigger:{kind:'unknown'}},{...config(),trigger:{kind:'equals',value:{kind:'bool',value:'true'}}},{...config(),trigger:{kind:'equals',value:{kind:'char',value:'ab'}}},{...config(),trigger:{kind:'equals',value:{kind:'float',type:'f64',decimal:'1',bits:-1n}}},{...config(),selector:{...config().selector,origin:'unknown'}},{...config(),limits:missingLimit}];
 for(const candidate of invalid){assert.throws(()=>registry.add(candidate));assert.throws(()=>registry.update(ack.watchId,candidate));assert.deepEqual(registry.snapshot(),before);assert.deepEqual(registry.list(),entries);}
});
test('watch equality accepts long valid scalar text within the configuration budget',()=>{
 for(const scalar of [{kind:'integer',type:'u128',decimal:'0'.repeat(80)+'7'},{kind:'float',type:'f64',decimal:'1.'.padEnd(82,'0'),bits:0n}]){
  const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
  const ack=registry.add({...config(),trigger:{kind:'equals',value:scalar}});
  registry.observe(()=>projection({name:'manual',version:1},(path,c)=>c.scalar(path,scalar)),context(0));
  assert.equal(registry.current(ack.watchId).kind.kind,'value_matched');
 }
 const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
 assert.throws(()=>registry.add({...config(),trigger:{kind:'equals',value:{kind:'integer',type:'u128',decimal:'9'.repeat(5000)}}}),/configuration byte limit/);
 assert.equal(registry.captureRevision,0n);
});
test('watch configuration budget charges logical headers and UTF-8 payload',()=>{
 // Current 64-bit Rust fixture: 296-byte config + 32-byte segment +
 // two-byte field + four-byte model + eighty-byte integer text = 414.
 const candidate={...config(),selector:{schemaVersion:1,path:[{kind:'field',id:'é'}],model:'😺'},trigger:{kind:'equals',value:{kind:'integer',type:'u128',decimal:'0'.repeat(79)+'7'}}};
 for(const [maximum,admitted] of [[413,false],[414,true]]){
  const registry=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxConfigBytes:maximum},WatchAuthorization.allowAll());
  if(admitted)assert.doesNotThrow(()=>registry.add(candidate));else{assert.throws(()=>registry.add(candidate),/configuration byte limit/);assert.equal(registry.captureRevision,0n);}
 }
});
test('watch retains only declared configuration fields after bounded admission',()=>{
 const candidate={...config(),unrelated:'x'.repeat(10000),selector:{...config().selector,unrelated:'y'.repeat(10000)}};
 Object.defineProperty(candidate,'hostile',{enumerable:true,get(){throw Error('unknown field was read');}});
 const registry=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxConfigBytes:296},WatchAuthorization.allowAll());registry.add(candidate);
 const retained=registry.list()[0].config;assert.equal(Object.hasOwn(retained,'unrelated'),false);assert.equal(Object.hasOwn(retained.selector,'unrelated'),false);
});
test('watch charges dynamic float type text but not canonical static float kinds',()=>{
 const value={kind:'float',type:'f64',decimal:'1.'.padEnd(80,'0'),bits:0n};
 const make=maximum=>new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxConfigBytes:maximum},WatchAuthorization.allowAll());
 assert.doesNotThrow(()=>make(376).add({...config(),trigger:{kind:'equals',value}}));
 assert.throws(()=>make(375).add({...config(),trigger:{kind:'equals',value}}),/configuration byte limit/);
 assert.throws(()=>make(379).add({...config(),trigger:{kind:'equals',value:{...value,type:'wide'}}}),/configuration byte limit/);
 assert.doesNotThrow(()=>make(380).add({...config(),trigger:{kind:'equals',value:{...value,type:'wide'}}}));
});
test('disabled and filtered watches never materialize projection',()=>{const r=new WatchRegistry();let calls=0;r.observe(()=>{calls++;return value(1);},context(0));assert.equal(calls,0);assert.throws(()=>r.add(config()),/unauthorized/);const s=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());s.add({...config(),selector:{path:[],schemaVersion:1,model:'other'}});s.observe(()=>{calls++;return value(1);},context(0));assert.equal(calls,0);assert.equal(s.health.filtered,1n);});
test('watch baseline changed unchanged sampling gap and failure provenance',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());const ack=r.add({...config(),evaluationInterval:2n});assert.equal(ack.captureRevision,1n);r.observe(()=>value(1),context(0));assert.equal(r.current(ack.watchId).kind.kind,'baseline');r.observe(()=>value(2),context(1));assert.equal(r.health.evaluationSampled,1n);r.observe(()=>value(3),context(2));assert.deepEqual(r.current(ack.watchId).kind,{kind:'change_since_last_sample',changed:true});assert.equal(r.current(ack.watchId).difference,'unknown');r.observe(()=>value(3),{...context(4),propertyFailure:true,failingProperties:['safe']});assert.equal(r.current(ack.watchId).kind.kind,'property_failure');assert.equal(r.current(ack.watchId).difference,'unknown');assert.equal(r.health.observationGaps,1n);assert.equal(r.failureSnapshot().events.some(e=>e.kind.kind==='property_failure'),true);});
test('equal queries share projection work at a boundary and authorizations precede evaluation',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());r.add(config());r.add(config());let schemas=0,projections=0;r.observe(()=>({schema(){schemas++;return {name:'manual',version:1};},inspect(p,c){projections++;return c.scalar(p,{kind:'bool',value:true});}}),context(0));assert.equal(projections,1);assert.equal(schemas,3);assert.equal(r.health.evaluated,2n);});
test('revision generation TTL and eviction stay independent from exact model state',()=>{const r=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxBaselineBytes:1},WatchAuthorization.allowAll());const ack=r.add({...config(),ttlObservations:1n});r.observe(()=>value(1),context(0));assert.equal(r.list()[0].state,'expired');assert.equal(r.health.baselineEvictions,1n);r.enable(ack.watchId);const update=r.update(ack.watchId,config());assert.equal(update.generation,3n);assert.equal(update.captureRevision,3n);r.pause(ack.watchId);assert.equal(r.interested(),false);r.resume(ack.watchId);assert.equal(r.interested(),true);r.remove(ack.watchId);assert.equal(r.list().length,0);});
test('ring loss and inspector errors are bounded observable health',()=>{const r=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxRingRecords:2,maxRingBytes:4096,maxEventBytes:2048},WatchAuthorization.allowAll());r.add({...config(),limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:2048}});for(let n=0;n<5;n++)r.observe(()=>projection({name:'manual',version:1},()=>{throw Error('inspector');}),context(n));const snapshot=r.snapshot();assert.ok(snapshot.events.length<=2);assert.ok(snapshot.retainedBytes<=4096);assert.ok(r.health.droppedEvents>0n);assert.equal(r.health.inspectionErrors,5n);assert.equal(snapshot.events.some(e=>e.kind.kind==='events_dropped'),true);});
test('failure-forced equals watch retains inspector error',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add({...config(),trigger:{kind:'equals',value:{kind:'integer',type:'u64',decimal:'7'}},evaluationInterval:5n,deliveryInterval:5n});const result=r.observe(()=>projection({name:'manual',version:1},()=>{throw Error('projection failed');}),{...context(0),propertyFailure:true,failingProperties:['safe']});const event=r.current(ack.watchId);assert.equal(result.emitted,1);assert.equal(event.kind.kind,'inspector_error');assert.equal(event.value,null);assert.equal(event.propertyFailure,true);assert.deepEqual(event.failingProperties,['safe']);assert.equal(r.failureSnapshot().events.at(-1).kind.kind,'inspector_error');});
test('validated registration rejects redaction and callback mutation reentry',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());assert.throws(()=>r.addValidated(config(),projection({name:'manual',version:1},(p,c)=>c.redacted(p)),context(0)),/redacted/);const ack=r.add(config());r.observe(()=>{assert.throws(()=>r.disable(ack.watchId),/owned/);return value(1);},context(0));assert.equal(r.list()[0].enabled,true);});
test('watch control generation increments and explicit snapshots preserve comparison baseline',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());const ack=r.add(config());r.observe(()=>value(1),context(0));const snap=r.snapshotNow(ack.watchId,value(9),context(1));assert.equal(snap.kind.kind,'snapshot');assert.equal(r.current(ack.watchId).value.kind.value.decimal,'1');assert.equal(r.disable(ack.watchId).generation,2n);assert.equal(r.enable(ack.watchId).generation,3n);assert.equal(r.updateValidated(ack.watchId,config(),value(2),context(1)).generation,4n);assert.equal(r.remove(ack.watchId).generation,5n);});
test('marker disabled/capacity paths skip details and truncation respects UTF8',async()=>{const {MarkerCollector}=await import('../dist/debug_watches.js');let calls=0;const disabled=MarkerCollector.disabled();disabled.marker('x',()=>{calls++;return 'secret';});assert.equal(calls,0);assert.equal(disabled.finish().markers.length,0);const collector=new MarkerCollector(true,1,53);collector.marker('id',()=>{calls++;return 'é😺';});collector.marker('other',()=>{calls++;return 'must not run';});const batch=collector.finish();assert.equal(calls,1);assert.equal(batch.markers[0].detail,'é');assert.equal(batch.truncated,1n);assert.equal(batch.dropped,1n);assert.ok(batch.retainedBytes<=53);});

test('validated configuration and observation context stay bound across callbacks',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),c=config(),v=projection({name:'manual',version:1},(p,cx)=>{c.enabled=false;c.trigger.kind='property_failure';return cx.scalar(p,{kind:'unit'});});const ack=r.addValidated(c,v,context(0));assert.equal(r.list()[0].enabled,true);assert.equal(r.list()[0].config.trigger.kind,'changed');const ctx={...context(1),propertyFailure:true,failingProperties:['original']};r.observe(()=>{ctx.snapshot.sequence=99n;ctx.propertyFailure=false;ctx.failingProperties[0]='changed';return value(1);},ctx);const event=r.current(ack.watchId);assert.equal(event.snapshot.sequence,1n);assert.equal(event.propertyFailure,true);assert.deepEqual(event.failingProperties,['original']);assert.equal(event.kind.kind,'property_failure');});

test('sampled-out terminal observation expires watch immediately',()=>{const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add({...config(),evaluationInterval:3n,ttlObservations:2n});r.observe(()=>value(1),context(0));let projected=0;r.observe(()=>{projected++;return value(2);},context(1));assert.equal(r.list()[0].state,'expired');assert.equal(r.list()[0].enabled,false);assert.equal(r.interested(),false);assert.equal(r.current(ack.watchId).snapshot.sequence,0n);assert.equal(r.health.evaluationSampled,1n);assert.equal(r.snapshot().events.filter(e=>e.kind.kind==='watch_expired').length,1);r.observe(()=>{projected++;return value(3);},context(2));assert.equal(projected,1);});

test('watch property IDs are bounded at UTF-8 boundaries and expose truncated metadata',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add(config());
 const ids=['a'.repeat(255)+'😀tail',...Array.from({length:17},(_,i)=>`id${i}`)];
 r.observe(()=>value(1),{...context(0),propertyFailure:true,failingProperties:ids});
 const event=r.current(ack.watchId);
 assert.equal(event.failingProperties.length,16);
 assert.equal(event.failingProperties[0],'a'.repeat(255));
 assert.deepEqual(event.completeness,{kind:'partial',reason:'value_limit'});
 const snap=r.snapshotNow(ack.watchId,value(2),{...context(1),failingProperties:ids});
 assert.equal(snap.failingProperties.length,16);
 assert.deepEqual(snap.completeness,{kind:'partial',reason:'value_limit'});
});

test('watch rejects malformed property IDs before advancing observation',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add(config());
 assert.throws(()=>r.observe(()=>value(1),{...context(0),failingProperties:['bad\ud800']}),/property ID/);
 assert.equal(r.current(ack.watchId),null);
 assert.equal(r.observe(()=>value(1),context(0)).stale,false);
});

test('watch model and machine metadata use UTF-8 prefixes and signal truncation',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add(config());
 const model='a'.repeat(1023)+'😀tail',machine='é'.repeat(512)+'x';
 r.observe(()=>value(1),{...context(0),model,machine});
 const event=r.current(ack.watchId);
 assert.equal(event.model,'a'.repeat(1023));
 assert.equal(event.machine,'é'.repeat(512));
 assert.deepEqual(event.completeness,{kind:'partial',reason:'value_limit'});
 const snapshot=r.snapshotNow(ack.watchId,value(1),{...context(1),model,machine});
 assert.equal(snapshot.model,event.model);assert.equal(snapshot.machine,event.machine);
 assert.deepEqual(snapshot.completeness,{kind:'partial',reason:'value_limit'});
 assert.throws(()=>r.observe(()=>value(1),{...context(1),model:'bad\ud800'}),/metadata/);
 assert.equal(r.observe(()=>value(1),context(1)).stale,false);
});

test('watch retains full schema identity while bounding event metadata',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add(config());
 const name='😀'.repeat(600),make=n=>projection({name:n,version:1},(p,cx)=>cx.scalar(p,{kind:'unit'}));
 r.observe(()=>make(name),context(0));
 const event=r.current(ack.watchId);
 assert.equal(event.schemaName,'😀'.repeat(256));
 assert.deepEqual(event.completeness,{kind:'partial',reason:'value_limit'});
 const snapshot=r.snapshotNow(ack.watchId,make(name),context(1));
 assert.equal(snapshot.schemaName,event.schemaName);
 assert.deepEqual(snapshot.completeness,{kind:'partial',reason:'value_limit'});
 r.observe(()=>make(name+'x'),context(1));
 assert.equal(r.list()[0].state,'paused');
});

test('oversized failure metadata cannot become a baseline or escape snapshot event limit',()=>{
 const r=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxEventBytes:5000},WatchAuthorization.allowAll());
 const ack=r.add({...config(),limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:5000}});
 const large=Array.from({length:16},()=> 'x'.repeat(256));
 r.observe(()=>value(1),{...context(0),propertyFailure:true,failingProperties:large});
 assert.equal(r.current(ack.watchId),null);
 assert.ok(r.health.droppedEvents>0n);
 assert.throws(()=>r.snapshotNow(ack.watchId,value(1),{...context(1),failingProperties:large}),/capacity|budget/);
 r.observe(()=>value(2),context(1));
 assert.deepEqual(r.current(ack.watchId).kind,{kind:'baseline',reason:'observation_gap'});
});

test('snapshot rejects completed events whose other metadata exceeds event limit',()=>{
 const r=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxEventBytes:5000},WatchAuthorization.allowAll());
 const ack=r.add({...config(),limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:5000}});
 assert.throws(()=>r.snapshotNow(ack.watchId,value(1),{...context(0),model:'\"'.repeat(1024)}),/capacity/);
});

test('oversized sampled observation preserves delivery cadence without reporting loss',()=>{
 const r=new WatchRegistry({...DEFAULT_WATCH_LIMITS,maxEventBytes:5000},WatchAuthorization.allowAll());
 const ack=r.add({...config(),trigger:{kind:'every_observation'},deliveryInterval:2n,limits:{...DEFAULT_INSPECT_LIMITS,maxBytes:5000}});
 assert.equal(r.observe(()=>value(1),context(0)).emitted,1);
 const dropped=r.health.droppedEvents;
 assert.equal(r.observe(()=>value(2),{...context(1),model:'"'.repeat(1024)}).emitted,0);
 assert.equal(r.current(ack.watchId),null);
 assert.equal(r.health.droppedEvents,dropped);
 assert.equal(r.health.deliverySampled,1n);
 assert.equal(r.observe(()=>value(3),context(2)).emitted,1);
});


test('marker collector applies Rust 64-bit logical header at exact byte thresholds',async()=>{
 const {MarkerCollector}=await import('../dist/debug_watches.js');
 let calls=0;
 const fits=new MarkerCollector(true,2,51);
 fits.marker('é',()=>{calls++;return 'x!';});
 assert.deepEqual(fits.finish(),{markers:[{id:'é',detail:'x'}],dropped:0n,truncated:1n,retainedBytes:51});
 const rejected=new MarkerCollector(true,2,49);
 rejected.marker('é',()=>{calls++;return 'unused';});
 assert.deepEqual(rejected.finish(),{markers:[],dropped:1n,truncated:0n,retainedBytes:0});
 assert.equal(calls,1);
});


test('watch schema identity ignores source location and allows long source paths',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add(config());
 const make=file=>projection({name:'model',version:1,source:{file,line:1,column:1}},(p,cx)=>cx.scalar(p,{kind:'unit'}));
 r.observe(()=>make('a'.repeat(2048)),context(0));
 assert.equal(r.list()[0].state,'active');
 assert.equal(r.current(ack.watchId).kind.kind,'baseline');
 r.observe(()=>make('b'.repeat(2048)),context(1));
 assert.equal(r.list()[0].state,'active');
 assert.equal(r.current(ack.watchId).kind.kind,'unchanged');
});


test('watch status retains updated schema source on sampled observations',()=>{
 const r=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll()),ack=r.add({...config(),evaluationInterval:2n});
 const make=file=>projection({name:'model',version:1,source:{file,line:1,column:2}},(p,cx)=>cx.scalar(p,{kind:'unit'}));
 r.observe(()=>make('first'),context(0));
 r.observe(()=>make('second'),context(1));
 assert.deepEqual(r.list()[0].displaySchema.source,{file:'second',line:1,column:2});
 assert.deepEqual(r.pause(ack.watchId).displaySchema.source,{file:'second',line:1,column:2});
});

test('local watch retains arbitrary Rust integer key text',()=>{
 for(const decimal of ['256','01','+1','','not-a-number','9'.repeat(64)]){
  const registry=new WatchRegistry(DEFAULT_WATCH_LIMITS,WatchAuthorization.allowAll());
  const path=[{kind:'map_key',key:{kind:'integer',type:'u8',decimal}}];registry.add({...config(),selector:{schemaVersion:1,path}});
  assert.equal(registry.list()[0].config.selector.path[0].key.decimal,decimal);
 }
});
