import test from 'node:test';
import assert from 'node:assert/strict';
import { accepted, check, metadata, NO_INPUT, fuzz, fuzzGuided, fuzzGuidedObserved, StateFeedback, exactStateFeedback, FeatureBuffer, DEFAULT_GUIDANCE, GUIDANCE_ALGORITHM, record, replay, shrink, ModelError } from '../dist/index.js';
import { addSkippedChecks } from '../dist/search_support.js';

const config = (overrides = {}) => ({ seed: 42n, cases: 1, maxSteps: 3, maxTransitions: 100n, mutationPercent: 0, ...overrides });
const guidance = (overrides = {}) => ({ ...DEFAULT_GUIDANCE, ...overrides });
const identity = { cloneFeature: x => structuredClone(x), equalFeatures: (a,b) => JSON.stringify(a) === JSON.stringify(b), hashFeature: () => 0 };
const fbmeta = { name: 'depth', version: 1, build: 'native-test' };
const model = (overrides = {}) => ({ initialState: () => 0, step: s => accepted(s+1, [s+1]), checkState: () => [], generate: () => 1, isEnabled: () => true,
    cloneState: s => s, cloneInput: i => i, cloneOutput: o => o, equalInputs: (a,b) => a===b, equalStates: (a,b) => a===b,
    metadata: () => metadata('guided-test','v1'), encodeState: s => Uint8Array.of(s), decodeState: b => b[0], encodeInput: i => Uint8Array.of(i), decodeInput: b => b[0], encodeOutput: o => Uint8Array.of(o), ...overrides });
const feedback = (overrides = {}) => ({ ...identity, metadata: () => ({...fbmeta}), transitionFeatures: (_b,_i,t,out) => { out.push(t.state); }, ...overrides });

test('guided fuzz retains successor and feedback observation before checks mutate aliases', () => {
 let emitted, calls=0; const before=[], features=[];
 const m=model({initialState:()=>({n:0}),cloneState:s=>({...s}),cloneInput:i=>({...i}),
  cloneOutput:o=>({...o}),generate:()=>({n:1}),
  step:(s,i)=>{before.push(s.n);emitted={n:s.n+i.n};return accepted(emitted,[{n:emitted.n}]);},
  checkState:s=>{if(s.n>0 && emitted)emitted.n=99;return [];}});
 const f=feedback({transitionFeatures:(b,i,t,out)=>{features.push([b.n,i.n,t.state.n,t.outputs[0].n]);calls++;out.push(calls);}});
 const result=fuzzGuided(m,config({maxSteps:2}),guidance(),f);
 assert.equal(result.transitions,2n);
 assert.deepEqual(before,[0,1]);assert.deepEqual(features,[[0,1,1,1],[1,1,2,2]]);
});
test('guided fuzz captures edge and feedback before history input copy hooks', () => {
 let emitted, armed=false;const checks=[],features=[];
 const m=model({initialState:()=>({n:0}),cloneState:s=>({...s}),
  cloneInput:i=>{const copy={...i};if(armed && emitted)emitted.n=99;return copy;},
  cloneOutput:o=>({...o}),generate:()=>({n:1}),
  step:(s,i)=>{emitted={n:s.n+i.n};armed=true;return accepted(emitted,[{n:1}]);},
  checkState:s=>{if(s.n)checks.push(s.n);return [];}});
 const f=feedback({transitionFeatures:(_b,_i,t,out)=>{features.push(t.state.n);out.push(1);}});
 fuzzGuided(m,config({maxSteps:1}),guidance(),f);
 assert.deepEqual(checks,[1]);assert.deepEqual(features,[1]);
});

