"""Packaging identity/negative-loading checks; uses no native engine or compiler."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

import binding_evidence as evidence

checker = runpy.run_path(str(Path(__file__).with_name("check-abi-v2-package.py")))
gates = runpy.run_path(str(Path(__file__).with_name("check-abi-v2-gates.py")))
benchmarks = runpy.run_path(str(Path(__file__).with_name("compare-abi-v2-benchmarks.py")))


class Evidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / "libstateless.so"
        self.binary.write_bytes(b"fixture library; never loaded")
        self.source = {"commit": "fixture", "files": {"Cargo.toml": "abc"}}
        self.source["sha256"] = evidence.content_identity(self.source["files"])

    def receipt(self):
        return evidence.build_receipt(
            self.source, {"native/lib/libstateless.so": self.binary}, "x86_64-unknown-linux-gnu",
            "rustc fixture", "cargo fixture", [{"argv": ["external-build"], "exit_code": 0}],
        )

    def test_prebuilt_staging_requires_both_source_and_binary_identity(self):
        receipt = self.receipt()
        with patch.object(evidence, "source_snapshot", return_value=self.source):
            self.assertEqual(evidence.validate_receipt(receipt, self.root, ["native/lib/libstateless.so"]),
                             {"native/lib/libstateless.so": self.binary.resolve()})
            self.binary.write_bytes(b"another build")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                evidence.validate_receipt(receipt, self.root, ["native/lib/libstateless.so"])
        changed = {**self.source, "sha256": "other"}
        with patch.object(evidence, "source_snapshot", return_value=changed):
            with self.assertRaisesRegex(ValueError, "source changed"):
                evidence.validate_receipt(receipt, self.root, ["native/lib/libstateless.so"])

    def test_failed_build_cannot_be_attested(self):
        with self.assertRaisesRegex(ValueError, "successful recorded"):
            evidence.build_receipt(self.source, {"native/lib/libstateless.so": self.binary}, "target",
                                   "rustc", "cargo", [{"argv": ["build"], "exit_code": 1}])

    def test_snapshot_inventory_cannot_be_changed_without_its_digest(self):
        changed = {**self.source, "files": {"Cargo.toml": "different"}}
        with self.assertRaisesRegex(ValueError, "digest"):
            evidence.same_source(changed, self.source)

    def test_load_rejects_missing_or_wrong_bytes_before_any_dynamic_loading(self):
        with patch.object(checker["ctypes"], "CDLL", side_effect=AssertionError("must not load")):
            with self.assertRaisesRegex(checker["LoadError"], "is missing"):
                checker["load_native"](self.root / "missing.so", "hash")
            with self.assertRaisesRegex(checker["LoadError"], "identity mismatch"):
                checker["load_native"](self.binary, "not its hash")

    def test_missing_symbols_and_wrong_versions_fail_explicitly(self):
        with self.assertRaisesRegex(checker["LoadError"], "lacks required"):
            checker["bootstrap"](object())
        class FakeLibrary:
            def __init__(self):
                self.stateless_abi_version = lambda: 1
                self.stateless_v2_abi_version = lambda: 1
                self.stateless_v2_capabilities = lambda: 3
        with self.assertRaisesRegex(checker["LoadError"], "version mismatch"):
            checker["bootstrap"](FakeLibrary())

    def test_package_inspection_rejects_escaping_and_tampered_files(self):
        with self.assertRaisesRegex(ValueError, "invalid package path"):
            checker["checked_path"](self.root, "../outside")
        metadata = {
            "schema": "stateless.engine-package.v1", "declared_abi_versions": [1, 2],
            "files": {"libstateless.so": "wrong"},
        }
        (self.root / "engine-build.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            checker["inspect_package"](self.root)

    def test_wasm_metadata_distinguishes_legacy_and_v2_host_imports(self):
        def integer(value):
            result = bytearray()
            while value >= 128:
                result.append((value & 127) | 128); value >>= 7
            result.append(value)
            return bytes(result)
        def name(value):
            data = value.encode()
            return integer(len(data)) + data
        def module(imports, exports):
            # Interface-only fixture bytes; never instantiated or executed.
            imported = integer(len(imports)) + b"".join(name(owner) + name(symbol) + b"\0\0" for owner, symbol in imports)
            exported = integer(len(exports)) + b"".join(name(symbol) + b"\0\0" for symbol in exports)
            return b"\0asm\1\0\0\0\2" + integer(len(imported)) + imported + b"\7" + integer(len(exported)) + exported
        imports = [("stateless_host", "dispatch")]
        exports = ["stateless_abi_version", "stateless_wasm_model_new", "stateless_enumerate", "stateless_record", "stateless_replay"]
        extra = ["stateless_v2_" + value for value in (
            "abi_version", "capabilities", "wasm_session_new", "session_close", "check_observed",
            "enumerate", "record_cursor_limited", "replay", "fuzz", "shrink", "trace_convert",
            "ring_new", "ring_observe", "ring_snapshot", "result_release",
        )]
        v1 = self.root / "stateless.wasm"
        v2 = self.root / "stateless_v2.wasm"
        v1.write_bytes(module(imports, exports))
        v2.write_bytes(module(imports + [("stateless_v2", "dispatch"), ("stateless_v2", "release")], exports + extra))
        checker["inspect_wasm_pair"](self.root)
        v1.write_bytes(v2.read_bytes())
        with self.assertRaisesRegex(ValueError, "default Wasm imports changed"):
            checker["inspect_wasm_pair"](self.root)
        v1.write_bytes(module(imports, exports))
        v2.write_bytes(v1.read_bytes())
        with self.assertRaisesRegex(ValueError, "separate host-import"):
            checker["inspect_wasm_pair"](self.root)

    def test_missing_runtime_gates_cannot_become_a_ready_default(self):
        report = gates["aggregate"](self.source, "python", "target", [])
        self.assertFalse(report["default_switch_ready"])
        self.assertEqual(report["gates"]["portable_profiles"]["status"], "pending")

    def test_compilation_cannot_satisfy_a_runtime_gate(self):
        receipt = {
            "schema": "stateless.binding-qualification.v1", "source_sha256": self.source["sha256"],
            "language": "python", "target": "target", "mode": "compile",
            "package_files": {"library": "sha"},
            "gates": {"portable_profiles": {"status": "passed"}},
        }
        path = self.root / "receipt.json"
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "executed runtime"):
            gates["aggregate"](self.source, "python", "target", [path])

    def test_different_artifact_receipts_cannot_be_combined(self):
        receipt = {
            "schema": "stateless.binding-qualification.v1", "source_sha256": self.source["sha256"],
            "language": "c", "target": "target", "mode": "inspect",
            "package_files": {"library": "first"}, "gates": {},
        }
        first, second = self.root / "first.json", self.root / "second.json"
        first.write_text(json.dumps(receipt))
        receipt["package_files"]["library"] = "second"
        second.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "different package bytes"):
            gates["aggregate"](self.source, "c", "target", [first, second])

    def test_failed_gate_cannot_be_hidden_by_another_pass(self):
        receipt = {
            "schema": "stateless.binding-qualification.v1", "source_sha256": self.source["sha256"],
            "language": "c", "target": "target", "mode": "runtime",
            "package_files": {"library": "same"}, "gates": {"lifetime": {"status": "failed"}},
        }
        first, second = self.root / "failed.json", self.root / "passed.json"
        first.write_text(json.dumps(receipt))
        receipt["gates"]["lifetime"]["status"] = "passed"
        second.write_text(json.dumps(receipt))
        for paths in ([first, second], [second, first]):
            report = gates["aggregate"](self.source, "c", "target", paths)
            self.assertEqual(report["gates"]["lifetime"]["status"], "failed")

    def measurement(self):
        return {
            "schema": "stateless.binding-measurements.v1", "execution": "completed",
            "command": ["fixture-runner"], "environment": {"os": "fixture", "cpu": "fixture"},
            "language": "python", "target": "target", "source_sha256": "source",
            "package_files": {"library": "sha"},
            "cells": [{"workload": workload, "model": model, "case": "fixture", "samples": 3,
                       "implementation": "fixture-measurement", "runtime": {"version": "fixture"},
                       "baseline_kind": "independent" if workload in benchmarks["PORTABLE_OPERATIONS"] else "rust-native",
                       "parameters": {"seed": 1, "bound": 10}, "corpus_sha256": "corpus",
                       "metrics": {metric: 10 for metric in benchmarks["REQUIRED_METRICS"]}}
                      for workload in benchmarks["WORKLOADS"] for model in benchmarks["MODELS"]],
        }

    def test_performance_requires_complete_executed_matrix_and_comparable_inputs(self):
        baseline, candidate = self.measurement(), self.measurement()
        budgets = {"schema": "stateless.binding-budgets.v1", "approved_by": "fixture",
                   "metrics": {metric: {"max_ratio": 1.1} for metric in benchmarks["REQUIRED_METRICS"]}}
        self.assertTrue(all(item["passed"] for item in benchmarks["compare"](baseline, candidate, budgets)))
        candidate["cells"][0]["metrics"]["median_ns"] = 12
        self.assertFalse(all(item["passed"] for item in benchmarks["compare"](baseline, candidate, budgets)))
        candidate["cells"][0]["corpus_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "inputs differ"):
            benchmarks["compare"](baseline, candidate, budgets)
        candidate = self.measurement()
        candidate["cells"].pop()
        with self.assertRaisesRegex(ValueError, "every operation"):
            benchmarks["compare"](baseline, candidate, budgets)
        candidate = self.measurement()
        candidate["execution"] = "not_run"
        with self.assertRaisesRegex(ValueError, "completed execution"):
            benchmarks["compare"](baseline, candidate, budgets)

    def test_unsupported_independent_baseline_is_not_filled_with_fake_measurements(self):
        baseline, candidate = self.measurement(), self.measurement()
        budgets = {"schema": "stateless.binding-budgets.v1", "approved_by": "fixture",
                   "metrics": {metric: {"max_ratio": 1.1} for metric in benchmarks["REQUIRED_METRICS"]}}
        next(cell for cell in baseline["cells"] if cell["workload"] == "fuzz")["baseline_kind"] = "independent"
        with self.assertRaisesRegex(ValueError, "does not expose"):
            benchmarks["compare"](baseline, candidate, budgets)


if __name__ == "__main__":
    unittest.main()
