import {compareEquivalence, qualificationParameters, type Proposal, type ExperimentConfig,
    type ResearchModel, type ExperimentReport, type Metadata, type Check, type Transition} from '@jagalite/statelessness';
// Node is the declared application host; no ambient Node type dependency is needed.
// @ts-ignore Node runtime builtin.
import {readFileSync, writeFileSync} from 'node:fs';
declare const process: {argv: string[]};

type Json = any;
function merged(base: Json, override: Json): Json {
    const result = {...base};
    for (const [key, value] of Object.entries(override))
        result[key] = value !== null && typeof value === 'object' && !Array.isArray(value)
            ? merged(result[key], value) : value;
    return result;
}
const camel = (key: string) => key.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
function native(value: Json): Json {
    if (Array.isArray(value)) return value.map(native);
    if (value !== null && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).map(([key, val]) => [camel(key), native(val)]));
    return value;
}
function normalized(value: Json): Json {
    if (typeof value === 'bigint') return value.toString();
    if (Array.isArray(value)) return value.map(normalized);
    if (value !== null && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).map(([key, val]) =>
            [key.replace(/[A-Z]/g, letter => '_' + letter.toLowerCase()), normalized(val)]));
    return value;
}

function runCase(defaults: Json, row: Json): Json {
    const log: string[] = [];
    const event = (name: string): void => {
        log.push(name);
        if (name === row.error_at) throw Error('fixture error at ' + name);
    };
    try {
        const scenario = row.scenario;
        const states = new Map<number, Json>(scenario.rows.map((r: Json) => [r.state, r]));
        const edge = (state: number, label: string): Json => states.get(state).inputs.find((e: Json) => e.input === label);
        const checks = (values: Json[]): Check[] => values.map(c => ({id: c.id, status: c.status, details: c.details ?? ''}));
        const model: ResearchModel<number, string, string> = {
            metadata: () => { event('metadata'); return defaults.metadata as Metadata; },
            initialState: () => { event('initial'); return scenario.initial; },
            cloneState: state => state, cloneInput: input => input, cloneOutput: output => output,
            checkState: state => { event(`state:${state}`); return checks(states.get(state).checks); },
            inputs: state => {
                event(`inputs:${state}`);
                const edges = states.get(state).inputs; let index = 0;
                return { [Symbol.iterator]() { return this; }, next(): IteratorResult<string> {
                    event(`next:${state}:${index}`);
                    if (index === edges.length) return {done: true, value: undefined};
                    return {done: false, value: edges[index++].input};
                }};
            },
            step: (state, label) => {
                event(`step:${state}:${label}`); const e = edge(state, label);
                return {state: e.target, outputs: e.outputs, disposition: {kind: e.disposition.kind, reason: e.disposition.reason ?? ''}} as Transition<number, string>;
            },
            checkTransition: (state, label) => { event(`transition:${state}:${label}`); return checks(edge(state, label).checks); },
        };
        const proposal = native(merged(defaults.proposal, row.proposal_overrides ?? {})) as Proposal;
        const c = native(merged(defaults.config, row.config_overrides ?? {}));
        const config: ExperimentConfig = {enabled: c.enabled, maxStates: BigInt(c.maxStates),
            maxEdges: BigInt(c.maxEdges), maxEquivalencePairs: BigInt(c.maxEquivalencePairs)};
        const equality = {equalStates: (a: number, b: number) => a === b,
            equalInputs: (a: string, b: string) => a === b, equalOutputs: (a: string, b: string) => a === b,
            equalProjections: (a: string, b: string) => a === b};
        const report: ExperimentReport<string> = compareEquivalence(model, scenario.states, proposal, config,
            (state: number) => { event(`projection:${state}`); return scenario.keys[scenario.states.indexOf(state)] as string; }, equality);
        return {case: row.case, error: null, report: {...normalized(report), declared_states: String(report.declaredStates)}, callback_log: log,
            qualification_parameters: qualificationParameters(report)};
    } catch (error) {
        return {case: row.case, error: error instanceof Error ? error.message : String(error), report: null,
            callback_log: log, qualification_parameters: null};
    }
}

if (process.argv.length !== 5 || process.argv[2] !== 'run') throw Error('usage: consumer run CORPUS OUTPUT');
const fixture = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const results = fixture.cases.map((row: Json) => runCase(fixture.defaults, row));
writeFileSync(process.argv[4], JSON.stringify({profile: 'research-equivalence-v1', results}) + '\n');
console.log(JSON.stringify({producer: 'typescript', mode: 'run', case_count: results.length, production_pruning_enabled: false}));
