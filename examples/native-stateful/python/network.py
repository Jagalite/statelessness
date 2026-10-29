from pathlib import Path
import sys
from statelessness.core import Metadata,Transition,Check,record,replay
from statelessness.network import Network,NetworkInput,Address,Route
from statelessness.stateful_codec import StatefulCodec,network_schemas
from statelessness.value_codec import U8
from statelessness.trace_json import dumps,loads
class Child:
    def metadata(self):return Metadata('ring-child',build='fixture-v1')
    def initial_state(self):return 0
    def step(self,s,i):return Transition(s+i,(i,))
    def check_state(self,s):return [Check('safe')]
class Wiring:
    def metadata(self):return Metadata('ring-global',build='fixture-v1')
    def routes(self,o):return [Route((o.machine+1)%3,7,o.value)]
    def check_state(self,s):return [Check('total','failed','total exceeds two') if sum(v for _,v in s.machines)>2 else Check('total')]
m=StatefulCodec(Network([(0,Child()),(1,Child()),(2,Child())],Wiring()),network_schemas(U8,U8,U8))
mode,path=sys.argv[1:]
if mode=='record':
 t=record(m,[NetworkInput('local',Address(0,1)),NetworkInput('deliver',0),NetworkInput('deliver',0)])
 Path(path).write_text(dumps(t));print('recorded')
else:
 r=replay(m,loads(Path(path).read_text()))
 assert r.outcome=='exact' and r.failure_reproduced,r
 print('exact failure reproduced')
