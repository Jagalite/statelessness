"""Installed five-engine N13 application; Output deliberately has no decoder."""
from dataclasses import dataclass, replace
from pathlib import Path
import json
import sys
from statelessness import (Metadata, Transition, Check, ordered_record, field,
    tagged_enum, case, model_codec, U8, U32, Schema, EncodeBuffer, DecodeLimits,
    ModelError, check_initial, check_turn, Recorder, RecorderOptions,
    EvidenceRecorder, EvidenceLimits, Provenance, CausalContext, OutputOrigin,
    Artifact, replay_with_observations_bounded)
from statelessness.trace_binary import RunConfig, BinaryLimits, audit_to_bytes

@dataclass(frozen=True)
class State:
    count: int

@dataclass(frozen=True)
class Add:
    amount: int

CONFIG = RunConfig('manual', 7, (('region', 'test'),))
PROVENANCE = Provenance('n13-installed-run', 7, 'manifest-v1', 'consumer-source', (('region', 'test'),))
LIMITS = BinaryLimits(max_total_bytes=16384, max_frame_bytes=4096, max_blob_bytes=64,
    max_string_bytes=256, max_steps=16, max_checks_per_step=16,
    max_outputs_per_step=16, max_parameters=32, max_items=256)
ELIMITS = EvidenceLimits(LIMITS, 1024, 8, 128, 4, 8)
OPTIONS = RecorderOptions(3, 16384, LIMITS)
OUTPUT_FAILURE = 'n13 controlled Output encoder failure'

class Model:
    def __init__(self):
        self.counts = dict(initial=0, reducer=0, state=0, transition=0,
                           encode_state=0, encode_input=0, encode_output=0,
                           decode_state=0, decode_input=0, limits=0)
        self.version = 1
        self.limits = DecodeLimits()
        self.fail_output = False
    def metadata(self): return Metadata('n13-installed-model', build='application-v1', codec_version=self.version)
    def initial_state(self): self.counts['initial'] += 1; return State(0)
    def step(self, state, input):
        self.counts['reducer'] += 1
        after = State(state.count + input.amount)
        return Transition(after, (after.count,))
    def check_state(self, state):
        self.counts['state'] += 1
        return (Check('threshold.bound', 'failed', 'at least three') if state.count >= 3 else Check('threshold.bound'),)
    def check_transition(self, *args):
        self.counts['transition'] += 1
        return (Check('edge.checked'),)

class Counted:
    """Capture ordinary codec direction calls without adding an Output decoder."""
    def __init__(self, schema, model, kind): self.schema, self.model, self.kind = schema, model, kind
    def encode_into(self, value, sink):
        self.model.counts['encode_' + self.kind] += 1
        self.schema.encode_into(value, sink)
    def decode(self, reader):
        self.model.counts['decode_' + self.kind] += 1
        return self.schema.decode(reader)

class Output:
    def __init__(self, model): self.model = model
    def encode_into(self, value, sink):
        self.model.counts['encode_output'] += 1
        if self.model.fail_output:
            sink.extend(b'\xee')
            raise ModelError(OUTPUT_FAILURE)
        U32.encode_into(value, sink)

STATE = ordered_record('State', value_type=State,
    fields=(field('count', U32, get=lambda v: v.count),), construct=lambda xs: State(*xs))
INPUT = tagged_enum('Input', select=lambda v: 'Add', cases=(case('Add', tag=1,
    fields=(field('amount', U8, get=lambda v: v.amount),), construct=lambda xs: Add(*xs)),))

def bind(m, custom=True):
    def provider(model):
        model.counts['limits'] += 1
        return model.limits
    output = Output(m)
    assert not hasattr(output, 'decode')
    return model_codec(state=Counted(STATE, m, 'state'), input=Counted(INPUT, m, 'input'),
        output=output, decode_limits=provider if custom else None).bind(m)

def expected(replay=False):
    return dict(initial=0, reducer=3, state=4, transition=3, encode_state=4 if replay else 14,
        encode_input=3 if replay else 6, encode_output=3 if replay else 6, decode_state=1 if replay else 0,
        decode_input=3 if replay else 0, limits=4 if replay else 0)

