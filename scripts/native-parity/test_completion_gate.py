"""Independent negative controls for completion evidence admission."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('gate', Path(__file__).with_name('completion-gate.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CompletionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        roots = ['python/src', 'python/tests', 'python/examples', 'conformance', 'scripts', 'examples', 'docs/native-parity']
        for directory in roots:
            p = self.root / directory / 'source.txt'
            p.parent.mkdir(parents=True)
            p.write_text('independent fixture')
        (self.root / 'python/pyproject.toml').write_text('version = "test"')
        inputs = ['python/pyproject.toml', 'python/README.md', 'python/LICENSE', 'python/qualify_debugger_installed.py']
        for name in inputs: (self.root/name).write_text('fixture packaging input')
        files = {str(p.relative_to(self.root)): gate.digest(p) for p in self.root.rglob('*') if p.is_file()}
        manifest = self.root / 'source.json'
        manifest.write_text(json.dumps(dict(schema='native-parity-source/1', roots=roots, inputs=inputs, files=files)))
        artifact = self.root / 'raw.log'; artifact.write_text('expected assertion passed')
        self.key = ('debug-session', 'python', 'macos/cpython', 'independent-tests')
        self.receipt = dict(schema='native-parity-evidence/1', rust_baseline=gate.BASELINE,
            requirement=self.key, source_manifest=dict(path='source.json', sha256=gate.digest(manifest)),
            execution=dict(kind='runtime', exit_code=0, skipped=0, argv=['python3','test.py'], kyoto_job='fixture', platform='macos',runtime='cpython'),
            assertions=[dict(id='assertion',outcome='passed',expected='1',observed='1')],
            review=dict(author='author',reviewer='independent',contract='fixture specification'))
        for name in ['tools','packages','corpora','logs']:
            self.receipt[name]=[dict(path=str(artifact),sha256=gate.digest(artifact))]

    def check(self):
        return gate.validate_receipt(self.root, self.receipt, {self.key})

    def test_valid_fixture_admission_is_not_parity(self):
        self.assertEqual(self.check(), self.key)

    def test_changed_source_and_added_source_fail(self):
        p=self.root/'python/src/source.txt';p.write_text('changed')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.check()
        p.write_text('independent fixture');(p.parent/'added.py').write_text('new')
        with self.assertRaisesRegex(ValueError,'membership'):self.check()

    def test_narrowed_manifest_fails(self):
        p=self.root/'source.json';m=json.loads(p.read_text());m['roots']=['python/src'];p.write_text(json.dumps(m))
        self.receipt['source_manifest']['sha256']=gate.digest(p)
        with self.assertRaisesRegex(ValueError,'scope narrowed'):self.check()

    def test_compile_only_skipped_and_failed_execution_fail(self):
        for patch in [dict(kind='compile'),dict(exit_code=1),dict(skipped=1),dict(argv=['cargo','test','--no-run'])]:
            before=self.receipt['execution'].copy();self.receipt['execution'].update(patch)
            with self.assertRaises(ValueError):self.check()
            self.receipt['execution']=before

    def test_assertion_missing_artifact_and_platform_mismatch_fail(self):
        self.receipt['assertions'][0]['observed']='2'
        with self.assertRaisesRegex(ValueError,'assertion'):self.check()
        self.receipt['assertions'][0]['observed']='1';self.receipt['execution']['platform']='linux'
        with self.assertRaisesRegex(ValueError,'platform'):self.check()
        self.receipt['execution']['platform']='macos';(self.root/'raw.log').unlink()
        with self.assertRaises(ValueError):self.check()

    def test_self_review_and_path_escape_fail(self):
        self.receipt['review']['reviewer']='author'
        with self.assertRaisesRegex(ValueError,'self-review'):self.check()
        self.receipt['source_manifest']['path']='../source.json'
        with self.assertRaisesRegex(ValueError,'nonlocal'):self.check()

    def test_new_symlink_directory_fails(self):
        (self.root/'python/src/imported').symlink_to(self.root/'examples', target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'): self.check()

    def test_platform_matrix_cannot_shrink(self):
        ledger=gate.read(gate.ROOT/'docs/native-parity/requirements.json')
        ledger['platforms']['python']=['macos/cpython']
        with self.assertRaisesRegex(ValueError,'matrix'): gate.validate_ledger(gate.ROOT,ledger)

    def test_inventory_default_is_not_promoted(self):
        ledger=gate.read(gate.ROOT/'docs/native-parity/requirements.json')
        ids, mapped=gate.validate_ledger(gate.ROOT,ledger)
        self.assertEqual(len(ids),5864)
        self.assertEqual(len(mapped),4)
        self.assertEqual(len(gate.requirements(ledger)),4032)
        ledger['default_symbol_disposition']='qualified'
        with self.assertRaisesRegex(ValueError,'promoted'):gate.validate_ledger(gate.ROOT,ledger)


if __name__ == '__main__':unittest.main()
