"""Fault-control integrity: exact sites, real dispatch omission, and oracle kills."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "python" / "src" / "statelessness"
spec = importlib.util.spec_from_file_location("conformance_mutations", HERE / "mutations.py")
mutations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutations)


class MutationTests(unittest.TestCase):
    def test_every_control_has_one_exact_site_and_compiles(self):
        self.assertEqual(len(mutations.MUTATIONS), 8)
        for name, (filename, old, new) in mutations.MUTATIONS.items():
            with self.subTest(mutation=name):
                original = (SOURCE / filename).read_text(encoding="utf-8")
                self.assertEqual(original.count(old), 1)
                changed = mutations.mutate_source(name, original)
                self.assertEqual(changed, original.replace(old, new, 1))
                self.assertNotEqual(changed, original)
                compile(changed, filename, "exec")
                self.assertEqual((SOURCE / filename).read_text(encoding="utf-8"), original)

    def test_missing_or_ambiguous_sites_fail_closed(self):
        for name, (_, old, _) in mutations.MUTATIONS.items():
            for source in ("", old + "\n" + old):
                with self.subTest(mutation=name, source=source):
                    with self.assertRaisesRegex(RuntimeError, "mutation site changed"):
                        mutations.mutate_source(name, source)

    def test_initial_mutation_omits_legacy_and_append_checker_execution(self):
        probe = '''
import sys
from statelessness import Check, Metadata, record
class Legacy:
    calls = 0
    def metadata(self): return Metadata("mutation-probe")
    def initial_state(self): return 0
    def encode_state(self, state): return b"0"
    def check_state(self, state):
        self.calls += 1
        return (Check("initial", "failed", "stop"),)
class Append(Legacy):
    def check_state(self, state): raise AssertionError("legacy fallback called")
    def check_state_into(self, state, sink):
        self.calls += 1
        sink.append(Check("initial", "failed", "stop"))
mutated = sys.argv[1] == "mutated"
for model in (Legacy(), Append()):
    trace = record(model, [])
    assert model.calls == (0 if mutated else 1), model.calls
    assert len(trace.initial_checks) == (0 if mutated else 1), trace
    assert trace.termination == ("completed" if mutated else "property_failed"), trace
'''
        original = (SOURCE / "core.py").read_text(encoding="utf-8")
        for mode in ("baseline", "mutated"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "statelessness"
                shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__"))
                if mode == "mutated":
                    (target / "core.py").write_text(
                        mutations.mutate_source("omit_initial_checks", original), encoding="utf-8")
                result = subprocess.run([sys.executable, "-c", probe, mode], cwd=directory,
                    env=dict(os.environ, PYTHONPATH=directory, PYTHONDONTWRITEBYTECODE="1"),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_eight_mutants_are_killed_without_modifying_engine(self):
        originals = {path: path.read_bytes() for path in SOURCE.glob("*.py")}
        result = subprocess.run([sys.executable, str(HERE / "mutations.py")],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["mutants"], report["detected"]), (8, 8))
        self.assertEqual({row["mutation"] for row in report["results"]}, set(mutations.MUTATIONS))
        for row in report["results"]:
            self.assertTrue(row["detected_by"])
        initial = next(row for row in report["results"] if row["mutation"] == "omit_initial_checks")
        self.assertIn("record-initial-failure-stops-before-invalid-input", initial["detected_by"])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original, str(path))


if __name__ == "__main__":
    unittest.main()
