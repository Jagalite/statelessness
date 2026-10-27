"""Hand-derived finite candidate search expectations, independent of engines."""
import csv
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
NUMBERS = {
    "seed", "cases", "max_steps", "max_transitions", "mutation_percent",
    "max_candidates", "max_attempts", "max_replayed_transitions",
    "started_cases", "transitions", "attempts",
}
ARRAYS = {"original", "witness", "trace"}


def corpus():
    with (ROOT / "conformance/search-v1.tsv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    for row in rows:
        for key in NUMBERS:
            row[key] = None if row[key] == "-" else int(row[key])
        for key in ARRAYS:
            row[key] = None if row[key] == "-" else json.loads(row[key])
        value = row["validated_original"]
        row["validated_original"] = None if value == "-" else value == "true"
    return rows


class SearchProfileTests(unittest.TestCase):
    def setUp(self):
        self.rows = corpus()
        self.cases = {row["case"]: row for row in self.rows}

    def test_wire_shape_and_candidate_boundary(self):
        self.assertEqual(len(self.rows), len(self.cases))
        self.assertGreaterEqual(len(self.rows), 20)
        for row in self.rows:
            self.assertIn(row["operation"], {"fuzz", "shrink"})
            self.assertIn(row["control"], {"none", "cancelled", "deadline"})
            for key in NUMBERS:
                if row[key] is not None:
                    self.assertGreaterEqual(row[key], 0, (row["case"], key))
            if row["error"] != "-":
                self.assertEqual(row["termination"], "-")
                continue
            self.assertNotEqual(row["termination"], "-")
            if row["operation"] == "fuzz":
                self.assertEqual(row["transitions"], sum(map(len, row["trace"])))
                self.assertEqual(row["started_cases"], len(row["trace"]))
            else:
                self.assertIsNotNone(row["validated_original"])
                self.assertGreater(row["attempts"], 0)
        spec = (ROOT / "spec/search-v1.md").read_text(encoding="utf-8")
        self.assertIn("experimental candidate", spec)
        self.assertIn("not a global minimality guarantee", spec)

    def test_hand_authored_history_shape(self):
        histories = json.loads((ROOT / "conformance/search-v1-history.json").read_text(encoding="utf-8"))
        expected = {row["case"] for row in self.rows if row["operation"] == "shrink" and row["error"] == "-"}
        self.assertEqual(set(histories), expected)
        for name, history in histories.items():
            self.assertEqual(len(history), self.cases[name]["attempts"])
            self.assertEqual([item["attempt"] for item in history], list(range(1, len(history) + 1)))
            self.assertEqual(history[0]["operation"], {"kind": "validate_original"})
            for item in history:
                self.assertGreaterEqual(item["candidate_length"], 0)
                self.assertIn(item["outcome"], {"retained", "invalid_causality", "no_failure", "different_failure", "already_seen", "resource_limit"})

    def test_closed_form_fuzz_boundaries(self):
        for name in ("zero-cases", "zero-steps", "zero-transitions", "empty-domain"):
            self.assertEqual(self.cases["fuzz-" + name]["transitions"], 0)
        initial = self.cases["fuzz-initial-failure"]
        self.assertEqual((initial["witness"], initial["failed_phase"]), ([], "initial_state"))
        self.assertEqual(self.cases["fuzz-exact-transitions"]["termination"], "cases_completed")
        self.assertEqual(self.cases["fuzz-binary-seed-zero"]["trace"], [[1, 2]])
        for name in ("cancelled-entry", "deadline-entry"):
            self.assertEqual(self.cases["fuzz-" + name]["started_cases"], 0)

    def test_independent_deletion_and_target_goldens(self):
        threshold = self.cases["shrink-threshold"]
        # Validation, two halves, then three single deletions. No sequence
        # shorter than three singleton increments reaches sum >= 3.
        self.assertEqual(threshold["attempts"], 1 + 2 + 3)
        self.assertEqual(threshold["transitions"], 3 + 1 + 2 + 3 * 2)
        self.assertEqual(threshold["witness"], [1, 1, 1])
        causal = self.cases["shrink-causal"]
        # [2] cannot step; [1] steps once and passes.
        self.assertEqual((causal["attempts"], causal["transitions"]), (3, 2 + 0 + 1))
        phase = self.cases["shrink-phase-identity"]
        self.assertEqual(phase["witness"], [1])
        self.assertEqual((phase["target_id"], phase["failed_phase"]), ("same", "state"))
        cycle = self.cases["shrink-cycle"]
        # Validate [2], delete [], retain [1], delete []; [2] already visited.
        self.assertEqual((cycle["attempts"], cycle["transitions"], cycle["witness"]), (4, 2, [1]))
        stopped = self.cases["shrink-zero-transitions"]
        self.assertFalse(stopped["validated_original"])
        self.assertEqual(stopped["witness"], stopped["original"])


if __name__ == "__main__":
    unittest.main()
