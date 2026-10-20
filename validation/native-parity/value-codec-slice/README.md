# Native canonical value codec candidate receipts

The finite Rust reference and four native suites passed on this macOS/arm64
candidate. Installed-package interoperability is **pending**; its owner is
running the separate consumer qualification and root will finalize that result.
This is not complete language parity or profile promotion. The broad matrix's
144 qualification cells remain pending.

The candidate contract is [canonical-value-codec-v1](../../../spec/canonical-value-codec-v1.md).
The independently authored [corpus](../../../conformance/canonical-value-codec-v1.json)
is frozen at SHA-256 `bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c`.
Its design-only status, empty qualified-target list and source-derived layout
metadata describe its historical authoring state. They were deliberately not
rewritten using engine output. Actual execution, target/compiler measurements
and qualification scope live in the external receipts here. The tested normative
specification SHA-256 is `c94ea1d77a51d66c1da3e7ff73d146fd35011d2c5519ef8060bd9f2d89393867`;
the current specification SHA-256 is `2e98d48a7893d33ac42cf644a18ce3cf4566bbe88d14a57419f79dbb739ed80c`. Only its introductory
status/link paragraph changed afterward; the normative body is unchanged. Those
new prose bytes are not claimed as the tested input.

## Frozen corpus accounting

The corpus contains 186 rows: 83 values, 30 decode errors, 8 encode-cap errors,
19 public reader scripts, 6 raw encoder scripts, 9 length cases and 31 native
admission cases. Of these, **149 execute real public Rust APIs**: the first five
groups and three unsupported-width length calls. Six length rows are arithmetic
witnesses of a private primitive, not huge-container executions. The 31 native
rows describe host admission boundaries that Rust typed values cannot express.
No assertion that Rust executed all 186 rows is made.

Native accounting differs honestly by public host surface:

| Engine | Public runtime rows | Arithmetic-only | Explicit language/type exclusions |
| --- | ---: | ---: | ---: |
| Rust | 149 | 6 | 31 |
| Go | 150 | 6 | 30 |
| Swift | 154 | 6 | 26 |
| Python | 179 | 6 | 1 |
| TypeScript | 180 | 6 | 0 |

Python does not manufacture a rounded or float value to claim execution of the
TypeScript-only unsafe Number boundary. Go and Swift distinguish typed or UTF-16
source exclusions from actual public runtime rejection. Arithmetic checks are
not reported as public codec execution in any language.

## Passed finite evidence

[Rust](rust/README.md) records build `10cf0553837042e1a7f3ed637841a54e` and actual
execution `dd28f779ef86442e91c6ddd93bfbdf1c`, with 477 proper-prefix rejections and
83 each of trailing-byte, bounded-subview and independent-owned-result checks.
Focused controls prove raw callbacks still execute on a failed live sink and
that independent LengthEncode/LengthDecode-only types work directly and through
a guarded width wrapper without ordinary trace-trait supertraits. Actual
Rust64 size/alignment measurements cover 41 registry entries, 21 named ledger
entries and 11 additional exact shapes; unknown shapes remain unqualified.

The separate [Rust application smoke](rust-app/) passed job
`144d7be340f7422e95dd86d6655fa046`: all 83 value bundle bytes matched independent
goldens, and real typed EvidenceRecorder/phase replay retained the producer's
configuration and exact v2 bytes. Its Output implements encoding only. The smoke
uses the pre-LengthOnly focused reference build, explicitly pinned in its own
source receipt; no identical-binary claim is made for that smoke and the later
reference executable. The installed matrix will use the final frozen executable.

Final native receipts record:

- [Go](go/RESULTS.md): uncached tests `c174d20805dc4e5c9c6b8ad481c813d9` and vet
  `6f5fe914a3f6427abcde1299eae3e551`.
- [Swift](swift/RESULTS.md): `48cd9305ccfe4ea68b9a1e6b82b9a40e`, 134 XCTest tests,
  zero failures. The separate zero-test Swift Testing footer is not the count.
- [Python](python/source-and-runtime.json): `2f7e702f48c440769c2ba54bd67f12fc`,
  248 tests with 12 opt-in ABI skips.
- [TypeScript](typescript/jobs/fa80f97beec847f99c93008a0d70710b.json):
  `fa80f97beec847f99c93008a0d70710b`, compilation and 829 tests, zero failures.

All listed jobs ran through Kyoto; their requested/effective command and terminal
status are retained. Compilation alone is not called a test pass. Failed,
cancelled and superseded runs remain separate, with lossless compressed raw logs
and byte/hash ledgers.

## Identity and remaining boundaries

Python's final full manifest was captured **after the job started**, not before
submission. Its held module/test hashes were reported before submission, all
recorded source mtimes precede execution, and final hashes reconcile. That is
useful evidence but does not establish a full pre-start source manifest. The
limitation is retained in [summary.json](summary.json) and the Python receipt.

[source-inputs-final.json](source-inputs-final.json) is a current snapshot of
runtime, package/test, fixture, reference and application/driver inputs. It is not
retroactive pre-start provenance or an installed pass. Documentation and receipts
are excluded to avoid recursive hashes; two embedded controller requirement
files are included as actual Rust runtime/build inputs. Root will reconcile
installed execution separately.

Independent static reviews are in [Go/Swift](review-go-swift.md) and
[Python/TypeScript](review-python-typescript.md). Their hashes and reviewed ranges
are distinct from executable evidence. Logical Rust64 storage weights are not
native heap/RSS measurements or an arbitrary-layout algorithm. Custom callback
purity and declared accounting weights remain cooperative contracts; arbitrary
callback execution, OOM behavior, Rust32, other platforms, browser execution,
registry distribution and performance remain unqualified. N13 derive and
ModelCodec convenience surfaces are future work. Existing promoted profiles
remain unchanged.
