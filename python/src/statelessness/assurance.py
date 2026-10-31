"""Experimental N10 assurance observer and strict assessment.

The independent manifest is a requirements catalog, not a generator or proof.
Use Observer with the existing search observer hooks, then finish with the actual
search outcome. Assured campaigns use explicit process policies for multiworker runs.
"""
from dataclasses import dataclass, replace
from .core import Metadata, ModelError, U64_MAX, _copy

ASSURANCE_VERSION = 1
DEFAULT_MAX_ASSURANCE_JOBS = 100_000
DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_COVERAGE_HITS = 4096
MAX_COVERAGE_IDENTIFIER_BYTES = 4096
MAX_ASSURANCE_ERRORS = 1024
MAX_OBSERVED_PROPERTIES = 4096
MAX_PROPERTY_IDENTIFIER_BYTES = 4096
MAX_MANIFEST_ITEMS = 8192
MAX_MANIFEST_TEXT_BYTES = 1_048_576
MAX_REPORT_TEXT_BYTES = 4 * 1024 * 1024

def _manifest_preflight(manifest):
    collections = (manifest.required_properties, manifest.input_families, manifest.cover_goals, manifest.bounds)
    if any(type(items) not in (tuple, list) or len(items) > MAX_MANIFEST_ITEMS for items in collections):
        raise ModelError('assurance manifest catalog limit exceeded or invalid catalog')
    if not isinstance(manifest.provenance, Provenance): raise ModelError('invalid assurance provenance')
    total = 0
    def text(value):
        nonlocal total
        if type(value) is not str or len(value) > MAX_PROPERTY_IDENTIFIER_BYTES:
            raise ModelError('assurance manifest text limit exceeded or invalid text')
        try: size = len(value.encode('utf-8'))
        except UnicodeError: raise ModelError('invalid assurance manifest text') from None
        if size > MAX_PROPERTY_IDENTIFIER_BYTES: raise ModelError('assurance manifest text limit exceeded')
        total += size
        if total > MAX_MANIFEST_TEXT_BYTES: raise ModelError('assurance manifest total text limit exceeded')
    for value in (manifest.model, manifest.provenance.manifest, manifest.provenance.model,
                  manifest.provenance.properties, manifest.required_search_outcome): text(value)
    for p in manifest.required_properties:
        if not isinstance(p, RequiredProperty): raise ModelError('invalid required property')
        text(p.id); text(p.phase)
    for family in manifest.input_families:
        if not isinstance(family, InputFamily): raise ModelError('invalid input family')
        text(family.name)
        if family.excluded_reason is not None: text(family.excluded_reason)
    for goal in manifest.cover_goals:
        if not isinstance(goal, CoverGoal): raise ModelError('invalid cover goal')
        text(goal.id); text(goal.scope)
    for pair in manifest.bounds:
        if type(pair) is not tuple or len(pair) != 2: raise ModelError('invalid environment bound')
        text(pair[0]); text(pair[1])

def _safe_error(error):
    try: text = str(error)
    except BaseException: text = 'exception text unavailable'
    # Bound retained diagnostics and avoid unencodable Unicode in later export.
    return text[:4096].encode('utf-8', 'backslashreplace')[:4096].decode('utf-8', 'ignore')

_SEARCH_TERMINATIONS = ('graph_exhausted', 'cases_completed', 'depth_bound', 'state_limit', 'transition_limit',
                        'estimated_state_byte_limit', 'failure_found', 'deadline', 'cancelled')
_CAMPAIGN_TERMINATIONS = ('jobs_completed', 'failure_found', 'cancelled', 'deadline', 'callback_error', 'worker_error')


def _rust_debug_str(text):
    """Approximate Rust `{:?}` for str: quoted, with Rust escape_debug rules.

    Exact for ASCII. Non-ASCII uses Unicode categories in place of Rust's
    printable and grapheme-extend tables, which can differ for rare code points.
    """
    import unicodedata
    out = ['"']
    for c in text:
        if c == '"': out.append('\\"')
        elif c == '\\': out.append('\\\\')
        elif c == '\n': out.append('\\n')
        elif c == '\r': out.append('\\r')
        elif c == '\t': out.append('\\t')
        elif c == '\0': out.append('\\0')
        elif c == ' ' or (' ' < c < '\x7f'): out.append(c)
        elif c < '\x80' or unicodedata.category(c) in ('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp', 'Zs', 'Mn', 'Me'):
            out.append('\\u{%x}' % ord(c))
        else: out.append(c)
    out.append('"')
    return ''.join(out)


def _rust_termination(value):
    """Return the Rust Debug spelling for a native search/fuzz termination."""
    return _variant_name(value) if value in _SEARCH_TERMINATIONS else value


