"""Admission-only fixtures; no independent corpus or candidate execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from consumer import AUTHORITY, read_frozen_corpus
from qualify_guided_install import read_frozen_manifest


class SealAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.corpus = Path(self.directory.name) / "corpus.json"
        self.freeze = self.corpus.with_name("FREEZE.json")
        # This is a JSON-admission stub, not a behavioral expected result. No
        # engine, comparison function or independent oracle is ever invoked.
        self.corpus.write_text(json.dumps({"schema": "stateless-guided-v2/1",
            "source_commit": AUTHORITY, "cases": [{"id": "admission-stub", "expected": {
                "report": {}, "error": None, "trace": [], "generator_rng": [],
                "witness_replays": [], "shrink": None}}]}))
        self.digest = hashlib.sha256(self.corpus.read_bytes()).hexdigest()
        self.manifest = {"schema": "stateless-guided-v2-freeze/1", "revision": 3,
            "source_commit": AUTHORITY, "candidate_output_used": False, "cases": 1,
            "files": {"conformance/guided-v2/corpus.json": self.digest}}

    def write_freeze(self):
        self.freeze.write_text(json.dumps(self.manifest))

    def readers(self):
        return (read_frozen_corpus, read_frozen_manifest)

    def test_independent_files_map_admits_without_legacy_top_level_digest(self):
        self.write_freeze()
        for reader in self.readers():
            with self.subTest(reader=reader.__name__): reader(self.corpus, self.freeze)
        # An unrelated old-style field cannot override the actual files binding.
        self.manifest["corpus_sha256"] = "unrelated legacy field"
        self.write_freeze()
        for reader in self.readers(): reader(self.corpus, self.freeze)

    def test_missing_or_malformed_files_map_cannot_use_legacy_digest(self):
        self.manifest["corpus_sha256"] = self.digest
        for files in (None, [], {}, {"corpus.json": self.digest}):
            self.manifest["files"] = files
            self.write_freeze()
            for reader in self.readers():
                with self.subTest(files=files, reader=reader.__name__):
                    with self.assertRaises(ValueError): reader(self.corpus, self.freeze)

    def test_changed_corpus_bytes_and_missing_seal_fail_closed(self):
        for reader in self.readers():
            with self.assertRaises(FileNotFoundError): reader(self.corpus, self.freeze)
        self.write_freeze()
        self.corpus.write_bytes(self.corpus.read_bytes() + b"\n")
        for reader in self.readers():
            with self.assertRaises(ValueError): reader(self.corpus, self.freeze)

    def test_external_hashes_and_source_authority_remain_required_when_supplied(self):
        self.write_freeze()
        freeze_digest = hashlib.sha256(self.freeze.read_bytes()).hexdigest()
        read_frozen_corpus(self.corpus, self.freeze, self.digest, freeze_digest)
        with self.assertRaisesRegex(ValueError, "--corpus-sha256"):
            read_frozen_corpus(self.corpus, self.freeze, "wrong", freeze_digest)
        with self.assertRaisesRegex(ValueError, "--freeze-sha256"):
            read_frozen_corpus(self.corpus, self.freeze, self.digest, "wrong")
        self.manifest["source_commit"] = "unexpected"
        self.write_freeze()
        with self.assertRaisesRegex(ValueError, "source authority"):
            read_frozen_corpus(self.corpus, self.freeze)


if __name__ == "__main__":
    unittest.main()
