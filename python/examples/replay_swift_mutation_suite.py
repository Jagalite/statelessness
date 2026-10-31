"""Replay a Swift controller suite with an explicitly pinned producer build."""
import argparse
from pathlib import Path

from statelessness import ModelError
from statelessness.reference_controller import MUTATION_TARGETS
from statelessness.reference_suite import verify_witness
from replay_rust_mutation_suite import _read_summary


def replay_suite(directory, producer_build):
    if type(producer_build) is not str or not producer_build.endswith(';fault=none'):
        raise ModelError('expected non-mutant Swift producer build identity')
    root = Path(directory)
    summary = _read_summary(root / 'mutations.json')
    if (type(summary) is not dict or set(summary) != {'schema_version', 'model', 'build', 'review_status', 'results'}
            or type(summary['schema_version']) is not int or summary['schema_version'] != 1
            or summary['review_status'] != 'awaiting_independent_review'):
        raise ModelError('invalid mutation summary')
    if summary['model'] != 'reference-controller' or summary['build'] != producer_build:
        raise ModelError('producer summary identity mismatch')
    rows = summary['results']
    if type(rows) is not list or len(rows) != len(MUTATION_TARGETS):
        raise ModelError('invalid mutation summary')
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'mutation', 'target', 'status', 'reason', 'search', 'shrink_complete', 'original', 'minimized'}:
            raise ModelError('invalid mutation result')
        mutation = row['mutation']
        if (type(mutation) is not str or mutation not in MUTATION_TARGETS or mutation in seen
                or row['status'] != 'killed' or row['reason'] is not None
                or row['search'] != 'Some(FailureFound)' or row['shrink_complete'] is not True
                or row['target'] != MUTATION_TARGETS[mutation]):
            raise ModelError('invalid mutation result')
        seen.add(mutation)
        build = producer_build[:-4] + mutation
        for label in ('original', 'minimized'):
            filename = f'{mutation}-{label}.sttrace'
            if row[label] != filename: raise ModelError('invalid mutation witness filename')
            verify_witness(root / filename, mutation, expected_build=build, expected_witness=label)
    return len(seen) * 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--producer-build', required=True)
    args = parser.parse_args()
    print(f'{replay_suite(args.directory, args.producer_build)} Swift witnesses replayed')
