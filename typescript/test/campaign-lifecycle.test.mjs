import test from 'node:test';
import assert from 'node:assert/strict';
import {
  accepted, ModelError, enumerateStates, fuzz, shrink, recordAudit, writeAuditTrace, readAuditTrace, replay,
} from '../dist/index.js';
import { Lifecycle } from '../dist/campaign_lifecycle.js';
import {
  AuditedLifecycle, LifecycleCodec, NativeStateRepresentationError, U64_MAX, OUTCOMES, coreChecks,
  claim, complete, receive, sink, worker, finish,
} from './fixtures/campaign-lifecycle.mjs';

const clean = checks => assert.deepEqual(checks.filter(c => c.status === 'failed'), []);
const failed = (checks, id) => assert.ok(checks.some(c => c.id === id && c.status === 'failed'), id);
const hex = bytes => Buffer.from(bytes).toString('hex');
const bytes = value => Uint8Array.from(Buffer.from(value, 'hex'));
const core = (jobs = 2, stopOnFailure = false, firstJob = 50n) => new Lifecycle(firstJob, jobs, stopOnFailure);
const reportCounts = ['requested', 'started', 'finished', 'notStarted', 'delivered', 'failures', 'errors'];
// Build protocol DTOs without constructing a safe-number runtime.
const initialWireState = jobs => ({
  report: {
    requested: jobs, started: 0n, finished: 0n, notStarted: jobs, delivered: 0n, failures: 0n, errors: 0n,
    reportedTransitions: 0n, reportedCases: 0n, reportedSkippedChecks: 0n,
    accountingComplete: true, termination: { kind: 'JobsCompleted' },
  },
  stop: null, pending: new Map(), delivering: null, lostWorker: false, finalized: false,
});

function apply(m, input, expected = []) {
  const before = m.cloneState(m.state), snapshot = m.step(before, input), result = m.apply(input);
  assert.equal(m.lastAccepted, true, `${input.kind}: ${m.lastRejection}`);
  assert.equal(m.lastRejection, null);
  assert.equal(snapshot.disposition.kind, 'accepted');
  assert.deepEqual(result, expected);
  assert.deepEqual(m.state, snapshot.state);
  clean(coreChecks(m.state, m.firstJob, m.jobs, m.stopOnFailure));
  return result;
}
function reject(m, input, reason) {
  const before = m.cloneState(m.state), identity = m.state;
  const snapshot = m.step(before, input);
  assert.deepEqual(m.apply(input), []);
  assert.equal(m.lastAccepted, false);
  assert.equal(m.state, identity);
  assert.deepEqual(m.state, before);
  assert.deepEqual(snapshot.state, before);
  assert.equal(snapshot.disposition.kind, 'rejected');
  assert.equal(m.lastRejection, snapshot.disposition.reason);
  if (reason !== undefined) assert.equal(m.lastRejection, reason);
}
function acceptedPrefix(model, inputs) {
  let state = model.initialState();
  for (const input of inputs) {
    const turn = model.step(state, input);
    assert.equal(turn.disposition.kind, 'accepted');
    clean(model.checkState(turn.state)); clean(model.checkTransition(state, input, turn)); state = turn.state;
  }
  return state;
}

test('lifecycle constructor preserves exact IDs and rejects unrepresentable ranges', () => {
  for (const [first, jobs] of [[-1n, 1], [U64_MAX + 1n, 1], [U64_MAX, 2], [0n, 0], [0n, 1.5], [0n, Number.MAX_SAFE_INTEGER + 1]]) {
    assert.throws(() => core(jobs, false, first), /invalid campaign lifecycle job range/);
  }
  const m = core(1, false, U64_MAX);
  assert.equal(m.lastAccepted, null); assert.equal(m.lastRejection, null);
  apply(m, claim(), [{ kind: 'RunJob', jobId: U64_MAX }]);
  reject(m, claim('Cancelled'));
  apply(m, complete(U64_MAX)); apply(m, receive(U64_MAX), [{ kind: 'Deliver', jobId: U64_MAX }]);
  apply(m, sink(U64_MAX)); apply(m, finish());
  assert.equal(m.state.report.termination.kind, 'JobsCompleted');
});

test('duplicate/stale/out-of-range events reject inertly and finalization is terminal', () => {
  const m = core(1);
  for (const input of [complete(50n), receive(50n), sink(50n), finish()]) reject(m, input);
  apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  reject(m, claim()); reject(m, complete(51n)); reject(m, receive(50n));
  for (const input of [complete(50n, 'FactoryError', { failure: true }), complete(50n, 'Panicked', { transitions: 1n }),
    complete(50n, 'Failure', { failure: false }), complete(50n, 'Completed', { failure: true }),
    complete(50n, 'Completed', { cases: -1n }), complete(50n, 'Completed', { skippedChecks: U64_MAX + 1n })]) {
    reject(m, input, 'inconsistent job summary');
  }
  // Summary validation precedes identity validation.
  reject(m, complete(999n, 'ModelError', { cases: 1n }), 'inconsistent job summary');
  apply(m, complete(50n)); reject(m, complete(50n, 'Panicked'));
  assert.equal(m.state.report.finished, 0);
  apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]);
  assert.equal(m.state.report.finished, 1); assert.equal(m.state.report.delivered, 0);
  reject(m, receive(50n)); reject(m, sink(51n)); reject(m, finish());
  apply(m, sink(50n)); reject(m, sink(50n)); reject(m, sink(50n, 'stale error'));
  apply(m, finish());
  for (const input of new AuditedLifecycle({ jobs: 1 }).alphabet()) reject(m, input, 'campaign is finalized');
  assert.deepEqual([m.state.report.started, m.state.report.finished, m.state.report.delivered], [1, 1, 1]);
});

