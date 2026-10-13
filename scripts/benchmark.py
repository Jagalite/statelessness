#!/usr/bin/env python3
"""Collect source-bound benchmark samples, or explicitly compile without measuring.

Standard library only. With --kyoto, every Cargo command is submitted directly
to the queue: a policy rejection fails the run and is never bypassed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import time
import uuid

from qualification_context import git_context


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "engine": {
        "direct_transition": (1, "transition"),
        "transition_state_checks": (1, "transition"),
        "transition_all_checks": (1, "transition"),
        "record_three": (3, "transition"),
        "encode_trace": (1, "trace"),
        "exact_replay_three": (3, "transition"),
    },
    "observation": {
        "borrowed_event": (1, "event"),
        "off_to_sink": (1, "event"),
        "info_to_sink": (1, "event"),
        "debug_to_sink": (1, "event"),
        "trace_to_sink": (1, "event"),
        "buffered_file_no_fsync": (1, "record"),
    },
}

CASES["roadmap"] = {
    "controller_reducer_cycle": (8, "transition"),
    "controller_checked_cycle": (8, "transition"),
    **{case: (1, "transition") for case in (
        "owned_step_32k", "shared_step_32k", "outputs_1", "outputs_64",
        "pair_owned_32k", "pair_shared_32k", "oracle_history_0", "oracle_history_256",
        "network_three")},
    "recorder_window_1": (64, "transition"), "recorder_window_16": (64, "transition"),
    "encode_state_32k": (1, "state"), "bfs_64": (128, "transition"),
    "fuzz_64": (64, "transition"), "guided_fuzz_64": (64, "transition"),
}
for suite in ("diagnostics_enabled", "diagnostics_compiled_out"):
    CASES[suite] = {case: (1, "operation") for case in (
        "effects_disabled", "effects_counters", "effects_timings", "effects_detail",
        "probe_runtime_disabled", "probe_enabled", "controller_delivery", "live_bridge_recording")}
INSTRUMENTED_SUITES = {"roadmap", "diagnostics_enabled", "diagnostics_compiled_out"}


def expected_iterations(suite, case, iterations):
    if suite in INSTRUMENTED_SUITES:
        return iterations // 100 + 1
    if suite == "engine" and case in {"record_three", "encode_trace", "exact_replay_three"}:
        return iterations // 10 + 1
    return iterations


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_context(root):
    """Hash actual benchmark/build inputs, including uncommitted new files."""
    paths = set()
    for folder in ("src", "benches", "crates", ".cargo", "docs/requirements"):
        for base, directories, files in os.walk(root / folder):
            directories[:] = sorted(d for d in directories if d not in
                                    {"target", ".git", "__pycache__"})
            paths.update(Path(base) / name for name in files)
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain",
                 "rust-toolchain.toml", "scripts/benchmark.py",
                 "scripts/qualification_context.py"):
        path = root / name
        if path.is_file():
            paths.add(path)
    files = {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths)}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    contract = {name: value for name, value in files.items()
                if name.startswith(("benches/", ".cargo/")) or "/benches/" in name
                or name.endswith(("Cargo.toml", "Cargo.lock", "/build.rs"))
                or name in {"build.rs", "rust-toolchain", "rust-toolchain.toml",
                            "scripts/benchmark.py", "scripts/qualification_context.py"}}
    contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    return {"git": git_context(root), "files_sha256": files,
            "inputs_sha256": digest, "benchmark_contract_sha256": contract_hash}


def probe(command, root, environment=None):
    try:
        result = subprocess.run(command, cwd=root, capture_output=True,
                                text=True, timeout=20, check=False, env=environment)
        return {"command": command, "exit_code": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "error": str(error)}


def tool_command(tool, toolchain):
    return ["rustup", "run", toolchain, tool] if toolchain else [tool]


def select_toolchain(root, toolchain, environment):
    """Pin Cargo's actual compiler even when PATH contains non-rustup binaries."""
    selected = dict(environment)
    if toolchain:
        result = subprocess.run(["rustup", "which", "--toolchain", toolchain, "rustc"],
                                cwd=root, capture_output=True, text=True, timeout=20)
        if result.returncode or not Path(result.stdout.strip()).is_file():
            raise RuntimeError(result.stderr.strip() or f"rustc unavailable for {toolchain}")
        compiler = Path(result.stdout.strip()).resolve()
        selected["RUSTC"] = str(compiler)
        selected["RUSTDOC"] = str(compiler.with_name("rustdoc.exe" if os.name == "nt" else "rustdoc"))
        selected["PATH"] = str(compiler.parent) + os.pathsep + selected.get("PATH", "")
    return selected


