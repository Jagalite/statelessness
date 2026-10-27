"""Public API compatibility after combining research and codec declarations."""
import unittest
import statelessness as public
from statelessness.core import ModelCodec as ProtocolModelCodec
from statelessness.codec_declarations import ModelCodec as DeclarationModelCodec


class IntegratedPublicAPI(unittest.TestCase):
    def test_existing_generic_protocol_and_new_binding_remain_distinct(self):
        self.assertIs(public.ModelCodec, ProtocolModelCodec)
        class ExistingConsumer(public.ModelCodec[int, int, int]):
            def initial_state(self): return 7
        self.assertEqual(ExistingConsumer().initial_state(), 7)
        self.assertIs(public.ModelCodecBinding, DeclarationModelCodec)
        binding = public.model_codec(state=public.U8, input=public.U8, output=public.U8)
        self.assertIsInstance(binding, public.ModelCodecBinding)
        self.assertEqual(public.__all__.count('ModelCodec'), 1)
        self.assertIn('research-equivalence-v1', public.EXPERIMENTAL_PROFILES)
