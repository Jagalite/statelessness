# N07 compile provenance: source-only implementation handoff

Round 2, 2026-10-22, shared isolated checkout
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.

This change addresses the admission gap in [R1 of the proof review](OBSERVATION-REVIEW-PROOF.md).
It adds only `scripts/observation_compile_proof.py`,
`scripts/test_observation_compile_proof.py`, and this note. Other agents own the
existing drivers. No commit, staging, push, PR, implementation execution, syntax
check, build, test, or Kyoto submission was performed for this handoff. The new
regressions are authored, not qualified. Parent integration, freeze and Kyoto
execution remain required; this document is not a passing result or N07 closure.

## Integration API

`validate_compile(q, attempt, receipt_path, receipt) -> dict[str, dict]` is exported
by `scripts/observation_compile_proof.py`. `q` is the existing
`check-native-observation-consumers.py` module object. Call it from `q.load_compile`
after the current attempt has successfully sealed its sources and the prior
receipt has been strictly decoded. Pass the absolute, retained `receipt.json`
path. It returns the admitted `rust_binaries` mapping or raises `ValueError`.
It does not run Git, Cargo, rustup, a test binary, or any model implementation.

An integration shape, using the existing importlib style to avoid reliance on
script search paths, is:

```python
from types import SimpleNamespace

_proof_spec = importlib.util.spec_from_file_location(
    'observation_compile_proof', ROOT / 'scripts/observation_compile_proof.py')
_proof = importlib.util.module_from_spec(_proof_spec)
_proof_spec.loader.exec_module(_proof)

def load_compile(attempt, path):
    path = path.resolve(strict=True)
    receipt = strict_json(path.read_bytes())
    binaries = _proof.validate_compile(SimpleNamespace(**globals()), attempt, path, receipt)
    attempt.report['compile_receipt'] = dict(path=str(path), **identity(path))
    attempt.report['rust_binaries'] = binaries
    return binaries
```

The module-like namespace works even when the qualifier creates `q` using
`module_from_spec` and `exec_module` without inserting it into `sys.modules`.
The helper also accepts the actual module object when available.
Do not keep a second, partial `validate_compile_chain` as a
parallel admission implementation. The caller remains responsible for recording
`compile_receipt` and `rust_binaries` in its current report. The helper itself
watches all admitted manifests, attempt/command journals and raw logs under
`compile-proof:<root-relative-path>`, plus binaries under `rust:<name>`, so the
consumer's `Attempt.finish()` detects later mutation.

The exact `q` interface used is `RUST_TESTS`, `RECEIPTS`, `FIXTURE`, `canonical`,
`sha`, `strict_json`, `identity`, `executable_identity`, `tree_identity`, and
`receipt_path`. The current attempt must provide `root`, `initial`, `report`,
and `watch(label, path)`. Its report must already contain `expected_commit`,
`committed_source`, `source_before`, and `fixture` from a valid seal.

## Producer changes required before qualification

1. Compile both public bins in the single `rust-public-consumer-compile` command.
   The accepted argv tail is exactly:

   ```text
   build --locked --offline --manifest-path validation/corpus/Cargo.toml --bin native-observation --bin native-checked -j 2 --message-format=json-render-diagnostics
   ```

   The six-suite command retains the existing argument order:

   ```text
   test --locked --offline -p statelessness -j 2 --no-run --message-format=json-render-diagnostics --test observation --test monitor --test monitor_limits --test monitor_review --test checked_replay --test pr_review_core
   ```

   Both argv arrays begin with the absolute watched `tool:cargo` path, use
   `attempt.root` as cwd, the same fresh external `CARGO_TARGET_DIR`, and
   `CARGO_INCREMENTAL=0`. The exact set is `q.RUST_TESTS` plus the helper's
   `PUBLIC_BINS = ('native-observation', 'native-checked')`. Add native-checked
   to collection, `compiled:<name>` watches and `rust_binaries` together.

