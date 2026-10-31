// Produce a new suite and separate source build pins for cross-engine replay.
// Pins identify the selected build; they are not an authentication mechanism.
import { writeFileSync } from 'node:fs';
import { writeReferenceMutationSuiteNew } from '../dist/node_reference.js';
import { ReferenceController, REFERENCE_MUTATIONS } from '../dist/index.js';
const [suite,pins]=process.argv.slice(2);
if(!suite||!pins)throw new Error('usage: node write_reference_suite.mjs SUITE_DIRECTORY PINS_FILE');
const result=writeReferenceMutationSuiteNew(suite,{maxStates:10000,maxTransitions:100000n,maxDepth:50});
const report_build=new ReferenceController().metadata().build;
const mutation_builds=Object.fromEntries(REFERENCE_MUTATIONS.map(name=>[name,new ReferenceController(undefined,name).metadata().build]));
writeFileSync(pins,JSON.stringify({report_build,mutation_builds})+'\n',{flag:'wx',mode:0o600});
if(result.results.some(row=>row.status.kind!=='Killed'))throw new Error('one or more fault controls were not killed; suite is incomplete');
