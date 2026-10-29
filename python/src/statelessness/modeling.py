"""Explicit bounded modeling helpers. No reflection, codec or debugger dependency."""
from dataclasses import dataclass
import inspect
from typing import Generic, TypeVar
from .core import Check, ModelError, _copy, _call, _natural
from .search import Auto, AutoOptions, EXHAUSTED

I = TypeVar('I')

@dataclass(frozen=True)
class DomainDescriptor:
    name: str
    assumptions: str
    max_entries: int

    def __post_init__(self):
        if not isinstance(self.name, str) or not isinstance(self.assumptions, str):
            raise ValueError('domain name and assumptions must be strings')
        _natural(self.max_entries, 'max_entries')

    def parameters(self):
        return [('model.domain.name', self.name), ('model.domain.assumptions', self.assumptions),
                ('model.domain.max_entries', str(self.max_entries))]

class Domain(Generic[I]):
    """Owned ordered entries. extend keeps the successful prefix on failure."""
    def __init__(self, descriptor: DomainDescriptor):
        if not isinstance(descriptor, DomainDescriptor):
            raise ValueError('domain descriptor required')
        self.__descriptor, self.__entries = descriptor, []

    @property
    def descriptor(self): return self.__descriptor
    @property
    def entries(self): return tuple(_copy(self.__entries))
    def into_entries(self):
        result, self.__entries = self.__entries, []
        return result
    def push(self, value):
        if len(self.__entries) >= self.descriptor.max_entries:
            raise ModelError('domain entry limit exceeded')
        value = _copy(value)
        if len(self.__entries) >= self.descriptor.max_entries:
            raise ModelError('domain entry limit exceeded')
        self.__entries.append(value)
    def extend(self, values):
        iterator = _call('domain iterator', iter, values)
        end = object()
        while True:
            value = _call('domain next', next, iterator, end)
            if value is end: return
            self.push(value)
    def contains(self, value): return _call('domain equality', lambda: value in self.entries)
    def sample_indexed(self, rng):
        """One unbiased index; EXHAUSTED is distinct from a valid None entry."""
        index = rng.index(len(self.__entries))
        return EXHAUSTED if index is None else _copy(self.__entries[index])
    @staticmethod
    def product(a, b, maximum, map):
        _natural(maximum, 'maximum')
        if len(a) * len(b) > maximum:
            raise ModelError('domain product limit exceeded')
        a, b = tuple(a), tuple(b)
        return [_copy(_call('domain product', map, _copy(x), _copy(y))) for x in a for y in b]

def check_lazy(id, passed, details):
    if type(passed) is not bool: raise ValueError('check predicate must be boolean')
    return Check(id) if passed else Check(id, 'failed', _call('check details', details))

@dataclass(frozen=True)
class PropertyMetadata:
    id: str
    phase: str = 'state'
    def __post_init__(self):
        if not isinstance(self.id, str) or self.phase not in ('state', 'transition'):
            raise ValueError('property requires string id and state/transition phase')

def declared_properties(properties):
    values = tuple(properties)
    if any(not isinstance(p, PropertyMetadata) for p in values):
        raise ValueError('property metadata required')
    if len({p.id for p in values}) != len(values): raise ValueError('duplicate property id')
    return values

class ModelAdapter:
    """Validate an explicit definition, preserving bound methods and codec binders.

    Catalogs describe declared properties, not evidence that checks ran. Explicit
    unchecked is needed with an empty catalog. Callbacks remain synchronous.
    """
    def __init__(self, model, *, properties=(), initial_default=None, unchecked=False):
        self.declared_properties = declared_properties(properties)
        if not self.declared_properties and not unchecked: raise ValueError('no properties; select unchecked')
        for role in ('metadata', 'step'):
            if not callable(getattr(model, role, None)): raise ValueError(f'{role} callback required')
        if initial_default is not None and hasattr(model, 'initial_state'):
            raise ValueError('initial_default conflicts with initial callback')
        initial = initial_default if initial_default is not None else getattr(model, 'initial_state', None)
        if not callable(initial): raise ValueError('initial callback or initial_default factory required')
        if not unchecked and not any(callable(getattr(model, n, None)) for n in ('check_state', 'check_state_into', 'check_transition', 'check_transition_into')):
            raise ValueError('property checker required')
        for role in ('metadata', 'step', 'initial_state', 'check_state', 'check_state_into', 'check_transition', 'check_transition_into', 'inputs'):
            callback = getattr(model, role, None)
            if inspect.iscoroutinefunction(callback) or inspect.isasyncgenfunction(callback):
                raise ValueError(f'{role} must be synchronous')
        if inspect.iscoroutinefunction(initial) or inspect.isasyncgenfunction(initial):
            raise ValueError('initial_default must be synchronous')
        self.__model, self.__initial = model, initial
    def __getattr__(self, name): return getattr(self.__model, name)
    def initial_state(self): return _copy(_call('initial state', self.__initial))
    def check_state(self, state):
        callback = getattr(self.__model, 'check_state', None)
        return () if callback is None else callback(state)
    def automatic(self, options=AutoOptions()):
        if not callable(getattr(self, 'inputs', None)): raise ValueError('generate requires inputs callback')
        return Auto(self, options)
