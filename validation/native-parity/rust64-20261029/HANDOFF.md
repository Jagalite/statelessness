# Rust64 count implementation handoff

Parent source: `1018337e01409e8d2022df364e3e883ce59d6676`.
Behavioral reference: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
This is a local implementation candidate. No merge, push, publication, release,
installed-package pass, or combined native parity completion is authorized.

## Implemented scope

Go and Swift add unsigned `64` API families; existing signed APIs become checked
adapters over the same execution kernels. TypeScript uses checked generic
number/bigint configurations and exact bigint loop/arithmetic paths, including
Node workers. Generation, ordinary/guided fuzz, shrinking, campaign execution,
report counters, acknowledgement scheduling and private lifecycle codecs are
covered. The codecs resume the real reducers without narrowing report counts.
Actual allocation growth and native indexing are checked at materialization;
requested budgets never cause eager allocation of that many elements.

API integration notes:

- [Go](../../../docs/native-parity/N03-N06-GO-U64.md)
- [Swift](../../../docs/native-parity/N03-N06-SWIFT-U64.md)
- [TypeScript](../../../docs/native-parity/N03-N06-TYPESCRIPT-U64.md)
- [Independent boundary contract](../../../docs/native-parity/N03-N06-U64-CONTRACT.md)

Python production remains unchanged as an independent comparator. Added bounded
integer witnesses in `python/tests/test_rust64_counts.py`. Two older Python
assertions and the matching TypeScript assertion now expect the pinned
`initial check_state` diagnostic. The previous failed logs remain intact.

The Swift pthread retain-transfer implementation is restructured around a narrow
integer-address shim while preserving immutable Sendable closure ownership.
This is a source workaround for the historical compiler crash; compilation is
required to determine whether that workaround succeeds with the installed compiler.

## Historical outcomes

`historical-jobs.json` preserves exact commands, states and terminal log hashes.
The r2 native run failed: Go passed its old source; Python and TypeScript had
stale diagnostic assertions, and Swift failed compilation. The independent r3
Swift run also crashed the compiler in SendNonSendable. Installed guided r4
stopped at the Swift build. Historical Rust job
`b0c8d8799b2f483abcc796713e4fccaa` succeeded with `--no-run`: compilation only.
The owner-held original snapshots and jobs were not modified or cancelled.

## Evidence and remaining gates

Independent review finished with no open source findings; see [REVIEW.md](REVIEW.md)
and `reviewed-source.json`. The parent verified that all 44 reviewed code/test
files match their final frozen language snapshots byte-for-byte.

Final queued jobs (all never started at this receipt cutoff):

- Go compilation: `5f93c5cc0c2b4c2481cf5ed2c2289329`.
- Swift compilation: `0ed248044a9442328b9d667ec2e87db6`.
- TypeScript compilation: `3a963e46c4084469b7c8174eda8354f7`.
- TypeScript API type fixture: `cc5090f32a1f49daacff577e2debdb41`.
- Independent Python suite: `59db931ca2ae47a4b3a3b44291bba3e3`.

`jobs.json` records requested/effective commands and all 14 lane attempts. The
superseded submissions were cancelled before starting; their copies remain.
The separate language API notes retain Go/Swift/TypeScript snapshot and tool
identities plus explicit post-compilation runtime commands.

Python final snapshot is `/tmp/stateless-u64-python-r2/source`; `source.json`
SHA256 is `13a07835ab612307e408fb2a2f7449b7028d420a0a1ff7b8a30144b38c6667c2`.
`tools.json` records the resolved interpreter and env launcher identities.
The final suite includes bounded generation/shrink witnesses added after r1.
The r1 source remains under `/tmp/stateless-u64-python-r1`; its source manifest
SHA256 is `c131bc23aca61777c784e480552e74cfc84688bcfa10db49f382bb502d580651`.
Its superseded job `5c1f278abd154e2b8ca2de47f962c91b` was owner-cancelled before
starting. No Python production implementation was changed.

The frozen source manifests and Kyoto receipts accompanying this handoff retain
the exact input/tool identities and every attempted job. Large copied sources,
build outputs and raw logs remain outside tracked source under `/tmp` and the
Kyoto state directory. An absent executable/package identity means it was not
built; it is not an installed or runtime result. No semantic mutant detection is
claimed by this lane.

At the recorded admission observation, Kyoto was not paused but admitted zero
jobs because of `OS memory pressure: warning`; see `admission.json`. The shared
policy was not changed. Compile and explicit runtime remain separate gates.
After successful compilation, identify/hash the emitted artifacts and submit
their explicit execution through Kyoto before promoting any source result.

The parent must integrate the exact-width API changes into the separately owned
installed consumers, execute the independent five-engine fixtures and negative
controls, and qualify the final combined source. Desktop/runtime matrices,
browser claims, resource budgets, packaging, and aggregate completion remain
parent gates. These are not exclusions from Rust64 count semantics.