2. **Required artifact receipt field:** `rust_compiler_artifacts`, a mapping from each
   of those eight names to its complete, unmodified Cargo `compiler-artifact`
   object parsed from the corresponding retained stdout. Do not construct a
   normalized replacement, truncate fields, or infer an object from a binary
   filename. Save the exact object at the same point the producer records
   `rust_binaries[name]` and watches `compiled:<name>`.

   The concurrently updated producer already calls this field `rust_artifacts`;
   the helper accepts that spelling as an alias. At least one full mapping is
   mandatory. If both fields occur, they must be identical. No producer rename
   is necessary.

   ```python
   artifacts[name] = item  # actual strict-decoded Cargo stdout object
   attempt.report['rust_compiler_artifacts'] = artifacts
   ```

   The helper cross-checks this object against the actual raw line, independently
   derives the expected Cargo path-package identity from the sealed `Cargo.toml`,
   and matches the executable bytes to the binary/watch/artifact manifests.

   The producer's optional `cargo_target` must match the command environment.
   Its `cargo-metadata-tests` / `cargo-metadata-consumers` commands are accepted
   as a complete pair, before compilation, with exact argv
   `cargo metadata --locked --offline --format-version=1 --manifest-path <path>`
   for `Cargo.toml` / `validation/corpus/Cargo.toml`, respectively. They use the
   same watched Cargo, root cwd and environment as compilation. Their stdout
   package ID/name/version/manifest must match the sealed manifests and
   `cargo_packages = {'tests': <id>, 'consumers': <id>}`. Every external package
   listed there needs the producer's `cargo-dependency:<id>` tree watch. A
   claimed `cargo_packages` map without both metadata commands is rejected.

3. Run `resolve-cargo`, `resolve-rustc`, `resolve-rustdoc`, `cargo-version` and
   `rustc-version` with **`cwd=attempt.root`**, the same cwd as compilation.
   Existing default staging cwd is insufficient: rustup may select a different
   directory override/toolchain there. Retain the existing `tool:cargo`,
   `tool:rustc`, `resolved-tool:*`, and discovered Cargo configuration watches.
   Resolution is a required complete trio, including when the launchers are
   regular-file hardlinked/copied rustup proxies. File names and symlink status
   cannot prove that a launcher is the actual compiler. The invoked Cargo and
   rustc file identities must match either their resolved tool or the recorded
   rustup proxy. Version outputs remain retained and nonempty. All resolver,
   version, metadata and build commands must have identical recorded environments.

   The current metadata contract therefore requires rustup on the qualification
   host; the producer should fail early if it is absent, rather than publish an
   inadmissible compile receipt. A direct installation without rustup needs a
   separately reviewed positive toolchain-resolution protocol before admission;
   it is not silently inferred from an executable basename.

4. Preserve `attempt.json`, numbered `commands/NNNN-name/{started.json,
   finished.json,stdout.bin,stderr.bin}`, `raw_logs`, `input_identities`,
   `compile_artifacts`, `committed_source`, `source_before`, `source_after`,
   fixture identity, timestamps and `runtime_executed=False`. These already
   exist in the inspected producer. Every command must succeed, preserve its
   executable, and retain a source-after manifest. `started.json` has the seven
   base start fields plus known optional `staged_inputs_expected` /
   `staged_inputs_before` fields whenever present in its receipt entry. All
   start fields must match exactly; nonempty staged manifests require a matching
   staged-after manifest. Unknown start fields fail closed until their schema
   is reviewed. Failed/aborted attempt logs stay
   retained, but cannot be admitted as successful compile evidence.

   Command success follows `observation_process.wait_owned`: positive process
   and inherited-group IDs, `owns_process_group=False`, `timed_out=False`,
   `raw_logs_stable=True`, `process_tree_termination_confirmed=True`, and retained
   `statelessness.owned-process.v1` evidence with sampled terminal identities,
   including the command process in the inherited group, no live owners, and no
   errors or cleanup signals. Missing/uncertain cleanup or any wait/cleanup
   error invalidates admission even with exit code zero. The helper never
   requests a new process group or signals one.

5. Integrate the new regression file into the parent's **single post-freeze
   Kyoto qualification**, for example the existing validator discovery pattern
   can become `test_observation*.py`. Count these as validator tests, never Rust
   tests or implementation case execution. Existing fake/minimal compile
   receipts in other tests must use a complete structural baseline or explicitly
   test rejection: a negative case is meaningful only after its unmodified
   baseline is admissible. No execution was submitted by this author.

