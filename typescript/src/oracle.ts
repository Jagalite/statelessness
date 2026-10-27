import {EncodeStorage,withEncodeBuffer,withChildEncodeBuffer,appendModelEncoding,requireSynchronousCodecReturn,type EncodeBuffer} from './encoding.js';
import {withCheckSink,withChildCheckSink,appendStateChecks,appendTransitionChecks,requireSynchronousCheckReturn} from './checks.js';
import type {CheckSink} from './checks.js';
/** Experimental paired-v1. Uses the existing native recorder, observer and BFS.
 * Advancement never receives actual next state; callbacks must remain pure. */
import { type Check, type Disposition, type Metadata, type Model, type EnumerateModel,
  type CodecModel, type Transition, ModelError, check, disposition, validateMetadata, type Rng } from './core.js';
import type { GenerateModel } from './search.js';

export const EXPERIMENTAL_PROFILES = ['paired-v1', 'search-v1', 'native-binary-v1', 'bfs-controls-v1', 'append-checks-v1', 'encode-buffers-v1', 'replay-evidence-v1', 'checked-results-v1', 'causal-evidence-v2', 'canonical-value-codec-v1', 'research-equivalence-v1'] as const;
export interface OracleState<S, H> { readonly model: S; readonly oracle: H }
export interface Oracle<S, H, I, O> {
  /** Application-owned history estimate in the caller's documented logical representation. */
  estimatedStateBytes?(history:H):bigint|null;
  metadata(): Metadata;
  initialState(): H;
  advance(before: H, input: I, outputs: readonly O[], disposition: Disposition): H;
  checkState(history: H, actual: S): Iterable<Check>;
  checkStateInto?(history:H,actual:S,sink:CheckSink):void;
  checkTransitionInto?(before:OracleState<S,H>,input:I,transition:Transition<OracleState<S,H>,O>,sink:CheckSink):void;
  checkTransition?(before: OracleState<S, H>, input: I, transition: Transition<OracleState<S, H>, O>): Iterable<Check>;
  cloneHistory(history: H): H;
  equalHistory(left: H, right: H): boolean;
  encodeHistory?(history: H): Uint8Array;
  encodeHistoryInto?(history:H,buffer:EncodeBuffer):void;
  decodeHistory?(bytes: Uint8Array): H;
}
type Adapter<S, I, O> = Model<S, I, O> & Partial<EnumerateModel<S, I, O> & CodecModel<S, I, O> & GenerateModel<S,I,O>>;
function need<T>(value: T | undefined, name: string): T {
  if (value === undefined) throw new ModelError(`unsupported oracle adapter operation: ${name}`);
  return value;
}
function checkedEstimate(n:bigint|null):bigint|null {
  if(n!==null&&(typeof n!=='bigint'||n<0n||n>((1n<<64n)-1n)))throw new ModelError('estimate must be u64 bigint or null');
  return n;
}
const length = (text: string): number => new TextEncoder().encode(text).length;
export class ObservationError<S, O> extends ModelError {
  constructor(readonly cause: unknown, readonly transition: Transition<S, O>) {
    super(cause instanceof Error ? cause.message : String(cause)); this.name = 'ObservationError';
  }
}
export class WithOracle<S, H, I, O> implements EnumerateModel<OracleState<S, H>, I, O>, CodecModel<OracleState<S, H>, I, O>, GenerateModel<OracleState<S,H>,I,O> {
  /** Wrapper overhead is caller-supplied logical bytes; absent is unknown, never inferred RSS. */
  constructor(readonly model: Adapter<S, I, O>, readonly oracle: Oracle<S, H, I, O>, readonly estimatedWrapperOverhead:bigint|null=null) {
    checkedEstimate(estimatedWrapperOverhead);
  }
  estimatedStateBytes(s:OracleState<S,H>):bigint|null {
    const model=checkedEstimate(this.model.estimatedStateBytes?this.model.estimatedStateBytes(this.model.cloneState(s.model)):null);
    if(model===null)return null;
    const history=checkedEstimate(this.oracle.estimatedStateBytes?this.oracle.estimatedStateBytes(this.oracle.cloneHistory(s.oracle)):null);
    if(history===null||this.estimatedWrapperOverhead===null)return null;
    const total=model+history+this.estimatedWrapperOverhead;
    return total>((1n<<64n)-1n)?null:total;
  }
  metadata(): Metadata {
    const m = validateMetadata(need(this.model.metadata, 'metadata').call(this.model));
    const o = validateMetadata(this.oracle.metadata());
    return { name: `with-oracle-v1:${length(m.name)}:${m.name}:${m.model_version}:${m.properties_version}:${m.codec_version}:${length(o.name)}:${o.name}:${o.model_version}:${o.properties_version}:${o.codec_version}`,
      model_version: m.model_version, properties_version: m.properties_version, codec_version: m.codec_version,
      build: `${length(m.build)}:${m.build}:${length(o.build)}:${o.build}` };
  }
  initialState(): OracleState<S, H> { return this.attach(this.model.initialState(), this.oracle.initialState()); }
  attach(model: S, oracle: H): OracleState<S, H> { return { model: this.model.cloneState(model), oracle: this.oracle.cloneHistory(oracle) }; }
  cloneState(s: OracleState<S, H>): OracleState<S, H> { return this.attach(s.model, s.oracle); }
  cloneInput(i: I): I { return this.model.cloneInput(i); }
  cloneOutput(o: O): O { return this.model.cloneOutput(o); }
  private copyTransition(t: Transition<S, O>): Transition<S, O> {
    return { state: this.model.cloneState(t.state), outputs: t.outputs.map(o => this.cloneOutput(o)), disposition: { ...t.disposition } };
  }
  observeTransition(before: OracleState<S, H>, input: I, transition: Transition<S, O>): Transition<OracleState<S, H>, O> {
    const retained = this.copyTransition(transition);
    try {
      const history = this.oracle.advance(this.oracle.cloneHistory(before.oracle), this.cloneInput(input),
        transition.outputs.map(o => this.cloneOutput(o)), { ...transition.disposition });
      return { state: this.attach(transition.state, history), outputs: retained.outputs, disposition: retained.disposition };
    } catch (error) { throw new ObservationError(error, retained); }
  }
  step(before: OracleState<S, H>, input: I): Transition<OracleState<S, H>, O> {
    return this.observeTransition(before, input, this.model.step(this.model.cloneState(before.model), this.cloneInput(input)));
  }
  inputs(s: OracleState<S, H>): Iterable<I> { return need(this.model.inputs, 'inputs').call(this.model, this.model.cloneState(s.model)); }
  generate(s:OracleState<S,H>,rng:Rng):ReturnType<GenerateModel<S,I,O>['generate']> {
    return need(this.model.generate,'generate').call(this.model,this.model.cloneState(s.model),rng);
  }
  isEnabled(s:OracleState<S,H>,i:I):boolean {
    need(this.model.generate,'generate');
    return this.model.isEnabled ? this.model.isEnabled(this.model.cloneState(s.model),this.cloneInput(i)) : true;
  }
  simplerInputs(i:I):Iterable<I> {
    need(this.model.generate,'generate');
    return this.model.simplerInputs ? this.model.simplerInputs(this.cloneInput(i)) : [];
  }
  equalInputs(a:I,b:I):boolean {return need(this.model.equalInputs,'equalInputs').call(this.model,this.cloneInput(a),this.cloneInput(b));}
  equalStates(a: OracleState<S, H>, b: OracleState<S, H>): boolean {
    return need(this.model.equalStates, 'equalStates').call(this.model, a.model, b.model) && this.oracle.equalHistory(a.oracle, b.oracle);
  }
  checkState(s:OracleState<S,H>):Iterable<Check> {
    const output:Check[]=[];withCheckSink(output,sink=>this.checkStateInto(s,sink));return output;
  }
  checkStateInto(s:OracleState<S,H>,sink:CheckSink):void {
    appendStateChecks(this.model,s.model,sink);
    withChildCheckSink(sink,child=>{
      const history=this.oracle.cloneHistory(s.oracle),actual=this.model.cloneState(s.model),hook=this.oracle.checkStateInto;
      if(hook!==undefined){if(typeof hook!=='function')throw new ModelError('append check hook must be a function');requireSynchronousCheckReturn(hook.call(this.oracle,history,actual,child));}
      else {const legacy=this.oracle.checkState;child.extend(legacy.call(this.oracle,history,actual));}
    });
  }
  checkTransition(before:OracleState<S,H>,input:I,t:Transition<OracleState<S,H>,O>):Iterable<Check> {
    const output:Check[]=[];withCheckSink(output,sink=>this.checkTransitionInto(before,input,t,sink));return output;
  }
  checkTransitionInto(before:OracleState<S,H>,input:I,t:Transition<OracleState<S,H>,O>,sink:CheckSink):void {
    const actual=this.copyTransition({state:t.state.model,outputs:t.outputs,disposition:t.disposition});
    appendTransitionChecks(this.model,before.model,input,actual,sink);
    withChildCheckSink(sink,child=>{
      const hook=this.oracle.checkTransitionInto;
      if(hook!==undefined&&typeof hook!=='function')throw new ModelError('append check hook must be a function');
      const legacy=hook===undefined?this.oracle.checkTransition:undefined;
      if(hook===undefined&&legacy===undefined)return;
      const b=this.cloneState(before),i=this.cloneInput(input),after={state:this.cloneState(t.state),outputs:t.outputs.map(o=>this.cloneOutput(o)),disposition:{...t.disposition}};
      if(hook!==undefined)requireSynchronousCheckReturn(hook.call(this.oracle,b,i,after,child));else child.extend(legacy!.call(this.oracle,b,i,after));
    });
  }
  encodeState(s:OracleState<S,H>):Uint8Array {
    const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeStateInto(s,buffer));return storage.snapshot();
  }
  encodeStateInto(s:OracleState<S,H>,buffer:EncodeBuffer):void {
    buffer.extend(Uint8Array.of(79,82,67,49));
    buffer.framed(child=>appendModelEncoding(this.model as CodecModel<S,I,O>,'state',s.model,child));
    buffer.framed(child=>withChildEncodeBuffer(child,historyBuffer=>{
      const history=this.oracle.cloneHistory(s.oracle),hook=this.oracle.encodeHistoryInto;
      if(hook!==undefined){if(typeof hook!=='function')throw new ModelError('codec hook must be a function');requireSynchronousCodecReturn(hook.call(this.oracle,history,historyBuffer));}
      else {const legacy=need(this.oracle.encodeHistory,'encodeHistory');historyBuffer.extendFromVec(legacy.call(this.oracle,history));}
    }));
  }
  decodeState(bytes: Uint8Array): OracleState<S, H> {
    if (!(bytes instanceof Uint8Array) || bytes.length < 4 || ![79, 82, 67, 49].every((v, n) => bytes[n] === v)) throw new ModelError('invalid oracle checkpoint format');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), parts: Uint8Array[] = [];
    let offset = 4;
    for (let n = 0; n < 2; n++) {
      if (bytes.length - offset < 8) throw new ModelError('truncated oracle checkpoint length');
      const size = view.getBigUint64(offset, true); offset += 8;
      if (size > BigInt(bytes.length - offset)) throw new ModelError('truncated oracle checkpoint component');
      parts.push(bytes.slice(offset, offset + Number(size))); offset += Number(size);
    }
    if (offset !== bytes.length) throw new ModelError('trailing oracle checkpoint bytes');
    return this.attach(need(this.model.decodeState, 'decodeState').call(this.model, parts[0]),
      need(this.oracle.decodeHistory, 'decodeHistory').call(this.oracle, parts[1]));
  }
  encodeInput(i:I):Uint8Array {const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeInputInto(i,buffer));return storage.snapshot();}
  encodeInputInto(i:I,buffer:EncodeBuffer):void {appendModelEncoding(this.model as CodecModel<S,I,O>,'input',i,buffer);}
  decodeInput(b: Uint8Array): I { return need(this.model.decodeInput, 'decodeInput').call(this.model, b); }
  encodeOutput(o:O):Uint8Array {const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeOutputInto(o,buffer));return storage.snapshot();}
  encodeOutputInto(o:O,buffer:EncodeBuffer):void {appendModelEncoding(this.model as CodecModel<S,I,O>,'output',o,buffer);}
}

