import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("lane",Path(__file__).with_name("validate-native-guided-installed.py"))
lane=importlib.util.module_from_spec(spec);spec.loader.exec_module(lane)

class ComparisonControls(unittest.TestCase):
    def test_boolean_is_not_a_work_counter(self):
        self.assertFalse(lane.exact({"cases":True},{"cases":1}))
        self.assertFalse(lane.exact({"cases":1.0},{"cases":1}))
    def test_omitted_and_extra_fields_fail(self):
        self.assertFalse(lane.exact({"cases":0},{"cases":0,"failure":None}))
    def test_order_and_multiplicity_are_evidence(self):
        self.assertFalse(lane.exact([1,2],[2,1]))
        self.assertFalse(lane.exact([1,1],[1]))
    def test_duplicate_json_keys_and_nonfinite_values_fail(self):
        for raw in ('{"cases":0,"cases":1}', '{"value":NaN}', '{"value":Infinity}'):
            with self.assertRaises(ValueError): lane.strict_json(raw)
    def test_empty_diagnostic_remains_present(self):
        self.assertFalse(lane.exact({"error":""},{"error":None}))
