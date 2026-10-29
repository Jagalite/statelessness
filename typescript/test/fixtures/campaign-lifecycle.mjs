// Test-only evidence. The core codec matches Rust lifecycle/codec.rs v1;
// the audited model adds a separately versioned binary event ledger envelope.
// Neither format is an exported campaign checkpoint or queue-resume API.
import { isDeepStrictEqual } from 'node:util';
import { ModelError, check, metadata } from '../../dist/index.js';
import { Lifecycle } from '../../dist/campaign_lifecycle.js';

export const U64_MAX = (1n << 64n) - 1n;
export const OUTCOMES = ['Completed', 'Failure', 'Cancelled', 'Deadline', 'FactoryError', 'ModelError', 'Panicked'];
const TERMINATIONS = ['JobsCompleted', 'FailureFound', 'Cancelled', 'Deadline', 'CallbackError', 'WorkerError'];
const STOPS = [null, 'Failure', 'Cancelled', 'Deadline', 'Sink', 'Worker'];
const REPORT_COUNTS = ['requested', 'started', 'finished', 'notStarted', 'delivered', 'failures', 'errors'];
const REPORT_TOTALS = ['reportedTransitions', 'reportedCases', 'reportedSkippedChecks'];
const errors = new Set(['FactoryError', 'ModelError', 'Panicked']);
const loss = new Set(['ModelError', 'Panicked']);
const equal = isDeepStrictEqual;
const u64 = n => typeof n === 'bigint' && n >= 0n && n <= U64_MAX;
const property = (id, passed) => check(id, passed ? 'passed' : 'failed', passed ? '' : 'campaign lifecycle contract violated');
const sorted = values => [...values].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
const difference = (left, right) => new Set([...left].filter(id => !right.has(id)));
const subset = (left, right) => [...left].every(id => right.has(id));
const invalid = () => new ModelError('invalid campaign lifecycle encoding');
export class NativeStateRepresentationError extends ModelError {
  constructor(field) {
    super(`campaign lifecycle native representability limitation: ${field} must be a nonnegative safe integer (<= Number.MAX_SAFE_INTEGER); use bigint lifecycle state`);
    this.name = 'NativeStateRepresentationError';
  }
}
const clone = value => {
  if (value instanceof ModelError) return new ModelError(value.message);
  if (value instanceof Map) return new Map([...value].map(([k, v]) => [k, clone(v)]));
  if (value instanceof Set) return new Set(value);
  if (Array.isArray(value)) return value.map(clone);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clone(v)]));
  return value;
};

export const summary = (outcome = 'Completed', overrides = {}) => ({
  outcome, failure: outcome === 'Failure', transitions: errors.has(outcome) ? 0n : 2n,
  cases: errors.has(outcome) ? 0n : 1n, skippedChecks: errors.has(outcome) ? 0n : 1n, ...overrides,
});
export const claim = (stop = null) => ({ kind: 'Claim', stop });
export const complete = (jobId, outcome = 'Completed', overrides = {}) => ({ kind: 'Complete', jobId, summary: summary(outcome, overrides) });
export const receive = jobId => ({ kind: 'Receive', jobId });
export const sink = (jobId, message = null) => ({ kind: 'SinkReturned', jobId, error: message === null ? null : new ModelError(message) });
export const worker = (message = 'worker', lostWork = false) => ({ kind: 'WorkerFailed', error: new ModelError(message), lostWork });
export const finish = () => ({ kind: 'Finish' });

// Independent predicate; never imports the production admission implementation.
function coherent(s) {
  return OUTCOMES.includes(s.outcome) && [s.transitions, s.cases, s.skippedChecks].every(u64)
    && s.failure === (s.outcome === 'Failure')
    && (!errors.has(s.outcome) || (s.transitions === 0n && s.cases === 0n && s.skippedChecks === 0n));
}

