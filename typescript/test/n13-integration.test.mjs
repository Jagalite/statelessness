import {test} from 'node:test';
import assert from 'node:assert/strict';
import {bindModelCodec, valueCodecs, customValueSchema, EncodeStorage,
  withEncodeBuffer, metadata, record, Recorder, EvidenceRecorder, checkInitial} from '../dist/index.js';

const hooks = ['encodeState', 'encodeInput', 'encodeOutput', 'encodeStateInto',
  'encodeInputInto', 'encodeOutputInto', 'decodeState', 'decodeInput'];

test('every binder hook resolves current metadata before values, sinks and limits', () => {
  class Model {
    #version = 2;
    metadata() { return {codec_version: 1}; }
    replacement() { return {codec_version: this.#version}; }
  }
  const model = new Model();
  let limits = 0, encoded = 0, metadataReads = 0, metadataCalls = 0;
  const output = customValueSchema({name: 'OnlyOutput', encodeInto() { encoded++; }});
  const bound = bindModelCodec(model, {state: valueCodecs.u8, input: valueCodecs.u8,
    output, decodeLimits() { limits++; return {}; }});
  Object.defineProperty(model, 'metadata', {configurable: true, get() {
    metadataReads++;
    return function () { metadataCalls++; return this.replacement(); };
  }});
  const hostile = new Proxy({}, {get() { throw Error('hostile value inspected'); }});
  const storage = new EncodeStorage();
  withEncodeBuffer(storage, 4, sink => {
    sink.extend(Uint8Array.of(99));
    for (const hook of hooks)
      assert.throws(() => bound[hook](hostile, sink), /ModelCodec derive version/);
  });
  assert.deepEqual([...storage.snapshot()], [99]);
  assert.equal(metadataReads, 8);
  assert.equal(metadataCalls, 8);
  assert.equal(limits, 0);
  assert.equal(encoded, 0);
  Object.defineProperty(model, 'metadata', {configurable: true, value: null});
  for (const hook of hooks)
    assert.throws(() => bound[hook](hostile, null), /model metadata required/);
});

test('capture rejects a replaced metadata method instead of stamping an unchecked version', () => {
  let encoded = 0;
  const state = customValueSchema({name: 'State', encodeInto(v, out) {
    encoded++; out.extend(Uint8Array.of(v));
  }, decodeFrom: reader => reader.take(1)[0]});
  const model = {metadata: () => metadata('integration', 'test'), initialState: () => 0,
    cloneState: s => s, cloneInput: i => i, cloneOutput: o => o, checkState: () => []};
  const bound = bindModelCodec(model, {state, input: valueCodecs.u8, output: valueCodecs.u8});
  const token = checkInitial(bound, 0);
  model.metadata = () => ({...metadata('integration', 'changed'), codec_version: 2});
  const config = {strategy: 'manual', seed: null, parameters: []};
  const mismatch = /ModelCodec derive version/;
  assert.throws(() => record(bound, []), mismatch);
  assert.throws(() => new Recorder(bound, 0, config), mismatch);
  assert.throws(() => Recorder.withCheckedInitial(token, config), mismatch);
  assert.throws(() => new EvidenceRecorder(token, 0n, config,
    {maxSteps: 3, maxRetainedBytes: 100000n},
    {run: 'integration', epoch: 0n, manifest: null, source: null, environment: []},
    {maxMetadataBytes: 1000}), mismatch);
  assert.equal(encoded, 0);
  model.metadata = () => metadata('integration', 'replacement-valid');
  assert.equal(record(bound, []).metadata.build, 'replacement-valid');
  assert.equal(encoded, 1);
});
