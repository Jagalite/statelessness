import unittest
from statelessness.debugger_live import LiveController, ControllerOptions, LiveError, LiveBridge, LiveLimits, EffectOrigin
from statelessness.checked import check_initial,check_turn
from test_debugger import Counter

class LiveTests(unittest.TestCase):
    def controller(self,**kw): return LiveController(2**63+1,2**63,ControllerOptions(pause_and_step=True,**kw))
    def test_pause_is_acknowledged_at_host_boundary(self):
        c=self.controller(); c.external_arrival('a',1); p,item=c.begin_next()
        c.request_pause(2**63+1,2**63)
        with self.assertRaises(LiveError): c.safe_point()
        c.delivered(p,1); dispatch=c.take_dispatch(0)
        with self.assertRaises(LiveError): c.safe_point()
        with self.assertRaisesRegex(LiveError,'Duplicate'): c.take_dispatch(0)
        c.dispatched(dispatch); ack=c.safe_point()
        self.assertEqual((ack.revision,ack.generation),(2**63+1,1))
        with self.assertRaises(LiveError): c.dispatched(dispatch)
        c.authorize_one(2**63+1,2**63+1); c.external_arrival('b',0)
        p,_=c.begin_next(); c.delivered(p,0)
        self.assertEqual(c.safe_point().generation,2)
    def test_foreign_permits_and_uncertain_dispatch(self):
        a=self.controller(); b=self.controller()
        a.external_arrival(1,1); p,_=a.begin_next(); b.external_arrival(2,1); q,_=b.begin_next()
        with self.assertRaises(LiveError): b.delivered(p,0)
        a.delivered(p,1); d=a.take_dispatch(0); a.disconnect()
        with self.assertRaises(LiveError): a.reconnect()
        a.dispatch_failed(d)
        with self.assertRaises(LiveError): a.reconnect()
        b.delivered(q,0)
    def test_overflow_preserves_queue_and_reconnect_origin(self):
        c=self.controller(max_arrivals=1,max_arrival_bytes=2)
        c.external_arrival('retained',2)
        with self.assertRaisesRegex(LiveError,'Overflow'): c.external_arrival('caller retains',1)
        c.disconnect(); c.safe_point(); epoch=c.reconnect()
        self.assertEqual(epoch,2**63+2)
        c.resume(epoch,2**63); p,item=c.begin_next(); self.assertEqual(item,'retained'); c.delivered(p,1)
        d=c.take_dispatch(0); self.assertEqual(d.origin.epoch,2**63+1)
    def test_terminate_during_delivery_accounts_actual_turn(self):
        c=self.controller(detach='Terminate'); c.external_arrival(1,0); p,_=c.begin_next(); c.disconnect()
        c.delivered(p,0)
        self.assertEqual((c.status()['phase'],c.status()['revision']),('Terminated',2**63+1))
    def test_checked_capture_does_not_reexecute(self):
        m=Counter(); initial=check_initial(m,0); bridge=LiveBridge(initial,0,1,limits=LiveLimits(max_events=1))
        t=m.step(0,1); checked=check_turn(m,0,1,t,1); bridge.observe(1,checked)
        self.assertEqual(bridge.health()['recorded_steps'],1)
        self.assertGreater(bridge.health()['event_drops'],0)
        with self.assertRaisesRegex(LiveError,'Duplicate'): bridge.observe(1,checked)
        t=m.step(1,1); checked=check_turn(m,1,1,t,3)
        with self.assertRaisesRegex(LiveError,'Gap'): bridge.observe(3,checked)
        self.assertFalse(bridge.health()['capture_complete'])
        self.assertEqual(bridge.health()['recorded_steps'],1)
    def test_bridge_rejects_metadata_callback_reentrancy(self):
        class Model(Counter):
            bridge=None
            reenter=False
            def metadata(self):
                if self.reenter:
                    with self_test.assertRaisesRegex(LiveError,'already in progress'): self.bridge.pop_event()
                return super().metadata()
        self_test=self; m=Model(); b=LiveBridge(check_initial(m,0),0,1); m.bridge=b
        t=m.step(0,1); checked=check_turn(m,0,1,t,1); m.reenter=True; b.observe(1,checked)
        self.assertEqual(b.health()['recorded_steps'],1)
