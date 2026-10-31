// Run after `npm run build`, or from a consumer with the package installed.
// The synchronous reducer is shared by finite exploration and the async host.
import { accepted, check, enumerateStates } from '@jagalite/statelessness';

const initial = () => ({ active: 0, used: 0, completed: 0 });
const inputs = [
  { kind: 'start', id: 1 }, { kind: 'start', id: 2 }, { kind: 'cancel' },
  { kind: 'complete', id: 1 }, { kind: 'complete', id: 2 },
];
function reduce(state, input, acceptStale = false) {
  if (input.kind === 'start') {
    const bit = 1 << input.id;
    if (state.active || state.used & bit) return accepted(state);
    return accepted({ ...state, active: input.id, used: state.used | bit }, [{ kind: 'start', id: input.id }]);
  }
  if (input.kind === 'cancel') {
    if (!state.active) return accepted(state);
    return accepted({ ...state, active: 0 }, [{ kind: 'cancel', id: state.active }]);
  }
  if (input.kind === 'complete') {
    if (state.active !== input.id) {
      return accepted(acceptStale && state.active && (state.used & (1 << input.id))
        ? { ...state, completed: input.id } : state);
    }
    return accepted({ ...state, active: 0, completed: input.id });
  }
  throw new Error('unknown input');
}
function model(acceptStale = false) {
  return {
    initialState: initial,
    step: (state, input) => reduce(state, input, acceptStale),
    checkState: () => [],
    checkTransition: (before, input, transition) => {
      const stale = input.kind === 'complete' && before.active !== input.id;
      const after = transition.state;
      const unchanged = before.active === after.active && before.used === after.used
        && before.completed === after.completed && transition.outputs.length === 0;
      return [stale && !unchanged
        ? check('no-stale-completion', 'failed', 'stale completion changed state or emitted work')
        : check('no-stale-completion')];
    },
    cloneState: state => ({ ...state }), cloneInput: input => ({ ...input }), cloneOutput: output => ({ ...output }),
    inputs: () => inputs,
    equalStates: (a, b) => a.active === b.active && a.used === b.used && a.completed === b.completed,
  };
}
const fixed = enumerateStates(model());
if (fixed.termination !== 'graph_exhausted' || fixed.failure || fixed.skipped_checks !== 0) throw new Error('fixed model did not exhaust');
const faulty = enumerateStates(model(true));
if (faulty.termination !== 'failure_found' || !faulty.failure) throw new Error('stale completion fault was not detected');

// Gates represent external work. The host signals cancellation through an
// AbortController, but this simulated work deliberately ignores the signal so
// its completion can arrive after cancellation.
class Host {
  state = initial();
  tasks = new Map();
  dispatch(input) {
    const result = reduce(this.state, input);
    this.state = result.state;
    for (const effect of result.outputs) {
      if (effect.kind === 'start') {
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const controller = new AbortController();
        const task = gate.then(() => this.dispatch({ kind: 'complete', id: effect.id }));
        this.tasks.set(effect.id, { release, controller, task });
      } else {
        this.tasks.get(effect.id).controller.abort();
      }
    }
  }
}
const host = new Host();
host.dispatch({ kind: 'start', id: 1 });
host.dispatch({ kind: 'cancel' });
if (!host.tasks.get(1).controller.signal.aborted) throw new Error('cancellation not signalled');
host.dispatch({ kind: 'start', id: 2 });
host.tasks.get(1).release();
await host.tasks.get(1).task;
if (host.state.active !== 2 || host.state.completed !== 0) throw new Error('late completion changed newer job');
host.tasks.get(2).release();
await host.tasks.get(2).task;
if (host.state.active !== 0 || host.state.completed !== 2) throw new Error('newer job did not complete');
console.log(`fixed graph exhausted (${fixed.states} states); stale completion fault found; async host assertions passed`);
