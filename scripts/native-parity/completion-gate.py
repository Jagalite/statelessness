#!/usr/bin/env python3
"""Validate reviewed requirements and source-bound evidence; never execute a suite.

A valid receipt covers only its exact requirement tuple. This checker does not
certify the honesty of an author: independent contract/receipt review is required.
Run through Kyoto. --check checks ledger consistency; default fails while open.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '0876717bee85e335449c2c99ada41f6c6e6112d1'
LANGUAGES = ('python', 'go', 'typescript', 'swift')
SOURCE_ROOTS = {'python': ['python/src', 'python/tests', 'python/examples'], 'go': ['go'], 'typescript': ['typescript/src', 'typescript/test', 'typescript/bin', 'typescript/examples'], 'swift': ['swift/Sources', 'swift/Tests']}
PLATFORMS = {'python': ['linux/cpython', 'macos/cpython', 'windows/cpython'], 'go': ['linux/go', 'macos/go', 'windows/go'], 'typescript': ['linux/node', 'macos/node', 'windows/node', 'browser/chromium', 'browser/firefox', 'browser/webkit'], 'swift': ['linux/swift', 'macos/swift']}
MANDATORY_BLOCKERS = ['individual-semantic-equivalence-review', 'supported-runtime-floors-and-current-versions', 'cross-engine-installed-interchange', 'nondefault-rust-inventory-snapshots']
SOURCE_INPUTS = {'python': ['python/pyproject.toml', 'python/README.md', 'python/LICENSE', 'python/qualify_debugger_installed.py'], 'go': [], 'typescript': ['typescript/package.json', 'typescript/package-lock.json', 'typescript/tsconfig.json', 'typescript/README.md', 'typescript/LICENSE', 'typescript/DEBUGGER.md'], 'swift': ['Package.swift']}
GATES = ('contract-review', 'independent-tests', 'negative-controls', 'installed-consumer',
         'fresh-process-replay', 'baseline-regression', 'resource-budget', 'integration-review')


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'not a regular artifact: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(root, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'nonlocal source path: {name}')
    result = root / path
    for p in [result, *result.parents]:
        if p == root: break
        if p.is_symlink(): raise ValueError(f'symlink source path: {name}')
    return result


def read(path):
    return json.loads(path.read_text())


def validate_ledger(root, ledger):
    if ledger['schema'] != 'native-parity-requirements/1' or ledger['rust_baseline'] != BASELINE:
        raise ValueError('wrong ledger schema/baseline')
    symbols_path = local(root, ledger['inventory']['path'])
    if digest(symbols_path) != ledger['inventory']['sha256']:
        raise ValueError('compiler inventory identity changed')
    symbols = [json.loads(line) for line in symbols_path.read_text().splitlines()]
    ids = {s['id']: s for s in symbols}
    if len(ids) != len(symbols) or len(ids) != ledger['inventory']['records']:
        raise ValueError('duplicate/missing compiler inventory records')
    if ledger['default_symbol_disposition'] != 'pending_individual_api_review':
        raise ValueError('unreviewed symbols cannot be promoted')
    seen = set()
    for row in ledger['correspondences']:
        if row['symbol'] in seen or row['symbol'] not in ids:
            raise ValueError('duplicate/unknown correspondence')
        seen.add(row['symbol'])
        if row['definition_sha256'] != ids[row['symbol']]['definition_sha256']:
            raise ValueError('symbol definition changed')
        if row['disposition'] != 'source_correspondence_only' or set(row['native']) != set(LANGUAGES):
            raise ValueError('correspondence is not runtime equivalence')
        for mapping in row['native'].values():
            if digest(local(root, mapping['path'])) != mapping['sha256']:
                raise ValueError('native correspondence changed: ' + mapping['path'])
            if mapping['anchor'] not in local(root, mapping['path']).read_text():
                raise ValueError('native anchor missing')
    capabilities = read(root / 'docs/native-parity/capabilities.json')['features']
    expected = {c['id'] for c in capabilities}
    actual = [c['capability'] for c in ledger['capabilities']]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('capability membership changed')
    if set(ledger['platforms']) != set(LANGUAGES):
        raise ValueError('language coverage missing')
    if set(ledger['required_gates']) != set(GATES):
        raise ValueError('required gates changed without checker review')
    if ledger['platforms'] != PLATFORMS:
        raise ValueError('reviewed platform matrix changed')
    if ledger.get('mandatory_blockers') != MANDATORY_BLOCKERS:
        raise ValueError('mandatory completion blockers changed')
    for language, contexts in ledger['platforms'].items():
        if not contexts or len(contexts) != len(set(contexts)):
            raise ValueError('platform/runtime coverage missing or duplicated: ' + language)
    for row in ledger['capabilities']:
        if not row['dependency'] or set(row['languages']) != set(LANGUAGES):
            raise ValueError('missing owner/language')
        if any(value != 'qualification-pending' for value in row['languages'].values()):
            raise ValueError('ledger declarations cannot promote qualification')
    return ids, seen


def requirements(ledger):
    return {(row['capability'], lang, context, gate)
            for row in ledger['capabilities'] for lang in LANGUAGES
            for context in ledger['platforms'][lang] for gate in ledger['required_gates']}


def validate_receipt(root, receipt, required):
    if receipt['schema'] != 'native-parity-evidence/1' or receipt['rust_baseline'] != BASELINE:
        raise ValueError('wrong receipt schema/baseline')
    key = tuple(receipt['requirement'])
    if key not in required:
        raise ValueError('unknown requirement tuple')
    # The producer must seal the entire declared source membership, not merely
    # an executable or one edited file. Parent defines this reviewed manifest.
    manifest = local(root, receipt['source_manifest']['path'])
    if digest(manifest) != receipt['source_manifest']['sha256']:
        raise ValueError('source manifest changed')
    source = read(manifest)
    if source['schema'] != 'native-parity-source/1' or not source['files'] or not source['roots']:
        raise ValueError('empty/invalid source manifest')
    expected_roots = SOURCE_ROOTS[key[1]] + ['conformance', 'scripts', 'examples', 'docs/native-parity']
    if set(source['roots']) != set(expected_roots) or set(source.get('inputs', [])) != set(SOURCE_INPUTS[key[1]]):
        raise ValueError('source scope narrowed or changed')
    membership = set(source['inputs'])
    for prefix in source['roots']:
        directory = local(root, prefix)
        if not directory.is_dir(): raise ValueError('source root missing')
        for p in directory.rglob('*'):
            if p.is_symlink(): raise ValueError('symlink in source membership')
            if not p.is_dir(): membership.add(str(p.relative_to(root)))
    if membership != set(source['files']):
        raise ValueError('source membership changed')
    for name, expected in source['files'].items():
        if digest(local(root, name)) != expected:
            raise ValueError('source bytes changed: ' + name)
    execution = receipt['execution']
    if execution['kind'] != 'runtime' or execution['exit_code'] != 0 or execution.get('skipped', 0) != 0:
        raise ValueError('not an unskipped runtime pass')
    if not execution['argv'] or '--no-run' in execution['argv']:
        raise ValueError('compile-only/empty execution')
    if not execution['kyoto_job'] or not execution['platform'] or not execution['runtime']:
        raise ValueError('execution identity missing')
    if execution['platform'] + '/' + execution['runtime'] != key[2]:
        raise ValueError('execution platform/runtime mismatch')
    for category in ('tools', 'packages', 'corpora', 'logs'):
        if not receipt[category]: raise ValueError('empty artifact category: ' + category)
        for artifact in receipt[category]:
            # External raw artifacts are allowed, but their bytes must remain.
            if digest(Path(artifact['path'])) != artifact['sha256']:
                raise ValueError('artifact changed: ' + artifact['path'])
    assertions = receipt['assertions']
    if not assertions or len({a['id'] for a in assertions}) != len(assertions):
        raise ValueError('empty/duplicate assertions')
    if any(a['outcome'] != 'passed' or not a['expected'] or a['expected'] != a['observed'] for a in assertions):
        raise ValueError('missing/failed assertion')
    if not receipt['review']['reviewer'] or not receipt['review']['contract']:
        raise ValueError('independent review missing')
    if receipt['review']['reviewer'] == receipt['review']['author']:
        raise ValueError('self-review cannot close requirement')
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=ROOT / 'docs/native-parity/requirements.json')
    parser.add_argument('--receipt', type=Path, action='append', default=[])
    parser.add_argument('--check', action='store_true', help='check structure, not completion')
    args = parser.parse_args()
    try:
        ledger = read(args.ledger)
        ids, mapped = validate_ledger(ROOT, ledger)
        required = requirements(ledger)
        satisfied = set()
        for path in args.receipt:
            key = validate_receipt(ROOT, read(path), required)
            if key in satisfied: raise ValueError('duplicate evidence for requirement')
            satisfied.add(key)
        missing = sorted(required - satisfied)
        result = {'inventory_records': len(ids), 'source_correspondences': len(mapped),
                  'pending_symbol_reviews': len(ids) - len(mapped),
                  'equivalence_qualified_symbols': 0,
                  'required_evidence_cells': len(required), 'satisfied_evidence_cells': len(satisfied),
                  'missing': missing, 'mandatory_blockers': ledger['mandatory_blockers'],
                  'complete': not missing and not ledger['mandatory_blockers']}
        print(json.dumps(result, indent=2))
        return 0 if args.check or result['complete'] else 1
    except (KeyError, ValueError, TypeError, OSError) as error:
        print(f'invalid parity evidence: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