def environment_context(root, toolchain, environment=None):
    environment = os.environ if environment is None else environment
    variables = {name: value for name, value in sorted(environment.items())
                 if name in {"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTUP_TOOLCHAIN",
                             "RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                             "CARGO_BUILD_TARGET", "CARGO_BUILD_JOBS", "CARGO_TARGET_DIR",
                             "CARGO_INCREMENTAL"}
                 or name.startswith("CARGO_PROFILE_")}
    cargo_home = Path(environment.get("CARGO_HOME", Path.home() / ".cargo"))
    configs = {str(path): sha256(path) for path in
               (cargo_home / "config", cargo_home / "config.toml") if path.is_file()}
    hardware = {"os": platform.system(), "release": platform.release(),
                "machine": platform.machine(), "logical_cpus": os.cpu_count(),
                "processor": platform.processor() or None}
    if sys.platform == "darwin":
        hardware["sysctl"] = probe(
            ["sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize", "hw.ncpu"], root)
    elif sys.platform.startswith("linux"):
        for path in (Path("/proc/cpuinfo"), Path("/proc/meminfo")):
            if path.is_file():
                # Omit per-core repetition, identifiers and unrelated host details.
                wanted = ("model name", "Hardware", "MemTotal")
                hardware[path.name] = sorted({line.strip() for line in path.read_text().splitlines()
                                             if line.split(":", 1)[0].strip() in wanted})
    return {"rustc": probe([environment.get("RUSTC", "rustc"), "-Vv"], root, environment),
            "cargo": probe([*tool_command("cargo", toolchain), "-V"], root, environment),
            "python": platform.python_version(), "hardware": hardware,
            "build_environment": variables, "user_cargo_config_sha256": configs}


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values):
    mean = statistics.mean(values)
    deviation = statistics.stdev(values)
    return {"count": len(values), "mean": mean, "sample_stddev": deviation,
            "coefficient_of_variation": deviation / mean if mean else None,
            "min": min(values), "max": max(values),
            "p50": percentile(values, .50), "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def parse_samples(log, suite, iterations, samples, warmup):
    rows = []
    seen = set()
    for line in log.splitlines():
        if not line.startswith('{"schema_version":'):
            continue  # Cargo diagnostics and fixture context remain in the raw log.
        row = json.loads(line)
        case = row.get("case")
        if type(row.get("schema_version")) is not int or row.get("schema_version") != 1 or row.get("suite") != suite:
            raise ValueError("unexpected benchmark schema or suite")
        if case not in CASES[suite] or case in seen:
            raise ValueError(f"unknown or duplicate {suite} case: {case}")
        seen.add(case)
        units, unit = CASES[suite][case]
        operation_count = expected_iterations(suite, case, iterations)
        for key, expected in (("iterations_per_sample", operation_count),
                              ("warmup_iterations", warmup),
                              ("units_per_operation", units), ("unit", unit)):
            if row.get(key) != expected or (type(expected) is int and type(row.get(key)) is not int):
                raise ValueError(f"{suite}/{case}: unexpected {key}")
        elapsed = row.get("elapsed_ns")
        if (not isinstance(elapsed, list) or len(elapsed) != samples
                or any(type(value) is not int or value <= 0 for value in elapsed)):
            raise ValueError(f"{suite}/{case}: invalid raw samples")
        probe = row.get("allocation_probe")
        if suite in INSTRUMENTED_SUITES:
            workload = row.get("workload")
            if (not isinstance(workload, dict) or not workload or
                    any(not isinstance(key, str) or type(value) is not int or value < 0
                        for key, value in workload.items())):
                raise ValueError(f"{suite}/{case}: missing or invalid workload metadata")
            if (not isinstance(probe, dict) or probe.get("timed") is not False or
                    type(probe.get("operations")) is not int or probe["operations"] != 32 or
                    any(type(probe.get(key)) is not int or probe[key] < 0
                        for key in ("allocations", "reallocations", "requested_bytes"))):
                raise ValueError(f"{suite}/{case}: invalid separate allocation probe")
        elif probe is not None:
            raise ValueError(f"{suite}/{case}: unexpected allocation probe")
        per_operation = [value / operation_count for value in elapsed]
        per_unit = [value / units for value in per_operation]
        rows.append({**row, "ns_per_operation": per_operation, "ns_per_unit": per_unit,
                     "ns_per_operation_summary": summarize(per_operation),
                     "ns_per_unit_summary": summarize(per_unit)})
    if seen != set(CASES[suite]):
        raise ValueError(f"missing benchmark cases: {sorted(set(CASES[suite]) - seen)}")
    return rows


def cargo_command(suites, profile, toolchain, compile_only):
    command = tool_command("cargo", toolchain)
    command.extend(["bench", "--locked", "--profile", profile])
    diagnostic = [suite for suite in suites if suite.startswith("diagnostics_")]
    if diagnostic:
        if len(suites) != 1:
            raise ValueError("diagnostic feature variants must use separate Cargo commands")
        command.extend(["-p", "statelessness-debug", "--bench", "diagnostics"])
        if suites[0] == "diagnostics_compiled_out":
            command.extend(["--features", "compiled-out-diagnostics"])
    else:
        for suite in suites:
            command.extend(["--bench", suite])
    if compile_only:
        command.append("--no-run")
    return command


def execute(command, root, environment, log_path, kyoto, timeout=1800):
    """No shells or opaque wrappers: Kyoto sees the complete Cargo invocation."""
    started = time.monotonic()
    receipt = {"command": command, "log": log_path.name}
    if kyoto:
        prefix = [sys.executable, str(kyoto)]
        # Unique per invocation, including when the caller has KYOTO_USER_ID set.
        # This lets recovery identify only our job if submission output is lost.
        owner = str(uuid.uuid4())
        receipt["kyoto_owner_id"] = owner
        job = None

        def invoke(arguments):
            result = subprocess.run([*prefix, *arguments], env=environment,
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                                   or f"Kyoto command failed: {arguments[0]}")
            return json.loads(result.stdout)

        def validate_job(value):
            if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
                    or value.get("status") not in {"queued", "running", "succeeded", "failed", "cancelled"}):
                raise ValueError("malformed Kyoto job result")
            if job is not None and value["id"] != job["id"]:
                raise ValueError("Kyoto result changed job identity")
            return value

        def cancel_owned():
            errors = []
            requested = []
            try:
                jobs = [job] if job is not None else invoke(["jobs", "--user-id", owner])
                if not isinstance(jobs, list):
                    raise ValueError("malformed Kyoto owner job list")
                for owned in jobs:
                    if not isinstance(owned, dict) or not isinstance(owned.get("id"), str):
                        raise ValueError("malformed Kyoto owned job")
                    invoke(["cancel", owned["id"], "--user-id", owner])
                    requested.append(owned["id"])
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
            receipt["cancellation_requested"] = requested
            receipt["cancellation_errors"] = errors

        try:
            receipt["stage"] = "submission"
            # Keep rejection separate from interrupted/lost submission responses:
            # a normal rejection has no job and needs no cancellation attempt.
            submitted = subprocess.run(
                [*prefix, "submit", "--user-id", owner, "--cwd", str(root), "--", *command],
                env=environment, capture_output=True, text=True, timeout=30)
            if submitted.returncode:
                log_path.write_text(submitted.stdout + submitted.stderr)
                return {**receipt, "exit_code": submitted.returncode,
                        "elapsed_seconds": time.monotonic() - started,
                        "resource_usage": queue_resources(None)}
            job = validate_job(json.loads(submitted.stdout))
            receipt["kyoto_job_id"] = job["id"]
            print(f"Kyoto job {job['id']}: {' '.join(command)}", flush=True)
            receipt["stage"] = "queue_execution"
            while job["status"] in {"queued", "running"}:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Kyoto queue/execution deadline reached")
                time.sleep(min(2, max(0, timeout - (time.monotonic() - started))))
                job = validate_job(invoke(["show", job["id"]]))
            receipt["kyoto_result"] = job
            receipt["resource_usage"] = queue_resources(job)
            if type(job.get("exit_code")) is not int:
                raise ValueError("Kyoto terminal job has no integer exit code")
            with log_path.open("w") as output:
                logs = subprocess.run([*prefix, "logs", job["id"]], env=environment,
                                      stdout=output, stderr=subprocess.STDOUT, timeout=30)
            receipt["exit_code"] = job["exit_code"] or logs.returncode
            if job["status"] != "succeeded" and not receipt["exit_code"]:
                receipt["exit_code"] = 1
        except (OSError, RuntimeError, ValueError, TimeoutError,
                subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            receipt["error"] = str(error) or type(error).__name__
            receipt["exit_code"] = 1
            cancel_owned()
            with log_path.open("a") as output:
                output.write(receipt["error"] + "\n")
    else:
        with log_path.open("w") as output:
            process = subprocess.Popen(command, cwd=root, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=30)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=30)
                receipt.update(exit_code=1, error=str(error) or type(error).__name__)
    receipt.setdefault("resource_usage", queue_resources(None))
    receipt["elapsed_seconds"] = time.monotonic() - started
    return receipt


