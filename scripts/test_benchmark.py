"""Benchmark evidence tests; these do not build or execute Rust."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import benchmark


def fixture_rows(suite="engine", iterations=20, samples=3, warmup=4):
    rows = []
    for case, (units, unit) in benchmark.CASES[suite].items():
        count = benchmark.expected_iterations(suite, case, iterations)
        rows.append({"schema_version": 1, "suite": suite, "case": case, "unit": unit,
                     "units_per_operation": units, "iterations_per_sample": count,
                     "warmup_iterations": warmup,
                     "elapsed_ns": [count * value for value in (10, 20, 30)][:samples]})
        if suite in benchmark.INSTRUMENTED_SUITES:
            rows[-1].update(workload={"seed": 42, "state_payload_bytes": 32768},
                            allocation_probe={"operations": 32, "allocations": 64,
                                              "reallocations": 2, "requested_bytes": 8192, "timed": False})
    return rows


def encode_rows(rows):
    return "Cargo diagnostic\n" + "\n".join(json.dumps(row, separators=(",", ":")) for row in rows)


class SampleTests(unittest.TestCase):
    def test_raw_samples_units_and_variance(self):
        rows = benchmark.parse_samples(encode_rows(fixture_rows()), "engine", 20, 3, 4)
        direct = rows[0]
        self.assertEqual(direct["ns_per_operation"], [10, 20, 30])
        self.assertEqual(direct["ns_per_operation_summary"]["sample_stddev"], 10)
        self.assertEqual(direct["ns_per_operation_summary"]["p95"], 29)
        recorded = next(row for row in rows if row["case"] == "record_three")
        self.assertEqual(recorded["ns_per_unit"], [10 / 3, 20 / 3, 10])

    def test_rejects_missing_duplicate_and_invalid_evidence(self):
        fixtures = fixture_rows()
        bad_sets = [fixtures[:-1], [*fixtures, fixtures[0]]]
        for field, value in (("iterations_per_sample", 21), ("units_per_operation", 0),
                             ("elapsed_ns", [0, 0, 0]), ("elapsed_ns", [1, 2]),
                             ("elapsed_ns", [1, 2, True]), ("suite", "unknown"),
                             ("schema_version", 2), ("warmup_iterations", 100)):
            bad_sets.append([{**fixtures[0], field: value}, *fixtures[1:]])
        for rows in bad_sets:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                benchmark.parse_samples(encode_rows(rows), "engine", 20, 3, 4)


class ProvenanceTests(unittest.TestCase):
    def test_compiled_controller_requirements_are_source_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirements = root / "docs" / "requirements"
            requirements.mkdir(parents=True)
            for name in ("controller-properties.txt", "controller.json"):
                source = requirements / name
                source.write_text("original")
                before = benchmark.source_context(root)
                self.assertIn(f"docs/requirements/{name}", before["files_sha256"])
                source.write_text("changed")
                self.assertNotEqual(before["inputs_sha256"], benchmark.source_context(root)["inputs_sha256"])

    def test_environment_binds_incremental_compilation_setting(self):
        with tempfile.TemporaryDirectory() as temp, patch("benchmark.probe", return_value={}):
            context = benchmark.environment_context(Path(temp), None,
                {"CARGO_HOME": temp, "CARGO_INCREMENTAL": "0"})
            self.assertEqual(context["build_environment"]["CARGO_INCREMENTAL"], "0")

    def test_pinned_toolchain_selects_actual_compiler_despite_non_shim_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compiler = root / "toolchain" / "rustc"
            compiler.parent.mkdir()
            compiler.touch()
            discovered = subprocess.CompletedProcess([], 0, str(compiler) + "\n", "")
            inherited = {"PATH": "/opt/homebrew/bin", "RUSTC": "/other/compiler"}
            with patch("benchmark.subprocess.run", return_value=discovered):
                selected = benchmark.select_toolchain(root, "1.90.0", inherited)
            self.assertEqual(selected["RUSTC"], str(compiler.resolve()))
            self.assertTrue(selected["PATH"].startswith(str(compiler.parent.resolve())))
            self.assertEqual(inherited["RUSTC"], "/other/compiler")
            self.assertEqual(benchmark.cargo_command(["engine"], "bench", "1.90.0", True)[:4],
                             ["rustup", "run", "1.90.0", "cargo"])

    def test_uncommitted_input_changes_change_digest_and_build_output_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "benches").mkdir()
            (root / "Cargo.toml").write_text("fixture")
            source = root / "benches" / "engine.rs"
            source.write_text("one")
            original = benchmark.source_context(root)
            self.assertFalse(original["git"]["available"])
            source.write_text("two")
            changed = benchmark.source_context(root)
            self.assertNotEqual(original["inputs_sha256"], changed["inputs_sha256"])
            target = root / "crates" / "fixture" / "target"
            target.mkdir(parents=True)
            (target / "artifact").write_text("not a source input")
            self.assertEqual(changed["inputs_sha256"], benchmark.source_context(root)["inputs_sha256"])


class QueueTests(unittest.TestCase):
    def test_policy_rejection_stops_without_wait_or_fallback(self):
        rejected = subprocess.CompletedProcess([], 2, "", "bench requires --no-run")
        with tempfile.TemporaryDirectory() as temp, patch("benchmark.subprocess.run", return_value=rejected) as run:
            root = Path(temp)
            command = benchmark.cargo_command(["engine"], "bench", None, False)
            result = benchmark.execute(command, root, {}, root / "run.log", Path("/kyoto.py"))
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(result["stage"], "submission")
            self.assertEqual(run.call_count, 1)
            submitted = run.call_args.args[0]
            self.assertEqual(submitted[submitted.index("--") + 1:], command)

    def test_queue_receipt_retains_visible_cargo_command(self):
        responses = [subprocess.CompletedProcess([], 0, '{"id":"job1","status":"queued"}', ""),
                     subprocess.CompletedProcess([], 0, '{"id":"job1","status":"succeeded","exit_code":0}', ""),
                     subprocess.CompletedProcess([], 0, "", "")]
        with tempfile.TemporaryDirectory() as temp, patch("benchmark.subprocess.run", side_effect=responses) as run, patch("benchmark.time.sleep"):
            root = Path(temp)
            command = benchmark.cargo_command(["engine"], "dev", "1.90.0", True)
            with contextlib.redirect_stdout(io.StringIO()):
                result = benchmark.execute(command, root, {}, root / "run.log", Path("/kyoto.py"))
            self.assertEqual(result["kyoto_job_id"], "job1")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("--no-run", run.call_args_list[0].args[0])
            self.assertEqual(run.call_args_list[1].args[0][-2:], ["show", "job1"])
            self.assertEqual(run.call_args_list[2].args[0][-2:], ["logs", "job1"])
            submitted = run.call_args_list[0].args[0]
            self.assertEqual(submitted[submitted.index("--user-id") + 1], result["kyoto_owner_id"])

    def test_deadline_cancels_only_the_submitted_owner(self):
        responses = [subprocess.CompletedProcess([], 0, '{"id":"job1","status":"queued"}', ""),
                     subprocess.CompletedProcess([], 0, '{"id":"job1","status":"cancelled"}', "")]
        with tempfile.TemporaryDirectory() as temp, patch("benchmark.subprocess.run", side_effect=responses) as run:
            root = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                result = benchmark.execute(["cargo", "bench", "--no-run"], root, {},
                                           root / "run.log", Path("/kyoto.py"), timeout=0)
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["cancellation_requested"], ["job1"])
            self.assertEqual(run.call_args_list[1].args[0][-4:],
                             ["cancel", "job1", "--user-id", result["kyoto_owner_id"]])

    def test_interrupt_or_malformed_poll_retains_and_cancels_owned_job(self):
        for response in (KeyboardInterrupt(), subprocess.CompletedProcess([], 0, "[]", "")):
            responses = [subprocess.CompletedProcess([], 0, '{"id":"job1","status":"queued"}', ""),
                         response,
                         subprocess.CompletedProcess([], 0, '{"id":"job1","status":"cancelled"}', "")]
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temp, \
                    patch("benchmark.subprocess.run", side_effect=responses), patch("benchmark.time.sleep"):
                root = Path(temp)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = benchmark.execute(["cargo", "bench", "--no-run"], root, {},
                                               root / "run.log", Path("/kyoto.py"))
                self.assertEqual(result["exit_code"], 1)
                self.assertEqual(result["kyoto_job_id"], "job1")
                self.assertEqual(result["cancellation_requested"], ["job1"])


class DriverTests(unittest.TestCase):
    def run_mocked(self, extra_args, execute):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            (root / "Cargo.toml").write_text("fixture")
            output = Path(temp) / "evidence"
            with patch.object(benchmark, "ROOT", root), \
                    patch.object(benchmark, "environment_context", return_value={}), \
                    patch.object(benchmark, "execute", side_effect=execute), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = benchmark.main(["--output", str(output), "--suite", "engine",
                                       "--iterations", "20", "--samples", "3", "--warmup", "4",
                                       *extra_args])
            return code, json.loads((output / "report.json").read_text())

    def test_compile_only_cannot_claim_measurement(self):
        commands = []

        def compile_command(command, root, environment, log_path, kyoto, timeout):
            commands.append(command)
            self.assertIn("--no-run", command)
            log_path.write_text("compiled")
            return {"exit_code": 0, "command": command}

        code, report = self.run_mocked(["--compile-only"], compile_command)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "compiled_only")
        self.assertEqual(report["measurements"], [])
        self.assertEqual(len(commands), 1)

    def test_source_mutation_invalidates_successful_command(self):
        def mutate(command, root, environment, log_path, kyoto, timeout):
            (root / "Cargo.toml").write_text("changed during run")
            return {"exit_code": 0, "command": command}

        code, report = self.run_mocked(["--compile-only"], mutate)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["source_stable"])

    def test_measurement_requires_complete_valid_logs(self):
        def measured(command, root, environment, log_path, kyoto, timeout):
            log_path.write_text("compiled" if "--no-run" in command else encode_rows(fixture_rows()))
            return {"exit_code": 0, "command": command}

        code, report = self.run_mocked([], measured)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(len(report["measurements"]), 6)


class RoadmapTests(unittest.TestCase):
    def test_debug_benchmark_helpers_are_self_contained_and_in_sync(self):
        for name in ("mod.rs", "allocations.rs"):
            core = benchmark.ROOT / "benches" / "support" / name
            debug = benchmark.ROOT / "crates/statelessness-debug/benches/support" / name
            self.assertEqual(core.read_bytes(), debug.read_bytes())

    def test_scaled_workloads_require_separate_non_timed_allocation_probe(self):
        fixtures = fixture_rows("roadmap")
        rows = benchmark.parse_samples(encode_rows(fixtures), "roadmap", 20, 3, 4)
        self.assertEqual(len(rows), len(benchmark.CASES["roadmap"]))
        self.assertEqual(rows[0]["iterations_per_sample"], 1)
        for field, value in (("allocation_probe", None), ("workload", {})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                benchmark.parse_samples(encode_rows([{**fixtures[0], field: value}, *fixtures[1:]]), "roadmap", 20, 3, 4)
        for key, value in (("timed", True), ("operations", 0), ("allocations", -1), ("requested_bytes", True)):
            first = {**fixtures[0], "allocation_probe": {**fixtures[0]["allocation_probe"], key: value}}
            with self.subTest(key=key), self.assertRaises(ValueError):
                benchmark.parse_samples(encode_rows([first, *fixtures[1:]]), "roadmap", 20, 3, 4)

    def test_debug_feature_variants_have_explicit_cargo_commands(self):
        for suite in ("diagnostics_enabled", "diagnostics_compiled_out"):
            command = benchmark.cargo_command([suite], "bench", "1.90.0", True)
            self.assertIn("--no-run", command)
            self.assertIn("statelessness-debug", command)
            self.assertIn("diagnostics", command)
            self.assertEqual("compiled-out-diagnostics" in command, suite.endswith("compiled_out"))
            benchmark.parse_samples(encode_rows(fixture_rows(suite)), suite, 20, 3, 4)
        with self.assertRaises(ValueError):
            benchmark.cargo_command(["engine", "diagnostics_enabled"], "bench", None, True)

    def test_queue_resources_are_scoped_and_unknown_values_are_not_invented(self):
        self.assertIsNone(benchmark.queue_resources(None)["cpu_seconds"])
        result = benchmark.queue_resources({"cpu_seconds": 1.5, "peak_rss_mb": 22.0,
                                            "wait_max_rss_mb": float("nan"), "rss_samples": True})
        self.assertEqual(result["cpu_seconds"], 1.5)
        self.assertEqual(result["peak_rss_mb"], 22.0)
        self.assertIsNone(result["wait_max_rss_mb"])
        self.assertIsNone(result["rss_samples"])
        self.assertIn("not per case", result["scope"])

    def test_mode_comparisons_never_promote_an_optimization(self):
        rows = benchmark.parse_samples(encode_rows(fixture_rows("roadmap")), "roadmap", 20, 3, 4)
        comparisons = benchmark.mode_comparisons(rows)
        self.assertEqual(len(comparisons), 4)
        self.assertTrue(all(row["promotion_decision"].startswith("none;") for row in comparisons))


def measured_report(multiplier=1):
    rows = fixture_rows()
    for row in rows:
        row["elapsed_ns"] = [row["iterations_per_sample"] * value * multiplier for value in (1000, 1001, 1002)]
    return {"status": "measured", "source_stable": True,
            "source": {"inputs_sha256": "source", "benchmark_contract_sha256": "fixture"},
            "source_after": {"inputs_sha256": "source"},
            "environment": {"rustc": {"exit_code": 0, "stdout": "rustc version"},
                            "cargo": {"exit_code": 0, "stdout": "cargo version"},
                            "hardware": {"os": "test", "machine": "test"}},
            "configuration": {"suites": ["engine"], "iterations": 20, "samples": 3,
                              "warmup_iterations": 4, "profile": "bench", "sample_kind": "batch means"},
            "measurements": rows}


class BaselineTests(unittest.TestCase):
    def test_local_cargo_configuration_changes_comparison_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".cargo").mkdir()
            configuration = root / ".cargo/config.toml"
            configuration.write_text("[build]\nrustflags = []\n")
            first = benchmark.source_context(root)
            configuration.write_text('[build]\nrustflags = ["-Copt-level=0"]\n')
            second = benchmark.source_context(root)
            self.assertNotEqual(first["benchmark_contract_sha256"], second["benchmark_contract_sha256"])

    def test_stable_comparison_allows_source_change_with_same_contract(self):
        candidate, baseline = measured_report(), measured_report()
        candidate["source"]["inputs_sha256"] = "new implementation"
        candidate["source_after"]["inputs_sha256"] = "new implementation"
        result = benchmark.compare_baseline(candidate, baseline, 10, .1)
        self.assertEqual(result["status"], "within_threshold")
        self.assertNotEqual(result["baseline_source"], result["candidate_source"])

    def test_regression_is_reported_from_raw_samples_not_forged_summary(self):
        candidate = measured_report(2)
        for row in candidate["measurements"]:
            row["ns_per_operation_summary"] = {"p50": 1, "coefficient_of_variation": 0}
        result = benchmark.compare_baseline(candidate, measured_report(), 10, .1)
        self.assertEqual(result["status"], "regression")
        self.assertTrue(all(row["regressed"] for row in result["cases"]))

    def test_incomplete_unstable_or_incomparable_receipts_fail(self):
        import copy
        baseline = measured_report()
        variants = []
        for field, value in (("status", "compiled_only"), ("source_stable", False)):
            item = copy.deepcopy(baseline); item[field] = value; variants.append(item)
        item = copy.deepcopy(baseline); item["source"]["benchmark_contract_sha256"] = "changed fixture"; variants.append(item)
        item = copy.deepcopy(baseline); item["environment"]["rustc"]["stdout"] = "different compiler"; variants.append(item)
        item = copy.deepcopy(baseline); item["environment"] = {}; variants.append(item)
        item = copy.deepcopy(baseline); item["measurements"][0]["elapsed_ns"] = [1, 100000, 100]; variants.append(item)
        item = copy.deepcopy(baseline); item["measurements"] = item["measurements"][:-1]; variants.append(item)
        item = copy.deepcopy(baseline); item["source_after"]["inputs_sha256"] = "changed midrun"; variants.append(item)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                benchmark.compare_baseline(candidate, baseline, 10, .1)

    def test_compile_only_cannot_claim_baseline_comparison(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            benchmark.main(["--compile-only", "--baseline", "does-not-exist.json"])


if __name__ == "__main__":
    unittest.main()
