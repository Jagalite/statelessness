"""Installed application: explicitly fence completed work and reject stale delivery."""
from statelessness.lifecycle import IndexedLifecycleMonitor,RetentionLimits,ScopedKey,Event
from statelessness.core import Metadata,Disposition
from statelessness.value_codec import U8
class DeliveryBarrier:
    def metadata(self):return Metadata('delivery-barrier-example')
    def input_events(self,input,disposition):return input
    def output_events(self,outputs):return outputs
m=IndexedLifecycleMonitor(DeliveryBarrier(),RetentionLimits(2,2,8),key_schema=U8)
k=ScopedKey(2,3,7);h=m.advance(m.initial_state(),[Event('begin',k,10)],[Event('settle',k),Event('fence',time=3)],Disposition())
h=m.decode_history(m.encode_history(h))
h=m.advance(h,[Event('settle',k)],[],Disposition())
assert h.violations[-1].code=='retired_epoch_reference'
assert m.check_state(h)[0].status=='failed'
print(m.encode_history(h).hex())