def queue_resources(job):
    """Whole final Cargo attempt, never per-operation RSS/CPU or summed retries."""
    fields = ("cpu_seconds", "user_cpu_seconds", "system_cpu_seconds",
              "peak_rss_mb", "wait_max_rss_mb", "rss_samples")
    values = {field: None for field in fields}
    if isinstance(job, dict):
        for field in fields:
            value = job.get(field)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                values[field] = value
    return {"source": "kyoto_final_attempt" if job is not None else "unavailable",
            "scope": "whole Cargo attempt including build/startup/warmup/samples/allocation pass; not per case",
            **values}


def verified_measurements(report):
    if report.get("status") != "measured" or report.get("source_stable") is not True:
        raise ValueError("baseline comparison requires completed measured, source-stable receipts")
    source, after = report.get("source", {}), report.get("source_after", {})
    if not source.get("inputs_sha256") or source.get("inputs_sha256") != after.get("inputs_sha256"):
        raise ValueError("benchmark source identity changed or is absent")
    if not source.get("benchmark_contract_sha256"):
        raise ValueError("benchmark contract identity is absent")
    environment = report.get("environment", {})
    for tool in ("rustc", "cargo"):
        if environment.get(tool, {}).get("exit_code") != 0 or not environment.get(tool, {}).get("stdout"):
            raise ValueError(f"actual {tool} identity is absent")
    hardware = environment.get("hardware", {})
    if not hardware.get("os") or not hardware.get("machine"):
        raise ValueError("benchmark hardware identity is absent")
    config = report.get("configuration", {})
    if (any(type(config.get(key)) is not int or config[key] <= 0
            for key in ("iterations", "samples", "warmup_iterations"))
            or config["samples"] < 2 or not config.get("sample_kind")
            or config.get("profile") not in ("bench", "dev")):
        raise ValueError("invalid benchmark comparison configuration")
    rows = report.get("measurements", [])
    result = {}
    for suite in config.get("suites", []):
        if suite not in CASES:
            raise ValueError("unknown comparison suite")
        raw = "\n".join(json.dumps(row, separators=(",", ":")) for row in rows if row.get("suite") == suite)
        for row in parse_samples(raw, suite, config["iterations"], config["samples"], config["warmup_iterations"]):
            result[(suite, row["case"])] = row
    if len(result) != len(rows) or not result:
        raise ValueError("unrecognized or empty benchmark measurement set")
    return result


