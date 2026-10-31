"""Phased N05 installed qualification. Cargo runs only as direct Kyoto jobs."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

from evidence import (Objects, Recorder, canonical, clean_environment, copy_manifest, digest,
                      file_hash, readonly, require_equal, strict_json, tree,
                      writable_copy, write_json)

AUTHORITY_COMMIT = "a4cc11eea8db62115b452eee3c6610c04fa06b96"
AUTHORITY = {
    "src/guided.rs": "bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7",
    "src/explore.rs": "496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb",
    "src/model.rs": "e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb",
}
ENGINES = ("rust", "python", "typescript", "go", "swift")
ROOT = Path(__file__).resolve().parents[2]
KYOTO = Path("/Volumes/seed1/Projects/kyoto/kyoto.py")
SELECTIONS = (
    "Cargo.toml", "Cargo.lock", "build.rs", "Package.swift", "LICENSE", "README.md",
    "PLAN.md", "VALIDATION.md", "RELEASING.md", "SUPPORT.md", "CHANGELOG.md",
    "src", "crates", "spec", "python/src", "python/pyproject.toml", "python/README.md", "python/LICENSE",
    "typescript/src", "typescript/bin", "typescript/examples", "typescript/tsconfig.json",
    "typescript/package.json", "typescript/package-lock.json", "typescript/README.md", "typescript/LICENSE",
    "go", "swift/Sources", "swift/Tests", "swift/Examples", "swift/README.md", "swift/LICENSE",
    "validation/corpus", "validation/adoption-2026-10-07.json",
    "conformance/corpus.json", "conformance/run.py", "conformance/portability.py", "conformance/guided-v2",
    "conformance/paired-v1.tsv", "conformance/test_paired_profile.py", "conformance/search-v1.tsv", "conformance/search-v1-history.json",
    "conformance/native-binary-v1.json", "conformance/bfs-controls-v1.json",
    "scripts/native-guided-consumers", "scripts/native-guided-support",
    "scripts/check-native-guided-consumers.py", "scripts/native-guided-qualification.py",
    "docs/requirements", "docs/NATIVE-PARITY-PLAN.md", "docs/native-parity/GUIDED-CAMPAIGN-AUDIT.md",
    "docs/native-parity/GUIDED-CAMPAIGN-REVIEW.md", "docs/native-parity/N05-SOURCE-CONTRACT.md",
    "docs/native-parity/N05-QUALIFICATION-HARNESS.md",
    "tests", "examples", "benches", "bindings",
)
RECEIPT_ROOTS = ("python/receipts", "typescript/receipts", "go/receipts", "swift/validation/n05",
                 "conformance/guided-v2/receipts", *(f"scripts/native-guided-consumers/{engine}/receipts" for engine in ENGINES))
FROZEN_SPEC_FILES = {"conformance/guided-v2/corpus.json", "conformance/guided-v2/oracle.py",
                     "conformance/guided-v2/README.md", "docs/native-parity/N05-SOURCE-CONTRACT.md"}


def capture_evidence(source, objects):
    # References only: never stage source-adjacent evidence as package inputs.
    # Swift's old snapshots can contain complete packages/.build trees; their
    # already authored compact manifests remain included, not those payloads.
    return {rel: objects.put(tree(source / rel, exclude_dirs={"snapshots", ".build", "target", "node_modules", "__pycache__"}))
            for rel in RECEIPT_ROOTS if (source / rel).is_dir()}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def metadata_command(argv, cwd):
    # Only VCS identity queries: prepare does not execute a product or compiler.
    return subprocess.check_output(argv, cwd=cwd).decode("utf-8", "strict").strip()


def validate_corpus(source, expected_sha256, freeze_sha256):
    directory = source / "conformance/guided-v2"
    if file_hash(directory / "corpus.json") != expected_sha256:
        raise RuntimeError("independent expected corpus revision mismatch")
    if file_hash(directory / "FREEZE.json") != freeze_sha256:
        raise RuntimeError("independent FREEZE revision mismatch")
    frozen = strict_json((directory / "FREEZE.json").read_bytes())
    if not isinstance(frozen, dict) or set(frozen) != {
            "schema", "revision", "source_commit", "derivation", "candidate_output_used", "cases", "files"}:
        raise RuntimeError("unsupported independent FREEZE schema; use the author's exact revision-3 seal")
    if frozen["schema"] != "stateless-guided-v2-freeze/1" or type(frozen["revision"]) is not int or frozen["revision"] != 3:
        raise RuntimeError("independent FREEZE must be specification revision 3")
    if frozen["source_commit"] != AUTHORITY_COMMIT or frozen["candidate_output_used"] is not False:
        raise RuntimeError("FREEZE must bind independent derivation and the Rust authority")
    if not isinstance(frozen["derivation"], str) or not frozen["derivation"]:
        raise RuntimeError("FREEZE lacks its independent derivation description")
    files = frozen["files"]
    if not isinstance(files, dict) or set(files) != FROZEN_SPEC_FILES:
        raise RuntimeError("FREEZE must pin corpus, oracle, README and source contract in its files map")
    if files["conformance/guided-v2/corpus.json"] != expected_sha256:
        raise RuntimeError("FREEZE files map does not bind this expected corpus")
    for rel, wanted in files.items():
        if not isinstance(wanted, str) or not re.fullmatch(r"[0-9a-f]{64}", wanted) or file_hash(source / rel) != wanted:
            raise RuntimeError(f"independently sealed input changed: {rel}")
    corpus = strict_json((directory / "corpus.json").read_bytes())
    if corpus.get("schema") != "stateless-guided-v2/1" or corpus.get("source_commit") != AUTHORITY_COMMIT:
        raise RuntimeError("unsupported corpus or wrong source authority commit")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("missing shared corpus cases")
    if type(frozen["cases"]) is not int or frozen["cases"] != len(cases):
        raise RuntimeError("independent FREEZE case count does not match corpus")
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids) or not all(isinstance(x, str) and x for x in ids):
        raise RuntimeError("duplicate or invalid corpus case IDs")
    keys = {"report", "error", "trace", "generator_rng", "witness_replays", "shrink"}
    for case in cases:
        expected = case["expected"]
        if not keys <= expected.keys() or expected.keys() - keys - {"rng_audit"}:
            raise RuntimeError(f"unexpected expected schema for {case['id']}")
        if (expected["report"] is None) == (expected["error"] is None):
            raise RuntimeError(f"report/error exclusivity violated for {case['id']}")
    return corpus


def authority(source):
    for rel, wanted in AUTHORITY.items():
        if file_hash(source / rel) != wanted:
            raise RuntimeError(f"source authority hash mismatch: {rel}")


def rust_toolchain(root):
    """Bind a real installation, not rustup's mutable selected-toolchain proxy."""
    if not root.is_absolute() or root != root.resolve():
        raise RuntimeError("Rust toolchain root must be an absolute resolved installation directory")
    if not (root / "lib/rustlib").is_dir():
        raise RuntimeError("Rust toolchain root lacks lib/rustlib; do not supply ~/.cargo/bin")
    executables = {}
    for name in ("cargo", "rustc", "rustdoc"):
        path = root / "bin" / name
        if not path.is_file() or path != path.resolve() or not os.access(path, os.X_OK):
            raise RuntimeError(f"Rust toolchain requires an actual executable: {path}")
        executables[name] = str(path)
    manifest = tree(root, ("bin/cargo", "bin/rustc", "bin/rustdoc", "lib"))
    # File links resolve through their entire chain. Directory links are not
    # admitted: a shallow scan would miss their nested external referents.
    referents = {}
    for rel, entry in manifest["entries"].items():
        if entry[0] != "symlink":
            continue
        target = (root / rel).resolve(strict=True)  # Broken links/cycles fail.
        if not target.is_file():
            raise RuntimeError(f"toolchain directory/non-file symlink is unsupported: {root / rel}")
        referents[rel] = {"realpath": str(target), "tree": tree(target)}
    manifest["symlink_referents"] = referents
    return {"root": str(root), "executables": executables, "manifest": manifest}


