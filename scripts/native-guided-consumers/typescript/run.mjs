#!/usr/bin/env node
// Fixture adapter only. Search, novelty, mutation, recording, replay, and shrink
// belong to the installed public package. Expected corpus values are discarded.
import { createHash } from 'node:crypto';
import { lstat, readFile, realpath, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validatePublishedSeal } from './seal.mjs';

const AUTHORITY = 'a4cc11eea8db62115b452eee3c6610c04fa06b96';
const SCHEMA = 'stateless-guided-v2/1';
const FEEDBACK_METADATA = { name: 'table-features', version: 1, build: 'guided-v2-fixture-v1' };
const RESET_CONTROL = { cancelled: false, zero_duration: false, cancel_after: null };
const PHASES = { initial_state: 'InitialState', state: 'State', transition: 'Transition' };
const STATUSES = { passed: 'Passed', failed: 'Failed', skipped: 'Skipped' };
const DISPOSITIONS = { accepted: 'Accepted', rejected: 'Rejected', ignored: 'Ignored' };
const FUZZ_TERMINATIONS = {
  cases_completed: 'CasesCompleted', transition_limit: 'TransitionLimit',
  failure_found: 'FailureFound', cancelled: 'Cancelled', deadline: 'Deadline',
};
const SHRINK_TERMINATIONS = {
  search_complete: 'SearchComplete', attempt_limit: 'AttemptLimit',
  transition_limit: 'TransitionLimit', cancelled: 'Cancelled', deadline: 'Deadline',
};
const SHRINK_OUTCOMES = {
  retained: 'Retained', invalid_causality: 'InvalidCausality', different_failure: 'DifferentFailure',
  no_failure: 'NoFailure', already_seen: 'AlreadySeen', resource_limit: 'ResourceLimit',
};
const clone = value => structuredClone(value);
const errorText = error => error instanceof Error ? error.message : String(error);

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

function enumValue(table, value) {
  requireValue(Object.hasOwn(table, value), `unknown public enum value: ${String(value)}`);
  return table[value];
}

function safeInteger(value, label) {
  requireValue(Number.isSafeInteger(value), `${label} must be an exact safe integer`);
  return value;
}

function inputU64(value, label) {
  if (typeof value === 'number') return BigInt(safeInteger(value, label));
  requireValue(typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value), `${label} must be unsigned decimal`);
  return BigInt(value);
}

