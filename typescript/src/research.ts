/** Opt-in finite-fixture research. Never changes production exploration equality. */
import { type Check, type EnumerateModel, type Metadata, type Transition, ModelError, scalar, validateMetadata, disposition } from './core.js';
import { withCheckSink, requireSynchronousCheckReturn } from './checks.js';
export type PropertyPhase = 'state' | 'transition';
export interface RequiredProperty {
    readonly id: string;
    readonly phase: PropertyPhase;
}
export interface Assumptions {
    readonly established: boolean;
    readonly finiteDomain: string;
    readonly equivalence: string;
    readonly effects: string;
    readonly transitionProperties: string;
    readonly queueOrder: string;
    readonly oracleHistory: string;
    readonly visibleTiming: string;
}
export interface Proposal {
    readonly name: string;
    readonly version: number;
    readonly build: string;
    readonly assumptions: Assumptions;
    readonly requiredProperties: readonly RequiredProperty[];
}
export interface ExperimentConfig {
    readonly enabled: boolean;
    readonly maxStates: bigint;
    readonly maxEdges: bigint;
    readonly maxEquivalencePairs: bigint;
}
export const DEFAULT_EXPERIMENT_CONFIG: ExperimentConfig = Object.freeze({ enabled: false, maxStates: 256n, maxEdges: 4096n, maxEquivalencePairs: 65536n });
export type ExperimentStatus = 'Disabled' | 'AssumptionsNotEstablished' | 'Incomplete' | 'Counterexample' | 'AgreesWithinDeclaredFixture';
export interface ViolationWitness<I> {
    property: RequiredProperty;
    details: string;
    inputs: I[];
}
export interface Reachability<I> {
    states: number[];
    edges: bigint;
    violations: ViolationWitness<I>[];
    observedProperties: RequiredProperty[];
    skippedChecks: bigint;
}
export interface EquivalenceCounterexample<I> {
    leftState: number;
    rightState: number;
    leftPrefix: I[];
    rightPrefix: I[];
    reason: string;
}
export interface ExperimentReport<I> {
    model: Metadata;
    proposal: Proposal;
    config: ExperimentConfig;
    status: ExperimentStatus;
    declaredStates: number;
    materializedEdges: bigint;
    comparedPairs: bigint;
    exact: Reachability<I> | null;
    reduced: Reachability<I> | null;
    counterexamples: EquivalenceCounterexample<I>[];
    missingViolations: ViolationWitness<I>[];
    missingProperties: RequiredProperty[];
    limitation: string;
}
export interface ResearchEquality<S, I, O, K> {
    equalStates(left: S, right: S): boolean;
    equalInputs(left: I, right: I): boolean;
    equalOutputs(left: O, right: O): boolean;
    equalProjections(left: K, right: K): boolean; /** Required for mutable projection keys; primitive keys need no copy callback. */
    cloneProjection?(key: K): K;
}
export type ResearchModel<S, I, O> = Omit<EnumerateModel<S, I, O>, 'equalStates'> & {
    metadata?(): Metadata;
};
const U64 = (1n << 64n) - 1n;
function fail(message: string): never { throw new ModelError(message); }
function exact(value: unknown, name: string): bigint {
    if (typeof value !== 'bigint' || value < 0n || value > U64)
        fail(`${name} must be a u64 bigint`);
    return value;
}
function bool(value: unknown): boolean {
    if (typeof value !== 'boolean')
        fail('research equality must return boolean');
    return value;
}
function sync<T>(value: T): T {
    if (value !== null && (typeof value === 'object' || typeof value === 'function') && typeof (value as any).then === 'function') {
        if (value instanceof Promise)
            Promise.prototype.then.call(value, undefined, () => { });
        fail('research callbacks must be synchronous');
    }
    return value;
}
function message(error: unknown): string {
    try {
        return error instanceof Error ? String(error.message) : String(error);
    }
    catch {
        return 'research callback failed';
    }
}
function copyArray<T>(value: readonly T[], length?: number): T[] {
    if (!Array.isArray(value))
        fail('research declaration must be an array');
    const n = length ?? value.length;
    if (!Number.isInteger(n) || n < 0 || n > 0xffffffff)
        fail('invalid research array length');
    const out: T[] = [];
    for (let i = 0; i < n; i++) {
        if (!Object.prototype.hasOwnProperty.call(value, i))
            fail('research declaration must not be sparse');
        out.push(value[i]);
    }
    return out;
}
function blank(value: string): boolean { return /^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u.test(value); }
function proposalCopy(p: Proposal): Proposal {
    if (p === null || typeof p !== 'object' || Array.isArray(p))
        fail('invalid research proposal');
    const { name, version, build, assumptions, requiredProperties } = p;
    if (assumptions === null || typeof assumptions !== 'object' || Array.isArray(assumptions))
        fail('invalid research assumptions');
    const { established, finiteDomain, equivalence, effects, transitionProperties, queueOrder, oracleHistory, visibleTiming } = assumptions;
    for (const s of [finiteDomain, equivalence, effects, transitionProperties, queueOrder, oracleHistory, visibleTiming]) {
        scalar(s);
        if (blank(s))
            fail('every reduction assumption needs an explicit statement');
    }
    scalar(name);
    scalar(build);
    if (blank(name) || blank(build) || typeof version !== 'number' || !Number.isInteger(version) || version <= 0 || version > 0xffffffff)
        fail('reduction proposal needs a name, positive version, and build identity');
    if (typeof established !== 'boolean')
        fail('assumptions established must be boolean');
    const properties = copyArray(requiredProperties).map(p => {
        if (p === null || typeof p !== 'object' || Array.isArray(p))
            fail('invalid expected research property');
        const { id, phase } = p;
        scalar(id);
        if (blank(id) || (phase !== 'state' && phase !== 'transition'))
            fail('empty or duplicate expected research property');
        return Object.freeze({ id, phase });
    });
    for (let i = 0; i < properties.length; i++)
        if (properties.slice(0, i).some(p => sameProperty(p, properties[i])))
            fail('empty or duplicate expected research property');
    return Object.freeze({ name, version, build, assumptions: Object.freeze({ established, finiteDomain, equivalence, effects, transitionProperties, queueOrder, oracleHistory, visibleTiming }), requiredProperties: Object.freeze(properties) });
}
function sameProperty(a: RequiredProperty, b: RequiredProperty): boolean { return a.id === b.id && a.phase === b.phase; }
function textOrder(a: string, b: string): number {
    const aa = Array.from(a, c => c.codePointAt(0)!), bb = Array.from(b, c => c.codePointAt(0)!);
    for (let i = 0; i < Math.min(aa.length, bb.length); i++)
        if (aa[i] !== bb[i])
            return aa[i] < bb[i] ? -1 : 1;
    return aa.length - bb.length;
}
function propertyOrder(a: RequiredProperty, b: RequiredProperty): number { return textOrder(a.id, b.id) || (a.phase === b.phase ? 0 : a.phase === 'state' ? -1 : 1); }
interface Edge<I, O> {
    input: I;
    target: number;
    outputs: O[];
    disposition: {
        kind: string;
        reason: string;
    };
    checks: Check[];
}
interface Graph<I, O> {
    initial: number;
    checks: Check[][];
    edges: Edge<I, O>[][];
}
/** Execute every declared edge once, then compare both traversals of the same owned table. */
export function compareEquivalence<S, I, O, K>(model: ResearchModel<S, I, O>, states: readonly S[], proposal: Proposal, config: Partial<ExperimentConfig>, projection: (state: S) => K, equality: ResearchEquality<S, I, O, K>, metadata?: () => Metadata): ExperimentReport<I> {
    const p = proposalCopy(proposal);
    if (config === null || typeof config !== 'object' || Array.isArray(config))
        fail('invalid research configuration');
    const { enabled = false, maxStates = 256n, maxEdges = 4096n, maxEquivalencePairs = 65536n } = config;
    if (typeof enabled !== 'boolean')
        fail('research enabled must be boolean');
    const c = Object.freeze({ enabled, maxStates: exact(maxStates, 'maxStates'), maxEdges: exact(maxEdges, 'maxEdges'), maxEquivalencePairs: exact(maxEquivalencePairs, 'maxEquivalencePairs') });
    if (!Array.isArray(states))
        fail('research states must be an array');
    const count = states.length;
    if (!Number.isInteger(count) || count < 0 || count > 0xffffffff)
        fail('research states must be an array');
    // Capture admitted topology before metadata can replace caller array entries.
    const declared = c.enabled && p.assumptions.established && count > 0 && BigInt(count) <= c.maxStates ? copyArray(states, count) : null;
    const metadataCallback = metadata ?? model.metadata;
    if (typeof metadataCallback !== 'function')
        fail('research requires explicit model metadata');
    const report: ExperimentReport<I> = { model: validateMetadata(sync(metadataCallback.call(model))), proposal: p, config: c, status: 'Disabled', declaredStates: count, materializedEdges: 0n, comparedPairs: 0n, exact: null, reduced: null, counterexamples: [], missingViolations: [], missingProperties: [], limitation: 'No production reduction is enabled. Evidence applies only to the explicit finite fixture and its assumptions.' };
    if (!c.enabled)
        return report;
    if (!p.assumptions.established) {
        report.status = 'AssumptionsNotEstablished';
        return report;
    }
    report.status = 'Incomplete';
    if (count === 0 || BigInt(count) > c.maxStates) {
        report.limitation = 'The explicit state domain is empty or exceeds the state budget.';
        return report;
    }
    // Capture callback references before invoking application code; keep original receivers.
    if (equality === null || typeof equality !== 'object')
        fail('research requires explicit equality callbacks');
    const { equalStates, equalInputs, equalOutputs, equalProjections, cloneProjection } = equality;
    for (const f of [equalStates, equalInputs, equalOutputs, equalProjections, projection])
        if (typeof f !== 'function')
            fail('research requires explicit equality callbacks');
    if (cloneProjection !== undefined && typeof cloneProjection !== 'function')
        fail('invalid projection clone callback');
    const cloneState = model.cloneState, cloneInput = model.cloneInput, cloneOutput = model.cloneOutput;
    const initialState = model.initialState, inputs = model.inputs, step = model.step;
    const checksModel = { checkState: model.checkState, checkStateInto: model.checkStateInto, checkTransition: model.checkTransition, checkTransitionInto: model.checkTransitionInto };
    const cs = (v: S) => sync(cloneState.call(model, v)), ci = (v: I) => sync(cloneInput.call(model, v)), co = (v: O) => sync(cloneOutput.call(model, v));
    const eqs = (a: S, b: S) => bool(sync(equalStates.call(equality, cs(a), cs(b))));
    const cp = (key: K): K => {
        if (cloneProjection)
            return sync(cloneProjection.call(equality, key));
        if (key !== null && (typeof key === 'object' || typeof key === 'function'))
            fail('mutable projection keys require cloneProjection');
        return key;
    };
    const eqk = (a: K, b: K) => bool(sync(equalProjections.call(equality, cp(a), cp(b))));
    let domain: S[];
    try {
        domain = declared!.map(cs);
    }
    catch (error) {
        report.limitation = message(error);
        return report;
    }
    let duplicate = false;
    try {
        outer: for (let i = 0; i < domain.length; i++)
            for (let j = 0; j < i; j++)
                if (eqs(domain[j], domain[i])) {
                    duplicate = true;
                    break outer;
                }
    }
    catch (error) {
        report.limitation = message(error);
        return report;
    }
    if (duplicate)
        fail('declared research domain contains duplicate exact states');
    const find = (state: S) => domain.findIndex(s => eqs(s, state));
    let graph: Graph<I, O>;
    try {
        const initial = find(sync(initialState.call(model)));
        if (initial < 0)
            fail('initial state is outside the declared finite domain');
        graph = { initial, checks: [], edges: [] };
        for (const state of domain) {
            const checks: Check[] = [];
            withCheckSink(checks, sink => {
                const stateValue = cs(state), hook = checksModel.checkStateInto;
                if (hook !== undefined) {
                    if (typeof hook !== 'function')
                        fail('append check hook must be a function');
                    requireSynchronousCheckReturn(hook.call(model, stateValue, sink));
                }
                else
                    sink.extend(checksModel.checkState.call(model, stateValue));
            });
            graph.checks.push(checks);
            const outgoing: Edge<I, O>[] = [];
            const iterator = sync(inputs.call(model, cs(state)))[Symbol.iterator]();
            const nextInput = iterator.next;
            if (typeof nextInput !== 'function')
                fail('research input iterator requires next');
            try {
                for (;;) {
                    const next = sync(nextInput.call(iterator));
                    if (next.done)
                        break;
                    if (report.materializedEdges === c.maxEdges)
                        fail('edge materialization budget reached; finite graph not completed');
                    const input = ci(next.value);
                    const t = sync(step.call(model, cs(state), ci(input)));
                    report.materializedEdges++;
                    const after = cs(t.state);
                    const target = find(after);
                    if (target < 0)
                        fail('successor is outside the declared finite domain');
                    const outputs = copyArray(t.outputs).map(co), d = disposition(t.disposition.kind, t.disposition.reason);
                    const edgeChecks: Check[] = [];
                    const transition: Transition<S, O> = { state: cs(after), outputs: outputs.map(co), disposition: { ...d } };
                    withCheckSink(edgeChecks, sink => {
                        const hook = checksModel.checkTransitionInto;
                        if (hook !== undefined) {
                            if (typeof hook !== 'function')
                                fail('append check hook must be a function');
                            requireSynchronousCheckReturn(hook.call(model, cs(state), ci(input), transition, sink));
                        }
                        else if (checksModel.checkTransition !== undefined)
                            sink.extend(checksModel.checkTransition.call(model, cs(state), ci(input), transition));
                    });
                    outgoing.push({ input, target, outputs, disposition: d, checks: edgeChecks });
                }
            }
            catch (error) {
                const primary = message(error);
                try {
                    iterator.return?.();
                }
                catch { }
                throw new ModelError(primary);
            }
            graph.edges.push(outgoing);
        }
    }
    catch (error) {
        report.limitation = message(error);
        return report;
    }
    try {
        const keys = domain.map(state => cp(sync(projection(cs(state)))));
        for (const key of keys)
            if (!eqk(key, key))
                fail('projection equality must be reflexive');
        const witness = (v: ViolationWitness<I>): ViolationWitness<I> => ({ property: { ...v.property }, details: v.details, inputs: v.inputs.map(ci) });
        const reach = (equivalent: (a: number, b: number) => boolean): [
            Reachability<I>,
            (I[] | null)[]
        ] => {
            const r: Reachability<I> = { states: [graph.initial], edges: 0n, violations: [], observedProperties: [], skippedChecks: 0n };
            const paths: (I[] | null)[] = domain.map(() => null);
            paths[graph.initial] = [];
            const seen: RequiredProperty[] = [];
            const observe = (checks: Check[], phase: PropertyPhase, inputs: I[]) => {
                for (const check of checks) {
                    const property = { id: check.id, phase };
                    if (!r.observedProperties.some(p => sameProperty(p, property)))
                        r.observedProperties.push({ ...property });
                    if (check.status === 'failed' && !seen.some(p => sameProperty(p, property))) {
                        seen.push(property);
                        r.violations.push({ property, details: check.details, inputs: inputs.map(ci) });
                    }
                    else if (check.status === 'skipped')
                        r.skippedChecks = r.skippedChecks === U64 ? U64 : r.skippedChecks + 1n;
                }
            };
            observe(graph.checks[graph.initial], 'state', []);
            for (let head = 0; head < r.states.length; head++) {
                const source = r.states[head];
                for (const edge of graph.edges[source]) {
                    r.edges++;
                    const path = [...paths[source]!.map(ci), ci(edge.input)];
                    observe(graph.checks[edge.target], 'state', path);
                    observe(edge.checks, 'transition', path);
                    if (r.states.some(old => old === edge.target || equivalent(old, edge.target)))
                        continue;
                    paths[edge.target] = path;
                    r.states.push(edge.target);
                }
            }
            r.observedProperties.sort(propertyOrder);
            return [r, paths];
        };
        const [ex, paths] = reach((a, b) => a === b), [re] = reach((a, b) => eqk(keys[a], keys[b]));
        report.missingViolations = ex.violations.filter(v => !re.violations.some(r => sameProperty(v.property, r.property))).map(witness);
        report.missingProperties = p.requiredProperties.filter(v => !ex.observedProperties.some(r => sameProperty(v, r))).map(v => ({ ...v }));
        const sameChecks = (a: Check[], b: Check[]) => a.length === b.length && a.every((v, i) => v.id === b[i].id && v.status === b[i].status && v.details === b[i].details);
        const compare = (left: number, right: number): string | null => {
            if (!sameChecks(graph.checks[left], graph.checks[right]))
                return 'state checks differ';
            const a = graph.edges[left], b = graph.edges[right];
            if (a.length !== b.length)
                return 'enabled input counts differ';
            for (let i = 0; i < a.length; i++) {
                const x = a[i], y = b[i];
                if (!bool(sync(equalInputs.call(equality, ci(x.input), ci(y.input)))))
                    return 'enabled input labels or order differ';
                if (x.outputs.length !== y.outputs.length || x.outputs.some((v, j) => !bool(sync(equalOutputs.call(equality, co(v), co(y.outputs[j]))))))
                    return 'ordered effects differ';
                if (x.disposition.kind !== y.disposition.kind || x.disposition.reason !== y.disposition.reason)
                    return 'transition disposition differs';
                if (!sameChecks(x.checks, y.checks))
                    return 'transition checks differ';
                if (!eqk(keys[x.target], keys[y.target]))
                    return 'successor equivalence classes differ';
            }
            return null;
        };
        let limited = false;
        outer: for (let i = 0; i < ex.states.length; i++)
            for (let j = i + 1; j < ex.states.length; j++) {
                const left = ex.states[i], right = ex.states[j];
                if (!eqk(keys[left], keys[right]))
                    continue;
                if (report.comparedPairs === c.maxEquivalencePairs) {
                    limited = true;
                    break outer;
                }
                report.comparedPairs++;
                const reason = compare(left, right);
                if (reason)
                    report.counterexamples.push({ leftState: left, rightState: right, leftPrefix: paths[left]!.map(ci), rightPrefix: paths[right]!.map(ci), reason });
            }
        const different = report.counterexamples.length > 0 || report.missingViolations.length > 0 || ex.states.some(s => !re.states.some(r => eqk(keys[s], keys[r]))) || re.violations.some(v => !ex.violations.some(e => sameProperty(v.property, e.property)));
        if (different) {
            report.status = 'Counterexample';
            report.limitation = 'The proposed equivalence changes observable behavior or property reachability in this finite fixture; it must not be used for pruning.';
        }
        else if (limited)
            report.limitation = 'Equivalence-pair budget reached; unchecked pairs cannot be assumed equivalent.';
        else if (report.missingProperties.length || ex.skippedChecks > 0n || re.skippedChecks > 0n || !p.requiredProperties.length)
            report.limitation = 'Expected properties were absent or checks were skipped; equivalence qualification is incomplete.';
        else
            report.status = 'AgreesWithinDeclaredFixture';
        report.exact = ex;
        report.reduced = re;
    }
    catch (error) {
        report.status = 'Incomplete';
        report.limitation = message(error);
        report.exact = null;
        report.reduced = null;
        report.counterexamples = [];
        report.missingViolations = [];
        report.missingProperties = [];
    }
    return report;
}
/** Sorted owned parameters; no outcome enables production pruning. */
export function qualificationParameters<I>(report: ExperimentReport<I>): Readonly<Record<string, string>> { const p = report.proposal, a = p.assumptions; const result: Record<string, string> = { 'research.proposal': p.name, 'research.proposal_version': String(p.version), 'research.proposal_build': p.build, 'research.model_build': report.model.build, 'research.status': report.status, 'research.declared_states': String(report.declaredStates), 'research.materialized_edges': String(report.materializedEdges), 'research.compared_pairs': String(report.comparedPairs), 'research.finite_domain': a.finiteDomain, 'research.equivalence': a.equivalence, 'research.effects': a.effects, 'research.transition_properties': a.transitionProperties, 'research.queue_order': a.queueOrder, 'research.oracle_history': a.oracleHistory, 'research.visible_timing': a.visibleTiming, 'research.limitation': report.limitation, 'research.production_pruning_enabled': 'false' }; return Object.freeze(Object.fromEntries(Object.keys(result).sort().map(key => [key, result[key]]))); }
