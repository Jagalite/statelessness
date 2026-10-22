import test from 'node:test';
import assert from 'node:assert/strict';
import {accepted, check, ModelError} from '../dist/index.js';
import {DebugSession} from '../dist/debug_session.js';
import {ProtocolSession, encodeRequest, decodeRequest, decodeResponse, escapeField, writeFrame, FrameReader, serve} from '../dist/debug_protocol.js';

const MAX = 18446744073709551615n;
const limits = {maxTurns: 100000n, maxContinue: 1000, maxBreakpoints: 64, maxCandidates: 1000};
function fixture(authority = {}, protocolLimits = {}, overrides = {}) {
  const calls = [];
  const model = {
    initialState() { calls.push('initial'); return 0n; },
    cloneState: value => value, cloneInput: value => value, cloneOutput: value => value,
    step(state, input) {
      calls.push(`step ${state} ${input}`);
      if (input === 3) throw new ModelError('private reducer detail');
      if (input === 1) return accepted(state + 1n, [state + 1n]);
      return {state, outputs: [], disposition: {kind: input === 0 ? 'ignored' : 'rejected', reason: 'private reason'}};
    },
    checkState(state) {
      calls.push(`state ${state}`);
      return [state <= 2n ? check('bounded') : check('bounded', 'failed', 'private state detail')];
    },
    checkTransition(before, input, transition) {
      calls.push(`transition ${before} ${input} ${transition.state}`);
      return [check('transition')];
    },
    encodeOutputInto(output, buffer) {
      const bytes = new Uint8Array(8);
      new DataView(bytes.buffer).setBigUint64(0, output, true);
      buffer.extend(bytes);
    },
    ...overrides,
  };
  const session = new DebugSession('wire\té😀', model, {kind: 'unrestricted', label: 'out-of-domain-injection'}, limits);
  const endpoint = new ProtocolSession(session, bytes => {
    calls.push(`decode ${Buffer.from(bytes).toString('hex')}`);
    if (bytes.length !== 1 || bytes[0] > 3) throw new ModelError('invalid fixture input');
    return bytes[0];
  }, {deliverInputs: true, ...authority}, protocolLimits);
  let next = 0n;
  const request = (command, extra = {}) => ({version: 1, session: session.id, epoch: endpoint.epoch,
    requestId: ++next, revision: session.revision, configuration: 0n, command, ...extra});
  const send = (command, extra = {}) => decodeResponse(endpoint.handlePayload(encodeRequest(request(command, extra))));
  return {session, endpoint, calls, request, send};
}
function fields(response) {
  assert.equal(response.error, null);
  assert.equal(new Set(response.fields.map(([key]) => key)).size, response.fields.length);
  return Object.fromEntries(response.fields);
}

test('status returns the complete public Rust field schema and actual configured authority/limits', () => {
  const f = fixture({deliverInputs: false, readExactValues: true}, {maxCacheEntries: 1});
  assert.deepEqual(fields(f.send({kind: 'status'}, {revision: MAX, configuration: MAX, epoch: 0n})), {
    mode: 'enum:simulation', phase: 'enum:Ready', sequence: 'u64:0', environment: 'string:out-of-domain-injection',
    'capability.input_delivery': 'bool:false', 'capability.inspection': 'bool:false',
    'capability.watch_configuration': 'bool:false', 'capability.exact_values': 'bool:true',
    'capability.live_control': 'bool:false', 'capability.live_restore': 'bool:false', 'capability.effects': 'bool:false',
    'capability.probe_queries': 'bool:false', 'capability.probe_configuration': 'bool:false',
    'capability.trace_export': 'bool:false', 'capability.effect_telemetry': 'bool:false', 'capability.metric_queries': 'bool:false',
    'limits.telemetry_snapshot_bytes': 'u64:8388608', 'limits.frame_bytes': 'u64:65536',
    'limits.dedup_entries': 'u64:1', 'limits.dedup_bytes': 'u64:4194304', 'limits.page_size': 'u64:100',
    'events.dropped': 'u64:0', stop: 'enum:none',
  });
  assert.deepEqual(f.calls, ['initial', 'state 0']);
});

