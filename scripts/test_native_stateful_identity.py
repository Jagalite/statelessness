import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from statelessness.core import Metadata, Trace, record
from native_stateful_identity import IdentityError, verify_and_replay, verify_and_compare_cross_build


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Model:
    def __init__(self, build): self.build = build
    def metadata(self): return Metadata('ring', build=self.build)
    def decode_state(self, value): return int(value)
    def encode_state_into(self, value, out): out.append(str(value).encode())
    def check_state(self, value): return ()
    def initial_state(self): return 0


class IdentityTests(unittest.TestCase):
    def test_pinned_mapping_never_overrides_build_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            for name in ('producer_source', 'producer_artifact', 'consumer_source', 'consumer_artifact'):
                path = root / name
                path.write_bytes(name.encode())
                files[name] = path
            semantic = {'name': 'ring', 'model_version': 1, 'properties_version': 1, 'codec_version': 1}
            manifest = {'version': 1, 'semantic': semantic,
                'producer': {'build': 'rust-build', 'source': {'producer_source': digest(b'producer_source')}, 'artifact': {'producer_artifact': digest(b'producer_artifact')}},
                'consumer': {'build': 'native-build', 'source': {'consumer_source': digest(b'consumer_source')}, 'artifact': {'consumer_artifact': digest(b'consumer_artifact')}}}
            raw = json.dumps(manifest).encode()
            trace = Trace(Metadata('ring', build='rust-build'), b'0', (), (), 'completed')
            result = verify_and_replay(Model('native-build'), trace, raw, digest(raw), files)
            self.assertEqual(result.report.outcome, 'incompatible')
            self.assertFalse(result.report.build_matches)
            self.assertEqual(result.producer_build, 'rust-build')
            self.assertEqual(result.consumer_build, 'native-build')
            with self.assertRaises(IdentityError):
                verify_and_replay(Model('wrong-build'), trace, raw, digest(raw), files)
            with self.assertRaises(IdentityError):
                verify_and_replay(Model('native-build'), trace, raw, '0' * 64, files)
            wrong_semantic = dict(manifest, semantic=dict(semantic, name='other'))
            wrong_raw = json.dumps(wrong_semantic).encode()
            with self.assertRaisesRegex(IdentityError, 'semantic identity'):
                verify_and_replay(Model('native-build'), trace, wrong_raw, digest(wrong_raw), files)
            duplicate = raw.replace(b'"version": 1', b'"version": 1, "version": 1', 1)
            with self.assertRaisesRegex(IdentityError, 'duplicate'):
                verify_and_replay(Model('native-build'), trace, duplicate, digest(duplicate), files)
            files['consumer_artifact'].write_bytes(b'changed consumer')
            with self.assertRaisesRegex(IdentityError, 'file digest'):
                verify_and_replay(Model('native-build'), trace, raw, digest(raw), files)
            files['consumer_artifact'].write_bytes(b'consumer_artifact')
            files['producer_source'].write_bytes(b'changed')
            with self.assertRaises(IdentityError):
                verify_and_replay(Model('native-build'), trace, raw, digest(raw), files)

    def test_matching_build_runs_strict_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'f'
            path.write_bytes(b'v')
            file_hash = digest(b'v')
            side = {'build': 'same', 'source': {'file': file_hash}, 'artifact': {'file': file_hash}}
            raw = json.dumps({'version': 1, 'semantic': {'name': 'ring', 'model_version': 1, 'properties_version': 1, 'codec_version': 1}, 'producer': side, 'consumer': side}).encode()
            trace = Trace(Metadata('ring', build='same'), b'0', (), (), 'completed')
            result = verify_and_replay(Model('same'), trace, raw, digest(raw), {'file': path})
            self.assertEqual(result.report.outcome, 'exact')
            self.assertTrue(result.report.build_matches)

    def test_explicit_cross_build_profile_runs_real_record_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            for name in ('producer_source', 'producer_artifact', 'consumer_source', 'consumer_artifact'):
                path = root / name
                path.write_bytes(name.encode())
                files[name] = path
            semantic = {'name': 'ring', 'model_version': 1, 'properties_version': 1, 'codec_version': 1}
            manifest = {'version': 1, 'semantic': semantic,
                'producer': {'build': 'rust-build', 'source': {'producer_source': digest(b'producer_source')}, 'artifact': {'producer_artifact': digest(b'producer_artifact')}},
                'consumer': {'build': 'native-build', 'source': {'consumer_source': digest(b'consumer_source')}, 'artifact': {'consumer_artifact': digest(b'consumer_artifact')}}}
            raw = json.dumps(manifest).encode()
            trace = record(Model('rust-build'), [])
            result = verify_and_compare_cross_build(Model('native-build'), trace, raw, digest(raw), files)
            self.assertEqual(result.strict_report.outcome, 'incompatible')
            self.assertFalse(result.strict_report.build_matches)
            self.assertEqual(result.observation_report.outcome, 'exact')
            self.assertFalse(result.observation_report.build_matches)
            class ChangingModel(Model):
                def __init__(self): super().__init__('native-build'); self.calls = 0; self.decodes = 0
                def metadata(self):
                    self.calls += 1
                    return Metadata('ring', build='native-build' if self.calls < 3 else 'unattested-build')
                def decode_state(self, value): self.decodes += 1; return super().decode_state(value)
            changing = ChangingModel()
            with self.assertRaises(Exception):
                verify_and_compare_cross_build(changing, trace, raw, digest(raw), files)
            self.assertEqual(changing.decodes, 0)
            with self.assertRaises(IdentityError):
                verify_and_compare_cross_build(Model('wrong-build'), trace, raw, digest(raw), files)
            files['consumer_artifact'].write_bytes(b'tampered')
            with self.assertRaises(IdentityError):
                verify_and_compare_cross_build(Model('native-build'), trace, raw, digest(raw), files)


if __name__ == '__main__':
    unittest.main()