export function coreChecks(s, firstJob, jobs, stopOnFailure) {
  const r = s.report;
  const callback = r.termination.kind === 'CallbackError', workerError = r.termination.kind === 'WorkerError';
  const infrastructure = callback || workerError;
  const inRange = id => u64(id) && id >= firstJob && id - firstJob < BigInt(r.started);
  const failureObserved = r.failures > 0 || [...s.pending.values()].some(p => p.kind === 'Ready' && p.summary.failure);
  const expected = s.stop === 'Cancelled' ? 'Cancelled' : s.stop === 'Deadline' ? 'Deadline'
    : s.stop === 'Failure' ? 'FailureFound' : 'JobsCompleted';
  const reason = callback ? ['Sink', 'Worker', 'Cancelled', 'Deadline'].includes(s.stop)
    : workerError ? ['Worker', 'Cancelled', 'Deadline'].includes(s.stop)
    : s.finalized ? equal(r.termination, { kind: expected }) && !['Sink', 'Worker'].includes(s.stop)
    : r.termination.kind === 'JobsCompleted' && !['Sink', 'Worker'].includes(s.stop);
  return [
    property('campaign.accounting', REPORT_COUNTS.every(k => Number.isSafeInteger(r[k]) && r[k] >= 0)
      && REPORT_TOTALS.every(k => u64(r[k])) && r.requested === jobs && r.started <= jobs
      && r.notStarted === jobs - r.started && r.finished <= r.started && r.delivered <= r.finished
      && r.failures <= r.finished && r.errors <= r.finished - r.failures),
    property('campaign.pending', r.started - r.finished === s.pending.size
      && [...s.pending].every(([id, p]) => inRange(id) && (p.kind === 'Running' || (p.kind === 'Ready' && coherent(p.summary))))),
    property('campaign.delivery', (callback ? r.finished > r.delivered : r.finished - r.delivered === Number(s.delivering !== null))
      && (s.delivering === null || (inRange(s.delivering) && !s.pending.has(s.delivering)
        && r.delivered < r.finished && !callback))),
    property('campaign.finalized', !s.finalized || (s.delivering === null
      && (s.pending.size === 0 || (s.lostWorker && !r.accountingComplete))
      && (r.termination.kind !== 'JobsCompleted' || r.started === jobs))),
    property('campaign.termination', reason && (!s.lostWorker || (infrastructure && !r.accountingComplete))
      && (s.stop !== 'Failure' || (stopOnFailure && failureObserved))),
  ];
}

/**
 * @typedef {Omit<import('../../dist/campaign_lifecycle.js').CampaignReport,
 * 'requested' | 'started' | 'finished' | 'notStarted' | 'delivered' | 'failures' | 'errors'> & {
 * requested: bigint, started: bigint, finished: bigint, notStarted: bigint,
 * delivered: bigint, failures: bigint, errors: bigint
 * }} WireReport
 * @typedef {Omit<import('../../dist/campaign_lifecycle.js').LifecycleState, 'report'> & { report: WireReport }} WireState
 */

// Rust64 check_state, independent of the native safe-number constructor/checks.
// Decoded scalars already have their u64/tag/boolean types. Arithmetic stays
// exact, including deficits near u64::MAX; it never saturates or wraps counts.
// These are state properties, not a reconstruction of the state's history.
function validWireState(s, firstJob, jobs, stopOnFailure) {
  const r = s.report;
  const callback = r.termination.kind === 'CallbackError', workerError = r.termination.kind === 'WorkerError';
  const inRange = id => id >= firstJob && id - firstJob < r.started;
  const failureObserved = r.failures > 0n || [...s.pending.values()].some(p => p.kind === 'Ready' && p.summary.failure);
  const expected = s.stop === 'Cancelled' ? 'Cancelled' : s.stop === 'Deadline' ? 'Deadline'
    : s.stop === 'Failure' ? 'FailureFound' : 'JobsCompleted';
  const reason = callback ? ['Sink', 'Worker', 'Cancelled', 'Deadline'].includes(s.stop)
    : workerError ? ['Worker', 'Cancelled', 'Deadline'].includes(s.stop)
    : s.finalized ? r.termination.kind === expected && !['Sink', 'Worker'].includes(s.stop)
    : r.termination.kind === 'JobsCompleted' && !['Sink', 'Worker'].includes(s.stop);
  return r.requested === jobs && r.started <= r.requested && r.notStarted === r.requested - r.started
    && r.finished <= r.started && r.delivered <= r.finished && r.failures <= r.finished
    && r.errors <= r.finished - r.failures
    && r.started - r.finished === BigInt(s.pending.size)
    && [...s.pending].every(([id, p]) => inRange(id) && (p.kind === 'Running' || coherent(p.summary)))
    && (callback ? r.finished > r.delivered : r.finished - r.delivered === BigInt(s.delivering !== null))
    && (s.delivering === null || (inRange(s.delivering) && !s.pending.has(s.delivering)
      && r.delivered < r.finished && !callback))
    && (!s.finalized || (s.delivering === null
      && (s.pending.size === 0 || (s.lostWorker && !r.accountingComplete))
      && (r.termination.kind !== 'JobsCompleted' || r.started === r.requested)))
    && reason && (!s.lostWorker || ((callback || workerError) && !r.accountingComplete))
    && (s.stop !== 'Failure' || (stopOnFailure && failureObserved));
}

