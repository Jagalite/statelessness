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
 get entries():readonly I[]{return this.#entries.map(v=>this.clone(v));}
 intoEntries():I[]{const result=this.#entries;this.#entries=[];return result;}
 contains(value:I,equal:(a:I,b:I)=>boolean):boolean{return this.#entries.some(v=>equal(this.clone(v),this.clone(value)));}
 sampleIndexed(rng:Rng):I|typeof NO_INPUT{const i=rng.index(BigInt(this.#entries.length));return i===null?NO_INPUT:this.clone(this.#entries[Number(i)]);}
 static product<A,B,I>(a:readonly A[],b:readonly B[],maximum:number,map:(a:A,b:B)=>I):I[]{natural(maximum,'maximum');if(b.length&&a.length>Math.floor(maximum/b.length))throw new ModelError('domain product limit exceeded');const out:I[]=[],frozenB=b.slice();for(const x of a.slice())for(const y of frozenB)out.push(map(x,y));return out;}
}
export function checkLazy(id:string,passed:boolean,details:()=>string){if(typeof passed!=='boolean')throw new ModelError('check predicate must be boolean');return passed?check(id):check(id,'failed',details());}
/** Catalog metadata alone is not proof that a checker executed. */
export interface PropertyMetadata {readonly id:string;readonly phase:'state'|'transition'}
export function declaredProperties(properties:readonly PropertyMetadata[]):readonly PropertyMetadata[]{const seen=new Set<string>();return Object.freeze(properties.map(p=>{scalar(p.id);if(p.phase!=='state'&&p.phase!=='transition')throw new ModelError('property phase must be state or transition');if(seen.has(p.id))throw new ModelError('duplicate property id');seen.add(p.id);return Object.freeze({id:p.id,phase:p.phase});}));}
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