def _campaign_termination(termination, error):
    """Rust `format!("Campaign::{:?}", termination)`, bounded for report export."""
    if termination not in _CAMPAIGN_TERMINATIONS: raise ModelError('unknown campaign termination')
    name = 'Campaign::' + _variant_name(termination)
    if termination not in ('callback_error', 'worker_error'): return name
    text = _safe_error('' if error is None else error)
    while True:
        result = f'{name}(ModelError({_rust_debug_str(text)}))'
        if len(result.encode('utf-8')) <= 8192 or not text: return result
        text = text[:len(text) // 2]


def _job_limit(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ModelError('assurance max_jobs must be an unsigned 64-bit integer')
    return value

def _evidence_limit(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ModelError('assurance max_evidence_bytes must be an unsigned 64-bit integer')
    return value

def _report_preflight(report):
    if (not isinstance(report.metadata, Metadata) or type(report.observed_properties) is not tuple
            or type(report.observed_families) is not frozenset or type(report.observed_goals) is not frozenset
            or type(report.budgets) is not tuple or type(report.callback_errors) is not tuple
            or len(report.observed_properties) > MAX_OBSERVED_PROPERTIES
            or len(report.observed_families) + len(report.observed_goals) > MAX_COVERAGE_HITS
            or len(report.budgets) > MAX_MANIFEST_ITEMS or len(report.callback_errors) > MAX_ASSURANCE_ERRORS + 8):
        raise ModelError('assurance report limit exceeded or invalid report')
    total = 0
    def text(value):
        nonlocal total
        if type(value) is not str or len(value) > 8192: raise ModelError('assurance report text limit exceeded')
        try: size = len(value.encode('utf-8'))
        except UnicodeError: raise ModelError('invalid assurance report text') from None
        if size > 8192: raise ModelError('assurance report text limit exceeded')
        total += size
        if total > MAX_REPORT_TEXT_BYTES: raise ModelError('assurance report total text limit exceeded')
    for value in (report.metadata.name, report.metadata.build, report.outcome, report.termination): text(value)
    for pair in report.budgets:
        if type(pair) is not tuple or len(pair) != 2: raise ModelError('invalid assurance budget')
        text(pair[0]); text(pair[1])
    for value in report.callback_errors: text(value)
    for value in report.observed_families: text(value)
    for value in report.observed_goals: text(value)
    for pair in report.observed_properties:
        if type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], RequiredProperty):
            raise ModelError('invalid assurance property')
        text(pair[0].id); text(pair[0].phase)

def search_outcome(termination):
    """Map native BFS or fuzz termination to the assurance outcome domain."""
    mapping = {'graph_exhausted': 'graph_exhausted', 'cases_completed': 'cases_completed',
               'depth_bound': 'depth_limited', 'state_limit': 'budget_limited',
               'transition_limit': 'budget_limited', 'estimated_state_byte_limit': 'budget_limited',
               'failure_found': 'failed', 'deadline': 'timed_out', 'cancelled': 'interrupted'}
    mapping.update({'GraphExhausted': 'graph_exhausted', 'CasesCompleted': 'cases_completed',
                    'DepthBound': 'depth_limited', 'StateLimit': 'budget_limited',
                    'TransitionLimit': 'budget_limited', 'EstimatedStateByteLimit': 'budget_limited',
                    'FailureFound': 'failed', 'Deadline': 'timed_out', 'Cancelled': 'interrupted'})
    try: return mapping[termination]
    except (KeyError, TypeError): raise ModelError('unknown search termination') from None

@dataclass(frozen=True, order=True)
class RequiredProperty:
    id: str
    phase: str  # state or transition

@dataclass(frozen=True)
class InputFamily:
    name: str
    excluded_reason: str | None = None

@dataclass(frozen=True)
class CoverGoal:
    id: str
    scope: str = 'transition'  # transition or history

@dataclass(frozen=True)
class Provenance:
    manifest: str
    model: str
    properties: str

@dataclass(frozen=True)
class Manifest:
    model: str
    provenance: Provenance
    required_properties: tuple = ()
    input_families: tuple = ()
    bounds: tuple = ()
    cover_goals: tuple = ()
    required_search_outcome: str = 'graph_exhausted'
    assurance_version: int = ASSURANCE_VERSION

    def require(self, id, phase):
        """Return a new manifest with one explicitly required property."""
        self.validate()
        result = replace(_copy(self), required_properties=tuple(_copy(self.required_properties)) + (RequiredProperty(id, phase),))
        result.validate()
        return result

    def add_domain(self, domain):
        """Return a new manifest recording domain assumptions and entry bound.

        Declaring a domain does not certify that its inputs were exercised.
        """
        from .modeling import DomainDescriptor
        self.validate()
        if not isinstance(domain, DomainDescriptor): raise ModelError('domain descriptor required')
        bounds = dict(self.bounds)
        bounds[f'domain.{domain.name}.assumptions'] = domain.assumptions
        bounds[f'domain.{domain.name}.max_entries'] = str(domain.max_entries)
        result = replace(_copy(self), bounds=tuple(sorted(bounds.items())))
        result.validate()
        return result

    def to_json(self):
        self.validate()
        return _json(_manifest_json(self))

    @classmethod
    def from_json(cls, data, *, max_bytes=1_048_576):
        return _manifest_from_value(_load_json(data, max_bytes))

    def validate(self):
        _manifest_preflight(self)
        if type(self.assurance_version) is not int or self.assurance_version != ASSURANCE_VERSION:
            raise ModelError('unsupported assurance manifest version')
        if not isinstance(self.provenance, Provenance) or any(not isinstance(s, str) or not s.strip() for s in
                (self.model, self.provenance.manifest, self.provenance.model, self.provenance.properties)):
            raise ModelError('assurance identity/provenance must not be empty')
        seen = set()
        for p in self.required_properties:
            if not isinstance(p, RequiredProperty) or not isinstance(p.id, str) or not p.id.strip() or p.phase not in ('state', 'transition') or p in seen:
                raise ModelError('empty or duplicate required property')
            seen.add(p)
        seen.clear()
        for f in self.input_families:
            if not isinstance(f, InputFamily) or not isinstance(f.name, str) or not f.name.strip() or f.name in seen:
                raise ModelError('empty or duplicate input family')
            if f.excluded_reason is not None and (not isinstance(f.excluded_reason, str) or not f.excluded_reason.strip()):
                raise ModelError('excluded input family needs a rationale')
            seen.add(f.name)
        seen.clear()
        for g in self.cover_goals:
            if not isinstance(g, CoverGoal) or not isinstance(g.id, str) or not g.id.strip() or g.id in seen or g.scope not in ('transition', 'history'):
                raise ModelError('empty or duplicate cover goal')
            seen.add(g.id)
        seen.clear()
        for pair in self.bounds:
            if not isinstance(pair, tuple) or len(pair) != 2 or any(not isinstance(x, str) or not x.strip() for x in pair) or pair[0] in seen:
                raise ModelError('environment bound names and assumptions must not be empty or duplicate')
            seen.add(pair[0])
        if self.required_search_outcome not in ('graph_exhausted', 'completed'):
            raise ModelError('invalid required search outcome')

@dataclass(frozen=True)
class CheckCounts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    missing: int = 0

@dataclass(frozen=True)
class PropertyResult:
    property: RequiredProperty
    status: str
    counts: CheckCounts

@dataclass(frozen=True)
class FamilyResult:
    name: str
    status: str
    reason: str | None = None

@dataclass(frozen=True)
class GoalResult:
    goal: CoverGoal
    status: str

@dataclass(frozen=True)
class Assessment:
    adequate: bool
    reasons: tuple
    confidence: str

class _CoverageState:
    def __init__(self):
        import threading
        self.families, self.goals, self.failure, self.active = set(), set(), None, True
        self.owner, self.lock = threading.get_ident(), threading.Lock()
    def add(self, kind, name):
        import threading
        with self.lock:
            if not self.active: raise ModelError('coverage hit lease has expired')
            if threading.get_ident() != self.owner:
                self.failure = 'coverage hit requires callback owner thread'
                raise ModelError(self.failure)
            return self._add_owned(kind, name)
    def _add_owned(self, kind, name):
        if not self.active: raise ModelError('coverage hit lease has expired')
        if self.failure is not None: raise ModelError(self.failure)
        values = self.families if kind == 'family' else self.goals
        if type(name) is not str:
            self.failure = 'coverage identity must be a string'
        else:
            try: length = len(name.encode('utf-8')) if len(name) <= MAX_COVERAGE_IDENTIFIER_BYTES else MAX_COVERAGE_IDENTIFIER_BYTES + 1
            except UnicodeError: length = MAX_COVERAGE_IDENTIFIER_BYTES + 1
            if length > MAX_COVERAGE_IDENTIFIER_BYTES: self.failure = 'coverage identity exceeds byte limit'
            elif name not in values and len(self.families) + len(self.goals) >= MAX_COVERAGE_HITS:
                self.failure = 'coverage hit limit exceeded'
        if self.failure is not None: raise ModelError(self.failure)
        values.add(name)
    def finish(self):
        with self.lock:
            self.active = False
            if self.failure is not None: raise ModelError(self.failure)
            return frozenset(self.families), frozenset(self.goals)
    def revoke(self):
        with self.lock: self.active = False


class CoverageHits:
    """Bounded callback lease; rejected writes remain failures until revocation."""
    __slots__ = ('__add',)
    def __init__(self, state=None):
        state = _CoverageState() if state is None else state
        self.__add = state.add
    def family(self, name): self.__add('family', name)
    def goal(self, name): self.__add('goal', name)

class Observer:
    """Synchronous SearchObserver; coverage.observe receives owned snapshots.

    Partial hits from a failing coverage callback never count as evidence.
    """
    def __init__(self, manifest, metadata, coverage):
        manifest.validate()
        if not isinstance(metadata, Metadata) or manifest.model != metadata.name:
            raise ModelError('assurance manifest model does not match actual model metadata')
        self._manifest, self._metadata, self._coverage = _copy(manifest), _copy(metadata), coverage
        self._properties, self._families, self._goals, self._errors = {}, set(), set(), set()
        self._transitions = self._unclassified = 0
        import threading
        self._lock, self._reentrant, self._finished = threading.Lock(), False, False

    def _enter(self):
        if not self._lock.acquire(blocking=False):
            self._reentrant = True
            raise ModelError('assurance observation already in progress')
        if self._finished:
            self._lock.release()
            raise ModelError('assurance observer is already finalized')
        self._reentrant = False

    def _leave(self):
        if self._reentrant: self._error('assurance observation reentry rejected')
        self._lock.release()

    @property
    def manifest(self): return _copy(self._manifest)
    @property
    def metadata(self): return _copy(self._metadata)
    @property
    def properties(self): return _copy(self._properties)
    @property
    def families(self): return frozenset(self._families)
    @property
    def goals(self): return frozenset(self._goals)
    @property
    def errors(self): return frozenset(self._errors)
    @property
    def transitions(self): return self._transitions
    @property
    def unclassified(self): return self._unclassified

    def _error(self, message):
        if message in self._errors: return
        if len(self._errors) < MAX_ASSURANCE_ERRORS: self._errors.add(message)
        else: self._errors.add('assurance error limit exceeded')

    def needs_history(self): return any(g.scope == 'history' for g in self._manifest.cover_goals)

    def _checks(self, phase, checks, complete):
        emitted = set()
        for check in checks:
            if (type(check.id) is not str or len(check.id) > MAX_PROPERTY_IDENTIFIER_BYTES):
                self._error('assurance property identity exceeds limit or is invalid')
                return
            try: identity_bytes = len(check.id.encode('utf-8'))
            except UnicodeError: identity_bytes = MAX_PROPERTY_IDENTIFIER_BYTES + 1
            if identity_bytes > MAX_PROPERTY_IDENTIFIER_BYTES:
                self._error('assurance property identity exceeds limit or is invalid')
                return
            if check.id not in emitted and len(emitted) >= MAX_OBSERVED_PROPERTIES:
                self._error('assurance property batch limit exceeded')
                return
            if check.id in emitted: self._error(f'ambiguous duplicate property ID in {phase} batch: {check.id}')
            emitted.add(check.id)
            key = RequiredProperty(check.id, phase)
            if key not in self._properties and len(self._properties) >= MAX_OBSERVED_PROPERTIES:
                self._error('assurance observed property limit exceeded')
                return
            counts = self._properties.setdefault(key, [0, 0, 0, 0])
            index = {'passed': 0, 'failed': 1, 'skipped': 2}[check.status]
            counts[index] = min(U64_MAX, counts[index] + 1)
        if complete:
            for p in self._manifest.required_properties:
                if p.phase == phase and p.id not in emitted:
                    if len(p.id) > MAX_PROPERTY_IDENTIFIER_BYTES:
                        self._error('assurance property identity exceeds limit or is invalid')
                        return
                    try: identity_bytes = len(p.id.encode('utf-8'))
                    except UnicodeError: identity_bytes = MAX_PROPERTY_IDENTIFIER_BYTES + 1
                    if identity_bytes > MAX_PROPERTY_IDENTIFIER_BYTES:
                        self._error('assurance property identity exceeds limit or is invalid')
                        return
                    if p not in self._properties and len(self._properties) >= MAX_OBSERVED_PROPERTIES:
                        self._error('assurance observed property limit exceeded')
                        return
                    counts = self._properties.setdefault(p, [0, 0, 0, 0])
                    counts[3] = min(U64_MAX, counts[3] + 1)

    def initial(self, state, checks):
        self._enter()
        try: self._checks('state', checks, True)
        finally: self._leave()
    def check_error(self, phase, checks, complete):
        self._enter()
        try:
            if not complete: self._error('assurance check batch incomplete')
            self._checks('state' if phase in ('state', 'initial_state') else 'transition', checks, complete)
        finally: self._leave()

    def transition(self, before, input, after, state_checks, transition_checks, history):
        self._enter()
        try: self._transition(before, input, after, state_checks, transition_checks, history)
        finally: self._leave()

    def _transition(self, before, input, after, state_checks, transition_checks, history):
        self._checks('state', state_checks, True)
        self._checks('transition', transition_checks, True)
        self._transitions = min(U64_MAX, self._transitions + 1)
        state = _CoverageState()
        hits = CoverageHits(state)
        try:
            result = self._coverage.observe(_copy(before), _copy(input), _copy(after), _copy(history), hits)
            if result is not None:
                import inspect
                if inspect.iscoroutine(result) or inspect.isgenerator(result): result.close()
                raise ModelError('coverage callback must return None')
            if self._reentrant: raise ModelError('assurance observation reentry rejected')
            families, goals = state.finish()
        except Exception as error:
            state.revoke()
            self._error('coverage callback: ' + _safe_error(error))
            self._unclassified = min(U64_MAX, self._unclassified + 1)
            return
        declared = {f.name for f in self._manifest.input_families}
        classified = families & declared
        for name in sorted(classified):
            if name not in self._families and len(self._families) + len(self._goals) >= MAX_COVERAGE_HITS:
                self._error('assurance retained coverage limit exceeded')
            else: self._families.add(name)
        for name in sorted(families - declared): self._error('undeclared input family: ' + name)
        if not classified: self._unclassified = min(U64_MAX, self._unclassified + 1)
        declared_goals = {g.id for g in self._manifest.cover_goals}
        for name in sorted(goals & declared_goals):
            if name not in self._goals and len(self._families) + len(self._goals) >= MAX_COVERAGE_HITS:
                self._error('assurance retained coverage limit exceeded')
            else: self._goals.add(name)
        for name in sorted(goals - declared_goals): self._error('undeclared cover goal: ' + name)

    def finish(self, outcome, termination, budgets=(), error=None):
        self._enter()
        try:
            result = self._finish(outcome, termination, budgets, error)
            if self._reentrant:
                result = replace(result, callback_errors=tuple(sorted(set(result.callback_errors) | {'assurance observation reentry rejected'})))
            self._finished = True
            return result
        finally: self._leave()

    def _finish(self, outcome, termination, budgets=(), error=None):
        if outcome not in ('graph_exhausted', 'cases_completed', 'depth_limited', 'budget_limited', 'interrupted', 'timed_out', 'failed', 'not_started'):
            raise ModelError('invalid assurance search outcome')
        if not isinstance(termination, str) or not termination.strip(): raise ModelError('assurance termination must not be empty')
        errors = set(self._errors)
        if error is not None: errors.add(_safe_error(error))
        return Report(_copy(self._manifest), _copy(self._metadata), outcome, _rust_termination(termination), tuple(budgets),
                      tuple(sorted((p, CheckCounts(*v)) for p, v in self._properties.items())),
                      frozenset(self._families), frozenset(self._goals), tuple(sorted(errors)),
                      self._transitions, self._unclassified)

@dataclass(frozen=True)
class Report:
    manifest: Manifest
    metadata: Metadata
    outcome: str
    termination: str
    budgets: tuple
    observed_properties: tuple
    observed_families: frozenset
    observed_goals: frozenset
    callback_errors: tuple
    checked_transitions: int
    unclassified_transitions: int

    def to_json(self):
        self.validate()
        return _json(_report_json(self))

    @classmethod
    def from_json(cls, data, *, max_bytes=1_048_576):
        return _report_from_value(_load_json(data, max_bytes))

    @property
    def properties(self):
        self.validate()
        counts = dict(self.observed_properties)
        results = []
        for prop in sorted(self.manifest.required_properties):
            c = counts.get(prop, CheckCounts())
            status = 'failed' if c.failed else 'skipped' if c.skipped else 'missing' if c.missing or not c.passed else 'passed'
            results.append(PropertyResult(prop, status, c))
        return tuple(results)

    @property
    def families(self):
        self.validate()
        results = []
        for family in sorted(self.manifest.input_families, key=lambda f: f.name):
            observed = family.name in self.observed_families
            if family.excluded_reason is None: status = 'observed' if observed else 'missing'
            else: status = 'exclusion_violated' if observed else 'excluded'
            results.append(FamilyResult(family.name, status, family.excluded_reason))
        return tuple(results)

    @property
    def coverage(self):
        self.validate()
        exhausted = self.outcome == 'graph_exhausted' and not self.callback_errors
        results = []
        for goal in sorted(self.manifest.cover_goals, key=lambda g: g.id):
            status = ('observed' if goal.id in self.observed_goals else
                      'unreachable_under_declared_model' if exhausted and goal.scope == 'transition' else
                      'not_reached_before_stop')
            results.append(GoalResult(goal, status))
        return tuple(results)

    def validate(self):
        self.manifest.validate()
        _report_preflight(self)
        declared_families = {f.name for f in self.manifest.input_families}
        declared_goals = {g.id for g in self.manifest.cover_goals}
        if (type(self.observed_properties) is not tuple or type(self.observed_families) is not frozenset
                or type(self.observed_goals) is not frozenset or type(self.callback_errors) is not tuple
                or type(self.budgets) is not tuple
                or len(self.observed_properties) > MAX_OBSERVED_PROPERTIES
                or len(self.observed_families) + len(self.observed_goals) > MAX_COVERAGE_HITS
                or any(type(error) is not str for error in self.callback_errors)
                or any(type(pair) is not tuple or len(pair) != 2 or any(type(v) is not str for v in pair) for pair in self.budgets)
                or len(dict(self.budgets)) != len(self.budgets)
                or self.outcome not in ('graph_exhausted', 'cases_completed', 'depth_limited', 'budget_limited', 'interrupted', 'timed_out', 'failed', 'not_started')
                or not self.observed_families <= declared_families or not self.observed_goals <= declared_goals
                or type(self.checked_transitions) is not int or not 0 <= self.checked_transitions <= U64_MAX
                or type(self.unclassified_transitions) is not int or not 0 <= self.unclassified_transitions <= U64_MAX
                or len(dict(self.observed_properties)) != len(self.observed_properties)
                or any(not isinstance(p, RequiredProperty) or type(p.id) is not str or p.phase not in ('state', 'transition') or not isinstance(c, CheckCounts)
                       or any(type(v) is not int or not 0 <= v <= U64_MAX for v in (c.passed, c.failed, c.skipped, c.missing))
                       for p, c in self.observed_properties)
                or not isinstance(self.metadata, Metadata) or self.metadata.name != self.manifest.model
                or not isinstance(self.termination, str) or not self.termination.strip()
                or self.unclassified_transitions > self.checked_transitions
                or (not self.checked_transitions and (self.observed_families or self.observed_goals))):
            raise ModelError('inconsistent assurance report')
        try: expected = search_outcome(self.termination)
        except ModelError:
            expected = 'failed' if self.termination in ('ModelError', 'Panicked') else None
        if expected is not None and self.outcome != expected:
            raise ModelError('assurance outcome contradicts search termination')
        if expected is None and self.termination not in ('MergedJobs', 'Campaign::jobs_completed', 'Campaign::JobsCompleted') and self.outcome in ('graph_exhausted', 'cases_completed'):
            raise ModelError('completed assurance outcome requires a recognized termination')

    def assess(self):
        try: self.validate()
        except (ModelError, TypeError, ValueError, AttributeError) as error:
            return Assessment(False, ('inconsistent assurance report: ' + _safe_error(error),), 'The report is internally inconsistent; no adequacy claim is established.')
        reasons = []
        if not self.manifest.required_properties: reasons.append('no expected properties declared')
        if not any(f.excluded_reason is None for f in self.manifest.input_families): reasons.append('no required input families declared')
        counts = dict(self.observed_properties)
        for result in self.properties:
            if result.status != 'passed':
                reasons.append(f'property {result.property.id} {_variant_name(result.property.phase)}: {_variant_name(result.status)}')
        if any(c.failed or c.skipped or c.missing for c in counts.values()): reasons.append('observed failed, skipped, or omitted required checks')
        # Reason wording follows Rust AssuranceReport::assess Debug formatting.
        for f in self.families:
            if f.status == 'missing': reasons.append(f'input family {f.name}: Missing')
            elif f.status == 'exclusion_violated':
                reasons.append(f'input family {f.name}: ExclusionViolated {{ reason: {_rust_debug_str(f.reason)} }}')
        for g in self.coverage:
            if g.status != 'observed': reasons.append(f'cover goal {g.goal.id}: {_variant_name(g.status)}')
        if self.unclassified_transitions: reasons.append('some checked inputs had no declared family classification')
        if self.callback_errors: reasons.append('model or observation callback error')
        complete = self.outcome == 'graph_exhausted' if self.manifest.required_search_outcome == 'graph_exhausted' else self.outcome in ('graph_exhausted', 'cases_completed')
        if not complete: reasons.append('required search outcome not met: ' + _variant_name(self.outcome))
        adequate = not reasons
        confidence = ('The declared properties held for the exhausted finite model and declared adequacy goals were observed.' if self.outcome == 'graph_exhausted' else 'The declared properties held for the completed sampled cases and declared adequacy goals were observed.') if adequate else 'Declared assurance requirements are not fully satisfied; no broader adequacy claim is established.'
        return Assessment(adequate, tuple(reasons), confidence)

@dataclass(frozen=True)
class MergedReport:
    report: Report
    jobs: tuple  # sorted (u64 job ID, report) pairs
    missing_jobs: tuple

    def to_json(self):
        self.report.validate()
        for _, report in self.jobs: report.validate()
        return _json({'assurance_version': ASSURANCE_VERSION, 'report': _report_json(self.report),
                      'jobs': {str(id): _report_json(report) for id, report in self.jobs},
                      'missing_jobs': list(self.missing_jobs)})

    @classmethod
    def from_json(cls, data, *, max_bytes=1_048_576, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS,
                  max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
        return _merged_from_value(_load_json(data, max_bytes), max_jobs, max_evidence_bytes)


def merge_reports(manifest, metadata, expected_jobs, reports, *, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS,
                  max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
    """Merge validated reports with identical full manifest and model metadata."""
    _job_limit(max_jobs)
    _evidence_limit(max_evidence_bytes)
    manifest.validate()
    if not isinstance(metadata, Metadata) or manifest.model != metadata.name:
        raise ModelError('merge model identity mismatch')
    expected = set()
    for job_id in expected_jobs:
        if len(expected) >= max_jobs: raise ModelError('assurance job limit exceeded')
        if type(job_id) is not int or not 0 <= job_id <= U64_MAX:
            raise ModelError('invalid assurance job id')
        if job_id in expected: raise ModelError('duplicate expected assurance job')
        expected.add(job_id)
    jobs, retained_bytes = {}, 0
    for job_id, report in reports:
        if type(job_id) is not int or not 0 <= job_id <= U64_MAX:
            raise ModelError('invalid assurance job id')
        if not isinstance(report, Report): raise ModelError('invalid assurance report')
        try: report.validate()
        except (TypeError, ValueError, AttributeError) as error:
            raise ModelError('inconsistent assurance report') from error
        if job_id not in expected: raise ModelError('unexpected assurance job')
        if report.manifest != manifest or report.metadata != metadata:
            raise ModelError('cannot merge different assurance manifests or executable identities')
        if job_id in jobs: raise ModelError('duplicate assurance job report')
        size = _evidence_size(report)
        if size > max_evidence_bytes - retained_bytes: raise ModelError('assurance evidence byte limit exceeded')
        retained_bytes += size
        jobs[job_id] = _copy(report)
    missing = tuple(sorted(expected - jobs.keys()))
    properties, families, goals, errors = {}, set(), set(), set()
    def add_error(error):
        if error in errors: return
        if len(errors) < MAX_ASSURANCE_ERRORS: errors.add(error)
        else: errors.add('assurance merged error limit exceeded')
    transitions = unclassified = 0
    for job_id, report in sorted(jobs.items()):
        for prop, counts in report.observed_properties:
            if prop not in properties and len(properties) >= MAX_OBSERVED_PROPERTIES:
                add_error('assurance merged property limit exceeded')
                continue
            prior = properties.get(prop, CheckCounts())
            properties[prop] = CheckCounts(*(min(U64_MAX, a + b) for a, b in zip(
                (prior.passed, prior.failed, prior.skipped, prior.missing),
                (counts.passed, counts.failed, counts.skipped, counts.missing))))
        for target, names in ((families, report.observed_families), (goals, report.observed_goals)):
            for name in sorted(names):
                if name not in target and len(families) + len(goals) >= MAX_COVERAGE_HITS:
                    add_error('assurance merged coverage limit exceeded')
                else: target.add(name)
        for error in report.callback_errors: add_error(_safe_error(f'job {job_id}: {error}'))
        transitions = min(U64_MAX, transitions + report.checked_transitions)
        unclassified = min(U64_MAX, unclassified + report.unclassified_transitions)
    outcomes = [report.outcome for report in jobs.values()]
    if not expected or not jobs: outcome = 'not_started'
    elif 'failed' in outcomes: outcome = 'failed'
    elif missing: outcome = 'interrupted'
    elif all(x == 'graph_exhausted' for x in outcomes): outcome = 'graph_exhausted'
    elif all(x in ('graph_exhausted', 'cases_completed') for x in outcomes): outcome = 'cases_completed'
    elif 'timed_out' in outcomes: outcome = 'timed_out'
    elif any(x in ('interrupted', 'not_started') for x in outcomes): outcome = 'interrupted'
    elif 'budget_limited' in outcomes: outcome = 'budget_limited'
    else: outcome = 'depth_limited'
    report = Report(_copy(manifest), _copy(metadata), outcome, 'MergedJobs',
                    (('reported_jobs', str(len(jobs))), ('requested_jobs', str(len(expected)))),
                    tuple(sorted(properties.items())), frozenset(families), frozenset(goals),
                    tuple(sorted(errors)), transitions, unclassified)
    return MergedReport(report, tuple(sorted(jobs.items())), missing)

@dataclass(frozen=True)
class AssuredRun:
    """Exactly one of search or error is populated; assurance survives errors."""
    search: object | None
    error: ModelError | None
    assurance: Report


def _control_budgets(control):
    result = {'cancellation_enabled': str(control.cancellation is not None).lower()}
    if control.max_duration is not None:
        from decimal import Decimal
        # Exact decimal seconds (shortest float repr) to whole nanoseconds, as Rust Duration::as_nanos.
        result['max_duration_ns'] = str(int(Decimal(repr(control.max_duration)) * 1_000_000_000))
    return result


def _finish_search(observer, run, budgets):
    try:
        search = run()
    except ModelError as error:
        return AssuredRun(None, error, observer.finish('failed', 'ModelError', tuple(sorted(budgets.items())), error))
    return AssuredRun(search, None, observer.finish(search_outcome(search.termination), search.termination,
                                                   tuple(sorted(budgets.items()))))


def assured_enumerate(model, manifest, coverage, config=None, *, limits=None):
    """Run BFS and retain assurance observations even if a model check fails."""
    from .core import SearchConfig, enumerate_states, _call
    from .search import SearchLimits
    config = SearchConfig() if config is None else config
    limits = SearchLimits() if limits is None else limits
    if not isinstance(config, SearchConfig) or not isinstance(limits, SearchLimits):
        raise ValueError('expected SearchConfig and SearchLimits')
    observer = Observer(manifest, _call('assurance metadata', model.metadata), coverage)
    budgets = {key: str(getattr(config, key)) for key in ('max_states', 'max_transitions', 'max_depth')}
    budgets.update(_control_budgets(limits.control))
    if limits.max_estimated_state_bytes is not None:
        budgets['max_estimated_state_bytes'] = str(limits.max_estimated_state_bytes)
    return _finish_search(observer, lambda: enumerate_states(model, config, limits=limits, observer=observer), budgets)


def assured_fuzz(model, manifest, coverage, config=None, *, limits=None):
    """Run sampled fuzzing and retain its checked assurance evidence."""
    from .core import _call
    from .search import FuzzConfig, RunLimits, fuzz
    config = FuzzConfig() if config is None else config
    limits = RunLimits() if limits is None else limits
    if not isinstance(config, FuzzConfig) or not isinstance(limits, RunLimits):
        raise ValueError('expected FuzzConfig and RunLimits')
    observer = Observer(manifest, _call('assurance metadata', model.metadata), coverage)
    budgets = {key: str(getattr(config, key)) for key in ('seed', 'cases', 'max_steps', 'max_transitions', 'mutation_percent')}
    budgets.update(_control_budgets(limits))
    return _finish_search(observer, lambda: fuzz(model, config, limits=limits, observer=observer), budgets)


def assured_fuzz_auto(model, manifest, coverage, config=None, *, options=None, limits=None):
    """Assured finite-domain fuzzing with cooperative automatic scans."""
    from .core import _call
    from .search import AutoOptions, FuzzConfig, RunLimits, fuzz_auto
    config = FuzzConfig() if config is None else config
    limits = RunLimits() if limits is None else limits
    options = AutoOptions() if options is None else options
    if not isinstance(config, FuzzConfig) or not isinstance(limits, RunLimits) or not isinstance(options, AutoOptions):
        raise ValueError('expected FuzzConfig, RunLimits and AutoOptions')
    options = AutoOptions(options.max_candidates)
    observer = Observer(manifest, _call('assurance metadata', model.metadata), coverage)
    budgets = {key: str(getattr(config, key)) for key in ('seed', 'cases', 'max_steps', 'max_transitions', 'mutation_percent')}
    budgets.update(_control_budgets(limits))
    return _finish_search(observer, lambda: fuzz_auto(model, config, options=options, limits=limits, observer=observer), budgets)


def assured_guided_fuzz_auto(model, manifest, coverage, feedback, config=None, guidance=None, *, options=None, limits=None):
    """Assured guided finite-domain fuzzing with cooperative scans."""
    from .core import _call
    from .guided import GuidanceConfig
    from .search import AutoOptions, FuzzConfig, RunLimits
    config = FuzzConfig() if config is None else config
    guidance = GuidanceConfig() if guidance is None else guidance
    limits = RunLimits() if limits is None else limits
    options = AutoOptions() if options is None else options
    if not isinstance(config, FuzzConfig) or not isinstance(guidance, GuidanceConfig) or not isinstance(limits, RunLimits) or not isinstance(options, AutoOptions):
        raise ValueError('expected FuzzConfig, GuidanceConfig, RunLimits and AutoOptions')
    options = AutoOptions(options.max_candidates)
    observer = Observer(manifest, _call('assurance metadata', model.metadata), coverage)
    budgets = {key: str(getattr(config, key)) for key in ('seed', 'cases', 'max_steps', 'max_transitions', 'mutation_percent')}
    budgets.update(_control_budgets(limits))
    return _run_guided_observed(observer, model, feedback, config, guidance, limits, budgets, options)


def assured_guided_fuzz(model, manifest, coverage, feedback, config=None, guidance=None, *, limits=None):
    """Run guided fuzz with one feedback-metadata call and retained evidence."""
    from .core import _call
    from .guided import GuidanceConfig
    from .search import FuzzConfig, RunLimits
    config = FuzzConfig() if config is None else config
    guidance = GuidanceConfig() if guidance is None else guidance
    limits = RunLimits() if limits is None else limits
    if not isinstance(config, FuzzConfig) or not isinstance(guidance, GuidanceConfig) or not isinstance(limits, RunLimits):
        raise ValueError('expected FuzzConfig, GuidanceConfig and RunLimits')
    observer = Observer(manifest, _call('assurance metadata', model.metadata), coverage)
    budgets = {key: str(getattr(config, key)) for key in ('seed', 'cases', 'max_steps', 'max_transitions', 'mutation_percent')}
    budgets.update(_control_budgets(limits))
    return _run_guided_observed(observer, model, feedback, config, guidance, limits, budgets)


def _run_guided_observed(observer, model, feedback, config, guidance, limits, budgets, auto_options=None):
    from .guided import ALGORITHM, FeedbackMetadata, guided_fuzz, guided_fuzz_auto
    budgets['guidance.algorithm'] = ALGORITHM
    for key in ('max_corpus_entries', 'max_corpus_inputs', 'max_features', 'max_features_per_observation'):
        budgets['guidance.' + key] = str(getattr(guidance, key))

    class CaptureFeedback:
        def __getattr__(self, name): return getattr(feedback, name)
        def metadata(self):
            metadata = feedback.metadata()
            if isinstance(metadata, FeedbackMetadata):
                budgets['guidance.feedback.name'] = metadata.name
                budgets['guidance.feedback.version'] = str(metadata.version)
                budgets['guidance.feedback.build'] = metadata.build
            return metadata

    def run():
        if auto_options is None:
            return guided_fuzz(model, CaptureFeedback(), config, guidance, limits=limits, observer=observer)
        return guided_fuzz_auto(model, CaptureFeedback(), config, guidance, options=auto_options, limits=limits, observer=observer)
    return _finish_search(observer, run, budgets)

@dataclass(frozen=True)
class AssuredCampaign:
    campaign: object
    assurance: MergedReport


def assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, *, process=None, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS, max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
    """Run assured jobs on the existing scheduler with independent evidence transfer."""
    return _assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, None, None, process, max_jobs, max_evidence_bytes)


def assured_guided_campaign(config, limits, manifest, metadata, guidance, factory, coverage_factory, feedback_factory, sink, *, process=None, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS, max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
    """Run guided assured jobs with fresh feedback per job."""
    from dataclasses import replace
    from .guided import GuidanceConfig
    if not isinstance(guidance, GuidanceConfig): raise ModelError('guidance must be GuidanceConfig')
    guidance.validate()
    return _assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink,
                             replace(guidance), feedback_factory, process, max_jobs, max_evidence_bytes)


def assured_auto_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, *, options=None, process=None, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS, max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
    """Run assured automatic jobs with worker-local cooperative scans."""
    from .search import AutoOptions
    options = AutoOptions() if options is None else options
    if not isinstance(options, AutoOptions): raise ValueError('options must be AutoOptions')
    return _assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, None, None, process, max_jobs, max_evidence_bytes, AutoOptions(options.max_candidates))


def assured_guided_auto_campaign(config, limits, manifest, metadata, guidance, factory, coverage_factory, feedback_factory, sink, *, options=None, process=None, max_jobs=DEFAULT_MAX_ASSURANCE_JOBS, max_evidence_bytes=DEFAULT_MAX_ASSURANCE_EVIDENCE_BYTES):
    """Run guided assured automatic jobs with worker-local cooperative scans."""
    from dataclasses import replace
    from .guided import GuidanceConfig
    from .search import AutoOptions
    if not isinstance(guidance, GuidanceConfig): raise ModelError('guidance must be GuidanceConfig')
    guidance.validate()
    options = AutoOptions() if options is None else options
    if not isinstance(options, AutoOptions): raise ValueError('options must be AutoOptions')
    return _assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, replace(guidance), feedback_factory, process, max_jobs, max_evidence_bytes, AutoOptions(options.max_candidates))