class Writer {
  chunks = [];
  raw(bytes) { this.chunks.push(bytes); }
  tag(n) { if (!Number.isInteger(n) || n < 0 || n > 255) throw invalid(); this.raw(Uint8Array.of(n)); }
  bool(flag) { if (typeof flag !== 'boolean') throw invalid(); this.tag(Number(flag)); }
  number(n) {
    if (!u64(n)) throw invalid();
    const bytes = new Uint8Array(8); new DataView(bytes.buffer).setBigUint64(0, n, true); this.raw(bytes);
  }
  count(n) { if (!Number.isSafeInteger(n) || n < 0) throw invalid(); this.number(BigInt(n)); }
  text(s) {
    if (typeof s !== 'string') throw invalid();
    const bytes = new TextEncoder().encode(s);
    if (new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes) !== s) throw invalid();
    this.count(bytes.length); this.raw(bytes);
  }
  error(error) { this.text(error.message); }
  optionalError(error) { this.tag(error === null ? 0 : 1); if (error !== null) this.error(error); }
  summary(s) {
    this.tag(OUTCOMES.indexOf(s.outcome)); this.bool(s.failure);
    this.number(s.transitions); this.number(s.cases); this.number(s.skippedChecks);
  }
  blob(bytes) { this.count(bytes.length); this.raw(bytes); }
  bytes() {
    const out = new Uint8Array(this.chunks.reduce((n, c) => n + c.length, 0));
    let offset = 0; for (const c of this.chunks) { out.set(c, offset); offset += c.length; }
    return out;
  }
}

class Reader {
  offset = 0;
  constructor(bytes) { if (!(bytes instanceof Uint8Array)) throw invalid(); this.bytes = bytes; }
  get remaining() { return this.bytes.length - this.offset; }
  take(n) {
    if (!Number.isSafeInteger(n) || n < 0 || n > this.remaining) throw invalid();
    const out = this.bytes.subarray(this.offset, this.offset + n); this.offset += n; return out;
  }
  tag() { return this.take(1)[0]; }
  choice(options) { const tag = this.tag(); if (tag >= options.length) throw invalid(); return options[tag]; }
  bool() { return this.choice([false, true]); }
  number() { const b = this.take(8); return new DataView(b.buffer, b.byteOffset, 8).getBigUint64(0, true); }
  length() {
    const n = this.number();
    // Only byte/collection lengths become numbers, after a bound by actual bytes.
    if (n > BigInt(this.remaining)) throw invalid();
    return Number(n);
  }
  text() {
    const bytes = this.take(this.length());
    try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); } catch { throw invalid(); }
  }
  error() { return new ModelError(this.text()); }
  optionalError() { return this.bool() ? this.error() : null; }
  summary() {
    return { outcome: this.choice(OUTCOMES), failure: this.bool(), transitions: this.number(), cases: this.number(), skippedChecks: this.number() };
  }
  blob() { return this.take(this.length()); }
  boundedCount(maximum, minBytes) {
    const n = this.number();
    if (n > BigInt(maximum) || n > BigInt(Math.floor(this.remaining / minBytes))) throw invalid();
    return Number(n);
  }
  finish() { if (this.remaining !== 0) throw invalid(); }
}

/**
 * Test-only Rust64 lifecycle/codec.rs v1 layout and full-u64 wire count domain.
 * encodeState/decodeState adapt native safe-number states; encodeWireState and
 * decodeWireState preserve all seven report counts as bigint. This does not
 * provide a public checkpoint/resume API. Exact states also feed Lifecycle<bigint>.
 */
