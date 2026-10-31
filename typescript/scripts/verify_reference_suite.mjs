// Verify a suite against explicit source and per-mutant build pins. Pins are
// identity selection, not authentication of the artifacts or their producer.
import {openSync,closeSync,fstatSync,readSync,constants} from 'node:fs';
import {strictParse,fields,string} from '../dist/json.js';
import {readAuditTrace,assessReferenceSuiteArtifacts,REFERENCE_MUTATIONS} from '../dist/index.js';
import {readReferenceMutationSuite} from '../dist/node_reference.js';
const [directory,pinPath]=process.argv.slice(2);
if(!directory||!pinPath)throw new Error('usage: node verify_reference_suite.mjs SUITE_DIRECTORY PINS_FILE');
const fd=openSync(pinPath,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
let bytes;try{const stat=fstatSync(fd);if(!stat.isFile()||!Number.isSafeInteger(stat.size)||stat.size<1||stat.size>1<<20)throw new Error('invalid or oversized pins');bytes=new Uint8Array(stat.size);let pos=0;while(pos<bytes.length){const n=readSync(fd,bytes,pos,bytes.length-pos,null);if(n<=0)throw new Error('truncated pins');pos+=n;}if(readSync(fd,new Uint8Array(1),0,1,null)!==0)throw new Error('pins changed during read');}finally{closeSync(fd);}
const root=fields(strictParse(bytes,1<<20),['report_build','mutation_builds']),reportBuild=string(root.report_build),pins=fields(root.mutation_builds,REFERENCE_MUTATIONS);
if(!reportBuild.trim())throw new Error('empty report build');for(const name of REFERENCE_MUTATIONS)if(!string(pins[name]).trim())throw new Error('empty mutant build');
const options={allowBuildMismatch:true,expectedBuild:reportBuild},files=readReferenceMutationSuite(directory,64*1024*1024,options);
for(const mutation of REFERENCE_MUTATIONS)for(const label of ['original','minimized']){const witness=files.get(`${mutation}-${label}.sttrace`);if(witness&&readAuditTrace(witness).metadata.build!==pins[mutation])throw new Error(`mutant build mismatch: ${mutation}`);}
const result=assessReferenceSuiteArtifacts(files,64*1024*1024,options);process.stdout.write(JSON.stringify(result)+'\n');if(!result.adequate)process.exitCode=1;
