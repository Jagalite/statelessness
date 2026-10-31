import test from 'node:test';
import assert from 'node:assert/strict';
import {projection,inspect,DEFAULT_INSPECT_LIMITS,compareNodes,diff,isComplete} from '../dist/debug_inspect.js';
const schema={name:'manual',version:1};const identity={session:18446744073709551615n,revision:9007199254740993n,sequence:0n};
const q=(path=[],page={offset:0,limit:100},limits=DEFAULT_INSPECT_LIMITS)=>({snapshot:identity,path,schemaVersion:1,page,limits});
const int=n=>projection(schema,(p,c)=>c.scalar(p,{kind:'integer',type:'u64',decimal:String(n)}));
test('bounded object path and redaction never evaluates hidden value',()=>{let evaluations=0;const value=projection(schema,(p,c)=>c.object(p,'manual',[{id:'public',label:'public',value:int(1)},{id:'secret',label:'secret',value:null}]));const result=inspect(value,q());assert.equal(result.snapshot.revision,9007199254740993n);assert.equal(result.node.children[1].node.kind.kind,'redacted');assert.equal(isComplete(result.node),false);assert.equal(compareNodes(result.node,result.node),'unknown');assert.equal(evaluations,0);assert.equal(inspect(value,q([{kind:'field',id:'public'}])).node.kind.value.decimal,'1');assert.throws(()=>inspect(value,q([{kind:'field',id:'secret'},{kind:'field',id:'x'}])),/path not found/);});
test('sequence pages do bounded work and page positions are not persistent identities',()=>{let evaluated=[];const value=projection(schema,(p,c)=>c.sequence(p,'items',100,i=>{evaluated.push(i);return int(i);}));const result=inspect(value,q([],{offset:5,limit:2}));assert.deepEqual(evaluated,[5,6]);assert.deepEqual(result.node.page,{offset:5,returned:2,total:100,nextOffset:7});assert.equal(result.node.completeness.reason,'pagination');assert.equal(compareNodes(result.node,result.node),'unknown');});
test('utf8 previews preserve codepoint boundaries and budgets bound builtin work',()=>{const value=projection(schema,(p,c)=>c.string(p,'é😺x'));const r=inspect(value,q([],undefined,{...DEFAULT_INSPECT_LIMITS,maxValueBytes:3}));assert.equal(r.node.kind.preview,'é');assert.equal(r.node.kind.totalBytes,7);assert.equal(r.node.completeness.reason,'value_limit');assert.throws(()=>inspect(value,q([],undefined,{...DEFAULT_INSPECT_LIMITS,maxWork:2})),/work limit/);assert.throws(()=>inspect(int(1),q([],undefined,{...DEFAULT_INSPECT_LIMITS,maxBytes:1})),/budget/);});
test('scalar text uses projection bytes rather than the preview limit',()=>{
 const limits={...DEFAULT_INSPECT_LIMITS,maxValueBytes:1,maxBytes:1024};
 const values=[{kind:'integer',type:'u8',decimal:'éé'},{kind:'float',type:'extended',decimal:'résultat',bits:3n}];
 for(const value of values){
  const built=projection(schema,(p,c)=>c.scalar(p,value));
  const node={kind:{kind:'scalar',value},children:[],childCount:0,page:null,completeness:{kind:'complete'}};
  assert.deepEqual(inspect(built,q([],undefined,limits)).node.kind.value,value);
  assert.deepEqual(inspect({schema:()=>schema,inspect:()=>node},q([],undefined,limits)).node.kind.value,value);
  assert.throws(()=>inspect(built,q([],undefined,{...limits,maxBytes:130})),/budget/);
  assert.throws(()=>inspect({schema:()=>schema,inspect:()=>node},q([],undefined,{...limits,maxBytes:130})),/budget/);
 }
});
test('custom inspection nodes honor the value-byte limit',()=>{
 const limits={...DEFAULT_INSPECT_LIMITS,maxValueBytes:3};
 const node=kind=>({kind,children:[],childCount:0,page:null,completeness:{kind:'partial',reason:'value_limit'}});
 for(const kind of [{kind:'string',preview:'éé',totalBytes:4},{kind:'opaque',label:'éé'},{kind:'bytes',preview:new Uint8Array(4),totalBytes:4}]){
  assert.throws(()=>inspect({schema:()=>schema,inspect:()=>node(kind)},q([],undefined,limits)),/budget/);
 }
 for(const kind of [{kind:'string',preview:'éx',totalBytes:3},{kind:'opaque',label:'éx'},{kind:'bytes',preview:new Uint8Array(3),totalBytes:3}]){
  assert.equal(inspect({schema:()=>schema,inspect:()=>node(kind)},q([],undefined,limits)).node.kind.kind,kind.kind);
 }
 const nested={kind:{kind:'object',name:'x'},children:[{segment:{kind:'field',id:'x'},label:'x',node:node({kind:'opaque',label:'éé'})}],childCount:1,page:null,completeness:{kind:'complete'}};
 assert.throws(()=>inspect({schema:()=>schema,inspect:()=>nested},q([],undefined,limits)),/budget/);
});
test('custom projection child segments must be valid typed paths within budget',()=>{
 const leaf={kind:{kind:'scalar',value:{kind:'unit'}},children:[],childCount:0,page:null,completeness:{kind:'complete'}};
 const model=segment=>({schema:()=>schema,inspect:()=>({kind:{kind:'object',name:'x'},children:[{segment,label:'x',node:leaf}],childCount:1,page:null,completeness:{kind:'complete'}})});
 for(const segment of [{kind:'map_key',key:{kind:'bool',value:'true'}},{kind:'map_key',key:{kind:'char',value:'ab'}},{kind:'map_key',key:{kind:'integer',type:'bad',decimal:'256'}},{kind:'field',id:'x'.repeat(262145)}])
  assert.throws(()=>inspect(model(segment),q()),/invalid path/);
 assert.equal(inspect(model({kind:'map_key',key:{kind:'bool',value:true}}),q()).node.children.length,1);
});
test('opaque inspection validates suffixes within its work budget',()=>{const limits={...DEFAULT_INSPECT_LIMITS,maxValueBytes:2};const value=s=>projection(schema,(p,c)=>c.opaque(p,s));assert.equal(inspect(value('é😺x'),q([],undefined,limits)).node.kind.label,'é');assert.throws(()=>inspect(value('ab\ud800'),q([],undefined,limits)));assert.throws(()=>inspect(value('abcdef'),q([],undefined,{...limits,maxWork:2})),/work limit/);assert.throws(()=>inspect(value('ab\ud800'),q([],undefined,{...limits,maxWork:2})),/work limit/);});
test('complete diffs preserve schema/path compatibility and unknown precedes equality',()=>{const a=inspect(int(1),q()),b=inspect(int(2),q());assert.equal(diff(a,a).kind,'unchanged');assert.equal(diff(a,b).kind,'changed');assert.equal(compareNodes(null,a.node),'added');assert.equal(compareNodes(a.node,null),'removed');assert.equal(diff(a,{...b,schema:{...schema,version:2}}).kind,'unknown');const opaque=inspect(projection(schema,(p,c)=>c.opaque(p,'debug-format')),q());assert.equal(diff(opaque,opaque).kind,'unknown');});
test('stable map key identity differs from sequence position; unstable comparison unknown',()=>{const value=ordering=>projection(schema,(p,c)=>c.map(p,ordering,2,i=>({key:{kind:'integer',type:'u64',decimal:String(9007199254740993n+BigInt(i))},value:int(i)})));const stable=inspect(value('stable'),q());assert.equal(isComplete(stable.node),true);const path=[{kind:'map_key',key:{kind:'integer',type:'u64',decimal:'9007199254740994'}}];assert.equal(inspect(value('stable'),q(path)).node.kind.value.decimal,'1');const unstable=inspect(value('unstable'),q());assert.equal(compareNodes(unstable.node,unstable.node),'unknown');});
test('depth/schema/path limits reject or mark incomplete without evaluating deeper nodes',()=>{let calls=0;const deep=projection(schema,(p,c)=>{calls++;return c.object(p,'root',[{id:'leaf',label:'leaf',value:int(1)}]);});const r=inspect(deep,q([],undefined,{...DEFAULT_INSPECT_LIMITS,maxDepth:0}));assert.equal(r.node.children[0].node.kind.kind,'truncated');assert.equal(calls,1);assert.throws(()=>inspect(deep,{...q(),schemaVersion:2}),/schema mismatch/);assert.throws(()=>inspect(deep,q([{kind:'field',id:'leaf'}],undefined,{...DEFAULT_INSPECT_LIMITS,maxDepth:0})),/path too deep/);});