test('defaults, default-empty initial feedback and exact-state identity are explicit', () => {
    assert.equal(GUIDANCE_ALGORITHM, 'feature-corpus-v2');
    assert.deepEqual(DEFAULT_GUIDANCE, {maxCorpusEntries:256,maxCorpusInputs:25600,maxFeatures:100000,maxFeaturesPerObservation:256});
    const plain = fuzzGuided(model(), config({maxSteps:0}), guidance(), feedback());
    assert.equal(plain.guidance.unique_features, 0);
    const exact = fuzzGuided(model(), config({maxSteps:0}), guidance(), exactStateFeedback(model()));
    assert.equal(exact.guidance.unique_features, 1);
    assert.deepEqual(exact.guidance.corpus, []);
    assert.deepEqual(exact.guidance.feedback, {name:'exact-state',version:1,build:'clone-eq-hash-v1'});
    const blank = fuzzGuided(model(), config({cases:0}), guidance(), feedback({metadata:()=>({name:'',version:0,build:''})}));
    assert.equal(blank.guidance.feedback.version,0);
});

test('guidance validates before fuzz/control/metadata and admission snapshots each field once', () => {
    let calls=0;
    const f=feedback({metadata(){calls++;return fbmeta;}});
    assert.throws(()=>fuzzGuided(model(),config({mutationPercent:101}),guidance({maxFeatures:0}),f,{maxDurationMs:-1}),/guidance limits must be positive/);
    assert.throws(()=>fuzzGuided(model(),config({mutationPercent:101}),guidance(),f,{maxDurationMs:-1}),/mutation_percent/);
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),f,{maxDurationMs:-1}),/maxDurationMs/);
    for(const value of [-1,NaN,Infinity,1.5,Number.MAX_SAFE_INTEGER+1]) assert.throws(()=>fuzzGuided(model(),config(),guidance({maxFeatures:value}),f));
    assert.equal(calls,0);
    const once = object => Object.fromEntries(Object.entries(object).map(([key,value])=>[key,value]));
    const g=once(guidance()),c=once(config());
    const meta={...fbmeta};
    const r=fuzzGuided(model({initialState(){g.maxFeatures=0;c.maxSteps=0;meta.name='mutated';return 0;}}),c,g,feedback({metadata:()=>meta}));
    assert.equal(r.transitions,3n);assert.equal(r.guidance.unique_features,3);assert.equal(r.guidance.feedback.name,'depth');
    const readOnce = obj => Object.defineProperties({},Object.fromEntries(Object.entries(obj).map(([key,value])=>{let read=false;return [key,{get(){assert.equal(read,false,key);read=true;return value;}}];})));
    assert.equal(fuzzGuided(model(),readOnce(config()),readOnce(guidance()),feedback()).transitions,3n);
});

test('metadata precedes pre-cancel/zero deadline and its duration counts', () => {
    const events=[];
    const m=model({initialState(){events.push('initial');return 0;}}),f=feedback({metadata(){events.push('metadata');return fbmeta;}});
    for(const limits of [{cancellation:()=>true},{maxDurationMs:0}]) {
        const r=fuzzGuided(m,config(),guidance(),f,limits);assert.equal(r.cases,0);assert.equal(r.guidance.unique_features,0);
    }
    assert.deepEqual(events,['metadata','metadata']);
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,'performance');let time=0;
    try {
        Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>time}});
        const r=fuzzGuided(m,config(),guidance(),feedback({metadata(){time=10;return fbmeta;}}),{maxDurationMs:5});
        assert.equal(r.termination,'deadline');assert.equal(r.cases,0);
    } finally {Object.defineProperty(globalThis,'performance',descriptor);}
});

test('feature collisions resolve equality, mutable emissions are snapshotted at each push', () => {
    const key={n:0};
    const f=feedback({transitionFeatures(_b,_i,t,out){key.n=t.state;out.push(key);key.n=999;out.push({n:t.state});}});
    const r=fuzzGuided(model(),config(),guidance(),f);
    assert.equal(r.guidance.unique_features,3);
    assert.deepEqual(r.guidance.corpus.map(c=>c.new_features),[1,1,1]);
    assert.deepEqual(r.guidance.corpus.map(c=>c.discovered_transition),[1n,2n,3n]);
    assert.deepEqual(r.guidance.corpus.map(c=>c.discovered_case),[1,1,1]);
    assert.equal(r.guidance.corpus_inputs,6);
});

