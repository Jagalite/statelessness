// N07 integration against the existing Recorder, never a replacement recorder.
// Source-authored only: execution is reserved for the parent's integrated freeze
// and Kyoto job. References: spec/observation-v1.md, pinned src/observation.rs,
// src/monitor.rs, tests/monitor_review.rs, and the historical checked-results audit.
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {
  Recorder, CheckedInitial, CheckedTurn, checkInitial, checkTurnExact,
  accepted, check, metadata, ModelError, readAuditTrace, writeAuditTrace,
  replayWithObservationsBounded, LogLevel, SnapshotPolicy, TextObserver,
  EncodedPayloads, NoPayloads, TextObservationError,
} from '../dist/index.js';

const CONFIG = {strategy: 'application-owned', seed: null, parameters: []};
const LEVELS = [LogLevel.Off, LogLevel.Error, LogLevel.Warn, LogLevel.Info, LogLevel.Debug, LogLevel.Trace];

function numberBytes(value) {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setInt32(0, value, true);
  return bytes;
}
function readNumber(bytes) {
  if (bytes.length !== 4) throw new Error('expected four-byte counter');
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getInt32(0, true);
}

// Primitive counter/variable state-check batches follow the existing monitor
// model pattern. Effects belong to the application harness, outside the reducer.
function counter({failAt = Infinity, skipAt = -1} = {}) {
  const calls = {initial: 0, reducer: 0, state: 0, transition: 0, effects: 0};
  const encodes = {state: 0, input: 0, output: 0};
  const effects = [];
  const faults = {codec: null};
  function encode(kind, value) {
    encodes[kind]++;
    if (faults.codec === kind) throw new Error(`diagnostic ${kind} codec failed`);
    return numberBytes(value);
  }
  const model = {
    metadata: () => metadata('observation-recorder-integration', 'source-v1'),
    initialState() { calls.initial++; throw new Error('must use supplied checkpoint'); },
    cloneState: value => value,
    cloneInput: value => value,
    cloneOutput: value => value,
    step(before, input) {
      calls.reducer++;
      if (input === 0) return {state: before, outputs: [91, 92], disposition: {kind: 'ignored', reason: 'duplicate'}};
      if (input < 0) return {state: before, outputs: [93], disposition: {kind: 'rejected', reason: 'invalid'}};
      return accepted(before + input, [before + input, before + input + 100]);
    },
    checkState(state) {
      calls.state++;
      const checks = [state >= failAt ? check('limit', 'failed', 'first property failure') : check('limit')];
      if (state % 2 === 0) checks.push(check('even-state'));
      if (state === skipAt) checks.push(check('optional', 'skipped', 'not applicable'));
      return checks;
    },
    checkTransition(before, input, transition) {
      calls.transition++;
      const next = input > 0 ? before + input : before;
      const outputs = input > 0 ? [next, next + 100] : input === 0 ? [91, 92] : [93];
      const kind = input > 0 ? 'accepted' : input === 0 ? 'ignored' : 'rejected';
      const valid = transition.state === next && transition.disposition.kind === kind
        && transition.outputs.length === outputs.length && outputs.every((v, i) => v === transition.outputs[i]);
      return [valid ? check('edge-output') : check('edge-output', 'failed', 'actual output differs')];
    },
    encodeState: value => encode('state', value), decodeState: readNumber,
    encodeInput: value => encode('input', value), decodeInput: readNumber,
    encodeOutput: value => encode('output', value),
  };
  return {model, calls, encodes, effects, faults};
}

function applyOnce(harness, before, input, sequence, policy) {
  const actual = harness.model.step(before, input);
  for (const output of actual.outputs) {
    harness.calls.effects++;
    harness.effects.push(output);
  }
  const token = checkTurnExact(harness.model, before, input, actual, sequence, policy);
  assert.deepEqual(token.transition, actual);
  return token;
}

