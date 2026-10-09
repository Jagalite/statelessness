# Shared conformance kit

This compares **independent engines**: the existing native Rust library and a
native Python library. It is distinct from the existing Rust/Go paired reducer
kit, whose Go SDK calls the Rust engine.

From the repository root:

```sh
python -m pip install ./python
cargo build --manifest-path validation/corpus/Cargo.toml --locked
python conformance/run.py \
  --runner '["python", "-m", "statelessness.conformance", "--runner"]' \
  --runner '["validation/corpus/target/debug/stateless-corpus"]' \
  --report target/conformance.json
python conformance/mutations.py
```

On Windows, append `.exe` to the Rust executable. The Rust JSON adapter is an
isolated Cargo workspace: its serde dependencies do not enter the library's
zero-dependency workspace or consumers. The Rust runner targets 64-bit systems
for full-width RNG bounds. Python-only users need no Rust installation:

```sh
python -m statelessness.conformance conformance/corpus.json
python conformance/run.py \
  --runner '["python", "-m", "statelessness.conformance", "--runner"]'
```

The harness invokes actual packaged/native engines; it never implements their
search or recording logic. `corpus.json` has 94 reviewed golden cases with rule
IDs. `build_corpus.py` reproduces the artifact without importing either engine.
Review changes to expectations against the specification, not just current code.

Additional qualification comprises all 530 directed graph topologies on one to
three labeled states, 32 state-order metamorphic cases, 13 malformed transport
cases plus post-error recovery, and fresh-process recording/replay for every
producer/consumer pairing. Generated expectations use shortest-distance
relaxation, independently of each engine's BFS queue and deduplication machinery.
Each implementation must match expected results; agreeing with another buggy
implementation is insufficient. JSON comparison preserves types and array order.

The table representation is deliberately finite: a state ID, ordered edges,
checks, outputs, dispositions, and optional controlled callback errors. It is
not an interpreter for arbitrary application source or a general expression DSL.
Native applications keep their own types and functions.

`mutations.py` tests eight isolated engine source changes. It requires structured
corpus mismatches; a crash, syntax error, or timeout is not counted as detection.
It leaves the original source unchanged. Add further fault controls with new
profiles. Passing a finite corpus is not a universal correctness proof.

The JSON replay envelope does not replace the existing binary `.sttrace` audit
format or make unrelated model codecs compatible. See `../spec/README.md` for
precise supported profiles, identity rules, and limits.
