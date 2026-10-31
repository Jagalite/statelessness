import {EncodeStorage,withEncodeBuffer,withChildEncodeBuffer,appendModelEncoding,requireSynchronousCodecReturn,type EncodeBuffer} from './encoding.js';
import {withCheckSink,withChildCheckSink,appendStateChecks,appendTransitionChecks,requireSynchronousCheckReturn} from './checks.js';
import type {CheckSink} from './checks.js';
/** Experimental paired-v1. Uses the existing native recorder, observer and BFS.
 * Advancement never receives actual next state; callbacks must remain pure. */
import { type Check, type Disposition, type Metadata, type Model, type EnumerateModel,
  type CodecModel, type Transition, ModelError, check, disposition, validateMetadata, type Rng } from './core.js';
import type { GenerateModel } from './search.js';

export const EXPERIMENTAL_PROFILES = ['paired-v1', 'search-v1', 'native-binary-v1', 'bfs-controls-v1', 'append-checks-v1', 'encode-buffers-v1', 'replay-evidence-v1', 'checked-results-v1', 'causal-evidence-v2', 'canonical-value-codec-v1', 'research-equivalence-v1', 'observation-v1'] as const;
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
function checkedEquality(value:unknown,context:string):boolean {
  if(typeof value==='boolean')return value;
  if(value instanceof Promise)void value.catch(()=>{});
  throw new ModelError(`${context} must return boolean synchronously`);
}
export class ObservationError<S, O> extends ModelError {
  constructor(readonly cause: unknown, readonly transition: Transition<S, O>) {
    let message='oracle advancement failed';
    try { if(typeof cause==='string')message=cause;else if(cause instanceof Error&&typeof cause.message==='string')message=cause.message; } catch {}
    super(message); this.name = 'ObservationError';
  }
}
export class WithOracle<S, H, I, O> implements EnumerateModel<OracleState<S, H>, I, O>, CodecModel<OracleState<S, H>, I, O>, GenerateModel<OracleState<S,H>,I,O> {
  /** Wrapper overhead is caller-supplied logical bytes; absent is unknown, never inferred RSS. */
  constructor(readonly model: Adapter<S, I, O>, readonly oracle: Oracle<S, H, I, O>, readonly estimatedWrapperOverhead:bigint|null=null) {
    checkedEstimate(estimatedWrapperOverhead);
  }
  estimatedStateBytes(s:OracleState<S,H>):bigint|null {
    // Own history before model callbacks can mutate an external alias. Defer
    // clone failure until the model estimate is known, preserving its error and
    // unknown-result precedence. Clone hooks may have observable side effects.
    // Pin callback availability for this estimate, including its receiver.
    const modelOwner=this.model, oracleOwner=this.oracle;
    const modelEstimate=modelOwner.estimatedStateBytes, oracleEstimate=oracleOwner.estimatedStateBytes;
    let historySnapshot:H|undefined, cloneError:unknown, cloneFailed=false;
    if(modelEstimate && oracleEstimate) {
      try { historySnapshot=oracleOwner.cloneHistory(s.oracle); }
      catch(error) { cloneFailed=true; cloneError=error; }
    }
    const model=checkedEstimate(modelEstimate?modelEstimate.call(modelOwner,modelOwner.cloneState(s.model)):null);
    if(model===null)return null;
    if(cloneFailed)throw cloneError;
    const history=checkedEstimate(oracleEstimate?oracleEstimate.call(oracleOwner,historySnapshot as H):null);
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
  initialState(): OracleState<S, H> {
    // Own the model result before invoking an unrelated host callback.
    const model=this.model.cloneState(this.model.initialState());
    return this.attach(model,this.oracle.initialState());
  }
  attach(model: S, oracle: H): OracleState<S, H> { return { model: this.model.cloneState(model), oracle: this.oracle.cloneHistory(oracle) }; }
  cloneState(s: OracleState<S, H>): OracleState<S, H> { return this.attach(s.model, s.oracle); }
  cloneInput(i: I): I { return this.model.cloneInput(i); }
  cloneOutput(o: O): O { return this.model.cloneOutput(o); }
  private copyTransition(t: Transition<S, O>): Transition<S, O> {
    return { state: this.model.cloneState(t.state), outputs: t.outputs.map(o => this.cloneOutput(o)), disposition: { ...t.disposition } };
  }
  observeTransition(before: OracleState<S, H>, input: I, transition: Transition<S, O>): Transition<OracleState<S, H>, O> {
    // Retain the oracle inputs before transition clone hooks can mutate aliases.
    // Their failures are consumed only after the completed transition is retained.
    const model = this.model, oracle = this.oracle, history = before.oracle;
    const transitionView = {state:transition.state,outputs:transition.outputs.slice(),disposition:{...transition.disposition}};
    const capture = <T>(f:()=>T): {ok:true;value:T}|{ok:false;error:unknown} => {
      try { return {ok:true,value:f()}; } catch (error) { return {ok:false,error}; }
    };
    const cloneInput = capture(() => model.cloneInput), cloneHistory = capture(() => oracle.cloneHistory);
    const cloneState = capture(() => model.cloneState), cloneOutput = capture(() => model.cloneOutput);
    const advance = capture(() => oracle.advance);
    const capturedInput = capture(() => {if (!cloneInput.ok) throw cloneInput.error; return cloneInput.value.call(model,input);});
    const capturedHistory = capture(() => {if (!cloneHistory.ok) throw cloneHistory.error; return cloneHistory.value.call(oracle,history);});
    if (!cloneState.ok) throw cloneState.error;
    const copyOutput = (o:O):O => {if (!cloneOutput.ok) throw cloneOutput.error; return cloneOutput.value.call(model,o);};
    const retained = {state:cloneState.value.call(model,transitionView.state),outputs:transitionView.outputs.map(copyOutput),disposition:transitionView.disposition};
    try {
      if (!capturedInput.ok) throw capturedInput.error;
      if (!capturedHistory.ok) throw capturedHistory.error;
      if (!advance.ok) throw advance.error;
      const nextHistory = advance.value.call(oracle,capturedHistory.value,capturedInput.value,
        retained.outputs.map(copyOutput), { ...retained.disposition });
      if (!cloneHistory.ok) throw cloneHistory.error;
      return { state: {model:cloneState.value.call(model,retained.state),oracle:cloneHistory.value.call(oracle,nextHistory)}, outputs: retained.outputs, disposition: retained.disposition };
    } catch (error) { throw new ObservationError(error, retained); }
  }
  step(before: OracleState<S, H>, input: I): Transition<OracleState<S, H>, O> {
    // Preserve the oracle's observation before the reducer can mutate external
    // aliases. Capture input first because state/history clone hooks are user code.
    const observedInput = this.cloneInput(input);
    const observedBefore = this.cloneState(before);
    const actual = this.model.step(this.model.cloneState(observedBefore.model), this.cloneInput(observedInput));
    return this.observeTransition(observedBefore, observedInput, actual);
  }
  inputs(s: OracleState<S, H>): Iterable<I> { return need(this.model.inputs, 'inputs').call(this.model, this.model.cloneState(s.model)); }
  generate(s:OracleState<S,H>,rng:Rng):ReturnType<GenerateModel<S,I,O>['generate']> {
    return need(this.model.generate,'generate').call(this.model,this.model.cloneState(s.model),rng);
  }
  isEnabled(s:OracleState<S,H>,i:I):boolean {
    const model=this.model;
    need(model.generate,'generate');
    const enabled=model.isEnabled;
    if (!enabled) return true;
    // A state clone is application code and may mutate the caller's input or
    // replace model callbacks. Capture the query and callbacks first.
    const cloneInput=model.cloneInput, cloneState=model.cloneState, state=s.model;
    const query=cloneInput.call(model,i);
    return enabled.call(model,cloneState.call(model,state),query);
  }
  simplerInputs(i:I):Iterable<I> {
    need(this.model.generate,'generate');
    return this.model.simplerInputs ? this.model.simplerInputs(this.cloneInput(i)) : [];
  }
  equalInputs(a:I,b:I):boolean {return need(this.model.equalInputs,'equalInputs').call(this.model,this.cloneInput(a),this.cloneInput(b));}
  equalStates(a: OracleState<S, H>, b: OracleState<S, H>): boolean {
    // Capture histories before model clone/equality hooks can mutate external aliases.
    const leftHistory=this.oracle.cloneHistory(a.oracle), rightHistory=this.oracle.cloneHistory(b.oracle);
    return checkedEquality(need(this.model.equalStates, 'equalStates').call(this.model, this.model.cloneState(a.model), this.model.cloneState(b.model)),'model equality') &&
      checkedEquality(this.oracle.equalHistory(leftHistory,rightHistory),'oracle equality');
  }
  checkState(s:OracleState<S,H>):Iterable<Check> {
    const output:Check[]=[];withCheckSink(output,sink=>this.checkStateInto(s,sink));return output;
  }
  checkStateInto(s:OracleState<S,H>,sink:CheckSink):void {
    const observed=this.cloneState(s);
    appendStateChecks(this.model,observed.model,sink);
    withChildCheckSink(sink,child=>{
      const history=this.oracle.cloneHistory(observed.oracle),actual=this.model.cloneState(observed.model),hook=this.oracle.checkStateInto;
      if(hook!==undefined){if(typeof hook!=='function')throw new ModelError('append check hook must be a function');requireSynchronousCheckReturn(hook.call(this.oracle,history,actual,child));}
      else {const legacy=this.oracle.checkState;child.extend(legacy.call(this.oracle,history,actual));}
    });
  }
  checkTransition(before:OracleState<S,H>,input:I,t:Transition<OracleState<S,H>,O>):Iterable<Check> {
    const output:Check[]=[];withCheckSink(output,sink=>this.checkTransitionInto(before,input,t,sink));return output;
  }
  checkTransitionInto(before:OracleState<S,H>,input:I,t:Transition<OracleState<S,H>,O>,sink:CheckSink):void {
    const capturedBefore=this.cloneState(before), capturedInput=this.cloneInput(input);
    const capturedAfter={state:this.cloneState(t.state),outputs:t.outputs.map(o=>this.cloneOutput(o)),disposition:{...t.disposition}};
    const actual=this.copyTransition({state:capturedAfter.state.model,outputs:capturedAfter.outputs,disposition:capturedAfter.disposition});
    appendTransitionChecks(this.model,capturedBefore.model,capturedInput,actual,sink);
    withChildCheckSink(sink,child=>{
      const hook=this.oracle.checkTransitionInto;
      if(hook!==undefined&&typeof hook!=='function')throw new ModelError('append check hook must be a function');
      const legacy=hook===undefined?this.oracle.checkTransition:undefined;
      if(hook===undefined&&legacy===undefined)return;
      const b=this.cloneState(capturedBefore),i=this.cloneInput(capturedInput),after={state:this.cloneState(capturedAfter.state),outputs:capturedAfter.outputs.map(o=>this.cloneOutput(o)),disposition:{...capturedAfter.disposition}};
      if(hook!==undefined)requireSynchronousCheckReturn(hook.call(this.oracle,b,i,after,child));else child.extend(legacy!.call(this.oracle,b,i,after));
    });
  }
  encodeState(s:OracleState<S,H>):Uint8Array {
    const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeStateInto(s,buffer));return storage.snapshot();
  }
  encodeStateInto(s:OracleState<S,H>,buffer:EncodeBuffer):void {
    // Capture before model callbacks can change an externally aliased history.
    // Defer capture errors to the history frame so model and framing errors keep
    // their precedence. The clone hook can have side effects even on failure;
    // in-place cross-alias mutation during copying cannot be made atomic.
    const modelOwner=this.model, oracleOwner=this.oracle, modelValue=s.model;
    const cloneState=modelOwner.cloneState, modelHook=modelOwner.encodeStateInto, modelLegacy=modelOwner.encodeState;
    const historyHook=oracleOwner.encodeHistoryInto, historyLegacy=oracleOwner.encodeHistory;
    // Keep the original receivers even if a callback replaces the adapters.
    const modelCodec={cloneState:(value:S)=>cloneState.call(modelOwner,value),
      encodeStateInto:typeof modelHook==='function' ? modelHook.bind(modelOwner) : modelHook,
      encodeState:typeof modelLegacy==='function' ? modelLegacy.bind(modelOwner) : modelLegacy};
    let history:H|undefined, captureError:unknown, captureFailed=false;
    try { history=oracleOwner.cloneHistory(s.oracle); }
    catch(error) { captureFailed=true; captureError=error; }
    buffer.extend(Uint8Array.of(79,82,67,49));
    buffer.framed(child=>appendModelEncoding(modelCodec as CodecModel<S,I,O>,'state',modelValue,child));
    buffer.framed(child=>withChildEncodeBuffer(child,historyBuffer=>{
      if(captureFailed)throw captureError;
      const hook=historyHook;
      if(hook!==undefined){if(typeof hook!=='function')throw new ModelError('codec hook must be a function');requireSynchronousCodecReturn(hook.call(oracleOwner,history as H,historyBuffer));}
      else {const legacy=need(historyLegacy,'encodeHistory');historyBuffer.extendFromVec(legacy.call(oracleOwner,history as H));}
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
    const model=this.model.cloneState(need(this.model.decodeState, 'decodeState').call(this.model, parts[0]));
    return this.attach(model,need(this.oracle.decodeHistory, 'decodeHistory').call(this.oracle, parts[1]));
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
  private pinChecks(reference:Adapter<H,I,R>):Adapter<H,I,R> {
    // Keep the original receiver while preventing a clone hook from swapping
    // the checks or clone functions halfway through this observation.
    const facade=Object.create(reference) as Adapter<H,I,R>;
    for(const key of ['cloneState','cloneInput','cloneOutput','checkState','checkStateInto','checkTransition','checkTransitionInto'] as const) {
      const value=reference[key];
      Object.defineProperty(facade,key,{value:typeof value==='function'?value.bind(reference):value});
    }
    return facade;
  }
  metadata(): Metadata {
    const r = validateMetadata(need(this.reference.metadata, 'reference metadata').call(this.reference)), p = validateMetadata(this.projection.metadata());
    return { name: `paired-v1:${length(r.name)}:${r.name}:${r.model_version}:${r.properties_version}:${r.codec_version}:${length(p.name)}:${p.name}:${p.model_version}`,
      model_version: p.model_version, properties_version: p.properties_version, codec_version: p.codec_version,
      build: `${length(r.build)}:${r.build}:${length(p.build)}:${p.build}` };
  }
  estimatedStateBytes(h:H):bigint|null {return checkedEstimate(this.reference.estimatedStateBytes?this.reference.estimatedStateBytes(this.reference.cloneState(h)):null);}
  initialState(): H { return this.reference.initialState(); }
  cloneHistory(h: H): H { return this.reference.cloneState(h); }
  equalHistory(a: H, b: H): boolean { return checkedEquality(need(this.reference.equalStates, 'reference equalStates').call(this.reference, this.reference.cloneState(a), this.reference.cloneState(b)),'reference equality'); }
  private referenceStep(before: H, input: I): Transition<H, R> {
    const reference=this.reference, cloneState=reference.cloneState, cloneInput=reference.cloneInput, cloneOutput=reference.cloneOutput, step=reference.step;
    try {
      const transition = step.call(reference,cloneState.call(reference,before),cloneInput.call(reference,input));
      return { state: cloneState.call(reference,transition.state),
        outputs: Array.from(transition.outputs, output => cloneOutput.call(reference,output)),
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
    const reference=this.reference, projection=this.projection, checks=this.pinChecks(reference);
    const clone=reference.cloneState, actualState=projection.actualState, referenceState=projection.referenceState, equal=projection.equalState;
    // Preserve the reference observation before checks can mutate external aliases.
    // Defer a copy failure until reference checks have had their normal turn.
    let captured:H|undefined, copyError:unknown, copyFailed=false;
    try { captured=clone.call(reference,history); } catch(error) { copyFailed=true; copyError=error; }
    appendStateChecks(checks,history,sink);
    const actualValue=actualState.call(projection,actual);
    if(copyFailed)throw copyError;
    const matches = checkedEquality(equal.call(projection,actualValue,referenceState.call(projection,captured as H)),'projected state equality');
    sink.push(check('pair.state', matches ? 'passed' : 'failed', matches ? '' : 'projected states differ'));
  }
  checkTransition(before:OracleState<S,H>,input:I,actual:Transition<OracleState<S,H>,O>):Iterable<Check>{const output:Check[]=[];withCheckSink(output,sink=>this.checkTransitionInto(before,input,actual,sink));return output;}
  checkTransitionInto(before:OracleState<S,H>,input:I,actual:Transition<OracleState<S,H>,O>,sink:CheckSink):void {
    const reference=this.reference, projection=this.projection, checks=this.pinChecks(reference);
    const cloneOutput=reference.cloneOutput, actualEffect=projection.actualEffect, referenceEffect=projection.referenceEffect, equalEffect=projection.equalEffect;
    const expected = this.referenceStep(before.oracle, input);
    let outputs:R[]|undefined, copyError:unknown, copyFailed=false;
    const outcomeBefore={...expected.disposition};
    try { outputs=expected.outputs.map(value=>cloneOutput.call(reference,value)); } catch(error) { copyFailed=true; copyError=error; }
    appendTransitionChecks(checks,before.oracle,input,expected,sink);
    const a = actual.outputs.map(o => actualEffect.call(projection,o));
    if(copyFailed)throw copyError;
    const r = (outputs as R[]).map(o => referenceEffect.call(projection,o));
    const effects = a.length === r.length && a.every((v, n) => checkedEquality(equalEffect.call(projection,v, r[n]),'projected effect equality'));
    const outcome = actual.disposition.kind === outcomeBefore.kind && actual.disposition.reason === outcomeBefore.reason;
    sink.push(check('pair.effects', effects ? 'passed' : 'failed', effects ? '' : 'projected ordered effects differ'));
    sink.push(check('pair.outcome', outcome ? 'passed' : 'failed', outcome ? '' : 'dispositions differ'));
  }
  encodeHistory(h:H):Uint8Array {const storage=new EncodeStorage();withEncodeBuffer(storage,Number.MAX_SAFE_INTEGER,buffer=>this.encodeHistoryInto(h,buffer));return storage.snapshot();}
  encodeHistoryInto(h:H,buffer:EncodeBuffer):void {appendModelEncoding(this.reference as CodecModel<H,I,R>,'state',h,buffer);}
  decodeHistory(b: Uint8Array): H { return need(this.reference.decodeState, 'reference decodeState').call(this.reference, b); }
}
