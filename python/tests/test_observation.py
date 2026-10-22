"""Source-authored diagnostics checks; no fixture expectations from execution."""
import io
import unittest

from statelessness import (
    Check, CheckPolicy, Disposition, Event, EncodedPayloads, LogLevel, Metadata,
    ModelError, NoPayloads, ObservationOptions, Recorder, RunConfig, SnapshotPolicy,
    TextObserver, TextObserverError, Transition, U8, check_initial, check_turn,
    model_codec,
)


class Model:
    def __init__(self): self.calls = []
    def metadata(self): return Metadata("counter", build="test")
    def initial_state(self): raise AssertionError("diagnostics initialized state")
    def step(self, *args): raise AssertionError("diagnostics ran reducer")
    def check_state(self, state): self.calls.append("check_state"); return (Check("state"),)
    def check_transition(self, *args): self.calls.append("check_transition"); return (Check("edge"),)
    def execute_effect(self, *args): raise AssertionError("diagnostics ran effects")
    def encode_state_into(self, value, out): self.calls.append(("state", value)); out.append(bytes((value,)))
    def encode_input_into(self, value, out): self.calls.append(("input", value)); out.append(bytes((value,)))
    def encode_output_into(self, value, out): self.calls.append(("output", value)); out.append(bytes((value,)))


def turn(sequence=1, checks=(), disposition=Disposition(), outputs=(3, 4)):
    return Event.Transition(sequence, 99, 2, Transition(5, outputs, disposition), checks)


def logger(level=LogLevel.TRACE, snapshots=SnapshotPolicy.EVERY_TRANSITION,
           maximum=1024, payloads=None, writer=None):
    return TextObserver(io.BytesIO() if writer is None else writer, "test",
                        ObservationOptions(level, snapshots, maximum),
                        EncodedPayloads(16) if payloads is None else payloads)


