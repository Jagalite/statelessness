"""Harness integrity tests, separate from native library semantics."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("conformance_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class HarnessTests(unittest.TestCase):
    def test_comparison_does_not_equate_boolean_integer_float(self):
        self.assertFalse(run.equivalent({"states": True}, {"states": 1}))
        self.assertFalse(run.equivalent({"states": 1.0}, {"states": 1}))
        self.assertTrue(run.equivalent({"a": 1, "b": 2}, {"b": 2, "a": 1}))
        self.assertFalse(run.equivalent([1, 2], [2, 1]))

    def test_response_rejects_duplicate_and_float(self):
        for value in ('{"states":1,"states":2}', '{"states":1.0}', '{"x":NaN}'):
            with self.assertRaises(ValueError):
                run.response_json(value)

    def test_missing_responses_are_not_pass(self):
        with self.assertRaises(RuntimeError):
            run.invoke([sys.executable, "-c", "pass"], [{"operation": "hello"}], 5)

    def test_process_crash_is_not_pass(self):
        with self.assertRaises(RuntimeError):
            run.invoke([sys.executable, "-c", "raise SystemExit(3)"], [], 5)

    def test_generated_topologies_are_unique(self):
        cases = list(run.generated_graphs())
        self.assertEqual(len(cases), 530)
        self.assertEqual(len({c["id"] for c in cases}), 530)

    def test_every_normative_rule_has_golden_links(self):
        text = (HERE.parent / "spec" / "README.md").read_text(encoding="utf-8")
        rules = set(re.findall(r"\*\*([A-Z]+-[0-9]{3})\.", text))
        cases = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))["cases"]
        used = {r for c in cases for r in c["rules"]}
        self.assertEqual(rules, used)

    def test_corpus_reproduction_is_exact(self):
        before = (HERE / "corpus.json").read_bytes()
        subprocess.run([sys.executable, str(HERE / "build_corpus.py")], check=True, capture_output=True)
        self.assertEqual((HERE / "corpus.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
