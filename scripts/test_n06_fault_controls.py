import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("controls", Path(__file__).with_name("run-n06-fault-controls.py"))
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


class CommandTests(unittest.TestCase):
    def test_output_limit_and_timeout_are_not_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "log limit"):
                controls.command(p, "large", [sys.executable, "-c", "print('x'*100000)"], p, os.environ, maximum=100)
            self.assertLessEqual((p / "large.log").stat().st_size, 100)
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                controls.command(p, "slow", [sys.executable, "-c", "import time;time.sleep(3)"], p, os.environ, timeout=.1)

    def test_descendant_is_not_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            code = "import os,time; p=os.fork(); os._exit(0) if p else (os.close(1),os.close(2),time.sleep(5))"
            with self.assertRaisesRegex(RuntimeError, "descendant"):
                controls.command(p, "fork", [sys.executable, "-c", code], p, os.environ, timeout=2)


if __name__ == "__main__":
    unittest.main()
