# Portable v1 qualification

## Local evidence

The native Python implementation was built and tested on Linux x86-64 with
CPython 3.13.5 and setuptools 82.0.1. The Rust runner was built with Rust 1.90.0
on GitHub Actions, then its compiled Linux executable was downloaded and run
against the same local corpus. Initial runner build and three smoke tests passed
in run [37878892923](https://github.com/Jagalite/statelessness/actions/runs/37878892923)
at commit `788c7028aae2c69c27e8d7b4dc8025d834135ad5`.

The actual Python wheel was installed into a fresh virtual environment with
`--no-index --no-deps`. Tests ran with `python -I` outside the source import path.
The installed package reports no runtime requirements and contains no shared
library, Wasm module, or Rust/FFI bridge.

| Check | Result |
| --- | --- |
| Native Python tests against installed wheel | 27 passed |
| Independent harness integrity tests | 7 passed |
| Reviewed golden corpus, each engine | 94 passed |
| All directed graph topologies on 1..3 states, each engine | 530 passed |
| State-storage-order metamorphic cases, each engine | 32 passed |
| Malformed/oversized transport cases, each engine | 13 passed; recovery passed |
| Fresh-process producer/consumer replay, Rust and Python | 40 passed |
| Deliberately broken Python engine variants | 8/8 detected by structured mismatches |
| Positive requests validated against JSON Schema | 71 valid |
| Wheel build, installation, source distribution build, native example | Passed locally |

Corpus SHA-256:
`6c9e4de68bad0b1188f7303ae9fd820bf75a3f6a2cdeeb292f3742aa9f8fbf11`.
The corpus is reproducible from reviewed expectation construction without
importing either implementation. Generated graph expectations use independent
shortest-distance relaxation, not an engine's BFS implementation.

## Reproduction and CI evidence

See `../conformance/README.md` and `../python/README.md` for commands. The
`Portable conformance` workflow additionally tests installed wheels and wheels
rebuilt from the source distribution on Linux (Python 3.10 and 3.13), macOS, and
Windows (Python 3.13). It runs the existing Rust workspace tests, Rust adapter
smoke tests and Clippy, native tests, shared/generative/differential checks,
schema checks, reproducibility, and fault controls. Its artifacts include the
source snapshot, wheel/sdist, corpus fingerprint, `GITHUB_SHA`, and reports.
The full four-job matrix passed in run
[37881358434](https://github.com/Jagalite/statelessness/actions/runs/37881358434)
at commit `3f08ffefd54885018b1ed3fb05e0491e5cf73c25`, including wheel and
source-distribution installation, Rust workspace tests, and Clippy on all three
operating systems. Its source archive exactly matched the locally tested files.
Review of the artifacts found Windows checkout/newline translation changed the
raw corpus fingerprint despite semantic agreement. The subsequent fix pins the
corpus to LF in Git and writes explicit UTF-8 bytes in its reproducer. Current
run artifacts identify the exact qualified revision and corpus fingerprint;
prior test results must not be silently carried over to changed source.

## Scope and limitations

This qualifies `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and `trace-json-v1` only.
It does not establish full Rust feature parity or universal application
correctness. Fuzzing, shrinking, guided search, independent-oracle composition,
runtime ring recording, campaigns, composition helpers, and the binary
`.sttrace` audit format are not Python capabilities in this release.

Only the declared finite inputs and properties are explored. Agreement can hide
shared mistakes. Mutation controls cover representative faults, not all possible
bugs. Native copy/hash/codecs remain application responsibilities. Callback
allocations are not process-memory bounded; no performance parity is claimed.
JSON replay envelopes do not retain the complete Rust audit configuration or
make different application codecs automatically compatible. No packages have
been published to a registry, and this work does not merge changes into main.
