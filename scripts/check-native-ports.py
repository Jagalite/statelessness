#!/usr/bin/env python3
"""Build native packages, exercise separate consumers, and qualify installed runners.

No source downloads or registry publication. Install developer toolchains first.
Optional --rust adds an already built Rust adapter to the producer/consumer matrix.
The Python library must already be installed in this interpreter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rust', type=Path)
    parser.add_argument('--without-swift', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/native-ports')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    logs = out / 'logs'; logs.mkdir(exist_ok=True)
    packages = out / 'packages'; packages.mkdir(exist_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''

    def run(name, argv, cwd=ROOT, env=None):
        print(f'[{name}] ' + ' '.join(map(str, argv)), flush=True)
        completed = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        text = completed.stdout.decode('utf-8', 'replace')
        (logs / (name + '.log')).write_text(text, encoding='utf-8')
        if completed.returncode:
            print(text, flush=True)
            raise RuntimeError(f'{name} failed with exit {completed.returncode}')
        print('\n'.join(text.splitlines()[-3:]), flush=True)
        return text

    def tool(name):
        result = shutil.which(name)
        if not result:
            raise RuntimeError(f'{name} must be installed')
        return result

    def copy(source, destination):
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.build', '.swiftpm', 'node_modules', 'dist', '__pycache__', '*.tgz'))

    def archive(source, name):
        dest = packages / (name + '.tar.gz')
        with tarfile.open(dest, 'w:gz') as stream:
            # Do not ship compiler products, dependency caches, or local metadata.
            def clean(info):
                if any(x in info.name.split('/') for x in ('.build', '.swiftpm', 'node_modules', 'dist', '__pycache__')):
                    return None
                return info
            stream.add(source, arcname=name, filter=clean)
        return dest

    go, node, npm = tool('go'), tool('node'), tool('npm')
    environment = dict(os.environ, CGO_ENABLED='0', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off')
    toolchains = {'python': sys.version, 'platform': platform.platform()}
    toolchains['go'] = run('go-version', [go, 'version']).strip()
    toolchains['node'] = run('node-version', [node, '--version']).strip()
    toolchains['npm'] = run('npm-version', [npm, '--version']).strip()
    go_source = out / 'go-package'
    copy(ROOT / 'go', go_source)
    run('go-tests-cgo-disabled', [go, 'test', '-count=1', '-v', './...'], go_source, environment)
    run('go-vet', [go, 'vet', './...'], go_source, environment)
    if os.name != 'nt':
        run('go-race', [go, 'test', '-race', '-count=1', './...'], go_source, dict(environment, CGO_ENABLED='1'))
    go_runner = out / ('go-corpus' + suffix)
    run('go-build', [go, 'build', '-trimpath', '-o', go_runner, './cmd/stateless-corpus'], go_source, environment)
    go_consumer = out / 'go-consumer'; go_consumer.mkdir(exist_ok=True)
    (go_consumer / 'go.mod').write_text('module consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n', encoding='utf-8')
    shutil.copy2(ROOT / 'go/examples/counter/main.go', go_consumer / 'main.go')
    run('go-separate-consumer', [go, 'run', '.'], go_consumer, environment)
    archive(go_source, 'statelessness-go-0.1.0')

    ts = ROOT / 'typescript'
    run('typescript-native-tests', [npm, 'test'], ts)
    run('npm-pack', [npm, 'pack', '--pack-destination', packages], ts)
    tarball = packages / 'jagalite-statelessness-0.1.0.tgz'
    consumer = out / 'typescript-consumer'
    if consumer.exists(): shutil.rmtree(consumer)
    consumer.mkdir()
    (consumer / 'package.json').write_text('{"name":"consumer","private":true,"type":"module"}\n', encoding='utf-8')
    run('npm-install-packed-package', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], consumer)
    installed = consumer / 'node_modules/@jagalite/statelessness'
    package = json.loads((installed / 'package.json').read_text(encoding='utf-8'))
    if package.get('dependencies'):
        raise RuntimeError('native TypeScript runtime must remain dependency-free')
    shutil.copy2(ts / 'examples/counter.ts', consumer / 'counter.ts')
    (consumer / 'tsconfig.json').write_text(json.dumps(dict(compilerOptions=dict(strict=True,target='ES2022',module='NodeNext',moduleResolution='NodeNext',outDir='build',lib=['ES2022','DOM']),files=['counter.ts'])), encoding='utf-8')
    local_tsc = ts / 'node_modules/typescript/bin/tsc'
    compiler = [node, local_tsc] if local_tsc.exists() else [tool('tsc')]
    toolchains['typescript'] = run('typescript-version', compiler+['--version']).strip()
    run('typescript-public-declarations', compiler+['-p','tsconfig.json'], consumer)
    run('typescript-separate-consumer', [node, 'build/counter.js'], consumer)
    runners = [[sys.executable, '-I', '-m', 'statelessness.conformance', '--runner']]
    if args.rust:
        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust adapter not found: {rust}')
        runners.append([str(rust)])
        toolchains['rust_adapter_sha256'] = hashlib.sha256(rust.read_bytes()).hexdigest()
    runners += [[str(go_runner)], [node, str(installed / 'bin/corpus.mjs')]]

    if not args.without_swift:
        swift = tool('swift')
        toolchains['swift'] = run('swift-version', [swift, '--version']).strip()
        source = out / 'swift-package'
        if source.exists(): shutil.rmtree(source)
        source.mkdir()
        shutil.copy2(ROOT / 'Package.swift', source / 'Package.swift')
        shutil.copy2(ROOT / 'LICENSE', source / 'LICENSE')
        copy(ROOT / 'swift', source / 'swift')
        # All package tests use XCTest. Avoid launching the unused Swift Testing
        # runner, whose Swift 6.0.3 Darwin loader cannot locate Xcode's XCTestCore.
        run('swift-native-tests', [swift, 'test', '--disable-swift-testing', '--package-path', source, '-j', '2'])
        run('swift-release-build', [swift, 'build', '--package-path', source, '-c', 'release', '-j', '2'])
        binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', source, '-c', 'release', '--show-bin-path']).strip()
        swift_runner = out / ('swift-corpus' + suffix)
        shutil.copy2(Path(binary_dir) / ('stateless-corpus' + suffix), swift_runner)
        swift_consumer = out / 'swift-consumer'
        copy(ROOT / 'swift/Examples/Counter', swift_consumer)
        manifest = (swift_consumer / 'Package.swift').read_text(encoding='utf-8').replace('path: "../../.."', 'path: "../swift-package"')
        (swift_consumer / 'Package.swift').write_text(manifest, encoding='utf-8')
        run('swift-separate-consumer', [swift, 'run', '--package-path', swift_consumer, '-j', '2', 'Counter'])
        archive(source, 'statelessness-swift-0.1.0')
        runners.append([str(swift_runner)])

    command = [sys.executable, '-I', ROOT / 'conformance/run.py', '--report', out / 'conformance.json']
    for runner in runners:
        command += ['--runner', json.dumps(runner)]
    run('installed-cross-language-conformance', command)
    # Record exactly which source bytes and distributables were qualified.
    tracked = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    sources = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in tracked if p and (ROOT / p).is_file()}
    report = dict(source_commit=os.environ.get('GITHUB_SHA'), toolchains=toolchains, runners=runners,
                  packages={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in packages.iterdir() if p.is_file()},
                  source_files=sources, source_manifest_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest())
    (out / 'qualification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f'Native package qualification passed; reports and distributions: {out}')


if __name__ == '__main__':
    main()
