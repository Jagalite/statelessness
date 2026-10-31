/** Explicit modeling helpers; codecs and optional inspection remain separate. */
import {NO_INPUT} from './search.js';
import {check, natural, scalar, ModelError, Rng, type Model} from './core.js';
export class DomainDescriptor {
 readonly name:string; readonly assumptions:string; readonly maxEntries:number;
 constructor(name:string,assumptions:string,maxEntries:number){scalar(name);scalar(assumptions);natural(maxEntries,'maxEntries');this.name=name;this.assumptions=assumptions;this.maxEntries=maxEntries;Object.freeze(this);}
 parameters():[string,string][]{return [['model.domain.name',this.name],['model.domain.assumptions',this.assumptions],['model.domain.max_entries',String(this.maxEntries)]];}
}
export class Domain<I> {
 #entries:I[]=[];
 #descriptor:DomainDescriptor;
 get descriptor():DomainDescriptor{return this.#descriptor;}
 constructor(descriptor:DomainDescriptor,private readonly clone:(v:I)=>I){if(!(descriptor instanceof DomainDescriptor)||typeof clone!=='function')throw new ModelError('domain descriptor and clone required');this.#descriptor=descriptor;}
 push(value:I):void{if(this.#entries.length>=this.descriptor.maxEntries)throw new ModelError('domain entry limit exceeded');const owned=this.clone(value);if(this.#entries.length>=this.#descriptor.maxEntries)throw new ModelError('domain entry limit exceeded');this.#entries.push(owned);}
 /** Successful prefix remains on overflow/iterator failure. */
 extend(values:Iterable<I>):void{for(const v of values)this.push(v);}
 get entries():readonly I[]{const source=this.#entries.slice(),clone=this.clone;return source.map(v=>clone.call(this,v));}
 intoEntries():I[]{const result=this.#entries;this.#entries=[];return result;}
 /** Clone hooks run eagerly; an unused entry's clone failure is deferred. */
 contains(value:I,equal:(a:I,b:I)=>boolean):boolean{if(this.#entries.length===0)return false;const source=this.#entries.slice(),clone=this.clone,candidate=clone.call(this,value);const entries=source.map(entry=>{try{return {ok:true as const,value:clone.call(this,entry)};}catch(error){return {ok:false as const,error};}});for(const entry of entries){if(!entry.ok)throw entry.error;const result:unknown=equal(clone.call(this,entry.value),clone.call(this,candidate));if(typeof result!=='boolean'){if(result instanceof Promise)void result.catch(()=>{});throw new ModelError('domain equality must return boolean synchronously');}if(result)return true;}return false;}
 /** Clone all entries before the draw (hooks may have side effects), defer unused errors, then clone the selected value for the caller. */
 sampleIndexed(rng:Rng):I|typeof NO_INPUT{const clone=this.clone,entries=this.#entries.slice().map(v=>{try{return {ok:true as const,value:clone.call(this,v)};}catch(error){return {ok:false as const,error};}}),i=rng.index(BigInt(entries.length));if(i===null&&entries.length===0)return NO_INPUT;if(typeof i!=='bigint'||i<0n||i>=BigInt(entries.length))throw new ModelError('domain sample index outside snapshot');const selected=entries[Number(i)];if(!selected.ok)throw selected.error;return clone.call(this,selected.value);}
 static product<A,B,I>(a:readonly A[],b:readonly B[],maximum:number,map:(a:A,b:B)=>I):I[]{
  natural(maximum,'maximum');
  const aLength=a.length;if(aLength===0)return [];
  const bLength=b.length;if(bLength===0)return [];
  natural(aLength,'domain length');natural(bLength,'domain length');
  if(aLength>Math.floor(maximum/bLength))throw new ModelError('domain product limit exceeded');
  // Indexed reads are bounded by the admitted lengths, even when a proxy or an
  // element getter changes the other input while these slots are captured.
  const frozenA:A[]=[],frozenB:B[]=[];
  for(let i=0;i<aLength;i++)frozenA.push(a[i]);
  for(let i=0;i<bLength;i++)frozenB.push(b[i]);
  const out:I[]=[];for(const x of frozenA)for(const y of frozenB)out.push(map(x,y));return out;
 }
}
export function checkLazy(id:string,passed:boolean,details:()=>string){if(typeof passed!=='boolean')throw new ModelError('check predicate must be boolean');return passed?check(id):check(id,'failed',details());}
/** Catalog metadata alone is not proof that a checker executed. */
export interface PropertyMetadata {readonly id:string;readonly phase:'state'|'transition'}
export function declaredProperties(properties:readonly PropertyMetadata[]):readonly PropertyMetadata[]{
 if(!Array.isArray(properties))throw new ModelError('properties must be an array');
 const length=properties.length; natural(length,'properties length');
 const seen=new Set<string>(),out:PropertyMetadata[]=[];
 for(let i=0;i<length;i++){
  const p=properties[i];if(p===null||typeof p!=='object')throw new ModelError('property declaration required');
  const id=p.id,phase=p.phase;scalar(id);
  if(phase!=='state'&&phase!=='transition')throw new ModelError('property phase must be state or transition');
  if(seen.has(id))throw new ModelError('duplicate property id');seen.add(id);
  out.push(Object.freeze({id,phase}));
 }
 return Object.freeze(out);
}
/** Typed builder preserves receiver-bound class methods and all binder capabilities. */
export function adaptModel<S,I,O,M extends Omit<Model<S,I,O>,'initialState'|'checkState'> & {initialState?:()=>S;checkState?:(s:S)=>ReturnType<Model<S,I,O>['checkState']>}>(m:M,options:{properties:readonly PropertyMetadata[];initialDefault?:()=>S;unchecked?:boolean}):M & Model<S,I,O>{
 const properties=declaredProperties(options.properties);
 if(!properties.length&&!options.unchecked)throw new ModelError('no properties; select unchecked');
 if(options.initialDefault&&m.initialState)throw new ModelError('initial_default conflicts with initial callback');
 const initial=options.initialDefault??m.initialState?.bind(m);
 if(typeof initial!=='function')throw new ModelError('initial callback or initialDefault factory required');
 for(const role of ['step','cloneState','cloneInput','cloneOutput'] as const)if(typeof m[role]!=='function')throw new ModelError(`${role} callback required`);
 if(!options.unchecked&&!m.checkState&&!m.checkStateInto&&!m.checkTransition&&!m.checkTransitionInto)throw new ModelError('property checker required');
 const overrides={initialState:()=>m.cloneState(initial()),checkState:m.checkState?.bind(m)??(()=>[])};
 return new Proxy(Object.create(Object.getPrototypeOf(m)),{get(_target,key){if(Object.hasOwn(overrides,key))return overrides[key as keyof typeof overrides];const value=Reflect.get(m,key,m);return typeof value==='function'?value.bind(m):value;},has(_target,key){return Object.hasOwn(overrides,key)||key in m;},ownKeys(){return [...new Set([...Reflect.ownKeys(m),...Reflect.ownKeys(overrides)])];},getOwnPropertyDescriptor(_target,key){if(Object.hasOwn(overrides,key))return {configurable:true,enumerable:true,writable:false,value:overrides[key as keyof typeof overrides]};const descriptor=Reflect.getOwnPropertyDescriptor(m,key);return descriptor?{...descriptor,configurable:true}:undefined;}}) as M & Model<S,I,O>;
}