test('late Claim cannot observe controls after exhaustion or a previous stop', () => {
  for (const stop of ['Cancelled', 'Deadline']) {
    const preflight = core(1); apply(preflight, claim(stop)); apply(preflight, finish());
    assert.equal(preflight.state.report.started, 0); assert.equal(preflight.state.report.termination.kind, stop);
    const exhausted = core(1); apply(exhausted, claim(), [{ kind: 'RunJob', jobId: 50n }]);
    reject(exhausted, claim(stop)); apply(exhausted, complete(50n));
    apply(exhausted, receive(50n), [{ kind: 'Deliver', jobId: 50n }]); apply(exhausted, sink(50n));
    reject(exhausted, claim(stop)); apply(exhausted, finish());
    assert.equal(exhausted.state.report.termination.kind, 'JobsCompleted');
  }
  for (const outcome of ['Failure', 'Deadline']) {
    const m = core(3, true);
    apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]); apply(m, complete(50n, outcome));
    reject(m, claim('Cancelled')); assert.equal(m.state.stop, outcome);
    apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]); apply(m, sink(50n)); apply(m, finish());
    assert.equal(m.state.report.termination.kind, outcome === 'Failure' ? 'FailureFound' : 'Deadline');
  }
});

test('active completion stop priority survives every order without stopping already claimed jobs', () => {
  for (const order of [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]]) {
    const m = core(4, true), outcomes = ['Failure', 'Deadline', 'Cancelled'];
    for (let n = 0; n < 3; n++) apply(m, claim(), [{ kind: 'RunJob', jobId: 50n + BigInt(n) }]);
    for (const n of order) { apply(m, complete(50n + BigInt(n), outcomes[n])); reject(m, claim()); }
    assert.equal(m.state.report.failures, 0); // Receipt, not completion, accounts.
    for (const id of [52n, 50n, 51n]) {
      apply(m, receive(id), [{ kind: 'Deliver', jobId: id }]); apply(m, sink(id));
    }
    apply(m, finish()); assert.equal(m.state.report.termination.kind, 'Cancelled');
    assert.deepEqual([m.state.report.started, m.state.report.finished, m.state.report.notStarted], [3, 3, 1]);
    assert.equal(m.state.report.failures, 1);
  }
});

test('first sink error survives drained work, ordinary stops and replacement worker failures', () => {
  for (const workerFirst of [false, true]) for (const message of ['', 'first sink error']) {
    const m = core(3);
    for (const id of [50n, 51n]) apply(m, claim(), [{ kind: 'RunJob', jobId: id }]);
    apply(m, complete(50n)); apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]);
    if (workerFirst) apply(m, worker('early worker'));
    apply(m, sink(50n, message)); reject(m, sink(50n, 'replacement'));
    apply(m, worker('late worker')); apply(m, complete(51n, 'Cancelled'));
    reject(m, claim()); apply(m, receive(51n), [{ kind: 'Discard', jobId: 51n }]); reject(m, sink(51n));
    apply(m, finish());
    assert.deepEqual(m.state.report.termination, { kind: 'CallbackError', error: new ModelError(message) });
    assert.deepEqual([m.state.report.finished, m.state.report.delivered, m.state.report.errors], [2, 0, 0]);
    assert.equal(m.state.report.reportedTransitions, 4n); assert.equal(m.state.report.accountingComplete, true);
  }
});

test('worker errors alone permit delivery; only explicit lost work permits missing outcomes at Finish', () => {
  const m = core(2); apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  apply(m, worker('spawn')); reject(m, finish());
  apply(m, complete(50n)); apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]);
  apply(m, worker('join', true)); reject(m, finish()); // An active sink is never waived.
  apply(m, sink(50n)); apply(m, worker('last', false)); apply(m, finish());
  assert.equal(m.state.report.termination.error.message, 'last');
  assert.equal(m.state.report.delivered, 1); assert.equal(m.state.report.errors, 0);
  assert.equal(m.state.report.accountingComplete, false); assert.equal(m.state.lostWorker, true);
  const missing = core(2); apply(missing, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  apply(missing, worker('lost', true)); apply(missing, finish());
  assert.deepEqual([missing.state.report.started, missing.state.report.finished, missing.state.report.notStarted], [1, 0, 1]);
  assert.equal(missing.state.pending.size, 1); assert.equal(missing.state.report.accountingComplete, false);
});

test('only one Ready report can enter the sink and its exact acknowledgement releases delivery', () => {
  const m = core(2);
  for (const id of [50n, 51n]) apply(m, claim(), [{ kind: 'RunJob', jobId: id }]);
  apply(m, complete(51n)); apply(m, complete(50n));
  apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]);
  reject(m, receive(51n), 'sink is already delivering a report');
  reject(m, sink(51n, 'wrong job'), 'job is not awaiting a sink result');
  apply(m, sink(50n)); apply(m, receive(51n), [{ kind: 'Deliver', jobId: 51n }]);
  reject(m, sink(50n, 'stale error')); apply(m, sink(51n)); apply(m, finish());
  assert.equal(m.state.report.delivered, 2);
});

