#!/usr/bin/env python3
"""Exercise actual native audit producers, sidecars, and native self-replay.

Run through Kyoto from any directory. This source-tree integration test builds
temporary consumers; it is not installed-package or cross-build qualification.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python/src'))
sys.path.insert(0, str(ROOT / 'examples/native-stateful/rust'))

from bridge import main as bridge, read_envelope  # noqa: E402
from statelessness.trace_binary import audit_from_bytes  # noqa: E402


def run(command, cwd, env):
    print('+', json.dumps(command), 'cwd=', str(cwd), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'python/src')
    with tempfile.TemporaryDirectory(prefix='stateless-native-config-') as directory:
        tmp = Path(directory)

        go = tmp / 'go'
        shutil.copytree(ROOT / 'examples/native-stateful/go', go)
        (go / 'go.mod').write_text(
            'module statefulconsumer\n\ngo 1.23.0\n\n'
            'require github.com/Jagalite/statelessness/go v0.0.0\n'
            'replace github.com/Jagalite/statelessness/go => '
            + json.dumps(str(ROOT / 'go')) + '\n', encoding='utf-8')
        go_binary = go / 'consumer'
        run(['go', 'build', '-o', str(go_binary), '.'], go, env)

        run(['npm', 'run', 'build'], ROOT / 'typescript', env)
        ts = tmp / 'typescript'
        ts.mkdir()
        shutil.copy2(ROOT / 'examples/native-stateful/typescript/network.mjs', ts)
        (ts / 'node_modules/@jagalite').mkdir(parents=True)
        (ts / 'node_modules/@jagalite/statelessness').symlink_to(ROOT / 'typescript', target_is_directory=True)

        swift = tmp / 'swift'
        shutil.copytree(ROOT / 'examples/native-stateful/swift', swift,
                        ignore=shutil.ignore_patterns('.build', '.swiftpm'))
        (tmp / 'swift-package').symlink_to(ROOT, target_is_directory=True)
        run(['swift', 'build'], swift, env)
        swift_binary = swift / '.build/debug/StatefulConsumer'

        producers = {
            'python': ([sys.executable, str(ROOT / 'examples/native-stateful/python/network.py')], ROOT, 'native-python-v1'),
            'go': ([str(go_binary)], go, 'native-go-v1'),
            'typescript': (['node', str(ts / 'network.mjs')], ts, 'native-typescript-v1'),
            'swift': ([str(swift_binary)], swift, 'native-swift-v1'),
        }
        for name, (command, cwd, expected_build) in producers.items():
            control = subprocess.run(command + ['control', str(tmp / 'unused-control')],
                                     cwd=cwd, env=env, text=True, capture_output=True)
            require(control.returncode == 0 and control.stdout.strip() == 'network-control-detected',
                    name + ': weakened-property control did not complete with the expected outcome')
            trace = tmp / (name + '.json')
            binary = Path(str(trace) + '.audit.bin')
            run(command + ['record-audit', str(binary)], cwd, env)
            original = audit_from_bytes(binary.read_bytes())
            bridge('from-binary', str(trace))
            converted = read_envelope(trace)
            require(converted == original, name + ': audit/config changed during conversion')
            require(converted.config.strategy == 'stateful-fixture', name + ': unexpected strategy')
            require(converted.config.seed is None, name + ': unexpected seed')
            builds = [v for k, v in converted.config.parameters if k == 'stateless.engine.build']
            require(builds == [expected_build], name + ': incorrect recorded engine build')
            run(command + ['replay', str(trace)], cwd, env)
            if name == 'python':
                # A failed strict replay must remain a failure even when Python
                # assertions are disabled. Check both a valid replay and a
                # deliberately incompatible build with the same optimized env.
                optimized = {**env, 'PYTHONOPTIMIZE': '1'}
                run(command + ['replay', str(trace)], cwd, optimized)
                altered = json.loads(trace.read_text(encoding='utf-8'))
                altered['metadata']['build'] += ':different-build'
                invalid = tmp / 'python-incompatible.json'
                invalid.write_text(json.dumps(altered), encoding='utf-8')
                result = subprocess.run(command + ['replay', str(invalid)], cwd=cwd,
                                        env=optimized, text=True, capture_output=True)
                require(result.returncode != 0 and 'exact failure was not reproduced' in result.stderr,
                        'optimized Python replay failed to reject incompatible build')
                print('python: optimized replay rejected incompatible build', flush=True)
            print(name + ': actual audit configuration preserved and self-replay passed', flush=True)


if __name__ == '__main__':
    main()