function memoryWriter() {
  const chunks = [];
  return {
    writes: 0, flushes: 0,
    write(bytes) { this.writes++; chunks.push(Uint8Array.from(bytes)); return bytes.length; },
    flush() { this.flushes++; },
    text() { return Buffer.concat(chunks.map(bytes => Buffer.from(bytes))).toString('utf8'); },
  };
}
function logger(level, snapshots = SnapshotPolicy.EveryTransition, payloads = new EncodedPayloads(64), writer = memoryWriter(), maxRecordBytes = 2048) {
  return new TextObserver(writer, 'integration', {level, snapshots, maxRecordBytes}, payloads);
}
function checkpoint(token, sequence = 0n) {
  return {kind: 'checkpoint', sequence, state: token.state, checks: token.checks};
}
function event(token) {
  return {kind: 'transition', ...token.observation()};
}
function observeOnly(harness, observer, observation) {
  const before = {...harness.calls};
  try { observer.observe(harness.model, observation); }
  finally { assert.deepEqual(harness.calls, before, 'logging must not reduce, check, initialize, or execute effects'); }
}
function recordOnly(harness, recorder, token) {
  const before = {...harness.calls};
  try { return recorder.observeChecked(token); }
  finally { assert.deepEqual(harness.calls, before, 'checked recording must not repeat application work'); }
}
function delta(after, before) {
  return Object.fromEntries(Object.keys(after).map(key => [key, after[key] - before[key]]));
}
function encodedTrace(recorder) {
  const bytes = writeAuditTrace(recorder.snapshot());
  assert.equal(recorder.retainedBytes(), BigInt(bytes.length), 'logical serialized bytes, not RSS');
  return bytes;
}
function samePrefix(actual, expected) {
  for (const field of ['metadata', 'config', 'initialState', 'initialChecks', 'steps']) {
    assert.deepEqual(actual[field], expected[field], `preserve coherent ${field}`);
  }
}
function expectStage(run, stage) {
  assert.throws(run, error => error instanceof TextObservationError && error.stage === stage);
}
function replaySuffix(recorder, modelOptions = {}) {
  const artifact = readAuditTrace(encodedTrace(recorder));
  const playback = counter(modelOptions);
  const observations = [];
  const report = replayWithObservationsBounded(playback.model, artifact, {}, {}, observation => { observations.push(observation); });
  assert.equal(report.outcome, 'exact');
  assert.equal(report.steps_verified, recorder.retainedSteps());
  assert.deepEqual(playback.calls, {
    initial: 0, reducer: recorder.retainedSteps(), state: recorder.retainedSteps() + 1,
    transition: recorder.retainedSteps(), effects: 0,
  });
  assert.deepEqual(observations[0].checks, artifact.initialChecks);
  for (let i = 0; i < artifact.steps.length; i++) {
    const observation = observations[i + 1];
    assert.deepEqual(observation.differences, {disposition: false, outputs: false, state: false, checks: false});
    assert.deepEqual(observation.actual.checks, artifact.steps[i].checks);
    assert.deepEqual(observation.actual.transition.outputs, artifact.steps[i].outputs.map(readNumber));
  }
  return report;
}

function runApplication(instrumentation) {
  const harness = counter({failAt: 3, skipAt: 2});
  // Checking is part of the application in the zero-instrumentation baseline.
  // It has neither Recorder nor TextObserver; the same check-once path is used.
  const initial = checkInitial(harness.model, 0);
  const recorder = instrumentation === null ? null : Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 8});
  const observer = typeof instrumentation === 'number' ? logger(instrumentation) : null;
  if (observer) observeOnly(harness, observer, checkpoint(initial));
  let state = 0;
  const tokens = [];
  for (const [index, input] of [1, 0, -1, 1, 1].entries()) {
    const token = applyOnce(harness, state, input, BigInt(index + 1));
    tokens.push(token);
    if (recorder) assert.deepEqual(recordOnly(harness, recorder, token), token.checks);
    if (observer) observeOnly(harness, observer, event(token));
    state = token.transition.state;
  }
  return {harness, recorder, observer, tokens, state};
}

for (const level of LEVELS) test(`N07 ${LogLevel[level]} preserves the zero-instrumentation application and exact recording`, () => {
  const baseline = runApplication(null);
  const recorded = runApplication('recorder-only');
  const observed = runApplication(level);
  const expected = {initial: 0, reducer: 5, state: 6, transition: 5, effects: 9};
  for (const run of [baseline, recorded, observed]) {
    assert.deepEqual(run.harness.calls, expected);
    assert.equal(run.state, 3);
    assert.deepEqual(run.harness.effects, [1, 101, 91, 92, 93, 2, 102, 3, 103]);
    assert.deepEqual(run.tokens.map(token => token.checks), baseline.tokens.map(token => token.checks));
  }
  assert.deepEqual(baseline.harness.encodes, {state: 0, input: 0, output: 0});
  assert.deepEqual(recorded.harness.encodes, {state: 11, input: 5, output: 9});
  assert.deepEqual(delta(observed.harness.encodes, recorded.harness.encodes), level === LogLevel.Trace
    ? {state: 6, input: 5, output: 9} : {state: 0, input: 0, output: 0});
  assert.deepEqual(encodedTrace(observed.recorder), encodedTrace(recorded.recorder));
  assert.equal(observed.recorder.snapshot().termination, 'property_failed');
  const lines = observed.observer.getRef().text().trim().split('\n').filter(Boolean);
  const sequences = level === LogLevel.Off ? [] : level === LogLevel.Error ? [5]
    : level <= LogLevel.Info ? [4, 5] : level === LogLevel.Debug ? [1, 2, 3, 4, 5] : [0, 1, 2, 3, 4, 5];
  assert.deepEqual(lines.map(line => Number(/sequence=(\d+)/.exec(line)[1])), sequences);
  for (const line of lines) {
    const sequence = Number(/sequence=(\d+)/.exec(line)[1]);
    assert.ok(line.startsWith(sequence === 5 ? 'Error ' : sequence === 4 ? 'Warn ' : sequence === 0 ? 'Trace ' : 'Debug '));
    assert.equal(line.includes('check="limit":Passed'), level === LogLevel.Trace && sequence !== 5);
    assert.equal(line.includes('hex:'), level === LogLevel.Trace);
  }
  if (level === LogLevel.Off) assert.equal(observed.observer.getRef().writes, 0);
  assert.equal(replaySuffix(observed.recorder, {failAt: 3, skipAt: 2}).failure_reproduced, true);
});

