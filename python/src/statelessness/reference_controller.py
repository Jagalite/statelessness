"""Experimental finite request-controller reducer, ported from src/reference.rs.

This candidate fixture contains the reducer, independent catalog and checks,
coverage, codec and fault controls. Full qualification remains open.
It executes no application effects.
"""
from dataclasses import dataclass, replace
from .core import Check, Disposition, Metadata, ModelError, Transition, U64_MAX, _natural
from .value_codec import (BOOL, U8, U64, Some, OptionOf, VectorOf, TupleOf, Rust64Layout,
                          DecodeLimits, encode_canonical, decode_canonical)

_WIRE_INPUT = TupleOf(U8, U64, U8, U8)
# Rust src/reference/codec.rs decodes Vec<(u64, u8, (u8, u64), bool)> and charges
# size_of::<(u64, u8, (u8, u64), bool)>() per element (value_codec.rs collection).
# repr(Rust) reorders by alignment: 8 + 16 + 1 + 1 = 26, padded to 32 (align 8).
# 40 would be the declaration-order (repr(C)) layout, which Rust does not use.
_WIRE_JOB = TupleOf(U64, U8, TupleOf(U8, U64), BOOL, layout=Rust64Layout(32, 8))
_WIRE_STATE = TupleOf(U64, OptionOf(U64), VectorOf(_WIRE_JOB))
_STATE_LIMITS = DecodeLimits(bytes=1 << 20, allocation=1 << 20, elements=32768, work=200000)

MUTATION_TARGETS = {
    'obsolete-publication': 'controller.publication_current', 'missing-cleanup': 'controller.cleanup_required',
    'double-settlement': 'controller.settlement_once', 'dropped-cancellation-ack': 'controller.cancellation_ack',
    'duplicate-completion': 'controller.duplicate_inert', 'early-release': 'controller.release_after_settlement',
    'skipped-retry-deadline': 'controller.retry_deadline', 'ignored-mutation': 'controller.ignored_inert',
    'reordered-effects': 'controller.effect_order',
}


@dataclass(frozen=True)
class Bounds:
    generations: int = 2
    attempts: int = 2
    time: int = 2
    def __post_init__(self):
        _natural(self.generations, 'generations', 1)
        _natural(self.attempts, 'attempts', 1, 255)
        _natural(self.time, 'time', maximum=U64_MAX - 1)


@dataclass(frozen=True)
class Input:
    kind: str
    generation: int = 0
    attempt: int = 0
    outcome: str = ''
    now: int = 0


@dataclass(frozen=True)
class Effect:
    kind: str
    generation: int
    attempt: int = 0
    result: str = ''


@dataclass(frozen=True)
class Job:
    generation: int
    attempt: int
    phase: str
    resource_owned: bool = True
    deadline: int = 0
    result: str = ''


@dataclass(frozen=True)
class State:
    now: int = 0
    current: int | None = None
    jobs: tuple = ()
    def job(self, generation): return next((j for j in self.jobs if j.generation == generation), None)
    def pending(self): return sum(j.phase != 'settled' for j in self.jobs)


def _uint(value, maximum): return type(value) is int and 0 <= value <= maximum


def _valid_job_shape(job):
    if (not isinstance(job, Job) or not _uint(job.generation, U64_MAX) or not _uint(job.attempt, 255)
            or type(job.resource_owned) is not bool or not _uint(job.deadline, U64_MAX)):
        return False
    if job.phase in ('running', 'cancelling'): return job.deadline == 0 and job.result == ''
    if job.phase == 'waiting': return job.result == ''
    if job.phase == 'settled': return job.deadline == 0 and job.result in ('succeeded', 'cancelled', 'failed')
    return False


def _valid_input_shape(input):
    if (not isinstance(input, Input) or not _uint(input.generation, U64_MAX)
            or not _uint(input.attempt, 255) or not _uint(input.now, U64_MAX) or type(input.outcome) is not str):
        return False
    if input.kind == 'submit': return input == Input('submit')
    if input.kind == 'cancel': return input == Input('cancel', input.generation)
    if input.kind == 'complete': return input.outcome in ('success', 'retryable_failure') and input == Input('complete', input.generation, input.attempt, input.outcome)
    if input.kind == 'cancelled': return input == Input('cancelled', input.generation, input.attempt)
    if input.kind == 'tick': return input == Input('tick', now=input.now)
    return False


