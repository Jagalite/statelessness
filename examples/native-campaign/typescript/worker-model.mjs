import { ModelError } from '@jagalite/statelessness';
import { QueueFeedback, QueueModel } from './queue-model.mjs';

// Imported by the campaign worker. No model or callback closure is sent from the
// caller. data contains structured-cloneable settings; context.cancellation is
// the shared cooperative token. The engine polls it around synchronous callbacks.
export function createModel(jobId, data, _context) {
  if (jobId === data.factoryErrorJob) throw new ModelError('example model factory refused this job');
  if (jobId === data.panicJob) throw new Error('example unexpected factory exception');
  return new QueueModel(jobId, { bug: data.bug });
}

export function createFeedback(jobId, model, data, _context) {
  if (jobId === data.feedbackErrorJob) throw new ModelError('example feedback factory refused this job');
  return new QueueFeedback(model);
}

// Transfers only application input values in failure/corpus reports. Model state,
// output values and Symbol-bearing feature keys remain local to the worker.
export function encodeInput(input) { return input.toWire(); }
export function disposeFeedback(feedback) { feedback.dispose(); }
export function disposeModel(model) { model.dispose(); }