class _AssuredJobRunner:
    def __init__(self, manifest, metadata, coverage_factory, guidance, feedback_factory, auto_options=None):
        self.manifest, self.metadata = _copy(manifest), _copy(metadata)
        self.coverage_factory, self.guidance, self.feedback_factory = coverage_factory, guidance, feedback_factory
        self.auto_options = auto_options
        self.reports = {}

    def __call__(self, job_id, model, actual, fuzz_config, job_limits):
        from .campaign import _sync, _cleanup, _CampaignControl
        from .search import fuzz, fuzz_auto
        if actual != self.metadata: raise ModelError('campaign assurance executable identity mismatch')
        coverage = _sync(self.coverage_factory(job_id, model), 'coverage factory')
        observer = Observer(self.manifest, actual, coverage)
        budgets = {key: str(getattr(fuzz_config, key)) for key in ('seed', 'cases', 'max_steps', 'max_transitions', 'mutation_percent')}
        budgets.update(_control_budgets(job_limits))
        feedback = None
        try:
            if self.guidance is None:
                run = (lambda: fuzz(model, fuzz_config, limits=job_limits, observer=observer)) if self.auto_options is None else (lambda: fuzz_auto(model, fuzz_config, options=self.auto_options, limits=job_limits, observer=observer))
                result = _finish_search(observer, run, budgets)
            else:
                runner_control = _CampaignControl(job_limits)
                feedback = _sync(self.feedback_factory(job_id, model), 'feedback factory')
                result = _run_guided_observed(observer, model, feedback, fuzz_config, self.guidance, runner_control.remaining(), budgets, self.auto_options)
            self.reports[job_id] = result.assurance
        except BaseException:
            if feedback is not None or self.guidance is None:
                self.reports[job_id] = observer.finish('failed', 'Panicked', tuple(sorted(budgets.items())),
                                                       ModelError('job panicked; partial observations retained'))
            raise
        finally:
            if feedback is not None: _cleanup(feedback)
        if result.error is not None: raise result.error
        return result.search


