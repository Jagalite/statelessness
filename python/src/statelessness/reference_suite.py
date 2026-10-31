"""Persist and independently replay controller mutation evidence.

Files are created exclusively and synced. A failed witness stays not_evaluated;
existing evidence is never replaced. This is not a cross-engine qualification.
"""
from dataclasses import dataclass, replace
import json
import os
import stat
from pathlib import Path

from .audit_record import record_audit
from .core import ModelError, SearchConfig, _natural
from .reference_controller import Controller, MUTATION_TARGETS, evaluate_mutation
from .replay_evidence import replay_with_observations_bounded
from .trace_binary import BinaryLimits, RunConfig, read_audit, write_audit


def _exact_target(model, trace, target, *, allow_build_mismatch=False):
    report = replay_with_observations_bounded(model, trace, allow_build_mismatch=allow_build_mismatch)
    checks = tuple(trace.initial_checks) + tuple(c for step in trace.steps for c in step.checks)
    if (trace.termination != 'property_failed' or report.outcome != 'exact' or not report.failure_reproduced
            or not any(c.id == target and c.status == 'failed' for c in checks)):
        raise ModelError('recorded witness did not exactly replay the required target')


def _remove_if_owned(path, identity):
    try:
        current=os.lstat(path)
        if (current.st_dev,current.st_ino)==identity: os.unlink(path)
    except FileNotFoundError:
        pass


def _exclusive(path, write):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    owned_fd=fd
    identity=None
    try:
        created=os.fstat(fd)
        identity=(created.st_dev,created.st_ino)
        stream=os.fdopen(fd, 'wb')
        owned_fd=None  # the stream owns and closes the descriptor
        with stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Only the file successfully created by this call is eligible for
        # cleanup; an O_EXCL collision occurs before entering this block.
        if owned_fd is not None:
            try: os.close(owned_fd)
            except OSError: pass
        # If fstat failed, identity is unknown and deleting a path could remove
        # a replacement. Leave that name for the caller to inspect.
        if identity is not None: _remove_if_owned(path,identity)
        raise
    return identity


def _sync_directory(directory):
    if os.name != 'posix': return
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def _publish_summary(directory, encoded):
    """Publish complete summary bytes once all witness names are durable."""
    temporary = directory / '.mutations.json.pending'
    final = directory / 'mutations.json'
    _sync_directory(directory)
    identity=_exclusive(temporary, lambda stream: stream.write(encoded))
    try:
        os.link(temporary, final)
        _sync_directory(directory)
    finally:
        _remove_if_owned(temporary,identity)
    _sync_directory(directory)


def verify_witness(path, mutation, *, expected_build=None, expected_witness=None,
                   _max_bytes=None, _bytes_used=None):
    """Replay a witness, optionally admitting exactly one expected foreign build.

    Model, property and codec identities and exact target replay remain required.
    """
    if mutation not in MUTATION_TARGETS: raise ModelError('unknown controller mutation')
    if expected_build is not None and (type(expected_build) is not str or not expected_build):
        raise ModelError('expected build must be a nonempty string')
    if expected_witness is not None and expected_witness not in ('original', 'minimized'):
        raise ModelError('invalid expected witness kind')
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        try: os.close(fd)
        except OSError: pass
        raise
    with stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ModelError('witness must be a regular file')
        if _max_bytes is not None and (info.st_size<0 or info.st_size>_max_bytes):
            raise ModelError('mutation suite byte limit exceeded')
        limits=BinaryLimits() if _max_bytes is None else replace(BinaryLimits(),max_total_bytes=min(_max_bytes,BinaryLimits().max_total_bytes))
        trace = read_audit(stream,limits=limits)
        if _max_bytes is not None:
            if os.fstat(stream.fileno()).st_size!=info.st_size:
                raise ModelError('mutation witness changed during read')
            _bytes_used[0]=info.st_size
    if expected_build is not None and trace.metadata.build != expected_build:
        raise ModelError('witness build identity mismatch')
    mutations = [value for key, value in trace.config.parameters if key == 'mutation']
    targets = [value for key, value in trace.config.parameters if key == 'target']
    witnesses = [value for key, value in trace.config.parameters if key == 'witness']
    if (trace.config.strategy != 'reference-fault-control' or mutations != [mutation]
            or targets != [MUTATION_TARGETS[mutation]]):
        raise ModelError('witness mutation identity mismatch')
    # Rust write_mutation_suite does not record a witness kind; when present
    # (older Python suites) it must match, but it is never required.
    if expected_witness is not None and witnesses not in ([], [expected_witness]):
        raise ModelError('witness kind identity mismatch')
    _exact_target(Controller(mutation=mutation), trace, MUTATION_TARGETS[mutation],
                  allow_build_mismatch=expected_build is not None)
    return True


