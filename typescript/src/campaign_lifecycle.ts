import { count64, countLike, type Count, type CountValue } from './counts.js';
/** Private campaign decision core. Executors own reports, clocks, workers and sinks.
 * This module is deliberately absent from the package's public index.
 */
import { ModelError } from './errors.js';
import type { Transition } from './core.js';

const U64_MAX = (1n << 64n) - 1n;

export type CampaignTermination =
  | { kind: 'JobsCompleted' | 'Cancelled' | 'Deadline' | 'FailureFound' }
  | { kind: 'CallbackError' | 'WorkerError'; error: ModelError };

export interface CampaignReport<C extends Count = number> {
  requested: CountValue<C>;
  started: CountValue<C>;
  finished: CountValue<C>;
  delivered: CountValue<C>;
  notStarted: CountValue<C>;
  failures: CountValue<C>;
  errors: CountValue<C>;
  reportedTransitions: bigint;
  reportedCases: bigint;
  reportedSkippedChecks: bigint;
  accountingComplete: boolean;
  termination: CampaignTermination;
}

export type Outcome = 'Completed' | 'Failure' | 'Cancelled' | 'Deadline'
  | 'FactoryError' | 'ModelError' | 'Panicked';
export interface Summary {
  outcome: Outcome;
  failure: boolean;
  transitions: bigint;
  cases: bigint;
  skippedChecks: bigint;
}
export type ExternalStop = 'Cancelled' | 'Deadline';
export type Stop = 'Failure' | ExternalStop | 'Sink' | 'Worker';
export type Pending = { kind: 'Running' } | { kind: 'Ready'; summary: Summary };
export interface LifecycleState<C extends Count = number> {
  report: CampaignReport<C>;
  stop: Stop | null;
  pending: Map<bigint, Pending>;
  delivering: bigint | null;
  lostWorker: boolean;
  finalized: boolean;
}
export type LifecycleInput =
  | { kind: 'Claim'; stop: ExternalStop | null }
  | { kind: 'Complete'; jobId: bigint; summary: Summary }
  | { kind: 'Receive'; jobId: bigint }
  | { kind: 'SinkReturned'; jobId: bigint; error: ModelError | null }
  | { kind: 'WorkerFailed'; error: ModelError; lostWork: boolean }
  | { kind: 'Finish' };
export type LifecycleOutput = { kind: 'RunJob' | 'Deliver' | 'Discard'; jobId: bigint };
export type State = LifecycleState;
export type Input = LifecycleInput;
export type Output = LifecycleOutput;

function isU64(value: bigint): boolean {
  return typeof value === 'bigint' && value >= 0n && value <= U64_MAX;
}

function validSummary(summary: Summary): boolean {
  if (![summary.transitions, summary.cases, summary.skippedChecks].every(isU64)) return false;
  switch (summary.outcome) {
    case 'FactoryError': case 'ModelError': case 'Panicked':
      return summary.failure === false && summary.transitions === 0n
        && summary.cases === 0n && summary.skippedChecks === 0n;
    case 'Completed': case 'Cancelled': case 'Deadline':
      return summary.failure === false;
    case 'Failure':
      return summary.failure === true;
    default:
      return false;
  }
}

function copyTermination(termination: CampaignTermination): CampaignTermination {
  if ('error' in termination) return { kind: termination.kind, error: new ModelError(termination.error.message) };
  return { ...termination };
}

export class Lifecycle<C extends Count = number> {
  state: LifecycleState<C>;
  lastAccepted: boolean | null = null;
  lastRejection: string | null = null;

  constructor(readonly firstJob: bigint, readonly jobs: C, readonly stopOnFailure: boolean) {
    if (!isU64(firstJob) || !validCount(jobs) || BigInt(jobs) < 1n
      || firstJob + (BigInt(jobs) - 1n) > U64_MAX) {
      throw new ModelError('invalid campaign lifecycle job range');
    }
    if (typeof stopOnFailure !== 'boolean') throw new ModelError('invalid campaign lifecycle failure policy');
    this.state = this.initialState();
  }

  initialState(): LifecycleState<C> {
    return {
      report: {
        requested: countLike(BigInt(this.jobs), this.jobs), started: countLike(0n, this.jobs), finished: countLike(0n, this.jobs), delivered: countLike(0n, this.jobs),
        notStarted: countLike(BigInt(this.jobs), this.jobs), failures: countLike(0n, this.jobs), errors: countLike(0n, this.jobs),
        reportedTransitions: 0n, reportedCases: 0n, reportedSkippedChecks: 0n,
        accountingComplete: true, termination: { kind: 'JobsCompleted' },
      },
      stop: null, pending: new Map(), delivering: null, lostWorker: false, finalized: false,
    };
  }

  cloneState(state: LifecycleState<C>): LifecycleState<C> {
    return {
      ...state,
      report: { ...state.report, termination: copyTermination(state.report.termination) },
      pending: new Map<bigint, Pending>(Array.from(state.pending, ([id, pending]): [bigint, Pending] => [id,
        pending.kind === 'Running' ? { kind: 'Running' } : { kind: 'Ready', summary: { ...pending.summary } },
      ])),
    };
  }