def _assured_campaign(config, limits, manifest, metadata, factory, coverage_factory, sink, guidance, feedback_factory, process, max_jobs, max_evidence_bytes, auto_options=None):
    from dataclasses import replace
    from .campaign import CampaignConfig, _run
    from .search import RunLimits
    _job_limit(max_jobs)
    _evidence_limit(max_evidence_bytes)
    if not isinstance(config, CampaignConfig): raise ModelError('config must be CampaignConfig')
    config.validate()
    if config.jobs > max_jobs: raise ModelError('assurance job limit exceeded')
    if not isinstance(limits, RunLimits): raise ModelError('limits must be RunLimits')
    manifest.validate()
    if not isinstance(metadata, Metadata) or metadata.name != manifest.model:
        raise ModelError('campaign model identity mismatch')
    # All later callbacks observe this private catalog and configuration snapshot.
    # A caller-owned list inside a frozen Manifest can otherwise be changed by a
    # sink after a job has completed but before aggregation.
    manifest, metadata, config = _copy(manifest), _copy(metadata), _copy(config)
    import threading
    runner = _AssuredJobRunner(manifest, metadata, coverage_factory, guidance, feedback_factory, auto_options)
    lock = threading.Lock()
    reports, retained_bytes, admission_error = {}, 0, None
    def collect(job_id, evidence):
        nonlocal retained_bytes, admission_error
        with lock:
            if job_id in reports: raise ModelError('duplicate assurance job evidence')
            try: size = _evidence_size(evidence)
            except Exception:
                admission_error = 'assurance evidence admission failed'
                return
            if size > max_evidence_bytes - retained_bytes:
                admission_error = 'assurance evidence byte limit exceeded'
                return
            reports[job_id] = evidence
            retained_bytes += size
    campaign = _run(config, limits, factory, sink, guidance, feedback_factory, process, runner, collect)
    merged = merge_reports(manifest, metadata, range(config.first_job, config.first_job + config.jobs), reports.items(),
                           max_jobs=max_jobs, max_evidence_bytes=max_evidence_bytes)
    report = merged.report
    budgets = dict(report.budgets)
    budgets.update({'campaign.master_seed': str(config.master_seed), 'campaign.first_job': str(config.first_job),
                    'campaign.workers': str(config.workers), 'campaign.result_buffer': str(config.result_buffer),
                    'campaign.stop_on_failure': str(config.stop_on_failure).lower(),
                    'campaign.started': str(campaign.started), 'campaign.finished': str(campaign.finished),
                    'campaign.delivered': str(campaign.delivered),
                    'campaign.accounting_complete': str(campaign.accounting_complete).lower()})
    budgets.update(_control_budgets(limits))
    outcome, errors = report.outcome, list(report.callback_errors)
    if admission_error is not None: errors.append(admission_error)
    if campaign.termination == 'failure_found': outcome = 'failed'
    elif campaign.termination in ('callback_error', 'worker_error'):
        outcome = 'failed'
        errors.append(_safe_error('campaign completion/delivery error: ' + str(campaign.error)))
    elif outcome != 'failed' and campaign.termination == 'cancelled': outcome = 'interrupted'
    elif outcome != 'failed' and campaign.termination == 'deadline': outcome = 'timed_out'
    if not campaign.accounting_complete: errors.append('campaign accounting is incomplete')
    if campaign.delivered != campaign.requested: errors.append('not every requested campaign report was delivered successfully')
    report = replace(report, outcome=outcome, termination=_campaign_termination(campaign.termination, campaign.error),
                     budgets=tuple(sorted(budgets.items())), callback_errors=tuple(sorted(set(errors))))
    return AssuredCampaign(campaign, replace(merged, report=report))