// The schema represents counters as JSON integers, including u64. Do not round
// bigint counters through Number or turn them into strings (only seeds/draws are strings).
function json(value) {
  if (typeof value === 'bigint') return value.toString();
  if (typeof value === 'number') safeInteger(value, 'JSON integer');
  if (value === null || typeof value !== 'object') {
    const result = JSON.stringify(value);
    requireValue(result !== undefined, 'undefined is not a normalized JSON value');
    return result;
  }
  if (Array.isArray(value)) return `[${value.map(json).join(',')}]`;
  return `{${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}:${json(item)}`).join(',')}}`;
}

function equalValue(left, right) {
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => equalValue(value, right[index]));
  }
  return typeof left === typeof right && left === right;
}

function checksJSON(checks) {
  return checks.map(item => ({
    id: item.id, status: enumValue(STATUSES, item.status),
    message: item.status === 'passed' ? null : item.details,
  }));
}

function dispositionJSON(disposition) {
  return { kind: enumValue(DISPOSITIONS, disposition.kind), reason: disposition.kind === 'accepted' ? null : disposition.reason };
}

function failureJSON(failure) {
  if (failure === null) return null;
  return {
    inputs: clone(failure.inputs),
    violations: failure.violations.map(item => ({
      phase: enumValue(PHASES, item.phase), check: checksJSON([item.check])[0],
    })),
  };
}

function guidanceJSON(guidance) {
  if (guidance === undefined || guidance === null) return null;
  return {
    algorithm: guidance.algorithm,
    config: {
      max_corpus_entries: guidance.config.maxCorpusEntries,
      max_corpus_inputs: guidance.config.maxCorpusInputs,
      max_features: guidance.config.maxFeatures,
      max_features_per_observation: guidance.config.maxFeaturesPerObservation,
    },
    feedback: {
      name: guidance.feedback.name, version: guidance.feedback.version, build: guidance.feedback.build,
    },
    unique_features: guidance.unique_features, corpus_inputs: guidance.corpus_inputs,
    corpus: guidance.corpus.map(entry => ({
      inputs: clone(entry.inputs), new_features: entry.new_features,
      discovered_case: entry.discovered_case, discovered_transition: entry.discovered_transition,
    })),
    mutation_cases: guidance.mutation_cases,
    dropped_prefixes: guidance.dropped_prefixes, evicted_prefixes: guidance.evicted_prefixes,
    feature_limit_reached: guidance.feature_limit_reached, corpus_limit_reached: guidance.corpus_limit_reached,
  };
}

function reportJSON(report) {
  return {
    termination: enumValue(FUZZ_TERMINATIONS, report.termination), seed: report.seed.toString(),
    cases: report.cases, transitions: report.transitions, skipped_checks: report.skipped_checks,
    failure: failureJSON(report.failure), guidance: guidanceJSON(report.guidance),
  };
}

function shrinkJSON(report) {
  return {
    original: failureJSON(report.original), minimized: failureJSON(report.minimized),
    attempts: report.attempts, termination: enumValue(SHRINK_TERMINATIONS, report.termination),
    replayed_transitions: report.replayed_transitions, validated_original: report.validated_original,
    history: report.history.map(attempt => {
      const operation = attempt.operation;
      let normalized;
      switch (operation.kind) {
        case 'validate_original': normalized = { kind: 'ValidateOriginal' }; break;
        case 'remove': normalized = { kind: 'Remove', start: operation.start, end: operation.end }; break;
        case 'simplify': normalized = { kind: 'Simplify', index: operation.index, alternative: operation.alternative }; break;
        default: throw new Error(`unknown shrink operation: ${operation.kind}`);
      }
      return {
        attempt: attempt.attempt, candidate_length: attempt.candidate_length,
        operation: normalized, outcome: enumValue(SHRINK_OUTCOMES, attempt.outcome),
      };
    }),
  };
}

class Events {
  constructor(control = RESET_CONTROL) {
    this.control = clone(control);
    this.cancelled = control.cancelled;
    this.case = 0;
    this.trace = [];
    this.generatorRng = [];
    this.calls = new Map();
    this.failedCheck = null;
  }

  log(...entry) { this.trace.push(clone(entry)); }

  done(callback) {
    const call = (this.calls.get(callback) ?? 0) + 1;
    this.calls.set(callback, call);
    const cancellation = this.control.cancel_after;
    if (cancellation?.callback === callback && cancellation.call === call) this.cancelled = true;
    return call;
  }
}

function encodeInteger(value) {
  const bytes = new Uint8Array(8);
  new DataView(bytes.buffer).setBigInt64(0, BigInt(safeInteger(value, 'fixture integer')), true);
  return bytes;
}

function decodeInteger(bytes) {
  requireValue(bytes instanceof Uint8Array && bytes.length === 8, 'integer requires 8 bytes');
  return safeInteger(Number(new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getBigInt64(0, true)), 'decoded integer');
}

function makeModel(api, spec, events, hash = 'value') {
  requireValue(spec.kind === 'table-walk-v1', `unsupported fixture model: ${spec.kind}`);
  requireValue(['successor', 'nonnegative'].includes(spec.enabled), `unsupported enabled mode: ${spec.enabled}`);
  requireValue(spec.rng_mod === null || spec.draws >= 1 && spec.rng_mod > 0, 'rng_mod requires positive modulus and draws >= 1');
  const requestedDisposition = spec.disposition ?? { kind: 'Accepted', reason: null };
  const dispositionKind = { Accepted: 'accepted', Rejected: 'rejected', Ignored: 'ignored' }[requestedDisposition.kind];
  requireValue(dispositionKind !== undefined && (dispositionKind === 'accepted'
    ? requestedDisposition.reason === null : typeof requestedDisposition.reason === 'string'), 'invalid fixture disposition');
  const finish = callback => {
    const call = events.done(callback);
    if (spec.fault?.callback === callback && spec.fault.call === call) throw new api.ModelError(spec.fault.message);
  };
  const batch = (phase, state) => {
    const rule = spec.checks.find(rule => rule.phase === phase
      && (rule.case === null || rule.case === events.case)
      && (rule.depth === null || rule.depth === state[0]));
    const checks = clone(rule?.checks ?? []);
    const error = rule?.error ?? null;
    if (error === null && phase !== 'Transition' && spec.fail_total !== null && state[1] >= spec.fail_total) {
      checks.push({ id: 'limit', status: 'Failed', message: 'total reached limit' });
    }
    return { checks, error };
  };
  const append = (sink, checks) => sink.extend(checks.map(item => {
    const status = { Passed: 'passed', Failed: 'failed', Skipped: 'skipped' }[item.status];
    requireValue(status !== undefined, `unknown fixture check status: ${item.status}`);
    return api.check(item.id, status, item.message ?? '');
  }));
  return {
    metadata() {
      // This event detects an unwanted metadata query during bare fuzz; record
      // and replay use fresh Events so their queries cannot pollute that trace.
      events.log('metadata');
      events.done('metadata');
      return { name: 'guided-table-walk', model_version: 1, properties_version: 1, codec_version: 1, build: 'table-walk-v1' };
    },
    initialState() {
      events.case++;
      events.log('initial_state', events.case);
      finish('initial_state');
      return [0, 0];
    },
    cloneState: state => [...state], cloneInput: input => input, cloneOutput: output => output,
    equalStates: equalValue, equalInputs: (left, right) => left === right,
    hashState: state => hash === 'constant' ? 0 : JSON.stringify(state),
    generate(state, rng) {
      const raw = Array.from({ length: spec.draws }, () => rng.nextU64());
      const values = raw.map(value => value.toString());
      const row = spec.generate[events.case - 1] ?? spec.generate.at(-1);
      // An explicit null exhausts the generator; only a missing depth falls back.
      let input = row !== undefined && state[0] < row.length ? row[state[0]] : spec.fallback;
      if (input !== null && spec.rng_mod !== null) {
        input = safeInteger(Number(1n + raw.at(-1) % inputU64(spec.rng_mod, 'rng_mod')), 'generated input');
      }
      events.log('generate', events.case, state, input, values);
      events.generatorRng.push({ case: events.case, state: [...state], values });
      finish('generate');
      return input === null ? api.NO_INPUT : input;
    },
    isEnabled(state, input) {
      const enabled = input >= 0 && (spec.enabled !== 'successor' || input === state[1] + 1);
      events.log('is_enabled', events.case, state, input, enabled);
      finish('is_enabled');
      return enabled;
    },
    step(state, input) {
      events.log('step', events.case, state, input);
      finish('step');
      const after = [safeInteger(state[0] + 1, 'depth'), safeInteger(state[1] + input, 'total')];
      return { state: after, outputs: [after[1]], disposition: api.disposition(dispositionKind, requestedDisposition.reason ?? '') };
    },
    checkState(state) {
      const output = [];
      api.withCheckSink(output, sink => this.checkStateInto(state, sink));
      return output;
    },
    checkStateInto(state, sink) {
      const phase = state[0] === 0 ? 'InitialState' : 'State';
      const { checks, error } = batch(phase, state);
      events.log('check_state', events.case, state, checks, error);
      append(sink, checks);
      events.done('check_state');
      if (error !== null) {
        events.failedCheck = { phase, message: error };
        throw new api.ModelError(error);
      }
    },
    checkTransitionInto(before, input, after, sink) {
      const { checks, error } = batch('Transition', after.state);
      events.log('check_transition', events.case, before, input, after.state, checks, error);
      append(sink, checks);
      events.done('check_transition');
      if (error !== null) {
        events.failedCheck = { phase: 'Transition', message: error };
        throw new api.ModelError(error);
      }
    },
    // Omitting simplerInputs selects the public default empty simplifier.
    encodeState(state) {
      const bytes = new Uint8Array(16);
      requireValue(safeInteger(state[0], 'depth') >= 0, 'negative depth');
      new DataView(bytes.buffer).setBigUint64(0, BigInt(state[0]), true);
      bytes.set(encodeInteger(state[1]), 8);
      return bytes;
    },
    decodeState(bytes) {
      requireValue(bytes instanceof Uint8Array && bytes.length === 16, 'state requires 16 bytes');
      const depth = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getBigUint64(0, true);
      return [safeInteger(Number(depth), 'decoded depth'), decodeInteger(bytes.subarray(8))];
    },
    encodeInput: encodeInteger, decodeInput: decodeInteger, encodeOutput: encodeInteger,
  };
}

function makeFeedback(api, spec, model, events) {
  requireValue(['constant', 'value'].includes(spec.hash), `unsupported feature hash: ${spec.hash}`);
  const identity = {
    cloneFeature: clone, equalFeatures: equalValue,
    hashFeature: key => spec.hash === 'constant' ? 0 : JSON.stringify(key),
  };
  const initialEntry = state => events.log('initial_feedback', events.case, state);
  const transitionEntry = (before, input, after) => events.log('transition_feedback', events.case, before, input, after.state, after.outputs, dispositionJSON(after.disposition));
  if (spec.kind === 'custom') {
    const emit = (keys, error, out) => {
      for (const key of keys) {
        let failure;
        let succeeded = false;
        try { out.push(clone(key)); succeeded = true; } catch (caught) { failure = caught; }
        events.log('push', key, succeeded);
        if (!succeeded && !spec.ignore_overrun) throw failure;
      }
      if (error !== null) throw new api.ModelError(error);
    };
    return {
      ...identity,
      metadata() { events.log('metadata'); events.done('metadata'); return { ...FEEDBACK_METADATA }; },
      ...(spec.initial === null ? {} : {
        initialFeatures(state, out) {
          initialEntry(state);
          try { emit(spec.initial, spec.initial_error, out); } finally { events.done('initial_feedback'); }
        },
      }),
      transitionFeatures(before, input, after, out) {
        transitionEntry(before, input, after);
        const row = spec.transitions.find(row => (row.case === null || row.case === events.case)
          && (row.depth === null || row.depth === after.state[0]));
        try { if (row !== undefined) emit(row.keys, row.error, out); } finally { events.done('transition_feedback'); }
      },
    };
  }
  requireValue(['state', 'exact'].includes(spec.kind), `unsupported feedback kind: ${spec.kind}`);
  if (spec.kind === 'state') requireValue(spec.project === 0 || spec.project === 1, 'state projection must be 0 or 1');
  const inner = spec.kind === 'exact' ? api.exactStateFeedback(model)
    : new api.StateFeedback(FEEDBACK_METADATA, state => state[spec.project], identity);
  return {
    cloneFeature: key => inner.cloneFeature(key),
    equalFeatures: (left, right) => inner.equalFeatures(left, right),
    hashFeature: key => inner.hashFeature(key),
    metadata() {
      events.log('metadata');
      try { return inner.metadata(); } finally { events.done('metadata'); }
    },
    initialFeatures(state, out) {
      initialEntry(state);
      try { inner.initialFeatures(state, out); } finally { events.done('initial_feedback'); }
    },
    transitionFeatures(before, input, after, out) {
      transitionEntry(before, input, after);
      try { inner.transitionFeatures(before, input, after, out); } finally { events.done('transition_feedback'); }
    },
  };
}

function makeObserver(events) {
  return {
    needsHistory: () => true,
    initial(state, checks) {
      events.log('observer_initial', events.case, state, checksJSON(checks));
      events.done('observer_initial');
    },
    transition(before, input, after, stateChecks, transitionChecks, history) {
      events.log('observer_transition', events.case, before, input, after.state, after.outputs,
        checksJSON(stateChecks), checksJSON(transitionChecks), history, dispositionJSON(after.disposition));
      events.done('observer_transition');
    },
    checkError(phase, checks, complete) {
      events.log('observer_check_error', events.case, enumValue(PHASES, phase), checksJSON(checks), complete);
      events.done('observer_check_error');
    },
  };
}

function normalizeError(api, error, events) {
  const text = errorText(error);
  // Only contextual spelling is adapted. Require the actual error to have the
  // known checker prefix and the exact callback message; unknown errors survive.
  if (error instanceof api.ModelError && events.failedCheck !== null) {
    const { phase, message } = events.failedCheck;
    const prefix = phase === 'Transition' ? 'transition check' : 'state check';
    if (text === `${prefix}: ${message}` || text === `${prefix}: ${prefix}: ${message}`) {
      const source = phase === 'InitialState' ? 'initial check_state'
        : phase === 'Transition' ? 'check_transition' : 'check_state';
      return `${source}: ${message}`;
    }
  }
  return text;
}

function replayWitness(api, spec, inputs, failing = false) {
  const recorder = makeModel(api, spec, new Events());
  const trace = api.record(recorder, inputs, inputs.length);
  requireValue(trace.termination === (failing ? 'property_failed' : 'completed'),
    `witness recording terminated ${trace.termination}: ${trace.error}`);
  requireValue(trace.steps.length === inputs.length, 'witness recording omitted an input');
  const states = [recorder.decodeState(trace.initialState), ...trace.steps.map(step => recorder.decodeState(step.postState))];
  const outputs = trace.steps.map(step => step.outputs.map(decodeInteger));
  const dispositions = trace.steps.map(step => dispositionJSON(step.disposition));
  requireValue(equalValue(trace.steps.map(step => recorder.decodeInput(step.input)), inputs), 'witness recording changed inputs');
  const freshReplay = () => {
    const events = new Events();
    // Replay decodes the initial checkpoint; it does not call initialState().
    events.case = 1;
    return makeModel(api, spec, events);
  };
  const requireExact = result => {
    requireValue(result.outcome === 'exact' && result.build_matches === true
      && result.steps_verified === inputs.length && result.failure_reproduced === failing,
    `witness replay failed: ${json(result)}`);
  };
  requireExact(api.replay(freshReplay(), trace));
  const checks = [];
  let observedTurns = 0;
  let observedInitial = false;
  const observed = api.replayWithObservations(freshReplay(), trace, {}, observation => {
    if (observation.kind === 'error') throw observation.error;
    if (observation.kind === 'initial') {
      requireValue(!observedInitial && equalValue(observation.state, states[0]), 'witness initial replay state mismatch');
      requireValue(json(checksJSON(observation.checks)) === json(checksJSON(trace.initialChecks)), 'witness initial check mismatch');
      checks.push({ phase: 'InitialState', checks: checksJSON(observation.checks) });
      observedInitial = true;
      return;
    }
    requireValue(observation.kind === 'turn' && observedInitial, 'unexpected replay observation');
    const actual = observation.actual;
    const index = observedTurns++;
    requireValue(actual.sequence === index + 1 && index < trace.steps.length, 'witness replay sequence mismatch');
    requireValue(equalValue(actual.before, states[index]) && equalValue(actual.transition.state, states[index + 1])
      && actual.input === inputs[index] && equalValue(actual.transition.outputs, outputs[index]), 'witness replay turn mismatch');
    requireValue(json(dispositionJSON(actual.transition.disposition)) === json(dispositions[index]), 'witness replay disposition mismatch');
    requireValue(actual.checks !== null && Number.isSafeInteger(actual.state_check_count)
      && actual.state_check_count >= 0 && actual.state_check_count <= actual.checks.length, 'missing replay check boundary');
    requireValue(json(checksJSON(actual.checks)) === json(checksJSON(trace.steps[index].checks)), 'witness turn check mismatch');
    checks.push({ phase: 'State', checks: checksJSON(actual.checks.slice(0, actual.state_check_count)) });
    checks.push({ phase: 'Transition', checks: checksJSON(actual.checks.slice(actual.state_check_count)) });
  });
  requireExact(observed);
  requireValue(observedInitial && observedTurns === inputs.length, 'missing replay observations');
  return {
    inputs: clone(inputs), states, outputs, dispositions, checks,
    termination: enumValue({ property_failed: 'PropertyFailed', completed: 'Completed' }, trace.termination),
    replay: { outcome: enumValue({ exact: 'Exact' }, observed.outcome), failure_reproduced: observed.failure_reproduced, steps_verified: observed.steps_verified },
  };
}

function runCase(api, fixture) {
  const events = new Events(fixture.control);
  const model = makeModel(api, fixture.model, events, fixture.feedback.hash);
  const config = {
    seed: inputU64(fixture.config.seed, 'seed'), cases: fixture.config.cases,
    maxSteps: fixture.config.max_steps, maxTransitions: inputU64(fixture.config.max_transitions, 'max_transitions'),
    mutationPercent: fixture.config.mutation_percent,
  };
  const guidance = {
    maxCorpusEntries: fixture.guidance.max_corpus_entries, maxCorpusInputs: fixture.guidance.max_corpus_inputs,
    maxFeatures: fixture.guidance.max_features, maxFeaturesPerObservation: fixture.guidance.max_features_per_observation,
  };
  const limits = {
    cancellation: () => events.cancelled,
    ...(fixture.control.zero_duration ? { maxDurationMs: 0 } : {}),
  };
  const observer = makeObserver(events);
  requireValue(['guided', 'ordinary'].includes(fixture.mode), `unsupported mode: ${fixture.mode}`);
  const feedback = fixture.mode === 'guided' ? makeFeedback(api, fixture.feedback, model, events) : null;
  let report = null;
  let error = null;
  try {
    report = fixture.mode === 'guided'
      ? api.fuzzGuidedObserved(model, config, guidance, feedback, observer, limits)
      : api.fuzzObserved(model, config, observer, limits);
  } catch (caught) {
    if (!(caught instanceof api.ModelError) && !(caught instanceof api.ConfigError)) throw caught;
    error = normalizeError(api, caught, events);
  }
  let minimized = null;
  const witnesses = [];
  if (report !== null) {
    for (const entry of report.guidance?.corpus ?? []) witnesses.push(replayWitness(api, fixture.model, entry.inputs));
    if (fixture.shrink_config !== null) {
      requireValue(report.failure !== null, 'shrink_config requires an actual fuzz failure');
      witnesses.push(replayWitness(api, fixture.model, report.failure.inputs, true));
      const result = api.shrink(makeModel(api, fixture.model, new Events()), report.failure,
        { maxAttempts: fixture.shrink_config.max_attempts });
      witnesses.push(replayWitness(api, fixture.model, result.minimized.inputs, true));
      minimized = shrinkJSON(result);
    }
  }
  return {
    report: report === null ? null : reportJSON(report), error,
    trace: events.trace, generator_rng: events.generatorRng,
    witness_replays: witnesses, shrink: minimized,
  };
}

function argumentsFor(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index];
    if (flag === '--help' || flag === '-h') return { help: true };
    requireValue(['--corpus', '--freeze', '--case', '--output', '--corpus-sha256', '--freeze-sha256'].includes(flag), `unknown argument: ${flag}`);
    requireValue(options[flag] === undefined, `duplicate argument: ${flag}`);
    const value = argv[++index];
    requireValue(value !== undefined && !value.startsWith('--'), `${flag} needs a value`);
    options[flag] = value;
  }
  requireValue(options['--corpus'] !== undefined, '--corpus PATH is required');
  return options;
}

async function frozenCorpus(options) {
  const corpusPath = resolve(options['--corpus']);
  const freezePath = resolve(options['--freeze'] ?? join(dirname(corpusPath), 'FREEZE.json'));
  const [corpusBytes, freezeBytes] = await Promise.all([readFile(corpusPath), readFile(freezePath)]);
  const hash = bytes => createHash('sha256').update(bytes).digest('hex');
  const corpusHash = hash(corpusBytes);
  const freezeHash = hash(freezeBytes);
  const freeze = JSON.parse(freezeBytes.toString('utf8'));
  validatePublishedSeal(freeze, AUTHORITY, corpusHash);
  for (const [flag, actual] of [['--corpus-sha256', corpusHash], ['--freeze-sha256', freezeHash]]) {
    if (options[flag] !== undefined) requireValue(options[flag] === actual, `${flag} mismatch`);
  }
  const corpus = JSON.parse(corpusBytes.toString('utf8'), (key, value) => {
    // Expected fields never reach fixtures or output production.
    if (key === 'expected' || key === 'rng_audit') return undefined;
    return value;
  });
  requireValue(corpus.schema === SCHEMA && corpus.source_commit === AUTHORITY, 'unsupported corpus schema or authority');
  requireValue(Array.isArray(corpus.cases) && corpus.cases.length > 0, 'corpus must contain cases');
  requireValue(freeze.cases === corpus.cases.length, 'FREEZE.json case count does not match corpus');
  const ids = new Set();
  const cases = corpus.cases.map(item => {
    requireValue(typeof item.id === 'string' && item.id.length > 0 && !ids.has(item.id), 'invalid or duplicate case ID');
    ids.add(item.id);
    // An explicit allowlist keeps any future explanatory/expected data out.
    const { id, mode, config, guidance, model, feedback, control, shrink_config } = item;
    requireValue(shrink_config !== undefined, `case ${id} is missing shrink_config`);
    const fixture = { id, mode, config, guidance, model, feedback, control, shrink_config };
    const validateNumbers = value => {
      if (typeof value === 'number') safeInteger(value, 'fixture numeric value');
      else if (value !== null && typeof value === 'object') Object.values(value).forEach(validateNumbers);
    };
    validateNumbers(fixture);
    return fixture;
  });
  const selected = options['--case'];
  const result = selected === undefined ? cases : cases.filter(item => item.id === selected);
  requireValue(result.length > 0, `no selected cases: ${selected}`);
  return result;
}

async function installedAPI() {
  const directory = dirname(fileURLToPath(import.meta.url));
  const packageRoot = join(directory, 'node_modules', '@jagalite', 'statelessness');
  requireValue(!(await lstat(packageRoot)).isSymbolicLink(), 'installed package must be a tarball installation, not a symlink');
  const wanted = await realpath(join(packageRoot, 'dist', 'index.js'));
  const actual = await realpath(fileURLToPath(import.meta.resolve('@jagalite/statelessness')));
  requireValue(actual === wanted && wanted.startsWith(`${await realpath(packageRoot)}/`), 'public import must resolve inside the adjacent installed package');
  const api = await import('@jagalite/statelessness');
  for (const name of ['fuzzGuidedObserved', 'fuzzObserved', 'StateFeedback', 'exactStateFeedback',
    'record', 'replay', 'replayWithObservations', 'shrink', 'check', 'withCheckSink', 'accepted', 'disposition', 'ModelError', 'ConfigError']) {
    requireValue(typeof api[name] === 'function', `installed public package is missing ${name}`);
  }
  requireValue(typeof api.NO_INPUT === 'symbol', 'installed public package is missing NO_INPUT');
  return api;
}

async function main() {
  const options = argumentsFor(process.argv.slice(2));
  if (options.help) {
    process.stdout.write('node run.mjs --corpus PATH [--case ID] [--output PATH] [--freeze PATH]\n'
      + '  [--corpus-sha256 HASH] [--freeze-sha256 HASH]\n');
    return;
  }
  // Freeze admission precedes candidate import and execution. This producer
  // performs no golden comparison; the parent qualification harness owns it.
  const fixtures = await frozenCorpus(options);
  const api = await installedAPI();
  const cases = fixtures.map(fixture => {
    try { return { id: fixture.id, actual: runCase(api, fixture) }; }
    catch (error) { throw new Error(`case ${fixture.id}: ${errorText(error)}`, { cause: error }); }
  });
  const output = `${json({ schema: 'stateless-guided-v2-results/1', engine: 'typescript', cases })}\n`;
  if (options['--output'] !== undefined) await writeFile(options['--output'], output, { flag: 'wx' });
  else process.stdout.write(output);
}

main().catch(error => {
  process.stderr.write(`native-guided-typescript: ${errorText(error)}\n`);
  process.exitCode = 2;
});
