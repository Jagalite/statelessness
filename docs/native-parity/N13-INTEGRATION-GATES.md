# N13 parent integration gates

## Follow-up candidate received

The source audit below pins the earlier work-in-progress snapshot. A separate
continuation has since returned source commit `67c1e3f` and handoff HEAD `eaf87d8`
on `t3/n13-parity-qualification`, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-229137a9`. It adds five 90-row
disposition ledgers, missing witnesses and a five-engine binder/Recorder/causal
evidence application. The old three-fixture driver retains its original scope.

The continuation reports independent static clearance. Its qualification jobs
were queued at handoff; this is **not** execution proof or parent acceptance.
Exact handles, source snapshots, compiler-only versus runtime wrappers and
remaining gates are in that worktree's
`validation/native-parity/derive-model-v2/HANDOFF.md`. In particular, the installed
job is `bc6dd9c046a3422e95649db12a4117ea`, Rust workspace execution is
`88dc1b05082642498013e2519d81f0d1`, and compiler rejection qualification is
`cfe860eae13e4c88942e4f7b8079f5a2`. Preserve those handles and the frozen tree.

Parent integration and exact-candidate requalification still remain. The
remaining generated-model/domain/inspection helpers and broad144 gates are not
closed by this declaration convenience work.

## Earlier static snapshot

Static parent review of child worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-b71b6b32`, inspected on 2026-10-20.
The child is still implementing and qualifying its changes. This document ran
no tests, builds or consumer processes and does not authorize closure or profile
promotion. Paths below refer to that child unless stated otherwise. A queued
job, authored assertion or independent static clearance is not execution proof.

## Evidence currently authored

The draft `docs/native-parity/derive-model-draft/cases.json` has 90 mixed-class
rows: 25 positive, 12 budget, 9 runtime error, 23 declaration negative, 12 model
binding, 5 diagnostics and 4 reader scripts. It still declares itself an
unexecuted draft. The inspected focused suites do not load that file or produce
a row-by-row result ledger. They supply important overlapping witnesses, not an
execution of all 90 rows. Native-only declaration conventions and Rust-only
syntax rejections must remain explicitly classified rather than silently counted
as identical runtime rows.

Concrete inspected authored sources:

- Rust: `crates/macro-qualification/tests/n13_derive_reference.rs`;
  `validation/native-parity/derive-model/rust/compile_fail.py` (22 independent
  source rejection cases with required compiler diagnostics).
- Python: `python/tests/test_codec_declarations.py` (11 named tests).
- TypeScript: `typescript/test/derive-model.test.mjs` (11 named tests).
- Go: `go/value_derive_test.go` (8 named tests).
- Swift: `swift/Tests/StatelessnessTests/DeriveCodecTests.swift` (6 named tests).
- Installed scope: `scripts/check-native-derive-consumers.py` and
  `examples/native-derive/{python,typescript,go,swift}`.

These counts identify source functions at review time, not a qualified test
count. They can change while the child remains active.

## Draft-to-source coverage map

