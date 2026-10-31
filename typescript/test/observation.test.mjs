import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {
  TextObserver, NoPayloads, EncodedPayloads, LogLevel, SnapshotPolicy,
  DEFAULT_OBSERVATION_OPTIONS, TextObservationError, ObservationError, WithOracle, accepted, disposition, check, metadata,
  bindModelCodec, customValueSchema, checkInitial, checkTurnExact, Recorder, writeAuditTrace,
} from '../dist/index.js';

const utf8 = value => new TextEncoder().encode(value);
const config = {strategy: 'manual', seed: null, parameters: []};
const max = (1n << 64n) - 1n;
const stages = expected => error => error instanceof TextObservationError && error.stage === expected;
function memory() {
  return {bytes: [], calls: 0, flushes: 0,
    write(bytes) { this.calls++; for (const byte of bytes) this.bytes.push(byte); return bytes.length; },
    flush() { this.flushes++; },
    text() { return new TextDecoder().decode(Uint8Array.from(this.bytes)); }};
}
function model(extra = {}) {
  const calls = {initial: 0, step: 0, stateCheck: 0, transitionCheck: 0, effect: 0, clones: [], codecs: []};
  const no = kind => () => { calls[kind]++; throw Error(`unexpected ${kind}`); };
  const m = {
    initialState: no('initial'), step: no('step'), checkState: no('stateCheck'), checkTransition: no('transitionCheck'), effect: no('effect'),
    metadata: () => metadata('observation', 'fixture'),
    cloneState: value => { calls.clones.push(`state:${value}`); return value; },
    cloneInput: value => { calls.clones.push(`input:${value}`); return value; },
    cloneOutput: value => { calls.clones.push(`output:${value}`); return value; },
    encodeState: () => { throw Error('legacy state dispatch'); },
    encodeInput: () => { throw Error('legacy input dispatch'); },
    encodeOutput: () => { throw Error('legacy output dispatch'); },
    decodeState: () => { throw Error('unexpected decode'); }, decodeInput: () => { throw Error('unexpected decode'); },
    encodeStateInto(value, out) { calls.codecs.push(`state:${value}`); out.extend(Uint8Array.of(value)); },
    encodeInputInto(value, out) { calls.codecs.push(`input:${value}`); out.extend(Uint8Array.of(value)); },
    encodeOutputInto(value, out) { calls.codecs.push(`output:${value}`); out.extend(Uint8Array.of(value)); },
    ...extra,
  };
  return {m, calls};
}
function turn(sequence = 1n, checks = [], outputs = [2]) {
  return {kind: 'transition', sequence, before: 99, input: 1, transition: accepted(3, outputs), checks};
}
const message = (text = 'ok', level = LogLevel.Info, sequence = null) => ({kind: 'message', level, sequence, message: text});
const checkpoint = (sequence = 0n, checks = []) => ({kind: 'checkpoint', sequence, state: 3, checks});
function logger(options = {}, payloads = new EncodedPayloads(16), stream = 's') {
  const sink = memory(), log = new TextObserver(sink, stream, options, payloads);
  return {sink, log};
}
function inert(calls) {
  assert.deepEqual([calls.initial, calls.step, calls.stateCheck, calls.transitionCheck, calls.effect], [0, 0, 0, 0, 0]);
}

test('public oracle ObservationError retains actual transitions separately from text diagnostics', () => {
  const {m, calls} = model(), cause = Error('oracle advance failed'), actual = accepted(3, [2]);
  const oracle = {cloneHistory: value => value, advance() { throw cause; }};
  const paired = new WithOracle(m, oracle);
  assert.notEqual(ObservationError, TextObservationError);
  assert.throws(() => paired.observeTransition({model: 99, oracle: 0}, 1, actual), error => {
    assert.ok(error instanceof ObservationError); assert.ok(!(error instanceof TextObservationError));
    assert.equal(error.cause, cause); assert.deepEqual(error.transition, actual); return true;
  });
  const diagnostic = new TextObservationError('format_input', 'formatter failed');
  assert.equal(diagnostic.name, 'TextObservationError'); assert.ok(!(diagnostic instanceof ObservationError));
  inert(calls);
});

test('default Off is inert even for hostile model/event/payload accessors', () => {
  assert.deepEqual(DEFAULT_OBSERVATION_OPTIONS, {level: LogLevel.Off, snapshots: SnapshotPolicy.None, maxRecordBytes: 1048576});
  const hostile = new Proxy({}, {get() { throw Error('filtered object touched'); }});
  const {sink, log} = logger({}, hostile);
  log.observe(hostile, hostile);
  assert.equal(sink.calls, 0);
});

test('each threshold filters before cloning, codec dispatch or formatter access', () => {
  for (const level of [0, 1, 2, 3, 4, 5]) {
    const {m, calls} = model(), {sink, log} = logger({level, snapshots: SnapshotPolicy.EveryTransition});
    log.observe(m, turn(1n, [check('ok')]));
    assert.equal(sink.calls, level >= LogLevel.Debug ? 1 : 0);
    assert.deepEqual(calls.codecs, level === LogLevel.Trace ? ['input:1', 'output:2', 'state:3'] : []);
    assert.deepEqual(calls.clones, calls.codecs); inert(calls);
  }
  const {m} = model(), hostile = new Proxy({}, {get() { throw Error('formatter accessed'); }});
  const {log} = logger({level: LogLevel.Debug}, hostile);
  log.observe(m, turn());
});

