# Go/Swift canonical value codec static review

## Swift reviewed snapshot

Reviewed at 2026-10-20T23:22:36.920675+00:00. This is read-only source review; the reviewer ran no builds or tests. Hashes pin reviewed bytes, not runtime qualification or broad parity.

Compared the candidate contract and Rust value decoder/encoder with Swift typed descriptors, exact 128-bit limbs, strict UTF-8/BOM preservation, independent guarded/raw/direct-length call graphs, collection/work/depth first-error ordering, owned reader results, bounded string admission before payload copying, retained prefixes, raw callback return priority, revocable leases and value-aware diagnostic variants. Finite explicit measured-layout lookup is distinct from caller-declared layout and native heap/RSS. Existing stronger encode helpers remain unchanged.

No unresolved concrete Swift implementation finding in this snapshot. The fixture adapter initially composed stronger encode calls; its final delta now composes encodeRaw/encodeLengthRaw. The six raw encoder scripts already used typed public APIs. The final test delta correctly executes three unsupported-width public calls and classifies six arithmetic-only witnesses, without calling them Rust execution. A new regression invokes two raw callbacks on an already failed sink, retains callback error priority and raw success, and separately checks sticky finish. Runtime results must be read from the separate Kyoto receipts; no pass is inferred here.

Additional final Swift delta: reviewed four independent ordinary/length encode/decode capability callbacks, constructor admission, encode-only/decode-only protocol adapters and composition propagation. Missing child directions reject before prefixes/guards even for absent options, empty vectors and zero arrays. Existing sticky reader errors precede native unavailable-direction errors; otherwise native direction rejection consumes no guard budget. Length-only descriptors and withLength preserve selected direct call graphs, layout/variant metadata and raw callback leases, without requiring ordinary traits. The five direction regression methods cover these public boundaries. No unresolved concrete finding in this delta; the queued final suite is separate evidence.

## Go final reviewed snapshot

Final frozen Go candidate reviewed after author-reported uncached tests/vet ecd094331b9d4945ae555c4acb967f53/c116ca9aa98c4c75b4d4d13c9f0700e8; those runtime claims are separate receipts, not reviewer execution. Reviewed optional encoder/decoder construction and per-child composition gates, independent LengthValueCodec constructor and selected-width adapters, raw callback leases/no automatic finish, sticky reader guard semantics, constant panic errors without panic-payload formatting, canonical finish error projection and typed fixed-array conversion adapter. Missing capabilities cannot bypass composition through absent/empty/zero values. Guarded unavailable callbacks remain native errors; Go's guard can record them, unlike Swift's earlier native direction admission.

Root subsequently found a mutable built-in Go decoder error alias: callers could edit a returned *ModelError.Message and rewrite the stored first error. The narrow final fix copies direct built-in *ModelError values both when recording the first failure and on every sticky return (take, collection, finish, guards and contained panic). Raw callback validation errors remain cooperative until propagated through a guard. Read-only review confirms no remaining direct intrinsic-error alias in those paths; the new ownership regression covers returned/callback mutations and hostile opaque errors without formatting. Final author-reported tests/vet c174d20805dc4e5c9c6b8ad481c813d9/6f5fe914a3f6427abcde1299eae3e551 are separate runtime evidence.

Opaque foreign Go/Swift callback Error objects retain native identity and caller-owned diagnostic behavior; they are not cloned or formatted to manufacture ownership. Go direct built-in ModelError is now detached; Swift intrinsic ModelError is an immutable value. No security boundary or arbitrary foreign-error snapshot guarantee is claimed.

No unresolved concrete Go finding in these bytes. The earlier extra-layout concern is resolved by the eleven actual Rust additional measurements archived under job 4b9fd11a1102436a8883200114ade29f: Option integer weights and unit/u8 tuple defaults match the measured values. Diagnostic schema spellings do not confer trusted built-in identity; explicit layout overrides clear it. Unknown compound collection element layouts require an explicit declaration. Broader capability/platform/resource qualification is not inferred.

## Exact reviewed bytes

