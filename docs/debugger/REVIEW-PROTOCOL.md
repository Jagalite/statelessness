# Independent pre-PR protocol review

Date: 2026-10-10 UTC  
Reviewed implementation: `461a369eb161d3924e9e9dd1e72633e122f144f7` on `debugger/v1.1`  
Full implementation baseline: `f3e00e79471b928d67ddb530c113d6d51af6f277`  
Environment: Linux x86_64; Rust `1.95.0 (59807616e 2026-04-14)`; offline development/test profiles

## Scope and method

Independently read the current authoritative
[`Statelessness-Rust-Debugger-Design-v1.1.md`](../Statelessness-Rust-Debugger-Design-v1.1.md),
particularly §§4, 6, 11–14, 16–18 and the M4 gate. Reviewed `protocol.rs`, the
`debug_stdio` executable, the OS-pipe qualification script, and the inspection,
watch, probe, metric, effect and recorder interfaces used by the protocol. Earlier
PASS reports were not used as evidence of correctness.

The review exercised hostile complete and incomplete frames, response preflight,
cache eviction, retries, revision/epoch/reconnect identity, candidate and snapshot
eviction, independent authority, sink privacy, failed capture admission, and
structured first-failure evidence. Fixes remain local; no commit, push or PR was
made by this review.

## Confirmed and resolved findings

### P2 — Rejected metric capture retired an acknowledged snapshot handle

**Before:** `MetricSnapshot { handle: None, ... }` and `MetricWindow` replaced the
single retained metric capture before attempting to format the requested bounded
wire response. With a 32-field response budget, an empty snapshot succeeded, a
host request introduced metric series, and a new oversized query returned
`limit`. Paging the original acknowledged handle then returned `stale_handle`.
The failed query had silently invalidated the only usable capture.

**Reproduction:**

```sh
cargo test -p statelessness-debug --offline --test pr_review_protocol \
  rejected_new_metric_capture_preserves_last_acknowledged_handle -- --nocapture
```

The pre-fix assertion observed `Err(StaleHandle)` instead of `Ok(())`. The same
order-of-operations flaw existed in the window branch.

**Resolution:** validate the capture budget and calculate the prospective handle,
format the whole bounded projection, then commit the retained capture and handle.
A rejected new snapshot or window leaves the previous acknowledged capture
pageable. A follow-up reproduction also found that `EffectObserver::metric_snapshot`
advanced/evicted host history before wire rejection: with two retained snapshots,
four rejected captures replaced acknowledged endpoints `[1, 2]` with `[6, 7]`.
The live/effects owner added a crate-private staged validation/commit API, and the
protocol now validates its entire wire response through that coherent boundary.
Rejected captures preserve both the protocol handle and host snapshot
revision/history/watermark. Only a prospective snapshot and eviction plan are
staged, without cloning retained history. Clock reads and actual clock-fault
health reporting remain real observations; they are not rolled back. Three
independent regressions now pass. The follow-up red run is preserved in
[`pr-review-protocol-history-before.log`](../../validation/debugger/pr-review-protocol-history-before.log).

### P2 — Unknown startup options silently enabled mutation authority

**Before:** the executable repeatedly searched arguments for known flags and
ignored every other argument. Starting it with the plausible typo
`--read-onyl` accepted a `step` and returned `delivered=bool:true`, sequence 1,
with exit code 0.

**Reproduction:**

```python
import subprocess, struct
payload = b'DDBG\tu32:1\trequest\tstring:request-lifecycle\tu64:1\tu64:1\tu64:0\tu64:0\tstep\tbytes:00'
r = subprocess.run(['target/debug/examples/debug_stdio', '--read-onyl'],
                   input=struct.pack('>I', len(payload)) + payload,
                   capture_output=True)
print(r.returncode, r.stdout[4:])
```

**Resolution:** parse all startup options once before constructing the session.
Unknown, malformed and non-UTF-8 options fail closed, without echoing raw argument
text into stderr. The OS-pipe script checks four rejected options, including
`--read-onyl`, malformed boolean spellings and a terminal-control-bearing
argument. Each exits unsuccessfully with no protocol response or delivery.
Known `--read-only` still permits separately authorized watch/probe configuration.
This is a simulated-model authority issue; the executable never dispatches real
effects.

### P2 — Check-list truncation hid a later first failing property