def reject(call, message=None):
    try: call()
    except ModelError as error:
        if message is not None: assert message in str(error), str(error)
        return
    raise AssertionError('expected rejection')

def controls():
    m = Model(); b = bind(m); m.version = 2
    prefix = bytearray(); sink = EncodeBuffer(prefix, 32); sink.extend(b'p')
    hostile = object()
    hooks = (lambda: b.encode_state(hostile), lambda: b.encode_input(hostile),
        lambda: b.encode_output(hostile), lambda: b.encode_state_into(hostile, sink),
        lambda: b.encode_input_into(hostile, sink), lambda: b.encode_output_into(hostile, sink),
        lambda: b.decode_state(hostile), lambda: b.decode_input(hostile))
    for hook in hooks: reject(hook, 'version does not match')
    assert prefix == b'p' and all(v == 0 for v in m.counts.values())
    m.version = 1
    assert b.encode_state(State(0)) == bytes(4)
    assert b.encode_input(Add(1)) == b'\1\1'
    assert b.encode_output(1) == b'\1\0\0\0'
    for kind, value in (('state', State(0)), ('input', Add(1)), ('output', 1)):
        getattr(b, 'encode_' + kind + '_into')(value, sink)
    assert m.counts['limits'] == 0
    # Exact byte admission, changing provider values, and fresh independent readers.
    m.limits = DecodeLimits(bytes=4)
    assert b.decode_state(bytes(4)) == State(0)
    m.limits = DecodeLimits(bytes=3)
    reject(lambda: b.decode_state(bytes(4)))
    m.limits = DecodeLimits(bytes=2)
    assert b.decode_input(b'\1\1') == Add(1)
    m.limits = DecodeLimits(bytes=1)
    reject(lambda: b.decode_input(b'\1\1'))
    m.limits = DecodeLimits()
    for kind, good in (('state', bytes(4)), ('input', b'\1\1')):
        reject(lambda: getattr(b, 'decode_' + kind)(good[:-1]))
        reject(lambda: getattr(b, 'decode_' + kind)(good + b'\0'))
    assert m.counts['limits'] == 8
    default = bind(Model(), False)
    assert default.decode_state(bytes(4)) == State(0)
    assert default.decode_input(b'\1\1') == Add(1)
    return dict(version_hooks=8, fresh_limit_calls=m.counts['limits'], encoder_limit_calls=0,
                malformed_trailing=4, output_decoder=False)

def reject_exact(call, message):
    try: call()
    except ModelError as error:
        assert str(error) == message, str(error)
        return
    raise AssertionError('expected ModelError: ' + message)