def cargo_configuration(work, environment):
    """Freeze absence of all effective Cargo config files, retaining shared cache.

    Both legacy and TOML names are checked for each planned Cargo cwd and every
    ancestor, plus the explicit CARGO_HOME. Unsupported ambient configuration is
    rejected, never silently ignored or modified. Thus includes and target flags
    cannot select unrecorded tools, libraries, sources or wrappers.
    """
    roots = {Path(environment["CARGO_HOME"])}
    for name in (".", "source", "rust-consumer", "rust-portable"):
        directory = (work / name).resolve()
        roots.update(parent / ".cargo" for parent in (directory, *directory.parents))
    paths = sorted(root / name for root in roots for name in ("config", "config.toml"))
    for path in paths:
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"effective Cargo configuration is not admitted by this harness: {path}")
    return {"schema": "n05-cargo-configuration/1", "files": {str(path): "absent" for path in paths}}


def rewrite_rust_manifest(path, package):
    raw = path.read_text()
    parsed = tomllib.loads(raw)
    dependencies = parsed.get("dependencies", {})
    names = [name for name, value in dependencies.items() if isinstance(value, dict)
             and value.get("package", name) == "statelessness"]
    if len(names) != 1:
        raise RuntimeError(f"expected one public statelessness dependency in {path}")
    name = names[0]
    # Existing public consumers use one inline path dependency. Do not silently
    # patch arbitrary TOML or inject a second dependency of the same package.
    pattern = rf'(?m)^({re.escape(name)}\s*=\s*\{{[^\n]*?\bpath\s*=\s*)"[^"]*"'
    changed, count = re.subn(pattern, lambda match: match[1] + json.dumps(str(package)), raw)
    if count != 1:
        raise RuntimeError(f"cannot identify public path dependency in {path}")
    tomllib.loads(changed)
    path.write_text(changed)
    return parsed["package"]["name"]


