import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('inventory', Path(__file__).with_name('native_parity_inventory.py'))
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class InventoryTests(unittest.TestCase):
    def test_comments_strings_raw_strings_and_lifetimes(self):
        source = '''// pub fn fake() {}
/* outer /* pub fn nested() {} */ pub fn hidden() {} */
const TEXT: &str = r##"pub fn nope() {} \"#"##;
pub fn actual<'a>(value: &'a str) {}
pub(crate) fn internal() {}
'''
        items = inventory.declarations('src/model.rs', source)
        self.assertEqual([item['name'] for item in items], ['TEXT', 'actual', 'internal'])
        self.assertEqual(items[1]['line'], 4)
        self.assertEqual(items[2]['visibility'], 'restricted')

    def test_trait_impl_reexports_fields_and_multiline(self):
        source = '''pub trait Model { fn run(&self); type State; const SIZE: usize; }
impl Model for Example { fn run(&self) {} type State = (); const SIZE: usize = 0; }
pub use crate::{Model, Other};
pub struct Config { pub limit: usize }
pub async fn
multiline() {}
pub const fn constant() {}
pub extern "C" fn foreign() {}
pub use {Model as Renamed, Other};
'''
        items = inventory.declarations('src/model.rs', source)
        self.assertEqual(sum(item['name'] == 'run' for item in items), 2)
        self.assertTrue(any(item['kind'] == 'use' for item in items))
        self.assertTrue(any(item['kind'] == 'field' for item in items))
        self.assertTrue(any(item['name'] == 'multiline' for item in items))
        self.assertTrue(any(item['name'] == 'constant' and item['kind'] == 'fn' for item in items))
        self.assertTrue(any(item['name'] == 'foreign' and item['visibility'] == 'public' for item in items))
        self.assertEqual(sum(item['kind'] == 'use' for item in items), 2)

    def test_mapping_and_duplicate_fail_closed(self):
        items = inventory.declarations('src/explore.rs', 'pub fn shrink() {} pub fn fuzz() {}')
        self.assertEqual([item['gap_ids'] for item in items], [['N04'], ['N03']])
        document = {'items': items}
        inventory.validate(document)
        duplicate = copy.deepcopy(document)
        duplicate['items'].append(duplicate['items'][0])
        with self.assertRaises(ValueError):
            inventory.validate(duplicate)
        document['items'][0]['gap_ids'] = []
        with self.assertRaises(ValueError):
            inventory.validate(document)
        with self.assertRaises(KeyError):
            inventory.gap_for('src/unreviewed.rs', 'new')

    def test_inventory_and_working_source_drift(self):
        pinned = inventory.inventory()
        inventory.validate(pinned)
        record = json.loads(inventory.AMENDMENTS.read_text())
        inventory.check_working(pinned, inventory.inventory(working=True), record)
        self.assertGreater(len(pinned['items']), 100)

    def test_amendments_accept_only_exact_reviewed_drift(self):
        item = lambda path, name: {'id': f'{path}:1:1:fn:{name}', 'path': path, 'name': name}
        expected = {'baseline_commit': 'b', 'files': [{'path': 'a.rs', 'sha256': '1'}, {'path': 'b.rs', 'sha256': '2'}],
                    'items': [item('a.rs', 'x'), item('b.rs', 'y')]}
        working = copy.deepcopy(expected)
        working['files'][0]['sha256'] = '9'
        working['items'][0] = item('a.rs', 'moved')
        amendment = {'path': 'a.rs', 'baseline_sha256': '1', 'sha256': '9', 'reason': 'r', 'gap_ids': ['N01']}
        record = {'schema_version': 1, 'baseline_commit': 'b', 'amendments': [amendment]}
        inventory.check_working(expected, working, record)
        failures = {
            'missing': {**record, 'amendments': []},
            'stale': {**record, 'amendments': [amendment, {**amendment, 'path': 'b.rs', 'baseline_sha256': '2', 'sha256': '2'}]},
            'hash': {**record, 'amendments': [{**amendment, 'sha256': '8'}]},
            'baseline': {**record, 'baseline_commit': 'other'},
            'reason': {**record, 'amendments': [{**amendment, 'reason': ''}]},
            'duplicate': {**record, 'amendments': [amendment, amendment]},
        }
        for name, bad in failures.items():
            with self.subTest(name), self.assertRaises(ValueError):
                inventory.check_working(expected, working, bad)
        unamended = copy.deepcopy(working)
        unamended['items'][1] = item('b.rs', 'renamed')
        with self.assertRaises(ValueError):
            inventory.check_working(expected, unamended, record)

    def test_added_files_require_exact_addition_amendment(self):
        item = lambda path, name: {'id': f'{path}:1:1:fn:{name}', 'path': path, 'name': name}
        expected = {'baseline_commit': 'b', 'files': [{'path': 'a.rs', 'sha256': '1'}], 'items': [item('a.rs', 'x')]}
        working = copy.deepcopy(expected)
        working['files'].append({'path': 'new.rs', 'sha256': '7'})
        working['items'].append(item('new.rs', 'added'))
        addition = {'path': 'new.rs', 'baseline_sha256': None, 'sha256': '7', 'reason': 'r', 'gap_ids': ['N01']}
        record = {'schema_version': 1, 'baseline_commit': 'b', 'amendments': [addition]}
        inventory.check_working(expected, working, record)
        failures = {
            'unrecorded': {**record, 'amendments': []},
            'hash': {**record, 'amendments': [{**addition, 'sha256': '8'}]},
            'claims-baseline': {**record, 'amendments': [{**addition, 'baseline_sha256': '7'}]},
        }
        for name, bad in failures.items():
            with self.subTest(name), self.assertRaises(ValueError):
                inventory.check_working(expected, working, bad)
        removed = copy.deepcopy(expected); removed['files'] = []; removed['items'] = []
        with self.assertRaises(ValueError):
            inventory.check_working(expected, removed, {'schema_version': 1, 'baseline_commit': 'b', 'amendments': []})
        self.assertEqual(inventory.gap_for('src/graph.rs', 'explore_graph'), ['N01'])
        self.assertEqual(inventory.gap_for('src/continued.rs', 'shrink_continued'), ['N04'])


if __name__ == '__main__':
    unittest.main()