test('step/cancel authority precedes revision, and terminal steps still decode before ended', () => {
  const readonly = fixture({deliverInputs: false});
  for (const command of [{kind: 'step', bytes: Uint8Array.of(1)}, {kind: 'cancel'}]) {
    assert.equal(readonly.send(command, {revision: MAX}).error, 'unauthorized');
  }
  assert.deepEqual(readonly.calls, ['initial', 'state 0']);
  const f = fixture();
  assert.deepEqual(fields(f.send({kind: 'cancel'})), {});
  assert.equal(f.send({kind: 'cancel'}, {revision: 1n}).error, 'stale_revision');
  assert.equal(f.send({kind: 'cancel'}).error, 'ended');
  assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(1)}, {revision: 1n}).error, 'stale_revision');
  assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(1)}).error, 'ended');
  assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(255)}).error, 'invalid_input');
  assert.deepEqual(f.calls, ['initial', 'state 0', 'decode 01', 'decode ff']);
});

test('accepted/ignored/rejected turns retain complete checks and exact outputs without callback replay', () => {
  const f = fixture({readExactValues: true});
  for (const [input, kind] of [[1, 'accepted'], [0, 'ignored'], [2, 'rejected']]) {
    assert.deepEqual(fields(f.send({kind: 'step', bytes: Uint8Array.of(input)})), {
      delivered: 'bool:true', sequence: `u64:${f.session.sequence}`,
    });
    const handle = BigInt(fields(f.send({kind: 'snapshot'})).handle.slice(4));
    const before = [...f.calls];
    assert.deepEqual(fields(f.send({kind: 'checks', handle}, {revision: MAX, configuration: MAX})), {
      'checks.complete': 'bool:true', 'checks.total': 'u64:2', 'checks.first_failure': 'none',
      'checks.returned': 'u64:2', 'checks.truncated': 'bool:false',
      'checks.0.id': 'string:bounded', 'checks.0.status': 'enum:passed',
      'checks.1.id': 'string:transition', 'checks.1.status': 'enum:passed',
    });
    assert.deepEqual(fields(f.send({kind: 'outputs', handle}, {revision: MAX})), {
      'outputs.total': input === 1 ? 'u64:1' : 'u64:0', disposition: `enum:${kind}`,
      'outputs.provenance': 'enum:simulated', 'outputs.payloads': 'enum:exact_sensitive',
      ...(input === 1 ? {'outputs.0.encoded': 'bytes:0100000000000000'} : {}), 'outputs.truncated': 'bool:false',
    });
    assert.deepEqual(f.calls, before);
  }
  assert.deepEqual(f.calls, ['initial', 'state 0',
    'decode 01', 'step 0 1', 'state 1', 'transition 0 1 1',
    'decode 00', 'step 1 0', 'state 1', 'transition 1 0 1',
    'decode 02', 'step 1 2', 'state 1', 'transition 1 2 1']);
});

test('failed checks commit and expose stable failure identity while reducer errors do not commit', () => {
  const f = fixture();
  for (let i = 0; i < 3; i++) assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(1)}).error, null);
  const handle = BigInt(fields(f.send({kind: 'snapshot'})).handle.slice(4));
  const checks = fields(f.send({kind: 'checks', handle}));
  assert.equal(checks['checks.total'], 'u64:2');
  assert.equal(checks['checks.first_failure.id'], 'string:bounded');
  assert.equal(checks['checks.first_failure.index'], 'u64:0');
  assert.equal(checks['checks.first_failure.phase'], 'enum:post_state');
  assert.equal(checks['checks.first_failure.sequence'], 'u64:3');
  assert.equal(fields(f.send({kind: 'status'})).stop, 'enum:property_failure');
  assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(1)}).error, 'ended');
  assert.deepEqual(f.calls.slice(-5), ['decode 01', 'step 2 1', 'state 3', 'transition 2 1 3', 'decode 01']);
  const reducer = fixture();
  assert.deepEqual(fields(reducer.send({kind: 'step', bytes: Uint8Array.of(3)})), {delivered: 'bool:false', sequence: 'u64:0'});
  assert.equal(fields(reducer.send({kind: 'status'})).stop, 'enum:model_error');
  assert.equal(reducer.send({kind: 'step', bytes: Uint8Array.of(255)}).error, 'invalid_input');
  assert.deepEqual(reducer.calls, ['initial', 'state 0', 'decode 03', 'step 0 3', 'decode ff']);
});

