import json
import unittest

from stateful_matrix_accounting import require_complete_matrix, verify_cross_build_report


class CrossBuildAccountingTests(unittest.TestCase):
    def setUp(self):
        self.result = dict(strict_outcome='incompatible', strict_build_matches=False,
                           strict_steps_verified=0, strict_failure_reproduced=False,
                           observation_outcome='exact', observation_build_matches=False,
                           observation_steps_verified=3, observation_failure_reproduced=True)

    def report(self, result):
        return 'STATEFUL_COMPARISON ' + json.dumps(result)

    def test_valid_result_and_raw_logs(self):
        self.assertEqual(verify_cross_build_report('setup\n'+self.report(self.result)+'\n'), self.result)

    def test_no_generic_failure_or_duplicate_report_counts(self):
        for output in ('panic: replay failed', '', self.report(self.result)+'\n'+self.report(self.result),
                       'STATEFUL_COMPARISON {"strict_outcome":"incompatible","strict_outcome":"incompatible"}'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                verify_cross_build_report(output)

    def test_every_mismatch_or_missing_field_fails(self):
        for key, value in self.result.items():
            changed = dict(self.result); changed[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_cross_build_report(self.report(changed))
            del changed[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                verify_cross_build_report(self.report(changed))
        # Python's bool is an int subclass; accept neither as an alias for the other.
        changed = dict(self.result, strict_steps_verified=False)
        with self.assertRaises(ValueError): verify_cross_build_report(self.report(changed))

    def test_all_twenty_five_cells_are_required(self):
        names = ('rust', 'python', 'go', 'typescript', 'swift')
        cells = {p: {c: {'status':'reported-build-match-strict','passed':True} for c in names} for p in names}
        require_complete_matrix(names, cells)
        for producer in names:
            for consumer in names:
                cell = cells[producer].pop(consumer)
                with self.subTest(producer=producer, consumer=consumer), self.assertRaises(ValueError):
                    require_complete_matrix(names, cells)
                cells[producer][consumer] = cell
        cells['rust']['go'] = {'status':'not-run','passed':True}
        with self.assertRaises(ValueError): require_complete_matrix(names, cells)
        cells['rust']['go'] = {'status':'cross-build-observation','passed':False}
        with self.assertRaises(ValueError): require_complete_matrix(names, cells)


if __name__ == '__main__': unittest.main()
