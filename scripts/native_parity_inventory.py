#!/usr/bin/env python3
"""Conservative Rust source inventory; does not resolve Rust's semantic exports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '0876717bee85e335449c2c99ada41f6c6e6112d1'
DEST = ROOT / 'docs/native-parity/rust-baseline.json'
ROOTS = ('src/', 'crates/statelessness-macros/src/', 'crates/statelessness-debug/src/')
LANGUAGES = ('python', 'go', 'typescript', 'swift')
GAPS = {
    'assurance': 'N10', 'automatic': 'N03', 'campaign': 'N06',
    'composition': 'N08', 'conformance': 'N14', 'demo': 'N14',
    'evidence': 'N11', 'execution': 'N01', 'explore': 'N01',
    'ffi': 'N14', 'ffi_v2': 'N14', 'guided': 'N05', 'lib': 'N14',
    'lifecycle': 'N09', 'main': 'N14', 'model': 'N01', 'modeling': 'N13',
    'monitor': 'N07', 'network': 'N08', 'observation': 'N07',
    'operations': 'N14', 'oracle': 'N02', 'reference': 'N10',
    'research': 'N14', 'trace': 'N11', 'value_codec': 'N11',
}
# Strings/comments are blanked with offsets and line breaks preserved. Lifetimes
# are not character literals; raw strings may contain arbitrary quote/hash runs.
LEXEME = re.compile(r'//[^\n]*|/\*|(?:b?r)(\#*)"|b?"|b?\'(?:\\.|[^\\\'\n])\'')
DECL = re.compile(r'\b(?:(pub)(\s*\([^)]*\))?\s+)?(?:(?:unsafe|async|default|extern|const(?=\s+fn))\s+)*(fn|struct|enum|trait|type|const|static|mod|use|union)\s+([A-Za-z_][A-Za-z_0-9]*|\*|\{)|\b(pub)(\s*\([^)]*\))?\s+([A-Za-z_][A-Za-z_0-9]*)\s*:')


def mask_source(source):
    chars = list(source)
    pos = 0
    while match := LEXEME.search(source, pos):
        start = match.start()
        token = match.group()
        end = match.end()
        if token == '/*':
            depth = 1
            while depth and end < len(source):
                if source.startswith('/*', end):
                    depth += 1
                    end += 2
                elif source.startswith('*/', end):
                    depth -= 1
                    end += 2
                else:
                    end += 1
        elif token.endswith('"'):
            raw = re.match(r'b?r(\#*)"', token)
            if raw:
                close = '"' + raw.group(1)
                found = source.find(close, end)
                end = len(source) if found < 0 else found + len(close)
            else:
                while end < len(source):
                    if source[end] == '\\':
                        end += 2
                    elif source[end] == '"':
                        end += 1
                        break
                    else:
                        end += 1
        for index in range(start, min(end, len(source))):
            if chars[index] != '\n':
                chars[index] = ' '
        pos = end
    return ''.join(chars)


def gap_for(path, name):
    if path.startswith('crates/statelessness-macros/'):
        return ['N13']
    if path.startswith('crates/statelessness-debug/'):
        return ['N12']
    module = path.split('/')[1].split('.')[0]
    gap = GAPS[module]  # New modules require an explicit mapping decision.
    if module == 'explore':
        if 'shrink' in name.lower():
            return ['N04']
        if 'fuzz' in name.lower():
            return ['N03']
    return [gap]


def declarations(path, source):
    masked = mask_source(source)
    result = []
    for match in DECL.finditer(masked):
        public, restricted, kind, name, field_pub, field_restricted, field_name = match.groups()
        if field_pub:
            kind, name = 'field', field_name
            public, restricted = field_pub, field_restricted
        # Include private/implicit fn/type/const declarations: these account for
        # trait members, impl methods and helper candidates without guessing
        # trait ownership or export reachability from lexical nesting.
        if not public and kind not in ('fn', 'type', 'const'):
            continue
        visibility = ('restricted' if restricted else 'public') if public else 'implicit_or_private'
        line = source.count('\n', 0, match.start()) + 1
        column = match.start() - source.rfind('\n', 0, match.start())
        result.append({
            'id': f'{path}:{line}:{column}:{kind}:{name}',
            'path': path, 'line': line, 'column': column, 'kind': kind,
            'name': name, 'visibility': visibility,
            'gap_ids': gap_for(path, name),
            'mapping_review': 'pending',
            'native_status': {language: 'not_started' for language in LANGUAGES},
        })
    return result


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def inventory(working=False):
    paths = sorted(path for path in git('ls-tree', '-r', '--name-only', BASELINE).decode().splitlines()
                   if path.endswith('.rs') and path.startswith(ROOTS))
    if working:
        actual = sorted(str(path.relative_to(ROOT)) for prefix in ROOTS
                        for path in (ROOT / prefix).rglob('*.rs'))
        if actual != paths:
            raise ValueError('Rust source file set differs from pinned baseline; review and update baseline explicitly')
    files, items = [], []
    for path in paths:
        data = (ROOT / path).read_bytes() if working else git('show', f'{BASELINE}:{path}')
        source = data.decode()
        files.append({'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
        items.extend(declarations(path, source))
    return {'schema_version': 1, 'baseline_commit': BASELINE,
            'scope': 'lexical_candidates_not_semantic_exports',
            'source_roots': list(ROOTS), 'files': files, 'items': items}


def validate(document):
    ids = [item['id'] for item in document['items']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate declaration identifiers')
    for item in document['items']:
        if not item['gap_ids'] or any(not re.fullmatch(r'N(?:0[1-9]|1[0-4])', gap) for gap in item['gap_ids']):
            raise ValueError(f'Missing/invalid mapping: {item["id"]}')
        if set(item['native_status']) != set(LANGUAGES):
            raise ValueError(f'Missing language: {item["id"]}')
        if any(value != 'not_started' for value in item['native_status'].values()):
            raise ValueError('Bootstrap inventory cannot assert implementation or qualification; use reviewed capability evidence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write', action='store_true', help='regenerate from pinned git source')
    mode.add_argument('--check', action='store_true', help='validate pinned and working sources (default)')
    args = parser.parse_args()
    expected = inventory()
    validate(expected)
    if args.write:
        # One source/candidate per line keeps this large audit artifact reviewable.
        header = {key: value for key, value in expected.items() if key not in ('files', 'items')}
        output = json.dumps(header, indent=2)[:-2]
        for key in ('files', 'items'):
            output += ',\n  ' + json.dumps(key) + ': [\n'
            output += ',\n'.join('    ' + json.dumps(row) for row in expected[key])
            output += '\n  ]'
        DEST.write_text(output + '\n}\n')
    else:
        saved = json.loads(DEST.read_text())
        validate(saved)
        if saved != expected:
            raise SystemExit('Inventory differs from pinned baseline; regenerate and review mapping changes')
        if inventory(working=True) != expected:
            raise SystemExit('Working Rust sources drifted from pinned baseline; record a reviewed baseline update')
    print(f'{len(expected["items"])} lexical candidates in {len(expected["files"])} pinned files; semantic export audit pending')


if __name__ == '__main__':
    main()
