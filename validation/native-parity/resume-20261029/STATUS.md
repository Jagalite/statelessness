# Service failure recovery

Base: 7c20b6efde4995b123d782ab3fb7f2a5595a8850, with local edits.

Corrected Python watch origin spelling and preserved typed inspection errors across custom adapter calls. Unknown adapter errors are normalized without formatting host exceptions. Swift candidate test now supplies the decoder required by canonical-byte selection and uses XCTUnwrap instead of crashing on missing tokens.

Kyoto Python job 53b99bc44e7f4f5f9dd4748db7295a52 succeeded: 611 tests, 12 skipped optional ABI tests. This is local source execution, not installed-consumer qualification.

Swift protocol job c565375c73b54bc49fd1de5ba678ac75 failed during compilation when swift-frontend crashed in campaignSpawnRetained; no tests executed. Bounded Swift build job 0ea217b245f94c348dcb6bba74b73e4c is running; its outcome is not yet known. Focused Python inspection and service job 50b9fdcf12c34aa990ebad3fd11cf29f passed 15 tests, including typed and hostile exception handling.

Earlier failed jobs and logs remain intact. Full parity and N12 remain open; see ../n12-services-20261029/HANDOFF.md for remaining implementation and qualification gates.

## 2026-10-30 continuation

Go `ConfigureSubscriptions` is a candidate atomic replacement operation. Kyoto
job `9f7203fcfe0846bc93ef1f29c5560bde` passed the focused producer-boundary
checks and `a2c67a808a394f52b70e8a5943b62ba2` passed the Go debugger suite.
This does not provide the missing protocol attachment. Go currently schedules
acknowledgement activation at the effective observation boundary, while the Rust
hub records an acknowledgement immediately and rejects turns before that boundary;
this distinction requires reconciliation before claiming direct-wire parity.

TypeScript build job `24fcceb25c54470cac0e7b8c0fb738c5` succeeded after fixes
to the composition and network sources. Full suite job
`74d844ae258847eaa4d7078601257a57` was still running at this checkpoint.
Swift's handle-conversion candidate reproduced the compiler crash and was removed.
The separate preconcurrency-import candidate is under build job
`d2a090d30c704d00bd1b17f5aaf44851`; it is not yet a compiler or test pass.

Go acknowledgement candidate now installs the acknowledged revision immediately,
rejects observations before its effective boundary, and rejects acknowledgements
at or before an observed boundary. Kyoto job `fa2417af0e7743f094e60706ef7f28ad`
passed the Go debugger suite including the new boundary test. This does not yet
address Go producer registration, scoped turns, or attached protocol coverage.

TypeScript full suite job `74d844ae258847eaa4d7078601257a57` failed: 1272 of
1273 tests passed. The failure used `state` for an initial failure where the
current engine emits `initial_state`. The fixture was corrected; targeted job
`30230d0e7f0e4bc8bb1b043bb913572e` remains pending and the full suite needs
rerun after it passes. The failed suite receipt remains unchanged.

Swift preconcurrency-import candidate `d2a090d30c704d00bd1b17f5aaf44851`
failed with the same `SendNonSendable` compiler crash and was reverted; no Swift
tests ran. Go explicit producer registration and detached status passed debugger
suite job `5325b32d561c405fa5d50c34c208e833`; implicit registration through the
legacy acknowledgement API remains a parity difference. Python callback-error
normalization and focused service tests passed job `5454c2ac6342462daed7b604b6a7f487`
(17 tests). TypeScript full suite job `d489ead9cee5461293ce7d8510b72a85` was
still running when this note was written.

Go debugger suite job `b2fe46a0d313454191b27a6b22e74886` passed after
correcting retained-generation eligibility. TypeScript full suite job
`d489ead9cee5461293ce7d8510b72a85` passed 1273 tests following the fixture fix.
The later TypeScript non-destructive diagnostic preview is a new source change;
its build job `4aba3115da3346b091584857ad369603` is pending. Swift language-mode
5 job `251034f0a70c4683a9b889180d8fe6ed` is diagnostic only and cannot qualify
Swift 6 behavior.

Go debugger suite job `8881d9a088d94257a84f969427da96a1` passed with
non-destructive diagnostic queue preview and commit primitives. Those primitives
are not yet connected to protocol commands. TypeScript build job
`4aba3115da3346b091584857ad369603` and targeted diagnostic test job
`94474ef4791d4e54a8e1ae6c67391399` passed; the latter ran 12 tests, including
owned non-destructive preview. Swift 5 diagnostic build job
`251034f0a70c4683a9b889180d8fe6ed` failed with a pthread C function-pointer
signature error. It was not a Swift 6 qualification attempt.

The platform-specific Swift callback signature compiled in normal Swift 6 build
job `1f4e077fa76b442d8299f8b58c9e16e9`. Full local Swift suite job
`6a4f4c08f2964f00b11a781351c308f5` succeeded: 383 tests, 0 failures. This is
local macOS evidence, not installed-consumer or multi-platform qualification.
The TypeScript probe attachment direct test job `bca351b8bf144bc38dc59293ad5b6326`
failed because active hub ownership was classified as invalid input. That candidate
was corrected and build job `27524d4ac1774f7abc9b3e21dd7f90e9` is pending.

Go attachment tests job `0b58d6e075ef47ffb86a0fb5d42d36aa` passed after
pre-clone profile size checks. Go profile status/configuration is partial: event
polling, complete sink health fields and full direct-wire comparison remain open.
Python probe configuration now truncates the pending-producer list to fit the
response without acknowledging producers; focused job
`df2a3b57dc404887a0dbb284e60c9be3` passed 9 tests. TypeScript full suite job
`d5db745ccb4e41949533f2ee53119bcb` passed 1276 tests for the latest probe
attachment source at that checkpoint. Installed and platform gates remain open.

Go diagnostic capture and acknowledgement now require explicit producer
registration; the earlier implicit-registration candidate was removed. Kyoto job
`1583959d996d4c77a15699853ae01d9a` passed the debugger suite including unknown
producer and acknowledgement-boundary cases. The earlier job
`b97552c448a34aaaa7d3dcaf553c249a` failed because its fixture acknowledged an
already observed boundary; that receipt remains intact. Protocol event polling
and installed cross-engine comparison are still missing.

Go diagnostic projection failures now retain observed schema and baseline metadata
and reset the following baseline to `InspectionIncomplete`. The direct failure
and recovery case passed as part of debugger suite job
`c9331ce5da6843d8872e2423d88c88fb`. Earlier job
`7ea2f084070b49cbaef459549ceb3034` failed to compile because of a missing import;
that receipt remains preserved. Unsupported or incomplete event forms still
remain unconsumed, and scoped-turn/installed direct-wire qualification is open.

Go diagnostic redaction is now retained as an incomplete projected node in the
diagnostic-only path; ordinary watch admission remains unchanged. The Go suite
`3211f8e23179482bb75e030d610211d6` and direct redaction/recovery test
`336dc197d49c4b1ea0940804512eb718` passed. Go scoped turns remain missing:
current Probe/Marker calls publish immediately, whereas the reference stages
records until turn completion. Explicit host turn-completeness metadata is not a
substitute for that scoped ownership and staging behavior.

Subsequently, Go gained an explicit scoped turn which stages diagnostic records
until finish and records whether the turn completed. The direct protocol case
passed Kyoto job `aee4f91d1dcf4d5ba68a73b29fe11b4c`. Separate turn record and
byte budgets now bound staging independently of sink queues; the full Go
debugger suite, including overflow and active-scope cases, passed Kyoto job
`6835ea25e0564f06b8bf292e06ea23f6`. This is local Go evidence: complete event
forms, sink health parity, installed cross-engine comparisons, and the broader
plan's qualification gates remain open.

Go sink health now includes inspection failures observed by the producer-local
projection registry, including in the attached protocol status. The failure and
recovery assertions passed in the full Go debugger suite through Kyoto job
`2802aa705b2b4c42bc7e5d3a50cd0b49`. Other reference sink health counters remain
to be implemented and checked against the reference semantics.

Go protocol sink status now reports measured captured and sampled counts.
Captured advances on successful publication to the sink, rather than during
turn staging; sampling is tracked separately for probes and markers. The full
Go debugger suite passed Kyoto job `b1d24e0e670a42569cc586df975db3d9`, with
direct coverage of marker sampling and staged publication. Other health fields
and installed cross-engine qualification remain open.

Go diagnostic projection failures now respect trigger filtering and sampling:
failed equality or threshold projections are filtered, while eligible changed
or every failures are sampled. Protocol status reports filtered and truncated
counts for the implemented event paths. The full debugger suite passed Kyoto
job `c8d5afd21e7f4bd0a0a6ff88d34a9cfb`, including a direct two-subscription
failure/filter/sampling health check. Complete reference health behavior and
cross-engine qualification remain open.

Go failed probe events with no available display schema now remain pollable;
the protocol omits schema fields while preserving the unavailable payload and
unknown-change evidence. The full Go debugger suite passed Kyoto job
`9de682baf11440269f3c9b77ee3bf282`, including a nil-adapter failure and queue
consumption case. This does not establish cross-engine qualification.

Go diagnostic baseline eviction now preserves the current event's trigger and
baseline reason, records eviction health, and marks the next baseline as
evicted. The full Go debugger suite passed Kyoto job
`289ebea527034dd6b1e52271ba89d0df`, including a constrained baseline case.
Follow-up full debugger suite job `b99ac083cba94e6eb1d271df955c1f46` also
passed an equality-trigger case showing eviction does not force delivery.

Parallel implementation checkpoint: Go status now advertises attached probe
capabilities and distinguishes metric queries from telemetry health; Kyoto
debugger suite `d06d9aaa4a1c4f44b17d8e16c12c0e9d` passed. Swift has a candidate
hub-backed probe protocol host; focused Kyoto job
`11e4cf38995d46cf8bfa582b32f0378b` passed four attachment tests, including
bounded profiles and pending-producer truncation. An earlier full Swift attempt
`4453f1fc8ad242f4bbb739005e1fe455` failed to compile before its initializer
fix; final-source full suite remains open. TypeScript diagnostic schema and
context ownership improvements passed the full suite at an intermediate source
checkpoint `418b642131e54e73985f36fb5849f23a` (1280 tests); later selector and
context edits passed focused job `7ed8beda8ae04a58a50f45c437612fae` (37 tests).
Python debugger job `5352a72e4fe44e1387835260c87ad59d` passed 144 tests after
schema and integer-encoding changes. The Python `python-int` wire tag remains
an explicit native extension requiring cross-engine reconciliation.

Later focused coding validation: TypeScript now validates runtime selectors and
counts diagnostic site limits in UTF-8 bytes; Kyoto build
`0e5d2751ab984af9ad2b52f5183a7610` and focused test job
`8fb188c859274856a2feac5415b3d904` passed 38 tests. This source is newer than
the full TypeScript checkpoint above. Go observer and Swift watch attachments
are being implemented separately; their status must be checked before claims.

Later coding checkpoint: Go context and selector labels now enforce bounded
valid UTF-8 and diagnostic origins; focused Kyoto job
`b7282de251cf42b599bcbf70c0238e53` passed. Go effect detail and timeline
attachments now use retained lifecycle evidence; the combined full Go module
suite `ee24c5ed305f476382aa9095273e43ae` passed after campaign tests were
adapted to unsigned count fields without discarding boundary controls. A
Go-specific `ready-removed` event remains unsupported on reference timeline wire.
Python metric response ordering and invalid-offset classification passed
focused Kyoto job `6849d0e11a16402fab7705c8031aa9eb` (13 tests). TypeScript
sink admission bounds and atomic rejection passed focused job
`d9bf5eb3884e45f2bed4ee5288199451` (39 tests).

Swift observer-backed protocol host now implements metric catalogue,
snapshot/window, effect details/timeline and telemetry health, including host
composition with probes. Focused Kyoto job `46e94f7391de440d9290c9e900500fb2`
passed six tests. Remaining coding gaps include shared metric-handle allocation,
missing effect origins and lifecycle wire reconciliation. The final-source full
Swift suite and installed comparisons have not been run.

Swift metric snapshots and windows now share the protocol adapter's handle
allocator, preflight before consuming a handle, and retire captures on
disconnect. Kyoto focused telemetry job `ee93180af967400c8e4d65591533cfa3`
passed seven tests. Swift `readyRemoved` already avoided clock reads and event
emission; that behavior passed regression job `fbb5044f000443c9a6154f36a90b2e08`.
Go now matches that lifecycle behavior, and its debugger suite including queue
metadata ownership passed job `97d0ff2edc9b4c669951bcf0b0c0c9d7`. Python
telemetry ordering and pause handling passed debugger job
`5cc7d720549d4bb2b2f199b484394e75` (146 tests). TypeScript diagnostic host
admission reentry and authorization tests passed focused job
`6716e6fd78cf4205b5b4b220cddb5960` (40 tests). These are local source checks;
the missing Swift effect origin representation and broad qualification remain.

Latest coding checkpoint: Go inspection nodes now serialize page and child
fields in reference order; the full debugger suite passed Kyoto job
`abdaacc903594780a7801a1dd7ab4286`, including the disabled-detail revision fix.
Python custom inspector values enforce max_value_bytes; debugger job
`1b921be10c044be4b9c64e2c9e1a11b3` passed 157 tests. TypeScript network
actions are owned before child callbacks; focused job
`e444091fa8b94d8fbc478ad778cdfd2c` passed 78 tests. Swift invalidates enabled
watch baselines across filtered or sampled observation gaps; focused job
`2f3dc1630dc74ca5afe7851d33c85916` passed 14 tests. Full final-source and
cross-engine qualification remain open.

Latest coding checkpoint: Go watch wire paths now normalize index and typed map
segments to their native representation (focused Kyoto job
`137789b37f4549189901a86ba65b4bee`); Go slow timing completeness and pending
age behavior passed debugger suite `1aad8c8a9ece45f4b9952676ce35e8c9`.
Python response quotas count result pairs and passed 148 debugger tests in
`046c06ea3e224283ac33213eedd7dc81`. Swift explicit transition-zero effect
origins passed eight telemetry tests in `9e8afd9cc04d433db5679865c9bf8dd3`.
TypeScript diagnostic path validation passed 42 focused tests in
`e79e9c741d304d1d87f89d10596b7f02`. Host-omitted Swift origins and Python's
unbounded integer wire extension still require explicit compatibility decisions.

Next coding checkpoint: Go watch acknowledgements now retain optional metadata
fields individually when the response budget allows; the Go debugger suite
`0a390b0d5b434315998f37f62a7570e5` passed. Earlier candidate jobs
`bbe8858733fc479fa4b5ba8c423d5486` and
`5b51f984b039444a88fd9d5f7b31f8a4` failed because the new test expected an
untruncated value under a deliberately small inspection budget; the corrected
test verifies the observed truncated type. Go slow-timing provenance and
bounded result tests passed debugger suite `f259481905a34ef7bf41442c859632ee`.
Python partial watch acknowledgement passed 13 focused tests in
`840123620e85432e9d1e39cb0353a2dd`. TypeScript probe field ordering and sink
ownership passed 43 focused tests in `aa032373a1d9498b9d5ba001bf0f231e`.
Swift native bounded protocol event streaming passed seven focused tests in
`3fe399214e5b48e1b4b5b8a69220cf9d`. Installed cross-engine evidence remains open.

Latest implementation work: Go now has a bounded native protocol event stream
with preflighted polling, drop accounting and reconnect behavior; full debugger
suite `7a1a9ad5795344289b7076b8a1691a38` passed. Go effect lifecycle clock,
abandonment and pause controls passed focused job `14a31dbef2cf4264bce097618de77e4e`.
An overlapping intermediate debugger run `19421f58652f4e17adcfb17eb57688aa`
failed on the old zero-page events fixture; the fixture now uses the reference's
nonzero page requirement and the later full debugger run passed. Swift pair and
network input iteration is lazy; new and existing stateful jobs
`5ba8c13b3c1b4243a061f6b9818c7c1c` and `4bfc47b7e10743daa198bf4ab5ddd819`
passed. TypeScript paired-oracle ownership and strict equality controls passed
73 focused tests in `be2df81522784b7a8b4455dfb1a87d57`; Python producer ordering
and eviction passed 16 focused tests in `9f2be66046764f37bccc999388fa3d76`.
These local checks do not establish installed cross-engine qualification.

Latest coding checkpoint: Go pair/network inputs are lazy and preserve detached
state and queue ordering; full module suite `b2448eb68b2d4777bdc971d6afb0acee`
passed after focused job `84e84b19baa14c2281c0b6d8572cccc9`. Go watch failure
trigger/sampling and event-order coverage passed debugger job
`9d9c7a870bb34fcaa0eadc48e73127fd` and focused job
`f60b526293dd4e8dbad146ee8b4cdcc5`. Python watch failure/sampling/recovery
passed 152 debugger tests in `b96dac9129b54571baa4a462eb0b3c45`; earlier
`c792713930824935aaf580385937bb02` failed on a stale snapshot assertion, fixed
to the actual previous failure boundary. TypeScript stateful codec, ownership and
callback checks passed 75 tests in `b8e8e18506034f33986fe15791a29ca8`. Swift
added an initial assurance observer; focused job `cb7f146a827942c2bcded4fa1114e774`
passed four tests. Its run metadata and termination remain caller supplied;
campaign/guided integration and report merging are incomplete, so N10 is open.

Subsequent N10 work adds Swift guided assurance and structured report/manifest
JSON export; focused Kyoto job `3ba77690f0b94b79950a2168366e561c` passed
seven tests. Campaign integration, merged-report interchange and independent
wire comparison remain open. Python Domain.product now bounds actual input
iteration before mapper calls; focused job `ea2adc49eb3a49098f57016028d67185`
passed two tests. TypeScript lazy enumeration cleanup passed 54 search tests
in `6f203e5e9f774b24a3c873d96ca4c709`. Go metric merge now accounts for owned
catalog/schema bytes, and reconnect releases captured metric snapshots; full
debugger suite `05d53955851144d69109606e24892468` passed. These Go byte budgets
remain implementation-specific, and cross-engine qualification is open.

Swift ordinary campaign assurance now retains per-job evidence before delivery,
checks identities and preserves missing/delivery/teardown failures; Kyoto job
`735788c21b3f45a5afed8dcc07bc2910` passed eight focused tests. Guided campaign
assurance remains open. Python deferred watch loss markers passed 16 tests in
`5ca83f579096443daa37c52f2c82c571`. TypeScript shrinking iterator cleanup passed
55 focused tests in `541727bb88e44cdba1b84a6416e7c573`. Go metric history now
charges each retained snapshot separately; staged copies own the cost slice and
observer attachment enforces protocol byte limits. The prior overlapping job
`3ee372c32204435fb34ca6cf24eed8a3` failed because its protocol fixture exceeded
those limits; the fixture was made admissible while preserving the historical
window control. Focused job `6eb9bb74162148ee8043b5e0539c03a4` and full Go
debugger suite `9dbb2ef9800542628e6e728acf9fae1d` passed afterward.

Latest coding: Swift guided-campaign assurance retains actual per-job feedback
identity and partial failures; focused Kyoto job `2d8a3f574bfe45abba3127457b730c11`
passed eight tests. Python incomplete-container and reported page-count handling
passed 156 debugger tests in `ecfe0e4c277b40149d2d567ac4025c64`. TypeScript pair
ownership fixes passed 77 tests in `29abe0cb441e48118c29f5fb423b4f37`. Go host
gauge snapshot ordering passed `9f0936e0bd0447f3b0957ef6fab72a71`; Go protocol
event minimum-budget validation passed `9e52805f73c246aead9f5c4377e6977d`.
Full source/platform and cross-engine qualification remain open.

Latest implementation checkpoint: Go protocol node serialization now enforces
the reference depth limit without consuming a rejected probe event; debugger
suite `243ded384be84b8eb9d5862c4440f103` passed. Go constrained telemetry
measurement ordering passed focused job `f3c81f56919d48299c265da08dc23c48`.
Python paired projections now receive detached state/effect values; eight tests
passed in `4c1e7ebe208f4899b910110d2a864279`. TypeScript pair input ownership
passed 79 focused tests in `a4086d2097dc4bb8b14bc5bef27a98a0`. Swift assurance
imports validated manifests/reports and exports merged reports; nine tests passed
in `4444dcf42fb14813b51b67812ebc4419`. Prior Swift job
`a8f59afac5b849fe8266c2f616c5c233` failed to compile a test dictionary key and
was corrected. Merged-report import and Rust-produced fixture verification remain
open; validation of an imported report is not evidence authentication.

Swift assurance now has BFS/fuzz wrappers that record actual run metadata,
termination and partial failures, plus identity-checked deterministic report
merging; Kyoto job `bb06092c857c4e639e2875ffa0938c18` passed six tests. Guided
and campaign wrappers and interoperable exports remain open. TypeScript network
policy and queue ownership changes passed 76 focused tests in
`ec2344f812cd4733829760a5dacf8053`; this is not a benchmark. Python disabled
watch boundary behavior passed 153 debugger tests in
`3bef4a72878b4d3aae5cc3f0804692ea`. Go production metric gauge-scope filtering
passed focused job `f45bf58ec26c431c9d26fccc527a06be`. Go watch event ordering
and preflight tests passed `f60b526293dd4e8dbad146ee8b4cdcc5`.

Subsequent implementation: Go provides explicit effect and delivery abandonment
without fabricating termination; debugger suite `cce753366b9f44b79f10c63d4a97c6ef`
passed. Hosts must invoke these APIs; Go does not detect dropped handles
automatically. Go watch handling now survives a panicking host error formatter;
debugger suite `07311ff4a14f4621bbbc850e95e2ff47` passed. Python diagnostic
health and eligibility counters saturate at u64 max; focused job
`a68d5f4cb33a4efa965a700b6af9ca30` passed 17 tests. Swift watch current values
and baselines now share a retention budget; focused job
`6d4ad1595cff4a989845668bb31b2de4` passed 13 tests. Swift's conservative
separate-copy accounting can reject current values earlier than Rust. TypeScript
runtime recording snapshots observations before callback alias mutation;
focused job `7629e0f67772459ca7e8dcae123ce8fc` passed 85 tests. Model clone
methods remain responsible for deep ownership.

Subsequent coding: Swift watch status supports pages beyond 1,000 entries and
effect queries classify foreign run/epoch as wrong_epoch; Kyoto Protocol job
`d5a857ff2795466eb5e967ce1038fb2f` passed 30 tests. Go historical metric
windows are sized from the retained window, and the full debugger suite passed
`b452caac0b294ddd84de1d27f6a2d804`. Reentrant Go subscription admission no
longer reclaims state while busy; focused job `6e03d89bafa84795a31ca2b0065f0dd5`
passed. Python probe field order and response-preflight retention passed 12 tests
in `6f7a79d9b76c4a069a8934442e4eb695`. TypeScript trigger validation passed
41 focused tests in `eace3a96f06041ad8b7d6438ce6b432f`. Swift's optional
missing effect origin and cross-engine wire qualification remain open.

Go observer attachment candidate provides metric catalogue, paged snapshots and
windows, and available telemetry health with preflighted snapshot commit. Full
Go debugger suite `cf5a070f26fd4f22bb492891fb7f9432` passed. Effect details and
timelines still need underlying retained reference fields. Swift watch attachment
candidate supports adapter-delivered turns and prepared watch operations; Kyoto
job `4a638113f0ce4cb48925562c1563f17d` passed 11 focused watch tests. Direct
session calls outside the adapter are not observed, and unusually constrained
watch status responses may return a limit error. Neither candidate has installed
cross-engine wire qualification. Coding continues before broader qualification.

Go diagnostic probes no longer inherit ordinary watches' forced delivery on a
known property failure: triggers and sampling still apply. The Go debugger
suite passed Kyoto job `3211ac46958c4daba029851eedbeea1a`, including property
failure contexts with failed and successful projections. Remaining scoped-turn
continuity and cross-engine qualification are still open.

Go scoped turns now invalidate existing producer-local probe baselines after a
selector gap, selected source change, or skipped transition, and report skipped
turns once per affected sink. The direct protocol continuity test and the full
Go debugger suite passed Kyoto job `2c3fa664b7b243b896826e15ce0c99ea`. A
selector gap before a producer's first probe registry exists is not yet retained;
full continuity and cross-engine qualification remain open.

The pre-registry selector-gap case is now retained in bounded producer state
and transferred to the first probe registry. Admission rejects insufficient
capacity before mutating the turn. The full Go debugger suite passed Kyoto job
`7645a536261b4374a95386edeb8e9875`, including first-probe continuity and
capacity-atomicity tests. Broader native and cross-engine qualification remains
open.

Go scoped markers now retain pending continuity reasons through selector,
source, and transition gaps; an evaluated marker consumes that reason even if
sampling suppresses it. The full Go debugger suite passed Kyoto job
`6be800eecaeb4f62ae45e8b6b02d6fb5`, including a direct selector-gap and
sampling sequence. Marker trigger parity and cross-engine qualification remain
open.

Go markers now filter equality and numeric-threshold subscriptions, which
cannot match a marker's absent payload, and report those decisions in sink
health. `every` and `changed` markers remain eligible. The full Go debugger
suite passed Kyoto job `3637ab9911fe4c9a80be7f45ba40f86e`, including all four
trigger cases. This local test does not complete cross-engine qualification.

Recent implementation work includes Swift merged assurance JSON import (nine
focused tests passed, Kyoto `b406d1cff0d142ecb1136d00cad7a0c3`), Go metric
measurement ordering (`f3c81f56919d48299c265da08dc23c48`), TypeScript pair
action ownership (79 focused tests, `a4086d2097dc4bb8b14bc5bef27a98a0`),
and Python generated-adapter ownership (25 search tests,
`4d51587e375242d798d3f2462a3b7d4f`). These are local implementation checks,
not cross-engine qualification.

Go watch observations now reject stale boundaries and invalidate registered
watch baselines on a skipped sequence or session change. The first debugger
suite run (`feb09e3426b5445fb86624322f188f20`) failed because an older test
replayed a stale boundary after adding a watch; the test now advances to the
next boundary, as the Rust reference requires. The full Go debugger suite then
passed (`112848f68aa746c4993441cbb85fc9e4`). Remaining implementation and
cross-engine qualification are still open.

Additional coding: Swift pair/network codecs now encode incrementally into
bounded sinks (six state/input/output paths, focused Kyoto test
`791a397050ac46909d35bd86ee868344`); application encoder allocations remain
outside that bound. Python network identities reject booleans, floats and
out-of-range u32 values, including restored state (11 stateful tests,
`d5eeca1cd72e44da923abdaab27d91f8`). TypeScript network transition checks
preserve the original transition across callbacks (build
`dbb9ba9a60be4c76a8a4eaffb2123fd2`, 81 tests
`51764e9104074651871f80420759106b`). Go clock-read failures now affect metric
completeness only when a measurement endpoint is missing (focused test
`0c8edba7777843dc91bd186eabaf41e7`).

Go explicit omitted-scope notifications now retain continuity before the first
probe and reserve all necessary state before modifying the producer or queues.
The complete Go debugger suite passed Kyoto `0d9056e7136d402784a177077dfce420`,
including capacity rejection and first-probe continuity. These local checks do
not establish cross-engine qualification; further implementation remains open.

Further implementation: Go omitted-scope records now use stable subscription
order and update sink capture health; the full debugger suite passed Kyoto
`ef0cacdd403d4100b7e0ec2713ea39df`. Go live trace provenance rejects reserved
caller keys and includes configured retention limits (focused test
`e78a8ba79dc04e599d917e99955af587`); event byte estimation remains Go-specific.
Python lifecycle validation now rejects malformed restored obligations,
resources and violation metadata before callbacks or encoding (12 stateful
tests, `e4cf94cf71684279a49ac0f553192414`). TypeScript pair and network state
checks isolate the state seen by later callbacks (build
`9aea775f8ef94c249ea9a88c345e3840`, 82 tests
`06e54f5e11dc4a6da7ad695d1591acfb`). The earlier note describing TypeScript
count APIs as lacking 64-bit domains relied on a stale gap document; current
source exposes bigint APIs, although full parity still needs verification.

Swift shrinking and offline minimization now offer an optional bound on all
simplification candidates, including repeated no-progress candidates, with an
explicit incomplete termination and count. Kyoto passed 12 search tests
(`bbb794b43e804bdc99b80fc0c637e591`) and seven evidence-viewer tests
(`10a8372a835b4384a1e072169961cfc0`). The limit is opt-in; without it or
cancellation a hostile candidate iterator can still run indefinitely, and the
new setting has no cross-engine contract yet.

Go diagnostic scopes can now record an omitted observation within the turn;
its notification uses staging and turn limits while its continuity gap remains
retained even if the notification is dropped. Go live events now include the
dispatch origin and expected/actual host sequence for gaps. The first full
debugger job `88c7c67e23b34d9896d187b685d06731` encountered a concurrent test
compilation error while the live-event test was being edited; after correction,
the combined suite passed `ebf87f0b41cb41d1af9abb0ceb417a92`.

Swift inspection builders now charge structural and child metadata to their
byte budget and enforce depth on redacted children. Kyoto passed focused
inspection tests `cc8fc8067977441b91093eb43ec18379` and 115 debugger tests
`710a1707963749d281876dce04b3ee16`. Bounds cover cooperative builders, not
arbitrary callback allocations; cross-engine exactness remains unqualified.
Python StateFeedback now isolates callback input snapshots, including on errors;
45 guided tests passed `df0c6766560b4fc9b80ab4f4c131eea3`. Current TypeScript
source has bigint count APIs and debugger candidate/export bindings despite
older gap documents; this observation is not cross-engine qualification.

Go probe capture now preflights capacity for all matching subscriptions before
calling the projection or publishing any record; the debugger suite passed
`f3eb6270ddd247e796f1e0ff7a72af1e`. Live attachment now rejects byte budgets
smaller than a Go LiveEvent and charges retention by its actual size (focused
job `9013429cea43491fab343f37a6af3b9b`). Swift structural diffs now detect
child ordering and metadata changes and treat duplicate paths as ambiguous;
eight inspection tests passed `55decc339a054120819193c01e0c748d`. Python
oracle and generated input iterators now detach each yielded value while
remaining lazy; 26 search tests passed `783c0595551a4c6f937b955b299d7a10`.
These are local checks, not cross-engine qualification.

N10 implementation has begun in TypeScript: an owned, validated assurance
manifest is exported; build `4981034ab239403cac8b4e79cde60310` and two focused
tests `28242fd37855442582294201ed84304c` passed. Coverage observation,
assessment, merge, assured runs and campaigns remain open. Python now has an
experimental manifest, search observer, property and coverage tracking, and
strict assessment. Four focused BFS and negative-control tests passed Kyoto
`dfbcd25f0e8a47fd8803612c13cb5a14` after an initial termination-mapping test
failure `e451030eab1b42ebb28cc121868e52fa`; report merging, derived catalogs,
campaign orchestration and broad qualification remain open.
Python package exports, an example and documented limitations now accompany
the experimental slice; five focused tests including the example passed
`f6b60c1d1f254e1498405829ad77b62b`.

Go marker admission now checks capacity before changing the producer or sinks;
the debugger suite passed `e9ddd043f01a49e6aa623f4ef4ec0a7c`. Go dashboard
JSON row label variants now follow reference spelling (focused test
`f0111aca35744aeda74eefcdf1ffd15b`), but health schema and some reference
health fields are still missing. Swift pair/network equality callbacks receive
detached values (four focused tests `f9c5b58f3b6c42d291860d19bffcc135`);
the guarantee depends on faithful application clone closures.

N10 implementation advanced across all ports. Go has an initial validated
manifest, real SearchObserver integration, coverage tracking and strict
assessment (focused Kyoto `12080753769944489af663b6b8c84ca4`); automatic run
wrappers, merge, campaign and several report details remain open. TypeScript
added an observer, phase-qualified property and coverage accounting, and strict
assessment (build `c042e43113c14e309fc5f24df81933ab`, 25 assurance/search
tests `9370cecf91944cd98b22900771b99797`); run wrappers, merging and campaign
integration remain open. In both, callers currently supply the final outcome.
Python added identity-checked report merging (eight tests
`2469115a3147498d9e16f1667f5f41a1`); derived catalogs, serialization and
campaign orchestration remain open. Swift assured BFS, fuzz and guided runs
now accept explicit metadata without a trace codec, checking agreement when a
codec exists (10 assurance tests `6d67e082795049ddbb0792dc02ca61b2`); the
explicit metadata is host-attested. None of these results establish N10 parity.

