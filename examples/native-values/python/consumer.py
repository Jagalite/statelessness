"""Installed typed value descriptors wired explicitly into existing model codecs."""
from dataclasses import dataclass,asdict
from pathlib import Path
import sys,json,statelessness
from statelessness import Check,Metadata,Transition,check_initial,check_turn,RecorderOptions,EvidenceRecorder,EvidenceLimits,Provenance,CausalContext,OutputOrigin,Artifact
from statelessness.trace_binary import BinaryLimits,RunConfig,audit_to_bytes
from statelessness.trace_json import dumps
import statelessness.value_codec as vc
import importlib.util
_helper_spec=importlib.util.spec_from_file_location('native_value_helpers',Path(__file__).with_name('values.py'))
values=importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(values)
LIMITS=BinaryLimits(max_total_bytes=16384,max_frame_bytes=4096,max_blob_bytes=64,max_string_bytes=256,max_steps=16,max_checks_per_step=16,max_outputs_per_step=16,max_parameters=32,max_items=256)
ELIMITS=EvidenceLimits(LIMITS,1024,8,128,4,8)
DL=vc.DecodeLimits(**{k:int(v)for k,v in values.CONTRACT['model_decode_limits'].items()})
OPTION=vc.OptionOf(vc.U64,layout=vc.Rust64Layout(16,8,'measured Option<u64>'))
STATE=vc.VectorOf(OPTION)
OUTPUT_FIELDS=vc.TupleOf(vc.U8,vc.STRING)
class OutputCodec:
    def encode_into(self,value,out):vc.encode_raw(OUTPUT_FIELDS,(value.code,value.message),out)
OUTPUT=OutputCodec()
P=values.CONTRACT['provenance'];PROVENANCE=Provenance(P['run'],int(P['epoch']),P['manifest'],P['source'],tuple(map(tuple,P['environment'])))
@dataclass(frozen=True)
class EncodeOnlyOutput:
    code:int
    message:str
class Threshold:
    def __init__(self):self.initial_calls=self.steps=self.state_calls=self.edge_calls=self.into=self.legacy=0
    def metadata(self):return Metadata('native-values-threshold',build='application-v1')
    def initial_state(self):self.initial_calls+=1;return []
    def step(self,state,input):
        self.steps+=1
        return Transition(list(state)+[input],(EncodeOnlyOutput(7,'\ufeffchanged'),))
    def check_state(self,state):raise RuntimeError('legacy state checker')
    def check_state_into(self,state,sink):
        self.state_calls+=1
        total=min(3,sum(min(3,x.value)for x in state if x is not None))
        sink.append(Check('threshold.bound','failed','at least three')if total>=3 else Check('threshold.bound'));sink.append(Check('state.appended'))
    def check_transition_into(self,before,input,turn,sink):self.edge_calls+=1;sink.append(Check('edge.checked'))
    def old(self,value):self.legacy+=1;raise RuntimeError('legacy encoder')
    encode_state=encode_input=encode_output=old
    def encode_state_into(self,value,out):self.into+=1;vc.encode_into(STATE,value,out)
    def encode_input_into(self,value,out):self.into+=1;vc.encode_into(OPTION,value,out)
    def encode_output_into(self,value,out):self.into+=1;vc.encode_into(OUTPUT,value,out)
    def decode_state(self,data):return vc.decode_canonical(STATE,data,DL)
    def decode_input(self,data):return vc.decode_canonical(OPTION,data,DL)
def report(value): return dict(outcome=value.outcome,divergence_step=value.step,divergence_field=value.field,steps_verified=value.steps_verified,failure_reproduced=value.failure_reproduced,build_matches=value.build_matches)
def config(value): return dict(strategy=value.strategy,seed=None if value.seed is None else str(value.seed),parameters=value.parameters)
def checks(values): return [dict(id=c.id,status=c.status,reason=c.details or None) for c in values]
def normalize(v):
    t=v.trace
    def causal(c): return dict(machine=c.machine,generation=None if c.generation is None else str(c.generation),correlation=c.correlation,logical_time=None if c.logical_time is None else str(c.logical_time),causes=[dict(sequence=str(o.sequence),output=o.output) for o in c.causes])
    return dict(trace=dict(metadata=asdict(t.metadata),config=config(t.config),initial_state=t.initial_state.hex(),initial_checks=checks(t.initial_checks),steps=[dict(input=x.input.hex(),disposition=dict(kind=x.disposition.kind,reason=x.disposition.reason or None),outputs=[y.hex() for y in x.outputs],post_state=x.post_state.hex(),checks=checks(x.checks)) for x in t.steps],termination=dict(kind=t.termination,reason=t.error or None)),checkpoint_after=str(v.checkpoint_after),provenance=dict(run=v.provenance.run,epoch=str(v.provenance.epoch),manifest=v.provenance.manifest,source=v.provenance.source,environment=v.provenance.environment),steps=[dict(sequence=str(x.sequence),state_check_count=x.state_check_count,causal=causal(x.causal)) for x in v.steps])

def context(seq):return CausalContext('worker-a',9,'request-1',10+seq,()if seq==1 else (OutputOrigin(seq-1,0),))
def counts(m):return dict(initial_state=m.initial_calls,reducer=m.steps,state=m.state_calls,transition=m.edge_calls)
mode,*paths=sys.argv[1:]
if mode in('values','transcode'):
    source=None if mode=='values'else paths[0];target=paths[0]if mode=='values'else paths[1]
    receipt=values.run(mode,source,target)
else:
    m=Threshold()
    if mode=='record':
        prefix,=paths;rec=EvidenceRecorder(check_initial(m,[]),0,RunConfig('manual'),RecorderOptions(3,16384,LIMITS),PROVENANCE,ELIMITS)
        state=[]
        for seq in(1,2,3):
            input=vc.Some(1);turn=m.step(state,input);checked=check_turn(m,state,input,turn,seq);before=counts(m);rec.observe(checked,context(seq))
            if counts(m)!=before:raise RuntimeError('checked handoff reexecuted')
            state=turn.state
        v=rec.snapshot();main=counts(m)
        if main!=dict(initial_state=0,reducer=3,state=4,transition=3):raise RuntimeError('check-once counts differ')
        if hasattr(m,'decode_output'):raise RuntimeError('output decoder unexpectedly required')
        v.write_raw_new(prefix+'.v2',limits=ELIMITS);Path(prefix+'.v1').write_bytes(audit_to_bytes(v.trace,limits=LIMITS));Path(prefix+'.json').write_text(dumps(v.trace.to_portable()[0]))
        receipt=dict(evidence=normalize(v),report=report(v.replay(m,limits=ELIMITS)),retained_config=config(v.trace.config),main_counts=main,output_encode_only=True)
    elif mode=='replay':
        source,target=paths
        with open(source,'rb')as f:a=Artifact.read(f,limits=ELIMITS)
        if a.version!=2:raise RuntimeError('expected v2')
        v=a.value;v.write_raw_new(target,limits=ELIMITS);receipt=dict(version=2,evidence=normalize(v),report=report(v.replay(m,limits=ELIMITS)),retained_config=config(v.trace.config))
    else:raise ValueError('unknown operation')
    receipt.update(legacy_encoder_calls=m.legacy,codec_into_calls=m.into)
print(json.dumps(dict(receipt,producer='python',mode=mode,package=str(Path(statelessness.__file__).resolve()))))
