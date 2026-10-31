import unittest
from statelessness import CampaignConfig, FuzzConfig, ModelError, fuzz

class AdmissionTests(unittest.TestCase):
    def test_enclosing_validation_precedes_mutation(self):
        config = FuzzConfig(mutation_percent=101)
        with self.assertRaisesRegex(ModelError, "campaign jobs must be positive"):
            CampaignConfig(jobs=0, fuzz=config).validate()
        with self.assertRaisesRegex(ModelError, "mutation_percent must be in"):
            CampaignConfig(jobs=1, fuzz=config).validate()
        with self.assertRaisesRegex(ModelError, "mutation_percent must be in"):
            fuzz(None, config)