test('N07 supplied ignored/rejected outputs, disposition and checks reach both consumers unchanged', () => {
  const {recorder, observer, tokens} = runApplication(LogLevel.Trace);
  const lines = observer.getRef().text().split('\n');
  const ignored = lines.find(line => line.includes('sequence=2 '));
  const rejected = lines.find(line => line.includes('sequence=3 '));
  for (const text of ['disposition=Ignored("duplicate")', 'outputs=2', 'input=hex:00000000', 'output[0]=hex:5b000000', 'output[1]=hex:5c000000', 'post_state=hex:01000000', 'check="edge-output":Passed']) assert.ok(ignored.includes(text), text);
  for (const text of ['disposition=Rejected("invalid")', 'outputs=1', 'input=hex:ffffffff', 'output[0]=hex:5d000000']) assert.ok(rejected.includes(text), text);
  const trace = recorder.snapshot();
  for (let i = 0; i < tokens.length; i++) {
    assert.deepEqual(trace.steps[i].outputs.map(readNumber), tokens[i].transition.outputs);
    assert.deepEqual(trace.steps[i].disposition, tokens[i].transition.disposition);
    assert.deepEqual(trace.steps[i].checks, tokens[i].checks);
  }
});

for (const failureFirst of [false, true]) test(`N07 failure beats skip in ${failureFirst ? 'failure-first' : 'skip-first'} authentic batches`, () => {
  const harness = counter();
  const ordinary = harness.model.checkState;
  harness.model.checkState = state => {
    const batch = ordinary(state);
    if (state === 1) batch.push(...(failureFirst
      ? [check('first-failure', 'failed', 'boom'), check('skip', 'skipped', 'policy')]
      : [check('skip', 'skipped', 'policy'), check('first-failure', 'failed', 'boom')]));
    return batch;
  };
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG);
  const token = applyOnce(harness, 0, 1, 1n);
  recordOnly(harness, recorder, token);
  const before = encodedTrace(recorder);
  for (const level of LEVELS) {
    const observer = logger(level);
    observeOnly(harness, observer, event(token));
    const text = observer.getRef().text();
    if (level === LogLevel.Off) assert.equal(text, '');
    else {
      assert.ok(text.startsWith('Error '));
      assert.ok(text.includes('check="first-failure":Failed("boom")'));
      assert.ok(text.includes('check="skip":Skipped("policy")'));
    }
  }
  assert.deepEqual(encodedTrace(recorder), before);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 1, state: 2, transition: 1, effects: 2});
});

