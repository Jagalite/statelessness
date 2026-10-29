import unittest
from statelessness.core import Metadata,Check,Transition,Disposition,ModelError,_state_checks
from statelessness.lifecycle import *
from statelessness.network import Network,NetworkInput,NetworkState,Address,Route,Message,QueuePolicy
from statelessness.composition import Pair,PairInput,Address as PairAddress
from statelessness.value_codec import U8

class Spec:
    def metadata(self):return Metadata('independent-lifecycle')
    def input_events(self,i,d):return i
    def output_events(self,o):return o
class Child:
    def metadata(self):return Metadata('token')
    def initial_state(self):return 0
    def inputs(self,s):return (1,)
    def step(self,s,i):return Transition(s+i,(i,),Disposition('ignored','observed'))
    def check_state(self,s):return (Check('nonnegative','passed' if s>=0 else 'failed','' if s>=0 else 'negative'),)
class Wiring:
    def metadata(self):return Metadata('ring')
    def routes(self,o):return (Route((o.machine+1)%3,7,o.value),)
    def check_state(self,s):return (Check('sum'),)
    def messages(self,o):return (PairAddress('right' if o.side=='left' else 'left',o.value),)

class StatefulTests(unittest.TestCase):
    def test_deadlines_none_and_prior(self):
        m=LifecycleMonitor(Spec())
        h=m.advance(m.initial_state(),[Event('begin',None,0)],[],Disposition())
        self.assertEqual(m.check_state(h)[0].status,'failed')
        h=m.advance(m.initial_state(),[Event('begin',7,10),Event('tick',time=11),Event('cancel',7)],[],Disposition())
        self.assertIn('required settlement missing before time advance',h.violations)
        h=m.advance(m.initial_state(),[Event('begin',7,10),Event('tick',time=10)],[Event('settle',7)],Disposition())
        self.assertEqual(m.check_state(h)[0].status,'passed')
    def test_legacy_and_indexed_codecs(self):
        for cls,size in ((LifecycleMonitor,20),(IndexedLifecycleMonitor,29)):
            m=cls(Spec(),key_schema=U8);h=m.initial_state();b=m.encode_history(h)
            self.assertEqual(b,(b'\1'+bytes(28)) if cls is IndexedLifecycleMonitor else bytes(20));self.assertEqual(m.decode_history(b),h)
            k=ScopedKey(2,3,7) if cls is IndexedLifecycleMonitor else 7
            h=m.advance(h,[Event('begin',k,10)],[],Disposition());b=m.encode_history(h)
            self.assertEqual(len(b),57 if cls is IndexedLifecycleMonitor else 31);self.assertEqual(m.decode_history(b),h)
            with self.assertRaises(ModelError):m.decode_history(b+b'\0')
    def test_fences_reuse_retention_restart(self):
        m=IndexedLifecycleMonitor(Spec(),RetentionLimits(2,2,8),key_schema=U8);h=m.initial_state()
        for epoch in range(1000):
            k=ScopedKey(epoch,0,7)
            h=m.advance(h,[Event('begin',k,10),Event('acquire',k)],[Event('settle',k),Event('release',k),Event('fence',time=epoch+1)],Disposition())
            self.assertEqual(h.retention()['historical_obligations'],0)
        h=m.decode_history(m.encode_history(h));h=m.advance(h,[Event('settle',ScopedKey(0,0,7))],[],Disposition())
        self.assertEqual(h.violations[-1].code,'retired_epoch_reference')
        h=m.advance(m.initial_state(),[Event('begin',ScopedKey(0,0,1),10),Event('fence',time=1)],[],Disposition())
        self.assertEqual(h.retired_before,0);self.assertEqual(h.violations[-1].code,'fence_pending_obligation')
    def test_duplicate_publication_difference(self):
        for cls in (LifecycleMonitor,IndexedLifecycleMonitor):
            m=cls(Spec());k=ScopedKey(0,0,1) if m.indexed else 1
            h=m.advance(m.initial_state(),[Event('begin',k,10)],[Event('settle',k),Event('publish',k),Event('publish',k)],Disposition())
            self.assertEqual(bool(h.violations),m.indexed)
    def test_network_fifo_cycle_drop_and_saturation(self):
        n=Network([(2,Child()),(0,Child()),(1,Child())],Wiring(),QueuePolicy(max_pending=2,max_per_channel=2,allow_drop=True,allow_duplicate=True))
        s=n.initial_state();t=n.step(s,NetworkInput('local',Address(0,1)));self.assertEqual(s.pending,());self.assertEqual(t.disposition.kind,'ignored');s=t.state
        self.assertEqual([c.id for c in _state_checks(n,s)],['machine/0::nonnegative','machine/1::nonnegative','machine/2::nonnegative','global::sum'])
        d=n.step(s,NetworkInput('duplicate',0)).state
        with self.assertRaisesRegex(ModelError,'FIFO'):n.step(d,NetworkInput('deliver',1))
        with self.assertRaisesRegex(ModelError,'incomplete'):n.step(d,NetworkInput('duplicate',0))
        self.assertEqual(len(n.step(d,NetworkInput('drop',1)).state.pending),1)
        for _ in range(100):s=n.step(s,NetworkInput('deliver',0)).state;self.assertEqual(len(s.pending),1)
        self.assertEqual(sum(v for _,v in s.machines),101)
    def test_pair_no_drain(self):
        p=Pair(Child(),Child(),Wiring(),1);s=p.step(p.initial_state(),PairInput('local',PairAddress('left',1))).state
        self.assertEqual((s.left,s.right,len(s.pending)),(1,0,1));s=p.step(s,PairInput('deliver',0)).state
        self.assertEqual((s.left,s.right,len(s.pending)),(1,1,1))