test('feature equality is independent from state equality and exact state retains all fields', () => {
    const m=model({initialState:()=>({depth:0,history:[]}),cloneState:s=>({depth:s.depth,history:[...s.history]}),step:s=>accepted({depth:0,history:[...s.history,1]}),equalStates:(a,b)=>JSON.stringify(a)===JSON.stringify(b)});
    const projected=fuzzGuided(m,config(),guidance(),new StateFeedback(fbmeta,s=>s.depth,identity));
    const exact=fuzzGuided(m,config(),guidance(),exactStateFeedback(m));
    assert.equal(projected.transitions,3n);assert.equal(projected.guidance.unique_features,1);assert.equal(exact.guidance.unique_features,4);
});

test('hash/equality callbacks receive detached features, nonboolean equality and invalid hashes fail', () => {
    const f=feedback({hashFeature(k){k.n=999;return 0;},equalFeatures(a,b){const same=a.n===b.n;a.n=999;b.n=999;return same;},transitionFeatures(_b,_i,t,out){out.push({n:t.state});out.push({n:t.state});}});
    assert.equal(fuzzGuided(model(),config(),guidance(),f).guidance.unique_features,3);
    for(const hash of [NaN,Infinity,{},null]) assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({hashFeature:()=>hash})),/feature hash/);
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({equalFeatures:()=>1})),/equalFeatures must return boolean/);
});

test('per-observation emissions include duplicates, overrun is sticky, callback error wins', () => {
    const g=guidance({maxFeaturesPerObservation:1});
    const swallowed=feedback({transitionFeatures(_b,_i,_t,out){out.push('same');try{out.push('same');}catch{};try{out.push('another');}catch{}}});
    assert.throws(()=>fuzzGuided(model(),config(),g,swallowed),/^ModelError: transition feedback: feedback exceeds per-observation feature limit$/);
    const own=feedback({transitionFeatures(...args){swallowed.transitionFeatures(...args);throw new Error('own callback error');}});
    assert.throws(()=>fuzzGuided(model(),config(),g,own),/^ModelError: transition feedback: own callback error$/);
    const initial=feedback({initialFeatures(_s,out){out.push(1);try{out.push(1);}catch{}}});
    assert.throws(()=>fuzzGuided(model(),config(),g,initial),/initial feedback: feedback exceeds/);
    assert.equal(fuzzGuided(model(),config(),g,feedback()).guidance.unique_features,3);
});

test('escaped sinks expire on success/error and cannot affect later observations or runs', () => {
    const escaped=[];
    const f=feedback({transitionFeatures(_b,_i,t,out){for(const old of escaped)assert.throws(()=>old.push(999),/lease expired/);escaped.push(out);out.push(t.state);}});
    const a=fuzzGuided(model(),config(),guidance(),f);
    for(const sink of escaped)assert.throws(()=>sink.push(999),/lease expired/);
    assert.equal(a.guidance.unique_features,3);
    let bad;
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({transitionFeatures(_b,_i,_t,out){bad=out;throw null;}})),/transition feedback: null/);
    assert.throws(()=>bad.push(1),/lease expired/);
    assert.throws(()=>new FeatureBuffer(),/issued/);
    assert.equal(fuzzGuided(model(),config(),guidance(),f).guidance.unique_features,3);
});

test('reentrant cloning cannot bypass the emission cap even if the clone swallows its error', () => {
    let sink;
    const f=feedback({cloneFeature(key){try{sink.push(key);}catch{}return key;},transitionFeatures(_b,_i,_t,out){sink=out;try{out.push(1);}catch{}}});
    assert.throws(()=>fuzzGuided(model(),config(),guidance({maxFeaturesPerObservation:1}),f),/transition feedback: feature buffer push is not reentrant/);
    assert.throws(()=>sink.push(1),/lease expired/);
});

