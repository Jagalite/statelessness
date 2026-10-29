"""Sealed N06-only Python tests; submit via Kyoto. No baseline/install claim."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tarfile
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    """Full lstat membership including directories, links and unexpected types."""
    def unreadable(error):
        raise error
    result = {}
    paths = [root]
    if stat.S_ISDIR(root.lstat().st_mode):
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
            paths.extend(Path(directory) / name for name in dirs + files)
    for path in sorted(set(paths)):
        info = path.lstat()
        item = {"mode": oct(stat.S_IMODE(info.st_mode))}
        if stat.S_ISLNK(info.st_mode):
            item.update(type="symlink", target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            item.update(type="directory")
        elif stat.S_ISREG(info.st_mode):
            data = path.read_bytes()
            item.update(type="file", size=len(data), sha256=sha(data))
        else:
            item.update(type="other", stat_type=stat.S_IFMT(info.st_mode))
        result[str(path.relative_to(root))] = item
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def clean_owned_group(process):
    """Require the private invocation's whole group to exit, even after main exit."""
    def exists():
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
    deadline = time.monotonic() + 1.0
    while exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    forced = exists()
    if forced:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2.0
        while exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    return {"forced_cleanup": forced, "group_absent": not exists()}


def main():
    root = Path(__file__).resolve().parent
    output = root / "execution"
    output.mkdir(exist_ok=False)  # Never overwrite any prior receipt.
    source = output / "source"
    receipt = {"schema": "n06-python-focused/1", "status": "running",
               "scope": "N06 campaign tests only; baseline and fresh installation remain separate",
               "python": sys.version, "executable": sys.executable,
               "source_manifest_sha256": sys.argv[1] if len(sys.argv) == 2 else None,
               "extraction": {"regular_file_mode": "0o444", "directory_mode": "0o755",
                              "source_archive_modes_verified": False}}
    before, process, code = None, None, 1
    started = time.monotonic()
    try:
        manifest_bytes = (root / "source.json").read_bytes()
        if len(sys.argv) != 2 or sha(manifest_bytes) != sys.argv[1]:
            raise RuntimeError("focused source manifest identity differs")
        manifest = json.loads(manifest_bytes)
        if sha(Path(__file__).read_bytes()) != manifest["runner_sha256"]:
            raise RuntimeError("focused runner identity differs")
        archive = root / "source.tar.gz"
        if sha(archive.read_bytes()) != manifest["archive_sha256"]:
            raise RuntimeError("focused source archive identity differs")
        source.mkdir(mode=0o755)
        source.chmod(0o755)
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if {m.name for m in members} != set(manifest["files"]) or len(members) != len(manifest["files"]):
                raise RuntimeError("source archive membership differs")
            for member in members:
                expected = manifest["files"][member.name]
                if (not member.isfile() or expected["type"] != "file" or member.name.startswith("/")
                        or ".." in Path(member.name).parts or oct(member.mode) != expected["mode"]):
                    raise RuntimeError("source archive type/path/mode differs: " + member.name)
                data = bundle.extractfile(member).read()
                if sha(data) != expected["sha256"] or len(data) != expected["size"]:
                    raise RuntimeError("source identity differs: " + member.name)
                path = source / member.name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_bytes(data)
                path.chmod(0o444)
            for directory, dirs, files in os.walk(source):
                Path(directory).chmod(0o755)
        receipt["extraction"]["source_archive_modes_verified"] = True
        before = inventory(source)
        write_json(output / "source-before.json", before)
        receipt["source_before_sha256"] = sha((output / "source-before.json").read_bytes())
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")
               and k not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__")}
        env.update(PYTHONPATH=str(source / "python/src"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "python/tests",
                   "-p", "test_campaign*.py", "-v"]
        receipt.update(argv=command, cwd=str(source), environment={k: env[k]
            for k in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE")})
        write_json(output / "receipt.json", receipt)
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(command, cwd=source, env=env, stdout=stdout, stderr=stderr,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                receipt["timeout"] = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code = 124
        receipt.update(exit_code=code, status="passed" if code == 0 else "failed")
    except BaseException as error:
        receipt.update(status="failed", error=repr(error))
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        code = 1
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        if process is not None:
            try:
                cleanup = clean_owned_group(process)
                receipt["owned_process_group"] = cleanup
                if cleanup["forced_cleanup"] or not cleanup["group_absent"]:
                    code, receipt["status"] = 127, "descendants_outlived_test_runner"
            except BaseException as error:
                code, receipt["status"] = 127, "process_group_cleanup_failed"
                receipt["cleanup_error"] = repr(error)
        if before is not None or os.path.lexists(source):
            try:
                after = inventory(source)
                write_json(output / "source-after.json", after)
                receipt["source_after_sha256"] = sha((output / "source-after.json").read_bytes())
                receipt["source_unchanged"] = before is not None and after == before
                if before is not None:
                    receipt["source_changes"] = {"added": sorted(after.keys() - before.keys()),
                        "removed": sorted(before.keys() - after.keys()),
                        "changed": sorted(k for k in after.keys() & before.keys() if after[k] != before[k])}
                if before is not None and after != before:
                    code, receipt["status"] = 125, "source_changed"
            except BaseException as error:
                code, receipt["status"] = 125, "manifest_failed"
                receipt["source_unchanged"] = False
                receipt["manifest_error"] = repr(error)
                write_json(output / "source-after.json", {"error": repr(error),
                           "source_root_present": os.path.lexists(source)})
                receipt["source_after_sha256"] = sha((output / "source-after.json").read_bytes())
        for name in ("stdout.log", "stderr.log"):
            path = output / name
            if path.exists():
                try:
                    data = path.read_bytes()
                    receipt[name + "_sha256"] = sha(data)
                except BaseException as error:
                    code, receipt["status"] = 126, "log_failed"
                    receipt[name + "_error"] = repr(error)
        receipt["exit_code"] = code
        write_json(output / "receipt.json", receipt)
    # Receipt is durable before forwarding raw logs to the Kyoto log stream.
    for name in ("stdout.log", "stderr.log"):
        path = output / name
        if path.exists():
            print(path.read_text(errors="replace"), end="", file=sys.stdout if name == "stdout.log" else sys.stderr)
    print(json.dumps({"status": receipt["status"], "receipt": str(output / "receipt.json")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
