# N07 observation proof review

Static review on 2026-10-22 in the shared isolated worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base/HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. The candidate was still being edited;
the SHA-256 identities below delimit this review. This is not source-freeze,
compilation, runtime, installed-package, or whole-N07 qualification evidence.
Line references describe those examined revisions. Driver edits continued after
that snapshot; the late-edit note below identifies revisions requiring a new
review round rather than implicitly approving their changes.

Only this document was authored by the review agent. No implementation, fixture,
or other worktree was edited; no staging, commit, build, test, implementation
execution, Kyoto submission, push, or PR was performed. Applicable ancestor and
in-tree `AGENTS.md` searches found none. The required
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read. A second
read-only reviewer independently inspected the fixture/spec/public Rust consumer.

## Findings requiring disposition

### R1 — P1: compile-receipt reuse does not authenticate its command/artifact chain

`scripts/check-native-observation-consumers.py:508-529` (`load_compile`) checks
claimed scope/status/source/fixture fields and current executable hashes. It
does not require or validate the compile command entries, their successful exit,
raw stdout/stderr hashes, `finished.json`, referenced manifests, or Cargo's
`compiler-artifact` records. A receipt with copied source fields and substituted
binary rows can pass this admission without any retained compile command proof.
Deleting the original compile logs also does not invalidate admission. Runtime
test discovery helps reject trivial wrong executables but does not establish
that the admitted executables were built by the claimed source-bound command.

The producer at `scripts/qualify-native-observation.py:56-88` does collect real
Cargo artifact records from a fresh target directory. Reuse must validate that
chain: required commands/arguments/cwd, successful completion, lossless log
identities, source/input manifests, and one matching artifact record for each
required executable, including target name, package/source, test profile and
path. Add rejection controls for missing/corrupt command logs, fabricated artifact
rows, wrong compiler output and changed executables. The reviewed receipt tests
contain no `load_compile` tests. This is a provenance gate, not a claim of
cryptographic protection against an actor able to rewrite every receipt.

### R2 — P1: installed package inputs are not sealed across their build phase

`scripts/check-native-observation-consumers.py:460-475` verifies each staged copy
immediately. In `installed`, Python package sources are staged at line 543, Go at
568-569, TypeScript at 593/601, and Swift at 635-637, but the broad
`installed-runtime-tree` watch begins only at line 653, after all builds, packing
and installation. Root checks in `Attempt.run` do not check these external
copies. A compiler/build hook or concurrent write can change staged source or
consumer input before compilation; the subsequently installed result becomes
the first watched baseline and can pass while the committed root remains intact.

Record the mapping from every consumed staged source/configuration to its sealed
root identity, watch that immutable subset before the first build, and compare it
after each relevant command and on failure. Exclude only explicitly generated
outputs. Also watch generated consumer manifests/configurations and the browser
graph checker before consumption. The newer native path does watch its staged
source directories (`qualify-native-observation.py:164-166`); that improvement
does not close this installed-build interval. Add a staged-input mutation control.

### R3 — P2: the freeze has an exact-HEAD handoff restriction

`scripts/check-native-observation-consumers.py:348-349` rejects any HEAD other
than `--expected-commit`, and `finish` repeats that requirement at lines 434-435.
`load_compile:514` additionally requires identical expected commit IDs. Thus a
receipt-only follow-up commit is rejected even when every nonreceipt source byte,
type and mode equals the frozen commit. Changing `--expected-commit` to the
follow-up commit then rejects the earlier compile receipt.

**Current mandatory handoff consequence:** if this implementation is retained,
freeze all consumed code/docs, including this proof, in one commit; keep HEAD at
that commit throughout every dependent compile/runtime/installed attempt; leave
receipt additions uncommitted until the dependent attempts finish. After a
receipt-only commit, existing receipts remain historical evidence but cannot be
reused by this driver against the advanced HEAD. A later attempt needs a new
compatible compile attempt or a reviewed driver change. Do not advertise
receipt-only continuation as supported by the current seal.

The alternative is to retain a pinned source commit, independently record current
HEAD, and compare current HEAD's nonreceipt committed tree plus actual consumed
files against the pin before and after. Preserve all source/docs membership,
type/mode/content requirements; do not broaden the receipt exclusion into a
general documentation exemption. Test receipt-only acceptance and code/docs
change rejection. This document explicitly states the exact-HEAD alternative;
the parent must carry that restriction into the final continuation instructions
if no code change is made.

### R4 — P2: timeout log finalization can leave writing descendants alive

`scripts/check-native-observation-consumers.py:392-409` sends SIGTERM to the
process group but waits only for its leader. If the leader exits and a descendant
ignores SIGTERM, no SIGKILL follows. The descendant can keep the raw log descriptors
open and change files after their sizes/hashes and `finished.json` are recorded.
This breaks the claimed final raw-log identity for that timeout scenario.