def capture_failure_controls():
    """Fresh applications; negative prefixes never enter the success envelope."""
    receipts = {}
    diagnostic = 'runtime encode output: ' + OUTPUT_FAILURE
    options = RecorderOptions(1, 16384, LIMITS)
    first_causal = CausalContext('worker-a', 9, 'request-1', 11, ())
    second_causal = CausalContext('worker-a', 9, 'request-1', 12, (OutputOrigin(1, 0),))
    for kind in ('recorder', 'evidence'):
        m = Model(); b = bind(m)
        initial = check_initial(b, State(0))
        if kind == 'evidence':
            recorder = EvidenceRecorder(initial, 0, CONFIG, options, PROVENANCE, ELIMITS)
            observe = recorder.observe
            snapshot = lambda: recorder.snapshot().trace
        else:
            recorder = Recorder.with_checked_initial(initial, CONFIG, options)
            observe = lambda token, causal: recorder.observe_checked(token)
            snapshot = recorder.snapshot
        want = dict(initial=0, reducer=0, state=1, transition=0, encode_state=1,
                    encode_input=0, encode_output=0, decode_state=0, decode_input=0, limits=0)
        assert m.counts == want, m.counts
        stages = dict(after_initial=dict(m.counts))
        first = check_turn(b, State(0), Add(1), b.step(State(0), Add(1)), 1)
        want.update(reducer=1, state=2, transition=1)
        assert m.counts == want, m.counts
        observe(first, first_causal)
        want.update(encode_state=3, encode_input=1, encode_output=1)
        assert m.counts == want, m.counts
        stages['after_first_capture'] = dict(m.counts)
        prefix = snapshot()
        journal = recorder.snapshot() if kind == 'evidence' else None
        assert prefix.termination == 'interrupted' and prefix.error == ''
        assert prefix.metadata == m.metadata() and prefix.initial_state == bytes(4)
        assert prefix.initial_checks == (Check('threshold.bound'),)
        assert len(prefix.steps) == 1
        step = prefix.steps[0]
        assert step.input == b'\1\1' and step.post_state == b'\1\0\0\0'
        assert step.outputs == (b'\1\0\0\0',)
        assert step.disposition.kind == 'accepted' and step.disposition.reason == ''
        assert step.checks == (Check('threshold.bound'), Check('edge.checked'))
        retained = dict(prefix.config.parameters)
        assert prefix.config.strategy == CONFIG.strategy and prefix.config.seed == CONFIG.seed
        assert prefix.config.parameters[0] == ('region', 'test')
        assert retained['stateless.monitor.max_steps'] == '1'
        assert retained['stateless.monitor.observed_steps'] == '1'
        assert retained['stateless.monitor.evicted_steps'] == '0'
        assert retained['stateless.monitor.checkpoint_after_sequence'] == '0'
        # The second checked token is valid. Only its bound Output callback fails.
        second = check_turn(b, State(1), Add(1), b.step(State(1), Add(1)), 2)
        want.update(reducer=2, state=3, transition=2)
        assert m.counts == want, m.counts
        stages['before_failed_capture'] = dict(m.counts)
        m.fail_output = True
        reject_exact(lambda: observe(second, second_causal), diagnostic)
        want.update(encode_state=4, encode_input=2, encode_output=2)
        assert m.counts == want, m.counts
        stages['after_failed_capture'] = dict(m.counts)
        frozen = snapshot()
        assert recorder.is_frozen()
        assert frozen == replace(prefix, termination='model_error', error=diagnostic)
        if journal is not None:
            value = recorder.snapshot()
            assert value == replace(journal, trace=frozen)
            assert value.provenance == PROVENANCE and value.checkpoint_after == 0
            assert len(value.steps) == 1 and value.steps[0].sequence == 1
            assert value.steps[0].state_check_count == 1 and value.steps[0].causal == first_causal
            assert value.check_phase(1, 0) == 'state' and value.check_phase(1, 1) == 'transition'
            assert value.check_phase(2, 0) is None
            assert Artifact.from_bytes(value.to_bytes(limits=ELIMITS), limits=ELIMITS).value == value
        else:
            assert recorder.observed_steps() == 1 and recorder.evicted_steps() == 0
            assert recorder.retained_steps() == 1
            assert Artifact.from_bytes(audit_to_bytes(frozen, limits=LIMITS), limits=ELIMITS).trace == frozen
        # Reusing the already checked token after freeze must do no callback work.
        m.fail_output = False
        owner = 'runtime evidence recorder' if journal is not None else 'runtime recorder'
        reject_exact(lambda: observe(second, second_causal), owner + ' is frozen; export its retained trace')
        assert snapshot() == frozen and m.counts == want
        if journal is not None:
            assert recorder.into_trace() == value
        else:
            assert recorder.into_trace() == frozen
        assert m.counts == want, m.counts
        receipts[kind] = stages
    return receipts