test('wire parser rejects unknown/version/shape failures with ID zero before request retirement', () => {
  const f = fixture();
  const original = encodeRequest(f.request({kind: 'status'}, {requestId: MAX}));
  const raw = new TextDecoder().decode(original);
  const cases = [
    [raw.replace(/status$/, 'eval'), 'unsupported'],
    [raw.replace('u32:1', 'u32:2'), 'unsupported_version'],
    [raw + '\textra', 'malformed'],
    [raw.replace(/status$/, 'step\tbytes:0'), 'malformed'],
    [raw.replace(/status$/, 'step\tbytes:gg'), 'malformed'],
  ];
  for (const [text, error] of cases) {
    const response = decodeResponse(f.endpoint.handlePayload(new TextEncoder().encode(text)));
    assert.equal(response.requestId, 0n);
    assert.equal(response.error, error);
  }
  assert.equal(f.send({kind: 'step', bytes: Uint8Array.of(1)}, {requestId: MAX}).error, null);
  assert.equal(f.session.revision, 1n);
});

test('strict UTF-8 decoding preserves a BOM rather than admitting a changed wire value', () => {
  assert.equal(escapeField('\uFEFFvalue'), '%EF%BB%BFvalue');
  const f = fixture(), encoded = encodeRequest(f.request({kind: 'status'}));
  const withBom = new Uint8Array(encoded.length + 3);
  withBom.set([239, 187, 191]); withBom.set(encoded, 3);
  assert.throws(() => decodeRequest(withBom), /malformed/);
  const raw = new TextDecoder().decode(encoded);
  assert.throws(() => decodeRequest(new TextEncoder().encode(raw.replace('status', '%EF%BB%BFstatus'))), /unsupported/);
  for (const text of ['%FF', '%C0%AF', '%ED%A0%80', '%F4%90%80%80', '%GG', '%0']) {
    assert.throws(() => decodeRequest(new TextEncoder().encode(raw.replace('status', text))), /malformed/);
  }
});

test('path grammar, numeric identities and mixed malformed/count failures match Rust decoding', () => {
  const f = fixture();
  const prefix = new TextDecoder().decode(encodeRequest(f.request({kind: 'host', name: 'inspect',
    arguments: ['u64:0', 'u64:1', 'u64:0', 'u64:1']})));
  for (const suffix of ['keyXu8:1', 'keyXstring:value', 'key.u8:-0', 'index:-0']) {
    assert.throws(() => decodeRequest(new TextEncoder().encode(prefix + '\t' + suffix)), /malformed/);
  }
  assert.throws(() => decodeRequest(new TextEncoder().encode(prefix + '\t' + Array(33).fill('field:x').join('\t'))), /limit/);
  const path = ['index:+01', 'key.usize:+1', 'key.isize:-01', 'key.u128:+340282366920938463463374607431768211455'];
  const decoded = decodeRequest(new TextEncoder().encode(prefix + '\t' + path.map(escapeField).join('\t')));
  assert.deepEqual(decoded.command.arguments.slice(4), ['index:1', 'key.usize:1', 'key.isize:-1', 'key.u128:340282366920938463463374607431768211455']);
  assert.throws(() => decodeRequest(new TextEncoder().encode(['%GG', ...Array(64).fill('x')].join('\t'))), /malformed/);
  assert.throws(() => decodeRequest(new TextEncoder().encode(Array(65).fill('x').join('\t'))), /limit/);
  const raw = new TextDecoder().decode(encodeRequest(f.request({kind: 'host', name: 'watch_current', arguments: ['u64:1']})));
  const first = f.endpoint.handlePayload(new TextEncoder().encode(raw.replace(/u64:1$/, 'u64:0001')));
  assert.deepEqual(f.endpoint.handlePayload(new TextEncoder().encode(raw)), first);
});

