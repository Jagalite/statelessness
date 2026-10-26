"""Candidate corpus shape and independent closed-form queue expectations."""
import csv
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def corpus():
    with (ROOT / "conformance/paired-v1.tsv").open(encoding="utf-8") as stream:
        cases = list(csv.DictReader(stream, delimiter="\t"))
    for case in cases:
        for key in ("max_states", "max_transitions", "max_depth", "states", "transitions", "skipped_checks"):
            case[key] = int(case[key])
        case["failed_ids"] = [] if case["failed_ids"] == "-" else case["failed_ids"].split(",")
        case["failed_phases"] = [] if case["failed_phases"] == "-" else case["failed_phases"].split(",")
        case["witness"] = [] if case["witness"] == "-" else list(map(int, case["witness"].split(",")))
    return dict(profile="paired-v1", version=1, status="experimental", cases=cases)


class PairedProfileTests(unittest.TestCase):
    def test_independent_counts_and_required_negative_cases(self):
        data = corpus()
        cases = {c["case"]: c for c in data["cases"]}
        self.assertEqual(len(cases), len(data["cases"]))
        self.assertEqual(cases["queue-exhausted"]["states"], sum(2 ** size for size in range(3)))
        self.assertEqual(cases["queue-exhausted"]["transitions"], 3 * sum(2 ** size for size in range(3)))
        self.assertEqual({c["mutation"] for c in cases.values()}, {"none", "state", "order", "omit", "reason", "initial"})
        self.assertEqual(cases["queue-exact-edge-budget"]["termination"], "graph_exhausted")
        self.assertEqual(cases["initial-mismatch"]["witness"], [])
        self.assertEqual(cases["initial-mismatch"]["transitions"], 0)
        self.assertEqual(cases["initial-mismatch"]["failed_phases"], ["initial_state"])
        for case in cases.values():
            self.assertEqual(len(case["failed_ids"]), len(case["failed_phases"]))
            self.assertEqual(case["termination"] == "failure_found", bool(case["failed_ids"]))
            self.assertEqual(case["skipped_checks"], 0)

    def test_normalized_schema_when_validator_is_available(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is an optional development dependency")
        schema = json.loads((ROOT / "spec/paired-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(corpus())


if __name__ == "__main__":
    unittest.main()