Go inspection now passes callbacks a detached query path so callbacks cannot
mutate caller-owned path slices. The full debugger suite passed
`466c0bb2ac3a4284b8da30c73103bf04`.

N10 assured run wrappers now derive outcomes from actual runs: Go enumeration
and fuzz wrappers retain partial evidence on errors and record budgets (Kyoto
`203f5a0ca78e40b9bdeb7a57b4058139`; first run
`6c0b0d0a4042461292a07f2a9de85536` exposed a corrected test fixture missing
equality). Go guided/campaign wrappers, merging, and derived goal statuses
remain open; the wrappers require codec metadata. TypeScript enumeration, fuzz
and guided wrappers record outcomes and reject contradictory termination pairs
(build `da802590ce7e43389c921bbee106f565`, 49 focused tests
`002e3c584cbf4fe9b9ddf82455729ba9`); external report validation, merging and
campaign integration remain open. Python BFS and fuzz wrappers retain partial
evidence on errors; report validation rejects contradictory and unknown
completed terminations (11 tests `7631c560c09a4cc8b1fc2416f9d74e17`). Its
merged-report marker is still externally forgeable; validation is not attestation.
Python guided and campaign integration remain open.

Swift now has a bounded host-driven reference controller with 14 independent
checks and nine named fault controls. Three tests passed Kyoto
`df6e3208c3dd43cc89acc7efb18b0f7a`, covering baseline exhaustion, named faults
and omission controls. Portable codec, independent catalog/coverage integration,
cross-language replay and model bounds beyond the explicit clock cap remain open.

N10 guided wrappers are now present in Go (`2ec5a8e348a74db9a18d91105d135b4e`)
and Python (`d7f8089b0a89446aab7a8d66ec0063e8`), retaining feedback identity,
configuration and failure evidence. Go strict assessment also rejects
inconsistent completion claims; Go report merging remains open. TypeScript
report validation and merging now checks identities, observations and job
membership (build `654660517ea741c985ebc939568141ca`, 49 focused tests
`ca1c310412e944d0902b0d9d83a07f6a`). Validation establishes internal
consistency, not authenticity of externally supplied observations. Campaign
integration and independent qualification remain open across these slices.

Swift's reference controller now has a bounded portable codec and local replay;
four focused tests passed `6c9b30b0c38c4b9da18e4ebf94a755d2` after initial job
`aaf30b4d504a4000bb826b31399f5c1d` found a corrected test naming collision.
Its manually derived wire vectors have not been checked against a Rust-produced
fixture; build identity and catalog/coverage integration remain open. Go
inspection now converts application schema/projection panics into errors; full
debugger suite `0a4d27c77674498e9cb92df02b00ab47` passed.

Go assurance merging now retains and verifies owned constituent reports,
including identity and missing-job checks (focused Kyoto
`9be685b2b82e4646a6c3225fa9b3d94d`); campaign and cross-language integration
remain open. TypeScript and Python now support single-worker ordinary assured
campaigns with delivery-failure and missing-job evidence: TypeScript build
`74975618621343a09f0748b16880bfcc` and 68 tests
`ae98c15dc20f46f992713cd92e97909a`; Python 14 assurance tests
`b1198d361c1d460ebe62d648dea90a47` and 28 campaign tests
`5eae4844475e48c783dd749a0ffdaf95`. Guided and multiworker assured campaigns
remain open in both ports.

Swift's controller now consumes caller-supplied requirement catalogs and
classifies five families and six goals. Five controller tests passed
`0ff9230adf4f4118a8424bd8c5ea57d2`, including a weakened-check control.
Catalog artifacts remain unbundled and their status is awaiting independent
review. Go watch adapter-factory panics are now contained and recorded as
inspection errors; full debugger suite `caee2f83bcab4d0bba1a4a06c5ca63a1`
passed. Cross-engine qualification remains open for all of these slices.

Go ordinary assured campaigns now run through the existing coordinator with
per-job evidence and delivery/accounting failure checks; focused Kyoto job
`5df7ad8069684967bf711b2f59023ce2` passed, including two workers, sink
failure and identity mismatch. Guided campaigns remain open. Python and
TypeScript guided assured campaigns now use their single-worker schedulers;
Python passed 16 assurance tests `53524206aefe497e921c66d64fbdeb32`, while
TypeScript build `1d3c6f3a868d40dc82e6a489ae3a975e` and 91 focused tests
`64664565b765414084ca17f97039f788` passed. Multiworker assurance remains open
in both ports.

SwiftPM now embeds exact reference catalog artifacts so an installed consumer
can load them without checkout documentation. Five controller tests passed
`40a58c920e46434eb18ef8ed49c35d19`; a separately staged consumer built and ran
`d81ebb4876ef4cdcbda1e5b009453fec` after an initial temporary consumer manifest
omitted the required platform (`c4ce4dbe89cd45c0a781477c9a4bf13b`). The
ABI-v2 staging path is not validated and the catalog remains awaiting
independent review. Go watch configuration now contains projection callback
panics without installing a watch; the full debugger suite passed
`64b0aa1779c4452ca270026bdebd41ad`.

Go guided assured campaigns now use the existing concurrent coordinator with
per-job feedback identity and cleanup/delivery failure evidence. Focused Kyoto
job `25564dda8b4b4a8ab04bbad0eb6b1cf5` passed. Python added derived property,
family and goal result catalogs (17 tests `2212eca2526b4b4c8ef91dea7bbe62e8`),
and TypeScript now orders assurance records by Rust-compatible UTF-8 ordering
(build `71413d9de99b408ba6ce20605267e4d5`, 69 tests
`cd4209893e824eb5a47208affdbf0db3`). Python and TypeScript multiworker assurance
still needs an evidence channel independent of bounded/discardable delivery.

Swift reference fault evaluation now records original and minimized target
witnesses and distinguishes incomplete search; seven focused controller tests
`6266a009375c49bdaecae2f84c426277` included all nine controls and local exact
replay. Fresh-process/cross-engine replay and independent catalog review remain
open. Go diagnostic dropped-record counters now saturate rather than wrap; the
full debugger suite passed `183229f9a78c4aacb23dff645f0b6b89`.

Python multiworker ordinary and guided assured campaigns now transfer and
acknowledge evidence before result encoding or delivery. Kyoto passed 20
assurance tests `a37ea4f030b94090a499558929ee06c4` and 28 existing campaign
tests `5190b46957cf45f6a220dc5308fe6ae0`; an earlier campaign run
`a9d77446d244421b9c2844a2c5fa40fe` exposed a corrected bridge compatibility
issue and was cancelled. Spawn-picklable factories and ProcessWorkers remain
required. Node ordinary multiworker assured campaigns now transfer evidence
before bounded delivery; build `c3cb91b559c64a6f86b3dbd852cdfe26` and 70
focused tests `8f908486f45b4d1c8f524d504151a7bc` passed, including a two-worker
sink-failure control. Node guided multiworker assurance remains open.

Go added derived assurance property, family and goal catalogs; focused job
`1a98e341cecc490bb435638355e47a7e` passed. Its explicit observation-gap count
now saturates, with full debugger suite `c12c011d1f2041dd8ad896b9f1959588`
passing. Swift added reference witness recording and strict verification;
eight controller tests `2c7dde7cb07a4ddcbeed2b34b36a4252`, a writer process
`f63c490634de44a792e7e7ae792be630` and a fresh verifier process
`e414fc56b10f4c30bf4d441773f2c7a6` passed for one missing-cleanup witness.
This is Swift-to-Swift replay only; v1 has no phase boundaries and cross-engine
replay and independent catalog review remain open.

Node guided multiworker assured campaigns now retain evidence before bounded
delivery; build `1c4706a5df0b4bf380adb4653cebaac2` and 93 focused tests
`90accbacf0d94eb68d1cd73a30ac0e14` passed. Go assurance JSON export passed
focused tests `6deff7c5fd0345d29df9ea3eb62fce0f`; Python assurance JSON export
passed 22 tests `9372b4fb07cd45d9a1728be1b6263fb8`. These schemas were compared
to Rust source, not yet executed against Rust-generated JSON fixtures.

An independent static review found Python assured campaigns could materialize
an unbounded u64 job range even when cancelled. Campaigns and report merging now
enforce a configurable maximum, default 100,000, with explicit rejection and no
silent truncation. Kyoto `f346251f555e45a6ac6959d8586bce5b` passed 23 tests,
including pre-cancelled maximum-u64 input. This is an explicit Python bound.

Rust and Swift reference witness replay passed in both directions for original
and minimized witnesses across nine faults (36 replays): Rust-to-Swift job
`79c0d62018294ebe90eaeb76e76a3267`, Swift-to-Rust
`ec6543e23d984e6e9732cbc7b8407573`. Every case explicitly allowed and reported
the differing build identity; Rust strict verification rejected mismatches.
Swift strict identity remains default (unit job `c073ace4f4e2466a8b8d17a832d7ec62`).
Artifacts and harness are in `/private/tmp/stateless-reference-cross.aXYIWR`.
Evidence covers two engines on macOS only; binary v1 lacks phase boundaries and
full five-engine qualification and independent catalog review remain open.

Go can now import individual Rust-shaped assurance reports with derived catalog
and consistency validation (Kyoto `1040946995054153b27a7a522b28aa26`); merged
import and duplicate JSON object-key rejection remain open. Python added bounded
manifest/report JSON import with duplicate-key and derived-catalog validation
(24 tests `472a5e74050041968af9f05fc4988cb1`); merged import remains open.
Neither import has an executed Rust differential yet. Swift merge now preserves
depth, budget, timeout, interruption and failure precedence (11 tests
`f3b04adcce664ac1b097e1ccd7d49c64`).

Independent TypeScript campaign review found caller metadata could change during
delivery, expected-job arrays were insufficiently bounded, and campaign stop
and control-budget provenance was omitted. These are corrected with owned
metadata, configurable default 100,000-job admission and explicit campaign
metadata. TypeScript also added Rust-shaped JSON export. Build
`b8b16c6e3c49471cad9463043f9be55b` and 94 focused tests
`897c87460208426c820612c93854bd9c` passed. JSON import and executed cross-engine
comparison remain open; campaign callback-error termination retains its kind
but not Rust's embedded debug-formatted error.

Assurance JSON import advanced: Go reconstructs merged aggregates from embedded
job evidence and rejects duplicate keys recursively and invalid UTF-8 (focused
Kyoto `b0ea2035400b4b678b14746dd85ed524`); campaign aggregates without
coordinator proof remain rejected. Python added bounded merged import with
identity, aggregate and delivery validation (25 tests
`b025193c52b6434b9c9024dbe861d5e7`, after correcting budget ordering found by
`476fda04c3e04cc7b11bbae3b36d354d`). TypeScript added bounded manifest/report
import with exact u64 and duplicate-key checks (build
`f40408f449264ce588d4dc83eefe59d8`, nine tests
`ec9b9b1c9d6d4aa8aa8e87a0a8990f85`); merged import remains open. Executed Rust
JSON interoperability remains unqualified.

Independent review identified unbounded Go campaign job catalog allocation and
invalid UTF-8 identity handling. Go now validates manifest/evidence text and
metadata and enforces configurable `AssuranceMaxJobs` before allocation or
callbacks, defaulting to 100,000 (covered by the same Go job above). Swift
ordinary and guided assurance campaigns likewise enforce configurable retained
job admission, default 100,000 (11 tests `bd18e1d955f44f9f8659559b13a27870`).
These limits bound job counts, not arbitrary callback allocations or report
bytes; a shared cross-engine limit contract remains open.

Independent import review found false completed claims possible through
unproven aggregate terminations and inconsistent campaign accounting, plus
unpaired surrogate handling. Python now rejects standalone and nested job
aggregate claims, rejects unpaired surrogates and validates campaign counts;
25 tests passed `444994457d074dc6b52147319a62a1a2`. TypeScript now requires
constituent job proof and reconciles requested, started, finished, delivered and
missing jobs (build `d67d2c469466440a8c4ef902e122f6c6`, 71 tests
`8c77ff0879114a529a12e51632e40535`). TypeScript merged JSON import itself was
added in build `0cc1e7a2a27d4942a74a23a366770e1b`, focused job
`2b28f55f6439490199a59173e6f8ec1f`.

Go campaign JSON uses an explicit coordinator-proof extension, recomputes its
aggregate, retains sink errors and rejects unpaired surrogates (focused job
`4cae6b94806046e5953a49353f1422e7`); the extension is not yet cross-language
qualified. Swift campaign coverage and existing observers now get independent
cloned snapshots (12 tests `c696c0c776cd4aed8f214c98bb049af9`), subject to
faithful application clone callbacks. A partial Python controller reducer,
domain and state validity check passed four tests
`352a8ac6941e4c9389e745fbf5904700`; transition obligations, fault controls,
coverage catalog and codec are still open.

Reference controller implementation advanced: Python now has 13 transition
obligations and all nine named fault controls (six tests
`fce23b1e88694dc19c095a16deceb247`); independent coverage and codec remain open.
Go added an initial bounded reducer, lazy input domain and state validity
(`85b6fedd39bc48abb1623e4d69969094`); transition obligations, faults, codec and
coverage remain open. TypeScript added an initial pure reducer (build
`6c050d8c3888421d9ace6edcec0dc1ec`, 11 tests
`d6d7c0a4f36849fea6cf8ee15e376e44`); checks, input domain, faults, codec and
coverage remain open.

Import re-review found Go completed campaign coordinator totals could contradict
retained job evidence; Go now rejects that (`e1090597eb634e628a72873fbcc121fd`).
TypeScript import now accepts honestly retained evidence before a failed result
delivery and incomplete JobsCompleted reports, without granting adequacy (build
`2fbf2af8fe534f68bf985fcb8646f8a9`, focused tests
`152d9da2c0c345e586d037c97f06ec8f`). Swift controller build identity now includes
a source-bound hash with drift check (nine tests `424906230d51431eb8d2db5caf628523`).
The earlier Rust/Swift replay evidence predates this identity change and does
not qualify the final source; the hash is provenance, not authentication.

Controller implementation advanced: Go now has 13 transition obligations and
nine fault controls; baseline exhaustion and all target faults passed focused
Kyoto `a630b8b9770b4d888dc5d68f0d4a4892`. Independent review fixes reject
invalid restored phase payloads and malformed completion outcomes, and bind
metadata to sources, bounds and mutation. Coverage and codecs remain open.
TypeScript added bounded enumeration, 13 obligations and nine fault controls
(build `9ec18f22369b4e2f9c63310b5767101d`, 12 tests
`c7bf7cbcb8e7445c997c610fba9b75b7`); generation, codec and assurance coverage
remain open. Python added packaged requirement catalog, coverage and codecs;
nine controller tests `d767f61803f54e689a4ab7c205e71a50` passed, including
finite assured BFS and a weakened-property control. Installed-package and
Rust differential evidence remain open.

Swift now embeds the retained fault-witness corpus and checks each mutation
against its target while requiring the fixed reducer to pass identical inputs.
Ten controller tests `ce806cd5b0a244939a917c19df0ba6e7` passed. These controls
are in-process; independent catalog review and broader qualification remain open.

Go controller now has a codec, independent requirements catalog and coverage;
focused tests `1db8f207156a4ea494062aef01909b6a` included exhaustive assurance
and a missing-property control. TypeScript added controller codecs and coverage
catalog (build `118203eda51043d8b7e69583b21f6565`, 14 tests
`97e2603daa614b56a22c8a0490b89326`); model binding, generation and independent
provenance remain open. Python added target-preserving mutation evaluation and
local witness replay, with all nine controls exercised. Review fixes reject
noncanonical payloads and malformed state, freeze controller configuration and
replace eager input generation with indexed sampling; 12 tests passed
`b33779e54d3246c88cf10f9c84bcc2af`. Rust differential and installed-package
qualification remain open for these ports.

A Rust-produced controller assurance report (`c47d2e02a9e4488295a352dfcccf0022`)
was imported and assessed by Swift in a separate process
(`38ddd625fcd54be8a1a2727ade509e7c`), with 3,983 transitions, 14 properties and
six goals; three altered reports were rejected (`493919de9bb745aa87487588cc32fe1a`).
This is one Rust-to-Swift report check on macOS, not authenticated evidence or
five-engine qualification; the requirements catalog still awaits review.

TypeScript controller now implements search/generation, bound codec, replay and
assurance APIs. Independent review corrections reject malformed runtime inputs,
phase payloads and codec tags, isolate configuration, and keep cancellation
acknowledgement separate from cleanup. Build `4071745166cc4ee1a6f1ba0f6d22874c`
and 17 focused tests `05f2d87c97c8436db750d177bdf4efd6` passed, including
assured BFS, fuzz and local replay. Host integration and source-hashed build
identity remain open.

Go added indexed generation, target-preserving shrinking/replay and a cooperative
reference host. Focused `a17a4cc19bc640dea8158ec1a4679be4` passed all nine
minimized witnesses and host cancellation controls; initial test compilation
`3a1dd11b9df74c7d9ebbc4d4d40cbb39` required a corrected RNG pointer. Python
added a single-owner host and retained witness corpus checks; 14 controller
tests passed `915a03de4c6e4a249dd64355fc49e71c`. Fresh-process, cross-language
and runtime race qualification remain open for these host slices.

Swift assessment now distinguishes exhaustive, sampled and inadequate
confidence and records cancellation-enabled provenance. Kyoto
`dccaf46868ff47a19f7a5c802aa8034e` passed 56 tests after initial compiler error
`237340420cc34e1885a2945f597755c8` was corrected. Duration remains represented
in seconds, so Rust budget-wire equivalence is not established.

TypeScript added a browser-safe cooperative reference host and source-bound
controller identity (build `6bf5949130a244eaa3135867fec230dd`, 18 tests
`493562c38477458c963580f114c8c174`). Worker creation remains caller-owned and
the host does not provide crash-durable execution. Go added retained witness
corpus controls and create-new binary mutation-suite persistence; all nine
controls and 18 witnesses passed `34a48864fe144a8e9efa33f7ca002ba0`. Python
added mutation-suite persistence and fresh-process replay of nine minimized
witnesses (`504325c345cf4734ac4dd33c00a738df`). Independent review exposed
Python host reentrancy, unsafe error formatting and mutable internals; fixes
and 16 controller/host tests passed `f9fe5be998e3483e9496f62be02aa163`.
Cross-engine and installed-package qualification remain open.

Swift now adds Rust-compatible nanosecond budget values when the supplied
Double roundtrips exactly and safely, retaining seconds otherwise. Thirteen
assurance tests passed `76b90fa2b4b04f2eb2399202d3e1e91f`; full Rust u128
duration support and cross-engine budget-wire qualification remain open.

Go exposed exclusive trace preservation and mutation-suite JSON; fixed-control
replay now requires complete input execution and passing initial checks (Kyoto
`9d5da478ddc544e5a39ccf061957c792`). Python assurance campaigns snapshot caller
manifest and configuration before callbacks, and direct reports validate
container/error/budget types (27 tests `5ea1fe9d21774bc3b8b31292fc2131e4`).

TypeScript mutation evaluation now performs target-preserving BFS and shrinking;
all nine controls and a cutoff negative passed. Independent host review fixes
make close detach uncooperative work, reserve receive ownership, safely format
hostile observer errors and protect authority state (build
`d178cca5da9c4939ae7a002e4361303d`, 22 tests
`d7453dc195874fc296760f3a229318d5`). Earlier test job
`0c0ed6a469c94de8b011518de95eb27f` required correction of an overly narrow
first-failure assumption. Persistent TypeScript witness storage remains open.

Swift assurance now bounds retained property identities, errors, coverage hits
and identity bytes; truncation records loss and prevents adequate assessment.
The final 24 assurance/reference tests passed `944fbd790eb44b3aa36e5d3005819d22`,
including a replaced-collector bypass control. Callback allocations remain
outside these limits, and the finite defaults are not a shared native contract.

Python assurance coverage now uses callback-scoped bounded hit collectors;
overflow is latched even if caught, and partial coverage cannot certify goals.
Twenty-nine assurance tests `6a34d940df8446798b110643a2c7581a` and 16 controller
tests `7eae920f286e47dca864f98c9ee857ac` passed. Go added configurable retained
observation limits with fail-closed incomplete evidence (focused job
`13dcc15bc31e4b1c9cc249ea93a5e5dc`). These limits do not bound allocations in
application callbacks and are not yet a shared cross-engine contract.

Independent Go host review found concurrent operation and mutable authority
hazards and a worker Goexit completion loss. Host now rejects overlapping
operations before dequeue, exposes owned snapshots, and records an unexpected
worker exit without fabricating cancellation acknowledgement. Full reference
race job `ba5616d73f214c9e91f114aad07203d8` and Goexit-after-cancel race job
`77e234d1432e45e6aae73890fdc137fb` passed.

TypeScript added retained witness corpus validation and browser-safe mutation
artifact preparation plus an exclusive Node writer; build
`0365ce173f0049c6af7173583a6a9cff` and 23 tests
`79ed57c1508f4e81ab3cb9629384cc13` passed, including 18 local trace replays.
Atomic directory publication, directory fsync and cross-language qualification
remain open. Swift added bounded in-memory reference fault-suite evidence;
11 tests `ba7a5d2c709f4e5ba8dbc148a4f4b98d` passed, including partial-evidence
controls. Its suite does not itself persist or fresh-process qualify traces.

Go assurance now bounds catalog counts, text and aggregate size before cloning,
retains effective observation limits in run and campaign budgets, and rejects
concurrent or reentrant collector use with fail-closed evidence. Focused race
tests passed Kyoto `47ecce66b81047469c1430ed24a1a9a7`. Python now bounds
observed property accounting and uses deterministic coverage error ordering;
30 assurance tests `9991c89353944870bf13af3d72307bab` and 16 controller tests
`0ec17be945e04b39a864b89d7e51732f` passed. These native-specific bounds do not
cover application callback allocations and do not establish exact Rust parity.

TypeScript now fsyncs trace files and publishes the completed mutation-suite
report atomically, with bounded artifact verification and exact replay. Build
`149c0f9eb87c46da8a729c9e84d909b8` and 23 tests
`89b5f559746548e9a5ba898252c66525` passed. Atomicity is for the report, not
the entire directory; durability depends on filesystem semantics. Swift added
write-once fault-suite persistence (12 tests `da0ef531322048bca0ce796ef0617cef`)
and separate producer/verifier processes `d09d2d9659e34883a4a0ae763f91060d`
and `abf1b9f5ea4b4281addc6dc8439b6287` replayed all 18 witnesses with matching
build identity. The Swift-specific summary format is not Rust-qualified.

Independent Swift assurance review identified replaceable hit-collector limit
bypass, observer reentrancy and incomplete manifest admission bounds. Swift now
fails closed on collector replacement or overlapping observer use and preflights
bounds count, retained text and aggregate bytes. Seventeen focused tests passed
`97c2ea9222e343879d36c931a88b9fc6`, including negative and reentrant controls.
A dedicated concurrent stress test remains open; caller allocations are outside
the bounds.

Inspection comparison across Rust, Go, Python, TypeScript and Swift now treats
duplicate sibling paths as incomplete/Unknown, recursively; distinct paths remain
valid. Swift inspection `2c01edc3cd2a48adac5694850754978b` and watch
`93ac4bcd60284e5bba1039f017605fb5` passed; Go focused
`dd27a7dffcdc47f78aa39d2c0e7ab6f3` passed; Rust compile-only
`d6fefe5f2c9646b3b1c0196025e71545` succeeded and its inspection binary executed
11 tests successfully; Python inspection `f0f6a461fe8e49238df2510da72cd5af` and
watch `142ace87a7fa49d699963c06a745038c` passed; TypeScript build
`979c28e18124492b98980668b647728e` and 10 tests
`44a3e8f4643e4f14a4f33a8bede76be5` passed. Cross-engine wire qualification
remains open.

Go effect lifecycle calls now preflight handle/phase before the unlocked clock
callback and revalidate after it; focused race tests
`512a9f98076742edae99002b063de03a` and full debugger
`f5633f3f5fdf4ea9a70a2f91aff9a77a` passed. A call can sample a clock and then
lose a concurrent revalidation race. Python diagnostic context admission now
limits iterator consumption and allows retry after failure (22 tests
`fbfcd9ce004747e99085502b999c99ae`). TypeScript dashboard evidence is private
with detached getters and bounded JSON traversal; build
`70ed2c061a0c40d98cdfa98a12d45814` and 13 tests
`aa1d6f1c48734eb98dc5e828869276a1` passed.

Go assurance JSON import now has configurable byte, depth, value/key and text
limits, applied to merged jobs; focused race tests passed
`e31dd7ac29cb41fbba5ebe0c9987a1ac`. Python added immutable property and domain
manifest helpers (31 tests `deab02a4868948d19e2384b9246486ed`). TypeScript
assured campaigns now preserve the cancellation callback's original receiver
(build `05020c41a5a449afa4fd60426c889f1e`, 71 tests
`7446bd5fb6e14bfb83b43485d495f051`). Cross-engine qualification remains open.

TypeScript assurance observations now have configurable retained-property,
error, hit and UTF-8 text limits; overflow or reentry is sticky incomplete,
partial coverage is discarded, and hostile error formatting and invalid scalar
text are contained. A bounded Node suite reader validates and replays files.
Build `608c1608d4bc482fafdac36da392b21c` and 86 tests
`83fc822db2a64d4bb2928c072887ab33` passed. Campaign wrappers currently use
default observation limits, callback allocations and manifest size are outside
these limits, and the Node reader's no-follow behavior is platform-dependent.

Python bounds cumulative coverage and merged summaries without losing original
job reports; 33 assurance tests passed `677266a62e934567aa6dc0803a171c38`.
Swift now bounds retained budget maps and records observation limit provenance;
18 tests passed `f48deca48a5843dbaeb8bc475f91c8ab`. There is no Swift
campaign-wide retained-byte limit. Go validates controller tagged-union inputs
consistently across reducer/checks/coverage; the reference race suite passed
`abaaef1f1e2f4304a096b06df506bf5a`. Native finite limits differ from Rust and
their shared contract remains open.

Independent Python assurance review identified mutable evidence authority,
resettable coverage overflow, reentry, lease races and unbounded manifest
admission. Observer state is now private with detached views, fail-closed
reentry and incomplete-batch errors; coverage leases use sticky owned state,
owner-thread checks and snapshots; manifest preflight bounds text and catalogs.
Kyoto passed 38 assurance tests `d1f4f486631c4835ac821b2d6f45cdaa` and 16
controller tests `bf90655fe34b4e4cb6ac83b0a7e2a26d`. Python private fields are
not a security sandbox and its finite limits differ from Rust.

Go mutation-target authority is now private with detached catalog access; full
reference race tests passed `6bf688d9eead4be9aca5813e905dfe81`. Swift ordinary
and guided campaigns now enforce a 256 MiB default logical aggregate evidence
budget; 18 tests passed `73dac73dc16542729a75bdf6824d9b53`. It excludes
temporary allocations and its concurrent retained job order is scheduling
dependent. TypeScript propagates configurable observation limits to ordinary
and guided campaigns including Node workers, with bounded manifest admission;
build `d5e07c3ceabc4c8db9f64dc578f38187` and 86 tests
`743a58b2c94342c89415df5dbcd2a5b3` passed. These limits do not bound RSS or
allocations already made by callers.

Go assurance campaigns now enforce a configurable logical retained-evidence
ledger limit (default 64 MiB); excess job evidence is missing and prevents
adequacy. Focused race tests passed `6422f77de1174814ac80fe53e1d3abce`.
Python direct merges and ordinary/guided campaigns now have a configurable
serialized-job evidence budget (default 64 MiB), with explicit rejection or
missing evidence; 39 assurance tests `93948ecceb78409c93cecd6772a83b48` and
28 campaign tests `4e366ddddab6433fab7b05d67989a2d2` passed. TypeScript now
limits retained report counts and serialized bytes in caller and Node campaigns;
manifest preflight snapshots fields once, and coverage hit leases expire after
the callback. Build `23a35082211844f9b519038891667db3` and 86 tests
`5b83c504ca09496698e54469198032b4` passed. These logical budgets do not bound
all callback, transfer, staging, merge or process-memory allocations.

Swift replayed all 18 Rust-produced suite witnesses in a fresh process (Rust
producer `fc4049fa238f4cdfa1d62eebd72cfb54`, Swift verifier
`8adc50e7e42f415098d2ded904e6aa01`). Build mismatch and absent witness-kind
provenance were explicitly reported; strict mode rejected unlabeled witnesses
in 12 tests `01cb9f2165b24a9b9fd2011d1c9ec9e0`. Original/minimized labels and
full Swift-to-Rust suite-summary verification remain unproven.

Go campaign proof import now validates coordinator configuration, canonical
budget values and the contiguous expected job range; focused race tests passed
`a0da8dd8ac3d43f3a3fa64d900bf4b9b`. Independent static review found no
additional concrete defect in Python campaign evidence-budget accounting;
temporary transfer and merge allocations remain outside its retained budget.
Python controller identity now captures source and catalog bytes at module load
so later file changes cannot relabel already-loaded code. Seventeen tests
`08a85bbdc59f47f0a6a1b1301f209c06` and fresh-process suite
`7427998a46904a9e989cab6a7c873bdb` passed. This fingerprint is not attestation.

Swift indexed lifecycle now preserves prior-obligation evidence when late keys
arrive out of order; six tests `8b12a4aac0d444b6ac15e9e8fb0a8954` passed.
TypeScript reference assurance now uses the independent property catalog at
build time; a missing-state-check negative produces an inadequate report. Build
`88b3e105b8ca4cf3b2296b967a84c9b3` and 25 tests
`d7943a63febe4bf494e5f61f4b83ac00` passed. Source builds require the repository
requirements files; cross-engine and installed-platform qualification remain open.

Go assured guided runs and campaigns now validate engine configuration before
feedback metadata and retain partial assurance when metadata fails; focused
race tests passed `33d436ab210949a39ef5bff6060d8f5b`. Python modeling adapters
now detach state/input for callbacks, own transition results and support direct
append-only checks; five modeling tests `d6ff54d2597545879805fe29ec8bfe32` and
26 search tests `e5cc8d0f5a21494bbf023352455561f2` passed.

TypeScript reference witness evaluation now gates on the independent catalog's
property IDs, mutation targets, bounds and goals; build
`d1970b7b250c48f593cdc8f7a1a0737c` and 26 tests
`5a457eb8372d4f76b346ae2c8fbb06e8` passed. Independent human catalog review
remains open. Swift coverage overflow now remains sticky across copied and
restored hit collectors; 19 tests `91effcf45cba4c439d549926b0564631` passed.
An independent static review found no additional concrete defect in the current
TypeScript reference host or codec; it did not execute a qualification suite.

Go assured campaigns now charge coverage setup against the remaining deadline,
and imported campaign proof budgets cannot overwrite derived job counts. Focused
race tests passed `c254222bba1c4f4ca8829778b6d7cb00`. Python controller build
identity now fingerprints packaged implementation and independent requirements,
plus bounds and mutation (17 controller tests `f39428ea302541fdbaee5a36711c0ebb`
and fresh-process suite `18835cfd5d3b44d39aef1b8d0e987a91`). The FNV fingerprint
is provenance rather than authentication.

TypeScript now assesses imported single and merged assurance evidence after
validating constituent proof; build `266ca23e92694618a3da4140f0eeb37d` and 86
tests `c407836c70e34fb3bee04b151087f460` passed. Internal consistency is not
evidence authenticity. Swift suite verification now checks each fault-specific
witness against its summary producer build, even with local mismatch allowed;
12 tests `3f9f2569207d459b9893cfd2f2a9142b`, forged identity control
`046260f9f9ae4184b25f754a6664cadf` and 18 Rust witness replays
`f905222245c74968a14bf7cf87f8db34` passed. Rust build mismatch and missing
witness-kind provenance remain explicitly reported; future build formats need
explicit support.