test('failed checks outrank skipped checks in either order for checkpoints and turns', () => {
  const {m, calls} = model();
  for (const checks of [[check('p'), check('s', 'skipped', 'policy'), check('f', 'failed', 'bad')], [check('f', 'failed', 'bad'), check('s', 'skipped', 'policy')]]) {
    for (const event of [turn(1n, checks), checkpoint(0n, checks)]) {
      const {log, sink} = logger({level: LogLevel.Error}); log.observe(m, event);
      assert.match(sink.text(), /^Error /); assert.match(sink.text(), /Skipped\("policy"\)/); assert.match(sink.text(), /Failed\("bad"\)/);
      assert.doesNotMatch(sink.text(), /check="p"/);
    }
  }
  for (const level of [LogLevel.Error, LogLevel.Warn]) {
    const {log, sink} = logger({level}); log.observe(m, checkpoint(1n, [check('p'), check('s', 'skipped', 'policy')]));
    assert.equal(sink.calls, level === LogLevel.Warn ? 1 : 0);
  }
  inert(calls); assert.deepEqual(calls.codecs, []);
});

test('accepted, rejected and ignored turns remain ordinary Debug events', () => {
  const {m} = model();
  for (const kind of ['accepted', 'rejected', 'ignored']) {
    const event = turn(); event.transition = {...event.transition, disposition: disposition(kind, kind === 'accepted' ? '' : 'why')};
    const {log, sink} = logger({level: LogLevel.Info}); log.observe(m, event); assert.equal(sink.calls, 0);
    log.setLevel(LogLevel.Debug); log.observe(m, event); assert.match(sink.text(), /^Debug /); assert.doesNotMatch(sink.text(), /input=/);
  }
});

test('ordinary checkpoints are Trace; only configured Trace shows passed details and payloads', () => {
  const {m, calls} = model(), {log, sink} = logger({level: LogLevel.Debug, snapshots: SnapshotPolicy.EveryTransition});
  log.observe(m, checkpoint(0n, [check('p')])); assert.equal(sink.calls, 0);
  log.setLevel(LogLevel.Trace); log.observe(m, checkpoint(0n, [check('p')]));
  log.observe(m, turn(1n, [check('failed', 'failed', 'why'), check('p')]));
  assert.equal(sink.text(), 'Trace stream="s" event=checkpoint sequence=0 checks=1 check="p":Passed state=hex:03\n' +
    'Error stream="s" event=transition sequence=1 disposition=Accepted outputs=1 checks=2 check="failed":Failed("why") check="p":Passed input=hex:01 output[0]=hex:02 post_state=hex:03\n');
  assert.deepEqual(calls.codecs, ['state:3', 'input:1', 'output:2', 'state:3']);
});

test('start/finish are Info and explicit messages use their own level including Off', () => {
  for (const threshold of [0, 1, 2, 3, 4, 5]) {
    const {m, calls} = model(), {log, sink} = logger({level: threshold});
    log.observe(m, {kind: 'started', metadata: metadata('name', 'build'), config});
    for (const severity of [0, 1, 2, 3, 4, 5]) log.observe(m, message('explicit', severity));
    log.observe(m, {kind: 'finished', transitions: 0n, reason: 'done'});
    assert.equal((sink.text().match(/event=message/g) ?? []).length, threshold);
    assert.equal(sink.text().includes('event=started'), threshold >= LogLevel.Info);
    assert.equal(sink.text().includes('event=finished'), threshold >= LogLevel.Info);
    assert.deepEqual(calls.codecs, []); inert(calls);
  }
});

test('lifecycle text preserves metadata fields, ordered duplicate parameters and exact optional u64', () => {
  const {m} = model(), {log, sink} = logger({level: LogLevel.Info});
  log.observe(m, {kind: 'started', metadata: metadata('n', 'b'), config: {strategy: 'manual', seed: max, parameters: [['x', '1'], ['x', '2']]}});
  log.observe(m, message('x', LogLevel.Info, max));
  log.observe(m, {kind: 'finished', transitions: max, reason: 'done'});
  assert.equal(sink.text(), 'Info stream="s" event=started metadata=ModelMetadata { name: "n", model_version: 1, properties_version: 1, codec_version: 1, build: "b" } config=RunConfig { strategy: "manual", seed: Some(18446744073709551615), parameters: [("x", "1"), ("x", "2")] }\n' +
    'Info stream="s" event=message sequence=Some(18446744073709551615) message="x"\n' +
    'Info stream="s" event=finished transitions=18446744073709551615 reason="done"\n');
});

test('snapshot intervals use absolute bigint sequence, explicit checkpoints ignore divisibility', () => {
  const {m, calls} = model(), {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.Every(max)});
  log.observe(m, checkpoint(1n)); log.observe(m, turn(max - 1n, [], [8, 7])); log.observe(m, turn(max, [], []));
  assert.deepEqual(calls.codecs, ['state:3', 'input:1', 'output:8', 'output:7', 'input:1', 'state:3']);
  assert.equal((sink.text().match(/post_state=/g) ?? []).length, 1);
  log.setSnapshots(SnapshotPolicy.None); log.observe(m, checkpoint(max)); log.observe(m, turn(1n, [], []));
  assert.deepEqual(calls.codecs.slice(-1), ['input:1']);
});

