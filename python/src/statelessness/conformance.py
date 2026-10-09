"""JSON table-model adapter over the native Python library.

python -m statelessness.conformance --runner  # one JSON response per input line
python -m statelessness.conformance conformance/corpus.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import PROFILES, SPEC_VERSION, __version__
from .core import (CheckPolicy, Metadata, ModelError, Rng, SearchConfig, Transition,
                   check_observed, enumerate_states, record, replay)
from .trace_json import (FormatError, array, boolean, check_from_data, disposition_from_data,
                         integer, metadata_from_data, object_fields, strict_loads, string,
                         trace_from_data, trace_to_data)

MAX_LINE = 4 * 1024 * 1024
MAX_WORK = 100_000


@dataclass(frozen=True)
class State:
    id: str

    def __hash__(self) -> int:
        # Exercise collision resolution in every portable BFS fixture.
        return 0


class TableModel:
    """Finite table fixture; this adapter never implements search or replay."""
    def __init__(self, value: Any):
        obj = object_fields(value, {"id", "initial", "states"}, {"metadata", "initial_error"})
        self.id, self.initial = string(obj["id"]), string(obj["initial"])
        self.identity = metadata_from_data(obj["metadata"]) if "metadata" in obj else Metadata(self.id, build="fixture-v1")
        self.initial_error = boolean(obj.get("initial_error", False))
        self.states: dict[str, dict[str, Any]] = {}
        self.step_calls = 0
        edge_count = 0
        for value in array(obj["states"], 1_000):
            row = object_fields(value, {"id"}, {"checks", "edges", "check_error", "inputs_error"})
            name = string(row["id"])
            if name in self.states:
                raise FormatError("duplicate state ID")
            checks = tuple(check_from_data(c) for c in array(row.get("checks", []), 4096))
            edges, seen = [], {}
            for value in array(row.get("edges", []), 10_000):
                edge = object_fields(value, {"input", "to"}, {"outputs", "disposition", "checks", "step_error", "check_error"})
                item = {"input": string(edge["input"]), "to": string(edge["to"]),
                        "outputs": tuple(string(o) for o in array(edge.get("outputs", []), 4096)),
                        "disposition": disposition_from_data(edge.get("disposition", {"kind": "accepted"})),
                        "checks": tuple(check_from_data(c) for c in array(edge.get("checks", []), 4096)),
                        "step_error": boolean(edge.get("step_error", False)),
                        "check_error": boolean(edge.get("check_error", False))}
                if item["input"] in seen and seen[item["input"]] != item:
                    raise FormatError("conflicting transitions for the same state/input")
                seen[item["input"]] = item
                edges.append(item)
                edge_count += 1
            self.states[name] = {"checks": checks, "edges": edges,
                                 "check_error": boolean(row.get("check_error", False)),
                                 "inputs_error": boolean(row.get("inputs_error", False))}
        if edge_count > 10_000 or self.initial not in self.states:
            raise FormatError("invalid model size or initial state")
        if any(e["to"] not in self.states for s in self.states.values() for e in s["edges"]):
            raise FormatError("unknown transition target")

    def metadata(self) -> Metadata:
        return self.identity

    def initial_state(self) -> State:
        if self.initial_error:
            raise ModelError("injected initial_state")
        return State(self.initial)

    def _row(self, state: State) -> dict[str, Any]:
        if state.id not in self.states:
            raise ModelError("unknown state")
        return self.states[state.id]

    def _edge(self, state: State, input: str) -> dict[str, Any]:
        for edge in self._row(state)["edges"]:
            if edge["input"] == input:
                return edge
        raise ModelError("input is not in this state's domain")

    def inputs(self, state: State) -> list[str]:
        row = self._row(state)
        if row["inputs_error"]:
            raise ModelError("injected inputs")
        return [edge["input"] for edge in row["edges"]]

    def step(self, state: State, input: str) -> Transition[State, str]:
        self.step_calls += 1
        edge = self._edge(state, input)
        if edge["step_error"]:
            raise ModelError("injected step")
        return Transition(State(edge["to"]), edge["outputs"], edge["disposition"])

    def check_state(self, state: State):
        row = self._row(state)
        if row["check_error"]:
            raise ModelError("injected check_state")
        return row["checks"]

    def check_transition(self, before: State, input: str, transition: Transition[State, str]):
        edge = self._edge(before, input)
        if edge["check_error"]:
            raise ModelError("injected check_transition")
        return edge["checks"]

    def encode_state(self, state: State) -> bytes:
        self._row(state)
        return state.id.encode("utf-8")

    def decode_state(self, data: bytes) -> State:
        state = State(data.decode("utf-8", "strict"))
        self._row(state)
        return state

    def encode_input(self, input: str) -> bytes:
        return input.encode("utf-8")

    def decode_input(self, data: bytes) -> str:
        return data.decode("utf-8", "strict")

    def encode_output(self, output: str) -> bytes:
        return output.encode("utf-8")


def u64(value: Any) -> int:
    text = string(value)
    if re.fullmatch(r"0|[1-9][0-9]{0,19}", text) is None or int(text) >= 1 << 64:
        raise FormatError("expected canonical decimal u64 string")
    return int(text)


def execute(request: Any) -> dict[str, Any]:
    if type(request) is not dict or "version" not in request:
        raise FormatError("request needs version")
    integer(request["version"])
    if request["version"] != 1:
        return {"error": "unsupported_version"}
    operation = string(request.get("operation"))
    base = {"version", "operation"}
    if operation == "hello":
        object_fields(request, base)
        return {"implementation": "python", "package_version": __version__, "spec_version": SPEC_VERSION,
                "profiles": list(PROFILES), "protocol_version": 1}
    if operation == "rng":
        object_fields(request, base | {"seed", "draws", "bounds"})
        rng = Rng(u64(request["seed"]))
        draws = integer(request["draws"], maximum=10_000)
        bounds = [u64(x) for x in array(request["bounds"], 10_000)]
        raw = [str(rng.next_u64()) for _ in range(draws)]
        indices = [rng.index(upper) for upper in bounds]
        return {"raw": raw, "indices": [None if x is None else str(x) for x in indices],
                "next": str(rng.next_u64())}
    if operation not in ("enumerate", "record", "observe", "replay"):
        return {"error": "unsupported_operation"}
    required = base | {"model"}
    if operation == "enumerate":
        required |= {"config"}
    elif operation == "record":
        required |= {"inputs", "max_steps"}
    elif operation == "observe":
        required |= {"before", "input", "transition", "sequence", "policy"}
    else:
        required |= {"trace", "allow_build_mismatch"}
    object_fields(request, required)
    model = TableModel(request["model"])
    if operation == "enumerate":
        config = object_fields(request["config"], {"max_states", "max_transitions", "max_depth"})
        values = {name: integer(value, maximum=MAX_WORK) for name, value in config.items()}
        if values["max_states"] == 0:
            return {"error": "invalid_config"}
        return asdict(enumerate_states(model, SearchConfig(**values)))
    if operation == "record":
        inputs = [string(x) for x in array(request["inputs"], MAX_WORK)]
        maximum = integer(request["max_steps"], maximum=MAX_WORK)
        return {"trace": trace_to_data(record(model, inputs, maximum))}
    if operation == "observe":
        before, input = State(string(request["before"])), string(request["input"])
        observed = object_fields(request["transition"], {"state", "outputs", "disposition"})
        transition = Transition(State(string(observed["state"])),
                                tuple(string(x) for x in array(observed["outputs"], 4096)),
                                disposition_from_data(observed["disposition"]))
        policy = object_fields(request["policy"], {"state_every", "transition_checks"})
        sequence = integer(request["sequence"], maximum=MAX_WORK)
        every = integer(policy["state_every"], maximum=MAX_WORK)
        enabled = boolean(policy["transition_checks"])
        if sequence == 0 or every == 0:
            return {"error": "invalid_config"}
        checks = check_observed(model, before, input, transition, sequence, CheckPolicy(every, enabled))
        return {"checks": [asdict(c) for c in checks], "step_calls": model.step_calls}
    trace = trace_from_data(request["trace"])
    allow = boolean(request["allow_build_mismatch"])
    return asdict(replay(model, trace, allow_build_mismatch=allow))


def respond(request: Any) -> dict[str, Any]:
    try:
        return execute(request)
    except ModelError as error:
        print(str(error), file=sys.stderr)
        return {"error": "model_error"}
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return {"error": "invalid_request"}


def raw_response(raw: bytes) -> dict[str, Any]:
    try:
        return respond(strict_loads(raw, MAX_LINE))
    except (ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return {"error": "invalid_request"}


def run_jsonl() -> None:
    stream = sys.stdin.buffer
    while True:
        raw = stream.readline(MAX_LINE + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE:
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(MAX_LINE + 1)
            response = {"error": "invalid_request"}
        else:
            response = raw_response(raw)
        print(json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", nargs="?", type=Path)
    parser.add_argument("--runner", action="store_true")
    args = parser.parse_args()
    if args.runner:
        if args.corpus:
            parser.error("--runner cannot take a corpus")
        run_jsonl()
        return 0
    if not args.corpus:
        parser.error("provide a corpus JSON file or --runner")
    data = strict_loads(args.corpus.read_bytes(), allow_floats=True)
    object_fields(data, {"corpus_version", "spec_version", "profiles", "cases"})
    if data["corpus_version"] != 1 or type(data["corpus_version"]) is not int or data["spec_version"] != SPEC_VERSION:
        raise FormatError("unsupported corpus/specification version")
    if not set(array(data["profiles"])) <= set(PROFILES):
        raise FormatError("unsupported required profile")
    cases = array(data["cases"])
    if not cases:
        raise FormatError("empty corpus is not qualification")
    failures, seen = [], set()
    for case in cases:
        object_fields(case, {"id", "rules", "request", "expected"})
        name = string(case["id"])
        if name in seen:
            raise FormatError("duplicate case ID")
        seen.add(name)
        actual = raw_response(json.dumps(case["request"], ensure_ascii=True, allow_nan=False).encode())
        # Canonical comparison ignores only object-key order, never value types.
        if json.dumps(actual, sort_keys=True) != json.dumps(case["expected"], sort_keys=True):
            failures.append({"id": name, "expected": case["expected"], "actual": actual})
    print(json.dumps({"implementation": "python", "cases": len(cases), "passed": len(cases) - len(failures),
                      "failures": failures}, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