const RETENTION_CASES = [
  {origin: 0n, maxSteps: 1, maxRetainedBytes: 8192n},
  {origin: 0n, maxSteps: 3, maxRetainedBytes: 8192n},
  {origin: 9007199254741001n, maxSteps: 1, maxRetainedBytes: 8192n},
  {origin: 9007199254741001n, maxSteps: 3, maxRetainedBytes: 8192n},
  {origin: 77n, maxSteps: 1024, maxRetainedBytes: 2048n},
];
for (const options of RETENTION_CASES) test(`N07 1024 turns retain an exact bounded suffix: origin ${options.origin}, capacity ${options.maxSteps}, bytes ${options.maxRetainedBytes}`, () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 5);
  const recorder = Recorder.withCheckedInitialAt(initial, options.origin, CONFIG, {...options, limits: {max_string_bytes: 128}});
  // The sink counts bytes and retains one record only, so the test's logger does
  // not accidentally retain a full history while assessing logical evidence.
  const sink = {bytes: 0, writes: 0, last: '', write(bytes) { this.bytes += bytes.length; this.writes++; this.last = new TextDecoder().decode(bytes); assert.ok(bytes.length <= 1024); return bytes.length; }, flush() {}};
  const observer = logger(LogLevel.Trace, SnapshotPolicy.Every(128n), new EncodedPayloads(4), sink, 1024);
  observeOnly(harness, observer, checkpoint(initial, options.origin));
  let state = 5;
  for (let local = 1; local <= 1024; local++) {
    const token = applyOnce(harness, state, 1, options.origin + BigInt(local));
    recordOnly(harness, recorder, token);
    observeOnly(harness, observer, event(token));
    state = token.transition.state;
    // Release application effect receipts; their execution count stays exact.
    harness.effects.length = 0;
    const trace = recorder.snapshot();
    const evicted = Number(recorder.evictedSteps());
    assert.equal(recorder.observedSteps(), BigInt(local));
    assert.equal(recorder.retainedSteps() + evicted, local);
    assert.ok(recorder.retainedSteps() <= options.maxSteps);
    assert.ok(recorder.retainedBytes() <= options.maxRetainedBytes);
    assert.equal(readNumber(trace.initialState), 5 + evicted);
    assert.deepEqual(trace.initialChecks, (5 + evicted) % 2 === 0 ? [check('limit'), check('even-state')] : [check('limit')]);
    assert.ok(trace.initialChecks.every(item => item.id !== 'edge-output'), 'promote only the state-check prefix');
    assert.equal(encodedTrace(recorder).length, Number(recorder.retainedBytes()));
  }
  assert.deepEqual(harness.calls, {initial: 0, reducer: 1024, state: 1025, transition: 1024, effects: 2048});
  // Recorder encodes both sides of each turn. Trace adds one initial snapshot,
  // eight absolute-period snapshots, each input, and the two actual outputs.
  assert.deepEqual(harness.encodes, {state: 2058, input: 2048, output: 4096});
  assert.equal(state, 1029);
  assert.ok(recorder.evictedSteps() > 0n);
  if (options.maxSteps < 1024) assert.equal(recorder.retainedSteps(), options.maxSteps);
  else assert.ok(recorder.retainedSteps() < 1024, 'byte budget itself must cause eviction');
  assert.equal(sink.writes, 1025);
  assert.ok(sink.last.includes(`sequence=${options.origin + 1024n} `));
  const trace = recorder.snapshot();
  const parameters = new Map(trace.config.parameters);
  assert.equal(parameters.get('stateless.monitor.sequence_origin'), options.origin === 0n ? undefined : String(options.origin));
  assert.equal(parameters.get('stateless.monitor.observed_steps'), '1024');
  assert.equal(parameters.get('stateless.monitor.evicted_steps'), String(recorder.evictedSteps()));
  // Like the pinned Rust monitor, this parameter is local, not origin + eviction.
  assert.equal(parameters.get('stateless.monitor.checkpoint_after_sequence'), String(recorder.evictedSteps()));
  assert.equal(parameters.get('stateless.monitor.checking'), 'full');
  assert.equal(replaySuffix(recorder).failure_reproduced, false);

  // A fresh process receives only the public binary artifact and model source;
  // no live token or Recorder object can confer accidental replay authority.
  // This subprocess executes only when the parent runs this test through Kyoto.
  const entry = new URL('../dist/index.js', import.meta.url).href;
  const source = `import {readFileSync} from 'node:fs';
import {accepted,check,metadata,readAuditTrace,replayWithObservationsBounded} from ${JSON.stringify(entry)};
${numberBytes.toString()}
${readNumber.toString()}
${counter.toString()}
const trace=readAuditTrace(Uint8Array.from(JSON.parse(readFileSync(0,'utf8'))));
const h=counter();
const report=replayWithObservationsBounded(h.model,trace);
process.stdout.write(JSON.stringify({report,calls:h.calls,checkpoint:readNumber(trace.initialState)}));`;
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', source], {
    input: JSON.stringify(Array.from(encodedTrace(recorder))), encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024,
  });
  assert.equal(child.error, undefined, child.error?.message);
  assert.equal(child.status, 0, child.stderr);
  const replayed = JSON.parse(child.stdout);
  assert.equal(replayed.report.outcome, 'exact');
  assert.equal(replayed.report.steps_verified, recorder.retainedSteps());
  assert.equal(replayed.report.failure_reproduced, false);
  assert.equal(replayed.checkpoint, 5 + Number(recorder.evictedSteps()));
  assert.deepEqual(replayed.calls, {initial: 0, reducer: recorder.retainedSteps(), state: recorder.retainedSteps() + 1, transition: recorder.retainedSteps(), effects: 0});
});