## Admission checks and limits

The helper strictly reconciles the original attempt, immutable start fields,
complete finished journals, success status, command ordering and complete raw-log
inventory. It rehashes both streams without decoding/replacing stderr; missing,
corrupted, unreported and orphan logs invalidate reuse. Canonical manifests must
have the content-addressed path, hash and length recorded in the receipt; nested
manifest references are checked too. Manifest and log references reject absolute
paths, traversal and symlink redirection. Manifest members use their actual
base: frozen root for sources, prior attempt for logs, Cargo target for artifacts.

All source manifests must equal the current attempt's sealed nonreceipt tree;
source bytes/type/mode are rechecked, and fixture/package/lock/source presence is
required. Source symlink entries retain the existing seal's link-text identity;
the helper does not claim to seal the contents of arbitrary external symlink
targets. Required Rust sources, package manifests, locks and the fixture must be
regular files. It validates the full retained Cargo target inventory and each
watched tool/configuration identity against retained before/after identities and
current files. Reuse therefore requires retaining the original external target
and tool paths; deleting them or rebuilding into that target invalidates reuse.

Each required Cargo record must occur once in the proper stream and match the
exact package ID/name/version/location, manifest path, target name/source/kind,
crate type, edition, Boolean test profile, filenames and executable. The stream
must end with one successful `build-finished`. Incidental library/build-script
or root CLI records do not widen the admitted suite set. Distinct admitted
executables must remain inside the external target and match the retained binary
identity, complete Cargo row, artifact manifest and `compiled:<name>` watch.

Source-pin equality is mandatory; exact current HEAD equality is intentionally
absent. **The parent driver must independently fix `Attempt.seal()` and
`finish()` to compare both HEAD's committed nonreceipt tree and actual working
inputs against the original pin.** Merely checking working files permits source
commits hidden by restored dirty bytes. The helper assumes that current seal and
never runs Git to recreate it. Receipt-only commits are allowed only with the
same original expected pin and unchanged nonreceipt source tree. The new unit
test exercises helper compatibility; driver tests must separately exercise real
receipt-only commits and the hidden-committed-change rejection.

This is nonmalicious provenance validation, not cryptographic attestation or a
hermetic-build proof. An actor able to rewrite every source, log and manifest is
outside its claim. Recording tools and configuration does not independently
attest all transitive registry inputs or arbitrary compiler wrapper behavior.
Compilation admission says nothing about execution success. Runtime still needs
the six source-audited suites: observation 13, monitor 10, monitor_limits 11,
monitor_review 3, checked_replay 14, pr_review_core 6. This is neither broader
workspace coverage nor `macro-qualification/monitor_release` coverage.

## Source contract preserved

The implementation reads the pinned `src/observation.rs`, `src/monitor.rs`,
`tests/observation.rs` and relevant monitor/checked tests alongside
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording).
The historical [checked-results audit](CHECKED-RESULTS-AUDIT.md) supplies semantic
requirements; its earlier missing-native-API statements are not current absence
claims. This change touches no observer, checked token, periodic checker,
Recorder, reducer, effect, or fixture behavior. Diagnostics remain observations
of already executed work and confer no continuity or checked authority.

## Authored regression cases

The structural baseline contains all eight hand-authored artifact rows, inert
text files in place of tools/binaries, retained binary stderr and a complete
attempt/journal/manifest chain. Tests prohibit process execution when invoking
the helper. They cover admission, same-pin receipt-only HEAD fields, missing or
corrupted attempt/journal/log/manifest evidence, nonzero/Boolean exits, failed
commands with lossless retained stderr, incorrect cwd/argv/tool/target, rustup
resolver outputs, metadata package/dependency watches, inherited-group cleanup,
stable-log flags, optional staged start fields,
rehashed-but-wrong source manifests, fixture/source drift, unsafe manifest paths,
strict/truncated Cargo JSON, missing/duplicate/fabricated artifact rows, package
and test-profile substitution, stream swaps, missing native-checked, binary
mutation, shared/incorrect executable paths, absent filename/watch/artifact
membership, and extra command/log evidence. These are specifications encoded in
tests, not reported passing outcomes.
