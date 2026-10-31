import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Domain,DomainDescriptor,Rng,NO_INPUT} from '../dist/index.js';

test('empty domain product does not traverse the other side',()=>{
 const values=new Proxy([1],{get(target,key,receiver){if(key==='0'||key==='slice')throw Error('unexpected traversal');return Reflect.get(target,key,receiver);}});
 const map=()=>{throw Error('unexpected mapping');};
 assert.deepEqual(Domain.product([],values,0,map),[]);
 assert.deepEqual(Domain.product(values,[],0,map),[]);
 assert.throws(()=>Domain.product([],values,-1,map),/maximum/);
});

test('domain product bounds captured slots when an input getter grows the other array',()=>{
 const left=[1],right=[2];
 Object.defineProperty(right,0,{get(){left.push(3,4);return 2;},configurable:true});
 const seen=Domain.product(left,right,1,(a,b)=>[a,b]);
 assert.deepEqual(seen,[[1,2]]);
 assert.equal(left.length,3);
});

test('domain entries capture slots and clone callback before hooks run',()=>{
 let active=false, calls=0;
 const domain=new Domain(new DomainDescriptor('numbers','finite',2),value=>{
  if(active && ++calls===1){const removed=domain.intoEntries();removed[1]={n:99};domain.clone=()=>({n:88});}
  return {...value};
 });
 domain.push({n:1});domain.push({n:2});active=true;
 assert.deepEqual(domain.entries,[{n:1},{n:2}]);
});

test('indexed domain sampling owns the candidate set before drawing',()=>{
 const domain=new Domain(new DomainDescriptor('numbers','finite',3),x=>({...x}));
 domain.push({n:1});domain.push({n:2});
 const rng=new Rng(0n);rng.index=upper=>{assert.equal(upper,2n);const removed=domain.intoEntries();removed[1].n=99;domain.push({n:9});return 1n;};
 assert.deepEqual(domain.sampleIndexed(rng),{n:2});assert.deepEqual(domain.entries,[{n:9}]);
});
test('indexed domain sampling rejects invalid indices and preserves empty-domain marker',()=>{
 const domain=new Domain(new DomainDescriptor('numbers','finite',2),x=>x),rng=new Rng(0n);
 assert.equal(domain.sampleIndexed(rng),NO_INPUT);domain.push(null);
 rng.index=()=>0n;assert.equal(domain.sampleIndexed(rng),null);
 for(const invalid of [null,-1n,1n,0]){rng.index=()=>invalid;assert.throws(()=>domain.sampleIndexed(rng),/outside snapshot/);}
});
test('indexed domain sampling only reports the selected entry copy failure',()=>{
 let active=false;const domain=new Domain(new DomainDescriptor('numbers','finite',2),x=>{if(active&&x.n===2)throw Error('copy two');return {...x};});
 domain.push({n:1});domain.push({n:2});active=true;const rng=new Rng(0n);
 rng.index=()=>0n;assert.deepEqual(domain.sampleIndexed(rng),{n:1});
 rng.index=()=>1n;assert.throws(()=>domain.sampleIndexed(rng),/copy two/);
 rng.index=()=>{throw Error('draw failed');};assert.throws(()=>domain.sampleIndexed(rng),/draw failed/);
});
test('domain membership owns candidate set and requires synchronous boolean equality',()=>{
 const domain=new Domain(new DomainDescriptor('numbers','finite',3),x=>({...x}));domain.push({n:1});domain.push({n:2});
 let calls=0;const found=domain.contains({n:2},(left,right)=>{if(++calls===1){const removed=domain.intoEntries();removed[1].n=99;}return left.n===right.n;});
 assert.equal(found,true);assert.equal(calls,2);
 domain.push({n:1});for(const invalid of [1,'yes',Promise.resolve(false),Promise.reject(Error('async equality'))])assert.throws(()=>domain.contains({n:1},()=>invalid),/boolean synchronously/);
});
test('empty domain membership does not invoke clone or equality callbacks',()=>{
 const domain=new Domain(new DomainDescriptor('empty','finite',1),()=>{throw Error('clone called');});
 assert.equal(domain.contains({},()=>{throw Error('equality called');}),false);
});

test('domain membership captures query before entry clones and defers unused clone failures',()=>{
 const query={n:1};let active=false;
 const domain=new Domain(new DomainDescriptor('numbers','finite',2),value=>{
   if(active && value.n===2) { query.n=9; throw Error('later clone'); }
   return {...value};
 });
 domain.push({n:1});domain.push({n:2});active=true;
 assert.equal(domain.contains(query,(entry,candidate)=>entry.n===candidate.n),true);
 query.n=3;
 assert.throws(()=>domain.contains(query,(entry,candidate)=>entry.n===candidate.n),/later clone/);
});
