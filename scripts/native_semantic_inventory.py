#!/usr/bin/env python3
"""Resolve the native/default Rust API from pinned schema-57 rustdoc JSON.

This program only reads compiler output and records metadata. It never builds,
executes model code, or promotes a native implementation/qualification state.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/native-parity'
RAW = ROOT / 'target/native-parity-rustdoc/doc'
BASELINE = '0876717bee85e335449c2c99ada41f6c6e6112d1'
KYOTO = '/Volumes/seed1/Projects/kyoto/kyoto.py'
CRATES = {'statelessness': 'stateless', 'statelessness-macros': 'statelessness_macros',
          'statelessness-debug': 'statelessness_debug'}
JOBS = {'statelessness': '363583b2ad8a475a995631ff6f3b2d52',
        'statelessness-macros': '7b8499d2e2304b55900ab57a96937bea',
        'statelessness-debug': 'b2f7ff0323274f5d91dc9fb4a92046de'}
MODULE_GAPS = {'assurance': 'N10', 'automatic': 'N03', 'campaign': 'N06',
               'composition': 'N08', 'conformance': 'N02', 'demo': 'N14',
               'evidence': 'N11', 'execution': 'N01', 'explore': 'N01',
               'ffi': 'N14', 'ffi_v2': 'N14', 'guided': 'N05', 'lifecycle': 'N09', 'model': 'N01',
               'modeling': 'N13', 'monitor': 'N07', 'network': 'N08',
               'observation': 'N07', 'oracle': 'N02', 'reference': 'N10',
               'research': 'N14', 'trace': 'N11', 'value_codec': 'N11',
               'lib': 'N14'}
PUBLIC_KINDS = {'module', 'use', 'struct', 'enum', 'union', 'trait', 'trait_alias',
                'function', 'constant', 'static', 'type_alias', 'macro',
                'proc_macro', 'extern_crate', 'extern_type'}
ASSOCIATED = {'function', 'assoc_type', 'assoc_const', 'type_alias', 'constant'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def command(*args):
    return subprocess.check_output(args, cwd=ROOT).decode().strip()


def kind(item):
    if len(item['inner']) != 1:
        raise ValueError('Expected exactly one rustdoc item kind')
    return next(iter(item['inner']))


def local_source(item):
    return (item.get('span') or {}).get('filename', '').startswith(('src/', 'crates/'))


def referenced_ids(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'id' and isinstance(child, int):
                yield str(child)
            else:
                yield from referenced_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from referenced_ids(child)


def gap_mapping(crate, item, paths, owner=None):
    filename = (item.get('span') or {}).get('filename', '')
    names = ' '.join(paths) + ' ' + (owner or '') + ' ' + (item.get('name') or '')
    if crate == 'statelessness-debug':
        return ['N12']
    if crate == 'statelessness-macros':
        return ['N12', 'N13'] if item.get('name') == 'Inspect' else ['N13']
    if filename.startswith('src/'):
        module = filename.split('/')[1].split('.')[0]
    else:
        # A crate root/reexport may have no source span. Resolve through its
        # actual exported namespace, failing if a new module lacks a policy.
        pieces = next(iter(paths), 'stateless').split('::')
        module = pieces[1] if len(pieces) > 1 else 'lib'
    if module == 'model':
        if any(name in names for name in ('ModelCodec', 'EncodeBuffer')):
            return ['N11']
        if any(name in names for name in ('Generate', 'Rng')):
            return ['N03', 'N04'] if 'simpler_inputs' in names else ['N03']
    if module == 'explore':
        if 'Shrink' in names or 'shrink' in names:
            return ['N04']
        if 'Fuzz' in names or 'fuzz' in names:
            return ['N03']
        if 'SearchObserver' in names:
            return ['N01', 'N03']
        if any(name in names for name in ('CheckPhase', 'PropertyFailure', '::Failure')):
            return ['N01', 'N03', 'N04']
    if module == 'oracle':
        if 'simpler_inputs' in names:
            return ['N02', 'N04']
        if 'Generate' in names or 'generate' in names or 'is_enabled' in names:
            return ['N02', 'N03']
        if 'estimated_state_bytes' in names:
            return ['N01', 'N02']
    if module not in MODULE_GAPS:
        raise ValueError(f'Unmapped public module {module}: {filename} {names}')
    return [MODULE_GAPS[module]]


class Resolver:
    def __init__(self, document, crate):
        if document.get('format_version') != 57:
            raise ValueError('Unsupported rustdoc JSON schema; review parser before adopting a new compiler')
        if document.get('includes_private') is not True:
            raise ValueError('Private items are required for auditable exclusion accounting')
        self.document, self.crate = document, crate
        self.index = {str(key): value for key, value in document['index'].items()}
        self.root = str(document['root'])
        self.local_crate_id = self.index[self.root]['crate_id']
        self.paths = defaultdict(set)
        self.roles = {}
        self.owners = {}
        self.unresolved = []
        self.edges = []
        self.impl_metadata = {}
        self.expanded_members = set()

    def item(self, identity):
        identity = str(identity)
        if identity not in self.index:
            self.unresolved.append({'reason': 'missing_index_target', 'rustdoc_id': identity})
            return None
        return self.index[identity]

    def add(self, identity, path, role, owner=None):
        identity = str(identity)
        item = self.item(identity)
        if item is None:
            return
        if item['crate_id'] != self.local_crate_id:
            self.unresolved.append({'reason': 'external_reexport_needs_dependency_resolution',
                                    'rustdoc_id': identity, 'export_path': path})
            return
        self.paths[identity].add(path)
        self.roles.setdefault(identity, role)
        if owner:
            self.owners[identity] = str(owner)

    def module(self, identity, path, active=()):
        identity = str(identity)
        if identity in active:
            # The finite declaration/reexport graph is fully recorded; arbitrary
            # repeated alias spellings from a cycle are not separate symbols.
            self.edges.append({'kind': 'cyclic_module_alias', 'target': identity, 'path': path})
            return
        module = self.item(identity)
        if module is None or kind(module) != 'module':
            self.unresolved.append({'reason': 'glob_target_is_not_module', 'rustdoc_id': identity})
            return
        for child_id in module['inner']['module']['items']:
            child_id = str(child_id)
            child = self.item(child_id)
            if child is None or child['visibility'] != 'public':
                continue
            child_kind = kind(child)
            if child_kind not in PUBLIC_KINDS:
                raise ValueError(f'Unrecognized public module child kind: {child_kind}')
            if child_kind == 'use':
                use = child['inner']['use']
                target = use.get('id')
                alias = path + '::' + use['name']
                self.add(child_id, alias + ('::*' if use['is_glob'] else ''), 'public_reexport', identity)
                self.edges.append({'kind': 'glob' if use['is_glob'] else 'alias',
                                   'use_id': child_id, 'source': use['source'],
                                   'target': str(target), 'export_path': alias})
                if target is None:
                    self.unresolved.append({'reason': 'unresolved_use_target', 'use_id': child_id})
                elif use['is_glob']:
                    self.module(target, path, (*active, identity))
                else:
                    self.add(target, alias, 'named_export', identity)
                    target_item = self.item(target)
                    if target_item and kind(target_item) == 'module':
                        self.module(target, alias, (*active, identity))
                continue
            export = path + '::' + child['name']
            self.add(child_id, export, 'named_export', identity)
            if child_kind == 'module':
                self.module(child_id, export, (*active, identity))

    def members(self, identity):
        if identity in self.expanded_members:
            return
        self.expanded_members.add(identity)
        item = self.index[identity]
        item_kind = kind(item)
        body = item['inner'][item_kind]
        paths = sorted(self.paths[identity])
        children, inherited = [], False
        if item_kind == 'trait':
            children, inherited = body['items'], True
        elif item_kind == 'enum':
            children, inherited = body['variants'], True
            if body.get('has_stripped_variants'):
                self.unresolved.append({'reason': 'stripped_public_enum_variants', 'rustdoc_id': identity})
        elif item_kind in ('struct', 'variant'):
            shape = body['kind']
            if isinstance(shape, dict):
                if 'tuple' in shape:
                    children = [x for x in shape['tuple'] if x is not None]
                else:
                    details = shape.get('plain', shape.get('struct'))
                    if details is None:
                        raise ValueError(f'Unknown fields shape: {shape}')
                    children = details['fields']
                    if details.get('has_stripped_fields'):
                        self.unresolved.append({'reason': 'stripped_fields_need_review', 'rustdoc_id': identity})
            inherited = item_kind == 'variant'
        elif item_kind == 'union':
            children = body['fields']
        for position, child_id in enumerate(children):
            child = self.item(child_id)
            if child is None or not (inherited or child['visibility'] == 'public'):
                continue
            child_kind = kind(child)
            if item_kind == 'trait' and child_kind not in ASSOCIATED:
                raise ValueError(f'Unknown trait member: {child_kind}')
            for path in paths:
                self.add(child_id, path + '::' + (child['name'] or str(position)),
                         'trait_member' if item_kind == 'trait' else 'public_member', identity)
            self.members(str(child_id))

    def receiver_accessible(self, receiver):
        if not isinstance(receiver, dict) or len(receiver) != 1:
            self.unresolved.append({'reason': 'unsupported_impl_receiver_shape', 'receiver': receiver})
            return False
        if next(iter(receiver)) not in {'resolved_path', 'primitive', 'tuple', 'borrowed_ref',
                                        'array', 'slice', 'generic', 'raw_pointer',
                                        'function_pointer', 'dyn_trait', 'qualified_path'}:
            self.unresolved.append({'reason': 'unsupported_impl_receiver_type', 'receiver': receiver})
            return False
        for identity in referenced_ids(receiver):
            item = self.index.get(identity)
            if item and item['crate_id'] == self.local_crate_id and identity not in self.paths:
                return False
        return True

    def implementations(self):
        for identity, item in self.index.items():
            if kind(item) != 'impl' or not local_source(item):
                continue
            impl = item['inner']['impl']
            trait = impl.get('trait')
            if impl['is_synthetic'] or impl.get('blanket_impl') is not None:
                raise ValueError('Unexpected source-backed synthetic/blanket impl: review schema')
            if not self.receiver_accessible(impl['for']):
                continue
            if trait:
                trait_id = str(trait['id'])
                local_trait = self.index.get(trait_id)
                if local_trait and local_trait['crate_id'] == self.local_crate_id and trait_id not in self.paths:
                    continue
            elif not any(ref in self.paths for ref in referenced_ids(impl['for'])):
                # Rust forbids inherent impls for foreign types; do not infer
                # accessibility for a malformed/unsupported compiler record.
                self.unresolved.append({'reason': 'inherent_impl_without_reachable_receiver', 'rustdoc_id': identity})
                continue
            receiver = impl['for']
            direct = receiver.get('resolved_path', {}).get('id')
            receiver_paths = sorted(self.paths.get(str(direct), []))
            if not receiver_paths:
                receiver_paths = [canonical(receiver)]
            trait_name = trait['path'] if trait else None
            trait_crate = None
            if trait:
                trait_info = self.document['paths'].get(str(trait['id']))
                if trait_info:
                    trait_crate = self.document['external_crates'].get(str(trait_info['crate_id']), {}).get('name')
            role = ('public_local_trait_implementation' if trait and str(trait['id']) in self.paths
                    else 'public_std_trait_implementation' if trait_crate in {'std', 'core', 'alloc'}
                    else 'public_external_trait_implementation' if trait else 'public_inherent_implementation')
            header = [f'<{path} as {trait_name}>' if trait else path for path in receiver_paths]
            for path in header:
                self.add(identity, path + '::{impl}', role)
            self.impl_metadata[identity] = {key: impl[key] for key in ('for', 'trait', 'generics', 'provided_trait_methods', 'is_negative')}
            for member_id in impl['items']:
                member = self.item(member_id)
                if member is None:
                    continue
                if kind(member) not in ASSOCIATED:
                    raise ValueError(f'Unknown impl member kind: {kind(member)}')
                if not trait and member['visibility'] != 'public':
                    continue
                for path in header:
                    self.add(member_id, path + '::' + member['name'], 'implementation_member', identity)

    def resolve(self):
        root_name = self.index[self.root]['name']
        self.add(self.root, root_name, 'crate_root')
        self.module(self.root, root_name)
        for identity in list(self.paths):
            self.members(identity)
        self.implementations()
        public = []
        for identity in sorted(self.paths, key=lambda value: (sorted(self.paths[value])[0], int(value))):
            item = self.index[identity]
            item_kind = kind(item)
            paths = sorted(self.paths[identity])
            owner_id = self.owners.get(identity)
            owner_names = ' '.join(sorted(self.paths.get(owner_id, [])))
            row = {'id': f'{self.crate}:{identity}', 'rustdoc_id': identity,
                   'kind': item_kind, 'name': item['name'], 'exports': paths,
                   'semantic_role': self.roles[identity], 'visibility': item['visibility'],
                   'owner_rustdoc_id': owner_id, 'source': item.get('span'),
                   'gap_ids': gap_mapping(self.crate, item, paths, owner_names),
                   'definition_sha256': digest(canonical(item['inner']).encode()),
                   'native_mapping': 'pending_individual_api_review',
                   'parity_role': ('retained_foreign_transport_alternative'
                                   if (item.get('span') or {}).get('filename', '').startswith(('src/ffi.', 'src/ffi_v2/'))
                                   else 'idiomatic_adapter_outcome' if item_kind in ('macro', 'proc_macro')
                                   else 'rust_trait_mechanism_equivalent_outcomes' if 'trait_implementation' in self.roles[identity]
                                   else 'behavioral_equivalent')}
            if item_kind == 'proc_macro':
                row['consumer_macro'] = item['inner']['proc_macro']
            if identity in self.impl_metadata:
                row['implementation'] = self.impl_metadata[identity]
            public.append(row)
        exclusions = defaultdict(list)
        for identity, item in self.index.items():
            if identity in self.paths:
                continue
            if item['crate_id'] != self.local_crate_id:
                disposition = 'external_crate_metadata'
            elif kind(item) == 'impl' and item['inner']['impl']['is_synthetic']:
                disposition = 'synthetic_auto_trait_metadata'
            elif kind(item) == 'impl' and item['inner']['impl'].get('blanket_impl') is not None:
                disposition = 'external_blanket_impl_metadata'
            elif not local_source(item):
                disposition = 'non_source_local_metadata'
            else:
                disposition = 'private_restricted_or_unreachable_local_item'
            exclusions[disposition].append(identity)
        references = defaultdict(set)
        for identity in self.paths:
            item = self.index[identity]
            if kind(item) == 'use':
                continue
            for target in referenced_ids(item['inner']):
                referred = self.index.get(target)
                if referred and referred['crate_id'] == self.local_crate_id and target not in self.paths:
                    references[target].add(identity)
        for target, users in references.items():
            self.unresolved.append({'reason': 'public_signature_references_nonexported_local_item',
                                    'rustdoc_id': target, 'referenced_by': sorted(users, key=int),
                                    'kind': kind(self.index[target]), 'name': self.index[target]['name']})
        accounted = len(public) + sum(len(values) for values in exclusions.values())
        if accounted != len(self.index):
            raise ValueError('Index accounting is not a complete disjoint partition')
        return {'symbols': public, 'excluded_ids': {key: sorted(values, key=int) for key, values in sorted(exclusions.items())},
                'reexport_edges': self.edges,
                'unresolved': sorted(self.unresolved, key=canonical),
                'counts': {'index_items': len(self.index), 'public_symbols': len(public),
                           'public_kinds': dict(sorted(Counter(row['kind'] for row in public).items())),
                           'excluded': {key: len(values) for key, values in sorted(exclusions.items())},
                           'unresolved': len(self.unresolved)}}


def source_identity():
    paths = {Path('Cargo.toml'), Path('Cargo.lock'), Path('build.rs'),
             Path('docs/requirements/controller-properties.txt'), Path('docs/requirements/controller.json')}
    for prefix in ('src', 'crates/statelessness-macros', 'crates/statelessness-debug'):
        directory = ROOT / prefix
        if prefix != 'src':
            paths.add(Path(prefix) / 'Cargo.toml')
            if (directory / 'build.rs').exists():
                paths.add(Path(prefix) / 'build.rs')
            directory /= 'src'
        paths.update(path.relative_to(ROOT) for path in directory.rglob('*') if path.is_file())
    files = []
    for path in sorted(paths):
        data = (ROOT / path).read_bytes()
        try:
            original = subprocess.check_output(['git', 'show', f'{BASELINE}:{path}'], cwd=ROOT, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            original = None
        files.append({'path': str(path), 'sha256': digest(data), 'matches_rust_baseline': data == original})
    return files


def outputs(raw_directory):
    result, rows = {}, []
    for package, crate in CRATES.items():
        path = raw_directory / f'{crate}.json'
        raw = path.read_bytes()
        document = json.loads(raw)
        resolved = Resolver(document, package).resolve()
        rows.extend(resolved.pop('symbols'))
        result[package] = {'raw_path': str(path.relative_to(ROOT)), 'raw_sha256': digest(raw),
                           'raw_size': len(raw), 'raw_mtime_ns': path.stat().st_mtime_ns,
                           'format_version': document['format_version'],
                           'target': document['target'], 'crate_version': document['crate_version'],
                           **resolved}
    return result, rows


def kyoto_receipts():
    receipts = {}
    for package, identity in JOBS.items():
        job = json.loads(command('python3', KYOTO, 'show', identity))
        if job['status'] != 'succeeded' or job['exit_code'] != 0:
            raise ValueError(f'Rustdoc job is not an executed success: {identity}')
        expected = ['cargo', '+nightly', 'rustdoc', '--locked', '-p', package, '--lib',
                    '--', '-Z', 'unstable-options', '--output-format', 'json', '--document-private-items']
        cmd = job['command']
        cargo_index = cmd.index('cargo')
        if cmd[cargo_index:] != expected or 'CARGO_TARGET_DIR=target/native-parity-rustdoc' not in cmd:
            raise ValueError('Rustdoc command/feature scope differs from reviewed native/default scope')
        receipts[package] = {key: job[key] for key in ('id', 'cwd', 'command', 'requested_command', 'status', 'exit_code', 'started', 'finished', 'log')}
        receipts[package]['log_sha256'] = digest(Path(job['log']).read_bytes())
    return receipts


def write_artifacts(directory):
    resolved, rows = outputs(directory)
    sources = source_identity()
    jobs = kyoto_receipts()
    earliest_start = min(job['started'] for job in jobs.values())
    if any((ROOT / item['path']).stat().st_mtime > earliest_start for item in sources):
        raise ValueError('Rust source/build inputs changed after the documentation jobs started; rebuild through Kyoto')
    for package, job in jobs.items():
        modified = resolved[package]['raw_mtime_ns'] / 1e9
        # The external worktree volume reports integer-second mtimes, so a
        # subsecond documentation job can legitimately straddle that boundary.
        if not job['started'] - 1 <= modified <= job['finished'] + 1:
            raise ValueError('Raw JSON timestamp is outside the successful Kyoto compilation window')
    receipt = {'schema_version': 1, 'scope': 'native_default_library_api_only',
               'rust_baseline_commit': BASELINE, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
               'head_commit': command('git', 'rev-parse', 'HEAD'),
               'git_status_at_generation': command('git', 'status', '--porcelain'),
               'compiler': {'rustc': command('rustup', 'run', 'nightly', 'rustc', '-vV'),
                            'rustdoc': command('rustup', 'run', 'nightly', 'rustdoc', '-V'),
                            'cargo': command('rustup', 'run', 'nightly', 'cargo', '-V')},
               'kyoto': jobs, 'source_files': sources,
               'source_inputs_older_than_all_rustdoc_jobs': True,
               'raw_timestamp_resolution_tolerance_seconds': 1,
               'source_manifest_sha256': digest(canonical(sources).encode()),
               'parser_sha256': digest(Path(__file__).read_bytes()),
               'ledger_sha256': '', 'crates': resolved,
               'limitations': ['Only aarch64-apple-darwin with default features was compiled; other targets/features remain unproved.',
                               'Public symbol enumeration does not establish per-language behavior, contract review or runtime qualification.',
                               'Every exported symbol maps to N01-N14; individual native API mappings remain pending review.',
                               'External auto/blanket impl metadata is partitioned separately; provided trait method names are retained on source-backed impls.',
                               'Alias edges record the finite namespace graph; infinite repeated module-alias spellings are not separate symbols.',
                               'Raw JSON is retained under target/ with hashes; source/compiled artifact identity requires these receipt files and Kyoto logs.']}
    ledger = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows)
    receipt['ledger_sha256'] = digest(ledger.encode())
    (OUTPUT / 'semantic-symbols.jsonl').write_text(ledger)
    (OUTPUT / 'semantic-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    unresolved = {package: body['unresolved'] for package, body in resolved.items()}
    (OUTPUT / 'semantic-unresolved.json').write_text(json.dumps(unresolved, indent=2) + '\n')
    table = ['| Crate | Public symbols | Compiler index items | Unresolved |', '| --- | ---: | ---: | ---: |']
    for package, body in resolved.items():
        counts = body['counts']
        table.append(f'| {package} | {counts["public_symbols"]} | {counts["index_items"]} | {counts["unresolved"]} |')
    (OUTPUT / 'semantic-summary.md').write_text('# Compiler-resolved native/default Rust API\n\n' + '\n'.join(table) + '\n\n' +
        'Generated from actual rustdoc JSON schema 57, nightly compiler and Kyoto receipts.\n'
        'See `semantic-receipt.json` for exact commands, compiler, target, raw/source\n'
        'hashes, item partitions, reexport edges and limitations. `semantic-symbols.jsonl`\n'
        'maps each reachable declaration/member/source-backed impl to N01-N14.\n'
        'Native per-symbol equivalence and runtime qualification remain pending.\n')
    print(json.dumps({package: body['counts'] for package, body in resolved.items()}, indent=2))


def check_artifacts(directory):
    receipt = json.loads((OUTPUT / 'semantic-receipt.json').read_text())
    resolved, rows = outputs(directory)
    ledger = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows)
    if resolved != receipt['crates'] or ledger != (OUTPUT / 'semantic-symbols.jsonl').read_text():
        raise ValueError('Semantic compiler inventory drifted; regenerate and review')
    if digest(ledger.encode()) != receipt['ledger_sha256']:
        raise ValueError('Ledger hash mismatch')
    if json.loads((OUTPUT / 'semantic-unresolved.json').read_text()) != {
            package: body['unresolved'] for package, body in resolved.items()}:
        raise ValueError('Unresolved-case artifact differs from compiler accounting')
    if source_identity() != receipt['source_files']:
        raise ValueError('Audited Rust source/build inputs changed since rustdoc capture')
    if digest(Path(__file__).read_bytes()) != receipt['parser_sha256']:
        raise ValueError('Parser changed since receipt generation')
    print('Semantic inventory and source/raw identities match; no runtime qualification implied')


class ResolverTests(unittest.TestCase):
    def document(self):
        def item(identity, name, body, visibility='public'):
            return {'id': identity, 'crate_id': 0, 'name': name, 'visibility': visibility,
                    'span': {'filename': 'src/model.rs', 'begin': [1, 1], 'end': [1, 2]},
                    'inner': body}
        index = {
            '0': item(0, 'stateless', {'module': {'items': [1, 2, 6]}}),
            '1': item(1, 'hidden', {'module': {'items': [3]}}, 'crate'),
            '2': item(2, None, {'use': {'source': 'hidden::E', 'name': 'Alias', 'id': 3, 'is_glob': False}}),
            '3': item(3, 'E', {'enum': {'variants': [4], 'impls': []}}),
            '4': item(4, 'V', {'variant': {'kind': 'plain'}}, 'default'),
            '6': item(6, 'T', {'trait': {'items': [7, 8]}}),
            '7': item(7, 'State', {'assoc_type': {}}, 'default'),
            '8': item(8, 'run', {'function': {}}, 'default'),
        }
        return {'format_version': 57, 'includes_private': True, 'root': 0,
                'index': index, 'paths': {}, 'external_crates': {}}

    def test_private_module_reexport_enum_and_trait_members(self):
        result = Resolver(self.document(), 'statelessness').resolve()
        names = {row['name']: row for row in result['symbols'] if row['name']}
        self.assertIn('stateless::Alias::V', names['V']['exports'])
        self.assertIn('stateless::T::State', names['State']['exports'])
        self.assertNotIn('hidden', names)
        self.assertEqual(result['counts']['unresolved'], 0)

    def test_glob_reexport_and_accounting(self):
        document = self.document()
        document['index']['2']['inner']['use'] = {'source': 'hidden', 'name': 'hidden', 'id': 1, 'is_glob': True}
        result = Resolver(document, 'statelessness').resolve()
        self.assertTrue(any('stateless::E' in row['exports'] for row in result['symbols']))
        self.assertEqual(len(result['symbols']) + sum(map(len, result['excluded_ids'].values())), len(document['index']))

    def test_schema_and_missing_target_fail_closed(self):
        document = self.document()
        document['format_version'] = 58
        with self.assertRaises(ValueError):
            Resolver(document, 'statelessness')
        document['format_version'] = 57
        document['index']['2']['inner']['use']['id'] = 999
        result = Resolver(document, 'statelessness').resolve()
        self.assertGreater(result['counts']['unresolved'], 0)

    def test_impl_members_private_traits_and_external_metadata(self):
        document = self.document()
        index = document['index']
        def item(identity, name, inner, visibility='default', span=True):
            return {'id': identity, 'crate_id': 0, 'name': name, 'visibility': visibility,
                    'span': {'filename': 'src/model.rs', 'begin': [1, 1], 'end': [1, 2]} if span else None,
                    'inner': inner}
        def implementation(identity, trait, members, *, synthetic=False, blanket=None):
            return item(identity, None, {'impl': {
                'trait': trait, 'for': {'resolved_path': {'path': 'E', 'id': 3, 'args': None}},
                'items': members, 'is_synthetic': synthetic, 'blanket_impl': blanket,
                'is_negative': False, 'generics': {}, 'provided_trait_methods': ['default_method']}},
                span=not synthetic and blanket is None)
        index['10'] = item(10, 'new', {'function': {}}, 'public')
        index['11'] = item(11, 'private_method', {'function': {}})
        index['12'] = implementation(12, None, [10, 11])
        index['13'] = item(13, 'HiddenTrait', {'trait': {'items': []}}, 'crate')
        index['14'] = item(14, 'hidden_trait_method', {'function': {}})
        index['15'] = implementation(15, {'path': 'HiddenTrait', 'id': 13}, [14])
        index['16'] = item(16, 'external_trait_method', {'function': {}})
        index['17'] = implementation(17, {'path': 'Display', 'id': 99}, [16])
        index['18'] = implementation(18, None, [], synthetic=True)
        index['19'] = implementation(19, None, [], blanket={'generic': 'T'})
        index['99'] = {'id': 99, 'crate_id': 1, 'name': 'Display', 'visibility': 'public',
                       'span': None, 'inner': {'trait': {'items': []}}}
        document['paths']['99'] = {'crate_id': 1, 'path': ['core', 'fmt', 'Display']}
        document['external_crates']['1'] = {'name': 'core'}
        result = Resolver(document, 'statelessness').resolve()
        names = {row['name']: row for row in result['symbols'] if row['name']}
        self.assertIn('stateless::Alias::new', names['new']['exports'])
        self.assertNotIn('private_method', names)
        self.assertNotIn('hidden_trait_method', names)
        self.assertIn('external_trait_method', names)
        impl = next(row for row in result['symbols'] if row['rustdoc_id'] == '17')
        self.assertEqual(impl['semantic_role'], 'public_std_trait_implementation')
        self.assertEqual(impl['implementation']['provided_trait_methods'], ['default_method'])
        self.assertEqual(result['excluded_ids']['synthetic_auto_trait_metadata'], ['18'])
        self.assertEqual(result['excluded_ids']['external_blanket_impl_metadata'], ['19'])
        self.assertEqual(result['counts']['unresolved'], 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--self-test', action='store_true')
    parser.add_argument('--raw-dir', type=Path, default=RAW)
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResolverTests)
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
    elif args.write:
        write_artifacts(args.raw_dir)
    else:
        check_artifacts(args.raw_dir)


if __name__ == '__main__':
    main()