Swift and Rust verified forks now reject ambiguous environment provenance; Rust
also rejects incomplete/unknown provenance and a restricted fork claiming an
unrestricted parent under the same label. Swift BranchTests passed
`82be7db967e84501b045f82e82655963`. Rust Kyoto compilation
`f873dcc1e4b145ec975d4e4fd949d538` was compile-only under policy; its compiled
workbench binary was separately executed and all six tests passed. An initially
suspected earlier-failure/later-pass fork case is unreachable because existing
trace validation rejects continuation after failure; its redundant guards/tests
were reverted. Broader cross-engine fork qualification remains open.

Go, Python, TypeScript and Swift forks now use the updated strict recorded
environment admission: duplicate/incomplete/unknown provenance is rejected
before replay, unrestricted parents cannot be mislabeled as declared, and
legacy traces with neither field remain accepted. Focused Kyoto results: Go
fork/minimization `24939a29576d49e9bcd7bb15971290fc`; Python workbench
`fba81e08aefb4e8e8a89ab660d1e0dec` and debugger
`16b571b891bb4cd78c3b5ebef74c5062`; TypeScript build
`c81811a7b10f44d1bdb1fb9680267af1` and 14 tests
`564e04496c2947ddb5c8a05d583655a4`; Swift branch
`35ab20199da84190933d397754c95235` and evidence viewer
`73753dd1a8c2470bb6dd33ba08797f08`. Independent static review found no
additional concrete regression in the Rust change. These focused tests do not
establish broad cross-engine protocol qualification.

Python legacy checkpoint parsing now accepts long leading-zero u64 values while
rejecting overflow without interpreter conversion-limit leakage; eight debugger
tests passed `80cf0e39669545d8a17a35cc64e0bb46`. Swift live arrival FIFO now
clears consumed slots and compacts periodically; five live tests
`724be5cd074448cc8f96e976054aa76b` and three bridge tests
`1045f34034f74ae7b5a61553ecb579fb` passed, including 230-arrival order/capacity
controls. No benchmark was run.

TypeScript debugger minimization now snapshots replay/attempt limits before
callbacks and preserves the cancellation receiver; build
`0429a98cbd234c948607371289d3aa37` and 15 tests
`30d87846575942c887080c0f4e797b7b` passed. Independent static review found no
additional concrete defect in current Go assurance JSON admission. The Go probe
polling path still rejects event kinds without a complete wire envelope; broader
N12 qualification remains open.

V1 retained ranges now add the recorder's sequence origin to the relative
checkpoint/eviction count in Rust, Go, Python, TypeScript and Swift. Duplicate
or malformed origin and overflowing ranges are rejected; Go V2 continues using
its separately encoded checkpoint. Focused results: Rust compile-only
`c654b48f85774280b4dad0beae51adb2` followed by eight executed workbench tests;
Go `6f13331844c34ef4923ed2306a039f25`; Python
`b2a43038d3a74f5d8a5c89bb74b11c37`; TypeScript build
`96641533e4ac42da80ce414d77e9c081` and 12 tests
`8606dfb1da2146aea3c6a5bac3fd4244`; Swift viewer
`99de32783e7a46b1b5f263ae0644053c` and real nonzero-origin/eviction recorder
control `711f303214bd4aab9323d5c3eed9ab9d`. TypeScript watch-status response
budget parity remains open.

TypeScript watch status now returns complete bounded-prefix rows with accurate
total/truncation and usable-baseline state. Build
`cd7a54da5ada466782fd1b7444ec02bc` and focused
`16dcad3a633447f5a9a3ba4809521124` passed. Full protocol job
`a2bfff04e48e4258aacf2e118df19e20` exposed fixtures missing the required
unrestricted-policy label; after correcting the fixtures, all 22 protocol tests
passed `863f4b35d1554caa9ff77907d508d835`. Cross-engine response equivalence is
not yet qualified.

Python diagnostic configure now holds ownership through selector hashing and
copying, staging the config before revision changes; 20 tests passed
`5c996739e17a4dfab60ebf2ef3351b60`. Swift check sink now rejects reservations
whose element byte size cannot be represented; eight append-check tests passed
`c69105870d754692a8c7cb24c80dbdda`. Representable allocations can still fail
under host memory pressure.

Potential V2 checkpoint reconciliation was investigated and not implemented:
the V2 contract makes its explicit checkpoint authoritative and does not require
matching embedded V1 monitor counters. Recorder-produced snapshots do use origin
plus evictions. Rejecting independently constructed V2 artifacts for mismatch
would change the current Rust wire contract.

Python diagnostic turn admission now rejects reentrant configuration from a
context iterator and releases its guard on failure; 19 tests
`a1c2b95a3ea94546a8a45d8df191943c` passed. Go assurance merged JSON import
now supports explicit recursive job, byte and depth admission bounds; focused
race tests `409429f09dcb42bf9e373856452c2457` passed. TypeScript mutation-suite
provenance rejects duplicate/conflicting parameters and impossible status claims;
build `3fb03661e1634a25a3854b1eb670e2e1` and 26 tests
`dc58e9c1cb634bdabc42664d39243b7a` passed. Python metrics reject gauge type
changes in windows and retain production scope entries independently of series;
15 tests `3b8fd70a32d9458d8a41401f4bf090ab` passed.

Rust, Go, Swift, Python and TypeScript legacy evidence viewers now reject
duplicate checkpoint sequence declarations, including identical duplicates.
Rust compile-only Kyoto job `282dc32ce3b549879321a747542ac7a6` succeeded after
a resource-pressure retry; its workbench binary was then executed with seven
tests passing. Focused jobs: Go `da9dc8ef6b954eb4a83c5d3e1966d378`; Swift
`8e4b79367d67498e8f43edc5a62d7d9e`; Python
`64031fede4af4493ba439da2a40a10e8`; TypeScript build
`7c7ff5e60e7e41be937237149ebf9f09` and 22 tests
`a67b59baf69a4b768097b563c19fff64`. Go V2 retains its separately encoded
checkpoint as authoritative. Broader wire qualification remains open.

Go diagnostic scopes now reject invalid UTF-8 phase/property metadata before
producer mutation; focused job `b510406129b041ab9e929a25e30dd368` passed.
Python protocol now classifies unpaired surrogates as malformed without consuming
request IDs (28 tests `f594547fb78e4d18b2541fde8009c13a`; earlier outdated NaN
expectations corrected after `2aced5d853f24441ab02df7ddee5aa14` failed).
TypeScript minimization now snapshots policy before replay and safely rejects
Promise-valued admission/equality; build `12b96fc57e4a466b8dd61183b1038447` and
38 tests `8aff40a1b9784b17a65acbb692952214` passed.

Python built-in float display now uses shortest round-tripping decimal formatting
with exact bits retained; 12 inspection tests `bc23a5608bb44c1ca626a099df60e7ea`
passed. Exhaustive Rust-produced float differential remains open. TypeScript
session policy, limits and recording provenance are now captured before model
callbacks; build `5ab325130aaa45de9a11f4610af149f4` and 33 tests
`3c139d6c01f54e9c9cd35452fcd8d4c4` passed. Swift recorder error byte copying is
now bounded before truncation; 22 tests `aa6f77619ce14c268b270c080c14c105`
passed; caller error formatting is outside that bound. Independent static review
found no additional concrete Swift probe-paging defect. Go probe polling still
rejects lifecycle events without complete diagnostic envelopes; the necessary
retained provenance and completeness semantics remain unimplemented.

Go now attaches an independent bounded protocol probe queue per exposed sink;
host DrainSink and protocol polling no longer consume each other's records,
and Go-only notifications stay host-visible. Full debugger tests passed
`54c817511ab04c3a8fd9d1fec5508dd2`; earlier focused development jobs
`382e4a7b2643458fa20d591c0b443571` and `beb4ec7a7e954a4c96eabd0351edfee0`
failed before correction. The queue observes events after attachment only;
unsupported incomplete metadata still fails closed and protocol loss accounting
is independent from host exporter failures.

Independent TypeScript review found fork policy could change between provenance
validation and replay. Forks now capture policy and limits before callbacks and
reuse the snapshot for branch creation; direct session initialization similarly
captures option hooks. Build `1008f1c8e7d1459d975baee64c446926` and 35 tests
`77fe8134bff54566b30f8694cdfb65a6` passed, including a restricted-to-unrestricted
mutation control. Python now charges custom prebuilt inspection trees against
work limits (13 tests `1911641602fc4e60b48ee3e75f1e700a`). Swift v2 evidence
checksum no longer copies the full artifact; five evidence tests
`9c6d01b69a1a4cb6bb5ebae908cdd675` passed; no memory benchmark was run.

Go nested assurance JSON now carries and validates a Go-specific nested job
proof extension (race tests `898a7faccb57447eae68b3e7625717bb`); cross-language
extension compatibility remains open. Python metrics now reject gauge type
changes in windows and preserve production scope entries independently of
series membership; 15 tests `3b8fd70a32d9458d8a41401f4bf090ab` passed.
TypeScript mutation-suite checks reject conflicting identity parameters and
inconsistent search statuses (build `3fb03661e1634a25a3854b1eb670e2e1`,
26 tests `dc58e9c1cb634bdabc42664d39243b7a`); Rust-generated fixture qualification
remains open.

Go now preflights assurance manifest/metadata before wrapper and campaign
callbacks or merge validation; focused race suite
`c9beeffd5bff46c28c898a4d0fb211f6` passed. Wrappers and merge currently use
default observation limits. Python indexed domain sampling now uses a stable
snapshot across user RNG callbacks, and lifecycle classifier events are copied
before subsequent callbacks; six modeling tests
`d4f6bf98905649379e636340412084ef` and 13 stateful tests
`2ec11b3a6bf14140ad206ef349f93780` passed.

TypeScript mutation-suite assessment now requires replayed evidence for every
declared fault; build `00ec7733773e437cba0502a456a9020a` and 26 tests
`741d8b3306804a27b630e5bd86e8d6a1` passed. This is mutation coverage, not
independent catalog review. Swift research equivalence now detects encountered
asymmetric comparisons and reports incomplete; 14 tests including 67 shared
fixture rows passed `2541968b286348939f50bac5e980f491`. It does not establish
comparator transitivity or purity.

Go assurance manifests now expose detached domain-assumption and required-
property declaration helpers without fabricating evidence; focused race tests
passed `ef03c9e1b3304f0bb6170fdc33d02c6d`. Python pair/network lazy input
enumeration now snapshots state at iterator creation; 14 stateful tests passed
`306bd7402c1f4c5ca181fc6988e510ff`. TypeScript mutation suites now accept
cooperative controls and an aggregate retained-artifact cap; over-budget pairs
remain not evaluated. Build `dc841e29bbb44e8185e9cc141bc4e411` and 26 tests
`319de0a08146429095cc8dbc5c6d5b16` passed; the cap excludes transient allocation
and controls are per evaluation rather than a suite-wide deadline.

Swift research equivalence now bounds projection comparator calls explicitly,
including relation validation, and reports incomplete when exhausted. Sixteen
tests `da64a07126a14f0b9448926d1c126a46` and boundary tests
`9a47ffaa7e364147be0e8a08c63529e7` passed. The bound does not cover graph
enumeration, callback cost or RSS. Independent static review found no concrete
transitivity defect under the documented pure-comparator assumption.

Go added bounded standalone assurance manifest JSON import; focused race tests
passed `2ac72efe7c58429b9a100878250970bf`. Python lifecycle classifiers now have
a configurable atomic per-transition event limit (default 100,000); 15
stateful tests `79cf9cd536a0473d9822b2cad735308f` and eight paired tests
`3d410b6bb81d41509dc13aa27387d215` passed. This is a Python-specific bound,
not a callback-allocation or RSS bound. A separate static review found no new
concrete Python pair/network iterator ownership issue.

TypeScript now snapshots direct assurance reports before assessment, export and
merge, and validates metadata, scalar text and budget fields; build
`7973ed191dab41a19561e8edc8da044d` and 88 tests
`88c20e3150b243bb99d9fbdc0d149027` passed. Direct in-memory snapshot size remains
unbounded. Swift research now separately bounds exact-state comparison calls;
17 tests `8ad857511abd4e948924209daadbfe29` passed, including shared rows and
exact/one-short controls. The limit does not bound wall time or memory.

Go assurance merge and merged JSON import now accept explicit nondefault
admission limits and retain them for reassessment; focused race tests passed
`1b8f34ec890f4da7a2ab520b208027ef`. Run/campaign wrappers still use defaults.
TypeScript direct in-memory report snapshots now have configurable structural,
text and depth limits with cycle rejection; build
`c36e968b076243feb8e60b56ccfcee6f` and 88 tests
`15a296f4dc8843a8a2daea68de3a6e82` passed. Source allocations and process RSS
are outside these limits. Swift network channel validation and pending action
enumeration now precompute channel counts/FIFO heads while preserving order and
lazy child inputs; 13 tests `c64b89a2fae9486c81f267940c582984` passed; no
benchmark was run.

Go strict assurance assessment now follows reference reason ordering and status
details; focused race tests passed `1b76ae7b3f004b419ed763a0600b8649`.
TypeScript merged assurance import and assessment now use caller-configured JSON
limits rather than unrelated defaults; build `8124528fb1a04a2a92a900fe716d0808`
and 88 tests `94a8a86dac1744c19df34cca6f443820` passed. Python watch status now
exposes schema, resolved type, enabled state and retained bytes with detached
responses; 21 watch tests `f945e1188c154ee0b368fd65a4ff8568` and 14 protocol
tests `1ba02a546b2c4a6480f1ec5143d4aa6a` passed. Python's untyped integer wire
extension remains a distinct compatibility gap; independent static watch-health
review found no additional concrete accounting defect.

Swift watch status now returns whole-entry truncated pages under field/frame
limits; six tests `5a0ac3c78bc5405197efe4adaac0eaca` passed after an initial
fixture incorrectly mutated an attached registry (`eacd4488cdad4d0abf7a1d7a530f8bd0`).
Python currently returns a limit error under this constraint, so wire parity
remains open.

Python watch status now returns the largest complete row prefix under field or
frame limits, with health and truncation preserved; 15 service tests passed
`15a61098e3b2498f83a7b95a295d7003`. This aligns the Swift behavior above;
Rust currently returns a limit error for field overflow, so cross-engine wire
behavior remains different. Swift watch registry now retains ordered active IDs
to avoid repeated sorting; 16 tests `7ab597be9dd44954bd5997bdb0f9543d` passed.

Rust and Go watch status now also return whole-row bounded prefixes with total,
truncation and health. Rust gathers lightweight bounded summaries instead of
cloning all configurations. Rust initial compile-only job
`a806b2a607144c6cb9306a94a0d0a093` failed on a corrected type conversion;
compile-only `3e8a81e705c448feb3f3449f75f9908d` passed, then the actual
compiled protocol suite executed 31 tests successfully
(`eeed52737b9545bf82739c3b138e051f`). Full Go debugger suite passed
`f7451e86237c4ecca301d110e55bafbd`. Rust's conservative byte reserve can yield
a shorter prefix than exact-fit native encoders; complete cross-engine wire
qualification remains open.

Python untyped integer inspection now uses an exact decimal-length boundary
after a conservative precheck; 11 tests `ae2f1ccad4c5473894e99bbc04389ded`
passed. Untyped `python-int` remains a distinct wire tag; explicit IntegerView
provides a specified width. Swift lifecycle history now rejects over-limit
collection counts before decoding entries; eight tests
`29899c0f819a47e09c537f180160efb2` passed. TypeScript campaign JSON now retains
Rust-shaped error termination details (build `52ad52001927414b978d3c86ab567e39`,
88 tests `a49c7da3471540bca9a893a5340441f3`); exact Rust debug escaping and
independent fixture qualification remain open.

Python diagnostic sink admission now holds its guard through permission copying;
21 tests `4d871a82f79143389f208ef0e5b63645` passed, including reentrant and
failed-copy retry controls. Go effect request validation now precedes clock
sampling; focused tests `a632259b7cf64ca6afca2188c300a843` passed. Other Go
effect lifecycle calls still sample before phase/handle validation and need a
concurrency-safe design.

TypeScript dashboard construction now snapshots metric and host-health evidence
before label callbacks, preventing a false completeness claim; build
`4e2753bd1d6143bfabba91f5c556ae40` and 11 tests
`d65223af2cf943b291b791b5977f05ee` passed. Swift value/evidence decoders now
check native array representability before reserve; six value-codec tests
`3d7270ec943b4dfe94e62348f7a6e3a6` and five evidence tests
`ea251ebc4da344bab1456590e867b2e0` passed after correcting an invalid initial
test assumption (`a5051f9ffb7047d89f4421dc3d5d4092`). Vector/evidence native
overflow guards were not directly exercised; representable allocations can
still fail under host memory pressure. A static TS watch-status review found no
additional concrete defect; it was not a qualification run.

Go nested assurance aggregates now carry an explicit nested-job proof extension
and validate it against visible evidence on import; focused race tests passed
`898a7faccb57447eae68b3e7625717bb`. The extension is Go-specific and is not
cross-language qualified. Python protocol step/select/cancel now preflight
acknowledgements before state mutation; 26 tests
`048b50a9e27644849b635c2bebfc900c` passed after correcting a test session ID in
`1f3be65481e54894b463cbec32f4bd5d`. The new boundary test covers step; separate
select/cancel boundary controls and extreme-frame Rust parity remain open.

Go reference checks and coverage now reject malformed effect, disposition and
phase variants while retaining structurally valid property-invalid states; full
reference race suite `46f4a53d42524d769ae9502d434b73fa` passed. A static review
found no additional concrete Go assurance JSON admission defect. Python session
recording failures now remain normalized even when exception formatting raises;
14 session tests `ff9afb7341f74bf48806a20f2d3ba341` passed.

TypeScript mutation-suite verification now owns one bounded artifact snapshot
for validation and assessment, rejecting duplicate names; build
`64a19fedb0d44c84beaf2b0172068b26` and 26 tests
`7033570abc5b4ccc92bdc7eeee43259e` passed. Caller allocations and concurrent
SharedArrayBuffer mutation during copying remain outside the bound. Swift
research now checks reflexivity and encountered symmetry of exact-state equality;
18 tests `28caaa061c554baf816bb9526d8fe833` passed, including 67 shared rows.
These checks do not prove comparator purity or behavior outside the finite domain.

Python now has separate select/cancel response-boundary controls (27 protocol
tests `826d566bb69f44059403549ab3215586`), and core callback/snapshot errors
remain normalized when exception formatting itself raises (13 session tests
`d256b9c2be484cb8b961a15cef5f38a6`). A separate static protocol preflight
review found no additional concrete defect; extreme-frame Rust parity remains
open. Go assurance report and manifest import now apply configurable observation
admission limits before constructing evidence maps; focused race tests passed
`206da42beaf440fbae6ee3acb7fb4fc5`.

Swift probe-configure response fitting now binary-searches whole producer pages
before commit; seven tests `dc97377811b8485f8e599e5cac02b30f` passed, including
200 producers and a 450-byte frame. No benchmark was run. TypeScript public
assurance manifest validation/export now uses bounded single-read snapshots;
build `3c6273b399824d2ca2ecabcdbf595ccb` and 88 tests
`39fb2fef5c844dd88b0f3210ef7e6c9e` passed.

Swift probe status now uses a bounded producer page and rejects negative host
limits; six tests `7785348c69dd4825b4d8a835a6355d72` passed. TypeScript reference
witness verification now offers an explicit expected-build mismatch policy,
while retaining model/codec identity and replay checks; build
`c6029a331a6142ee8af28e360c7fb4f2` and 26 tests
`2d69c3db79b446bf9b2635e6f380ba6b` passed using synthetic foreign builds. Rust
interoperability and per-witness build provenance remain unqualified.

Go direct assurance merge now bounds job count, logical evidence bytes and
nested proof depth before cloning; focused race tests passed
`3d2280dc0e99454f9444e87c6cd0cdaf` and follow-up
`6c67a9dd4c2d4645988582f9121f955a`. TypeScript report-store entries now return
detached snapshots and retention overflow fails closed while delivery continues;
build `fff0b92c29dc4070a34dd21108b50463` and 88 tests
`7b1eef231612411aa24bcb6308a5154f` passed. Logical and serialized limits do not
bound callback allocations or process RSS.

Python watch update/pause/resume/removal now retains effective boundaries and
proper lifecycle state, and lifecycle classifiers receive detached dispositions
so they cannot alter oracle transitions. Kyoto passed 18 watch tests
`60b8cb4ec0364ac889fee1bc5a03b746`, 14 protocol-service tests
`00501db066b84ef39c961fd21d55331f`, 16 stateful tests
`b19dfa9512374c55b65e513df8913d49` and eight paired tests
`94d09f927a0c4c88ab816b5f21a139f8`. An earlier stateful job
`92131e233a7a4ff7bb327a5144632376` failed due to a corrected fixture.

Go coverage classification now uses bounded immutable family/goal indexes;
focused race tests passed `e4063c3f90e3493e985a591c8b0ba8d0`. Python assurance
observer finish is now single-use like Rust's consuming API; 41 assurance tests
`6ad6fd385dc6436a9cedf44d38526898` and 17 controller tests
`2ca724393e7f42518a2ad97c107a5f27` passed. TypeScript reference mutation
evaluation now forwards cooperative search and replay limits (build
`25abbb588b5046e0ab256a089dd08d6c`, 26 tests
`cc860505361f49e1be5343dcc3cb9ace`); suite-wide preparation still uses default
controls.

Swift campaign teardown now remeasures replacement reports against the retained
budget; 20 tests `0eb39482f4bc45a795f841db68d2b587` passed, including exact
and one-byte-short controls. Swift research equivalence additionally checks
transitivity among reachable keys; 15 tests `41139841aef9425da93c984819987cc4`
passed. This adds quadratic comparisons and does not prove callback purity.

Go assured enumerate, fuzz, guided and campaign APIs now accept explicit
observation limits while default APIs preserve their defaults; selected limits
are recorded and used in aggregate proof (race job
`bb1744e579e945de9c3bb2ac3dc4f95f`, import control
`b6bec49b28f8426aa971a3411c8664ab`). Python watch health now records sampling,
filtering, truncation, eviction, gaps and inspection failures; 20 watch tests
`a08814af50114690b23e15c0bb7b56f6` and 14 protocol-service tests
`b56e70539f6f456e8ce43f616d834bff` passed. Broader N12 qualification is open.

Swift network-state decoding now uses canonical-list binary search without an
extra machine-ID array; two codec tests passed
`818d0ee5a37d43c4b00462a4c2431e9c`. No benchmark was run. TypeScript campaign
retention now snapshots admitted reports and derives merge snapshot limits from
the campaign retention budget; build `69a62d31ca80436eb6206e4e3d22ec41` and
88 tests `5b23d437a79f4479a6e4c80818f1237f` passed. Limits do not bound RSS or
temporary serialization. A separate static review found no concrete FIFO or
channel-count regression in the Swift network precomputation.

Python protocol inspection now holds the session guard during application
projection, preventing nested steps from mutating a snapshot in flight; 30
protocol tests `39d2c321e2874e84a491a72b2c705a83` passed. Independent static
review found no additional concrete mismatch in Python inspection wire fields.
TypeScript watches now snapshot validated configuration and observation context
before projection callbacks; build `cda17299c26842cc9e7390968ac570b1` and nine
tests `a80525e77ec847f5974159dda9a78827` passed. Swift map-key authorization
controls passed ten watch tests `81775f6555ed4349b56166414c670088`, including
legacy/typed aliases and byte-distinct Unicode denial. Go effect ownership review
found no additional concrete issue; Go destructor/handle telemetry differences
remain open and must not be represented as invented zeroes.

Selected inspection paths now reject ambiguous duplicate sibling identities
before invoking selected-value callbacks, scanning within the work budget. Swift
passed nine tests `5ad1a6fc0aed4ef499d505f056146149`; Go's full debugger suite
passed `28e9bbb1c00c4bf5b8528df5a6c5ef38`; Rust compile-only job
`f46e25250c614212b743368d66c9408e` succeeded and its inspection binary executed
12 tests; Python inspection `29cf6c78670c43e2991cd5975dd3047b` and protocol
`e10429b7ec674ce99924576d89887ddb` passed; TypeScript build
`48e364c311294e95b403d509283a509a` and 11 tests
`7d70333cd327489690fc0aeace74f482` passed. Unique early matches may require
more work because uniqueness requires scanning sibling metadata. Go additionally
rejects malformed child segment variants (`9ff66bfb66144333b05762f1ca1c5b4d`),
and Python rejects unrepresentable custom projection shapes
(`d39b78e50baf465e98d00a8ef6769771`, diagnostic
`b26d68a7315f4380890ef8d9665d75a0`).

TypeScript watch_current now uses direct ID lookup rather than cloning all watch
configs; build `0e92d25bbc634de9b69657d35db44be2` and 22 protocol tests
`91453dbc7e4b407dab5c02fbf3afabfa` passed. Independent static review found no
additional concrete duplicate-completeness discrepancy across the five ports.

Python inspection adapter now emits the Rust protocol schema/snapshot/node field
layout instead of its previous inspect.root layout; 29 protocol tests
`c8e94b31156549328c2e8a2bd92df579` passed. This is a wire change; an executed
Rust-host differential and Python's untyped integer tag remain open. TypeScript
inspection now snapshots and freezes the request and returned schema before
callbacks; build `93d286611a1c4f048f26ecf4b9b342b8` and 12 tests
`2135ffe57fc64abeade757957e0798f7` passed.

Independent Go effect review found selected-attempt pointer mutation during a
clock callback could change timing attribution. Resolution now snapshots that
handle before preflight; focused race tests `dc2b1ef2fd3545b09f7f4ec5847483ca`
passed. Swift legacy and typed string map-key paths now share exact UTF-8
identity, matching wire encoding; ten inspection tests
`fde1ff48afd44be89e0f0fa23b5e2fd9` and 16 watch tests
`c3c89ef1e68f4ad6869e4f0f4a1e9f42` passed.

Implementation continuation: Go and TypeScript now expire watches immediately
when the final selected observation is sampled out; Go also expires immediately
after an evaluated final observation and emits a single payload-free lifecycle
event. Go debugger suite `368cdc67cee2486eb9211df9d4f281e4` passed after the
final changes; TypeScript build `534c0dbbea024cd3b931bf10308f8141` and ten watch
tests `cd278ab685e34c16b95f9c2a13887caa` passed. Python and Swift already had
immediate sampled-out expiration; regression tests passed in Python
`93ed614fd3584d31b2a7dd7f9fd0e694` and Swift
`bfb91c2c007b445ab504f744c6378d3c`.

Python inspection protocol now maps typed InspectError to invalid_input, with
31 protocol tests passing `cd7c08e3e0dc405198ebe15f01b9f9a7`; its inspection
query budget defaults still differ from Rust and require further work. Swift
protocol handles cache size overflow for unencodable responses, with 11 tests
passing `c3030792522746ae9904be25a1a8f523`. Go inspection completeness rejects
malformed scalar segments and invalid UTF-8; its debugger suite passed as above.
These focused results do not establish full native parity or qualify the PR for
merge.

Python's built-in inspection protocol adapter now derives the Rust native
inspection work, depth, node, frame, field and page budgets for each request;
explicit host bounds can tighten these limits. The protocol suite passed Kyoto
`4d06ba5800f94d1eb0ebd3f840fe1a66`; the earlier
`2ae666321e7d4ed1a1ebd9d140bedd62` failed due to a missing PYTHONPATH.
TypeScript watch attachment records an observation gap when initial projection
fails and resets its baseline on a subsequent successful observation. Build
`a894ebe2c45f4fe18a733f3fb165be2a` and protocol tests
`4b480d60b35c40efbc66d1108d8344bf` passed. The TypeScript reference evidence
reader opens files nonblocking before checking they are regular, preventing a
FIFO from hanging the reader. Build `33e3b7d99aa545ceaadbbc6a6ae8401f` and
reference-controller tests `e62e5d2ce85047b9b5baf346713b9fa8` passed, including
a timeout-bounded FIFO regression (skipped on Windows). These are focused
checks, not full native parity or merge qualification.

Go now provides an opt-in protocol inspection adapter with explicit state
projection and Rust-aligned protocol budgets. Focused test
`251d4666db684599b713f58cd8154739` and full Go debugger suite
`ff177ec3fbde408c82820a112f59c2d5` passed; cross-engine wire qualification
remains open. No analogous Go production filesystem suite reader was found.

Python witness verification now requires exactly one matching mutation and target
and the reference-fault-control strategy, and opens regular files without
blocking on FIFOs where the OS exposes the flags. Reference-suite test
`bc7b784cfe6c411ba8a07a802c71a76b` passed with fresh-process and negative
controls. Swift suite summary and witness readers now reject symlinks and
nonregular files without blocking on FIFOs. Focused Swift test
`b843eef4d67948ab8d78d31f86e20a20` passed; prior job
`b414bf3a885b4b00a3fb2d415c1f0cce` failed after its test was edited during
compilation. Static TypeScript guided and campaign review found no additional
concrete issue in the inspected boundaries; it is not qualification.

Python observed checking snapshots and validates its policy before invoking
callbacks, preventing mutation of the policy from suppressing transition checks;
Kyoto append-check suite `d3b292cac85349a5a8335819b4bdba11` passed. Independent
static review found no concrete regression in this change.

Go protocol inspection now treats schema metadata separately from the node
projection byte budget, as Rust does, while enforcing the protocol field bound.
A default-limit 8,185-byte schema regression and the full Go debugger suite
passed `5cc5d4676aea442698e4a4d1cbe4682c`. This supersedes the earlier
`ae41fc3cab1e4cad937ca93560a327de`, which preceded the corrected boundary.
Independent static re-review found no additional defect in this schema-specific
change. Scoped Swift N01 review found no concrete new defect; neither static
review constitutes qualification.

All five engines now bound globally repeated already-seen simplifier hints: up
to maxAttempts are skipped, and a further duplicate ends with attempt_limit.
Replay attempt counts and historical records remain unchanged; finite iterators
ending exactly at the cap can complete normally. Cancellation is cooperative
inside callbacks/iterators. Go focused shrink and golden corpus passed Kyoto
`07946c5bdd6241f5a1da541002b6a336`; Python search and corpus passed
`f54dbc0defe1453eb0beedbdebd2c92c`; TypeScript build
`607ad2d295ef4b0abb3bfaf40c53ad45` and search tests
`dc5d0331d9fd457f932ed23a6fe31534` passed; Swift 13 SearchTests passed
`9a441048ea28422783b9ace170013e8b`. Swift prior job
`d005c331f784441ea4c49d9bdae28f5c` failed after test edits during compilation.
Rust Kyoto `8e1e618d1ad24f489e0123fa214f9f29` was compile-only; the resulting
binaries were then executed separately and 29 exploration tests plus the native
search golden corpus passed. Independent static cross-port review found no
additional concrete defect. Python also snapshots Auto's candidate bound before
callbacks; the same final search suite covers that regression. These results do
not qualify the complete N02-N04 slices.

Paired oracle ownership: Go, Python, TypeScript and Swift snapshot model state
before independent oracle initialization/decoding callbacks where needed, and
snapshot observation inputs before history cloning. TypeScript and Swift use the
retained transition for oracle observation; Python already did so and gained a
regression. Go also isolates comparator arguments and snapshots output slice
elements before state-clone hooks. Focused final Kyoto results: Go oracle,
paired and search `c4ad0fc485e5415db9efefea88b0f30b`; Python paired
`62130ed08181463198ddb0bb5783128c`; TypeScript build
`af275671e8464bce8dca069436668a68` and paired
`e7c37ae0b29f40f998a514c7bbf224f9`; Swift OracleTests
`6fdd58c061e544eca598e905b84ddc59`. Python ordinary fuzz now snapshots config
and run limits before callbacks; search suite `cbcc02df910d405fa2d6604c8ff4fafa`
passed. Go earlier job `c9c10b6391c14c4cb1035cf4ac128d08` failed on incomplete
test fixtures and was corrected. Independent static review found no additional
concrete regression in these changes. Clone hooks remain application callbacks;
arbitrary cross-component mutation inside them cannot yield an atomic snapshot
without stronger host ownership guarantees. Go ObservationError retains the
original transition if cloning fails. These focused tests do not qualify N02-N06.

