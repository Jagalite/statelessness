"""Frozen golden comparison and losslessly logged public portable regression."""
from __future__ import annotations

import contextlib
import importlib.util
import json
from pathlib import Path
import sys

from evidence import (Recorder, canonical, file_hash, require_equal, strict_json,
                      tree, write_json)
from installed import verify_installed
from qualification import ENGINES, validate_corpus


FIELDS = {"report", "error", "trace", "generator_rng", "witness_replays", "shrink"}
RESULT_ARTIFACTS = ("guided-comparison.json", "execution-summary.json", "rust-host-checks.json",
                    "portable-baseline.json", "portable-orchestrator.stdout.txt")


def result_artifacts(session):
    for name in RESULT_ARTIFACTS:
        if not (session.out / name).is_file() or (session.out / name).is_symlink():
            raise RuntimeError(f"missing/nonregular execution result artifact: {name}")
    return session.objects.put(tree(session.out, RESULT_ARTIFACTS))


def verify_results(session, run):
    require_equal(result_artifacts(session), run["result_artifacts_manifest"],
                  "execution result artifact membership/type/mode/hash")
    if file_hash(session.out / "portable-baseline.json") != run["baseline"]["report_sha256"]:
        raise RuntimeError("portable baseline report changed after execution")
    comparison = strict_json((session.out / "guided-comparison.json").read_bytes())
    if comparison["failures"]:
        raise RuntimeError("failed guided comparison cannot seal")
    wanted = [(engine, case) for engine in ENGINES for case in session.data["cases"]]
    require_equal([(row["engine"], row["id"]) for row in comparison["results"]], wanted,
                  "complete ordered guided comparison rows")
    for row in comparison["results"]:
        if row["status"] != "passed":
            raise RuntimeError("incomplete guided comparison cannot seal")
        actual = session.objects.get(row["actual"])
        expected = session.objects.get(row["expected"])
        require_equal(actual, expected, f"sealed comparison objects {row['engine']}/{row['id']}")


def rust_receipts(session):
    for name in session.plans:
        if name.startswith("rust-") or name == "cargo-version":
            job = session.successful_job(name)
            require_equal(job["rust_toolchain"], session.data["rust_toolchain"], "Rust job toolchain")
    installed = json.loads((session.out / "install-rust.json").read_bytes())
    if installed["status"] != "complete-unsealed":
        raise RuntimeError("Rust package extraction did not finish")
    require_equal(session.objects.put(tree(session.work / "rust-package")),
                  installed["package_manifest"], "extracted Rust package membership/type/mode/hash")
    package = session.successful_job("rust-package")
    if file_hash(package["crate"]["path"]) != installed["crate_sha256"]:
        raise RuntimeError("Rust crate archive changed")
    for kind in ("guided", "portable"):
        build = session.successful_job(f"rust-{kind}-build")
        metadata = session.successful_job(f"rust-{kind}-metadata")
        lock = session.successful_job(f"rust-{kind}-lock")
        if not lock["finished"] <= metadata["started"] <= metadata["finished"] <= build["started"]:
            raise RuntimeError(f"{kind} Rust jobs must run lock, metadata, build in that order")
        if file_hash(build["binary"]["path"]) != build["binary"]["sha256"]:
            raise RuntimeError(f"{kind} Rust binary changed after its Kyoto build")
        for key, value in session.verify_rust_lock(kind).items():
            require_equal([lock[key], metadata[key], build[key]], [value] * 3,
                          f"{kind} Rust lock generation/metadata/build identity")
        data = session.objects.get(metadata["cargo_metadata"])
        current = {p["id"]: tree(Path(p["manifest_path"]).parent, source=True)
                   for p in data["packages"] if p["source"] is not None}
        require_equal(session.objects.put(current), metadata["dependency_sources"], "Rust dependency source bytes")