def prepare(args):
    source = args.source.resolve()
    out = args.output.resolve()
    if out.exists():
        raise RuntimeError("output must not exist; preserve all earlier attempts")
    if out.is_relative_to(source):
        raise RuntimeError("receipt directory must be outside the source checkout")
    out.mkdir(parents=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix="stateless-n05-")).resolve()
    objects = Objects(out / "objects")
    record = {"schema": "n05-installed-session/1", "created": now(), "source": str(source),
              "workspace": str(work), "expected_head": args.expect_head,
              "host": {"uname": list(os.uname()), "python_version": sys.version,
                       "python_executable": sys.executable, "cpu_count": os.cpu_count()},
              "authority_commit": AUTHORITY_COMMIT, "authority_sha256": AUTHORITY,
              "expected_sha256": args.expected_sha256, "freeze_sha256": args.freeze_sha256,
              "engines": list(ENGINES), "source_selections": list(SELECTIONS),
              "source_exclusions": "generated directories only; see evidence.GENERATED",
              "scope": "bare N05 installed candidate; no N06, N10 or profile promotion"}
    try:
        if metadata_command(["git", "rev-parse", "HEAD"], source) != args.expect_head:
            raise RuntimeError("source HEAD differs from the explicit freeze HEAD")
        record["git_status"] = metadata_command(["git", "status", "--porcelain=v1", "--untracked-files=normal"], source)
        authority(source)
        toolchain = rust_toolchain(args.rust_toolchain_root)
        record["rust_toolchain"] = {"root": toolchain["root"], "executables": toolchain["executables"],
                                    "manifest": objects.put(toolchain["manifest"])}
        cargo = toolchain["executables"]["cargo"]
        environment = clean_environment(work)
        environment.update(RUSTC=toolchain["executables"]["rustc"],
                           RUSTDOC=toolchain["executables"]["rustdoc"], CARGO=cargo,
                           CARGO_HOME=str((Path(environment["HOME"]) / ".cargo").resolve()),
                           CARGO_ENCODED_RUSTFLAGS="--sysroot\x1f" + toolchain["root"],
                           CARGO_ENCODED_RUSTDOCFLAGS="--sysroot\x1f" + toolchain["root"],
                           RUSTC_WRAPPER="", RUSTC_WORKSPACE_WRAPPER="",
                           PATH=str(args.rust_toolchain_root / "bin") + os.pathsep + environment.get("PATH", ""))
        record["execution_environment"] = environment
        record["cargo_configuration"] = objects.put(cargo_configuration(work, environment))
        corpus = validate_corpus(source, args.expected_sha256, args.freeze_sha256)
        record["cases"] = [case["id"] for case in corpus["cases"]]
        write_json(out / "source-evidence-before.json", {"root": str(source), "references": capture_evidence(source, objects)})
        before = tree(source, SELECTIONS, source=True)
        record["source_manifest"] = objects.put(before)
        snapshot = work / "source"
        copy_manifest(source, snapshot, before)
        require_equal(tree(snapshot, SELECTIONS, source=True), before, "copied source")
        require_equal(tree(source, SELECTIONS, source=True), before, "source during preparation")
        readonly(snapshot)
        validate_corpus(source, args.expected_sha256, args.freeze_sha256)
        validate_corpus(snapshot, args.expected_sha256, args.freeze_sha256)
        record["snapshot_manifest"] = objects.put(tree(snapshot, SELECTIONS, source=True))
        for language in ENGINES:
            if not (snapshot / "scripts/native-guided-consumers" / language).is_dir():
                raise RuntimeError(f"missing {language} external public consumer")
        record["packages"] = str(work / "packages")
        (work / "packages").mkdir()
        (work / "empty.npmrc").write_bytes(b"")
        # Consumers import the extracted .crate, never the live checkout or
        # qualification source tree. install-rust creates it after direct Cargo
        # packaging. Only external manifests/locks and targets are writable.
        writable_copy(snapshot / "scripts/native-guided-consumers/rust", work / "rust-consumer")
        guided_binary = rewrite_rust_manifest(work / "rust-consumer/Cargo.toml", work / "rust-package")
        writable_copy(snapshot / "validation/corpus", work / "rust-portable")
        rewrite_rust_manifest(work / "rust-portable/Cargo.toml", work / "rust-package")
        # Freeze the staged authored Rust inputs independently of generated locks.
        record["rust_authored_inputs"] = objects.put(rust_inputs(work))
        require_equal(objects.put(cargo_configuration(work, environment)), record["cargo_configuration"],
                      "Cargo configuration during preparation")
        plans = {}
        package_command = [cargo, "package", "--offline", "--locked", "--allow-dirty", "--no-verify",
                           "--manifest-path", str(snapshot / "Cargo.toml"), "--target-dir", str(work / "targets/package"),
                           "-p", "statelessness"]
        plans["rust-package"] = {"argv": package_command, "cwd": str(snapshot),
                                 "submit_argv": ["python3", str(KYOTO), "submit", "--cwd", str(snapshot), "--", *package_command]}
        package_metadata = tomllib.loads((snapshot / "Cargo.toml").read_text())["package"]
        record["rust_crate"] = str(work / "targets/package/package" / f"statelessness-{package_metadata['version']}.crate")
        for kind, project in (("guided", "rust-consumer"), ("portable", "rust-portable")):
            manifest = work / project / "Cargo.toml"
            target = work / "targets" / kind
            for action, argv in (
                ("lock", [cargo, "generate-lockfile", "--offline", "--manifest-path", str(manifest)]),
                ("metadata", [cargo, "metadata", "--offline", "--locked", "--format-version", "1", "--manifest-path", str(manifest)]),
                ("build", [cargo, "build", "--offline", "--locked", "--manifest-path", str(manifest),
                           "--target-dir", str(target), "--bin", guided_binary if kind == "guided" else "stateless-corpus"]),
            ):
                name = f"rust-{kind}-{action}"
                plans[name] = {"argv": argv, "cwd": str(work / project),
                               "submit_argv": ["python3", str(KYOTO), "submit", "--cwd", str(work / project), "--", *argv]}
        plans["cargo-version"] = {"argv": [cargo, "--version"], "cwd": str(work),
                                  "submit_argv": ["python3", str(KYOTO), "submit", "--cwd", str(work), "--", cargo, "--version"]}
        record["rust_binaries"] = {"guided": str(work / "targets/guided/debug" / guided_binary),
                                   "portable": str(work / "targets/portable/debug/stateless-corpus")}
        for phase in ("build-native", "run"):
            argv = [sys.executable, "-I", str(snapshot / "scripts/native-guided-qualification.py"),
                    phase, "--session", str(out)]
            plans[phase] = {"argv": argv, "cwd": str(work),
                            "submit_argv": ["python3", str(KYOTO), "submit", "--cwd", str(work), "--", *argv]}
        for plan in plans.values():
            plan["environment"] = environment
        record["job_plan"] = objects.put(plans)
        if args.supersedes:
            old = args.supersedes.resolve()
            record["supersedes"] = {"path": str(old), "manifest": objects.put(tree(old))}
        write_json(out / "session.json", record)
        write_json(out / "job-plan.json", plans)
        write_json(out / "prepare.json", {"status": "prepared", "time": now(),
                                          "session_sha256": file_hash(out / "session.json")})
        print(json.dumps({"status": "prepared", "session": str(out), "workspace": str(work),
                          "plan": str(out / "job-plan.json"), "cases": len(record["cases"])}))
    except BaseException as exc:
        write_json(out / "prepare-failed.json", {"status": "failed", "error": repr(exc), "partial": record})
        raise


