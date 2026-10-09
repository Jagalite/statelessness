#!/usr/bin/env node
// Byte-bounded JSONL framing, including malformed UTF-8 and post-error recovery.
import { once } from 'node:events';
import { rawResponse, MAX_LINE } from '../dist/conformance.js';
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
      await send(oversized ? { error: 'invalid_request' } : rawResponse(Buffer.concat(chunks, size)));
      chunks = []; size = 0; oversized = false;
    }
    start = end;
  }
}
if (size) await send(oversized ? { error: 'invalid_request' } : rawResponse(Buffer.concat(chunks, size)));
