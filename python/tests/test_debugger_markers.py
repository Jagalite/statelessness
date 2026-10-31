"""Bounded post-turn marker capture."""
import unittest
import tracemalloc

from statelessness import ModelError
from statelessness.debugger_watches import DiagnosticMarker, MarkerCollector


class MarkerTests(unittest.TestCase):
    def test_disabled_and_full_do_not_call_detail(self):
        def forbidden(): raise AssertionError('detail evaluated')
        disabled=MarkerCollector.disabled()
        disabled.marker('x',forbidden)
        self.assertEqual(disabled.finish().dropped,0)
        full=MarkerCollector(True,1,100)
        full.marker('x',lambda:'ok')
        full.marker('x',forbidden)
        batch=full.finish()
        self.assertEqual(batch.markers,(DiagnosticMarker('x','ok'),))
        self.assertEqual((batch.dropped,batch.truncated,batch.retained_bytes),(1,0,51))

    def test_utf8_boundary_and_exact_overhead(self):
        collector=MarkerCollector(True,2,52)
        collector.marker('x',lambda:'éé')
        batch=collector.finish()
        self.assertEqual(batch.markers,(DiagnosticMarker('x','é'),))
        self.assertEqual((batch.truncated,batch.retained_bytes),(1,51))
        with self.assertRaises(ModelError): collector.finish()

    def test_reentry_and_callback_failure_leave_collector_usable(self):
        collector=MarkerCollector(True,2,200)
        def recursive():
            collector.marker('inner',lambda:'bad')
            return 'bad'
        with self.assertRaises(ModelError): collector.marker('outer',recursive)
        with self.assertRaises(ValueError): collector.marker('x',lambda:'\ud800')
        collector.marker('safe',lambda:'ok')
        batch=collector.finish()
        self.assertEqual(batch.markers,(DiagnosticMarker('safe','ok'),))
        self.assertEqual((batch.dropped,batch.truncated),(0,0))

    def test_large_inputs_do_not_create_full_utf8_temporaries(self):
        large='é'*200_000
        collector=MarkerCollector(True,2,52)
        tracemalloc.start()
        try:
            collector.marker(large,lambda:'unused')
            collector.marker('x',lambda:large)
            _,peak=tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        batch=collector.finish()
        self.assertEqual((batch.dropped,batch.truncated),(1,1))
        self.assertEqual(batch.markers,(DiagnosticMarker('x','é'),))
        self.assertLess(peak,100_000)


if __name__=='__main__': unittest.main()
