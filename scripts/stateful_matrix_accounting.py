"""Fail-closed decoding for the stateful cross-build negative control.

The fixture emits this only after checking its live and recorded identities,
strict replay, and the separately authorized observation comparison. This is
not evidence that different artifacts have identical builds.
"""
import json

_PREFIX = 'STATEFUL_COMPARISON '
_EXPECTED = {
    'strict_outcome': 'incompatible',
    'strict_build_matches': False,
    'strict_steps_verified': 0,
    'strict_failure_reproduced': False,
    'observation_outcome': 'exact',
    'observation_build_matches': False,
    'observation_steps_verified': 3,
    'observation_failure_reproduced': True,
}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate structured result key')
        result[key] = value
    return result


def verify_cross_build_report(output):
    """Accept exactly one complete result, never an exit code or prose alone."""
    lines = [line[len(_PREFIX):] for line in output.splitlines() if line.startswith(_PREFIX)]
    if len(lines) != 1:
        raise ValueError('missing or repeated structured result')
    try:
        result = json.loads(lines[0], object_pairs_hook=_unique)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError('invalid structured result') from error
    if type(result) is not dict or result.keys() != _EXPECTED.keys():
        raise ValueError('unexpected structured result fields')
    for key, expected in _EXPECTED.items():
        if type(result[key]) is not type(expected) or result[key] != expected:
            raise ValueError('unexpected structured result: ' + key)
    return result


def require_complete_matrix(names, observations):
    """Require every ordered cell to have executed and passed its proper mode."""
    expected = set(names)
    if len(expected) != len(names) or set(observations) != expected:
        raise ValueError('missing or unexpected producer')
    for producer in names:
        row = observations[producer]
        if type(row) is not dict or set(row) != expected:
            raise ValueError('missing or unexpected consumer: ' + producer)
        for consumer in names:
            cell = row[consumer]
            if (type(cell) is not dict or type(cell.get('passed')) is not bool
                    or not cell['passed'] or cell.get('status') not in
                    ('reported-build-match-strict', 'cross-build-observation')):
                raise ValueError('unsuccessful cell: '+producer+' to '+consumer)
