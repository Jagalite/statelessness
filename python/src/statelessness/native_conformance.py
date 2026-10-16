"""Explicit Rust ABI 2 fixture runner; runtime qualification is opt-in.

python -m statelessness.native_conformance --library /absolute/libstateless.dylib
"""
from __future__ import annotations
import argparse
from .conformance import run_jsonl
from .native import NativeEngine


class Backend:
    def __init__(self, engine: NativeEngine):
        self.engine = engine
        self.identity = {"implementation": "python-rust-abi2", "engine_family": "rust-shared",
                         "abi_version": 2, "artifact_sha256": engine.sha256}

    def rng(self, seed): return self.engine.rng(seed)

    def enumerate_states(self, model, config):
        with self.engine.session(model) as session:
            return session.enumerate_states(config)

    def record(self, model, inputs, maximum):
        with self.engine.session(model) as session:
            return session.record(inputs, maximum)

    def check_observed(self, model, before, input, transition, sequence, policy):
        with self.engine.session(model) as session:
            return session.check_observed(before, input, transition, sequence, policy)

    def replay(self, model, trace, *, allow_build_mismatch=False):
        with self.engine.session(model) as session:
            return session.replay(trace, allow_build_mismatch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    run_jsonl(Backend(NativeEngine(args.library, expected_sha256=args.sha256)))


if __name__ == '__main__': main()
