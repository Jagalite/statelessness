"""Build immutable native packages and external consumers; no Cargo subprocesses."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tarfile

from evidence import (Recorder, file_hash, readonly, require_equal, strict_json,
                      tree, writable_copy, write_json)


def tool(name):
    result = shutil.which(name)
    if result is None:
        raise RuntimeError(f"required tool missing: {name}; no engine may be skipped")
    return str(Path(result).absolute())


def one(paths, context):
    values = list(paths)
    if len(values) != 1:
        raise RuntimeError(f"expected one {context}, found {len(values)}")
    return values[0]


def archive(source, target):
    # A source distribution for path-based Go/Swift packages; the hash binds the
    # actual bytes, while its tree manifest provides unpacked file-level identity.
    with tarfile.open(target, "x:gz", format=tarfile.PAX_FORMAT) as output:
        output.add(source, arcname=source.name, recursive=True)


def build(session):
    work, source = session.work, session.snapshot
    rust_install = json.loads((session.out / "install-rust.json").read_bytes())
    if rust_install["status"] != "complete-unsealed":
        raise RuntimeError("install-rust must finish before native package staging")
    consumers = source / "scripts/native-guided-consumers"
    packages = work / "packages"
    recorder = Recorder(session.out, work, "build-native", env=session.environment())
    tools = {name: tool(name) for name in ("python3", "go", "node", "npm", "swift")}
    tools["rustc"] = session.data["rust_toolchain"]["executables"]["rustc"]
    versions = {}
    for name, args in (("python3", ["--version"]), ("go", ["version"]),
                       ("node", ["--version"]), ("npm", ["--version"]),
                       ("swift", ["--version"]), ("rustc", ["-Vv"])):
        versions[name] = recorder.text(name + "-version", [tools[name], *args])
    rust_sysroot = recorder.text("rustc-sysroot", [tools["rustc"], "--sysroot",
                                session.data["rust_toolchain"]["root"], "--print", "sysroot"])
    require_equal(rust_sysroot, session.data["rust_toolchain"]["root"], "actual configured Rust sysroot")
    tool_manifest = session.objects.put({name: {"path": path, "realpath": str(Path(path).resolve()),
                                               "sha256": file_hash(path), "version": versions[name]}
                                         for name, path in tools.items()})

    # A wheel builder and the installed runtime use different fresh venvs.
    python_source = work / "python-build"
    writable_copy(source / "python", python_source)
    python_inputs = session.objects.put(tree(python_source, source=True))
    recorder.run("python-builder-venv", [tools["python3"], "-m", "venv", work / "builder-venv"])
    builder = work / "builder-venv/bin/python"
    recorder.run("python-builder-tools", [builder, "-I", "-m", "pip", "install", "setuptools>=77", "wheel"])
    recorder.run("python-builder-freeze", [builder, "-I", "-m", "pip", "freeze", "--all"])
    recorder.run("python-wheel", [builder, "-I", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                                  "--wheel-dir", packages, python_source])
    require_equal(session.objects.put(tree(python_source, source=True)), python_inputs, "Python wheel build inputs")
    wheel = one(packages.glob("*.whl"), "Python wheel")
    recorder.run("python-runtime-venv", [tools["python3"], "-m", "venv", work / "venv"])
    py = work / "venv/bin/python"
    recorder.run("python-wheel-install", [py, "-I", "-m", "pip", "install", "--no-index", "--no-deps", wheel])
    recorder.run("python-installed-freeze", [py, "-I", "-m", "pip", "freeze", "--all"])
    writable_copy(consumers / "python", work / "python-consumer")
    probe = """import importlib.metadata as m, json, pathlib, statelessness, sys
d=m.distribution('statelessness')
print(json.dumps({'file':statelessness.__file__,'prefix':sys.prefix,'version':d.version,
 'direct_url':json.loads(d.read_text('direct_url.json'))}))