def rust_inputs(work):
    result = {}
    for name in ("rust-consumer", "rust-portable"):
        manifest = tree(work / name, source=True)
        # Lock creation is a separately recorded direct Cargo phase.
        manifest["entries"].pop("Cargo.lock", None)
        result[name] = manifest
    return result


class Session:
    def __init__(self, out):
        self.out = Path(out).resolve()
        self.data = json.loads((self.out / "session.json").read_bytes())
        prepared = json.loads((self.out / "prepare.json").read_bytes())
        if file_hash(self.out / "session.json") != prepared["session_sha256"]:
            raise RuntimeError("session metadata changed after preparation")
        self.work = Path(self.data["workspace"])
        self.snapshot = self.work / "source"
        self.objects = Objects(self.out / "objects")
        self.plans = self.objects.get(self.data["job_plan"])

    def environment(self):
        return dict(self.data["execution_environment"])

    def rust_lock(self, kind):
        project = "rust-consumer" if kind == "guided" else "rust-portable"
        path = self.work / project / "Cargo.lock"
        return {"lock_sha256": file_hash(path),
                "lock_manifest": self.objects.put(tree(self.work / project, ("Cargo.lock",)))}

    def verify_rust_lock(self, kind):
        locked = self.successful_job(f"rust-{kind}-lock")
        current = self.rust_lock(kind)
        require_equal(current, {key: locked[key] for key in current}, f"{kind} Rust lock since generation")
        return current

    def verify(self, phase):
        checkpoints = self.out / "checkpoints"
        checkpoints.mkdir(exist_ok=True)
        source = Path(self.data["source"])
        actual = {
            "source": self.objects.put(tree(source, SELECTIONS, source=True)),
            "snapshot": self.objects.put(tree(self.snapshot, SELECTIONS, source=True)),
            "rust_authored_inputs": self.objects.put(rust_inputs(self.work)),
            "rust_toolchain": self.objects.put(rust_toolchain(Path(self.data["rust_toolchain"]["root"]))["manifest"]),
            "cargo_configuration": self.objects.put(cargo_configuration(self.work, self.environment())),
            "head": metadata_command(["git", "rev-parse", "HEAD"], source),
        }
        write_json(checkpoints / f"{phase}.json", actual)
        require_equal(actual, {"source": self.data["source_manifest"],
                               "snapshot": self.data["snapshot_manifest"],
                               "rust_authored_inputs": self.data["rust_authored_inputs"],
                               "rust_toolchain": self.data["rust_toolchain"]["manifest"],
                               "cargo_configuration": self.data["cargo_configuration"],
                               "head": self.data["expected_head"]}, "frozen membership/type/mode/hash")
        authority(source)
        validate_corpus(source, self.data["expected_sha256"], self.data["freeze_sha256"])
        validate_corpus(self.snapshot, self.data["expected_sha256"], self.data["freeze_sha256"])
        for kind in ("guided", "portable"):
            if any((self.out / "jobs" / f"rust-{kind}-lock").glob("*/*/receipt.json")):
                self.verify_rust_lock(kind)

    def successful_job(self, name):
        files = sorted((self.out / "jobs" / name).glob("*/*/receipt.json"))
        matches = {}
        for path in files:
            value = json.loads(path.read_bytes())
            if value["status"] == "succeeded" and value["exit_code"] == 0:
                raw_directory = path.parent / "commands"
                if self.objects.put(tree(raw_directory)) != value["raw_readback_manifest"]:
                    raise RuntimeError(f"attached Kyoto readback bytes changed: {path}")
                if name.endswith("-lock") and file_hash(path.parent / "Cargo.lock") != value["lock_sha256"]:
                    raise RuntimeError(f"captured generated lock bytes changed: {path}")
                matches[value["job_id"]] = value
        if len(matches) != 1:
            raise RuntimeError(f"require exactly one successful attached Kyoto job: {name}")
        return next(iter(matches.values()))


