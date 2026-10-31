"""Strict JSON observation envelopes (not Rust's binary .sttrace audit format).

The format retains exact model/codec/build identities and checkpoint bytes.
It is neither an authenticated artifact nor a lossless .sttrace converter.
"""
from __future__ import annotations

import json
import math
import re
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
        # Every Unicode code point needs at least one UTF-8 byte. Reject an
        # obviously oversized string before allocating its encoded copy.
        if isinstance(data, str) and str.__len__(data) > maximum:
            raise FormatError("JSON byte limit exceeded")
        # Preserve the supplied text even when a str subclass overrides encode.
        # The built-in descriptor also keeps admission and parsing on the same
        # value rather than permitting application code to substitute bytes.
        raw = str.encode(data, "utf-8", "strict") if isinstance(data, str) else data
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
    except FormatError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
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
    def hex_blob(blob: bytes) -> str:
        # Do not dispatch an overridable .hex method after the size check.
        if isinstance(blob, bytes):
            return bytes.hex(blob)
        if type(blob) is bytearray:
            return bytearray.hex(blob)
        if type(blob) is memoryview:
            return memoryview.hex(blob)
        raise FormatError("trace blob must be bytes-like")
    # Construct the schema directly: dataclasses.asdict deep-copies arbitrary
    # field values before validation and can invoke application __deepcopy__.
    def check_data(check: Check) -> dict[str, Any]:
        return {"id": check.id, "status": check.status, "details": check.details}
    def disposition_data(disposition: Disposition) -> dict[str, Any]:
        return {"kind": disposition.kind, "reason": disposition.reason}
    metadata = trace.metadata
    return {"format": "stateless.trace-json", "version": 1,
            "metadata": {"name": metadata.name, "model_version": metadata.model_version,
                         "properties_version": metadata.properties_version,
                         "codec_version": metadata.codec_version, "build": metadata.build},
            "initial_state": hex_blob(trace.initial_state),
            "initial_checks": [check_data(c) for c in trace.initial_checks],
            "steps": [{"input": hex_blob(step.input), "disposition": disposition_data(step.disposition),
                       "outputs": [hex_blob(o) for o in step.outputs], "post_state": hex_blob(step.post_state),
                       "checks": [check_data(c) for c in step.checks]} for step in trace.steps],
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
    """Encode an envelope, preflighting payload limits before hex expansion.

    Mutable bytearray and memoryview blobs must remain stable during this call.
    A lower bound on JSON size is checked before the tree or hex strings are
    built. Escaping and structural overhead are checked exactly after rendering;
    intermediate allocations are proportional to the configured limit, not a
    bound on caller-owned data or process memory. If multiple fields are invalid,
    a size limit can be reported before a semantic validation error.
    """
    if len(trace.steps) > limits.max_steps:
        raise FormatError("trace step limit exceeded")
    items = len(trace.initial_checks)
    if items > limits.max_items:
        raise FormatError("aggregate trace item limit exceeded")
    payload = 0
    json_lower_bound = 0
    def add_json_bytes(count: int) -> None:
        nonlocal json_lower_bound
        json_lower_bound += count
        if json_lower_bound > limits.max_json_bytes:
            raise FormatError("JSON byte limit exceeded")
    def text_lower_bound(value: Any) -> None:
        # Reject invalid types before constructing the tree; in particular a
        # str subclass must not reach a potentially unbounded copy callback.
        # Each valid UTF-8 byte requires at least one ASCII
        # byte in ensure_ascii JSON. Chunking avoids a full temporary encoding.
        if type(value) is not str:
            raise FormatError("expected string")
        remaining = limits.max_json_bytes - json_lower_bound
        if len(value) > remaining:
            raise FormatError("JSON byte limit exceeded")
        try:
            for start in range(0, len(value), 4096):
                add_json_bytes(len(value[start:start + 4096].encode("utf-8", "strict")))
        except UnicodeError as error:
            raise FormatError("invalid Unicode scalar value") from error
    def check_text(check: Check) -> None:
        text_lower_bound(check.id)
        text_lower_bound(check.status)
        text_lower_bound(check.details)
    # Every check, step and output contributes structural JSON bytes even when
    # its textual fields or byte payload are empty.
    add_json_bytes(items)
    text_lower_bound(trace.metadata.name)
    text_lower_bound(trace.metadata.build)
    text_lower_bound(trace.termination)
    text_lower_bound(trace.error)
    for check in trace.initial_checks:
        check_text(check)
    def preflight(blob: bytes) -> None:
        nonlocal payload
        if isinstance(blob, bytes): count = bytes.__len__(blob)
        elif type(blob) is bytearray: count = bytearray.__len__(blob)
        elif type(blob) is memoryview: count = blob.nbytes
        else: raise FormatError("trace blob must be bytes-like")
        if count > limits.max_blob_bytes:
            raise FormatError("trace blob byte limit exceeded")
        payload += count
        if payload > limits.max_payload_bytes:
            raise FormatError("aggregate payload byte limit exceeded")
        add_json_bytes(count * 2)
    preflight(trace.initial_state)
    for step in trace.steps:
        step_items = 1 + len(step.outputs) + len(step.checks)
        items += step_items
        if items > limits.max_items:
            raise FormatError("aggregate trace item limit exceeded")
        add_json_bytes(step_items)
        text_lower_bound(step.disposition.kind)
        text_lower_bound(step.disposition.reason)
        for check in step.checks:
            check_text(check)
        preflight(step.input)
        for output in step.outputs:
            preflight(output)
        preflight(step.post_state)
    data = trace_to_data(trace)
    trace_from_data(data, limits)
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limits.max_json_bytes:
        raise FormatError("JSON byte limit exceeded")
    return encoded


def loads(data: str | bytes, *, limits: TraceLimits = TraceLimits()) -> Trace:
    return trace_from_data(strict_loads(data, limits.max_json_bytes), limits)
