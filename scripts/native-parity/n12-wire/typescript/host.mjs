// Fresh installed consumer: every request, response and frame belongs to the library.
import {writeSync} from 'node:fs';
import {accepted, check, metadata, ModelError} from '@jagalite/statelessness';
import {DebugSession} from '@jagalite/statelessness/debug/session';
import {ProtocolSession, serve} from '@jagalite/statelessness/debug/protocol';

const authority = {deliverInputs: true};
const limits = {};
for (const argument of process.argv.slice(2)) {
  if (argument === '--read-only') authority.deliverInputs = false;
  else if (argument === '--exact') authority.readExactValues = true;
  else if (argument === '--cache-one') limits.maxCacheEntries = 1;
  else throw new Error('unrecognized N12 TypeScript host option');
}

// Keep a broken stdout pipe on the public serve error path so FINAL is emitted.
process.on('SIGPIPE', () => {});

function writeAll(descriptor, bytes) {
  for (let offset = 0; offset < bytes.length;) {
    const written = writeSync(descriptor, bytes, offset, bytes.length - offset);
    if (written === 0) throw new Error('transport write made no progress');
    offset += written;
  }
}

function stderrLine(value) {
  writeAll(2, Buffer.from(`${value}\n`, 'utf8'));
}

function callback(value) {
  stderrLine(`CALL ${value}`);
}

function u64(value) {
  if (value < 0n || value > 18446744073709551615n) throw new ModelError('fixture state overflow');
  const bytes = new Uint8Array(8);
  new DataView(bytes.buffer).setBigUint64(0, value, true);
  return bytes;
}

const model = {
  metadata: () => metadata('n12-wire-counter', 'n12-wire-v1'),
  initialState() { callback('initial'); return 0n; },
  cloneState: state => state,
  cloneInput: input => input,
  cloneOutput: output => output,
  inputs: () => [0, 1, 2, 3],
  equalInputs: (left, right) => left === right,
  step(state, input) {
    callback(`step ${state} ${input}`);
    if (input === 0) return {state, outputs: [], disposition: {kind: 'ignored', reason: 'fixture ignored'}};
    if (input === 1) {
      if (state === 18446744073709551615n) throw new ModelError('fixture state overflow');
      return accepted(state + 1n, [state + 1n]);
    }
    if (input === 2) return {state, outputs: [], disposition: {kind: 'rejected', reason: 'fixture rejection'}};
    if (input === 3) throw new ModelError('private reducer detail');
    throw new ModelError('invalid fixture input');
  },
  checkState(state) {
    callback(`state ${state}`);
    return [state <= 2n ? check('bounded') : check('bounded', 'failed', 'private state detail')];
  },
  checkTransition(before, input, transition) {
    callback(`transition ${before} ${input} ${transition.state}`);
    return [check('transition')];
  },
  encodeState: u64,
  decodeState(bytes) {
    if (bytes.length !== 8) throw new ModelError('fixture state requires eight bytes');
    return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getBigUint64(0, true);
  },
  encodeInput: input => Uint8Array.of(input),
  decodeInput(bytes) {
    callback(`decode ${Buffer.from(bytes).toString('hex')}`);
    if (bytes.length !== 1 || bytes[0] > 3) throw new ModelError('fixture input requires one byte in 0..=3');
    return bytes[0];
  },
  encodeOutput: u64,
  encodeStateInto: (state, buffer) => { buffer.extend(u64(state)); },
  encodeInputInto: (input, buffer) => { buffer.extend(Uint8Array.of(input)); },
  encodeOutputInto: (output, buffer) => { buffer.extend(u64(output)); },
};

const session = new DebugSession('wire\té😀', model,
  {kind: 'unrestricted', label: 'out-of-domain-injection'},
  {maxTurns: 100000n, maxContinue: 1000, maxBreakpoints: 64, maxCandidates: 1000});
const endpoint = new ProtocolSession(session, bytes => model.decodeInput(bytes), authority, limits);
try {
  await serve(endpoint, process.stdin, frame => writeAll(1, frame));
} catch (error) {
  stderrLine(`transport error: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
} finally {
  stderrLine(`FINAL ${session.state} ${session.revision} ${session.sequence}`);
}
