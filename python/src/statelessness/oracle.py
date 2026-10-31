"""Experimental paired-v1: independent replayable history using the native engine.

The four qualified portable profiles are unchanged. Oracle advancement observes
inputs/effects/disposition, never actual next state. User callbacks remain pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterable, Protocol, TypeVar

from .core import (Check, Disposition, Metadata, ModelError, Transition,
                   _Captured, _call, _copy, _encode, _state_checks, _step,
                   _transition_checks, _natural, _dispatch_checks, CheckSink)

S = TypeVar("S")
H = TypeVar("H")
I = TypeVar("I")
O = TypeVar("O")

EXPERIMENTAL_PROFILES = ("paired-v1",)


@dataclass(frozen=True)
class OracleState(Generic[S, H]):
    model: S
    oracle: H

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        # Capture references before user-defined copying/comparison can replace
        # fields through an external alias. Preserve tuple comparison's identity
        # shortcut for an object compared with itself.
        if self is other:
            return True
        left_model, right_model = self.model, other.model
        histories = _Captured((self.oracle, other.oracle))
        # Rust compares model states first. A failed history copy must not
        # override an unequal model comparison or an exception raised by it.
        if (left_model,) != (right_model,):
            return False
        left_history, right_history = histories.get()
        return (left_history,) == (right_history,)


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


class WithOracle(Generic[S, H, I, O]):
    def __init__(self, model: Any, oracle: Oracle[H, I, O], *,
                 estimated_wrapper_overhead: int | None = None):
        if estimated_wrapper_overhead is not None:
            _natural(estimated_wrapper_overhead, "estimated_wrapper_overhead")
        self.model, self.oracle = model, oracle
        self.estimated_wrapper_overhead = estimated_wrapper_overhead

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
        advance = self.oracle.advance
        history_source = before.oracle
        # Capture structural fields before invoking application copy hooks.
        # Payload objects still require cooperative deepcopy implementations.
        disposition = transition.disposition
        transition_source = Transition(transition.state, transition.outputs,
                                       Disposition(disposition.kind, disposition.reason))
        # Capture input before transition copy hooks can mutate its external alias.
        # Defer its copy failure until the executed transition is retained, so an
        # observation failure still carries that transition.
        captured_input = _Captured(input)
        # Preserve the already executed transition even if a callback mutates its
        # defensive inputs and subsequently fails.
        retained = _copy(transition_source)
        try:
            retained_input = captured_input.get()
            history = _call("advance history", advance, _copy(history_source),
                            _copy(retained_input), _copy(retained.outputs), _copy(retained.disposition))
            return Transition(OracleState(_copy(retained.state), _copy(history)),
                              _copy(retained.outputs), _copy(retained.disposition))
        except Exception as error:
            raise ObservationError(error, retained) from error

    def step(self, before: OracleState[S, H], input: I) -> Transition[OracleState[S, H], O]:
        # Own the observation before application code can mutate external aliases.
        # Capture input first, since application-defined deepcopy hooks for state
        # or history can themselves reach the caller's input.
        retained_input = _copy(input)
        retained_before = OracleState(_copy(before.model), _copy(before.oracle))
        actual = _step(self.model, retained_before.model, retained_input)
        return self.observe_transition(retained_before, retained_input, actual)

    def inputs(self, state: OracleState[S, H]) -> Iterable[I]:
        return (_copy(value) for value in self.model.inputs(_copy(state.model)))

    def estimated_state_bytes(self, state: OracleState[S, H]) -> int | None:
        """Application logical bytes plus explicit wrapper overhead; never RSS.

        Managed Python layout has no Rust-equivalent inline padding. Unknown
        overhead (the default) or unknown component/overflow yields None.
        History is copied before the model estimator, even if that estimator
        returns None or fails; application-defined copy hooks may have effects.
        Copy hooks must cooperate, and external concurrent mutation is not atomic.
        """
        from .search import _estimate
        # Preserve history before model snapshot/estimator hooks can mutate an
        # external alias. Defer snapshot failures until the model estimate is
        # known so an unknown or failed model estimate keeps its precedence.
        retained_history = _Captured(state.oracle)
        model = _estimate(self.model, state.model)
        if model is None:
            return None
        history = _estimate(self.oracle, retained_history.get())
        if history is None or self.estimated_wrapper_overhead is None:
            return None
        total = model + history + self.estimated_wrapper_overhead
        return total if total <= (1 << 64) - 1 else None

    def generate(self, state: OracleState[S, H], rng):
        callback = getattr(self.model, "generate", None)
        if callback is None:
            raise ModelError("wrapped model does not support generation; use Generated or Auto")
        return _copy(_call("oracle model generate", callback, _copy(state.model), rng))

    def is_enabled(self, state: OracleState[S, H], input: I) -> bool:
        callback = getattr(self.model, "is_enabled", None)
        if callback is None:
            return True
        # A model-state copy hook may mutate an externally aliased query.
        model_state = state.model
        captured_input = _copy(input)
        enabled = _call("oracle model is_enabled", callback, _copy(model_state), captured_input)
        if type(enabled) is not bool:
            raise ModelError("oracle model is_enabled must return bool")
        return enabled

    def simpler_inputs(self, input: I) -> Iterable[I]:
        callback = getattr(self.model, "simpler_inputs", None)
        return () if callback is None else (_copy(value) for value in
            _call("oracle model simpler_inputs", callback, _copy(input)))

    def check_state_into(self, state: OracleState[S, H], checks: CheckSink) -> None:
        model_state, history = _copy((state.model, state.oracle))
        _dispatch_checks(self.model, "check_state_into", "check_state",
                         (_copy(model_state),), checks, "model state check")
        _dispatch_checks(self.oracle, "check_state_into", "check_state",
                         (_copy(history), _copy(model_state)), checks, "oracle state check")

    def check_state(self, state: OracleState[S, H]) -> tuple[Check, ...]:
        return _state_checks(self, state)

    def check_transition_into(self, before: OracleState[S, H], input: I,
                              transition: Transition[OracleState[S, H], O], checks: CheckSink) -> None:
        before, input, transition = _copy((before, input, transition))
        actual = Transition(transition.state.model, transition.outputs, transition.disposition)
        _dispatch_checks(self.model, "check_transition_into", "check_transition",
                         (_copy(before.model), _copy(input), _copy(actual)), checks,
                         "model transition check", optional=True)
        _dispatch_checks(self.oracle, "check_transition_into", "check_transition",
                         (_copy(before), _copy(input), _copy(transition)), checks,
                         "oracle transition check", optional=True)

    def check_transition(self, before: OracleState[S, H], input: I,
                         transition: Transition[OracleState[S, H], O]) -> tuple[Check, ...]:
        return _transition_checks(self, before, input, transition)

    def encode_state_into(self, state: OracleState[S, H], buffer) -> None:
        """Encode both components using history captured before model callbacks.

        History copying occurs early and may have side effects even if the model
        frame fails. Copy failures are deferred until the history frame is entered
        so model and framing errors retain their precedence. Copy hooks must
        cooperate; this does not make concurrent external mutation atomic.
        """
        from .codec import _dispatch_encode
        buffer.append(b"ORC1")
        history = _Captured(state.oracle)
        buffer.framed(lambda child: _dispatch_encode(self.model, "state", state.model, child))
        def encode_history(child):
            _dispatch_encode(self.oracle, "history", history.get(), child)
        buffer.framed(encode_history)

    def encode_state(self, state: OracleState[S, H]) -> bytes:
        from .codec import encode_value
        return encode_value(self, "state", state)

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
        # Own each decoded component before another application callback can
        # mutate an aliased value returned by the preceding decoder.
        model = _copy(self.model.decode_state(parts[0]))
        history = _copy(self.oracle.decode_history(parts[1]))
        return OracleState(model, history)

    def encode_input_into(self, input: I, buffer) -> None:
        from .codec import _dispatch_encode
        _dispatch_encode(self.model, "input", input, buffer)

    def encode_input(self, input: I) -> bytes:
        from .codec import encode_value
        return encode_value(self, "input", input)

    def decode_input(self, data: bytes) -> I:
        return self.model.decode_input(data)

    def encode_output_into(self, output: O, buffer) -> None:
        from .codec import _dispatch_encode
        _dispatch_encode(self.model, "output", output, buffer)

    def encode_output(self, output: O) -> bytes:
        from .codec import encode_value
        return encode_value(self, "output", output)


class Projection(Protocol):
    def metadata(self) -> Metadata: ...
    def actual_state(self, state: Any) -> Any: ...
    def reference_state(self, state: Any) -> Any: ...
    def actual_effect(self, effect: Any) -> Any: ...
    def reference_effect(self, effect: Any) -> Any: ...


def _compare(id: str, actual: Any, reference: Any) -> Check:
    return Check(id) if actual == reference else Check(id, "failed", f"actual={actual!r}; reference={reference!r}")


class _CapturedHooks:
    """Keep bound hooks stable while deferring lookup failures until use."""
    def __init__(self, owner, names):
        self.values = {}
        for name in names:
            try:
                self.values[name] = (getattr(owner, name, None), None)
            except Exception as error:
                self.values[name] = (None, error)

    def __getattr__(self, name):
        value, error = self.values[name]
        if error is not None:
            raise error
        return value


def _capture_checks(owner, append_name, legacy_name):
    hooks = _CapturedHooks(owner, (append_name,))
    value, error = hooks.values[append_name]
    # Do not touch a legacy descriptor when the append hook takes precedence.
    if value is None and error is None:
        hooks.values.update(_CapturedHooks(owner, (legacy_name,)).values)
    return hooks


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

    def estimated_state_bytes(self, history: Any) -> int | None:
        from .search import _estimate
        return _estimate(self.reference, history)

    def initial_state(self) -> Any:
        return self.reference.initial_state()

    def advance(self, before: Any, input: Any, _outputs: Any, _disposition: Any) -> Any:
        return _step(self.reference, before, input).state

    def check_state_into(self, history: Any, actual: Any, checks: CheckSink) -> None:
        # Capture both sides before invoking reference callbacks, which may hold
        # external aliases to the caller's objects.
        reference, projection = self.reference, self.projection
        reference_checks = _capture_checks(reference, "check_state_into", "check_state")
        projected = _CapturedHooks(projection, ("actual_state", "reference_state"))
        history = _copy(history)
        captured_actual = _Captured(actual)
        _dispatch_checks(reference_checks, "check_state_into", "check_state",
                         (_copy(history),), checks, "reference state check")
        actual = captured_actual.get()
        checks.append(_compare("pair.state", projected.actual_state(_copy(actual)),
                               projected.reference_state(_copy(history))))

    def check_state(self, history: Any, actual: Any) -> tuple[Check, ...]:
        values = []
        _dispatch_checks(self, "check_state_into", "check_state", (_copy(history), _copy(actual)),
                         values, "paired state check")
        return tuple(values)

    def check_transition_into(self, before: OracleState[Any, Any], input: Any,
                              transition: Transition[OracleState[Any, Any], Any], checks: CheckSink) -> None:
        # Reference execution and checks must not change the actual observation
        # through aliases retained by application code.
        reference_model, projection = self.reference, self.projection
        reference_step = _CapturedHooks(reference_model, ("step",))
        reference_checks = _capture_checks(reference_model, "check_transition_into", "check_transition")
        projected = _CapturedHooks(projection, ("actual_effect", "reference_effect"))
        observed_outputs, observed_disposition = transition.outputs, transition.disposition
        outputs_error = None
        try:
            slots = tuple(observed_outputs)
        except Exception as error:
            outputs_error = error
            slots = ()
        history, input = _copy((before.oracle, input))
        outputs = [_Captured(output) for output in slots]
        disposition = _Captured(observed_disposition)
        reference = _step(reference_step, history, input)
        _dispatch_checks(reference_checks, "check_transition_into", "check_transition",
                         (_copy(history), _copy(input), _copy(reference)), checks,
                         "reference transition check", optional=True)
        if outputs_error is not None:
            raise outputs_error
        actual_effects = []
        for output in outputs:
            actual_effects.append(projected.actual_effect(_copy(output.get())))
        actual_effects = tuple(actual_effects)
        reference_effects = tuple(projected.reference_effect(_copy(o)) for o in reference.outputs)
        checks.append(_compare("pair.effects", actual_effects, reference_effects))
        checks.append(_compare("pair.outcome", disposition.get(), reference.disposition))

    def check_transition(self, before: OracleState[Any, Any], input: Any,
                         transition: Transition[OracleState[Any, Any], Any]) -> tuple[Check, ...]:
        return _transition_checks(self, before, input, transition)

    def encode_history_into(self, history: Any, buffer) -> None:
        from .codec import _dispatch_encode
        _dispatch_encode(self.reference, "state", history, buffer)

    def encode_history(self, history: Any) -> bytes:
        from .codec import encode_value
        return encode_value(self, "history", history)

    def decode_history(self, data: bytes) -> Any:
        return self.reference.decode_state(data)