test('N07 zero capacity is rejected without rechecking or encoding the authentic initial token', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 7);
  const calls = {...harness.calls};
  assert.throws(() => Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 0}), /max_steps must be positive/);
  assert.deepEqual(harness.calls, calls);
  assert.deepEqual(harness.encodes, {state: 0, input: 0, output: 0});
  const observer = logger(LogLevel.Trace);
  observeOnly(harness, observer, checkpoint(initial));
  assert.ok(observer.getRef().text().includes('state=hex:07000000'));
});

const RECOVERABLE = ['encode_input', 'encode_output', 'encode_state', 'format_input', 'format_output', 'format_state', 'record_limit', 'swallowed_record_limit'];
for (const fault of RECOVERABLE) test(`N07 ${fault} discards only the diagnostic record and preserves Recorder recovery`, () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 1});
  let armed = false;
  const formatter = Object.fromEntries(['input', 'output', 'state'].map(kind => [kind, (_model, value, out) => {
    if (armed && fault === `format_${kind}`) throw new Error(`formatter ${kind} failed`);
    if (armed && kind === 'input' && fault.endsWith('record_limit')) {
      if (fault === 'swallowed_record_limit') {
        try { out.writeAll('x'.repeat(4096)); } catch { /* Deliberately swallow; the record must remain failed. */ }
      } else out.writeAll('x'.repeat(4096));
    }
    out.writeAll(String(value));
  }]));
  const observer = logger(LogLevel.Trace, SnapshotPolicy.EveryTransition, fault.startsWith('encode_') ? new EncodedPayloads(4) : formatter, memoryWriter(), 1024);
  observeOnly(harness, observer, checkpoint(initial));
  const textPrefix = observer.getRef().text();
  const token = applyOnce(harness, 0, 1, 1n);
  recordOnly(harness, recorder, token);
  const durablePrefix = encodedTrace(recorder);
  armed = true;
  harness.faults.codec = fault.startsWith('encode_') ? fault.slice('encode_'.length) : null;
  try { expectStage(() => observeOnly(harness, observer, event(token)), fault.endsWith('record_limit') ? 'record_limit' : fault); }
  finally { armed = false; harness.faults.codec = null; }
  assert.equal(observer.isPoisoned(), false);
  assert.equal(observer.getRef().text(), textPrefix, 'no partial diagnostic record is published');
  assert.deepEqual(encodedTrace(recorder), durablePrefix);
  const next = applyOnce(harness, 1, 1, 2n);
  recordOnly(harness, recorder, next);
  observeOnly(harness, observer, event(next));
  assert.ok(observer.getRef().text().includes('sequence=2 '));
  assert.equal(observer.getRef().text().includes('sequence=1 '), false);
  assert.equal(recorder.evictedSteps(), 1n);
  assert.deepEqual(recorder.snapshot().initialChecks, token.checks.slice(0, token.stateCheckCount));
  assert.deepEqual(harness.calls, {initial: 0, reducer: 2, state: 3, transition: 2, effects: 4});
  assert.equal(replaySuffix(recorder).failure_reproduced, false);
});

for (const stage of ['sink_write', 'sink_flush']) test(`N07 ${stage} poison leaves Recorder live and fails even after Off`, () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 1});
  const sink = {
    writes: 0, delivered: 0, flushes: 0,
    write(bytes) { this.writes++; if (stage === 'sink_write') { if (this.writes > 1) throw new Error('disk full'); this.delivered++; return 1; } this.delivered += bytes.length; return bytes.length; },
    flush() { this.flushes++; throw new Error('flush failed'); },
  };
  const observer = logger(LogLevel.Trace, SnapshotPolicy.EveryTransition, new EncodedPayloads(4), sink);
  const token = applyOnce(harness, 0, 1, 1n);
  recordOnly(harness, recorder, token);
  const before = encodedTrace(recorder);
  if (stage === 'sink_write') expectStage(() => observeOnly(harness, observer, event(token)), stage);
  else { observeOnly(harness, observer, event(token)); expectStage(() => observer.flush(), stage); }
  assert.equal(observer.isPoisoned(), true);
  if (stage === 'sink_write') assert.equal(sink.delivered, 1, 'a writer may expose a diagnostic prefix');
  assert.deepEqual(encodedTrace(recorder), before);
  assert.equal(recorder.isFrozen(), false);
  const writerCalls = {writes: sink.writes, flushes: sink.flushes};
  const encodes = {...harness.encodes};
  observer.setLevel(LogLevel.Off);
  expectStage(() => observeOnly(harness, observer, event(token)), 'sink_poisoned');
  expectStage(() => observer.flush(), 'sink_poisoned');
  assert.deepEqual(harness.encodes, encodes);
  assert.deepEqual({writes: sink.writes, flushes: sink.flushes}, writerCalls);
  const next = applyOnce(harness, 1, 1, 2n);
  recordOnly(harness, recorder, next);
  const replacement = logger(LogLevel.Debug);
  observeOnly(harness, replacement, event(next));
  assert.ok(replacement.getRef().text().includes('sequence=2 '));
  assert.deepEqual(harness.calls, {initial: 0, reducer: 2, state: 3, transition: 2, effects: 4});
  assert.equal(replaySuffix(recorder).failure_reproduced, false);
});