Finalize logs after bounded cleanup of the entire owned process group, including
the case where its leader has exited. Retain initial error/timeout and cleanup
failure separately. `scripts/test_observation_receipts.py:212-218` covers one
sleeping process, not a surviving descendant; add that distinct control. Ordinary
nonzero exits already retain separate binary stdout/stderr without lossy decoding.

### R5 — resolved in source during review: zero-test receipt regression admission

The earlier qualifier relied solely on unittest's exit status, allowing missing
or renamed receipt tests to discover zero tests successfully. The final reviewed
revision checks a unique nonzero `Ran N tests` summary and records
`validator_unittest_count` (`scripts/qualify-native-observation.py:169-174`). This
closes that specific source finding; it has not been executed. Validator counts
remain separate from native observation cases. Native suite summaries retain
skip counts; a total that includes skipped cases is not an executed-pass count.

### R6 — P2: declaration-binding success is not an independent bypass control

`examples/native-observation/typescript/consumer.ts:43-50` routes both direct
hooks and bound schemas through the same encoder. Removing the binder assignment
would still satisfy `conformance/observation-v1.json:1677-1687`. Python's direct
hooks and adapter callbacks similarly share `FixtureModel.encode`
(`examples/native-observation/python/consumer.py:33-54,114`), and Swift initializes
working direct hooks before replacing the codec
(`examples/native-observation/swift/Sources/ObservationConsumer/main.swift:204-214`).
The inspected code does invoke the public binding APIs; the fixture cannot prove
that those wrappers were necessary to produce the bytes.

For the declaration branch, make original model codec hooks fail and make the
expected encoders reachable only through declaration adapters. A deliberate
binding bypass must fail qualification. Keep callback names/order and independent
literal bytes unchanged. Go's declaration branch constructs the actual
`BindModelCodec`/`OrderedRecord` path at `examples/native-observation/go/main.go:141-159`.

### R7 — P2: shared corpus misses record-overflow callback cutoffs

The zero-capacity case (`conformance/observation-v1.json:899`) reaches its error at
Debug; the swallowed formatter write case (line 1430) has no following payloads.
Neither detects eager extra codec calls after a record bound has already failed.

Add source-authored Trace cases for the ordinary stream `s`, sequence 1, accepted
one-output transition, zero checks, input 1/output 2/state 3. With a zero record
bound, no payload callback runs. With bound 97, the prefix through `input=hex:01`
fits exactly; writing the following output field fails, so callbacks are exactly
`["encode_input:1"]`. Both deliver empty bytes, report `record_limit`, and leave
the sink healthy. Follow with an admissible operation to prove recovery. These
expectations follow `src/observation.rs:391,431-447`; no execution generated them.

### R8 — P3: declare the native short-write boundary

`spec/observation-v1.md:64-65,85-91` specifies injected failures but not successful
short writes. Rust retries short/interrupted writes
(`tests/observation.rs:600-627`); Go deliberately treats a short nil-error write
as `io.ErrShortWrite` (`go/observation.go:297-309`), consistent with Go's writer
contract. State this native distinction explicitly. Shared failing-prefix fixtures
do not establish complete cross-language writer behavior equivalence.

## Literal fixture and public API proof

No incorrect existing expected record bytes or callback ordering was found in the
34-case source inspection. That is a static conclusion about the listed corpus
hash, not a report that any consumer emitted those bytes. The JSON holds literal
`record_hex` and explanatory `record_text`; the validator checks their equivalence,
ordered arrays, scalar types and exact result fields without executing an engine.
Its negative controls mutate copies and remain validator controls, not extra
language cases. Their own reference value is the independently supplied fixture.

Representative independent derivations:

- `trace-payload-order` emits Debug severity while configured Trace adds the
  passed check and calls input 1, outputs 2/255, then post-state 3. Before-state
  is unused. Lower configured levels invoke no codec (`src/observation.rs:376-447`).
- Failed checks win over skipped checks regardless of order. Nonpassed details
  retain supplied order. Only configured Trace prints passed details
  (`src/observation.rs:357-367,461-468`). Rejected/ignored alone stay Debug.
- Explicit Trace checkpoints include state under any non-None snapshot policy;
  transition Every(n) uses absolute u64 divisibility. Max-u64 then sequence 1 is
  legal diagnostic input, not continuity evidence (`src/observation.rs:152-158,417-421`).
- The `é` message record is 57 UTF-8 bytes including LF; its ending is
  `c3 a9 22 0a`. Bound 56 rejects that record but admits the following ASCII `a`
  record. The literal fixture hex retains these byte differences and final LF
  (`conformance/observation-v1.json:841-895`).
