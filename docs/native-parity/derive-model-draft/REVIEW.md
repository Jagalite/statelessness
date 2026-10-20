# Independent N13 draft review

Static design review only. No runtime implementation, macro expansion, build or
fixture execution was performed. Reviewed the four API proposals, shared draft
contract and initially 81, now 90 manually authored rows against DERIVE-MODEL-CODEC-AUDIT and the
actual Rust `codec.rs`, `model_codec.rs`, `value_codec.rs` and model codec hooks.
This is not qualification or approval of future implementation bytes.

## Findings and resolution

1. **P2, corrected: Go constructed before fields succeeded.** The initial
   `begin`/setter design ran application construction during partial decode,
   contradicting Rust's field-local construction and the draft's zero-constructor
   error witnesses. The revised [Go typed product](go-api.md#typed-ordered-fields)
   collects field locals and invokes one final constructor only after all its
   fields succeed. Nested records construct after their own successful fields;
   outer construction still waits. The shared contract was not weakened.
2. **P2, resolved design alignment: neither-direction declarations.** The
   revised contract explicitly permits inferred capability intersections,
   including neither, but does not require every factory to offer inference.
   An explicitly requested unavailable direction rejects at construction/type
   checking. Thus a selected-direction-only builder and an inference builder
   have distinct documented admission policies, without invented callbacks or
   data-dependent bypass. Go, TypeScript and Swift now spell out default inference and optional
   eager requirements without filtering inferred capabilities. Swift also
   specifies optional constructors and all-case enum intersections.
3. **P2, corrected finite acceptance coverage.** The revised 90 rows add u16
   maximum tag65535 (`ffff`), encode-only/decode-only record and enum-unit
   positives, and four raw/guarded first-error reader scripts. Positive protocol
   now dispatches by operation instead of demanding both directions of every
   positive declaration. The explicit unselected-case missing-decoder schema
   ties declaration rejection to all-case capability validation. Earlier review
   overstated that rejection's absence: an initial negative row already existed;
   the revision makes its concrete directional schema explicit.
4. **P3, corrected: Go legacy maximum wording.** The revised proposal identifies
   MaxInt as the host slice/index ceiling, not Rust64 usize::MAX equivalence.
   This remains an unbounded-legacy-allocation boundary, not a budget guarantee.

## Verified design details and remaining gates

Manual positive wire/budget arithmetic is consistent: bool's nested u8 makes the
three-field Record work5/depth3; Pair is work3/depth2; ordinary String abc is
work6/depth3 versus direct-length work5/depth2; adapter take-only field is
work1/depth1; enum payload fields have no hidden record guard; PairVector is
work8/depth3 with its explicitly declared cooperative storage weight8 total.
The new u16 maximum witness, directional decode work2/depth2, swallowed raw unknown-tag continuation and guarded/first-structural-error scripts are also consistent with the Rust call graph. These are manual source calculations, not execution results.

No unresolved concrete draft-semantic finding after the documented revisions. This closes static review of the proposed design only; implementation and acceptance execution remain future work.

The proposals preserve raw field encoding, direct length/adapter decoding,
independent length traits, explicit enum widths/tags, unknown-tag rejection before
payload construction, immutable captured declaration configuration and explicit
record/enum collection weights. All eight model codec methods gate current
metadata version before value/sink/reader/limit callbacks. State/Input require
both ordinary directions; Output requires encoding only. Limits are fresh per
decode and never evaluated during encoding. Model forwarding/callback ownership
remain native responsibilities and need implementation-level regressions.

Future evidence must separately classify builder/type/compile rejection, real
Rust runtime paths, native-only callback/ownership behavior and installed-package
interchange. No automatic Rust layout inference, schema-label identity,
reflection fallback, output decoder, profile promotion or broad parity follows
from these drafts.