export interface Projection<S, H, O, R, V, E> {
  metadata(): Metadata;
  actualState(state: S): V;
  referenceState(state: H): V;
  actualEffect(effect: O): E;
  referenceEffect(effect: R): E;
  equalState(actual: V, reference: V): boolean;
  equalEffect(actual: E, reference: E): boolean;
}
export class Paired<S, H, I, O, R, V, E> implements Oracle<S, H, I, O> {
  constructor(readonly reference: Adapter<H, I, R>, readonly projection: Projection<S, H, O, R, V, E>) {}
  metadata(): Metadata {
    const r = validateMetadata(need(this.reference.metadata, 'reference metadata').call(this.reference)), p = validateMetadata(this.projection.metadata());
    return { name: `paired-v1:${length(r.name)}:${r.name}:${r.model_version}:${r.properties_version}:${r.codec_version}:${length(p.name)}:${p.name}:${p.model_version}`,
      model_version: p.model_version, properties_version: p.properties_version, codec_version: p.codec_version,
      build: `${length(r.build)}:${r.build}:${length(p.build)}:${p.build}` };
  }
  estimatedStateBytes(h:H):bigint|null {return checkedEstimate(this.reference.estimatedStateBytes?this.reference.estimatedStateBytes(this.reference.cloneState(h)):null);}
  initialState(): H { return this.reference.initialState(); }
  cloneHistory(h: H): H { return this.reference.cloneState(h); }
  equalHistory(a: H, b: H): boolean { return need(this.reference.equalStates, 'reference equalStates').call(this.reference, a, b); }
  private referenceStep(before: H, input: I): Transition<H, R> {
    try {
      const transition = this.reference.step(this.reference.cloneState(before), this.reference.cloneInput(input));
      return { state: this.reference.cloneState(transition.state),
        outputs: Array.from(transition.outputs, output => this.reference.cloneOutput(output)),
        disposition: disposition(transition.disposition.kind, transition.disposition.reason) };
    } catch (error) {
      throw new ModelError(`reference transition: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  advance(before: H, input: I, _outputs: readonly O[], _disposition: Disposition): H {
    return this.referenceStep(before, input).state;
  }
  checkState(history:H,actual:S):Iterable<Check>{const output:Check[]=[];withCheckSink(output,sink=>this.checkStateInto(history,actual,sink));return output;}
  checkStateInto(history:H,actual:S,sink:CheckSink):void {
    appendStateChecks(this.reference,history,sink);
    const matches = this.projection.equalState(this.projection.actualState(actual), this.projection.referenceState(history));
    sink.push(check('pair.state', matches ? 'passed' : 'failed', matches ? '' : 'projected states differ'));
  }
  checkTransition(before:OracleState<S,H>,input:I,actual:Transition<OracleState<S,H>,O>):Iterable<Check>{const output:Check[]=[];withCheckSink(output,sink=>this.checkTransitionInto(before,input,actual,sink));return output;}
  checkTransitionInto(before:OracleState<S,H>,input:I,actual:Transition<OracleState<S,H>,O>,sink:CheckSink):void {
    const expected = this.referenceStep(before.oracle, input);
    appendTransitionChecks(this.reference,before.oracle,input,expected,sink);
    const a = actual.outputs.map(o => this.projection.actualEffect(o)), r = expected.outputs.map(o => this.projection.referenceEffect(o));
    const effects = a.length === r.length && a.every((v, n) => this.projection.equalEffect(v, r[n]));
    const outcome = actual.disposition.kind === expected.disposition.kind && actual.disposition.reason === expected.disposition.reason;
    sink.push(check('pair.effects', effects ? 'passed' : 'failed', effects ? '' : 'projected ordered effects differ'));
    sink.push(check('pair.outcome', outcome ? 'passed' : 'failed', outcome ? '' : 'dispositions differ'));
  }
  encodeHistory(h:H):Uint8Array {const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeHistoryInto(h,buffer));return storage.snapshot();}
  encodeHistoryInto(h:H,buffer:EncodeBuffer):void {appendModelEncoding(this.reference as CodecModel<H,I,R>,'state',h,buffer);}
  decodeHistory(b: Uint8Array): H { return need(this.reference.decodeState, 'reference decodeState').call(this.reference, b); }
}
