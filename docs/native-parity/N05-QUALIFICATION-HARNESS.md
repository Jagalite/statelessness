# N05 installed qualification harness

Status: **implemented source; not executed or qualified**. No source freeze,
package build, fixture execution or candidate comparison has been performed by
the harness author. The root schedules this work after all runtime/consumer
sources and the independent expectations are stable. Existing queued language
jobs remain owned by their submitters; this harness neither duplicates nor
cancels them. No N06/N10 prerequisite, profile/default promotion, push or PR.

Owned files: `scripts/check-native-guided-consumers.py`,
`scripts/native-guided-qualification.py`, `scripts/native-guided-support/`, and
this document. Runtime, corpus and consumer files belong to their respective
agents. All changes are unstaged for the root to review and commit.

## Consumer contract

Consumers live in `scripts/native-guided-consumers/{python,typescript,go,swift,rust}`.
They call public installed APIs. The shared invocation is:

```text
CONSUMER --corpus PATH --case ID
```

`PATH` is the exact independently frozen corpus, beside its original FREEZE.json.
Consumers exclude `expected` from fixture execution; the harness never passes
`--compare` and owns the cross-engine comparison. Each invocation is a fresh OS
process selecting exactly one case. Stdout must contain one JSON object:

```text
{schema:"stateless-guided-v2-results/1",engine:"LANGUAGE",
 cases:[{id:"ID",actual:{report,error,trace,generator_rng,witness_replays,shrink}}]}
```

All six fields are mandatory, and unknown output fields, missing/duplicate case
IDs, incorrect engine identity, floats, nonfinite numbers and duplicate JSON
keys fail. Comparison preserves array order and distinguishes booleans from
integers. `rng_audit` belongs only to the independent expected corpus and is not
compared. No expected value is generated from candidate output. Source behavior
and the fixture schema remain owned by `conformance/guided-v2/README.md` and
`docs/native-parity/N05-SOURCE-CONTRACT.md`.

README specification revision 3 keeps those six fields and adds actual
`{kind,reason}` dispositions to transition observer/feedback entries and witness
records. Witnesses are retained passing entries in FIFO order, followed only
for `shrink_config` cases by original and minimized failing witnesses. Each
includes the public replay outcome, failure reproduction and verified step
count. The harness compares these nested fields exactly, including empty error
strings and trace ordering. It neither synthesizes witnesses nor shrinks passing
entries. The archived 78-case seal is historical evidence; only the current
`conformance/guided-v2/FREEZE.json` with the explicitly supplied hash is admitted.

Package entrypoints and staging:

| Engine | External consumer | Installed dependency |
| --- | --- | --- |
| Python | `consumer.py`, fresh venv Python `-I -B` | Wheel built in a separate fresh builder venv; `pip install --no-index --no-deps` |
| TypeScript | `run.mjs`, or `consumer.ts` compiled against installed declarations | `npm pack` tarball installed into a new external project; staged package's declared TypeScript compiler |
| Go | `main.go` and sibling Go source in a fresh module | Read-only source distribution through a local module replacement; package import and module paths verified |
| Swift | One executable directory under `Sources/` | External SwiftPM package pointing at read-only staged package; resolved dependency path verified |
| Rust | `native-guided-rust`, separate Cargo workspace | Actual `.crate` from direct `cargo package`; safely extracted read-only; staged `stateless.path` rewritten to this absolute package path |

The harness writes explicit build manifests only in the temporary external
consumer projects. Authored originals remain in the read-only snapshot. Python
distribution metadata must bind the wheel URL and SHA-256; its imported module
must reside in that venv. Node resolution and npm's lock bind the installed
package. Go/Swift public dependency metadata, Rust Cargo metadata, dependency
sources/locks, dependency files and final executable hashes are retained.
Rust `--host-checks` runs separately and does not create cross-language goldens.
Native ownership/lifetime/async/throwable policy tests remain separately scoped
language evidence; passing this corpus alone does not prove those boundaries.

## CLI and execution order

The orchestration script requires Python 3.11+ (`tomllib`). macOS/POSIX is the
current harness host. No missing-engine switches exist. First obtain the corpus
and FREEZE hashes from the independent specification author, not from candidate
output. Prepare only after that author's freeze and the root's source freeze:

```sh
python3 scripts/native-guided-qualification.py prepare \
  --source /Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7 \
  --expect-head FULL_FROZEN_CHECKOUT_HEAD \
  --expected-sha256 INDEPENDENT_CORPUS_SHA256 \
  --freeze-sha256 INDEPENDENT_FREEZE_SHA256 \
  --rust-toolchain-root /Users/jagatranvo/.rustup/toolchains/1.90.0-aarch64-apple-darwin \
  --output /tmp/n05-installed-UNIQUE
```