  /** Runtime entry: rejected inputs are inert and return []. Accepted inputs can
   * also return []; lastAccepted/lastRejection distinguish them after apply().
   * Live events do not clone the pending map or retain application reports.
   */
  apply(input: LifecycleInput): LifecycleOutput[] {
    const transition = this.reduce(this.state, input);
    this.lastAccepted = transition.disposition.kind === 'accepted';
    this.lastRejection = this.lastAccepted ? null : transition.disposition.reason;
    return transition.outputs as LifecycleOutput[];
  }

  /** Snapshot entry for the self-model; it shares the runtime transition law. */
  step(before: LifecycleState<C>, input: LifecycleInput): Transition<LifecycleState<C>, LifecycleOutput> {
    return this.reduce(this.cloneState(before), input);
  }

  private reduce(state: LifecycleState<C>, input: LifecycleInput): Transition<LifecycleState<C>, LifecycleOutput> {
    const reject = (reason: string): Transition<LifecycleState<C>, LifecycleOutput> =>
      ({ state, outputs: [], disposition: { kind: 'rejected', reason } });
    if (state.finalized) return reject('campaign is finalized');
    const outputs: LifecycleOutput[] = [];
    const report = state.report;
    switch (input.kind) {
      case 'Claim': {
        // A sampled control on a rejected late claim must not rewrite a stop.
        if (state.stop !== null || report.started === this.jobs) return reject('campaign cannot claim more jobs');
        if (input.stop !== null) state.stop = input.stop;
        else {
          const jobId = this.firstJob + BigInt(report.started);
          state.pending.set(jobId, { kind: 'Running' });
          report.started = countLike(BigInt(report.started) + 1n, this.jobs);
          report.notStarted = countLike(BigInt(report.notStarted) - 1n, this.jobs);
          outputs.push({ kind: 'RunJob', jobId });
        }
        break;
      }
      case 'Complete': {
        if (!validSummary(input.summary)) return reject('inconsistent job summary');
        if (state.pending.get(input.jobId)?.kind !== 'Running') return reject('job is not running');
        const summary = { ...input.summary };
        state.pending.set(input.jobId, { kind: 'Ready', summary });
        if (summary.outcome === 'Cancelled') state.stop = 'Cancelled';
        else if (summary.outcome === 'Deadline' && state.stop !== 'Cancelled') state.stop = 'Deadline';
        else if (summary.outcome === 'Failure' && this.stopOnFailure && state.stop === null) state.stop = 'Failure';
        break;
      }
      case 'Receive': {
        const pending = state.pending.get(input.jobId);
        if (pending?.kind !== 'Ready') return reject('job has no completed report');
        if (state.delivering !== null) return reject('sink is already delivering a report');
        const summary = pending.summary;
        state.pending.delete(input.jobId);
        report.finished = countLike(BigInt(report.finished) + 1n, this.jobs);
        if (summary.failure) report.failures = countLike(BigInt(report.failures) + 1n, this.jobs);
        if (summary.outcome === 'FactoryError') report.errors = countLike(BigInt(report.errors) + 1n, this.jobs);
        else if (summary.outcome === 'ModelError' || summary.outcome === 'Panicked') {
          report.errors = countLike(BigInt(report.errors) + 1n, this.jobs);
          report.accountingComplete = false;
        }
        for (const [key, count] of [
          ['reportedTransitions', summary.transitions],
          ['reportedCases', summary.cases],
          ['reportedSkippedChecks', summary.skippedChecks],
        ] as const) {
          const sum = report[key] + count;
          report[key] = sum > U64_MAX ? U64_MAX : sum;
          if (sum > U64_MAX) report.accountingComplete = false;
        }
        if (report.termination.kind === 'CallbackError') outputs.push({ kind: 'Discard', jobId: input.jobId });
        else {
          state.delivering = input.jobId;
          outputs.push({ kind: 'Deliver', jobId: input.jobId });
        }
        break;
      }
      case 'SinkReturned': {
        if (state.delivering !== input.jobId) return reject('job is not awaiting a sink result');
        state.delivering = null;
        if (input.error !== null) {
          report.termination = { kind: 'CallbackError', error: new ModelError(input.error.message) };
          state.stop = 'Sink';
        } else report.delivered = countLike(BigInt(report.delivered) + 1n, this.jobs);
        break;
      }
      case 'WorkerFailed': {
        if (report.termination.kind !== 'CallbackError') {
          report.termination = { kind: 'WorkerError', error: new ModelError(input.error.message) };
        }
        state.stop = 'Worker';
        state.lostWorker ||= input.lostWork;
        report.accountingComplete &&= !input.lostWork;
        break;
      }
      case 'Finish': {
        if (state.delivering !== null || (state.pending.size !== 0 && !state.lostWorker)
          || (state.stop === null && report.started !== this.jobs)) return reject('campaign has unfinished work');
        report.accountingComplete &&= report.started === report.finished;
        if (report.termination.kind !== 'CallbackError' && report.termination.kind !== 'WorkerError') {
          report.termination = { kind: state.stop === 'Cancelled' ? 'Cancelled'
            : state.stop === 'Deadline' ? 'Deadline' : state.stop === 'Failure' ? 'FailureFound' : 'JobsCompleted' };
        }
        state.finalized = true;
        break;
      }
    }
    return { state, outputs, disposition: { kind: 'accepted', reason: '' } };
  }
}

function validCount(value: Count): boolean { try { count64(value, "jobs"); return true; } catch { return false; } }
export type CampaignReport64 = CampaignReport<bigint>;
export type LifecycleState64 = LifecycleState<bigint>;