if __name__=='__main__':unittest.main()

class CodecIdentityTests(unittest.TestCase):
    def test_network_codec_routing_and_queue_identity(self):
        from statelessness.stateful_codec import StatefulCodec,network_schemas
        m=StatefulCodec(Network([(0,Child()),(1,Child()),(2,Child())],Wiring()),network_schemas(U8,U8,U8))
        h=m.step(m.initial_state(),NetworkInput('local',Address(0,1))).state
        self.assertEqual(m.decode_state(m.encode_state(h)),h)
        self.assertEqual(m.decode_input(m.encode_input(NetworkInput('deliver',0))),NetworkInput('deliver',0))
        changed=NetworkState(h.machines,(Message(0,Route(2,7,1)),))
        self.assertNotEqual(m.encode_state(h),m.encode_state(changed))
        self.assertFalse(m.equal_states(h,changed))
    def test_lossless_historical_text(self):
        m=LifecycleMonitor(Spec(),key_schema=U8);h=History(violations=['time_backwards'])
        self.assertEqual(m.decode_history(m.encode_history(h)).violations,['time_backwards'])

class HandoffTests(unittest.TestCase):
    def test_acknowledged_transfer_and_early_grant_fault(self):
        class Role:
            def __init__(self,id,faulty):self.id,self.faulty=id,faulty
            def initial_state(self):return int(self.id==20)
            def step(self,s,i):
                if self.id==10 and i==1:return Transition(1,(10,11) if self.faulty else (10,))
                if self.id==10 and i==4:return Transition(2,(11,))
                if self.id==20 and i==2:return Transition(0,(12,))
                if self.id==30 and i==3:return Transition(1,())
                return Transition(s,(),Disposition('ignored','not applicable'))
            def check_state(self,s):return []
        class Routes:
            def routes(self,o):
                destination,input={10:(20,2),11:(30,3),12:(10,4)}[o.value]
                return [Route(destination,0,input)]
            def check_state(self,s):
                return [Check('unique-owner','failed','two owners') if s.machine(20)+s.machine(30)>1 else Check('unique-owner')]
        def net(fault):return Network([(id,Role(id,fault)) for id in (10,20,30)],Routes())
        safe=net(False);s=safe.step(safe.initial_state(),NetworkInput('local',Address(10,1))).state
        for _ in range(3):s=safe.step(s,NetworkInput('deliver',0)).state;self.assertEqual(_state_checks(safe,s)[-1].status,'passed')
        self.assertEqual((s.machine(20),s.machine(30)),(0,1))
        bad=net(True);s=bad.step(bad.initial_state(),NetworkInput('local',Address(10,1))).state
        s=bad.step(s,NetworkInput('deliver',1)).state
        self.assertEqual(_state_checks(bad,s)[-1],Check('global::unique-owner','failed','two owners'))
    def test_routed_payload_snapshot(self):
        payload=[1]
        class Mutable(Child):
            def step(self,s,i):return Transition(s,(payload,))
        class Router(Wiring):
            def routes(self,o):return [Route(1,0,o.value)]
        n=Network([(0,Mutable()),(1,Mutable())],Router());t=n.step(n.initial_state(),NetworkInput('local',Address(0,0)))
        payload.append(2);self.assertEqual(t.state.pending[0].route.input,[1])
        t.outputs[0].value.append(3);self.assertEqual(t.state.pending[0].route.input,[1])
