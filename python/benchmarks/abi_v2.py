"""Explicit, currently unexecuted ABI 2 workload driver.

PYTHONPATH=python/src python python/benchmarks/abi_v2.py --library PATH
Outputs raw timing/callback samples, not a performance qualification receipt.
Whole-process RSS and native allocation/retention measurements need an external
profiler; Python counters alone do not measure the Rust allocator.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
import platform
import statistics
import time
from statelessness import Check, Metadata, SearchConfig, Transition
from statelessness.native import NativeEngine

MASK = (1 << 64) - 1
PARAMETERS = {"states": 32, "inputs": [1, 2], "trace_steps": 64,
              "large_payload_bytes": 65536, "expensive_iterations": 4096,
              "failure_state": 24, "seed": 0}

@dataclass
class State:
    value: int
    payload: bytearray

class Counter:
    def __init__(self, kind, fail=False):
        self.payload = 65536 if kind == 'large' else 0
        self.iterations = 4096 if kind == 'expensive' else 0
        self.fail, self.callbacks, self.sink = fail, 0, 0
    def hit(self): self.callbacks += 1
    def metadata(self): return Metadata('abi-v2-matched-counter', build='benchmark-v1')
    def initial_state(self): self.hit(); return State(0, bytearray(self.payload))
    def clone_state(self, state): self.hit(); return State(state.value, state.payload.copy())
    def clone_input(self, value): self.hit(); return value
    def clone_output(self, value): self.hit(); return value
    def step(self, state, input):
        self.hit(); return Transition(State((state.value+input)%32, state.payload), (state.value,))
    def check_state(self, state):
        self.hit(); digest = state.value
        for i in range(self.iterations): digest = ((digest ^ i) * 0x9e3779b97f4a7c15 + 1) & MASK
        self.sink ^= digest
        if not 0 <= state.value < 32 or len(state.payload) != self.payload:
            return (Check('valid', 'failed', 'invalid state'),)
        if self.fail and state.value >= 24:
            return (Check('bound', 'failed', 'state reached failure region'),)
        return (Check('valid'),)
    def check_transition(self, before, input, transition):
        self.hit()
        if transition.state.value != (before.value+input)%32 or transition.outputs != (before.value,):
            return (Check('transition', 'failed', 'inconsistent transition'),)
        return (Check('transition'),)
    def inputs(self, state): self.hit(); return (1, 2)
    def equal_states(self, left, right): self.hit(); return left.value == right.value
    def hash_state(self, state): self.hit(); return state.value
    def estimated_state_bytes(self, state): self.hit(); return 4+len(state.payload)
    def encode_state(self, state): self.hit(); return state.value.to_bytes(4,'little')+bytes(state.payload)
    def decode_state(self, data):
        self.hit()
        if len(data) != 4+self.payload: raise ValueError('state length')
        return State(int.from_bytes(data[:4],'little'), bytearray(data[4:]))
    def encode_input(self, value): self.hit(); return bytes([value])
    def decode_input(self, data):
        self.hit()
        if len(data) != 1: raise ValueError('input length')
        return data[0]
    def encode_output(self, value): self.hit(); return value.to_bytes(4,'little')
    def generate(self, state, rng): self.hit(); return rng.index(2)+1
    def is_enabled(self, state, input): self.hit(); return input in (1,2)
    def equal_inputs(self, left, right): self.hit(); return left == right
    def simpler_inputs(self, input): self.hit(); return (1,) if input == 2 else ()


def workload(session, model, operation):
    """Prepare reusable inputs outside the timer and return work plus teardown."""
    initial = State(0, bytearray(model.payload)); inputs = [1,2]*32
    artifact = session.record_binary(inputs,64) if operation == 'replay' else None
    failure = session.fuzz(seed=0,cases=1,max_steps=64,max_transitions=256) if operation == 'shrink' else None
    if failure is not None and failure.status != 1: raise AssertionError('missing seed failure')
    def work():
        if operation == 'observation':
            checks = session.check_observed(initial,2,Transition(State(2,initial.payload),(0,)),1)
            assert len(checks)==2 and all(c.status=='passed' for c in checks)
        elif operation == 'bfs':
            report=session.enumerate_states(SearchConfig(64,256,64))
            assert (report.states,report.transitions,report.termination)==(32,64,'graph_exhausted')
        elif operation == 'record': session.record_binary(inputs,64)
        elif operation == 'replay':
            result=session.replay(artifact)
            assert (result.outcome,result.steps_verified)==('exact',64)
        elif operation == 'fuzz':
            with session.fuzz(seed=0,cases=1,max_steps=64,max_transitions=256) as result:
                assert result.status==1
        elif operation == 'shrink':
            with session.shrink(failure,max_attempts=256,max_transitions=4096) as result:
                assert result.scalar(19)==1
        elif operation == 'ring':
            with session.ring(initial,max_steps=16,max_retained_bytes=16*1024*1024) as ring:
                before=initial
                for input in inputs:
                    after=State((before.value+input)%32,before.payload)
                    ring.observe(before,input,Transition(after,(before.value,)))
                    before=after
                assert ring.snapshot()
        else: raise ValueError(operation)
    return work, (failure.close if failure else lambda:None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True)
    parser.add_argument('--iterations',type=int,default=5)
    parser.add_argument('--samples',type=int,default=7)
    args=parser.parse_args()
    if args.iterations<1 or args.samples<2: parser.error('positive iterations and at least two samples required')
    engine=NativeEngine(args.library); cells=[]
    for kind in ('cheap','large','expensive'):
        for operation in ('observation','bfs','record','replay','fuzz','shrink','ring'):
            model=Counter(kind,operation in ('fuzz','shrink'))
            with engine.session(model) as session:
                work,cleanup=workload(session,model,operation)
                try:
                    baseline_roots=session.retained_roots; times=[]; callbacks=[]
                    for _ in range(args.samples):
                        model.callbacks=0; start=time.perf_counter_ns()
                        for _ in range(args.iterations): work()
                        times.append((time.perf_counter_ns()-start)/args.iterations)
                        callbacks.append(model.callbacks/args.iterations)
                        assert session.retained_roots==baseline_roots
                    cells.append({'model':kind,'workload':operation,'samples_ns':times,
                                  'median_ns':statistics.median(times),'callbacks_per_operation':callbacks,
                                  'retained_roots_after_operation':baseline_roots})
                finally: cleanup()
    print(json.dumps({'schema':'stateless.binding-raw-timing.v1','engine_family':'rust-shared',
          'artifact_sha256':engine.sha256,'runtime':platform.python_version(),'parameters':PARAMETERS,
          'qualification':'incomplete: external allocation/RSS evidence and baseline budgets required',
          'cells':cells},indent=2))

if __name__=='__main__': main()
