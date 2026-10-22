// Run this complete setup/full/installed qualification only through Kyoto.
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync,lstatSync} from 'node:fs';
import {createHash} from 'node:crypto';
const out='validation/native-parity/n12-typescript-recovery/'+Date.now();mkdirSync(out,{recursive:true});
function manifest(){const paths=execFileSync('git',['ls-files','--cached','--others','--exclude-standard','-z','--','typescript','conformance/debugger-diagnostics-v1.json']).toString().split('\0').filter(Boolean).sort();return paths.map(path=>{const s=lstatSync(path);if(!s.isFile())throw Error('source type drift: '+path);const b=readFileSync(path);return {path,type:'regular',size:b.length,sha256:createHash('sha256').update(b).digest('hex')};});}
const before=manifest();writeFileSync(out+'/candidate-source-before.json',JSON.stringify(before,null,2)+'\n');let failure;
try{execFileSync('npm',['ci','--ignore-scripts'],{cwd:'typescript',stdio:'inherit'});execFileSync(process.execPath,['typescript/test/debug-installed.mjs','--qualify'],{stdio:'inherit'});}catch(e){failure=e;}
const after=manifest();writeFileSync(out+'/candidate-source-after.json',JSON.stringify(after,null,2)+'\n');if(JSON.stringify(before)!==JSON.stringify(after))throw Error('qualification source membership/type/hash drift');if(failure)throw failure;
