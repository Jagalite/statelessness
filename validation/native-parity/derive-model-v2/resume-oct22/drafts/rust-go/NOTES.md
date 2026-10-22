Drafts only, 2026-10-22. No application execution, compilation, or tests were run.
Only the two copied source files and this note were written under rust-go/.
Both copied sources were formatted with rustfmt/gofmt; this is syntax/formatting
work, not type checking or runtime qualification. Parent applies after the freeze,
qualifies through Kyoto, and obtains independent review.

Complete proposed files, with original relative paths preserved:

- crates/macro-qualification/src/bin/n13-model-interchange.rs
- examples/native-model/go/main.go

The negative control creates a fresh application and two independent recorders.
One engine-checked initial state and each engine-checked turn are handed to both
recorders, with full checks. The first Add(1) produces state/output 1, one passing
state check and one passing transition check. The next Add(1) produces state/output
2 and passes both checks before failure is armed. The existing bound custom
encode-only Output writes its actual u32 payload into the candidate sink and then
returns the injected callback error. No decoder, replacement binder hook, fabricated
trace error, stop-with-error call, library change, or oracle change supplies the
failure. Each recorder must independently reach that callback and fail.

Expected exact cumulative counts (authored assertions, not executed evidence):

M = [initial_state, reducer, state_checker, transition_checker].
C = [state_encode, input_encode, output_encode, state_decode, input_decode, decode_limits].
F = actual error returns from the custom Output callback.

| Boundary | M | C | F |
| --- | --- | --- | --- |
| Checked initial | [0,0,1,0] | [0,0,0,0,0,0] | 0 |
| Recorder attached | [0,0,1,0] | [1,0,0,0,0,0] | 0 |
| EvidenceRecorder attached | [0,0,1,0] | [2,0,0,0,0,0] | 0 |
| First turn checked | [0,1,2,1] | [2,0,0,0,0,0] | 0 |
| First Recorder capture | [0,1,2,1] | [4,1,1,0,0,0] | 0 |
| First EvidenceRecorder capture | [0,1,2,1] | [6,2,2,0,0,0] | 0 |
| Second turn checked, before arming | [0,2,3,2] | [6,2,2,0,0,0] | 0 |
| Recorder Output failure | [0,2,3,2] | [7,3,3,0,0,0] | 1 |
| EvidenceRecorder Output failure | [0,2,3,2] | [8,4,4,0,0,0] | 2 |
| Each frozen retry and final negative round trips | [0,2,3,2] | [8,4,4,0,0,0] | 2 |

Each attachment encodes its checkpoint once. Successful capture encodes before
state, input, output, then post state. Failure occurs before post-state encoding.
Thus each recorder independently contributes C=[4,2,2,0,0,0] over this control.
Rust counts actual value adapters/Output; Go retains its existing binder-entry
counters and additionally checks actual Output callback entries equal output_encode.
There are four actual Output entries and two intentional callback errors in total.
The model count assertions immediately before/after each handoff and retry prove
that neither recorder repeats the reducer or either checker.

Exact first capture errors and retained model_error footer text:

- Rust ModelError: `runtime encode output: n13 output encode failure`
- Go *ModelError: `runtime encode output: encode callback: n13 output encode failure`

Go wraps the callback into a staged ModelError. Its sticky encode batch preserves
the first `encode callback:` error across the outer `encode buffer callback`
boundary; Recorder adds `runtime encode output:`. The assertion checks the actual
public type/message, not pointer identity or errors.Is propagation. Rust compares
the complete ModelError value. Both messages fit the chosen 256-byte reason limit.
Source basis: src/monitor.rs prepare/freeze/stage; src/evidence.rs observe;
go/recorder.go recorderPrepare/freeze; go/evidence_recorder.go ObserveEvidenceRecorder;
go/encode_buffer.go appendEncoded/invokeEncodeBatch/encodeFailure; go/model.go call.

Both failure snapshots retain exactly the first successful row, unchanged producer
metadata/config, checkpoint bytes/checks, and zero evictions. Evidence retains the
original provenance, checkpoint_after=0, sequence=1, state_check_count=1, and full
worker/generation/correlation/logical-time context. The second causal context has a
valid reference to retained output 1:0, so metadata validation cannot substitute for
the intended Output failure. No second phase row or causal link is committed.
Public phase queries must still report initial_state, state, transition for the
retained checks and no phase for row 2.

After disarming the encoder, retries must return exactly the recorder-specific
frozen diagnostic, invoke no callbacks, and leave the original stored error and
complete snapshots unchanged. Negative v1/v2 snapshots are serialized/read only in
memory and compared exactly. No replay claims reproduction of the terminal error:
only the coherent prefix is replayable. These local negative artifacts never enter
the success envelope or its independent golden-byte comparison.

Controls execute separately before produce/transcode. Rust resets its globals on
entry/exit; Go uses a separate fresh model/counter closure. Existing success
producer/replay counts and wire-generation code remain intact. Each receipt gains
capture_failure_controls=true only after the assertion-bearing control returns.

Unresolved integration/qualification concerns:

- All expected counts and error strings need Kyoto execution on the applied exact
  source. No compile/runtime pass is claimed by these drafts.
- The currently edited driver requires capture_counts and replay_counts with its
  normalized schema. Existing Rust/Go receipts do not yet expose those fields.
  This gate-5 draft deliberately preserves their existing count reporting except
  for capture_failure_controls. Merge the parent's receipt changes before running
  the complete installed driver; do not weaken the driver or independent oracle.
- Rust failure instrumentation is process-global like the existing codec counters;
  the CLI is sequential. Concurrent in-process invocation would require isolation.
- No eviction-pressure or long-error truncation claim is made by this minimal
  one-row retained-prefix scenario.

Source identities (rechecked unchanged after drafting):

- crates/macro-qualification/src/bin/n13-model-interchange.rs
  base SHA-256: 6953eb5374adce398ff92d8c93118ca67289d1527ca10abdb5fb380fe02c2e23
  draft SHA-256: 98b9bb18e35952519644eec0467f082a8c8238f790e6d3c373dbc62bb943ed9a

- examples/native-model/go/main.go
  base SHA-256: 54ed31adb13e3c441d0a08cb2bfbba69cdb5649d608ec6dece109dfe5c6dfb81
  draft SHA-256: 1af18542e438629a31182153fea390cc0de91de8e802ad970faad4523b8beb77