# Rust src/assurance.rs quoted(): escape '"', '\\', \n, \r, \t, and every other
# code point below U+0020 as lowercase \u00XX (so \u0008 and \u000c, never \b/\f).
_QUOTE_TABLE = {i: '\\u%04x' % i for i in range(0x20)}
_QUOTE_TABLE.update({ord('"'): '\\"', ord('\\'): '\\\\', ord('\n'): '\\n', ord('\r'): '\\r', ord('\t'): '\\t'})


def _quoted(text): return '"' + text.translate(_QUOTE_TABLE) + '"'


def _json_chunks(value):
    kind = type(value)
    if kind is str: yield _quoted(value)
    elif value is None: yield 'null'
    elif kind is bool: yield 'true' if value else 'false'
    elif kind is int: yield str(value)
    elif kind is dict:
        yield '{'
        for index, (key, item) in enumerate(value.items()):
            if type(key) is not str: raise ModelError('invalid assurance JSON key')
            yield (',' if index else '') + _quoted(key) + ':'
            yield from _json_chunks(item)
        yield '}'
    elif kind in (list, tuple):
        yield '['
        for index, item in enumerate(value):
            if index: yield ','
            yield from _json_chunks(item)
        yield ']'
    else: raise ModelError('unsupported assurance JSON value')


