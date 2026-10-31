from pathlib import Path
import sys,os
from statelessness.core import Metadata,Transition,Check,record,replay
from statelessness.network import Network,NetworkInput,Address,Route
from statelessness.stateful_codec import StatefulCodec,network_schemas
from statelessness.value_codec import U8
from statelessness.trace_json import dumps,loads
from statelessness.audit_record import record_audit
from statelessness.trace_binary import RunConfig,audit_to_bytes
class Child:
    def metadata(self):return Metadata('ring-child',build='fixture-v1')
    def initial_state(self):return 0
    def step(self,s,i):return Transition(s+i,(i,))
    def check_state(self,s):return [Check('safe')]
class Wiring:
    def __init__(self,weaken=False):self.weaken=weaken
    def metadata(self):return Metadata('ring-global',build='fixture-v1')
    def routes(self,o):return [Route((o.machine+1)%3,7,o.value)]
    def check_state(self,s):return [Check('total','failed','total exceeds two') if sum(v for _,v in s.machines)>(3 if self.weaken else 2) else Check('total')]
def model(weaken=False):
 return StatefulCodec(Network([(0,Child()),(1,Child()),(2,Child())],Wiring(weaken)),network_schemas(U8,U8,U8))
def inputs():return [NetworkInput('local',Address(0,1)),NetworkInput('deliver',0),NetworkInput('deliver',0)]
def expected(m,t,weak=False):
 # This oracle is specified independently of the network checks: all machines
 # process one message, one message remains, and global::total has this status.
 if len(t.steps)!=3 or t.termination!=('completed' if weak else 'property_failed'):return False
 state=m.decode_state(t.steps[-1].post_state)
 check=Check('global::total') if weak else Check('global::total','failed','total exceeds two')
 return state.machines==((0,1),(1,1),(2,1)) and len(state.pending)==1 and check in t.steps[-1].checks
def require_expected(m,t,weak=False):
 if not expected(m,t,weak):raise RuntimeError('independent expected network outcome not observed')
m=model()
mode,path=sys.argv[1:]
if mode=='record-audit':
 t=record_audit(m,inputs(),RunConfig(strategy='stateful-fixture'))
 require_expected(m,t)
 Path(path).write_bytes(audit_to_bytes(t));print('audit recorded')
elif mode=='record':
 t=record(m,inputs())
 require_expected(m,t)
 Path(path).write_text(dumps(t));print('recorded')
elif mode=='control':
 weak=model(True);t=record(weak,inputs())
 require_expected(weak,t,True)
 if expected(weak,t):raise RuntimeError('weakened property unexpectedly satisfied failure oracle')
 print('network-control-detected')
elif mode=='compare-cross-build':
 t=loads(Path(path).read_text()); own=m.metadata()
 require_expected(m,t)
 if not (t.metadata.build==os.environ['STATEFUL_PRODUCER_BUILD'] and own.build==os.environ['STATEFUL_CONSUMER_BUILD'] and t.metadata.build!=own.build):
  raise RuntimeError('cross-build identity mismatch')
 strict=replay(m,t)
 if strict.outcome!='incompatible' or strict.build_matches or strict.steps_verified!=0 or strict.failure_reproduced: raise RuntimeError(f'strict replay did not reject different builds before execution: {strict!r}')
 r=replay(m,t,allow_build_mismatch=True)
 if r.outcome!='exact' or r.build_matches or r.steps_verified!=3 or not r.failure_reproduced: raise RuntimeError(f'cross-build observations did not match: {r!r}')
 print('STATEFUL_COMPARISON {"strict_outcome":"incompatible","strict_build_matches":false,"strict_steps_verified":0,"strict_failure_reproduced":false,"observation_outcome":"exact","observation_build_matches":false,"observation_steps_verified":3,"observation_failure_reproduced":true}')
elif mode=='replay':
 t=loads(Path(path).read_text());require_expected(m,t)
 r=replay(m,t)
 if r.outcome!='exact' or not r.failure_reproduced: raise RuntimeError(f'exact failure was not reproduced: {r!r}')
 print('exact failure reproduced')
else: raise ValueError('unknown mode')