export class LifecycleCodec {
  constructor(firstJob = 50n, jobs = 2, stopOnFailure = false) {
    if (typeof jobs === 'number') {
      if (!Number.isSafeInteger(jobs) || jobs < 0) throw new NativeStateRepresentationError('jobs');
      jobs = BigInt(jobs);
    }
    if (!u64(firstJob) || !u64(jobs) || typeof stopOnFailure !== 'boolean') throw invalid();
    // Like Rust decode_state, do not impose initial_state's job-range gate.
    Object.assign(this, { firstJob, jobs, stopOnFailure });
  }
  /** Representation conversion only; decoding separately checks wire semantics.
   * @returns {WireState}
   */
  fromNativeState(s) {
    const counts = {};
    for (const key of REPORT_COUNTS) {
      if (!Number.isSafeInteger(s.report[key]) || s.report[key] < 0) throw new NativeStateRepresentationError(`report.${key}`);
      counts[key] = BigInt(s.report[key]);
    }
    const state = clone(s); Object.assign(state.report, counts); return state;
  }
  /** No rounding: an otherwise valid wire state may not fit the native runtime.
   * @param {WireState} s
   */
  toNativeState(s) {
    if (!REPORT_COUNTS.every(key => u64(s.report[key]))) throw invalid();
    const counts = {};
    for (const key of REPORT_COUNTS) {
      if (s.report[key] > BigInt(Number.MAX_SAFE_INTEGER)) throw new NativeStateRepresentationError(`report.${key}`);
      counts[key] = Number(s.report[key]);
    }
    const state = clone(s); Object.assign(state.report, counts); return state;
  }
  encodeInput(input) {
    const w = new Writer();
    switch (input.kind) {
      case 'Claim': w.tag(0); w.tag([null, 'Cancelled', 'Deadline'].indexOf(input.stop)); break;
      case 'Complete': w.tag(1); w.number(input.jobId); w.summary(input.summary); break;
      case 'Receive': w.tag(2); w.number(input.jobId); break;
      case 'SinkReturned': w.tag(3); w.number(input.jobId); w.optionalError(input.error); break;
      case 'WorkerFailed': w.tag(4); w.error(input.error); w.bool(input.lostWork); break;
      case 'Finish': w.tag(5); break;
      default: throw invalid();
    }
    return w.bytes();
  }
  decodeInput(bytes) {
    const r = new Reader(bytes); let result;
    switch (r.tag()) {
      case 0: result = claim(r.choice([null, 'Cancelled', 'Deadline'])); break;
      case 1: result = { kind: 'Complete', jobId: r.number(), summary: r.summary() }; break;
      case 2: result = receive(r.number()); break;
      case 3: result = { kind: 'SinkReturned', jobId: r.number(), error: r.optionalError() }; break;
      case 4: result = { kind: 'WorkerFailed', error: r.error(), lostWork: r.bool() }; break;
      case 5: result = finish(); break;
      default: throw invalid();
    }
    r.finish(); return result;
  }
  encodeOutput(output) {
    const w = new Writer(); w.tag(['RunJob', 'Deliver', 'Discard'].indexOf(output.kind)); w.number(output.jobId); return w.bytes();
  }
  encodeExactState(s) { return this.encodeWireState(s); }
  decodeExactState(bytes) { return this.decodeWireState(bytes); }
  encodeState(s) { return this.encodeWireState(this.fromNativeState(s)); }
  decodeState(bytes) { return this.toNativeState(this.decodeWireState(bytes)); }
  /** @param {WireState} s */
  encodeWireState(s) {
    const w = new Writer(), r = s.report;
    for (const key of REPORT_COUNTS) w.number(r[key]);
    for (const key of REPORT_TOTALS) w.number(r[key]);
    w.bool(r.accountingComplete); w.tag(TERMINATIONS.indexOf(r.termination.kind));
    if (['CallbackError', 'WorkerError'].includes(r.termination.kind)) w.error(r.termination.error);
    w.tag(STOPS.indexOf(s.stop)); w.count(s.pending.size);
    for (const id of sorted(s.pending.keys())) {
      const p = s.pending.get(id); w.number(id); w.tag(['Running', 'Ready'].indexOf(p.kind));
      if (p.kind === 'Ready') w.summary(p.summary);
    }
    w.tag(s.delivering === null ? 0 : 1); if (s.delivering !== null) w.number(s.delivering);
    w.bool(s.lostWorker); w.bool(s.finalized); return w.bytes();
  }
  /** @returns {WireState} */
  decodeWireState(bytes) {
    const r = new Reader(bytes), report = {};
    for (const key of REPORT_COUNTS) report[key] = r.number();
    for (const key of REPORT_TOTALS) report[key] = r.number();
    report.accountingComplete = r.bool(); report.termination = { kind: r.choice(TERMINATIONS) };
    if (['CallbackError', 'WorkerError'].includes(report.termination.kind)) report.termination.error = r.error();
    const stop = r.choice(STOPS), count = r.boundedCount(this.jobs, 9), pending = new Map();
    let previous = -1n;
    for (let i = 0; i < count; i++) {
      const id = r.number(); if (id <= previous) throw invalid(); previous = id;
      pending.set(id, r.choice(['Running', 'Ready']) === 'Running' ? { kind: 'Running' } : { kind: 'Ready', summary: r.summary() });
    }
    const state = { report, stop, pending, delivering: r.bool() ? r.number() : null, lostWorker: r.bool(), finalized: r.bool() };
    r.finish();
    if (!validWireState(state, this.firstJob, this.jobs, this.stopOnFailure)) {
      throw new ModelError('decoded campaign state violates lifecycle properties');
    }
    return state;
  }
}

