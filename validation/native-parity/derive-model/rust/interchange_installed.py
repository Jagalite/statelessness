#!/usr/bin/env python3
"""Execute finite native/Rust interchange through Kyoto after installed success."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', type=Path, required=True)
    parser.add_argument('--rust', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.installed / 'qualification.json').read_text())
    if source['status'] != 'passed':
        raise RuntimeError('installed source qualification must pass first')
    args.output.mkdir(parents=True, exist_ok=False)
    names = {'point': 'Point', 'empty': 'Empty', 'variant': 'EventPoint'}
    reverse = {v: k for k, v in names.items()}
    report = {'status': 'running', 'scope': 'three finite fixtures; four native bundles to Rust and Rust bundle to four installed native consumers',
              'installed_receipt': str(args.installed / 'qualification.json'), 'commands': []}
    def save():
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(label, command):
        path = args.output / (label + '.log')
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        except subprocess.TimeoutExpired as error:
            data = error.stdout or b''
            path.write_bytes(data)
            report['commands'].append({'label': label, 'command': list(map(str, command)), 'timeout': True,
                                       'log': path.name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            save()
            raise
        path.write_bytes(result.stdout)
        report['commands'].append({'label': label, 'command': list(map(str, command)), 'exit_code': result.returncode,
                                   'log': path.name, 'size_bytes': len(result.stdout),
                                   'sha256': hashlib.sha256(result.stdout).hexdigest()})
        save()
        if result.returncode:
            raise RuntimeError(label + ' failed')
        return result.stdout
    try:
        binary = args.rust.read_bytes()
        report['rust_binary'] = {'path': str(args.rust), 'size_bytes': len(binary), 'sha256': hashlib.sha256(binary).hexdigest()}
        rows = []
        for line in run('rust-produce', [str(args.rust)]).decode().splitlines():
            name, encoded = line.split()
            rows.append({'id': reverse[name], 'hex': encoded})
        rust_values = args.output / 'rust-values.json'
        rust_values.write_text(json.dumps(rows) + '\n')
        for language, runner in source['runners'].items():
            native = json.loads((args.installed / 'values' / (language + '.json')).read_text())
            if native != rows:
                raise RuntimeError('Rust/native producer bytes differ: ' + language)
            for row in native:
                run(language + '-to-rust-' + row['id'], [str(args.rust), names[row['id']], row['hex']])
            dest = args.output / ('rust-to-' + language + '.json')
            run('rust-to-' + language, runner + ['transcode', str(rust_values.resolve()), str(dest.resolve())])
            if json.loads(dest.read_text()) != rows:
                raise RuntimeError('native consumer output differs: ' + language)
        report['status'] = 'passed'
        report['native_to_rust_processes'] = 12
        report['rust_to_native_processes'] = 4
        save()
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        save()
        raise

if __name__ == '__main__':
    main()