def compare_baseline(current, baseline, max_regression_percent, max_cv):
    candidate, previous = verified_measurements(current), verified_measurements(baseline)
    if current["source"]["benchmark_contract_sha256"] != baseline["source"]["benchmark_contract_sha256"]:
        raise ValueError("incomparable benchmark fixture, harness or Cargo contract")
    for field in ("suites", "profile", "iterations", "samples", "warmup_iterations", "sample_kind"):
        if current["configuration"].get(field) != baseline["configuration"].get(field):
            raise ValueError(f"incomparable benchmark configuration: {field}")
    for field in ("rustc", "cargo", "hardware", "build_environment", "user_cargo_config_sha256"):
        if current.get("environment", {}).get(field) != baseline.get("environment", {}).get(field):
            raise ValueError(f"incomparable benchmark environment: {field}")
    if set(candidate) != set(previous):
        raise ValueError("incomparable benchmark case sets")
    changes = []
    for key in sorted(candidate):
        new, old = candidate[key], previous[key]
        if new.get("workload") != old.get("workload"):
            raise ValueError(f"incomparable workload metadata: {key}")
        for row in (new, old):
            cv = row["ns_per_operation_summary"]["coefficient_of_variation"]
            if cv is None or cv > max_cv:
                raise ValueError(f"unstable samples exceed max CV {max_cv}: {key}")
        ratio = new["ns_per_operation_summary"]["p50"] / old["ns_per_operation_summary"]["p50"]
        changes.append({"suite": key[0], "case": key[1], "median_ratio": ratio,
                        "regression_percent": (ratio - 1) * 100,
                        "regressed": ratio > 1 + max_regression_percent / 100})
    return {"status": "regression" if any(row["regressed"] for row in changes) else "within_threshold",
            "maximum_regression_percent": max_regression_percent, "maximum_cv": max_cv,
            "baseline_source": baseline["source"]["inputs_sha256"],
            "candidate_source": current["source"]["inputs_sha256"], "cases": changes,
            "scope": "empirical warmed batch medians on this matched environment; no statistical or production guarantee"}