**Before:** `checks` returned only the first `max_page_size` checks, with no way to
page the remainder. A failing observation with 128 passing checks followed by
`late-first-failure`, under a one-check display page, returned only `pass-0` plus
`checks.truncated=true`. The client could see a property-failure stop but could
not retrieve the exact failing property identity required by design §11.4.

**Reproduction:**

```sh
cargo test -p statelessness-debug --offline --test pr_review_protocol \
  first_failure_identity -- --nocapture
```

The pre-fix response had `checks.total=u64:129`, `checks.returned=u64:1`, and no
`checks.first_failure.id`.

**Resolution:** return bounded first-failure ID, zero-based check index,
transition sequence, and actual check phase independently of the display prefix.
The phase is obtained from `DebugObservation` and `state_check_count`, not inferred
from property IDs: `initial_state`, `post_state`, or `transition`. The regression
uses the same failure ID at all three boundaries and verifies the first failure
at index 128 remains available. Failure-message text stays omitted, and a canary
message is absent from the wire. With no failed check, `checks.first_failure` is
`none`; clients must still honor `checks.complete`.

Pre-fix command/output excerpts are preserved in
[`pr-review-protocol-before.log`](../../validation/debugger/pr-review-protocol-before.log).

## Verification

Run from the repository root:

```sh
source /workspace/shared/render-rust-1.95.0/activate.sh
cargo test -p statelessness-debug --offline --test pr_review_protocol --test protocol
cargo test -p statelessness-debug --offline --all-targets
cargo build -p statelessness-debug --example debug_stdio --offline
python3 scripts/check-debugger-stdio.py
cargo clippy -p statelessness-debug --offline --all-targets -- -D warnings
rustfmt --check --edition 2024 \
  crates/statelessness-debug/src/protocol.rs \
  crates/statelessness-debug/examples/debug_stdio.rs \
  crates/statelessness-debug/tests/pr_review_protocol.rs
git diff --check
```

Results against the final protocol edits:

- **PASS:** 11 independent `pr_review_protocol` tests and 30 existing protocol
  tests (41 total)
- **PASS:** the full debugger crate with `--all-targets` (246 tests at this
  final review snapshot, including all concurrent review regressions and examples)
- **PASS:** the real subprocess/OS-pipe client, including original bug/fixed-model,
  exact-export, read-only/watch and probe-profile workflows; 14 malformed complete
  frame cases; 87 fatal zero/oversized/truncated pipe cases; byte-at-a-time writes;
  cache conflict and eviction; epoch-zero status discovery; stale revisions and handles; denied
  profile/sink/export access; interrupted transport after a committed turn;
  process-restart reconciliation; and four fail-closed startup options
- **PASS:** strict all-targets Clippy, owned-file rustfmt checks and diff whitespace
  checks

Evidence:

- [`pr-review-protocol-after.log`](../../validation/debugger/pr-review-protocol-after.log)
- [`pr-review-protocol-stdio.log`](../../validation/debugger/pr-review-protocol-stdio.log)
- [`pr-review-protocol-clippy.log`](../../validation/debugger/pr-review-protocol-clippy.log)
- [`pr-review-protocol-full-test-after.log`](../../validation/debugger/pr-review-protocol-full-test-after.log)
- [`pr-review-protocol-source.sha256`](../../validation/debugger/pr-review-protocol-source.sha256)

The independent Rust tests additionally qualify:

- Snapshot/window response rejection leaves an acknowledged metric capture intact
  and does not evict previously acknowledged metric-window endpoints
- The smallest accepted field/frame/count limits still produce valid mutation
  acknowledgements, including large request IDs and deduplicated delivery
- Candidate and view eviction never aliases a replacement handle
- Epoch-zero discovery cannot deliver inputs; reconnect does not resurrect a
  cached old mutation or bypass execution-revision checking
- A fatal frame after delivery disconnects without rolling back the completed
  turn, sending an extra response, or executing another turn
- Every tested truncated request prefix is rejected before delivery
- One exposed probe sink preserves redaction and full `u128` precision while an
  unexposed sink remains queued and inaccessible before and after reconnect
- Exact-value permission alone cannot export the raw trace
- Truncated first-failure evidence keeps its actual check phase and omits private
  message text

