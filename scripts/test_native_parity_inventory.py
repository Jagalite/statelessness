import copy
import importlib.util
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
        self.assertEqual(pinned, inventory.inventory(working=True))
        self.assertGreater(len(pinned['items']), 100)


if __name__ == '__main__':
    unittest.main()
