"""Replay Rust controller witnesses with an explicitly pinned producer build.

Usage: python replay_rust_mutation_suite.py DIRECTORY --producer-build BUILD
BUILD is the non-mutant Rust controller build recorded by the producer; it is
provided independently, not inferred from the artifacts being verified.
"""
import argparse
import json
import os
import stat
from pathlib import Path

from statelessness import ModelError
from statelessness.reference_controller import MUTATION_TARGETS
from statelessness.reference_suite import verify_witness

_SUMMARY_LIMIT = 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelError('duplicate summary key')
        result[key] = value
    return result


def _read_summary(path):
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _SUMMARY_LIMIT:
            raise ModelError('invalid or oversized mutation summary')
        data = stream.read(_SUMMARY_LIMIT + 1)
    if len(data) > _SUMMARY_LIMIT:
        raise ModelError('invalid or oversized mutation summary')
    def invalid_constant(_): raise ModelError('invalid summary number')
    try:
        return json.loads(data, object_pairs_hook=_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as error:
        raise ModelError('invalid mutation summary') from error


def replay_suite(directory, producer_build):
    if type(producer_build) is not str or not producer_build.endswith('-None'):
        raise ModelError('expected non-mutant Rust producer build identity')
    root = Path(directory)
    summary = _read_summary(root / 'mutations.json')
    if (type(summary) is not dict or set(summary) != {'schema_version', 'model', 'build', 'review_status', 'results'}
            or type(summary['schema_version']) is not int or summary['schema_version'] != 1
            or summary['review_status'] != 'awaiting_independent_review'):
        raise ModelError('invalid mutation summary')
    if summary['model'] != 'reference-controller' or summary['build'] != producer_build:
        raise ModelError('producer summary identity mismatch')
    rows = summary.get('results')
    if not isinstance(rows, list) or len(rows) != len(MUTATION_TARGETS):
        raise ModelError('invalid mutation summary')
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'mutation', 'target', 'status', 'reason', 'search', 'shrink_complete', 'original', 'minimized'}:
            raise ModelError('invalid mutation result')
        mutation = row.get('mutation')
        if (type(mutation) is not str or mutation not in MUTATION_TARGETS or mutation in seen
                or row['status'] != 'killed' or row['reason'] is not None
                or row['search'] != 'Some(FailureFound)' or row['shrink_complete'] is not True
                or row['target'] != MUTATION_TARGETS[mutation]):
            raise ModelError('invalid mutation result')
        seen.add(mutation)
        variant = ''.join(part.capitalize() for part in mutation.split('-'))
        build = producer_build[:-4] + 'Some(' + variant + ')'
        for label in ('original', 'minimized'):
            filename = f'{mutation}-{label}.sttrace'
            if row.get(label) != filename:
                raise ModelError('invalid mutation witness filename')
            verify_witness(root / filename, mutation, expected_build=build)
    return len(seen) * 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--producer-build', required=True)
    args = parser.parse_args()
    print(f'{replay_suite(args.directory, args.producer_build)} Rust witnesses replayed')
