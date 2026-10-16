"""Fail-closed PR base and approval-scope checks; never invokes Git or Rust."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import check_requirement_changes as gate


class RequirementChangeTests(unittest.TestCase):
    def invoke(self, tree, read=None):
        commands = [subprocess.CompletedProcess([], 0, b"", b""), subprocess.CompletedProcess([], 0, tree, "")]
        output = io.StringIO()
        with patch.object(gate.subprocess, "run", side_effect=commands), patch.object(gate, "git_read", side_effect=read), contextlib.redirect_stdout(output):
            code = gate.main(["--base-ref", "base-fixture"])
        return code, json.loads(output.getvalue())

    def test_new_baseline_does_not_claim_independent_review(self):
        code, report = self.invoke("")
        self.assertEqual(code, 0)
        self.assertEqual(report["review_status"], "new_baseline_awaiting_independent_review")
        self.assertTrue(report["validation_only"])

    def test_inaccessible_base_is_not_absent_baseline(self):
        with patch.object(gate.subprocess, "run", side_effect=subprocess.CalledProcessError(128, ["git"])):
            with self.assertRaises(subprocess.CalledProcessError):
                gate.main(["--base-ref", "unavailable"])

    def test_phase_changes_require_review_even_with_identical_catalog_text(self):
        def read(_, path):
            data = (gate.ROOT / path).read_bytes()
            if path == gate.PROPERTIES:
                data = data.replace(b"state controller.state_valid", b"transition controller.state_valid")
            return data
        code, report = self.invoke(gate.CATALOG + "\n", read)
        self.assertEqual(code, 1)
        self.assertTrue(report["requires_independent_review"])
        self.assertEqual(report["changes"][0]["category"], "property_phases")

    def test_missing_original_corpus_fails_instead_of_waiving_regressions(self):
        def read(_, path):
            if path == gate.CORPUS:
                raise ValueError("missing original corpus")
            return (gate.ROOT / path).read_bytes()
        with self.assertRaisesRegex(ValueError, "missing original corpus"):
            self.invoke(gate.CATALOG + "\n", read)


if __name__ == "__main__":
    unittest.main()
