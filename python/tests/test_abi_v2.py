"""ABI 2 qualification sources; runtime requires STATELESS_ABI2_LIBRARY.

No library is built or discovered implicitly. Missing opt-in skips native cases;
an explicitly supplied invalid artifact fails instead of falling back to Python.
"""
import gc
import json
import os
from pathlib import Path
import unittest
import weakref

from statelessness import Check, Metadata, SearchConfig, TraceLimits, Transition
from statelessness.core import Disposition, Trace, TraceStep
from statelessness import _native_packet
from statelessness.native import NativeEngine, NativeError
from statelessness.native_conformance import Backend
from statelessness.conformance import raw_response
from statelessness.trace_json import FormatError


class Cursor:
    def __init__(self, values, *, fail_after=None):
        self.values = iter(values)
        self.fail_after = fail_after
        self.pulls = self.closed = 0

    def __iter__(self): return self

    def __next__(self):
        self.pulls += 1
        if self.fail_after is not None and self.pulls > self.fail_after:
            raise RuntimeError("cursor failed after prefix")
        return next(self.values)

    def close(self): self.closed += 1


class Counter:
    def __init__(self): self.steps, self.cursors = 0, []
    def metadata(self): return Metadata("abi2-counter", build="qualification-source")
    def initial_state(self): return {"n": 0}

    def inputs(self, state):
        cursor = Cursor([{"delta": 1}])
        self.cursors.append(cursor)
        return cursor

    def step(self, state, input):
        self.steps += 1
        state["n"] += input["delta"]
        return Transition(state, ({"n": state["n"]},))

    def check_state(self, state):
        return (Check("bound", "failed", "three") if state["n"] >= 3 else Check("bound"),)

    def generate(self, state, rng):
        rng.next_u64()
        return {"delta": 1}

    def is_enabled(self, state, input): return input == {"delta": 1}
    def simpler_inputs(self, input): return ()
    def encode_state(self, state): return bytes([state["n"]])
    def decode_state(self, data): return {"n": data[0]}
    def encode_input(self, input): return bytes([input["delta"]])
    def decode_input(self, data): return {"delta": data[0]}
    def encode_output(self, output): return bytes([output["n"]])


class PacketTests(unittest.TestCase):
    def trace(self):
        return Trace(Metadata("", build=""), b"a", (Check(""),),
                     (TraceStep(b"b", Disposition(), (b"c",), b"d", (Check("x"),)),),
                     "completed")

    def test_packet_roundtrip_empty_identifiers_and_owned_blobs(self):
        trace = self.trace()
        self.assertEqual(_native_packet.decode(_native_packet.encode(trace)), trace)

    def test_every_truncated_prefix_and_trailing_bytes_fail(self):
        packet = _native_packet.encode(self.trace())
        for length in range(len(packet)):
            with self.subTest(length=length), self.assertRaises((FormatError, UnicodeError)):
                _native_packet.decode(packet[:length])
        with self.assertRaisesRegex(FormatError, "trailing"):
            _native_packet.decode(packet + b"x")

    def test_blob_payload_and_cumulative_collection_limits(self):
        packet = _native_packet.encode(self.trace())
        for limits in (TraceLimits(max_blob_bytes=0), TraceLimits(max_payload_bytes=3),
                       TraceLimits(max_items=3), TraceLimits(max_steps=0)):
            with self.subTest(limits=limits), self.assertRaises(FormatError):
                _native_packet.decode(packet, limits)
        self.assertEqual(_native_packet.decode(packet, TraceLimits(max_items=4)), self.trace())

    def test_discarded_binary_parameters_do_not_consume_portable_item_budget(self):
        trace = Trace(Metadata("", build=""), b"a", (Check(""),), (), "completed")
        packet = _native_packet.encode(trace)
        offset = packet.index(b"manual") + len(b"manual") + 1
        parameter = b"\x01\x00\x00\x00k\x01\x00\x00\x00v"
        packet = packet[:offset] + b"\x01\x00\x00\x00" + parameter + packet[offset + 4:]
        self.assertEqual(_native_packet.decode(packet, TraceLimits(max_items=1)), trace)