def portable_regression(session, recorder, runners):
    """Keep existing conformance/run.py semantics; replace only its I/O logger.

    The unmodified, frozen suite generates its own baseline expectations. Each
    public packaged runner still executes in a fresh OS process. This interception
    prevents the old suite's stderr truncation from losing original evidence.
    """
    path = session.snapshot / "conformance/run.py"
    spec = importlib.util.spec_from_file_location("n05_frozen_portable_suite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    invocations = 0
    names = {tuple(command): engine for engine, command in runners.items()}

    def invoke(command, requests, timeout):
        nonlocal invocations
        invocations += 1
        engine = names[tuple(command)]
        request_bytes = b"".join((request if isinstance(request, bytes) else canonical(request)) + b"\n"
                                  for request in requests)
        raw = recorder.run(f"portable-{engine}-{invocations}", command, stdin=request_bytes, timeout=timeout)
        if len(raw) > module.MAX_OUTPUT:
            raise RuntimeError("portable output exceeds the existing suite's parsing limit; raw bytes retained")
        responses = [strict_json(line) for line in raw.splitlines()]
        if len(responses) != len(requests):
            raise RuntimeError(f"portable {engine}: expected {len(requests)} responses, got {len(responses)}")
        return responses

    # Validate engine attribution ourselves; the old runner accepts unknown
    # identities and must not silently count a duplicate engine as five engines.
    for engine, command in runners.items():
        hello = invoke(command, [{"version": 1, "operation": "hello"}], 60)[0]
        if hello.get("implementation") != engine:
            raise RuntimeError(f"portable runner identity mismatch: {engine}: {hello}")
    module.invoke = invoke
    report = session.out / "portable-baseline.json"
    arguments = [str(path), "--corpus", str(session.snapshot / "conformance/corpus.json"),
                 "--report", str(report)]
    for engine in ENGINES:
        arguments.extend(["--runner", json.dumps(runners[engine])])
    original_argv = sys.argv
    try:
        sys.argv = arguments
        with (session.out / "portable-orchestrator.stdout.txt").open("x", encoding="utf-8") as output:
            with contextlib.redirect_stdout(output):
                code = module.main()
    finally:
        sys.argv = original_argv
    result = json.loads(report.read_bytes())
    if code != 0 or result["failures"]:
        raise RuntimeError("public packaged portable regression failed; full raw child logs retained")
    if len(result["implementations"]) != 5 or result["independent_engine_count"] != 5:
        raise RuntimeError("portable baseline omitted an independent engine")
    require_equal([row["implementation"] for row in result["implementations"]], list(ENGINES),
                  "portable baseline engine order and identity")
    # These counts belong to the preserved portable suite, not the evolving
    # guided corpus. A shortened/partially collected baseline cannot seal green.
    counts = {"golden_cases": 94, "generated_graphs": 530, "metamorphic_cases": 32,
              "portability_cases": 38, "transport_cases": 23, "failures": 0}
    for row in result["implementations"]:
        require_equal({key: row[key] for key in counts}, counts, "portable per-engine coverage")
    require_equal(result["fresh_process_replays"], 550, "portable ordered-pair replay coverage")
    require_equal(invocations, 45, "portable fresh-process count, including identity probes")
    return {"report_sha256": file_hash(report), "fresh_process_invocations": invocations,
            "suite_sha256": file_hash(path), "suite_argv": arguments,
            "per_engine_counts": counts, "ordered_pair_replay_checks": 550,
            "profiles": ["core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"]}


def run(session):
    session.successful_job("build-native")
    built = json.loads((session.out / "build-native.json").read_bytes())
    if built["status"] != "complete-unsealed":
        raise RuntimeError("native package build did not finish")
    verify_installed(session, built)
    rust_receipts(session)
    corpus = validate_corpus(session.snapshot, session.data["expected_sha256"], session.data["freeze_sha256"])
    runners = dict(built["runners"], rust=[session.data["rust_binaries"]["guided"]])
    portable = dict(built["portable_runners"], rust=[session.data["rust_binaries"]["portable"]])
    if set(runners) != set(ENGINES) or set(portable) != set(ENGINES):
        raise RuntimeError("missing installed guided or portable engine")
    recorder = Recorder(session.out, session.work, "run", env=session.environment())
    for name, filename in (("STATELESS_PAIRED_CORPUS", "paired-v1.tsv"),
                           ("STATELESS_SEARCH_CORPUS", "search-v1.tsv"),
                           ("STATELESS_SEARCH_HISTORY", "search-v1-history.json"),
                           ("STATELESS_BINARY_CORPUS", "native-binary-v1.json"),
                           ("STATELESS_BFS_CORPUS", "bfs-controls-v1.json")):
        recorder.env[name] = str(session.snapshot / "conformance" / filename)
    # Preserve independent FREEZE admission in consumers such as Python. The
    # fixture adapters must ignore expected; comparison remains harness-owned.
    request_path = session.snapshot / "conformance/guided-v2/corpus.json"
    wanted_input_hash = file_hash(request_path)
    results, failures = [], []
    for engine in ENGINES:
        for index, case in enumerate(corpus["cases"]):
            result = {"engine": engine, "id": case["id"], "status": "failed"}
            try:
                raw = recorder.run(f"guided-{engine}-{index:04d}",
                                   [*runners[engine], "--corpus", request_path, "--case", case["id"]], timeout=120)
                value = strict_json(raw)
                if set(value) != {"schema", "engine", "cases"} or value["schema"] != "stateless-guided-v2-results/1" or value["engine"] != engine:
                    raise RuntimeError("unexpected consumer response schema/engine")
                if len(value["cases"]) != 1 or set(value["cases"][0]) != {"id", "actual"} or value["cases"][0]["id"] != case["id"]:
                    raise RuntimeError("fresh process must execute exactly its selected case")
                actual = value["cases"][0]["actual"]
                if set(actual) != FIELDS:
                    raise RuntimeError("consumer must return all six required fields, with no extras")
                expected = {key: value for key, value in case["expected"].items() if key != "rng_audit"}
                result["actual"] = session.objects.put(actual)
                result["expected"] = session.objects.put(expected)
                require_equal(actual, expected, f"{engine}/{case['id']}")
                result["status"] = "passed"
            except Exception as exc:
                result["error"] = repr(exc)
                failures.append({"engine": engine, "id": case["id"], "error": repr(exc)})
            results.append(result)
            print("KYOTO_PROGRESS " + json.dumps({"completed": len(results), "total": len(ENGINES) * len(corpus["cases"]), "phase": "guided cases"}), flush=True)
        if file_hash(request_path) != wanted_input_hash:
            raise RuntimeError("consumer mutated the independently frozen corpus")
        verify_installed(session, built)
        rust_receipts(session)
    comparison = {"schema": "n05-comparison/1", "expected_sha256": session.data["expected_sha256"],
                  "input_sha256": wanted_input_hash, "results": results, "failures": failures,
                  "case_count": len(corpus["cases"]), "engines": list(ENGINES)}
    write_json(session.out / "guided-comparison.json", comparison)
    # Host-only Rust assertions are separate from the cross-language expected
    # corpus. Their exact coverage labels remain visible in the original output.
    try:
        host = strict_json(recorder.run("rust-host-boundaries", [*runners["rust"], "--host-checks"], timeout=120))
        if host.get("schema") != "stateless-guided-rust-host/1" or not host.get("passed") or type(host.get("count")) is not int or host["count"] != len(host["passed"]):
            raise RuntimeError("Rust public host assertions did not report successful coverage")
        write_json(session.out / "rust-host-checks.json", host)
    except Exception as exc:
        failures.append({"engine": "rust", "id": "host-checks", "error": repr(exc)})
    baseline = None
    try:
        baseline = portable_regression(session, recorder, portable)
    except Exception as exc:
        failures.append({"engine": "all", "id": "portable-baseline", "error": repr(exc)})
    write_json(session.out / "execution-summary.json", {
        "failures": failures, "baseline": baseline, "guided_comparison_sha256": file_hash(session.out / "guided-comparison.json")})
    verify_installed(session, built)
    rust_receipts(session)
    if failures:
        raise RuntimeError(f"{len(failures)} qualification failures; preserve this session and correct in a new freeze")
    return {"engines": list(ENGINES), "case_count": len(corpus["cases"]),
            "fresh_guided_processes": len(results), "baseline": baseline,
            "runners": runners, "portable_runners": portable,
            "counts": {"shared_cases": len(corpus["cases"]), "guided_processes": len(results),
                       "host_assertions": host["count"]},
            "memory_scope": "guided limits count retained values/features; no hard RSS bound",
            "result_artifacts_manifest": result_artifacts(session),
            "results_manifest": session.objects.put(tree(session.out / "commands/run"))}
