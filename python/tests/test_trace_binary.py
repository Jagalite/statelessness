"""Independent binary-v1 shared goldens and adversarial stream/limit tests."""
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest

from statelessness import (AuditTrace, BinaryLimits, BinaryTraceError, Check,
    Disposition, Metadata, RunConfig, Trace, TraceStep, audit_from_bytes,
    audit_to_bytes, read_audit, write_audit)

CORPUS = Path(__file__).resolve().parents[2] / "conformance/native-binary-v1.json"


def fixture_trace(value):
    def checks(values):
        return tuple(Check(c["id"], c["status"], c["reason"] or "") for c in values)
    return AuditTrace(Metadata(**value["metadata"]),
        RunConfig(value["config"]["strategy"],
                  None if value["config"]["seed"] is None else int(value["config"]["seed"]),
                  value["config"]["parameters"]), bytes.fromhex(value["initial_state"]),
        checks(value["initial_checks"]),
        tuple(TraceStep(bytes.fromhex(s["input"]),
            Disposition(s["disposition"]["kind"], s["disposition"]["reason"] or ""),
            tuple(bytes.fromhex(o) for o in s["outputs"]), bytes.fromhex(s["post_state"]),
            checks(s["checks"])) for s in value["steps"]),
        value["termination"]["kind"], value["termination"]["reason"] or "")


class BinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS.read_text())
        cls.valid = [f for f in cls.corpus["fixtures"] if f["valid"]]

    def test_shared_goldens_and_error_categories(self):
        for fixture in self.corpus["fixtures"]:
            with self.subTest(case=fixture["case"]):
                data = bytes.fromhex(fixture["bytes_hex"])
                limits = BinaryLimits(**fixture.get("limits", {}))
                if fixture["valid"]:
                    expected = fixture_trace(fixture["trace"])
                    self.assertEqual(audit_from_bytes(data, limits=limits), expected)
                    self.assertEqual(audit_to_bytes(expected, limits=limits), data)
                else:
                    with self.assertRaises(BinaryTraceError) as caught:
                        audit_from_bytes(data, limits=limits)
                    self.assertEqual(caught.exception.kind, fixture["expected_error"]["kind"])
                    self.assertEqual(caught.exception.detail, fixture["expected_error"]["detail"])

    def test_every_byte_prefix_is_truncated_not_accepted(self):
        for fixture in self.valid:
            data = bytes.fromhex(fixture["bytes_hex"])
            for length in range(len(data)):
                with self.subTest(case=fixture["case"], length=length):
                    with self.assertRaises(BinaryTraceError) as caught:
                        audit_from_bytes(data[:length])
                    self.assertEqual(caught.exception.kind, "truncated")

    def test_chunked_reader_and_short_writer(self):
        class Chunked(io.BytesIO):
            def read(self, size=-1): return super().read(min(size, 3))
        class Short(io.BytesIO):
            def write(self, data): return super().write(data[:2])
        for fixture in self.valid:
            data = bytes.fromhex(fixture["bytes_hex"])
            trace = read_audit(Chunked(data))
            output = Short()
            write_audit(trace, output)
            self.assertEqual(output.getvalue(), data)
            self.assertFalse(output.closed)

    def test_writer_preflight_each_quota_no_publication(self):
        trace = fixture_trace(next(f["trace"] for f in self.valid if f["case"] == "single-step"))
        populated = replace(trace, config=RunConfig("manual", 0, (("k", "v"),)))
        limits = {
            "max_total_bytes": 0, "max_frame_bytes": 0, "max_blob_bytes": 0,
            "max_string_bytes": 0, "max_steps": 0, "max_checks_per_step": 0,
            "max_outputs_per_step": 0, "max_parameters": 0, "max_items": 0,
        }
        for name, limit in limits.items():
            with self.subTest(limit=name):
                stream = io.BytesIO()
                with self.assertRaises(BinaryTraceError) as caught:
                    write_audit(populated, stream, limits=BinaryLimits(**{name: limit}))
                self.assertEqual(caught.exception.kind, "limit_exceeded")
                self.assertEqual(stream.getvalue(), b"")
        # A late footer/step error must also prevent even the header publication.
        stream = io.BytesIO()
        with self.assertRaises(BinaryTraceError):
            write_audit(replace(trace, termination="model_error", error="x" * 100), stream,
                        limits=BinaryLimits(max_string_bytes=20))
        self.assertEqual(stream.getvalue(), b"")

    def test_lossless_explicit_configuration_conversion_and_unusual_semantics(self):
        trace = fixture_trace(next(f["trace"] for f in self.valid if f["case"] == "unicode-config-full-u64"))
        portable, retained_config = trace.to_portable()
        self.assertEqual(AuditTrace.from_portable(portable, retained_config), trace)
        self.assertEqual(retained_config.seed, (1 << 64) - 1)
        self.assertEqual(retained_config.parameters[:2], (("k", "α"), ("k", "β")))
        with self.assertRaises(TypeError): AuditTrace.from_portable(portable)
        # Binary is structural: preserve failure checks even with completed footer.
        odd = replace(trace, initial_checks=(Check("odd", "failed", ""),))
        self.assertEqual(audit_from_bytes(audit_to_bytes(odd)), odd)
        with self.assertRaises(BinaryTraceError):
            AuditTrace.from_portable(replace(portable, error="lost reason"), retained_config)
        model_error = replace(trace, termination="model_error", error="")
        self.assertEqual(audit_from_bytes(audit_to_bytes(model_error)), model_error)

    def test_strict_utf8_and_unsafe_numbers_fail_before_write(self):
        trace = fixture_trace(self.valid[0]["trace"])
        stream = io.BytesIO()
        bad = replace(trace, config=RunConfig("\ud800"))
        with self.assertRaises(BinaryTraceError) as caught: write_audit(bad, stream)
        self.assertEqual(caught.exception.kind, "invalid_data")
        self.assertEqual(stream.getvalue(), b"")
        for seed in (True, -1, 1 << 64, 1.5):
            with self.assertRaises(ValueError): RunConfig(seed=seed)
        for limit in (True, -1, 1.5):
            with self.assertRaises(ValueError): BinaryLimits(max_items=limit)

    def test_io_failures_interrupted_operations_and_no_implicit_flush(self):
        trace = fixture_trace(self.valid[0]["trace"])
        data = audit_to_bytes(trace)
        class InterruptRead(io.BytesIO):
            interrupted = False
            def read(self, size=-1):
                if not self.interrupted:
                    self.interrupted = True
                    raise InterruptedError()
                return super().read(size)
        self.assertEqual(read_audit(InterruptRead(data)), trace)
        class BrokenRead:
            def read(self, size): raise OSError("read failed")
        with self.assertRaises(BinaryTraceError) as caught: read_audit(BrokenRead())
        self.assertEqual(caught.exception.kind, "io")
        class BrokenWrite:
            def write(self, value): raise OSError("write failed")
        with self.assertRaises(BinaryTraceError) as caught: write_audit(trace, BrokenWrite())
        self.assertEqual(caught.exception.kind, "io")
        class NoFlush(io.BytesIO):
            def flush(self): raise AssertionError("codec must not flush")
        output = NoFlush()
        write_audit(trace, output)
        self.assertEqual(output.getvalue(), data)

    def test_exact_native_semantic_replay_keeps_config(self):
        from statelessness import replay, Transition
        trace = fixture_trace(next(f["trace"] for f in self.valid if f["case"] == "single-step"))
        class Model:
            def metadata(self): return Metadata("counter", 1, 2, 3, "b")
            def decode_state(self, data): return data[0]
            def encode_state(self, state): return bytes([state])
            def decode_input(self, data): return data[0]
            def encode_input(self, input): return bytes([input])
            def encode_output(self, output): return output
            def step(self, state, input): return Transition(state + input * 2, (b"", bytes([state, 255])))
            def check_state(self, state): return (Check("ok"),)
            def check_transition(self, before, input, transition): return (Check("later", "skipped", "not run"),)
        decoded = audit_from_bytes(audit_to_bytes(trace))
        portable, config = decoded.to_portable()
        report = replay(Model(), portable)
        self.assertEqual(report.outcome, "exact")
        self.assertEqual(report.steps_verified, 1)
        self.assertEqual(config, trace.config)
        self.assertFalse(report.failure_reproduced)




