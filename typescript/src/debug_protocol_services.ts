/** Wire projections for explicitly attached native services. */
import {ProtocolError} from './debug_protocol.js';
import type {PathSegment,IntegerType,InspectNode} from './debug_inspect.js';
const n=(v:number|bigint)=>`u64:${v}`;
export function decodeProtocolPath(path:readonly string[]):PathSegment[]{return path.map(v=>{const split=v.indexOf(':'),kind=v.slice(0,split),value=v.slice(split+1);if(kind==='field'||kind==='variant')return {kind,id:value};if(kind==='index'){const index=Number(value);if(!Number.isSafeInteger(index))throw new ProtocolError('limit');return {kind,index};}if(kind==='key.string'||kind==='key.char')return {kind:'map_key',key:{kind:kind==='key.string'?'string':'char',value}};if(kind==='key.bool')return {kind:'map_key',key:{kind:'bool',value:value==='true'}};return {kind:'map_key',key:{kind:'integer',type:kind.slice(4) as IntegerType,decimal:value}};});}
export function protocolSegment(s:PathSegment):string{if(s.kind==='field'||s.kind==='variant')return `${s.kind}:${s.id}`;if(s.kind==='index')return `index:${s.index}`;if(s.kind==='map_key')return s.key.kind==='integer'?`key.${s.key.type}:${s.key.decimal}`:`key.${s.key.kind}:${s.key.value}`;throw new ProtocolError('malformed');}
export function nodeProtocolFields(prefix:string,node:InspectNode,depth=0):[string,string][]{
 if(depth>32)throw new ProtocolError('limit');const f:[string,string][]=[[`${prefix}.completeness`,`enum:${node.completeness.kind==='complete'?'Complete':`Partial(${protocolVariant(node.completeness.reason)})`}`],[`${prefix}.child_count`,node.childCount===null?'none':n(node.childCount)]];
 if(node.page)f.push([`${prefix}.page.offset`,n(node.page.offset)],[`${prefix}.page.returned`,n(node.page.returned)],[`${prefix}.page.total`,n(node.page.total)],[`${prefix}.page.next`,node.page.nextOffset===null?'none':n(node.page.nextOffset)]);
 const k=node.kind;let kind:string=k.kind,value='none';
 if(k.kind==='scalar'){const v=k.value;kind=v.kind;switch(v.kind){case 'unit':value='unit';break;case 'integer':value=`${v.type}:${v.decimal}`;break;case 'float':value=`${v.type}:${v.decimal}`;f.push([`${prefix}.bits`,n(v.bits)]);break;default:value=`${v.kind}:${v.value}`;}}
 else if(k.kind==='string'||k.kind==='bytes'){value=k.kind==='string'?`string:${k.preview}`:`bytes:${Array.from(k.preview,b=>b.toString(16).padStart(2,'0')).join('')}`;f.push([`${prefix}.total_bytes`,n(k.totalBytes)]);}
 else if(k.kind==='object'||k.kind==='sequence'||k.kind==='enum'){value=`string:${k.name}`;if(k.kind==='enum')f.push([`${prefix}.variant_id`,`string:${k.variantId}`],[`${prefix}.variant_label`,`string:${k.variantLabel}`]);}
 else if(k.kind==='map')value=`enum:${protocolVariant(k.ordering)}`;else if(k.kind==='opaque')value=`string:${k.label}`;else if(k.kind==='truncated')value=`enum:${protocolVariant(k.reason)}`;
 f.push([`${prefix}.kind`,`enum:${kind}`],[`${prefix}.value`,value],[`${prefix}.children.returned`,n(node.children.length)]);
 node.children.forEach((c,i)=>{const p=`${prefix}.children.${i}`;f.push([`${p}.segment`,protocolSegment(c.segment)],[`${p}.label`,`string:${c.label}`],...nodeProtocolFields(p+'.node',c.node,depth+1));});return f;
}

export function protocolVariant(value:string):string{return value.split('_').map(v=>v[0].toUpperCase()+v.slice(1)).join('');}

export function protocolInspectionError(message:string):string{return ({'inspection path not found':'PathNotFound','wrong inspection path segment':'WrongPathSegment','invalid page':'InvalidPage','work limit':'WorkLimit','inspection work limit':'WorkLimit','projection budget exceeded':'BudgetExceeded','inspection budget exceeded':'BudgetExceeded','inspection returned projection exceeds budget':'BudgetExceeded'} as Record<string,string>)[message]??'InvalidMetadata';}