class ObservationTests(unittest.TestCase):
    def test_defaults_and_native_configuration_validation(self):
        self.assertEqual(ObservationOptions(), ObservationOptions(LogLevel.OFF, SnapshotPolicy.NONE, 1048576))
        for value in (0, -1, True, 1.0, 2**64):
            with self.subTest(value=value), self.assertRaises(ValueError): SnapshotPolicy.every(value)
            with self.subTest(value=value), self.assertRaises(ValueError): turn(value)
        for value in (-1, True, 1.0, 2**64):
            with self.assertRaises(ValueError): Event.Checkpoint(value, 0)
            with self.assertRaises(ValueError): Event.Message(LogLevel.INFO, value, "x")
            with self.assertRaises(ValueError): Event.Finished(value, "x")
            with self.assertRaises(ValueError): ObservationOptions(max_record_bytes=value)
        self.assertEqual(turn(2**64 - 1).sequence, 2**64 - 1)
        with self.assertRaises(ValueError): ObservationOptions(level=5)
        with self.assertRaises(ValueError): ObservationOptions(snapshots=None)

    def test_filtering_before_payloads_and_no_execution(self):
        model = Model()
        for level in LogLevel:
            log = logger(level)
            log.observe(model, turn())
            self.assertEqual(bool(log.get_ref().getvalue()), level >= LogLevel.DEBUG)
        self.assertEqual(model.calls, [("input", 2), ("output", 3), ("output", 4), ("state", 5)])

    def test_off_does_not_inspect_event_or_model(self):
        class Hostile:
            def __getattribute__(self, name): raise AssertionError("inspected while off")
        class Writer(io.BytesIO):
            def __init__(self): super().__init__(); self.writes = self.flushes = 0
            def write(self, data): self.writes += 1; return super().write(data)
            def flush(self): self.flushes += 1; return super().flush()
        writer = Writer()
        log = TextObserver(writer, "off", payloads=Hostile())
        log.observe(Hostile(), Hostile())
        self.assertEqual((writer.writes, writer.flushes), (0, 0))
        self.assertEqual(log.get_ref().getvalue(), b"")

    def test_failure_precedence_over_skip_both_orders_and_checkpoint(self):
        skipped, failed = Check("skip", "skipped", "policy"), Check("bad", "failed", "wrong")
        for checks in ((skipped, failed), (failed, skipped)):
            for event in (turn(checks=checks), Event.Checkpoint(1, 9, checks)):
                log = logger(LogLevel.ERROR)
                log.observe(Model(), event)
                text = log.get_ref().getvalue()
                self.assertTrue(text.startswith(b'Error stream="test"'))
                self.assertIn(b'Skipped("policy")', text)
                self.assertIn(b'Failed("wrong")', text)

    def test_skips_warn_and_passed_details_trace_only(self):
        checks = (Check("ok"), Check("skip", "skipped", "policy"))
        for level in LogLevel:
            model, log = Model(), logger(level)
            log.observe(model, turn(checks=checks))
            text = log.get_ref().getvalue()
            self.assertEqual(bool(text), level >= LogLevel.WARN)
            self.assertEqual(b'check="ok":Passed' in text, level == LogLevel.TRACE)
            self.assertEqual(bool(model.calls), level == LogLevel.TRACE)

    def test_dispositions_do_not_promote_ordinary_transitions(self):
        for kind in ("accepted", "rejected", "ignored"):
            disposition = Disposition(kind, "" if kind == "accepted" else "reason")
            for level in (LogLevel.WARN, LogLevel.INFO, LogLevel.DEBUG):
                log = logger(level)
                log.observe(Model(), turn(disposition=disposition))
                self.assertEqual(bool(log.get_ref().getvalue()), level == LogLevel.DEBUG)

    def test_lifecycle_message_threshold_matrix(self):
        for level in LogLevel:
            log = logger(level)
            log.observe(object(), Event.Started(Metadata("m"), RunConfig()))
            for severity in LogLevel:
                log.observe(object(), Event.Message(severity, None, "message"))
            log.observe(object(), Event.Finished(0, "done"))
            data = log.get_ref().getvalue()
            self.assertEqual(data.count(b"event=message"), int(level))
            self.assertEqual(b"event=started" in data, level >= LogLevel.INFO)
            self.assertEqual(b"event=finished" in data, level >= LogLevel.INFO)

    def test_exact_lifecycle_rust_text_and_escaping(self):
        log = TextObserver(io.BytesIO(), 's\n"\\\t', ObservationOptions(LogLevel.INFO))
        log.observe(object(), Event.Started(Metadata("m\r", 2, 3, 4, "é"),
            RunConfig("manual", 2**64-1, (("key", "a\nb"),))))
        log.observe(object(), Event.Message(LogLevel.INFO, None, 'x\0\x01\u2028🙂'))
        expected = (
            'Info stream="s\\n\\"\\\\\\t" event=started metadata=ModelMetadata { name: "m\\r", model_version: 2, properties_version: 3, codec_version: 4, build: "é" } config=RunConfig { strategy: "manual", seed: Some(18446744073709551615), parameters: [("key", "a\\nb")] }\n'
            'Info stream="s\\n\\"\\\\\\t" event=message sequence=None message="x\\0\\u{1}\\u{2028}🙂"\n'
        ).encode()
        self.assertEqual(log.get_ref().getvalue(), expected)

    def test_exact_transition_order_actual_payloads_and_before_unused(self):
        model, log = Model(), logger()
        log.observe(model, turn(12, (Check("first"), Check("second", "skipped", "policy")), Disposition("ignored", "duplicate")))
        self.assertEqual(log.get_ref().getvalue(),
            b'Warn stream="test" event=transition sequence=12 disposition=Ignored("duplicate") outputs=2 checks=2 check="first":Passed check="second":Skipped("policy") input=hex:02 output[0]=hex:03 output[1]=hex:04 post_state=hex:05\n')
        self.assertEqual(model.calls, [("input", 2), ("output", 3), ("output", 4), ("state", 5)])

    def test_snapshot_policies_and_checkpoint_interval_independence(self):
        for policy, states in ((SnapshotPolicy.NONE, []), (SnapshotPolicy.every(2), [9, 5]),
                               (SnapshotPolicy.EVERY_TRANSITION, [9, 5, 5])):
            model, log = Model(), logger(snapshots=policy)
            log.observe(model, Event.Checkpoint(1, 9))
            log.observe(model, turn(2))
            log.observe(model, turn(3))
            self.assertEqual([v for k, v in model.calls if k == "state"], states)
        model, log = Model(), logger(snapshots=SnapshotPolicy.every(2**64-1))
        log.observe(model, turn(2**64-1))
        self.assertEqual(model.calls[-1], ("state", 5))

    def test_runtime_changes_do_not_restore_omitted_history(self):
        model, log = Model(), logger(LogLevel.OFF, SnapshotPolicy.every(2))
        log.observe(model, turn())
        log.set_level(LogLevel.TRACE)
        log.observe(model, Event.Checkpoint(1, 7))
        log.observe(model, turn(2))
        log.set_snapshots(SnapshotPolicy.NONE)
        log.observe(model, turn(4))
        self.assertEqual([v for k, v in model.calls if k == "state"], [7, 5])
        self.assertEqual(log.get_ref().getvalue().count(b"\n"), 3)

    def test_no_payloads_opaque_values_need_no_copy_repr_or_codec(self):
        class Opaque:
            def __deepcopy__(self, memo): raise AssertionError("copied")
            def __repr__(self): raise AssertionError("formatted")
        value = Opaque()
        log = logger(payloads=NoPayloads())
        log.observe(object(), Event.Transition(1, value, value, Transition(value, (value,))))
        self.assertEqual(log.get_ref().getvalue().count(b"unavailable"), 3)

    def test_custom_formatter_borrows_values_and_owns_multiline_escaping(self):
        value, seen = [], []
        class Payloads:
            def state(self, model, arg, out): seen.append(arg); out.write(b"custom\ntext")
        log = logger(payloads=Payloads())
        event = Event.Checkpoint(0, value)
        self.assertIs(event.state, value)
        log.observe(object(), event)
        self.assertIs(seen[0], value)
        self.assertTrue(log.get_ref().getvalue().endswith(b" state=custom\ntext\n"))

    def test_utf8_exact_bound_includes_newline(self):
        expected = 'Info stream="test" event=finished transitions=0 reason="é🙂"\n'.encode()
        for limit in (0, len(expected)-1, len(expected)):
            log = logger(LogLevel.INFO, maximum=limit)
            if limit < len(expected):
                with self.assertRaises(TextObserverError) as caught: log.observe(object(), Event.Finished(0, "é🙂"))
                self.assertEqual(caught.exception.stage, "record_limit")
                self.assertEqual(log.get_ref().getvalue(), b"")
                self.assertFalse(log.sink_poisoned)
            else:
                log.observe(object(), Event.Finished(0, "é🙂"))
                self.assertEqual(log.get_ref().getvalue(), expected)

    def test_swallowed_record_overflow_stays_sticky_and_next_event_recovers(self):
        class Payloads(NoPayloads):
            def input(self, model, value, out):
                try: out.write(b"x"*1024)
                except TextObserverError: pass
        log = logger(maximum=256, payloads=Payloads())
        with self.assertRaises(TextObserverError) as caught: log.observe(object(), turn(outputs=()))
        self.assertEqual(caught.exception.stage, "record_limit")
        self.assertEqual(log.get_ref().getvalue(), b"")
        log.set_level(LogLevel.DEBUG)
        log.observe(object(), turn(2))
        self.assertEqual(log.get_ref().getvalue().count(b"\n"), 1)

    def test_swallowed_invalid_formatter_write_cannot_publish(self):
        class Payloads(NoPayloads):
            def input(self, model, value, out):
                try: out.write("text, not bytes")
                except TextObserverError: pass
        log = logger(payloads=Payloads())
        with self.assertRaises(TextObserverError): log.observe(object(), turn())
        self.assertEqual(log.get_ref().getvalue(), b"")

    def test_formatter_errors_each_stage_are_atomic_and_recover(self):
        for kind in ("input", "output", "state"):
            class Payloads(NoPayloads): pass
            def fail(self, model, value, out): out.write(b"partial"); raise RuntimeError("broken")
            payloads = Payloads()
            setattr(Payloads, kind, fail)
            log = logger(payloads=payloads)
            with self.assertRaises(TextObserverError) as caught: log.observe(Model(), turn())
            self.assertEqual(caught.exception.stage, "format_"+kind)
            self.assertEqual(log.get_ref().getvalue(), b"")
            setattr(Payloads, kind, getattr(NoPayloads, kind))
            log.observe(Model(), turn(2))
            self.assertFalse(log.sink_poisoned)

    def test_hostile_exception_text_is_contained(self):
        class HostileError(Exception):
            def __str__(self): raise RuntimeError("bad str")
        class Payloads(NoPayloads):
            def input(self, *args): raise HostileError()
        log = logger(payloads=Payloads())
        with self.assertRaises(TextObserverError) as caught: log.observe(object(), turn())
        self.assertEqual(caught.exception.stage, "format_input")
        self.assertIn("text unavailable", str(caught.exception))
        self.assertFalse(log.sink_poisoned)

    def test_async_and_non_none_formatter_returns_rejected_without_publication(self):
        for result in (1, False, b"ignored"):
            class Payloads(NoPayloads):
                def input(self, *args): return result
            log = logger(payloads=Payloads())
            with self.assertRaisesRegex(TextObserverError, "synchronously"): log.observe(object(), turn())
            self.assertEqual(log.get_ref().getvalue(), b"")
        class AsyncPayloads(NoPayloads):
            async def input(self, *args): raise AssertionError("must not execute")
        with self.assertRaisesRegex(TextObserverError, "synchronously"):
            logger(payloads=AsyncPayloads()).observe(object(), turn())

    def test_retained_formatter_lease_expires_and_cannot_poison_next_record(self):
        leases = []
        class Payloads(NoPayloads):
            def input(self, model, value, out): leases.append(out); out.write(b"value")
        log = logger(payloads=Payloads())
        log.observe(object(), turn())
        with self.assertRaisesRegex(TextObserverError, "expired"): leases[0].write(b"corrupt")
        with self.assertRaisesRegex(TextObserverError, "expired"): leases[0].flush()
        log.observe(object(), turn(2))
        self.assertEqual(log.get_ref().getvalue().count(b"\n"), 2)

    def test_bounded_codec_errors_each_stage_and_recovery(self):
        for kind in ("input", "output", "state"):
            class Broken(Model): pass
            def fail(self, value, out): out.append(b"x"); raise RuntimeError("broken")
            setattr(Broken, "encode_"+kind+"_into", fail)
            model, log = Broken(), logger()
            with self.assertRaises(TextObserverError) as caught: log.observe(model, turn())
            self.assertEqual(caught.exception.stage, "encode_"+kind)
            self.assertEqual(log.get_ref().getvalue(), b"")
            setattr(Broken, "encode_"+kind+"_into", getattr(Model, "encode_"+kind+"_into"))
            log.observe(model, turn(2))
            self.assertEqual(log.get_ref().getvalue().count(b"\n"), 1)

    def test_swallowed_codec_overflow_and_expired_encode_lease(self):
        leases = []
        class Broken(Model):
            def encode_input_into(self, value, out):
                leases.append(out)
                try: out.append(b"xx")
                except ModelError: pass
        log = logger(payloads=EncodedPayloads(1))
        with self.assertRaises(TextObserverError) as caught: log.observe(Broken(), turn())
        self.assertEqual(caught.exception.stage, "encode_input")
        self.assertEqual(log.get_ref().getvalue(), b"")
        with self.assertRaisesRegex(ModelError, "expired"): leases[0].append(b"x")
        log.observe(Model(), turn(2))
        self.assertFalse(log.sink_poisoned)

    def test_legacy_encoding_bound_and_into_precedence(self):
        class Legacy:
            def encode_input(self, value): return b"ab"
        log = logger(payloads=EncodedPayloads(1), snapshots=SnapshotPolicy.NONE)
        with self.assertRaises(TextObserverError): log.observe(Legacy(), turn(outputs=()))
        class Into(Legacy):
            def encode_input_into(self, value, out): out.append(b"\xab")
        log.observe(Into(), turn(outputs=()))
        self.assertIn(b"input=hex:ab", log.get_ref().getvalue())

    def test_declaration_binding_uses_public_into_dispatch(self):
        model = Model()
        bound = model_codec(state=U8, input=U8, output=U8).bind(model)
        log = logger()
        log.observe(bound, turn())
        self.assertIn(b"input=hex:02 output[0]=hex:03 output[1]=hex:04 post_state=hex:05", log.get_ref().getvalue())
        self.assertEqual(model.calls, [])

    def test_direct_encoded_payload_failure_writes_no_prefix(self):
        payloads = EncodedPayloads(0)
        for kind in ("state", "input", "output"):
            out = io.BytesIO()
            with self.assertRaises(TextObserverError) as caught: getattr(payloads, kind)(Model(), 1, out)
            self.assertEqual(caught.exception.stage, "encode_"+kind)
            self.assertEqual(out.getvalue(), b"")

    def test_direct_public_payload_formatters_complete_short_writes(self):
        class Writer(io.BytesIO):
            calls = 0
            def write(self, data):
                self.calls += 1
                if self.calls % 3 == 1: raise InterruptedError()
                return super().write(data[:1])
        for payloads, expected in ((NoPayloads(), b"unavailable"), (EncodedPayloads(1), b"hex:ab")):
            for kind in ("state", "input", "output"):
                out = Writer()
                getattr(payloads, kind)(Model(), 171, out)
                self.assertEqual(out.getvalue(), expected)

    def test_short_and_interrupted_writes_complete_record(self):
        class Writer(io.BytesIO):
            calls = 0
            def write(self, data):
                self.calls += 1
                if self.calls % 3 == 1: raise InterruptedError()
                return super().write(data[:3])
        writer, log = Writer(), None
        log = logger(LogLevel.INFO, writer=writer)
        log.observe(object(), Event.Finished(0, "done"))
        log.flush()
        self.assertEqual(writer.getvalue(), b'Info stream="test" event=finished transitions=0 reason="done"\n')
        self.assertFalse(log.sink_poisoned)

    def test_partial_writer_failure_poison_precedes_off(self):
        class Writer(io.BytesIO):
            calls = 0
            flushes = 0
            def write(self, data):
                self.calls += 1
                if self.calls == 1: return super().write(data[:2])
                raise OSError("disk full")
            def flush(self): self.flushes += 1; return super().flush()
        writer = Writer()
        log = logger(LogLevel.INFO, writer=writer)
        with self.assertRaises(TextObserverError) as caught: log.observe(object(), Event.Finished(0, "done"))
        self.assertEqual(caught.exception.stage, "sink_write")
        self.assertEqual(writer.getvalue(), b"In")
        log.set_level(LogLevel.OFF)
        with self.assertRaises(TextObserverError) as caught: log.observe(object(), turn())
        self.assertEqual(caught.exception.stage, "sink_poisoned")
        self.assertEqual((writer.calls, writer.flushes), (2, 0))
        self.assertTrue(log.sink_poisoned)

    def test_zero_invalid_and_async_write_results_poison(self):
        for result in (0, None, False, -1, 10000, "1"):
            class Writer:
                def write(self, data): return result
            log = logger(LogLevel.INFO, writer=Writer())
            with self.assertRaises(TextObserverError): log.observe(object(), Event.Finished(0, "done"))
            self.assertTrue(log.sink_poisoned)
        class AsyncWriter:
            async def write(self, data): raise AssertionError("async writer body executed")
        log = logger(LogLevel.INFO, writer=AsyncWriter())
        with self.assertRaises(TextObserverError): log.observe(object(), Event.Finished(0, "done"))
        self.assertTrue(log.sink_poisoned)

    def test_hostile_writer_exception_text_still_poisons(self):
        class HostileError(Exception):
            def __str__(self): raise RuntimeError("bad str")
        class Writer:
            def write(self, data): raise HostileError()
        log = logger(LogLevel.INFO, writer=Writer())
        with self.assertRaises(TextObserverError) as caught: log.observe(object(), Event.Finished(0, "done"))
        self.assertEqual(caught.exception.stage, "sink_write")
        self.assertIn("text unavailable", str(caught.exception))
        self.assertTrue(log.sink_poisoned)
        log.set_level(LogLevel.OFF)
        with self.assertRaises(TextObserverError): log.observe(object(), turn())

    def test_codec_snapshot_failure_is_atomic_without_reducer_or_check_work(self):
        class Opaque:
            def __deepcopy__(self, memo): raise RuntimeError("copy unavailable")
        model, log = Model(), logger()
        with self.assertRaises(TextObserverError) as caught:
            log.observe(model, Event.Transition(1, Opaque(), Opaque(), Transition(5)))
        self.assertEqual(caught.exception.stage, "encode_input")
        self.assertEqual(model.calls, [])
        self.assertEqual(log.get_ref().getvalue(), b"")
        log.observe(model, turn(2))
        self.assertFalse(log.sink_poisoned)

    def test_library_text_subclasses_cannot_inject_newlines(self):
        class Text(str):
            def __repr__(self): return "injected\nrepr"
            def __str__(self): return "injected\nstr"
            def capitalize(self): return "injected\nvariant"
        log = TextObserver(io.BytesIO(), Text("s"), ObservationOptions(LogLevel.DEBUG))
        log.observe(object(), Event.Transition(1, None, None,
            Transition(None, (), Disposition(Text("ignored"), Text("reason\ntext"))),
            (Check(Text("id\n"), Text("skipped"), Text("why\n")),)))
        self.assertEqual(log.get_ref().getvalue(),
            b'Warn stream="s" event=transition sequence=1 disposition=Ignored("reason\\ntext") outputs=0 checks=1 check="id\\n":Skipped("why\\n")\n')

    def test_flush_failure_poison_and_into_inner_never_flushes(self):
        class Writer(io.BytesIO):
            calls = 0
            def flush(self): self.calls += 1; raise OSError("flush")
        writer = Writer()
        log = logger(LogLevel.INFO, writer=writer)
        log.observe(object(), Event.Finished(0, "done"))
        self.assertEqual(writer.calls, 0)
        with self.assertRaises(TextObserverError) as caught: log.flush()
        self.assertEqual(caught.exception.stage, "sink_flush")
        self.assertTrue(log.sink_poisoned)
        with self.assertRaises(TextObserverError): log.flush()
        self.assertIs(log.into_inner(), writer)
        self.assertEqual(writer.calls, 1)
        with self.assertRaises(TextObserverError): log.get_ref()

    def test_into_inner_healthy_moves_without_close_or_flush(self):
        class Writer(io.BytesIO):
            def flush(self): raise AssertionError("implicit flush")
            def close(self): raise AssertionError("implicit close")
        writer = Writer()
        log = logger(writer=writer)
        self.assertIs(log.get_ref(), writer)
        self.assertIs(log.into_inner(), writer)
        with self.assertRaises(TextObserverError): log.observe(object(), turn())
        # Avoid invoking this test sentinel's close during normal finalization.
        writer.close = lambda: None

    def test_writer_retaining_input_cannot_pin_next_record_buffer(self):
        class Writer(io.BytesIO):
            def __init__(self): super().__init__(); self.retained = []
            def write(self, data): self.retained.append(data); return super().write(data)
        writer = Writer()
        log = logger(LogLevel.INFO, writer=writer)
        log.observe(object(), Event.Finished(1, "one"))
        log.observe(object(), Event.Finished(2, "two"))
        self.assertTrue(bytes(writer.retained[0]).endswith(b'reason="one"\n'))

    def test_reentrancy_and_reinitialization_cannot_replace_active_record(self):
        outer = self
        class Payloads(NoPayloads):
            def input(self, model, value, out):
                with outer.assertRaisesRegex(TextObserverError, "in progress"): log.observe(model, turn())
                with outer.assertRaisesRegex(TextObserverError, "in progress"): log.into_inner()
                with outer.assertRaisesRegex(TextObserverError, "reinitialized"): log.__init__(io.BytesIO(), "new")
                out.write(b"safe")
        log = logger(payloads=Payloads())
        log.observe(object(), turn())
        self.assertIn(b"input=safe", log.get_ref().getvalue())

    def test_events_allow_gaps_and_reordering_but_cannot_forge_checked_authority(self):
        model = Model()
        initial = check_initial(model, 0)
        rec = Recorder.with_checked_initial(initial)
        log = logger(LogLevel.DEBUG)
        forged = Event.Transition(42, 123, 4, Transition(250), (Check("fake"),))
        count = model.calls[:]
        log.observe(model, forged)
        log.observe(model, turn(1))
        self.assertEqual(model.calls, count)
        with self.assertRaisesRegex(ModelError, "authentic CheckedTurn"): rec.observe_checked(forged)
        self.assertEqual(model.calls, count)

    def test_checked_handoff_recorder_and_diagnostics_do_not_recheck(self):
        for level in LogLevel:
            model = Model()
            rec = Recorder.with_checked_initial(check_initial(model, 0))
            checked = check_turn(model, 0, 2, Transition(5, (3, 4)), 1)
            rec.observe_checked(checked)
            before = (model.calls.count("check_state"), model.calls.count("check_transition"))
            trace = rec.snapshot()
            log = logger(level)
            diagnostic = checked.observation()
            log.observe(model, Event.Transition(diagnostic.sequence, diagnostic.before,
                diagnostic.input, diagnostic.transition, diagnostic.checks))
            self.assertEqual((model.calls.count("check_state"), model.calls.count("check_transition")), before)
            self.assertEqual(rec.snapshot(), trace)

    def test_sampled_token_remains_non_authoritative_for_full_recorder(self):
        model = Model()
        rec = Recorder.with_checked_initial(check_initial(model, 0))
        sampled = check_turn(model, 0, 2, Transition(5), 1, CheckPolicy(2, False))
        log = logger(LogLevel.WARN)
        diagnostic = sampled.observation()
        count = model.calls[:]
        log.observe(model, Event.Transition(1, 0, 2, diagnostic.transition, diagnostic.checks))
        self.assertEqual(model.calls, count)
        with self.assertRaises(ModelError): rec.observe_checked(sampled)
        self.assertEqual(model.calls, count)

    def test_shared_fixtures_through_public_installed_consumer(self):
        import importlib.util
        import json
        import os
        from pathlib import Path
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads(Path(os.environ.get("STATELESS_OBSERVATION_CORPUS",
            root / "conformance/observation-v1.json")).read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location("observation_consumer",
            root / "examples/native-observation/python/consumer.py")
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        result = consumer.observe_fixture(fixture)
        self.assertEqual(result["schema"], "statelessness.observation-results.v1")
        self.assertEqual(result["language"], "python")
        self.assertEqual(len(result["cases"]), len(fixture["cases"]))
        for row, actual in zip(fixture["cases"], result["cases"]):
            with self.subTest(case=row["id"]):
                self.assertEqual(actual, dict(id=row["id"], **{key: row["expected"][key]
                    for key in ("record_hex", "callbacks", "error_stage", "sink_poisoned")}))
                if row["id"] == "declaration-bound-codec":
                    unbound = consumer.DeclarationModel([])
                    for kind in ("state", "input", "output"):
                        with self.assertRaisesRegex(AssertionError, "direct encoder"):
                            getattr(unbound, "encode_"+kind+"_into")(17, None)
                        with self.assertRaisesRegex(AssertionError, "legacy encoder"):
                            getattr(unbound, "encode_"+kind)(17)
                    self.assertEqual(unbound.callbacks, [])
                    with patch.object(consumer, "declaration_bound", lambda model: model):
                        bypassed = consumer.observe_case(row)
                    self.assertEqual(bypassed["error_stage"], "encode_input")
                    self.assertEqual(bypassed["record_hex"], "")
                    self.assertEqual(bypassed["callbacks"], [])
                    self.assertFalse(bypassed["sink_poisoned"])


if __name__ == "__main__":
    unittest.main()
