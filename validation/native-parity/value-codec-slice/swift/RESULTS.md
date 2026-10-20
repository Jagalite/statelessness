# Swift canonical-value candidate qualification

Final Kyoto `48cd9305ccfe4ea68b9a1e6b82b9a40e` succeeded, exit0.
The earlier129-pass `e32bc0c651b14f4f9c5016addf9329b8` remains intermediate evidence.
Requested and effective command: `swift test` in the native-parity worktree.
Actual XCTest execution: **134 tests, zero failures**. Kyoto execution wall24.108s;
XCTest suite2.397s. The trailing Swift Testing zero-test suite is not the test count.

All52 source inputs in source-direction-length-before/after are identical. The current compiler
identity is recorded in toolchain.json. This tests the observed Apple arm64 target;
it does not qualify all supported deployment OS/runtime combinations or Rust32.

Frozen corpus bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c:
83 value rows,30 decode-error rows,8 encode-error rows,19 reader scripts,
6 raw-encoder scripts,3 public empty-length-override errors =149 shared executable
rows. Tests also check every proper prefix, trailing bytes, ownership/subviews,
BOM, callback leases, failed-sink raw callbacks, and first structural errors.
Of31 native-admission rows,5 exercise public runtime rejection (portable128
constructors and fixed-array size);26 are excluded by typed Swift parameters.
Six prefix arithmetic-only rows remain unexecuted; no huge allocation loops.

Finite logical Rust64 layout reference: f0351a737e3f43758f30430d8a92d4c4,
Rust1.99.0/aarch64-apple-darwin/usize64. Raw layout output and its SHA are retained.
These weights are not native heap/RSS measurements or an arbitrary layout algorithm.
Unknown compound layouts require explicit caller descriptors. Independent encode/decode and length-encode/length-decode capabilities now
compose through all builtins, including None/empty/zero-sized cases. Five native
test methods cover protocol-only types, mixed-neither-trait shapes, early missing
direction admission, optional callbacks, custom length-only types and selected
width adapters without an extra raw guard. No N13 model/derive
generation, new default codec adoption, or promoted-profile expansion is claimed.

The cancelled unstarted9046 receipt and failed4afa/7a37/6cff/8b668 intermediate receipts
are retained. Failed4afa exposed BOM fixture bridging;7a37 exposed concurrently
expanded encoder fixture harness mismatches;6cff exposed an incorrect arithmetic
classification assertion;8b668 was a test argument-order compilation error.
The final source corrects those issues. Logs are lossless
gzip, with original byte sizes and SHA256 in logs.json, preserving whitespace.
Independent static review is in ../review-go-swift.md; static review is separate
from runtime evidence. No commits were made by the Swift implementation agent.
