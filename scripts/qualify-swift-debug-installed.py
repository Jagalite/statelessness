#!/usr/bin/env python3
"""Fresh copied Swift package consumer proof; execute only through Kyoto."""
from pathlib import Path
import hashlib
import json
import platform
import os
import stat
import shutil
import subprocess
import tempfile
import uuid
root = Path(__file__).resolve().parent.parent
def source_manifest():
    paths = [root / "Package.swift"]
    for directory in [root / "swift",root / "examples/native-debug/swift"]:
        for base,dirs,files in os.walk(directory,followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != '.build')
            paths.extend(Path(base)/name for name in sorted(files))
            paths.extend(Path(base)/name for name in dirs if (Path(base)/name).is_symlink())
    result = {}
    for path in sorted(paths):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"nonregular qualification source: {path}")
        data = path.read_bytes()
        result[str(path.relative_to(root))] = {"type":"regular","bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    return result
before = source_manifest()
stage = Path(tempfile.mkdtemp(prefix="stateless-debug-swift-"))
package = stage / "swift-package"
package.mkdir()
shutil.copy2(root / "Package.swift", package / "Package.swift")
shutil.copytree(root / "swift", package / "swift", ignore=shutil.ignore_patterns('.build'))
shutil.copytree(root / "examples/native-debug/swift", stage / "swift", ignore=shutil.ignore_patterns('.build'))
if source_manifest() != before:
    raise RuntimeError(f"source changed during staging; partial snapshot retained at {stage}")
manifest = {str(p.relative_to(stage)): {"bytes": p.stat().st_size,"sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(stage.rglob('*')) if p.is_file()}
version = subprocess.run(["swift","--version"],text=True,capture_output=True)
command = ["swift", "run", "-j", "1", "--package-path", str(stage / "swift"), "DebugConsumer"]
receipt = {"schema": "statelessness.native-debug.swift-installed/1","stage": str(stage),"platform": platform.platform(),"machine": platform.machine(),"swift_version_command": ["swift","--version"],"swift_version_returncode": version.returncode,"swift_version_stdout": version.stdout,"swift_version_stderr": version.stderr,"manifest": manifest,"source_before":before,"command": command,"returncode": None,"scope": "fresh process, copied local package source; no registry publication or full N12 closure"}
output = root / "validation/native-parity/debugger-diagnostics/swift-installed" / (str(uuid.uuid4())+".json")
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(receipt,indent=2)+"\n")
print(f"staged source manifest receipt: {output}",flush=True)
log_path = output.with_suffix('.log')
with log_path.open('xb') as log:
    result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
raw = log_path.read_bytes()
print(raw.decode('utf-8',errors='replace'),end='')
receipt["raw_log"] = {"path":str(log_path),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
receipt["source_after"] = source_manifest()
receipt["source_stable"] = before == receipt["source_after"]
receipt["returncode"] = result.returncode
receipt["qualified"] = result.returncode == 0 and receipt["source_stable"]
output.write_text(json.dumps(receipt,indent=2)+"\n")
raise SystemExit(result.returncode if result.returncode else (0 if receipt["source_stable"] else 1))