test('watch_current and unwatch ignore envelope revision; inspection checks its handle first', () => {
  const f = fixture();
  const p = new ProtocolSession(f.session, bytes => bytes[0], {configureWatches: true}, {}, {
    watch_current: () => ({fields: [['watch.current', 'none']]}),
    unwatch: () => ({fields: []}),
  });
  const send = (name, args) => p.handle(f.request({kind: 'host', name, arguments: args}, {epoch: p.epoch, revision: MAX}));
  assert.equal(send('watch_current', ['u64:1']).error, null);
  assert.equal(send('unwatch', ['u64:1']).error, null);
  assert.equal(send('inspect', ['u64:0', 'u64:1', 'u64:0', 'u64:1']).error, 'stale_handle');
  assert.equal(send('watch', ['u64:1', 'u64:0']).error, 'stale_configuration');
  assert.equal(send('metric_catalog', [`u64:${MAX}`, 'u64:1']).error, 'limit');
  assert.equal(send('metric_snapshot', ['none', 'u64:1', 'u64:1']).error, 'invalid_input');
});

test('initial checker errors report incomplete checking; native event loss is observable in status', () => {
  const failed = fixture({}, {}, {checkState() { throw new ModelError('private checker detail'); }});
  const handle = BigInt(fields(failed.send({kind: 'snapshot'})).handle.slice(4));
  assert.equal(fields(failed.send({kind: 'checks', handle}))['checks.complete'], 'bool:false');
  const f = fixture({}, {maxEventEntries: 1});
  f.send({kind: 'step', bytes: Uint8Array.of(1)});
  f.send({kind: 'step', bytes: Uint8Array.of(0)});
  assert.equal(fields(f.send({kind: 'status'}))['events.dropped'], 'u64:1');
  const event = fields(f.send({kind: 'host', name: 'events', arguments: ['u64:100']}));
  assert.equal(event['events.returned'], 'u64:1');
  assert.equal(event['events.0.kind'], 'enum:transition');
  assert.equal(event['events.0.revision'], 'u64:1');
  assert.equal(fields(f.send({kind: 'host', name: 'events', arguments: ['u64:100']}))['events.returned'], 'u64:0');
});

test('serve preserves a committed response before a partial EOF and disconnects on every exit', async () => {
  for (const suffix of [new Uint8Array(), Uint8Array.of(0), Uint8Array.of(0, 0, 0, 5, 1), Uint8Array.of(0, 0, 0, 0)]) {
    const f = fixture(), frame = writeFrame(encodeRequest(f.request({kind: 'step', bytes: Uint8Array.of(1)})));
    const written = [];
    async function* source() { for (const byte of frame) yield Uint8Array.of(byte); if (suffix.length) yield suffix; }
    const pending = serve(f.endpoint, source(), bytes => { written.push(bytes); });
    if (suffix.length) await assert.rejects(pending); else await pending;
    const parser = new FrameReader(), responses = written.flatMap(chunk => [...parser.feed(chunk)]).map(bytes => decodeResponse(bytes));
    parser.finish();
    assert.equal(responses.length, 1);
    assert.deepEqual(fields(responses[0]), {delivered: 'bool:true', sequence: 'u64:1'});
    assert.equal(f.endpoint.handle(f.request({kind: 'status'})).error, 'disconnected');
    assert.deepEqual(f.calls, ['initial', 'state 0', 'decode 01', 'step 0 1', 'state 1', 'transition 0 1 1']);
  }
});
