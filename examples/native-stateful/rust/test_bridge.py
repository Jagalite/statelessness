"""Run-config sidecar tests; no Rust executable is needed for these checks."""
import json
from pathlib import Path
import tempfile
import unittest

from bridge import main, read_envelope, sidecar_path, write_envelope
from statelessness.core import Metadata, Trace
from statelessness.trace_binary import AuditTrace, RunConfig, audit_to_bytes
from statelessness.trace_json import dumps


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'trace.json'
        self.trace = Trace(Metadata('fixture'), b'initial', (), (), 'completed')

    def test_binary_configuration_roundtrip_is_bound_to_exact_json(self):
        for seed in (None, 0, (1 << 64) - 1):
            config = RunConfig('fuzz-λ', seed, (('same', 'one'), ('same', '二'), ('', '')))
            original = AuditTrace.from_portable(self.trace, config)
            write_envelope(self.path, original)
            self.assertEqual(read_envelope(self.path), original)
            self.assertEqual(self.path.read_text(), dumps(self.trace))
        self.path.write_bytes(self.path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            read_envelope(self.path)

    def test_native_binary_export_preserves_recorded_configuration(self):
        config = RunConfig('native-fixture', 17, (('duplicate', 'one'), ('duplicate', 'two')))
        original = AuditTrace.from_portable(self.trace, config)
        Path(str(self.path) + '.audit.bin').write_bytes(audit_to_bytes(original))
        main('from-binary', str(self.path))
        self.assertEqual(read_envelope(self.path), original)
        self.assertEqual(self.path.read_text(), dumps(self.trace))

    def test_missing_or_malformed_sidecar_never_invents_configuration(self):
        self.path.write_text(dumps(self.trace))
        with self.assertRaises(FileNotFoundError):
            read_envelope(self.path)
        write_envelope(self.path, AuditTrace.from_portable(self.trace, RunConfig()))
        sidecar = sidecar_path(self.path)
        data = json.loads(sidecar.read_text())
        for seed in ('00', '-1', '18446744073709551616', 0):
            data['seed'] = seed
            sidecar.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read_envelope(self.path)
        sidecar.write_text('{"format":"a","format":"b"}')
        with self.assertRaises(ValueError):
            read_envelope(self.path)


if __name__ == '__main__':
    unittest.main()
