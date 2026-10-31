import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { WithOracle, Paired, ObservationError, ModelError, metadata, accepted, disposition, check,
  enumerateStates, checkObserved, record, replay, parseTrace, stringifyTrace, DEFAULT_TRACE_LIMITS } from '../dist/index.js';

class Queue {
  constructor(mutation = 'none') { this.mutation = mutation; }
  metadata() { return metadata(`paired-queue-actual:${this.mutation}`, 'paired-fixture-v1'); }
  initialState() { return this.mutation === 'initial' ? [1] : []; }
  inputs() { return [0, 1, 2]; }
  cloneState(state) { return [...state]; }
  cloneInput(input) { return input; }
  cloneOutput(output) { return output; }
  equalStates(a, b) { return a.length === b.length && a.every((x, n) => x === b[n]); }
  step(state, input) {
    if (![0, 1, 2].includes(input)) throw new ModelError('invalid queue input');
    if (input < 2) {
      if (state.length === 2) return { state, outputs: [], disposition: disposition('rejected', 'full') };
      const next = [...state, this.mutation === 'state' ? 1 - input : input];
      let outputs = [String(10 + input), String(20 + next.length)];
      if (this.mutation === 'order') outputs.reverse();
      if (this.mutation === 'omit') outputs = outputs.slice(0, 1);
      return accepted(next, outputs);
    }
    return state.length ? accepted(state.slice(1), [String(30 + state[0]), String(19 + state.length)]) :
      { state, outputs: [], disposition: disposition('ignored', this.mutation === 'reason' ? 'empty!' : 'empty') };
  }
  checkState(state) { return [state.length <= 2 ? check('queue.bound') : check('queue.bound', 'failed', 'overfull')]; }
  checkTransition() { return [check('queue.transition')]; }
  encodeState(state) { const b = new Uint8Array(4 + state.length); new DataView(b.buffer).setUint32(0, state.length, true); b.set(state, 4); return b; }
  decodeState(bytes) {
    if (bytes.length < 4 || bytes.length > 6 || new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true) !== bytes.length - 4 || bytes.slice(4).some(x => x > 1)) throw new ModelError('invalid queue checkpoint');
    return Array.from(bytes.slice(4));
  }
  encodeInput(i) { return Uint8Array.of(i); }
  decodeInput(b) { if (b.length !== 1 || b[0] > 2) throw new ModelError('invalid input'); return b[0]; }
  encodeOutput(o) { return new TextEncoder().encode(o); }
}
class Reference {
  metadata() { return metadata('paired-queue-reference', 'paired-fixture-v1'); }
  initialState() { return 0; }
  cloneState(s) { return s; }
  cloneInput(i) { return i; }
  cloneOutput(o) { return o; }
  equalStates(a, b) { return a === b; }
  step(packed, operation) {
    const size = Number(packed !== 0) + Number(packed >= 3);
    if (operation === 2) return size ? accepted(Math.floor(packed / 3), [29 + packed % 3, 19 + size]) : { state: 0, outputs: [], disposition: disposition('ignored', 'empty') };
    if (![0, 1].includes(operation)) throw new ModelError('invalid reference input');
    return size === 2 ? { state: packed, outputs: [], disposition: disposition('rejected', 'full') } : accepted(packed + (operation + 1) * 3 ** size, [10 + operation, 21 + size]);
  }
  checkState(s) { return [[0, 1, 2, 4, 5, 7, 8].includes(s) ? check('reference.bound') : check('reference.bound', 'failed', 'invalid digits')]; }
  checkTransition() { return [check('reference.transition')]; }
  encodeState(s) { return Uint8Array.of(s); }
  decodeState(b) { if (b.length !== 1 || ![0, 1, 2, 4, 5, 7, 8].includes(b[0])) throw new ModelError('invalid reference checkpoint'); return b[0]; }
}
class Projection {
  metadata() { return metadata('paired-queue-projection', 'paired-fixture-v1'); }
  actualState(s) { return s; }
  referenceState(s) { const values = []; while (s) { values.push(s % 3 - 1); s = Math.floor(s / 3); } return values; }
  actualEffect(o) { return Number(o); }
  referenceEffect(o) { return o; }
  equalState(a, b) { return a.length === b.length && a.every((x, n) => x === b[n]); }
  equalEffect(a, b) { return a === b; }
}
const paired = (mutation = 'none') => new WithOracle(new Queue(mutation), new Paired(new Reference(), new Projection()));
test('WithOracle enabled query is captured before state clone callbacks',()=>{
 const input={value:1};
 const model={generate(){return [];}, cloneInput(i){return {...i};},
  cloneState(s){input.value=9; this.isEnabled=()=>false; return {...s};},
  isEnabled(_s,i){return i.value===1;}};
 const wrapped=new WithOracle(model,{});
 assert.equal(wrapped.isEnabled({model:{},oracle:{}},input),true);
 const state={model:{value:1},oracle:{}};
 const replacing={generate(){return [];},cloneInput(i){state.model={value:9}; return i;},
  cloneState(s){return {...s};},isEnabled(s){return s.value===1;}};
 assert.equal(new WithOracle(replacing,{}).isEnabled(state,0),true);
 const noHook={generate(){return [];},cloneInput(){throw Error('unused');},cloneState(){throw Error('unused');}};
 assert.equal(new WithOracle(noHook,{}).isEnabled({model:{},oracle:{}},input),true);
});
test('WithOracle state encoding captures history before model codec and preserves error precedence',()=>{
 const state={model:0,oracle:{value:7}}, modelFailure=new Error('model codec'), cloneFailure=new Error('history copy');
 const model={cloneState:s=>s,encodeState:()=>{state.oracle.value=90;return Uint8Array.of(3);}};
 const oracle={cloneHistory:h=>({...h}),encodeHistory:h=>Uint8Array.of(h.value)};
 const wrapper=new WithOracle(model,oracle);
 const bytes=wrapper.encodeState(state);
 assert.equal(bytes.at(-1),7);
 oracle.cloneHistory=()=>{throw cloneFailure;};
 model.encodeState=()=>{throw modelFailure;};
 assert.throws(()=>wrapper.encodeState(state),/model codec/);
 model.encodeState=()=>Uint8Array.of(3);
 assert.throws(()=>wrapper.encodeState(state),/history copy/);
});
test('WithOracle state encoding pins codec adapters and model reference',()=>{
 const original={value:3},state={model:original,oracle:{value:7}};let wrapper;
 const model={cloneState(s){assert.equal(this,model);assert.equal(s,original);return {...s};},
  encodeState(s){assert.equal(this,model);oracle.encodeHistory=()=>Uint8Array.of(99);return Uint8Array.of(s.value);}};
 const oracle={cloneHistory(h){wrapper.model={};wrapper.oracle={};state.model={value:88};return {...h};},
  encodeHistory(h){assert.equal(this,oracle);return Uint8Array.of(h.value);}};
 wrapper=new WithOracle(model,oracle);
 const bytes=wrapper.encodeState(state);
 assert.equal(bytes[12],3);assert.equal(bytes.at(-1),7);
});
test('WithOracle estimates retained history before model callbacks with unknown and error precedence',()=>{
 const state={model:0,oracle:{value:7}}, cloneFailure=new Error('history clone'), modelFailure=new Error('model estimate');
 const model={cloneState:s=>{state.oracle.value=90;return s;},estimatedStateBytes:()=>5n};
 const oracle={cloneHistory:h=>({...h}),estimatedStateBytes:h=>BigInt(h.value)};
 assert.equal(new WithOracle(model,oracle,0n).estimatedStateBytes(state),12n);
 let calls=0;oracle.cloneHistory=()=>{calls++;throw cloneFailure;};
 model.estimatedStateBytes=()=>null;
 assert.equal(new WithOracle(model,oracle,0n).estimatedStateBytes(state),null);
 assert.equal(calls,1);
 model.estimatedStateBytes=()=>{throw modelFailure;};
 assert.throws(()=>new WithOracle(model,oracle,0n).estimatedStateBytes(state),e=>e===modelFailure);
 model.estimatedStateBytes=()=>5n;
 assert.throws(()=>new WithOracle(model,oracle,0n).estimatedStateBytes(state),e=>e===cloneFailure);
});
test('WithOracle estimates pin callbacks across model callback mutations',()=>{
 const state={model:0,oracle:{value:7}}, model={cloneState:s=>s}, oracle={cloneHistory:h=>({...h})};
 oracle.estimatedStateBytes=function(h){assert.equal(this,oracle);return BigInt(h.value);};
 model.estimatedStateBytes=function(){assert.equal(this,model);oracle.estimatedStateBytes=undefined;return 5n;};
 assert.equal(new WithOracle(model,oracle,0n).estimatedStateBytes(state),12n);
 model.estimatedStateBytes=()=>{oracle.estimatedStateBytes=()=>100n;return 5n;};
 assert.equal(new WithOracle(model,oracle,0n).estimatedStateBytes(state),null);
});
test('WithOracle estimates retain callback owners if clone replaces adapters',()=>{
 const state={model:0,oracle:{value:7}};let wrapper;
 const model={cloneState:s=>s,estimatedStateBytes(){assert.equal(this,model);return 5n;}};
 const oracle={cloneHistory(h){wrapper.model={};wrapper.oracle={};return {...h};},
  estimatedStateBytes(h){assert.equal(this,oracle);return BigInt(h.value);}};
 wrapper=new WithOracle(model,oracle,0n);
 assert.equal(wrapper.estimatedStateBytes(state),12n);
});
test('WithOracle step captures input and history before reducer and state cloning',()=>{
 const before={model:{value:1},oracle:{value:2}},input={value:3};
 const model={cloneInput:i=>({...i}),cloneOutput:o=>o,
  cloneState:s=>{input.value=90;return {...s};},
  step:(state,i)=>{before.oracle.value=91;input.value=92;return accepted(state,[i.value]);}};
 let seen;
 const oracle={cloneHistory:h=>({...h}),advance:(history,i,outputs)=>{seen={history,input:i,outputs};return history;}};
 const result=new WithOracle(model,oracle).step(before,input);
 assert.deepEqual(seen,{history:{value:2},input:{value:3},outputs:[3]});
 assert.deepEqual(result.state.oracle,{value:2});
});
test('WithOracle advancement receives retained transition and input despite history callback mutation',()=>{
 const model=paired(),before=model.initialState(),input={value:1},actual=accepted([0],['original']);
 model.model.cloneInput=i=>({...i});
 model.oracle.cloneHistory=h=>{input.value=9;actual.outputs[0]='changed';actual.disposition.kind='ignored';actual.disposition.reason='changed';return h;};
 let seen;
 model.oracle.advance=(history,ownedInput,outputs,outcome)=>{seen={history,ownedInput,outputs,outcome};return history;};
 const result=model.observeTransition(before,input,actual);
 assert.deepEqual(seen,{history:0,ownedInput:{value:1},outputs:['original'],outcome:{kind:'accepted',reason:''}});
 assert.deepEqual(result.outputs,['original']);assert.deepEqual(result.disposition,{kind:'accepted',reason:''});
});
test('WithOracle owns initial and decoded model state before oracle callbacks',()=>{
 const model=paired(),borrowed=[0];
 model.model.initialState=()=>borrowed;
 model.oracle.initialState=()=>{borrowed.push(1);return 0;};
 assert.deepEqual(model.initialState(),{model:[0],oracle:0});
 borrowed.length=1;
 const bytes=model.encodeState({model:[0],oracle:0});
 model.model.decodeState=()=>borrowed;
 model.oracle.decodeHistory=()=>{borrowed.push(1);return 0;};
 assert.deepEqual(model.decodeState(bytes),{model:[0],oracle:0});
});
test('equal actual state retains independently differing reference history', () => {
  class Actual {
    metadata() { return metadata('constant-actual'); }
    initialState() { return 0; }
    inputs() { return [0, 1]; }
    cloneState(s) { return s; }
    cloneInput(i) { return i; }
    cloneOutput(o) { return o; }
    equalStates(a, b) { return a === b; }
    step() { return accepted(0); }
    checkState() { return []; }
  }
  class HiddenReference extends Actual {
    metadata() { return metadata('hidden-reference'); }
    initialState() { return false; }
    step(state, input) { return accepted(input === 0 ? !state : state, state && input === 1 ? [7] : []); }
  }
  const projection = { metadata: () => metadata('hidden-projection'),
    actualState: () => 0, referenceState: () => 0,
    actualEffect: e => e, referenceEffect: e => e,
    equalState: (a, b) => a === b, equalEffect: (a, b) => a === b };
  const model = new WithOracle(new Actual(), new Paired(new HiddenReference(), projection));
  const report = enumerateStates(model);
  assert.deepEqual([report.termination, report.states, report.transitions, report.skipped_checks], ['failure_found', 2, 4, 0]);
  assert.deepEqual(report.failure.inputs, [0, 1]);
  assert.deepEqual(report.failure.violations.map(v => [v.phase, v.check.id]), [['transition', 'pair.effects']]);
  const bounded = enumerateStates(model, { maxStates: 10, maxTransitions: 3, maxDepth: 10 });
  assert.deepEqual([bounded.termination, bounded.states, bounded.transitions], ['transition_limit', 2, 3]);
});
const pairedFixture = readFileSync(new URL('../../conformance/paired-v1.tsv', import.meta.url), 'utf8');
const parseRows = text => text.trim().split(/\r?\n/).map(line => line.split('\t'));
test('paired TSV preserves final fields under LF and CRLF', () => {
  const lf = pairedFixture.replace(/\r\n/g, '\n');
  assert.deepEqual(parseRows(lf.replace(/\n/g, '\r\n')), parseRows(lf));
});
const rows = parseRows(pairedFixture);
for (const row of rows.slice(1)) test(`paired-v1 independent golden: ${row[0]}`, () => {
  const [name, mutation, states, edges, depth, termination, expectedStates, expectedEdges, failed, witness, phases, skipped] = row;
  const result = enumerateStates(paired(mutation), { maxStates: Number(states), maxTransitions: Number(edges), maxDepth: Number(depth) });
  assert.equal(result.termination, termination, name);
  assert.equal(result.states, Number(expectedStates)); assert.equal(result.transitions, Number(expectedEdges));
  assert.deepEqual(result.failure?.violations.map(v => v.check.id) ?? [], failed === '-' ? [] : failed.split(','));
  assert.deepEqual(result.failure?.inputs ?? [], witness === '-' ? [] : witness.split(',').map(Number));
  assert.deepEqual(result.failure?.violations.map(v => v.phase) ?? [], phases === '-' ? [] : phases.split(','));
  assert.equal(result.skipped_checks, Number(skipped));
  if (result.failure) {
    const trace = record(paired(mutation), result.failure.inputs);
    const batch = trace.steps.at(-1)?.checks ?? trace.initialChecks;
    const ids = ['queue.bound', 'reference.bound', 'pair.state',
      ...(trace.steps.length ? ['queue.transition', 'reference.transition', 'pair.effects', 'pair.outcome'] : [])];
    assert.deepEqual(batch.map(c => [c.id, c.status]), ids.map(id => [id, failed.split(',').includes(id) ? 'failed' : 'passed']));
  }
});
test('paired ordering, codec restart, fail-fast and exact replay', () => {
  const model = paired(), before = model.initialState(), transition = model.step(before, 1);
  assert.deepEqual(checkObserved(model, before, 1, transition, 1).map(c => c.id), ['queue.bound', 'reference.bound', 'pair.state', 'queue.transition', 'reference.transition', 'pair.effects', 'pair.outcome']);
  assert.deepEqual(model.step(model.decodeState(model.encodeState(transition.state)), 2), model.step(transition.state, 2));
  assert.equal(Buffer.from(model.encodeState(before)).toString('hex'), '4f524331040000000000000000000000010000000000000000');
  const faulty = paired('order'), trace = record(faulty, [0, 1, 2]);
  assert.equal(trace.steps.length, 1); assert.equal(trace.termination, 'property_failed');
  const result = replay(faulty, parseTrace(stringifyTrace(trace)));
  assert.equal(result.outcome, 'exact'); assert.equal(result.failure_reproduced, true);
  assert.equal(replay(model, trace, true).outcome, 'incompatible');
});
test('paired observation retains executed transition and never reruns actual', () => {
  const model = paired(), before = model.initialState(), actual = accepted([0], ['10', '21']);
  model.model.step = () => { throw new Error('must not run'); };
  assert.equal(model.observeTransition(before, 0, actual).state.oracle, 1);
  model.oracle.advance = () => { throw new Error('history failed'); };
  assert.throws(() => model.observeTransition(before, 0, actual), e => e instanceof ObservationError && JSON.stringify(e.transition) === JSON.stringify(actual));
});
test('oracle observation captures input and history before transition clone hooks', () => {
  const model = paired(), before = {model:[0], oracle:[7]}, query = {value:3};
  const actual = accepted([1], ['x']); let seen;
  model.model.cloneInput = value => ({...value});
  model.oracle.cloneHistory = history => [...history];
  model.model.cloneState = state => { query.value=99; before.oracle[0]=88; return [...state]; };
  model.oracle.advance = (history, input) => { seen=[history[0],input.value]; return history; };
  model.observeTransition(before,query,actual);
  assert.deepEqual(seen,[7,3]);
  model.model.cloneInput = () => { throw new Error('input copy'); };
  assert.throws(() => model.observeTransition(before,query,actual), e => e instanceof ObservationError && e.transition !== actual && e.transition.state !== actual.state && e.cause.message === 'input copy');
});
test('oracle observation pins owners before copy callbacks', () => {
  const wrapper=paired(), originalModel=wrapper.model, originalOracle=wrapper.oracle;
  const before={model:[0],oracle:1}, actual=accepted([2],['a']);
  originalOracle.advance = history => history+1;
  originalModel.cloneInput = value => { wrapper.model={}; wrapper.oracle={}; return value; };
  const result=wrapper.observeTransition(before,0,actual);
  assert.deepEqual(result.state.model,[2]); assert.equal(result.state.oracle,2);
  wrapper.model=originalModel; wrapper.oracle=originalOracle;
});
test('oracle observation does not consume an unused output clone failure', () => {
  const wrapper=paired();
  Object.defineProperty(wrapper.model,'cloneOutput',{configurable:true,get(){throw Error('unused output clone');}});
  wrapper.oracle.advance = history => history+1;
  assert.equal(wrapper.observeTransition({model:[0],oracle:1},0,accepted([2],[])).state.oracle,2);
});
test('oracle advancement owns the executed transition and survives hostile thrown values',()=>{
 const model=paired(),before=model.initialState(),actual=accepted([0],['10','21']);
 model.oracle.advance=()=>{actual.state.push(9);actual.outputs.push('changed');return 1;};
 const lifted=model.observeTransition(before,0,actual);assert.deepEqual(lifted.state.model,[0]);assert.deepEqual(lifted.outputs,['10','21']);
 const hostile={toString(){throw Error('must not format');}};const next=accepted([1],['11']);
 model.oracle.advance=()=>{throw hostile;};
 assert.throws(()=>model.observeTransition(before,1,next),error=>error instanceof ObservationError&&error.cause===hostile&&error.message==='oracle advancement failed'&&JSON.stringify(error.transition)===JSON.stringify(next));
});
test('paired equality callbacks receive snapshots and cannot turn non-booleans into passes',()=>{
 const model=paired(),a={model:[0],oracle:[1]},b={model:[0],oracle:[1]};
 model.model.equalStates=(left,right)=>{left.push(3);right.push(4);return true;};
 model.oracle.cloneHistory=h=>[...h];model.oracle.equalHistory=(left,right)=>{left.push(5);right.push(6);return true;};
 assert.equal(model.equalStates(a,b),true);assert.deepEqual(a,{model:[0],oracle:[1]});assert.deepEqual(b,{model:[0],oracle:[1]});
 model.model.equalStates=()=>1;assert.throws(()=>model.equalStates(a,b),/model equality must return boolean/);
 model.model.equalStates=()=>true;model.oracle.equalHistory=()=>Promise.resolve(true);assert.throws(()=>model.equalStates(a,b),/oracle equality must return boolean/);
 const checked=paired(),before=checked.initialState(),transition=checked.step(before,0);
 checked.oracle.projection.equalState=()=>Promise.resolve(true);assert.throws(()=>Array.from(checked.oracle.checkState(transition.state.oracle,transition.state.model)),/projected state equality must return boolean/);
 checked.oracle.projection.equalEffect=()=>1;assert.throws(()=>Array.from(checked.oracle.checkTransition(before,0,transition)),/projected effect equality must return boolean/);
});
test('oracle equality captures histories before model equality callbacks',()=>{
 const model=paired(),a={model:[0],oracle:[1]},b={model:[0],oracle:[1]};
 model.oracle.cloneHistory=h=>[...h];
 model.oracle.equalHistory=(left,right)=>left[0]===right[0];
 model.model.equalStates=()=>{a.oracle[0]=8;return true;};
 assert.equal(model.equalStates(a,b),true);
 let historyCompared=false;
 model.model.equalStates=()=>false;
 model.oracle.equalHistory=()=>{historyCompared=true;return true;};
 assert.equal(model.equalStates(a,b),false);
 assert.equal(historyCompared,false);
});
test('paired malformed checkpoint, strict identity, limits and callback error', () => {
  const model = paired(), bytes = model.encodeState(model.initialState());
  for (const b of [new Uint8Array(), bytes.slice(0, -1), Uint8Array.from([...bytes, 0]), Uint8Array.from([79, 82, 67, 49, ...Array(8).fill(255)])]) assert.throws(() => model.decodeState(b), ModelError);
  const trace = record(model, [0]);
  model.oracle.projection.metadata = () => ({ ...metadata('paired-queue-projection', 'paired-fixture-v1'), properties_version: 2 });
  assert.equal(replay(model, trace, true).outcome, 'incompatible');
  assert.throws(() => record(paired(), [], 10, { ...DEFAULT_TRACE_LIMITS, maxBlobBytes: 4 }), ModelError);
  const bounded = record(paired(), [0, 1], 1); assert.equal(bounded.termination, 'step_limit');
  model.oracle.projection.actualEffect = () => { throw new Error('projection failed'); };
  assert.throws(() => enumerateStates(model), /projection failed/);
});
test('paired rejects malformed reference transitions before history or comparisons', () => {
  for (const invalid of [{ kind: 'invalid', reason: '' }, { kind: 'accepted', reason: 'invalid' }, { kind: 'ignored', reason: '\ud800' }]) {
    const model = paired(), before = model.initialState(), valid = model.step(before, 0);
    model.oracle.reference.step = () => ({ state: 1, outputs: [10, 21], disposition: invalid });
    assert.throws(() => model.step(before, 0), error => error instanceof ObservationError && /reference transition/.test(error.message));
    assert.throws(() => Array.from(model.oracle.checkTransition(before, 0, valid)), error => error instanceof ModelError && /reference transition/.test(error.message));
    assert.throws(() => enumerateStates(model), ModelError);
  }
});
test('exact paired replay rejects noncanonical components accepted by a permissive decoder', () => {
  const model = paired(), trace = record(model, []), bytes = new Uint8Array(trace.initialState);
  const decode = model.oracle.reference.decodeState.bind(model.oracle.reference);
  model.oracle.reference.decodeState = component => component.length === 1 && component[0] === 255 ? 0 : decode(component);
  bytes[bytes.length - 1] = 255;
  assert.deepEqual(model.decodeState(bytes), model.initialState());
  assert.throws(() => replay(model, { ...trace, initialState: bytes }), /not canonical/);
});

