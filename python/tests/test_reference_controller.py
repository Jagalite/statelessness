"""Source-derived examples for the partial request controller reducer."""
import unittest
from statelessness import ModelError, Rng, SearchConfig, assured_enumerate, enumerate_states, record, replay
from statelessness.reference_controller import Bounds, Controller, Effect, Input, Job, State, MUTATION_TARGETS, ReferenceCoverage, manifest, evaluate_mutation, replay_mutation_witness, regression_corpus, check_regressions

class ControllerTests(unittest.TestCase):
    def test_superseded_completion_and_duplicate_are_inert(self):
        model = Controller()
        first = model.step(State(), Input('submit'))
        second = model.step(first.state, Input('submit'))
        self.assertEqual(second.outputs, (Effect('cancel', 1, 1), Effect('start', 2, 1)))
        stale = model.step(second.state, Input('complete', 1, 1, 'success'))
        self.assertEqual(stale.outputs, (Effect('settle', 1, result='cancelled'), Effect('release', 1)))
        self.assertEqual(stale.state.current, 2)
        duplicate = model.step(stale.state, Input('complete', 1, 1, 'success'))
        self.assertEqual(duplicate.disposition.kind, 'ignored')
        self.assertEqual(duplicate.state, stale.state)
        self.assertEqual(duplicate.outputs, ())
    def test_retry_deadline_and_publication_order(self):
        model = Controller()
        start = model.step(State(), Input('submit'))
        failure = model.step(start.state, Input('complete', 1, 1, 'retryable_failure'))
        self.assertEqual(failure.state.job(1).phase, 'waiting')
        self.assertEqual(failure.state.job(1).deadline, 1)
        self.assertEqual(model.step(failure.state, Input('tick', now=0)).disposition.kind, 'ignored')
        retry = model.step(failure.state, Input('tick', now=1))
        self.assertEqual(retry.outputs, (Effect('start', 1, 2),))
        done = model.step(retry.state, Input('complete', 1, 2, 'success'))
        self.assertEqual(done.outputs, (Effect('publish', 1), Effect('settle', 1, result='succeeded'), Effect('release', 1)))
        self.assertIsNone(done.state.current)
        self.assertFalse(done.state.job(1).resource_owned)
    def test_cancel_waiting_releases_and_bad_state_fails(self):
        model = Controller()
        start = model.step(State(), Input('submit'))
        waiting = model.step(start.state, Input('complete', 1, 1, 'retryable_failure'))
        cancelled = model.step(waiting.state, Input('cancel', 1))
        self.assertEqual(cancelled.outputs, (Effect('settle', 1, result='cancelled'), Effect('release', 1)))
        self.assertEqual(model.check_state(cancelled.state)[0].status, 'passed')
        self.assertEqual(model.check_state(State(jobs=(Job(1, 1, 'settled', True),)))[0].status, 'failed')
    def test_finite_bfs_checks_state_validity(self):
        result = enumerate_states(Controller(Bounds(1, 1, 1)), SearchConfig(max_depth=10))
        self.assertEqual(result.termination, 'graph_exhausted')
        self.assertIsNone(result.failure)

    def test_named_mutations_trip_independent_target(self):
        model = Controller()
        initial = State()
        running = model.step(initial, Input('submit')).state
        superseded = model.step(running, Input('submit')).state
        waiting = model.step(running, Input('complete', 1, 1, 'retryable_failure')).state
        settled = model.step(running, Input('complete', 1, 1, 'success')).state
        cases = {
            'obsolete-publication': (superseded, Input('complete', 1, 1, 'success')),
            'missing-cleanup': (running, Input('complete', 1, 1, 'success')),
            'double-settlement': (running, Input('complete', 1, 1, 'success')),
            'dropped-cancellation-ack': (superseded, Input('cancelled', 1, 1)),
            'duplicate-completion': (settled, Input('complete', 1, 1, 'success')),
            'early-release': (running, Input('cancel', 1)),
            'skipped-retry-deadline': (waiting, Input('tick', now=1)),
            'ignored-mutation': (initial, Input('cancel', 0)),
            'reordered-effects': (running, Input('submit')),
        }
        for mutation, (before, input) in cases.items():
            with self.subTest(mutation=mutation):
                clean = model.step(before, input)
                self.assertTrue(all(check.status == 'passed' for check in model.check_transition(before, input, clean)))
                broken = Controller(mutation=mutation).step(before, input)
                checks = {check.id: check.status for check in model.check_transition(before, input, broken)}
                self.assertEqual(checks[MUTATION_TARGETS[mutation]], 'failed')

    def test_omitted_completion_and_submit_fail_progress(self):
        model = Controller()
        running = model.step(State(), Input('submit')).state
        from statelessness import Transition
        no_op = Transition(running)
        checks = {c.id: c.status for c in model.check_transition(running, Input('complete', 1, 1, 'success'), no_op)}
        self.assertEqual(checks['controller.completion_progress'], 'failed')
        checks = {c.id: c.status for c in model.check_transition(running, Input('submit'), no_op)}
        self.assertEqual(checks['controller.submission_progress'], 'failed')

    def test_independent_catalog_matches_source_and_detects_omitted_property(self):
        from pathlib import Path
        from statelessness.reference_controller import _catalog_bytes
        root = Path(__file__).resolve().parents[2]
        for name in ('controller-properties.txt', 'controller.json', 'controller-witnesses.txt'):
            self.assertEqual(_catalog_bytes(name), (root / 'docs' / 'requirements' / name).read_bytes())
        class Omitted(Controller):
            def check_transition(self, *args):
                return tuple(c for c in super().check_transition(*args) if c.id != 'controller.publication_current')
        model = Omitted(Bounds(1, 1, 1))
        run = assured_enumerate(model, manifest(model), ReferenceCoverage(), SearchConfig(max_depth=10))
        results = {p.property.id: p.status for p in run.assurance.properties}
        self.assertEqual(results['controller.publication_current'], 'missing')
        self.assertFalse(run.assurance.assess().adequate)

    def test_assured_default_controller_exercises_declared_goals(self):
        model = Controller()
        run = assured_enumerate(model, manifest(model), ReferenceCoverage())
        self.assertEqual(run.search.termination, 'graph_exhausted')
        self.assertTrue(run.assurance.assess().adequate, run.assurance.assess().reasons)

    def test_codec_exact_wire_and_property_invalid_checkpoint(self):
        model = Controller()
        event = Input('complete', 2, 1, 'retryable_failure')
        self.assertEqual(model.encode_input(event), b'\x02' + (2).to_bytes(8, 'little') + b'\x01\x01')
        self.assertEqual(model.decode_input(model.encode_input(event)), event)
        self.assertEqual(model.encode_output(Effect('settle', 2, result='cancelled')),
                         b'\x03' + (2).to_bytes(8, 'little') + b'\x00\x01')
        bad = State(jobs=(Job(1, 1, 'settled', True, result='succeeded'),))
        self.assertEqual(model.decode_state(model.encode_state(bad)), bad)
        self.assertEqual(model.check_state(bad)[0].status, 'failed')
        with self.assertRaises(ModelError): model.decode_input(b'\x00' * 10 + b'\x01')
        with self.assertRaises(ModelError): model.decode_state(model.encode_state(State()) + b'\x00')
        inputs = (Input('submit'), Input('complete', 1, 1, 'success'))
        trace = record(model, inputs)
        self.assertEqual(replay(model, trace).outcome, 'exact')

    def test_controller_build_identity_tracks_loaded_source_and_catalogs(self):
        from unittest.mock import patch
        from statelessness import reference_controller as reference
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        model = Controller()
        original = model.metadata().build
        trace = record(model, (Input('submit'),))
        # A file change cannot relabel code already loaded in this interpreter.
        with patch.object(reference, '_reference_source_bytes', return_value=b'changed'):
            self.assertEqual(model.metadata().build, original)
            self.assertEqual(replay(model, trace).outcome, 'exact')
        self.assertNotEqual(Controller(Bounds(1, 1, 1)).metadata().build, original)
        self.assertNotEqual(Controller(mutation='missing-cleanup').metadata().build, original)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(__file__).resolve().parents[1] / 'src' / 'statelessness'
            package = Path(directory) / 'statelessness'
            shutil.copytree(source, package, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            env = dict(os.environ, PYTHONPATH=directory, PYTHONDONTWRITEBYTECODE='1')
            command = [sys.executable, '-c', 'from statelessness.reference_controller import Controller; print(Controller().metadata().build)']
            def build(): return subprocess.check_output(command, env=env, text=True).strip()
            baseline = build()
            for relative in ('reference_controller.py', 'controller_catalog/controller-properties.txt', 'controller_catalog/controller.json'):
                path = package / relative
                saved = path.read_bytes()
                path.write_bytes(saved + b'\n')
                try:
                    with self.subTest(relative=relative): self.assertNotEqual(build(), baseline)
                finally: path.write_bytes(saved)

    def test_reject_noncanonical_python_values(self):
        model = Controller()
        for input in (Input('submit', generation=1), Input('tick', generation=1, now=1),
                      Input('complete', 1, 1, 'unknown'), Input('cancel', True)):
            with self.subTest(input=input):
                with self.assertRaises(ModelError): model.encode_input(input)
                with self.assertRaises(ModelError): model.step(State(), input)
        for job in (Job(1, 1, 'running', deadline=1), Job(1, 1, 'running', result='failed'),
                    Job(1, 1, 'unknown'), Job(1, True, 'running'), Job(1, 1, 'settled', result='unknown')):
            state = State(jobs=(job,))
            with self.subTest(job=job):
                self.assertEqual(model.check_state(state)[0].status, 'failed')
                with self.assertRaises(ModelError): model.encode_state(state)
        for state in (State(now=-1), State(now=True), State(now=1.5), State(current=True)):
            self.assertEqual(model.check_state(state)[0].status, 'failed')
        with self.assertRaises(ModelError): model.encode_output(Effect('publish', 1, attempt=1))

    def test_configuration_immutable_and_generation_index(self):
        model = Controller()
        with self.assertRaises(AttributeError): model.mutation = 'missing-cleanup'
        with self.assertRaises(AttributeError): model.bounds = Bounds(1, 1, 1)
        state = model.step(State(), Input('submit')).state
        choices = tuple(model.inputs(state))
        left, right = Rng(42), Rng(42)
        for _ in range(100):
            self.assertEqual(model.generate(state, left), choices[right.index(len(choices))])
        huge = Controller(Bounds(1, 1, (1 << 64) - 2))
        rng = Rng(1)
        with self.assertRaisesRegex(ModelError, 'exceeds u64'): huge.generate(State(), rng)
        self.assertEqual(rng.state, 1)
        self.assertTrue(huge.is_enabled(State(), Input('tick', now=(1 << 64) - 2)))

    def test_mutation_search_shrink_and_replay(self):
        for mutation in MUTATION_TARGETS:
            with self.subTest(mutation=mutation):
                result = evaluate_mutation(mutation)
                self.assertEqual(result.status, 'killed', result.reason)
                self.assertIsNotNone(result.original)
                self.assertIsNotNone(result.minimized)
                trace = replay_mutation_witness(mutation, result.minimized)
                self.assertEqual(trace.termination, 'property_failed')
        limited = evaluate_mutation('missing-cleanup', SearchConfig(max_transitions=0))
        self.assertEqual(limited.status, 'not_evaluated')
        self.assertIsNone(limited.minimized)
        # Rust {:?} spelling for the incomplete termination.
        self.assertRegex(limited.reason, r'^incomplete search: TransitionLimit; skipped=\d+$')

    def test_state_codec_job_weight_matches_rust_tuple_layout(self):
        from statelessness.reference_controller import _WIRE_JOB
        from statelessness.value_codec import Rust64Layout
        # size_of::<(u64, u8, (u8, u64), bool)>() on 64-bit Rust is 32, not repr(C) 40.
        self.assertEqual(_WIRE_JOB.allocation_layout, Rust64Layout(32, 8))

    def test_every_evaluation_error_is_not_evaluated(self):
        # Rust evaluate_mutation maps every error to NotEvaluated.
        from unittest.mock import patch
        class Interrupt(BaseException): pass
        with patch('statelessness.core.enumerate_states', side_effect=RuntimeError('host bug')):
            result = evaluate_mutation('missing-cleanup')
        self.assertEqual((result.status, result.reason), ('not_evaluated', 'host bug'))
        with patch('statelessness.core.enumerate_states', side_effect=Interrupt()):
            with self.assertRaises(Interrupt): evaluate_mutation('missing-cleanup')

    def test_independent_regression_corpus_and_negative_controls(self):
        from statelessness.reference_controller import _catalog_bytes
        text = _catalog_bytes('controller-witnesses.txt').decode('utf-8')
        self.assertEqual(len(regression_corpus(text)), 9)
        self.assertTrue(check_regressions(text))
        for malformed in (text + '\n[missing-cleanup]\nsubmit\n', text.replace('[missing-cleanup]', '[unknown]'),
                          text.replace('cancel 0', 'cancel -1'), text.replace('complete 1 1 retry', 'complete 1 999 retry')):
            with self.subTest(malformed=malformed[-40:]), self.assertRaises(ModelError): regression_corpus(malformed)

    def test_host_worker_cancellation_and_recording_failure(self):
        import threading
        from statelessness.reference_host import Host
        release = threading.Event()
        host = Host(lambda generation, attempt: (release.wait(3), 'success')[1], record=True)
        try:
            host.deliver(Input('submit'))
            host.deliver(Input('cancel', 1))
            self.assertEqual(host.pending_workers(), 1)
            release.set()
            self.assertTrue(host.receive(3))
            self.assertEqual(host.settled, {1: 'cancelled'})
            self.assertEqual(host.pending_workers(), 0)
            self.assertEqual(host.published, [])
            self.assertEqual(host.recording().termination, 'interrupted')
        finally:
            release.set(); host.close()
        class BrokenRecorder:
            def observe(self, *args): raise ValueError('broken recorder')
        host = Host(lambda generation, attempt: 'success')
        host._recorder = BrokenRecorder()
        try:
            self.assertEqual(host.deliver(Input('submit')), (Effect('start', 1, 1),))
            self.assertTrue(host.receive(3))
            self.assertEqual(host.published, [1])
            self.assertEqual(host.settled, {1: 'succeeded'})
            self.assertEqual(host.executed_effects, 4)
            self.assertTrue(any('recording unavailable' in x for x in host.diagnostics))
        finally: host.close()

    def test_host_context_manager_and_finalizer_signal_workers(self):
        # Rust src/reference/host.rs Drop for Host requests cancellation of
        # pending workers without fabricating acknowledgements.
        import gc
        import threading
        from statelessness.reference_host import Host
        release = threading.Event()
        try:
            with Host(lambda generation, attempt: (release.wait(3), 'success')[1]) as host:
                host.deliver(Input('submit'))
                (cancelled, thread), = host._workers.values()
                self.assertFalse(cancelled.is_set())
            self.assertTrue(cancelled.is_set())
            with self.assertRaisesRegex(ModelError, 'closed'): host.state
            release.set(); thread.join(3)
            self.assertEqual(host._events.get(timeout=3), Input('cancelled', 1, 1))
        finally: release.set()
        held = threading.Event()
        try:
            host = Host(lambda generation, attempt: (held.wait(3), 'success')[1])
            host.deliver(Input('submit'))
            (cancelled, thread), = host._workers.values()
            del host; gc.collect()
            self.assertTrue(cancelled.is_set())
        finally:
            held.set(); thread.join(3)

    def test_host_reentrancy_bad_diagnostics_and_spawn_failure(self):
        from unittest.mock import patch
        from statelessness.reference_host import Host
        class BadError(Exception):
            def __str__(self): raise RuntimeError('bad error text')
        class Reentrant:
            def __init__(self, host): self.host = host; self.rejected = False
            def observe(self, *args):
                try: self.host.deliver(Input('submit'))
                except ModelError: self.rejected = True
                raise BadError()
        host = Host(lambda generation, attempt: 'success')
        recorder = Reentrant(host)
        host._recorder = recorder
        try:
            host.deliver(Input('submit'))
            self.assertTrue(recorder.rejected)
            self.assertEqual(len(host.state.jobs), 1)
            self.assertTrue(host.receive(3))
            self.assertEqual(host.settled, {1: 'succeeded'})
            self.assertEqual(host.executed_effects, 4)
            self.assertTrue(any('exception text unavailable' in s for s in host.diagnostics))
        finally: host.close()
        host = Host(lambda generation, attempt: 'success', Bounds(1, 1, 1))
        try:
            with patch('threading.Thread.start', side_effect=BadError()): host.deliver(Input('submit'))
            self.assertTrue(host.receive(3))
            self.assertEqual(host.settled, {1: 'failed'})
            self.assertTrue(any('exception text unavailable' in s for s in host.diagnostics))
        finally: host.close()

    def test_worker_cannot_replace_host_state(self):
        from statelessness.reference_host import Host
        holder, attempts = {}, []
        def work(generation, attempt):
            host = holder['host']
            try: host.state = State()
            except AttributeError: attempts.append('state rejected')
            try: host.settled = {}
            except AttributeError: attempts.append('settled rejected')
            host.events = None
            host.workers = None
            return 'success'
        host = Host(work)
        holder['host'] = host
        try:
            host.deliver(Input('submit'))
            self.assertTrue(host.receive(3))
            self.assertEqual(attempts, ['state rejected', 'settled rejected'])
            self.assertEqual(host.published, [1])
            self.assertEqual(host.settled, {1: 'succeeded'})
        finally: host.close()

if __name__ == '__main__': unittest.main()
