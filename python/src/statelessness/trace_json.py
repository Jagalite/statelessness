"""Strict JSON observation envelopes (not Rust's binary .sttrace audit format).

The format retains exact model/codec/build identities and checkpoint bytes.
It is neither an authenticated artifact nor a lossless .sttrace converter.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from typing import Any

from .core import Check, Disposition, Metadata, Trace, TraceLimits, TraceStep


class FormatError(ValueError):
    """Malformed, oversized, or unsupported portable JSON."""


def object_fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict or set(value) - required - (optional or set()) or required - set(value):
        raise FormatError(f"expected fields {sorted(required)}, optional {sorted(optional or set())}")
    return value


def string(value: Any) -> str:
    if type(value) is not str:
        raise FormatError("expected string")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as error:
        raise FormatError("invalid Unicode scalar value") from error
    return value


def integer(value: Any, minimum: int = 0, maximum: int = (1 << 53) - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FormatError(f"expected integer in {minimum}..{maximum}")
    return value


def boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise FormatError("expected boolean")
    return value


def array(value: Any, maximum: int = 250_000) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        raise FormatError("expected bounded array")
    return value


def strict_loads(data: str | bytes, maximum: int = 64 * 1024 * 1024, *, allow_floats: bool = False) -> Any:
    """UTF-8 JSON with unique keys, scalar strings, integer numbers, depth <=64.

    allow_floats is for corpus containers with deliberately invalid requests.
    The input byte cap is checked before JSON parsing. As with the native parser,
    nesting is checked after parsing; this is not an untrusted-code sandbox.
    """
    try:
        raw = data.encode("utf-8", "strict") if isinstance(data, str) else data
        if type(raw) is not bytes or len(raw) > maximum:
            raise FormatError("JSON byte limit exceeded")
        text = raw.decode("utf-8", "strict")
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for key, value in items:
                if key in result:
                    raise FormatError("duplicate object key")
                result[key] = value
            return result
        def constant(_: str) -> Any:
            raise FormatError("non-finite number")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        def validate(node: Any, depth: int = 0) -> None:
            if depth > 64:
                raise FormatError("JSON nesting limit exceeded")
            if isinstance(node, str):
                string(node)
            elif type(node) is dict:
                for key, child in node.items():
                    string(key)
                    validate(child, depth + 1)
            elif type(node) is list:
                for child in node:
                    validate(child, depth + 1)
            elif type(node) is float and (not allow_floats or not math.isfinite(node)):
                raise FormatError("floating-point values are outside the portable profile")
        validate(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise FormatError(f"invalid JSON: {error}") from error


def check_from_data(value: Any) -> Check:
    obj = object_fields(value, {"id", "status"}, {"details"})
    return Check(string(obj["id"]), string(obj["status"]), string(obj.get("details", "")))


def disposition_from_data(value: Any) -> Disposition:
    obj = object_fields(value, {"kind"}, {"reason"})
    return Disposition(string(obj["kind"]), string(obj.get("reason", "")))


def metadata_from_data(value: Any) -> Metadata:
    obj = object_fields(value, {"name", "model_version", "properties_version", "codec_version", "build"})
    return Metadata(string(obj["name"]), integer(obj["model_version"], maximum=(1 << 32) - 1),
                    integer(obj["properties_version"], maximum=(1 << 32) - 1),
                    integer(obj["codec_version"], maximum=(1 << 32) - 1), string(obj["build"]))


def trace_to_data(trace: Trace) -> dict[str, Any]:
    return {"format": "stateless.trace-json", "version": 1,
            "metadata": asdict(trace.metadata), "initial_state": trace.initial_state.hex(),
            "initial_checks": [asdict(c) for c in trace.initial_checks],
            "steps": [{"input": step.input.hex(), "disposition": asdict(step.disposition),
                       "outputs": [o.hex() for o in step.outputs], "post_state": step.post_state.hex(),
                       "checks": [asdict(c) for c in step.checks]} for step in trace.steps],
            "termination": trace.termination, "error": trace.error}


def trace_from_data(value: Any, limits: TraceLimits = TraceLimits()) -> Trace:
    obj = object_fields(value, {"format", "version", "metadata", "initial_state", "initial_checks", "steps", "termination", "error"})
    if obj["format"] != "stateless.trace-json" or type(obj["version"]) is not int or obj["version"] != 1:
        raise FormatError("unsupported trace format/version")
    items = payload = 0
    def checks(values: Any) -> tuple[Check, ...]:
        nonlocal items
        values = array(values, limits.max_items)
        items += len(values)
        if items > limits.max_items:
            raise FormatError("aggregate trace item limit exceeded")
        return tuple(check_from_data(c) for c in values)
    def blob(value: Any) -> bytes:
        nonlocal payload
        text = string(value)
        if len(text) > limits.max_blob_bytes * 2 or len(text) % 2 or re.fullmatch(r"[0-9a-f]*", text) is None:
            raise FormatError("expected bounded lowercase hexadecimal bytes")
        payload += len(text) // 2
        if payload > limits.max_payload_bytes:
            raise FormatError("aggregate payload byte limit exceeded")
        return bytes.fromhex(text)
    initial = blob(obj["initial_state"])
    initial_checks = checks(obj["initial_checks"])
    steps = []
    for value in array(obj["steps"], limits.max_steps):
        step = object_fields(value, {"input", "disposition", "outputs", "post_state", "checks"})
        values = array(step["outputs"], limits.max_items)
        items += 1 + len(values)
        if items > limits.max_items:
            raise FormatError("aggregate trace item limit exceeded")
        outputs = tuple(blob(o) for o in values)
        steps.append(TraceStep(blob(step["input"]), disposition_from_data(step["disposition"]),
                               outputs, blob(step["post_state"]), checks(step["checks"])))
    termination, error = string(obj["termination"]), string(obj["error"])
    if termination not in ("completed", "property_failed", "step_limit", "interrupted", "model_error"):
        raise FormatError("unknown trace termination")
    if error and termination != "model_error":
        raise FormatError("error text requires model_error termination")
    return Trace(metadata_from_data(obj["metadata"]), initial, initial_checks, tuple(steps), termination, error)


def dumps(trace: Trace, *, limits: TraceLimits = TraceLimits()) -> str:
    data = trace_to_data(trace)
    trace_from_data(data, limits)
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limits.max_json_bytes:
        raise FormatError("JSON byte limit exceeded")
    return encoded


def loads(data: str | bytes, *, limits: TraceLimits = TraceLimits()) -> Trace:
    return trace_from_data(strict_loads(data, limits.max_json_bytes), limits)