Raw recorder observations now reject absolute sequence overflow in all five
engines before host callbacks and preserve the valid retained prefix. Focused
Kyoto results: Go `96bcb9566f3a4cf485109e064f2e2894`, Python
`5cbcb6e637d6470b8f54346ad90103d8`, TypeScript build
`c7fe48b32f254423a14b6be38f949fd6` and recorder tests
`e8393b69d2df4c70a7fa86dcff247e28`, Swift RecorderTests
`5fadd548511447019e1af0bf43868ec9`. Rust compile-only job
`6b596756ae114573b4d336aa2290918d` was followed by execution of all four
monitor_review tests through Kyoto `d93e112be35542899cd3f01a1dd59f56`;
checked_replay boundary regression also executed after compile-only
`0a53af6d4ca847e09733fb3c95e605ee`. Independent review caught and confirmed
correction of Rust and Swift callback ordering.

Python network now snapshots queue policy across step callbacks and lazy input
iteration, with stateful suite `0de28b3a5869415cb7a77a51d379dc3e` passing.
TypeScript snapshots network disposition before routing, and admits state and
policy before lazy input enumeration and step callbacks; final build
`84c8d147b34340ee8c0c01137bbc7d7d` and stateful tests
`c7cf16ded09c4ddda055a51c043d1a15` passed. Go pair/network state isolation and
recorder controls passed `96bcb9566f3a4cf485109e064f2e2894`. Independent
static re-review found no additional concrete defect in the corrected callback
ordering cases. These focused results do not qualify the full N07-N09 slices.

Go reference mutation suites now have a bounded artifact verifier: it validates
report and witness identities, rejects malformed/duplicate JSON and unexpected
artifacts, and exactly replays both witnesses for every claimed kill; valid
incomplete suites remain inadequate. The writer cleans up owned witnesses on
pair failure and publishes the report through a synced, exclusively created
temporary file and no-overwrite link. Injected failures and final reference
suite passed Kyoto `b3cc078871154ff8b4b72eaf7a2f9819`. Independent static
review found no additional concrete defect under exclusive directory ownership.
Directory fsync, crash durability and concurrent external path replacement are
not established; loading artifacts from the filesystem remains caller-owned.

TypeScript now has a runnable reference-controller package example exercising
assured search, a named missing-cleanup fault, shrinking and exact replay. Kyoto
`0c455394e6f943668b30a62eedec2f6d` passed: 168 states, 3983 transitions and a
two-step witness. It uses package self-reference and is not an installed-package
qualification. Python research rejects nonreflexive exact equality and observed
asymmetric comparisons, recording incomplete evidence; research suite
`3c8ade124f82484585416e9f33311a18` passed. This does not prove transitivity.
Swift exact integer duration enforcement remains open: current controls use
TimeInterval and must not claim arbitrary exact nanosecond deadlines. None of
these focused results qualifies the complete N10-N14 slices.

Swift direct search RunLimits now supports exact unsigned 128-bit nanosecond
budgets using integer monotonic elapsed tracking, with exact direct-assurance
budget reporting. Existing seconds APIs remain; campaign limits are still
seconds-based, and debugger deadline paths reject the integer form explicitly.
The cooperative integer clock assumes fewer than one UInt64 clock cycle between
polls. Kyoto AssuranceTests/EvidenceViewerTests
`5528bde710f7495e91b5b37d47154551` passed 31 tests. Independent static review
found no remaining silently ignored direct RunLimits budget in reviewed paths.

TypeScript gained opt-in protocol inspection with explicit projection and
revision/handle ownership, ordered page fields and separated protocol path/node
budgets. Schema names are bounded in UTF-8 before wire-field construction. Final
build `3b8905b5f4fa48dea49864ddacd4e618` and protocol suite
`a105f81fd5804aada51388fda9dc6a7c` passed; inspection plus protocol suite
`bea712251b8441a8a117885e2e25b1e4` passed before the final schema preflight.
Independent static re-review found no additional concrete defect in those
cases; exact cross-engine byte-accounting boundaries remain unqualified.

Go now has a bounded reference-suite filesystem loader for macOS/Linux, using
nonblocking no-follow opens and regular-file checks before exact replay. Kyoto
reference suite `554556f96d57426c84e0eff411c03fe9` passed on macOS; Windows
cross-compilation `7970933fcdff4910895e3905bc827f23` succeeded but the loader
explicitly rejects unsupported platforms. The caller must prevent directory
replacement while loading because child opens are pathname-based. Python oracle
append checks now snapshot both checker inputs before either callback; paired
suite `d305b230a5fc4e2c9cd74a5211be38b7` passed. These focused results do not
qualify the full native parity plan.

Swift CampaignLimits now admits exact u128 nanosecond deadlines, shares elapsed
accounting across workers and propagates remaining budgets into ordinary and
guided searches. Assurance records total and per-job budgets, including feedback
setup time. Kyoto `4928a80ba7344e5c8fddf29276119061` passed 40 assurance and
campaign lifecycle tests. Independent review found stale duration provenance on
a seconds-budget rewrite; that was corrected and AssuranceTests
`22723e4b86664a2488b91b18cd7a909d` passed, with static re-review confirming the
specific fix. Deadlines remain cooperative and assume fewer than one UInt64
clock wrap between polls; offline debugger integer duration is still rejected.

Go now has a runnable reference-controller example driving the actual async host,
two gated workers, supersession and cancellation, recorded evidence and exact
replay. Kyoto `dcbf0fe7f3624a71a51013b31c6c7be0` passed. This is local example
execution, not installed-consumer or platform qualification. Python and
TypeScript capability reviews found previously logged features already present;
they did not establish complete parity or add changes in this pass.

Swift offline evidence verification and minimization now support exact u128
nanosecond deadlines, sharing the monotonic deadline across verification and
passing the remaining budget into shrink. Kyoto AssuranceTests/EvidenceViewerTests
`5401371c3a3c43c7b5f2c2b45bf0a5f8` passed. Independent static review found no
additional concrete defect; callbacks remain cooperative and the integer clock
assumes fewer than one full UInt64 wrap between polls.

TypeScript now provides an opt-in Node framed stdio protocol adapter. Real-pipe,
output-failure and late-error controls passed with build
`f73adb1ea6dd472eb6583b73d7212909` and protocol suite
`b0c9d07927cf4c6ca6394cfab1ccf4bf`. Independent review caught and confirmed
the late-error fix. If output fails while input is idle, interruption of input
remains caller-owned.

Go reference-suite reader now includes a Windows local-drive implementation
with final-component reparse rejection, device/drive checks and bounded reads.
Windows test target cross-compiled in Kyoto `202e95626765481daa76f227c2969533`;
Windows runtime tests have not executed. UNC and mapped remote drives are
rejected; callers must ensure intermediate components remain stable and resolve
to suitable local storage, and synchronous filesystem calls may block. Static
review confirmed the documented limits. These results do not qualify platform
support or the full native parity plan.

Swift now offers opt-in protocol inspection with detached projection, bounded
query and standard node fields. Final ProtocolTests
`548cb0e1575740609f37385f4fd81b42` passed, including stale handle/revision
precedence. Independent static review found Swift responses sort dictionary keys
while Rust emits semantic insertion order; exact response wire-order parity is
still open across Swift protocol, including fitting and cache behavior.

Go suite verification and Python single-witness verification now allow foreign
builds only under explicit exact build pins while preserving model, property,
codec and target replay checks; strict local identity remains default. Go
reference suite `8cea6badca114453a527096b02fff10e` and Python reference suite
`dcdb18bea00245aa8355c6638eec7a26` passed synthetic foreign-build controls.
Independent static reviews found no additional concrete defect. These synthetic
identities do not establish actual cross-engine interoperability.

TypeScript adds a Node reference-suite create/verify CLI. Its bounded reader now
stops directory enumeration at the 20th entry. Final build
`42020852f03a4d70b5ca201884f6d22f` and reference-controller suite
`92601dbee62c41119de2dcc1d780ae2a` passed; independent review confirmed the
specific entry-bound fix. Installed package qualification remains open.

Swift protocol now carries ordered fields through construction, encoding,
decoding, bounded fitting and caching while preserving dictionary APIs. Core,
inspection, watch, probe and telemetry builders use semantic ordering; final
combined Kyoto `619e654d9fac42c5a52e030689f3bf2b` passed 41 tests. Legacy host
bindings use sorted fallback. Rust effect responses emit duplicate
`effect.debugger_affected` fields while Swift's dictionary collapses them;
exact effect wire parity and full response differential remain open. Independent
static review found no other concrete scoped ordering regression.

Actual cross-engine controls: Rust produced a reference suite and Python replayed
all 18 witnesses in a separate process (`0a85f6adec21448d9d032f01abb25727`);
runner admission controls passed `31526cccb09c487284451a086f9bdf71`. TypeScript
produced a suite and Go replayed all 18 witnesses (`db1abe34ed8f45b086cf47fbd4164b82`);
strict pin-parser controls passed `9d0b519f71bd4da0ab5b4c73c487670f`. Pins are
caller assertions, not producer authentication. Rust and TypeScript scalar
inspection plus schema-error responses matched byte-for-byte in executed Kyoto
`6b6118fd015d42fdaa3b322147e4931f`. Independent reviews prompted and confirmed
bounded/strict runner input fixes. These are specific local cases, not complete
cross-engine qualification or the full producer-consumer matrix.

Effect protocol wire correction: Rust emitted two distinct values under the
same effect.debugger_affected key. All five ports now emit the original metric
label as effect.labels.debugger_affected and the aggregate effect state as
effect.debugger_affected; protocol documentation records the compatibility
change. Rust compile-only `f2153f1022df40eeb464e295a66608b3` was followed by
one executed differing-value/decode regression; Go debugger suite
`2dda9c857f2d4e4cb38e39fb49fa56a8`, Python effects
`e13ee73b8e344de79d9fe13a0c80bd50`, TypeScript build
`d01d98896893422ab85a28710bb444a1` and effects tests
`690d0689e88e49c8b0ba2352287768d5`, and Swift telemetry
`54f72842062440069f0e98e77434bda5` passed. Independent five-port static review
found no further concrete discrepancy in this correction. A full effect wire
differential remains open.

Actual Swift→Python reference witness replay passed for 18 witnesses from fresh
Swift producer `49a04f4160a84b95bb39926a6f33173c`, Python consumer
`d4cc799ae353412e9bfe8fd9216029b6`; controls
`c55fcb0d7b674c72a44bb9d944e5d09a` passed. Actual Go→TypeScript final producer
`4645323b42084191933f6ae544c2af2c` and consumer
`636f0126f0204b6caeaab6a47330b297` passed; wrong-pin negative was rejected in
`078f4eb210bf4083bc5578ab5ead0c6f`. Producer pin failure controls passed
`7ef4f62a9ab04b98847123c3bbced7a7`; pins are caller assertions and direct pin
publication is not atomic. The Rust–TypeScript inspection wire differential
`3d73bcb503b04842b4f869788b28e67f` passed four cases including nested pagination
and selected path. These are bounded local cases, not the full matrix.

Rust and TypeScript effect-details and truncated-timeline responses now match
byte-for-byte in live differential `21e9bf5c18604f4f926174dcb2b124dc`, with
original label false and aggregate true. The initial differential
`0d1c89af071b4ad589d557f1b18ef1f5` exposed TS timeline field ordering; it was
fixed and TS effects `bf87382fcc334edd9a4182a3bfa8a4bc` passed. Independent
static review confirmed the fixture compares the independently emitted frames
without field normalization. It covers only those two effect cases.

Rust–Swift inspection ordered-body differential covers paginated object, scalar,
empty and past-end pages, missing path and schema mismatch; volatile envelope
headers are normalized. Swift gained lazy paginated object projection, corrected
scalar child counts, page offset clamping, and an opt-in separate schema
preflight preserving the projection-only API. Final Swift focused job
`a5db08a328c94ae79ae754db24c9c778` passed. Rust fixture compile-only
`26fc78f2e86b4a74b011138672caca81` was followed by one executed verification;
fixture SHA-256 is 550340a9f718420b2bc69ede970abdb44034c7d8964c0d8ef20f0934fbcaf81d.
Review caught schema callback and error-precedence differences and past-end
pages; fixes were independently re-reviewed. The legacy projection-only adapter
cannot preflight schema without invoking projection.

Fresh Rust→Go reference suite: Rust producer `da070a48f72d48d0a5712dc949b3ac5d`,
pins `f269ac1445484494abff11a9ef334a91`, Go consumer
`781774f71b6045c39515d777ddbb4af8` replayed 18 witnesses; wrong-pin negative
`fd3ded9d0d3347f499e19253d634f9be` rejected. Go→Python producer
`35e3597fbfc34aeebe514c4e46e9a84d`, Python consumer
`756fc04b76054612b0034817628908c6` replayed 18, and runner controls
`eec7dd26f5604bbb9dce995e74603c0e` passed. Pins identify producer builds but do
not authenticate them; Go artifacts do not embed original/minimized kind. These
cases do not qualify the full producer-consumer matrix.

Rust–TypeScript bounded watch-status live wire comparison
`5da75ce1187943c39adab378224e0a44` passed for seven watches under a 32-field
cap, retaining five whole rows with total/truncation. Rust–Go inspection compared
seven ordered payloads against freshly executed Rust fixture producer
`bdf081c4aa49415793d6022a8a62c884`; Go consumer
`7b8cda89305e40419bcf8fe122d7d598` and full debugger suite
`27192a2b0b1e4190b79da19ecdea8a6c` passed after correcting Go scalar leaf child
counts. Eight process-specific envelope fields are excluded in that comparison.

Rust–Python seven-case ordered inspection differential passed against fresh Rust
export `473947be7aa24be18b1b91b568860837` and Python job
`936f349ab65541b89b880d0889cdbc68`, after Python past-end page clamping. Python
inspection now retains bounded partial container prefixes on child budget
exhaustion, rolling back node/byte charges while retaining work; inspection
`d2358a3a6fac4c789ac77fce34de6538` and watches
`687008193e7443ff870c81b777914073` passed. Independent static review found no
additional concrete issue in this slice. Python logical work and byte charges
remain different from Rust; exact budget threshold differential is open.

Rust–Swift effect detail/timeline ordered-body differential passed after
correcting admission and attempt-created timing attribution. Swift 23 focused
tests `58406c4fc7044f4f9bd207af11aa30f0` passed; Rust compile-only
`da36c079215046dabb26a26635954061` was followed by an executed fixture test.
Fixture SHA-256 21e884c5b57b1f543a7c4b4b22f11d4847b28b2d8518bac5e3f0786d92ad96a9.
Process-specific envelope fields are excluded; independent static review found
no concrete discrepancy in the exercised effect. These targeted comparisons do
not qualify complete debugger behavior or the full cross-engine matrix.

Rust–Go effect detail and timeline ordered outcome/payload matched fresh Rust
fixture producer `803b5f463d154238bdfae033a5a7e5d5` and Go consumer
`e15fb8b1f73c41d586b6c1f1957a7a71` after Go timing attribution correction;
full Go debugger suite `afe9ac907f494bdf93dca9b091a1439b` passed. Initial Go
`6d3270bf960d4df08c9d87b79665ee33` exposed the mismatch. Rust compilation
`6a8ac95af6394d1d94f1756f0d9f0211` was compile-only before fixture execution.
Independent static review found no additional scoped defect. Eight envelope
fields are excluded; only the exercised effect is covered.

Rust–TypeScript probe configuration/status live wire differential
`709252a3acba4f70b8375c41f477edb5` passed, including 40 producers and a bounded
32-field prefix. Rust–Swift ordered watch/probe status fixture SHA-256
f6b50e2c43f0d800acb7207cd26cda56a6cc6437e527bf6e2433896746f5bfba passed Swift
`6bfb210a069d4b44a7a45d8c98956caa` and executed Rust fixture test following
compile-only `ca95fbf7b3fd45bfb662135fc15980f3`. Swift comparison excludes
process-dependent headers and covers one watch prefix and one probe page.

Rust–Python node/depth differential passed five cases with fresh Rust exporter
`9bda043b4422483cbbc556892dc0a446` after compile-only
`979456e5bc644a6e910c0887e9b675b9`, and Python
`858fcd0bc38746a79563d92e312f0ab6`. It compares root completeness and page
counts/next only. Python byte/work accounting remains intentionally different;
no exact threshold equivalence or full debugger qualification is claimed.

Swift inspection at depth limits now returns partial metadata for nonempty
object, sequence, map and enum without projecting children; empty containers
remain complete. Parent containers propagate the first child partial reason
before pagination. Final focused Kyoto `fe78935917fe440c873163bfd38ad831`
passed Inspection/WireParity/Protocol/Watch tests. Independent review found
nested and enum boundaries, which were fixed and re-reviewed; this depth case
has not yet been tested in an executed Rust-host differential.

TypeScript Domain.sampleIndexed now snapshots/clones candidates before an RNG
callback and validates its returned index, retaining empty-domain semantics.
Build `c9799ffd4be04f5a82c46150034d0260` and modeling/search tests
`7e910ed5c40f4775a3e32f0757d50bea` passed. Snapshotting is O(domain size) and
application clone hooks remain responsible for their own cross-object mutation.
Independent static review found no further concrete defect in the change.
Go and Python capability reviews did not identify another sufficiently specified
missing feature in their inspected areas; this does not establish full parity.

Go direct enumeration, fuzz, guided search, shrinking and assurance now accept
exact u128 nanosecond RunLimits, enforce elapsed monotonic budgets and record
exact decimal assurance values. Root focused Kyoto
`0e2b0ae728b44e2a92cddf951bc2af03` and debugger rejection controls
`735bcd573cd0431ea0d501e12d78f9d9` passed. Campaign and debugger verification/
minimization explicitly reject the new form until propagation is implemented.
Deadlines are cooperative; clock accumulation assumes polls within ~292 years.
Independent static review found no further concrete issue in inspected paths.

Swift adds a lazy paged enum inspection overload. Engine budget exhaustion
caught and swallowed in a callback is tracked and returns partial metadata with
node/byte rollback; application errors propagate. Public typed
InspectError.budgetExceeded catches are preserved. An uncaught engine budget
error still propagates instead of Rust's partial-page result because the public
error does not identify its origin; this is explicitly documented and covered
by InspectionTests `088ce3ad57204870ba7b9dc4359ec7d0`. Focused broader job
`37c65f5340634e77bb156da0379e4caf` passed. Review caught a private-error API
regression and the final code preserves typed catches. Full budget parity remains
open.

Python research now checks encountered projection equality symmetry and
transitivity before reporting equivalence; a fixed one-million-comparison cap
fails incomplete on exhaustion. Kyoto research `6360085f5e494e21bbbaa4fd5099ea88`
passed, with independent static review. This is limited to the finite reachable
fixture and does not prove callback purity. TypeScript Domain.contains now
requires synchronous boolean equality and snapshots candidates; empty domain
returns without callbacks. Build `7b3478b46ba142a69ec665ce39d7ccb7` and
modeling tests `8b1989db3211444db17b91456b73f72a` passed. Nonempty eager
snapshot cloning changes callback/error order and is documented. These results
do not complete the native parity plan.

Go campaigns now share exact u128 elapsed accounting and propagate remaining
nanosecond budgets to ordinary/guided workers and assurance. Per-job assurance
records the exact limit passed after setup, not an earlier larger value. Focused
campaign/assurance/search golden suite `d45303b31153442db7e69362f2374b08` and
four-worker race job `2394455ce69d4b7cbb36b94a78af5a16` passed in Kyoto.
Earlier race job `491412b9acea4098bcfd078f27545ffa` failed to compile due to a
wrong test field and was corrected. Independent review found and re-reviewed the
per-job provenance fix. Debugger verification/minimization still reject exact
durations; deadlines remain cooperative with documented polling assumptions.

Swift inspection validates nonnegative custom child/page/string/bytes totals;
focused Kyoto `bf2dacf395dd44ada87cfface273c128` passed, and a static re-review
found no further obvious signed-to-u64 gap. Its uncaught budget-error partial
page limitation documented above remains open. TypeScript lifecycle now detaches
input-classifier events before output classification; build
`80161d84a63f496b861f4920fb2168b8` and stateful suite
`fe8756e3de544126b52ce1a786283738` passed, with independent static review.
Classifier event accumulation remains unbounded by retention limits.

Go offline verification/minimization now propagate exact u128 deadlines across
verification, v2 phases, decoding and shrink using one shared monotonic budget.
Full debugger Kyoto `6e017c1ddcbf4242a8206d4f8172a32f` passed after independent
review found the need for a post-replay stop check on early incompatible output;
callback/replay errors retain precedence. Campaign/assurance/search controls
`ff0e1dff676d4a0eb06d4d7fdf03892d` passed before that debugger-only follow-up.
Deadlines remain cooperative with the documented polling assumption.

Swift inspection supports separate stable field IDs and display labels; metadata
uses an independent 4096-byte bound before work/child overhead admission. Final
focused Kyoto `279a818aba1e479cba258630203a2622` passed, with review-driven
ordering corrections. The uncaught ambiguous budget-error partial-page gap
remains open. TypeScript LifecycleMonitor now enforces its private frozen bounds
regardless of public getter shadowing; build `d308311259ce40c1a1fba3e86e7289c2`
and stateful suite `b91a3085ae094de0ab0072803be379eb` passed, including the
review-discovered shadow control.

Python adds an opt-in configurable projection-comparison research budget and
reports attempted comparison count without changing legacy shared reports. Kyoto
research suite `638e8787eef54556b79f00389eee1b13` passed and static review found
no concrete issue in that scope. It bounds projection equality, not all runtime
work, and does not establish equivalence outside the finite reachable fixture.

Go now has a native port of Rust's demo RequestModel (fixed/buggy lifecycle,
ordered generation, checks and canonical codecs). Final focused demo Kyoto
`d64f243ad1df4e688ee2ea600fc3ec24` passed; independent source review found no
concrete discrepancy for representable inputs. Its static native build ID is
not source-hash attestation and cross-engine qualification remains open.

Swift object, sequence and enum/variant names now use the independent 4096-byte
metadata bound instead of scalar preview limits. Focused Kyoto
`f942638bf0ce41fdaa4bf3a52ca9d974` passed after correcting an old expectation;
review found no concrete new defect. Retained-tree byte accounting still differs
near limits. Python's opt-in bounded research API now separately bounds and
reports exact-state comparisons, preserving the legacy shared report. Kyoto
`bb835bf5bf9d4607870b6bc831dd8156` passed and static review found no scoped
issue; other callback categories and total runtime are not bounded by it.

TypeScript lifecycle monitors now reject asynchronous, nonnumeric and nonfinite
comparators and use a private validated comparator even if public properties are
shadowed. Final build `029b4a827ce0491984be153171f7e416` and stateful suite
`b8519c4067be4197b011d197a29a029b` passed; review identified and confirmed the
shadowing fix. Full parity and qualification remain open.

Go demo now has an optional debugger projection and the Rust→Go inspection
comparison uses the actual Go demo model, rather than a synthetic projection.
Focused debugger/demo Kyoto `a6f280786e1f49ac8937bbf5bf706f70` passed against
the previously executed Rust fixture. Review caught a bound that excluded
property-failing states; the final adapter admits the codec's full 255 pending
entries including such counterexamples and rejects larger inputs before copying.
This remains a documented projection bound for non-codec live states; it is not
an arbitrary-length lazy projection.

Swift opaque labels now clip to maxValueBytes on UTF-8 scalar boundaries and
remain Partial(Opaque). Kyoto `6630d3a3563d4e469758611e74f7bd59` passed focused
suites; independent static review found no concrete issue in this scope. Python
research default comparison now limits exact-state equality attempts to one
million and returns Incomplete on exhaustion; `81de1d236bb44d90b6939f41af767567`
passed, with static review. This intentionally changes very large legacy
comparisons from unbounded to incomplete and does not bound other callbacks.
TypeScript review identified no additional sufficiently grounded change here.

Go inspection now offers SequenceIndexed, constructing only requested page or
selected children. The demo projection no longer rejects pending lists over
255, and supports arbitrary supplied lengths including property-failing states;
the owned snapshot necessarily copies the input slice before query budgets, so
snapshot time/memory remain O(n). Independent review found nested page offset
and selected-depth differences; fixes and full debugger/demo Kyoto
`e6e29889e78d4f67bbd0bcf607aed24e` passed, followed by explicit error/panic
restoration controls `082cb5ea12d64a95905579b8b96b2812`. Re-review found no
additional concrete issue in that scoped implementation.

Swift exact bool/integer/float inspection scalars no longer use the preview-only
maxValueBytes limit; total-byte and shape validation remain. Focused Kyoto
`85c0d198cf784fcaba2d2065edc04b65` passed 67 tests; static review found no
concrete issue. Exact host byte thresholds remain language-specific. The live
Rust–TypeScript watch-current baseline response matched in
`0e24b65f1abd49048c460b79830752cd`, a single case rather than broad protocol
qualification. Full parity and qualification remain open.

Swift watch configs now carry pages, page-aware validation/observation callbacks
receive them, and projection cache identity includes page. Legacy validation
rejects nondefault pages before callback/mutation. Final Kyoto watch/protocol/wire
suite `ce713ce9a2fa4ea0983e01c581109ca3` passed; independent review found and
confirmed correction of page-unaware installation. Cross-engine page qualification
remains open.

Go inspection propagates included children's partial reasons to parents and
preflights nonempty container depth before child callbacks, including indexed
sequences and past-end pages. Full debugger/demo and Rust fixture Kyoto
`d61f02983b394ef7b7a3f0d2d9bb4b96` passed; review found the depth follow-up and
confirmed the correction. Python research now rejects ambiguous exact-state
matches after scanning its bounded declared domain (`7f346d7c6f4e49fd8d5ab59914809bd4`).
TypeScript research rejects nonreflexive/asymmetric/ambiguous observed state
equality; build `727aebccc8c64cf0850c8f47dec4a510` and suite
`610557f7e1004dd494ffdd75687ef072` passed. Static review found no scoped defect;
TS comparison work is quadratic with no separate comparison budget, and these
finite checks do not prove general transitivity or callback purity.

Go inspection now supports MapIndexed lazy pages and bounded selected-key
metadata scans, checks duplicate identity before value callbacks, validates
canonical numeric key ranges/spelling and bounds metadata before parsing. Full
Go debugger/demo and Rust fixture Kyoto `b6860d43532848d8a51ec74be5b837b4`
passed. Independent review found and confirmed fixes for numeric aliases and
oversized selected metadata. Accessor callback allocations remain host-owned.

Swift adds page-aware validated watch updates and clears stale schema on ordinary
updates. Add/update validation now checks schema/source bounds before mutation.
Kyoto `002a1f89216745a4bf066374bbae6883` passed focused suites after review
found the metadata admission gap. TypeScript research now exposes separate
bounded state/projection comparison budgets and actual callback counts, with
legacy reports unchanged; build `5b70587a75da436380c7a1e8868c0e2f` and tests
`44704701871e4722ab9f279b456387c4` passed after review corrected clone-failure
counting. Python research checks encountered input/output equality in both
directions; Kyoto `585259c36eca4f9c8cbd39dc928de3ce` passed. These finite
research checks do not prove general comparator laws or bound callback work.

Go ObjectIndexed constructs dynamic object pages lazily and scans selected field
metadata within work bounds, rejecting ambiguous identities before value calls.
Object/map indexed paths now validate bounded UTF-8 label/source metadata before
value callbacks; debugger/demo Kyoto `4d1b44763e35435c8dfa1c0695e489ed` passed.
Independent review found and confirmed this admission follow-up. Callback-owned
allocations are outside engine bounds.

Swift watch registry now exposes bounded ring snapshots and a separately retained
latest property-failure snapshot, plus clear APIs. Public snapshot rejects
reentrant reads during observation; private failure capture occurs after watch
processing. Kyoto `5e884f507c2f42cf8c9478af2bf451f2` passed; independent review
found and confirmed the reentrancy fix. These are projected diagnostics, not
replayable exact evidence.

Python enumeration snapshots configuration and control limits before callbacks;
Kyoto `8774f168203341f183917063df5db337` passed and static review found no
scoped defect. TypeScript research validates encountered projection symmetry and
reachable-class transitivity within its comparison budget; build
`9a00ad7f4f9d4eaa98d1a1e9565226dd` and research suite
`30688622d9a447f6adcd23e75caf5df0` passed. Independent review found and
confirmed the transitivity follow-up. These checks do not establish comparator
purity or equivalence outside the reachable finite fixture.

TypeScript watch authorization compares typed paths structurally, clones and
validates owned configuration before authorization, and guards context capture
against reentrant mutation. Final build `706dcf1544704951a16853067b088f3f` and
watch/protocol suites `1af3eeaaa4c5455ea9ee81bcaf6d19a8` passed; earlier full
watch/diagnostic/inspect/protocol suite `bb097597db2347c992f7454f7f4ea34e`
passed. Independent review found and re-reviewed the accessor authorization and
context reentry follow-ups.

Go legacy inspection now validates metadata before value callbacks, requires
child segment kinds consistent with containers, emits Rust-like enum variants,
and rejects forged parent/child shapes in IsComplete. Final debug/demo plus Rust
fixture Kyoto `1a5b91dfa42f4d40b7cd19b43dcc61df` passed; enum wire shape has
source comparison and Go tests, not an independent cross-engine enum fixture.
Independent review caught and confirmed the completeness follow-up.

Swift adds page-aware watch snapshotNow for authorized paused or active watches
without advancing observation order or changing baseline. Focused Kyoto
`ac5d77e436fb4151aa09e1111362c21b` passed and scoped static review found no
concrete issue; event-retention/wire parity remains unqualified. Python core
record snapshots TraceLimits before callbacks; native tests
`bf376f7b021d4af7bab186123978555e` passed and scoped static review found no
issue. Native/audit recording use separate APIs and were not changed.

Swift WatchRegistry now provides interested and latestEvent access; final failed
selected observation expires immediately while retaining its inspector failure
and emitting one lifecycle event. Focused Kyoto
`731d7e3994124bff9550d467da27e132` passed; independent review found and
confirmed the TTL failure-path fix. Latest event denotes observation/failure/gap,
not lifecycle-only ring events.

Python record preserves a coherent error prefix on iterable construction/next
failure and safely formats hostile ModelError subclasses. Kyoto native suite
`97b5d756ff804f73bf374fb93f9b0fe8` passed after review found the latter error
path. TypeScript watch config validates trigger/selector/limits atomically and
accepts long equality scalar text subject to its config bound. Final build
`33f0ebab03a947f6ad3ac2ba372cf1f2` and watch/protocol tests
`15b874aeb645440ab88f4c6ea87bc800` passed. TS config-byte accounting remains
more conservative than Rust, so exact bound parity is not established. Current Go
source already addresses older wide-count and composed Auto panic notes; this
scoped observation does not qualify the full Go surface.

TypeScript watch config accounting now uses logical headers matching measured
64-bit Rust (296-byte config, 32-byte path segment) plus bounded UTF-8 payloads;
standard f32/f64 kind literals are static and not charged. Known fields are
captured under reentry guard with bounds before validation/authorization, without
cloning unknown object graphs. Review caught a proxy path length race; final
build `f1c0cf5455a645e5a547d5fde5750ba3` and watch tests
`116783f56e534c9bbc5d4246eedea578` passed; combined watch/protocol/inspect/
diagnostic tests `86c7a226c81b4b66bf9791378e3a5d90` passed. Rust executed layout
fixture measured 296/32 and a 413/414-byte acceptance boundary. Other Rust ABI
layouts, actual JavaScript object overhead and caller-owned allocations are not
qualified or claimed bounded by these logical charges.

