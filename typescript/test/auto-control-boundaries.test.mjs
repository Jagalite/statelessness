import test from 'node:test';
import assert from 'node:assert/strict';
import { accepted, fuzzAuto, fuzzGuidedAuto, DEFAULT_GUIDANCE } from '../dist/index.js';

const options = { maxCandidates: 30 };
const config = { seed: 1n, cases: 2, maxSteps: 1, maxTransitions: 4n, mutationPercent: 100 };
const base = inputs => ({ initialState: () => 0, step: s => accepted(s + 1), checkState: () => [], cloneState: s => s, cloneInput: i => i, cloneOutput: o => o, equalInputs: (a, b) => a === b, inputs });
const feedback = () => ({ metadata: () => ({ name: 'test', version: 1, build: 'test' }), cloneFeature: x => x, equalFeatures: (a, b) => a === b, transitionFeatures: (_b, _i, _t, out) => { out.push('novel'); } });

test('ordinary and guided deadlines interrupt inside enumeration and close the iterator', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'performance');
  let clock = 0;
  Object.defineProperty(globalThis, 'performance', { configurable: true, value: { now: () => clock } });
  try {
    for (const guided of [false, true]) {
      clock = 0;
      let yielded = 0, closed = 0, transitions = 0;
      const m = { ...base(function* () { try { for (let i = 0; i < 10; i++) { yielded++; if (yielded === 3) clock = 10; yield i; } } finally { closed++; } }), step(s) { transitions++; return accepted(s + 1); } };
      const limits = { maxDurationMs: 10 };
      const report = guided ? fuzzGuidedAuto(m, options, config, DEFAULT_GUIDANCE, feedback(), limits) : fuzzAuto(m, options, config, limits);
      assert.equal(report.termination, 'deadline');
      assert.equal(report.transitions, 0n);
      assert.equal(transitions, 0);
      assert.equal(yielded, 3);
      assert.equal(closed, 1);
    }
  } finally {
    if (original) Object.defineProperty(globalThis, 'performance', original);
    else delete globalThis.performance;
  }
});

test('guided retained corpus membership scan stops before reusing a prefix', () => {
  let currentCase = 0, cancelled = false, closed = 0, secondCaseScans = 0, steps = 0;
  const m = { ...base(function* () { try { if (currentCase === 2) { secondCaseScans++; cancelled = true; } yield 7; } finally { closed++; } }), initialState() { currentCase++; return 0; }, step(s) { steps++; return accepted(s + 1); } };
  const report = fuzzGuidedAuto(m, options, config, DEFAULT_GUIDANCE, feedback(), { cancellation: () => cancelled });
  assert.equal(report.termination, 'cancelled');
  assert.equal(report.cases, 2);
  assert.equal(report.transitions, 1n);
  assert.equal(steps, 1);
  assert.equal(report.guidance.corpus.length, 1);
  assert.equal(report.guidance.mutation_cases, 1);
  assert.equal(secondCaseScans, 1);
  assert.equal(closed, 3);
});