const SETS = ['claims', 'receipts', 'sinkAttempts', 'sinkReturns', 'sinkSuccesses', 'discards'];
const FLAGS = ['cancelled', 'deadline', 'failureStop', 'lostWork', 'invalidProtocol', 'finalized'];
function initialLedger() {
  return { ...Object.fromEntries(SETS.map(k => [k, new Set()])), completions: new Map(),
    firstSinkError: null, lastWorkerError: null, ...Object.fromEntries(FLAGS.map(k => [k, false])) };
}
const stopped = l => l.cancelled || l.deadline || l.failureStop || l.firstSinkError !== null || l.lastWorkerError !== null;
const activeSink = l => difference(l.sinkAttempts, l.sinkReturns);

// Every test event's admission comes from predecessor ledger facts, not the
// returned disposition or production counters/pending map.
function admissible(l, input, jobs) {
  if (l.finalized) return false;
  switch (input.kind) {
    case 'Claim': return !stopped(l) && l.claims.size < jobs;
    case 'Complete': return coherent(input.summary) && l.claims.has(input.jobId) && !l.completions.has(input.jobId);
    case 'Receive': return activeSink(l).size === 0 && l.completions.has(input.jobId) && !l.receipts.has(input.jobId);
    case 'SinkReturned': return activeSink(l).has(input.jobId);
    case 'WorkerFailed': return true;
    case 'Finish': return activeSink(l).size === 0 && (equal(l.claims, l.receipts) || l.lostWork)
      && (stopped(l) || l.claims.size === jobs);
    default: return false;
  }
}