class BinaryPreflightTests(unittest.TestCase):
    def test_invalid_parameter_container_is_not_reinterpreted(self):
        with self.assertRaises(ValueError): RunConfig(parameters=("ab",))
        with self.assertRaises(ValueError): RunConfig(parameters=(b"ab",))
        self.assertEqual(RunConfig(parameters=iter([("a", "b")])).parameters, (("a", "b"),))

    def test_utf8_quota_before_allocation_and_nested_collection_snapshot(self):
        class Guard(str):
            def encode(self, *args): raise AssertionError("unexpected allocation")
        trace = AuditTrace(Metadata(Guard("雪雪雪")), RunConfig(), b"", (), (), "completed")
        stream = io.BytesIO()
        from unittest.mock import patch
        with patch("statelessness.trace_binary._utf8_payload", side_effect=AssertionError("UTF8 allocation before quota")):
            with self.assertRaises(BinaryTraceError) as caught:
                write_audit(trace, stream, limits=BinaryLimits(max_string_bytes=8))
        self.assertEqual((caught.exception.kind, caught.exception.detail), ("limit_exceeded", "string bytes"))
        self.assertEqual(stream.getvalue(), b"")
        outputs, checks = [b"x"], [Check("ok")]
        step = TraceStep(b"", Disposition(), outputs, b"", checks)
        trace = AuditTrace(Metadata("m"), RunConfig(), b"", (), (step,), "completed")
        expected = audit_to_bytes(trace)
        class Mutating(io.BytesIO):
            def write(self, value):
                outputs.extend([b"x"] * 100)
                checks.extend([Check("extra")] * 100)
                return super().write(value)
        stream = Mutating()
        write_audit(trace, stream, limits=BinaryLimits(max_outputs_per_step=1, max_checks_per_step=1))
        self.assertEqual(stream.getvalue(), expected)




class BinaryStreamFailureTests(unittest.TestCase):
    def test_eof_probe_errors_and_partial_io_failures_remain_errors(self):
        trace = AuditTrace(Metadata("m"), RunConfig(), b"", (), (), "completed")
        data = audit_to_bytes(trace)
        class EofFailure(io.BytesIO):
            def read(self, size=-1):
                if self.tell() == len(data): raise OSError("EOF probe failed")
                return super().read(size)
        with self.assertRaises(BinaryTraceError) as caught: read_audit(EofFailure(data))
        self.assertEqual(caught.exception.kind, "io")
        class PartialFailure(io.BytesIO):
            def write(self, value):
                if self.tell(): raise OSError("after header")
                return super().write(value)
        output = PartialFailure()
        with self.assertRaises(BinaryTraceError) as caught: write_audit(trace, output)
        self.assertEqual(caught.exception.kind, "io")
        self.assertEqual(output.getvalue(), data[:12])
        closed = io.BytesIO()
        closed.close()
        with self.assertRaises(BinaryTraceError) as caught: write_audit(trace, closed)
        self.assertEqual(caught.exception.kind, "io")




