# Stateless JavaScript byte-model adapter

Experimental ABI 1 source package, built with `python3 scripts/build-bindings.py`.
The generated `target/bindings/browser/` directory includes the engine Wasm,
adapter, package manifest and license. Install that directory as a local package,
or use `npm pack` there to produce a distributable tarball. No registry publication
is implied. No runtime npm dependencies are required.

```js
import { createModel, u32 } from "@jagalite/stateless";
// Browser: fetch the packaged stateless.wasm from your own asset URL.
// Node: readFile(new URL(import.meta.resolve("@jagalite/stateless/stateless.wasm"))).
const wasm = await (await fetch("/assets/stateless.wasm")).arrayBuffer();
const number = bytes => {
  if (bytes.length !== 4) throw new Error("invalid counter bytes");
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true);
};
const engine = await createModel(wasm, {
  metadata: { name: "my-counter", build: "replace-with-your-build-fingerprint",
    modelVersion: 1, propertiesVersion: 1, codecVersion: 1 },
  initial: () => u32(0),
  step: (state, input) => ({ state: u32(number(state) + number(input)), outputs: [] }),
  checkState: state => [{ id: "bound", status: number(state) <= 2 ? "passed" : "failed" }],
  inputs: () => [u32(1)],
});
const { status, artifact } = engine.record([u32(1), u32(1), u32(1)]);
console.assert(status === 1);
console.assert(engine.replay(artifact).status === 1);
```

`initial`, `step`, `checkState`, optional `checkTransition(before, input,
transition)`, and `inputs` are synchronous callbacks. State/input/output payloads
are canonical `Uint8Array` values. State validation belongs in `checkState`;
input decoding belongs in `step`. A transition contains `state`, `outputs`,
optional `disposition` (`accepted`, `rejected`, `ignored`), and `reason`.
Checks contain `id`, `status` (`passed`, `failed`, `skipped`), and `details`.
No asynchronous callbacks, real side effects, hidden logical state or reentry.

`record(inputs, maxSteps)` returns `{status, artifact}`. `enumerate({maxStates,
maxTransitions, maxDepth})` returns `{status, artifact, report}`; transition limits
are BigInt. Inspect report termination: status 0 can mean a bounded search.
`replay(artifact)` returns `{status, detail}`. ABI statuses: 0 exact/success,
1 property failure/reproduced failure, 2 divergence, 3 incompatible identity,
10 invalid argument, 11 model error, 12 trace error, 13 caught Rust panic.
Record/enumeration throw for engine errors; replay returns its status for inspection.

Each operation frees its native model and buffers; returned arrays are owned
copies. Exceptions in callbacks are contained and reported as model errors.
The engine cannot preempt callbacks or bound allocations made by application code.
Recording and enumeration run synchronously; use your own Worker to avoid
blocking a browser UI during long searches. Foreign fuzz/shrink and live recorder
APIs are not exposed by ABI 1.