def mode_comparisons(rows):
    indexed = {(row["suite"], row["case"]): row for row in rows}
    comparisons = []
    pairs = [("roadmap", "owned_step_32k", "shared_step_32k"),
             ("roadmap", "pair_owned_32k", "pair_shared_32k"),
             ("roadmap", "oracle_history_0", "oracle_history_256"),
             ("roadmap", "recorder_window_1", "recorder_window_16")]
    for suite, before, after in pairs:
        if (suite, before) not in indexed or (suite, after) not in indexed:
            continue
        a, b = indexed[suite, before], indexed[suite, after]
        comparisons.append({"suite": suite, "reference": before, "candidate": after,
                            "median_ratio": b["ns_per_operation_summary"]["p50"] / a["ns_per_operation_summary"]["p50"],
                            "allocation_probes": {before: a.get("allocation_probe"), after: b.get("allocation_probe")},
                            "promotion_decision": "none; independent semantic parity and repeated stable measurements required"})
    for suite in ("diagnostics_enabled", "diagnostics_compiled_out"):
        for reference, candidate in (("effects_disabled", "effects_counters"),
                                     ("effects_disabled", "effects_timings"),
                                     ("effects_disabled", "effects_detail"),
                                     ("probe_runtime_disabled", "probe_enabled")):
            if (suite, reference) in indexed and (suite, candidate) in indexed:
                a, b = indexed[suite, reference], indexed[suite, candidate]
                comparisons.append({"suite": suite, "reference": reference, "candidate": candidate,
                                    "median_ratio": b["ns_per_operation_summary"]["p50"] / a["ns_per_operation_summary"]["p50"],
                                    "allocation_probes": {reference: a.get("allocation_probe"), candidate: b.get("allocation_probe")},
                                    "promotion_decision": "none; mode comparison only"})
    reference, candidate = ("diagnostics_enabled", "probe_enabled"), ("diagnostics_compiled_out", "probe_enabled")
    if reference in indexed and candidate in indexed:
        comparisons.append({"reference_suite": reference[0], "candidate_suite": candidate[0],
                            "case": "probe_enabled", "median_ratio": indexed[candidate]["ns_per_operation_summary"]["p50"] / indexed[reference]["ns_per_operation_summary"]["p50"],
                            "promotion_decision": "none; probe expression feature comparison only"})
    return comparisons