test('runtime setters emit no history, and the before-state field is never read', () => {
  const {m, calls} = model(), {log, sink} = logger({snapshots: SnapshotPolicy.Every(2n)});
  const event = turn(2n); Object.defineProperty(event, 'before', {get() { throw Error('before read'); }});
  log.observe(m, event); log.setLevel(LogLevel.Trace); assert.equal(sink.calls, 0);
  log.observe(m, event); log.setSnapshots(SnapshotPolicy.None); log.observe(m, event);
  assert.deepEqual(calls.codecs, ['input:1', 'output:2', 'state:3', 'input:1', 'output:2']); inert(calls);
});

test('base model and custom payload formatting require no codec and do not clone borrowed payloads', () => {
  const base = new Proxy({}, {get() { throw Error('base model callback accessed'); }});
  const event = turn(); const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition}, new NoPayloads());
  log.observe(base, event);
  assert.match(sink.text(), /input=unavailable output\[0\]=unavailable post_state=unavailable/);
  const seen = [], format = (m, value, out) => { assert.equal(m, base); seen.push(value); out.writeAll('borrowed'); };
  const custom = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition}, {state: format, input: format, output: format});
  custom.log.observe(base, event); assert.deepEqual(seen, [event.input, event.transition.outputs[0], event.transition.state]);
});

test('library-owned strings escape controls while preserving common printable UTF-8', () => {
  const {m} = model(), {log, sink} = logger({level: LogLevel.Trace}, new NoPayloads(), 's\n"\\é');
  log.observe(m, message('\0\t\r\n\x1b\x7f\u2028😀', LogLevel.Info));
  assert.equal(sink.text(), 'Info stream="s\\n\\"\\\\é" event=message sequence=None message="\\0\\t\\r\\n\\u{1b}\\u{7f}\\u{2028}😀"\n');
  const event = turn(1n, [check('p\n', 'failed', 'bad\r\n')]); event.transition = {...event.transition, disposition: disposition('ignored', 'why\t"')};
  log.observe(m, event); assert.equal(sink.text().split('\n').length, 3);
  assert.match(sink.text(), /Ignored\("why\\t\\""\)/); assert.match(sink.text(), /check="p\\n":Failed\("bad\\r\\n"\)/);
});

test('custom payloads are deliberately unsanitized and byte writes may be binary', () => {
  const {m} = model(), formatter = {input(_m, _v, out) { out.writeAll('a\nb'); out.writeAll(Uint8Array.of(0xff)); }, output() {}, state() {}};
  const {log, sink} = logger({level: LogLevel.Trace}, formatter); log.observe(m, turn(1n, [], []));
  assert.equal(sink.bytes.at(-2), 0xff); assert.equal(sink.bytes.filter(b => b === 10).length, 2);
});

test('record bounds count UTF-8 and final LF exactly; oversized records recover without destination bytes', () => {
  const expected = 'Info stream="s" event=message sequence=None message="é😀"\n';
  const limit = utf8(expected).length, {m} = model();
  for (const maximum of [limit - 1, limit]) {
    const {log, sink} = logger({level: LogLevel.Info, maxRecordBytes: maximum});
    if (maximum === limit) { log.observe(m, message('é😀')); assert.deepEqual(sink.bytes, [...utf8(expected)]); }
    else { assert.throws(() => log.observe(m, message('é😀')), stages('record_limit')); assert.equal(sink.calls, 0); log.observe(m, message('')); assert.equal(sink.calls, 1); }
    assert.equal(log.isPoisoned(), false);
  }
  const {log, sink} = logger({maxRecordBytes: 0}); log.observe(m, turn()); log.setLevel(LogLevel.Info);
  assert.throws(() => log.observe(m, message()), stages('record_limit')); assert.equal(sink.calls, 0);
});

test('record bound is sticky when a formatter catches rejected writes and flushes', () => {
  const {m} = model(); let rejects = 0;
  const formatter = {input(_m, _i, out) {
    for (const write of [() => out.writeAll('x'.repeat(1024)), () => out.writeAll('x'), () => out.flush()]) {
      try { write(); } catch { rejects++; }
    }
  }, state() {}, output() {}};
  const {log, sink} = logger({level: LogLevel.Trace, maxRecordBytes: 256}, formatter);
  assert.throws(() => log.observe(m, turn(1n, [], [])), stages('record_limit')); assert.equal(rejects, 3); assert.equal(sink.calls, 0);
  log.setLevel(LogLevel.Debug); log.observe(m, turn(2n, [], [])); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
});

test('UTF-8 scratch boundaries preserve surrogate pairs and multibyte chunks exactly', () => {
  const {m} = model();
  for (const text of ['x'.repeat(511) + '😀' + 'é'.repeat(600), '界'.repeat(1200), '😀'.repeat(600)]) {
    let written;
    const formatter = {input(_m, _v, out) { written = out.write(text); }, output() {}, state() {}};
    const expected = `Debug stream="s" event=transition sequence=1 disposition=Accepted outputs=0 checks=0 input=${text}\n`;
    const {log, sink} = logger({level: LogLevel.Trace, maxRecordBytes: utf8(expected).length}, formatter);
    log.observe(m, turn(1n, [], [])); assert.equal(written, utf8(text).length); assert.deepEqual(sink.bytes, [...utf8(expected)]);
  }
});

test('formatter errors preserve their payload stage, suppress partial publication and recover', () => {
  const {m} = model();
  for (const failing of ['input', 'output', 'state']) {
    const seen = [], format = kind => (_m, _v, out) => { seen.push(kind); out.writeAll('partial'); if (kind === failing) throw Error('format failed'); };
    const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition}, {state: format('state'), input: format('input'), output: format('output')});
    assert.throws(() => log.observe(m, turn()), stages(`format_${failing}`)); assert.equal(sink.calls, 0);
    assert.deepEqual(seen, ['input', 'output', 'state'].slice(0, ['input', 'output', 'state'].indexOf(failing) + 1));
    log.observe(m, message()); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
  }
});