test('Promise/thenable hooks are rejected, leases expire before microtasks and rejections are contained', async () => {
    let sink;
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({async transitionFeatures(_b,_i,_t,out){sink=out;await Promise.resolve();out.push(1);}})),/transition feedback: guided callbacks must return synchronously/);
    assert.throws(()=>sink.push(1),/lease expired/);
    await new Promise(resolve=>setImmediate(resolve));
    for(const method of ['metadata','cloneFeature','hashFeature','equalFeatures']) {
        assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({[method]:()=>Promise.reject(Error('async rejected'))})),/synchronously/);
    }
    let thenCalled=false;
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({transitionFeatures:()=>({then(){thenCalled=true;}})})),/synchronously/);
    assert.equal(thenCalled,false);
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback(),{}, {initial:async()=>{throw Error('observer async');}}),/observer initial: guided callbacks/);
    assert.throws(()=>fuzzGuided(model({generate:async()=>1}),config(),guidance(),feedback()),/generate: guided callbacks/);
    await new Promise(resolve=>setImmediate(resolve));
});

test('arbitrary feedback throwables retain precedence through cleanup and return no report', () => {
    for(const [value,message] of [[undefined,'undefined'],[null,'null'],[7,'7'],[{toString(){throw Error('bad conversion');}},'<unprintable thrown value>']]) {
        let escaped;
        assert.throws(()=>fuzzGuided(model(),config(),guidance({maxFeaturesPerObservation:1}),feedback({transitionFeatures(_b,_i,_t,out){escaped=out;out.push(1);try{out.push(2);}catch{}throw value;}})),error=>error instanceof ModelError&&error.message===`transition feedback: ${message}`);
        assert.throws(()=>escaped.push(3),/lease expired/);
    }
});

test('ordinary fuzz preserves secondary formatting exceptions; feedback normalization is guided-only', () => {
    const secondary = new Error('secondary formatting exception');
    const withMessageGetter = new Error('unreadable');
    Object.defineProperty(withMessageGetter, 'message', {get(){throw secondary;}});
    const withStringFailure = {toString(){throw secondary;}};
    for (const thrown of [withMessageGetter, withStringFailure]) {
        for (const hook of ['initialState', 'generate', 'isEnabled', 'step']) {
            assert.throws(() => fuzz(model({[hook](){throw thrown;}}), config()), error => error === secondary, hook);
        }
        assert.throws(() => fuzzGuided(model(), config(), guidance(), feedback({transitionFeatures(){throw thrown;}})),
            error => error instanceof ModelError && error.message === 'transition feedback: <unprintable thrown value>');
    }
});

test('malformed Error.message coercion stays inside guided formatting and cleanup', () => {
    const secondary = new Error('hostile message coercion');
    const malformed = new Error('primary');
    Object.defineProperty(malformed, 'message', { value: { [Symbol.toPrimitive](){throw secondary;} } });
    let sink;
    assert.throws(() => fuzzGuided(model(), config(), guidance({maxFeaturesPerObservation:1}), feedback({
        transitionFeatures(_b,_i,_after,out){
            sink=out;
            out.push(1);
            try { out.push(2); } catch {}
            throw malformed;
        },
    })), error => error instanceof ModelError && error.message === 'transition feedback: <unprintable thrown value>');
    assert.throws(() => sink.push(3), /lease expired/);
    assert.throws(() => fuzz(model({generate(){throw malformed;}}), config()), error => error === secondary);
    for (const message of [7, Symbol('message')]) {
        const value = new Error('primary');
        Object.defineProperty(value, 'message', { value: message });
        assert.throws(() => fuzzGuided(model(), config(), guidance(), feedback({transitionFeatures(){throw value;}})),
            error => error instanceof ModelError && error.message === `transition feedback: ${String(message)}`);
    }
});

test('FIFO evictions preserve witnessed bits; exact capacity alone does not set saturation', () => {
    const r=fuzzGuided(model(),config({cases:2}),guidance({maxCorpusEntries:1,maxCorpusInputs:3,maxFeatures:3}),feedback());
    assert.equal(r.guidance.unique_features,3);assert.equal(r.guidance.evicted_prefixes,2n);
    assert.deepEqual(r.guidance.corpus,[{inputs:[1,1,1],new_features:1,discovered_case:1,discovered_transition:3n}]);
    assert.equal(r.guidance.feature_limit_reached,false);assert.equal(r.guidance.corpus_limit_reached,true);
    const exact=fuzzGuided(model(),config({maxSteps:1}),guidance({maxCorpusEntries:1,maxCorpusInputs:1,maxFeatures:1}),feedback());
    assert.equal(exact.guidance.feature_limit_reached,false);assert.equal(exact.guidance.corpus_limit_reached,false);
});