Preparation performs only VCS identity, file validation and staging. It requires
an absent receipt directory outside the checkout and creates a separate temporary
workspace. It checks the authority hashes for Rust guided/explore/model against
the audit's `a4cc11eea8db62115b452eee3c6610c04fa06b96`, explicitly pins the candidate
HEAD, and records dirty/untracked source bytes. Expected corpus and FREEZE must
match the supplied hashes; FREEZE must bind that corpus and authority. The source
manifest also includes the independent oracle, source contract, corpus README,
all source/package/consumer/harness inputs and build-embedded requirements.
The admitted independent seal is `stateless-guided-v2-freeze/1`, `revision: 3`,
with `candidate_output_used: false`. Corpus identity is read only from
`files["conformance/guided-v2/corpus.json"]`, not a top-level `corpus_sha256`.
All four independently sealed files (corpus, oracle, README, source contract)
are hash-checked in both live source and immutable snapshot during preparation
and at every verification boundary. The seal's case count must equal the corpus
length; no 78/89/90-case constant is embedded in the guided harness.
The harness neither writes nor repairs the independent seal. The author's
stdout summary has a `corpus_sha256` field; that summary is not the seal schema.
`spec/**` and `conformance/test_paired_profile.py` are staged because Cargo's
explicit package include list names them. Extracted Rust package `spec/` bytes
are checked against the snapshot alongside library and embedded requirements.
The explicit selection in `qualification.SELECTIONS` includes `swift/Sources`,
`swift/Tests`, `swift/Examples` and package metadata, never `swift/validation`.
Python and TypeScript use explicit package roots; Go and consumer receipt
directories are excluded. Only the authority/source-contract/harness documents
and Rust's embedded requirements are selected under `docs/`. Unrelated progress
documents and language evidence updates cannot change the runtime source hash.

`--rust-toolchain-root` must name an installed toolchain containing real
`bin/cargo`, `bin/rustc`, `bin/rustdoc`, and `lib/rustlib`; it must not name the
rustup proxy directory `~/.cargo/bin`. Preparation uses file inspection only.
It binds executable and library membership/modes/hashes, including library
file-symlink referents resolved through the entire chain. Directory symlinks,
broken links and cycles are rejected rather than scanned incompletely.
Every Cargo plan uses that absolute Cargo executable, with
absolute `RUSTC`/`RUSTDOC`, empty compiler wrappers and that toolchain's `bin`
first on the recorded PATH. Encoded rustc/rustdoc flags explicitly select that
same installation as `--sysroot`; version and sysroot probes use the real rustc.
The root has selected the installed Rust 1.90 toolchain shown above; the Rust
owner's static dependency MSRV eligibility review is not executable proof.
The toolchain is rechecked at submission, attachment, execution boundaries and
finalization. Changing rustup's default cannot select another compiler for these
jobs. The root supplies the intended installed toolchain directory.

Cargo's shared dependency cache is retained through an explicit, resolved
`CARGO_HOME`. The harness freezes the absence of `config` and `config.toml` in
that directory and in every `.cargo` directory from each planned Cargo cwd up
to the filesystem root. It rejects an existing or newly appearing configuration
file, including symlinks, without changing any user configuration. This prevents
ambient includes, target rustflags, source replacements or wrappers from escaping
the pinned tools/sysroot. Sanitized submission environments omit inherited Cargo
and compiler flags; the frozen explicit sysroot flags are the ones submitted.

Preparation emits `job-plan.json`. `submit-job` submits its literal command to
the default shared `python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit` with a
recorded clean environment. Cargo remains the command visible to Kyoto's policy;
it is never launched inside the native build/run wrapper. No policy changes,
private queue, direct Cargo fallback, auto-retry or automatic watching occur.

For each job, submit once, retain its returned ID, inspect its terminal outcome,
then attach it:

```sh
python3 scripts/native-guided-qualification.py submit-job \
  --session /tmp/n05-installed-UNIQUE --name JOB_NAME
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show RETURNED_JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs RETURNED_JOB_ID
python3 scripts/native-guided-qualification.py attach-job \
  --session /tmp/n05-installed-UNIQUE --name JOB_NAME --job-id RETURNED_JOB_ID
```

Use this order:

1. `rust-package`: direct `cargo package --offline --locked --allow-dirty
   --no-verify ... -p statelessness`. This packages only; it is not runtime proof.
2. `install-rust --session ...`: safely extract that successful package and bind
   source contents. This is file staging, not compilation or fixture execution.
3. `cargo-version` and `build-native`: the latter is a Kyoto wrapper running the
   Python/npm/Go/Swift package builds and public portable runner builds. It never
   starts Cargo. Rust package installation must precede it.
4. `rust-guided-lock`, `rust-guided-metadata`, `rust-guided-build`, attaching each
   terminal job before the next. These are direct Cargo jobs with separate target
   directories and `CARGO_INCREMENTAL=0` in the recorded submission environment.
5. `rust-portable-lock`, `rust-portable-metadata`, `rust-portable-build`, likewise.
6. `run`: Kyoto executes every shared case in every engine, Rust host assertions,
   and the preserved public portable baseline. Attach the terminal run job.
7. `finalize --session ...`: verify all receipts and identities again, then write
   `qualification.json` with `status: passed`. This final step only seals evidence.

`scripts/check-native-guided-consumers.py --session ...` is an execution-only
alias for `run`, useful for inspection/manual tooling. The planned, sealable
Kyoto `run` job uses `native-guided-qualification.py run`; execute the emitted
job plan so the requested/effective command checks match exactly.