Swift watch incomplete projections now yield Unknown and do not count as usable
baseline; expiry retains complete baseline after evaluated or sampled final
observation, while failure retains the inspector error and clears baseline.
Focused Kyoto `a81c1c564fef4019be083b019f61fb03` passed; independent review
found and confirmed the status and TTL follow-ups. Go nonindexed Sequence and
IsComplete now enforce positional index identity; debugger/demo Kyoto
`f101f43bbbc7471595a06fa2604a3a23` passed with fixture and static re-review.
Full parity and qualification remain open.

Swift watch add/update/enable/disable/pause/remove now emit bounded lifecycle
ring events with next-sequence effective boundaries and overflow handling. TTL
expiration uses diagnosticControl origin for evaluated, sampled-out and failed
observations. Focused 54-test watch/protocol/wire Kyoto
`35faaa65acb34492bdbf11be6e25f19e` passed before the origin follow-up; final
WatchTests `67f29fd12b6b4e20850717a7b28c71e4` passed 21 tests. Independent
review found and confirmed the origin correction. Swift WatchEvent still exposes
less retained metadata than Rust, so full event/wire parity is open.

Python mutation-suite summary publication now uses a synced exclusive pending
file and no-overwrite hard link, with failure cleanup for owned partial witness
pairs and checks for unexpected destination entries. Final reference suite Kyoto
`74de03bb891a4a2b864fab9f8b27b654` passed; review found and confirmed the
failure/collision follow-ups. Directory admission/recheck assumes one owner, and
post-link sync failure can leave a visible final report. No power-loss or Windows
qualification is claimed.

Go nested field projection resets child page offsets and restores parent context
on completion/error/panic. Debugger/demo Kyoto `24052876eb674677bc816d7280b51215`
passed with Rust fixture and scoped static review. TypeScript review found no
new concrete capability gap in this pass; that does not establish full parity.

Swift watch events now retain bounded path, trigger, schema, phase, model,
machine, previous snapshot and property-failure metadata where available; no
failing property IDs are invented. Review led to corrections: schema-change and
gap events remain in ring but clear current, gap lifecycle uses prior snapshot
and control metadata for all entries only when interested, and stale input does
not increase filtered health. Final combined Kyoto
`f4b85c13c0bf4477892153a83b429d36` passed watch/protocol/wire suites. Review
confirmed the identified cases; failing property ID input and full wire/retention
parity remain open.

Go redacted/unavailable inspection fields preserve work/node/byte truncation
reasons rather than overwrite them; Go debug/demo Kyoto
`034fbe5b090a4193a7ee50acfecfb7bd` passed, with scoped review. Some parent
container resource-exhaustion representations still differ from Rust. Python
suite cleanup checks created file identity before removal and closes file
descriptors on fstat/fdopen failure, including witness read; reference suite
`d58f67cbe80d463e9d222381ed6a13e3` passed. Single-writer directory ownership is
still required: identity-check/unlink and pending-file linking are not atomic
against external replacements. These checks do not qualify full native parity.

Swift watch observation context/events now accept bounded host-supplied failing
property IDs (up to 16, clipped to 256 UTF-8 bytes each) without inventing IDs;
retention charges metadata. Sampling tracks actual skipped observations and
inspector errors follow trigger/delivery rules, retaining error current and
resetting recovery baseline. Kyoto watch/protocol/wire
`1f3f9ce7fa714859aba39c074c5bbcb7` passed after review-driven follow-ups. The
protocol adapter does not yet supply failure IDs; direct context does. Swift
model/machine labels clip at 1024 bytes; event completeness records truncation.

Rust and TypeScript no longer relabel a forced Equals-trigger inspector error
as ValueMatched on property failure. Rust compile-only
`28bddbaf2df244328c3227eaa5c3a673` was followed by executed 17-test watch
binary `204f90fa1c044f27b44ce632de5f7bd8`; TypeScript build
`b137ae1820694dd38c2208afcdd5dff5` and watch tests
`1d0783247ebd4740a3fba089ae8d7680` passed. Go full debugger
`4178a58cd2324627ad71363698fa3983` and Python watch tests
`3d3a3aa4bc2a493a8ee915173e6aa6cc` passed equivalent regression controls.
Independent static review found no further concrete issue in that scope.

Go inspection containers omit engine-origin work/node/byte exhausted children
and restore their retained node/byte charges, while preserving host-returned
truncated children. Kyoto debug/demo `0e89ce77c0c948b4bc567fbf6fdfd378`
passed, with independent review. Go still precharges child header bytes before
callbacks while Rust charges afterward, so near-limit callback ordering differs.
Python newly produced reference witnesses label original/minimized provenance;
explicit expected_witness strictly checks it while default remains compatible
with legacy unlabeled traces. Kyoto `f6608f39523343048ef982c5d51ffb79` passed;
actual cross-engine replay of the newly labeled artifacts remains open.

Go inspection now charges child overhead after value projection, preserving
metadata preflight and rolling back unadmitted retained charges. It snapshots
validated SourceSite before callbacks so later mutation cannot alter retained
metadata. Final debug/demo Kyoto `4348947925a34b53ae3d6c1740ac0059` passed
with Rust fixture; independent review found and confirmed the source ownership
follow-up. Metadata preflight and representation-specific byte thresholds still
differ from Rust at some limits.

Swift protocol watch observations now forward actual failing initial/turn check
IDs in order, retaining a bounded prefix and reporting truncation. Focused Kyoto
`c127b5bc84b44ccbb2d94e48f38dac8f` passed; scoped review found no concrete
issue. Protocol tests exercised a failing turn; exhaustive initial/>16 protocol
qualification is not claimed.

Fresh Python-produced labeled reference suite `1f6a04cea17b41f785932684d6c90f49`
was replayed by pinned Swift consumer `37b41bebffbf444dbeb7006d6054134d`: all
18 witnesses verified with kinds, build_matches=false. Focused Swift tests
`5871537f3f58488689eba560f1a3d5fb` and cross-process wrong pin/kind/shrink
negative controls `c1b9264b89fe4dd38159f7b22a5e3808` passed. Independent review
found no concrete verifier defect in scope; pins are identities, not producer
authentication, and directory stability remains caller-owned. This is one local
producer-consumer direction, not full matrix qualification.

Python adds a same-build whole mutation-suite verifier: strict summary and label
identity, bounded incremental directory admission, and exact replay of all 18
witnesses. Kyoto reference suite `1960c8cb743541c1be6f4758199c6ce3` passed;
review caught and confirmed the directory bound. Existing per-trace limits apply;
a shared aggregate suite byte budget, foreign-build API and concurrent directory
mutation guarantees remain open.

Swift watch_current now reports event completeness independently of node
completeness, including sampled and unavailable states. Focused Kyoto
`9e3145cb2de84c9fa3c4433bb6a97a26` passed; scoped review found no concrete defect.
Go opaque inspection emits a UTF-8 scalar-boundary preview rather than rejecting
an overlong label. Debug/demo Kyoto `005443f34e314cee83303c2f1b6e3dfd` passed;
only the retained prefix is validated, so invalid bytes beyond it are not scanned.
These focused changes do not qualify the full native parity plan.

Python verify_mutation_suite now enforces a configurable aggregate summary plus
witness byte budget and permits foreign suites only with explicit complete report
and per-mutation build pins; same-build strict remains default, and witness kinds
remain mandatory. Kyoto reference tests `099719759d7f49ed9f84bff3d186a522`
passed exact-fit/one-short and pin negatives. A real Swift-produced suite replayed
18 witnesses through this API in `4ba682119a2a4972b20849a6f42bc797`; scoped
review found no concrete defect. Directory stability is caller-owned and pins are
identity assertions, not authentication.

Go nonindexed inspection pages now start at the requested offset rather than
charging work for skipped entries; debug/demo Kyoto
`7ecd8f7089774ab6b5c8676ce8b94706` passed last-page work controls and Rust
fixture, with scoped review. Selected-path uniqueness remains a separate scan.
Swift changed-trigger watches now deliver a sampled unchanged comparison after a
real skip, and compare nodes independently of schema source metadata after schema
identity checks. Final Kyoto `037274a5a9dc4effbbdd9f2d55143587` passed; static
review found no additional scoped issue. The added source-change test covers the
unsampled case; a sampled source-change case remains unexecuted. These cases do
not qualify the entire native parity plan.

Go nonindexed sequence selected paths now directly index the requested entry,
validating its metadata before callback without scanning prior descriptors.
Kyoto debug/demo `a87f83c2ad4d454aa4849460405fd0f3` passed selected index 9,999
under a one-work-unit budget and Rust wire fixture; scoped review found no defect.

Python suite verifier optionally returns verified count, bytes read/limit and
build_matches across report and every mutant build; legacy integer return remains
default. Kyoto `712c1839a2a241d4a5da42dbf817663d` passed local and synthetic
foreign controls; static review found no concrete issue. Directory stability
remains caller-owned.

Swift validated-watch lifecycle events now include the newly admitted schema;
validated add preflights capacity and revision before projection. Kyoto
`cf78284f6fe1458d9603131fd8421f3d` passed focused watch/protocol/wire tests.
Independent review found and confirmed capacity preflight and exhausted-ID error
follow-ups. Swift checks revision before projection whereas Rust validated-add
checks it afterward; this callback/error ordering remains different. TypeScript
review identified no grounded new feature in its inspected scope.

Swift WatchAck/WatchStatus now expose validated schema and resolved type, nil for
provisional watches. Focused Kyoto `0de33c0e337c40a7a9a6f3006eac1345` passed;
scoped review found no defect in added fields. On a first unvalidated projection
failure Swift cannot retain separately obtained schema as Rust can; this remains
open and must not be inferred from an error.

Go inspection retains explicitly returned partial children and continues later
page rows while internal resource exhaustion still rolls back and stops. Debug/
demo Kyoto `f7d76c780d3f4a5b88c7dc0509b5e791` passed; review found no scoped
issue. Python adds a strict local whole-suite fresh-process CLI; reference suite
`101aa66902fd45bea8c1c074f0e99e80` passed after review led to rejecting an
explicit suite-byte option in witness mode instead of ignoring it. TypeScript
inspection of automatic exploration found no substantiated missing operation in
that scope; this is not a complete API audit.

TypeScript now exports a RequestModel fixed/buggy demo with ordered input
generation, checks and canonical codecs. Final build
`fbbfd4b68c934a5fbb33b319262f3811` and demo tests
`d9c54cb8cdc049b79ef0752fb41a1aa7` passed after independent review found and
confirmed rejection of malformed JS discriminants. Source-derived byte vectors
are not yet fresh-process Rust–TS interchange qualification.

Swift watch attachment can obtain declared schema independently before a first
projection failure; protocol validated admission also checks declared schema
before projection and rejects mismatch atomically. Kyoto
`c00647ed4d734b8d8195880ec9b9e75c` passed; scoped re-review found no concrete
issue. A throwing schema callback is not separately exercised by that regression.
Python suite CLI now accepts explicitly supplied full foreign build pins, with
strict bounded JSON and no null/omitted-policy ambiguity. Kyoto reference suite
`29007e9f0d9a4654966dff7165053db9` passed after review-driven null-pin fix.
Pins identify builds and do not authenticate producer provenance. Go review did
not identify another concrete feature in its inspected scope; full parity is
not established.

Python Network now snapshots machine registry, wiring and queue policy across
step, input admission/iteration, check dispatch and metadata/initial-state
construction before callbacks. Final stateful Kyoto
`c2fc4b7d18bd45e99055aae0a1285a99` passed; independent review found and
confirmed the check/admission and initialization follow-ups. Model instances
remain application-owned and concurrent mutation is unsupported.

Go nonindexed sequence inspection now generates Rust-like decimal index labels
and no source before validation/accounting; irrelevant caller metadata cannot
block a selected/page request. Debug/demo Kyoto
`c99a91c1dbb34e6d82f0daebf6d4255a` passed with Rust fixture and scoped review.
Swift watch health now counts evaluated incomplete projections even if delivery
is suppressed; focused Kyoto `34158261ef984ecfb463947e3e9e3046` passed and
review found no scoped defect. Swift's baseline retention/eviction policy still
differs from Rust, so baseline-eviction parity remains open. TypeScript review
found no substantiated missing operation in automatic exploration scope.

Swift watch retention now evicts oldest retained baseline/current by sequence,
tie-breaking by watch ID, resets evicted baseline state and records saturating
baselineEvictions; ring/failure snapshots remain independent. Focused Kyoto
`e2e9de065f9542e3a106cef77c4e3ea9` passed; initial
`79d547...` fixture budget was too small for one Swift event and was corrected.
Independent static review found no scoped policy defect. Swift logical retained
bytes do not establish Rust's exact host byte thresholds.

Go Pair/Network now expose InitialState only when all children supply it, so
missing optional hooks fail preflight rather than panic. Full uncached Go suite
Kyoto `5ef712b7fec74afc8d70f7455de1d040` passed and static review found no
scoped regression. Go map inspection currently retains explicitly supplied
label/source metadata whereas Rust map emits empty label/no source; this remains
an observable documented difference. Python Network.step snapshots input before
copying pending payloads and revalidates the copied state against its captured
registry/policy. Stateful Kyoto `52a16e01d2224cf4baf42b31f2aa682d` passed;
application copy callbacks remain cooperative. Full parity and qualification
remain open.

Go sequence projection emits canonical decimal index labels and no source for
nonindexed and indexed paths; unused caller label/source is discarded before
validation. Final debug/demo Kyoto `c99a91c1dbb34e6d82f0daebf6d4255a` passed
with Rust fixture; review found and confirmed the metadata-order correction.
Python Network metadata, initial_state, checks, inputs and step capture the
machine registry/wiring/policy for each operation before callbacks; stateful
Kyoto `c2fc4b7d18bd45e99055aae0a1285a99` passed, with scoped re-review. Model
internals and concurrent external mutation remain application-owned.

Swift watch health now counts evaluated incomplete projections even if trigger
delivery is suppressed; `34158261ef984ecfb463947e3e9e3046` passed. Separately
supplied protocol watch schema is resolved lazily after selector matching; a
schema failure after a previously recorded noncontiguous gap no longer records
the same boundary twice. Final focused Kyoto `c86ee4f858e54af28aa3458aebd21f33`
passed after independent review identified and confirmed the duplicate-gap fix.
Swift resolves the schema once per boundary while Rust calls it per selected
watch; callback count parity is not established. Full parity remains open.

Swift watch schema callbacks now run once per selected watch in reference order;
shared projection work retains each watch's separately declared schema. Schema
failure invalidates all watches before rethrow and deduplicates a previously
recorded gap at that boundary. Final focused Kyoto
`01dd1de169ed46e5bfd4d79e59c875b9` passed; independent review found and
confirmed the later-watch failure/cache follow-ups. Shared-node validity for
matching queries still relies on the host projection contract.

Go Pair/Network now expose Step and Inputs only if all children provide them,
preventing nil-hook execution; full uncached Go suite
`34160590890b4522839c3e8778d56050` passed and scoped review found no concrete
issue. The absent Inputs hook replaces a callable error for partial composites.
Python Pair/Network snapshot each child transition before routing callbacks;
stateful Kyoto `e7ad1f0cbd0c4dd48b750d866d99b669` passed and scoped review
found no defect. Arbitrary host deepcopy callbacks remain cooperative.

Go Pair/Network expose EqualStates only when all child and queued-input equality
hooks exist, avoiding nil-hook panics. Full uncached Go suite Kyoto
`14048cfdc66e48f5832f9e1f8cc63b96` passed; scoped review found no defect.
Swift WatchStatus now distinguishes active, paused, disabled and expired states;
focused Kyoto `a50f04aef380478b8a5d1050e2ced8dd` passed and scoped review found
no transition defect.

Python reference suite producer can publish separate create-new build pins for
native consumers. Publication requires all nine unique killed controls, complete
shrinking and both witnesses; Kyoto `832c7cd90d684f5d80efb21ec2f0ff1d` passed
after review found the incomplete-shrink case. Scoped re-review found no further
issue. The pins are identity assertions, not authentication, and suite directory
ownership remains single-writer. Full native parity remains open.

Go Pair/Network state and transition checks now capture complete observations
before child check callbacks, with detached global views. Full uncached Go suite
Kyoto `1d31f9af74b54817bc3f061242819dea` passed after independent review found
the transition follow-up; re-review found no concrete issue in that scope.
Application clone hooks remain cooperative and cannot guarantee atomic snapshots
against arbitrary external alias mutation.

Swift watch ring emits best-effort EventsDropped markers for unreported loss
when space permits without evicting to fit the marker; failure snapshots include
the marker if retained. Kyoto `91647c0a10364f95aae5697d3ec8af71` passed focused
watch/protocol/wire tests; independent review found no scoped defect. Swift supports separate event and ring byte limits; the earlier single-limit
assessment was stale.

Python reference CLI now supports fresh-process `--write-suite` with separately
created `--write-pins`; suite creation and pinned verification passed Kyoto
`2903d16a8cb6484489c935086a4f4503`. Scoped review found no concrete defect;
pins remain identity assertions, and a completed suite can remain if subsequent
pin publication fails. TypeScript review found no new concrete capability in the
inspected scope; full parity remains open.

Swift watch projection reserves known event metadata against maxEventBytes;
snapshotNow validates declared schema and rejects oversized completed events.
Observation also fails closed by clearing over-cap current/baseline after final
metadata accounting, with loss when delivery was due and observation-gap recovery.
Final focused Kyoto `9d81f8429d5b4ffdbade102d3252dcce` passed after independent
review found and confirmed the truncated-counter correction. For legacy unknown or
changed schema, this post-projection fallback is deliberately conservative and
is not exact Rust pre-known-schema threshold parity.

TypeScript now provides stateless-demo for fixed/buggy RequestModel exploration
and seeded fuzzing, with bounded u64 parsing and documented JSON/exit behavior.
Kyoto process tests `c946091470cc4d01b294c318082b218e` passed after review-driven
seed bound; installed-package execution and full Rust CLI parity remain open.
Python suite producer CLI exposes --max-evidence-bytes for serialized witnesses;
reference suite `5fcfb5e3fbc94af8a4d071ee89ef93a6` passed with fresh-process
zero-budget/mode controls. Summary, pins, callback allocations and RSS are not
part of that witness budget.

TypeScript watch contexts support outputVariants with bounded, validated snapshots;
Kyoto build `03249fb818444565a8bd4445721e8cea` and watch tests
`91629b0243db455d9034c17876b9a059` passed. Host collection limits differ from
Rust and do not bound allocations already made by the caller.
Go network accessors expose policy, sorted detached machine IDs, wiring, and
state lookup; the full uncached Go suite passed Kyoto
`1942286ff49e4cac8178079eeaaddf8a`. State values retain normal Go reference
aliasing. Python runtime_build_id hashes packaged source resources on demand;
modeling tests passed Kyoto `3298838b623a4521a091590efd47adf1`. It does not
attest loaded code, extensions, or authenticity, and installed distributions
remain unqualified.
Swift watch Equals accepts Rust-representable display scalars including arbitrary
bounded numeric display text; focused watch/protocol/wire tests passed Kyoto
`fbe46cbc24c14f9b9ba200175486074f`. These focused checks do not establish full
native parity or qualify the overall local changes for merge.

Go WatchContext now accepts multiple output variants; Observe and Gap validate
and copy bounded input before mutating state. Debugger suite Kyoto
`1b7869ce2bad4ecc8f552193229eb024` passed and scoped re-review found no further
defect. Go admission limits are host-specific and stricter than Rust.
Python watches retain bounded failing-property IDs and report them in snapshots;
metadata is accounted for in event budgets. Focused Kyoto
`d965eb30361d404ab20550bf596aa9db` passed after review fixes. Post-projection
capacity fallback is conservative relative to Rust exact thresholds.
TypeScript embeds a source-input build identity, with explicit symlink rejection
and filesystem-safe paths. Kyoto build `79d2fe96a5ca442baca94313f65225ce` and
identity tests `3ab16f324e344a4391a26e8546619934` passed. It is not an
attestation of emitted or executing code, toolchain, or package authenticity.
Swift defaults now match Rust's 32 KiB event/projection and 4 MiB ring limits;
explicit nil preserves the earlier ring-sized event limit. WatchTests Kyoto
`3db689c68b4a4ea3bcbbff3338fa8ecf` passed. Protocol-installed watch event
limits honor custom registry and frame limits; protocol tests Kyoto
`c0dd828dd72745e495d279576228e6e4` passed.
Swift protocol watch value/page limits also honor configured limits for paged
adapters. Nonpaged adapters retain their supported first page of at most 100;
Kyoto `755c5fd2468f4557994aed4688b5dd86` passed attachment tests for both
adapter types after review fixes. Scoped final re-review found no further defect.

Python watch admission validates schema, TTL, path/depth, and projection limits
before mutation; debugger suite Kyoto `cd6e5db7c1fa4dd89cab942ed8efd6e1`
passed (196 tests, one skip), with no scoped review finding. Registry schema
version defaults to 1 and must be configured for other versions.
TypeScript watches retain bounded failing-property metadata and account for it
in event admission, with oversized events failing closed and delivery cadence
preserved. Kyoto build `183eb7bcae0745dfbec5baa6bfaa0e5f` and watch tests
`90e8361863f1451ab404988506580e0d` passed after review fixes. Accounting remains
conservative versus Rust's exact thresholds.
Swift MarkerCollector provides bounded lazy markers with UTF-8-safe truncation;
Kyoto `90dba2afa0fb41e2a59c73dd04d74da8` passed three focused tests; scoped
review found no defect. Its 48-byte marker overhead is a logical 64-bit profile,
not a bound on Swift heap or callback allocations.
Go WatchRegistry.SnapshotNow provides safe-point projection and checks retained
schema identity. Observe retains schema before projection and pauses on identity
changes; debugger suite Kyoto `92959aa736f148f78066771a17c9de25` passed after
review fixes. Schema changes on sampled-out observations are detected only at
the next evaluated observation; retained-size accounting differs from Rust.

Go now checks watch schema on selected sampled-out boundaries, superseding the
prior delayed-detection note. Source changes remain sticky until evaluation;
incomplete projections emit unknown and recover through observation-gap reset.
Kyoto debugger suite `c6b547ab3a5b46e7bd01976073d9187e` passed after review
fixes; adapter creation now occurs on sampled selected boundaries, but projection
remains skipped. Scoped re-review found no further defect.
Swift retains sampled source changes and baseline timestamps so expiration wins
when both apply. Kyoto WatchTests `32a679d2591f4fc8b62b923c93edfeb3` passed;
scoped re-review found no further defect.
Python MarkerCollector provides bounded lazy markers and immutable batches.
Kyoto `1e87d348e8734740b67724e98859ae7d` passed four tests after bounded UTF-8
traversal review fix. Its 48-byte logical accounting does not bound Python heap
or callback allocations; usage is single-threaded.
TypeScript watch model, machine and schema metadata now truncate at UTF-8
boundaries while full schema identity is retained. Kyoto build
`d294cd191a524711a7bf4e47003f74ea` and focused tests
`fbe6f7e2d16f42ad81001b2df26776cf` passed; scoped re-review found no further
defect. JS metadata admission has a 1,048,576 UTF-16-unit host limit.

Go MarkerCollector supports bounded lazy markers; Kyoto focused tests
`637accb387c446cc83d7e5488aac5019` passed. Its 48-byte logical profile does not
bound heap or callback allocation. Go watch TTL now uses retained current events
and failed retention triggers eviction reset for complete, incomplete and error
events. Uncached debugger suite `5e70285f10cc4968870e013ae482a2ed` passed after
review fixes; zero-byte retention is rejected by current Go configuration.
Python watches preserve sampled source resets, use retained events for TTL, and
compare schema identity by name/version. Focused Kyoto
`1ac95a9b83d04c928373a67f00b41290` passed after review fixes.
Swift watches compare schema identity by name/version, validate cached projection
identity per watch, and preserve TTL after retained inspector errors. Kyoto
WatchTests `3004e170a2cd48c5aaeb81d307026248` and
`457fdc2a220b47c7aa4b9d41d5900699` passed.
TypeScript MarkerCollector now uses the 48-byte Rust 64-bit logical record
charge; build `fb2d248f142e426283552077e72ca982` and watch tests
`ffbf649ea0b644f59a4914618dff9568` passed. This charge is not a JS heap or
callback allocation bound. Scoped independent reviews found no further defects
in these corrections.

Go watch registries accept zero capacities and safely handle empty rings and
retention; watch projection bounds and validated-add capacity are enforced before
callbacks. Full debugger suite Kyoto `764bfcb7c3aa4ce78106b271372ce8e5` passed
after review fixes. Existing synthetic dropped-event and retained-size accounting
differences remain.
Python watch lifecycle events now carry selector, trigger, phase and validated
schema; add/update prepare validated schema before emitting. Kyoto
`43fe622d94ff43c29c4fc5b4ea98714a` passed; u64 exhaustion rejection remains
atomic, matching Rust. Scoped review found no further defect.
TypeScript watches accept long schema source locations while preserving them in
status/acknowledgments, with name/version identity. Kyoto build
`83b6011710fa4cc7a2dac94dd0a3ee3d` and watch tests
`33345c1ed6f14cbcb87956743ff766b0` passed. Inspection retains separate metadata
budget and JS host cap remains.
Swift watch registries enforce a configurable schema version (default 1), with
typed expected/actual mismatch; protocol maps mismatch to invalidInput. Kyoto
`10234ab4669d491d9564ba8dca44245b` passed 70 watch/protocol tests. The added
public WatchError case may require clients' exhaustive switches to be updated;
non-1 schema clients must configure the registry. Scoped reviews found no further
defect in these changes.

Go watch dropped-event markers now use bounded best-effort ring delivery at
interested boundaries; unreported loss persists across drains/exports. Kyoto
uncached debugger suite `20b4dee3f32c44d9b86c7beb4554109c` passed after review
fixes. Diagnostic-probe queues retain their existing behavior; Go retained-size
accounting remains host-specific.
Python Network.machine_ids returns sorted IDs and NetworkState.machine returns
None for absent IDs. Kyoto stateful suite `85d3d949b11442b2854529dcc58350ff`
passed 27 tests. This changes absent-ID behavior from KeyError; None is also a
possible application state, as documented.
TypeScript MetricsStore snapshot guards its whole staged transaction against
reentrant mutations, including timestamp getters and prepared capture commits.
Kyoto build `a03276bbfa4d42dc9bda74f8cac7b7b9` and focused metrics tests
`e65eea58722e4de8b8daccca9dee9190` passed. Mutations during validation now throw.
Swift diagnostics retain schema name/version without source location. Kyoto
DiagnosticsTests `24116d87d422458bbdb5caaa7b438229` passed after correcting a
stale scalar child-count golden against Rust. Scoped independent re-reviews
found no further concrete defects in these changes.

TypeScript adds networkMachine binary lookup for canonical network state; Kyoto
build `5b1889540f03473ba38fbde4920899b3` and stateful tests
`950b3dc115e24d48b2ac7e2e796c07be` passed. It returns a stored reference;
undefined can also be an application value.
Swift Pair/Network check callbacks receive observations captured before child
callbacks; Kyoto `d77c337129934218af79bc36dce1ce63` passed two focused tests.
Clone hooks must cooperate and cannot ensure atomicity against external mutation.
Python Network snapshots mutable registrations in sorted order at each operation
and rejects emptied/invalid registrations; Kyoto stateful suite
`7e9441877ab04885b8e50978d2525a68` passed. Public mappings and child models
remain mutable, and snapshots do not guarantee atomicity against concurrent edits.
Go metrics revisions saturate at u64 maximum and windows select the first match
when revisions repeat, matching Rust; Kyoto debugger suite
`be52b5bd62f846b5b8ff269ac4aaf8a7` passed. Later snapshots with the same saturated
revision cannot be selected individually by revision. Scoped independent reviews
found no further concrete defects in these changes.

Swift Network.machineIDs and NetworkState.machine provide sorted ID snapshots
and binary lookup over canonical states. Kyoto `a09134473d454ed2b5d2d95b92bd92b3`
passed the focused test; reference-valued results retain normal aliasing.
TypeScript Pair.inputs captures cloned state at API call before iterator pull;
Kyoto build `95d3ea6eefb2431f9f633788d358e20f` and stateful tests
`86daf24bc7944613b918c9bd5bc4cd83` passed. Child enumeration remains lazy and
clone hooks must cooperate.
Python stateful codecs reject unknown Pair input/address and Network input tags;
Kyoto stateful suite `251b7ba8a7d84520b0febdd07095f984` passed with valid and
malformed binary controls.
Go MetricSnapshot.Get and Histogram return detached lookup values, including
copied big integers; Kyoto debugger suite `b9ec3a8c816e49609f0d97904cdcdbc8`
passed. Scoped independent reviews found no further concrete defects in these
changes. These checks do not qualify full core parity.

Go Pair operations reject malformed pending-address tags before cloning or
checking; Kyoto focused job `cc800ac473f2448b8a7825ca33d0fd6a` passed. Direct
clone hooks cannot return errors and can still panic on malformed tagged values.
Python MetricSnapshot.histogram returns the stored histogram or None; focused
Kyoto `67f124d625344718b5e21f7e8d867a0c` passed. TypeScript provides the same
lookup; Kyoto build `67bdcaa5801240f185e08f6de49cecc8` and tests
`edfa40f50ee147f692da236889c47273` passed. Both accessors alias mutable snapshot
values, unlike Rust's immutable borrow.
Swift MetricRegistry now rejects stale prepared snapshot commits even when
revision and eviction counters saturate; Kyoto `a490d0695fc54619af083a75823c9114`
passed nine MetricsTests. The registry still requires serialized access; its
generation token is not a thread-safety mechanism. Scoped reviews found no
further concrete defects in these changes.

Go OracleModel captures state/history/input/transition observations before child
checks; focused Kyoto `1dbbc534c1984a49ae965dbc268c834a` passed. Clone hooks
must cooperate and cannot guarantee atomicity against concurrent external edits.
Python metrics snapshot blocks reentrant store mutation through validation and
prepared protocol commits, and saturated revision windows select first match.
Kyoto `fe8fbe5fbd1c4db2a0282cd0e3bc04ef` passed; store access must remain
serialized and the guard is not thread safety.
TypeScript Network registrations are held in an ECMAScript private map; Kyoto
build `3ea2b967a8f04fe694c09e2310126550` and stateful tests
`76972204245d41a5b3fb1a1416c9fde2` passed. Policy remains runtime writable and
child model references shared for compatibility.
Swift validated-watch add/update and staged protocol add inspect after config and
capacity checks but before revision exhaustion; Kyoto WatchTests
`a86145387d5a4fd6aff19de3d5ac3d65` passed. This supersedes the earlier callback
ordering difference. Scoped reviews found no further concrete defects.

Go OracleModel.Step captures input, state and history before invoking reducer;
Kyoto Oracle suite `cbf5aa67ee014ba2949fd7aab1c449f6` passed after input-first
clone-order review fix. Swift and TypeScript WithOracle check callbacks capture
observations before child checks; Swift Kyoto `c15b4cc5c54647fb9ec55c4e6e3f1943`
passed including absent optional callback, and TypeScript build
`ce6304e325f244cbbd0498ac6377ff7a` plus paired/append tests
`94ce8254669c4a9d8f11aa2639525c10` passed. Python Pair/Network transition checks
isolate disposition copies; Kyoto stateful suite
`4e686eb63ce54ee9ae33a34408790f94` passed. Scoped re-reviews found no further
concrete defects. Clone/deepcopy hooks remain cooperative and cannot provide an
atomic snapshot against arbitrary external concurrent mutation.

Go OracleModel.EqualStates captures histories before the actual comparator;
Kyoto equality tests `9129c22a2dd2467d9f0aed7476029ca9` passed. History clone
callbacks now run even if actual comparison is false. Python, Swift and
TypeScript WithOracle.step capture input/history before the reducer; Kyoto paired
suite `e5abf172ec15474493edd141c8d8bdca`, Swift OracleTests
`80e898015a2249879ac5127a75e93ec9` (15 tests), and TypeScript build
`20a0f8bae3574bb69a200e45af952862` with paired/append tests
`668649439fea4ffd9dfc3763dc407212` passed. Scoped reviews found no further
concrete defects. Copy hooks must cooperate and cannot make arbitrary concurrent
external mutation atomic.