test('job errors do not stop claims; known zero work and hidden work remain distinct', () => {
  for (const outcome of ['FactoryError', 'ModelError', 'Panicked', 'Failure']) {
    const m = core(2, outcome !== 'Failure');
    for (const id of [50n, 51n]) {
      apply(m, claim(), [{ kind: 'RunJob', jobId: id }]); apply(m, complete(id, outcome));
      apply(m, receive(id), [{ kind: 'Deliver', jobId: id }]); apply(m, sink(id));
    }
    apply(m, finish()); assert.equal(m.state.report.termination.kind, 'JobsCompleted');
    assert.equal(m.state.report.errors, outcome === 'Failure' ? 0 : 2);
    assert.equal(m.state.report.failures, outcome === 'Failure' ? 2 : 0);
    assert.equal(m.state.report.accountingComplete, ['FactoryError', 'Failure'].includes(outcome));
  }
});

test('each u64 work total saturates only on overflow and loss remains sticky', () => {
  for (const [field, reportField] of [['transitions', 'reportedTransitions'], ['cases', 'reportedCases'], ['skippedChecks', 'reportedSkippedChecks']]) {
    const m = core(3);
    for (const [id, count] of [[50n, U64_MAX], [51n, 1n], [52n, 0n]]) {
      apply(m, claim(), [{ kind: 'RunJob', jobId: id }]);
      apply(m, complete(id, 'Completed', { transitions: 0n, cases: 0n, skippedChecks: 0n, [field]: count }));
      apply(m, receive(id), [{ kind: 'Deliver', jobId: id }]); apply(m, sink(id));
      assert.equal(m.state.report[reportField], U64_MAX);
      assert.equal(m.state.report.accountingComplete, id === 50n);
    }
    apply(m, finish());
  }
});

test('completion summaries and error messages are owned; snapshot stepping does not mutate live state', () => {
  const m = core(1), original = m.cloneState(m.state), t = m.step(m.state, claim());
  assert.deepEqual(m.state, original); assert.equal(m.lastAccepted, null);
  t.state.pending.clear(); t.state.report.started = 99; assert.deepEqual(m.state, original);
  apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]); const input = complete(50n);
  apply(m, input); input.summary.transitions = 999n;
  apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]); assert.equal(m.state.report.reportedTransitions, 2n);
  const ack = sink(50n, 'original'); apply(m, ack); ack.error.message = 'changed';
  apply(m, finish()); assert.equal(m.state.report.termination.error.message, 'original');
});

test('canonical lifecycle input/output byte vectors are independently specified', () => {
  const codec = new LifecycleCodec();
  const vectors = [
    [claim(), '0000'], [claim('Cancelled'), '0001'], [claim('Deadline'), '0002'],
    [receive(U64_MAX), '02ffffffffffffffff'], [sink(0n), '03000000000000000000'],
    [sink(0n, ''), '030000000000000000010000000000000000'],
    [sink(50n, '\0é'), '03320000000000000001030000000000000000c3a9'],
    [worker('worker\n', true), '040700000000000000776f726b65720a01'],
    [worker('', false), '04000000000000000000'], [finish(), '05'],
  ];
  for (const [outcome, tag, flag, work] of [
    ['Completed', '00', '00', true], ['Failure', '01', '01', true], ['Cancelled', '02', '00', true],
    ['Deadline', '03', '00', true], ['FactoryError', '04', '00', false], ['ModelError', '05', '00', false], ['Panicked', '06', '00', false],
  ]) vectors.push([complete(50n, outcome), '013200000000000000' + tag + flag
    + (work ? '020000000000000001000000000000000100000000000000' : '000000000000000000000000000000000000000000000000')]);
  for (const [input, expected] of vectors) {
    assert.equal(hex(codec.encodeInput(input)), expected);
    assert.deepEqual(codec.decodeInput(bytes(expected)), input);
  }
  for (const [kind, tag] of [['RunJob', '00'], ['Deliver', '01'], ['Discard', '02']]) {
    assert.equal(hex(codec.encodeOutput({ kind, jobId: U64_MAX })), tag + 'ffffffffffffffff');
  }
  const large = complete(U64_MAX, 'Completed', { transitions: U64_MAX, cases: U64_MAX, skippedChecks: U64_MAX });
  assert.equal(hex(codec.encodeInput(large)), '01ffffffffffffffff0000' + 'ffffffffffffffff'.repeat(3));
});