def attach_job(session, args):
    if args.name not in session.plans:
        raise RuntimeError("job name is absent from frozen job plan")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.job_id):
        raise RuntimeError("invalid Kyoto job ID")
    parent = session.out / "jobs" / args.name / args.job_id
    parent.mkdir(parents=True, exist_ok=True)
    # A premature readback is preserved and does not prevent a later terminal
    # attachment of the same job. No job is resubmitted by this tool.
    out = parent / f"readback-{len(list(parent.iterdir())) + 1:03d}"
    out.mkdir(exist_ok=False)
    recorder = Recorder(out, session.work, "kyoto-readback")
    raw = recorder.run("show", [sys.executable, KYOTO, "show", args.job_id])
    # Kyoto timing fields are floats; product protocol JSON is stricter.
    job = json.loads(raw)
    recorder.run("logs", [sys.executable, KYOTO, "logs", args.job_id])
    recorder.run("attempts", [sys.executable, KYOTO, "attempts", args.job_id])
    if job["status"] in ("queued", "running"):
        raise RuntimeError("job is still live; do not attach it or duplicate it")
    planned = session.plans[args.name]
    submission_file = session.out / "submissions" / args.name / "submitted.json"
    submission = json.loads(submission_file.read_bytes())
    if submission["job_id"] != args.job_id:
        raise RuntimeError("job ID differs from this session's controlled-environment submission")
    if session.objects.put(tree(submission_file.parent / "commands")) != submission["raw_manifest"]:
        raise RuntimeError("raw Kyoto submission bytes changed")
    require_equal(job["requested_command"], planned["argv"], "Kyoto requested command")
    require_equal(job["command"], planned["argv"], "Kyoto effective command")
    if Path(job["cwd"]).resolve() != Path(planned["cwd"]).resolve():
        raise RuntimeError("Kyoto job cwd differs from frozen external workspace")
    receipt = {"job_id": args.job_id, "status": job["status"], "exit_code": job["exit_code"],
               "requested_command": job["requested_command"], "effective_command": job["command"],
               "cwd": job["cwd"], "policy_message": job.get("policy_message"),
               "started": job.get("started"), "finished": job.get("finished"),
               "elapsed_seconds": job.get("elapsed_seconds"), "plan": session.data["job_plan"]}
    if args.submission:
        submission = out / "submission.bin"
        shutil.copyfile(args.submission, submission)
        receipt["submission_sha256"] = file_hash(submission)
    if job["status"] == "succeeded" and job["exit_code"] == 0:
        session.verify(f"attach-{args.name}-{args.job_id}-{out.name}")
        receipt["rust_toolchain"] = session.data["rust_toolchain"]
        if args.name == "rust-package":
            crate = Path(session.data["rust_crate"])
            receipt["crate"] = {"path": str(crate), "sha256": file_hash(crate)}
        elif args.name.endswith("-lock"):
            kind = args.name.split("-")[1]
            receipt.update(session.rust_lock(kind))
            project = "rust-consumer" if kind == "guided" else "rust-portable"
            shutil.copyfile(session.work / project / "Cargo.lock", out / "Cargo.lock")
            if file_hash(out / "Cargo.lock") != receipt["lock_sha256"]:
                raise RuntimeError("Rust generated lock changed while attaching its capture")
        elif args.name.endswith("-build"):
            kind = args.name.split("-")[1]
            receipt.update(session.verify_rust_lock(kind))
            metadata = session.successful_job(f"rust-{kind}-metadata")
            for key in ("lock_sha256", "lock_manifest"):
                require_equal(receipt[key], metadata[key], f"{kind} build/metadata {key}")
            binary = Path(session.data["rust_binaries"][kind])
            if not binary.is_file():
                raise RuntimeError("successful Cargo job did not create its planned binary")
            receipt["binary"] = {"path": str(binary), "sha256": file_hash(binary)}
            depfile = binary.with_suffix(".d")
            receipt["depfile_sha256"] = file_hash(depfile)
            if str(session.work / "rust-package/src/guided.rs") not in depfile.read_text():
                raise RuntimeError("Rust build dependency file does not bind frozen guided source")
        elif args.name.endswith("-metadata"):
            kind = args.name.split("-")[1]
            receipt.update(session.verify_rust_lock(kind))
            logs = next((out / "commands/kyoto-readback").glob("*-logs/stdout.bin")).read_bytes()
            rows = []
            for line in logs.splitlines():
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and "packages" in value and "resolve" in value:
                    rows.append(value)
            if len(rows) != 1:
                raise RuntimeError("Cargo metadata receipt must contain exactly one metadata document")
            data = rows[0]
            package = [p for p in data["packages"] if p["name"] == "statelessness"]
            if len(package) != 1 or Path(package[0]["manifest_path"]).resolve() != session.work / "rust-package/Cargo.toml" or package[0]["source"] is not None:
                raise RuntimeError("Rust dependency provenance is not the frozen package")
            receipt["cargo_metadata"] = session.objects.put(data)
            receipt["dependency_sources"] = session.objects.put({
                p["id"]: tree(Path(p["manifest_path"]).parent, source=True)
                for p in data["packages"] if p["source"] is not None})
        elif args.name in ("build-native", "run"):
            phase_path = session.out / (args.name + ".json")
            if json.loads(phase_path.read_bytes())["status"] != "complete-unsealed":
                raise RuntimeError("successful Kyoto job did not finish its harness phase")
            receipt["phase_result_sha256"] = file_hash(phase_path)
    receipt["raw_readback_manifest"] = session.objects.put(tree(out / "commands"))
    write_json(out / "receipt.json", receipt)
    print(json.dumps({"name": args.name, "job_id": args.job_id, "status": job["status"]}))