test('public staged errors thrown by formatters get the current payload stage without poisoning', () => {
  const {m, calls} = model();
  for (const failing of ['input', 'output', 'state']) {
    for (const stage of ['format', 'record_limit', 'format_state', 'format_input', 'format_output',
      'encode_input', 'encode_output', 'encode_state', 'sink_write', 'sink_flush', 'sink_poisoned']) {
      const foreign = new TextObservationError(stage, 'foreign'); let fail = true;
      const format = kind => (_m, _v, out) => { out.writeAll('ok'); if (fail && kind === failing) throw foreign; };
      const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition},
        {input: format('input'), output: format('output'), state: format('state')});
      assert.throws(() => log.observe(m, turn()), error => stages(`format_${failing}`)(error) && error !== foreign);
      assert.equal(sink.calls, 0); assert.deepEqual(sink.bytes, []); assert.equal(log.isPoisoned(), false);
      log.setLevel(LogLevel.Off); log.observe(m, turn()); assert.equal(sink.calls, 0);
      fail = false; log.setLevel(LogLevel.Trace); log.observe(m, turn());
      assert.equal(sink.text(), 'Debug stream="s" event=transition sequence=1 disposition=Accepted outputs=1 checks=0 input=ok output[0]=ok post_state=ok\n');
      assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
    }
  }
  inert(calls); assert.deepEqual(calls.codecs, []);
});

test('nested observer failures belong to the outer formatter and leave its writer healthy', () => {
  const {m, calls} = model();
  for (const failing of ['input', 'output', 'state']) {
    for (const stage of ['encode_state', 'record_limit', 'sink_write', 'sink_flush', 'sink_poisoned']) {
      const nestedModel = model(stage === 'encode_state' ? {encodeStateInto() { throw Error('nested codec'); }} : {});
      const nestedSink = memory();
      if (stage === 'sink_write') nestedSink.write = function () { this.calls++; throw Error('nested writer'); };
      if (stage === 'sink_flush' || stage === 'sink_poisoned') nestedSink.flush = function () { this.flushes++; throw Error('nested flush'); };
      const nested = new TextObserver(nestedSink, 'nested', {level: LogLevel.Trace,
        snapshots: SnapshotPolicy.EveryTransition, maxRecordBytes: stage === 'record_limit' ? 0 : 256}, new EncodedPayloads(16));
      if (stage === 'sink_poisoned') assert.throws(() => nested.flush(), stages('sink_flush'));
      let fail = true, nestedError;
      const format = kind => (_m, _v, out) => {
        out.writeAll('ok');
        if (!fail || kind !== failing) return;
        try {
          if (stage === 'sink_flush') nested.flush();
          else nested.observe(nestedModel.m, checkpoint());
        } catch (error) { nestedError = error; throw error; }
      };
      const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition},
        {input: format('input'), output: format('output'), state: format('state')});
      assert.throws(() => log.observe(m, turn()), error => stages(`format_${failing}`)(error) && error !== nestedError);
      assert.ok(stages(stage)(nestedError));
      assert.equal(nested.isPoisoned(), stage.startsWith('sink_'));
      assert.equal(sink.calls, 0); assert.deepEqual(sink.bytes, []); assert.equal(log.isPoisoned(), false);
      log.setLevel(LogLevel.Off); log.observe(m, turn()); assert.equal(sink.calls, 0);
      fail = false; log.setLevel(LogLevel.Trace); log.observe(m, turn());
      assert.equal(sink.text(), 'Debug stream="s" event=transition sequence=1 disposition=Accepted outputs=1 checks=0 input=ok output[0]=ok post_state=ok\n');
      assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false); inert(nestedModel.calls);
    }
  }
  inert(calls); assert.deepEqual(calls.codecs, []);
});

test('genuine codec attribution cannot escape its callback or survive into a later call', () => {
  const {m} = model(), encoded = new EncodedPayloads(0); let retained, fail = true;
  const {log, sink} = logger({level: LogLevel.Trace}, {
    input(model, value, out) {
      if (fail && retained) throw retained;
      if (fail) {
        try { encoded.input(model, value, out); } catch (error) { retained = error; }
        assert.ok(stages('encode_input')(retained));
      }
      out.writeAll('ok');
    },
    output(_m, _v, out) { if (fail) throw retained; out.writeAll('ok'); }, state() {},
  });
  assert.throws(() => log.observe(m, turn()), stages('format_output'));
  assert.throws(() => log.observe(m, turn()), stages('format_input'));
  assert.equal(sink.calls, 0); assert.deepEqual(sink.bytes, []); assert.equal(log.isPoisoned(), false);
  fail = false; log.observe(m, turn()); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
});