test('canonical initial/running/ready state bytes pin count order, flags and pending layout', () => {
  const codec = new LifecycleCodec(), m = core();
  // Seven report counts and three work totals, each literal is eight LE bytes.
  const initial = ['0200000000000000', '0000000000000000', '0000000000000000', '0200000000000000',
    '0000000000000000', '0000000000000000', '0000000000000000', '0000000000000000',
    '0000000000000000', '0000000000000000', '010000', '0000000000000000', '000000'].join('');
  assert.equal(hex(codec.encodeState(m.state)), initial); assert.deepEqual(codec.decodeState(bytes(initial)), m.state);
  apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  const running = ['0200000000000000', '0100000000000000', '0000000000000000', '0100000000000000',
    '0000000000000000', '0000000000000000', '0000000000000000', '0000000000000000',
    '0000000000000000', '0000000000000000', '010000', '0100000000000000', '3200000000000000', '00', '000000'].join('');
  assert.equal(hex(codec.encodeState(m.state)), running); assert.deepEqual(codec.decodeState(bytes(running)), m.state);
  apply(m, complete(50n));
  const ready = running.slice(0, -8) + '01' + '0000020000000000000001000000000000000100000000000000' + '000000';
  assert.equal(hex(codec.encodeState(m.state)), ready); assert.deepEqual(codec.decodeState(bytes(ready)), m.state);
});

test('full-u64 wire initial states roundtrip exact bytes independently of native count limits', () => {
  const zero = '0000000000000000';
  for (const [jobs, count] of [[9007199254740992n, '0000000000002000'],
    [9007199254740993n, '0100000000002000'], [U64_MAX, 'ffffffffffffffff']]) {
    const codec = new LifecycleCodec(0n, jobs), state = initialWireState(jobs);
    // requested/notStarted are large scalars; the map is empty and the wire is 94 bytes.
    const expected = [count, zero, zero, count, zero, zero, zero, zero, zero, zero,
      '010000', zero, '000000'].join('');
    const encoded = codec.encodeWireState(state), decoded = codec.decodeWireState(bytes(expected));
    assert.equal(encoded.length, 94); assert.equal(hex(encoded), expected);
    assert.deepEqual(decoded, state); assert.equal(decoded.pending.size, 0);
    for (const key of reportCounts) assert.equal(typeof decoded.report[key], 'bigint');
    assert.deepEqual(codec.encodeWireState(decoded), encoded);
    assert.throws(() => codec.toNativeState(decoded), NativeStateRepresentationError);
    assert.throws(() => codec.decodeState(encoded), {
      name: 'NativeStateRepresentationError', message: /native representability limitation: report.requested/,
    });
    // All prefixes and trailing data remain malformed even at the full count limit.
    for (let n = 0; n < encoded.length; n++) assert.throws(() => codec.decodeWireState(encoded.subarray(0, n)), ModelError);
    assert.throws(() => codec.decodeWireState(Uint8Array.from([...encoded, 0])), /invalid campaign lifecycle encoding/);
  }
});

test('all seven wire counts preserve full-u64 values, including exact accounting deficits', () => {
  for (const jobs of [9007199254740992n, U64_MAX]) for (const field of ['failures', 'errors']) {
    const codec = new LifecycleCodec(0n, jobs), state = initialWireState(jobs);
    Object.assign(state.report, { started: jobs, finished: jobs, notStarted: 0n, delivered: jobs,
      [field]: jobs, reportedTransitions: U64_MAX, reportedCases: U64_MAX, reportedSkippedChecks: U64_MAX });
    state.finalized = true;
    const encoded = codec.encodeWireState(state), decoded = codec.decodeWireState(encoded);
    assert.deepEqual(decoded, state); assert.deepEqual(codec.encodeWireState(decoded), encoded);
    // A total at u64::MAX alone is not evidence of overflow or lost work.
    assert.equal(decoded.report.accountingComplete, true);
    assert.throws(() => codec.toNativeState(decoded), NativeStateRepresentationError);
  }
  const codec = new LifecycleCodec(1n, U64_MAX), pending = initialWireState(U64_MAX);
  Object.assign(pending.report, { started: U64_MAX, finished: U64_MAX - 1n, notStarted: 0n, delivered: U64_MAX - 1n });
  pending.pending.set(U64_MAX, { kind: 'Ready', summary: complete(U64_MAX, 'Completed', { transitions: U64_MAX }).summary });
  assert.deepEqual(codec.decodeWireState(codec.encodeWireState(pending)), pending);
  const delivering = { ...pending, pending: new Map(), delivering: U64_MAX,
    report: { ...pending.report, finished: U64_MAX } };
  assert.deepEqual(codec.decodeWireState(codec.encodeWireState(delivering)), delivering);
  const outside = { ...pending, pending: new Map([[0n, { kind: 'Running' }]]) };
  assert.throws(() => codec.decodeWireState(codec.encodeWireState(outside)), /properties/);
  assert.throws(() => new LifecycleCodec(0n, U64_MAX).decodeWireState(codec.encodeWireState(pending)), /properties/);
});