def submit_job(session, args):
    """Submit literal planned argv to Kyoto, which still sees/classifies Cargo.

    This is not a build wrapper: only Kyoto starts the executable. An exclusive
    submission directory is the retry fence; uncertain/failed submissions are
    inspected, never blindly repeated.
    """
    if args.name not in session.plans:
        raise RuntimeError("unknown frozen job plan entry")
    if args.name.startswith("rust-") and args.name not in ("rust-package",):
        installed = json.loads((session.out / "install-rust.json").read_bytes())
        if installed["status"] != "complete-unsealed":
            raise RuntimeError("install-rust must precede external Rust consumer jobs")
        if args.name.endswith("-metadata"):
            session.verify_rust_lock(args.name.split("-")[1])
        if args.name.endswith("-build"):
            metadata = session.successful_job(args.name.replace("-build", "-metadata"))
            for key, value in session.verify_rust_lock(args.name.split("-")[1]).items():
                require_equal(value, metadata[key], "Rust lock before build submission")
    if args.name == "build-native":
        if not (session.out / "install-rust.json").is_file():
            raise RuntimeError("install-rust must precede native packaging")
    if args.name == "run":
        session.successful_job("build-native")
        from execution import rust_receipts
        rust_receipts(session)
    session.verify("submit-" + args.name)
    out = session.out / "submissions" / args.name
    out.mkdir(parents=True, exist_ok=False)
    plan = session.plans[args.name]
    require_equal(plan["environment"], session.environment(), "planned execution environment")
    recorder = Recorder(out, session.work, "submit", env=session.environment())
    raw = recorder.run("kyoto-submit", plan["submit_argv"])
    value = json.loads(raw)
    job_id = value.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("Kyoto submission returned no ID; inspect raw output before any retry")
    write_json(out / "submitted.json", {"job_id": job_id, "name": args.name,
                                        "raw_manifest": session.objects.put(tree(out / "commands"))})
    print(json.dumps({"name": args.name, "job_id": job_id, "status": value.get("status")}))