Python Paired checks capture observations before reference callbacks; Kyoto
paired suite `6fcc46dd24b24ded81ebe231351b0775` passed. Go OracleModel resource
estimation captures history before the model estimator while preserving actual
nil/error precedence; Kyoto `84fbd635e26c4b6da61136e0e5d98c53` passed. History
cloning now occurs even when the model estimate is unavailable or errors.
Swift and TypeScript WithOracle equality capture histories before application
comparison; Swift Kyoto `24f9b8c0a839441381b8e167ea75af52` passed 16 OracleTests,
and TypeScript build `051b1787df98475fbcd4d8c9c739c43b` plus paired tests
`65e7d0eefec147f5aec969c5f93e8f14` passed. History clone hooks now run even
when model comparison returns false; Swift hooks can throw there. Scoped reviews
found no further concrete defects. Copy hooks remain cooperative and cannot
make concurrent external mutation atomic.

Python, Swift and TypeScript WithOracle resource estimation now captures history
before model callbacks while preserving model unknown/error precedence. Kyoto
Python paired suite `882d70c2029a4a5cabf67f86aeeafbcd`, Swift OracleTests
`47ce422bbf3e4f5ca9010eb48b80c486`, and TypeScript build
`d9511648703e47c3b42620f521ab9fd8` with paired/BFS tests
`e4a81db9592449c98fd3d082fffd3c7f` passed. TS pins estimator callbacks and
owners across the operation. History clones may run even when model estimates
are unavailable or fail; copy hooks remain cooperative.
Go oracle state encoding captures history before model callbacks, while model
and frame errors retain precedence over capture errors. Focused Kyoto
`3fff7dc8f8f14395acf35403f16946fa` passed; history clone side effects can occur
before a model encode failure. Scoped independent reviews found no further
concrete defects in these changes.

Go Paired reference history/effects are captured before checks, with check errors
retaining precedence; Kyoto `1796cdfea85348d2b0b8477eabef8936` passed two focused
regressions. Actual-side S/O lack clone hooks and remain outside this isolation.
Python Pair validates forged address and input tags including adversarial equality
objects; Kyoto stateful suite `e50c2784545548cfa565f862ff06551a` passed.
TypeScript Pair.equalStates captures states and pins child owners/callbacks before
comparison; Kyoto build `2f206572bcff4b2ca5696edc6f88b569` and stateful tests
`f351c0ba09e640cf9eabd78b34ef7de5` passed. Clone callbacks run even if the first
comparison returns false. Swift containsEnumerated64 captures the query before
domain callbacks and defers copy failure until a candidate is compared; Kyoto
SearchTests `c4171d89a7834f0c906bd289cee1ea03` passed. Its clone hook runs even
for an empty domain. Scoped reviews found no further concrete defects; clone hooks
must cooperate and cannot make concurrent external mutation atomic.

Go Network.step captures selected input and all pending payloads before machine
state clone hooks; Kyoto `4ab6d60c5942422eb39c3f0db8dff923` passed three focused
regressions. Clone ordering is observable and hooks must cooperate.
Python WithOracle raw state encoding captures history before the model encoder,
while model/frame errors retain precedence; Kyoto paired suite
`f7986a08c1334e0aad6d79503b35de27` passed. Early copy may have side effects on
model failure.
Swift Paired captures reference observations before checks with deferred copy
errors; Kyoto OracleTests `6bb824785fa3438e8243f5faf6ba1c29` passed. Actual-side
generic values lack clone hooks. TypeScript containsEnumerated captures its query
before state/domain callbacks and pins callbacks; Kyoto build
`0934261548ab4a739edeff6c9ba2b742` and search tests
`94011bcf6e97485ba5e894b18a8412d1` passed. Query copying can have side effects
on empty/error domains. Scoped reviews found no further concrete defect; clone
hooks are cooperative and do not guarantee atomicity against external mutation.

The full uncached Go suite `0a13d369d0934ed1a1b3938a0be60a93` also passed
following the previous Network.step correction. Go Network.inputs now captures
pending channel identities and machine addresses before clone callbacks; focused
Kyoto `ecbffa74ccd647a78cc918ddc23e1935` passed.
Python Network.step captures pending messages before machine copies and validates
invalid actions before application copy hooks. Kyoto stateful suite
`8fa4d6ae2fad484c9bb61d7aed164de5` passed after review strengthened the error and
zero-copy controls. Swift Network.step captures selected input and pending queue
before machine clones; focused Kyoto `c48346b0f67044f2a5d1be88d2adeda8` passed.
TypeScript Network.equalStates captures both observations and pins callbacks;
Kyoto build `fd73737b48af451189d69f08eaff42f9` and stateful tests
`cecff2925df0414cb0ce2f96bf5cf7ea` passed. Eager clone hooks can run or throw
even if a prior comparison would be false. Scoped reviews found no further
concrete defects; hooks must cooperate and capture is not atomic against arbitrary
concurrent external mutation.

Go Network.EqualStates captures pending payloads before machine clone hooks;
Kyoto focused equality suite `aba2b002b097477c87c24da23a9ec976` passed. Eager
clones can run even on early inequality. Python and Swift Network.inputs capture
channel identities without copying unused queued payloads; Kyoto Python stateful
suite `393c5b67b19c4292892b3b4ae2d755eb` and Swift focused test
`5316808f064547ec82a0e33f73440979` passed. Swift child enumeration remains lazy.
TypeScript Network.step captures structural metadata before input callbacks and
pending payloads before machine clone callbacks; Kyoto build
`101189d6aa37446ca13d0c5c2d1ee786` and stateful suite
`83fd52cb56ad46a58412e2f9156c248e` passed. Scoped reviews found no further
concrete defect. Copy hooks must cooperate; arbitrary cross-alias mutations
during capture cannot be atomic.

Go Pair.Step captures selected input and pending address structures before clone
callbacks and uses detached child state. Focused Kyoto
`b88f5a6bf340478ba34ea9aaa6a9d6d2` and full uncached Go suite
`9bbc32470a1945d688e73a253546fd33` passed after review fixes. Python Network
rejects malformed action tags and forged machine IDs before direct method copies;
Kyoto stateful suite `7886208faf4640acb56905b4dab5687f` passed 36 tests. The
generic transition-check wrapper still copies before dispatch, and invalid pending
index types retain ValueError behavior.
Swift Network equality captures queued payloads before machine clone hooks;
focused Kyoto `3efb27a8d2e54ad898f7937165c1f87a` passed. TypeScript WithOracle
state encoding captures history and pins codec owners/hooks, deferring history
copy failures behind model/frame errors; Kyoto build
`2f1fc3b0a0fc4c1582cc315a555625d9` and paired tests
`f9e3486ac18e41ad8560e10d769706c1` passed. Eager copies may run on failing or
short-circuit paths; copy hooks must cooperate and cannot guarantee atomicity
against cross-alias mutation. Scoped reviews found no further concrete defects.

Go Pair equality captures pending payloads before child clone hooks while applying
pending length after child comparisons; focused Kyoto
`fde9e42a105c431a8face41ec9fa830d` passed. Python Network equality captures
queued values ahead of comparison, defers queue copy errors until machine checks,
and shares deepcopy memo on successful capture; partial failed memo is discarded.
Kyoto stateful suite `23767bb185a94ec584b5eb4d834d70e6` passed. Eager copy hooks
can still run on early-false paths.
Swift Pair.step captures selected input and pending queue before child state clone;
Kyoto `8d204c994cb84729abd813c0c91cc8cf` passed a focused regression. TypeScript
Network.inputs captures channel metadata before clone callbacks without copying
unused payloads; Kyoto build `a5796e40080e4dfe94bfb2e47902a2a4` and stateful
suite `5d06a5e65eff4f48b6a5ca8dbb1f50b3` passed. Child enumeration remains lazy.
Scoped re-reviews found no further concrete defects. Copy hooks remain cooperative
and cannot make external cross-alias mutation atomic.

Go Domain.Contains captures a nonempty query once before comparisons; focused
Kyoto `cbbad22e3dfb41c8b02792563e935a8e` passed. Empty domains do not clone;
nonempty calls perform one additional clone.
Python Pair.step snapshots pending structures before selected-input copy and
payloads before child state copy; Kyoto stateful suite
`327d74e01b50433190607f204e9319b5` passed after review fix.
Swift Pair equality captures child states/pending before comparison and defers
unused copy errors with snapshotFailure classification; Kyoto
`7b5cc3dad630462e8d93962031f0b051` passed. TypeScript Pair.step prevalidates,
captures pending state and pins callbacks; Kyoto build
`2af37492871c492caac77c9a54e7d823` and stateful tests
`6b7408f2fb184ee5aeb8c4172c9f8a7f` passed. Scoped reviews found no further
concrete defects. Earlier clone hooks have observable side effects; hooks must
cooperate and cannot provide atomicity against arbitrary external mutation.

Go Domain.Contains captures entries before equality callbacks, deferring unused
clone errors; clean Kyoto `cfdd04246eec4529aed0300000dbc11e` passed. Entry clone
hooks run eagerly even on an early match. Python Domain.contains gives each
comparison a fresh query copy; Kyoto modeling suite `cb2d44fb63554c22b14c4f49d222ab48`
passed. Swift Domain.contains captures a nonempty query before callbacks; Kyoto
`1eac8f1ad1874e7e9f11e513ad32c2aa` passed two tests. Both add observable copy
hooks. TypeScript WithOracle.isEnabled captures the input and model reference and
pins callbacks before cloning; Kyoto build `296a6d9d6171473eaaec6ca5cffb0c4f`
and paired tests `8bf534f4ce8c47feb88a6c3dc2ad21b6` passed. Scoped independent
reviews found no further concrete defects. Clone hooks remain cooperative and
cannot ensure atomicity against arbitrary cross-alias mutation.

Python and TypeScript Domain.contains capture entry sequences and defer unused
entry-copy failures, preserving an earlier match; Kyoto Python modeling suite
`6f97cb55e3b2404a9579c97770c3dccc`, TypeScript build
`f049d42cafef441eba365cff9243bca5` and modeling tests
`c7d4515738894513ae0e651830704d4b` passed. Go automatic membership captures the
query before callbacks and defers its copy failure until needed; Kyoto
`03668069eda24176a38456a2b54d88ce` passed three focused tests. These paths invoke
copy hooks eagerly, including on some early-match or empty-domain paths.
Swift WithOracle encoding captures history before model encoding and preserves
model/frame-capacity error precedence; Kyoto OracleTests
`7bdc62bd148d44c6a254f8aab78b1c97` passed 23 tests. History copy hooks may run
on model/limit failure. Scoped reviews found no further concrete defects; copy
hooks must cooperate and cross-alias capture is not atomic.

Go DomainProduct snapshots input slice slots before mapper callbacks; Kyoto
`a9f4538533a14c42a8735ab486c674df` passed. Snapshot is shallow and reference
contents remain shared. Python WithOracle.is_enabled captures query before state
copy; paired Kyoto `c38ab2f8e80941e1907a547c8a98528c` passed. Swift
Domain.contains captures entries before equality and defers unused copy failures;
clean Kyoto `f400a658aed9473ebae4754eab0669db` passed three tests.
TypeScript Paired captures reference observations before checks, preserves actual
projection error precedence and pins reference/projection callbacks. Kyoto build
`616cf3a8a4004acb8fa07738be8c6b98` and paired tests
`0e2e450412904d83a3bf91fe1c1b07e2` passed, including direct transition mutation.
Actual-side generic values lack clone hooks. Scoped reviews found no further
concrete defects. Early copy hooks are observable and must cooperate.

Go Network.CloneState now copies queued inputs before machine clone callbacks;
focused Kyoto `abc236d0c6c84f8789deabb2904965a1` and full uncached Go suite
`0bdbe4dda325490ab0e1de2ac2e625a2` passed. TypeScript Network.cloneState similarly
captures queue first and pins child clone callbacks; Kyoto build
`f461f0c5dd92425599009ffc92dfcb9d` and stateful suite
`de103001924d4530984c59efec2d763b` passed.
Python OracleState equality captures histories before model equality and defers
unused copy errors; paired suite `3d11a1074d664c768f03f2ec4d54d23c` passed.
Equality/hash consistency depends on value-preserving deepcopy hooks. Swift
Generate.withOracle enabled checks capture input before state cloning; focused
Kyoto `e3fe791f3214449aba41063fe912e7fd` passed. Scoped reviews found no further
concrete defects. Clone order and early hooks are observable, and capture is not
atomic against arbitrary cross-alias mutation.

Go Pair.CloneState snapshots pending address slots before input clone hooks;
Kyoto `b8ac05b5280b410589682b43e224f95b` passed the focused test. Direct clone
calls may still panic for malformed tags. Python automatic membership captures
query before domain callbacks and defers copy failure until comparison; Kyoto
search suite `10dcce4fd1064ef2a3ac6a4797fe385f` passed. Its query hook runs even
for empty/error domains. Swift Pair.cloneState clones pending inputs before child
state hooks; Kyoto `f245e9f337c04bd0b874c2616e46f99b` passed two tests.
TypeScript Domain.sampleIndexed defers unused entry-copy failures until selection;
Kyoto build `627aeb7a0419474abe74088146d2cfe2` and modeling tests
`6cbd71d25a38470fb3791999f7311a76` passed. All entry clone hooks still run before
the draw and selected value is copied again. Scoped reviews found no further
concrete defects. Clone order is observable; hooks remain cooperative.

Go Domain.Entries captures slice slots before clone callbacks; Kyoto focused job
`89d0b76267df47fabe3b1e44948fe6c8` passed. The structural snapshot is shallow.
Python Domain.sample_indexed defers unselected entry copy failures until after
selection; Kyoto modeling suite `04e76e6269264501bf7f60e648aada7b` passed. Entry
copy hooks still run eagerly and the selected value is copied again.
Swift Pair.checkTransition validates target/output tags before clone callbacks and
captures input before state clones; Kyoto `80b00cacc28e4ef68e2620b3e8cfb4c5`
passed StatefulCheckSnapshotTests after review fix. TypeScript shrink replay
captures input before state clone and pins enabled callbacks; Kyoto build
`2e07eee3a79b47c78e8cf499e87b2fc1` and search suite
`6621553769d34ef09e63e00a5da964a3` passed. Scoped reviews found no further
concrete defects. Copy hook ordering remains observable and hooks must cooperate.

TypeScript fuzz and shrink enabled helpers both capture input before state clone
and pin callbacks; clean Kyoto build `d04bb5a8b1a349328193c3b83f796f7d` and
search suite `d3caf7c83fb648c99d5dae0c6beeaaa0` passed. This extends the earlier
shrink-only status. Go Pair.CheckTransitionInto captures input and address
structures before state hooks; focused Kyoto `345118a87ebf4a9386d74014d9eeba57`
passed. Python debugger InputPolicy captures query before state/domain callbacks,
with deferred copy failure; Kyoto session suite `e86e9cb8494f4fd1ac56f17ef9cff013`
passed. Its query copy runs even for empty/error domains. Swift MetricSnapshot
histogram lookup was added; focused Kyoto `8b322182cb654e8cb8694eb56b6c3938`
passed one test. Scoped reviews found no further concrete defect. Copy hooks
remain cooperative and their ordering is observable.

Go PairCodec captures a bounded pending-address prefix before child encoders,
preserving original length and error precedence even for insufficient output
capacity; focused Kyoto `5c7d2645d065432e859c6cd120219b89` passed after review
fix. Prefix allocation scales with output-byte cap in address slots, not an RSS
limit; referenced payloads remain cooperative.
Python Pair equality freezes pending address structures and child references
before payload copy callbacks, with deferred unused copy failures; Kyoto
`4f5de24ff1314d089ef0f3b6741ce21f` passed. Swift Network.cloneState captures
queued inputs before machine-state hooks; Kyoto
`e3f4f84200244022b9f912c96cde9488` passed two tests. TypeScript Pair.cloneState
captures pending inputs and pins child clone callbacks before execution; Kyoto
build `78e368e1fc06420283b6a061b9909d6d` and stateful suite
`fc3cec29a5414198a90b7802d3b0ff62` passed. Scoped reviews found no further
concrete defects. Copy hooks remain cooperative and their order is observable.

Go NetworkCodec snapshots bounded structural prefixes before child encoders;
Kyoto `28bfb04d76404e50b670ca5e86261224` passed strengthened 31-byte capacity
and mutation controls. Snapshot is shallow and transient allocation scales with
output limit and struct sizes, not RSS. Python Domain.entries freezes entry slots
before deepcopy callbacks; Kyoto modeling suite `0b17d66589414a949a58f7ab415b761f`
passed. TypeScript Domain.entries captures slots and clone callback; Kyoto build
`38575beba91f4417baf53dc309180b73` and modeling tests
`9ec4302acc3e43139aad3a9b351fada4` passed.
Swift debugger admission copies input before state callbacks for session and
minimization adapter; Kyoto `a57bd46bd41e49dd9a4f3a6dbb05f34e` passed 17 tests.
The minimization control exercises its shared helper rather than a full shrink
trace. Scoped reviews found no further concrete defect. Copy hooks remain
cooperative and early invocation is observable.

Go debugger session/minimization admission captures input before state callbacks,
defers input clone panic until needed, and preserves terminal/no-op behavior;
Kyoto session suite `da39054a05fd463aab9defe0e665fabb` passed. The minimize
adapter lacks a full end-to-end shrink-specific regression; eager hooks can have
side effects on empty/error paths.
Python WithOracle.observe_transition captures input/history/transition structure
and pins advance before copy callbacks; Kyoto paired suite
`db92f0c13aff4b5781e95f59e5916dd5` passed. Swift fuzz and shrink enabled paths
capture input before state clones, and shrink uses retained input through witness;
Kyoto `fd83908a5c324c7aac7eae40601ce167` passed three focused tests and
`9e203e68ab1e4599a16de0f2698fa6e6` passed 14 SearchTests. Unused witness-copy
errors are deferred but hooks run eagerly. TypeScript Domain.product returns on
zero cardinality without traversing the other side; Kyoto build
`f66bf8f132f94f998c01164ada51c823` and modeling tests
`8b27777b7932430497fd6f306a26fafb` passed. Scoped reviews found no further
concrete defects. Copy hooks remain cooperative and order is observable.

Go Paired preserves actual projection errors over reference snapshot failures and
defers each reference-output capture error to its projection position; Kyoto
`e78245f774624a97af94505814cc9c2d` passed focused paired tests. Python Domain.product
returns without traversing the other side for an exact empty built-in list/tuple,
while custom sequences retain bounded iteration; Kyoto modeling suite
`1f4f5ab8cc1b44aeb34e0d482f041890` passed.
TypeScript Pair.equalStates freezes child references and pending address slots
before clone callbacks; Kyoto build `39ad2bb0d9994c359f6f58a662148f59` and
stateful suite `a3cb9960b298408a9917fe1cf5800240` passed. Swift Network transition
checks capture input before state cloning and avoid cloning queue actions with no
global check; Kyoto `d05a7c7005544a89a1c353c06936b2d3` passed seven tests.
Swift malformed mixed-output preflight still checks all tags before output clones,
unlike Rust's sequential clone/error order. Scoped reviews found no other concrete
defects in these changes. Early copy hooks remain observable and cooperative.

Swift Network transition checks now map outputs sequentially so an earlier output
clone failure precedes a later wrong address; Kyoto `5aa58148db45461a8e404fee2b78a48e`
passed ten focused tests. This supersedes the prior Swift mixed-output ordering
note. Earlier input/state snapshot hooks and child-check copies remain observable.
Go Network transition checks capture input before state hooks and skip cloning
for queue actions without a global check; Kyoto `59eec4d8601a4ce7824755dbf3870b96`
passed. Go still preflights all output tags before cloning, unlike Rust sequential
mapping. Python Pair.inputs captures queue count/state references before bound
child method lookup and does not copy unused pending payloads; Kyoto stateful suite
`9fd67267c4584386a60a160bcaa19bf0` passed. TypeScript Network.inputs pins child
callbacks and snapshots policy values; Kyoto build `4b3793cd81f54364afbbd582985e8a8b`
and stateful suite `52312ad3dff54f83b284b93de63e5c36` passed. Enumeration remains
lazy in Python/TypeScript. Scoped reviews found no further concrete defects;
copy hooks remain cooperative and their order observable.

Go Network transition output mapping now checks tags and clones sequentially,
with captured observation errors deferred; Kyoto `dcc8cc3697da45049f1832f75c05f142`
passed focused mixed-output, no-op and snapshot tests. This supersedes the prior
Go all-output preflight ordering note. Early observation hooks remain observable.
Python Domain.product captures built-in slots with a maximum+1 bound after custom
length callbacks; Kyoto modeling suite `1885d7cc1bb44795838770440ca533c0` passed,
including adversarial growth. Allocation remains O(maximum).
Swift searchEdge captures transition/observer observations before state checks and
defers errors to the appropriate stage; Kyoto `c321827ffb314e00bcbbcaaa5d832493`
passed four focused tests. TypeScript Network queue-only transition checks avoid
clone hooks when no global callback exists; Kyoto build
`16f10b1e92724d5e866b7100a563071a` and stateful suite
`69cd2a4715654e35b4b84a1edfec56f4` passed. Addressed-action output ordering in TS
remains open. Scoped reviews found no further concrete defects; copy hooks remain
cooperative and their earlier invocation observable.

TypeScript Network addressed transition output mapping now validates and clones
outputs sequentially, freezes target/callbacks, and preserves optional hook
lookup behavior; Kyoto build `6ada2c58d6b04dc49b44bfc4705d0fa8` and stateful
suite `ebbb4d6a18384c9a85a84dcdbce085dc` passed after review fixes. This
supersedes the earlier TS addressed-output ordering gap; early clone/callback
reads remain observable and hooks cooperative.
Swift Pair transition mapping likewise processes outputs sequentially; Kyoto
`691d62ed22ae463eb734033ab4470a4e` passed 11 focused tests. Go ObserveTransition
captures history before transition clone and retains completed transition before
returning input-copy failure; Kyoto `64cf015abd464414ad5212830ad8ff56` passed.
Python Network direct transition checks skip copying queue-only actions with no
wiring callback, while resolving descriptors once and preserving error wrapping;
Kyoto stateful suite `61c54742292e450dbcb3cebedc2fa23c` passed. Generic wrapper
still snapshots before dispatch. Scoped reviews found no further concrete defects.

Go Pair transition outputs are checked/cloned sequentially; Kyoto focused job
`cf28dd23de5d4294a49bcd1ec7126020` passed mixed-error and single-copy controls.
Python Network transition outputs are likewise mapped sequentially and avoid
redundant copies without callbacks; Kyoto stateful suite
`ec859b67b3bd428aafbd4f13fd7c0191` passed. Its generic wrapper still copies before
dispatch. TypeScript Domain.product fixes iteration bounds before slot reads;
Kyoto build `789c34d8f0fc44c6865e235ee96f4ecb` and modeling tests
`6dd1fcbb68d1484c98aab777876e555a` passed; getters can still mutate unread slots.
Swift BFS captures an enumerated input before reducer callbacks for checks and
witnesses; Kyoto `6006e33805a1400491ffc6361180bbdc` passed two focused tests.
It now makes an extra pre-reducer copy that may fail or have side effects, unlike
Rust borrowing. Scoped reviews found no further concrete defects. Copy hooks
remain cooperative and multi-value capture is not atomic against alias mutation.

Go BFS retains enumerated input before reducer for checks and witnesses; Kyoto
`2dbdfacbfe394442af5612894a064db6` passed two focused tests. Extra pre-reducer
copy may fail or have side effects. Python search-edge observation snapshots
precede state checks, with checker/observer errors deferred to the needed stage
and observer descriptor resolved once; Kyoto search suite
`6f6905474bbc493ba41bdd9fed9b530c` passed.
Swift WithOracle.observeTransition captures input/history before transition clone
and defers capture errors until retention; Kyoto OracleTests
`e57e394fe2504270a0c225276bda485e` passed 26 tests. TypeScript shrink replay
captures candidate input before callbacks and defers unused copies; Kyoto build
`1afd627e910a4056b82ad96f7991c700` and search suite
`aad185e7bad747b493092bd4ec2a9ad1` passed. TS searchChecks still captures other
transition arguments after state callbacks, an open alias-exposure gap. Scoped
reviews found no other concrete defects. Early copy hooks remain observable and
cooperative, and capture is not atomic against external alias mutation.

Go searchEdgeObserved captures edge/history before state callbacks, pins observer
and history slots; Kyoto `570c2d4eab9a452ba9370a7e8574538f` passed focused tests.
Python BFS retains enumerated input before reducer; Kyoto search suite
`d3228943152846f091dc77407e7d7688` passed, including the explicit second-copy
failure control. Swift initial search observer captures state before checks, with
error deferred; Kyoto `2eff58e20c1441e4b2b45991c16bd5a8` passed six tests.
TypeScript searchChecks now captures edge/history before state checks and pins
transition/clone callbacks, deferring failures to consuming stages; Kyoto build
`2d6f047aa3094c30971d1bbc8438c52b` and search suite
`85592cf733a14ec9ac0d7db4834fce2a` passed. This supersedes the previous TS
searchChecks alias-exposure note. Scoped reviews found no further concrete defects.
Eager callback/copy side effects remain observable and hooks cooperative.

Go initial search observer captures state and callback before state checks;
Kyoto `86f1d6e61a4840d7baf87e5b4626b442` passed focused tests. Python Pair
transition outputs map sequentially and pin callbacks; Kyoto stateful suite
`21910f14e9114c05aa4385463aa28906` passed after review fixes. TypeScript BFS
retains enumerated input for reducer, checks and witnesses; Kyoto build
`9729fed2fc7a44748763b63e29c65e5a` and search suite
`42c81e5ae803406c94f78d4719131193` passed. Swift guided feedback captures input
before enabled/reducer/history/check callbacks, deferring unused copy failures;
Kyoto `fc6c4d9ab7164486a4808e182493835f` passed 24 GuidedTests. Scoped reviews
found no further concrete defects. Earlier descriptor/copy hooks are observable,
cooperative, and cannot provide atomicity against external cross-alias mutation.

Go Pair.Step captures independent returned/wiring outputs before wiring callbacks;
Kyoto `b8ed863f8d2c4878be66e3d023bcb03e` passed focused regression. Eager output
clones scale with output count and can have side effects before earlier wiring
failure. Python shrink replay captures a transition-budget-bounded candidate
prefix before callbacks and defers per-position copy errors; Kyoto search suite
`9ae889d59df74d70a82726b8b2b2a031` passed. A pre-fix negative control failed as
intended. Early copies can run for unreached inputs.
Swift BFS captures initial/successor states before checks, deferring copy failures
behind violations/cancellation; Kyoto `6030e79e00304df7add022b76718924a` passed
seven focused tests (successor-side cancellation is not separately covered).
TypeScript debugger session/minimization captures query before state callbacks,
pins causal callback and preserves rejection/error precedence; Kyoto build
`83830fb828bc48239837eabb4a31a44a` and session/debug tests
`c9435f6ae110461ab2dfafb64cc84262` passed, including full minimization controls.
Scoped reviews found no further concrete defects; copy/getter side effects remain
observable and hooks cooperative.

Go shrink replay captures candidate inputs up to remaining transition budget
before callbacks, with per-position deferred copy errors; Kyoto
`a5aa5c39c49a497889035665eaccf8da` passed focused tests. Python fuzz/guided
execution captures generated/reused inputs before admission and uses the retained
value for checks/witness/feedback; Kyoto guided suite
`cf01f433b9324bf291c91f4331dfd4a8` passed. Swift fuzz captures initial/successor
states and feedback observations before callbacks, with unused failures deferred;
Kyoto `103fd332846544a589bf579895e13de6` passed focused tests. TypeScript initial
search observer pins cloneState and captures state before checks; Kyoto build
`631610f62daa4a9dab2da54d1b5ac4cb` and search suite
`7c45e42ea79c4aa2951795c157124356` passed. Scoped reviews found no further
concrete defects. Eager copy hooks remain observable and cooperative; capture is
not atomic against arbitrary cross-alias mutation.

Go fuzz captures initial/successor and guided feedback observations and protects
execution/feedback inputs across admission; Kyoto focused fuzz/guided suite
`9f648011407849089c3135f6e10124f3` passed after review fixes. Python fuzz captures
initial/successor/check/feedback observations with staged errors and a distinct
missing-state sentinel; Kyoto guided suite `9c15ec81cbf24cffb669b2102f6886a1`
passed. Swift WithOracle equality defers history copy failures until actual states
compare equal; Kyoto `84285454a3be44e886658dbea4dfe981` passed 27 OracleTests.
TypeScript fuzz captures admission/execution state, input, successor and feedback
observations before callbacks; Kyoto build `cf1356b9e127478086dc904ea61f6f4f`
and search/guided suite `99f9f9e4c9ef4a08ac3c97943f7f44e8` passed. Scoped
reviews found no further concrete defects. Early copy hooks are observable and
cooperative; multi-value capture cannot be atomic against arbitrary cross-alias
mutation.

Go fuzz captures admission/execution state and check/feedback transitions before
relevant callbacks, pins observer before step, and defers unused failures; Kyoto
focused `91862c72f3154e40a1fa35bbce974d6d` and full uncached suite
`3a020bc908064d28aaffc0b98a0a5e57` passed. Python BFS captures initial/successor
nodes and check/observer edges before callbacks with staged fallback and a valid
None-state sentinel; Kyoto search suite `3f93dbf113634cbbbabd04ea01b9f0d9`
passed after review fixes.
Swift fuzz captures execution state and separate check/continuation observations
before admission/history callbacks; Kyoto `c40a6986e32b4b548b666c1a280d1ef4`
passed 13 focused tests. TypeScript shrink replay captures candidate length and a
budget-bounded prefix before initial callbacks; Kyoto build
`3f16b1a7e5a44be4b566fbd789634281` and search suite
`a5d8a44d53f64b499fc643e1a3434b68` passed with shrink/grow controls. Scoped
reviews found no further concrete defects. Early copy/descriptor hooks remain
observable and cooperative; multi-value capture is not atomic against arbitrary
external cross-alias mutation.

Go BFS retains initial/successor nodes and prepared edge observations before
checks/history callbacks; Kyoto `18a8964690464d699203886ce7a2d36a` passed five
focused tests. An additional mandatory check-state clone may fail before the check;
successor-side cancellation is not separately tested. Python Generated.is_enabled
pins its callback before copy hooks; Kyoto search suite
`6bca3aa937e14c2ea7458548ba6411a6` passed.
Swift shrink replay captures a transition-budget-bounded candidate prefix and
separate step/check/witness copies; Kyoto `4b9bd175fa664007abf25fbf7fc8df79`
passed 22 SearchTests. TypeScript WithOracle.observeTransition pins adapter owners
and callbacks, with unused output-getter errors deferred; Kyoto build
`ae8152956d3645b6b203f2adc3996135` and paired suite
`0be7c1e2c6d942418f8a498e8fa42158` passed. Scoped reviews found no further
concrete defects. Earlier copy/getter side effects remain observable; multi-value
capture requires cooperative hooks and is not atomic against external mutation.

Go cloneInputs captures slice slots before application clone callbacks; focused
Kyoto `8bbdea4f5df14a11bbd90bbc57f89fff` passed. This adds a second temporary
allocation proportional to slice length, shallow until clone hooks run.
Python Paired pins adapter/check/projection hooks and defers actual output and
disposition copy failures to their projection stages; Kyoto paired suite
`e863d3f8748e443582203a17f7b368f3` passed. Swift searchInitialChecks captures a
separate check observation before observer copy; Kyoto
`e6f4ccc5c5fa426d90ac65f5d4e36b60` passed seven tests. The extra clone may fail
before the mandatory check. TypeScript BFS captures node/edge observations before
checks and callback getters, pins consumers and stages failures; Kyoto build
`c45c356cc50b4c72959092576499bb32` and search tests
`e5f7cfb8f2bc48f5a5517b30c9b84cd3` passed. Scoped reviews found no further
concrete defects. Eager copy/getter effects remain observable; snapshots require
cooperative hooks and cannot make arbitrary external mutation atomic.

