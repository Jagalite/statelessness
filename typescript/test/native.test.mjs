import test from 'node:test';
import assert from 'node:assert/strict';
import { ModelError, ConfigError, Rng, metadata, accepted, check, disposition, checkObserved, enumerateStates, record, replay, parseTrace, stringifyTrace, DEFAULT_TRACE_LIMITS } from '../dist/index.js';
import { strictParse, traceFromData, traceToData } from '../dist/json.js';

class CounterState { constructor(value = 0) { this.value = value; } }
class Counter {
  initialState() { return new CounterState(); }
  cloneState(s) { return new CounterState(s.value); }
  cloneInput(i) { return i; }
  cloneOutput(o) { return o; }
  equalStates(a, b) { return a.value === b.value; }
  hashState() { return 0; }
  inputs() { return ['increment']; }
  step(state, input) {
    assert.ok(state instanceof CounterState, 'copy must retain application type');
    if (input !== 'increment') throw new Error('unknown input');
    return accepted(new CounterState(state.value + 1), ['changed']);
  }
  checkState(s) { return [s.value <= 2 ? check('bound') : check('bound', 'failed', 'above two')]; }
  metadata() { return metadata('counter', 'native-test-v1'); }
  encodeState(s) { return new TextEncoder().encode(String(s.value)); }
  decodeState(b) { return new CounterState(Number(new TextDecoder('utf-8', { fatal: true }).decode(b))); }
  encodeInput(i) { return new TextEncoder().encode(i); }
  decodeInput(b) { return new TextDecoder('utf-8', { fatal: true }).decode(b); }
  encodeOutput(o) { return new TextEncoder().encode(o); }
}
const config = { maxStates: 10, maxTransitions: 20, maxDepth: 10 };
test('application classes, collision chains, and failure witnesses', () => {
  const r = enumerateStates(new Counter(), config);
  assert.equal(r.termination, 'failure_found'); assert.equal(r.states, 3); assert.equal(r.transitions, 3);
  assert.deepEqual(r.failure.inputs, ['increment', 'increment', 'increment']); assert.equal(r.failure.violations[0].phase, 'state');
});
test('record, portable JSON roundtrip, replay', () => {
  const t = record(new Counter(), Array(5).fill('increment'));
  assert.equal(t.steps.length, 3); assert.equal(t.termination, 'property_failed');
  const decoded = parseTrace(stringifyTrace(t)); assert.deepEqual(decoded, t);
  assert.equal(replay(new Counter(), decoded).failure_reproduced, true);
});
test('observation does not invoke reducer', () => {
  const m = new Counter(); m.step = () => { throw new Error('must not execute'); };
  assert.deepEqual(checkObserved(m, new CounterState(), 'increment', accepted(new CounterState(1)), 1), [check('bound')]);
});
test('periodic and disabled checks remain visible', () => {
  const c = checkObserved(new Counter(), new CounterState(), 'increment', accepted(new CounterState(3)), 1, { stateEvery: 2, transitionChecks: false });
  assert.deepEqual(c.map(x => x.status), ['skipped', 'skipped']);
});
test('checker error discards partial batch', () => {
  const m = new Counter(); m.checkTransition = () => { throw new Error('broken checker'); };
  assert.throws(() => checkObserved(m, new CounterState(), 'increment', accepted(new CounterState(3)), 1), /transition check: broken checker/);
});
test('lazy iterator errors are model errors', () => {
  const m = new Counter(); m.inputs = function* () { yield 'increment'; throw new Error('domain failed'); };
  assert.throws(() => enumerateStates(m), /enumerate inputs: domain failed/);
});
test('depth boundary probes at most one candidate', () => {
  let calls = 0; const m = new Counter(); m.inputs = function* () { calls++; yield 'increment'; calls++; throw new Error('extra probe'); };
  const r = enumerateStates(m, { ...config, maxDepth: 0 }); assert.equal(r.termination, 'depth_bound'); assert.equal(calls, 1);
});
test('initial callback error does not produce a trace', () => {
  const m = new Counter(); m.initialState = () => { throw new Error('initial failed'); };
  assert.throws(() => record(m, []), ModelError);
});
test('later callback error retains only coherent prefix', () => {
  const t = record(new Counter(), ['increment', 'unknown']);
  assert.equal(t.termination, 'model_error'); assert.equal(t.steps.length, 1); assert.equal(replay(new Counter(), t).outcome, 'exact');
});
test('invalid codec results are model errors', () => {
  const m = new Counter(); m.encodeState = () => 'not bytes'; assert.throws(() => record(m, []), ModelError);
});
test('canonical initial bytes are enforced', () => {
  const t = { ...record(new Counter(), []), initialState: new TextEncoder().encode('00') };
  assert.throws(() => replay(new Counter(), t), /not canonical/);
});
test('canonical input bytes are enforced', () => {
  const m = new Counter(); m.decodeInput = b => new TextDecoder().decode(b).trim(); const t = record(m, ['increment']);
  const changed = { ...t, steps: [{ ...t.steps[0], input: new TextEncoder().encode('increment ') }] };
  assert.throws(() => replay(m, changed), /not canonical/);
});
test('replay never reconstructs the initial state', () => {
  const t = record(new Counter(), ['increment']); const m = new Counter(); m.initialState = () => { throw new Error('must not call'); };
  assert.equal(replay(m, t).outcome, 'exact');
});
test('only explicit build mismatches may be bypassed', () => {
  const t = record(new Counter(), []); const m = new Counter(); m.metadata = () => metadata('counter', 'changed');
  assert.equal(replay(m, t).outcome, 'incompatible'); assert.equal(replay(m, t, true).build_matches, false);
  m.metadata = () => ({ ...metadata('counter', 'changed'), model_version: 2 }); assert.equal(replay(m, t, true).outcome, 'incompatible');
});
test('duplicate failure IDs and order are retained', () => {
  const m = new Counter(); m.checkState = () => [check('same', 'failed', 'one'), check('same', 'failed', 'two')];
  assert.deepEqual(enumerateStates(m).failure.violations.map(v => v.check.details), ['one', 'two']);
});
test('mutable branches and caller-owned data stay independent', () => {
  const start = { items: [] };
  const m = { initialState: () => start, cloneState: s => ({ items: [...s.items] }), cloneInput: i => i, cloneOutput: o => o,
    inputs: s => s.items.length ? [] : ['a', 'b'], equalStates: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    step: (s, i) => { s.items.push(i); return accepted(s); }, checkState: s => [check('one', s.items.length > 1 ? 'failed' : 'passed')] };
  const r = enumerateStates(m); assert.equal(r.termination, 'graph_exhausted'); assert.equal(r.states, 3); assert.deepEqual(start, { items: [] });
});
test('checker mutations cannot change application state or observations', () => {
  const m = new Counter(); m.checkState = s => { s.value = 99; return []; };
  const t = accepted(new CounterState(1)); checkObserved(m, new CounterState(), 'increment', t, 1); assert.equal(t.state.value, 1);
});
test('codec buffers are owned copies including Node Buffer subclasses', () => {
  const m = new Counter(), shared = Buffer.alloc(1);
  m.encodeState = s => { shared[0] = 48 + s.value; return shared; };
  m.encodeOutput = () => { shared[0] = 120; return shared; };
  const t = record(m, ['increment', 'increment']); shared[0] = 255;
  assert.deepEqual([...t.initialState], [48]); assert.deepEqual([...t.steps[0].postState], [49]);
  assert.deepEqual([...t.steps[0].outputs[0]], [120]); assert.equal(replay(m, t).outcome, 'exact');
});
test('invalid numeric work limits are rejected', () => {
  for (const n of [true, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, 0]) assert.throws(() => enumerateStates(new Counter(), { ...config, maxStates: n }), ConfigError);
});
test('zero random domain consumes no state', () => {
  const a = new Rng(123n), b = new Rng(123n); assert.equal(a.index(0n), null); assert.equal(a.nextU64(), b.nextU64());
});
test('u64 bounds and vector', () => {
  for (const seed of [1, -1n, 1n << 64n]) assert.throws(() => new Rng(seed), ConfigError);
  assert.equal(new Rng(0n).nextU64(), 16294208416658607535n);
  assert.ok(new Rng((1n << 64n) - 1n).nextU64() < (1n << 64n));
});
test('native retention limits are enforced', () => {
  assert.equal(record(new Counter(), ['increment'], 10, { ...DEFAULT_TRACE_LIMITS, maxPayloadBytes: 1 }).termination, 'model_error');
  assert.throws(() => record(new Counter(), [], 10, { ...DEFAULT_TRACE_LIMITS, maxBlobBytes: 0 }), ModelError);
  assert.throws(() => record(new Counter(), [], 10, { ...DEFAULT_TRACE_LIMITS, maxItems: 0 }), ModelError);
});
test('JSON byte and aggregate limits', () => {
  const t = record(new Counter(), ['increment']); const raw = stringifyTrace(t);
  for (const limits of [{ maxJSONBytes: 1 }, { maxBlobBytes: 0 }, { maxItems: 1 }, { maxPayloadBytes: 1 }]) assert.throws(() => parseTrace(raw, { ...DEFAULT_TRACE_LIMITS, ...limits }));
});
test('malformed JSON and scalars are not silently normalized', () => {
  for (const s of ['{"x":1,"x":2}', '{"x":"\\ud800"}', '{"x":NaN}', '{"x":1.0}', '{} trailing', '\ufeff{}']) assert.throws(() => strictParse(s));
  assert.equal(strictParse('"\\ud83d\\ude00"'), '😀'); assert.throws(() => strictParse(new Uint8Array([255])));
});
test('false termination and continuation after failures are rejected', () => {
  const t = record(new Counter(), Array(4).fill('increment'));
  assert.throws(() => replay(new Counter(), { ...t, termination: 'completed' }), ModelError);
  assert.throws(() => replay(new Counter(), { ...t, steps: [...t.steps, t.steps[2]] }), ModelError);
});
test('reason strings and check details are exact Unicode scalars', () => {
  assert.notDeepEqual(disposition('rejected', 'é'), disposition('rejected', 'e\u0301'));
  const m = new Counter(); m.checkState = () => [check('p', 'failed', 'é')]; const t = record(m, []);
  m.checkState = () => [check('p', 'failed', 'e\u0301')]; const r = replay(m, t);
  assert.equal(r.outcome, 'diverged'); assert.equal(r.failure_reproduced, true);
});
test('zero and exact step limits distinguish completed from bounded prefix', () => {
  const m = new Counter(); assert.equal(record(m, [], 0).termination, 'completed'); assert.equal(record(m, ['increment'], 0).termination, 'step_limit');
  assert.equal(record(m, ['increment'], 1).termination, 'completed'); assert.equal(record(m, ['increment', 'increment'], 1).termination, 'step_limit');
});
test('invalid checks do not become passing observations', () => {
  const m = new Counter(); m.checkState = () => [{ id: 'x', status: 'passed', details: 'wrong' }]; assert.throws(() => enumerateStates(m), ModelError);
});

test('all trace entry points reject invalid custom limits before touching input', () => {
  const trace = record(new Counter(), []), raw = stringifyTrace(trace), data = traceToData(trace);
  for (const key of Object.keys(DEFAULT_TRACE_LIMITS)) {
    for (const value of [NaN, Infinity, -Infinity, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, undefined, true]) {
      const limits = { ...DEFAULT_TRACE_LIMITS, [key]: value };
      for (const run of [() => parseTrace(raw, limits), () => stringifyTrace(trace, limits), () => traceFromData(data, limits), () => record(new Counter(), [], 10, limits)]) assert.throws(run, ConfigError, `${key}=${value}`);
      assert.throws(() => parseTrace('invalid JSON', limits), ConfigError);
      assert.throws(() => stringifyTrace(null, limits), ConfigError);
    }
  }
});
test('zero and exact custom trace limits remain valid', () => {
  const trace = record(new Counter(), []), raw = stringifyTrace(trace);
  const limits = { maxSteps: 0, maxBlobBytes: 1, maxItems: 1, maxPayloadBytes: 1, maxJSONBytes: new TextEncoder().encode(raw).length };
  assert.deepEqual(parseTrace(raw, limits), trace);
  assert.equal(stringifyTrace(trace, limits), raw);
});