test('N07 first property failure survives later diagnostic errors, recorder errors and continued application work', () => {
  const harness = counter({failAt: 3});
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 1});
  const observer = logger(LogLevel.Trace);
  let state = 0;
  for (let sequence = 1n; sequence <= 3n; sequence++) {
    const token = applyOnce(harness, state, 1, sequence);
    recordOnly(harness, recorder, token);
    if (sequence < 3n) observeOnly(harness, observer, event(token));
    else {
      harness.faults.codec = 'output';
      try { expectStage(() => observeOnly(harness, observer, event(token)), 'encode_output'); }
      finally { harness.faults.codec = null; }
    }
    state = token.transition.state;
  }
  const frozen = encodedTrace(recorder);
  assert.equal(recorder.snapshot().termination, 'property_failed');
  assert.equal(recorder.evictedSteps(), 2n);
  assert.deepEqual(recorder.snapshot().initialChecks, [check('limit'), check('even-state')]);
  assert.deepEqual(recorder.snapshot().steps[0].checks[0], check('limit', 'failed', 'first property failure'));
  recorder.stopWithError(new ModelError('later observer failure'));
  const later = applyOnce(harness, state, 1, 4n);
  assert.throws(() => recordOnly(harness, recorder, later), /frozen/);
  observeOnly(harness, observer, event(later));
  observeOnly(harness, observer, {kind: 'message', level: LogLevel.Error, sequence: 4n, message: 'application continues after frozen recording'});
  assert.ok(observer.getRef().text().includes('sequence=4 '));
  assert.deepEqual(encodedTrace(recorder), frozen);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 4, state: 5, transition: 4, effects: 8});
  assert.equal(replaySuffix(recorder, {failAt: 3}).failure_reproduced, true);
});

test('N07 failed initial checkpoint logs at Error and stays the first failure without an initial-state call', () => {
  const harness = counter({failAt: 7});
  const initial = checkInitial(harness.model, 7);
  const recorder = Recorder.withCheckedInitialAt(initial, 99n, CONFIG, {maxSteps: 1});
  const observer = logger(LogLevel.Error);
  observeOnly(harness, observer, checkpoint(initial, 99n));
  assert.ok(observer.getRef().text().startsWith('Error '));
  assert.ok(observer.getRef().text().includes('check="limit":Failed("first property failure")'));
  const before = encodedTrace(recorder);
  recorder.stopWithError(new ModelError('secondary'));
  assert.deepEqual(encodedTrace(recorder), before);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 0, state: 1, transition: 0, effects: 0});
  assert.equal(replaySuffix(recorder, {failAt: 7}).failure_reproduced, true);
});

test('N07 candidate Recorder codec failure at full capacity cannot evict the coherent prefix or stop diagnostics', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 1});
  const observer = logger(LogLevel.Trace);
  const first = applyOnce(harness, 0, 1, 1n);
  recordOnly(harness, recorder, first);
  observeOnly(harness, observer, event(first));
  const prefix = recorder.snapshot();
  const candidate = applyOnce(harness, 1, 1, 2n);
  // Log the same authentic completed token before Recorder attempts admission.
  observeOnly(harness, observer, event(candidate));
  harness.faults.codec = 'output';
  try { assert.throws(() => recordOnly(harness, recorder, candidate), /diagnostic output codec failed/); }
  finally { harness.faults.codec = null; }
  samePrefix(recorder.snapshot(), prefix);
  assert.equal(recorder.snapshot().termination, 'model_error');
  assert.match(recorder.snapshot().error, /diagnostic output codec failed/);
  assert.equal(recorder.observedSteps(), 1n);
  assert.equal(recorder.evictedSteps(), 0n);
  const frozen = encodedTrace(recorder);
  recorder.stopWithError(new ModelError('later sink failure'));
  assert.throws(() => recordOnly(harness, recorder, candidate), /frozen/);
  const later = applyOnce(harness, 2, 1, 3n);
  observeOnly(harness, observer, event(later));
  assert.deepEqual(encodedTrace(recorder), frozen);
  assert.equal(observer.getRef().text().trim().split('\n').length, 3);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 3, state: 4, transition: 3, effects: 6});
  assert.equal(replaySuffix(recorder).steps_verified, 1);
});