TypeScript fuzz initial checks now use a separate pre-continuation snapshot, with
snapshot errors reported through checkError; Kyoto build
`3e41ba2e243c4854a2ff9d57f903245d` and search tests
`a8f0017314e24ceeac11dbbea7baf483` passed after review fix. Swift shrink replay
retains continuation and check observations before callbacks, staging unused
errors; Kyoto `b8e3b05b13ff4683aa1588ed3c9bb12e` passed two focused tests.
Scoped reviews found no further concrete defects. Python recorder/fuzz and Go
modeling/generation scopes were inspected this pass without a defensible new
implementation gap; no changes or tests were made for those inspections. Earlier
copy hooks remain observable, cooperative and not atomic across arbitrary aliases.

Python protocol watch observations now carry failed property IDs with explicit
incompleteness for omitted or truncated IDs, and bound scanning to 1,024 checks.
Kyoto protocol-services `245f5b18cf754593b742e879391caea9`, watch
`7aabecbc779443be996cac77aed600fe`, and bounded-scan regression
`862e6c40fe904c0bbe4ca2704b2af048` passed. Swift BackgroundJobs shares its
reducer between exploration and a controlled async task host, including a
cancelled task completing late while a newer job is active; Kyoto
`294a2a9805eb4f4289bb32dea53d0986` passed its graph and host assertions.
Independent static recheck found no concrete host issue. This example uses a
local package dependency and controlled simulated external work; installed
consumer and broader native qualification remain open. Go and TypeScript API
inventories found no substantiated additional gap in the scopes inspected, not a
complete behavioral parity audit. `git diff --check` passed.

Continuation: Go LifecycleOracle now snapshots retained history, output slots,
and input events across classifier callbacks; mutable output elements remain
caller-owned. Kyoto `15bd96f3779c4a82a72a879cbb11f751` passed focused lifecycle
tests, with independent static recheck. Python and Swift watch control snapshots
now report incomplete truncated failure metadata consistently with observation;
Swift also bounds automatic failure-check scanning. Kyoto Python watch suite
`ae676af8a6834f42a23d9b1165d35ee1` and Swift watch-filtered suite
`8f86e5276966481aad45aae4d26d1e56` (73 tests) passed. Rust snapshot_now applies
the corresponding metadata completeness rule; Kyoto
`e9b1e1e097e745fa8c3549693dd6d644` was compile-only under shared policy, and a
matching direct execution passed the focused regression. Static review found no
further concrete defect; preservation of an existing partial projection reason
was not separately covered by the new Rust regression.

Python and TypeScript background-job examples share reducers with finite
exploration and exercise controlled async hosts with stale-completion controls.
Kyoto Python source-tree execution `6afdfbdff68349ee83f3fa0799a8ada6`,
TypeScript build `0ad48f0681b24091b76cec7be797272c`, and final example execution
`e29a73ff41424d3695403ab4dd8bac60` passed. Independent static reviews found no
further concrete defects. The hosts simulate external work; the TypeScript work
ignores cancellation after its signal is sent. These do not establish installed
consumer or platform qualification. A scoped macro export inventory was added at
docs/native-parity/M0-MACRO-SYMBOL-MAP.md; it is not a completed M0 ledger or
behavioral qualification. Broader parity and release gates remain open.

Continuation: TypeScript control snapshots now mark truncated property, identity,
and schema metadata incomplete while preserving an existing projection partial
reason; Kyoto build `b08c922110e649a0819ecde1010e5c67` and watch suite
`d75687ddd9a547eb9382e4bfe85f1a9c` passed. Python LifecycleOracle snapshots
outputs before the input classifier with deferred copy errors; Kyoto stateful
suite `e61b3305252d4cfa81f04b72ce080a11` passed. Swift LifecycleOracle snapshots
history and input-event keys across callbacks; Kyoto focused regression
`e5ab89f9a51d402b899f004eb96bd0a1` passed. Copy hooks are observable and require
cooperation; Swift reference-valued input/output elements remain caller-owned.
Independent static reviews found no further concrete defect in these scoped
changes. Initial Python and Swift jobs failed due to missing PYTHONPATH and test
argument types respectively; corrected reruns passed.

A proposed Go network snapshot change was reverted after review showed existing
child argument isolation: Kyoto `53c8195a0c59474aa367fc3284838949` confirmed the
candidate tests passed on the pre-change implementation. The macro symbol map
was independently checked against receipt, cited source symbols and hashes; this
is static inventory review, not behavioral qualification. `git diff --check`
passed; full native parity and platform qualification remain open.

Continuation: Go watch control snapshots now mark truncated failing-property IDs
incomplete; Kyoto `264477bd05604aa289a3cb91e53b91e9` demonstrated the prior
failure and `19017a849cd74152876d86c5bb08ff27` passed focused tests. Python watch
schema metadata is UTF-8 bounded and marked incomplete on truncation, and inspect
label validation bounds work before encoding; final Kyoto watch
`5e599533f0f34b91a81abc89c0c126c4` and inspect
`c1b7ba61b0104d6e90df4b11ab9c0240` suites passed. Oversized invalid labels now
report the size limit before invalid UTF-8. Swift lifecycle oracles accept an
optional output clone hook to isolate mutable reference outputs; Kyoto focused
`55fdc4ab8d834239aff25d5faaa26ded` passed. Default identity still requires stable
reference outputs. TypeScript lifecycle captures output slots in a plain array
before input classification; Kyoto build `10fc48412a9546b2b0592b5489274382` and
stateful suite `885d48ada09847f797d57cd0df509aa5` passed. Snapshot elements are
shallow and accessor/copy hook side effects remain observable. Scoped independent
reviews found no further concrete defects.

M0-RESEARCH-SYMBOL-MAP.md accounts for 120 compiler receipt records against
candidate native APIs. Independent static review checked rows, source identities,
and named candidates; it does not establish behavior or complete M0. Full
parity, installed consumers, performance and platform qualification remain open.
`git diff --check` passed.

Continuation: Go watch events retain full schema identity while bounding emitted
schema metadata at UTF-8 boundaries and reporting truncation. Old-source Kyoto
`52383c1705ea4922a7826d9a981fe78d` failed the regression; final focused job
`8d481a4e7de24e368e758aeb92cb3cc0` passed. Swift supports full 4096-byte schema
identity with 1024-byte event metadata; baseline accounting includes full names.
Kyoto `e6be5637b6bd48a781950709ba1e1069` passed 57 watch tests. Python opaque
inspection bounds scanning by max_work and stores only a UTF-8 preview; Kyoto
`8ce838fd7bd14b21a7ff6380d82062d3` passed, with one existing Rust fixture test
skipped. Invalid UTF-8 beyond exhausted work now reports the work limit first.
TypeScript debugger map lookup compares typed keys instead of unrelated object
properties; Kyoto build `685f41e114ad48e8b8170bd593c158f3` and inspect suite
`239a87db7fe6484b9f754ba250651cf0` passed. Each scoped change received an
independent static review with no remaining concrete defect.

M0-MONITOR-SYMBOL-MAP.md covers 29 compiler receipt records with native API
candidates and unresolved obligations; independent static review checked rows,
source hashes and candidates. The pinned and current Rust source differ, as
recorded. This is not behavioral qualification or a complete M0 ledger.
`git diff --check` passed. Full parity, distribution and platform gates remain open.

Continuation: Python built-in string inspection now reports invalid Unicode as
InspectError and honors work-limit precedence; prior-source Kyoto
`ae49440dc9ba49b281e88c001068b44a` failed the regression, and final inspection
suite `00b7ba0ae429476783d8880721cbf033` passed 19 tests with one pre-existing
Rust-fixture skip. Go opaque labels now validate UTF-8 beyond the preview within
work budget; prior-source Kyoto `7603898d319d4d6d937fb7c446c419cc` failed and
focused `03baae1e438f4f15b55091ed603f7cbb` passed. Unlike Rust prevalidated str,
long valid Go labels can exhaust the scan budget, as documented. TypeScript direct
inspection validates typed keys and malformed paths, rejecting over-depth arrays
before scanning slots; final Kyoto build `a0e306afe6c74bdaa9391d40bc4998d0` and
inspection suite `bc2af34d17104128a6dab6be173d7008` passed. Scoped independent
reviews found no further concrete defects. Swift inspection, network and campaign
scopes yielded no substantiated change; this is not a full parity audit.

M0-ORACLE-SYMBOL-MAP.md maps 78 compiler receipt records to candidate APIs with
source identities; independent static review checked records, hashes and named
candidates. This is not behavioral qualification or a complete M0 ledger.
`git diff --check` passed; broader parity, distribution and platform gates remain
open.

Continuation: Go string and TypeScript opaque inspection validate full UTF-8
within work budgets while retaining bounded previews. Prior-source Kyoto jobs
`b372e7c95c744111995ca10c53d6d2d5` and `e1cff54709e540878844d2b0174ae28e`
failed their regressions; final Go debug `eb6d47f9271545caad0012ed6a420972`,
Go UTF-8 boundary `9a6458ac500e45c69aed467f7e2f3d5f`, TypeScript build
`153f682ab7eb4224a1afe36666bf9d95`, and inspect
`a57949d9d404495eb95456d472b1a8dc` passed. Long valid strings may exhaust work,
unlike Rust prevalidated str; this is documented. Python opaque preview now uses
configured value limit rather than metadata limit and bounds custom adapter output;
prior-source Kyoto `dbc4683486444fe3a5c0f42649c67f2e` failed, final suite
`f875cc9fa9684d90b054ad3100e88c29` passed with one existing fixture skip.
Independent scoped reviews found no further concrete defect in those changes.

Python retains a documented 256 Ki-code-point per-string payload admission cap.
Removing it requires budget-aware exact sizing through inspection, public node
completeness, watch eviction, and diagnostic accounting; approximate accounting
would corrupt retained-byte totals. This remains an implementation gap. Swift
value codec, RNG, guided and inspection scopes showed no substantiated new gap,
not a complete audit. M0-GUIDED-SYMBOL-MAP.md maps 85 receipt records; independent
static review checked rows, hashes and candidates, not behavioral equivalence.
`git diff --check` passed; wider parity and qualification gates remain open.

Continuation: Python inspection now supports budgeted large values with exact
chunked UTF-8 sizing carried through watch capture/eviction and configurable
diagnostic projection and baseline accounting. Kyoto debugger suite
`ee4d649299924b7a8f56657a9d181c59` passed 211 tests with one existing fixture
skip, including large multibyte watch and diagnostic capture/eviction. Public
unbudgeted is_complete and diff retain their 256 Ki-code-point default; use
complete_with_budget or explicit diff max_chars for larger nodes. Diagnostic
projection value/work defaults are unchanged. Scoped independent review found
no further concrete defect.

Go direct inspect rejects malformed typed path segments before value Inspect;
prior-source Kyoto `3960e2425c9e455da3fedad460c75d7f` failed the regression and
full debug suite `c087ee90c1784dbaa4d725e2b542db69` passed. Existing schema
callback still runs first. TypeScript enforces custom inspection value-byte
limits including nested nodes; prior-source `45f3b7a01922476c9af2d8edd90a4a8c`
failed and final build `1313929d833c47fd963b2126ebc584e3` plus inspect suite
`e20f473b3559449891341fc316d0f7c3` passed. Swift watch validation bounds UTF-8
rejection traversal; Kyoto `4c4e2c954d624137bd9df8b36c9110f6` passed 57 watch
tests. Scoped static reviews found no further concrete defects. M0 automatic
symbol map covers 54 receipt records and was independently checked against
hashes and candidate APIs; it is not qualification. `git diff --check` passed;
broader parity, installed-consumer and platform gates remain open.

Continuation: Python watch validates schema before retaining it and counts full
baseline schema, with separately bounded validated identities; prior-source
Kyoto `c94016e29c9f4903b501fcf77cde036b` failed and debugger suite
`b4ba68a7355b4235bc51b3968c26dc2e` passed 223 tests with one existing fixture
skip. Go root truncation reasons are independently capped at 32 bytes; prior
`ac3ab28408ff44ebbccabdad9ac11839` failed, debug suite
`b077d563308f44a284da7690afc00e46` and final focused
`28fc66f9be8940469526328d51ba589c` passed. TypeScript custom child paths are
bounded and validated; final combined tests below include this path.

Rust local MapKey integer decimal is arbitrary String, distinct from wire grammar.
Swift watch/inspection, Go local inspection and TypeScript inspection/watch/
diagnostic now preserve bounded arbitrary key text while wire parsers retain
numeric restrictions. Swift Kyoto WatchTests `50512185f19d4449b3024bd02b32b724`
and InspectionTests `82e5719a70ce4deba08d99d2421d5cdd` passed; Go debug suite
`9c89a0de2ad34aeeae886d4d3e13ad3f`, final TypeScript build
`d247f98a5e2046a188ab0b69ed6a1908` and combined suite
`c1d436bf609f45739d34ccfbeb41f109` passed. Independent scoped rechecks found no
further concrete defect; arbitrary-key TS sink permission is not separately
tested. M0 lifecycle map covers 66 receipt records and was statically reviewed;
it is not behavioral qualification. `git diff --check` passed. Broader parity,
installed-consumer and platform gates remain open.

Continuation: Python local Rust-typed integer map keys and scalar identities now
preserve bounded arbitrary text, including watch/diagnostic equals and permissions;
inbound wire parsing retains numeric validation. Prior-source Kyoto
`a5cc6097219e4beead256db14a041c74` failed and debugger suite
`e31c2d02b4de47e5ab826f0da094228b` passed 226 tests with one existing fixture
skip. Go watch authorization compares active typed segment identity, validates
malformed segments, and bounds config before authorization; diagnostic sinks
share this path. Prior-source `84d665908db04c21ad0b0c9ee03aae1a` failed and full
Go debug suite `d5803c73853b42a5b885570722691b9e` passed. Invalid/oversized
config now takes precedence over unauthorized. Independent scoped reviews found
no further concrete defects. Swift network, generation, campaign, recorder and
research and TypeScript lifecycle/guided bounded inspections found no new
substantiated gap; these are not complete parity audits. `git diff --check`
passed; full parity, distribution, performance and platform gates remain open.

Continuation: Python legacy lifecycle validation rejects indexed-only retirement
and publication fields that legacy encoding cannot preserve; baseline Kyoto
`9b6354bb1ea3491b8db1e242020c70a9` failed and stateful suite
`0c7b58c5bf3a44dcb8d40c10382924e3` passed. TypeScript diagnostic equals now
compares active scalar fields rather than object layout; prior-source
`eaddb449a03d4f10b088d01c790b59d6` failed, build
`ca1c49bd5b184db2918b6fda4543f100` and diagnostic suite
`b09aad9c208b4de7b4051e82e6579542` passed. Regression covers integer and kind
mismatch; other scalar branches were statically reviewed but not individually
covered by that test.

Swift local scalar inspection preserves arbitrary Rust integer/float identity,
with owned float kind capped at 4096 bytes and charged to budget; prior-source
`d4a07baf3b3b445998758a3afe158a42` failed and inspection suite
`e7bf3b58123c43038a56a368630d3c7d` passed. Go FloatScalar supports arbitrary
float-kind identities including collisions with integer kinds, and validated
watch acknowledgements return actual kind. Go full debug
`e43a2c0eaafa496b9ef09330d450c3cc` and final arbitrary-float tests
`eab7a57fcb904aae94aa2d1e0025a994` passed. Scoped independent reviews found no
further concrete defects. `git diff --check` passed; broader parity and release
qualification remain open.

Continuation: Go, TypeScript and Swift legacy lifecycle validation/encoding now
reject indexed-only retirement/publication state rather than silently discarding
it. TypeScript legacy encoding retains Rust's ability to encode representable
histories exceeding monitor retention. Python, Go, TypeScript and Swift legacy
publish/release predicates now examine all matching restored obligations while
preserving indexed semantics and first-match resource mutation where applicable.
Prior-source regressions failed for these cases. Final focused Kyoto jobs: Python
stateful `4acfe8313d9c44eaa535cc67cad2f091`; Go lifecycle
`18ff0b99733840b191cfbd095a4b8207`; TypeScript build
`e8f41c0e67c04f118a56e14e2b60e910` and stateful
`bfd65b73e75a4dfbb5dc38314f130307`; Swift StatefulTests
`0e9e3305ddcb4e8e8211f495ec077def`. Scoped independent reviews found no further
concrete defects. Go invalid legacy histories now report representability errors
before key/retention errors. `git diff --check` passed; broader parity and
qualification remain open.

Continuation: Python legacy history encoding now permits representable histories
above monitor retention, as Rust does; decode/advance still enforce limits. Prior
Kyoto `d80afca1f0a4469380828a55a2f45a7c` failed and stateful suite
`1f9bd9d4de57481b86d2916fc24b845e` passed. TypeScript validates u64 obligation
deadlines and indexed violation/scoped-key timestamps, epochs and generations;
prior regressions failed and final build `6272d1b21dac4553864f21f8244d0fc9` and
stateful suite `718b8997c25442b883aab9f066a3add0` passed. Go legacy history
equality compares encoded violation text including fallback Code; prior
`06838f78d8634c6cbc8395b1294c3613` failed and focused
`5dae25aff96146e1994183cc39e3a4c3` passed.

Swift and Go lifecycle oracles now validate history and handle prior overdue
violation failures before classifier callbacks. Prior regressions failed and
Swift StatefulTests `4937aef90ccc4ae7b5880e21676388e7` and final Go focused
`161e4c6f4359407ea730f1c4a5441765` passed. Python and TypeScript source checks
found this classifier ordering already present. Go indexed validation hooks may
run more than once and must cooperate; TS output-slot accessor effects remain
observable. Scoped independent reviews found no further concrete defect.
`git diff --check` passed; broader parity and qualification remain open.

Continuation: Go legacy lifecycle Checks now uses the exact legacy violation text
selected by codec/equality, maintaining detail through a codec roundtrip. Prior
Kyoto `f1d49a741844443e9f1924316cf558b8` failed; focused
`3dfe716b360249499b73e101f414dd9a` passed. TypeScript restored lifecycle state
now rejects nonboolean flags and non-undefined malformed indexed violation prior
values before checks/encoding; final Kyoto build `5f45aea165464f93a4a8bea3cf515dda`
and stateful suite `e946997aea064e3ba3852cd4817edf5a` passed. Scoped independent
reviews found no further concrete defect. Python and Swift bounded lifecycle/
codec inspections found no substantiated new gap, not a complete parity audit.
`git diff --check` passed; broader parity and qualification remain open.

Continuation: TypeScript Pair.checkTransitionInto checks output side before
calling that output's clone hook. Prior Kyoto `26e02be076574f8682cb5a6142acead1`
failed the regression; build `c365afec2f8040deb79043ba757aa085` and stateful
suite `c99862e30c58487a90d4f1d8e4c4c73a` passed. Scoped static review found no
further defect; earlier clone hook effects remain observable.

Added a Rust three-machine network producer/replayer and Python binary/JSON
bridge to the native stateful harness. The Rust fixture asserts independent
expected final state, queue and failure on record and replay, and a weakened
property control must complete normally with the expected passing observation.
Kyoto build `fb8f17aaa77847428c911f5f57507318` and Rust control/self-replay
`80f105bb274c4b19ad59d307df322526` passed; scoped static review found no further
concrete defect. Rust-to-Python replay `9a3dcd6d3b4849adaa0a6b7b42ea0e01` failed
as incompatible because recorder build fingerprints differ. No metadata rewrite
or relaxed identity check was used. The JSON v1 bridge cannot preserve binary
run configuration parameters and uses Python conversion, so this is not proof of
Rust-native JSON compatibility or run provenance. The 5x5 matrix and all broader
qualification gates remain open. N08/N10 bounded source inspections in Python,
Go and Swift found no substantiated additional gap, not a complete parity audit.
`git diff --check` passed.

Continuation: Rust bridge now carries binary RunConfig in a separately bounded
sidecar, retains strict JSON v1, and fails closed when the sidecar is absent or
mismatched. Kyoto sidecar suite `ea17e8b99f2b4f4ab4e0a875d5d5ac53` and final
Rust record/bridge/replay `85d9e40ad38a4345890df1c888ab9d82` passed. Harness
receipts hash both artifacts. Sidecar digest is a one-way association, not
authentication: identical JSON with differing configs permits swaps unless a
trusted receipt binds both hashes. Native JSON-only producers lack sidecars and
native-to-Rust conversion fails closed.

Added standalone native_stateful_identity.py pinned-manifest verification before
strict replay; Kyoto `009e7872468b4fc39a514d78f9d58eee` passed same-build,
cross-build incompatible, semantic mismatch, duplicate-key and tamper controls.
The mapping is caller-attested and does not prove the running model derives from
hashed files. No build-mismatch override or metadata rewrite is used; Rust/native
strict cross-build replay remains incompatible and the 5x5 matrix remains open.
Independent scoped reviews found no further concrete defects. Go and Swift N08/
N10 bounded source audits found no new substantiated gap, not full qualification.
`git diff --check` passed.

Continuation: all four native stateful fixture producers now write actual binary
audit traces with their recorded RunConfig; bridge converts to JSON v1 plus
sidecar and asserts exact audit reconstruction. Kyoto producer jobs Python
`e09f6555343a44ffb99137215f31e689`, Go `c3efb7cb9fdf49ad93e9b5145d5d5e6b`, TS
`1f8b99ddda9e496d8ba3f2a628ff8dcc`, Swift `df613484c82946278a8d26d2deeeb504`,
and four-producer config conversion/self-replay `2d206680031a46b692f6d616371704b1`
passed. Integration used a temporary external script; bridge unit suite
`a6749b5da9724a50873263e3412a5bb8` passed. Python conversion and trusted receipt
limitations remain.

A separate explicit cross-build observation profile follows the existing
REPLAY-004 policy, runs strict replay first, requires strict incompatibility and
build_matches=false, checks pinned caller-attested metadata/artifacts, then allows
build mismatch only for labeled observation comparison. Strict replay unchanged.
Kyoto verifier tests `246d9d5ed403421eb5706edfc0b8cef2` and actual Rust-to-Python
smoke `75a3e8e283414654a20755b1a9ad4c1a` passed; smoke compared three steps and
reproduced failure. Manifest is self-attested and does not establish execution
provenance; this is one direction, not exact-build replay or a full 5x5 matrix.
Independent scoped reviews found no further concrete defect. Python arbitrary
float kind identity now propagates through inspection/watch/diagnostic/protocol;
Kyoto debugger suite `d5a224d435574ea69e3966d24f251f54` passed 227 tests with
one existing skip; float-kind names retain a 4096-byte metadata cap. Full frozen
source matrix and broader qualification remain open. `git diff --check` passed.

Continuation: stateful harness now has separate explicit REPLAY-004 observation
matrix, preserving strict matrix and its qualification failures. Kyoto mutable-
source integration `4d7531670a354dee9a0902f28aea594c` passed all eight directed
Rust/native comparisons and wrong-consumer-build controls; each requires strict
incompatibility, exact three-step comparison and reproduced failure with
build_matches=false. Equal native metadata strings are labeled
reported-build-match-strict, not identical artifacts. Selected source file set and
contents are rechecked at end. This is not frozen 5x5 qualification; manifests
remain caller-attested and only Rust has independent N08 expected/fault control.

Persistent scripts/test-native-stateful-config.py runs actual four producers,
sidecar roundtrip and native self-replay; final-source Kyoto
`7a92d8e6ed654fa287e22a2dd019c66c` passed, including optimized Python positive
and incompatible-build negative controls. This is source-tree integration, not
installed/frozen proof. TypeScript scalar inspection now uses overall maxBytes
rather than preview maxValueBytes; prior `a9a07dc415444c7eb802a40c2a82d127`
failed and final build `ec59deb0026646ba9b11db1ca190199d` and inspect suite
`768d94dcf4944465a043990b730b2c2c` passed. Independent scoped reviews found no
further concrete defect. Full parity, independent per-engine fault controls,
provenance and release gates remain open. `git diff --check` passed.

Continuation: Python, Go, TypeScript and Swift native network fixtures now check
an independently specified three-step state, queue and global failure on record
and replay. Each runs an actual weakened threshold control and requires normal
three-step completion, the expected state/queue and passed global check; errors
or early stops cannot count. Qualification harness and persistent config test
require successful control exit plus exact detection marker. Kyoto source-tree
integration `458c179d71284f3c80e1915ffa18e2e5` passed all four native controls,
config roundtrips and self-replays. Independent static reviews of all four
fixtures and both harness scripts found no concrete false-success defect. This is
fixture-level, caller-attested source-tree evidence, not frozen 5x5, installed
consumer, general N08 or full parity qualification. `git diff --check` passed.

Continuation: frozen-source stateful Kyoto `1eecf70291bf4afd9cbb5575614edd86`
exited 1 solely on eight strict Rust/native build incompatibilities. Source set and
content verified; 17/25 strict cells passed, while the separate observation matrix
passed 25/25 (including eight explicitly cross-build comparisons). Python passed
909 tests with 13 skips; Go suite passed; TypeScript passed 1518 tests; Swift
passed 651 tests. Four installed consumers and four network controls passed on
this host; all four lifecycle semantic mutants were detected. These results do
not establish strict cross-build replay or other platform qualification.

Preserved receipt `frozen-stateful-bhL4qf/` includes source manifest, logs,
results, trace/config, mutation evidence and hashes; independent review verified
98 copied files and their hashes against the original temporary receipt. Binary
artifacts are not copied, so their recorded hashes cannot be reverified from the
preserved receipt alone. Fixed TS CLI test cwd resolution, mutant requirements
staging and compile-safe fence mutation, Swift fixture clean error exit, and
Python wheel staging outside the snapshot; focused checks and independent reviews
found no weakening. Python strict_loads now rejects oversized text before UTF-8
allocation; prior `dd6417eb3b51469fb5087d5b114866cb` failed regression, final
native suite `5f1d8eeab2c24fce9f472ff85912f0bf` passed 32 tests. Oversized
invalid Unicode now yields byte-limit precedence. `git diff --check` passed.

Continuation: N08-N09 contract says engine builds differ; REPLAY-004 requires
strict mismatch rejection and separately permits explicit build-mismatch
observation replay. Harness now records structured expected strict-negative
controls, requires incompatible/build_matches=false/zero steps and exact separate
observation comparison, fails missing/malformed cells, retains strict labels and
does not count generic nonzero as detection. Build-only decoder/observer spy
controls with active positive controls passed for all five engines (native tests
cover bounded/unbounded). Rust spy was compiled directly through Kyoto and
executed exactly once by the harness; source and executable linkage remains
caller-attested.

Frozen Kyoto `c581378a825a4da19db7ab927d087c59` passed: 25 observation outcomes
comprise 17 reported-build-match strict and eight verified strict incompatibility
plus explicit cross-build comparisons; this is not 25 exact-build replays.
Four semantic mutants detected; Python 911 tests/13 skips, Go suite, TS 1519,
Swift 652, host installed consumers and controls passed. Source set verification
passed. Preserved receipt `frozen-stateful-hpkBhg/` includes 99 log/manifest files,
Rust spy binary and checksums; independent review verified copied files and hashes.
Other binaries omitted; matching native metadata does not prove identical
artifacts. Snapshot predates subsequent documentation-only live edit. Python
strict_loads now bypasses str subclass encode override; Kyoto
`419e2af7fac74cf2b1cf6e6445e0881c` passed 33 native tests. Composition symbol map
covers 94 receipt records and was independently checked as static inventory.
Full N01-N14, other platforms, provenance and release gates remain open.
`git diff --check` passed.

Continuation: recovered N06 five-engine comparison component
`scripts/qualify-n06-matrix.py` and Go/TypeScript 49-case public campaign
adapters. Actual Kyoto run `99a11eb670674fce86613ee10a93e8ed` produced 49/49
ordered rows matching independently sealed corpus for Rust, Python, Go,
TypeScript and Swift. Independent review verified raw outputs, seal, command/log
hashes and 807 declared artifact hashes. Receipt `validation/native-parity/n06/
comparison-66/` preserves logs and failed setup attempt; binaries omitted. Python
wheel and TS tarball were externally installed; Go/Swift staged source, Rust
current-source binary. Source snapshot, execution provenance, host fault,
multiworker and mutation qualification remain open; manifest is caller-attested.
Comparator has bounded output, finite timeout, process-group checks, exact seal,
pinned artifacts, sanitized env and fail-closed 49 rows. Final tests
`7efcf855621a45c185a11922e0c5ed1a` passed nine controls; escaped process groups
are outside containment claim. Independent scoped reviews found no concrete
remaining defect.

Python feedback metadata unwind classification now matches Rust in campaign while
preserving direct guided ModelError behavior; campaign/guided suites passed and
installed consumer matched 49 rows. Swift campaign helper rename fixed compile
collision. TS strictParse and Swift traceFromJSON now reject oversized text before
full UTF-8 allocation; focused Kyoto tests passed. Swift behavior test does not
measure memory or distinguish old allocation. `git diff --check` passed; full
N06 and overall parity/release remain open.

Continuation: source-isolated N06 multiworker mutation controls detected actual
engine faults semantically. Python Kyoto `bb1fa8d309f146b4b16ac6b691269033`
checks spawned-worker/sink precedence; Go `b81d3495fe034997ac1b65aa6e4228eb`
checks two-worker sink precedence after separately compiling mutant; TypeScript
mutant build `b44c64d91ee0478ca56206a7dc4507ec` succeeded and test
`2b9f60537cae482a90d5360dc5c0d605` failed expected sink count; Swift runtime
suite `5b7bd50184394fba8fb41d7bca0c61fa` passed 32 tests and compiled mutant
`a9368e50656e469fbb7a3d965e1318ec` failed expected lost-work accounting check.
Independent reviews verified expected assertions, no crash/compile/timeout
counted as detection. These are source-tree, single-host controls, not frozen or
installed qualification; Go initial worker-loss mutant panicked and was excluded.

Added scripts/prepare-n06-snapshot.py with selected source file/mode/status
manifest and before/after verification. Kyoto unit `aa6dd592e655473c94c071ccbc059b67`,
snapshot `6fd72afb52b84d3f8f1dcb9a8c4cb124`, direct Rust build
`e9197c7777744e5b9d0fa9845a259646`, post-build verify
`52dfd0925b4c4d05b2cb7aba0b0cab9b` passed. Independent review found no concrete
issue. Snapshot is writable and build/source linkage caller-attested; full frozen
five-engine build/install orchestration, per-platform gates and complete N06
qualification remain open. `git diff --check` passed.

Continuation: implemented scripts/run-n06-snapshot.py two-stage frozen workflow:
Rust reference built directly through Kyoto, then verified snapshot stages native
consumers and executes sealed 49-row comparison. Fresh snapshot Kyoto
`a1d5eaab78ca45e9af1e809507096879`, Rust build
`722134254d5540da938bac17c0d9fd49`, workflow
`2dc6a09a277a4fcc9a177bc138c6cc6b` succeeded. All five emitted 49 ordered rows
matching sealed corpus; selected snapshot 1301 files verified before/after.
Independent review verified raw output, 853 present artifact hashes, commands,
logs, source manifest and preserved receipt `validation/native-parity/n06/
frozen-comparison-68/` (52 checksum entries). This is macOS arm64 49-row
comparison only, not full N06: snapshot writable, source-to-binary/execution
linkage caller-attested, pip build-isolation backend unpinned, Go/Swift staged
source, full environments/staged trees omitted from preserved receipt. Multiworker,
host-fault and mutation controls are separate source-tree evidence; other
platforms and remaining N06 requirements remain open. `git diff --check` passed.

Continuation: N06 snapshot workflow now requires SHA-256-pinned setuptools
80.9.0 wheel, verifies/copies it, installs offline and builds without isolation or
index. Wrong/missing/tamper and snapshot-bound controls passed. Shared snapshot
`/private/tmp/n06-fault69-final2` manifest
`441aa8eab3acd3b6de24ffc17a1c808fb4398185298b1432ed5867048ba30e84`: direct
Rust build Kyoto `fc64c4e98b844826af35c84cc37d649b`, five-engine 49-row
comparison `1e930a19567e4d9a82e4be3b9b8bcc4a` passed; all 18 stages and 853
artifact hashes verified. Same-snapshot fault runner
`b7666d4c1c19498c864124a28ac5ce4f` passed four semantic mutation controls,
source verified after. Prior run failed safely on TS reporter parsing; retained.
Independent reviews checked outputs, logs, same source-manifest identity and
preserved `validation/native-parity/n06/pin69-final/` and `fault-controls-69/`
receipts (77 checksum entries). Caller-attested source/executable linkage,
writable snapshot, unpinned interpreter/pip/toolchains, Go/Swift staged source,
inner Python/Go child-output buffering, escaped process groups and omitted full
environments remain limitations. Single-host controls do not establish full N06,
other-platform, performance or release qualification. `git diff --check` passed.