test('sticky current record failure outranks a later forged stage and cannot authorize a later call', () => {
  const {m} = model(); let phase = 'overflow', retained;
  const {log, sink} = logger({level: LogLevel.Trace, maxRecordBytes: 256}, {
    input(_m, _v, out) {
      if (phase === 'overflow') {
        try { out.writeAll('x'.repeat(1024)); } catch (error) { retained = error; }
        throw new TextObservationError('sink_poisoned', 'foreign');
      }
      if (phase === 'retained') throw retained;
      out.writeAll('ok');
    }, output() {}, state() {},
  });
  assert.throws(() => log.observe(m, turn(1n, [], [])), stages('record_limit'));
  assert.ok(stages('record_limit')(retained));
  phase = 'retained'; assert.throws(() => log.observe(m, turn(1n, [], [])), stages('format_input'));
  assert.equal(sink.calls, 0); assert.deepEqual(sink.bytes, []); assert.equal(log.isPoisoned(), false);
  log.setLevel(LogLevel.Off); log.observe(m, turn()); assert.equal(sink.calls, 0);
  phase = 'recover'; log.setLevel(LogLevel.Trace); log.observe(m, turn(1n, [], []));
  assert.match(sink.text(), /input=ok\n$/); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
});

test('codec failures preserve each stage and bounded failure cannot be swallowed into success', () => {
  for (const kind of ['input', 'output', 'state']) {
    const name = `encode${kind[0].toUpperCase()}${kind.slice(1)}Into`, {m} = model({[name]() { throw Error('codec failed'); }});
    const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition});
    assert.throws(() => log.observe(m, turn()), error => stages(`encode_${kind}`)(error) && error.message.includes('codec failed'));
    assert.equal(sink.calls, 0); log.observe(m, message()); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
  }
  let fail = true;
  const {m} = model({encodeInputInto(_value, out) {
    if (fail) { try { out.extend(Uint8Array.of(1, 2)); } catch {} try { out.extend(Uint8Array.of(3)); } catch {} }
    else out.extend(Uint8Array.of(4));
  }}), {log, sink} = logger({level: LogLevel.Trace}, new EncodedPayloads(1));
  assert.throws(() => log.observe(m, turn(1n, [], [])), stages('encode_input')); assert.equal(sink.calls, 0);
  fail = false; log.observe(m, turn(2n, [], [])); assert.match(sink.text(), /input=hex:04/);
});

test('bounded legacy fallback allocates before admission and recovers on the next event', () => {
  let allocations = 0, bytes = Uint8Array.of(1, 2);
  const {m} = model({encodeInputInto: undefined, encodeInput() { allocations++; return bytes; }});
  const {log, sink} = logger({level: LogLevel.Trace}, new EncodedPayloads(1));
  assert.throws(() => log.observe(m, turn(1n, [], [])), stages('encode_input')); assert.equal(allocations, 1); assert.equal(sink.calls, 0);
  bytes = Uint8Array.of(10); log.observe(m, turn(2n, [], [])); assert.equal(allocations, 2); assert.match(sink.text(), /input=hex:0a/);
});