def positive(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new evidence directory (must not exist)")
    parser.add_argument("--suite", action="append", choices=sorted(CASES))
    parser.add_argument("--profile", choices=("bench", "dev"), default="bench")
    parser.add_argument("--toolchain", help="rustup toolchain, e.g. 1.90.0")
    parser.add_argument("--iterations", type=positive, default=20_000)
    parser.add_argument("--samples", type=positive, default=7)
    parser.add_argument("--warmup", type=positive, default=100)
    parser.add_argument("--kyoto", type=Path, help="submit visible cargo commands through kyoto.py")
    parser.add_argument("--command-timeout", type=positive, default=1800,
                        help="queue/execution deadline in seconds for each command (default 1800)")
    parser.add_argument("--compile-only", action="store_true", help="compile benches; never measure")
    parser.add_argument("--baseline", type=Path, help="source-bound measured report to compare; incompatible/noisy evidence fails")
    parser.add_argument("--max-regression-percent", type=float, default=10.0)
    parser.add_argument("--max-cv", type=float, default=0.10, help="maximum sample coefficient of variation for both receipts")
    args = parser.parse_args(argv)
    if args.baseline and args.compile_only:
        parser.error("--baseline requires measurements; compile-only receipts cannot be compared")
    if not math.isfinite(args.max_regression_percent) or args.max_regression_percent < 0 or not math.isfinite(args.max_cv) or args.max_cv <= 0:
        parser.error("comparison thresholds must be finite and positive (regression may be zero)")
    if args.samples < 2:
        parser.error("--samples must be at least two to report variance")
    suites = sorted(set(args.suite or CASES))
    output = args.output or ROOT / "artifacts" / "benchmarks" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    environment = {**select_toolchain(ROOT, args.toolchain, os.environ), "STATELESS_BENCH_FORMAT": "jsonl",
                   "STATELESS_BENCH_ITERS": str(args.iterations),
                   "STATELESS_BENCH_SAMPLES": str(args.samples),
                   "STATELESS_BENCH_WARMUP": str(args.warmup)}
    report = {"schema_version": 1, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "status": "running", "source": source_context(ROOT),
              "environment": environment_context(ROOT, args.toolchain, environment),
              "configuration": {"suites": suites, "profile": args.profile,
                                "toolchain_selector": args.toolchain,
                                "iterations": args.iterations, "samples": args.samples,
                                "warmup_iterations": args.warmup,
                                "compile_only": args.compile_only,
                                "command_timeout_seconds": args.command_timeout,
                                "kyoto": str(args.kyoto.resolve()) if args.kyoto else None,
                                "workload": "fixed fixture matrix; per-case sizes and deterministic seed recorded where applicable",
                                "sample_kind": "within-process warmed batch means"},
              "commands": [], "measurements": [],
              "limitations": ["Percentiles describe batch means, not individual-operation tail latency.",
                              "Compile/queue command time is not benchmark execution time.",
                              "Allocation traffic is measured separately; queue CPU/RSS covers whole Cargo attempts, not individual cases.",
                              "File writes use OS caching and exclude final flush/fsync.",
                              "Optional empirical regression gate refuses incompatible/noisy receipts; no production performance claims."]}

    def save():
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    save()
    try:
        core = [suite for suite in suites if not suite.startswith("diagnostics_")]
        commands = [("compile", cargo_command(core, args.profile, args.toolchain, True))] if core else []
        commands.extend(("compile-" + suite, cargo_command([suite], args.profile, args.toolchain, True))
                        for suite in suites if suite.startswith("diagnostics_"))
        if not args.compile_only:
            commands.extend((suite, cargo_command([suite], args.profile, args.toolchain, False))
                            for suite in suites)
        for name, command in commands:
            print(f"{name}: {' '.join(command)}", flush=True)
            log_path = output / f"{name}.log"
            receipt = execute(command, ROOT, environment, log_path, args.kyoto, args.command_timeout)
            report["commands"].append(receipt)
            save()
            if receipt["exit_code"]:
                raise RuntimeError(f"{name} failed with exit {receipt['exit_code']}; see {log_path}")
            if not name.startswith("compile"):
                report["measurements"].extend(parse_samples(log_path.read_text(), name,
                                                           args.iterations, args.samples, args.warmup))
        report["status"] = "compiled_only" if args.compile_only else "measured"
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["error"] = str(error) or type(error).__name__
    finally:
        report["source_after"] = source_context(ROOT)
        report["source_stable"] = (report["source"]["inputs_sha256"] ==
                                   report["source_after"]["inputs_sha256"])
        if not report["source_stable"]:
            report["status"] = "failed"
            report["error"] = "benchmark source changed during the run; repeat on a stable checkout"
        report["mode_comparisons"] = mode_comparisons(report["measurements"])
        if args.baseline and report["status"] == "measured":
            try:
                baseline = json.loads(args.baseline.read_text())
                comparison = compare_baseline(report, baseline, args.max_regression_percent, args.max_cv)
                report["baseline_comparison"] = comparison
                if comparison["status"] == "regression":
                    report["status"] = "failed"
                    report["error"] = "benchmark regression threshold exceeded"
            except (OSError, ValueError, KeyError, TypeError) as error:
                report["status"] = "failed"
                report["error"] = f"baseline comparison rejected: {error}"
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    print(f"{report['status']}: {report_path}", flush=True)
    return int(report["status"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
