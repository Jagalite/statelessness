import {codecField,orderedRecord,enumCases,taggedEnum,valueCodecs,encodeValue,decodeValue} from '@jagalite/statelessness';
// Node IO stays outside the codec algorithm.
declare const process: {argv:string[]};
// @ts-ignore Node builtins supplied by the installed consumer host.
import {readFileSync,writeFileSync} from 'node:fs';
// @ts-ignore Node builtin Buffer supplied by host.
import {Buffer} from 'node:buffer';
type Point={x:number;y:string};
const f=codecField<Point>();
const point=orderedRecord<Point>({name:'Point',fields:[f.value('x',valueCodecs.u32),f.value('y',valueCodecs.string)],construct:v=>({x:v.x,y:v.y}),require:'both'});
type Event={kind:'Empty'}|{kind:'Point';x:number;y:string};
const c=enumCases<Event,'kind'>('kind'), pf=codecField<Extract<Event,{kind:'Point'}>>();
const event=taggedEnum<Event>({name:'Event',discriminator:'kind',cases:[c.unit('Empty',0,()=>({kind:'Empty'})),c.record('Point',1,{fields:[pf.value('x',valueCodecs.u32),pf.value('y',valueCodecs.string)],construct:v=>({kind:'Point',x:v.x,y:v.y})})],require:'both'});
const expectations=[{id:'point',hex:'07000000020000006869'},{id:'empty',hex:'00'},{id:'variant',hex:'0107000000020000006869'}];
const args=process.argv.slice(2), input=args[0]==='produce'?expectations:JSON.parse(readFileSync(args[1],'utf8'));
if(args[0]!=='produce'&&args[0]!=='transcode')throw Error('expected produce or transcode');
if(input.length!==3)throw Error('case count');
const result=input.map((row:{id:string;hex:string},index:number)=>{
 if(row.id!==expectations[index].id||row.hex!==expectations[index].hex)throw Error('unexpected wire bytes');
 const bytes=Buffer.from(row.hex,'hex');
 let encoded:Uint8Array;
 if(row.id==='point'){const value=decodeValue(point,bytes);if(value.x!==7||value.y!=='hi')throw Error('Point mismatch');encoded=encodeValue(point,value,100);}
 else{const value=decodeValue(event,bytes);if(row.id==='empty'?value.kind!=='Empty':value.kind!=='Point'||value.x!==7||value.y!=='hi')throw Error('Event mismatch');encoded=encodeValue(event,value,100);}
 const hex=Buffer.from(encoded).toString('hex');if(hex!==row.hex)throw Error('reencode mismatch');return {id:row.id,hex};
});
writeFileSync(args[0]==='produce'?args[1]:args[2],JSON.stringify(result)+'\n');console.log(JSON.stringify({case_count:3}));
