#!/usr/bin/env python3
"""Check the N13 five-language row accounting, not runtime qualification.

Submit through Kyoto. This validates ledger completeness and source references;
it does not prove assertions executed or that a reference covers its row.
"""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / 'docs/native-parity/derive-model-draft'


def main():
    draft = json.loads((DRAFT / 'cases.json').read_text())
    expected = {(family, row['case']) for family, rows in draft.items()
                if isinstance(rows, list) for row in rows
                if isinstance(row, dict) and 'case' in row}
    assert len(expected) == 90, len(expected)
    result = {}
    for language in ('rust', 'python', 'typescript', 'go', 'swift'):
        ledger = json.loads((DRAFT / (language + '-dispositions.json')).read_text())
        rows = ledger['rows']
        actual = [(row['family'], row['case']) for row in rows]
        assert len(actual) == len(set(actual)), (language, 'duplicate case')
        assert set(actual) == expected, (language, 'missing', expected - set(actual), 'extra', set(actual) - expected)
        counts = collections.Counter()
        for row in rows:
            assert row['disposition'] and row['notes'] and row['qualification'], (language, row)
            refs = row['assertion_reference']
            assert refs, (language, row['case'], 'missing source reference')
            # Reference interpretation is reviewed independently. Require at
            # least its first file to exist; do not pretend this proves coverage.
            first = refs[0] if isinstance(refs, list) else refs
            path = first.split('::', 1)[0].split('#', 1)[0].split(':', 1)[0]
            assert (ROOT / path).is_file(), (language, row['case'], path)
            counts[row['disposition']] += 1
        result[language] = dict(counts)
    print(json.dumps({'scope': '90 mixed-class dispositions per engine; source accounting only', 'languages': result}, indent=2))


if __name__ == '__main__':
    main()