def _json(value):
    return ''.join(_json_chunks(value))


def _property_json(prop): return {'id': prop.id, 'phase': prop.phase}
def _goal_json(goal): return {'id': goal.id, 'scope': goal.scope}
def _counts_json(counts):
    return {'passed': counts.passed, 'failed': counts.failed,
            'skipped': counts.skipped, 'missing': counts.missing}


def _manifest_json(manifest):
    families = []
    for family in manifest.input_families:
        item = {'name': family.name, 'coverage': 'required' if family.excluded_reason is None else 'excluded'}
        if family.excluded_reason is not None: item['reason'] = family.excluded_reason
        families.append(item)
    return {'assurance_version': manifest.assurance_version, 'model': manifest.model,
            'required_properties': [_property_json(p) for p in manifest.required_properties],
            'input_families': families, 'bounds': dict(sorted(manifest.bounds)),
            'cover_goals': [_goal_json(g) for g in manifest.cover_goals],
            'required_search_outcome': ('exhausted_within_declared_bounds' if manifest.required_search_outcome == 'graph_exhausted' else 'completed'),
            'provenance': {'manifest': manifest.provenance.manifest, 'model': manifest.provenance.model,
                           'properties': manifest.provenance.properties}}


def _variant_name(value): return ''.join(part.capitalize() for part in value.split('_'))


