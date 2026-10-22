"""Installed public Python observation consumer; no source-path or ABI fallback."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys

from statelessness import (
    Check, Disposition, EncodedPayloads, Event, LogLevel, Metadata, ModelError,
    NoPayloads, ObservationOptions, RunConfig, SnapshotPolicy, TextObserver,
    TextObserverError, Transition, adapter, adapted_field, model_codec,
    ordered_record,
)


def snapshots(value):
    if value == "None": return SnapshotPolicy.NONE
    if value == "EveryTransition": return SnapshotPolicy.EVERY_TRANSITION
    if isinstance(value, dict) and set(value) == {"every"}:
        return SnapshotPolicy.every(int(value["every"]))
    raise ValueError("unknown snapshot policy")


class FixtureModel:
    def __init__(self, callbacks):
        self.callbacks, self.fault, self.decode_calls = callbacks, None, 0
    def forbidden_decode(self, *args):
        self.decode_calls += 1
        raise AssertionError("unexpected decoder")
    def decode_state(self, *args): return self.forbidden_decode(*args)
    def decode_input(self, *args): return self.forbidden_decode(*args)
    def metadata(self): return Metadata("observation-fixture", build="fixture")
    def initial_state(self): raise AssertionError("unexpected initial_state")
    def step(self, *args): raise AssertionError("unexpected reducer")
    def check_state(self, *args): raise AssertionError("unexpected checker")
    def check_transition(self, *args): raise AssertionError("unexpected checker")
    def execute_effect(self, *args): raise AssertionError("unexpected effect execution")
    def encode(self, kind, value, out):
        self.callbacks.append(f"encode_{kind}:{value}")
        if self.fault == "encode_"+kind:
            raise ModelError("fixture codec failure")
        out.append(bytes((value,)))
    def encode_state_into(self, value, out): self.encode("state", value, out)
    def encode_input_into(self, value, out): self.encode("input", value, out)
    def encode_output_into(self, value, out): self.encode("output", value, out)
    def encode_state(self, value): raise AssertionError("legacy encoder used")
    def encode_input(self, value): raise AssertionError("legacy encoder used")
    def encode_output(self, value): raise AssertionError("legacy encoder used")


class DeclarationModel(FixtureModel):
    # Every inherited encode_*_into hook reaches this trap. Legacy hooks also
    # trap, so bypassing ModelCodec.bind cannot supply declaration payload bytes.
    def encode(self, kind, value, out):
        raise AssertionError("direct encoder used for declaration binding")


def declaration_bound(model):
    def schema(kind):
        def encode(value, out):
            model.callbacks.append(f"encode_{kind}:{value}")
            if model.fault == "encode_"+kind:
                raise ModelError("fixture codec failure")
            out.append(bytes((value,)))
        byte = adapter(encode=encode, decode=model.forbidden_decode)
        return ordered_record("Fixture"+kind.title(), value_type=int,
            fields=(adapted_field("byte", byte, get=lambda value: value),),
            construct=lambda fields: fields[0])
    return model_codec(state=schema("state"), input=schema("input"),
                       output=schema("output")).bind(model)


class CustomPayloads:
    def __init__(self, fixture, text): self.fixture, self.text = fixture, text.encode("utf-8")
    def format(self, kind, value, out):
        self.fixture.callbacks.append(f"format_{kind}:{value}")
        if self.fixture.fault == "format_"+kind:
            raise ValueError("fixture formatter failure")
        if self.fixture.fault == "swallow_input" and kind == "input":
            try: out.write(b"x" * 1024)
            except OSError: pass
            return
        out.write(self.text)
    def state(self, model, value, out): self.format("state", value, out)
    def input(self, model, value, out): self.format("input", value, out)
    def output(self, model, value, out): self.format("output", value, out)


class Sink(io.BytesIO):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.writes = 0
        self.flushes = 0
    def write(self, data):
        self.writes += 1
        maximum = self.settings.get("fail_after_bytes")
        if maximum is not None:
            remaining = maximum - self.tell()
            if remaining <= 0: raise OSError("fixture writer failure")
            data = data[:remaining]
        return super().write(data)
    def flush(self):
        self.flushes += 1
        if self.settings.get("fail_flush", False): raise OSError("fixture flush failure")


def event(value):
    kind = value["kind"]
    checks = tuple(Check(c["id"], c["status"].lower(), c.get("message", "")) for c in value.get("checks", ()))
    if kind == "transition":
        disposition = value["disposition"]
        return Event.Transition(int(value["sequence"]), value["before"], value["input"],
            Transition(value["state"], tuple(value["outputs"]),
                       Disposition(disposition["kind"].lower(), disposition.get("reason", ""))), checks)
    if kind == "checkpoint":
        return Event.Checkpoint(int(value["sequence"]), value["state"], checks)
    if kind == "message":
        return Event.Message(LogLevel[value["level"].upper()],
            None if value["sequence"] is None else int(value["sequence"]), value["message"])
    if kind == "finished": return Event.Finished(int(value["transitions"]), value["reason"])
    if kind == "started":
        config = value["config"]
        return Event.Started(Metadata(**value["metadata"]), RunConfig(config["strategy"],
            None if config["seed"] is None else int(config["seed"]), tuple(map(tuple, config["parameters"]))))
    raise ValueError("unknown event kind: " + kind)


def observe_case(row):
    callbacks = []
    declaration = row.get("binding") == "declaration"
    fixture = DeclarationModel(callbacks) if declaration else FixtureModel(callbacks)
    model = declaration_bound(fixture) if declaration else fixture
    settings, presentation = row["options"], row["payloads"]
    if presentation["kind"] == "encoded":
        payloads = EncodedPayloads(presentation.get("max_blob_bytes", 16))
    elif presentation["kind"] == "custom":
        payloads = CustomPayloads(fixture, presentation["text"])
    elif presentation["kind"] == "none": payloads = NoPayloads()
    else: raise ValueError("unknown payload format")
    sink = Sink(row.get("sink", {}))
    observer = TextObserver(sink, row["stream"], ObservationOptions(
        LogLevel[settings.get("level", "Off").upper()],
        snapshots(settings.get("snapshots", "None")), settings.get("max_record_bytes", 1048576)), payloads)
    first_error = None
    for operation in row["events"]:
        fixture.fault = operation.get("fault")
        try:
            kind = operation["kind"]
            if kind == "set_level": observer.set_level(LogLevel[operation["level"].upper()])
            elif kind == "set_snapshots": observer.set_snapshots(snapshots(operation["snapshots"]))
            elif kind == "flush": observer.flush()
            else: observer.observe(model, event(operation))
        except TextObserverError as error:
            if first_error is None: first_error = error.stage
    # Probe real health at Off, independently of earlier formatting errors.
    before = tuple(callbacks)
    writer_calls = (sink.writes, sink.flushes)
    observer.set_level(LogLevel.OFF)
    poisoned = False
    try: observer.observe(model, Event.Message(LogLevel.OFF, None, "health"))
    except TextObserverError as error:
        if error.stage != "sink_poisoned": raise
        poisoned = True
    if tuple(callbacks) != before: raise AssertionError("Off health probe invoked a callback")
    if (sink.writes, sink.flushes) != writer_calls:
        raise AssertionError("Off health probe wrote or flushed")
    if observer.get_ref() is not sink or observer.into_inner() is not sink:
        raise AssertionError("writer ownership changed")
    if (sink.writes, sink.flushes) != writer_calls:
        raise AssertionError("extraction implicitly wrote or flushed")
    if fixture.decode_calls:
        raise AssertionError("diagnostics invoked a decoder")
    return dict(id=row["id"], record_hex=sink.getvalue().hex(), callbacks=callbacks,
                error_stage=first_error, sink_poisoned=poisoned)


def observe_fixture(fixture):
    if fixture.get("schema") != "statelessness.observation.v1":
        raise ValueError("unknown observation schema")
    if not fixture["cases"]: raise ValueError("observation fixture must not be empty")
    return dict(schema="statelessness.observation-results.v1", language="python",
                cases=[observe_case(row) for row in fixture["cases"]])


def main():
    mode, path = sys.argv[1:]
    if mode not in ("observe", "self-test"): raise ValueError("expected observe or self-test")
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    result = observe_fixture(fixture)
    if mode == "self-test":
        for row, actual in zip(fixture["cases"], result["cases"]):
            expected = dict(id=row["id"], **{key: row["expected"][key]
                for key in ("record_hex", "callbacks", "error_stage", "sink_poisoned")})
            if actual != expected: raise AssertionError(f"{row['id']}: {actual!r} != {expected!r}")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
