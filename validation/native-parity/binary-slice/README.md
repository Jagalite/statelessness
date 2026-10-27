# Native binary-v1 candidate receipts

This finite macOS/arm64 slice passed through Kyoto. It does not close N11, promote
an experimental profile, publish a package, or qualify other platforms/runtime
floors, browser TypeScript, performance, binary v2, or recorder capture budgets.

`summary.json` records requested/effective commands, terminal states and toolchain
identity. `source-inputs-final.json` identifies the source bytes; the installed
report independently records package, executable, staged-source and artifact
hashes. All native/reference package inputs were compared again after the matrix
and remained unchanged. Parent HEAD is the previous implementation commit; the
uncommitted source manifest is the precise identity of this slice.

Final native suites: Python 85 tests with 12 ABI opt-in skips; Go all packages with
cgo disabled; Swift 59 tests; TypeScript 154 tests; independent fixture builder
5 tests. The actual Rust tool was built with `cargo build` and then executed as a
separate Kyoto job: 20 valid cases, 34 malformed/limit cases, 2,565 proper-prefix
truncations, 120 chunked reads, 280 I/O checks and 18 writer preflight checks.
Native suites additionally exercise their public streaming/byte APIs and complete
writer validation before first publication.

`installed-r2/qualification.json` records 560 successful fresh-process checks:
500 exact fixture roundtrips (20 cases across 25 ordered engine pairs), 25 exact
roundtrips of fresh public recordings, 25 semantic replays, and 10 negative replay
controls. Native packages were installed/staged outside the checkout. Every
semantic replay returns the full retained configuration, independently compared
to Rust's decoding of that producer's artifact. Empty model-error strings and
other structurally unusual traces are not automatically semantic replay claims.

The first reference/matrix attempts failed because the application compared a
fresh Rust recording, including automatic engine provenance stamps, to a static
unstamped format golden. `installed-r1` and initial logs retain that failure and
45 successful preliminary roundtrips. The corrected application separates static
format goldens from actual recordings and retains every producer's configuration;
no fixture bytes or engine configuration were normalized to hide the difference.

Individual logs/artifacts and Python wheel/npm tarball bytes are retained in the
installed receipt directories. Temporary external workspaces are recorded for
provenance, but are not required to inspect the saved evidence. These finite
checks bound the claims above; callback memory, host allocation failure behavior,
durable/private publication, direct binary recorder parity and remaining
N01–N14 gates stay open.