def produce():
    m = Model(); b = bind(m)
    initial = check_initial(b, State(0))
    recorder = Recorder.with_checked_initial(initial, CONFIG, OPTIONS)
    evidence = EvidenceRecorder(initial, 0, CONFIG, OPTIONS, PROVENANCE, ELIMITS)
    for sequence in (1, 2, 3):
        before = State(sequence - 1); input = Add(1)
        checked = check_turn(b, before, input, b.step(before, input), sequence)
        before_counts = dict(m.counts)
        recorder.observe_checked(checked)
        evidence.observe(checked, CausalContext('worker-a', 9, 'request-1', 10 + sequence,
            () if sequence == 1 else (OutputOrigin(sequence - 1, 0),)))
        assert all(m.counts[k] == before_counts[k] for k in ('initial', 'reducer', 'state', 'transition')), 'checked handoff reexecuted callbacks'
    value = evidence.snapshot(); trace = recorder.snapshot()
    assert trace.steps == value.trace.steps and trace.initial_checks == value.trace.initial_checks
    assert m.counts == expected(), m.counts
    return dict(v1=audit_to_bytes(trace, limits=LIMITS).hex(), v2=value.to_bytes(limits=ELIMITS).hex()), m.counts

def consume(envelope):
    rewritten = {}; receipts = {}
    for version in (1, 2):
        artifact = Artifact.from_bytes(bytes.fromhex(envelope['v' + str(version)]), limits=ELIMITS)
        assert artifact.version == version
        config = artifact.trace.config
        assert config.strategy == 'manual' and config.seed == 7
        assert config.parameters[0] == ('region', 'test')
        retained = dict(config.parameters)
        assert retained['stateless.monitor.max_steps'] == '3'
        assert retained['stateless.monitor.observed_steps'] == '3'
        assert retained['stateless.monitor.max_retained_bytes'] == ('16384' if version == 1 else '15328')
        assert artifact.trace.metadata == Model().metadata()
        assert artifact.trace.initial_state == bytes(4)
        assert [step.input for step in artifact.trace.steps] == [b'\1\1'] * 3
        assert [step.outputs for step in artifact.trace.steps] == [(n.to_bytes(4, 'little'),) for n in (1,2,3)]
        assert artifact.trace.termination == 'property_failed'
        m = Model(); b = bind(m)
        if version == 2:
            value = artifact.value
            assert value.provenance == PROVENANCE and value.checkpoint_after == 0
            assert [s.state_check_count for s in value.steps] == [1,1,1]
            for sequence, step in enumerate(value.steps, 1):
                assert step.causal == CausalContext('worker-a',9,'request-1',10+sequence,
                    () if sequence == 1 else (OutputOrigin(sequence-1,0),))
                assert value.check_phase(sequence,0) == 'state'
                assert value.check_phase(sequence,1) == 'transition'
            report = value.replay(b, limits=ELIMITS)
            raw = value.to_bytes(limits=ELIMITS)
        else:
            observations = []
            report = replay_with_observations_bounded(b, artifact.trace, observations.append, limits=LIMITS)
            assert len(observations) == 4
            assert artifact.check_phase(1,0) is None
            raw = audit_to_bytes(artifact.trace, limits=LIMITS)
        assert report.outcome == 'exact' and report.steps_verified == 3 and report.failure_reproduced and report.build_matches
        assert m.counts == expected(True), m.counts
        rewritten['v' + str(version)] = raw.hex()
        receipts['v' + str(version)] = dict(counts=m.counts, outcome=report.outcome,
            steps_verified=report.steps_verified, failure_reproduced=report.failure_reproduced)
    assert rewritten == envelope
    return rewritten, receipts

def main():
    mode = sys.argv[1]
    control = controls()
    failures = capture_failure_controls()
    replay_counts = {}; capture = {}
    if mode == 'produce':
        envelope, counts = produce(); destination = sys.argv[2]
        receipt = dict(production_counts=counts)
        capture = dict(capture_counts=counts)
    elif mode == 'transcode':
        envelope, receipt = consume(json.loads(Path(sys.argv[2]).read_text())); destination = sys.argv[3]
        replay_counts = {version: result['counts'] for version, result in receipt.items()}
    else: raise ValueError('unknown mode')
    Path(destination).write_text(json.dumps(envelope, sort_keys=True))
    print(json.dumps(dict(engine='python', controls=control, receipt=receipt,
        capture_failure_controls=True, capture_failure_counts=failures,
        replay_counts=replay_counts, **capture)))

if __name__ == '__main__': main()
