# N05/N06 continuation on PR12

This lane recovers `028167236bcc783cc497b7ce65400c33b1294d87` onto
`4e84cf1a8bf15b2e6b0698d6801a92dc94a45d00`. It does **not** complete N05/N06
qualification or authorize merging PR12. The historical contracts and review
reports retain their original source cutoffs. See the continuation receipt under
`validation/native-parity/n06/continuation-20261029/` for actual run outcomes.

## Source decisions

- Preserve PR12 exports, optional debugger/modeling entrypoints and package
  boundaries when combining the four text conflicts. No Rust ABI calls added.
- Python FuzzConfig accepts the Rust u8 mutation operand through 255; semantic
  validation of mutation >100 occurs at execution admission. Campaign jobs,
  workers and buffer validation therefore precedes mutation validation. Standalone
  fuzz and guided calls validate before invoking callbacks. Other count and seed
  construction guards remain unsigned-u64 guards.
- TypeScript guided feedback metadata retains a private typed panic cause while
  standalone callers still receive ModelError. Campaigns recover that cause
  without parsing diagnostic text. Metadata callbacks remain synchronous.
- Swift preserves the explicit recoverable CampaignPanic marker at feedback
  metadata; ordinary thrown errors retain the previous ModelError wrapping.
  This does not catch Swift fatal traps or aborts.
- Go retains an internal callback panic error through composed Auto calls. Its
  ModelError view remains reachable through Unwrap; campaigns use only direct
  internal type assertions, never application As/Unwrap methods. Error text is
  not used to manufacture panic provenance. Existing standalone panic diagnostic
  formatting and revoked feature-buffer leases remain intact.
- Fuzz/search raw callback stages use `initial_state`, `step`, `initial
  check_state`, and `check_state`. Core recording/checked-token diagnostic APIs
  retain their prior stage names. Consumers do not repair N06 result diagnostics.
- Swift Oracle.swift overlap is exactly four additive lines: conditional
  Equatable and Hashable conformances on OracleState, including both state and
  oracle history. Parent must retain N07's separate semantic edits.
- Rust private reference fixture generation uses `campaign/mod.rs` so ordinary
  child module lookup resolves lifecycle/codec/tests. Generated files are written
  as bytes, avoiding inherited read-only source modes. Production Rust is unchanged.
- Swift's pthread opaque pointer is locally `nonisolated(unsafe)`: the context
  remains checked Sendable with an immutable Sendable closure. Successful
  pthread_create transfers its one retain to the entrypoint; failure releases it.
  This addresses the Swift 6.3.3 SendNonSendable compiler crash observed in r1;
  it is not a relaxation of model/report worker-transfer constraints.

## Corpus and harness recovery

N05 re-authoring produced 90 cases with SHA256
`12c762786c729e6e2030a2cb279184f23767695e15bc85afdb0ea74fecc21b35`
and the exact historical seal
`8a7f6bdc8e8ac335ec71ed6f6483c9ad470a1e72689d85f56961f757180e8547`.
N06 re-authoring produced 49 rows with SHA256
`8979c16366f91fc1e7eae4326bf17bb67278083271cca9486cc0809886881cc2`.
Its new seal is `60c5f86a7a6abaa4ce63380490086a7c9309f13eefd3e150c2ded5393e9b6516`:
only the author workspace guard and output location changed, so this is a new
provenance seal, not the unavailable historical FREEZE file. The rejected first
attempt remains in Kyoto job `0a56270d66844dcbac2aa269fc1d56f1`.

The historical R3 N06 harness manifest references uncommitted files absent from
0281672 and from the old checkout. Its 61-file implementation has **not** been
recovered. The bounded lane runners added here are explicitly narrower; they do
not implement R3's hostile-filesystem receipt recovery, supported-platform
matrix, installed N06 49-row five-engine contract, or full qualification.
The existing qualify-native-integration.py identity/output guard is untouched.

## N10 integration hooks

N10 should consume owned public FuzzReport/GuidanceReport values and identity-bearing
JobReports. Retain metadata absence, incomplete accounting, failures and raw errors.
CampaignReport's delivered count is acknowledgement after a successful sink;
worker errors and sink errors are not property failures. There is no Rust generic
CampaignReport merge API: sort retained job identities only in deterministic
comparison/assessment, retaining actual callback receipt order separately.
No assurance module is implemented by this lane.

## Remaining gates

Full Rust64 fuzz/guided/campaign count configurations, counters and lifecycle
codecs remain unsupported beyond signed Int in Go/Swift and safe integers in
TypeScript. This is unfinished implementation, not a platform exception or a pass.
An API-preserving resolution must share exact-width kernels and avoid narrowing
before cooperative cancellation/zero-work admission; wire DTO acceptance alone
is insufficient. Python already represents the unsigned-u64 count domain.

All current-source failures must be resolved and fresh snapshots executed before
acceptance. Required installed N06 five-engine tests, semantic negative controls,
resource budgets and desktop platform/runtime matrices remain separate gates.
Source snapshots and raw builds/logs live under `/tmp/stateless-n0506-r{1,2,3,4}`;
these are copied inputs, not additional Git worktrees. Every queued invocation
must retain its source manifest and effective Kyoto command. Peak child RSS from
the native suite is only an aggregate smoke observation, not an approved budget.
