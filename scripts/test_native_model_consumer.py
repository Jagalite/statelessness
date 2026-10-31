"""Exercise the actual N13 consumer against the public Python replay API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONSUMER = ROOT / "examples/native-model/python/consumer.py"


class NativeModelConsumerTests(unittest.TestCase):
    def test_produce_and_transcode_preserve_artifacts_and_replay_receipts(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python/src"))
        with tempfile.TemporaryDirectory() as directory:
            produced = Path(directory) / "produced.json"
            transcoded = Path(directory) / "transcoded.json"
            producer = subprocess.run(
                [sys.executable, str(CONSUMER), "produce", str(produced)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(producer.returncode, 0, producer.stderr)
            consumer = subprocess.run(
                [sys.executable, str(CONSUMER), "transcode", str(produced), str(transcoded)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(consumer.returncode, 0, consumer.stderr)
            self.assertEqual(transcoded.read_bytes(), produced.read_bytes())
            receipt = json.loads(consumer.stdout)
            self.assertTrue(receipt["capture_failure_controls"])
            # Independent scenario counts: one initial observation and three
            # replayed turns, with no reexecution introduced by the observer.
            expected = dict(initial=0, reducer=3, state=4, transition=3,
                            encode_state=4, encode_input=3, encode_output=3,
                            decode_state=1, decode_input=3, limits=4)
            self.assertEqual(receipt["replay_counts"], {"v1": expected, "v2": expected})
            for version in ("v1", "v2"):
                self.assertEqual(receipt["receipt"][version]["outcome"], "exact")
                self.assertEqual(receipt["receipt"][version]["steps_verified"], 3)
                self.assertTrue(receipt["receipt"][version]["failure_reproduced"])


if __name__ == "__main__":
    unittest.main()