The portable lane uses the original frozen `conformance/run.py`, its corpus and
portability vectors with all five public packaged runners. The harness replaces
only its subprocess I/O logger, retaining the suite's expectations, generated
graphs, metamorphic/transport cases and fresh-process replay pairs. It checks
engine identity independently. The four preserved profiles are core-v1, bfs-v1,
rng-splitmix64-v1 and trace-json-v1; this is not qualification of every later
portable profile.
The baseline requires 694 cases per engine (94 golden, 530 generated, 32
metamorphic, 38 portability), 23 transport cases plus recovery per engine, and
550 replay checks across 25 ordered engine pairs. Including five identity
probes, those baseline batches use 45 fresh processes.

## Evidence, failures and corrections

Every child invocation has exact argv, cwd, effective environment, raw stdin,
raw stdout, raw stderr, exit code, wall-clock duration in nanoseconds, timeout
and SHA-256/byte counts. No replacement-character decoding or diagnostic
truncation touches those logs. Protocol parsing is a separate strict operation.
Timeout handling terminates only the process group created for that invocation.

Compact `objects/<sha256>.json` manifests map paths to file type, permission mode,
content hash and size (or symlink target). Directory membership is included.
Unchanged trees reuse the same hash reference across before/after checkpoints.
Source/package symlinks are rejected during staging; installed venv symlinks are
recorded and interpreter target identities are checked separately. Generated
build/cache/receipt directories are explicitly excluded from authored source
membership, while package installations and runtime artifacts get their own
complete manifests. Cargo dependency source trees and locks are also bound.
Attaching each lock-generation job preserves the raw Cargo.lock and its original
hash/mode. Metadata submission/attachment, build submission/attachment, execution
and finalization all require that same lock identity; the post-build lock cannot
replace the resolution whose metadata was captured.
Existing language and corpus evidence is referenced separately by
`source-evidence-before.json` and `source-evidence-after.json`, through reused
content-addressed manifests. These references are not freeze prerequisites.
Swift's historical `validation/n05/snapshots` payloads and build caches are
excluded even from those reference scans; their authored compact manifest and
raw log files remain referenced. Append-only receipt changes cannot invalidate
the runtime or package identity.

The wrapper records measured counts and wall times. `peak_rss_bytes` is explicitly
null because portable child-process-tree RSS measurement is not implemented;
Kyoto's own raw resource observations are retained without treating a sampled
value as a hard bound. Guidance limits count features, entries and retained
inputs; they do not cap process RSS or application callback allocations.

Source membership/type/mode/hash, original HEAD, immutable snapshot, expected
revision, installed packages, tools, dependencies and executable identities are
checked before/after execution and again before sealing. Missing engines, drift,
wrong authority, mismatched expected revisions, incomplete/failed Cargo jobs,
command changes and incomplete baseline results fail closed. Compile/package
success is never labeled runtime success. Until the terminal run job is attached
and `finalize` succeeds, successful phase receipts say `complete-unsealed`.
The run receipt binds all top-level result artifacts: guided comparison, execution
summary, Rust host assertions, portable baseline, and portable orchestration
stdout. Finalization checks their membership/type/mode/hash and independently
rechecks the recorded portable baseline hash, as well as the raw command logs.
Every actual/expected content-addressed object referenced by the comparison is
loaded through its hash validator and compared again before sealing; missing or
corrupt objects fail even if the top-level comparison file is unchanged.

All raw failures remain. The comparison accumulates case failures across engines
and still attempts the host/baseline lanes. Any correction requires a new source
freeze and absent output directory; use `prepare --supersedes OLD_SESSION` to
retain a manifest link to the previous attempt. A submission directory is an
exclusive retry fence: if submission response is lost, inspect its raw bytes and
the shared Kyoto queue rather than resubmitting. Readback of failed/terminated
jobs remains possible after a killed worker leaves a lock. Do not delete old
receipts, steal a live phase lock, or reinterpret focused reruns as aggregate
success.

Source review corrections (before any harness execution): the first review's
remaining findings concerned a lock first bound after build, rustup proxy hashes,
and unbound top-level result files. The generation-time lock capture, explicit
actual toolchain, and result artifact manifest above address those findings.
Root review then identified a mismatch between the author's files-map seal and
the harness's earlier top-level corpus hash assumption; the harness now reads
and verifies the exact independent revision-3 schema and all four file pins.
The second source review confirmed the original three findings closed, then
identified ambient Cargo configuration, nested toolchain directory symlinks and
unchecked comparison-object references. The configuration absence guard plus
explicit sysroot, directory-symlink rejection, and referenced-object validation
above address those remaining findings. These corrections received source-only
inspection and whitespace checks; no executable harness validation has run.
No runtime pass or new candidate expectation is inferred from this source review.

## Pending root actions

Wait for all language consumers, native runtime tests and independent FREEZE.
Confirm the common consumer flags and the final FREEZE schema, review this source,
then perform the phased qualification above. All implementation/qualification
defects discovered by execution must be corrected in new source-bound attempts.
The harness author has not run even a preparatory freeze against moving sources.
