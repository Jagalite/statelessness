"""Source-authored operation attribution regressions; parent qualifies via Kyoto."""
import io
import unittest

from statelessness import (
    EncodedPayloads, Event, LogLevel, ModelError, NoPayloads, ObservationOptions,
    SnapshotPolicy, TextObserver, TextObserverError, Transition,
)


KINDS = ("input", "output", "state")


class Sink:
    def __init__(self, fault=None):
        self.data = bytearray()
        self.writes = self.flushes = 0
        self.fault = fault

    def write(self, data):
        self.writes += 1
        if self.fault == "write":
            raise OSError("nested writer failed")
        self.data.extend(data)
        return len(data)

    def flush(self):
        self.flushes += 1
        if self.fault == "flush":
            raise OSError("nested flush failed")


class Codec:
    def encode_input_into(self, value, out): out.append(bytes((value,)))
    def encode_output_into(self, value, out): out.append(bytes((value,)))
    def encode_state_into(self, value, out): out.append(bytes((value,)))


def turn(sequence=1):
    return Event.Transition(sequence, 99, 2, Transition(5, (3,)))


def observer(payloads, maximum=1024, sink=None):
    return TextObserver(Sink() if sink is None else sink, "attribution",
                        ObservationOptions(LogLevel.TRACE,
                                           SnapshotPolicy.EVERY_TRANSITION, maximum),
                        payloads)


def custom(kind, callback):
    payloads = NoPayloads()
    setattr(payloads, kind, callback)
    return payloads


