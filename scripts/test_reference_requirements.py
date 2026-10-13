import copy
import tempfile
import unittest
from pathlib import Path
from review_requirements import check_catalog_files, compare, load, review_matches, validate
from check_reference_mutations import check

ROOT = Path(__file__).resolve().parents[1]

class RequirementTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load(ROOT / "docs/requirements/controller.json")

    def test_current_catalog_and_seed_corpus_agree(self):
        validate(self.catalog)
        check_catalog_files(ROOT, self.catalog)

    def test_removed_property_missing_mutation_target_is_invalid(self):
        del self.catalog["properties"]["controller.publication_current"]
        with self.assertRaises(ValueError):
            validate(self.catalog)

    def test_weakening_domains_bounds_and_goals_requires_review(self):
        for field, changed in [("input_domains", {"attempts": [1]}), ("bounds", {"max_depth": 1000}), ("coverage_targets", ["supersede_running"]), ("codec_version", 2)]:
            new = copy.deepcopy(self.catalog)
            new[field] = changed
            result = compare(self.catalog, new)
            self.assertTrue(result["requires_independent_review"])
            self.assertEqual(result["changes"][0]["category"], field)

    def test_corpus_content_changes_are_not_silent(self):
        result = compare(self.catalog, self.catalog, "old", "new")
        self.assertTrue(result["requires_independent_review"])
        self.assertEqual(result["changes"][0]["category"], "regression_corpus_content")

    def test_review_receipt_is_bound_to_exact_requirements_and_corpus(self):
        changed = copy.deepcopy(self.catalog)
        changed["bounds"]["max_depth"] += 1
        report = compare(self.catalog, changed, "a", "b")
        receipt = {key: report[key] for key in ("base_sha256", "current_sha256", "base_corpus_sha256", "current_corpus_sha256")}
        receipt.update(decision="approve_requirements_change", reviewer="external reviewer", rationale="reasoned review", approved_categories=["bounds", "regression_corpus_content"])
        self.assertTrue(review_matches(report, receipt))
        receipt["current_sha256"] = "stale"
        self.assertFalse(review_matches(report, receipt))

    def report(self, directory):
        results = []
        for name, target in self.catalog["mutations"].items():
            item = dict(mutation=name, target=target, status="killed")
            for label in ("original", "minimized"):
                item[label] = f"{name}-{label}.sttrace"
                (directory / item[label]).write_bytes(b"test fixture, never valid Rust evidence")
            results.append(item)
        return dict(schema_version=1, model=self.catalog["model"], build="test-fixture", results=results)

    def test_gate_replays_all_witnesses_and_rejects_missing_or_renamed(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            report = self.report(directory)
            calls = []
            check(report, self.catalog, directory, lambda *args: calls.append(args))
            self.assertEqual(len(calls), 18)
            bad = copy.deepcopy(report)
            bad["results"].pop()
            with self.assertRaises(ValueError):
                check(bad, self.catalog, directory)
            bad = copy.deepcopy(report)
            bad["results"][0]["target"] = "renamed"
            with self.assertRaises(ValueError):
                check(bad, self.catalog, directory)

    def test_survival_cutoff_and_missing_evidence_never_pass(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            report = self.report(directory)
            for status in ("survives", "not_evaluated", None):
                bad = copy.deepcopy(report)
                bad["results"][0]["status"] = status
                with self.assertRaises(ValueError):
                    check(bad, self.catalog, directory)
            (directory / report["results"][0]["minimized"]).unlink()
            with self.assertRaises(ValueError):
                check(report, self.catalog, directory)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text('{"schema_version":1,"schema_version":2}')
            with self.assertRaises(ValueError):
                load(path)

if __name__ == "__main__":
    unittest.main()
