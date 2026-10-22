/** Private campaign decision core. Executors own reports, clocks, workers and sinks.
 * This module is deliberately absent from the package's public index.
 */
import { ModelError } from './errors.js';
import type { Transition } from './core.js';

const U64_MAX = (1n << 64n) - 1n;

export type CampaignTermination =
  | { kind: 'JobsCompleted' | 'Cancelled' | 'Deadline' | 'FailureFound' }
  | { kind: 'CallbackError' | 'WorkerError'; error: ModelError };

export interface CampaignReport {
  requested: number;
  started: number;
  finished: number;
  delivered: number;
  notStarted: number;
  failures: number;
  errors: number;
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
export interface LifecycleState {
  report: CampaignReport;
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

export class Lifecycle {
  state: LifecycleState;
  lastAccepted: boolean | null = null;
  lastRejection: string | null = null;

  constructor(readonly firstJob: bigint, readonly jobs: number, readonly stopOnFailure: boolean) {
    if (!isU64(firstJob) || !Number.isSafeInteger(jobs) || jobs < 1
      || firstJob + BigInt(jobs - 1) > U64_MAX) {
      throw new ModelError('invalid campaign lifecycle job range');
    }
    if (typeof stopOnFailure !== 'boolean') throw new ModelError('invalid campaign lifecycle failure policy');
    this.state = this.initialState();
  }

  initialState(): LifecycleState {
    return {
      report: {
        requested: this.jobs, started: 0, finished: 0, delivered: 0,
        notStarted: this.jobs, failures: 0, errors: 0,
        reportedTransitions: 0n, reportedCases: 0n, reportedSkippedChecks: 0n,
        accountingComplete: true, termination: { kind: 'JobsCompleted' },
      },
      stop: null, pending: new Map(), delivering: null, lostWorker: false, finalized: false,
    };
  }

  cloneState(state: LifecycleState): LifecycleState {
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
  step(before: LifecycleState, input: LifecycleInput): Transition<LifecycleState, LifecycleOutput> {
    return this.reduce(this.cloneState(before), input);
  }

  private reduce(state: LifecycleState, input: LifecycleInput): Transition<LifecycleState, LifecycleOutput> {
    const reject = (reason: string): Transition<LifecycleState, LifecycleOutput> =>
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
          report.started++;
          report.notStarted--;
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
        report.finished++;
        if (summary.failure) report.failures++;
        if (summary.outcome === 'FactoryError') report.errors++;
        else if (summary.outcome === 'ModelError' || summary.outcome === 'Panicked') {
          report.errors++;
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
        } else report.delivered++;
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
