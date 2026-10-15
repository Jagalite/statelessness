// Qualification sources only. Runtime requires STATELESS_ABI2_WASM pointing to
// the explicit abi-v2-wasm artifact; this suite never builds or falls back.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createEngine, NativeError, check, disposition, DEFAULT_TRACE_LIMITS} from './v2.mjs';
import {toPacket, fromPacket} from './v2-packet.mjs';

const path = process.env.STATELESS_ABI2_WASM;
const engine = path ? await createEngine(new Uint8Array(await readFile(path))) : null;
const native = (name, work) => test(name, {skip: !path && 'set STATELESS_ABI2_WASM to opt into Wasm execution'}, work);

class Cursor {
  constructor(values, failAfter = Infinity) {
    this.values = values[Symbol.iterator]();
    this.failAfter = failAfter; this.pulls = 0; this.closed = 0;
  }
  [Symbol.iterator]() { return this; }
  next() {
    if (++this.pulls > this.failAfter) throw new Error('cursor failed after prefix');
    return this.values.next();
  }
  return() { this.closed++; return {done: true}; }
}

function counter(overrides = {}) {
  return {
    steps: 0, cursors: [],
    metadata: () => ({name: 'abi2-counter', build: 'qualification-source', model_version: 1, properties_version: 1, codec_version: 1}),
    initialState: () => ({n: 0}),
    cloneState: state => ({...state}),
    cloneInput: input => ({...input}),
    cloneOutput: output => ({...output}),
    equalStates: (left, right) => left.n === right.n,
    equalInputs: (left, right) => left.delta === right.delta,
    inputs() { const cursor = new Cursor([{delta: 1}]); this.cursors.push(cursor); return cursor; },
    step(state, input) { this.steps++; state.n += input.delta; return {state, outputs: [{n: state.n}], disposition: disposition()}; },
    checkState: state => [state.n >= 3 ? check('bound', 'failed', 'three') : check('bound')],
    generate(_state, rng) { rng.nextU64(); return {delta: 1}; },
    isEnabled: (_state, input) => input.delta === 1,
    simplerInputs: () => [],
    encodeState: state => Uint8Array.of(state.n),
    decodeState: bytes => ({n: bytes[0]}),
    encodeInput: input => Uint8Array.of(input.delta),
    decodeInput: bytes => ({delta: bytes[0]}),
    encodeOutput: output => Uint8Array.of(output.n),
    ...overrides,
  };
}

const trace = () => ({
  metadata: {name: '', build: '', model_version: 1, properties_version: 1, codec_version: 1},
  initialState: Uint8Array.of(1), initialChecks: [check('')],
  steps: [{input: Uint8Array.of(2), disposition: disposition(), outputs: [Uint8Array.of(3)],
    postState: Uint8Array.of(4), checks: [check('x')]}], termination: 'completed', error: '',
});

// Keep corpus number spellings intact: parsing and stringifying would turn the
// deliberately invalid 1.0 request into a valid integer before the runner sees it.
function rawRequests(source) {
  const requests = [], marker = /"request"\s*:/g;
  for (let found; (found = marker.exec(source));) {
    let start = marker.lastIndex;
    while (/\s/.test(source[start])) start++;
    assert.equal(source[start], '{');
    let depth = 0, quoted = false, escaped = false, end = start;
    for (; end < source.length; end++) {
      const c = source[end];
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === '\\') escaped = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === '{' || c === '[') depth++;
      else if ((c === '}' || c === ']') && --depth === 0) break;
    }
    assert.ok(end < source.length, 'unterminated corpus request');
    requests.push(source.slice(start, end + 1));
    marker.lastIndex = end + 1;
  }
  return requests;
}

test('packet reader accepts a nonzero-offset Buffer and owns its decoded blobs', () => {
  const packet = toPacket(trace()), backing = Buffer.alloc(packet.length + 13, 0xee);
  backing.set(packet, 7);
  const decoded = fromPacket(backing.subarray(7, 7 + packet.length));
  backing.fill(0);
  assert.deepEqual(decoded, trace());
});

test('every truncated packet, trailing data and aggregate bounds are rejected', () => {
  const packet = toPacket(trace());
  for (let length = 0; length < packet.length; length++) assert.throws(() => fromPacket(packet.subarray(0, length)));
  assert.throws(() => fromPacket(Uint8Array.from([...packet, 0])), /trailing/);
  for (const limit of [{maxBlobBytes: 0}, {maxPayloadBytes: 3}, {maxItems: 3}, {maxSteps: 0}])
    assert.throws(() => fromPacket(packet, {...DEFAULT_TRACE_LIMITS, ...limit}));
  assert.deepEqual(fromPacket(packet, {...DEFAULT_TRACE_LIMITS, maxItems: 4}), trace());
  assert.throws(() => fromPacket(packet, {...DEFAULT_TRACE_LIMITS, maxItems: NaN}), /maxItems/);
});