| File | SHA-256 |
| --- | --- |
| `spec/canonical-value-codec-v1.md` | `c94ea1d77a51d66c1da3e7ff73d146fd35011d2c5519ef8060bd9f2d89393867` |
| `src/value_codec.rs` | `9c8ec3107cc69f6febe24cb2969d66d344fc1f19cee3341484f7b0dda274baa3` |
| `src/model.rs` | `e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb` |
| `conformance/canonical-value-codec-v1.json` | `bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c` |
| `swift/Sources/Statelessness/ValueCodec.swift` | `ee822a34fa231667e4899fb5f0c529698f46fd20ab0c62bd28a0b29c8ca4fb09` |
| `swift/Sources/Statelessness/ValueCodec128.swift` | `7b5baafc2af0cd39d2065129747e960b926ee3b7b7adb48a5d4e13770d664e8e` |
| `swift/Sources/Statelessness/ValueCodecBuiltins.swift` | `72d3ed6b50d928c663df8e15fb3342dc09bdaa6ebe7d522aaf4389932b6a081b` |
| `swift/Sources/Statelessness/ValueCodecDecoder.swift` | `afe240b9b9b0ef714b252851287caf8d52f5ffbb52a1d8b607f5af63ddd483f4` |
| `swift/Sources/Statelessness/ValueCodecLayouts.swift` | `7d6f502cffad7c962118309db01523716719d26b469417ba456bdff55f90b219` |
| `swift/Sources/Statelessness/EncodeBuffer.swift` | `82fbf8cc7383046bfc3bee342415b203256b7ab009149cd264e0355ebc900686` |
| `swift/Tests/StatelessnessTests/ValueCodecTests.swift` | `b72301bc83ffbf081d5f7f5f56bc928383521ecddbd3e29efa138b53e1f7147b` |
| `swift/Tests/StatelessnessTests/ValueCodecDecoderTests.swift` | `2e2d2a7d0257edf3e571337f6b8ea8fcfb144632c4a006f2d8b38f7099d86323` |
| `swift/Tests/StatelessnessTests/ValueCodecEncoderCorpusTests.swift` | `42d31eb8c38819099f4229370745e78c611d2c0a1629aa21d99c648bc040ef47` |
| `swift/Tests/StatelessnessTests/ValueCodecReaderCorpusTests.swift` | `c556dcd659e34fd5b8c98d41e6e89f1a19b961d74f98dacc85715e0a65d36996` |
| `swift/Tests/StatelessnessTests/ValueCodecDirectionsTests.swift` | `fbf9294b7daac23598e257a40a50e0351472712e4c2448187de13ce45e683211` |
| `go/value_codec.go` | `1d1d0cec2828015e11e171592a267c3ece191111db32ecfaf75e22c2f3766108` |
| `go/value_composite.go` | `c6fc074dc2d8e86529790bd8f27481fa0107efa057d864d4702d577d6736d02a` |
| `go/value_array_adapter.go` | `797762172d9675ae4ccf9ae6ba16a00e309c5cfc649ae0484e803387e85bedba` |
| `go/value_length.go` | `f71b4d950bce4df709999a9e15066a0e649069e26aa360718a55a3996f8b925c` |
| `go/encode_buffer.go` | `3860dddd9bb441c27b57e4cc6a3547206d5b2548d736baf3067c1a2d0f06030a` |
| `go/value_corpus_test.go` | `1c1b921a5215018bd55af208e7efb24cc1847ede8cc1a77be9b7ccda712cfb49` |
| `go/value_composite_test.go` | `f19e75a084a15529cdabc114e9a3d7a4fe824106bb981e656be06dd6a33de0b4` |
| `go/value_array_adapter_test.go` | `0ad6585578eb878edab3abd492e5e7065f024e97d00f618c1f3026157a0abe76` |
| `go/value_capability_test.go` | `56a19f1df42df361dce5d023d0ad03c82fc6d303b3fe3808eb40ecca8efbe227` |
| `go/value_length_test.go` | `ac68af4417b22c1f63b6a8b58c47586474daedc6e9128d06f96e9a5302e7cf9f` |
| `validation/native-parity/value-codec-slice/rust/intermediate-additional-layouts.json` | `272fc1a5a8c9d52835900e97b4befe08b0117fdd4e94a4a542c082910f86b4da` |
| `go/value_error_ownership_test.go` | `6d1b3cdbfb6f34fc7d64ef6d51a48fd0544befa8ebfe77286299a0a1936419bd` |
