import {
  accepted, check, enumerateStates, metadata, record, replay, parseTrace, stringifyTrace,
  type CodecModel, type EnumerateModel,
} from '@jagalite/statelessness';

interface State { readonly count: number }
// Clone explicitly; real applications may have their own classes/reference graphs.
const model: CodecModel<State, string, string> & EnumerateModel<State, string, string> = {
  initialState: () => ({ count: 0 }),
  step: (state, input) => {
    if (input !== 'increment') throw new Error('unknown input');
    return accepted({ count: state.count + 1 }, ['changed']);
  },
  checkState: state => [state.count > 2 ? check('counter.bound', 'failed', 'above two') : check('counter.bound')],
  cloneState: state => ({ ...state }), cloneInput: input => input, cloneOutput: output => output,
  inputs: () => ['increment'], equalStates: (a, b) => a.count === b.count,
  metadata: () => metadata('counter', 'example-v1'),
  encodeState: state => new TextEncoder().encode(String(state.count)),
  decodeState: bytes => ({ count: Number(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)) }),
  encodeInput: input => new TextEncoder().encode(input),
  decodeInput: bytes => new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes),
  encodeOutput: output => new TextEncoder().encode(output),
};
const result = enumerateStates(model);
if (result.failure?.inputs.length !== 3) throw new Error('expected three-step witness');
const trace = record(model, result.failure.inputs);
const replayed = replay(model, parseTrace(stringifyTrace(trace)));
if (replayed.outcome !== 'exact' || !replayed.failure_reproduced) throw new Error('failure did not replay');
console.log(result.termination, result.failure.inputs, replayed.outcome);
