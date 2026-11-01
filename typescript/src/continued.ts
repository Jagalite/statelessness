/** Experimental extension `continued-failure-evidence-v1` (S5); see
 * spec/continued-failure-evidence-v1.md. A continued trace may record steps
 * after a failed check. It is a distinct versioned format: `validateRecording`,
 * `replay`, and `parseTrace` (trace-json v1) reject it rather than silently
 * reinterpreting a later failure as the end of a stop-at-failure trace. */
import { type Check, type CodecModel, type ContinuedTrace, type ReplayReport, type Trace, type TraceLimits, ModelError, DEFAULT_TRACE_LIMITS, validateTraceLimits } from './core.js';
import { FormatError, array, fields, integer, strictParse, stringifyTrace, traceFromData } from './json.js';
import { replayContinuedKernel } from './replay.js';

export const CONTINUED_TRACE_FORMAT = 'stateless.continued-trace-json';
export const CONTINUED_TRACE_VERSION = 1;
const failedBatch = (checks: readonly Check[]) => checks.some(c => c.status === 'failed');
/** 0 for the initial state; otherwise 1-based steps whose checks failed. Bounded by
 * the step array length, so Number sequences are exact. */
export function continuedFailingSequences(t: ContinuedTrace): number[] {
  const out = failedBatch(t.trace.initialChecks) ? [0] : [];
  t.trace.steps.forEach((step, index) => { if (failedBatch(step.checks)) out.push(index + 1); });
  return out;
}
export function validateContinuedRecording(t: ContinuedTrace): void {
  const { termination, error } = t.trace;
  if (!['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].includes(termination)) throw new ModelError('unknown trace termination');
  if (error && termination !== 'model_error') throw new ModelError('error text requires model_error termination');
  if (termination === 'property_failed') throw new ModelError('continued traces do not terminate on property failure');
}
export interface ContinuedReplayReport {
  replay: ReplayReport;
  recordedFailures: number[];
  /** Recorded failing sequences whose replayed checks failed with an equal ID. */
  reproducedFailures: number[];
  /** Exact outcome, every recorded step verified (states, dispositions, outputs
   * and checks), and every recorded failure reproduced. A verified prefix is
   * never complete reproduction. */
  completeReproduction: boolean;
}
export function replayContinued<S, I, O>(m: CodecModel<S, I, O>, trace: ContinuedTrace, allowBuildMismatch = false): ContinuedReplayReport {
  const reproduced: number[] = [];
  const same = (expected: readonly Check[], actual: readonly Check[]) => {
    const failed = expected.filter(c => c.status === 'failed');
    return failed.length > 0 && failed.every(e => actual.some(a => a.status === 'failed' && a.id === e.id));
  };
  const report = replayContinuedKernel(m, trace.trace, allowBuildMismatch, t => validateContinuedRecording({ trace: t }), observation => {
    if (observation.kind === 'initial') { if (same(observation.expected_checks, observation.checks)) reproduced.push(0); }
    else if (observation.kind === 'turn') { if (observation.actual.checks && same(observation.expected.checks, observation.actual.checks)) reproduced.push(observation.actual.sequence); }
  });
  const recorded = continuedFailingSequences(trace);
  const complete = report.outcome === 'exact' && report.steps_verified === trace.trace.steps.length && reproduced.length === recorded.length && reproduced.every((n, i) => n === recorded[i]);
  return { replay: report, recordedFailures: recorded, reproducedFailures: reproduced, completeReproduction: complete };
}
/** The trace-json v1 object with the continued format, version 1 and `failing_sequences`. */
export function encodeContinuedTrace(t: ContinuedTrace, limits: TraceLimits = DEFAULT_TRACE_LIMITS): string {
  validateContinuedRecording(t);
  const data = JSON.parse(stringifyTrace(t.trace, limits)) as Record<string, unknown>;
  data.format = CONTINUED_TRACE_FORMAT;
  data.version = CONTINUED_TRACE_VERSION;
  data.failing_sequences = continuedFailingSequences(t);
  const result = JSON.stringify(data);
  if (new TextEncoder().encode(result).length > limits.maxJSONBytes) throw new FormatError('trace JSON limit');
  return result;
}
export function parseContinuedTrace(bytes: string | Uint8Array, limits: TraceLimits = DEFAULT_TRACE_LIMITS): ContinuedTrace {
  validateTraceLimits(limits);
  const o = fields(strictParse(bytes, limits.maxJSONBytes), ['format', 'version', 'metadata', 'initial_state', 'initial_checks', 'steps', 'termination', 'error', 'failing_sequences']);
  if (o.format !== CONTINUED_TRACE_FORMAT || o.version !== CONTINUED_TRACE_VERSION) throw new FormatError('unsupported continued trace format');
  const declared = array(o.failing_sequences, limits.maxSteps + 1).map(v => integer(v));
  const { failing_sequences: _, ...rest } = o;
  const t: ContinuedTrace = { trace: traceFromData({ ...rest, format: 'stateless.trace-json', version: 1 }, limits) as Trace };
  try { validateContinuedRecording(t); } catch (error) { throw new FormatError(error instanceof Error ? error.message : String(error)); }
  const actual = continuedFailingSequences(t);
  if (declared.length !== actual.length || declared.some((n, i) => n !== actual[i])) throw new FormatError('failing_sequences disagree with recorded checks');
  return t;
}