export class AuditedLifecycle {
  constructor({ firstJob = 50n, jobs = 2, stopOnFailure = false, duplicateDelivery = false } = {}) {
    Object.assign(this, { firstJob, jobs, stopOnFailure, duplicateDelivery });
    this.core = new Lifecycle(firstJob, jobs, stopOnFailure);
    this.codec = new LifecycleCodec(firstJob, jobs, stopOnFailure);
  }
  metadata() {
    return { ...metadata(`stateless-campaign-ledger:${this.firstJob}:${this.jobs}:${this.stopOnFailure}`,
      `n06-ts-ledger-v1${this.duplicateDelivery ? ':duplicate-delivery' : ''}`), properties_version: 2 };
  }
  initialState() { return { core: this.core.initialState(), ledger: initialLedger() }; }
  cloneState(s) { return clone(s); }
  cloneInput(i) { return clone(i); }
  cloneOutput(o) { return { ...o }; }
  equalStates(a, b) { return equal(a, b); }
  equalInputs(a, b) { return equal(a, b); }
  // Binary hashing is only an acceleration: deep structural equality resolves
  // collisions, and includes the complete independent ledger.
  hashState(s) { return Buffer.from(this.encodeState(s)).toString('hex'); }
  step(before, input) {
    const actual = this.core.step(before.core, input);
    if (this.duplicateDelivery && actual.outputs[0]?.kind === 'Deliver') actual.outputs.push({ ...actual.outputs[0] });
    return this.observeTransition(before, input, actual);
  }
  observeTransition(before, input, actual) {
    const l = clone(before.ledger);
    if (actual.disposition.kind === 'accepted') {
      switch (input.kind) {
        case 'Claim': l.cancelled ||= input.stop === 'Cancelled'; l.deadline ||= input.stop === 'Deadline'; break;
        case 'Complete':
          l.invalidProtocol ||= !l.claims.has(input.jobId) || l.completions.has(input.jobId);
          l.completions.set(input.jobId, clone(input.summary));
          l.cancelled ||= input.summary.outcome === 'Cancelled'; l.deadline ||= input.summary.outcome === 'Deadline';
          l.failureStop ||= this.stopOnFailure && input.summary.outcome === 'Failure'; break;
        case 'Receive':
          l.invalidProtocol ||= !l.completions.has(input.jobId) || l.receipts.has(input.jobId);
          l.receipts.add(input.jobId); break;
        case 'SinkReturned':
          l.invalidProtocol ||= !l.sinkAttempts.has(input.jobId) || l.sinkReturns.has(input.jobId);
          l.sinkReturns.add(input.jobId);
          if (input.error !== null) { if (l.firstSinkError === null) l.firstSinkError = clone(input.error); }
          else { l.invalidProtocol ||= l.sinkSuccesses.has(input.jobId); l.sinkSuccesses.add(input.jobId); }
          break;
        case 'WorkerFailed': l.lastWorkerError = clone(input.error); l.lostWork ||= input.lostWork; break;
        case 'Finish': l.finalized = true; break;
      }
    }
    for (const { kind, jobId } of actual.outputs) {
      if (kind === 'RunJob') {
        l.invalidProtocol ||= l.claims.has(jobId) || stopped(before.ledger) || before.ledger.finalized;
        l.claims.add(jobId);
      } else if (kind === 'Deliver') {
        l.invalidProtocol ||= !l.receipts.has(jobId) || l.sinkAttempts.has(jobId)
          || l.firstSinkError !== null || l.discards.has(jobId);
        l.sinkAttempts.add(jobId);
      } else if (kind === 'Discard') {
        l.invalidProtocol ||= !l.receipts.has(jobId) || l.discards.has(jobId)
          || l.firstSinkError === null || l.sinkAttempts.has(jobId);
        l.discards.add(jobId);
      } else l.invalidProtocol = true;
    }
    return { ...actual, state: { core: actual.state, ledger: l } };
  }
  checkState(s) {
    const l = s.ledger, r = s.core.report, received = [...l.receipts].map(id => l.completions.get(id)).filter(Boolean);
    const sum = key => received.reduce((n, v) => n + v[key], 0n);
    const totals = [sum('transitions'), sum('cases'), sum('skippedChecks')];
    const accounting = !l.lostWork && !received.some(v => loss.has(v.outcome)) && totals.every(n => n <= U64_MAX)
      && (!l.finalized || equal(l.claims, l.receipts));
    const termination = l.firstSinkError !== null ? { kind: 'CallbackError', error: l.firstSinkError }
      : l.lastWorkerError !== null ? { kind: 'WorkerError', error: l.lastWorkerError }
      : { kind: !l.finalized ? 'JobsCompleted' : l.cancelled ? 'Cancelled' : l.deadline ? 'Deadline'
        : l.failureStop ? 'FailureFound' : 'JobsCompleted' };
    const pending = new Map([...difference(l.claims, l.receipts)].map(id => [id,
      l.completions.has(id) ? { kind: 'Ready', summary: l.completions.get(id) } : { kind: 'Running' }]));
    return [...coreChecks(s.core, this.firstJob, this.jobs, this.stopOnFailure),
      property('audit.protocol', !l.invalidProtocol),
      property('audit.stop_observed', (s.core.stop !== null) === stopped(l)),
      property('audit.active_sink', subset(l.sinkReturns, l.sinkAttempts)
        && equal(activeSink(l), new Set(s.core.delivering === null ? [] : [s.core.delivering]))),
      property('audit.identity_accounting', r.started === l.claims.size && r.finished === l.receipts.size
        && r.delivered === l.sinkSuccesses.size && r.failures === received.filter(v => v.failure).length
        && r.errors === received.filter(v => errors.has(v.outcome)).length && equal(pending, s.core.pending)),
      property('audit.work_accounting', REPORT_TOTALS.every((k, i) => r[k] === (totals[i] > U64_MAX ? U64_MAX : totals[i]))
        && r.accountingComplete === accounting),
      property('audit.receipt_has_one_destination', [...l.sinkAttempts].every(id => !l.discards.has(id))
        && equal(new Set([...l.sinkAttempts, ...l.discards]), l.receipts)),
      property('audit.stop_precedence', equal(r.termination, termination)),
      property('audit.finalization', s.core.finalized === l.finalized && s.core.lostWorker === l.lostWork),
    ];
  }
  checkTransition(before, input, t) {
    const allowed = admissible(before.ledger, input, this.jobs), accepted = t.disposition.kind === 'accepted';
    let effects = [];
    if (allowed && input.kind === 'Claim' && input.stop === null) {
      effects = [{ kind: 'RunJob', jobId: this.firstJob + BigInt(before.ledger.claims.size) }];
    } else if (allowed && input.kind === 'Receive') {
      effects = [{ kind: before.ledger.firstSinkError === null ? 'Deliver' : 'Discard', jobId: input.jobId }];
    }
    return [
      property('campaign.admission', accepted === allowed),
      property('campaign.effects', equal(t.outputs, effects)),
      property('campaign.rejection_is_inert', accepted || (equal(before, t.state) && t.outputs.length === 0)),
      property('campaign.first_sink_error', before.ledger.firstSinkError === null
        || equal(t.state.core.report.termination, { kind: 'CallbackError', error: before.ledger.firstSinkError })),
      property('campaign.accounting_loss_is_sticky', before.core.report.accountingComplete || !t.state.core.report.accountingComplete),
      property('campaign.finalization_is_terminal', !before.ledger.finalized || equal(before, t.state)),
    ];
  }
  alphabet() {
    const values = [claim(), claim('Cancelled'), claim('Deadline'), worker(), worker('worker', true), finish()];
    for (let n = 0; n <= this.jobs; n++) {
      const id = this.firstJob + BigInt(n);
      values.push(...OUTCOMES.map(outcome => complete(id, outcome)), receive(id), sink(id), sink(id, 'sink'));
      // Well-formed wire events with inconsistent semantics must reach actual
      // BFS/fuzz admission, not only the standalone decoder/negative tests.
      values.push(complete(id, 'Completed', { failure: true }), complete(id, 'FactoryError', { cases: 1n }));
    }
    return values;
  }
  inputs(state) { return state.ledger.finalized ? [] : this.alphabet(); }
  isEnabled() { return true; } // Invalid protocol events are legitimate test inputs.
  simplerInputs() { return []; }
  generate(state, rng) {
    let choices = [];
    if (!state.ledger.finalized && rng.index(4n) !== 0n) {
      choices = this.alphabet().filter(i => admissible(state.ledger, i, this.jobs)
        && !(i.kind === 'Claim' && i.stop !== null) && i.kind !== 'WorkerFailed');
    }
    if (!choices.length) choices = this.alphabet();
    return this.cloneInput(choices[Number(rng.index(BigInt(choices.length)))]);
  }
  encodeInput(i) { return this.codec.encodeInput(i); }
  decodeInput(b) { return this.codec.decodeInput(b); }
  encodeOutput(o) { return this.codec.encodeOutput(o); }
  encodeState(s) {
    const w = new Writer(); w.tag(1); w.blob(this.codec.encodeState(s.core));
    for (const key of SETS) { w.count(s.ledger[key].size); for (const id of sorted(s.ledger[key])) w.number(id); }
    w.count(s.ledger.completions.size);
    for (const id of sorted(s.ledger.completions.keys())) { w.number(id); w.summary(s.ledger.completions.get(id)); }
    w.optionalError(s.ledger.firstSinkError); w.optionalError(s.ledger.lastWorkerError);
    for (const key of FLAGS) w.bool(s.ledger[key]);
    return w.bytes();
  }
  decodeState(bytes) {
    const r = new Reader(bytes); if (r.tag() !== 1) throw invalid();
    const core = this.codec.decodeState(r.blob()), ledger = initialLedger();
    const ids = (minBytes, insert) => {
      const count = r.boundedCount(this.jobs, minBytes); let previous = -1n;
      for (let i = 0; i < count; i++) { const id = r.number(); if (id <= previous) throw invalid(); previous = id; insert(id); }
    };
    for (const key of SETS) ids(8, id => ledger[key].add(id));
    ids(34, id => ledger.completions.set(id, r.summary()));
    ledger.firstSinkError = r.optionalError(); ledger.lastWorkerError = r.optionalError();
    for (const key of FLAGS) ledger[key] = r.bool();
    r.finish(); return { core, ledger };
  }
}
