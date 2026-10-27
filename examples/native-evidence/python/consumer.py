"""Installed checked handoff and bounded runtime Recorder application."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import statelessness
from statelessness import (Check, Metadata, Transition, CheckPolicy, check_initial, check_turn, RecorderOptions,
    EvidenceRecorder, EvidenceLimits, Provenance, CausalContext, OutputOrigin, StepEvidence, TraceV2, Artifact,
    ExportPolicy, diagnostic_json, export_diagnostic_new)
from statelessness.trace_binary import RunConfig, BinaryLimits, audit_to_bytes
from statelessness.trace_json import dumps
from dataclasses import asdict
from io import BytesIO

LIMITS = BinaryLimits(max_total_bytes=16384, max_frame_bytes=4096, max_blob_bytes=64, max_string_bytes=256,
    max_steps=16, max_checks_per_step=16, max_outputs_per_step=16, max_parameters=32, max_items=256)


class Threshold:
    def __init__(self, overrun=False): self.legacy = self.into = self.decode = self.steps = 0; self.leases = []; self.overrun = overrun; self.initial_calls = self.state_calls = self.edge_calls = 0
    def metadata(self): return Metadata("native-evidence-threshold", build="application-v1")
    def initial_state(self): self.initial_calls += 1; return 0
    def step(self, state, input):
        self.steps += 1
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, (("x" * 65 if self.overrun else "changed"),) if input else ())
    def check_state(self, state): raise RuntimeError("legacy state checker invoked")
    def check_transition(self, before, input, transition): raise RuntimeError("legacy edge checker invoked")
    def check_state_into(self, state, sink):
        self.state_calls += 1
        sink.append(Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"))
        sink.append(Check("state.appended"))
    def check_transition_into(self, before, input, transition, sink):
        self.edge_calls += 1; sink.append(Check("edge.checked"))
    def legacy_encode(self, value): self.legacy += 1; raise RuntimeError("legacy encoder invoked")
    encode_state = encode_input = encode_output = legacy_encode
    def encode(self, value, sink):
        self.into += 1; self.leases.append(sink)
        for byte in str(value).encode("ascii"): sink.append(bytes((byte,)))
    encode_state_into = encode_input_into = encode_output_into = encode
    def decode_state(self, data): self.decode += 1; return int(data.decode("ascii"))
    def decode_input(self, data):
        self.decode += 1
        if data not in (b"0", b"1"): raise ValueError("expected zero or one")
        return int(data)


ELIMITS = EvidenceLimits(LIMITS,1024,8,128,4,8)
PROVENANCE = Provenance("installed-evidence-run",7,"manifest-v1","consumer-source",(("region","test"),("note","é")))
def report(value): return dict(outcome=value.outcome,divergence_step=value.step,divergence_field=value.field,steps_verified=value.steps_verified,failure_reproduced=value.failure_reproduced,build_matches=value.build_matches)
def config(value): return dict(strategy=value.strategy,seed=None if value.seed is None else str(value.seed),parameters=value.parameters)
def checks(values): return [dict(id=c.id,status=c.status,reason=c.details or None) for c in values]
def normalize(v):
    t=v.trace
    def causal(c): return dict(machine=c.machine,generation=None if c.generation is None else str(c.generation),correlation=c.correlation,logical_time=None if c.logical_time is None else str(c.logical_time),causes=[dict(sequence=str(o.sequence),output=o.output) for o in c.causes])
    return dict(trace=dict(metadata=asdict(t.metadata),config=config(t.config),initial_state=t.initial_state.hex(),initial_checks=checks(t.initial_checks),steps=[dict(input=x.input.hex(),disposition=dict(kind=x.disposition.kind,reason=x.disposition.reason or None),outputs=[y.hex() for y in x.outputs],post_state=x.post_state.hex(),checks=checks(x.checks)) for x in t.steps],termination=dict(kind=t.termination,reason=t.error or None)),checkpoint_after=str(v.checkpoint_after),provenance=dict(run=v.provenance.run,epoch=str(v.provenance.epoch),manifest=v.provenance.manifest,source=v.provenance.source,environment=v.provenance.environment),steps=[dict(sequence=str(x.sequence),state_check_count=x.state_check_count,causal=causal(x.causal)) for x in v.steps])
def context(seq, full=True): return CausalContext("worker-a" if full else None,9 if full else None,"request-1" if full else None,10+seq if full else None,() if seq==1 else (OutputOrigin(seq-1,0),))
def opts(n=3): return RecorderOptions(n,16384,LIMITS)
def attach(m,n=3,limits=ELIMITS,provenance=PROVENANCE):return EvidenceRecorder(check_initial(m,0),0,RunConfig("manual"),opts(n),provenance,limits)
def token(m,before,seq):return check_turn(m,before,1,Transition(before+1,("changed",)),seq)
def counts(m):return dict(initial_state=m.initial_calls,reducer=m.steps,state=m.state_calls,transition=m.edge_calls)
def rejected(f,contains=None):
    try:f()
    except Exception as error:
        if contains and contains not in str(error).lower():raise
        return
    raise RuntimeError("expected rejection")
POLICY=ExportPolicy(2,4096,("threshold.bound",))
def controls(v,prefix):
    for kind in ("metadata","cause","trace"):
        m=Threshold();l=replace(ELIMITS,max_metadata_bytes=70 if kind=="metadata" else 256,max_total_causes=1 if kind=="cause" else 8)
        rec=attach(m,1 if kind=="trace" else 3,l,Provenance())
        for seq in range(1,3 if kind=="trace" else 4):rec.observe(token(m,seq-1,seq),context(seq,False) if kind=="cause" else CausalContext())
        snap=rec.snapshot();snap.validate(l)
        expected=2 if kind=="cause" else 1
        if snap.checkpoint_after!=expected or len(snap.steps)!=len(snap.trace.steps) or len(snap.trace.initial_checks)!=2 or any(c.id=="edge.checked" for c in snap.trace.initial_checks) or snap.replay(Threshold(),limits=l).outcome!="exact":raise RuntimeError("coordinated eviction failed")
    m=Threshold();l=replace(ELIMITS,max_metadata_bytes=62);rec=attach(m,3,l,Provenance());rec.observe(token(m,0,1),CausalContext())
    rejected(lambda:rec.observe(token(m,1,2),CausalContext(causes=(OutputOrigin(1,99),))),"output")
    snap=rec.snapshot();snap.validate(l)
    if not rec.is_frozen() or snap.checkpoint_after!=0 or len(snap.steps)!=1:raise RuntimeError("invalid cause corrupted prefix")
    changed=replace(v,steps=(replace(v.steps[0],state_check_count=1),)+v.steps[1:]);negative=changed.replay(Threshold(),limits=ELIMITS)
    if negative.outcome!="diverged" or negative.field!="check_phase" or negative.step!=1 or negative.steps_verified!=0 or negative.failure_reproduced:raise RuntimeError("phase comparison bypassed")
    upgraded=TraceV2.verify_upgrade(Threshold(),v.trace,PROVENANCE,0,limits=ELIMITS)
    if upgraded.replay(Threshold(),limits=ELIMITS).outcome!="exact" or any(x.causal.causes or x.causal.machine is not None for x in upgraded.steps):raise RuntimeError("upgrade fabricated causal labels")
    artifact=Artifact(v);text=diagnostic_json(artifact,POLICY)
    if diagnostic_json(artifact,replace(POLICY,max_bytes=len(text)))!=text:raise RuntimeError("diagnostic exact fit")
    rejected(lambda:diagnostic_json(artifact,replace(POLICY,max_bytes=len(text)-1)))
    zero=__import__('json').loads(diagnostic_json(artifact,replace(POLICY,max_steps=0)))
    if len(zero['observations'])!=1 or not zero['truncated']:raise RuntimeError("zero-step diagnostic omitted initial")
    path=prefix+".diagnostic";export_diagnostic_new(artifact,path,POLICY);rejected(lambda:export_diagnostic_new(artifact,path,POLICY))
    if Path(path).read_bytes()!=text:raise RuntimeError("overwrite changed diagnostic")
    rejected(lambda:v.write_raw_new(prefix+".v2",limits=ELIMITS))
    before=counts(m);legacy=Artifact.read(BytesIO(audit_to_bytes(v.trace,limits=LIMITS)),limits=ELIMITS)
    if legacy.version!=1 or legacy.check_phase(0,0)!="initial_state" or legacy.check_phase(1,0) is not None or counts(m)!=before:raise RuntimeError("passive v1 phase invention")
    return dict(check_once=True,coordinated_metadata_eviction=True,coordinated_cause_eviction=True,coordinated_trace_eviction=True,source_index_before_eviction=True,phase_divergence=True,verified_upgrade=True,diagnostic_exact=True,diagnostic_caps=True,exclusive_private_files=True,no_overwrite=True,passive_v1_no_execution=True),text.decode()

mode,*paths=sys.argv[1:];m=Threshold()
if mode=="record":
    path,=paths;rec=attach(m)
    for seq in (1,2,3):
        actual=m.step(seq-1,1);checked=check_turn(m,seq-1,1,actual,seq);before=counts(m);rec.observe(checked,context(seq))
        if counts(m)!=before:raise RuntimeError("evidence handoff reexecuted")
    v=rec.snapshot();main_counts=counts(m)
    if main_counts!=dict(initial_state=0,reducer=3,state=4,transition=3):raise RuntimeError("check-once counts differ")
    v.write_raw_new(path+".v2",limits=ELIMITS);Path(path+".v1").write_bytes(audit_to_bytes(v.trace,limits=LIMITS));Path(path+".json").write_text(dumps(v.trace.to_portable()[0]))
    c,text=controls(v,path);receipt=dict(evidence=normalize(v),report=report(v.replay(m,limits=ELIMITS)),retained_config=config(v.trace.config),main_counts=main_counts,controls=c,diagnostic_text=text)
elif mode=="read-v1":
    path,=paths
    with open(path,'rb') as f:a=Artifact.read(f,limits=ELIMITS)
    if a.version!=1:raise RuntimeError("expected original v1 artifact")
    receipt=dict(version=1,initial_phase=a.check_phase(0,0),turn_phase=a.check_phase(1,0),execution_calls=m.steps+m.state_calls+m.edge_calls+m.initial_calls,retained_config=config(a.trace.config))
elif mode in ("replay","upgrade"):
    path,out=paths
    with open(path,'rb') as f:a=Artifact.read(f,limits=ELIMITS)
    v=a.value if mode=="replay" else TraceV2.verify_upgrade(m,a.trace,PROVENANCE,0,limits=ELIMITS)
    if not isinstance(v,TraceV2):raise RuntimeError("expected v2 evidence")
    v.write_raw_new(out,limits=ELIMITS);receipt=dict(version=2,evidence=normalize(v),report=report(v.replay(m,limits=ELIMITS)),retained_config=config(v.trace.config))
elif mode=="diagnostic":
    path,out=paths
    with open(path,'rb') as f:a=Artifact.read(f,limits=ELIMITS)
    text=diagnostic_json(a,POLICY);export_diagnostic_new(a,out,POLICY);rejected(lambda:export_diagnostic_new(a,out,POLICY))
    if Path(out).read_bytes()!=text:raise RuntimeError("diagnostic overwritten")
    receipt=dict(diagnostic_text=text.decode(),execution_calls=m.steps+m.state_calls+m.edge_calls+m.initial_calls,no_overwrite=True)
else:raise ValueError("unknown mode")
print(json.dumps(dict(receipt,producer="python",mode=mode,package=str(Path(statelessness.__file__).resolve()),legacy_encoder_calls=m.legacy,codec_into_calls=m.into)))
