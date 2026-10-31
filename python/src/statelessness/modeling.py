"""Explicit bounded modeling helpers. No reflection, codec or debugger dependency."""
from dataclasses import dataclass
import inspect
from typing import Generic, TypeVar
from .core import Check, ModelError, _copy, _call, _natural, _step, _dispatch_checks
from .search import Auto, AutoOptions, EXHAUSTED

I = TypeVar('I')

def _runtime_resource_digest(root):
    """Deterministic package-content identity over Python sources and data."""
    import hashlib
    files=[]
    def collect(directory,prefix):
        for item in directory.iterdir():
            name=prefix+item.name
            if item.is_dir():
                if item.name!='__pycache__': collect(item,name+'/')
            elif item.name.endswith(('.py','.json','.txt')):
                files.append((name,item))
    collect(root,'')
    result=hashlib.sha256()
    for name,item in sorted(files,key=lambda entry:entry[0]):
        data_hash=hashlib.sha256()
        with item.open('rb') as stream:
            for chunk in iter(lambda:stream.read(65536),b''): data_hash.update(chunk)
        encoded=name.encode('utf8')
        result.update(len(encoded).to_bytes(8,'little'));result.update(encoded);result.update(data_hash.digest())
    return 'python-package-sha256:'+result.hexdigest()


def runtime_build_id():
    """Hash the current packaged source/data files, not loaded Python code.

    Calculated on demand so normal package imports do not read optional modules.
    Files must remain stable during the walk; concurrent edits can produce a
    mixed digest. Imported code can differ from files edited after import. This
    is not an authenticity proof and excludes interpreter, native extension,
    environment and bytecode identity.
    """
    from importlib.resources import files
    return _runtime_resource_digest(files('statelessness'))

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
    def entries(self):
        # Freeze the sequence before user-defined deepcopy hooks can consume or
        # modify the domain's backing list. Payload copying remains cooperative.
        return _copy(tuple(self.__entries))
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
    def contains(self, value):
        """Search a captured sequence, copying each entry only when it is reached.

        An entry copy failure is raised only if that entry is reached, so an
        earlier match wins. Copy and equality hooks must cooperate: a hook may
        still mutate a later entry object before it is copied, and concurrent
        or cross-alias mutation is not made atomic.
        """
        if not self.__entries:
            return False
        # Capture the entry sequence (references only) before application
        # copy/equality hooks can mutate this Domain's backing list.
        originals = tuple(self.__entries)
        candidate = _copy(value)
        memo = {}
        for original in originals:
            entry = _copy(original, memo)
            if _call('domain equality', lambda: entry == _copy(candidate)):
                return True
        return False
    def sample_indexed(self, rng):
        """Sample a captured sequence and copy only the selected entry.

        Unselected entries are never copied, so their copy failures cannot
        preempt a draw. The selected entry is copied after the draw.
        """
        # Shallow reference snapshot: the rng callback cannot change the
        # population or the selected slot.
        entries = tuple(self.__entries)
        index = _call('domain indexed sample', rng.index, len(entries))
        if index is None and not entries: return EXHAUSTED
        if type(index) is not int or not 0 <= index < len(entries):
            raise ModelError('domain sample index outside snapshot')
        return _copy(entries[index])
    @staticmethod
    def product(a, b, maximum, map):
        _natural(maximum, 'maximum')
        # Built-in sequences cannot lie about emptiness. Avoid inspecting the
        # other side, which cannot contribute a value to the product.
        # Custom sequences still require bounded iteration below because their
        # reported length need not agree with their iterator.
        if (type(a) in (list, tuple) and not a) or (type(b) in (list, tuple) and not b):
            return []
        if len(a) * len(b) > maximum:
            raise ModelError('domain product limit exceeded')
        # A custom __len__ above may have grown the other input. Retain at most
        # maximum+1 slots: the extra slot is enough to detect an over-limit
        # product once the other side is known to be nonempty. This also keeps
        # snapshot allocation bounded before application deepcopy callbacks.
        if type(a) in (list, tuple): a = tuple(a[:maximum + 1])
        if type(b) in (list, tuple): b = tuple(b[:maximum + 1])
        # A Python sequence can report a smaller __len__ than its iterator
        # actually produces. Bound actual pulls before invoking the mapper.
        sentinel = object()
        right_iter = _call('domain product iterator', iter, b)
        first = _call('domain product next', next, right_iter, sentinel)
        if first is sentinel: return []
        left_iter = _call('domain product iterator', iter, a)
        left = []
        while True:
            value = _call('domain product next', next, left_iter, sentinel)
            if value is sentinel: break
            if len(left) >= maximum: raise ModelError('domain product limit exceeded')
            left.append(_copy(value))
        if not left: return []
        right = [_copy(first)]
        bound = maximum // len(left)
        if not bound: raise ModelError('domain product limit exceeded')
        while True:
            value = _call('domain product next', next, right_iter, sentinel)
            if value is sentinel: break
            if len(right) >= bound: raise ModelError('domain product limit exceeded')
            right.append(_copy(value))
        return [_copy(_call('domain product', map, _copy(x), _copy(y))) for x in left for y in right]

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
    def step(self, state, input): return _step(self.__model, state, input)
    def check_state(self, state):
        checks = []
        self.check_state_into(state, checks)
        return tuple(checks)
    def check_state_into(self, state, sink):
        _dispatch_checks(self.__model, 'check_state_into', 'check_state', (_copy(state),),
                         sink, 'model adapter state check', optional=True)
    def check_transition(self, before, input, transition):
        checks = []
        self.check_transition_into(before, input, transition, checks)
        return tuple(checks)
    def check_transition_into(self, before, input, transition, sink):
        _dispatch_checks(self.__model, 'check_transition_into', 'check_transition',
                         (_copy(before), _copy(input), _copy(transition)), sink,
                         'model adapter transition check', optional=True)
    def automatic(self, options=AutoOptions()):
        if not callable(getattr(self, 'inputs', None)): raise ValueError('generate requires inputs callback')
        return Auto(self, options)