class BinaryVariantValidationTests(unittest.TestCase):
    class EqualAnything:
        def __eq__(self, other): return True
    class Spoof(str):
        def __eq__(self, other): return True
    class HiddenReason(str):
        def __bool__(self): return False
        def __len__(self): return 0

    def base(self):
        return AuditTrace(Metadata("m"), RunConfig(), b"", (), (), "completed")

    def assertRejectedBeforePublication(self, trace):
        stream = io.BytesIO()
        with self.assertRaises(BinaryTraceError) as caught: write_audit(trace, stream)
        self.assertEqual(caught.exception.kind, "invalid_data")
        self.assertEqual(stream.getvalue(), b"")

    def test_public_check_status_spoof_objects_and_unknown_text_are_rejected(self):
        for status in (self.EqualAnything(), self.Spoof("unknown")):
            check = Check("bad", status)
            self.assertRejectedBeforePublication(replace(self.base(), initial_checks=(check,)))
        for kind in (self.EqualAnything(), self.Spoof("unknown")):
            step = TraceStep(b"", Disposition(kind), (), b"", ())
            self.assertRejectedBeforePublication(replace(self.base(), steps=(step,)))
        for termination in (self.EqualAnything(), self.Spoof("unknown")):
            self.assertRejectedBeforePublication(replace(self.base(), termination=termination))

    def test_hidden_nonempty_reasons_are_not_dropped(self):
        check = Check("bad", "passed", self.HiddenReason("must not disappear"))
        self.assertRejectedBeforePublication(replace(self.base(), initial_checks=(check,)))
        step = TraceStep(b"", Disposition("accepted", self.HiddenReason("must not disappear")), (), b"", ())
        self.assertRejectedBeforePublication(replace(self.base(), steps=(step,)))
        bad = replace(self.base(), error=self.HiddenReason("must not disappear"))
        self.assertRejectedBeforePublication(bad)
        with self.assertRaises(BinaryTraceError): bad.to_portable()
        with self.assertRaises(BinaryTraceError):
            AuditTrace.from_portable(Trace(Metadata("m"), b"", (), (), "completed", self.HiddenReason("lost")), RunConfig())
        self.assertRejectedBeforePublication(replace(self.base(), error=self.EqualAnything()))

    def test_known_subclass_variant_text_encodes_exact_actual_data(self):
        check = Check("check", self.Spoof("failed"), self.HiddenReason("failure"))
        disposition = Disposition(self.Spoof("ignored"), self.HiddenReason("why"))
        step = TraceStep(b"", disposition, (), b"", (check,))
        trace = replace(self.base(), steps=(step,), termination=self.Spoof("model_error"),
                        error=self.HiddenReason("terminal"))
        decoded = audit_from_bytes(audit_to_bytes(trace))
        self.assertEqual(decoded.termination, "model_error")
        self.assertEqual(decoded.error, "terminal")
        self.assertEqual(decoded.steps[0].checks, (Check("check", "failed", "failure"),))
        self.assertEqual(decoded.steps[0].disposition, Disposition("ignored", "why"))
        portable, config = trace.to_portable()
        self.assertIs(type(portable.termination), str)
        self.assertEqual(portable.error, "terminal")




class BinaryCanonicalizationQuotaTests(unittest.TestCase):
    def test_large_subclass_reason_is_not_copied_before_string_quota(self):
        import tracemalloc
        class Hidden(str):
            def __bool__(self): return False
        reason = Hidden("x" * (8 * 1024 * 1024))
        trace = AuditTrace(Metadata(""), RunConfig(), b"", (), (), "model_error", reason)
        tracemalloc.start()
        try:
            with self.assertRaises(BinaryTraceError): audit_to_bytes(trace, limits=BinaryLimits(max_string_bytes=8))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # A full canonical base-str copy would allocate >8MiB before rejection.
        self.assertLess(peak, 1024 * 1024)

    def test_class_attribute_spoof_is_not_a_real_string(self):
        class Fake:
            @property
            def __class__(self): return str
            def __eq__(self, other): return True
        trace = AuditTrace(Metadata(""), RunConfig(), b"", (), (), Fake())
        stream = io.BytesIO()
        with self.assertRaises(BinaryTraceError) as caught: write_audit(trace, stream)
        self.assertEqual((caught.exception.kind, stream.getvalue()), ("invalid_data", b""))


if __name__ == "__main__": unittest.main()
