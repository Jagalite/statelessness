# N06 Python campaign implementation

Source implementation in the isolated `t3/native-campaign-runtime` checkout,
based on N05 `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. N05 final
qualification remains pending; its queued jobs are not restarted here.

## Coordination contract

Public APIs are `run_campaign(config, limits, factory, sink, *, process=None)` and
`run_guided_campaign(config, limits, guidance, factory, feedback_factory, sink,
*, process=None)` from `statelessness`. Config/report fields use Rust snake_case;
`JobReport.outcome` is `JobOutcome(kind, report=None, error=None)` with kinds
`completed`, `factory_error`, `model_error`, `panicked`. Campaign termination is a
snake_case string and infrastructure message is `CampaignReport.error`.

Configured workers1 executes on the caller. Configured workers>1 requires an
explicit `ProcessWorkers(input_transfer=InputTransfer(encode, decode))` policy.
`PickleInputTransfer()` is an explicit trusted-local-process opt-in. Factories,
feedback factories and input encoders must be top-level importable callables or
standard multiprocessing spawn-pickleable callable objects. Input decoders stay
caller-local and may be closures. Worker-bound closures are not
rewritten, cloudpickled, or guessed. Spawned workers construct models and feedback
locally; models/states/outputs/features never cross the process boundary. Only
report input values use the application transfer policy, separate from ModelCodec.
`CampaignCancellation()` is a spawn-context shared cooperative token and is the
supported multiworker cancellation source. Serial runs accept existing RunLimits
cancellation callables. The sink always runs serially on the original caller.

Private `_campaign_lifecycle.py` is the live scheduler law. Its test-only codec
and independently audited self-model live under `python/tests`; there is no public
queue resume or generic CampaignReport merge. N10 is separate.

Python errors: a factory-raised ModelError is FactoryError; other recoverable
factory/metadata exceptions become Panicked. Existing public fuzz/guided APIs
convert their ordinary callback exceptions to ModelError. A feedback-factory
ModelError is ModelError with metadata retained. Optional `campaign_close` hooks
provide deterministic cleanup; Python `__del__` exceptions are unraisable
and cannot be reinterpreted as caught Rust destructor unwinds. Process termination
is infrastructure lost work, not a synthetic job outcome. No abort/trap recovery,
hard memory bound, callback preemption or durable delivery is claimed.

Root owns cross-language goldens, integration, receipts and final promotion.
Focused qualification identity and queue state are recorded at the end of this
document; authored tests and queue admission do not establish an execution pass.

## Native callback and disposal policy

The existing N05/public search `_call` and feedback containment are unchanged:
ordinary Python callback Exceptions inside public fuzz/guided search become
ModelError. This is an explicit native policy, not Rust unwind equivalence.
`campaign_close()` is optional on model and feedback. It runs before job completion,
feedback before model, including failed runs. Every cleanup failure (including
ModelError) becomes Panicked and overrides the computed job outcome. Metadata is
retained once successfully captured. Python `__del__` remains outside catchable
cleanup; applications needing deterministic error reporting use campaign_close.

Complete is accepted before application input encoding and blocking send. Encode
failure loses the undelivered outcome and becomes WorkerError after drain/join;
no job report is fabricated. A received wire report is accounted before decoding.
After the first delivery error it is discarded without decode or sink calls.
A healthy receipt whose application decoder raises becomes CallbackError at the
delivery boundary, with known counters retained and delivered unchanged. Decoder
callbacks run on the sink's caller. That explicit Python transfer failure policy
extends Rust's infallible Send boundary; it is not a search ModelError. The original
sink/decoder error remains authoritative over later drain and worker failures.

## Review resolutions (source, execution pending)

- Root early finding 1: `_CleanupPanic` puts feedback and model campaign_close
  failures outside the runner's ModelError handler. Tests cover both ModelError
  cleanup failures, retained metadata, incomplete accounting and ordered cleanup.
- Root early finding 2: `_deliver` applies Receive before decode, returns on
  Discard, and treats healthy decode failure as CallbackError. Tests cover no
  decoder calls on drain, first-sink-error retention, and complete known counters
  on decoder failure. Only the encoder travels to spawned workers; decoder
  closures remain caller-local.
- `N06-CONTRACT-REVIEW.md` finding 5 / independent Python P1: the entire Pipe,
  Process constructor/start and Thread constructor/start sequence is inside one
  ownership-aware handler. It closes partial endpoints, compensates channel.active
  only when enrolled without a running bridge, records lost_work=false, and
  continues to drain/join existing workers. A process started without its bridge
  receives no job and is released by closing its request pipe, then joined.
  `test_later_pipe_process_and_thread_construction_failures_drain_rendezvous`
  injects each later constructor failure only after the first real worker has
  started, asserting its report still reaches the sink and all workers/bridges
  have exited. Spawn pickling failure also has a separate no-work test.

All listed tests are authored, not yet executed. No N05 source change is required
by these fixes. Existing error string and constructor-validation differences
remain native policy and are visible in public-reference rows.

## Public installed application entries

`examples/native-campaign/python/consumer.py` is a standalone installed-only
consumer (requires `-I -B`, a noneditable archive installation and a venv).

- `--demo` runs ordinary/guided applications with workers/jobs1/3, 2/1 and 2/3,
  buffer0, application-defined nonpickleable values and explicit input transfer.
- `--case ordinary-basic` (or any Rust public case ID) emits a single
  native-campaign-v1 row. `--all-reference` emits the independent Rust public
  fixture inventory in source order, retaining actual error strings/differences.
- `python/tests/qualify_campaign_install.py --driver-sha256 HASH --output /absolute/new/attempt` builds
  a wheel and source archive, rebuilds the latter, installs in separate fresh
  consumers and runs those entries. It retains input/package/installation hashes,
  exact commands and failed outputs. It does not submit Kyoto itself.
- Integration's separate `scripts/native-campaign-consumers/python/consumer.py`
  is harness-owned; this implementation does not edit that adapter or the root
  marker `docs/native-parity/N06-INTEGRATION.md`.

The input decoder is caller-local and may be a closure; only factories, feedback
factories and the input **encoder** must satisfy spawn transfer. No arbitrary
closure or canonical ModelCodec serialization is inferred. PickleInputTransfer
is an explicit opt-in for trusted local report values, never an artifact reader.

Qualification commands (submit only once per frozen attempt through Kyoto):

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 -B -m unittest discover -s python/tests -p 'test_campaign*.py' -v
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 -I -B python/tests/qualify_campaign_install.py --driver-sha256 "$N06_PYTHON_DRIVER_SHA256" --output /absolute/new/n06-python-installed
```

