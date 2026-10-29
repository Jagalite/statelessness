import { accepted, check, metadata, NO_INPUT } from '../../dist/index.js';
export function createModel() { return { metadata: () => metadata('wide-worker','1'), initialState: () => 0,
    generate: () => NO_INPUT, step: s => accepted(s), checkState: () => [check('failure','failed','bounded worker failure')],
    cloneState: x => x, cloneInput: x => x, cloneOutput: x => x, equalInputs: (a,b) => a === b }; }
export function createFeedback() { return { metadata: () => ({name:'wide-worker',version:1,build:'1'}),
    cloneFeature: x => x, equalFeatures: (a,b) => a === b, transitionFeatures: () => {} }; }
export function encodeInput(x) { return x; }