def _report_json(report):
    metadata = report.metadata
    return {'assurance_version': ASSURANCE_VERSION, 'manifest': _manifest_json(report.manifest),
            'metadata': {'name': metadata.name, 'model_version': metadata.model_version,
                         'properties_version': metadata.properties_version, 'codec_version': metadata.codec_version,
                         'build': metadata.build},
            'search': {'outcome': _variant_name(report.outcome), 'termination': _rust_termination(report.termination),
                       'budgets': dict(sorted(report.budgets)), 'checked_transitions': report.checked_transitions},
            'properties': [{'property': _property_json(p.property), 'status': _variant_name(p.status),
                            'counts': _counts_json(p.counts)} for p in report.properties],
            'families': [{'name': f.name, 'status': _variant_name(f.status), 'reason': f.reason} for f in report.families],
            'coverage': [{'goal': _goal_json(g.goal), 'status': _variant_name(g.status)} for g in report.coverage],
            'observed_properties': [{'property': _property_json(prop), 'counts': _counts_json(counts)}
                                    for prop, counts in sorted(report.observed_properties)],
            'observed_families': sorted(report.observed_families), 'observed_goals': sorted(report.observed_goals),
            'callback_errors': list(report.callback_errors), 'unclassified_transitions': report.unclassified_transitions}


def _evidence_size(report):
    """Count the exact UTF-8 export size without assembling a full encoded copy."""
    report.validate()
    return sum(len(chunk.encode('utf-8')) for chunk in _json_chunks(_report_json(report)))


def _load_json(data, max_bytes):
    import json
    if type(max_bytes) is not int or max_bytes < 0: raise ModelError('invalid assurance JSON byte limit')
    if type(data) not in (str, bytes): raise ModelError('assurance JSON must be text or bytes')
    try:
        if len(data) > max_bytes: raise ModelError('assurance JSON byte limit exceeded')
        raw = data.encode('utf-8') if type(data) is str else data
        if len(raw) > max_bytes: raise ModelError('assurance JSON byte limit exceeded')
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError('duplicate JSON key')
                result[key] = value
            return result
        def invalid_constant(value): raise ValueError('invalid JSON number')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
        stack = [value]
        while stack:
            item = stack.pop()
            if type(item) is str: item.encode('utf-8')
            elif type(item) is list: stack.extend(item)
            elif type(item) is dict:
                stack.extend(item.keys()); stack.extend(item.values())
        return value
    except ModelError: raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ModelError('invalid assurance JSON') from error


def _same_json(left, right):
    if type(left) is not type(right): return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _manifest_from_value(data):
    try:
        if type(data) is not dict: raise ValueError('manifest must be an object')
        provenance = data['provenance']
        families = []
        for family in data['input_families']:
            if family['coverage'] == 'required': reason = None
            elif family['coverage'] == 'excluded': reason = family['reason']
            else: raise ValueError('invalid family coverage')
            families.append(InputFamily(family['name'], reason))
        outcome = {'exhausted_within_declared_bounds': 'graph_exhausted', 'completed': 'completed'}[data['required_search_outcome']]
        manifest = Manifest(data['model'], Provenance(provenance['manifest'], provenance['model'], provenance['properties']),
                            tuple(RequiredProperty(p['id'], p['phase']) for p in data['required_properties']),
                            tuple(families), tuple(sorted(data['bounds'].items())),
                            tuple(CoverGoal(g['id'], g['scope']) for g in data['cover_goals']), outcome,
                            data['assurance_version'])
        manifest.validate()
        if not _same_json(data, _manifest_json(manifest)): raise ValueError('noncanonical manifest')
        return manifest
    except (KeyError, TypeError, ValueError, AttributeError, ModelError, RecursionError) as error:
        raise ModelError('invalid assurance manifest JSON') from error


