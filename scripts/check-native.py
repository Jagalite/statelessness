#!/usr/bin/env python3
"""Compile against the native distribution, relocate the result, and run its ABI smoke."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
native = root / "target/bindings/native"
system = platform.system()
if system not in ["Darwin", "Linux"]:
    raise SystemExit("native relocation check currently supports macOS/Linux")
library = "libstateless.dylib" if system == "Darwin" else "libstateless.so"
rpath = "@loader_path/lib" if system == "Darwin" else "$ORIGIN/lib"
with tempfile.TemporaryDirectory(prefix="stateless-native-") as temporary:
    stage = Path(temporary) / "stage"
    (stage / "lib").mkdir(parents=True)
    shutil.copy2(native / "lib" / library, stage / "lib" / library)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(native / "include"),
                    str(root / "bindings/c/smoke.c"), "-L", str(stage / "lib"), "-lstateless",
                    "-Wl,-rpath," + rpath, "-o", str(stage / "smoke")], check=True)
    if system == "Darwin":
        links = subprocess.check_output(["otool", "-L", str(stage / "smoke")], text=True)
        assert "@rpath/libstateless.dylib" in links, links
        assert str(root) not in links, links
    relocated = Path(temporary) / "relocated"
    stage.rename(relocated)
    subprocess.run([str(relocated / "smoke")], check=True)
print("Native distribution relocation passed")