An additional full-crate `--all-targets` run initially encountered the concurrently
added sessions-review regression `initial_failure_minimization_needs_no_transition_budget`
(`ModelError("minimization budget cannot cover verification and original validation")`).
It was reported to the sessions owner, rather than changing another reviewer's
files. The owner subsequently fixed it, and an independent focused rerun of that
regression passed. The initial aggregate run is preserved in
[`pr-review-protocol-full-test-initial.log`](../../validation/debugger/pr-review-protocol-full-test-initial.log).
After the owner fixed it, the independent full-crate rerun passed all 246 tests,
as recorded in `pr-review-protocol-full-test-after.log`. The parent review's final
workspace/archive qualification remains the delivery gate; these crate-local
results are not a substitute for it.

## Remaining boundaries and qualification gaps

No unresolved correctness finding remains in this review's owned protocol changes.
The following are explicit scope limits, not newly established guarantees:

- Epochs and deduplication are unique within a process, not durable across process
  restart. A restarted client must discard outstanding mutations and reconcile a
  new run. The pipe test checks fresh status after restart and deliberately does
  not claim durable cross-process exactly-once delivery
- Same-process reconnect and failed-write state preservation are exercised through
  the Rust `serve`/session APIs. The executable owns one stdio connection and exits
  on EOF; its OS-pipe test cannot reattach to that exited process
- Host metric/effect wire adapters are covered by the Rust protocol suite. The
  stdio fixture does not manufacture real lifecycle telemetry for its simulated
  outputs. Production adapters, network transport and authentication are outside
  this review's OS-pipe qualification
- Byte and count budgets cover debugger-owned data accounting, not allocator
  capacity, arbitrary model/codec/inspector callback memory, callback blocking,
  or wall-clock deadlines. No hard process-memory or universal overhead guarantee
  was measured
- Check lists, output lists and effect timelines can remain explicitly truncated.
  First-failure identity now survives that check-list truncation; this is not a
  claim that arbitrary histories or every omitted item can be retrieved
- Generic framing, probe redaction, exact-value and raw-export authority were
  tested. Real application-specific schemas, labels and host-approved profile
  definitions still require their own privacy review

## Additional verifier and CI cross-review

At the parent review's request, independently reviewed the additive optional-Git
provenance helper, qualifier changes, compile-only target checker, and manual CI
integration. The review found and the parent resolved these verifier issues:

- The standalone macro qualifier initially omitted its new provenance helper from
  source hashes. It now includes that dependency and its regression-test file
- The provenance regression gate initially required an installed `git` binary even
  though provenance was optional. A real empty-`PATH` run reproduced two fixture
  errors. Git-dependent fixture tests now skip explicitly, while metadata-free,
  mocked-missing-Git, and manifest tests still execute
- Integrating the Inspect qualifier into Windows CI exposed raw Windows paths in
  TOML strings. A `PureWindowsPath` fixture reproduced an invalid backslash escape
- The first JSON-quoting fix still emitted invalid TOML surrogate escapes for an
  astral-Unicode checkout name. An actual generated `/tmp/test-🐈` manifest
  reproduced that error. Both qualifiers now use non-ASCII-preserving JSON string
  quoting and explicit UTF-8 manifest writes

Final checks passed: five `test_qualification_context.py` tests, an actual
missing-Git run with two explicit skips, and independent generated-manifest TOML
round trips for POSIX quotes, Windows drive/spaces, UNC and astral-Unicode paths.
The exact-root provenance check does not attribute a nested archive to its parent
repository. The target checker validates compiler-declared triples and clearly
labels results compile-only, without linked/native-runtime claims. The CI change
retains `workflow_dispatch` and `contents: read`; no trigger or permission expansion
was introduced. The final native Inspect-consumer execution and all 32 invalid
attribute fixtures are recorded in
[`pr-review-verifier-cross-review.log`](../../validation/debugger/pr-review-verifier-cross-review.log).

### Package payload follow-up

The additional packaged-consumer gate independently reproduced fourteen generated
`scripts/__pycache__/*.pyc` entries in Cargo's explicit include list. Source-only
script globs retain all seventeen tracked scripts and remove exactly those
fourteen entries. The actual resulting crate has 139 entries and no Python
bytecode/cache paths. The packaged consumer passes three tests and fresh-process
check/find, original/minimized replay, corrected-build divergence, no-clobber,
and truncated-input cases. Both the new packaged-consumer qualification stage
and source bindings for root examples/benches were reviewed without further
findings. The before/after logs are `pr-review-packaging-{before,after}.log`.