test('schema and source metadata share retained projection byte budget',()=>{const huge='x'.repeat(300000);assert.throws(()=>inspect(projection({name:huge,version:1},(p,c)=>c.scalar(p,{kind:'unit'})),q()),/metadata budget/);const value=projection(schema,(p,c)=>c.object(p,'root',[{id:'x',label:'x',source:{file:huge,line:1,column:1},value:int(1)}]));assert.throws(()=>inspect(value,q()),/metadata budget/);});

test('projection results retain only declared structural fields and reject cyclic trees',()=>{const raw={kind:{kind:'scalar',value:{kind:'unit'}},children:[],childCount:0,page:null,completeness:{kind:'complete'}};Object.defineProperty(raw,'secret',{enumerable:true,get(){throw Error('must not read undeclared payload');}});const result=inspect({schema:()=>({name:'manual',version:1,get secret(){throw Error('schema extra');}}),inspect:()=>raw},q());assert.equal(Object.hasOwn(result.node,'secret'),false);assert.equal(Object.hasOwn(result.schema,'secret'),false);const cyclic={kind:raw.kind,children:[],childCount:raw.childCount,page:raw.page,completeness:raw.completeness};cyclic.children.push({segment:{kind:'field',id:'self'},label:'self',node:cyclic});assert.throws(()=>inspect({schema:()=>schema,inspect:()=>cyclic},q()),/exceeds budget/);});