class ObservationAttributionTests(unittest.TestCase):
    def assert_atomic_healthy(self, log):
        sink = log.get_ref()
        self.assertEqual(sink.writes, 0)
        self.assertEqual(sink.flushes, 0)
        self.assertEqual(sink.data, b"")
        self.assertFalse(log.sink_poisoned)
        log.set_level(LogLevel.OFF)
        log.observe(object(), object())
        self.assertEqual(sink.writes, 0)
        self.assertEqual(sink.flushes, 0)
        log.set_level(LogLevel.TRACE)

    def assert_recovery(self, log):
        self.assert_atomic_healthy(log)
        log.observe(object(), Event.Finished(1, "recovered"))
        self.assertEqual(log.get_ref().data,
            b'Info stream="attribution" event=finished transitions=1 reason="recovered"\n')
        self.assertEqual(log.get_ref().writes, 1)
        self.assertFalse(log.sink_poisoned)

    def test_public_staged_errors_are_contextualized_for_every_payload(self):
        stages = ("sink_write", "sink_flush", "sink_poisoned", "record_limit",
                  "encode_input", "encode_output", "encode_state", "format_state")
        for kind in KINDS:
            for stage in stages:
                with self.subTest(kind=kind, claimed_stage=stage):
                    foreign = TextObserverError(stage, "foreign failure")

                    def fail(model, value, out):
                        out.write(b"partial")
                        raise foreign

                    log = observer(custom(kind, fail))
                    with self.assertRaises(TextObserverError) as caught:
                        log.observe(object(), turn())
                    self.assertEqual(caught.exception.stage, "format_" + kind)
                    self.assertIsNot(caught.exception, foreign)
                    self.assertIs(caught.exception.__cause__, foreign)
                    self.assert_recovery(log)

    def test_nested_observer_sink_failures_do_not_poison_the_outer_sink(self):
        for kind in KINDS:
            for fault in ("write", "flush", "poisoned"):
                with self.subTest(kind=kind, fault=fault):
                    inner_sink = Sink("write" if fault == "poisoned" else fault)
                    inner = observer(NoPayloads(), sink=inner_sink)
                    if fault == "poisoned":
                        with self.assertRaises(TextObserverError):
                            inner.observe(object(), Event.Finished(0, "inner"))
                    foreign = []

                    def fail(model, value, out):
                        try:
                            if fault == "flush":
                                inner.flush()
                            else:
                                inner.observe(model, Event.Finished(0, "inner"))
                        except TextObserverError as error:
                            foreign.append(error)
                            raise

                    log = observer(custom(kind, fail))
                    with self.assertRaises(TextObserverError) as caught:
                        log.observe(object(), turn())
                    self.assertEqual(foreign[0].stage, "sink_" + fault)
                    self.assertEqual(caught.exception.stage, "format_" + kind)
                    self.assertIs(caught.exception.__cause__, foreign[0])
                    self.assertTrue(inner.sink_poisoned)
                    self.assertEqual(inner_sink.writes, 0 if fault == "flush" else 1)
                    self.assertEqual(inner_sink.flushes, int(fault == "flush"))
                    self.assert_recovery(log)

    def test_nested_observer_codec_error_is_foreign_to_outer_formatting(self):
        inner = observer(EncodedPayloads(0))

        def fail(model, value, out):
            inner.observe(Codec(), Event.Checkpoint(0, 5))

        log = observer(custom("input", fail))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(object(), turn())
        self.assertEqual(caught.exception.stage, "format_input")
        self.assertEqual(caught.exception.__cause__.stage, "encode_state")
        self.assert_recovery(inner)
        self.assert_recovery(log)

    def test_direct_codec_call_using_another_writer_has_no_outer_attribution(self):
        foreign_writer = io.BytesIO()

        def fail(model, value, out):
            EncodedPayloads(0).state(Codec(), 5, foreign_writer)

        log = observer(custom("input", fail))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(object(), turn())
        self.assertEqual(caught.exception.stage, "format_input")
        self.assertEqual(caught.exception.__cause__.stage, "encode_state")
        self.assertEqual(foreign_writer.getvalue(), b"")
        self.assert_recovery(log)

    def test_codec_errors_keep_actual_stage_when_delegated_through_current_lease(self):
        for kind in KINDS:
            for mode in ("raised", "swallowed_bound", "wrapped"):
                with self.subTest(kind=kind, mode=mode):
                    model, encoded = Codec(), EncodedPayloads(1)

                    def fail(value, out):
                        if mode != "swallowed_bound":
                            raise TextObserverError("sink_write", "codec callback failure")
                        try:
                            out.append(b"xx")
                        except ModelError:
                            pass

                    setattr(model, "encode_" + kind + "_into", fail)

                    def delegate(model, value, out):
                        try:
                            getattr(encoded, kind)(model, value, out)
                        except TextObserverError as error:
                            if mode == "wrapped":
                                raise TextObserverError("sink_poisoned", "wrapped") from error
                            # A public display field is not the source of attribution.
                            error.stage = "sink_flush"
                            raise

                    log = observer(custom(kind, delegate))
                    with self.assertRaises(TextObserverError) as caught:
                        log.observe(model, turn())
                    self.assertEqual(caught.exception.stage,
                                     ("format_" if mode == "wrapped" else "encode_") + kind)
                    self.assert_atomic_healthy(log)
                    log.observe(Codec(), turn(2))
                    self.assertEqual(log.get_ref().writes, 1)
                    self.assertFalse(log.sink_poisoned)

    def test_codec_failure_cannot_be_replayed_in_a_later_payload_or_event(self):
        for same_record in (True, False):
            with self.subTest(same_record=same_record):
                saved = []
                encoded = EncodedPayloads(0)

                def input_payload(model, value, out):
                    if saved:
                        raise saved[0]
                    try:
                        encoded.input(Codec(), value, out)
                    except TextObserverError as error:
                        saved.append(error)
                    out.write(b"fallback")

                payloads = custom("input", input_payload)
                if same_record:
                    def output_payload(model, value, out): raise saved[0]
                    payloads.output = output_payload
                    log = observer(payloads)
                else:
                    # Capture the still-private error inside a callback, then
                    # replay it through a fresh operation on the same observer.
                    log = observer(payloads)
                    log.observe(object(), turn())
                    self.assertEqual(log.get_ref().writes, 1)
                    log.get_ref().data.clear()
                    log.get_ref().writes = 0
                with self.assertRaises(TextObserverError) as caught:
                    log.observe(object(), turn(2))
                self.assertEqual(caught.exception.stage,
                                 "format_output" if same_record else "format_input")
                self.assert_recovery(log)

    def test_codec_failure_cannot_be_replayed_by_the_next_output(self):
        saved = []
        encoded = EncodedPayloads(0)

        def output_payload(model, value, out):
            if saved:
                raise saved[0]
            try:
                encoded.output(Codec(), value, out)
            except TextObserverError as error:
                saved.append(error)
            out.write(b"fallback")

        log = observer(custom("output", output_payload))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(object(), Event.Transition(1, 99, 2, Transition(5, (3, 4))))
        self.assertEqual(caught.exception.stage, "format_output")
        self.assert_recovery(log)

    def test_swallowed_record_overflow_precedes_later_callback_failures(self):
        for kind in KINDS:
            for after in ("return", "raise", "forged", "encode", "invalid_return"):
                with self.subTest(kind=kind, after=after):
                    leases = []

                    def fail(model, value, out):
                        leases.append(out)
                        try:
                            out.write(b"x" * 1024)
                        except TextObserverError as error:
                            self.assertEqual(error.stage, "record_limit")
                            error.stage = "sink_write"
                        if after == "raise":
                            raise RuntimeError("later callback failure")
                        if after == "forged":
                            raise TextObserverError("encode_state", "foreign failure")
                        if after == "encode":
                            EncodedPayloads(0).state(Codec(), 5, out)
                        if after == "invalid_return":
                            return 1

                    log = observer(custom(kind, fail), maximum=256)
                    with self.assertRaises(TextObserverError) as caught:
                        log.observe(object(), turn())
                    self.assertEqual(caught.exception.stage, "record_limit")
                    self.assertIn("exceeds byte limit", str(caught.exception))
                    with self.assertRaisesRegex(TextObserverError, "expired"):
                        leases[0].write(b"late")
                    with self.assertRaisesRegex(TextObserverError, "expired"):
                        leases[0].flush()
                    self.assert_recovery(log)

    def test_overflow_while_rendering_encoded_bytes_keeps_record_stage(self):
        # Literal prefix derived from the source format, never captured output.
        prefix = (b'Debug stream="attribution" event=transition sequence=1 '
                  b'disposition=Accepted outputs=0 checks=0 input=hex:')
        log = observer(EncodedPayloads(1), maximum=len(prefix))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(Codec(), Event.Transition(1, 0, 2, Transition(5)))
        self.assertEqual(caught.exception.stage, "record_limit")
        self.assert_recovery(log)

    def test_direct_builtin_formatter_errors_and_short_writes_are_preserved(self):
        class ShortWriter(io.BytesIO):
            def write(self, data): return super().write(data[:1])

        foreign = TextObserverError("sink_write", "direct writer failure")

        class FailedWriter:
            def write(self, data): raise foreign

        for kind in KINDS:
            with self.subTest(kind=kind):
                writer = io.BytesIO()
                with self.assertRaises(TextObserverError) as caught:
                    getattr(EncodedPayloads(0), kind)(Codec(), 171, writer)
                self.assertEqual(caught.exception.stage, "encode_" + kind)
                self.assertEqual(writer.getvalue(), b"")
                for payloads, expected in ((NoPayloads(), b"unavailable"),
                                           (EncodedPayloads(1), b"hex:ab")):
                    writer = ShortWriter()
                    getattr(payloads, kind)(Codec(), 171, writer)
                    self.assertEqual(writer.getvalue(), expected)
                    with self.assertRaises(TextObserverError) as caught:
                        getattr(payloads, kind)(Codec(), 171, FailedWriter())
                    self.assertIs(caught.exception, foreign)

    def test_custom_payloads_still_borrow_values_and_revoke_after_failure(self):
        class Opaque:
            def __deepcopy__(self, memo): raise AssertionError("copied borrowed value")
            def __repr__(self): raise AssertionError("formatted borrowed value")

        model, value, seen, leases = object(), Opaque(), [], []

        def fail(actual_model, actual_value, out):
            seen.append((actual_model, actual_value))
            leases.append(out)
            raise TextObserverError("sink_poisoned", "foreign failure")

        log = observer(custom("state", fail))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(model, Event.Checkpoint(0, value))
        self.assertEqual(caught.exception.stage, "format_state")
        self.assertIs(seen[0][0], model)
        self.assertIs(seen[0][1], value)
        with self.assertRaisesRegex(TextObserverError, "expired"):
            leases[0].write(b"late")
        self.assert_recovery(log)

    def test_lease_is_revoked_before_callback_exception_text_is_rendered(self):
        expired = []

        def fail(model, value, out):
            class CallbackError(Exception):
                def __str__(self):
                    try:
                        out.write(b"late exception text")
                    except TextObserverError as error:
                        expired.append("expired" in str(error))
                    return "callback failure"
            raise CallbackError()

        log = observer(custom("input", fail))
        with self.assertRaises(TextObserverError) as caught:
            log.observe(object(), turn())
        self.assertEqual(caught.exception.stage, "format_input")
        self.assertEqual(expired, [True])
        self.assert_recovery(log)

    def test_process_interrupts_propagate_with_lease_revoked_and_sink_healthy(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(1)):
            with self.subTest(interrupt=type(interrupt).__name__):
                leases = []

                def fail(model, value, out):
                    leases.append(out)
                    out.write(b"partial")
                    raise interrupt

                log = observer(custom("input", fail))
                with self.assertRaises(type(interrupt)) as caught:
                    log.observe(object(), turn())
                self.assertIs(caught.exception, interrupt)
                with self.assertRaisesRegex(TextObserverError, "expired"):
                    leases[0].write(b"late")
                self.assert_recovery(log)


if __name__ == "__main__":
    unittest.main()
