#!/usr/bin/env python3
"""Build an isolated consumer of the packaged crate, including fresh-process replay."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--allow-dirty", action="store_true")
parser.add_argument("--registry", action="store_true", help="download the exact manifest version from crates.io instead")
parser.add_argument("--target-dir", type=Path, default=root / "target", help="package build directory")
args = parser.parse_args()
target = args.target_dir.resolve()
command = ["cargo", "package", "--locked", "--offline"]
if args.allow_dirty:
    command.append("--allow-dirty")
if not args.registry:
    subprocess.run(command, cwd=root, env={**os.environ, "CARGO_TARGET_DIR": str(target)}, check=True)
package = tomllib.loads((root / "Cargo.toml").read_text())["package"]
source = target / "package" / f"{package['name']}-{package['version']}"
dependency = ('version=' + json.dumps('=' + package['version'])) if args.registry else ('path=' + json.dumps(str(source)))
with tempfile.TemporaryDirectory(prefix="stateless-consumer-") as temporary:
    consumer = Path(temporary)
    environment = {**os.environ, "CARGO_TARGET_DIR": str(consumer / "target")}
    (consumer / "src").mkdir()
    (consumer / "Cargo.toml").write_text(
        '[package]\nname="stateless-consumer"\nversion="0.0.0"\nedition="2024"\n'
        '[dependencies]\nstateless={package="statelessness",' + dependency + '}\n'
    )
    example_source = root if args.registry else source
    (consumer / "src/main.rs").write_text((example_source / "examples/application.rs").read_text().replace(
        'include_bytes!("application.rs")', 'include_bytes!("main.rs")'))
    if args.registry:
        subprocess.run(["cargo", "fetch"], cwd=consumer, env=environment, check=True)
    subprocess.run(["cargo", "test", "--offline"], cwd=consumer, env=environment, check=True)
    subprocess.run(["cargo", "build", "--offline"], cwd=consumer, env=environment, check=True)
    binary = consumer / "target/debug" / ("stateless-consumer.exe" if os.name == "nt" else "stateless-consumer")
    def run(arguments, code, expected=""):
        result = subprocess.run([str(binary), *arguments], cwd=consumer, capture_output=True, text=True)
        assert result.returncode == code, result
        assert expected in result.stdout + result.stderr, result
        print(arguments[0], result.returncode, result.stdout.strip())
    run(["check"], 0, "Graph exhausted")
    run(["find", "failure"], 1)
    for name in ["original", "minimized"]:
        run(["replay", f"failure/{name}.sttrace"], 0, "failure reproduced: true")
    run(["replay-fixed", "failure/minimized.sttrace"], 3, "Diverged")
    original = (consumer / "failure/minimized.sttrace").read_bytes()
    run(["find", "failure"], 2)
    assert (consumer / "failure/minimized.sttrace").read_bytes() == original
    (consumer / "truncated.sttrace").write_bytes(original[:-1])
    run(["replay", "truncated.sttrace"], 2, "truncated")
print("Registry consumer verified" if args.registry else "Packaged consumer verified")
