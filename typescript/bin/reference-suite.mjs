#!/usr/bin/env node
// Create-new evidence and verify its retained, exactly replayed fault witnesses.
import {writeReferenceMutationSuiteNew,readReferenceMutationSuite} from '../dist/node_reference.js';
import {assessReferenceSuiteArtifacts} from '../dist/reference_evidence.js';

const [command,directory,...extra]=process.argv.slice(2);
if (!['create','verify'].includes(command) || !directory || extra.length) {
  console.error('usage: stateless-reference-suite <create|verify> DIRECTORY');
  process.exitCode=2;
} else {
  try {
    if (command==='create') {
      writeReferenceMutationSuiteNew(directory,{maxStates:10_000,maxTransitions:100_000n,maxDepth:50});
    }
    const files=readReferenceMutationSuite(directory);
    const assessment=assessReferenceSuiteArtifacts(files);
    console.log(JSON.stringify({command,adequate:assessment.adequate,build_matches:assessment.buildMatches,
      review_status:assessment.reviewStatus,artifacts:files.size,reasons:assessment.reasons}));
    if (!assessment.adequate) process.exitCode=1;
  } catch (error) {
    console.error(`reference suite ${command} failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode=1;
  }
}
