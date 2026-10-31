"""Mutation evidence is preserved and replayed in a separate Python process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import importlib.util
import shutil
from dataclasses import replace

from statelessness import ModelError
from statelessness.reference_suite import (_exclusive, _publish_summary, verify_mutation_suite,
    verify_witness, write_mutation_suite, write_mutation_suite_with_pins)
from statelessness.trace_binary import read_audit, write_audit

class MutationSuiteTests(unittest.TestCase):
    def test_suite_evidence_budget_never_publishes_partial_pairs_or_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); suite=root/'suite'; pins=root/'pins.json'
            results=write_mutation_suite(suite,max_evidence_bytes=0)
            self.assertTrue(all(result.status=='not_evaluated' for result in results))
            self.assertEqual({p.name for p in suite.iterdir()},{'mutations.json'})
            self.assertGreater((suite/'mutations.json').stat().st_size,0)
            with self.assertRaisesRegex(ModelError,'suite incomplete'):
                write_mutation_suite_with_pins(root/'other',pins,max_evidence_bytes=0)
            self.assertFalse(pins.exists())
            with self.assertRaises(ValueError): write_mutation_suite(root/'invalid',max_evidence_bytes=-1)
            self.assertFalse((root/'invalid').exists())

    def test_fresh_process_suite_writer_and_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); suite=root/'suite'; pins=root/'pins.json'
            process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--write-suite',str(suite),
                                    '--write-pins',str(pins)],capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertIn('written=9 controls',process.stdout)
            process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(suite),
                                    '--pins',str(pins)],capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertIn('verified=18 build_matches=true',process.stdout)
            process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--write-suite',str(root/'other')],
                                    capture_output=True,text=True,timeout=30)
            self.assertNotEqual(process.returncode,0)
            self.assertFalse((root/'other').exists())
            limited=root/'limited'; absent=root/'absent.json'
            process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--write-suite',str(limited),
                                    '--write-pins',str(absent),'--max-evidence-bytes','0'],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(process.returncode,0)
            self.assertFalse(absent.exists())
            self.assertEqual({path.name for path in limited.iterdir()},{'mutations.json'})
            process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(suite),
                                    '--max-evidence-bytes','0'],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(process.returncode,0)
            self.assertIn('--max-evidence-bytes requires --write-suite',process.stderr)

    def test_suite_producer_publishes_separate_nonoverwriting_build_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); suite=root/'suite'; pins=root/'pins.json'
            with self.assertRaises(ModelError): write_mutation_suite_with_pins(suite,suite/'pins.json')
            self.assertFalse(suite.exists())
            pins.write_bytes(b'existing')
            with self.assertRaises(FileExistsError): write_mutation_suite_with_pins(suite,pins)
            self.assertEqual(pins.read_bytes(),b'existing'); self.assertFalse(suite.exists())
            pins.unlink()
            results=write_mutation_suite_with_pins(suite,pins)
            self.assertTrue(all(result.status=='killed' for result in results))
            values=json.loads(pins.read_text())
            report=verify_mutation_suite(suite,expected_build=values['report_build'],
                                         expected_mutation_builds=values['mutation_builds'],return_report=True)
            self.assertEqual((report.verified_witnesses,report.build_matches),(18,True))
            self.assertFalse((root/'.pins.json.pending').exists())
            from types import SimpleNamespace
            incomplete=root/'incomplete'; absent=root/'absent-pins.json'
            with patch('statelessness.reference_suite.write_mutation_suite',return_value=[SimpleNamespace(status='not_evaluated')]):
                with self.assertRaisesRegex(ModelError,'suite incomplete'):
                    write_mutation_suite_with_pins(incomplete,absent)
            self.assertFalse(absent.exists())
            from statelessness.reference_controller import MUTATION_TARGETS
            controls=[SimpleNamespace(mutation=name,status='killed',reason=None,search='failure_found',
                                      shrink_complete=True,original=object(),minimized=object())
                      for name in MUTATION_TARGETS]
            for mode in ('incomplete-shrink','duplicate-mutation'):
                altered=[SimpleNamespace(**vars(item)) for item in controls]
                if mode=='incomplete-shrink': altered[0].shrink_complete=False
                else: altered[0].mutation=altered[1].mutation
                with self.subTest(mode=mode), patch('statelessness.reference_suite.write_mutation_suite',return_value=altered):
                    with self.assertRaisesRegex(ModelError,'suite incomplete'):
                        write_mutation_suite_with_pins(incomplete,absent)
                self.assertFalse(absent.exists())

    def test_verify_witness_closes_descriptor_when_fdopen_fails(self):
        actual_open=os.open
        opened=[]
        def capture(*args,**kwargs):
            fd=actual_open(*args,**kwargs); opened.append(fd); return fd
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'witness'
            path.write_bytes(b'unchanged')
            with patch('statelessness.reference_suite.os.open',side_effect=capture), \
                 patch('statelessness.reference_suite.os.fdopen',side_effect=OSError('fdopen failure')):
                with self.assertRaisesRegex(OSError,'fdopen failure'):
                    verify_witness(path,'obsolete-publication')
            with self.assertRaises(OSError): os.fstat(opened[0])
            self.assertEqual(path.read_bytes(),b'unchanged')

    def test_suite_rejects_nonempty_directory_without_touching_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            existing=root/'unrelated'
            existing.write_bytes(b'preserve')
            with self.assertRaisesRegex(ModelError,'must be empty'):
                write_mutation_suite(root)
            self.assertEqual(existing.read_bytes(),b'preserve')
            self.assertEqual({p.name for p in root.iterdir()},{'unrelated'})

    def test_suite_aborts_on_witness_collision_without_publishing_summary(self):
        def collide(path,writer):
            if str(path).endswith('obsolete-publication-minimized.sttrace'):
                path.write_bytes(b'preexisting')
            return _exclusive(path,writer)
        with tempfile.TemporaryDirectory() as directory:
            with patch('statelessness.reference_suite._exclusive',side_effect=collide):
                with self.assertRaises(FileExistsError): write_mutation_suite(directory)
            root=Path(directory)
            self.assertFalse((root/'mutations.json').exists())
            self.assertFalse((root/'obsolete-publication-original.sttrace').exists())
            self.assertEqual((root/'obsolete-publication-minimized.sttrace').read_bytes(),b'preexisting')

    def test_suite_rechecks_for_unexpected_artifacts_before_publication(self):
        from statelessness.reference_suite import evaluate_mutation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def inject(mutation,config):
                (root/'unexpected').write_bytes(b'preserve')
                return evaluate_mutation(mutation,config)
            with patch('statelessness.reference_suite.evaluate_mutation',side_effect=inject):
                with self.assertRaisesRegex(ModelError,'unexpected mutation suite artifact'):
                    write_mutation_suite(root)
            self.assertFalse((root/'mutations.json').exists())
            self.assertEqual((root/'unexpected').read_bytes(),b'preserve')

    def test_failed_exclusive_write_removes_only_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'evidence'
            def broken(stream):
                stream.write(b'partial')
                raise OSError('write failed')
            with self.assertRaisesRegex(OSError,'write failed'): _exclusive(path,broken)
            self.assertFalse(path.exists())
            path.write_bytes(b'preexisting')
            with self.assertRaises(FileExistsError): _exclusive(path,broken)
            self.assertEqual(path.read_bytes(),b'preexisting')

    @unittest.skipIf(os.name == 'nt', 'Windows cannot unlink a file that is still open for writing')
    def test_failed_writer_does_not_unlink_replacement_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'evidence'
            def replace_file(stream):
                path.unlink()
                path.write_bytes(b'replacement')
                raise OSError('failed after replacement')
            with self.assertRaisesRegex(OSError,'failed after replacement'):
                _exclusive(path,replace_file)
            self.assertEqual(path.read_bytes(),b'replacement')

    def test_exclusive_closes_descriptor_on_fstat_and_fdopen_failure(self):
        actual_open=os.open
        for stage in ('fstat','fdopen'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'evidence'
                opened=[]
                def capture(*args,**kwargs):
                    fd=actual_open(*args,**kwargs); opened.append(fd); return fd
                with patch('statelessness.reference_suite.os.open',side_effect=capture), \
                     patch('statelessness.reference_suite.os.'+stage,side_effect=OSError(stage+' failure')):
                    with self.assertRaisesRegex(OSError,stage+' failure'):
                        _exclusive(path,lambda stream: stream.write(b'data'))
                with self.assertRaises(OSError): os.fstat(opened[0])
                self.assertEqual(path.exists(),stage=='fstat')

    def test_failed_second_witness_removes_first_owned_witness(self):
        def fail_second(path,writer):
            if str(path).endswith('obsolete-publication-minimized.sttrace'):
                raise OSError('injected second witness failure')
            return _exclusive(path,writer)
        with tempfile.TemporaryDirectory() as directory:
            with patch('statelessness.reference_suite._exclusive',side_effect=fail_second):
                results=write_mutation_suite(directory)
            root=Path(directory)
            self.assertEqual(results[0].status,'not_evaluated')
            self.assertFalse((root/'obsolete-publication-original.sttrace').exists())
            self.assertFalse((root/'obsolete-publication-minimized.sttrace').exists())
            summary=json.loads((root/'mutations.json').read_text())
            self.assertIsNone(summary['results'][0]['original'])
            self.assertIsNone(summary['results'][0]['minimized'])

    def test_summary_publication_never_exposes_partial_or_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            if os.name == 'posix':
                with patch('statelessness.reference_suite.os.fsync',side_effect=[None,OSError('sync failure')]):
                    with self.assertRaisesRegex(OSError,'sync failure'):
                        _publish_summary(root,b'{"complete":true}')
                self.assertFalse((root/'mutations.json').exists())
                self.assertFalse((root/'.mutations.json.pending').exists())
            with patch('statelessness.reference_suite.os.link', side_effect=OSError('link failure')):
                with self.assertRaisesRegex(OSError,'link failure'):
                    _publish_summary(root,b'{"complete":true}')
            self.assertFalse((root/'mutations.json').exists())
            self.assertFalse((root/'.mutations.json.pending').exists())
            _publish_summary(root,b'{"complete":true}')
            with self.assertRaises(FileExistsError): _publish_summary(root,b'{"changed":true}')
            self.assertEqual((root/'mutations.json').read_bytes(),b'{"complete":true}')
            self.assertFalse((root/'.mutations.json.pending').exists())

    def test_go_suite_runner_requires_independent_complete_pins(self):
        from statelessness.reference_controller import MUTATION_TARGETS
        examples = Path(__file__).resolve().parents[1] / 'examples'
        sys.path.insert(0, str(examples))
        try:
            import replay_go_mutation_suite as runner
        finally:
            sys.path.pop(0)
        builds = {name: 'go-build-' + name for name in MUTATION_TARGETS}
        pins = {'report_build': 'go-base', 'mutation_builds': builds}
        rows = [{'mutation': name, 'target': target, 'status': 'killed', 'reason': None,
                 'search': 'Some(FailureFound)', 'shrink_complete': True,
                 'original': name + '-original.sttrace', 'minimized': name + '-minimized.sttrace'}
                for name, target in MUTATION_TARGETS.items()]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = {'schema_version': 1, 'model': 'reference-controller', 'build': 'go-base',
                       'review_status': 'awaiting_independent_review', 'results': rows}
            (root / 'mutations.json').write_text(json.dumps(summary))
            pins_path = root / 'pins.json'
            pins_path.write_text(json.dumps(pins))
            with patch.object(runner, 'verify_witness') as verify:
                self.assertEqual(runner.replay_suite(root, pins_path), 18)
                self.assertEqual(verify.call_args_list[0].kwargs['expected_build'], builds[rows[0]['mutation']])
                del builds[rows[0]['mutation']]
                pins_path.write_text(json.dumps(pins))
                with self.assertRaisesRegex(ModelError, 'pins'): runner.replay_suite(root, pins_path)

    def test_swift_suite_runner_checks_build_and_witness_kind(self):
        from statelessness.reference_controller import MUTATION_TARGETS
        examples = Path(__file__).resolve().parents[1] / 'examples'
        sys.path.insert(0, str(examples))
        try:
            import replay_swift_mutation_suite as runner
        finally:
            sys.path.pop(0)
        build = 'swift-reference-source;fault=none'
        rows = [{'mutation': name, 'target': target, 'status': 'killed', 'reason': None,
                 'search': 'Some(FailureFound)', 'shrink_complete': True,
                 'original': name + '-original.sttrace', 'minimized': name + '-minimized.sttrace'}
                for name, target in MUTATION_TARGETS.items()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mutations.json'
            summary = {'schema_version': 1, 'model': 'reference-controller', 'build': build,
                       'review_status': 'awaiting_independent_review', 'results': rows}
            path.write_text(json.dumps(summary))
            with patch.object(runner, 'verify_witness') as verify:
                self.assertEqual(runner.replay_suite(directory, build), 18)
                self.assertEqual(verify.call_args_list[0].kwargs, {'expected_build': 'swift-reference-source;fault=obsolete-publication', 'expected_witness': 'original'})
                with self.assertRaises(ModelError): runner.replay_suite(directory, 'swift-reference-other;fault=none')
                rows[0]['shrink_complete'] = False
                path.write_text(json.dumps(summary))
                with self.assertRaises(ModelError): runner.replay_suite(directory, build)
    def test_rust_suite_runner_pins_producer_and_each_witness(self):
        from statelessness.reference_controller import MUTATION_TARGETS
        example = Path(__file__).resolve().parents[1] / 'examples' / 'replay_rust_mutation_suite.py'
        spec = importlib.util.spec_from_file_location('rust_suite_runner', example)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        build = 'reference-source-Bounds-None'
        rows = [{'mutation': name, 'target': target, 'status': 'killed', 'reason': None,
                 'search': 'Some(FailureFound)', 'shrink_complete': True,
                 'original': name + '-original.sttrace', 'minimized': name + '-minimized.sttrace'}
                for name, target in MUTATION_TARGETS.items()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mutations.json'
            summary = {'schema_version': 1, 'model': 'reference-controller', 'build': build,
                       'review_status': 'awaiting_independent_review', 'results': rows}
            path.write_text(json.dumps(summary))
            with patch.object(runner, 'verify_witness') as verify:
                self.assertEqual(runner.replay_suite(directory, build), 18)
                self.assertEqual(verify.call_count, 18)
                self.assertEqual(verify.call_args_list[0].kwargs['expected_build'],
                                 'reference-source-Bounds-Some(ObsoletePublication)')
                with self.assertRaises(ModelError): runner.replay_suite(directory, 'reference-wrong-None')
                rows[0]['original'] = '../other.sttrace'
                path.write_text(json.dumps(summary))
                with self.assertRaises(ModelError): runner.replay_suite(directory, build)
                rows[0]['original'] = rows[0]['mutation'] + '-original.sttrace'
                rows[0]['shrink_complete'] = False
                path.write_text(json.dumps(summary))
                with self.assertRaises(ModelError): runner.replay_suite(directory, build)
                path.write_text('{"schema_version":1,"schema_version":1}')
                with self.assertRaisesRegex(ModelError, 'duplicate'): runner.replay_suite(directory, build)
                path.write_bytes(b'x' * (1024 * 1024 + 1))
                with self.assertRaisesRegex(ModelError, 'oversized'): runner.replay_suite(directory, build)
                path.unlink()
                if hasattr(os, 'symlink') and hasattr(os, 'O_NOFOLLOW'):
                    os.symlink('missing', path)
                    with self.assertRaises(OSError): runner.replay_suite(directory, build)
                    path.unlink()
                if hasattr(os, 'mkfifo') and hasattr(os, 'O_NONBLOCK'):
                    os.mkfifo(path)
                    result = subprocess.run([sys.executable, str(example), directory, '--producer-build', build],
                                            capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('invalid or oversized mutation summary', result.stderr)

    def test_persist_and_fresh_process_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            results = write_mutation_suite(directory)
            self.assertEqual([r.status for r in results], ['killed'] * 9)
            root = Path(directory)
            summary = json.loads((root / 'mutations.json').read_text())
            self.assertEqual(len(summary['results']), 9)
            # Rust src/reference/evidence.rs suite_json: {:?} search and line layout.
            self.assertTrue(all(row['search'] == 'Some(FailureFound)' for row in summary['results']))
            raw_summary = (root / 'mutations.json').read_text()
            self.assertEqual(raw_summary.count('},\n{'), 8)
            self.assertTrue(raw_summary.endswith(']}\n'))
            with (root / summary['results'][0]['original']).open('rb') as stream:
                first_trace = read_audit(stream)
            self.assertEqual([key for key, _ in first_trace.config.parameters],
                             ['mutation', 'target', 'max_states', 'max_transitions', 'max_depth',
                              'stateless.engine.version', 'stateless.engine.build'])
            self.assertEqual(verify_mutation_suite(root),18)
            suite_process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(root)],
                                         capture_output=True,text=True,timeout=20)
            self.assertEqual(suite_process.returncode,0,suite_process.stderr)
            self.assertIn('verified=18 build_matches=true',suite_process.stdout)
            limited_process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(root),
                                            '--max-total-bytes','0'],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(limited_process.returncode,0)
            total=sum(path.stat().st_size for path in root.iterdir())
            self.assertEqual(verify_mutation_suite(root,max_total_bytes=total),18)
            assessment=verify_mutation_suite(root,max_total_bytes=total,return_report=True)
            self.assertEqual((assessment.verified_witnesses,assessment.build_matches,assessment.bytes_read,assessment.max_total_bytes),(18,True,total,total))
            with self.assertRaises(ModelError): verify_mutation_suite(root,max_total_bytes=total-1)
            with self.assertRaises(ModelError): verify_mutation_suite(root,max_total_bytes=0)
            with self.assertRaises(ModelError): verify_mutation_suite(root,expected_build='foreign')
            foreign=root.parent/(root.name+'-foreign')
            shutil.copytree(root,foreign)
            try:
                pins={entry['mutation']:'foreign-'+entry['mutation'] for entry in summary['results']}
                for entry in summary['results']:
                    for label in ('original','minimized'):
                        witness=foreign/entry[label]
                        with witness.open('rb') as stream: trace=read_audit(stream)
                        with witness.open('wb') as stream:
                            write_audit(replace(trace,metadata=replace(trace.metadata,build=pins[entry['mutation']])),stream)
                foreign_summary=dict(summary); foreign_summary['build']='foreign-summary'
                (foreign/'mutations.json').write_text(json.dumps(foreign_summary))
                with self.assertRaises(ModelError): verify_mutation_suite(foreign)
                self.assertEqual(verify_mutation_suite(foreign,expected_build='foreign-summary',expected_mutation_builds=pins),18)
                pins_path=root.parent/(root.name+'-pins.json')
                pins_path.write_text(json.dumps({'report_build':'foreign-summary','mutation_builds':pins}))
                try:
                    foreign_process=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(foreign),
                                                    '--pins',str(pins_path)],capture_output=True,text=True,timeout=20)
                    self.assertEqual(foreign_process.returncode,0,foreign_process.stderr)
                    self.assertIn('verified=18 build_matches=false',foreign_process.stdout)
                    pins_path.write_text('{"report_build":null,"mutation_builds":null}')
                    rejected=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(root),
                                              '--pins',str(pins_path)],capture_output=True,text=True,timeout=20)
                    self.assertNotEqual(rejected.returncode,0)
                    self.assertIn('invalid mutation suite build pins',rejected.stderr)
                    pins_path.write_text('{"report_build":"a","report_build":"b"}')
                    rejected=subprocess.run([sys.executable,'-m','statelessness.reference_suite','--suite',str(foreign),
                                              '--pins',str(pins_path)],capture_output=True,text=True,timeout=20)
                    self.assertNotEqual(rejected.returncode,0)
                finally:
                    pins_path.unlink()
                assessment=verify_mutation_suite(foreign,expected_build='foreign-summary',expected_mutation_builds=pins,return_report=True)
                self.assertEqual((assessment.verified_witnesses,assessment.build_matches),(18,False))
                badpins=dict(pins); badpins[summary['results'][0]['mutation']]='wrong'
                with self.assertRaises(ModelError):
                    verify_mutation_suite(foreign,expected_build='foreign-summary',expected_mutation_builds=badpins)
                with self.assertRaises(ModelError):
                    verify_mutation_suite(foreign,expected_build='wrong',expected_mutation_builds=pins)
            finally:
                shutil.rmtree(foreign)
            class UnexpectedEntries:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def __iter__(self):
                    from types import SimpleNamespace
                    yield SimpleNamespace(name='unexpected')
                    raise AssertionError('must reject before reading more entries')
            with patch('statelessness.reference_suite.os.scandir',return_value=UnexpectedEntries()):
                with self.assertRaisesRegex(ModelError,'unexpected'): verify_mutation_suite(root)
            unexpected=root/'unexpected'
            unexpected.write_bytes(b'extra')
            with self.assertRaisesRegex(ModelError,'unexpected'): verify_mutation_suite(root)
            unexpected.unlink()
            summary_path=root/'mutations.json'
            original_summary=summary_path.read_bytes()
            bad=dict(summary); bad['results']=[dict(row) for row in summary['results']]
            bad['results'][0]['shrink_complete']=False
            summary_path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ModelError,'not established'): verify_mutation_suite(root)
            summary_path.write_bytes(original_summary.replace(b'"schema_version":1',b'"schema_version":1,"schema_version":1',1))
            with self.assertRaisesRegex(ModelError,'duplicate'): verify_mutation_suite(root)
            summary_path.write_bytes(original_summary)
            for entry in summary['results']:
                path = root / entry['minimized']
                self.assertTrue(verify_witness(path, entry['mutation'],expected_witness='minimized'))
                # Rust-format witnesses carry no witness-kind parameter, so the
                # kind is not required (a recorded kind must still match).
                self.assertTrue(verify_witness(path,entry['mutation'],expected_witness='original'))
                self.assertTrue(verify_witness(root / entry['original'],entry['mutation'],expected_witness='original'))
                result = subprocess.run([sys.executable, '-m', 'statelessness.reference_suite', entry['mutation'], str(path)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            first = root / summary['results'][0]['original']
            rejected=subprocess.run([sys.executable,'-m','statelessness.reference_suite',summary['results'][0]['mutation'],str(first),
                                     '--max-total-bytes','0'],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn('--max-total-bytes requires --suite',rejected.stderr)
            before = first.read_bytes()
            with self.assertRaises(FileExistsError): _exclusive(first, lambda stream: stream.write(b'changed'))
            self.assertEqual(first.read_bytes(), before)
            with self.assertRaises(ModelError): verify_witness(first, summary['results'][1]['mutation'])
            mutation = summary['results'][0]['mutation']
            with first.open('rb') as stream: trace = read_audit(stream)
            labeled = root / 'labeled.sttrace'
            with labeled.open('wb') as stream:
                params=tuple((key,value) for key,value in trace.config.parameters if key!='witness')+(('witness','original'),)
                write_audit(replace(trace, config=replace(trace.config, parameters=params)), stream)
            self.assertTrue(verify_witness(labeled, mutation, expected_witness='original'))
            with self.assertRaises(ModelError): verify_witness(labeled, mutation, expected_witness='minimized')
            foreign = root / 'foreign.sttrace'
            with foreign.open('wb') as stream:
                write_audit(replace(trace, metadata=replace(trace.metadata, build='foreign-build')), stream)
            with self.assertRaises(ModelError): verify_witness(foreign, mutation)
            with self.assertRaises(ModelError): verify_witness(foreign, mutation, expected_build='other-build')
            self.assertTrue(verify_witness(foreign, mutation, expected_build='foreign-build'))
            result = subprocess.run([sys.executable, '-m', 'statelessness.reference_suite', mutation, str(foreign),
                                     '--expected-build', 'foreign-build'], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(ModelError): verify_witness(foreign, mutation, expected_build='')
            with foreign.open('wb') as stream:
                write_audit(replace(trace, metadata=replace(trace.metadata, build='foreign-build', codec_version=2)), stream)
            with self.assertRaises(ModelError): verify_witness(foreign, mutation, expected_build='foreign-build')
            for config in (replace(trace.config, strategy='manual'),
                           replace(trace.config, parameters=trace.config.parameters + (('mutation', mutation),)),
                           replace(trace.config, parameters=trace.config.parameters + (('target', summary['results'][0]['target']),))):
                altered = root / 'altered.sttrace'
                with altered.open('wb') as stream: write_audit(replace(trace, config=config), stream)
                with self.assertRaises(ModelError): verify_witness(altered, mutation)
            if hasattr(os, 'mkfifo'):
                fifo = root / 'witness.fifo'
                os.mkfifo(fifo)
                result = subprocess.run([sys.executable, '-m', 'statelessness.reference_suite', mutation, str(fifo)],
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('witness must be a regular file', result.stderr)
            if os.name == 'posix': self.assertEqual(first.stat().st_mode & 0o777, 0o600)

if __name__ == '__main__': unittest.main()