def _valid_effect_shape(effect):
    if (not isinstance(effect, Effect) or not _uint(effect.generation, U64_MAX)
            or not _uint(effect.attempt, 255) or type(effect.result) is not str): return False
    if effect.kind in ('start', 'cancel'): return effect == Effect(effect.kind, effect.generation, effect.attempt)
    if effect.kind in ('publish', 'release'): return effect == Effect(effect.kind, effect.generation)
    if effect.kind == 'settle': return effect.result in ('succeeded', 'cancelled', 'failed') and effect == Effect('settle', effect.generation, result=effect.result)
    return False


def reduce(before, input, bounds=Bounds()):
    """Apply one event and return ordered effect data without executing effects."""
    if not isinstance(bounds, Bounds) or not isinstance(before, State) or not _valid_input_shape(input):
        raise ModelError('invalid controller event, state or bounds')
    jobs, current, now, effects = list(before.jobs), before.current, before.now, []
    def index(generation):
        return next((n for n, j in enumerate(jobs) if j.generation == generation), None)
    def finish(n, result):
        nonlocal current
        job = jobs[n]
        jobs[n] = replace(job, phase='settled', resource_owned=False, deadline=0, result=result)
        effects.extend((Effect('settle', job.generation, result=result), Effect('release', job.generation)))
        if current == job.generation: current = None
    def cancel(generation):
        nonlocal current
        n = index(generation)
        if n is None: return False
        job = jobs[n]
        if job.phase == 'running':
            jobs[n] = replace(job, phase='cancelling')
            effects.append(Effect('cancel', generation, job.attempt))
            if current == generation: current = None
            return True
        if job.phase == 'waiting':
            finish(n, 'cancelled'); return True
        return False
    accepted = False
    if input.kind == 'submit' and len(jobs) < bounds.generations:
        if current is not None: cancel(current)
        generation = len(jobs) + 1
        jobs.append(Job(generation, 1, 'running'))
        current = generation
        effects.append(Effect('start', generation, 1)); accepted = True
    elif input.kind == 'cancel':
        accepted = cancel(input.generation)
    elif input.kind == 'complete' and input.outcome in ('success', 'retryable_failure'):
        n = index(input.generation)
        if n is not None and jobs[n].attempt == input.attempt:
            job = jobs[n]
            if job.phase == 'cancelling': finish(n, 'cancelled'); accepted = True
            elif job.phase == 'running':
                if input.outcome == 'success':
                    if current == input.generation: effects.append(Effect('publish', input.generation))
                    finish(n, 'succeeded')
                elif input.attempt < bounds.attempts and now < bounds.time:
                    jobs[n] = replace(job, phase='waiting', deadline=now + 1)
                else: finish(n, 'failed')
                accepted = True
    elif input.kind == 'cancelled':
        n = index(input.generation)
        if n is not None and jobs[n].attempt == input.attempt and jobs[n].phase == 'cancelling':
            finish(n, 'cancelled'); accepted = True
    elif input.kind == 'tick' and now < input.now <= bounds.time:
        now = input.now
        for n, job in enumerate(jobs):
            if job.phase == 'waiting' and job.deadline <= now:
                if job.attempt >= 255: raise ModelError('retry attempt counter overflow')
                jobs[n] = replace(job, phase='running', attempt=job.attempt + 1, deadline=0)
                effects.append(Effect('start', job.generation, job.attempt + 1))
        accepted = True
    return Transition(State(now, current, tuple(jobs)), tuple(effects),
                      Disposition() if accepted else Disposition('ignored', 'outside active request/attempt/time domain'))


