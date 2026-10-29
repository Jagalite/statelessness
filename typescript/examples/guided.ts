import { accepted, check, DEFAULT_FUZZ, DEFAULT_GUIDANCE, StateFeedback, fuzzGuided, type GenerateModel } from '../src/index.js';

const counter: GenerateModel<number, number, never> = {
    initialState: () => 0,
    step: (state, input) => accepted(state + input),
    checkState: state => [check('limit', state < 8 ? 'passed' : 'failed', state < 8 ? '' : 'limit reached')],
    generate: (_state, rng) => Number(rng.index(2n)) + 1,
    isEnabled: (_state, input) => input > 0,
    equalInputs: (left, right) => left === right,
    cloneState: state => state,
    cloneInput: input => input,
    cloneOutput: output => output,
};
const feedback = new StateFeedback<number, number>(
    { name: 'counter-depth', version: 1, build: 'example-v1' },
    state => state,
    { cloneFeature: key => key, equalFeatures: (left, right) => left === right, hashFeature: key => key },
);
const report = fuzzGuided(counter, { ...DEFAULT_FUZZ, seed: 42n, cases: 20 }, DEFAULT_GUIDANCE, feedback);
console.log(report.termination, report.guidance.unique_features, report.failure?.inputs);
