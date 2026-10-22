"""Installed Go/Swift compile controls for diagnostic versus checked authority.

Called only inside the parent's Kyoto installed scope after package builds.
Successful positive controls distinguish an authority rejection from an unrelated
compiler/toolchain/import failure. These are compilation counts, not runtime tests.
"""
from pathlib import Path
import json


def qualify(q, attempt, swift_binary_directory):
    root = attempt.work / 'authority-sources'
    root.mkdir(exist_ok=False)
    go = q.require_tool(attempt, 'go')
    swiftc = q.require_tool(attempt, 'swiftc')
    module = Path(swift_binary_directory) / 'Modules'
    if not module.is_dir():
        raise ValueError('installed Swift consumer did not provide its module directory')
    attempt.watch('authority-swift-modules', module)
    cases = []
    go_types = {'checked': '*s.CheckedTurn[int,int,string]',
                'event': 's.Event[int,int,string]',
                'diagnostic': 's.TurnObservation[int,int,string]'}
    swift_types = {'checked': 'CheckedTurn<Int,Int,String>',
                   'event': 'Event<Int,Int,String>',
                   'diagnostic': 'TurnObservation<Int,Int,String>'}
    for case, kind in go_types.items():
        directory = root / ('go-' + case); directory.mkdir()
        (directory / 'go.mod').write_text(
            'module observation-authority\n\ngo 1.23.0\n\n'
            'require github.com/Jagalite/statelessness/go v0.0.0\n'
            'replace github.com/Jagalite/statelessness/go => ' + json.dumps(str(attempt.work / 'go-package')) + '\n',
            encoding='utf-8')
        source = directory / 'main.go'
        source.write_text('package main\nimport s "github.com/Jagalite/statelessness/go"\n'
            'func accept(r *s.Recorder, data ' + kind + ') error {\n'
            ' _, err := s.ObserveCheckedRecorder[int,int,string](r, data); return err\n}\n'
            'func main() {}\n', encoding='utf-8')
        cases.append(('go', case, [go, 'build', '-trimpath', '-o',
                     str(attempt.work / ('authority-go-' + case)), '.'], directory))
    for case, kind in swift_types.items():
        source = root / ('swift-' + case + '.swift')
        source.write_text('import Statelessness\nfunc accept(_ r: Recorder, _ data: ' + kind +
                          ') throws { _ = try r.observeChecked(data) }\n', encoding='utf-8')
        cases.append(('swift', case, [swiftc, '-typecheck', '-I', str(module),
                     '-module-cache-path', str(attempt.work / 'authority-swift-module-cache'),
                     str(source)], root))
    # Watch the complete source membership before any compilation consumes it.
    for path in sorted(root.rglob('*')):
        if path.is_file():
            attempt.register_generated(path, 'authority:' + str(path.relative_to(root)))
    attempt.watch('authority-compile-sources', root)
    report = dict(schema='statelessness.observation-authority-compile.v1',
                  status='running', controls=[], positive_compilations=0,
                  expected_rejections=0, runtime_executed=False)
    attempt.report['authority_compile'] = report
    try:
        for language, case, argv, cwd in cases:
            raw, errors, command = attempt.run('authority-' + language + '-' + case,
                                               argv, cwd, accept_failure=True)
            row = dict(language=language, case=case, exit_code=command['exit_code'],
                       matched=False, stdout=command['stdout'], stderr=command['stderr'])
            report['controls'].append(row)
            if case == 'checked':
                if command['exit_code'] != 0:
                    raise ValueError(language + ': positive checked-token compile control failed')
                report['positive_compilations'] += 1
            else:
                diagnostic = (raw + b'\n' + errors).decode('utf-8', errors='strict')
                actual_type = 'TurnObservation' if case == 'diagnostic' else 'Event'
                markers = ('cannot use', 'does not match', 'cannot convert value of type')
                if (command['exit_code'] == 0 or actual_type not in diagnostic
                        or 'CheckedTurn' not in diagnostic
                        or not any(marker in diagnostic for marker in markers)):
                    raise ValueError(language + ': rejection did not establish the diagnostic/token type boundary')
                report['expected_rejections'] += 1
            row['matched'] = True
        report['status'] = 'passed'
        return report
    except BaseException as error:
        report['status'] = 'failed'; report['error'] = repr(error)
        raise
