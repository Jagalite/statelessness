# Python/TypeScript canonical value codec static review

Read-only implementation review against the actual Rust public traits and
decoder/encoder, the candidate contract and the frozen 186-row corpus. The
reviewer ran no native build or test for this review. Separate Kyoto receipts
provide runtime evidence; this document does not promote a profile or qualify
arbitrary custom layouts, callbacks or native heap consumption.

No unresolved concrete finding remains in the implementation snapshots below.

Reviewed fixed integer and signed-128 boundaries, strict UTF-8/BOM handling,
tagged options and unit, declared collection storage weights, element/work/depth
ordering, guarded versus raw decode and direct length overrides, and sticky
first errors. Reviewed byte input ownership and subviews, quota admission before
payload copying, exact native integer admission and immutable built-in descriptor
configuration. The TypeScript-only unsafe Number fixture is explicitly
inapplicable to Python; its adapter no longer manufactures a different host value
to claim execution of that boundary.

Raw encode calls retain callback-result versus sticky-write/finish distinctions,
run on an already failed live sink, revoke child leases on every exit, and never
implicitly finish. Existing stronger callback helpers remain distinct. Returned
validation errors may preserve accepted bytes; ignored rejected writes still
fail a subsequent explicit finish. String encoding retains the length prefix
before rejecting an oversized body.

Independent custom encoding and decoding capabilities now propagate through
options, vectors, zero-length arrays, tuples and OracleState, including empty or
absent values. Length-only callbacks have independent availability without an
ordinary encode/decode supertrait; fixed-width wrappers derive their directions
from those overrides. Encoding does not require an unused decoder or collection
layout. Unknown element weights fail only on collection decoding; composed
collections retain the captured weight rather than rereading a mutable custom
descriptor binding.

Findings resolved during this review were TypeScript's eager collection layout
requirement, mutable/missing layout rereads, and an undefined sticky encoder
diagnostic if an application error formatter threw or reentered. Its encoder now
publishes a defined placeholder before formatting and owns the stored diagnostic
independently of caught mutable Error objects. Python's corresponding formatter
placeholder and prefix-before-body admission were already present; its final
delta captures custom element weight bindings. Root's independent trait-direction
findings and the subsequent composition and length-only fixes were also reread.

Custom callback purity, faithful application descriptors and explicit cooperative
allocation weights remain contract preconditions, not a code sandbox. Logical
Rust64 weights are not managed-runtime allocation or RSS bounds.

## Exact reviewed bytes

| File | SHA-256 |
| --- | --- |
| `spec/canonical-value-codec-v1.md` | `c94ea1d77a51d66c1da3e7ff73d146fd35011d2c5519ef8060bd9f2d89393867` |
| `conformance/canonical-value-codec-v1.json` | `bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c` |
| `src/value_codec.rs` | `9c8ec3107cc69f6febe24cb2969d66d344fc1f19cee3341484f7b0dda274baa3` |
| `src/model.rs` | `e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb` |
| `python/src/statelessness/value_codec.py` | `2033feabba16cea05a94d65a7e27ce014fd374cbd7f5fc853e8dad4236a5084e` |
| `python/src/statelessness/codec.py` | `d2763cf6b78423d8abfcab6efe84c68ebced60cacc22a92f5c3556b8fa75a02a` |
| `typescript/src/value_codec.ts` | `d0b174932f64490bb7ad4351c54f148cdbe70cae1a3598769a1922e0c35cfc8f` |
| `typescript/src/encoding.ts` | `cdf3f58810813dcb555a9479c2a65962f12ca5a6290659e50a41b6b7b9e7f93b` |

## Final custom-length width delta

The subsequent narrow boundary delta was independently reread without builds or
tests. No unresolved finding remains. Python validates an exact int in 0..255
before capability lookup or custom callbacks in both raw length helpers, exports
the symmetric direct `decode_length_raw`, and neither adds an outer value guard
nor implicitly finishes the reader. TypeScript validates an exact Number integer
in 0..255 before its custom length methods and before descriptor access in
`withLength`. A custom width of 3 remains valid for both languages; the builtin
length codecs independently restrict their own supported widths to 1/2/4.

The focused test source checks invalid width admission without callbacks or byte
consumption, positive custom width 3, and the independent direct decode surface.
Existing encoding glue remains at the hashes above. These are static review
identities; runtime results remain in the owners' separate Kyoto receipts.

| Final file | SHA-256 |
| --- | --- |
| `python/src/statelessness/value_codec.py` | `c85edbe7133374af8cbe01b50ba6256fd34ff897819a82abd461e7fb61fe5735` |
| `python/src/statelessness/__init__.py` | `397a1ea891572fbfe6de2d3ba2a5b2e835be2ef3c5751450ef08c7d100d56f72` |
| `python/tests/test_value_codec.py` | `f061cb63ea44e1ff8a94d17a98836607097c256094a7328b5a089a2fe283680b` |
| `typescript/src/value_codec.ts` | `5735f0def5d622d94eddcb5206668900d9396a2bb4ab4f506741386b7ab682a5` |
| `typescript/src/index.ts` | `0e1f9b9d5d31b9935ec510ffb604fc427aae3dbe56aeb441b214886fe3157264` |
| `typescript/test/value-codec.test.mjs` | `0fa55eb47f42badf5409ce3b34426564e23f3a3fa8a66ab3aa90180798532038` |
