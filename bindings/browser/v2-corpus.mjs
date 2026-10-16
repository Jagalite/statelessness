#!/usr/bin/env node
// Byte-bounded JSONL framing, including malformed UTF-8 and post-error recovery.
import { once } from 'node:events';
import { rawResponse, MAX_LINE } from '../../typescript/dist/conformance.js';
import {readFile} from 'node:fs/promises';
import {createEngine} from './v2.mjs';
import {conformanceBackend} from './v2-conformance.mjs';
const file=process.argv[2];
if(!file)throw new Error('usage: node bindings/browser/v2-corpus.mjs /absolute/stateless_v2.wasm');
const engine=await createEngine(new Uint8Array(await readFile(file)));
const backend=conformanceBackend(engine);
async function send(value) {
  if (!process.stdout.write(JSON.stringify(value) + '\n')) await once(process.stdout, 'drain');
}
let chunks = [], size = 0, oversized = false;
for await (const chunk of process.stdin) {
  let start = 0;
  while (start < chunk.length) {
    const lf = chunk.indexOf(10, start);
    const end = lf < 0 ? chunk.length : lf + 1;
    const piece = chunk.subarray(start, end);
    size += piece.length;
    if (size > MAX_LINE) { oversized = true; chunks = []; }
    if (!oversized) chunks.push(piece);
    if (lf >= 0) {
      await send(oversized ? { error: 'invalid_request' } : rawResponse(Buffer.concat(chunks, size), backend));
      chunks = []; size = 0; oversized = false;
    }
    start = end;
  }
}
if (size) await send(oversized ? { error: 'invalid_request' } : rawResponse(Buffer.concat(chunks, size), backend));
