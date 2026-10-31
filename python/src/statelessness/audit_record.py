"""Candidate incremental audit recording under exact binary-v1 quotas."""
from dataclasses import replace
from .core import (Metadata, ModelError, TraceStep, U64_MAX, _call, _copy,
                   _dispatch_checks, _check_observed_count_into, _failed, _natural, _step)
from .codec import encode_value
from .trace_binary import (AuditTrace, BinaryLimits, RunConfig, U32_MAX, _Encoder,
                           _encode_end, _encode_step, _limit)
from .replay_evidence import _run_account, _step_fixed


def _footer_size(count, kind, error, limits):
    encoder = _Encoder(limits, [0])
    trace = AuditTrace(Metadata(""), RunConfig(), b"", (), (None,) * count, kind, error)
    _encode_end(encoder, trace)
    return 9 + encoder.length


def _error_reason(error, limits):
    maximum = min(256, limits.max_string_bytes, max(0, limits.max_frame_bytes - 13))
    text = str(error)
    # Python exception text can contain surrogate code points. Escape those only
    # in newly captured recorder diagnostics; supplied audit evidence is unchanged.
    selected, size, truncated = [], 0, False
    for character in text:
        if 0xD800 <= ord(character) <= 0xDFFF:
            character = f"\\u{ord(character):04x}"
        width = len(character.encode("utf-8"))
        if size + width > maximum:
            truncated = True
            break
        selected.append(character)
        size += width
    if truncated and maximum >= 3:
        while size > maximum - 3:
            size -= len(selected.pop().encode("utf-8"))
        selected.extend("...")
    return "".join(selected)


def record_audit(model, inputs, config: RunConfig, max_steps=100_000, *, limits=BinaryLimits()):
    """Preserve explicit config; admit only complete steps with footer headroom.

    These wire budgets exclude callback/legacy codec allocations and process RSS.
    Initial errors raise. Later errors retain only the last coherent checked prefix.
    """
    limits = BinaryLimits(**{name: getattr(limits, name) for name in BinaryLimits.__dataclass_fields__})
    if not isinstance(config, RunConfig): raise ValueError("record_audit requires explicit RunConfig")
    from . import __version__
    for key, _ in config.parameters:
        if str.startswith(key, "stateless.engine."):
            raise ModelError("stateless.engine. parameters are reserved")
    config = RunConfig(config.strategy, config.seed, config.parameters + (
        ("stateless.engine.version", __version__),
        ("stateless.engine.build", "native-python-v1")))
    _natural(max_steps, "max_steps")
    blob_limit = min(limits.max_blob_bytes, limits.max_frame_bytes, U32_MAX, limits.max_total_bytes)
    state = _copy(_call("initial state", model.initial_state))
    check_storage = []
    _dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), check_storage, "initial check")
    initial_checks = tuple(check_storage)
    state_storage, input_storage, output_storage = bytearray(), bytearray(), []
    initial = encode_value(model, "state", state, maximum=blob_limit, storage=state_storage, stage="encode initial state")
    metadata = _call("metadata", model.metadata)
    if not isinstance(metadata, Metadata): raise ModelError("metadata: model must return Metadata")
    trace = AuditTrace(metadata, config, initial, initial_checks, (), "completed")
    try:
        run_bytes, items = _run_account(metadata, config, initial, initial_checks, limits)
        total = 12 + run_bytes
        terminal_footer = _footer_size(0, "property_failed", "", limits)
        maximum_reason = min(256, limits.max_string_bytes, max(0, limits.max_frame_bytes - 13))
        reserve_footer = _footer_size(0, "model_error", "x" * maximum_reason, limits)
        initial_failed = _failed(initial_checks)
        _limit(total + (terminal_footer if initial_failed else reserve_footer), limits.max_total_bytes, "total bytes")
    except Exception as error:
        raise ModelError("recording limit: " + str(error)) from error
    if initial_failed: return replace(trace, termination="property_failed")
    steps = []
    def stopped(error):
        return replace(trace, steps=tuple(steps), termination="model_error", error=_error_reason(error, limits))
    try: iterator = _call("record input", iter, inputs)
    except Exception as error: return stopped(error)
    sentinel = object()
    while True:
        try: input = _call("record input", next, iterator, sentinel)
        except Exception as error: return stopped(error)
        if input is sentinel: return replace(trace, steps=tuple(steps))
        if len(steps) >= min(max_steps, limits.max_steps):
            return replace(trace, steps=tuple(steps), termination="step_limit")
        try:
            frame_budget = min(max(0, limits.max_total_bytes - total - terminal_footer), min(limits.max_frame_bytes, U32_MAX) + 9)
            encoded_input = encode_value(model, "input", input, maximum=min(blob_limit, frame_budget), storage=input_storage, stage="encode input")
            transition = _step(model, state, input)
            _check_observed_count_into(model, state, input, transition, len(steps) + 1, check_storage)
            checks = tuple(check_storage)
            count = len(transition.outputs)
            _limit(count, min(limits.max_outputs_per_step, U32_MAX), "outputs per step")
            base, base_items = _step_fixed(encoded_input, transition.disposition, 0, checks, limits)
            _limit(items + base_items + count, limits.max_items, "aggregate items")
            remaining = frame_budget - base - 9
            if remaining < 0: raise ModelError("recording limit: remaining frame bytes")
            outputs = []
            for index, output in enumerate(transition.outputs):
                remaining -= 4
                if remaining < 0: raise ModelError("recording limit: output frame bytes")
                if index == len(output_storage): output_storage.append(bytearray())
                data = encode_value(model, "output", output, maximum=min(blob_limit, remaining), storage=output_storage[index], stage="encode output")
                outputs.append(data)
                remaining -= len(data)
            post_state = encode_value(model, "state", transition.state, maximum=min(blob_limit, remaining), storage=state_storage, stage="encode state")
            step = TraceStep(encoded_input, transition.disposition, tuple(outputs), post_state, checks)
            encoder = _Encoder(limits, [1])
            _encode_step(encoder, len(steps), step)
            next_total = total + encoder.length + 9
            failed = _failed(checks)
            _limit(next_total + (terminal_footer if failed else reserve_footer), limits.max_total_bytes, "total bytes")
            _limit(items + encoder.items[0], limits.max_items, "aggregate items")
            steps.append(step)
            total, items, state = next_total, items + encoder.items[0], transition.state
            if failed: return replace(trace, steps=tuple(steps), termination="property_failed")
        except Exception as error:
            return stopped(error)
