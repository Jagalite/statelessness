"""Six isolated native engine fault controls; crashes/build failures do not count.

Requires Go, TypeScript's compiler, and Swift unless --without-swift is specified.
No original source is modified and no engine is imported by the test orchestrator.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-swift', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    harness = runpy.run_path(str(ROOT / 'conformance/run.py'))
    vectors = json.loads((ROOT / 'conformance/corpus.json').read_text(encoding='utf-8'))['cases']
    vectors += list(runpy.run_path(str(ROOT / 'conformance/portability.py'))['cases']())
    mutations = [
        ('go', 'wrong-rng-increment', 'go/rng.go', '0x9e3779b97f4a7c15', '0x9e3779b97f4a7c14'),
        ('go', 'ignore-duplicate-states', 'go/explore.go', 'if duplicate {', 'if duplicate && false {'),
        ('typescript', 'wrong-rng-increment', 'typescript/src/core.ts', '0x9e3779b97f4a7c15n', '0x9e3779b97f4a7c14n'),
        ('typescript', 'hide-depth-cutoff', 'typescript/src/core.ts', "return report(cutoff ? 'depth_bound' : 'graph_exhausted');", "return report('graph_exhausted');"),
        ('swift', 'wrong-rng-increment', 'swift/Sources/Statelessness/Rng.swift', '0x9e3779b97f4a7c15', '0x9e3779b97f4a7c14'),
        ('swift', 'normalize-unicode-equality', 'swift/Sources/Statelessness/Model.swift', 'left.utf8.elementsEqual(right.utf8)', 'left == right'),
    ]
    reports = []
    for language, name, path, old, new in mutations:
        if language == 'swift' and args.without_swift:
            continue
        original = (ROOT / path).read_text(encoding='utf-8')
        if original.count(old) != 1:
            raise RuntimeError(f'mutation site changed: {language}/{name}')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            folder = 'swift' if language == 'swift' else language
            shutil.copytree(ROOT / folder, target / folder,
                            ignore=shutil.ignore_patterns('.build','.swiftpm','dist','node_modules','*.tgz'))
            if language == 'swift':
                shutil.copy2(ROOT / 'Package.swift', target / 'Package.swift')
            mutated = original.replace(old, new)
            if name == 'normalize-unicode-equality':
                # Mutate hashing with equality: otherwise the control violates
                # Hashable and may crash instead of yielding a semantic mismatch.
                exact_hash = 'for byte in value.utf8 { hasher.combine(byte) }'
                if mutated.count(exact_hash) != 1:
                    raise RuntimeError('Unicode mutation hash site changed')
                mutated = mutated.replace(exact_hash, 'hasher.combine(value)')
            (target / path).write_text(mutated, encoding='utf-8')
            env = dict(os.environ, CGO_ENABLED='0', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off')
            if language == 'go':
                binary = target / ('mutant.exe' if os.name == 'nt' else 'mutant')
                build = ['go','build','-o',str(binary),'./cmd/stateless-corpus']; cwd = target / 'go'; runner = [str(binary)]
            elif language == 'typescript':
                local = ROOT / 'typescript/node_modules/typescript/bin/tsc'
                compiler = ['node',str(local)] if local.exists() else [shutil.which('tsc') or 'tsc']
                build = compiler+['-p','tsconfig.json']; cwd = target / 'typescript'; runner = ['node',str(cwd / 'bin/corpus.mjs')]
            else:
                build = ['swift','build','--package-path',str(target),'-j','2']; cwd = target; runner = [str(target / '.build/debug/stateless-corpus')]
            completed = subprocess.run(build,cwd=cwd,env=env,capture_output=True,timeout=180)
            if completed.returncode:
                raise RuntimeError(f'{language}/{name} build failed; NOT detection:\n{completed.stderr.decode(errors="replace")}')
            actual = harness['invoke'](runner,[c['request'] for c in vectors],60)
            # A process crash/timeout/malformed response raises before this point.
            detected = [c['id'] for c,r in zip(vectors,actual) if not harness['equivalent'](r,c['expected'])]
            if not detected: raise RuntimeError(f'mutation survived: {language}/{name}')
            reports.append(dict(implementation=language,mutation=name,detected_by=detected))
    result = dict(mutants=len(reports),detected=len(reports),results=reports)
    rendered = json.dumps(result,indent=2)+'\n'
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(rendered,encoding='utf-8')
    print(rendered)


if __name__ == '__main__': main()
