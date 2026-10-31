"""Sealed check-once results for application-owned execution.

Payloads are detached snapshots; the model is retained by reference. Sealing is
an API integrity boundary, not a security sandbox against Python reflection.
"""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
from .core import (Check, CheckPolicy, ModelError, Transition, _copy, _natural,
                   _dispatch_checks, _check_observed_count_into)

_initials = WeakKeyDictionary()
_turns = WeakKeyDictionary()


class _Sealed:
    __slots__ = ("__weakref__",)
    def __new__(cls, *args, **kwargs):
        raise TypeError("checked results are created by check_initial/check_turn")
    def __setattr__(self, name, value):
        raise AttributeError("checked results are read-only")
    def __copy__(self): return self
    def __deepcopy__(self, memo): return self
    def __reduce_ex__(self, protocol):
        raise TypeError("checked results cannot be serialized as authority")


class CheckedInitial(_Sealed):
    """Complete checks bound to one detached supplied checkpoint."""
    __slots__ = ()
    def __init_subclass__(cls, **kwargs): raise TypeError("CheckedInitial cannot be subclassed")
    @property
    def model(self): return _initial_context(self)[0]
    @property
    def state(self): return _copy(_initial_context(self)[1])
    @property
    def checks(self): return _copy(_initial_context(self)[2])
    @property
    def state_check_count(self): return len(_initial_context(self)[2])
    def into_checks(self):
        """Return detached checks. Python results remain reusable, like a borrow."""
        return self.checks


class CheckedTurn(_Sealed):
    """Complete checks, coverage and sequence bound to one detached turn."""
    __slots__ = ()
    def __init_subclass__(cls, **kwargs): raise TypeError("CheckedTurn cannot be subclassed")
    @property
    def model(self): return _turn_context(self)[0]
    @property
    def before(self): return _copy(_turn_context(self)[1])
    @property
    def input(self): return _copy(_turn_context(self)[2])
    @property
    def transition(self): return _copy(_turn_context(self)[3])
    @property
    def sequence(self): return _turn_context(self)[4]
    @property
    def checks(self): return _copy(_turn_context(self)[5])
    @property
    def state_check_count(self): return _turn_context(self)[6]
    @property
    def full_checking(self): return _turn_context(self)[7]
    def into_checks(self): return self.checks
    def observation(self):
        _, before, input, transition, sequence, checks, boundary, _ = _turn_context(self)
        before, input, transition, checks = _copy((before, input, transition, checks))
        return TurnObservation(sequence, before, input, transition, checks, boundary)


@dataclass(frozen=True)
class TurnObservation:
    """Detached diagnostics only; not authority for checked recording."""
    sequence: int
    before: object
    input: object
    transition: Transition
    checks: tuple[Check, ...]
    state_check_count: int


def _initial_context(token):
    if type(token) is not CheckedInitial or token not in _initials:
        raise ModelError("expected an authentic CheckedInitial result")
    return _initials[token]


def _turn_context(token):
    if type(token) is not CheckedTurn or token not in _turns:
        raise ModelError("expected an authentic CheckedTurn result")
    return _turns[token]


def check_initial(model, state):
    """Check a supplied checkpoint once, without initial_state, step or codecs."""
    state = _copy(state)
    checks = []
    _dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), checks, "initial check")
    token = object.__new__(CheckedInitial)
    _initials[token] = (model, state, tuple(checks))
    return token


def check_turn(model, before, input, transition, sequence, policy=CheckPolicy()):
    """Check an executed transition once; positive sequences cover full u64."""
    _natural(sequence, "sequence")
    if sequence == 0: raise ModelError("transition sequence must be positive")
    if not isinstance(transition, Transition): raise ValueError("transition must be a Transition")
    if not isinstance(policy, CheckPolicy): raise ValueError("policy must be CheckPolicy")
    # Revalidate a supplied policy's primitive values before any callback.
    policy = CheckPolicy(policy.state_every, policy.transition_checks)
    before, input, transition = _copy((before, input, transition))
    checks = []
    boundary = _check_observed_count_into(model, before, input, transition, sequence, checks, policy)
    token = object.__new__(CheckedTurn)
    _turns[token] = (model, before, input, transition, sequence, tuple(checks), boundary,
                     policy.state_every == 1 and policy.transition_checks)
    return token