| Draft family | Concrete overlapping authored assertions | Remaining row-level gate |
| --- | --- | --- |
| 25 positive | Rust `finite_independent_wire_goldens`: named/tuple/unit/empty/nested records, String widths 1/2/4, big-endian adapter, unit/tuple/named enums, sparse u16, maximum u8/u16/u32, generic array, encode-only/decode-only records. `length_only_field_needs_no_ordinary_direction` adds length-only fields. Native record/forms/enum/direction tests cover representative equivalents. | No 25-row native or Rust ledger. Rust Text16 and Text32 use `abc`, so they do not instantiate draft `length16-utf8` and `length32-bom` literally. Empty/u8-length vector rows, explicit record collection storage weight, and direction-only enum rows need explicit per-language disposition/assertion pointers rather than inference from builtin/vector tests. |
| 12 budget | Rust `exact_guard_and_direct_field_budgets`: work/depth for Record, ordinary Text, direct Text8, raw adapter, enum raw payload, raw Pair. Python `test_order_and_exact_budgets`, `test_length_only_and_adapter_routes`; TypeScript first two tests; Go `TestDerivedOrderedRecords`, `TestDerivedDirectionsAndRawAdapters`; Swift first two tests. | Map each draft byte/allocation/elements/work/depth underflow and exact boundary. Presence of a work/depth witness does not cover `direct-length-field-allocation-one-under`, `direct-length-field-elements-one-under`, `record-collection-weight-one-under`, or admission-before-field callbacks. Raw unit zero-value budget needs a concrete direct raw assertion; guarded unit is different. |
| 9 runtime error | Rust `raw_unknown_tag_can_be_swallowed_but_guarded_and_structural_errors_stick` and `raw_callback_error_priority_and_sticky_actual_write_failure`; native unknown tag, truncation/trailing, raw adapter and failed-writer assertions. | No row ledger for invalid bool, invalid UTF-8 through a derived length field, one-under record encoding cap, and u8 length overflow before prefix. The same builtin error in prior slices does not prove the derive field route/callback order. |
| 23 declaration negative | Rust compile-fail harness has 22 authored cases, including missing/duplicate/u8 overflow tags, unsupported tag width/record tag option, discriminant, skip/default, duplicate/conflicting/invalid field lengths, missing ordinary/length/adapter traits, zero/overflow/unknown/duplicate binder options and nonstruct target. Native suites check selected duplicates, unsupported declarations/directions and immutable captures. | Counts are not a bijection: harness adds separate missing encode/decode and length decode cases, while draft includes negative tag, u32 tag overflow and unknown type option not identically named in the 22-case table. Build an explicit mapping and retain non-runtime/static-native exclusions. Syntax that an explicit API cannot represent needs a real type/builder boundary proof, not a fixture-only adapter rejection. |
| 12 model binding | Rust `all_eight_hooks_gate_version_and_limits_are_fresh_decode_only`; Python `test_all_eight_version_gates_and_fresh_limits`; TypeScript similarly named test; Go `TestBoundModelCodecGatesAllHooks`; Swift `testBinderAllEightVersionGatesAndFreshLimits`. | Eight mismatches are authored in each language. Still map exact-limit success/failure, per-call fresh limit changes, encoding never invoking limits, and trailing bytes for both State/Input. Several native tests count provider calls but do not independently exercise all draft malformed/trailing input outcomes. |
| 5 diagnostics | Rust checks Pair variant None, selected unit label and schema contains Record. TypeScript/Swift inspect selected enum labels; other native enum tests inspect labels. | Literal selected named label, same tag/wire after label rename, nonenum None and Rust token spelling require explicit classification/mapping. Native schema labels intentionally need not equal Rust token spelling; do not invent that equality. |
| 4 reader scripts | Rust `raw_unknown_tag_can_be_swallowed_but_guarded_and_structural_errors_stick` authors raw recoverability and guarded sticky behavior. Go `TestDerivedEnumRawUnknownAndGuardedSticky`; TypeScript enum test checks raw unknown followed by another value. | Map all four complete scripts, including actual failed tag read and first structural error versus later callback error. A raw unknown-tag witness alone does not cover those sequences or exact callback counts. |

The table records authored witnesses and missing accounting. It is not a claim
that every unlisted row necessarily lacks an assertion somewhere in the repository.
Before closure, the child should provide precise source/assertion mappings for
any witnesses supplied by existing suites instead of requiring the parent to
infer them from a broad passing test count.

## Installed coverage is materially narrower

The actual driver hardcodes three fixtures:

- a named Point (`u32` + String),
- an enum unit case,
- an enum named/payload case.

It stages four native packages, builds installed consumers, launches four native
producers and 16 native-to-native fresh-process transcoding pairs, and compares
literal bytes. Its report explicitly says **no ModelCodec binder claim**. There
is no Rust producer/consumer in this driver and no binder/Recorder/evidence
command in these installed examples. Passing it would prove those three fixture
formats among four native packages only. It cannot close all 90 rows, five-engine
interchange, or binder integration. Keep its current honest scope label.

## Required additional integration scenario

Add a separate installed binder/evidence scenario rather than broadening the
three-fixture receipt after the fact. Use one genuine application State/Input
record or enum and a **custom encode-only Output with no decoder**. Bind the
actual model, preserve its existing metadata, generation/append checking and
private receiver behavior, and exercise preferred sink encoding through real
Recorder and EvidenceRecorder. The scenario must:

1. Produce bounded binary v1 and causal v2 artifacts with producer RunConfig and
   metadata; capture actual encoded outputs, failed property, phase evidence and
   provenance. Replay fresh outputs through the binder without output decoding.
2. Cross-read and replay in fresh installed processes among Rust plus all four
   native engines. Require actual model reducers/checkers/codec callbacks, exact
   expected reports and meaningful observations, not just decode/re-encode.
