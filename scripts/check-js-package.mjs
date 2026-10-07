// Pack and install the generated distribution into a separate, offline consumer.
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

const packageDirectory = resolve(process.argv[2] ?? "target/bindings/browser");
const temporary = mkdtempSync(join(tmpdir(), "stateless-npm-"));
try {
  const packed = JSON.parse(execFileSync("npm", ["pack", "--offline", "--json", "--pack-destination", temporary],
    { cwd: packageDirectory, encoding: "utf8" }));
  const consumer = join(temporary, "consumer");
  mkdirSync(consumer);
  writeFileSync(join(consumer, "package.json"), JSON.stringify({ name: "stateless-consumer", version: "0.0.0", private: true, type: "module" }));
  execFileSync("npm", ["install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund", join(temporary, packed[0].filename)],
    { cwd: consumer, stdio: "inherit" });
  writeFileSync(join(consumer, "check.mjs"), `
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createModel, u32 } from "@jagalite/stateless";
const wasm = await readFile(new URL(import.meta.resolve("@jagalite/stateless/stateless.wasm")));
const number = bytes => { assert.equal(bytes.length, 4); return new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0, true); };
const model = {
  metadata: { name: "installed-counter", build: "v1", modelVersion: 1, propertiesVersion: 1, codecVersion: 1 },
  initial: () => u32(0),
  step: (state, input) => ({ state: u32(number(state) + number(input)), outputs: [] }),
  checkState: state => [{ id: "bound", status: number(state) <= 2 ? "passed" : "failed" }],
  inputs: () => [u32(1)],
};
const engine = await createModel(wasm, model);
const recorded = engine.record([u32(1), u32(1), u32(1)]);
assert.equal(recorded.status, 1);
assert.equal((await createModel(wasm, model)).replay(recorded.artifact).status, 1);
assert.equal(engine.enumerate().status, 1);
console.log("Installed npm tarball: custom model, recording, fresh-model replay and enumeration passed");
`);
  execFileSync(process.execPath, ["check.mjs"], { cwd: consumer, stdio: "inherit" });
} finally { rmSync(temporary, { recursive: true, force: true }); }