The unit command requires the frozen package `python/src` on PYTHONPATH. Root's
final aggregate still requires its own source freeze and approval marker; this
Python slice does not supply that marker. Queue admission is not execution.
The standalone driver hash must be selected and authenticated by root from the
reviewed source seal, then supplied through `N06_PYTHON_DRIVER_SHA256`; do not
compute an expected hash from the running candidate in the launch command.

## Final review follow-ups before focused freeze

- `N06-REVIEW-PYTHON-TYPESCRIPT-R1.md` R2: all `_message` extraction is guarded;
  exact-string args are copied, otherwise the constant `non-string exception`
  is used. No exception metaclass/name/formatter lookup is attempted. Serial
  and spawned hostile-metaclass sink tests retain CallbackError and join workers;
  a later genuine worker loss cannot replace that first sink diagnostic.
- T1: test outputs are `Opaque`, faithfully deepcopyable and explicitly
  nonpickleable. Successful process cases assert Completed, including exact
  failure witnesses transferred through application InputTransfer.
- T3: the upper-bound witness wraps the real `_Channel.put` and its actual
  Condition.wait (it does not copy scheduling logic). Before releasing the sink,
  both bridges must be blocked at send admission/receipt. Under that same lock,
  the test checks queue occupancy, waiting offers and the distinct outstanding
  IDs against buffer+workers+sink. The former short quiet interval is removed.
- Worker observation order: bridge failures retain their launch slot. After
  drain, each owned process is joined in launch order and emits at most one
  WorkerFailed event; nonzero exit status supersedes its accompanying pipe EOF.
  Tests causally release slot0 only after slot1's EOF has been observed, then
  assert events41,42 and latest-joined diagnostic42. A second scenario first
  receives a real job and raises a hostile sink exception, then observes both
  reversed worker exits while retaining the original CallbackError.
- `N06-REVIEW-GO-SWIFT-R1.md` false-loss pattern: Python already compares
  `core.lost_worker == ledger.lost_work`, and Finish admission uses independent
  claim/receipt/attempt/return sets plus genuine lost events. A new corruption
  witness sets core loss after a received ModelError: it detects both the false
  flag and the consequent premature Finish. The duplicate-Deliver BFS/fuzz/
  shrink/record/read/replay pipeline is retained.
- Focused runner integrity: every extracted entry is inventoried with lstat type,
  mode, file hash/size or symlink target, including directories and unexpected
  additions. Archive modes are checked against the seal; extraction intentionally
  maps regular files to 0444 and directories to 0755. Before/after manifests and
  failure receipts are preserved in finally, including launch/log failures.
  Its 900-second watchdog targets the private test process group; the later
  ownership limitation below narrows this source review's cleanup conclusion. This runner
  executes campaign tests only; baseline and fresh-package execution are separate.

