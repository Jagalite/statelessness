#!/usr/bin/env python3
"""Run through Kyoto: independent wheel build and installed fresh-process proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import platform
import stat
import atexit

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--baseline', action='store_true', help='Run full staged source suite before wheel qualification')
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
work = Path(tempfile.mkdtemp(prefix='stateless-debugger-installed-'))
source = Path(__file__).resolve().parent
commands = []
report = {'workspace': str(work), 'commands': commands, 'qualification': 'candidate optional Python diagnostics; inspect executed assertions',
          'python': sys.version, 'executable': sys.executable, 'platform': platform.platform()}
env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1')

def inventory():
    listed=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z','--','python','conformance'],cwd=source.parent)
    result={}
    for raw in sorted(set(listed.split(b'\0'))-{b''}):
        relative=os.fsdecode(raw); path=source.parent/relative
        # Every ancestor and file must remain regular/non-symlink.
        current=path.parent
        while current!=source.parent:
            if not stat.S_ISDIR(current.lstat().st_mode): raise RuntimeError('non-directory source ancestor: '+str(current))
            current=current.parent
        before=path.lstat()
        if not stat.S_ISREG(before.st_mode): raise RuntimeError('nonregular source: '+relative)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            pinned=os.fstat(fd)
            with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read()
            after=os.fstat(fd)
        finally: os.close(fd)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if identity(before)!=identity(pinned) or identity(pinned)!=identity(after) or identity(after)!=identity(path.lstat()): raise RuntimeError('source changed while reading: '+relative)
        result[relative]={'type':'regular','mode':stat.S_IMODE(before.st_mode),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    return result

before=inventory();report['source_before']=before
def finish():
    try:
        report['source_after']=inventory()
        report['source_stable']=report['source_after']==before
    except BaseException as error:
        report['source_stable']=False; report['source_error']=repr(error)
    (output/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['source_stable']:
        print('SOURCE DRIFT: qualification invalid',file=sys.stderr,flush=True)
        os._exit(1)
atexit.register(finish)

def run(name, argv):
    print(name, flush=True)
    timeout=False
    try:
        result = subprocess.run(list(map(str, argv)), cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        raw=result.stdout; code=result.returncode
    except subprocess.TimeoutExpired as error:
        raw=error.stdout or b''; code=None; timeout=True
    (output / (name + '.log')).write_bytes(raw)
    commands.append({'name': name, 'argv': list(map(str, argv)), 'exit_code': code, 'timeout':timeout,
                     'raw_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    if timeout or code:
        print(raw.decode('utf8', 'replace'), flush=True)
        raise RuntimeError(name + ' failed')
    return raw

package = work / 'package'
package.mkdir()
for relative in before:
    origin=source.parent/relative
    destination=package/Path(relative).relative_to('python') if relative.startswith('python/') else work/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(origin,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise RuntimeError('nonregular staging source')
        with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read()
    finally: os.close(fd)
    if hashlib.sha256(data).hexdigest()!=before[relative]['sha256']: raise RuntimeError('staging source changed')
    destination.write_bytes(data); destination.chmod(before[relative]['mode'])
if inventory()!=before: raise RuntimeError('source changed during staging')
report['staged_files'] = {str(file.relative_to(package)): {'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'bytes':file.stat().st_size}
                          for file in sorted(package.rglob('*')) if file.is_file()}
if {str(Path(k).relative_to('python')):{'sha256':v['sha256'],'bytes':v['bytes']} for k,v in before.items() if k.startswith('python/')}!=report['staged_files']: raise RuntimeError('staging mismatch')
if args.baseline:
    env['PYTHONPATH']=str(package/'src')
    run('full-source-tests',[sys.executable,'-m','unittest','discover','-s',package/'tests','-v'])
    env['PYTHONPATH']=''
report['driver_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
report['source_debugger_sha256'] = hashlib.sha256((package / 'src/statelessness/debugger.py').read_bytes()).hexdigest()
report['source_session_sha256'] = hashlib.sha256((package / 'src/statelessness/debugger_session.py').read_bytes()).hexdigest()
run('builder-venv', [sys.executable, '-m', 'venv', work / 'builder'])
builder = work / 'builder/bin/python'
run('builder-tools', [builder, '-m', 'pip', 'install', 'setuptools>=77', 'wheel'])
run('builder-freeze', [builder, '-m', 'pip', 'freeze', '--all'])
run('wheel', [builder, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', output, package])
run('consumer-venv', [sys.executable, '-m', 'venv', work / 'consumer'])
consumer = work / 'consumer/bin/python'
wheel, = output.glob('*.whl')
report['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
report['wheel_bytes'] = wheel.stat().st_size
run('install', [consumer, '-m', 'pip', 'install', '--no-deps', '--no-index', wheel])
run('fresh-import', [consumer, '-I', '-c', "import statelessness, sys, importlib; names=('debugger','debugger_session','debugger_workbench','debugger_traceview','debugger_diagnostic','debugger_inspect','debugger_watches','debugger_metrics','debugger_dashboard','debugger_live','debugger_effects','debugger_protocol'); assert all('statelessness.'+name not in sys.modules for name in names); [print(importlib.import_module('statelessness.'+name).__file__) for name in names]"])
tests = work / 'python' / 'tests'; tests.mkdir(parents=True)
for file in (package / 'tests').glob('test_debugger*.py'):
    shutil.copy2(file, tests / file.name)
run('installed-tests', [consumer, '-I', '-m', 'unittest', 'discover', '-s', tests, '-p', 'test_debugger*.py', '-v'])
print(json.dumps(report, indent=2), flush=True)