3. Retain producer config and codec identity, record pre/post callback counters,
   and demonstrate checked handoff does not repeat reduction or checks. Assert
   exact counts, not merely nonzero or lower bounds.
4. Include all eight version mismatch methods with a valid existing sink prefix
   and hostile/invalid value/input to prove the version check wins before
   value/sink/reader/limits work. Test exact default/custom decode admission,
   trailing and truncated input, changing fresh limits on consecutive decodes,
   and zero provider calls during every encoder path.
5. Keep negative cases separate from success artifacts; preserve coherent
   Recorder/evidence prefixes and diagnostic ownership where an actual capture
   callback fails. No fabricated output decoder may appear in helper glue.

Local source coverage is a useful start but not this scenario:

- Python `test_engine_recorder_and_causal_capture_encode_only_output` genuinely
  binds an output unit record without decoding, records/replays, uses Recorder
  and evidence, and checks no repeated step/check on evidence handoff. Its
  checks are in-process and do not cross engines or preserve/assert an exchanged
  producer configuration.
- TypeScript `encode-only model Output composes with record, rich replay,
  Recorder and evidence` uses an actual custom encoder-only descriptor. It
  asserts exact replay and capture existence but only `outputCalls >= 8`, not
  exact callback/config/phase receipts or cross-process consumption.
- Go `TestBoundModelCodecEngineComposition` binds an encoder-only output,
  records/replays, then checked Recorder and evidence replay. It lacks the
  installed cross-language artifact/count/config proof.
- Swift `testEncodeOnlyOutputThroughRecorderAndReplay` currently authors actual
  Recorder plus bounded rich replay and counts two observations. It does **not**
  construct an EvidenceRecorder in that test and has no cross-engine artifact
  path. Add actual causal evidence capture/replay, not only an audit trace.
- Rust binder reference proves eight method gates and fresh limits. It does not
  in that test construct Recorder/EvidenceRecorder or export an installed
  interoperable artifact. Add the corresponding concrete application side.

## Execution gates versus missing coverage

**Pending execution:** inspect terminal Kyoto results and effective commands for
already-authored native suites, Rust runtime reference, Rust compile-fail
harness, existing regression suites and the finite installed driver. Preserve
failed/superseded diagnostics and correlate final source hashes with the actual
execution interval. This review did not fetch queue state or treat archived
queued manifests as terminal evidence.

**Missing or not yet accounted:** a 90-row disposition/assertion matrix; complete
per-language witnesses for the uncovered subcases above; actual installed
five-engine binder/Recorder/evidence interchange with exact counts/config and
version/limits gates; Swift causal capture coverage; installed Rust participation.
These require new tests/apps or explicit existing assertion pointers, not merely
waiting for queued jobs to finish.

**Parent acceptance:** integrate child changes without disturbing the separate
frozen value-codec installed qualification; rereview final changed glue, run
qualification on the exact integrated candidate, and retain a separate N13
receipt. Do not claim broad144, profile promotion, generated-model macro parity
or retirement of any handwritten API from this finite convenience slice.

## Inspected file identities

Child files may continue changing; these hashes pin this static snapshot.

| Child-relative path | SHA-256 |
| --- | --- |
| `docs/native-parity/derive-model-draft/cases.json` | `4223152ca12925c906af52ea0ef90d922b222bd608c869070490c5bdafcaf7c0` |
| `scripts/check-native-derive-consumers.py` | `286dc672916e64632ecbdc8f4f04da140308eef31bbef7ee82362ed7776423bf` |
| `crates/macro-qualification/tests/n13_derive_reference.rs` | `b6acb73806172064d564a1d77a44682136715b6b0f280d71b71d61a6a045033a` |
| `validation/native-parity/derive-model/rust/compile_fail.py` | `2b480acd806881589137a346d4eacd6346472ab8796d7206342dfc698915a032` |
| `python/tests/test_codec_declarations.py` | `b2a8ef86056961e2f1bdf98559b8738b951118d459e9816c51570083f6d646d6` |
| `typescript/test/derive-model.test.mjs` | `cc8257978355f5df87c77d80b2b4be9915589de8d523578f2756cdefeb7287fb` |
| `go/value_derive_test.go` | `bdebbae2520b0ede128b94bbea3116acc541edd793031999d2ae3e8549ba299a` |
| `swift/Tests/StatelessnessTests/DeriveCodecTests.swift` | `55efb57d93e18fe50cbc6b6e847f41254e0b41fb964d4e49c057e00f6349af3f` |