@unittest.skipUnless(os.environ.get("STATELESS_ABI2_LIBRARY"), "set STATELESS_ABI2_LIBRARY to opt into native execution")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = NativeEngine(os.environ["STATELESS_ABI2_LIBRARY"])

    def test_wrong_thread_and_hash_mismatch_fail_without_losing_owner(self):
        import threading
        with self.assertRaisesRegex(NativeError, "hash mismatch"):
            NativeEngine(self.engine.path, expected_sha256='0'*64)
        with self.engine.session(Counter()) as session:
            errors=[]
            def other_thread():
                try: session.search()
                except NativeError as error: errors.append(error.status)
            thread=threading.Thread(target=other_thread)
            thread.start(); thread.join()
            self.assertEqual(errors,[15])
            self.assertEqual(session.enumerate_states().termination,'failure_found')
            self.assertEqual(session.retained_roots,0)

    def test_owned_witness_copies_survive_result_and_session_close(self):
        model = Counter()
        with self.engine.session(model) as session:
            with session.search(SearchConfig(20, 20, 10)) as result:
                self.assertEqual(result.status, 1)
                owned = result.inputs()
                self.assertEqual(owned, ({"delta": 1},) * 3)
                owned[0]["delta"] = 99
                self.assertEqual(result.inputs()[0], {"delta": 1})
                self.assertGreater(session.retained_roots, 0)
            self.assertEqual(session.retained_roots, 0)
        self.assertEqual(owned[0], {"delta": 99})
        self.assertTrue(model.cursors)
        self.assertTrue(all(cursor.closed == 1 for cursor in model.cursors))

    def test_lazy_callback_failure_preserves_only_completed_prefix(self):
        model, cursor = Counter(), Cursor([{"delta": 1}], fail_after=1)
        with self.engine.session(model) as session:
            trace = session.record(cursor)
            self.assertEqual((trace.termination, len(trace.steps), model.steps), ("model_error", 1, 1))
            self.assertIn("cursor failed after prefix", trace.error)
            self.assertEqual(cursor.closed, 1)
            self.assertEqual(session.retained_roots, 0)
            self.assertEqual(session.replay(trace).outcome, "exact")

    def test_partial_check_batch_cannot_return_success(self):
        class Broken(Counter):
            def check_state(self, state):
                yield Check("partial")
                raise RuntimeError("checker failed")
        with self.engine.session(Broken()) as session:
            with self.assertRaisesRegex(NativeError, "checker failed"):
                session.check_initial({"n": 0})
            self.assertEqual(session.retained_roots, 0)

    def test_observed_transition_never_calls_reducer_or_mutates_caller(self):
        class Observe(Counter):
            def initial_state(self): raise AssertionError("initial must not run")
            def step(self, *_): raise AssertionError("step must not run")
            def check_transition(self, before, input, transition):
                before["n"] = 90
                transition.state["n"] = 91
                return (Check("effect"),)
        before, after = {"n": 0}, {"n": 1}
        with self.engine.session(Observe()) as session:
            checks = session.check_observed(before, {"delta": 1}, Transition(after), 1)
            self.assertEqual(checks, (Check("bound"), Check("effect")))
            self.assertEqual((before, after), ({"n": 0}, {"n": 1}))
            self.assertEqual(session.retained_roots, 0)

    def test_unpublished_cursor_is_closed_on_root_quota_failure(self):
        model = Counter()
        with self.engine.session(model, max_values=1) as session:
            with self.assertRaises(NativeError): session.search()
            self.assertEqual(len(model.cursors), 1)
            self.assertEqual(model.cursors[0].closed, 1)
            self.assertEqual(session.retained_roots, 0)

    def test_portable_limit_rejects_candidate_before_prefix_commit(self):
        model, cursor = Counter(), Cursor([{"delta": 1}] * 10)
        with self.engine.session(model) as session:
            trace = session.record(cursor, limits=TraceLimits(max_payload_bytes=1))
            self.assertEqual((trace.termination, len(trace.steps)), ("model_error", 0))
            self.assertEqual((model.steps, cursor.pulls, cursor.closed), (1, 1, 1))
            self.assertEqual(session.retained_roots, 0)
            empty = session.record([], limits=TraceLimits(max_items=1))
            self.assertEqual((empty.termination, len(empty.initial_checks)), ("completed", 1))
        model, cursor = Counter(), Cursor([{"delta": 1}])
        with self.engine.session(model) as session:
            with self.assertRaises(NativeError):
                session.record(cursor, limits=TraceLimits(max_payload_bytes=0))
            self.assertEqual((model.steps, cursor.pulls, cursor.closed), (0, 0, 1))

    def test_cross_session_shrink_rejected_and_source_result_remains_usable(self):
        with self.engine.session(Counter()) as first, self.engine.session(Counter()) as second:
            with first.fuzz(cases=1, max_steps=4) as failure:
                with self.assertRaisesRegex(NativeError, "another session"):
                    second.shrink(failure)
                self.assertEqual(failure.inputs(), ({"delta": 1},) * 3)
                with first.shrink(failure) as shrunk:
                    self.assertEqual(shrunk.status, 1)
            self.assertEqual(first.retained_roots, 0)
            self.assertEqual(second.retained_roots, 0)

    def test_live_session_keeps_callback_trampolines_rooted_until_close(self):
        session = self.engine.session(Counter())
        reference = weakref.ref(session)
        del session
        gc.collect()
        self.assertIsNotNone(reference())
        try: self.assertEqual(reference().check_initial({"n": 0}), (Check("bound"),))
        finally: reference().close()
        gc.collect()
        self.assertIsNone(reference())

    def test_cursor_cleanup_reentry_is_rejected_and_outer_session_recovers(self):
        attempts = []
        with self.engine.session(Counter()) as session:
            class Reentrant(Cursor):
                def close(self):
                    super().close()
                    try: session.search()
                    except NativeError as error: attempts.append(error.status)
            cursor = Reentrant([])
            session.record(cursor, max_steps=0)
            self.assertEqual((cursor.closed, attempts), (1, [16]))
            self.assertEqual(session.retained_roots, 0)
            self.assertEqual(session.check_initial({"n": 0}), (Check("bound"),))

    def test_unsupported_persistence_fails_before_snapshot_or_iterator(self):
        class Minimal:
            def initial_state(self): return 0
            def step(self, *_): raise AssertionError("unexpected step")
            def check_state(self, _): return ()
            def clone_state(self, _): raise AssertionError("unexpected clone")
        class Untouched:
            def __iter__(self): raise AssertionError("unexpected iterator")
        with self.engine.session(Minimal()) as session:
            for operation in (lambda: session.record(Untouched()),
                              lambda: session.record_binary(Untouched()),
                              lambda: session.ring(0)):
                with self.assertRaises(NativeError) as raised: operation()
                self.assertEqual(raised.exception.status, 17)
            self.assertEqual(session.retained_roots, 0)

    def test_fixture_corpus_uses_explicit_rust_backend_and_recovers_after_bad_input(self):
        backend = Backend(self.engine)
        hello = raw_response(b'{"version":1,"operation":"hello"}', backend)
        self.assertEqual(hello["engine_family"], "rust-shared")
        self.assertEqual(hello["artifact_sha256"], self.engine.sha256)
        self.assertEqual(raw_response(b"\xff", backend), {"error": "invalid_request"})
        corpus = json.loads((Path(__file__).resolve().parents[2] / "conformance/corpus.json").read_text(encoding="utf-8"))
        for case in corpus["cases"]:
            if case["request"]["operation"] == "hello": continue
            with self.subTest(case=case["id"]):
                actual = raw_response(json.dumps(case["request"], ensure_ascii=True).encode(), backend)
                self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(case["expected"], sort_keys=True))


if __name__ == "__main__": unittest.main()
