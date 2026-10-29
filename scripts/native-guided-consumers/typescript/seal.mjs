/** Admission only: this module never imports or runs a candidate engine. */
export const CORPUS_SEAL_PATH = 'conformance/guided-v2/corpus.json';

export function validatePublishedSeal(freeze, sourceCommit, corpusHash) {
  const valid = freeze !== null && typeof freeze === 'object' && !Array.isArray(freeze)
    && freeze.schema === 'stateless-guided-v2-freeze/1'
    && freeze.source_commit === sourceCommit
    && freeze.candidate_output_used === false
    && Number.isSafeInteger(freeze.revision) && freeze.revision > 0
    && Number.isSafeInteger(freeze.cases) && freeze.cases > 0
    && freeze.files !== null && typeof freeze.files === 'object' && !Array.isArray(freeze.files)
    && Object.hasOwn(freeze.files, CORPUS_SEAL_PATH)
    && freeze.files[CORPUS_SEAL_PATH] === corpusHash;
  if (!valid) throw new Error('FREEZE.json does not bind these corpus bytes and source authority');
}