Continuation: N06 campaign self-model suites executed from one verified source
snapshot: Python 8, Go 3, TypeScript 28, Swift 18 passed; Rust Kyoto compile-only
followed by direct execution passed 15 lifecycle tests. Receipt
`validation/native-parity/n06/self-model-70/` independently reviewed. Optional
Rust persisted-fault case returned early without env path and is not fresh-process
replay evidence. Binaries omitted and source-to-executable linkage caller-attested;
Swift emitted Sendable warnings. Go two-worker active cancellation barrier test
Kyoto `aff292a95127459487a146435bb6e0e8` passed, ensuring both claimed jobs are
delivered cancelled and six remain unstarted.

Integrated build-window checkpoint into N06 snapshot workflow, requiring before/
after selected-source verification, exact direct Kyoto Cargo job/log and binary
hash. Fresh snapshot `4fd581afd8834641990b871a679b76ba`, Rust build
`d88f396d24154971827b5cd87a018bc4`, full comparison
`e8ec642591cb494ba1123b8d55ed3e12` passed all five 49-row outputs. Preserved
receipt `validation/native-parity/n06/build-window-70/` includes checkpoint/window,
logs/manifests and selected binaries; independent review verified checksums and
outputs. Snapshot writable and linkage remains caller-attested: transient changes
and post-build artifact replacement cannot be excluded. Single-host source-tree
self-model and comparison do not close installed/fresh-process/cross-platform,
all host-fault, performance or overall N06 gates. `git diff --check` passed.

Continuation: Rust actual campaign duplicate-delivery fault discovered and shrunk
5→3, persisted then replayed in a separate process with exact witness/failure
identity and healthy strict/relaxed controls. Kyoto compile-only
`32571f8fddf9489c94d04e41fbeff95b` succeeded followed by direct producer/replay
execution; original five-step trace fails intended negative assertion. Receipt
`validation/native-parity/n06/rust-persisted-71/final/` independently checked;
source-to-binary caller-attested, not frozen/cross-engine.

Python installed wheel seven selected self-model/real spawned-worker tests
`653e38313a404b77ba2b6c1917d434f4` passed, imports verified in parent/children;
receipt `n06/python/installed-selfmodel-71/` verified. TypeScript external npm
tarball self-model 4/4 `18e4d94992394d78b9b4d9be4159b243` and worker 4/4
`0fed5674f3774892982b818aa6c60ef7` passed, installed dist verified; receipt
`n06/typescript-installed-71/`. Go external staged internal package seven tests
`82d8f4eed2ad412a854f770855f214f2` passed; 279 staged source files/modes match
snapshot; receipt `n06/go/staged-selfmodel-71/`. Not installed Go proof.

Swift external public consumer initially failed stale fixture expectations;
corrected exact values against sealed corpus, no result mapping. Final host
checks `63f2904647234576af8af7210abdb68f` passed 14 groups; receipt
`n06/swift/external-consumer-71/` preserves failed attempts. Engine snapshot plus
corrected fixture is mixed-source, local SwiftPM staging; internal @testable
self-model tests did not compile as external public consumer and no pass claimed.
Independent scoped reviews verified receipts. All evidence single host and
caller-attested source linkage; other platform and full N06 gates remain open.
`git diff --check` passed.

Continuation: Go external public-only consumer via private file module proxy and
isolated cache passed two-worker cancellation/accounting Kyoto
`adac74c96bde40758b3ae09ed7127c92`; receipt
`validation/native-parity/n06/go/public-host-72/` independently verified. This is
one public host control, not external campaign lifecycle self-model; Go's actual
lifecycle law/types remain unexported, as is Rust's. Proxy zip not preserved in
receipt and source linkage caller-attested.

Swift staged internal self-model runner `validation/native-parity/n06/swift/
staged_selfmodel.py` verifies snapshot and copied source paths/hashes/modes
before/after, including failure path, and requires named XCTest passes. Kyoto
`328de8ebf7d84b18842039f476f8af75` passed 18 tests; receipt
`validation/native-parity/n06/swift/staged-selfmodel-72/` independently verified.
This uses package-internal @testable from staged source, not a public consumer or
installed release. Swift and Go lifecycle decision laws remain internal; public
external self-model BFS/shrink/replay is not claimed. Bounded N06 Python/Go/TS
source inspection found no substantiated new gap, not complete parity audit.
Single-host provenance, installed/platform and full N06 gates remain open.
`git diff --check` passed.

Continuation: N06 same-snapshot instrumented backpressure tests passed in four
engines: Python capacities 0/1, Go eight 0/1 sink subcases, TS 0/1/2 and Swift
0/1. Actual blocked channel/transport wait was witnessed; receipt
`validation/native-parity/n06/backpressure-73/` independently checked. Single-host
source/staged, not installed qualification.

Python fresh-process control discovers/shrinks duplicate-delivery witness, writes
original and reduced traces, checks exact three-input witness/failure and healthy
controls in separate processes; unreduced trace must fail specific witness check.
Kyoto `2c060530e6ff47d5b56e0253223e33e4` passed; preserved receipt
`validation/native-parity/n06/python/fresh-replay-73/` independently verified.
Go external public consumer out-of-order job-bound callback/cancellation tests
`e3d8ca19af0f4774ac78d0909c2c97e2` passed using existing private module proxy;
not proof of all alias isolation or fresh release. Swift source-tree public
consumer strengthened cancellation accounting and host checks
`3b30557dcbb74b77b809031c55cf159f` passed. Scoped reviews found no concrete
false pass. All remain single-host and caller-attested; full installed, platform,
provenance and N06 qualification remain open. `git diff --check` passed.

Continuation: Go, TypeScript and Swift N06 self-model controls now persist real
original and shrunk duplicate-delivery traces and replay exact three-input
witnesses in fresh processes, with healthy strict/relaxed controls and specific
unreduced-trace negative assertions. Kyoto Go producer
`c22f96ae0a42466c9242e9faf0a5b9db`/replay
`129996216a8047c186f429c70b7e1e4c`, TS build
`6021b2db96584ae6a9cf0e0982425bc1`/control
`05fbb7bf59324a6b857acfb8b7243c24`, Swift producer
`bf146088835040db9f9cd3f4b0f274f7`/replay
`96b2d002df2b4c3db893948f3a3aafe8` passed. Receipts under n06/{go,swift}/
fresh-replay-74 and n06/typescript-fresh-replay-74 independently reviewed;
single-host source-tree caller-attested, not installed/cross-engine proof.

Python installed-wheel causal backpressure 0/1 was already covered by
`653e38313a404b77ba2b6c1917d434f4` (test-instrumented); TS installed tarball
0/1/2 `55560212ddc14362ac31a66f0e05732b` passed with private transport probe,
receipt `n06/typescript-installed-backpressure-74/`. Swift external SwiftPM
@testable staged-source 0/1 `493cbe8744e240578d8269e3f1b70df7` passed; no
pre-test snapshot verify, not public/installed-release, receipt `n06/swift/
external-backpressure-74/`. Go external installed-source with test-only overlay
0/1 `147047001d0a4ae89429f92d03e0b33a` passed; not unmodified production binary,
receipt `n06/go/installed-backpressure-74/`. Independent reviews checked blocked
send witness and receipt hashes. All single-host caller-attested; other platforms,
production provenance and full N06/overall parity remain open. `git diff --check`
passed.

Continuation: Python VectorOf/ArrayOf/TupleOf snapshot list slots before element
encoder callbacks; baseline Kyoto `8a52584bce1543daa9450f8c27638562` failed
all three regressions, final value-codec suite `9ed7dbd8cedb4a708c92eab8f9eb4a0c`
passed 33 tests. Snapshot is shallow and O(n) temporary references independent of
encoded-byte limit; mutable elements require cooperation, documented.

Python installed-wheel six additional N06 worker tests Kyoto
`048f8583d3dd4ad194654bb27d467e58` passed, including spawned worker, claimed
property stop, sink drain/decoder suppression, transfer errors and spawn policy;
receipt `validation/native-parity/n06/python/installed-extra-75/` independently
verified. Reused writable venv, no immediate package inventory or independently
proven source-to-wheel linkage.

Real Rust→Swift N06 core fault trace: Swift reads retained Rust binary without
metadata rewrite, strict replay incompatible/build_matches=false, explicit
cross-build replay exact 3 steps/failure reproduced and healthy model diverges.
Kyoto positive `78a2aa2d477a43049fa4f8d34d1639f0` passed, original 5-step
negative `7281e23793fb469581f0b654ac067826` failed intended witness assertion;
receipt `validation/native-parity/n06/rust-swift-replay-75/` independently
verified. One direction, single-host source tree; source-to-binary caller-
attested and three-file source list not complete. Other native self-model
fixtures have different ledger/identities and were not relabeled. Scoped reviews
found no further concrete defects. Full parity/platform qualification remains
open; `git diff --check` passed.

Continuation: Go vector/array and TypeScript vector/array/tuple value encoders
snapshot sequence slots before application element callbacks; prior-source
regressions failed and final Kyoto Go `c4f4d2a539e14eb58fc207afcd82e106`, TS
build `356651c9cd1f46a7a6debf392a4a23be` and value-codec suite
`073566f2eaf047de9696adcbea5ea593` passed. Vector length/prefix validation
precedes snapshot; copies are shallow O(n) references outside encoded-byte
budget. TS eager indexed getter errors can precede earlier encoder errors and
raw sink can retain length prefix; documented. Swift array/tuple value semantics
already protect slots; mutable reference contents still require cooperation.

Swift→Rust N06 persisted campaign fault trace replay now uses retained actual
Swift binary unchanged. Strict replay rejects distinct build; explicit cross-build
replay is exact for three steps with failure reproduced and healthy divergence.
Kyoto Rust `4f01941cb06846ccb986765678bdd948` compiled only; direct execution
passed positive and original 5-step negative failed intended assertion. Receipt
`validation/native-parity/n06/swift-rust-replay-76/` independently checked. This
is reverse of prior Rust→Swift evidence, each single-host/source-tree/caller-
attested; not a full cross-engine or installed matrix. Full parity, resource and
platform gates remain open. `git diff --check` passed.

Continuation: Python OracleStateOf captures model/history references before
application encoder callbacks; prior `cefb90502d2e4392927d6611ca613c0f` failed,
value-codec suite `6505f49f214c42c281c95458ca9141ef` passed. TS ordered/tuple
record and tagged-enum encoders capture declared fields before application
encoders; prior `147eaf89171e494faaff51c676415023` failed, build
`2e2394eab0924f5e843cb3d6a1f49a35` and derive-model suite
`81ecbcff19c2424da30dba433c992e4b` passed. Captures are shallow; TS eager
accessor errors/side effects may precede earlier encoders, O(field count)
references and partial raw tag remain documented. Go composite decoder now
classifies recoverable make allocation panics as decode allocation failure
rather than callback panic; prior `8ee24dd1fc4c400f9d240495d043a699` failed,
focused `45a991bb909c4e1fa1def653588d01d2` passed. Fatal Go OOM is not generally
recoverable. Independent scoped reviews found no concrete defects. Swift value
semantics protect slots in inspected composites; mutable references still require
cooperation. This does not qualify all N11 or other platform gates.
`git diff --check` passed.

Continuation: N11 selected-snapshot Kyoto `3e0eb30b3391466aaf0694bc9860ef35`
passed 25 ordered producer/consumer pairs each for v2 replay/rewrite, v1 upgrade,
diagnostics and passive v1, plus portable baseline. All replay receipts exact
three steps/failure reproduced; ten existing-destination controls exit 1 with
export-call marker and file-exists signature, sentinel preserved and no signal.
Prior run exposed Swift fatal and Go panic; consumers now exit gracefully. A
negative control rejects unrelated failure containing existing-files path. The
marker binds the export call, not a general structured errno/path proof. Receipt
`validation/native-parity/evidence-slice/current-frozen-78f/` independently
verified: 141 command log hashes, 102 listed artifact hashes, 274 copied output
files, snapshot verification. Single macOS arm64 selected snapshot, local/
caller-attested source-to-binary linkage, supplied unpinned TS compiler; not full
hostile-input/other-platform qualification. Prior mixed-source run retained
separately and not used as frozen evidence.

Python record/tuple-record/enum field captures prevent callback alias changes;
Kyoto declarations `b9b274c1ffde4de8aa4b06253c7ee96f` passed. Go evidence label
length-first check Kyoto `c717392ed0e84d8f96cff375286e3d7f` passed; oversized
invalid strings report limit first. Swift trace encode preflights blob/payload
before hex expansion; focused `b3f9479044514012b28344580529a30e` passed, but
metadata/final JSON can still allocate before JSON byte cap and multiple-invalid
error precedence changes. TS v1/v2 writer rejects nonvoid return, handles ordinary
cross-realm promises and avoids arbitrary thenables; build
`efd7e114783c42a380c0674838af738f`, suites
`42a60432668b4674acb9bb89b9259ba3` passed. Hostile Promise species may still
produce unhandled rejection; writer may already publish bytes. Scoped reviews
found no further concrete defect. `git diff --check` passed; broader parity and
platform qualification remain open.

Continuation: Swift encodeTrace preflights lower bound for retained UTF-8 text,
hex payload and item counts before JSON tree construction; exact renderer still
checks escaped size. Kyoto `1ac345d0a4a64bd88637d6e6af949f39` passed two
focused tests. Scoped review found no false-rejection/overflow defect; multiply
invalid error precedence can change, caller/process memory not bounded and tests
do not measure RSS. Python trace_json.dumps preflights step/item/payload before
hex expansion, uses built-in bytes-like hex to avoid hostile overrides; Kyoto
`a8a8b29a70084cab86fdf736ae45e9ce` passed native suite. Mutable blobs must stay
stable; metadata/final JSON allocation still precedes max_json_bytes and error
precedence may change. Independent review identified and corrected an initial
bytes-subclass/duck bypass, no further concrete defect.

N11 hostile suites against earlier selected frozen-78f snapshot passed: Rust
101 corpus cases including 3150 truncations/44 chunked reads, Python 36, Go
filtered evidence/causal suites, TypeScript 127, Swift 5 tests. Snapshot verified
after; receipt `validation/native-parity/evidence-slice/hostile-79/` independently
verified. Initial TS staged npm build failed missing tsc link; generator steps ran
and direct staged compiler retry succeeded. Older snapshot, source/staged macOS
arm64, unpinned TS compiler; does not qualify later changes, installed packages,
other platforms or complete hostile-input gate. `git diff --check` passed.

Continuation: Python trace_json.dumps preflights conservative JSON byte lower
bound before tree construction and projects fields explicitly, rejecting non-exact
strings before deepcopy hooks; Kyoto native suite
`d58d4998bf4b4c71a564936e57d1603c` passed. Go MarshalTrace preflights
step/item/blob/payload/JSON lower bound before hex/tree; focused
`e912294c9e9647648ba03ce306cc06b8` and broader trace
`911b3dbebfe34095bc22ac59a9ef9f1c` passed. TypeScript stringifyTrace preflights
bounds, uses indexed plain arrays/validated hook-free projection, rejects malformed
root before expansion, preserves accepted optional omission/property order;
final build `60534da8712246b48a2fbf5d7d4f11b9` and native suite
`5a3565caa7e944f2a31dfcda4cf04ad8` passed. Reviews found and fixes addressed
subclass/deepcopy, iterators/map, toJSON and malformed root bypasses. Bounds assume
stable cooperative input; caller getter/proxy effects and total process memory
are not bounded, and multiply-invalid error precedence can change. Go TraceData
remains unbounded convenience. Python Artifact.read now rejects callback returning
more than requested; Kyoto evidence `7d9d5c2f160e42a79f9cbede22f2943c` passed;
it cannot bound memory inside callback. Independent scoped reviews found no
further concrete defects. Full N11, resource measurement and platform gates remain
open. `git diff --check` passed.

Continuation: TypeScript traceFromData no longer trusts array subclass map and
captures one validated length for each steps/checks/outputs collection before
bounds/count/traversal; baseline `8517e12bfad441fab9b43d8b947f0311` failed,
final build `a9b926ce71094f3b8cf7a4144e10a072` and native suite
`7c7d1e8d688643789d74702498271f36` passed. Python strict_loads maps interpreter
oversized integer ValueError to FormatError while preserving existing FormatError;
baseline `87e05a7df65c4043a06dfe21fa6e0409` failed and native suite
`1047b06ba2cf4c4cb776538b26f4a229` passed 37. Independent scoped reviews found
no further concrete defect.

Fresh selected-source N11 snapshot Kyoto `926f8f4db50a475eb6dbd37becebc4af`,
Rust build `0f2382cd52964ee7bf0fb84c65241ed9`, matrix
`ee488625843341a089933edb15dd2823` succeeded. Four complete 25-pair matrices
(v2 replay/rewrite, v1 upgrade, diagnostic, passive v1), 10 expected destination
controls and portable baseline passed. Independent review verified 141 command
log hashes, 102 artifacts, 1742 preserved checksums and 1470 snapshot files;
receipt `validation/native-parity/evidence-slice/current-frozen-81/`. Snapshot
records HEAD 7c20b6e but includes dirty/untracked changes; do not equate to clean
commit. Scope macOS arm64 selected snapshot, TS compiler supplied live/unpinned,
source-to-binary linkage locally attested; other platforms, complete hostile-
input/resource and release qualification remain open. `git diff --check` passed.

Continuation: Go audit binary reader now bounds consecutive zero-progress reads
and rejects invalid reader counts, including the footer probe; full Go suite Kyoto
`a2040db5315c40258ebef6675d32886b` passed. Python TraceV2 validation avoids a
step-prefix copy while preserving semantic precedence; evidence suite
`32858d3fd28547d89529dd7402e1d28f` passed. TypeScript binary callback errors are
classified at their boundary, with invocation-scoped capture errors; build and
binary suite `56db74101c32423aa90d479bc49d7cc6` passed. Error detail formatting
can still invoke caller-controlled hooks. Swift evidence vectors now grow incrementally rather than reserving based on an
untrusted count, preserving shared malformed-input precedence; evidence suite
`0c7d966d598b499485829a54596de562` passed. Logical budgets do not measure RSS.
Independent scoped reviews informed these changes.

Hostile-input suites on the older selected-source `current-frozen-81` passed:
Rust 101 corpus cases, 3,150 truncations and 44 chunked reads; Python 37 tests;
Go 22 named tests; Swift 5 tests; TypeScript 127 tests. Exact jobs, logs and
checksums are in `validation/native-parity/evidence-slice/hostile-82/README.md`;
an independent receipt review confirmed consistency, not provenance or an
independent rerun. This snapshot predates the changes above, is writable and
caller-attested, and uses an unpinned TypeScript compiler on one macOS arm64
host. It does not establish full N11, installed-consumer, mutation, other-platform,
or release qualification.

Continuation: Rust binary trace reader validates callback byte counts instead of
panicking/misclassifying on an overreported count; Kyoto compile-only
`58f0546b2b3a456a91dddd18075fc202` followed by direct execution of matching
`trace_format` binary passed 14 tests. Repeated interruptions and callback time
are not bounded. Go evidence vectors grow incrementally after logical quota
admission, retaining malformed-element precedence; full Go suite Kyoto
`3fc19577c2724370910144f33ed76d7e` passed. TypeScript evidence reader uses a
bounded geometric buffer instead of retaining an object per read; build and 96
evidence tests Kyoto `6086649173054f69a1738ebff6877f58` passed. Independent
scoped static reviews found no further defect. These resource controls are not
process-RSS or callback-allocation bounds. N09 opt-in Rust-style diagnostics
implementation is in progress; existing native diagnostic behavior remains the
default and full cross-engine qualification remains open.

Continuation: added opt-in N09 indexed lifecycle Rust Debug-style diagnostics
in Python, Go, TypeScript and Swift, preserving native-code defaults. Callers
supply key and string Debug renderers and a stable formatter identity; callback
correctness and toolchain-compatible rendering remain their responsibility.
The opt-in metadata suffix identifies that policy and is not Rust metadata.
The candidate contract is in `docs/native-parity/N08-N09-CONTRACT.md`.

Rust Debug structure and real duplicate-Begin monitor references compiled via
Kyoto `a8c7156775114459830edd8cfcd3699d` and
`648bfe67512c4aa48e9c88877c5c1744` (both compile-only) and their matching
binaries executed directly, passing one test each. The same actual monitor
scenario passed in Python Kyoto `abffb3dd9b424834bf1aced62471e36b`, Go full
suite `8eaf82ddffea4ff69f8fafce3eb1d9b7`, TypeScript build/stateful suite
`f44dc686388b45a39db1d9173aae878b`, and Swift focused suite
`7c490e4c16844e29956f9de078fca2bc`. Independent scoped reviews found and fixed
TypeScript identity substitution/receiver and Go source compatibility issues;
final reviews reported no concrete issue. This is selected-scenario local test
coverage, not comprehensive cross-engine, installed-consumer, Unicode, platform,
resource or release qualification. Full N09 and native parity remain open.
`git diff --check` passed.

Continuation: Go adds opt-in `FuzzAutoWithLimits64`, sharing the fuzz run's
control with automatic domain generation and membership scanning. Cooperative
cancellation/deadline becomes a normal report and does not commit tentative RNG;
callback errors preserve precedence. Full Go suite Kyoto
`a57ae9514da74e87b87aefa4e0677559` passed, including completed-run equivalence.
Independent review found and fixed deadline start ordering. Individual callbacks
cannot be preempted; existing Auto64/Fuzz, guided and campaign integrations are
unchanged, so the broader N03 cooperative-scan gap remains open.

Python check-sink failure is now sticky before formatting hostile exceptions,
preventing swallowed invalid checks and reentrant append. Append suite Kyoto
`7d999c2567b34ebdb80885295852388e` passed; a staged copy restoring the prior
implementation failed both corrected controls (`9b1670762a224ee3a049e0cb7d9957e4`).
Go debugger frame reader now rejects invalid reader counts and bounds consecutive
zero-progress reads; debugger suite `30313122f2f14d96b69617c4cf83b6fe` passed.
TypeScript debugger protocol preflights frame/collection sizes and uses bounded
indexed snapshots; build and protocol suite `30a36f665640473688fa926fb7c301a6`
passed. Multiply malformed input may have different error precedence; callbacks,
proxy effects and total RSS are outside these bounds.

N09 formatter errors now follow the candidate contract for Swift arbitrary
thrown errors and TypeScript revoked/trapped proxy results. Focused Swift Kyoto
`b634e87b1cd148dea688180d325cc87e` and TypeScript stateful build/suite
`f13ec718947140bda65180394128eb44` passed. TypeScript native Promise rejection
suppression can still invoke a species hook. Independent scoped reviews of all
above changes found no further concrete defect; they are not full parity or
platform qualification. `git diff --check` passed.

Continuation: Go adds opt-in guided auto fuzz and ordinary/guided auto campaign
entry points, using each run's cancellation/deadline during generator and
membership scans. Guided full suite Kyoto `24dddaddcc8e4018927ee997dc57c53f`
passed; final campaign full suite `ade7a2788b6442369e0a4e7a1905f493` and
focused auto-campaign race tests `c40b9eff8fdf4dc887590e5f59fa4707` passed.
Independent reviews prompted membership and multi-worker/cleanup regressions and
found no further concrete defects. Callbacks already running cannot be preempted;
feedback factory scans are callback work, and the shared equality callback must
be concurrency-safe. Existing APIs remain unchanged. This does not close N03
across all languages or qualify other campaign platforms.

Python debugger framing rejects overlong reader chunks and invalid writer counts
while retaining subclass and partial-I/O compatibility; protocol suite Kyoto
`c5b463a695b94c6f959325eb39422c67` passed. Swift debugger protocol preflights
escaped frame and collection sizes and checks unescape bounds before copying;
ProtocolTests `00186c216b5b455ea55140813d7f59ce` passed 18 tests. Independent
reviews led to boundary fixes and found no further concrete issues. Swift
multiply-invalid error precedence can change, and caller-owned memory, callbacks
and RSS remain outside these bounds. Full N12 qualification remains open.

Continuation: added opt-in cooperative Auto scan integrations for ordinary and
guided fuzz plus ordinary/guided campaigns in Python and Swift, and TypeScript
caller-thread campaigns. Each shares run control with generation/membership
scans and avoids committing interrupted sampling RNG. Python Kyoto
`607450e156de4370b366f45067fcf7ab` passed cooperative/search/guided/campaign
suites including shared cancellation in both process-worker campaign variants.
Swift `45ba9764663042a59541bfe3dc47d38f` passed nine focused auto tests and
`84c574c4324e42d0ade04282ef6b5011` passed existing Count64 tests. TypeScript
`b2ef42a2b7f34e8eaaf029dbd0563fa6` passed build, enumerate-only consumer
typecheck and auto/search/guided/campaign suites. Independent scoped reviews
found and fixed deadline-start and TS campaign type issues, and reported no
further concrete defects. Swift and TS deterministic in-scan deadline and guided
retained-corpus membership coverage remain open; TypeScript Node worker auto
campaign support remains open. Callbacks already executing cannot be preempted;
these results do not establish full N03 or platform qualification.

Go debugger protocol now preflights escaped frame/field sizes and delimiter
counts before expansion, with overflow-safe derived limits. Kyoto debugger suite
`c0f37cb2ab0642759348450d2dfb58af` passed; independent review prompted per-field
admission and input-limit overflow fixes. Multiply-invalid error precedence may
change; caller-owned memory and total RSS remain outside the bound.

Continuation: TypeScript Node worker-host ordinary/guided auto campaigns now
share cancellation/deadline controls with worker scans. Kyoto
`660c8ab499a84543aec02bcd37b30206` passed build, enumerate-only consumer
typecheck and 70 campaign tests including actual one/two-worker cancellation,
completed/failing witness transfer and cleanup. Independent static review found
no concrete defect; broader worker deadline and external cancellation timing are
not established by that review. Callbacks already running cannot be preempted.

Previously missing deterministic in-scan deadline and guided retained-corpus
membership cancellation cases now pass in Swift (`be05a8bcde844887a91d13862304d527`,
11 FuzzAutoTests) and TypeScript (`d19702d963374315a29d12232f4d70f7`, build and
both auto suites). Swift uses an internal injected clock in the test; scoped
independent reviews found no concrete defect. Go recorder WriteTo now returns
only its prior confirmed byte prefix when a writer reports an invalid count;
full Go suite `acf3109765614bc08138310da4caf588` passed and scoped review found
no further issue. Invalid callback counts cannot establish actual published
bytes. Full native parity and platform/release qualification remain open.

Clarification of the earlier Go probe note: current `DiagnosticScope` stages
probe/marker records and stamps actual completeness on `Finish`; protocol and
host queues are independent, and Go-only lifecycle notifications remain host
visible. A direct legacy `Probe`/`Marker` call with nil `TurnComplete` can still
place an unknown-completeness record at the protocol head; polling then returns
`unsupported` without consuming it, since the Rust wire requires a boolean.
Explicit detach releases all protocol-queue records while preserving host queues;
it is a coarse discard, not per-event attestation. README documents these paths.
Focused Kyoto `2304f804644242329513760064dea0d9` passed the existing scoped,
unknown-completeness, and independent-consumer tests. This does not establish a
per-event resolution API or broader N12 qualification.

Continuation: Python ModelAdapter now detaches inputs to direct and append-style
state/transition checks, preserving append precedence and nested sink failure
semantics. Focused modeling Kyoto `48388f7150064a30a9c41bceb559f6f2` passed,
including the final engine-path regression. Full Python suite
`db7e69f140ef4879a2baf8f4d2536117` passed 937 tests, 13 skips, and started before
the final test-only addition. Python shrink now closes simplifier iterables on
early cancellation/error while preserving primary exceptions; search suite
`29903754f6a443be82b72b9f2aa6d3f0` passed. Python debugger protocol preflights
escaped frame/field sizes and caps field traversal before expansion; protocol
suite `58d092fda9574aac8977d301e467b123` passed. Independent reviews found and
helped fix nested-sink, cancellation cleanup and protocol bound bypass cases;
final scoped reviews found no further concrete defects. Protocol limits do not
bound caller iterator work or process RSS; multiply-invalid error precedence may
change. These results do not qualify the full plan. `git diff --check` passed.

## 2026-10-31 review and implementation continuation

Pre-change working tree preserved at `refs/wip/native-parity-pre-impl-20261031`
(`f529cc7`, excludes validation run output). Independent read-only reviews of the
uncommitted Go, Swift, TypeScript, Python and Rust/scripts/docs work found no
component requiring a rewrite. A code-checked gap audit found most handoff gaps
already implemented; the confirmed remaining source gaps were Go sink health
(`abandoned`, `Abandon`), Go telemetry health/dashboard shape and TypeScript
`telemetry_health` field order. All three are now implemented.

Cross-language corrections: the shrink skipped-hint bound is per simplifier
iterator and resets after each replayed hint (Rust `shrink_ops` changed; the
previous whole-run count changed finite results). All four ports follow it.
Native assurance JSON follows Rust spellings, budget keys/units and reasons;
host-only keys moved out of Rust-defined maps. `watch_status`, `telemetry_health`
and per-sink probe health use Rust field order in every binding. Go diagnostic
scopes gained an idempotent `Close` for abandoned turns, and only fully
enveloped scoped records enter the Go protocol queue (patched, not rewritten).
Other review fixes cover clone reduction, Python watch expiry, Swift strict watch
path decoding and TTL order, the TypeScript reference host, reference-suite
publication and the stateful runner's mutant timeout.

Nothing in this continuation was compiled or executed except `gofmt` and
`py_compile`; all suites, installed consumers and mutation controls must run on
the final source. Known open points: Python `_WIRE_JOB` layout 40→32 is inferred
from Rust tuple layout and needs a Rust `size_of` check; Go and TypeScript
handle byte counts are not comparable with Rust; Swift fuzz copy reduction is
partial; non-ASCII Debug escaping is approximated in Go, Python and Swift.

### 2026-10-31 local test pass (live working tree, not frozen qualification)

Kyoto jobs, all on macOS against the live tree:
- Go `go vet ./...` plus `go test ./...`: first run `4f9570ad…` failed two new
  tests; both fixed (shrink test lacked an enabled zero input; verifier rule was
  stricter than Rust, which keeps `shrink_complete` when preservation failure
  downgrades a kill to `not_evaluated`). Rerun `020f1070…` passed.
- Python unittest discovery: `c94e3917…` 957 tests, 1 failure, 1 error, both
  test bugs (nonexistent `CampaignConfig` field; omitted Rust-equivalent
  `stateless.engine.*` trace parameters). Rerun `c2753615…` passed, 13 skipped.
- TypeScript `npm test`: `21b6cd41…` 4 of 1567 failed. The lazy edge-capture
  change broke pinned alias-isolation guarantees and was reverted to the
  pre-change source with its two dependent tests; two stale tests updated.
  Rerun `ccbda1a0…` passed 1565 of 1565.
- Swift `swift test`: `85e5a14e…` 686 tests, 2 failures in one new copy-count
  test with wrong expected counts (corrected). Full rerun `64bbfc2d…` passed,
  3 skipped.
- Rust: `cargo test --workspace` is compile-only by policy; the 68 produced test
  binaries were executed directly from their package directories:
  `e8293cf0…` 675 passed, 0 failed. `cargo fmt --check` is clean after
  formatting changed files; clippy reports only three pre-existing warnings in
  unmodified `conformance/fixtures/native-campaign-public.rs`.

These are local live-tree results. They do not replace frozen-source
qualification, installed consumers, platform matrices or mutation controls.
