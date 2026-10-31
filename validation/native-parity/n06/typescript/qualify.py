#!/usr/bin/env python3
"""Focused TypeScript qualification. Execute only as a frozen shared-default Kyoto job."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


CHECKOUT = Path('/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336')
GENERATED = ('typescript/node_modules', 'typescript/dist')
INHERITED_ENV = ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ')
DRIVER_MEMBER = 'validation/native-parity/n06/typescript/qualify.py'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path, excluded: tuple[str, ...] = (), excluded_files: tuple[str, ...] = ()) -> list[dict]:
    """Retain partial observations/errors; never follow links or hide missing roots."""
    result = []

    def visit(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir())
        except OSError as error:
            result.append({'path': directory.relative_to(root).as_posix(), 'type': 'error', 'error': repr(error)})
            return
        for path in children:
            relative = path.relative_to(root).as_posix()
            entry = {'path': relative}
            try:
                info = path.lstat()
                entry['mode'] = info.st_mode & 0o777
                if stat.S_ISLNK(info.st_mode):
                    entry.update(type='symlink', target=os.readlink(path))
                elif stat.S_ISREG(info.st_mode):
                    if relative in excluded_files:
                        continue
                    entry.update(type='file', size=info.st_size, sha256=digest(path))
                elif stat.S_ISDIR(info.st_mode):
                    if relative in excluded:
                        continue
                    entry['type'] = 'directory'
                    visit(path)
                else:
                    entry['type'] = 'unsupported'
            except OSError as error:
                entry.update(type='error', error=repr(error))
            result.append(entry)

    try:
        if not stat.S_ISDIR(root.lstat().st_mode) or root.resolve() != root.absolute():
            raise RuntimeError(f'manifest root is not an unaliased directory: {root}')
        visit(root)
    except (OSError, RuntimeError) as error:
        result.append({'path': '.', 'type': 'error', 'error': repr(error)})
    # Match the original frozen manifest's sorted(Path.rglob(...)) ordering.
    return sorted(result, key=lambda entry: Path(entry['path']))


def executable_identity(path: str) -> dict:
    invocation = Path(os.path.abspath(path))
    current, links = invocation, []
    # Include links in parent directories and links reached through other links.
    for _ in range(40):
        link = next((part for part in [*reversed(current.parents), current] if part.is_symlink()), None)
        if link is None:
            break
        target = os.readlink(link)
        links.append({'path': str(link), 'target': target, 'mode': link.lstat().st_mode & 0o777,
                      'target_sha256': hashlib.sha256(os.fsencode(target)).hexdigest()})
        destination = Path(target) if os.path.isabs(target) else link.parent / target
        current = Path(os.path.abspath(destination / current.relative_to(link)))
    else:
        raise RuntimeError(f'executable symlink chain too deep: {invocation}')
    resolved = invocation.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f'executable is not a regular file: {resolved}')
    return {'path': str(invocation), 'realpath': str(resolved), 'symlinks': links,
            'mode': info.st_mode & 0o777, 'size': info.st_size, 'sha256': digest(resolved)}


def write_json(path: Path, value: object) -> None:
    # A failed replacement must leave the previous complete receipt available.
    encoded = json.dumps(value, sort_keys=True, indent=2) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass  # Do not replace the primary persistence failure.


class Interruptions:
    """Record signals without unwinding Popen before its owned handle is assigned."""
    def __init__(self) -> None:
        self.received: list[int] = []
        self.previous: dict = {}

    def install(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGCHLD):
            self.previous[sig] = signal.getsignal(sig)
            signal.signal(sig, signal.SIG_DFL if sig == signal.SIGCHLD else self.record)

    def record(self, signum: int, _frame: object) -> None:
        self.received.append(signum)

    def check(self) -> None:
        if self.received:
            raise SystemExit(f'qualification interrupted by signal {self.received[0]}')

    def restore(self) -> None:
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)


class GroupMembers:
    """Inspect a retained leader's group without spawning an auxiliary command."""
    def __init__(self) -> None:
        if not all(hasattr(os, key) for key in ('waitid', 'WNOWAIT', 'WNOHANG', 'WEXITED', 'P_PID')):
            raise RuntimeError('owned group cleanup requires waitid/WNOWAIT; no reaping fallback')
        if sys.platform != 'darwin':
            # Enumerating /proc and then looking up each PGID can miss a child
            # forked by a member that exits between those two operations.
            raise RuntimeError('owned group inventory requires Darwin libproc; other hosts fail closed')
        self.library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.listpids = self.library.proc_listpids
        self.listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        self.listpids.restype = ctypes.c_int

    def __call__(self, pgid: int) -> list[int]:
        # SDK libproc.h: proc_listpids returns bytes; PROC_PGRP_ONLY is 2.
        # Spare entries tolerate growth between sizing and copying. A full
        # buffer is retried, never mistaken for a complete group inventory.
        capacity = 64
        for _ in range(12):
            buffer = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            used = self.listpids(2, pgid, buffer, ctypes.sizeof(buffer))
            error = ctypes.get_errno()
            if used < 0 or (used == 0 and error):
                raise OSError(error, 'proc_listpids failed')
            if used % ctypes.sizeof(ctypes.c_int):
                raise RuntimeError('proc_listpids returned a partial PID')
            if used < ctypes.sizeof(buffer):
                return sorted({pid for pid in buffer[:used // ctypes.sizeof(ctypes.c_int)] if pid > 0})
            capacity *= 2
        raise RuntimeError('owned group inventory exceeded bounded capacity')


def peek_exit(process: subprocess.Popen):
    if process.returncode is not None:
        raise RuntimeError('owned leader was reaped before group verification')
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def observed_exit_code(event) -> int:
    if event.si_code == os.CLD_EXITED:
        return event.si_status
    if event.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
        return -event.si_status
    raise RuntimeError('unexpected waitid exit observation')


def clean_owned_group(process: subprocess.Popen, members: GroupMembers, record: dict) -> None:
    """Retain the leader PID through every inventory/signal; reap only at the end."""
    pgid = process.pid
    cleanup = record['owned_process_group'] = {
        'pgid': pgid, 'leader_retained_with': 'waitid/WNOWAIT', 'signals': [],
        'natural_grace_seconds': 1.0, 'term_grace_seconds': 5.0, 'kill_grace_seconds': 5.0,
        'descendants_outlived_leader': False, 'cleanup_required': False,
        'descendants_gone_before_reap': False, 'leader_reaped': False, 'group_gone': False,
    }
    if pgid <= 1 or pgid == os.getpgrp():
        raise RuntimeError('refusing cleanup of a nonprivate process group')

    def sample() -> bool:
        event = peek_exit(process)  # ChildProcessError means ownership was lost.
        others = [pid for pid in members(pgid) if pid != pgid]
        cleanup.setdefault('initial_descendants', others)
        cleanup['remaining_descendants'] = others
        if event is not None:
            cleanup['observed_leader_exit_code'] = observed_exit_code(event)
            if others:
                cleanup['descendants_outlived_leader'] = True
        return event is not None and not others

    def quiescent_within(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if sample():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))

    def send(sig: signal.Signals) -> None:
        # No poll()/wait() has released this PID. SIGCHLD is default, so no
        # automatic reaper can free it between this ownership check and killpg.
        peek_exit(process)
        cleanup['cleanup_required'] = True
        try:
            os.killpg(pgid, sig)
            cleanup['signals'].append({'signal': sig.name, 'sent': True})
        except ProcessLookupError:
            cleanup['signals'].append({'signal': sig.name, 'group_absent': True})

    try:
        quiet = sample()
        if not quiet and 'observed_leader_exit_code' in cleanup:
            quiet = quiescent_within(cleanup['natural_grace_seconds'])
        if not quiet:
            for sig, grace in ((signal.SIGTERM, cleanup['term_grace_seconds']),
                               (signal.SIGKILL, cleanup['kill_grace_seconds'])):
                send(sig)
                if quiescent_within(grace):
                    quiet = True
                    break
        cleanup['descendants_gone_before_reap'] = quiet
    except BaseException as error:
        cleanup['error'] = repr(error)
        # Inventory failure must not abandon a live command. A conservative KILL
        # is safe only while WNOWAIT still proves ownership of the exact leader.
        try:
            send(signal.SIGKILL)
        except BaseException as cleanup_error:
            cleanup['fallback_error'] = repr(cleanup_error)
    finally:
        # No group inventory or signal is permitted after this point: a reap
        # releases the numerical PGID. The last inventory precedes this release.
        try:
            code = process.wait(timeout=1)
            cleanup['leader_reaped'] = True
            if record.get('exit_code') is None:
                record['exit_code'] = code
        except BaseException as error:
            cleanup['reap_error'] = repr(error)
        cleanup['group_gone'] = cleanup['descendants_gone_before_reap'] and cleanup['leader_reaped']


def run_owned_command(argv: list[str], cwd: Path, env: dict, stdout_path: Path, stderr_path: Path,
                      timeout: float, record: dict, save, members: GroupMembers,
                      interruptions: Interruptions) -> None:
    """Preserve a primary failure while checking the whole group on every exit."""
    process = None
    primary = None
    try:
        interruptions.check()
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                record.update(pid=process.pid, pgid=process.pid)
                save()
                deadline = time.monotonic() + timeout
                while True:
                    interruptions.check()
                    event = peek_exit(process)
                    if event is not None:
                        record['exit_code'] = observed_exit_code(event)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(min(0.05, remaining))
                if record['exit_code']:
                    raise RuntimeError(f"{record['label']} failed with exit {record['exit_code']}; see complete logs")
            except BaseException as error:
                primary = error
                record.update(exception=repr(error), timed_out=isinstance(error, subprocess.TimeoutExpired))
                raise
            finally:
                if process is not None:
                    try:
                        clean_owned_group(process, members, record)
                    except BaseException as error:
                        record['cleanup_error'] = repr(error)
    finally:
        record['interruptions'] = list(interruptions.received)
        record['finished_unix'] = time.time()
        try:
            save()
        except BaseException as error:
            record['receipt_save_error'] = repr(error)
            if primary is None:
                raise
    interruptions.check()
    cleanup = record.get('owned_process_group', {})
    if (not cleanup.get('group_gone') or cleanup.get('descendants_outlived_leader')
            or 'error' in cleanup or 'reap_error' in cleanup or 'cleanup_error' in record):
        raise RuntimeError(f"{record['label']} failed owned process-group verification; see retained exit/logs")


def main() -> int:
    raw_driver_file = __file__
    invocation = {'file': raw_driver_file, 'argv': list(sys.argv),
                  'orig_argv': list(getattr(sys, 'orig_argv', sys.argv)), 'cwd': str(Path.cwd())}
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    source_manifest = args.manifest.resolve()
    if any(path == CHECKOUT or CHECKOUT not in path.parents for path in (source, output, source_manifest)):
        raise RuntimeError('source, manifest and output must stay within this checkout')
    if source == output or source in output.parents or output in source.parents:
        raise RuntimeError('source and output must not overlap')
    if output.exists():
        raise RuntimeError('qualification output already exists; refusing to replace a prior attempt')
    output.mkdir(parents=True)
    logs = output / 'logs'
    work = output / 'work'
    commands: list[dict] = []
    status: dict = {'scope': 'TypeScript N06 focused candidate; no Rust comparison or browser qualification',
                    'state': 'running', 'source': str(source), 'manifest': str(source_manifest),
                    'manifest_sha256': args.manifest_sha256, 'commands': commands,
                    'invocation': invocation, 'integrity_checks': {}, 'capture_errors': {},
                    'versions': {'python': sys.version}}
    env = {key: os.environ[key] for key in INHERITED_ENV if key in os.environ}
    tool_paths = {'python': sys.executable, 'npm_script_shell': '/bin/sh'}
    toolchain_before: dict = {}

    def save() -> None:
        write_json(output / 'receipt.json', status)

    def report_persistence_failure(phase: str, error: BaseException) -> None:
        status['state'] = 'failed'
        status.setdefault('persistence_errors', []).append({'phase': phase, 'error': repr(error)})
        # Keep the original status['error'] and command exception/log paths. The
        # Kyoto stderr stream is independent of a failed receipt-directory write.
        try:
            write_json(output / 'receipt-write-failure.json', status)
        except BaseException as fallback_error:
            status['persistence_errors'].append({'phase': 'fallback_receipt', 'error': repr(fallback_error)})
        try:
            data = ('QUALIFICATION_RECEIPT_FAILURE ' + json.dumps(status, sort_keys=True) + '\n').encode()
            while data:
                written = os.write(2, data)
                if written <= 0:
                    break
                data = data[written:]
        except BaseException:
            # All sinks can fail (for example disk failure plus closed stderr).
            # The preserved prior receipt and a nonzero result remain the limit.
            pass

    def save_final(phase: str) -> None:
        try:
            save()
        except BaseException as error:
            report_persistence_failure(phase, error)

    def capture_driver(phase: str) -> None:
        check = {'verified': False, 'file': raw_driver_file, 'sealed_member': DRIVER_MEMBER,
                 'required_file': str(source / DRIVER_MEMBER)}
        try:
            # Validate the interpreter's spelling before Path/abspath can erase
            # traversal or aliases: link/../file can open a different file than
            # its lexical normalization. Retain the raw spelling in the receipt.
            if (not raw_driver_file.startswith('/')
                    or any(part in ('', '.', '..') for part in raw_driver_file[1:].split('/'))):
                raise RuntimeError('qualifier __file__ must be absolute without traversal or path aliases')
            driver_file = Path(raw_driver_file)
            if str(driver_file) != raw_driver_file:
                raise RuntimeError('qualifier __file__ spelling is not canonical')
            matches = [entry for entry in expected or [] if entry['path'] == DRIVER_MEMBER]
            if len(matches) != 1 or matches[0]['type'] != 'file':
                raise RuntimeError('source manifest must contain exactly one regular qualifier member')
            check['expected'] = matches[0]
            identity = executable_identity(str(driver_file))
            check['identity'] = identity
            info = driver_file.lstat()
            observed = {'path': DRIVER_MEMBER, 'type': 'file' if stat.S_ISREG(info.st_mode) else 'unsupported',
                        'mode': stat.S_IMODE(info.st_mode), 'size': info.st_size, 'sha256': identity['sha256']}
            check['observed'] = observed
            # Reject outer copies, aliases and links even if their bytes match.
            check['verified'] = (driver_file == source / DRIVER_MEMBER
                                 and identity['realpath'] == str(driver_file) and not identity['symlinks']
                                 and observed == matches[0])
            if phase == 'after':
                before = status['integrity_checks'].get('driver_before', {})
                check['unchanged'] = identity == before.get('identity')
                check['verified'] &= before.get('verified', False) and check['unchanged']
        except BaseException as error:
            check.update(verified=False, error=repr(error))
        try:
            write_json(output / f'driver-identity-{phase}.json', check)
        except BaseException as error:
            status['capture_errors']['driver_' + phase] = repr(error)
            check['verified'] = False
        status['integrity_checks']['driver_' + phase] = check
        if not check['verified']:
            status['state'] = 'failed'

    def capture_tree(name: str, root: Path, reference: list[dict] | None,
                     excluded: tuple[str, ...] = (), *, excluded_files: tuple[str, ...] = (),
                     baseline: bool = False) -> list[dict] | None:
        actual = None
        checks: dict = {'verified': False, 'excluded_directories': list(excluded), 'excluded_files': list(excluded_files)}
        try:
            actual = manifest(root, excluded, excluded_files)
            stem, phase = name.rsplit('_', 1)
            write_json(output / f'{stem}-manifest-{phase}.json', actual)
            checks['readable'] = all(entry['type'] in ('file', 'directory', 'symlink') for entry in actual)
            checks['verified'] = checks['readable'] and (baseline or reference is not None and actual == reference)
            if reference is not None:
                before = {entry['path']: entry for entry in reference}
                after = {entry['path']: entry for entry in actual}
                checks.update(added=sorted(after.keys() - before.keys()), missing=sorted(before.keys() - after.keys()),
                              changed=sorted(path for path in before.keys() & after.keys() if before[path] != after[path]))
                selected = {path: entry for path, entry in before.items() if entry['type'] != 'directory'}
                checks['selected_verified'] = all(after.get(path) == entry for path, entry in selected.items())
                checks['selected_count'] = len(selected)
        except BaseException as error:
            checks.update(verified=False, error=repr(error))
        status[name + '_verified'] = checks['verified']
        status['integrity_checks'][name] = checks
        if not checks['verified']:
            status['state'] = 'failed'
        try:
            write_json(output / (name.replace('_', '-') + '-checks.json'), checks)
        except BaseException as error:
            status['capture_errors'][name] = repr(error)
            status['state'] = 'failed'
        return actual

    def capture_manifest_identity(phase: str) -> None:
        check = {'verified': False, 'expected_sha256': args.manifest_sha256}
        try:
            check['sha256'] = digest(source_manifest)
            check['verified'] = check['sha256'] == args.manifest_sha256
        except BaseException as error:
            check.update(verified=False, error=repr(error))
        try:
            write_json(output / f'source-manifest-identity-{phase}.json', check)
        except BaseException as error:
            status['capture_errors']['source_manifest_' + phase] = repr(error)
            check['verified'] = False
        status['integrity_checks']['source_manifest_' + phase] = check
        if not check['verified']:
            status['state'] = 'failed'

    def capture_tools(phase: str) -> dict:
        identities = {}
        for name, path in tool_paths.items():
            try:
                identities[name] = executable_identity(path)
            except BaseException as error:
                identities[name] = {'path': path, 'error': repr(error)}
                status['state'] = 'failed'
        try:
            write_json(output / f'toolchain-{phase}.json', {'executables': identities, 'versions': status['versions']})
        except BaseException as error:
            status['capture_errors']['toolchain_' + phase] = repr(error)
            status['state'] = 'failed'
        status['toolchain_' + phase] = identities
        return identities

    def run(argv: list[str], cwd: Path, label: str, timeout: int = 900) -> None:
        record = {'argv': argv, 'cwd': str(cwd), 'label': label, 'started_unix': time.time(),
                  'stdout': f'logs/{label}.stdout', 'stderr': f'logs/{label}.stderr',
                  'timeout_seconds': timeout, 'exit_code': None}
        commands.append(record)
        save()
        print(json.dumps({'executing': label, 'argv': argv, 'cwd': str(cwd)}), flush=True)
        run_owned_command(argv, cwd, env, logs / f'{label}.stdout', logs / f'{label}.stderr',
                          timeout, record, save, members, interruptions)
        print('KYOTO_PROGRESS ' + json.dumps({'completed': len(commands), 'total': 9, 'phase': 'TypeScript candidate'}), flush=True)

    expected = work_expected = installed_before = consumer_expected = None
    consumer = output / 'installed-consumer'
    installed = output / 'installed-consumer/node_modules/@jagalite/statelessness'
    installed_attempted = work_attempted = consumer_attempted = False

    interruptions = Interruptions()
    try:
        interruptions.install()
        members = GroupMembers()
        status['process_group_verification'] = 'waitid/WNOWAIT retention plus Darwin libproc inventory; other hosts rejected'
        save()
        logs.mkdir()
        for directory in ('tmp', 'home', 'tool-bin'):
            (output / directory).mkdir()
        for name in ('user-npmrc', 'global-npmrc'):
            (output / name).write_text('')
        # These are the only inherited variables. All caches/config/temp files are local.
        inherited_path = env.get('PATH', os.defpath)
        if any(not os.path.isabs(part) for part in inherited_path.split(os.pathsep)):
            raise RuntimeError('PATH must contain only absolute, nonempty directories')
        env.update({'PATH': inherited_path, 'HOME': str(output / 'home'),
                    'TMPDIR': str(output / 'tmp'), 'TMP': str(output / 'tmp'), 'TEMP': str(output / 'tmp'),
                    'npm_config_cache': str(output / 'npm-cache'), 'npm_config_audit': 'false',
                    'npm_config_fund': 'false', 'npm_config_update_notifier': 'false',
                    'npm_config_userconfig': str(output / 'user-npmrc'),
                    'npm_config_globalconfig': str(output / 'global-npmrc'),
                    'npm_config_registry': 'https://registry.npmjs.org/', 'npm_config_include': 'dev',
                    'npm_config_global': 'false', 'npm_config_ignore_scripts': 'true',
                    'npm_config_script_shell': '/bin/sh'})
        status.update(inherited_environment_allowlist=list(INHERITED_ENV), inherited_path=inherited_path,
                      controlled_environment=env)
        capture_manifest_identity('before')
        if not status['integrity_checks']['source_manifest_before']['verified']:
            raise RuntimeError('source manifest identity changed before execution')
        frozen = json.loads(source_manifest.read_text())
        expected = frozen['entries']
        capture_driver('before')
        if not status['integrity_checks']['driver_before']['verified']:
            raise RuntimeError('executing qualifier path/type/mode/bytes differ from the exact sealed member')
        capture_tree('source_before', source, expected)
        if not status['source_before_verified']:
            raise RuntimeError('frozen source membership/type/mode/content changed before execution')
        if any(entry['type'] == 'symlink' for entry in expected):
            raise RuntimeError('frozen inputs must be regular files/directories, without external symlink inputs')
        if any(entry['path'] == directory or entry['path'].startswith(directory + '/')
               for entry in expected for directory in GENERATED):
            raise RuntimeError('frozen source must not contain generated-directory inputs')
        # Config is supplied above, never from a frozen or inherited project npmrc.
        if any(Path(entry['path']).name == '.npmrc' for entry in expected):
            raise RuntimeError('project npmrc is not allowed in this focused qualification')
        work_expected = [dict(entry, mode=0o755) if entry['type'] == 'directory' else dict(entry) for entry in expected]
        work_attempted = True
        try:
            shutil.copytree(source, work, symlinks=True)
            for directory in [work, *(work / entry['path'] for entry in expected if entry['type'] == 'directory')]:
                if directory.resolve() != directory.absolute():
                    raise RuntimeError(f'workcopy directory became a symlink: {directory}')
                directory.chmod(0o755)
        finally:
            capture_tree('work_before', work, work_expected)
        if not status['work_before_verified']:
            raise RuntimeError('workcopy differs from frozen inputs before execution')
        # Copied candidate inputs stay read-only; build products have separate writable directories.
        package = work / 'typescript'
        for name in ('node', 'npm'):
            path = shutil.which(name, path=inherited_path)
            if path is None:
                raise RuntimeError(f'{name} not found on the allowlisted PATH')
            tool_paths[name] = path
        toolchain_before = capture_tools('before')
        if status['state'] == 'failed':
            raise RuntimeError('could not retain executable identities')
        node, npm = (toolchain_before[name]['realpath'] for name in ('node', 'npm'))
        # npm is a JS entrypoint. Invoke it with the pinned Node and put that Node
        # first on PATH for npm lifecycle scripts (including tsc's env shebang).
        (output / 'tool-bin/node').symlink_to(node)
        (output / 'tool-bin/npm').symlink_to(npm)
        env['PATH'] = str(output / 'tool-bin') + os.pathsep + inherited_path
        run([node, '-p', 'JSON.stringify({version:process.version,execPath:process.execPath})'], package, 'node-version')
        node_version = json.loads((logs / 'node-version.stdout').read_text())
        status['versions']['node'] = node_version
        if str(Path(node_version['execPath']).resolve()) != node:
            raise RuntimeError('Node launcher differs from the interpreter actually used')
        run([node, npm, '--version'], package, 'npm-version')
        status['versions']['npm'] = (logs / 'npm-version.stdout').read_text().strip()
        write_json(output / 'toolchain-before.json', {'executables': toolchain_before, 'versions': status['versions']})
        run([node, npm, 'ci', '--ignore-scripts'], package, 'npm-ci')
        # Lockfile integrity pins the downloaded package; retain the exact
        # compiler scripts that the build and baseline type tests will execute.
        tool_paths.update(typescript_cli=str(package / 'node_modules/typescript/bin/tsc'),
                          typescript_loader=str(package / 'node_modules/typescript/lib/tsc.js'),
                          typescript_compiler=str(package / 'node_modules/typescript/lib/_tsc.js'))
        toolchain_before = capture_tools('before')
        if status['state'] == 'failed':
            raise RuntimeError('could not retain compiler identities')
        if any(os.path.lexists(parent / 'node_modules/.bin/node') for parent in (package, *package.parents)):
            raise RuntimeError('dependency-local node would shadow the pinned interpreter')
        run([node, npm, 'run', 'build'], package, 'typescript-build')
        tests = [str(path.relative_to(package)) for path in sorted((package / 'test').glob('*.test.mjs'))]
        if not tests:
            raise RuntimeError('no TypeScript test files found')
        status['test_files'] = tests
        run([node, '--test', '--test-concurrency=1', *tests], package, 'baseline-and-campaign-tests', timeout=1800)
        tarballs = output / 'packages'
        tarballs.mkdir()
        run([node, npm, 'pack', '--ignore-scripts', '--json', '--pack-destination', str(tarballs)], package, 'npm-pack')
        archives = list(tarballs.glob('*.tgz'))
        if len(archives) != 1:
            raise RuntimeError('expected exactly one packed candidate')
        status['package'] = {'path': str(archives[0]), 'sha256': digest(archives[0])}
        example_prefix = 'examples/native-campaign/typescript/'
        consumer_expected = [dict(entry, path=entry['path'][len(example_prefix):]) for entry in work_expected
                             if entry['path'].startswith(example_prefix)
                             and entry['path'] != example_prefix + 'package-lock.json']
        consumer_attempted = True
        shutil.copytree(work / 'examples/native-campaign/typescript', consumer, symlinks=True)
        consumer.chmod(0o755)
        consumer_json = consumer / 'package.json'
        consumer_json.chmod(0o644)
        replacement = {'name': 'n06-fresh-typescript-example', 'private': True, 'type': 'module',
                       'dependencies': {'@jagalite/statelessness': 'file:' + str(archives[0])}}
        encoded = (json.dumps(replacement, sort_keys=True, indent=2) + '\n').encode()
        consumer_json.write_bytes(encoded)
        consumer_expected = sorted([entry for entry in consumer_expected if entry['path'] != 'package.json'] +
            [{'path': 'package.json', 'type': 'file', 'mode': 0o644, 'size': len(encoded),
              'sha256': hashlib.sha256(encoded).hexdigest()}], key=lambda entry: Path(entry['path']))
        status['consumer_package_json_replacement'] = replacement
        write_json(output / 'consumer-manifest-expected.json', consumer_expected)
        capture_tree('consumer_preinstall', consumer, consumer_expected, ('node_modules',), excluded_files=('package-lock.json',))
        if not status['consumer_preinstall_verified']:
            raise RuntimeError('fresh consumer differs from the frozen examples plus package.json replacement')
        installed_attempted = True
        try:
            run([node, npm, 'install', '--ignore-scripts'], consumer, 'fresh-install')
        finally:
            installed_before = capture_tree('installed_before', installed, None, baseline=True)
            capture_tree('consumer_before', consumer, consumer_expected, ('node_modules',), excluded_files=('package-lock.json',))
        if not status['installed_before_verified'] or not status['consumer_before_verified']:
            raise RuntimeError('fresh installed package or consumer source failed integrity checks')
        try:
            run([node, 'node.mjs', 'ordinary'], consumer, 'installed-ordinary-workers')
            run([node, 'node.mjs', 'guided'], consumer, 'installed-guided-workers')
        finally:
            capture_tree('installed_after', installed, installed_before)
            capture_tree('consumer_after', consumer, consumer_expected, ('node_modules',), excluded_files=('package-lock.json',))
        interruptions.check()
        if status['state'] != 'failed':
            status['state'] = 'passed'
    except BaseException as error:
        status['state'] = 'failed'
        status['error'] = repr(error)
    finally:
        # Each capture guards its own failures so no earlier error masks later evidence.
        if installed_attempted and 'installed_after_verified' not in status:
            capture_tree('installed_after', installed, installed_before)
        if consumer_attempted:
            for phase in ('before', 'after'):
                if 'consumer_' + phase + '_verified' not in status:
                    capture_tree('consumer_' + phase, consumer, consumer_expected, ('node_modules',),
                                 excluded_files=('package-lock.json',))
        if work_attempted:
            capture_tree('work_after', work, work_expected, GENERATED)
        capture_tree('source_after', source, expected)
        capture_manifest_identity('after')
        status['source_after_verified'] &= status['integrity_checks']['source_manifest_after']['verified']
        capture_driver('after')
        toolchain_after = capture_tools('after')
        status['toolchain_unchanged'] = bool(toolchain_before) and toolchain_after == toolchain_before
        if not status['toolchain_unchanged']:
            status['state'] = 'failed'
        def reconcile_interruptions() -> bool:
            changed = status.get('interruptions') != interruptions.received
            status['interruptions'] = list(interruptions.received)
            if interruptions.received:
                status['state'] = 'failed'
                status.setdefault('error', f'qualification interrupted by signal {interruptions.received[0]}')
            return changed

        reconcile_interruptions()
        save_final('final_receipt')
        try:
            interruptions.restore()
        except BaseException as error:
            report_persistence_failure('restore_signal_handlers', error)
        # Restore closes the recording window. A signal recorded during the
        # final save or handler restoration cannot subsequently return success.
        if reconcile_interruptions():
            save_final('late_interruption_receipt')
        try:
            print(json.dumps({key: status.get(key) for key in ['state', 'error', 'persistence_errors',
                  'interruptions', 'package', 'source_before_verified', 'source_after_verified',
                  'work_after_verified', 'installed_after_verified', 'consumer_after_verified',
                  'toolchain_unchanged']}), flush=True)
        except BaseException as error:
            report_persistence_failure('final_summary', error)
    return 0 if status['state'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
