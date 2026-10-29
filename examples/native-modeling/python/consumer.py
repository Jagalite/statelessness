"""Installed teaching application: deterministic reducer, external async effects."""
import asyncio
import json
from statelessness import *


def rejects(callback, text):
    try: callback()
    except Exception as error:
        assert text in str(error), str(error)
    else: raise AssertionError('expected rejection: '+text)


def controls():
    d = Domain(DomainDescriptor('actions', 'ordered duplicates carry weight', 4))
    d.extend([3, 1, 3, 7]); assert d.entries == (3, 1, 3, 7)
    rejects(lambda: d.push(9), 'limit')
    a, b = Rng(42), Rng(42)
    for _ in range(100): assert d.sample_indexed(a) == d.entries[b.index(4)]
    assert Domain.product([1, 2], [4, 5], 4, lambda a,b:a+b) == [5, 6, 6, 7]
    rejects(lambda: Domain.product([1,2], [4,5], 3, lambda *_:1/0), 'limit')
    empty = Domain(DomainDescriptor('empty', '', 0)); before=a.state
    assert empty.sample_indexed(a) is EXHAUSTED and a.state == before
    owned = Domain(DomainDescriptor('owned', '', 2)); value=[1]; owned.push(value); value.append(2)
    escaped=owned.entries; escaped[0].append(3); assert owned.entries == ([1],)
    assert check_lazy('lazy', True, lambda:1/0) == Check('lazy')
    assert check_lazy('lazy', False, lambda:'detail') == Check('lazy', 'failed', 'detail')
    rejects(lambda: check_lazy('lazy', False, lambda:1/0), 'division')
    rejects(lambda: declared_properties([PropertyMetadata('p'),PropertyMetadata('p','transition')]), 'duplicate')
    rejects(lambda: PropertyMetadata('p','wrong'), 'phase')
    rejects(lambda: DomainDescriptor('bad','',-1), 'max_entries')
    assert owned.into_entries() == [[1]] and owned.entries == ()
    nullable=Domain(DomainDescriptor('null','',1)); nullable.push(None)
    assert nullable.sample_indexed(Rng(0)) is None
    right=[4,5]
    def mutate(a,b): right.append(6); return a+b
    assert Domain.product([1,2],right,4,mutate)==[5,6,6,7]
    nested=Domain(DomainDescriptor('reentrant','',1))
    class Reentrant:
        def __deepcopy__(self,memo): nested.push(1); return self
    rejects(lambda:nested.push(Reentrant()),'limit'); assert nested.entries==(1,)
    class AsyncDefinition:
        def metadata(self): return Metadata('async')
        async def step(self,s,i): return Transition(s)
    rejects(lambda:ModelAdapter(AsyncDefinition(),initial_default=lambda:0,unchecked=True),'synchronous')



class Application:
    def metadata(self): return Metadata('native-modeling-application', build='teaching-v1')
    # Same reducer called by production host and record/replay verification.
    def step(self, state, event):
        if event == 1: return Transition(1, (1,))
        if event == 3: return Transition(0, (3,))
        if event == 2 and state == 1: return Transition(2, (2,))
        return Transition(state, (), Disposition('rejected', 'not busy'))
    def check_state(self, state): return [check_lazy('state.range', 0<=state<=2, lambda:f'invalid {state}')]
    def inputs(self, state): return (1,2,3)


async def main():
    controls()
    raw=Application()
    rejects(lambda: ModelAdapter(raw), 'no properties')
    model=ModelAdapter(raw, properties=[PropertyMetadata('state.range')], initial_default=lambda:0)
    rejects(lambda: ModelAdapter(model,properties=[PropertyMetadata('state.range')],initial_default=lambda:0),'conflicts')
    generated=model.automatic(AutoOptions(2)); rng=Rng(42); before=rng.state
    rejects(lambda: generated.generate(0,rng),'max_candidates'); assert rng.state==before
    model=model_codec(state=U8, input=U8, output=U8).bind(model)
    assert model.encode_state(2)==b'\x02' and model.decode_state(b'\x02')==2
    rejects(lambda:model.decode_state(b'\x02\x00'),'trailing')
    for events in ((1,3,1,2),(1,2)):
        state=model.initial_state(); observed=[]
        for event in events:
            turn=model.step(state,event); observed.append(turn); state=turn.state
        trace=record(model,events)
        assert replay(model,trace).outcome=='exact'
        assert [model.decode_state(s.post_state) for s in trace.steps] == [t.state for t in observed]
    # Nondeterminism (scheduler/network/job) belongs here, outside step().
    state=model.initial_state(); start=model.step(state,1)
    assert start.outputs==(1,)
    await asyncio.sleep(0)
    completion=2; finish=model.step(start.state,completion)
    assert finish.state==2 and replay(model,record(model,[1,completion])).outcome=='exact'
    print(json.dumps({'engine':'python','controls':'passed','hosts':['ui','background','async'],'turns':8}))

if __name__=='__main__': asyncio.run(main())
