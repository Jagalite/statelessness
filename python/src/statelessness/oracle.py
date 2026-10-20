"""Experimental paired-v1: independent replayable history using the native engine.

The four qualified portable profiles are unchanged. Oracle advancement observes
inputs/effects/disposition, never actual next state. User callbacks remain pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterable, Protocol, TypeVar

from .core import (Check, Disposition, Metadata, ModelError, Transition,
                   _call, _checks, _copy, _encode, _state_checks, _step,
                   _transition_checks)

S = TypeVar("S")
H = TypeVar("H")
I = TypeVar("I")
O = TypeVar("O")

EXPERIMENTAL_PROFILES = ("paired-v1",)


@dataclass(frozen=True)
class OracleState(Generic[S, H]):
    model: S
    oracle: H


class Oracle(Protocol[H, I, O]):
    def metadata(self) -> Metadata: ...
    def initial_state(self) -> H: ...
    def advance(self, before: H, input: I, outputs: tuple[O, ...],
                disposition: Disposition) -> H: ...
    def check_state(self, history: H, actual: Any) -> Iterable[Check]: ...


class ObservationError(ModelError):
    """History failed after application execution; retain the actual transition."""
    def __init__(self, error: Exception, transition: Transition[Any, Any]):
        super().__init__(str(error))
        self.error = error
        self.transition = transition


def _length(value: str) -> int:
    return len(value.encode("utf-8"))


def _iter_checks(stage, callback, *args):
    """Preserve successful emissions when a later iterator/callback fails."""
    iterator = _call(stage, iter, _call(stage, callback, *args))
    sentinel = object()
    while True:
        check = _call(stage, next, iterator, sentinel)
        if check is sentinel:
            return
        if not isinstance(check, Check):
            raise ModelError(stage + ": checker must return Check values")
        yield check


class WithOracle(Generic[S, H, I, O]):
    def __init__(self, model: Any, oracle: Oracle[H, I, O]):
        self.model, self.oracle = model, oracle

    def metadata(self) -> Metadata:
        m, o = self.model.metadata(), self.oracle.metadata()
        name = (f"with-oracle-v1:{_length(m.name)}:{m.name}:{m.model_version}:"
                f"{m.properties_version}:{m.codec_version}:{_length(o.name)}:{o.name}:"
                f"{o.model_version}:{o.properties_version}:{o.codec_version}")
        build = f"{_length(m.build)}:{m.build}:{_length(o.build)}:{o.build}"
        return Metadata(name, m.model_version, m.properties_version, m.codec_version, build)

    def initial_state(self) -> OracleState[S, H]:
        return OracleState(_copy(_call("initial state", self.model.initial_state)),
                           _copy(_call("initial history", self.oracle.initial_state)))

    def attach(self, model: S, oracle: H) -> OracleState[S, H]:
        """Attach explicit saved history; never derive it from application state."""
        return OracleState(_copy(model), _copy(oracle))

    def observe_transition(self, before: OracleState[S, H], input: I,
                           transition: Transition[S, O]) -> Transition[OracleState[S, H], O]:
        if not isinstance(transition, Transition):
            raise ModelError("observation needs a Transition")
        # Preserve the already executed transition even if a callback mutates its
        # defensive inputs and subsequently fails.
        retained = _copy(transition)
        try:
            history = _call("advance history", self.oracle.advance, _copy(before.oracle),
                            _copy(input), _copy(transition.outputs), transition.disposition)
            return Transition(OracleState(_copy(transition.state), _copy(history)),
                              _copy(transition.outputs), transition.disposition)
        except Exception as error:
            raise ObservationError(error, retained) from error

    def step(self, before: OracleState[S, H], input: I) -> Transition[OracleState[S, H], O]:
        return self.observe_transition(before, input, _step(self.model, before.model, input))

    def inputs(self, state: OracleState[S, H]) -> Iterable[I]:
        return self.model.inputs(_copy(state.model))

    def generate(self, state: OracleState[S, H], rng):
        callback = getattr(self.model, "generate", None)
        if callback is None:
            raise ModelError("wrapped model does not support generation; use Generated or Auto")
        return _copy(_call("oracle model generate", callback, _copy(state.model), rng))

    def is_enabled(self, state: OracleState[S, H], input: I) -> bool:
        callback = getattr(self.model, "is_enabled", None)
        if callback is None:
            return True
        enabled = _call("oracle model is_enabled", callback, _copy(state.model), _copy(input))
        if type(enabled) is not bool:
            raise ModelError("oracle model is_enabled must return bool")
        return enabled

    def simpler_inputs(self, input: I) -> Iterable[I]:
        callback = getattr(self.model, "simpler_inputs", None)
        return () if callback is None else _call("oracle model simpler_inputs", callback, _copy(input))

    def check_state(self, state: OracleState[S, H]) -> Iterable[Check]:
        yield from _iter_checks("model state check", self.model.check_state, _copy(state.model))
        yield from _iter_checks("oracle state check", self.oracle.check_state,
                                _copy(state.oracle), _copy(state.model))

    def check_transition(self, before: OracleState[S, H], input: I,
                         transition: Transition[OracleState[S, H], O]) -> Iterable[Check]:
        actual = Transition(transition.state.model, transition.outputs, transition.disposition)
        callback = getattr(self.model, "check_transition", None)
        if callback is not None:
            yield from _iter_checks("model transition check", callback,
                                    _copy(before.model), _copy(input), _copy(actual))
        callback = getattr(self.oracle, "check_transition", None)
        if callback is not None:
            yield from _iter_checks("oracle transition check", callback,
                                    _copy(before), _copy(input), _copy(transition))

    def encode_state(self, state: OracleState[S, H]) -> bytes:
        model = _encode("encode model", self.model.encode_state, state.model)
        history = _encode("encode history", self.oracle.encode_history, state.oracle)
        return b"ORC1" + len(model).to_bytes(8, "little") + model + len(history).to_bytes(8, "little") + history

    def decode_state(self, data: bytes) -> OracleState[S, H]:
        if type(data) is not bytes or not data.startswith(b"ORC1"):
            raise ModelError("invalid oracle checkpoint format")
        offset = 4
        parts = []
        for _ in range(2):
            if len(data) - offset < 8:
                raise ModelError("truncated oracle checkpoint length")
            size = int.from_bytes(data[offset:offset + 8], "little")
            offset += 8
            if size > len(data) - offset:
                raise ModelError("truncated oracle checkpoint component")
            parts.append(data[offset:offset + size])
            offset += size
        if offset != len(data):
            raise ModelError("trailing oracle checkpoint bytes")
        return OracleState(self.model.decode_state(parts[0]), self.oracle.decode_history(parts[1]))

    def encode_input(self, input: I) -> bytes:
        return self.model.encode_input(input)

    def decode_input(self, data: bytes) -> I:
        return self.model.decode_input(data)

    def encode_output(self, output: O) -> bytes:
        return self.model.encode_output(output)


class Projection(Protocol):
    def metadata(self) -> Metadata: ...
    def actual_state(self, state: Any) -> Any: ...
    def reference_state(self, state: Any) -> Any: ...
    def actual_effect(self, effect: Any) -> Any: ...
    def reference_effect(self, effect: Any) -> Any: ...


def _compare(id: str, actual: Any, reference: Any) -> Check:
    return Check(id) if actual == reference else Check(id, "failed", f"actual={actual!r}; reference={reference!r}")


class Paired:
    """Reference advancement ignores actual effects and disposition.

    Projection equality is application-owned. Failure details are diagnostic;
    check IDs, ordering and statuses are the portable observations.
    """
    def __init__(self, reference: Any, projection: Projection):
        self.reference, self.projection = reference, projection

    def metadata(self) -> Metadata:
        r, p = self.reference.metadata(), self.projection.metadata()
        name = (f"paired-v1:{_length(r.name)}:{r.name}:{r.model_version}:"
                f"{r.properties_version}:{r.codec_version}:{_length(p.name)}:{p.name}:{p.model_version}")
        build = f"{_length(r.build)}:{r.build}:{_length(p.build)}:{p.build}"
        return Metadata(name, p.model_version, p.properties_version, p.codec_version, build)

    def initial_state(self) -> Any:
        return self.reference.initial_state()

    def advance(self, before: Any, input: Any, _outputs: Any, _disposition: Any) -> Any:
        return _step(self.reference, before, input).state

    def check_state(self, history: Any, actual: Any) -> Iterable[Check]:
        yield from _iter_checks("reference state check", self.reference.check_state, _copy(history))
        yield _compare("pair.state", self.projection.actual_state(actual),
                       self.projection.reference_state(history))

    def check_transition(self, before: OracleState[Any, Any], input: Any,
                         transition: Transition[OracleState[Any, Any], Any]) -> Iterable[Check]:
        reference = _step(self.reference, before.oracle, input)
        callback = getattr(self.reference, "check_transition", None)
        if callback is not None:
            yield from _iter_checks("reference transition check", callback,
                                    _copy(before.oracle), _copy(input), _copy(reference))
        actual_effects = tuple(self.projection.actual_effect(o) for o in transition.outputs)
        reference_effects = tuple(self.projection.reference_effect(o) for o in reference.outputs)
        yield _compare("pair.effects", actual_effects, reference_effects)
        yield _compare("pair.outcome", transition.disposition, reference.disposition)

    def encode_history(self, history: Any) -> bytes:
        return self.reference.encode_state(history)

    def decode_history(self, data: bytes) -> Any:
        return self.reference.decode_state(data)