test('projection captures schema and inspection limits resist callback mutation',()=>{const metadata={name:'frozen',version:1,source:{file:'a',line:1,column:1}},value=projection(metadata,(p,c)=>{assert.throws(()=>{c.limits.maxBytes=999999;},TypeError);return c.scalar(p,{kind:'unit'});});metadata.name='changed';metadata.source.file='changed';const r=inspect(value,q());assert.equal(r.schema.name,'frozen');assert.equal(r.schema.source.file,'a');});

test('duplicate child segments are incomplete and comparisons remain unknown',()=>{const leaf=inspect(int(1),q()).node,make=segments=>({kind:{kind:'object',name:'x'},children:segments.map(segment=>({segment,label:'x',node:leaf})),childCount:segments.length,page:null,completeness:{kind:'complete'}});const duplicate=make([{kind:'field',id:'x'},{kind:'field',id:'x'}]);assert.equal(isComplete(duplicate),false);assert.equal(compareNodes(duplicate,duplicate),'unknown');assert.equal(compareNodes(null,duplicate),'unknown');const distinct=make([{kind:'field',id:'x'},{kind:'variant',id:'x'}]);assert.equal(isComplete(distinct),true);assert.equal(compareNodes(distinct,distinct),'unchanged');const keys=make([{kind:'map_key',key:{kind:'integer',type:'u64',decimal:'1'}},{kind:'map_key',key:{kind:'integer',type:'u64',decimal:'1'}}]);assert.equal(isComplete(keys),false);const nested=make([{kind:'field',id:'outer'}]);nested.children[0].node=duplicate;assert.equal(isComplete(nested),false);});

test('selected object and map paths reject ambiguity before evaluating child',()=>{let calls=0;const child=projection(schema,(p,c)=>{calls++;return c.scalar(p,{kind:'unit'});});const object=projection(schema,(p,c)=>c.object(p,'root',[{id:'x',label:'a',value:child},{id:'x',label:'b',value:child}]));const field=[{kind:'field',id:'x'}];assert.throws(()=>inspect(object,q(field)),/ambiguous/);assert.equal(calls,0);assert.throws(()=>inspect(object,q(field,undefined,{...DEFAULT_INSPECT_LIMITS,maxWork:1})),/work limit/);assert.equal(calls,0);const key={kind:'string',value:'x'},map=projection(schema,(p,c)=>c.map(p,'stable',2,()=>({key,value:child})));assert.throws(()=>inspect(map,q([{kind:'map_key',key}])),/ambiguous/);assert.equal(calls,0);assert.throws(()=>inspect(map,q([{kind:'map_key',key}],undefined,{...DEFAULT_INSPECT_LIMITS,maxWork:1})),/work limit/);assert.equal(calls,0);});

test('inspection snapshots request and schema before application callbacks',()=>{const query=q([{kind:'field',id:'x'}]),metadata={name:'original',version:1},value={schema(){query.path[0].id='changed';query.snapshot={session:1n,revision:1n,sequence:1n};query.limits={...query.limits,maxBytes:0};return metadata;},inspect(path,context){assert.equal(path[0].id,'x');assert.throws(()=>{path[0].id='poison';},TypeError);metadata.name='changed';return context.scalar([], {kind:'unit'});}};const result=inspect(value,query);assert.equal(result.path[0].id,'x');assert.equal(result.schema.name,'original');assert.deepEqual(result.snapshot,identity);});

test('map lookup uses typed key identity, ignoring unrelated object properties',()=>{
 const key={kind:'string',value:'x'};
 const selected=projection(schema,(p,c)=>c.map(p,'stable',1,()=>({key:{...key,extra:'ignored'},value:int(7)})));
 assert.equal(inspect(selected,q([{kind:'map_key',key}])).node.kind.value.decimal,'7');
 const ambiguous=projection(schema,(p,c)=>c.map(p,'stable',2,i=>({key:{...key,extra:i},value:int(i)})));
 assert.throws(()=>inspect(ambiguous,q([{kind:'map_key',key}])),/ambiguous/);
});

test('direct inspection rejects invalid typed query paths before invoking the model',()=>{
 let calls=0;
 const model=projection(schema,(p,c)=>{calls++;return c.scalar([],{kind:'unit'});});
 for(const key of [{kind:'char',value:'ab'},{kind:'bool',value:'true'},{kind:'integer',type:'bad',decimal:'256'}]){
  assert.throws(()=>inspect(model,q([{kind:'map_key',key}])),/invalid path/);
 }
 assert.throws(()=>inspect(model,{...q(),path:null}),/invalid diagnostic path/);
 assert.throws(()=>inspect(model,{...q(),path:new Array(1)}),/sparse diagnostic path/);
 assert.equal(calls,0);
});

test('direct inspection preserves arbitrary Rust integer key text',()=>{
 for(const decimal of ['256','01','+1','','not-a-number','9'.repeat(64)]){
  let seen;const value=projection(schema,(path,c)=>{seen=path[0].key.decimal;return c.scalar([],{kind:'unit'});});
  const path=[{kind:'map_key',key:{kind:'integer',type:'u8',decimal}}];
  assert.equal(inspect(value,q(path)).path[0].key.decimal,decimal);assert.equal(seen,decimal);
 }
});