test('discarded binary parameters do not consume the portable item budget', () => {
  const original = {...trace(), steps: []}, packet = toPacket(original);
  const offset = Buffer.from(packet).indexOf('manual') + 'manual'.length + 1;
  const parameter = Uint8Array.of(1, 0, 0, 0, 1, 0, 0, 0, 107, 1, 0, 0, 0, 118);
  const withParameter = Uint8Array.from([...packet.subarray(0, offset), ...parameter, ...packet.subarray(offset + 4)]);
  assert.deepEqual(fromPacket(withParameter, {...DEFAULT_TRACE_LIMITS, maxItems: 1}), original);
});

native('owned witness copies outlive results and sessions without retained roots', () => {
  const model = counter(), session = engine.createSession(model);
  let owned;
  try {
    const result = session.search({maxStates: 20, maxTransitions: 20, maxDepth: 10});
    try {
      assert.equal(result.status, 1);
      owned = result.inputs();
      assert.deepEqual(owned, [{delta: 1}, {delta: 1}, {delta: 1}]);
      owned[0].delta = 99;
      assert.deepEqual(result.inputs()[0], {delta: 1});
      assert.ok(session.retainedRoots > 0);
    } finally { result.close(); }
    assert.equal(session.retainedRoots, 0);
  } finally { session.close(); }
  assert.deepEqual(owned[0], {delta: 99});
  assert.ok(model.cursors.length);
  assert.ok(model.cursors.every(cursor => cursor.closed === 1));
});

native('lazy callback failure preserves a replayable prefix and closes its cursor', () => {
  const model = counter(), cursor = new Cursor([{delta: 1}], 1), session = engine.createSession(model);
  try {
    const recorded = session.record(cursor);
    assert.equal(recorded.termination, 'model_error');
    assert.equal(recorded.steps.length, 1);
    assert.match(recorded.error, /cursor failed after prefix/);
    assert.equal(model.steps, 1);
    assert.equal(cursor.closed, 1);
    assert.equal(session.retainedRoots, 0);
    assert.equal(session.replay(recorded).outcome, 'exact');
  } finally { session.close(); }
});

native('partial callback check batches fail without publishing passing results', () => {
  const session = engine.createSession(counter({*checkState() { yield check('partial'); throw new Error('checker failed'); }}));
  try {
    assert.throws(() => session.checkInitial({n: 0}), /checker failed/);
    assert.equal(session.retainedRoots, 0);
  } finally { session.close(); }
});

native('observation uses supplied values and never calls initialState or step', () => {
  const session = engine.createSession(counter({
    initialState() { throw new Error('initial must not run'); },
    step() { throw new Error('step must not run'); },
    checkTransition(before, _input, transition) { before.n = 90; transition.state.n = 91; return [check('effect')]; },
  }));
  const before = {n: 0}, after = {n: 1};
  try {
    assert.deepEqual(session.checkObserved(before, {delta: 1}, {state: after, outputs: [], disposition: disposition()}, 1), [check('bound'), check('effect')]);
    assert.deepEqual([before, after], [{n: 0}, {n: 1}]);
    assert.equal(session.retainedRoots, 0);
  } finally { session.close(); }
});

native('unpublished cursors close exactly once when host roots reach quota', () => {
  const model = counter(), session = engine.createSession(model, {maxValues: 1});
  try {
    assert.throws(() => session.search(), NativeError);
    assert.equal(model.cursors.length, 1);
    assert.equal(model.cursors[0].closed, 1);
    assert.equal(session.retainedRoots, 0);
  } finally { session.close(); }
});

native('portable payload limits stop before candidate checkpoint commit', () => {
  const model = counter(), cursor = new Cursor(Array.from({length: 10}, () => ({delta: 1}))), session = engine.createSession(model);
  try {
    const recorded = session.record(cursor, 100, {...DEFAULT_TRACE_LIMITS, maxPayloadBytes: 1});
    assert.equal(recorded.termination, 'model_error');
    assert.equal(recorded.steps.length, 0);
    assert.deepEqual([model.steps, cursor.pulls, cursor.closed], [1, 1, 1]);
    assert.equal(session.retainedRoots, 0);
    const unpulled = new Cursor([{delta: 1}]);
    assert.throws(() => session.record(unpulled, 100, {...DEFAULT_TRACE_LIMITS, maxPayloadBytes: 0}), NativeError);
    assert.deepEqual([unpulled.pulls, unpulled.closed], [0, 1]);
    const empty = session.record([], 100, {...DEFAULT_TRACE_LIMITS, maxItems: 1});
    assert.equal(empty.termination, 'completed');
    assert.equal(empty.initialChecks.length, 1);
  } finally { session.close(); }
});

