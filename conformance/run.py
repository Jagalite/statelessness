"""Independent, standard-library-only conformance orchestrator.

Each --runner is a JSON array of executable arguments. No shell evaluation.
Example: python conformance/run.py --runner '["python","-m","statelessness.conformance","--runner"]'
The harness imports no library under test and never replaces engine decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
MAX_OUTPUT = 64 * 1024 * 1024


def equivalent(actual, expected):
    # Python equality treats True == 1 and 1.0 == 1. The wire contract does not.
    return json.dumps(actual, sort_keys=True, ensure_ascii=True, allow_nan=False) == json.dumps(expected, sort_keys=True, ensure_ascii=True, allow_nan=False)


def response_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate response key")
            result[key] = value
        return result
    def unsupported(_):
        raise ValueError("floating/non-finite response number")
    return json.loads(raw, object_pairs_hook=pairs, parse_float=unsupported, parse_constant=unsupported)


def invoke(command, requests, timeout):
    """Finite, deadline-bounded process; stdout protocol and stderr stay separate."""
    raw = b"".join((x if isinstance(x, bytes) else json.dumps(x, ensure_ascii=True).encode()) + b"\n" for x in requests)
    # Files prevent pipe deadlock and keep stdout/stderr out of parent RAM until
    # size validation. These caps are audit limits, not an OS process sandbox.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
        try:
            result = subprocess.run(command, input=raw, stdout=output, stderr=diagnostics, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"runner timed out after {timeout}s: {command}") from error
        size = output.tell()
        if size > MAX_OUTPUT:
            raise RuntimeError(f"runner response exceeds {MAX_OUTPUT} bytes")
        diagnostics.seek(0)
        errors = diagnostics.read(8192).decode("utf-8", "replace")
        if result.returncode != 0:
            raise RuntimeError(f"runner exit {result.returncode}: {command}\n{errors}")
        output.seek(0)
        try:
            responses = [response_json(line) for line in output.read().splitlines()]
        except (ValueError, UnicodeError) as error:
            raise RuntimeError(f"runner emitted non-JSON protocol output: {command}") from error
        if len(responses) != len(requests):
            raise RuntimeError(f"expected {len(requests)} responses, got {len(responses)}")
        return responses


def generated_graphs():
    """Every directed topology on 1..3 labeled states: 2 + 16 + 512 cases.

    Expected graph statistics come from independent shortest-distance relaxation,
    not from the engines' queue, deduplication, or table adapter implementation.
    State zero is initial; unreachable components must not be explored.
    """
    for n in range(1, 4):
        for mask in range(1 << (n*n)):
            adjacency = [[j for j in range(n) if mask & (1 << (i*n+j))] for i in range(n)]
            distance = [n+1] * n
            distance[0] = 0
            for _ in range(n):
                previous = distance[:]
                for i in range(n):
                    for j in adjacency[i]:
                        distance[j] = min(distance[j], previous[i]+1)
            reachable = [i for i in range(n) if distance[i] <= n]
            model = {"id": f"graph-{n}-{mask}", "initial": "0", "states": [
                {"id": str(i), "edges": [{"input": str(j), "to": str(j)} for j in adjacency[i]]}
                for i in range(n)]}
            expected = dict(termination="graph_exhausted", states=len(reachable),
                            transitions=sum(len(adjacency[i]) for i in reachable),
                            max_depth_reached=max((distance[i]+1 for i in reachable if adjacency[i]), default=0),
                            skipped_checks=0, failure=None)
            yield {"id": model["id"], "request": {"version": 1, "operation": "enumerate", "model": model,
                   "config": {"max_states": n, "max_transitions": n*n, "max_depth": n+1}}, "expected": expected}


def transport_cases():
    for raw in [b'', b'{', b'null', b'[]', b'{"version":1,"version":1,"operation":"hello"}',
                b'{"version":1.0,"operation":"hello"}', b'{"version":true,"operation":"hello"}',
                b'{"version":1,"operation":"hello","x":NaN}', b'{"version":1,"operation":"hello"} garbage',
                b'{"version":1,"operation":"hello","x":"\\ud800"}',
                b'{"version":1,"operation":"hello","x":"\xff"}',
                b'[' * 70 + b'0' + b']' * 70,
                b' ' * (4*1024*1024 + 1)]:
        yield raw, {"error": "invalid_request"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", action="append", required=True, help="JSON array of executable arguments; repeat for each implementation")
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus.json")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error("timeout must be in (0,3600]")
    commands = [json.loads(x) for x in args.runner]
    if any(not isinstance(c, list) or not c or any(not isinstance(x, str) for x in c) for c in commands):
        parser.error("each --runner must be a nonempty JSON array of strings")
    corpus_bytes = args.corpus.read_bytes()
    corpus = json.loads(corpus_bytes)
    if corpus.get("corpus_version") != 1 or corpus.get("spec_version") != "1.0.0":
        raise ValueError("unsupported corpus version")
    golden = corpus["cases"]
    if not golden or len({x["id"] for x in golden}) != len(golden):
        raise ValueError("corpus must be nonempty with unique IDs")
    generated = list(generated_graphs())
    # Reversing state storage order is a metamorphic change, not input order.
    reordered = []
    for case in generated[::17]:
        changed = json.loads(json.dumps(case))
        changed["id"] += "-reordered"
        changed["request"]["model"]["states"].reverse()
        reordered.append(changed)
    portability = runpy.run_path(str(ROOT / "portability.py"))
    portable_cases = list(portability["cases"]())
    cases = golden + generated + reordered + portable_cases
    transport = list(transport_cases()) + list(portability["transport"]())
    failures, summaries, recordings = [], [], []
    started = time.monotonic()
    for command in commands:
        hello = invoke(command, [dict(version=1, operation="hello")], args.timeout)[0]
        if hello.get("protocol_version") != 1 or hello.get("spec_version") != corpus["spec_version"]:
            raise RuntimeError(f"unsupported runner protocol/specification: {hello}")
        if not set(corpus["profiles"]) <= set(hello.get("profiles", [])):
            raise RuntimeError(f"missing required profile: {hello}")
        actual = invoke(command, [c["request"] for c in cases], args.timeout)
        local_failures = []
        for case, response in zip(cases, actual):
            if not equivalent(response, case["expected"]):
                local_failures.append(dict(id=case["id"], expected=case["expected"], actual=response))
        raw_results = invoke(command, [r for r, _ in transport] + [dict(version=1, operation="hello")], args.timeout)
        for i, ((_, expected), response) in enumerate(zip(transport, raw_results)):
            if not equivalent(response, expected):
                local_failures.append(dict(id=f"transport-{i}", expected=expected, actual=response))
        if not equivalent(raw_results[-1], hello):
            local_failures.append(dict(id="runner-recovery-after-malformed-input", actual=raw_results[-1]))
        records = [(case, response["trace"]) for case, response in zip(cases, actual)
                   if case["request"]["operation"] == "record" and "trace" in response]
        recordings.append((hello["implementation"], records))
        summaries.append(dict(**hello, command=command, golden_cases=len(golden), generated_graphs=len(generated),
                              metamorphic_cases=len(reordered), portability_cases=len(portable_cases), transport_cases=len(transport), failures=len(local_failures)))
        failures.extend(dict(implementation=hello["implementation"], **f) for f in local_failures)
    cross_count = 0
    # Fresh process for every producer/consumer pairing. Only fixture models share
    # codecs and model identities; arbitrary different model builds are not waived.
    for producer, records in recordings:
        for summary, command in zip(summaries, commands):
            requests, expected = [], []
            for case, trace in records:
                requests.append(dict(version=1, operation="replay", model=case["request"]["model"], trace=trace, allow_build_mismatch=False))
                expected.append(dict(outcome="exact", steps_verified=len(trace["steps"]), failure_reproduced=trace["termination"] == "property_failed", build_matches=True, step=None, field=None))
            actual = invoke(command, requests, args.timeout)
            for (case, _), response, wanted in zip(records, actual, expected):
                cross_count += 1
                if not equivalent(response, wanted):
                    failures.append(dict(id=case["id"], producer=producer, consumer=summary["implementation"], expected=wanted, actual=response))
    result = dict(spec_version=corpus["spec_version"], corpus_sha256=hashlib.sha256(corpus_bytes).hexdigest(),
                  portability_sha256=hashlib.sha256((ROOT / "portability.py").read_bytes()).hexdigest(),
                  source_commit=os.environ.get("GITHUB_SHA"), implementations=summaries,
                  fresh_process_replays=cross_count, elapsed_seconds=round(time.monotonic()-started, 3),
                  failures=failures)
    rendered = json.dumps(result, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