def _report_from_value(data, *, allow_aggregate=False):
    try:
        if type(data) is not dict or type(data['assurance_version']) is not int or data['assurance_version'] != ASSURANCE_VERSION:
            raise ValueError('invalid report version')
        manifest = _manifest_from_value(data['manifest'])
        metadata = data['metadata']
        model = Metadata(metadata['name'], metadata['model_version'], metadata['properties_version'], metadata['codec_version'], metadata['build'])
        search = data['search']
        if not allow_aggregate and (search['termination'] == 'MergedJobs' or search['termination'].startswith('Campaign::')):
            raise ValueError('aggregate report requires embedded job evidence')
        outcomes = {_variant_name(value): value for value in ('graph_exhausted', 'cases_completed', 'depth_limited', 'budget_limited', 'interrupted', 'timed_out', 'failed', 'not_started')}
        counts = []
        for item in data['observed_properties']:
            prop, count = item['property'], item['counts']
            counts.append((RequiredProperty(prop['id'], prop['phase']), CheckCounts(count['passed'], count['failed'], count['skipped'], count['missing'])))
        report = Report(manifest, model, outcomes[search['outcome']], search['termination'],
                        tuple(sorted(search['budgets'].items())), tuple(counts),
                        frozenset(data['observed_families']), frozenset(data['observed_goals']),
                        tuple(data['callback_errors']), search['checked_transitions'], data['unclassified_transitions'])
        report.validate()
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in report.budgets): raise ValueError('invalid budgets')
        if any(not isinstance(error, str) for error in report.callback_errors): raise ValueError('invalid callback error')
        if not _same_json(data, _report_json(report)): raise ValueError('report catalogs do not match evidence')
        return report
    except (KeyError, TypeError, ValueError, AttributeError, ModelError, RecursionError) as error:
        raise ModelError('invalid assurance report JSON') from error


def _merged_from_value(data, max_jobs, max_evidence_bytes):
    try:
        _job_limit(max_jobs)
        if type(data) is not dict or set(data) != {'assurance_version', 'report', 'jobs', 'missing_jobs'}:
            raise ValueError('invalid merged report fields')
        if type(data['assurance_version']) is not int or data['assurance_version'] != ASSURANCE_VERSION:
            raise ValueError('invalid merged report version')
        raw_jobs, missing = data['jobs'], data['missing_jobs']
        if type(raw_jobs) is not dict or type(missing) is not list or len(raw_jobs) + len(missing) > max_jobs:
            raise ValueError('assurance job limit exceeded or invalid jobs')
        ids = []
        jobs = []
        for key, value in raw_jobs.items():
            if type(key) is not str or not key.isascii() or not key.isdecimal() or len(key) > 20:
                raise ValueError('invalid job identifier')
            job_id = int(key)
            if str(job_id) != key or job_id > U64_MAX: raise ValueError('invalid job identifier')
            ids.append(job_id)
            jobs.append((job_id, _report_from_value(value)))
        if any(type(id) is not int or not 0 <= id <= U64_MAX for id in missing):
            raise ValueError('invalid missing job identifier')
        if missing != sorted(set(missing)) or set(ids) & set(missing):
            raise ValueError('duplicate or unsorted job identifiers')
        aggregate = _report_from_value(data['report'], allow_aggregate=True)
        expected = sorted(ids + missing)
        recomputed = merge_reports(aggregate.manifest, aggregate.metadata, expected, jobs, max_jobs=max_jobs,
                                   max_evidence_bytes=max_evidence_bytes)
        base = recomputed.report
        for field in ('observed_properties', 'observed_families', 'observed_goals',
                      'checked_transitions', 'unclassified_transitions'):
            if getattr(aggregate, field) != getattr(base, field): raise ValueError('aggregate evidence mismatch')
        aggregate_budgets = dict(aggregate.budgets)
        for key in ('requested_jobs', 'reported_jobs'):
            if aggregate_budgets.get(key) != dict(base.budgets)[key]: raise ValueError('job accounting mismatch')
        if aggregate.termination == 'MergedJobs':
            if aggregate.outcome != base.outcome or aggregate.callback_errors != base.callback_errors or aggregate_budgets != dict(base.budgets):
                raise ValueError('merged outcome mismatch')
        elif aggregate.termination.startswith('Campaign::'):
            if not set(base.callback_errors).issubset(aggregate.callback_errors): raise ValueError('job errors omitted')
            accounting = aggregate_budgets.get('campaign.accounting_complete')
            def count(name):
                value = aggregate_budgets.get(name)
                if type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 20:
                    raise ValueError('invalid campaign accounting')
                number = int(value)
                if str(number) != value or number > U64_MAX: raise ValueError('invalid campaign accounting')
                return number
            requested = count('requested_jobs')
            reported = count('reported_jobs')
            started, finished, delivered = (count('campaign.' + key) for key in ('started', 'finished', 'delivered'))
            if (accounting not in ('true', 'false') or not delivered <= finished <= started <= requested
                    or reported > started or (accounting == 'true' and started != finished)):
                raise ValueError('invalid campaign accounting')
            if accounting == 'false' and 'campaign accounting is incomplete' not in aggregate.callback_errors:
                raise ValueError('missing accounting failure')
            if delivered != requested and 'not every requested campaign report was delivered successfully' not in aggregate.callback_errors:
                raise ValueError('missing delivery failure')
            added = set(aggregate.callback_errors) - set(base.callback_errors)
            if any(not (error.startswith('campaign completion/delivery error: ') or error in
                        ('campaign accounting is incomplete', 'not every requested campaign report was delivered successfully',
                         'assurance evidence byte limit exceeded', 'assurance evidence admission failed')) for error in added):
                raise ValueError('invalid campaign error')
            termination = aggregate.termination[len('Campaign::'):]
            if termination in ('jobs_completed', 'JobsCompleted'): outcome = base.outcome
            elif termination in ('failure_found', 'FailureFound', 'callback_error', 'worker_error') or termination.startswith(('CallbackError(', 'WorkerError(')):
                outcome = 'failed'
            elif termination in ('cancelled', 'Cancelled'): outcome = 'failed' if base.outcome == 'failed' else 'interrupted'
            elif termination in ('deadline', 'Deadline'): outcome = 'failed' if base.outcome == 'failed' else 'timed_out'
            else: raise ValueError('unknown campaign termination')
            if aggregate.outcome != outcome: raise ValueError('campaign outcome mismatch')
        else:
            raise ValueError('unknown aggregate termination')
        result = MergedReport(aggregate, tuple(sorted(jobs)), tuple(missing))
        if not _same_json(data, {'assurance_version': ASSURANCE_VERSION, 'report': _report_json(result.report),
                                 'jobs': {str(id): _report_json(job) for id, job in result.jobs},
                                 'missing_jobs': list(result.missing_jobs)}):
            raise ValueError('noncanonical merged report')
        return result
    except (KeyError, TypeError, ValueError, AttributeError, ModelError, RecursionError) as error:
        raise ModelError('invalid merged assurance JSON') from error
