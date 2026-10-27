"""Independent, standard-library-only conformance orchestrator.

Each --runner is a JSON array of executable arguments. No shell evaluation.
Example: python conformance/run.py --runner '["python","-m","statelessness.conformance","--runner"]'
The harness imports no library under test and never replaces engine decisions.
CLI runs retain exact invocation streams in --artifact-dir (a fresh directory),
or a unique directory beside --report / in the system temporary directory.
One JSONL journal references shared SHA-256 blobs; the report locates it.
Without --report, report.json is also saved inside the artifact directory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
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


class InvocationError(RuntimeError):
    def __init__(self, message, receipt):
        super().__init__(f"{message}\ninvocation receipt: {receipt['directory']}/{receipt['journal']}#{receipt['id']}")
        self.receipt = receipt


class InvocationReceipts:
    """One append-only journal and deduplicated, lossless binary streams per run."""

    def __init__(self, directory=None, *, parent=None):
        if directory is None:
            self.directory = Path(tempfile.mkdtemp(prefix="conformance-receipts-", dir=parent)).resolve()
        else:
            self.directory = Path(directory).resolve()
            # Never replace an earlier attempt's receipts.
            self.directory.mkdir(parents=True, exist_ok=False)
        self.blobs = self.directory / "blobs"
        self.blobs.mkdir()
        self.journal = self.directory / "invocations.jsonl"
        self.journal.touch(exist_ok=False)
        self.count = 0

    def _blob(self, stream):
        stream.seek(0)
        digest, size = hashlib.sha256(), 0
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.blobs, delete=False) as target:
                temporary = Path(target.name)
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    target.write(chunk)
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        destination = self.blobs / digest.hexdigest()
        if destination.exists():
            temporary.unlink()
        else:
            temporary.replace(destination)
        return dict(sha256=digest.hexdigest(), bytes=size,
                    path=destination.relative_to(self.directory).as_posix())

    def record(self, metadata, raw, output, diagnostics):
        streams = {"input": self._blob(io.BytesIO(raw)), "stdout": self._blob(output),
                   "stderr": self._blob(diagnostics)}
        invocation_id = self.count + 1
        entry = dict(metadata, id=invocation_id, streams=streams)
        with self.journal.open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=True) + "\n")
        self.count = invocation_id
        return dict(directory=str(self.directory), journal=self.journal.name, id=invocation_id)

    def summary(self):
        return dict(format="conformance-invocations-v1", directory=str(self.directory),
                    journal=self.journal.name, invocations=self.count,
                    journal_sha256=hashlib.sha256(self.journal.read_bytes()).hexdigest())


def invoke(command, requests, timeout, *, receipts=None, context=None):
    """Return decoded responses; failures raise InvocationError with a receipt.

    Pass one InvocationReceipts store to retain every invocation. Without one,
    successful calls keep their legacy no-artifact behavior; failures create a
    persistent temporary store identified by the exception's receipt attribute.
    """
    raw = b"".join((x if isinstance(x, bytes) else json.dumps(x, ensure_ascii=True).encode()) + b"\n" for x in requests)
    # Files prevent pipe deadlock and keep stdout/stderr out of parent RAM until
    # size validation. Retention is uncapped, including oversized/invalid output.
    # These caps are audit limits, not an OS process sandbox.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
        started = time.monotonic()
        process = None
        metadata = dict(command=[os.fspath(x) for x in command], context=context,
                        timeout_seconds=timeout, status="exception", timed_out=False,
                        termination_requested=None, input_submitted=False,
                        input_delivery="planned bytes; input_submitted marks communicate called, not child consumption")

        def retain(store):
            returncode = process.returncode if process is not None else None
            metadata.update(process_started=process is not None,
                            pid=process.pid if process is not None else None,
                            returncode=returncode,
                            signal=-returncode if returncode is not None and returncode < 0 else None,
                            elapsed_seconds=round(time.monotonic() - started, 6))
            return store.record(metadata, raw, output, diagnostics)

        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=diagnostics)
            metadata["input_submitted"] = True
            process.communicate(input=raw, timeout=timeout)
            if output.tell() > MAX_OUTPUT:
                metadata["status"] = "output_limit"
                raise RuntimeError(f"runner response exceeds {MAX_OUTPUT} bytes")
            if process.returncode != 0:
                metadata["status"] = "nonzero_exit"
                diagnostics.seek(0)
                errors = diagnostics.read(8192).decode("utf-8", "replace")
                raise RuntimeError(f"runner exit {process.returncode}: {command}\n{errors}")
            output.seek(0)
            try:
                responses = [response_json(line) for line in output.read().splitlines()]
            except (ValueError, UnicodeError) as error:
                metadata["status"] = "malformed_response"
                raise RuntimeError(f"runner emitted non-JSON protocol output: {command}") from error
            if len(responses) != len(requests):
                metadata["status"] = "response_count"
                raise RuntimeError(f"expected {len(requests)} responses, got {len(responses)}")
        except BaseException as error:
            if isinstance(error, subprocess.TimeoutExpired):
                metadata.update(status="timeout", timed_out=True)
                message = f"runner timed out after {timeout}s: {command}"
            else:
                if process is None:
                    metadata["status"] = "spawn_error"
                message = str(error)
            # Reap the direct child before snapshotting file-backed streams.
            if process is not None:
                try:
                    if process.poll() is None:
                        metadata["termination_requested"] = "kill"
                        process.kill()
                    process.wait()
                finally:
                    if process.stdin is not None:
                        try:
                            process.stdin.close()
                        except OSError as close_error:
                            metadata["stdin_close_error"] = repr(close_error)
            metadata["error"] = dict(type=type(error).__name__, message=message)
            reference = retain(receipts if receipts is not None else InvocationReceipts())
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise InvocationError(message, reference) from error
        metadata["status"] = "passed"
        if receipts is not None:
            retain(receipts)
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


def run_suite(commands, corpus_path, timeout, receipts, result):
    """Update the aggregate as comparisons complete, including before an abort."""
    corpus_bytes = corpus_path.read_bytes()
    result["corpus_sha256"] = hashlib.sha256(corpus_bytes).hexdigest()
    corpus = json.loads(corpus_bytes)
    if corpus.get("corpus_version") != 1 or corpus.get("spec_version") != "1.0.0":
        raise ValueError("unsupported corpus version")
    golden = corpus["cases"]
    if not golden or len({x["id"] for x in golden}) != len(golden):
        raise ValueError("corpus must be nonempty with unique IDs")
    result["spec_version"] = corpus["spec_version"]
    generated = list(generated_graphs())
    # Reversing state storage order is a metamorphic change, not input order.
    reordered = []
    for case in generated[::17]:
        changed = json.loads(json.dumps(case))
        changed["id"] += "-reordered"
        changed["request"]["model"]["states"].reverse()
        reordered.append(changed)
    result["portability_sha256"] = hashlib.sha256((ROOT / "portability.py").read_bytes()).hexdigest()
    portability = runpy.run_path(str(ROOT / "portability.py"))
    portable_cases = list(portability["cases"]())
    cases = golden + generated + reordered + portable_cases
    transport = list(transport_cases()) + list(portability["transport"]())
    failures, summaries, recordings = result["failures"], result["implementations"], []
    result["planned_scope"] = dict(runners=len(commands), cases_per_runner=len(cases),
                                  transport_cases_per_runner=len(transport), fresh_process_pairs=len(commands)**2)

    def call(command, requests, **context):
        result["active_invocation"] = context
        return invoke(command, requests, timeout, receipts=receipts, context=context)

    for runner_index, command in enumerate(commands):
        hello = call(command, [dict(version=1, operation="hello")], phase="hello", runner_index=runner_index)[0]
        if not isinstance(hello, dict) or not isinstance(hello.get("implementation"), str):
            raise RuntimeError(f"invalid runner hello: {hello}")
        if "engine_family" in hello and not isinstance(hello["engine_family"], str):
            raise RuntimeError(f"invalid runner engine family: {hello}")
        if hello.get("protocol_version") != 1 or hello.get("spec_version") != corpus["spec_version"]:
            raise RuntimeError(f"unsupported runner protocol/specification: {hello}")
        if not set(corpus["profiles"]) <= set(hello.get("profiles", [])):
            raise RuntimeError(f"missing required profile: {hello}")
        attributed_hello = dict(hello)
        attributed_hello.setdefault("engine_family", {"rust": "rust-shared",
                         "python": "python-independent", "go": "go-independent",
                         "swift": "swift-independent", "typescript": "typescript-independent"}
                         .get(hello["implementation"], "unattributed"))
        summary = dict(**attributed_hello, command=command, golden_cases=len(golden), generated_graphs=len(generated),
                       metamorphic_cases=len(reordered), portability_cases=len(portable_cases),
                       transport_cases=len(transport), failures=0, status="running", completed_phases=["hello"],
                       completed_cases=0, completed_transport_cases=0)
        summaries.append(summary)

        def mismatch(**failure):
            failures.append(dict(implementation=hello["implementation"], **failure))
            summary["failures"] += 1

        actual = call(command, [c["request"] for c in cases], phase="cases", runner_index=runner_index)
        for case, response in zip(cases, actual):
            if not equivalent(response, case["expected"]):
                mismatch(id=case["id"], expected=case["expected"], actual=response)
            summary["completed_cases"] += 1
        summary["completed_phases"].append("cases")
        raw_results = call(command, [r for r, _ in transport] + [dict(version=1, operation="hello")],
                           phase="transport", runner_index=runner_index)
        for i, ((_, expected), response) in enumerate(zip(transport, raw_results)):
            if not equivalent(response, expected):
                mismatch(id=f"transport-{i}", expected=expected, actual=response)
            summary["completed_transport_cases"] += 1
        if not equivalent(raw_results[-1], hello):
            mismatch(id="runner-recovery-after-malformed-input", actual=raw_results[-1])
        summary["completed_phases"].append("transport")
        records = [(case, response["trace"]) for case, response in zip(cases, actual)
                   if case["request"]["operation"] == "record" and "trace" in response]
        recordings.append((hello["implementation"], records))
        summary["status"] = "failed" if summary["failures"] else "passed"
    # Fresh process for every producer/consumer pairing. Only fixture models share
    # codecs and model identities; arbitrary different model builds are not waived.
    for producer, records in recordings:
        for runner_index, (summary, command) in enumerate(zip(summaries, commands)):
            requests, expected = [], []
            for case, trace in records:
                requests.append(dict(version=1, operation="replay", model=case["request"]["model"], trace=trace, allow_build_mismatch=False))
                expected.append(dict(outcome="exact", steps_verified=len(trace["steps"]), failure_reproduced=trace["termination"] == "property_failed", build_matches=True, step=None, field=None))
            actual = call(command, requests, phase="replay", runner_index=runner_index,
                          producer=producer, consumer=summary["implementation"])
            for (case, _), response, wanted in zip(records, actual, expected):
                result["fresh_process_replays"] += 1
                if not equivalent(response, wanted):
                    failures.append(dict(id=case["id"], producer=producer, consumer=summary["implementation"], expected=wanted, actual=response))
            result["completed_replay_pairs"] += 1
    result["active_invocation"] = None
    result["complete"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", action="append", required=True, help="JSON array of executable arguments; repeat for each implementation")
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus.json")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--artifact-dir", type=Path, help="new directory for exact streams and invocation journal; defaults to a unique directory beside --report, otherwise system temp")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 3600:
        parser.error("timeout must be in (0,3600]")
    commands = [json.loads(x) for x in args.runner]
    if any(not isinstance(c, list) or not c or any(not isinstance(x, str) for x in c) for c in commands):
        parser.error("each --runner must be a nonempty JSON array of strings")
    if args.report and args.artifact_dir is None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
    receipts = InvocationReceipts(args.artifact_dir, parent=args.report.parent if args.report else None)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
    result = dict(source_commit=os.environ.get("GITHUB_SHA"), implementations=[], failures=[],
                  fresh_process_replays=0, completed_replay_pairs=0, complete=False,
                  engine_attribution="runner-declared; unknown runners are not counted as independent")
    started = time.monotonic()
    interrupted = None
    try:
        run_suite(commands, args.corpus, args.timeout, receipts, result)
    except BaseException as error:
        failure = dict(id="harness-error", type=type(error).__name__, message=str(error),
                       context=result.get("active_invocation"))
        if isinstance(error, InvocationError):
            failure["receipt"] = error.receipt
        result["failures"].append(failure)
        for summary in result["implementations"]:
            if summary["status"] == "running":
                summary["status"] = "aborted"
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            interrupted = error
    result.update(status="failed" if result["failures"] else "passed", receipts=receipts.summary(),
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  engine_families=sorted({s["engine_family"] for s in result["implementations"]}),
                  independent_engine_count=len({s["engine_family"] for s in result["implementations"]} - {"unattributed"}))
    rendered = json.dumps(result, indent=2)
    report_path = args.report if args.report else receipts.directory / "report.json"
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(rendered + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print(rendered)
    if interrupted is not None:
        raise interrupted
    return int(bool(result["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