@dataclass(frozen=True)
class MutationSuiteVerification:
    verified_witnesses: int
    build_matches: bool
    bytes_read: int
    max_total_bytes: int


def _read_bounded_json(path,maximum):
    flags=os.O_RDONLY | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags)
    try: stream=os.fdopen(fd,'rb')
    except BaseException:
        try: os.close(fd)
        except OSError: pass
        raise
    with stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>maximum:
            raise ModelError('invalid or oversized mutation summary')
        encoded=stream.read(maximum+1)
    if len(encoded)>maximum: raise ModelError('invalid or oversized mutation summary')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ModelError('duplicate mutation summary key')
            result[key]=value
        return result
    def invalid_number(_): raise ModelError('invalid mutation summary number')
    try: return json.loads(encoded,object_pairs_hook=pairs,parse_constant=invalid_number),len(encoded)
    except (ValueError,UnicodeError) as error: raise ModelError('invalid mutation summary') from error


def verify_mutation_suite(directory, *, max_total_bytes=67_108_864,
                          expected_build=None, expected_mutation_builds=None, return_report=False):
    """Validate and replay a complete controller suite with bounded input.

    By default all builds must be local. Foreign suites require explicit pins
    for the summary and every mutation; caller owns directory stability.
    """
    _natural(max_total_bytes,'max_total_bytes')
    if type(return_report) is not bool: raise ValueError('return_report must be boolean')
    if (expected_build is None)!=(expected_mutation_builds is None):
        raise ModelError('foreign suite requires summary and mutation build pins')
    foreign=expected_build is not None
    if foreign:
        if (type(expected_build) is not str or not expected_build or type(expected_mutation_builds) is not dict
                or set(expected_mutation_builds)!=set(MUTATION_TARGETS)
                or any(type(value) is not str or not value for value in expected_mutation_builds.values())):
            raise ModelError('invalid mutation suite build pins')
        expected_mutation_builds=dict(expected_mutation_builds)
    directory=Path(directory)
    summary,summary_bytes=_read_bounded_json(directory/'mutations.json',min(1_048_576,max_total_bytes))
    if (type(summary) is not dict or set(summary)!={'schema_version','model','build','review_status','results'}
            or type(summary['schema_version']) is not int or summary['schema_version']!=1
            or summary['model']!='reference-controller' or summary['build']!=(expected_build if foreign else Controller().metadata().build)
            or summary['review_status']!='awaiting_independent_review'):
        raise ModelError('mutation suite identity mismatch')
    rows=summary['results']
    if type(rows) is not list or len(rows)!=len(MUTATION_TARGETS): raise ModelError('incomplete mutation suite')
    expected={'mutations.json'}; seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'mutation','target','status','reason','search','shrink_complete','original','minimized'}:
            raise ModelError('invalid mutation result')
        mutation=row['mutation']
        if (type(mutation) is not str or mutation not in MUTATION_TARGETS or mutation in seen
                or row['target']!=MUTATION_TARGETS[mutation] or row['status']!='killed'
                or row['reason'] is not None or row['search'] not in ('failure_found','Some(FailureFound)')
                or row['shrink_complete'] is not True):
            raise ModelError('mutation control not established')
        seen.add(mutation)
        for label in ('original','minimized'):
            name=f'{mutation}-{label}.sttrace'
            if row[label]!=name: raise ModelError('invalid mutation witness filename')
            expected.add(name)
    actual=set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in actual or len(actual)>=len(expected):
                raise ModelError('unexpected mutation suite artifact')
            actual.add(entry.name)
    if actual!=expected: raise ModelError('unexpected mutation suite artifact')
    remaining=max_total_bytes-summary_bytes
    # Deterministic catalog order (seen equals the full catalog here).
    for mutation in (name for name in MUTATION_TARGETS if name in seen):
        for label in ('original','minimized'):
            used=[0]
            verify_witness(directory/f'{mutation}-{label}.sttrace',mutation,expected_witness=label,
                           expected_build=expected_mutation_builds[mutation] if foreign else None,
                           _max_bytes=remaining,_bytes_used=used)
            remaining-=used[0]
    verified=len(seen)*2
    if not return_report: return verified
    build_matches=(summary['build']==Controller().metadata().build and
                   (not foreign or all(expected_mutation_builds[name]==Controller(mutation=name).metadata().build for name in seen)))
    return MutationSuiteVerification(verified,build_matches,max_total_bytes-remaining,max_total_bytes)