@dataclass(frozen=True, init=False)
class Controller:
    bounds: Bounds
    mutation: str | None
    def __init__(self, bounds=Bounds(), mutation=None):
        if not isinstance(bounds, Bounds): raise ModelError('invalid controller bounds')
        if mutation is not None and mutation not in MUTATION_TARGETS: raise ModelError('unknown controller mutation')
        object.__setattr__(self, 'bounds', Bounds(bounds.generations, bounds.attempts, bounds.time))
        object.__setattr__(self, 'mutation', mutation)
    def metadata(self):
        return Metadata('reference-controller', build=f'python-controller-v1:{_CONTROLLER_BUILD_DIGEST}:{self.bounds!r}:{self.mutation!r}')
    def initial_state(self): return State()
    def step(self, state, input):
        after = reduce(state, input, self.bounds)
        outputs, next_state, disposition = list(after.outputs), after.state, after.disposition
        job = state.job(input.generation)
        if self.mutation == 'obsolete-publication' and input.kind == 'complete' and input.outcome == 'success' and job is not None and job.phase == 'cancelling' and state.current != input.generation:
            outputs.insert(0, Effect('publish', input.generation))
        elif self.mutation == 'missing-cleanup': outputs = [e for e in outputs if e.kind != 'release']
        elif self.mutation == 'double-settlement':
            effect = next((e for e in outputs if e.kind == 'settle'), None)
            if effect is not None: outputs.append(effect)
        elif self.mutation == 'dropped-cancellation-ack' and input.kind == 'cancelled' and disposition.kind == 'accepted':
            next_state, outputs = state, []
        elif self.mutation == 'duplicate-completion' and input.kind == 'complete' and job is not None and job.phase == 'settled':
            outputs.append(Effect('settle', input.generation, result='succeeded')); disposition = Disposition()
        elif self.mutation == 'early-release':
            effect = next((e for e in outputs if e.kind == 'cancel'), None)
            if effect is not None: outputs.append(Effect('release', effect.generation))
        elif self.mutation == 'skipped-retry-deadline' and input.kind == 'tick' and any(e.kind == 'start' for e in outputs):
            next_state, outputs = replace(next_state, jobs=state.jobs), []
        elif self.mutation == 'ignored-mutation' and disposition.kind == 'ignored' and next_state.now < self.bounds.time:
            next_state = replace(next_state, now=next_state.now + 1)
        elif self.mutation == 'reordered-effects' and any(e.kind == 'cancel' for e in outputs) and any(e.kind == 'start' for e in outputs):
            outputs.reverse()
        return Transition(next_state, tuple(outputs), disposition)
    def inputs(self, state):
        yield Input('submit')
        for generation in range(len(state.jobs) + 1):
            yield Input('cancel', generation)
            for attempt in range(1, self.bounds.attempts + 1):
                yield Input('cancelled', generation, attempt)
                for outcome in ('success', 'retryable_failure'):
                    yield Input('complete', generation, attempt, outcome)
        for now in range(self.bounds.time + 1): yield Input('tick', now=now)
    def generate(self, state, rng):
        per_generation = 1 + 3 * self.bounds.attempts
        generation_count = (len(state.jobs) + 1) * per_generation
        total = 1 + generation_count + self.bounds.time + 1
        if total > U64_MAX: raise ModelError('controller input domain exceeds u64')
        index = rng.index(total)
        if index == 0: return Input('submit')
        index -= 1
        if index < generation_count:
            generation, offset = divmod(index, per_generation)
            if offset == 0: return Input('cancel', generation)
            attempt, variant = divmod(offset - 1, 3)
            if variant == 0: return Input('cancelled', generation, attempt + 1)
            return Input('complete', generation, attempt + 1, ('success', 'retryable_failure')[variant - 1])
        return Input('tick', now=index - generation_count)
    def is_enabled(self, state, input):
        if not _valid_input_shape(input): return False
        if input.kind == 'submit': return True
        if input.kind == 'tick': return input.now <= self.bounds.time
        if input.generation > len(state.jobs): return False
        return input.kind == 'cancel' or 1 <= input.attempt <= self.bounds.attempts
    def encode_state(self, state):
        phases = {'running': 0, 'cancelling': 1, 'waiting': 2}
        results = {'succeeded': 3, 'cancelled': 4, 'failed': 5}
        wire = []
        for job in state.jobs:
            if not _valid_job_shape(job): raise ModelError('invalid/noncanonical controller job')
            if job.phase == 'settled' and job.result in results: phase = (results[job.result], 0)
            elif job.phase in phases: phase = (phases[job.phase], job.deadline if job.phase == 'waiting' else 0)
            else: raise ModelError('invalid controller phase')
            wire.append((job.generation, job.attempt, phase, job.resource_owned))
        return encode_canonical(_WIRE_STATE, (state.now, None if state.current is None else Some(state.current), wire), maximum=1 << 20)
    def decode_state(self, data):
        now, current, wire = decode_canonical(_WIRE_STATE, data, _STATE_LIMITS)
        current = None if current is None else current.value
        if len(wire) > self.bounds.generations or now > self.bounds.time or (current is not None and not 0 < current <= len(wire)):
            raise ModelError('state exceeds declared domain')
        jobs = []
        for n, (generation, attempt, (tag, deadline), owned) in enumerate(wire):
            if generation != n + 1 or not 0 < attempt <= self.bounds.attempts: raise ModelError('invalid generation/attempt')
            if tag == 2:
                if deadline > self.bounds.time: raise ModelError('deadline outside domain')
                job = Job(generation, attempt, 'waiting', owned, deadline)
            elif deadline != 0 or tag not in (0, 1, 3, 4, 5): raise ModelError('invalid/noncanonical phase')
            elif tag in (0, 1): job = Job(generation, attempt, ('running', 'cancelling')[tag], owned)
            else: job = Job(generation, attempt, 'settled', owned, result={3: 'succeeded', 4: 'cancelled', 5: 'failed'}[tag])
            jobs.append(job)
        return State(now, current, tuple(jobs))
    def encode_input(self, input):
        if not _valid_input_shape(input): raise ModelError('invalid/noncanonical controller input')
        if input.kind == 'submit': wire = (0, 0, 0, 0)
        elif input.kind == 'cancel': wire = (1, input.generation, 0, 0)
        elif input.kind == 'complete' and input.outcome in ('success', 'retryable_failure'):
            wire = (2, input.generation, input.attempt, int(input.outcome == 'retryable_failure'))
        elif input.kind == 'cancelled': wire = (3, input.generation, input.attempt, 0)
        elif input.kind == 'tick': wire = (4, input.now, 0, 0)
        else: raise ModelError('invalid controller input')
        return encode_canonical(_WIRE_INPUT, wire, maximum=32)
    def decode_input(self, data):
        tag, value, attempt, outcome = decode_canonical(_WIRE_INPUT, data)
        if (tag, value, attempt, outcome) == (0, 0, 0, 0): return Input('submit')
        if tag == 1 and value <= self.bounds.generations and attempt == outcome == 0: return Input('cancel', value)
        if tag == 2 and value <= self.bounds.generations and 0 < attempt <= self.bounds.attempts and outcome in (0, 1):
            return Input('complete', value, attempt, ('success', 'retryable_failure')[outcome])
        if tag == 3 and value <= self.bounds.generations and 0 < attempt <= self.bounds.attempts and outcome == 0:
            return Input('cancelled', value, attempt)
        if tag == 4 and value <= self.bounds.time and attempt == outcome == 0: return Input('tick', now=value)
        raise ModelError('invalid/noncanonical input')
    def encode_output(self, output):
        if not _valid_effect_shape(output): raise ModelError('invalid/noncanonical controller effect')
        if output.kind in ('start', 'cancel'): wire = ((0 if output.kind == 'start' else 1), output.generation, output.attempt, 0)
        elif output.kind == 'publish': wire = (2, output.generation, 0, 0)
        elif output.kind == 'settle' and output.result in ('succeeded', 'cancelled', 'failed'):
            wire = (3, output.generation, 0, ('succeeded', 'cancelled', 'failed').index(output.result))
        elif output.kind == 'release': wire = (4, output.generation, 0, 0)
        else: raise ModelError('invalid controller effect')
        return encode_canonical(_WIRE_INPUT, wire, maximum=32)
    def check_state(self, state):
        if (not isinstance(state, State) or not _uint(state.now, U64_MAX) or type(state.jobs) is not tuple
                or (state.current is not None and not _uint(state.current, U64_MAX))
                or any(not _valid_job_shape(job) for job in state.jobs)):
            return (Check('controller.state_valid', 'failed', 'independent controller obligation was violated'),)
        valid = state.now <= self.bounds.time and len(state.jobs) <= self.bounds.generations
        for n, job in enumerate(state.jobs):
            valid &= (job.generation == n + 1 and 0 < job.attempt <= self.bounds.attempts
                      and job.resource_owned != (job.phase == 'settled')
                      and (job.phase != 'waiting' or state.now < job.deadline <= self.bounds.time and job.attempt < self.bounds.attempts))
        if state.current is not None:
            job = state.job(state.current)
            valid &= job is not None and job.phase in ('running', 'waiting')
        return (Check('controller.state_valid') if valid else
                Check('controller.state_valid', 'failed', 'independent controller obligation was violated'),)

    def check_transition(self, before, input, after):
        """Independent safety and progress checks over the observed transition."""
        if not _valid_input_shape(input): raise ModelError('invalid controller input')
        out, state = after.outputs, after.state
        count = out.count
        prior = before.job(input.generation)
        publication = all(e.kind != 'publish' or (before.current == e.generation and input.kind == 'complete'
                          and input.generation == e.generation and input.outcome == 'success'
                          and prior is not None and prior.phase == 'running' and prior.attempt == input.attempt) for e in out)
        successful = not (input.kind == 'complete' and input.outcome == 'success' and before.current == input.generation
                          and prior is not None and prior.phase == 'running' and prior.attempt == input.attempt) or count(Effect('publish', input.generation)) == 1
        cleanup = all(not (j.phase == 'settled' and (old := before.job(j.generation)) is not None and old.phase != 'settled')
                      or count(Effect('release', j.generation)) == 1 for j in state.jobs)
        settlement = all(e.kind != 'settle' or ((old := before.job(e.generation)) is not None and old.phase != 'settled'
                         and (new := state.job(e.generation)) is not None and new.phase == 'settled' and new.result == e.result
                         and sum(x.kind == 'settle' and x.generation == e.generation for x in out) == 1) for e in out)
        settlement &= all(not (j.phase == 'settled' and (old := before.job(j.generation)) is not None and old.phase != 'settled')
                          or count(Effect('settle', j.generation, result=j.result)) == 1 for j in state.jobs)
        ack = not (input.kind == 'cancelled' and prior is not None and prior.phase == 'cancelling' and prior.attempt == input.attempt) or (
            (new := state.job(input.generation)) is not None and new.phase == 'settled' and new.result == 'cancelled'
            and count(Effect('settle', input.generation, result='cancelled')) == 1)
        duplicate = not (input.kind == 'complete' and (prior is None or prior.attempt != input.attempt or prior.phase not in ('running', 'cancelling'))) or (
            before == state and not out and after.disposition.kind == 'ignored')
        release = all(e.kind != 'release' or (any(x.kind == 'settle' and x.generation == e.generation for x in out[:n])
                      and (new := state.job(e.generation)) is not None and new.phase == 'settled' and not new.resource_owned)
                      for n, e in enumerate(out))
        retry = not (input.kind == 'tick' and before.now < input.now <= self.bounds.time) or all(
            j.phase != 'waiting' or j.deadline > input.now or (j.attempt < 255 and (new := state.job(j.generation)) is not None
            and new.attempt == j.attempt + 1 and new.phase == 'running' and count(Effect('start', j.generation, j.attempt + 1)) == 1)
            for j in before.jobs)
        ignored = after.disposition.kind != 'ignored' or (before == state and not out)
        old = before.job(before.current) if before.current is not None else None
        order = True
        if input.kind == 'submit' and old is not None and old.phase == 'running' and len(state.jobs) != len(before.jobs):
            cancel_index = next((n for n, e in enumerate(out) if e.kind == 'cancel' and e.generation == old.generation), None)
            start_index = next((n for n, e in enumerate(out) if e.kind == 'start' and e.generation == state.current), None)
            order = cancel_index is not None and start_index is not None and cancel_index < start_index
        def terminal(generation, result):
            j = state.job(generation)
            return (j is not None and j.phase == 'settled' and j.result == result and not j.resource_owned
                    and state.current != generation and count(Effect('settle', generation, result=result)) == 1
                    and count(Effect('release', generation)) == 1)
        def cancellation(generation):
            old = before.job(generation)
            if old is None: return True
            if old.phase == 'waiting': return terminal(generation, 'cancelled')
            if old.phase != 'running': return True
            j = state.job(generation)
            return (j is not None and j.phase == 'cancelling' and j.attempt == old.attempt and j.resource_owned
                    and state.current != generation and count(Effect('cancel', generation, old.attempt)) == 1)
        completion = True
        if input.kind == 'complete' and prior is not None and prior.attempt == input.attempt:
            if prior.phase == 'cancelling': completion = after.disposition.kind == 'accepted' and terminal(input.generation, 'cancelled')
            elif prior.phase == 'running':
                if input.outcome == 'success': progress = terminal(input.generation, 'succeeded')
                elif input.attempt < self.bounds.attempts and before.now < self.bounds.time:
                    new = state.job(input.generation)
                    progress = (new is not None and new.phase == 'waiting' and new.deadline == before.now + 1
                                and new.attempt == input.attempt and new.resource_owned and state.current == before.current)
                else: progress = terminal(input.generation, 'failed')
                completion = after.disposition.kind == 'accepted' and progress
        cancellation_progress = not (input.kind == 'cancel' and prior is not None and prior.phase in ('running', 'waiting')) or (
            after.disposition.kind == 'accepted' and cancellation(input.generation))
        submission = True
        if input.kind == 'submit' and len(before.jobs) < self.bounds.generations:
            generation = len(before.jobs) + 1
            new = state.job(generation)
            submission = (after.disposition.kind == 'accepted' and len(state.jobs) == len(before.jobs) + 1
                          and state.current == generation and new is not None and new.attempt == 1
                          and new.phase == 'running' and new.resource_owned and count(Effect('start', generation, 1)) == 1
                          and (before.current is None or cancellation(before.current)))
        ids = ('publication_current', 'success_published', 'cleanup_required', 'settlement_once', 'cancellation_ack',
               'duplicate_inert', 'release_after_settlement', 'retry_deadline', 'ignored_inert', 'effect_order',
               'completion_progress', 'cancellation_progress', 'submission_progress')
        values = (publication, successful, cleanup, settlement, ack, duplicate, release, retry, ignored, order,
                  completion, cancellation_progress, submission)
        return tuple(Check('controller.' + id) if holds else Check('controller.' + id, 'failed',
                     'independent controller obligation was violated') for id, holds in zip(ids, values))