const SAMPLED = [
  {origin: 0n, policy: {stateEvery: 2n, transitionChecks: true}, stateCalls: 1, edgeCalls: 1, skip: 'stateless.state_checks'},
  {origin: 1n, policy: {stateEvery: 2n, transitionChecks: true}, stateCalls: 2, edgeCalls: 1, skip: null},
  {origin: 0n, policy: {stateEvery: 1n, transitionChecks: false}, stateCalls: 2, edgeCalls: 0, skip: 'stateless.transition_checks'},
];
for (const sample of SAMPLED) test(`N07 sampled coverage is diagnostic only: origin ${sample.origin}, state period ${sample.policy.stateEvery}, transitions ${sample.policy.transitionChecks}`, () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitialAt(initial, sample.origin, CONFIG, {maxSteps: 1});
  const before = recorder.snapshot();
  const observer = logger(LogLevel.Warn);
  const token = applyOnce(harness, 0, 1, sample.origin + 1n, sample.policy);
  assert.equal(token.fullChecking, false);
  assert.deepEqual(token.checks.filter(item => item.status === 'skipped').map(item => item.id), sample.skip ? [sample.skip] : []);
  observeOnly(harness, observer, event(token));
  assert.equal(observer.getRef().text().startsWith('Warn '), sample.skip !== null);
  assert.throws(() => recordOnly(harness, recorder, token), /requires full checking/);
  samePrefix(recorder.snapshot(), before);
  assert.equal(recorder.snapshot().termination, 'model_error');
  assert.equal(recorder.observedSteps(), 0n);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 1, state: sample.stateCalls, transition: sample.edgeCalls, effects: 2});
  // Exact here concerns only the initial checkpoint, never the sampled turn.
  assert.equal(replaySuffix(recorder).steps_verified, 0);
});

test('N07 a missing same-state turn differs from skipped checking and freezes the complete prefix', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const origin = 9007199254741001n;
  const recorder = Recorder.withCheckedInitialAt(initial, origin, CONFIG, {maxSteps: 2});
  const observer = logger(LogLevel.Debug);
  const first = applyOnce(harness, 0, 0, origin + 1n);
  recordOnly(harness, recorder, first);
  observeOnly(harness, observer, event(first));
  const prefix = recorder.snapshot();
  const omitted = applyOnce(harness, 0, 0, origin + 2n);
  observeOnly(harness, observer, event(omitted));
  const next = applyOnce(harness, 0, 1, origin + 3n);
  observeOnly(harness, observer, event(next));
  for (const token of [first, omitted, next]) {
    assert.equal(token.fullChecking, true);
    assert.ok(token.checks.every(item => item.status === 'passed'));
  }
  assert.throws(() => recordOnly(harness, recorder, next), /sequence is missing or reordered/);
  samePrefix(recorder.snapshot(), prefix);
  assert.equal(recorder.observedSteps(), 1n);
  assert.equal(recorder.evictedSteps(), 0n);
  assert.equal(recorder.snapshot().termination, 'model_error');
  assert.equal(observer.getRef().text().trim().split('\n').length, 3);
  const frozen = encodedTrace(recorder);
  assert.throws(() => recordOnly(harness, recorder, omitted), /frozen/);
  assert.deepEqual(encodedTrace(recorder), frozen);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 3, state: 4, transition: 3, effects: 6});
  assert.equal(replaySuffix(recorder).steps_verified, 1);
});

test('N07 caller-made diagnostics and forged prototypes cannot authorize existing Recorder', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const token = applyOnce(harness, 0, 1, 1n);
  const diagnostic = event(token);
  const observer = logger(LogLevel.Trace);
  observeOnly(harness, observer, diagnostic);
  for (const forged of [diagnostic, token.observation(), {...diagnostic, model: harness.model, fullChecking: true}, Object.create(CheckedTurn.prototype)]) {
    const recorder = Recorder.withCheckedInitial(initial, CONFIG);
    const before = recorder.snapshot();
    assert.throws(() => recordOnly(harness, recorder, forged), /invalid checked turn token/);
    samePrefix(recorder.snapshot(), before);
    assert.equal(recorder.snapshot().termination, 'model_error');
    assert.equal(recorder.observedSteps(), 0n);
  }
  for (const forged of [{model: harness.model, state: 0, checks: initial.checks}, Object.create(CheckedInitial.prototype)]) {
    assert.throws(() => Recorder.withCheckedInitial(forged, CONFIG), /invalid checked initial token/);
  }
  assert.throws(() => { token.fullChecking = true; }, TypeError);
  assert.throws(() => { token.sequence = 9n; }, TypeError);
  assert.throws(() => token.checks.push(check('forged')), TypeError);
  // Runtime WeakMap authenticity is exercised here. This is an ordinary public
  // API integrity test, not a hostile-reflection or cryptographic security claim.
  const first = Recorder.withCheckedInitial(initial, CONFIG);
  const second = Recorder.withCheckedInitial(initial, CONFIG);
  recordOnly(harness, first, token);
  recordOnly(harness, second, token);
  assert.deepEqual(encodedTrace(first), encodedTrace(second), 'authentic token is reusable across independent consumers');
  assert.deepEqual(harness.calls, {initial: 0, reducer: 1, state: 2, transition: 1, effects: 2});
});