test('WithOracle forwards independent generation, causal shrink and codecs through paired search',async()=>{
 const {automatic,fuzz,shrink,Rng}=await import('../dist/index.js');
 class GeneratedQueue extends Queue {
  #domain=[0,1,2];equalInputs(a,b){return a===b;}
  generate(_s,r){return this.#domain[Number(r.index(3n))];}
  isEnabled(_s,i){return this.#domain.includes(i);}
  simplerInputs(i){return i===1?[0]:[];}
 }
 const model=new WithOracle(new GeneratedQueue('state'),new Paired(new Reference(),new Projection()));
 const r=fuzz(model,{seed:0n,cases:20,maxSteps:10,maxTransitions:100n,mutationPercent:50});assert.equal(r.termination,'failure_found');
 const minimized=shrink(model,r.failure);assert.equal(minimized.validated_original,true);assert.equal(minimized.termination,'search_complete');assert.ok(minimized.minimized.inputs.length<=r.failure.inputs.length);
 const trace=record(model,minimized.minimized.inputs);assert.equal(replay(model,trace).outcome,'exact');assert.equal(replay(model,trace).failure_reproduced,true);
 const autoModel=new WithOracle(automatic(new GeneratedQueue('state')),new Paired(new Reference(),new Projection()));assert.equal(fuzz(autoModel,{seed:0n,cases:10,maxSteps:5,maxTransitions:50n,mutationPercent:0}).termination,'failure_found');
 const unsupported=paired();assert.throws(()=>unsupported.generate(unsupported.initialState(),new Rng()),/unsupported oracle adapter operation: generate/);
});
test('paired/oracle search preserves emitted checks when later checker/projection fails',async()=>{
 const {fuzz}=await import('../dist/index.js');const config={seed:0n,cases:1,maxSteps:1,maxTransitions:1n,mutationPercent:0};
 const errors=[],observer={checkError:(phase,checks,complete)=>errors.push([phase,checks.map(c=>c.id),complete])};
 class GeneratedQueue extends Queue {equalInputs(a,b){return a===b;}generate(){return 0;}}
 class ThrowingProjection extends Projection {actualState(){throw Error('projection failed');}}
 const m=new WithOracle(new GeneratedQueue(),new Paired(new Reference(),new ThrowingProjection()));
 assert.throws(()=>fuzz(m,config,{},observer),/projection failed/);assert.deepEqual(errors,[['initial_state',['queue.bound','reference.bound'],false]]);
 errors.length=0;
 class PartialQueue extends GeneratedQueue { *checkState(){yield check('partial actual');throw Error('actual checker failed');}}
 assert.throws(()=>fuzz(new WithOracle(new PartialQueue(),new Paired(new Reference(),new Projection())),config,{},observer),/actual checker failed/);assert.deepEqual(errors,[['initial_state',['partial actual'],false]]);
 errors.length=0;
 class EdgeProjection extends Projection {actualEffect(){throw Error('effect projection failed');}}
 const edge=new WithOracle(new GeneratedQueue(),new Paired(new Reference(),new EdgeProjection()));assert.throws(()=>fuzz(edge,config,{},observer),/effect projection failed/);
 assert.deepEqual(errors,[['state',['queue.bound','reference.bound','pair.state'],true],['transition',['queue.transition','reference.transition'],false]]);
});

test('WithOracle captures complete check observations before child callbacks',()=>{
 const state={model:{value:1},oracle:{value:2}}, input={value:3};
 const transition={state:{model:{value:4},oracle:{value:5}},outputs:[{value:6}],disposition:{kind:'accepted',reason:''}};
 const mutate=()=>{state.model.value=91;state.oracle.value=92;input.value=93;transition.state.model.value=94;transition.state.oracle.value=95;transition.outputs[0].value=96;transition.disposition.reason='changed';};
 const model={metadata:()=>metadata('check-capture'),cloneState:s=>({...s}),cloneInput:i=>({...i}),cloneOutput:o=>({...o}),
  checkState:()=>{mutate();return [];},checkTransition:()=>{mutate();return [];}};
 let seenState,seenTransition;
 const oracle={metadata:()=>metadata('check-capture-oracle'),cloneHistory:h=>({...h}),
  checkState:(history,actual)=>{seenState={history,actual};return [];},
  checkTransition:(before,i,t)=>{seenTransition={before,input:i,transition:t};return [];}};
 const wrapped=new WithOracle(model,oracle);
 wrapped.checkState(state);
 assert.deepEqual(seenState,{history:{value:2},actual:{value:1}});
 Object.assign(state.model,{value:1});Object.assign(state.oracle,{value:2});input.value=3;
 transition.state.model.value=4;transition.state.oracle.value=5;transition.outputs[0].value=6;transition.disposition.reason='';
 wrapped.checkTransition(state,input,transition);
 assert.deepEqual(seenTransition,{before:{model:{value:1},oracle:{value:2}},input:{value:3},transition:{state:{model:{value:4},oracle:{value:5}},outputs:[{value:6}],disposition:{kind:'accepted',reason:''}}});
});

test('Paired preserves reference state across reference checks',()=>{
 const history={value:1};
 const reference={cloneState:s=>({...s}),checkState(){history.value=9;return [];}};
 const projection={actualState:s=>s,referenceState:s=>s.value,equalState:(a,b)=>a===b};
 const oracle=new Paired(reference,projection);
 const checks=Array.from(oracle.checkState(history,1));
 assert.equal(checks.at(-1).status,'passed');
 const failure=new Error('reference check');
 let copies=0; reference.cloneState=s=>{if(++copies===1)throw Error('copy failed');return {...s};};
 reference.checkState=()=>{throw failure;};
 assert.throws(()=>Array.from(oracle.checkState(history,1)),/reference check/);
});

test('Paired preserves reference effects across reference transition checks',()=>{
 let retained; const source={value:7};
 const reference={cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>{const copy={...o};if(o===source)retained=copy;return copy;},
  step:s=>accepted(s,[source]),checkTransition(){retained.value=99;return [];}};
 const projection={actualEffect:o=>o.value,referenceEffect:o=>o.value,equalEffect:(a,b)=>a===b};
 const oracle=new Paired(reference,projection);
 const checks=Array.from(oracle.checkTransition({model:0,oracle:0},0,accepted({model:0,oracle:0},[{value:7}])));
 assert.equal(checks.find(c=>c.id==='pair.effects').status,'passed');
});

test('Paired pins projection and preserves state projection error precedence',()=>{
 const projectionError=new Error('actual projection failed'); let copies=0;
 const reference={cloneState:s=>{if(++copies===1)throw Error('copy failed');return s;},checkState:()=>[]};
 const projection={actualState:()=>{throw projectionError;},referenceState:s=>s,equalState:()=>true};
 const paired=new Paired(reference,projection);
 assert.throws(()=>Array.from(paired.checkState(1,1)),/actual projection failed/);
 const replacement={actualState:()=>99,referenceState:()=>0,equalState:()=>false};
 const original={actualState:s=>s,referenceState:s=>s,equalState:(a,b)=>a===b};
 const other=new Paired({cloneState:s=>s,checkState(){other.projection=replacement;return [];}},original);
 assert.equal(Array.from(other.checkState(1,1)).at(-1).status,'passed');
});

test('Paired pins reference check hooks before observation copies',()=>{
 let observed=0; const ref={cloneState(s){this.checkState=()=>{throw Error('replacement');};return s;},checkState(){observed++;return [];}};
 const projection={actualState:s=>s,referenceState:s=>s,equalState:(a,b)=>a===b};
 const paired=new Paired(ref,projection);
 assert.equal(Array.from(paired.checkState(1,1)).at(-1).status,'passed');
 assert.equal(observed,1);
});

test('Paired pins reference transition checks before output copies',()=>{
 let seen=0;
 const ref={cloneState:s=>s,cloneInput:i=>i,cloneOutput(o){this.checkTransition=()=>{throw Error('replacement');};return o;},
  step:s=>accepted(s,[7]),checkTransition(){seen++;return [];}};
 const projection={actualEffect:o=>o,referenceEffect:o=>o,equalEffect:(a,b)=>a===b};
 const oracle=new Paired(ref,projection);
 assert.equal(Array.from(oracle.checkTransition({model:0,oracle:0},0,accepted({model:0,oracle:0},[7]))).find(c=>c.id==='pair.effects').status,'passed');
 assert.equal(seen,1);
});