- Input/output/state codec errors stop at the first failing callback, discard
  that record, and permit later recovery. A sink failure exposes only its delivered
  prefix and permanently poisons health, including after switching Off. Swallowed
  record writes remain failed because the final LF checks the sticky bound.
- The declaration case's codes 17/51/34 produce `hex:11`, `hex:33`, `hex:22`, with
  callbacks input/output/state in that order. This verifies bytes but needs R6
  to discriminate the binding path.

`validation/corpus/src/bin/native-observation.rs` imports and calls the actual
public `Observer`, `TextObserver`, `Event`, `EncodedPayloads`, `NoPayloads`,
`PayloadFormat`, `EncodeBuffer`, and `ModelCodec`. It directly supplies transition
values; initial-state/reducer/check/decode/legacy-encode hooks panic if invoked.
The `observe` path does not read `expected`; only `self-test` compares against it.
Health is probed through public Off observation, with writer/formatter call-count
checks, and extraction is checked for no implicit flush. Its small error-stage
adapter does not implement the observer's formatting algorithm.

The native text contract is explicitly a finite Rust Debug/common-printable-UTF-8
profile (`spec/observation-v1.md:93-102`). It does not freeze arbitrary Unicode
Debug spellings or future aggregate layouts. Custom payload bytes are not escaped
or sanitized by TextObserver. Additional corpus opportunities remain for swallowed
codec-buffer overflow, legacy-only codecs, and declaration version-gate recovery;
language-local tests of such paths do not turn them into shared fixture coverage.

## Existing authority and retained-suffix gate

The historical `CHECKED-RESULTS-AUDIT.md` missing-native-API statements are stale
for this base. Current Python `checked.py`/`monitor.py`, Go
`checked_results.go`/`recorder.go`, TypeScript `checked.ts`/`runtime_recorder.ts`,
and Swift `CheckedResults.swift`/`Recorder.swift` already provide checked results,
periodic policies and bounded Recorder handoff. This review requests no replacement.
Rust `CheckedTurn::observation` remains diagnostic; private checked context and
coverage plus Recorder's metadata/continuity/sequence validation remain authority.

`src/monitor.rs:327-345` rejects incomplete coverage and wrong absolute sequence;
the checked preparation path reuses the complete check batch and its phase
boundary. Eviction promotes only the evicted state-check prefix. The inspected
monitor tests cover retained checkpoint replay, small capacities, variable phase
boundaries, failed candidate atomicity and frozen-prefix preservation. Observation
integration tests supply already executed transitions/checks and compare behavior
across logging levels. These are source-presence findings only.

**Open N07 completion gate:** `docs/NATIVE-PARITY-PLAN.md:165-167` requires
fresh-process retained-suffix replay. The new installed observation driver
explicitly leaves it open (`check-native-observation-consumers.py:667-670`). The
older checked driver inspects ring replay results at
`scripts/check-native-checked-consumers.py:219-225` inside producer receipts;
its fresh-process pairs at lines 246-259 consume the separate full witness.
Those pairs cannot be relabeled fresh-process ring-suffix proof. The Python long
session at `python/tests/test_observation_recorder_integration.py:209-270` also
replays the retained suffix in process. Reuse requires exporting the actual
post-eviction checkpoint/steps/configuration and replaying that artifact in a new
installed process, with independent expected state-check prefix, absolute origin,
evicted counts, outputs/checks and first-failure identity. It must exclude evicted
history from its replay claim.

## Identity, failure retention and execution accounting

The reviewed seal inventories tracked/untracked paths and nonexempt ignored input,
including ignored Rust source descendants consumed by `build.rs`. It compares
regular bytes, symlink text, mode and membership against a committed tree before
and after commands. It excludes only this observation receipt family plus named
ignored build products. Other historical receipt directories and source/docs
remain in the committed source comparison. Staging rejects symlink source files.
Git index blob/stage identities are not part of its manifest: working bytes are
compared directly to the selected commit. Parent-owned staged content therefore
still needs exact commit review before the freeze.

Remaining identity boundaries must not be described as a fully hermetic build:
`identity`/`tree_identity` hash symlink text without recursively sealing external
targets; Rust compiles in the source checkout rather than through `stage`'s
symlink rejection. No committed symlink was present in the inspected HEAD.
If consumed symlinks are introduced, reject them or seal their resolved input
closure and add a target-mutation control. Ignored caches excluded from the root
manifest must not become unrecorded execution input. The TypeScript compiler tree
is separately watched and its version checked against the lock; this is not an
integrity verification of the lock's package archive. Python's build backend is
installed with `setuptools>=77`/`wheel` and inventoried by version, but its builder
site-package bytes are not watched. Rust's external registry dependencies are
locked/offline but their consumed source trees are not separately hashed here.

