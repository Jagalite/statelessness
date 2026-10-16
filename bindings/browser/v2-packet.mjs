// Structural trace transport. JSON omits native run configuration deliberately.
import {traceFromData,traceToData} from './v2-json.mjs';
import {DEFAULT_TRACE_LIMITS,scalar,validateTraceLimits} from './v2-values.mjs';
const MAX=64*1024*1024, encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true});
const kinds=['accepted','rejected','ignored'], statuses=['passed','failed','skipped'];
const terminations=['completed','property_failed','step_limit','interrupted','model_error'];
export function toPacket(trace,limits=DEFAULT_TRACE_LIMITS) {
  const t=traceFromData(traceToData(trace),limits),parts=[];let length=0;
  const raw=b=>{if(b.length>MAX-length)throw new Error('packet byte limit');length+=b.length;parts.push(b);};
  const u32=n=>{const b=new Uint8Array(4);new DataView(b.buffer).setUint32(0,n,true);raw(b);};
  const byte=n=>raw(Uint8Array.of(n));
  const blob=b=>{u32(b.length);raw(b);};
  const text=s=>{scalar(s);blob(encoder.encode(s));};
  const checks=cs=>{u32(cs.length);for(const c of cs){text(c.id);byte(statuses.indexOf(c.status));text(c.details);}};
  raw(encoder.encode('STV2P001'));text(t.metadata.name);text(t.metadata.build);
  for(const k of ['model_version','properties_version','codec_version'])u32(t.metadata[k]);
  text('manual');byte(0);u32(0);blob(t.initialState);checks(t.initialChecks);u32(t.steps.length);
  for(const s of t.steps){blob(s.input);byte(kinds.indexOf(s.disposition.kind));text(s.disposition.reason);u32(s.outputs.length);s.outputs.forEach(blob);blob(s.postState);checks(s.checks);}
  byte(terminations.indexOf(t.termination));text(t.error);
  const result=new Uint8Array(length);let offset=0;for(const b of parts){result.set(b,offset);offset+=b.length;}return result;
}
export function fromPacket(packet,limits=DEFAULT_TRACE_LIMITS) {
  validateTraceLimits(limits);
  if(!(packet instanceof Uint8Array)||packet.length>MAX)throw new Error('packet byte limit');packet=new Uint8Array(packet);let offset=0,items=0,payload=0;
  const raw=n=>{if(n>packet.length-offset)throw new Error('truncated packet');const b=packet.slice(offset,offset+n);offset+=n;return b;};
  const u32=()=>new DataView(raw(4).buffer).getUint32(0,true),byte=()=>raw(1)[0];
  const count=()=>{const n=u32();if(n>limits.maxItems-items)throw new Error('packet item limit');items+=n;return n;};
  const blob=(logical=true)=>{const n=u32();if(logical){if(n>limits.maxBlobBytes||n>limits.maxPayloadBytes-payload)throw new Error('packet payload limit');payload+=n;}return raw(n);},text=()=>decoder.decode(blob(false));
  const tag=values=>{const n=byte();if(n>=values.length)throw new Error('invalid packet tag');return values[n];};
  const checks=()=>Array.from({length:count()},()=>({id:text(),status:tag(statuses),details:text()}));
  if(decoder.decode(raw(8))!=='STV2P001')throw new Error('unsupported packet');
  const metadata={name:text(),build:text(),model_version:u32(),properties_version:u32(),codec_version:u32()};
  text();const seed=byte();if(seed>1)throw new Error('invalid seed tag');if(seed)raw(8);const parameters=u32();if(parameters>Math.floor((packet.length-offset)/8))throw new Error('packet parameter limit');for(let n=parameters;n>0;n--){text();text();}
  const initialState=blob(),initialChecks=checks(),steps=[];
  const stepCount=count();if(stepCount>limits.maxSteps)throw new Error('packet step limit');
  for(let n=stepCount;n>0;n--){const input=blob(),disposition={kind:tag(kinds),reason:text()},outputs=Array.from({length:count()},()=>blob()),postState=blob();steps.push({input,disposition,outputs,postState,checks:checks()});}
  const termination=tag(terminations),error=text();if(offset!==packet.length)throw new Error('trailing packet bytes');
  return traceFromData(traceToData({metadata,initialState,initialChecks,steps,termination,error}),limits);
}