test('native state adapters explicitly preserve safe counts and reject narrowing', () => {
  const jobs = Number.MAX_SAFE_INTEGER, codec = new LifecycleCodec(0n, jobs), native = core(jobs, false, 0n).state;
  const wire = codec.fromNativeState(native);
  assert.deepEqual(wire, initialWireState(BigInt(jobs)));
  assert.deepEqual(codec.toNativeState(wire), native);
  assert.deepEqual(codec.encodeState(native), codec.encodeWireState(wire));
  assert.deepEqual(codec.decodeState(codec.encodeWireState(wire)), native);
  // Conversion checks representation, not history or lifecycle semantics.
  for (const field of reportCounts) {
    const boundary = { ...native, report: { ...native.report, [field]: jobs } };
    assert.equal(codec.fromNativeState(boundary).report[field], BigInt(jobs));
    assert.deepEqual(codec.toNativeState(codec.fromNativeState(boundary)), boundary);
    const tooLarge = { ...wire, report: { ...wire.report, [field]: 9007199254740992n } };
    assert.throws(() => codec.toNativeState(tooLarge), NativeStateRepresentationError);
    for (const count of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, NaN, Infinity]) {
      const invalidNative = { ...native, report: { ...native.report, [field]: count } };
      assert.throws(() => codec.fromNativeState(invalidNative), NativeStateRepresentationError);
      assert.throws(() => codec.encodeState(invalidNative), NativeStateRepresentationError);
    }
  }
  wire.report.requested = 0n; wire.pending.set(0n, { kind: 'Running' });
  assert.equal(native.report.requested, jobs); assert.equal(native.pending.size, 0);
  const source = initialWireState(1n), converted = codec.toNativeState(source);
  converted.report.requested = 9; converted.pending.set(0n, { kind: 'Running' });
  assert.equal(source.report.requested, 1n); assert.equal(source.pending.size, 0);
  assert.throws(() => new LifecycleCodec(0n, Number.MAX_SAFE_INTEGER + 1), NativeStateRepresentationError);
});

test('full-u64 wire codec rejects overflow, precise invariant violations and hostile lengths', () => {
  const codec = new LifecycleCodec(0n, U64_MAX), initial = initialWireState(U64_MAX);
  for (const field of reportCounts) for (const count of [-1n, U64_MAX + 1n, 0, Number.MAX_SAFE_INTEGER + 1]) {
    const invalidState = { ...initial, report: { ...initial.report, [field]: count } };
    assert.throws(() => codec.encodeWireState(invalidState), /invalid campaign lifecycle encoding/);
    assert.throws(() => codec.toNativeState(invalidState), /invalid campaign lifecycle encoding/);
  }
  const terminal = { ...initial, finalized: true, report: { ...initial.report,
    started: U64_MAX, finished: U64_MAX, notStarted: 0n, delivered: U64_MAX, failures: U64_MAX } };
  for (const report of [
    { ...initial.report, notStarted: U64_MAX - 1n },
    { ...initial.report, finished: 1n }, // finished > started
    { ...initial.report, delivered: 1n }, // delivered > finished
    { ...initial.report, started: 1n, notStarted: U64_MAX - 1n }, // missing pending identity
    { ...terminal.report, errors: 1n }, // failures + errors overflows u64; do not saturate
    { ...terminal.report, delivered: U64_MAX - 1n }, // missing active sink despite a one-unit deficit
    { ...terminal.report, started: U64_MAX - 1n, notStarted: 1n }, // finished > started near the limit
  ]) assert.throws(() => codec.decodeWireState(codec.encodeWireState({ ...initial, report })), /properties/);
  const precise = new LifecycleCodec(0n, 9007199254740992n);
  assert.throws(() => precise.decodeWireState(precise.encodeWireState(initialWireState(9007199254740993n))), /properties/);
  // Exact u64 boundary without overflow remains valid.
  terminal.report.failures -= 1n; terminal.report.errors = 1n;
  assert.deepEqual(codec.decodeWireState(codec.encodeWireState(terminal)), terminal);
  for (const count of [1n, 9007199254740992n, U64_MAX]) {
    const hostile = codec.encodeWireState(initial);
    new DataView(hostile.buffer).setBigUint64(83, count, true);
    assert.throws(() => codec.decodeWireState(hostile), /invalid campaign lifecycle encoding/);
  }
  const workerError = { ...initial, stop: 'Worker', report: { ...initial.report,
    termination: { kind: 'WorkerError', error: new ModelError('x') } } };
  const invalidUtf8 = codec.encodeWireState(workerError); invalidUtf8[90] = 255;
  assert.throws(() => codec.decodeWireState(invalidUtf8), /invalid campaign lifecycle encoding/);
  const hostileText = codec.encodeWireState(workerError); hostileText.fill(255, 82, 90);
  assert.throws(() => codec.decodeWireState(hostileText), /invalid campaign lifecycle encoding/);
});

test('all input forms reject proper prefixes, trailing bytes, invalid tags/flags/UTF-8 and hostile lengths', () => {
  const codec = new LifecycleCodec();
  const inputs = [...new AuditedLifecycle().alphabet(), sink(0n, ''), sink(1n, '\ufeff\0錯誤'),
    complete(50n, 'FactoryError', { transitions: 1n })];
  for (const input of inputs) {
    const encoded = codec.encodeInput(input); assert.deepEqual(codec.decodeInput(encoded), input);
    for (let n = 0; n < encoded.length; n++) assert.throws(() => codec.decodeInput(encoded.subarray(0, n)), ModelError);
    assert.throws(() => codec.decodeInput(Uint8Array.from([...encoded, 0])), ModelError);
  }
  for (const malformed of ['ff', '0003', '04000000000000000002', '04ffffffffffffffff', '040100000000000000ff00',
    '040200000000000000c08000', '03000000000000000002']) assert.throws(() => codec.decodeInput(bytes(malformed)), ModelError);
  for (const offset of [9, 10]) { const b = codec.encodeInput(complete(50n)); b[offset] = 255; assert.throws(() => codec.decodeInput(b), ModelError); }
  const inconsistent = codec.decodeInput(codec.encodeInput(complete(50n, 'FactoryError', { transitions: 1n })));
  const m = core(); apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]); reject(m, inconsistent, 'inconsistent job summary');
});

