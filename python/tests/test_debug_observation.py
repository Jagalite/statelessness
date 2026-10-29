import unittest
from statelessness import DebugObservation, DebugInitial, DebugTurn, TurnObservation, Check, Transition, ModelError
from statelessness.checked import _turn_context, _initial_context

class DebugObservationTests(unittest.TestCase):
    def test_variants_preserve_payload_and_check_boundary_without_authority(self):
        state = [9]
        checks = [Check("one"), Check("two", "skipped", "periodic")]
        initial = DebugObservation.Initial(state, checks)
        checks.clear()
        self.assertIsInstance(initial, DebugInitial)
        self.assertIs(initial.state, state)
        self.assertEqual(len(initial.checks), 2)
        turn = TurnObservation(2**64-1, state, 1, Transition([10], ()), initial.checks, 1)
        diagnostic = DebugObservation.Turn(turn)
        self.assertIsInstance(diagnostic, DebugTurn)
        self.assertIs(diagnostic.observation, turn)
        self.assertEqual(diagnostic.observation.state_check_count, 1)
        for value in (initial, diagnostic):
            with self.assertRaises(ModelError): _turn_context(value)
            with self.assertRaises(ModelError): _initial_context(value)
    def test_turn_requires_diagnostic_view(self):
        with self.assertRaises(ValueError): DebugObservation.Turn(object())