Direct executable link/resolved-file identities are collected before/after.
Recent driver edits also watch Go's tool directory and xcrun-resolved Swift tools;
Rust resolves compiler tools through rustup and watches existing Cargo configs.
Rustup resolution/version commands run in the temporary work directory while
Cargo builds run in the checkout, so a directory-specific toolchain override can
select a different compiler; resolve from the actual build cwd or invoke the
resolved tools explicitly before claiming that exact compiler identity.

Command logs use exclusive binary stdout/stderr files and retain nonzero exits,
invalid UTF-8, NULs and already written timeout bytes. Started/finished command
records and attempt directories are not overwritten. The current regression file
is present and covers these basic cases; R1/R2/R4 remain unproved. Retain failed
and superseded attempts even if later targeted attempts pass.

All four qualification scopes must be launched by the parent inside Kyoto;
subcommands inherit that job. The scripts themselves do not enforce a scheduler
token. Record Kyoto job ID, requested/effective outer command and policy alongside
the inner command receipts. A queued job has no execution evidence. `rust-compile`
uses `cargo test --no-run` and a separate consumer build, explicitly records
`runtime_executed: false`, and proves no Rust test/consumer ran. `rust-runtime`
discovers and runs six exact integration binaries, checks unfiltered counts, and
separately invokes the public consumer; it excludes workspace/doctest and
macro-qualification coverage. Native skips, executed passes, five-language fixture
cases and ten per-language validator controls are different accounting units.
This review supplies no result for any of them.

## Examined identities

SHA-256 of source read for this review; later edits require renewed review of the
changed files. The driver/consumer changed during inspection; the final listed
driver revisions were reread before this report. The independent fixture reviewer
also reread its intervening Rust-consumer revision. No frozen integrated commit
or runtime artifact identity exists for this review.

```text
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
772b3e871abd564d1be221a7b07af3536d1756e8a11bbd7a3ee55caa843a53d8  conformance/observation-v1.json
da04cfbaa4a268a9d5f7857a5c32b833230b5325b3777a3404d6065c6939d8c7  spec/observation-v1.md
49ce24f15beb064eeee617cbb8e4a3d402ed4afa448bb67f4a87b8773f359393  validation/corpus/src/bin/native-observation.rs
49ba777de7292480cc3b8e7b0bfc84e68f20b7e07dbe6bf330f81742c28cba5c  scripts/check-native-observation-consumers.py
6152830ac820a66c71169472744b32c7640b7320278119394bc9023e1f817a37  scripts/qualify-native-observation.py
1688fb1b9550d63f7db3c6498d6f0f38dbeb26a0e073836cedb034f34b919254  scripts/test_observation_receipts.py
efeda2e83efc5dadfb897d5359654c171f92efae0962fca5f86e77a313e97778  examples/native-observation/python/consumer.py
d9d819b529b97cafd21082ebe8084d15927a6ecc36df30dcab1aa0fac3622f2d  examples/native-observation/go/main.go
5fbf2e4a7351f7025d9f34d60f2f0156ac60545d46f331c3788ad41bbd08d78d  examples/native-observation/typescript/consumer.ts
b4116a87bd5bcd2d06058c33e38ff735d549cfe37baeb89ca402b828cd91cf70  examples/native-observation/swift/Sources/ObservationConsumer/main.swift
```

Earlier examined driver hashes were `df8cb390418018b963cd0c8602850687ca0b94663f804aae574e3d40f65016d6`
and `d916f2e15984ddc0442b51599ee0038c6759ad87d31c8613e56125d3510d646d`,
followed by qualifier `edefc1f2c83f3df53d9442ed94cf94f3445fa460796342093323c8250f7dfa9f`.
The earlier spec hash was `2dad672cf08714d861e9e92d5175b382c9fc09958ce9ee27395ff3274e4bde85`.
Earlier Rust consumer hashes were `960aece595d445523467c687f7177c93a181e32cf0e07f2b194b7d7a9b638415`
and `704488f9a3560c22ba12e6e4dc7c36e3eedf4c189e09c177a98cbadd5f98c8ea`.
These identify superseded inspection snapshots, not qualification receipts.

Late-edit notice: after the reviewed snapshot, the consumer driver was observed
at `0caa405bf3ded4186595543215e2e68d9d8e65f9621a31005ae63b3ef45b9cc2`
and qualifier at `b70a5180c27c26a263ff541e63d488b873595e40e5fcadb178a4418912d07be2`.
Their changed process-handling paths were not given a complete second review.
Reconcile R1-R8 against the parent's final frozen source, preserving this
snapshot's findings and any later source-only resolutions separately.