COVER_GOALS = ('supersede_running', 'stale_success', 'cancel_ack', 'retry_started',
               'duplicate_completion', 'settled_cleanup')


def _read_catalog_bytes(name):
    from importlib.resources import files
    return files('statelessness').joinpath('controller_catalog', name).read_bytes()


def _reference_source_bytes():
    from importlib.resources import files
    return files('statelessness').joinpath('reference_controller.py').read_bytes()


def _fingerprint(data):
    value = 0xcbf29ce484222325
    for byte in data: value = ((value ^ byte) * 0x100000001b3) & U64_MAX
    return f'fnv1a64:{value:016x}'


_CONTROLLER_CATALOG = {name: _read_catalog_bytes(name) for name in
                       ('controller-properties.txt', 'controller.json', 'controller-witnesses.txt')}
_CONTROLLER_BUILD_DIGEST = _fingerprint(_reference_source_bytes() + _CONTROLLER_CATALOG['controller-properties.txt'] +
                                       _CONTROLLER_CATALOG['controller.json'])


def _catalog_bytes(name):
    return _CONTROLLER_CATALOG[name]


def manifest(model):
    """Read the separately packaged requirement catalog, not emitted checks."""
    from .assurance import Manifest, Provenance, RequiredProperty, InputFamily, CoverGoal
    properties = _catalog_bytes('controller-properties.txt')
    declarations = []
    for line in properties.decode('utf-8').splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[0] not in ('state', 'transition'):
            raise ModelError('invalid independent property declaration')
        declarations.append(RequiredProperty(parts[1], parts[0]))
    bounds = (('max_generations', str(model.bounds.generations)), ('max_attempts', str(model.bounds.attempts)),
              ('max_time', str(model.bounds.time)),
              ('payload', 'outcome abstraction; no real file/network effects during verification'),
              ('delivery', 'unknown/stale/duplicate deliveries included; eventual delivery not proven'))
    result = Manifest('reference-controller', Provenance(_fingerprint(_catalog_bytes('controller.json')),
                      model.metadata().build, _fingerprint(properties)), tuple(declarations),
                      tuple(InputFamily(name) for name in ('submit', 'cancel', 'complete', 'cancelled', 'tick')),
                      bounds, tuple(CoverGoal(id) for id in COVER_GOALS))
    result.validate()
    return result


