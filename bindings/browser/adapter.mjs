// Counter fixture built on the reusable byte-model adapter.
import { createModel, u32 } from "./stateless.mjs";
const integer = bytes => {
  if (bytes.length !== 4) throw new Error("invalid counter value");
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true);
};
export async function createCounter(wasmBytes) {
  const definition = changed => ({
    // The deliberate changed-reducer control retains identity to test divergence.
    metadata: { name: "javascript-counter", build: "javascript-counter-fixture-v1",
      modelVersion: 1, propertiesVersion: 1, codecVersion: 1 },
    initial: () => u32(0),
    step(state, input) {
      const next = u32(integer(state) + integer(input) + Number(changed));
      return { state: next, outputs: [next] };
    },
    checkState(state) {
      const failed = integer(state) > 2;
      return [{ id: "counter_bound", status: failed ? "failed" : "passed",
        details: failed ? "counter exceeded 2" : "" }];
    },
    inputs(state) { integer(state); return [u32(1)]; },
  });
  const original = await createModel(wasmBytes, definition(false));
  const changed = await createModel(wasmBytes, definition(true));
  return {
    record: () => original.record([u32(1), u32(1), u32(1)]).artifact,
    replay: (artifact, useChanged = false) => (useChanged ? changed : original).replay(artifact),
    enumerate: (maxDepth = 10) => original.enumerate({ maxStates: 20, maxTransitions: 100n, maxDepth }),
  };
}
