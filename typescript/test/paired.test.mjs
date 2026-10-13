import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { WithOracle, Paired, ObservationError, ModelError, metadata, accepted, disposition, check,
  enumerateStates, checkObserved, record, replay, parseTrace, stringifyTrace, DEFAULT_TRACE_LIMITS } from '../dist/index.js';

class Queue {
  constructor(mutation = 'none') { this.mutation = mutation; }
  metadata() { return metadata(`paired-queue-actual:${this.mutation}`, 'paired-fixture-v1'); }
  initialState() { return this.mutation === 'initial' ? [1] : []; }
  inputs() { return [0, 1, 2]; }
  cloneState(state) { return [...state]; }
  cloneInput(input) { return input; }
  cloneOutput(output) { return output; }
  equalStates(a, b) { return a.length === b.length && a.every((x, n) => x === b[n]); }
  step(state, input) {
    if (![0, 1, 2].includes(input)) throw new ModelError('invalid queue input');
    if (input < 2) {
      if (state.length === 2) return { state, outputs: [], disposition: disposition('rejected', 'full') };
      const next = [...state, this.mutation === 'state' ? 1 - input : input];
      let outputs = [String(10 + input), String(20 + next.length)];
      if (this.mutation === 'order') outputs.reverse();
      if (this.mutation === 'omit') outputs = outputs.slice(0, 1);
      return accepted(next, outputs);
    }
    return state.length ? accepted(state.slice(1), [String(30 + state[0]), String(19 + state.length)]) :
      { state, outputs: [], disposition: disposition('ignored', this.mutation === 'reason' ? 'empty!' : 'empty') };
  }
  checkState(state) { return [state.length <= 2 ? check('queue.bound') : check('queue.bound', 'failed', 'overfull')]; }
  checkTransition() { return [check('queue.transition')]; }
  encodeState(state) { const b = new Uint8Array(4 + state.length); new DataView(b.buffer).setUint32(0, state.length, true); b.set(state, 4); return b; }
  decodeState(bytes) {
    if (bytes.length < 4 || bytes.length > 6 || new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true) !== bytes.length - 4 || bytes.slice(4).some(x => x > 1)) throw new ModelError('invalid queue checkpoint');
    return Array.from(bytes.slice(4));
  }
  encodeInput(i) { return Uint8Array.of(i); }
  decodeInput(b) { if (b.length !== 1 || b[0] > 2) throw new ModelError('invalid input'); return b[0]; }
  encodeOutput(o) { return new TextEncoder().encode(o); }
}
class Reference {
  metadata() { return metadata('paired-queue-reference', 'paired-fixture-v1'); }
  initialState() { return 0; }
  cloneState(s) { return s; }
  cloneInput(i) { return i; }
  cloneOutput(o) { return o; }
  equalStates(a, b) { return a === b; }
  step(packed, operation) {
    const size = Number(packed !== 0) + Number(packed >= 3);
    if (operation === 2) return size ? accepted(Math.floor(packed / 3), [29 + packed % 3, 19 + size]) : { state: 0, outputs: [], disposition: disposition('ignored', 'empty') };
    if (![0, 1].includes(operation)) throw new ModelError('invalid reference input');
    return size === 2 ? { state: packed, outputs: [], disposition: disposition('rejected', 'full') } : accepted(packed + (operation + 1) * 3 ** size, [10 + operation, 21 + size]);
  }
  checkState(s) { return [[0, 1, 2, 4, 5, 7, 8].includes(s) ? check('reference.bound') : check('reference.bound', 'failed', 'invalid digits')]; }
  checkTransition() { return [check('reference.transition')]; }
  encodeState(s) { return Uint8Array.of(s); }
  decodeState(b) { if (b.length !== 1 || ![0, 1, 2, 4, 5, 7, 8].includes(b[0])) throw new ModelError('invalid reference checkpoint'); return b[0]; }
}
class Projection {
  metadata() { return metadata('paired-queue-projection', 'paired-fixture-v1'); }
  actualState(s) { return s; }
  referenceState(s) { const values = []; while (s) { values.push(s % 3 - 1); s = Math.floor(s / 3); } return values; }
  actualEffect(o) { return Number(o); }
  referenceEffect(o) { return o; }
  equalState(a, b) { return a.length === b.length && a.every((x, n) => x === b[n]); }
  equalEffect(a, b) { return a === b; }
}
const paired = (mutation = 'none') => new WithOracle(new Queue(mutation), new Paired(new Reference(), new Projection()));
test('equal actual state retains independently differing reference history', () => {
  class Actual {
    metadata() { return metadata('constant-actual'); }
    initialState() { return 0; }
    inputs() { return [0, 1]; }
    cloneState(s) { return s; }
    cloneInput(i) { return i; }
    cloneOutput(o) { return o; }
    equalStates(a, b) { return a === b; }
    step() { return accepted(0); }
    checkState() { return []; }
  }
  class HiddenReference extends Actual {
    metadata() { return metadata('hidden-reference'); }
    initialState() { return false; }
    step(state, input) { return accepted(input === 0 ? !state : state, state && input === 1 ? [7] : []); }
  }
  const projection = { metadata: () => metadata('hidden-projection'),
    actualState: () => 0, referenceState: () => 0,
    actualEffect: e => e, referenceEffect: e => e,
    equalState: (a, b) => a === b, equalEffect: (a, b) => a === b };
  const model = new WithOracle(new Actual(), new Paired(new HiddenReference(), projection));
  const report = enumerateStates(model);
  assert.deepEqual([report.termination, report.states, report.transitions, report.skipped_checks], ['failure_found', 2, 4, 0]);
  assert.deepEqual(report.failure.inputs, [0, 1]);
  assert.deepEqual(report.failure.violations.map(v => [v.phase, v.check.id]), [['transition', 'pair.effects']]);
  const bounded = enumerateStates(model, { maxStates: 10, maxTransitions: 3, maxDepth: 10 });
  assert.deepEqual([bounded.termination, bounded.states, bounded.transitions], ['transition_limit', 2, 3]);
});
const rows = readFileSync(new URL('../../conformance/paired-v1.tsv', import.meta.url), 'utf8').trim().split('\n').map(l => l.split('\t'));
for (const row of rows.slice(1)) test(`paired-v1 independent golden: ${row[0]}`, () => {
  const [name, mutation, states, edges, depth, termination, expectedStates, expectedEdges, failed, witness, phases, skipped] = row;
  const result = enumerateStates(paired(mutation), { maxStates: Number(states), maxTransitions: Number(edges), maxDepth: Number(depth) });
  assert.equal(result.termination, termination, name);
  assert.equal(result.states, Number(expectedStates)); assert.equal(result.transitions, Number(expectedEdges));
  assert.deepEqual(result.failure?.violations.map(v => v.check.id) ?? [], failed === '-' ? [] : failed.split(','));
  assert.deepEqual(result.failure?.inputs ?? [], witness === '-' ? [] : witness.split(',').map(Number));
  assert.deepEqual(result.failure?.violations.map(v => v.phase) ?? [], phases === '-' ? [] : phases.split(','));
  assert.equal(result.skipped_checks, Number(skipped));
  if (result.failure) {
    const trace = record(paired(mutation), result.failure.inputs);
    const batch = trace.steps.at(-1)?.checks ?? trace.initialChecks;
    const ids = ['queue.bound', 'reference.bound', 'pair.state',
      ...(trace.steps.length ? ['queue.transition', 'reference.transition', 'pair.effects', 'pair.outcome'] : [])];
    assert.deepEqual(batch.map(c => [c.id, c.status]), ids.map(id => [id, failed.split(',').includes(id) ? 'failed' : 'passed']));
  }
});
test('paired ordering, codec restart, fail-fast and exact replay', () => {
  const model = paired(), before = model.initialState(), transition = model.step(before, 1);
  assert.deepEqual(checkObserved(model, before, 1, transition, 1).map(c => c.id), ['queue.bound', 'reference.bound', 'pair.state', 'queue.transition', 'reference.transition', 'pair.effects', 'pair.outcome']);
  assert.deepEqual(model.step(model.decodeState(model.encodeState(transition.state)), 2), model.step(transition.state, 2));
  assert.equal(Buffer.from(model.encodeState(before)).toString('hex'), '4f524331040000000000000000000000010000000000000000');
  const faulty = paired('order'), trace = record(faulty, [0, 1, 2]);
  assert.equal(trace.steps.length, 1); assert.equal(trace.termination, 'property_failed');
  const result = replay(faulty, parseTrace(stringifyTrace(trace)));
  assert.equal(result.outcome, 'exact'); assert.equal(result.failure_reproduced, true);
  assert.equal(replay(model, trace, true).outcome, 'incompatible');
});
test('paired observation retains executed transition and never reruns actual', () => {
  const model = paired(), before = model.initialState(), actual = accepted([0], ['10', '21']);
  model.model.step = () => { throw new Error('must not run'); };
  assert.equal(model.observeTransition(before, 0, actual).state.oracle, 1);
  model.oracle.advance = () => { throw new Error('history failed'); };
  assert.throws(() => model.observeTransition(before, 0, actual), e => e instanceof ObservationError && JSON.stringify(e.transition) === JSON.stringify(actual));
});
test('paired malformed checkpoint, strict identity, limits and callback error', () => {
  const model = paired(), bytes = model.encodeState(model.initialState());
  for (const b of [new Uint8Array(), bytes.slice(0, -1), Uint8Array.from([...bytes, 0]), Uint8Array.from([79, 82, 67, 49, ...Array(8).fill(255)])]) assert.throws(() => model.decodeState(b), ModelError);
  const trace = record(model, [0]);
  model.oracle.projection.metadata = () => ({ ...metadata('paired-queue-projection', 'paired-fixture-v1'), properties_version: 2 });
  assert.equal(replay(model, trace, true).outcome, 'incompatible');
  assert.throws(() => record(paired(), [], 10, { ...DEFAULT_TRACE_LIMITS, maxBlobBytes: 4 }), ModelError);
  const bounded = record(paired(), [0, 1], 1); assert.equal(bounded.termination, 'step_limit');
  model.oracle.projection.actualEffect = () => { throw new Error('projection failed'); };
  assert.throws(() => enumerateStates(model), /projection failed/);
});
test('paired rejects malformed reference transitions before history or comparisons', () => {
  for (const invalid of [{ kind: 'invalid', reason: '' }, { kind: 'accepted', reason: 'invalid' }, { kind: 'ignored', reason: '\ud800' }]) {
    const model = paired(), before = model.initialState(), valid = model.step(before, 0);
    model.oracle.reference.step = () => ({ state: 1, outputs: [10, 21], disposition: invalid });
    assert.throws(() => model.step(before, 0), error => error instanceof ObservationError && /reference transition/.test(error.message));
    assert.throws(() => model.oracle.checkTransition(before, 0, valid), error => error instanceof ModelError && /reference transition/.test(error.message));
    assert.throws(() => enumerateStates(model), ModelError);
  }
});
test('exact paired replay rejects noncanonical components accepted by a permissive decoder', () => {
  const model = paired(), trace = record(model, []), bytes = new Uint8Array(trace.initialState);
  const decode = model.oracle.reference.decodeState.bind(model.oracle.reference);
  model.oracle.reference.decodeState = component => component.length === 1 && component[0] === 255 ? 0 : decode(component);
  bytes[bytes.length - 1] = 255;
  assert.deepEqual(model.decodeState(bytes), model.initialState());
  assert.throws(() => replay(model, { ...trace, initialState: bytes }), /not canonical/);
});
