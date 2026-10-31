// Run from an installed package or this package directory after `npm run build`.
// This is a finite, in-memory teaching model; it does not prove host liveness.
import {
  ReferenceController, referenceAssuranceManifest, referenceCoverage,
  assuredEnumerate, evaluateReferenceMutation, REFERENCE_MUTATION_TARGETS,
  record, replay,
} from '@jagalite/statelessness';

const bounds = {maxStates: 10_000, maxTransitions: 100_000n, maxDepth: 50};
const model = new ReferenceController();
const run = assuredEnumerate(model, referenceAssuranceManifest(model), referenceCoverage, bounds);
if (!run.search.ok) throw run.search.error;
if (run.search.value.termination !== 'graph_exhausted' || !run.assessment.adequate) {
  throw new Error(`reference assurance incomplete: ${run.assessment.reasons.join('; ')}`);
}
console.log(`finite controller search: ${run.search.value.termination}; states=${run.search.value.states}; transitions=${run.search.value.transitions}`);

const mutation = 'missing-cleanup';
const result = evaluateReferenceMutation(mutation, bounds);
if (result.status.kind !== 'Killed' || !result.minimized) {
  throw new Error(`fault control was not killed: ${JSON.stringify(result.status)}`);
}
const faulty = new ReferenceController(undefined, mutation);
const trace = record(faulty, result.minimized.inputs);
const replayed = replay(faulty, trace);
if (replayed.outcome !== 'exact' || !replayed.failure_reproduced) {
  throw new Error('minimized fault witness did not replay exactly');
}
console.log(`fault control: ${mutation}; target=${REFERENCE_MUTATION_TARGETS[mutation]}; witness steps=${result.minimized.inputs.length}; replay=${replayed.outcome}`);
console.log('finite model only; the independent requirement catalog awaits review and host liveness is not established');
