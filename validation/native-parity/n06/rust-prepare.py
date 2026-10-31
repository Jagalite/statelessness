"""Prepare an immutable source copy and compact manifest; does not execute code."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
ALLOWED = Path("/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336")
assert ROOT == ALLOWED, ROOT
DEST = ROOT / "validation/native-parity/n06" / sys.argv[1]
DEST.mkdir(exist_ok=False)
SNAPSHOT = DEST / "snapshot"
SNAPSHOT.mkdir()
tracked = subprocess.check_output([
    "git", "ls-files", "-z", "--", "Cargo.toml", "Cargo.lock", "build.rs", "README.md",
    "src", "crates", "benches", "docs/requirements", "tests/campaign.rs"
], cwd=ROOT).decode().split("\0")
paths = {Path(p) for p in tracked if p}
paths.add(Path("tests/native_campaign_reference.rs"))
paths.add(Path("docs/native-parity/N06-RUST-CONTRACT.md"))
paths.add(Path("validation/native-parity/n06/rust-prepare.py"))
for path in (ROOT / "conformance/fixtures").glob("native-campaign*"):
    paths.update([path.relative_to(ROOT)] if path.is_file() else [
        p.relative_to(ROOT) for p in path.rglob("*") if p.is_file() and "target" not in p.parts
    ])
records = {}
for relative in sorted(paths):
    source = ROOT / relative
    info = source.lstat()
    assert stat.S_ISREG(info.st_mode), relative
    data = source.read_bytes()
    records[str(relative)] = {"type": "file", "mode": oct(stat.S_IMODE(info.st_mode)),
                              "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    target = SNAPSHOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    assert target.read_bytes() == data
for relative, record in records.items():
    assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == record["sha256"], relative
    (SNAPSHOT / relative).chmod(0o444)
manifest = {"schema": "n06-rust-source-manifest-v1", "root": str(ROOT), "snapshot": str(SNAPSHOT),
            "base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
            "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT).decode().strip(),
            "files": records, "qualification": "not-executed"}
encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
(DEST / "source-manifest.json").write_bytes(encoded)
(DEST / ".gitignore").write_text("/snapshot/\n/target/\n")
(DEST / "source-manifest.sha256").write_text(hashlib.sha256(encoded).hexdigest() + "  source-manifest.json\n")
print(json.dumps({"directory": str(DEST), "files": len(records), "manifest_sha256": hashlib.sha256(encoded).hexdigest()}))
