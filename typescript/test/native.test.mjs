import test from 'node:test';
import assert from 'node:assert/strict';
import { ModelError, ConfigError, Rng, metadata, accepted, check, disposition, checkObserved, enumerateStates, record, replay, parseTrace, stringifyTrace, DEFAULT_TRACE_LIMITS } from '../dist/index.js';
import { strictParse, traceFromData, traceToData } from '../dist/json.js';

test('traceFromData validates indexed entries without invoking array map overrides', () => {
  class HostileArray extends Array {
    map() { return []; }
  }
  const data = traceToData(record(new Counter(), ['increment']));
  data.initial_checks = HostileArray.from([{ id: 'bad', status: 'not-a-status' }]);
  assert.throws(() => traceFromData(data));
  const valid = traceToData(record(new Counter(), ['increment']));
  valid.steps = HostileArray.from(valid.steps);
  assert.equal(traceFromData(valid).steps.length, 1);
  valid.steps[0].outputs = HostileArray.from(['not-hex']);
  assert.throws(() => traceFromData(valid));
});

test('traceFromData validates and uses one captured array length', () => {
  const changingLength = (array, first, later) => {
    let reads = 0;
    return [new Proxy(array, { get(target, key, receiver) {
      return key === 'length' ? (reads++ === 0 ? first : later) : Reflect.get(target, key, receiver);
    } }), () => reads];
  };
  const data = traceToData(record(new Counter(), ['increment']));
  const [steps, stepReads] = changingLength(data.steps, 0, 1);
  data.steps = steps;
  assert.equal(traceFromData(data, { ...DEFAULT_TRACE_LIMITS, maxSteps: 0 }).steps.length, 0);
  assert.equal(stepReads(), 1);

  const valid = traceToData(record(new Counter(), ['increment']));
  const [checks, checkReads] = changingLength(valid.initial_checks, 0, 1);
  const [outputs, outputReads] = changingLength(valid.steps[0].outputs, 0, 1);
  valid.initial_checks = checks;
  valid.steps[0].outputs = outputs;
  const decoded = traceFromData(valid);
  assert.equal(decoded.initialChecks.length, 0);
  assert.equal(decoded.steps[0].outputs.length, 0);
  assert.equal(checkReads(), 1);
  assert.equal(outputReads(), 1);

  for (const length of [-1, 0.5, NaN, '1']) {
    const malformed = traceToData(record(new Counter(), []));
    [malformed.steps] = changingLength(malformed.steps, length, 0);
    assert.throws(() => traceFromData(malformed), /bounded array/);
  }
});

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
test('oversized JSON strings are rejected before UTF-8 allocation', () => {
  const OriginalEncoder = globalThis.TextEncoder;
  let calls = 0;
  globalThis.TextEncoder = class extends OriginalEncoder {
    encode(...args) { calls++; return super.encode(...args); }
  };
  try {
    assert.throws(() => strictParse('"123456"', 4));
    assert.equal(calls, 0);
  } finally {
    globalThis.TextEncoder = OriginalEncoder;
  }
  assert.equal(strictParse('"é"', 4), 'é');
  assert.throws(() => strictParse('"é"', 3));
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
test('trace encoding rejects oversized payload and text before hex expansion', () => {
  const trace = record(new Counter(), []);
  let iterations = 0;
  class ObservedBytes extends Uint8Array {
    [Symbol.iterator]() { iterations++; throw new Error('hex expansion ran'); }
  }
  const oversized = new ObservedBytes(32);
  assert.throws(() => stringifyTrace({ ...trace, initialState: oversized }, { ...DEFAULT_TRACE_LIMITS, maxBlobBytes: 4 }), /invalid hexadecimal bytes/);
  assert.equal(iterations, 0);
  assert.throws(() => stringifyTrace({ ...trace, metadata: { ...trace.metadata, name: 'é'.repeat(100) }, initialState: oversized }, { ...DEFAULT_TRACE_LIMITS, maxJSONBytes: 50 }), /trace JSON limit/);
  assert.equal(iterations, 0);
  const accepted = stringifyTrace({ ...trace, initialState: new ObservedBytes([7]) });
  assert.equal(parseTrace(accepted).initialState[0], 7);
  assert.equal(iterations, 0);
  const raw = stringifyTrace(trace);
  const exact = new TextEncoder().encode(raw).length;
  assert.equal(stringifyTrace(trace, { ...DEFAULT_TRACE_LIMITS, maxJSONBytes: exact }), raw);
  assert.throws(() => stringifyTrace(trace, { ...DEFAULT_TRACE_LIMITS, maxJSONBytes: exact - 1 }), /trace JSON limit/);
});
test('trace encoding ignores collection overrides and object toJSON hooks', () => {
  const trace = record(new Counter(), ['increment']);
  let called = 0;
  class HostileArray extends Array {
    map() { throw new Error('map override'); }
    [Symbol.iterator]() { throw new Error('iterator override'); }
    static get [Symbol.species]() { throw new Error('species override'); }
  }
  const metadata = { ...trace.metadata };
  Object.defineProperty(metadata, 'toJSON', { value() { called++; return 'x'.repeat(100_000); } });
  const checks = new HostileArray(...trace.initialChecks.map(c => {
    const copy = { ...c };
    Object.defineProperty(copy, 'toJSON', { value() { called++; return 'x'.repeat(100_000); } });
    return copy;
  }));
  const step = { ...trace.steps[0], outputs: new HostileArray(...trace.steps[0].outputs), checks: new HostileArray(...trace.steps[0].checks) };
  const disposition = { ...step.disposition };
  Object.defineProperty(disposition, 'toJSON', { value() { called++; return 'x'.repeat(100_000); } });
  step.disposition = disposition;
  const raw = stringifyTrace({ ...trace, metadata, initialChecks: checks, steps: new HostileArray(step) });
  assert.deepEqual(parseTrace(raw), trace);
  assert.equal(called, 0);
});
test('trace encoding retains accepted omitted optional record fields', () => {
  const trace = record(new Counter(), ['increment']);
  const initialChecks = trace.initialChecks.map(({ id, status }) => ({ status, id }));
  const steps = trace.steps.map(s => ({ ...s, disposition: { kind: s.disposition.kind }, checks: s.checks.map(({ id, status }) => ({ id, status })) }));
  const metadata = { build: trace.metadata.build, codec_version: trace.metadata.codec_version, properties_version: trace.metadata.properties_version, model_version: trace.metadata.model_version, name: trace.metadata.name };
  const input = { ...trace, metadata, initialChecks, steps };
  const encoded = stringifyTrace(input);
  assert.equal(encoded, JSON.stringify(traceToData(input)));
  const data = JSON.parse(encoded);
  assert.equal(Object.hasOwn(data.initial_checks[0], 'details'), false);
  assert.equal(Object.hasOwn(data.steps[0].disposition, 'reason'), false);
});
test('trace encoding rejects malformed root before expanding its payload', () => {
  const trace = record(new Counter(), []);
  assert.throws(() => stringifyTrace({ ...trace, initialChecks: {}, initialState: new Uint8Array(128) }, { ...DEFAULT_TRACE_LIMITS, maxBlobBytes: 1 }), /expected bounded array/);
});
