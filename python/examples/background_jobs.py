"""Explore a job reducer and run it with a controlled asynchronous host.

Run after installing ./python: python examples/background_jobs.py
The gates simulate external work; no network or real service is contacted.
"""
import asyncio
from dataclasses import dataclass

from statelessness import Check, Disposition, Metadata, Transition, enumerate_states


@dataclass(frozen=True)
class State:
    active: int | None = None
    used: frozenset[int] = frozenset()
    accepted_stale: bool = False


def reduce(state, event, *, accept_stale=False):
    """Pure application policy; outputs describe work for an external host."""
    kind, job = event
    if kind == 'start':
        if state.active is not None or job in state.used:
            return Transition(state, disposition=Disposition('ignored', 'busy or reused identifier'))
        return Transition(State(job, state.used | {job}), (('run', job),))
    if kind == 'cancel':
        if state.active is None:
            return Transition(state, disposition=Disposition('ignored', 'idle'))
        return Transition(State(None, state.used), (('cancel', state.active),))
    if kind == 'completed':
        if state.active != job and not accept_stale:
            return Transition(state, disposition=Disposition('ignored', 'stale completion'))
        return Transition(State(None, state.used, state.active != job))
    raise ValueError('unknown event')


class Jobs:
    def __init__(self, accept_stale=False):
        self.accept_stale = accept_stale

    def metadata(self):
        return Metadata('background-jobs', build='example-v1-not-a-content-fingerprint')

    def initial_state(self):
        return State()

    def inputs(self, state):
        return [('start', 1), ('start', 2), ('cancel', None), ('completed', 1), ('completed', 2)]

    def step(self, state, event):
        return reduce(state, event, accept_stale=self.accept_stale)

    def check_state(self, state):
        return [Check('job.current', 'failed', 'accepted stale completion') if state.accepted_stale else Check('job.current')]


async def host_example():
    state = State()
    started = {1: asyncio.Event(), 2: asyncio.Event()}
    released = {1: asyncio.Event(), 2: asyncio.Event()}
    completions = asyncio.Queue()
    tasks = {}

    async def work(job):
        started[job].set()
        cancelled = False
        try:
            await released[job].wait()
        except asyncio.CancelledError:
            # External operations can finish after cancellation was requested.
            cancelled = True
            await released[job].wait()
        await completions.put((('completed', job), cancelled))

    def dispatch(event):
        nonlocal state
        transition = reduce(state, event)
        state = transition.state
        for effect, job in transition.outputs:
            if effect == 'run':
                tasks[job] = asyncio.create_task(work(job))
            elif effect == 'cancel':
                tasks[job].cancel()
            else:
                raise AssertionError('unexpected effect')
        return transition

    try:
        dispatch(('start', 1))
        await started[1].wait()
        dispatch(('cancel', None))
        dispatch(('start', 2))
        await started[2].wait()
        released[1].set()
        late, cancelled = await completions.get()
        ignored = dispatch(late)
        assert cancelled and ignored.disposition.kind == 'ignored' and state.active == 2
        released[2].set()
        completion, cancelled = await completions.get()
        dispatch(completion)
        assert not cancelled and state.active is None and not state.accepted_stale
        reused = dispatch(('start', 1))
        assert reused.disposition.kind == 'ignored' and state.active is None
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


def main():
    broken = enumerate_states(Jobs(accept_stale=True))
    assert broken.failure is not None, 'negative control missed stale completion'
    fixed = enumerate_states(Jobs())
    assert fixed.failure is None and fixed.termination == 'graph_exhausted' and fixed.skipped_checks == 0
    asyncio.run(host_example())
    print(f'negative control found; fixed graph exhausted ({fixed.states} states); late completion ignored')


if __name__ == '__main__':
    main()
