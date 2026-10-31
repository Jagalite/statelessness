import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('n06_matrix', Path(__file__).with_name('qualify-n06-matrix.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class MatrixControls(unittest.TestCase):
    def test_sealed_oracle_and_exact_json_types(self):
        rows = m.load_oracle()
        self.assertEqual(len(rows), 49)
        self.assertNotEqual(m.canonical({'v': False}), m.canonical({'v': 0}))
        with self.assertRaises(ValueError):
            m.strict_json('{"x":1,"x":2}')

    def test_missing_engine_and_artifact_tamper_fail(self):
        with tempfile.TemporaryDirectory() as d:
            d = str(Path(d).resolve())
            exe = str(Path(sys.executable).resolve())
            engines = {}
            for name in m.ENGINES:
                script = Path(d) / (name + '.py')
                script.write_text('# ' + name)
                engines[name] = {'argv':[exe, str(script)], 'cwd':d, 'consumer':str(script),
                                 'artifacts':{exe:m.sha(Path(exe)), str(script):m.sha(script)}}
            manifest = {'schema':'n06-installed-commands/1', 'engines':engines}
            m.validate_manifest(manifest)
            missing = engines.pop('go')
            with self.assertRaisesRegex(ValueError, 'five engines'):
                m.validate_manifest(manifest)
            engines['go'] = missing
            Path(missing['consumer']).write_text('changed')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                m.validate_manifest(manifest)

    def test_duplicate_consumers_and_unpinned_script_fail(self):
        with tempfile.TemporaryDirectory() as d:
            d = str(Path(d).resolve())
            exe = str(Path(sys.executable).resolve())
            engines = {}
            for name in m.ENGINES:
                script = Path(d) / (name + '.py')
                script.write_text('# ' + name)
                engines[name] = {'argv':[exe, str(script)], 'cwd':d, 'consumer':str(script),
                                 'artifacts':{exe:m.sha(Path(exe)), str(script):m.sha(script)}}
            manifest = {'schema':'n06-installed-commands/1', 'engines':engines}
            engines['go'] = engines['python'].copy()
            with self.assertRaisesRegex(ValueError, 'distinct'):
                m.validate_manifest(manifest)
            extra = Path(d) / 'untrusted.py'; extra.write_text('pass')
            engines['go'] = dict(engines['go'], argv=[exe, engines['go']['consumer'], str(extra)])
            with self.assertRaisesRegex(ValueError, 'unpinned file'):
                m.validate_manifest(manifest)

    def test_stream_limit_and_environment_are_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            old = m.MAX_OUTPUT
            m.MAX_OUTPUT = 100
            try:
                with self.assertRaisesRegex(RuntimeError, 'output limit'):
                    m.run_engine('flood', {'argv':[sys.executable, '-c', 'print("x"*1000000)'], 'cwd':d}, Path(d), 10)
                self.assertEqual((Path(d)/'flood.stdout').stat().st_size, 100)
            finally:
                m.MAX_OUTPUT = old
            saved = os.environ.get('PYTHONPATH')
            saved_opt = os.environ.get('PYTHONOPTIMIZE')
            os.environ['PYTHONPATH'] = '/invalid/injected/path'
            os.environ['PYTHONOPTIMIZE'] = '2'
            try:
                rows, _ = m.run_engine('env', {'argv':[sys.executable, '-c',
                    'import os,json; print(json.dumps({"value":os.getenv("PYTHONPATH"),"opt":__debug__}))'], 'cwd':d}, Path(d), 10)
                self.assertEqual(rows, [{'value':None, 'opt':True}])
            finally:
                if saved is None: os.environ.pop('PYTHONPATH', None)
                else: os.environ['PYTHONPATH'] = saved
                if saved_opt is None: os.environ.pop('PYTHONOPTIMIZE', None)
                else: os.environ['PYTHONOPTIMIZE'] = saved_opt

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_remaining_descendant_fails_and_is_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            code = 'import os,time\nif os.fork()==0: time.sleep(30); os._exit(0)\nprint("[]",flush=True)\n'
            with self.assertRaisesRegex(RuntimeError, 'descendant remained'):
                m.run_engine('child', {'argv':[sys.executable, '-c', code], 'cwd':d}, Path(d), 5)

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_remaining_descendant_with_closed_pipes_fails(self):
        with tempfile.TemporaryDirectory() as d:
            code = ('import os,time\n'
                    'if os.fork()==0:\n'
                    ' os.close(1); os.close(2); time.sleep(30); os._exit(0)\n'
                    'print("[]",flush=True)\n')
            with self.assertRaisesRegex(RuntimeError, 'descendant remained'):
                m.run_engine('closed-child', {'argv':[sys.executable, '-c', code], 'cwd':d}, Path(d), 5)

    def test_failed_consumer_cannot_count_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with self.assertRaisesRegex(RuntimeError, 'exit 3'):
                m.run_engine('negative', {'argv':[sys.executable, '-c', 'raise SystemExit(3)'], 'cwd':d}, out, 10)
            self.assertTrue((out / 'negative.stderr').is_file())

    def test_end_to_end_rejects_mismatched_and_missing_engines(self):
        # Synthetic process controls exercise the runner, not the actual engines.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            d = str(root)
            oracle = root / 'rows.jsonl'
            oracle.write_bytes(m.CORPUS.read_bytes())
            exe = str(Path(sys.executable).resolve())
            engines = {}
            for name in m.ENGINES:
                emitter = root / (name + '.py')
                emitter.write_text('import pathlib,sys\nsys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())\n# ' + name)
                artifacts = {exe:m.sha(Path(exe)), str(emitter):m.sha(emitter), str(oracle):m.sha(oracle)}
                engines[name] = {'argv':[exe, str(emitter), str(oracle)], 'cwd':d,
                                 'consumer':str(emitter), 'artifacts':artifacts}
            manifest = {'schema':'n06-installed-commands/1', 'engines':engines}
            command_file = root / 'commands.json'
            def run(label):
                command_file.write_text(json.dumps(manifest))
                return subprocess.run([sys.executable, str(Path(m.__file__)), '--commands', str(command_file),
                    '--commands-sha256', m.sha(command_file), '--output', str(root / label)], capture_output=True)
            self.assertEqual(run('ok').returncode, 0)
            self.assertEqual(json.loads((root/'ok/result.json').read_text())['status'], 'passed_49_case_comparison')
            swift = manifest['engines'].pop('swift')
            self.assertEqual(run('missing').returncode, 1)
            self.assertEqual(json.loads((root/'missing/result.json').read_text())['status'], 'failed')
            manifest['engines']['swift'] = swift
            altered = root / 'wrong.jsonl'
            altered.write_bytes(oracle.read_bytes().replace(b'16294208416658607535', b'0', 1))
            entry = manifest['engines']['go']
            wrong_entry = dict(entry, argv=[exe,entry['consumer'],str(altered)],
                               artifacts={**entry['artifacts'], str(altered):m.sha(altered)})
            manifest['engines']['go'] = wrong_entry
            self.assertEqual(run('wrong').returncode, 1)
            self.assertIn('rows differ', json.loads((root/'wrong/result.json').read_text())['error'])

    def test_wrong_and_missing_rows_differ_from_oracle(self):
        rows = m.load_oracle()
        expected = [m.canonical(r) for r in rows]
        self.assertNotEqual(expected, [m.canonical(r) for r in rows[:-1]])
        wrong = [dict(r) for r in rows]
        wrong[0]['seed'] = '0'
        self.assertNotEqual(expected, [m.canonical(r) for r in wrong])


if __name__ == '__main__':
    unittest.main()
