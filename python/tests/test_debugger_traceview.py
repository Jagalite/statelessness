import unittest
from statelessness.debugger_traceview import TraceViewer
from test_debugger import Counter, fixture


class TraceViewTests(unittest.TestCase):
    def test_passive_cursor_and_explicit_decoder(self):
        viewer=TraceViewer(fixture())
        self.assertEqual(viewer.position,0)
        viewer.seek(1)
        self.assertEqual(viewer.state_bytes().bytes,b'5')
        self.assertEqual(viewer.outputs(),((),True))
        self.assertEqual(viewer.decode_state(Counter(),1),5)
        self.assertEqual(viewer.position,1)
        self.assertEqual(viewer.verify(Counter()).outcome,'exact')
        self.assertEqual(viewer.position,1)