test('state codec rejects truncation, tags, hostile lengths, unsorted IDs and impossible properties', () => {
  const codec = new LifecycleCodec(), m = core();
  const states = [m.cloneState(m.state)];
  for (const input of [claim(), claim(), complete(50n), complete(51n, 'FactoryError'), receive(50n), sink(50n, ''), receive(51n), finish()]) {
    m.apply(input); assert.equal(m.lastAccepted, true); states.push(m.cloneState(m.state));
  }
  for (const state of states) {
    const encoded = codec.encodeState(state); assert.deepEqual(codec.decodeState(encoded), state);
    assert.deepEqual(codec.decodeWireState(encoded), codec.fromNativeState(state));
    assert.deepEqual(codec.encodeState(codec.decodeState(encoded)), encoded);
    for (let n = 0; n < encoded.length; n++) assert.throws(() => codec.decodeState(encoded.subarray(0, n)), ModelError);
    assert.throws(() => codec.decodeState(Uint8Array.from([...encoded, 0])), ModelError);
  }
  for (const offset of [80, 81, 82, 91, 92, 93]) {
    const b = codec.encodeState(states[0]); b[offset] = 255; assert.throws(() => codec.decodeState(b), ModelError);
  }
  // A full-u64 requested count is valid wire data, but disagrees with jobs=2 here.
  const wrongRequested = codec.encodeState(states[0]); wrongRequested.fill(255, 0, 8);
  assert.throws(() => codec.decodeWireState(wrongRequested), /properties/);
  const hostilePending = codec.encodeState(states[0]); hostilePending.fill(255, 83, 91);
  assert.throws(() => codec.decodeWireState(hostilePending), /invalid campaign lifecycle encoding/);
  const twice = codec.encodeState(states[2]);
  for (const [offset, replacement] of [[100, 50n], [91, 51n]]) {
    const b = twice.slice(); new DataView(b.buffer).setBigUint64(offset, replacement, true);
    assert.throws(() => codec.decodeState(b), ModelError);
  }
  const badPending = twice.slice(); badPending[99] = 2; assert.throws(() => codec.decodeState(badPending), ModelError);
  const impossible = core().state; impossible.stop = 'Sink'; impossible.report.termination = { kind: 'CallbackError', error: new ModelError('never called') };
  assert.throws(() => codec.decodeState(codec.encodeState(impossible)), /properties/);
  const noFailure = core().state; noFailure.stop = 'Failure'; noFailure.finalized = true; noFailure.report.termination = { kind: 'FailureFound' };
  assert.throws(() => codec.decodeState(codec.encodeState(noFailure)), /properties/);
  const lost = core().state; lost.lostWorker = true; lost.stop = 'Worker'; lost.report.termination = { kind: 'WorkerError', error: new ModelError('lost') };
  assert.throws(() => codec.decodeState(codec.encodeState(lost)), /properties/);
  assert.throws(() => new LifecycleCodec(70n, 2).decodeState(codec.encodeState(states[1])), /properties/);
  const malformedReady = core(2).step(core(2).state, claim()).state;
  malformedReady.pending.set(50n, { kind: 'Ready', summary: complete(50n, 'ModelError', { skippedChecks: 1n }).summary });
  assert.throws(() => codec.decodeState(codec.encodeState(malformedReady)), /properties/);
});

