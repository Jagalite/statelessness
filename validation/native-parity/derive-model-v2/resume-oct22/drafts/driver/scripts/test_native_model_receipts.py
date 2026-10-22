"""Regression controls for installed N13 callback receipts; execute via Kyoto."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "native_model_consumers", Path(__file__).with_name("check-native-model-consumers.py")
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)

# Scenario expectations, independently literal here: dual capture and one replay.
CAPTURE = dict(initial=0, reducer=3, state=4, transition=3, encode_state=14,
               encode_input=6, encode_output=6, decode_state=0, decode_input=0, limits=0)
REPLAY = dict(initial=0, reducer=3, state=4, transition=3, encode_state=4,
              encode_input=3, encode_output=3, decode_state=1, decode_input=3, limits=4)


def receipt(engine, mode):
    return dict(engine=engine, capture_failure_controls=True,
                capture_counts=copy.deepcopy(CAPTURE) if mode == "produce" else None,
                replay_counts={} if mode == "produce" else
                {"v1": copy.deepcopy(REPLAY), "v2": copy.deepcopy(REPLAY)})


class InstalledCallbackReceipts(unittest.TestCase):
    def test_engine_build_identity_excludes_macro_crate(self):
        output = (
            "[statelessness-macros 0.2.0] cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:ae9df36b08c1cafc\n"
            "[statelessness 0.2.0] cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:8aa7d4daeb850d74\n"
        )
        self.assertEqual(DRIVER.rust_engine_build_identity(output),
                         "source-fnv1a64:8aa7d4daeb850d74")
        for invalid in (output.splitlines()[0], output + output.splitlines()[1].replace(
                "8aa7d4daeb850d74", "1111111111111111")):
            with self.assertRaises(RuntimeError):
                DRIVER.rust_engine_build_identity(invalid)

    def test_all_engines_and_actual_extra_replays(self):
        for engine in ("rust", "python", "go", "typescript", "swift"):
            for mode in ("produce", "transcode"):
                with self.subTest(engine=engine, mode=mode):
                    value = receipt(engine, mode)
                    DRIVER.validate_receipt(engine, mode, value)
                    value["replay_counts"] = {
                        "v1": dict(REPLAY), "v2": dict(REPLAY), "v2_causal": dict(REPLAY)
                    }
                    DRIVER.validate_receipt(engine, mode, value)

    def test_missing_or_wrong_measured_counter_fails_closed(self):
        for mode, bucket in (("produce", "capture_counts"), ("transcode", "v1"),
                             ("transcode", "v2")):
            for key in CAPTURE:
                for mutation in ("missing", "wrong", "boolean"):
                    with self.subTest(mode=mode, bucket=bucket, key=key, mutation=mutation):
                        value = receipt("typescript", mode)
                        counts = value[bucket] if mode == "produce" else value["replay_counts"][bucket]
                        if mutation == "missing":
                            del counts[key]
                        elif mutation == "wrong":
                            counts[key] += 1
                        else:
                            counts[key] = bool(counts[key])
                        with self.assertRaises(AssertionError):
                            DRIVER.validate_receipt("typescript", mode, value)

    def test_old_typescript_zero_provider_count_is_rejected(self):
        value = receipt("typescript", "transcode")
        value["replay_counts"]["v1"]["limits"] = 0
        with self.assertRaises(AssertionError):
            DRIVER.validate_receipt("typescript", "transcode", value)

    def test_missing_controls_engine_replay_or_extra_counts_are_rejected(self):
        mutations = (
            lambda r: r.pop("capture_failure_controls"),
            lambda r: r.update(capture_failure_controls=1),
            lambda r: r.update(engine="different"),
            lambda r: r.pop("replay_counts"),
            lambda r: r["replay_counts"].pop("v2"),
            lambda r: r["replay_counts"].update(unexpected=dict(REPLAY)),
            lambda r: r["replay_counts"]["v2"].update(unexpected=0),
            lambda r: r.update(capture_counts=dict(CAPTURE)),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                value = receipt("swift", "transcode")
                mutate(value)
                with self.assertRaises(AssertionError):
                    DRIVER.validate_receipt("swift", "transcode", value)


if __name__ == "__main__":
    unittest.main()