def install_rust(session):
    job = session.successful_job("rust-package")
    crate = Path(job["crate"]["path"])
    if file_hash(crate) != job["crate"]["sha256"]:
        raise RuntimeError("Cargo package changed before extraction")
    destination = session.work / "rust-package"
    destination.mkdir(exist_ok=False)
    with tarfile.open(crate, "r:gz") as archive:
        members = archive.getmembers()
        roots = {Path(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise RuntimeError("crate archive must have one package root")
        seen = set()
        for member in members:
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or ".." in parts or not (member.isdir() or member.isfile()):
                raise RuntimeError("unsafe/non-regular crate member")
            rel = Path(*parts[1:])
            if rel in seen:
                raise RuntimeError("duplicate crate archive member")
            seen.add(rel)
            target = destination / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
    authority(destination)
    readonly(destination)
    for rel in ("src", "build.rs", "docs/requirements", "spec"):
        require_equal(tree(destination, (rel,))["entries"], tree(session.snapshot, (rel,))["entries"],
                      f"Rust packaged source {rel}")
    shutil.copyfile(crate, session.work / "packages" / crate.name)
    return {"crate_sha256": file_hash(crate), "package_manifest": session.objects.put(tree(destination))}


def phase(session, name, operation):
    result_path = session.out / (name + ".json")
    if result_path.exists():
        raise RuntimeError(f"phase {name} already has a receipt; use a new session for corrections")
    result = {"status": "failed", "phase": name, "started": now()}
    try:
        session.verify(name + "-before")
        result.update(operation(session))
        session.verify(name + "-after")
        result["status"] = "complete-unsealed"
    except BaseException as exc:
        result["error"] = repr(exc)
        raise
    finally:
        result["finished"] = now()
        write_json(result_path, result)


def finalize(session):
    session.verify("finalize")
    for name in session.plans:
        session.successful_job(name)
    from installed import verify_installed
    installed = json.loads((session.out / "build-native.json").read_bytes())
    run = json.loads((session.out / "run.json").read_bytes())
    if installed["status"] != "complete-unsealed" or run["status"] != "complete-unsealed":
        raise RuntimeError("failed/incomplete native build or execution phase")
    for name in ("build-native", "run"):
        if file_hash(session.out / (name + ".json")) != session.successful_job(name)["phase_result_sha256"]:
            raise RuntimeError(f"{name} result changed after attached Kyoto completion")
    require_equal(session.objects.put(tree(session.out / "commands/build-native")),
                  installed["commands_manifest"], "native build raw command evidence")
    require_equal(session.objects.put(tree(session.out / "commands/run")),
                  run["results_manifest"], "execution raw command evidence")
    verify_installed(session, installed)
    from execution import rust_receipts, verify_results
    rust_receipts(session)
    verify_results(session, run)
    if run["engines"] != list(ENGINES) or run["case_count"] != len(session.data["cases"]):
        raise RuntimeError("missing engine or shared case")
    write_json(session.out / "source-evidence-after.json", {
        "root": session.data["source"], "references": capture_evidence(Path(session.data["source"]), session.objects)})
    receipt_manifest = session.objects.put(tree(session.out, ("commands", "jobs", "checkpoints", "submissions")))
    write_json(session.out / "qualification.json", {
        "status": "passed", "time": now(), "session_sha256": file_hash(session.out / "session.json"),
        "run_sha256": file_hash(session.out / "run.json"),
        "build_sha256": file_hash(session.out / "build-native.json"),
        "result_artifacts_manifest": run["result_artifacts_manifest"],
        "receipt_manifest": receipt_manifest,
        "separate_source_evidence": {name: file_hash(session.out / name)
                                     for name in ("source-evidence-before.json", "source-evidence-after.json")},
        "manifest_objects": "objects/<sha256>.json; identical trees reuse references",
        "engines": list(ENGINES), "case_count": run["case_count"],
        "corpus_sha256": session.data["expected_sha256"], "freeze_sha256": session.data["freeze_sha256"],
        "claims": session.data["scope"], "peak_rss_bytes": None,
        "memory_scope": "measured report counts only; no hard RSS cap"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    prepare_parser = sub.add_parser("prepare", help="freeze inputs and emit direct Kyoto jobs; no execution")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--source", type=Path, default=ROOT)
    prepare_parser.add_argument("--expect-head", required=True)
    prepare_parser.add_argument("--expected-sha256", required=True)
    prepare_parser.add_argument("--freeze-sha256", required=True)
    prepare_parser.add_argument("--rust-toolchain-root", type=Path, required=True,
                                help="absolute resolved Rust installation, containing real bin/cargo and bin/rustc")
    prepare_parser.add_argument("--supersedes", type=Path)
    for name in ("build-native", "install-rust", "submit-job", "run", "attach-job", "finalize"):
        command = sub.add_parser(name)
        command.add_argument("--session", type=Path, required=True)
        if name in ("submit-job", "attach-job"):
            command.add_argument("--name", required=True)
        if name == "attach-job":
            command.add_argument("--job-id", required=True)
            command.add_argument("--submission", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "prepare":
        prepare(args)
        return 0
    session = Session(args.session)
    # A killed worker can leave active.lock. Read-only Kyoto capture remains
    # possible without stealing that lock or resuming the failed experiment.
    if args.phase == "attach-job":
        attach_job(session, args)
        return 0
    if args.phase == "submit-job":
        submit_job(session, args)
        return 0
    lock = session.out / "active.lock"
    with lock.open("x") as stream:
        stream.write(f"{os.getpid()} {args.phase}\n")
    try:
        if args.phase == "finalize":
            finalize(session)
        elif args.phase == "install-rust":
            phase(session, "install-rust", install_rust)
        elif args.phase == "build-native":
            from installed import build
            phase(session, "build-native", build)
        elif args.phase == "run":
            from execution import run
            phase(session, "run", run)
    finally:
        lock.unlink()
    return 0
