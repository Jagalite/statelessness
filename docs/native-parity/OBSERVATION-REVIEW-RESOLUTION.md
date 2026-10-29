# N07 review disposition

This ledger connects the independently reviewed source findings to corrections.
The original reports retain their inspected hashes and findings. Later source
changes do not turn those snapshots into executable evidence. All compilation,
tests, installed consumer execution and platform/resource qualification belong to
the frozen-source Kyoto receipts, separately from these source reviews.

| Finding | Source correction / scope |
| --- | --- |
| New diagnostic errors collided with existing Go and TypeScript oracle errors | Diagnostics use `TextObserverError` in Go and `TextObservationError` in TypeScript. Existing generic/oracle `ObservationError` exports and handlers remain. |
| TypeScript method variance erased encoded-model requirements | Function-valued observer and formatter properties retain codec requirements through constructor defaults, interface widening and concrete NoPayloads assignment; negative declarations cover each route. |
| Public, nested or stale callback errors could spoof an outer codec/sink stage | All four boundaries attribute errors to the current operation. Internal codec attribution is tied to a private active lease; public stage fields confer no authority. Sticky record failure takes precedence and formatting remains recoverable. |
| Go typed-nil callback errors could panic or hide bounds | Nil-safe diagnostic formatting/unwrapping and opaque callback retention preserve the active stage and sticky failure. |
| Swift Every(1) identity conflated with EveryTransition | Policy identity preserves the variant even though both select every transition. |
| Shared declaration fixtures could bypass binders | Declaration consumer encoders trap when called without the public binding; adapters supply the fixture bytes. |
| Declaration schemas replaced forbidden decoder traps | Bound decoder callbacks also trap and count forbidden work in the installed consumers; error fixtures cannot silently hide decoder activity. |
| Corpus omitted early Trace callback cutoffs | Two independently authored overflow/recovery cases bring the corpus to 36; output remains literal source-derived data. |
| Swift Recorder footer/output limit ordering differed | Reserved-footer validation precedes checkpoint encoding/checking; output-count and aggregate-item limits are separate. Authentic checked-token admission remains intact. |
| Swift STEP item correction exposed frame/item precedence | STEP admission is early; each field's frame bound is checked in wire order before later collection/string admission. Public regressions preserve both competing-limit scenarios. |
| Shared Swift helper callers have different admission orders | Replay now uses an isolated check-batch preflight before combined items; recording admits cumulative items before check text. Independent review confirmed the caller-specific ordering and thirteen replay/four recording regression scenarios without executing them. |
| Installed consumers could miss Off/extraction I/O | Destination attempt counters and independent invariants supplement fixture bytes and first-error receipts. |
| Compile receipts admitted incomplete provenance | Admission validates the original attempt, command records, raw logs, content-addressed manifests, Cargo artifact/package/target records, executable bytes and resolved toolchain identities. |
| Staged source membership and generated inputs were incomplete | Explicit generated-file registration prevents rebasing identities; directory membership and copied source identity are checked around commands. Built package artifacts and builder dependencies are watched before consumption. |
| Python runner imports or ignored bytecode could replace sealed code | Entry points and generated runners require `-I -B`; runner directory membership and explicit import roots are watched. Dynamic qualification helpers execute source bytes rather than cache-aware loaders. |
| Installed package and consumer outputs were watched too late | Python/TypeScript packages and built consumers receive immediate watches before their first consumer. Suffix Go/TypeScript/Swift outputs receive the same early protection; independent review found no actionable issue in the three watches or six positive/mutation fixture scenarios. |
| Exact HEAD checks rejected receipt-only follow-ups | A later HEAD is accepted only when its entire committed nonreceipt source matches the original pin and working sources also match. |
| Timeout handling could leave writers alive outside Kyoto tracking | Commands retain Kyoto's process group. The helper fingerprints observed descendants and confirms cleanup before claiming stable raw-log hashes; uncertainty is retained as a failed attempt. |
| Darwin ownership lookup rejected already-exited, unreaped children | The libproc query now requests zombie lookup with argument 1. Independent review confirmed the kernel contract and authored real Darwin regression/control preconditions; actual Darwin execution remains required. |
| Stable outer commands could hide uncertain nested Go log writers | Aggregate sealing reconciles every nested start/finish/receipt record and stability flag before hashing logs. Incomplete or uncertain writers retain paths and fail qualification without a replacement hash baseline. |
| Inherited Go compatibility settings could alter the declared profile | The driver sets and records `GODEBUG=panicnil=0`; the legacy panicnil profile remains outside this candidate. |
| Validator discovery could silently omit tests | The native driver requires every `test_observation_*.py` module, a nonzero test count, and no validator skips or expected failures. |
| Retained suffix proof lacked full JSON observations/counts/failure freeze | Two independent contracts require complete rich/bounded sessions, separate replay callback counts, first-failure retention and frozen-artifact assertions. Planned counts are 10 producers, 50 binary pairs and 50 JSON pairs. |

Read [Python/Go original review](OBSERVATION-REVIEW-PYGO.md),
[TypeScript/Swift original review](OBSERVATION-REVIEW-TSSWIFT.md),
[proof review](OBSERVATION-REVIEW-PROOF.md), and
[suffix review](OBSERVATION-REVIEW-SUFFIX.md) with their `OBSERVATION-FIXES-*`
responses. Final review rounds identify the exact later source they inspected;
their source-only conclusions must not be presented as passing compiler/runtime
checks. The final [TypeScript correction](OBSERVATION-FIXES-TS-FINAL.md),
[Go consumer review](OBSERVATION-REVIEW-GO-CONSUMER-FINAL.md),
[Swift caller correction](OBSERVATION-FIXES-SWIFT-CALLERS.md), and
[Darwin ownership correction](OBSERVATION-FIXES-PROCESS-FINAL.md) each received
independent source review. The [assembled driver correction](OBSERVATION-FIXES-DRIVER-FINAL.md)
has no actionable finding in its four reviewed files. Its final suffix-output
watch finding is corrected and received a focused independent
[source review](OBSERVATION-REVIEW-SUFFIX-WATCH-FINAL.md) with no actionable
findings. All listed corrections remain subject to executable qualification.

Six dedicated `DebugObservation` sum/member mappings remain explicitly open in
the [source audit](OBSERVATION-SOURCE-AUDIT.md). Universal Rust Debug text
equivalence, browser-host execution, other native platforms, race campaigns,
allocator/RSS bounds, arbitrary callback memory and replay of evicted history
are not established by this slice. Process ancestry sampling is not hostile
process containment. Default-action SIGTERM, SIGKILL or supervisor death may
interrupt evidence publication and leave an incomplete failed attempt; no graceful
cancellation receipt is claimed. These limits remain visible in the full parity plan.