test('overlong discovery recovers at the feature cap with new_features zero; repeats do not redrop', () => {
    let caseNumber=0;
    const m=model({initialState(){caseNumber++;return 0;}});
    const f=feedback({transitionFeatures(_b,_i,t,out){if(caseNumber===1&&t.state>=2||caseNumber===2&&t.state===1)out.push('late');}});
    const r=fuzzGuided(m,config({cases:2}),guidance({maxCorpusInputs:1,maxFeatures:1}),f);
    assert.equal(r.guidance.unique_features,1);assert.equal(r.guidance.dropped_prefixes,1n);assert.equal(r.guidance.feature_limit_reached,false);
    assert.deepEqual(r.guidance.corpus,[{inputs:[1],new_features:0,discovered_case:2,discovered_transition:4n}]);
    assert.equal(r.guidance.corpus_inputs,1);
    const saturated=fuzzGuided(model(),config(),guidance({maxFeatures:1}),feedback());
    assert.equal(saturated.guidance.feature_limit_reached,true);assert.equal(saturated.guidance.unique_features,1);
});

test('mutable model inputs and feedback/observer arguments cannot alter retained corpus or failure', () => {
    const raw={n:1};const m=model({initialState:()=>({n:0}),cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),generate:()=>raw,
        step:(s,i)=>{raw.n=1;return accepted({n:s.n+i.n},[{n:s.n+i.n}]);},isEnabled:()=>true,equalInputs:(a,b)=>a.n===b.n});
    const f=feedback({transitionFeatures(b,i,t,out){out.push(t.state.n);b.n=999;i.n=999;t.state.n=999;t.outputs[0].n=999;}});
    const r=fuzzGuidedObserved(m,config(),guidance(),f,{transition(b,i,t,_s,_c,h){b.n=999;i.n=999;t.state.n=999;t.outputs[0].n=999;h[0].n=999;}});
    assert.equal(r.guidance.unique_features,3);
    assert.deepEqual(r.guidance.corpus.map(e=>e.inputs),[[{n:1}],[{n:1},{n:1}],[{n:1},{n:1},{n:1}]]);
    r.guidance.corpus[0].inputs[0].n=8;assert.equal(r.guidance.corpus[1].inputs[0].n,1);
});

test('checks and observer precede property failure, which suppresses feedback and wins cancellation', () => {
    const calls=[];let cancel=false;
    const m=model({checkState(s){calls.push(`state:${s}`);return s?[check('bad','failed','state failed')]:[];},checkTransition(){calls.push('transition-check');cancel=true;return[];}});
    const f=feedback({initialFeatures(){calls.push('initial-feedback');},transitionFeatures(){calls.push('transition-feedback');throw Error('must not happen');}});
    const r=fuzzGuidedObserved(m,config(),guidance(),f,{initial(){calls.push('initial-observer');},transition(){calls.push('transition-observer');}},{cancellation:()=>cancel});
    assert.deepEqual(calls,['state:0','initial-observer','initial-feedback','state:1','transition-check','transition-observer']);
    assert.equal(r.termination,'failure_found');assert.equal(r.guidance.unique_features,0);assert.deepEqual(r.failure.inputs,[1]);
});

test('partial checker errors stay phase-ordered, observer secondary failures preserve primary', () => {
    const batches=[];
    const m=model({checkState:s=>s?[check('failed','failed','bad')]:[],checkTransitionInto(_b,_i,_t,out){out.push(check('partial'));throw Error('primary checker');}});
    const f=feedback({transitionFeatures(){assert.fail('feedback after checker error');}});
    assert.throws(()=>fuzzGuidedObserved(m,config(),guidance(),f,{checkError(phase,checks,complete){batches.push([phase,checks.map(c=>c.id),complete]);throw Error('secondary');}}),/primary checker; observer check_error: secondary; observer check_error: secondary/);
    assert.deepEqual(batches,[['state',['failed'],true],['transition',['partial'],false]]);
});