class ReferenceCoverage:
    def observe(self, before, input, after, history, hits):
        hits.family(input.kind)
        current = before.job(before.current) if before.current is not None else None
        job = before.job(input.generation)
        if input.kind == 'submit' and current is not None and current.phase == 'running' and len(after.state.jobs) > len(before.jobs):
            hits.goal('supersede_running')
        if input.kind == 'complete' and input.outcome == 'success' and before.current != input.generation and job is not None and job.phase == 'cancelling':
            hits.goal('stale_success')
        if input.kind == 'cancelled' and job is not None and job.phase == 'cancelling' and job.attempt == input.attempt:
            hits.goal('cancel_ack')
        if input.kind == 'tick' and any(e.kind == 'start' and e.attempt > 1 for e in after.outputs): hits.goal('retry_started')
        if input.kind == 'complete' and job is not None and job.phase == 'settled': hits.goal('duplicate_completion')
        if any(e.kind == 'release' for e in after.outputs): hits.goal('settled_cleanup')


@dataclass(frozen=True)
class MutationResult:
    mutation: str
    status: str
    reason: str | None = None
    search: str | None = None
    original: object | None = None
    minimized: object | None = None
    shrink_complete: bool = False


def evaluate_mutation(mutation, config=None):
    """Search and shrink only the named target; cutoffs never imply a kill."""
    from .core import Failure, SearchConfig, enumerate_states
    from .search import shrink
    config = SearchConfig() if config is None else config
    if mutation not in MUTATION_TARGETS: raise ModelError('unknown controller mutation')
    target = MUTATION_TARGETS[mutation]
    result = MutationResult(mutation, 'not_evaluated', 'evaluation not started')
    try:
        if target not in {p.id for p in manifest(Controller()).required_properties}:
            raise ModelError('mutation target was removed from the property catalog')
        model = Controller(mutation=mutation)
        report = enumerate_states(model, config)
        result = replace(result, search=report.termination)
        if report.failure is None:
            if report.termination == 'graph_exhausted' and report.skipped_checks == 0:
                return replace(result, status='survives', reason=None)
            # Rust formats the termination with {:?} (e.g. DepthBound).
            termination = ''.join(part.capitalize() for part in str(report.termination).split('_'))
            return replace(result, reason=f'incomplete search: {termination}; skipped={report.skipped_checks}')
        failure = report.failure
        position = next((n for n, v in enumerate(failure.violations) if v.check.id == target and v.phase == 'transition'), None)
        if position is None: raise ModelError('search found an unrelated failure before the required target')
        violations = list(failure.violations)
        violations[0], violations[position] = violations[position], violations[0]
        failure = Failure(failure.inputs, tuple(violations))
        result = replace(result, original=failure)
        minimized = shrink(model, failure)
        if not minimized.validated_original or not any(v.phase == 'transition' and v.check.id == target and v.check.status == 'failed'
                                                       for v in minimized.minimized.violations):
            raise ModelError('target property did not survive causal replay/shrinking')
        return replace(result, status='killed', reason=None, minimized=minimized.minimized,
                       shrink_complete=minimized.termination == 'search_complete')
    except Exception as error:
        # Rust evaluate_mutation maps every evaluation error to NotEvaluated.
        # BaseException (interrupts, exits) still propagates.
        from .core import _error_detail
        return replace(result, status='not_evaluated', reason=_error_detail(error))


