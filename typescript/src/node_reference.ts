/** Node-only durable creation of a new, non-overwriting reference suite. */
// @ts-ignore Node declarations are not required by the browser package.
import {mkdirSync,openSync,writeSync,fsyncSync,closeSync,linkSync,unlinkSync,opendirSync,fstatSync,readSync,constants} from 'node:fs';
// @ts-ignore Node declarations are not required by the browser package.
import {join,dirname} from 'node:path';
import type {ExactSearchConfig} from './core.js';
import {prepareReferenceMutationSuite,verifyReferenceSuiteArtifacts,type ReferenceSuite,type ReferenceSuiteOptions,type ReferenceSuiteVerificationOptions} from './reference_evidence.js';

export function writeReferenceMutationSuiteNew(directory:string,config:ExactSearchConfig,options:ReferenceSuiteOptions={}):ReferenceSuite{
 const suite=prepareReferenceMutationSuite(config,options);mkdirSync(directory,{mode:0o700});
 // Windows cannot fsync a directory handle; file data is still synced before publication.
 const syncDirectory=(path:string)=>{if((globalThis as {process?:{platform?:string}}).process?.platform==='win32')return;const fd=openSync(path,'r');try{fsyncSync(fd);}finally{closeSync(fd);}};
 const write=(name:string,bytes:Uint8Array)=>{const fd=openSync(join(directory,name),'wx',0o600);let error:unknown;try{let offset=0;while(offset<bytes.length){const n=writeSync(fd,bytes,offset,bytes.length-offset);if(!Number.isSafeInteger(n)||n<=0||n>bytes.length-offset)throw new Error('invalid evidence write count');offset+=n;}fsyncSync(fd);}catch(e){error=e;}try{closeSync(fd);}catch(e){if(error===undefined)error=e;}if(error!==undefined)throw error;};
 syncDirectory(dirname(directory));for(const [name,bytes] of suite.files)if(name!=='mutations.json')write(name,bytes);syncDirectory(directory);
 const report=suite.files.get('mutations.json');if(!report)throw new Error('missing suite report');write('.mutations.json.pending',report);linkSync(join(directory,'.mutations.json.pending'),join(directory,'mutations.json'));syncDirectory(directory);unlinkSync(join(directory,'.mutations.json.pending'));syncDirectory(directory);
 return suite;
}
/** Read bounded regular files without following symlinks, then validate and replay all witnesses. */
export function readReferenceMutationSuite(directory:string,maxBytes=64*1024*1024,options:ReferenceSuiteVerificationOptions={}):ReadonlyMap<string,Uint8Array>{
 if(!Number.isSafeInteger(maxBytes)||maxBytes<1)throw new Error('invalid evidence byte limit');const names:string[]=[],dir=opendirSync(directory);try{for(;;){const entry=dir.readSync();if(entry===null)break;const name:string=entry.name;if(names.length===19||name!=='mutations.json'&&!/^[a-z-]+-(original|minimized)\.sttrace$/.test(name))throw new Error('unexpected mutation evidence artifact');names.push(name);}}finally{dir.closeSync();}
 const files=new Map<string,Uint8Array>();let total=0;for(const name of names){const fd=openSync(join(directory,name),constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);try{const stat=fstatSync(fd);if(!stat.isFile()||!Number.isSafeInteger(stat.size)||stat.size<0||stat.size>maxBytes-total)throw new Error('invalid or oversized evidence file');const bytes=new Uint8Array(stat.size);let offset=0;while(offset<bytes.length){const n=readSync(fd,bytes,offset,bytes.length-offset,null);if(!Number.isSafeInteger(n)||n<=0)throw new Error('truncated evidence file');offset+=n;}const end=new Uint8Array(1);if(readSync(fd,end,0,1,null)!==0)throw new Error('evidence file changed during read');total+=bytes.length;files.set(name,bytes);}finally{closeSync(fd);}}
 verifyReferenceSuiteArtifacts(files,maxBytes,options);return files;
}
