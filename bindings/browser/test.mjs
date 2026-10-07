import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createModel } from "./stateless.mjs";

const wasm = await readFile(new URL("../../target/wasm32-unknown-unknown/release/stateless.wasm", import.meta.url));
const definition = overrides => ({
  metadata: { name: "adapter-test", build: "test-v1", modelVersion: 1, propertiesVersion: 1, codecVersion: 1 },
  initial: () => new Uint8Array([0]),
  step: (state, input) => ({ state: new Uint8Array([state[0] + input[0]]), outputs: [input] }),
  checkState: state => [{ id: "bound", status: state[0] <= 2 ? "passed" : "failed" }],
  checkTransition(before, input, transition) {
    assert.equal(before.length, 1);
    assert.deepEqual(transition.outputs, [input]);
    assert.equal(transition.disposition, "accepted");
    return [{ id: "effect", status: "passed" }];
  },
  inputs: () => [new Uint8Array([1])],
  ...overrides,
});
const inputs = Array.from({ length: 3 }, () => new Uint8Array([1]));
test("large valid input batches do not hit the JavaScript function-argument limit", async () => {
  const model = await createModel(wasm, definition());
  const result = model.record(Array.from({ length: 150_000 }, () => new Uint8Array([1])), 0);
  assert.equal(result.status, 0);
  assert.equal(model.replay(result.artifact).status, 0);
});
test("aggregate input packet limits are checked before copying repeated payloads", async () => {
  const model = await createModel(wasm, definition());
  const block = new Uint8Array(1024 * 1024);
  assert.throws(() => model.record(Array(64).fill(block), 0), /packet exceeds 64 MiB/);
  assert.equal(model.record(inputs).status, 1);
});
test("non-string model and property identities are rejected instead of coerced", async () => {
  const model = definition(); model.metadata.name = 42;
  await assert.rejects(createModel(wasm, model), /nonempty strings/);
  const invalid = await createModel(wasm, definition({ checkState: () => [{ id: 42, status: "passed" }] }));
  assert.throws(() => invalid.record(inputs), /expected string/);
});
test("fresh models reproduce traces, changed behavior diverges, identity remains strict", async () => {
  const original = await createModel(wasm, definition());
  const { status, artifact } = original.record(inputs);
  assert.equal(status, 1);
  assert.equal((await createModel(wasm, definition())).replay(artifact).status, 1);
  const changed = await createModel(wasm, definition({ step: (s, i) => ({ state: new Uint8Array([s[0] + 2]), outputs: [i] }) }));
  assert.equal(changed.replay(artifact).status, 2);
  const renamed = definition(); renamed.metadata.build = "another-build";
  assert.equal((await createModel(wasm, renamed)).replay(artifact).status, 3);
  assert.equal(original.replay(artifact.slice(0, -1)).status, 12);
});
test("bounded enumeration and recording expose their limits", async () => {
  const model = await createModel(wasm, definition());
  const search = model.enumerate();
  assert.equal(search.status, 1);
  assert.equal(model.replay(search.artifact).status, 1);
  const bounded = model.enumerate({ maxDepth: 1 });
  assert.equal(bounded.status, 0);
  assert.match(bounded.report, /DepthBound/);
  assert.equal(bounded.artifact.length, 0);
  const prefix = model.record(inputs, 1);
  assert.equal(prefix.status, 0);
  assert.equal(model.replay(prefix.artifact).status, 0);
  assert.throws(() => model.record(inputs, -1), /u32/);
  assert.throws(() => model.enumerate({ maxTransitions: 1 }), /u64/);
});
test("callback exceptions and reentry cannot escape Wasm or poison the next call", async () => {
  let bad = true, model;
  model = await createModel(wasm, definition({ step(state, input) {
    if (bad) model.record(inputs);
    return { state: new Uint8Array([state[0] + input[0]]), outputs: [input] };
  } }));
  assert.throws(() => model.record(inputs), /reenter/);
  bad = false;
  assert.equal(model.record(inputs).status, 1);
});
test("invalid callback tags and async callbacks fail instead of becoming passing checks", async () => {
  const invalid = await createModel(wasm, definition({ checkState: () => [{ id: "bad", status: "__proto__" }] }));
  assert.throws(() => invalid.record(inputs));
  const asyncModel = await createModel(wasm, definition({ initial: async () => new Uint8Array([0]) }));
  assert.throws(() => asyncModel.record(inputs), /Uint8Array/);
});
test("owned artifacts survive subsequent memory growth", async () => {
  const model = await createModel(wasm, definition());
  const artifact = model.record(inputs).artifact;
  const copy = artifact.slice();
  model.replay(new Uint8Array(2 * 1024 * 1024));
  assert.deepEqual(artifact, copy);
  assert.equal(model.replay(artifact).status, 1);
});