def _quoted(text):
    """Rust src/reference/evidence.rs quoted(): escape quote, backslash, LF, CR, TAB, other controls as u00XX."""
    out=['"']
    for c in text:
        if c=='"': out.append('\\"')
        elif c=='\\': out.append('\\\\')
        elif c=='\n': out.append('\\n')
        elif c=='\r': out.append('\\r')
        elif c=='\t': out.append('\\t')
        elif c<' ': out.append('\\u%04x'%ord(c))
        else: out.append(c)
    out.append('"')
    return ''.join(out)


def _search_debug(search):
    """Rust format!("{:?}", Option<SearchTermination>), e.g. Some(FailureFound)."""
    if search is None: return 'None'
    return 'Some('+''.join(part.capitalize() for part in str(search).split('_'))+')'


def suite_json(results):
    """Byte-for-byte Rust suite_json layout: one result per line, trailing newline."""
    items=[]
    for r in results:
        killed=r.status=='killed'
        witness=lambda label: _quoted(f'{r.mutation}-{label}.sttrace') if killed else 'null'
        items.append('{"mutation":%s,"target":%s,"status":%s,"reason":%s,"search":%s,"shrink_complete":%s,"original":%s,"minimized":%s}' % (
            _quoted(r.mutation),_quoted(MUTATION_TARGETS[r.mutation]),_quoted(r.status),
            'null' if r.reason is None else _quoted(r.reason),_quoted(_search_debug(r.search)),
            'true' if r.shrink_complete else 'false',witness('original'),witness('minimized')))
    return ('{"schema_version":1,"model":"reference-controller","build":%s,"review_status":"awaiting_independent_review","results":[%s]}\n'
            % (_quoted(Controller().metadata().build),',\n'.join(items)))


def write_mutation_suite(directory, config=None, *, max_evidence_bytes=67_108_864):
    """Write controls with a combined serialized witness-byte cap.

    The cap excludes the JSON summary, separate pins, callback allocations and
    process memory. An unretained witness pair is reported as not evaluated.
    """
    config = SearchConfig() if config is None else config
    if not isinstance(config, SearchConfig): raise ModelError('config must be SearchConfig')
    _natural(max_evidence_bytes,'max_evidence_bytes')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _sync_directory(directory.parent)
    if any(directory.iterdir()):
        raise ModelError('mutation suite directory must be empty')
    results = []; retained=0
    for mutation, target in MUTATION_TARGETS.items():
        result = evaluate_mutation(mutation, config)
        if result.status == 'killed':
            model = Controller(mutation=mutation)
            created = []; pair_bytes=0
            try:
                for label, failure in (('original', result.original), ('minimized', result.minimized)):
                    if failure is None: raise ModelError('missing killed witness')
                    # Exactly Rust write_mutation_suite's RunConfig parameters.
                    params = (('mutation', mutation), ('target', target), ('max_states', str(config.max_states)),
                              ('max_transitions', str(config.max_transitions)), ('max_depth', str(config.max_depth)))
                    limits=replace(BinaryLimits(),max_total_bytes=min(BinaryLimits().max_total_bytes,max_evidence_bytes-retained-pair_bytes))
                    trace = record_audit(model, failure.inputs, RunConfig(strategy='reference-fault-control', parameters=params),
                                         max_steps=len(failure.inputs),limits=limits)
                    _exact_target(model, trace, target)
                    path = directory / f'{mutation}-{label}.sttrace'
                    identity=_exclusive(path, lambda stream: write_audit(trace, stream,limits=limits))
                    created.append((path,identity))
                    pair_bytes+=path.stat().st_size
                retained+=pair_bytes
            except Exception as error:
                for path,identity in created:
                    _remove_if_owned(path,identity)
                if isinstance(error, FileExistsError): raise
                result = replace(result, status='not_evaluated', reason='evidence preservation/replay: ' + str(error))
        results.append(result)
    expected = {f'{r.mutation}-{label}.sttrace' for r in results if r.status == 'killed'
                for label in ('original', 'minimized')}
    if {path.name for path in directory.iterdir()} != expected:
        raise ModelError('unexpected mutation suite artifact')
    encoded = suite_json(results).encode('utf-8')
    _publish_summary(directory, encoded)
    return tuple(results)