test('successful feedback learns before cancellation; errors beat cancellation', () => {
    let cancel=false;
    const f=feedback({transitionFeatures(_b,_i,t,out){out.push(t.state);cancel=true;}});
    const r=fuzzGuided(model(),config(),guidance(),f,{cancellation:()=>cancel});
    assert.equal(r.termination,'cancelled');assert.equal(r.transitions,1n);assert.equal(r.guidance.corpus.length,1);
    cancel=false;
    assert.throws(()=>fuzzGuided(model(),config(),guidance(),feedback({transitionFeatures(){cancel=true;throw Error('feedback wins');}}),{cancellation:()=>cancel}),/feedback wins/);
});

test('zero cases/steps/transition bounds retain setup ordering and final-step semantics', () => {
    const f=new StateFeedback(fbmeta,s=>s,identity);
    assert.equal(fuzzGuided(model(),config({cases:0}),guidance(),f).guidance.unique_features,0);
    const zero=fuzzGuided(model(),config({cases:2,maxSteps:0,maxTransitions:0n}),guidance(),f);
    assert.equal(zero.cases,2);assert.equal(zero.termination,'cases_completed');assert.equal(zero.guidance.unique_features,1);
    assert.equal(fuzzGuided(model(),config({maxTransitions:0n}),guidance(),f).cases,1);
    assert.equal(fuzzGuided(model(),config({maxSteps:1,maxTransitions:1n}),guidance(),f).termination,'cases_completed');
    const capped=fuzzGuided(model(),config({cases:2,maxSteps:1,maxTransitions:1n,mutationPercent:100}),guidance(),f);
    assert.equal(capped.cases,2);assert.equal(capped.guidance.mutation_cases,1);assert.equal(capped.termination,'transition_limit');
});

test('enabled-generation error/control ordering and exhaustion remain shared with ordinary fuzz', () => {
    for(const guided of [false,true]) {
        const run=(m,limits)=>guided?fuzzGuided(m,config(),guidance(),feedback(),limits):fuzz(m,config(),limits);
        let cancelled=false;
        const r=run(model({isEnabled(){cancelled=true;return false;}}),{cancellation:()=>cancelled});
        assert.equal(r.termination,'cancelled');assert.equal(r.transitions,0n);
        assert.equal(run(model({generate:()=>NO_INPUT}),{}).termination,'cases_completed');
        assert.throws(()=>run(model({isEnabled(){throw Error('enabled fault');}}),{}),/enabled fault/);
    }
});

test('retained passing witnesses replay and generated property failures shrink/replay through public APIs', () => {
    const m=model();const r=fuzzGuided(m,config(),guidance(),feedback());
    for(const entry of r.guidance.corpus)assert.equal(replay(m,record(m,entry.inputs)).outcome,'exact');
    const bad=model({checkState:s=>s>=3?[check('limit','failed','too deep')]:[]});
    const failure=fuzzGuided(bad,config(),guidance(),feedback());
    assert.equal(failure.guidance.unique_features,2);
    const smaller=shrink(bad,failure.failure);
    assert.equal(smaller.validated_original,true);
    const replayed=replay(bad,record(bad,smaller.minimized.inputs));
    assert.equal(replayed.outcome,'exact');assert.equal(replayed.failure_reproduced,true);
});

test('skipped-check accounting remains exact and saturating', () => {
    const r=fuzzGuided(model({checkState:()=>[check('skip','skipped','fixture')],checkTransition:()=>[check('edge','skipped','fixture')]}),config(),guidance(),feedback());
    assert.equal(r.skipped_checks,7n);
    assert.equal(addSkippedChecks((1n<<64n)-2n,10n),(1n<<64n)-1n);
});