No source changes to core.py/search.py/guided.py were made.

The last bounded source review found three test/runner issues, now corrected
before any queue submission: the channel-ready predicate is gated on the held
sink and rechecked under the real channel lock; final inventory is mandatory
once a before-manifest exists even if the source root disappears; and the runner
checks/cleans its owned process group on every exit path before final attestation.
A surviving descendant requires cleanup and fails qualification. None of these
source corrections is an executed result.

The last independent source review confirmed those three fixes against runtime
`2901518ab33f887864fb2f0b689fc2e588f889dc20755006f5a9c1942bc2c01c`,
runtime tests `4c05d49a54ebb2b15e0bdb12c74986eacfd996bbc863e50decad685cae4c2c5c`,
and focused runner `4545398d812c034dbe9ba0222af2e4272d5448f2763c733bf47498770feeb8c0`.
It found no additional concrete defect within that bounded source review.

The standalone installed consumer now includes the Rust author's 49th case,
`feedback-metadata-panic`, retaining Python's existing public guided ModelError
classification with present model metadata. Rust's Panicked expectation is not
rewritten. The inventory is 15 seed, 15 validation and 19 campaign cases; exact
comparison remains pending and native policy differences remain visible.

## Frozen focused handoff

One shared-default Kyoto job was submitted: `be50a84c471f4fb6908ce960e2a54fd7`.
At the original r1 handoff, submission status was queued, with no start time,
exit status or test output. This post-r1 source follow-up does not poll that job.
No retry or duplicate was submitted. Source authors have finished editing; no
watcher is running. The focused job executes 53 authored runtime/lifecycle tests,
including the final reviewed corrections, from a 29-file immutable input archive.

Attempt directory: `validation/native-parity/n06/python/focused-r1`.

- `source.json` SHA-256:
  `8f2c35e9b161d6fb6de2d74a3c59c617a3fd1c58cb80b1aacc8bcc15c5d94bbc`.
- `source.tar.gz` SHA-256:
  `c560e67520e662c342d012724d94167f592ab19f4f322bc0d8793898b5fea40a`.
- Runner SHA-256:
  `4545398d812c034dbe9ba0222af2e4272d5448f2763c733bf47498770feeb8c0`.
- `requested-command.json`, `submit.json`, `show-at-handoff.json` and
  `kyoto-at-handoff.log` preserve the exact request and observed queue state.
  The requested and effective executable commands are identical:

```sh
python3 -B /Users/jagatranvo/.t3/worktrees/stateless/t3-25250336/validation/native-parity/n06/python/focused-r1/run.py 8f2c35e9b161d6fb6de2d74a3c59c617a3fd1c58cb80b1aacc8bcc15c5d94bbc
```

The inner command is `python -B -m unittest discover -s python/tests -p
'test_campaign*.py' -v`, with PYTHONPATH set to the extracted package source.
Execution will preserve stdout/stderr, before/after lstat inventories and final
receipt beneath `focused-r1/execution`. The runner verifies its own hash, the
manifest argument and the archive before executing. Do not edit these queued
inputs or resubmit this identity.

Pending root steps: inspect this job's eventual execution receipt; run baseline
Python tests separately; build/run fresh wheel and sdist consumers; compare all
49 public rows and lifecycle cases against the independent Rust seal; and
complete final integration review. The standalone consumer's latest 49th-case
addition is source-only and is not executed by the focused unit job. N05 final
qualification and the explicit Swift N05 feedback provenance gap remain separate
root-owned gates. This handoff supplies no aggregate pass or promotion receipt.

## Post-r1 standalone qualifier corrections

The root's R2 review was checked against current source before editing:
`qualify_campaign_install.py` was
`a87c1c4d2b5592357ea6a284069247fbc7a89e9b94dcbeac3d139d6b74162e8a`,
and the standalone consumer was
`401993bf223e02051f0f813ac316cc27ffee42f6306b02eb18043d70e6cc2fd6`.
Q1–Q4 still applied. These corrections touch the standalone qualification path,
example and separate regression source, not production/runtime, lifecycle tests
or N05 foundations. Root committed its runtime/reference slices separately.

- **Q1:** The driver checks the full ordered 49-case inventory, rejecting missing,
  additional, duplicate, substituted or reordered IDs. The example now uses the
  exact shared operands `S = 9007199254740991` and overflow `jobs = 2049`,
  including S for the capped product and maximum-job-count cases. There is no
  clamping or output normalization. Native full-width host checks stay separate;
  the feedback metadata RuntimeError still becomes ModelError through public
  guided containment and remains a visible Rust comparison difference.