native('cross-session shrink rejects a foreign result without consuming it', () => {
  const first = engine.createSession(counter()), second = engine.createSession(counter());
  try {
    const failure = first.fuzz({cases: 1, maxSteps: 4});
    try {
      assert.throws(() => second.shrink(failure), error => error instanceof NativeError && error.status === 14);
      assert.deepEqual(failure.inputs(), [{delta: 1}, {delta: 1}, {delta: 1}]);
      const shrunk = first.shrink(failure);
      try { assert.equal(shrunk.status, 1); } finally { shrunk.close(); }
    } finally { failure.close(); }
    assert.equal(first.retainedRoots, 0);
    assert.equal(second.retainedRoots, 0);
  } finally { first.close(); second.close(); }
});

native('cursor cleanup rejects reentry and leaves the outer session usable', () => {
  const session = engine.createSession(counter()), attempts = [];
  const cursor = new Cursor([]);
  cursor.return = () => {
    cursor.closed++;
    try { session.search(); } catch (error) { attempts.push(error.status); }
    return {done: true};
  };
  try {
    session.record(cursor, 0);
    assert.equal(cursor.closed, 1);
    assert.deepEqual(attempts, [16]);
    assert.equal(session.retainedRoots, 0);
    assert.deepEqual(session.checkInitial({n: 0}), [check('bound')]);
  } finally { session.close(); }
});

native('unsupported persistence rejects before cloning or opening an iterator', () => {
  const model = counter({metadata: undefined, cloneState() { throw new Error('unexpected clone'); }});
  const session = engine.createSession(model);
  const untouched = {[Symbol.iterator]() { throw new Error('unexpected iterator'); }};
  try {
    for (const run of [() => session.record(untouched), () => session.recordBinary(untouched), () => session.ring({n: 0})])
      assert.throws(run, error => error instanceof NativeError && error.status === 17);
    assert.equal(session.retainedRoots, 0);
  } finally { session.close(); }
});

native('fixture corpus explicitly dispatches to Rust and recovers after invalid UTF-8', async () => {
  const {conformanceBackend} = await import('./v2-conformance.mjs');
  const {rawResponse} = await import('../../typescript/dist/conformance.js');
  const backend = conformanceBackend(engine), encoder = new TextEncoder();
  const hello = rawResponse(encoder.encode('{"version":1,"operation":"hello"}'), backend);
  assert.equal(hello.engine_family, 'rust-shared');
  assert.equal(hello.artifact_sha256, engine.sha256);
  assert.deepEqual(rawResponse(Uint8Array.of(0xff), backend), {error: 'invalid_request'});
  const source = await readFile(new URL('../../conformance/corpus.json', import.meta.url), 'utf8');
  const corpus = JSON.parse(source), requests = rawRequests(source);
  assert.equal(requests.length, corpus.cases.length);
  for (const [index, item] of corpus.cases.entries()) {
    if (item.request.operation === 'hello') continue;
    assert.deepEqual(rawResponse(encoder.encode(requests[index]), backend), item.expected, item.id);
  }
  assert.equal(engine.sessions.size, 0);
});

native('Wasm memory growth during callbacks preserves copied buffers and result reads', () => {
  let grew=false;
  const model=counter({checkState(state){if(!grew){engine.api.memory.grow(1);grew=true;}return [check('bound',state.n>=3?'failed':'passed',state.n>=3?'three':'')];}});
  const session=engine.createSession(model);
  try {
    const trace=session.record([{delta:1}]);
    assert.equal(trace.steps.length,1);
    assert.equal(session.replay(trace).outcome,'exact');
    assert.equal(session.retainedRoots,0);
  } finally {session.close();}
});

native('cooperative callback cancellation is resettable and clock limits fail explicitly', () => {
  let session, cancel=true;
  const model=counter({hashState(){if(cancel)session.cancel();return 0;}});
  session=engine.createSession(model);
  try {
    assert.equal(session.enumerateStates().termination,'cancelled');
    assert.equal(session.retainedRoots,0);
    cancel=false;session.resetCancellation();
    assert.equal(session.enumerateStates().termination,'failure_found');
    assert.throws(()=>session.search({maxDurationMillis:1}),NativeError);
    assert.equal(session.retainedRoots,0);
  } finally {session.close();}
});

// A BOM inside a semantic string is data, unlike a transport-level JSON BOM.
test('packet strings preserve a leading Unicode byte-order mark', () => {
  const value=trace();value.metadata.name='\uFEFFname';value.metadata.build='\uFEFFbuild';
  value.initialChecks=[check('\uFEFFid','skipped','\uFEFFdetails')];
  assert.deepEqual(fromPacket(toPacket(value)),value);
});
native('native result text preserves leading BOM code points', () => {
  const model=counter({checkState:()=>[check('\uFEFFid','skipped','\uFEFFdetails')]});
  const session=engine.createSession(model);
  try{assert.deepEqual(session.checkInitial({n:0}),[check('\uFEFFid','skipped','\uFEFFdetails')]);}
  finally{session.close();}
});
