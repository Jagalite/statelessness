# Review resolution before executable qualification

First candidate source commit: `06f3d3962756a687224ca682609fa7ed75a40bf3`.
These are static dispositions. Runtime results belong to the attempt receipts
and final handoff; the existence of a test does not establish a pass.

The initial acceptance audit's concrete native findings were addressed in the
native protocol sources: complete Rust response schemas; command-specific
revision/authority ordering; typed decoding before request admission; discovery
epoch canonicalization; Go oldest-handle eviction; strict UTF-8/BOM handling;
Swift session ownership while decoding; and transport teardown. Additional
review aligned connected-reconnect rejection in Go, Python and TypeScript and
preserved Python handle/candidate counters across reconnect. Native regression
sources cover these changes. The direct fixture covers the finite schemas,
callback ordering, authority/admission, retention, identities and process-pipe
cases specified in the comparator, not every optional attachment.

Independent comparator/preparation rounds found the following harness issues.
The retained JSON transcripts include the original findings and observed hashes.

| Finding | Candidate correction |
| --- | --- |
| Response decoding hid alternative escaping and count spellings | Enforce canonical percent encoding and decimal count before field-order/epoch normalization. |
| Launch and installed execution inputs were unbound | Compare exact prepared argv/cwd/environment; use a minimal runtime environment; manifest complete Python and TypeScript consumers and executable link chains/resolved bytes; reject directory symlinks in package trees. |
| Frozen and copied build inputs omitted additions | Re-enumerate the frozen tree; verify every copied input against it and compare complete membership/types/hashes after preparation. Explicit generated directories are recorded separately. |
| Mutable manifests allowed arbitrary redirection | Authored Go/Rust path rewrites and the TypeScript tarball dependency have exact expected post-rewrite hashes; arbitrary changes or reversion fail the copy guard. |
| Inherited environment could substitute corpus/compiler inputs | Bind corpus variables to the frozen corpus, disable Go environment/overlay configuration, clear Rust wrapper/flag overrides, and use private Cargo configuration with only the existing registry cache shared. Shared Kyoto policy is unchanged. |
| Preparation success depended on implicit Node reporter | Explicit TAP reporter for the full TypeScript suite. |
| Failed/interrupted attempts could lose receipts or retain active children | Controlled termination handlers, owned-process cleanup/reaping, unconditional failed case/aggregate receipts, best-effort abort snapshots and retained command termination details. |
| Input receipt described intended bytes, with unbounded writes | Separate planned and successfully transmitted input streams; nonblocking writes with a finite deadline. |
| Fatal framing could pass on signal death | Reject signal termination and require the actual public session's final state/revision/sequence. |
| Finite pipe/retention corpus was incomplete | Add coalesced valid+fatal-tail cases, two queued steps after closing the real output pipe (only the first commits), cached replay after a later turn, and retained checks/outputs after reducer error/cancel. |
| Wire success could obscure failed baseline regressions | Record finite `wire_passed` separately; aggregate `passed` additionally requires preparation/regression success and all identity guards. |

The final static review is a separate round. Full optional N12 acceptance remains
open as enumerated in [ACCEPTANCE.md](../ACCEPTANCE.md).