test('declaration codec dispatch preserves receivers, version gate, output-only capability and callback order', () => {
  const callbacks = []; let decoded = 0;
  class Application {
    #version = 1;
    metadata() { return {...metadata('bound', 'fixture'), codec_version: this.#version}; }
    setVersion(v) { this.#version = v; }
    cloneState(v) { assert.equal(this.#version, this.#version); return v; }
    cloneInput(v) { assert.equal(this.#version, this.#version); return v; }
    cloneOutput(v) { assert.equal(this.#version, this.#version); return v; }
    initialState() { throw Error('initial'); } step() { throw Error('step'); } checkState() { throw Error('check'); }
    encodeInput() { throw Error('legacy'); }
  }
  const schema = (kind, decode) => customValueSchema({name: kind,
    encodeInto(v, out) { callbacks.push(`${kind}:${v}`); out.extend(Uint8Array.of(v)); },
    ...(decode ? {decodeFrom() { decoded++; throw Error('observation decoded a payload'); }} : {})});
  const original = new Application(), m = bindModelCodec(original, {state: schema('state', true), input: schema('input', true), output: schema('output', false), decodeLimits() { decoded++; throw Error('observation requested decode limits'); }});
  const {log, sink} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition});
  log.observe(m, turn(1n, [], [255, 0])); assert.deepEqual(callbacks, ['input:1', 'output:255', 'output:0', 'state:3']); assert.equal(decoded, 0);
  original.setVersion(2); const old = sink.text();
  assert.throws(() => log.observe(m, turn()), error => stages('encode_input')(error) && /derive version/.test(error.message)); assert.equal(sink.text(), old);
  original.setVersion(1); log.observe(m, turn()); assert.equal(log.isPoisoned(), false); assert.equal(decoded, 0);
});

test('hostile thrown values cannot replace error stages or prevent later recovery', () => {
  const badMessage = new Error('old'); Object.defineProperty(badMessage, 'message', {get() { throw Error('diagnostic getter'); }});
  const hostile = [badMessage, {toString() { throw Error('conversion'); }}, new Proxy({}, {getPrototypeOf() { throw Error('prototype'); }, get() { throw Error('property'); }}), null, 'plain failure'];
  const {m} = model();
  for (const error of hostile) {
    const {log, sink} = logger({level: LogLevel.Trace}, {input() { throw error; }, output() {}, state() {}});
    assert.throws(() => log.observe(m, turn()), stages('format_input')); assert.equal(sink.calls, 0);
    log.observe(m, message()); assert.equal(sink.calls, 1); assert.equal(log.isPoisoned(), false);
  }
  const sink = memory(); sink.write = () => { throw badMessage; };
  const log = new TextObserver(sink, 's', {level: LogLevel.Info});
  assert.throws(() => log.observe(m, message()), stages('sink_write')); assert.equal(log.isPoisoned(), true);
});

test('hostile codec diagnostics and retained encode leases remain contained', () => {
  let retained, fail = true;
  const hostile = new Proxy({}, {get() { throw Error('getter'); }, getPrototypeOf() { throw Error('prototype'); }});
  const {m} = model({encodeInputInto(_value, out) { retained = out; out.extend(Uint8Array.of(7)); if (fail) throw hostile; }});
  const {log, sink} = logger({level: LogLevel.Trace});
  assert.throws(() => log.observe(m, turn(1n, [], [])), stages('encode_input')); assert.equal(sink.calls, 0);
  assert.throws(() => retained.extend(Uint8Array.of(8)), /lease expired/);
  fail = false; log.observe(m, turn(2n, [], [])); assert.match(sink.text(), /input=hex:07/);
});

test('formatter writer expires before rendering a thrown diagnostic', () => {
  const {m} = model(); let retained, rendered = 0, expired = false;
  const failure = new Error('formatter');
  Object.defineProperty(failure, 'message', {get() {
    rendered++;
    assert.throws(() => retained.writeAll('late'), /lease expired/);
    expired = true;
    return 'formatter failed';
  }});
  const {log, sink} = logger({level: LogLevel.Trace}, {
    input(_m, _v, out) { retained = out; throw failure; }, output() {}, state() {},
  });
  assert.throws(() => log.observe(m, turn()), stages('format_input'));
  assert.equal(rendered, 1); assert.equal(expired, true); assert.equal(sink.calls, 0); assert.equal(log.isPoisoned(), false);
  log.observe(m, message()); assert.equal(sink.calls, 1);
});

test('short writes and EINTR retries deliver the exact complete record without implicit flush', () => {
  const sink = memory(), original = sink.write; let attempts = 0;
  sink.write = function (bytes) { if (++attempts % 3 === 1) throw Object.assign(Error('interrupted'), {code: 'EINTR'}); return original.call(this, bytes.subarray(0, 3)); };
  const {m} = model(), expected = 'Info stream="s" event=message sequence=None message="é😀"\n';
  const log = new TextObserver(sink, 's', {level: LogLevel.Info, maxRecordBytes: utf8(expected).length});
  log.observe(m, message('é😀')); assert.deepEqual(sink.bytes, [...utf8(expected)]); assert.ok(sink.calls > 1); assert.equal(sink.flushes, 0);
  log.flush(); assert.equal(sink.flushes, 1); assert.equal(log.getRef(), sink); assert.equal(log.intoInner(), sink); assert.equal(sink.flushes, 1);
  assert.throws(() => log.observe(m, message()), /ownership transferred/); assert.throws(() => log.getRef(), /ownership transferred/);
});

test('writer prefix errors poison permanently including Off and suppress later callbacks', () => {
  const sink = memory(), original = sink.write;
  sink.write = function (bytes) { if (this.calls) throw Error('disk full'); return original.call(this, bytes.subarray(0, 5)); };
  const {m, calls} = model(), log = new TextObserver(sink, 's', {level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition}, new EncodedPayloads(16));
  assert.throws(() => log.observe(m, turn()), stages('sink_write')); assert.equal(sink.text(), 'Debug');
  const before = [...calls.codecs]; log.setLevel(LogLevel.Off);
  assert.throws(() => log.observe(m, turn()), stages('sink_poisoned')); assert.throws(() => log.flush(), stages('sink_poisoned'));
  assert.deepEqual(calls.codecs, before); assert.equal(sink.flushes, 0); assert.equal(log.intoInner(), sink);
});

test('invalid write counts and asynchronous writers are permanent sink failures', async () => {
  const {m} = model();
  for (const returned of [0, -1, 1.5, Infinity, NaN, true, undefined, 100000, Promise.reject(Error('async writer'))]) {
    const sink = memory(); sink.write = () => returned;
    const log = new TextObserver(sink, 's', {level: LogLevel.Info});
    assert.throws(() => log.observe(m, message()), stages('sink_write')); assert.equal(log.isPoisoned(), true);
  }
  await new Promise(resolve => setImmediate(resolve));
});

test('flush failure poisons even a never-enabled observer; taking ownership never flushes', () => {
  const {m} = model(), sink = memory(); sink.flush = function () { this.flushes++; throw Error('flush failed'); };
  const log = new TextObserver(sink, 's'); assert.throws(() => log.flush(), stages('sink_flush'));
  assert.throws(() => log.observe(m, turn()), stages('sink_poisoned')); assert.throws(() => log.flush(), stages('sink_poisoned'));
  assert.equal(sink.flushes, 1); assert.equal(log.intoInner(), sink); assert.equal(sink.flushes, 1);
  const healthy = new TextObserver(memory(), 's'); assert.equal(healthy.intoInner().flushes, 0);
});

test('asynchronous codec and flush callbacks reject at their stage', async () => {
  const {m} = model({encodeInputInto() { return Promise.reject(Error('async codec')); }});
  const {log, sink} = logger({level: LogLevel.Trace});
  assert.throws(() => log.observe(m, turn()), stages('encode_input')); assert.equal(sink.calls, 0); assert.equal(log.isPoisoned(), false);
  const destination = memory(); destination.flush = () => Promise.reject(Error('async flush'));
  const observer = new TextObserver(destination, 's'); assert.throws(() => observer.flush(), stages('sink_flush')); assert.equal(observer.isPoisoned(), true);
  await new Promise(resolve => setImmediate(resolve));
});

test('formatter leases expire on success/error; asynchronous formatters are rejected without leaking rejections', async () => {
  const {m} = model(); let retained, late;
  const {log, sink} = logger({level: LogLevel.Trace}, {input(_m, _v, out) { retained = out; out.writeAll('ok'); }, output() {}, state() {}});
  log.observe(m, turn(1n, [], [])); assert.throws(() => retained.writeAll('late'), /lease expired/);
  const failing = logger({level: LogLevel.Trace}, {input(_m, _v, out) { late = out; return Promise.reject(Error('async formatter')); }, output() {}, state() {}});
  assert.throws(() => failing.log.observe(m, turn()), stages('format_input')); assert.equal(failing.sink.calls, 0);
  assert.throws(() => late.flush(), /lease expired/); failing.log.observe(m, message());
  await new Promise(resolve => setImmediate(resolve)); assert.equal(sink.calls, 1);
});

test('observer and payload encoding reject reentrant mutation/transfer without corrupting the outer record', () => {
  const {m} = model(); let log, guarded = 0;
  const format = {input(_m, _v, out) {
    for (const operation of [() => log.observe(m, message()), () => log.flush(), () => log.intoInner(), () => log.setLevel(LogLevel.Off), () => log.setSnapshots(SnapshotPolicy.None)]) {
      assert.throws(operation, /already in progress/); guarded++;
    }
    out.writeAll('outer');
  }, output() {}, state() {}};
  const setup = logger({level: LogLevel.Trace}, format); log = setup.log;
  log.observe(m, turn(1n, [], [])); assert.equal(guarded, 5); assert.match(setup.sink.text(), /input=outer\n$/);
  const payloads = new EncodedPayloads(1);
  const nested = model({encodeInputInto(_v, out) {
    assert.throws(() => payloads.state(m, 4, {writeAll() { throw Error('nested output'); }}), /already in progress/);
    out.extend(Uint8Array.of(1));
  }});
  const second = logger({level: LogLevel.Trace}, payloads); second.log.observe(nested.m, turn(1n, [], [])); assert.match(second.sink.text(), /input=hex:01/);
});

test('exact u64/config validation rejects rounding and policies are captured before callbacks', () => {
  for (const value of [0n, -1n, 1n << 64n, 1, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => SnapshotPolicy.Every(value));
  const {m} = model(), {log, sink} = logger({level: LogLevel.Trace});
  for (const value of [0n, -1n, 1n << 64n, 1, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => log.observe(m, turn(value)), /u64 bigint/);
  for (const value of [-1n, 1n << 64n, 0]) assert.throws(() => log.observe(m, checkpoint(value)), /u64 bigint/);
  assert.equal(sink.calls, 0); log.observe(m, checkpoint(0n));
  for (const maxRecordBytes of [-1, 1.5, Infinity, 2 ** 53]) assert.throws(() => new TextObserver(memory(), 's', {maxRecordBytes}));
  const snapshots = {kind: 'every', every: 2n}, options = {level: LogLevel.Trace, snapshots, maxRecordBytes: 256};
  const selected = logger(options); snapshots.every = 3n; options.level = LogLevel.Off; options.maxRecordBytes = 0;
  selected.log.observe(m, turn(2n)); assert.match(selected.sink.text(), /post_state=hex:03/);
});

test('diagnostic events accept gaps and forged checks but cannot authorize checked recording', () => {
  let checks = 0;
  const {m, calls} = model({checkState() { checks++; return [check('state')]; }, checkTransition() { checks++; return [check('edge')]; }});
  const initial = checkInitial(m, 0), token = checkTurnExact(m, 0, 1, accepted(1), 1n);
  const recorder = Recorder.withCheckedInitial(initial, config), badRecorder = Recorder.withCheckedInitial(initial, config);
  const count = checks, {log} = logger({level: LogLevel.Trace, snapshots: SnapshotPolicy.EveryTransition});
  const forged = {...token.observation(), kind: 'transition', sequence: 99n, checks: [check('invented')]};
  log.observe(m, forged); log.observe(m, {...forged, sequence: 1n}); log.observe(m, forged);
  assert.equal(checks, count); recorder.observeChecked(token); assert.equal(checks, count);
  assert.throws(() => badRecorder.observeChecked(forged), /invalid checked turn token/); assert.equal(badRecorder.retainedSteps(), 0);
  assert.equal(recorder.retainedSteps(), 1); inert(calls);
});

test('logging verbosity leaves checked recording bytes and execution/check counts unchanged', () => {
  let baseline;
  for (const level of [0, 1, 2, 3, 4, 5]) {
    let checks = 0;
    const {m, calls} = model({checkState() { checks++; return [check('s')]; }, checkTransition() { checks++; return [check('t')]; }});
    const recorder = Recorder.withCheckedInitial(checkInitial(m, 0), config, {maxSteps: 1});
    const {log} = logger({level, snapshots: SnapshotPolicy.EveryTransition});
    for (let i = 1; i <= 3; i++) {
      const token = checkTurnExact(m, i - 1, 1, accepted(i, [i]), BigInt(i)); recorder.observeChecked(token);
      log.observe(m, {kind: 'transition', ...token.observation()});
    }
    const bytes = writeAuditTrace(recorder.snapshot()); if (baseline) assert.deepEqual(bytes, baseline); else baseline = bytes;
    assert.equal(checks, 7); assert.equal(recorder.evictedSteps(), 2n); inert(calls);
  }
});

test('public TypeScript declarations support opaque base models and require codecs only for encoded payloads', () => {
  const directory = mkdtempSync(join(tmpdir(), 'stateless-observation-types-'));
  try {
    const entry = fileURLToPath(new URL('../dist/index.js', import.meta.url));
    const source = `import {TextObserver,TextObservationError,ObservationError,NoPayloads,EncodedPayloads,LogLevel,SnapshotPolicy,type TextObservationErrorStage,type Transition,type Model,type CodecModel,type Event,type Observer,type PayloadFormat,type ObservationWriter} from ${JSON.stringify(entry)};
type Opaque={value:number}; declare const base:Model<Opaque,Opaque,Opaque>; declare const codec:CodecModel<Opaque,Opaque,Opaque>;
declare const writer:ObservationWriter; declare const event:Event<Opaque,Opaque,Opaque>;
declare const actual:Transition<Opaque,Opaque>;
const oracleFailure:ObservationError<Opaque,Opaque>=new ObservationError(new Error('oracle'),actual);
const retained:Transition<Opaque,Opaque>=oracleFailure.transition;
const diagnostic=new TextObservationError('format_input','formatter');
const stage:TextObservationErrorStage=diagnostic.stage;
const options={level:LogLevel.Trace,snapshots:SnapshotPolicy.Every(18446744073709551615n)};
new TextObserver<Opaque,Opaque,Opaque>(writer,'s',options).observe(base,event);
const plain:Observer<Opaque,Opaque,Opaque>=new TextObserver<Opaque,Opaque,Opaque>(writer,'s',options,new NoPayloads()); plain.observe(base,event);
const custom:PayloadFormat<Opaque,Opaque,Opaque>={state:(_m,v,out)=>{out.writeAll(String(v.value));},input:(_m,v,out)=>{out.writeAll(String(v.value));},output:(_m,v,out)=>{out.writeAll(String(v.value));}};
new TextObserver<Opaque,Opaque,Opaque>(writer,'s',options,custom).observe(base,event);
const encoded=new TextObserver<Opaque,Opaque,Opaque,CodecModel<Opaque,Opaque,Opaque>>(writer,'s',options,new EncodedPayloads<Opaque,Opaque,Opaque>(16)); encoded.observe(codec,event);
const inferredEncoded=new TextObserver(writer,'s',options,new EncodedPayloads<Opaque,Opaque,Opaque>(16)); inferredEncoded.observe(codec,event);
const codecObserver:Observer<Opaque,Opaque,Opaque,CodecModel<Opaque,Opaque,Opaque>>=encoded; codecObserver.observe(codec,event);
const baseAsCodecObserver:Observer<Opaque,Opaque,Opaque,CodecModel<Opaque,Opaque,Opaque>>=plain; baseAsCodecObserver.observe(codec,event);
// @ts-expect-error Three explicit generics default to the codec-free Model, which cannot satisfy EncodedPayloads.
new TextObserver<Opaque,Opaque,Opaque>(writer,'s',options,new EncodedPayloads<Opaque,Opaque,Opaque>(16));
// @ts-expect-error Codec-only payloads cannot widen to a base-model formatter.
const basePayloads:PayloadFormat<Opaque,Opaque,Opaque>=new EncodedPayloads<Opaque,Opaque,Opaque>(16);
// @ts-expect-error The concrete NoPayloads type must not erase an encoded formatter's codec requirement.
const widenedNoPayloads:NoPayloads=new EncodedPayloads<Opaque,Opaque,Opaque>(16);
// @ts-expect-error A concrete codec observer cannot widen to accept codec-free models.
const widenedConcrete:Observer<Opaque,Opaque,Opaque>=encoded;
// @ts-expect-error An interface-typed codec observer must preserve the same model requirement.
const widenedInterface:Observer<Opaque,Opaque,Opaque>=codecObserver;
// @ts-expect-error Encoded observer requires codec hooks.
encoded.observe(base,event);
// @ts-expect-error Inference also retains the encoded formatter's codec requirement.
inferredEncoded.observe(base,event);
// @ts-expect-error Exact intervals never accept floating point sequences.
SnapshotPolicy.Every(2);
`;
    const path = join(directory, 'consumer.mts'); writeFileSync(path, source);
    const tsc = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url));
    const result = spawnSync(process.execPath, [tsc, '--noEmit', '--strict', '--target', 'es2022', '--module', 'nodenext', '--moduleResolution', 'nodenext', path], {encoding: 'utf8'});
    assert.equal(result.status, 0, result.stdout + result.stderr);
  } finally { rmSync(directory, {recursive: true, force: true}); }
});