def replay_mutation_witness(mutation, failure):
    """Record and exactly replay a failure, requiring the mutation's target."""
    from .core import record, replay
    if mutation not in MUTATION_TARGETS: raise ModelError('unknown controller mutation')
    model = Controller(mutation=mutation)
    trace = record(model, failure.inputs, max_steps=len(failure.inputs))
    report = replay(model, trace)
    checks = tuple(trace.initial_checks) + tuple(check for step in trace.steps for check in step.checks)
    if (trace.termination != 'property_failed' or report.outcome != 'exact' or not report.failure_reproduced
            or not any(check.id == MUTATION_TARGETS[mutation] and check.status == 'failed' for check in checks)):
        raise ModelError('recorded witness did not exactly replay the required target')
    return trace


def regression_corpus(text):
    """Parse the independent complete mutation witness catalog."""
    if not isinstance(text, str): raise ModelError('regression corpus must be text')
    cases, seen = [], set()
    def number(value, maximum):
        if not value.isascii() or not value.isdecimal() or len(value) > 20:
            raise ModelError('bad regression integer')
        parsed = int(value)
        if parsed > maximum: raise ModelError('bad regression integer')
        return parsed
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1]
            if name not in MUTATION_TARGETS: raise ModelError('unknown regression mutation')
            if name in seen: raise ModelError('duplicate regression mutation')
            seen.add(name); cases.append((name, [])); continue
        if not cases: raise ModelError('regression input before section')
        parts = line.split()
        if parts == ['submit']: event = Input('submit')
        elif len(parts) == 2 and parts[0] == 'cancel': event = Input('cancel', number(parts[1], U64_MAX))
        elif len(parts) == 3 and parts[0] == 'cancelled': event = Input('cancelled', number(parts[1], U64_MAX), number(parts[2], 255))
        elif len(parts) == 4 and parts[0] == 'complete' and parts[3] in ('success', 'retry'):
            event = Input('complete', number(parts[1], U64_MAX), number(parts[2], 255), 'success' if parts[3] == 'success' else 'retryable_failure')
        elif len(parts) == 2 and parts[0] == 'tick': event = Input('tick', now=number(parts[1], U64_MAX))
        else: raise ModelError('bad regression input')
        cases[-1][1].append(event)
    if seen != set(MUTATION_TARGETS) or any(not inputs for _, inputs in cases):
        raise ModelError('missing/empty required regression cases')
    return tuple((name, tuple(inputs)) for name, inputs in cases)


def check_regressions(text=None):
    """Require each target to replay exactly and its fixed control to pass."""
    from .core import record, replay
    if text is None: text = _catalog_bytes('controller-witnesses.txt').decode('utf-8')
    for mutation, inputs in regression_corpus(text):
        model = Controller(mutation=mutation)
        trace = record(model, inputs, max_steps=len(inputs))
        report = replay(model, trace)
        checks = tuple(trace.initial_checks) + tuple(c for step in trace.steps for c in step.checks)
        if (trace.termination != 'property_failed' or report.outcome != 'exact' or not report.failure_reproduced
                or not any(c.id == MUTATION_TARGETS[mutation] and c.status == 'failed' for c in checks)):
            raise ModelError('recorded witness did not exactly replay the required target')
        fixed = record(Controller(), inputs, max_steps=len(inputs))
        if fixed.termination != 'completed' or any(c.status != 'passed' for c in fixed.initial_checks) or any(
                c.status != 'passed' for step in fixed.steps for c in step.checks):
            raise ModelError('fixed reducer failed a retained counterexample')
    return True