test('N07 runtime snapshot/level changes use absolute sequence and explicit checkpoint without backfill', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 7);
  const recorder = Recorder.withCheckedInitialAt(initial, 11n, CONFIG, {maxSteps: 2});
  const observer = logger(LogLevel.Off, SnapshotPolicy.Every(2n));
  observeOnly(harness, observer, checkpoint(initial, 11n));
  const first = applyOnce(harness, 7, 1, 12n);
  recordOnly(harness, recorder, first);
  observeOnly(harness, observer, event(first));
  assert.equal(observer.getRef().text(), '');
  const before = {...harness.encodes};
  observer.setLevel(LogLevel.Trace);
  // A supplied checkpoint is not another checkInitial operation. It uses the
  // authentic completed turn's state-check prefix at an odd sequence below.
  const second = applyOnce(harness, 8, 1, 13n);
  recordOnly(harness, recorder, second);
  const afterRecording = {...harness.encodes};
  observeOnly(harness, observer, event(second));
  observeOnly(harness, observer, {kind: 'checkpoint', sequence: second.sequence, state: second.transition.state, checks: second.checks.slice(0, second.stateCheckCount)});
  assert.deepEqual(delta(harness.encodes, afterRecording), {state: 1, input: 1, output: 2});
  assert.deepEqual(delta(afterRecording, before), {state: 2, input: 1, output: 2});
  const lines = observer.getRef().text().trim().split('\n');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].includes('post_state='), false);
  assert.ok(lines[1].includes('sequence=13 ') && lines[1].includes('state=hex:09000000'));
  assert.equal(observer.getRef().text().includes('sequence=12 '), false);
  observer.setSnapshots(SnapshotPolicy.None);
  const third = applyOnce(harness, 9, 1, 14n);
  recordOnly(harness, recorder, third);
  const beforeLog = {...harness.encodes};
  observeOnly(harness, observer, event(third));
  assert.deepEqual(delta(harness.encodes, beforeLog), {state: 0, input: 1, output: 2});
  assert.equal(observer.getRef().text().includes('post_state='), false);
  assert.deepEqual(harness.calls, {initial: 0, reducer: 3, state: 4, transition: 3, effects: 6});
  assert.equal(replaySuffix(recorder).steps_verified, 2);
});

test('N07 checker callback error yields diagnostics with no token and cannot replace Recorder prefix', () => {
  const harness = counter();
  const initial = checkInitial(harness.model, 0);
  const recorder = Recorder.withCheckedInitial(initial, CONFIG, {maxSteps: 1});
  const observer = logger(LogLevel.Error, SnapshotPolicy.None, new NoPayloads());
  const first = applyOnce(harness, 0, 1, 1n);
  recordOnly(harness, recorder, first);
  const prefix = recorder.snapshot();
  const originalCheck = harness.model.checkTransition;
  harness.model.checkTransition = (...args) => { originalCheck(...args); throw new Error('checker unavailable after complete state batch'); };
  const actual = harness.model.step(1, 1);
  for (const output of actual.outputs) { harness.calls.effects++; harness.effects.push(output); }
  let failedToken;
  assert.throws(() => { failedToken = checkTurnExact(harness.model, 1, 1, actual, 2n); }, /checker unavailable/);
  assert.equal(failedToken, undefined);
  assert.deepEqual(actual, accepted(2, [2, 102]));
  observeOnly(harness, observer, {kind: 'message', level: LogLevel.Error, sequence: 2n, message: 'checking unavailable; no complete token'});
  recorder.stopWithError(new ModelError('checking unavailable; no complete token'));
  samePrefix(recorder.snapshot(), prefix);
  assert.equal(recorder.snapshot().termination, 'model_error');
  assert.deepEqual(harness.calls, {initial: 0, reducer: 2, state: 3, transition: 2, effects: 4});
  assert.equal(replaySuffix(recorder).steps_verified, 1);
});