test('state codec covers every pending outcome, stop, final termination and missing-worker flag', () => {
  const roundtrip = (m, codec = new LifecycleCodec(m.firstJob, m.jobs, m.stopOnFailure)) => {
    const encoded = codec.encodeState(m.state); assert.deepEqual(codec.decodeState(encoded), m.state);
  };
  for (const outcome of OUTCOMES) {
    const m = core(1, true); apply(m, claim(), [{ kind: 'RunJob', jobId: 50n }]);
    apply(m, complete(50n, outcome)); roundtrip(m);
    apply(m, receive(50n), [{ kind: 'Deliver', jobId: 50n }]); roundtrip(m);
    apply(m, sink(50n)); apply(m, finish()); roundtrip(m);
  }
  for (const stop of ['Cancelled', 'Deadline']) {
    const m = core(1); apply(m, claim(stop)); roundtrip(m); apply(m, finish()); roundtrip(m);
  }
  const lost = core(2); apply(lost, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  apply(lost, worker('lost\0錯誤', true)); roundtrip(lost); apply(lost, finish()); roundtrip(lost);
  const sinkError = core(1); apply(sinkError, claim(), [{ kind: 'RunJob', jobId: 50n }]);
  apply(sinkError, complete(50n)); apply(sinkError, receive(50n), [{ kind: 'Deliver', jobId: 50n }]);
  apply(sinkError, sink(50n, '')); roundtrip(sinkError); apply(sinkError, finish()); roundtrip(sinkError);
  const disabled = core(1, false); disabled.apply(claim()); disabled.apply(complete(50n, 'Failure'));
  disabled.state.stop = 'Failure';
  assert.throws(() => new LifecycleCodec(50n, 1, false).decodeState(new LifecycleCodec().encodeState(disabled.state)), /properties/);
});

test('independent admission detects an inert erroneous rejection of every event kind', () => {
  const model = new AuditedLifecycle({ jobs: 1 }), running = [claim()], ready = [...running, complete(50n)];
  const delivering = [...ready, receive(50n)], delivered = [...delivering, sink(50n)];
  for (const [prefix, input] of [[[], claim()], [[], claim('Cancelled')], [[], claim('Deadline')],
    [running, complete(50n)], [ready, receive(50n)], [delivering, sink(50n)], [delivering, sink(50n, 'sink')],
    [[], worker()], [delivered, finish()]]) {
    const before = acceptedPrefix(model, prefix);
    const faulty = model.observeTransition(before, input, { state: model.core.cloneState(before.core), outputs: [],
      disposition: { kind: 'rejected', reason: 'injected incorrect rejection' } });
    failed(model.checkTransition(before, input, faulty), 'campaign.admission');
  }
});

test('independent ledger catches stale accepted acknowledgements with and without another delivery active', () => {
  for (const otherActive of [false, true]) {
    const model = new AuditedLifecycle();
    const inputs = [claim(), complete(50n), receive(50n), sink(50n)];
    if (otherActive) inputs.push(claim(), complete(51n), receive(51n));
    const before = acceptedPrefix(model, inputs), input = sink(50n, 'stale');
    const damaged = model.core.cloneState(before.core); damaged.stop = 'Sink'; damaged.delivering = null;
    damaged.report.termination = { kind: 'CallbackError', error: new ModelError('stale') };
    const faulty = model.observeTransition(before, input, accepted(damaged));
    failed(model.checkTransition(before, input, faulty), 'campaign.admission');
    failed(model.checkState(faulty.state), 'audit.protocol');
    if (otherActive) failed(model.checkState(faulty.state), 'audit.active_sink');
    const healthy = model.step(before, input); assert.equal(healthy.disposition.kind, 'rejected');
    assert.deepEqual(healthy.state, before);
  }
});

test('independent ledger catches a forgotten stop before it admits an extra job', () => {
  const model = new AuditedLifecycle({ stopOnFailure: true });
  for (const [prefix, input] of [[[], claim('Cancelled')], [[], claim('Deadline')], [[], worker()],
    [[claim()], complete(50n, 'Failure')], [[claim()], complete(50n, 'Deadline')],
    [[claim()], complete(50n, 'Cancelled')], [[claim(), complete(50n), receive(50n)], sink(50n, 'sink')]]) {
    const before = acceptedPrefix(model, prefix), actual = model.core.step(before.core, input);
    actual.state.stop = null;
    const faulty = model.observeTransition(before, input, actual);
    failed(model.checkState(faulty.state), 'audit.stop_observed');
    const extra = model.step(faulty.state, claim());
    failed(model.checkState(extra.state), 'audit.protocol');
    failed(model.checkTransition(faulty.state, claim(), extra), 'campaign.admission');
  }
});

test('independent ledger detects fabricated worker loss and early Finish after ModelError accounting loss', () => {
  const model = new AuditedLifecycle({ jobs: 2 });
  const before = acceptedPrefix(model, [claim(), claim(), complete(50n, 'ModelError'), receive(50n), sink(50n)]);
  assert.equal(before.core.report.accountingComplete, false);
  assert.deepEqual([...before.core.pending.keys()], [51n]);
  assert.equal(before.ledger.lostWork, false); assert.equal(before.core.lostWorker, false);
  const input = worker('worker failed without losing work', false), actual = model.core.step(before.core, input);
  assert.equal(actual.disposition.kind, 'accepted'); assert.equal(actual.state.lostWorker, false);
  const healthy = model.observeTransition(before, input, actual);
  clean(model.checkState(healthy.state)); clean(model.checkTransition(before, input, healthy));
  const refused = model.step(healthy.state, finish());
  assert.equal(refused.disposition.kind, 'rejected'); assert.deepEqual(refused.state, healthy.state);
  clean(model.checkTransition(healthy.state, finish(), refused));

  const corrupted = { ...actual, state: model.core.cloneState(actual.state) };
  corrupted.state.lostWorker = true;
  const faulty = model.observeTransition(before, input, corrupted);
  assert.equal(faulty.state.ledger.lostWork, false);
  // Existing incomplete accounting hides this fault from core properties and totals.
  clean(coreChecks(faulty.state.core, model.firstJob, model.jobs, model.stopOnFailure));
  assert.equal(model.checkState(faulty.state).find(c => c.id === 'audit.work_accounting').status, 'passed');
  failed(model.checkState(faulty.state), 'audit.finalization');
  const early = model.step(faulty.state, finish());
  assert.equal(early.disposition.kind, 'accepted'); assert.equal(early.state.core.finalized, true);
  assert.deepEqual([...early.state.core.pending.keys()], [51n]);
  failed(model.checkTransition(faulty.state, finish(), early), 'campaign.admission');
  failed(model.checkState(early.state), 'audit.finalization');
});

test('self-model alphabet includes encodable inconsistent summaries and independently rejects them', () => {
  const model = new AuditedLifecycle(), before = acceptedPrefix(model, [claim()]);
  const malformed = model.alphabet().filter(input => input.kind === 'Complete' && input.jobId === 50n
    && ((input.summary.outcome === 'Completed' && input.summary.failure)
      || (input.summary.outcome === 'FactoryError' && input.summary.cases !== 0n)));
  assert.equal(malformed.length, 2);
  for (const input of malformed) {
    assert.deepEqual(model.decodeInput(model.encodeInput(input)), input);
    const t = model.step(before, input);
    assert.equal(t.disposition.kind, 'rejected'); assert.deepEqual(t.state, before);
    clean(model.checkTransition(before, input, t)); clean(model.checkState(t.state));
  }
});

test('finite lifecycle self-model exhausts the real decision law with an independent identity ledger', () => {
  for (const stopOnFailure of [false, true]) {
    const model = new AuditedLifecycle({ stopOnFailure });
    const report = enumerateStates(model, { maxStates: 200000, maxTransitions: 8000000, maxDepth: 20 });
    assert.equal(report.failure, null); assert.equal(report.termination, 'graph_exhausted'); assert.equal(report.skipped_checks, 0);
  }
});

test('fixed-seed public fuzz exercises hostile lifecycle events without violating the ledger', () => {
  for (const stopOnFailure of [false, true]) {
    const report = fuzz(new AuditedLifecycle({ jobs: 4, stopOnFailure }), {
      seed: 0x53544154454c4553n, cases: 256, maxSteps: 64, maxTransitions: 16384n, mutationPercent: 50,
    });
    assert.equal(report.failure, null); assert.equal(report.termination, 'cases_completed'); assert.equal(report.skipped_checks, 0n);
  }
});

test('duplicate Deliver is found by public search/fuzz, shrunk, recorded, read and replayed with healthy controls', () => {
  const faulty = new AuditedLifecycle({ duplicateDelivery: true }), healthy = new AuditedLifecycle();
  const explored = enumerateStates(faulty, { maxStates: 10000, maxTransitions: 100000, maxDepth: 10 });
  assert.equal(explored.termination, 'failure_found');
  assert.ok(explored.failure.violations.some(v => v.phase === 'transition' && v.check.id === 'campaign.effects'));
  assert.ok(explored.failure.violations.some(v => v.check.id === 'audit.protocol'));
  const fuzzed = fuzz(faulty, { seed: 0x53544154454c4553n, cases: 256, maxSteps: 64, maxTransitions: 16384n, mutationPercent: 50 });
  assert.equal(fuzzed.termination, 'failure_found');
  // Keep the specified transition target even though state-ledger checks also fail.
  const original = { inputs: [sink(999n), claim(), ...explored.failure.inputs],
    violations: explored.failure.violations.filter(v => v.phase === 'transition' && v.check.id === 'campaign.effects') };
  const minimized = shrink(faulty, original, { maxAttempts: 1000 });
  assert.equal(minimized.validated_original, true); assert.equal(minimized.termination, 'search_complete');
  assert.equal(minimized.minimized.inputs.length, 3); assert.ok(original.inputs.length > minimized.minimized.inputs.length);
  assert.deepEqual(minimized.minimized.inputs.map(i => i.kind), ['Claim', 'Complete', 'Receive']);
  for (const inputs of [original.inputs, minimized.minimized.inputs]) {
    const trace = recordAudit(faulty, inputs, { strategy: 'campaign-self-model-fault-control', seed: null,
      parameters: [['injected_fault', 'duplicate Deliver']] }, 100);
    assert.equal(trace.termination, 'property_failed');
    const recovered = readAuditTrace(writeAuditTrace(trace)); assert.deepEqual(recovered, trace);
    const reproduced = replay(faulty, recovered);
    assert.equal(reproduced.outcome, 'exact'); assert.equal(reproduced.failure_reproduced, true);
    const strict = replay(healthy, recovered); assert.equal(strict.outcome, 'incompatible'); assert.equal(strict.build_matches, false);
    const relaxed = replay(healthy, recovered, true);
    assert.equal(relaxed.outcome, 'diverged'); assert.equal(relaxed.build_matches, false); assert.equal(relaxed.failure_reproduced, false);
  }
});

test('healthy test-only ledger bytes and recorded traces roundtrip and replay independently', () => {
  const model = new AuditedLifecycle();
  const inputs = [claim(), claim(), complete(51n, 'FactoryError'), complete(50n, 'Failure'),
    receive(51n), sink(51n), receive(50n), sink(50n), finish()];
  let state = model.initialState();
  for (const input of inputs) {
    state = model.step(state, input).state;
    const encoded = model.encodeState(state); assert.deepEqual(model.decodeState(encoded), state);
    const copy = model.cloneState(state); copy.ledger.claims.add(999n); assert.equal(state.ledger.claims.has(999n), false);
  }
  const trace = recordAudit(model, inputs, { strategy: 'campaign-self-model-healthy', seed: null, parameters: [] }, 100);
  assert.equal(trace.termination, 'completed');
  const replayed = replay(model, readAuditTrace(writeAuditTrace(trace)));
  assert.equal(replayed.outcome, 'exact'); assert.equal(replayed.steps_verified, 9); assert.equal(replayed.failure_reproduced, false);
});
