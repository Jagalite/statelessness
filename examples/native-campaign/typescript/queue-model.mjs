import { ModelError, accepted, check, metadata } from '@jagalite/statelessness';

// structuredClone does not preserve this class or its private fields. The worker
// module encodes inputs explicitly, and the caller reconstructs their behavior.
export class QueueInput {
  #kind;
  #value;

  constructor(kind, value = null) {
    if (kind !== 'push' && kind !== 'pop') throw new ModelError('unknown queue input');
    if (kind === 'push'
      ? !Number.isInteger(value) || value < 0 || value > 7
      : value !== null) throw new ModelError('invalid queue input value');
    this.#kind = kind;
    this.#value = value;
  }

  get kind() { return this.#kind; }
  get value() { return this.#value; }
  clone() { return new QueueInput(this.#kind, this.#value); }
  equals(other) {
    return other instanceof QueueInput
      && this.#kind === other.#kind && this.#value === other.#value;
  }
  describe() { return this.#kind === 'push' ? `push(${this.#value})` : 'pop()'; }
  toWire() { return { version: 1, kind: this.#kind, value: this.#value }; }

  static fromWire(wire) {
    if (!wire || typeof wire !== 'object' || wire.version !== 1) {
      throw new ModelError('unsupported queue input transfer version');
    }
    return new QueueInput(wire.kind, wire.value);
  }
}

// Each factory constructs a new identity. State and feature keys contain a Symbol,
// which is deliberately not transferable. Only report inputs leave this model.
export class QueueModel {
  #identity = Symbol('job-local queue');
  #disposed = false;
  #bug;

  constructor(jobId, { bug = false } = {}) {
    this.jobId = jobId;
    this.#bug = bug;
  }

  metadata() {
    return metadata('campaign-queue', this.#bug ? 'queue-lifo-fault-v1' : 'queue-fifo-v1');
  }

  #own(state) {
    if (this.#disposed || state.owner !== this.#identity) {
      throw new ModelError('queue state belongs to a different or disposed model');
    }
  }

  initialState() { return { owner: this.#identity, values: [] }; }
  cloneState(state) {
    this.#own(state);
    return { owner: this.#identity, values: [...state.values] };
  }
  cloneInput(input) { return input.clone(); }
  cloneOutput(output) { return { ...output }; }
  equalInputs(left, right) { return left.equals(right); }

  isEnabled(state, input) {
    this.#own(state);
    return input.kind === 'push' ? state.values.length < 3 : state.values.length > 0;
  }

  generate(state, rng) {
    this.#own(state);
    const push = state.values.length === 0
      || (state.values.length < 3 && rng.index(2n) === 0n);
    return push ? new QueueInput('push', Number(rng.index(8n))) : new QueueInput('pop');
  }

  step(state, input) {
    if (!this.isEnabled(state, input)) throw new ModelError('queue input is not enabled');
    const next = this.cloneState(state);
    if (input.kind === 'push') {
      next.values.push(input.value);
      return accepted(next);
    }
    const value = this.#bug ? next.values.pop() : next.values.shift();
    return accepted(next, [{ kind: 'popped', value }]);
  }

  checkState(state) {
    this.#own(state);
    return [check('queue.capacity', state.values.length <= 3 ? 'passed' : 'failed',
      state.values.length <= 3 ? '' : 'queue capacity exceeded')];
  }

  checkTransition(before, input, after) {
    this.#own(before);
    this.#own(after.state);
    const expected = input.kind === 'push'
      ? [...before.values, input.value] : before.values.slice(1);
    const fifo = expected.length === after.state.values.length
      && expected.every((value, index) => value === after.state.values[index]);
    const output = input.kind === 'push' ? after.outputs.length === 0
      : after.outputs.length === 1 && after.outputs[0].kind === 'popped'
        && after.outputs[0].value === before.values[0];
    return [
      check('queue.fifo', fifo ? 'passed' : 'failed', fifo ? '' : 'queue order changed'),
      check('queue.output', output ? 'passed' : 'failed', output ? '' : 'wrong dequeued value'),
    ];
  }

  feature(state) {
    this.#own(state);
    // A projection for novelty, not state equality or exhaustive coverage.
    return { owner: this.#identity, depth: state.values.length, head: state.values[0] ?? null };
  }

  dispose() { this.#disposed = true; }
}

export class QueueFeedback {
  #model;
  constructor(model) { this.#model = model; }
  metadata() { return { name: 'queue-depth-head', version: 1, build: 'queue-features-v1' }; }
  cloneFeature(feature) { return { ...feature }; }
  equalFeatures(left, right) {
    return left.owner === right.owner && left.depth === right.depth && left.head === right.head;
  }
  hashFeature(feature) { return `${feature.depth}:${feature.head}`; }
  initialFeatures(state, out) { out.push(this.#model.feature(state)); }
  transitionFeatures(_before, _input, after, out) { out.push(this.#model.feature(after.state)); }
  dispose() { this.#model = null; }
}
