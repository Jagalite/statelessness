"""Optional single-owner reference host with ordered effect execution.

Worker completion is delivered through a queue. Cancellation is cooperative;
close requests cancellation but does not fabricate acknowledgements or wait for
arbitrary blocked application work. This is not a durable outbox.
"""
import queue
import threading
from .core import ModelError
from .monitor import Recorder
from .trace_binary import RunConfig
from .reference_controller import Bounds, Controller, Input


def _error_text(error):
    try: return str(error)
    except BaseException: return 'exception text unavailable'


class Host:
    def __init__(self, work, bounds=Bounds(), *, record=False):
        if not callable(work): raise ModelError('work must be callable')
        self._owner = threading.get_ident()
        self._model, self._state = Controller(bounds), Controller(bounds).initial_state()
        self._work, self._events, self._workers = work, queue.Queue(), {}
        self._recorder = Recorder(self._model, self._state, RunConfig(strategy='reference-live-host')) if record else None
        self._published, self._settled, self._diagnostics = [], {}, []
        self._executed_effects, self._closed, self._busy = 0, False, False

    def _active(self):
        if threading.get_ident() != self._owner: raise ModelError('host requires its owner thread')
        if self._closed: raise ModelError('host is closed')
        if self._busy: raise ModelError('host delivery is already in progress')

    @property
    def state(self):
        self._active()
        return self._state

    @property
    def model(self):
        self._active()
        return self._model

    @property
    def published(self):
        self._active()
        return list(self._published)

    @property
    def settled(self):
        self._active()
        return dict(self._settled)

    @property
    def diagnostics(self):
        self._active()
        return list(self._diagnostics)

    @property
    def executed_effects(self):
        self._active()
        return self._executed_effects

    def recording(self):
        self._active()
        return None if self._recorder is None else self._recorder.snapshot()

    def deliver(self, input):
        self._active()
        self._busy = True
        try:
            transition = self._model.step(self._state, input)
            if self._recorder is not None:
                try:
                    checks = self._recorder.observe(self._model, self._state, input, transition)
                    self._diagnostics.extend('property failure: ' + c.id for c in checks if c.status == 'failed')
                except Exception as error:
                    self._diagnostics.append('recording unavailable: ' + _error_text(error))
            self._state = transition.state
            for effect in transition.outputs: self._execute(effect)
            return transition.outputs
        finally:
            self._busy = False

    def _execute(self, effect):
        self._executed_effects += 1
        if effect.kind == 'start':
            key = (effect.generation, effect.attempt)
            if key in self._workers: raise ModelError('duplicate effect dispatch')
            cancelled = threading.Event()
            work, events = self._work, self._events
            def worker():
                try:
                    outcome = work(*key)
                    if outcome not in ('success', 'retryable_failure'): outcome = 'retryable_failure'
                except BaseException:
                    outcome = 'retryable_failure'
                events.put(Input('cancelled', *key) if cancelled.is_set() else Input('complete', *key, outcome))
            thread = threading.Thread(target=worker, name=f'request-{key[0]}-{key[1]}', daemon=True)
            try:
                thread.start()
                self._workers[key] = (cancelled, thread)
            except Exception as error:
                self._diagnostics.append('worker spawn failed: ' + _error_text(error))
                self._events.put(Input('complete', *key, 'retryable_failure'))
        elif effect.kind == 'cancel':
            worker = self._workers.get((effect.generation, effect.attempt))
            if worker is not None: worker[0].set()
        elif effect.kind == 'publish': self._published.append(effect.generation)
        elif effect.kind == 'settle':
            if effect.generation in self._settled: raise ModelError('host saw duplicate settlement')
            self._settled[effect.generation] = effect.result
        elif effect.kind == 'release':
            if effect.generation not in self._settled: raise ModelError('host saw release before settlement')
            for key in tuple(self._workers):
                if key[0] == effect.generation:
                    _, thread = self._workers.pop(key)
                    thread.join()

    def receive(self, timeout):
        self._active()
        try: event = self._events.get(timeout=timeout)
        except queue.Empty: return False
        self.deliver(event)
        return True

    def tick(self, now): self.deliver(Input('tick', now=now))
    def pending_workers(self): self._active(); return len(self._workers)
    def close(self):
        """Request cancellation of every pending worker and detach it.

        Mirrors Rust `Drop for Host`: no acknowledgement is fabricated and
        blocked application work is not joined.
        """
        if self._closed: return
        self._active()
        self._closed = True
        self._signal_workers()

    def _signal_workers(self):
        for cancelled, _ in tuple(self._workers.values()): cancelled.set()

    def __enter__(self):
        self._active()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def __del__(self):
        # Best-effort analogue of Rust Drop for an unclosed host: signal workers
        # without owner checks, joins or exceptions escaping the finalizer.
        try:
            if not self.__dict__.get('_closed', True):
                self._closed = True
                self._signal_workers()
        except Exception:
            pass