"""
    python_provenance = strict_json(recorder.run("python-import-provenance", [py, "-I", "-B", "-c", probe]))
    if not Path(python_provenance["file"]).resolve().is_relative_to(work / "venv") or Path(python_provenance["prefix"]).resolve() != work / "venv":
        raise RuntimeError("Python did not import the fresh installed wheel")
    direct = python_provenance["direct_url"]
    if direct["url"] != wheel.as_uri() or direct.get("archive_info", {}).get("hashes", {}).get("sha256") != file_hash(wheel):
        raise RuntimeError("Python installed distribution is not bound to staged wheel bytes")
    runners = {"python": [str(py), "-I", "-B", str(work / "python-consumer/consumer.py")]}
    portable = {"python": [str(py), "-I", "-B", "-m", "statelessness.conformance", "--runner"]}

    # Compile the staged package with its declared compiler; no live-checkout tsc.
    ts_source = work / "typescript-build"
    writable_copy(source / "typescript", ts_source)
    npm = tools["npm"]
    install = "ci" if (ts_source / "package-lock.json").exists() else "install"
    recorder.run("typescript-build-tools", [npm, install, "--ignore-scripts", "--no-audit", "--no-fund"], ts_source)
    typescript_inputs = session.objects.put(tree(ts_source, source=True))
    compiler = [tools["node"], str(ts_source / "node_modules/typescript/bin/tsc")]
    recorder.run("typescript-compiler-version", [*compiler, "--version"])
    recorder.run("typescript-package-build", [*compiler, "-p", "tsconfig.json"], ts_source)
    recorder.run("typescript-pack", [npm, "pack", "--json", "--ignore-scripts", "--pack-destination", packages], ts_source)
    require_equal(session.objects.put(tree(ts_source, source=True)), typescript_inputs, "TypeScript package build inputs")
    tarball = one(packages.glob("*.tgz"), "npm package")
    ts_consumer = work / "typescript-consumer"
    writable_copy(consumers / "typescript", ts_consumer)
    # Authored package/config, if any, remain frozen in the snapshot. These are
    # explicit harness-owned build manifests in a separate external project.
    (ts_consumer / "package.json").write_text(json.dumps({"name": "n05-installed-consumer", "private": True, "type": "module"}))
    recorder.run("typescript-install", [npm, "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund",
                                        "--omit=dev", tarball], ts_consumer)
    if (ts_consumer / "consumer.ts").is_file():
        (ts_consumer / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
            "strict": True, "target": "ES2022", "module": "NodeNext", "moduleResolution": "NodeNext",
            "outDir": "build", "lib": ["ES2022", "DOM"]}, "files": ["consumer.ts"]}))
        recorder.run("typescript-consumer-declarations", [*compiler, "-p", "tsconfig.json"], ts_consumer)
        ts_entry = ts_consumer / "build/consumer.js"
    else:
        ts_entry = ts_consumer / "run.mjs"
        if not ts_entry.is_file():
            raise RuntimeError("TypeScript consumer must provide consumer.ts or run.mjs")
    provenance_script = "console.log(JSON.stringify({resolved:import.meta.resolve('@jagalite/statelessness')}))"
    ts_provenance = strict_json(recorder.run("typescript-import-provenance",
                                           [tools["node"], "--input-type=module", "-e", provenance_script], ts_consumer))
    installed_ts = ts_consumer / "node_modules/@jagalite/statelessness"
    if ts_provenance["resolved"] != (installed_ts / "dist/index.js").as_uri():
        raise RuntimeError("TypeScript did not resolve the installed external npm package")
    lock = strict_json((ts_consumer / "package-lock.json").read_bytes())
    package_entry = lock["packages"]["node_modules/@jagalite/statelessness"]
    if "integrity" not in package_entry or not package_entry.get("resolved", "").endswith(tarball.name):
        raise RuntimeError("npm package-lock does not bind the staged tarball")
    recorder.run("typescript-installed-tree", [npm, "ls", "--json", "--all"], ts_consumer)
    runners["typescript"] = [tools["node"], str(ts_entry)]
    portable["typescript"] = [tools["node"], str(installed_ts / "bin/corpus.mjs")]

    # Go imports the immutable, independently archived package from an external
    # module. Baseline runner is also compiled from that package's public API.
    shutil.copytree(source / "go", work / "go-package")
    readonly(work / "go-package")
    go_inputs = session.objects.put(tree(work / "go-package"))
    archive(work / "go-package", packages / "go-source.tar.gz")
    go_consumer = work / "go-consumer"
    writable_copy(consumers / "go", go_consumer)
    (go_consumer / "go.mod").write_text("module n05-installed-consumer\n\ngo 1.23.0\n\n"
        "require github.com/Jagalite/statelessness/go v0.0.0\n"
        "replace github.com/Jagalite/statelessness/go => ../go-package\n")
    recorder.run("go-module-provenance", [tools["go"], "list", "-m", "-json", "all"], go_consumer)
    actual_go = strict_json(recorder.run("go-import-provenance", [tools["go"], "list", "-json", "github.com/Jagalite/statelessness/go"], go_consumer))
    if Path(actual_go["Dir"]).resolve() != work / "go-package" or Path(actual_go["Module"]["Replace"]["Dir"]).resolve() != work / "go-package":
        raise RuntimeError("Go public import does not resolve the frozen external module")
    recorder.run("go-consumer-build", [tools["go"], "build", "-trimpath", "-o", work / "go-guided", "."], go_consumer)
    recorder.run("go-binary-provenance", [tools["go"], "version", "-m", work / "go-guided"])
    baseline_go = work / "go-portable"
    baseline_go.mkdir()
    shutil.copyfile(go_consumer / "go.mod", baseline_go / "go.mod")
    shutil.copyfile(source / "go/cmd/stateless-corpus/main.go", baseline_go / "main.go")
    recorder.run("go-portable-build", [tools["go"], "build", "-trimpath", "-o", work / "go-corpus", "."], baseline_go)
    runners["go"] = [str(work / "go-guided")]
    portable["go"] = [str(work / "go-corpus")]

    swift_source = work / "swift-package"
    swift_source.mkdir()
    shutil.copyfile(source / "Package.swift", swift_source / "Package.swift")
    shutil.copytree(source / "swift", swift_source / "swift")
    readonly(swift_source)
    swift_inputs = session.objects.put(tree(swift_source))
    archive(swift_source, packages / "swift-source.tar.gz")
    swift_consumer = work / "swift-consumer"
    writable_copy(consumers / "swift", swift_consumer)
    swift_target = one((path for path in (swift_consumer / "Sources").iterdir() if path.is_dir()), "Swift executable source target")
    # External package identity is deliberately explicit, independent of an
    # authored relative path that points to the developer's checkout.
    (swift_consumer / "Package.swift").write_text('''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "GuidedConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness", path: "../swift-package")],
 targets: [.executableTarget(name: "GuidedConsumer", dependencies: [
  .product(name: "Statelessness", package: "Statelessness")], path: SOURCE_PATH)])
'''.replace("SOURCE_PATH", json.dumps("Sources/" + swift_target.name)))
    swift_dependencies = strict_json(recorder.run("swift-resolved-provenance", [tools["swift"], "package", "--package-path", swift_consumer,
                                                                              "show-dependencies", "--format", "json"]))
    dependencies = swift_dependencies.get("dependencies", [])
    if len(dependencies) != 1 or Path(dependencies[0]["path"]).resolve() != swift_source:
        raise RuntimeError("SwiftPM external dependency does not resolve the frozen package")
    recorder.run("swift-consumer-build", [tools["swift"], "build", "--package-path", swift_consumer,
                                          "--product", "GuidedConsumer", "-j", "2"])
    binary_dir = Path(recorder.text("swift-consumer-bin-path", [tools["swift"], "build", "--package-path", swift_consumer, "--show-bin-path"]))
    runners["swift"] = [str(binary_dir / "GuidedConsumer")]
    swift_baseline = work / "swift-portable"
    swift_baseline.mkdir()
    (swift_baseline / "Sources/PortableConsumer").mkdir(parents=True)
    for path in (source / "swift/Sources/StatelessCorpus").iterdir():
        if path.is_file():
            shutil.copyfile(path, swift_baseline / "Sources/PortableConsumer" / path.name)
    (swift_baseline / "Package.swift").write_text('''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "PortableConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness", path: "../swift-package")],
 targets: [.executableTarget(name: "PortableConsumer", dependencies: [
  .product(name: "StatelessConformance", package: "Statelessness")])])
''')
    recorder.run("swift-portable-build", [tools["swift"], "build", "--package-path", swift_baseline, "--product", "PortableConsumer", "-j", "2"])
    baseline_dir = Path(recorder.text("swift-portable-bin-path", [tools["swift"], "build", "--package-path", swift_baseline, "--show-bin-path"]))
    portable["swift"] = [str(baseline_dir / "PortableConsumer")]
    require_equal(session.objects.put(tree(work / "go-package")), go_inputs, "Go immutable package inputs")
    require_equal(session.objects.put(tree(swift_source)), swift_inputs, "Swift immutable package inputs")
    readonly(packages)
    for path in (work / "venv", work / "python-consumer", ts_consumer, go_consumer, baseline_go):
        readonly(path)
    for path in (work / "go-guided", work / "go-corpus", Path(runners["swift"][0]), Path(portable["swift"][0])):
        path.chmod(path.stat().st_mode & 0o555)
    # Runtime identity includes installed code, generated declarations, consumers,
    # dependency manifests and native binaries, not mutable compiler caches.
    runtime_paths = ["packages", "venv", "python-consumer", "typescript-consumer", "go-package",
                     "go-consumer", "go-portable", "go-guided", "go-corpus", "swift-package"]
    for binary in (Path(runners["swift"][0]), Path(portable["swift"][0])):
        runtime_paths.append(str(binary.relative_to(work)))
    # Keep authored Swift inputs/dependency/build descriptions bound without a
    # giant per-object manifest of its disposable incremental compilation cache.
    for directory in (swift_consumer, swift_baseline):
        for rel, row in tree(directory, source=True)["entries"].items():
            if row[0] == "file":
                runtime_paths.append(str((directory / rel).relative_to(work)))
    # venv symlinks are recorded without dereference; executable target hashes
    # below additionally bind the host interpreter behind them.
    runtime_manifest = session.objects.put(tree(work, runtime_paths))
    return {"runners": runners, "portable_runners": portable,
            "rust_sysroot": rust_sysroot,
            "runtime_paths": runtime_paths, "runtime_manifest": runtime_manifest,
            "tool_manifest": tool_manifest,
            "build_input_manifests": {"python": python_inputs, "typescript": typescript_inputs,
                                      "go": go_inputs, "swift": swift_inputs},
            "compiler_manifest": session.objects.put(tree(ts_source / "node_modules/typescript")),
            "compiler_path": str(ts_source / "node_modules/typescript"),
            "python_builder_manifest": session.objects.put(tree(work / "builder-venv")),
            "commands_manifest": session.objects.put(tree(session.out / "commands/build-native")),
            "provenance": {"python": python_provenance, "typescript": ts_provenance,
                           "go": actual_go, "swift": swift_dependencies},
            "executables": {name: {"path": command[0], "realpath": str(Path(command[0]).resolve()),
                                    "sha256": file_hash(command[0])} for name, command in runners.items()},
            "counts": {"native_engines_built": 4, "native_portable_runners_built": 4}}


def verify_installed(session, built):
    require_equal(session.objects.put(tree(session.work, built["runtime_paths"])),
                  built["runtime_manifest"], "installed package/consumer membership/type/mode/hash")
    require_equal(session.objects.put(tree(Path(built["compiler_path"]))),
                  built["compiler_manifest"], "TypeScript compiler package")
    require_equal(session.objects.put(tree(session.work / "builder-venv")),
                  built["python_builder_manifest"], "Python wheel build tooling")
    for value in session.objects.get(built["tool_manifest"]).values():
        if file_hash(value["path"]) != value["sha256"] or str(Path(value["path"]).resolve()) != value["realpath"]:
            raise RuntimeError(f"toolchain executable drift: {value['path']}")
    for value in built["executables"].values():
        if file_hash(value["path"]) != value["sha256"] or str(Path(value["path"]).resolve()) != value["realpath"]:
            raise RuntimeError(f"installed executable drift: {value['path']}")