- **Q2:** Every launched command enters group cleanup on success, nonzero exit,
  timeout and interruption. `waitid(WNOWAIT)` retains the owned leader PID while
  the driver enumerates its process-group members and, when necessary, signals
  that group. It reaps the leader only after all signalling is finished; ECHILD
  refuses numeric-ID signalling. Group inspection and cleanup are bounded,
  recorded, and fail qualification if descendants require forced cleanup or
  remain. Primary command errors and raw stdout/stderr survive cleanup errors.
  With no earlier primary error, a first interruption during cleanup, log hashing,
  receipt writing or final observation retains its object and is re-raised after
  the remaining cleanup/captures, rather than continuing to another consumer.
  This driver explicitly requires POSIX waitid/WNOWAIT, no-follow file opens and
  `ps`; it does not claim Windows or escaped-process-group containment.
- **Q3:** Inventories include the root, every directory, regular file and
  unexpected entry with lstat type/mode. Regular files add size/SHA-256;
  symlinks and special entries are recorded and rejected without following or
  opening them. Failed scans retain partial entries and per-path errors.
  Traversal uses no-follow directory descriptors for ancestors and children;
  before/after descriptor and directory-entry checks detect replacement without
  traversing a substituted link. This also rejects symlink ancestors rather than
  resolving them away inside the inventory operation. Allowed absence requires
  a descriptor-relative FileNotFoundError; inaccessible or invalid ancestors
  produce failed observations. Parent-traversal spelling is rejected before
  lexical normalization, including for the executing driver's original path.
- **Q4:** Before observations are persisted when scopes are registered. A final
  pass independently captures every attempted source preparation, immutable
  snapshot, build source/output, archive, consumer and installed tree, even
  after copy/build/install errors or interruption. Mutable build/install scopes
  are distinguished from immutable execution inputs. Failure of one final scan
  does not skip the others or replace the original error. A failed final receipt
  write corrects both status and comparison to failed, then attempts to persist
  that correction without replacing the first exception. If correction fails,
  it attempts to rename the possibly flushed bytes to
  `qualification-unconfirmed.json`, removing the authoritative success path.

The executing-source follow-up requires an explicit caller-authenticated
`--driver-sha256`, records original `__file__` spelling and the executing regular
file's path/mode/size/hash before any command, and includes that file in final
immutable observations. The archived driver must match the same expected hash.
Original package source, pyproject/README/LICENSE, example, reference/author/
contract and optional sealed expectations also receive before/after captures.
Copying currently observed driver bytes is not presented as proof of loaded
interpreter bytes. File checks do not attest interpreter memory or hostile
concurrent replacement; root must launch its reviewed, independently selected
source identity. No authenticated current-source launch seal is created here.

`validation/native-parity/n06/python/post-r1/test_install_qualifier.py` is a
separate regression entry with 30 authored methods for these qualification
fixes, including driver mismatch, post-admission original-source drift and
symlink/parent-traversal rejection. Its later execution
must go through Kyoto; no test, build, installation or new job is run/submitted
by this source-only follow-up. Targeted independent review disposition and final
file hashes are recorded in `validation/native-parity/n06/python/post-r1/handoff.json`.
The final bounded qualifier review (`n06-python-qualifier-q1-q4-review-r4`) and
the complete 30-method regression review
(`n06-python-qualifier-regressions-review-r3`) found no actionable source defects.
They inspected qualifier SHA-256
`f72e2ff676254172e56d6abd99a9c5be152eb6965a82ba002700c282e52633ac`
and regression SHA-256
`928134e0c6205996c7d05df929c442af3e2880a289b4ee80a1069da1c6c5ab80`.
These are source-only dispositions. The comparison downgrade and rename after
a failed correction have no dedicated fault-test branches yet; their handling
is source reviewed, and all authored regressions still await execution.

**Frozen r1 limitation:** Root subsequently identified that r1's cleanup probes
and signals a numeric PGID after `process.wait()` has reaped its leader. Its
all-exit-path structure does not establish retained ownership against PGID
reuse. The frozen runner, archive, manifest and job
`be50a84c471f4fb6908ce960e2a54fd7` remain unchanged. An eventual successful r1
receipt does not qualify the new retained-leader cleanup, standalone qualifier,
example operands or regression source. These need a later root-authorized source
seal binding the then-current root HEAD and new execution evidence.

The owned `validation/native-parity/n06/python/.gitignore` excludes the r1
archive, extracted source and generated `.log` files from staging. They remain
losslessly on disk. Compact `source.json`, archive digest/path and receipts stay
available for the root's scoped commit; no original snapshot identity is rewritten.