def write_mutation_suite_with_pins(directory, pins_path, config=None, *, max_evidence_bytes=67_108_864):
    """Write a suite and separately publish explicit producer-build pins.

    Pins are published only for a complete suite, never overwrite an existing
    path, and must be outside the suite directory. The evidence byte limit
    covers serialized witnesses, excluding the summary and pins. If pin
    publication fails, the already created suite remains available for inspection.
    """
    directory=Path(directory)
    pins_path=Path(pins_path)
    if pins_path.parent.resolve()==directory.resolve():
        raise ModelError('producer pins must be outside the suite directory')
    if os.path.lexists(pins_path): raise FileExistsError('producer pins already exist')
    results=write_mutation_suite(directory,config,max_evidence_bytes=max_evidence_bytes)
    if (len(results)!=len(MUTATION_TARGETS)
            or any(type(result.mutation) is not str or result.mutation not in MUTATION_TARGETS
                   or result.status!='killed' or result.reason is not None or result.search!='failure_found'
                   or result.shrink_complete is not True or result.original is None or result.minimized is None
                   for result in results)
            or {result.mutation for result in results}!=set(MUTATION_TARGETS)):
        raise ModelError('mutation suite incomplete; producer pins not published')
    payload={'report_build':Controller().metadata().build,
             'mutation_builds':{name:Controller(mutation=name).metadata().build for name in MUTATION_TARGETS}}
    encoded=(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8')
    pending=pins_path.with_name('.'+pins_path.name+'.pending')
    identity=_exclusive(pending,lambda stream: stream.write(encoded))
    try:
        os.link(pending,pins_path)
        _sync_directory(pins_path.parent)
    finally:
        _remove_if_owned(pending,identity)
    _sync_directory(pins_path.parent)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Replay a controller mutation witness or complete local suite')
    parser.add_argument('mutation', nargs='?', choices=tuple(MUTATION_TARGETS))
    parser.add_argument('path', nargs='?')
    parser.add_argument('--suite', help='verify a complete local suite directory')
    parser.add_argument('--write-suite', help='create a new mutation suite')
    parser.add_argument('--write-pins', help='publish producer pins separately for --write-suite')
    parser.add_argument('--max-evidence-bytes', type=int,
                        help='combined serialized witness byte limit for --write-suite')
    parser.add_argument('--max-total-bytes', type=int,
                        help='aggregate input byte limit for --suite')
    parser.add_argument('--pins', help='separate JSON producer build pins for --suite')
    parser.add_argument('--expected-build', help='admit a witness from this exact build identity')
    args = parser.parse_args()
    if args.write_suite is not None:
        if (args.write_pins is None or args.suite is not None or args.mutation is not None or args.path is not None
                or args.expected_build is not None or args.max_total_bytes is not None or args.pins is not None):
            parser.error('--write-suite requires --write-pins and cannot be combined with verification options')
        maximum=67_108_864 if args.max_evidence_bytes is None else args.max_evidence_bytes
        results=write_mutation_suite_with_pins(args.write_suite,args.write_pins,max_evidence_bytes=maximum)
        print(f'written={len(results)} controls pins={args.write_pins}')
    elif args.suite is not None:
        if args.write_pins is not None: parser.error('--write-pins requires --write-suite')
        if args.max_evidence_bytes is not None: parser.error('--max-evidence-bytes requires --write-suite')
        if args.mutation is not None or args.path is not None or args.expected_build is not None:
            parser.error('--suite cannot be combined with witness arguments or --expected-build')
        maximum=67_108_864 if args.max_total_bytes is None else args.max_total_bytes
        options={}
        if args.pins is not None:
            pins,_=_read_bounded_json(args.pins,1_048_576)
            if type(pins) is not dict or set(pins)!={'report_build','mutation_builds'}:
                raise ModelError('invalid mutation suite build pins')
            if (type(pins['report_build']) is not str or not pins['report_build']
                    or type(pins['mutation_builds']) is not dict
                    or set(pins['mutation_builds'])!=set(MUTATION_TARGETS)
                    or any(type(value) is not str or not value for value in pins['mutation_builds'].values())):
                raise ModelError('invalid mutation suite build pins')
            options={'expected_build':pins['report_build'],'expected_mutation_builds':pins['mutation_builds']}
        report=verify_mutation_suite(args.suite,max_total_bytes=maximum,return_report=True,**options)
        print(f'verified={report.verified_witnesses} build_matches={str(report.build_matches).lower()} bytes={report.bytes_read}')
    else:
        if args.write_pins is not None: parser.error('--write-pins requires --write-suite')
        if args.max_evidence_bytes is not None: parser.error('--max-evidence-bytes requires --write-suite')
        if args.mutation is None or args.path is None:
            parser.error('provide MUTATION PATH or --suite DIRECTORY')
        if args.max_total_bytes is not None:
            parser.error('--max-total-bytes requires --suite')
        if args.pins is not None:
            parser.error('--pins requires --suite')
        verify_witness(args.path, args.mutation, expected_build=args.expected_build)


if __name__ == '__main__': main()
