# M0 macro symbol map: source inventory, not qualification

This is a scoped mapping of the eight `statelessness-macros` records in
`semantic-symbols.jsonl`, including the crate root. The compiler-resolved IDs
are from the existing semantic receipt and identify its default-target snapshot;
they are not stable across rustdoc runs. The Rust behavior baseline is commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`; the working
`crates/statelessness-macros/src/lib.rs` matched it when inspected, SHA-256
`4e9c4142739699ec35b5143a15024a942cb3659e54dffd94a7cc7660ba8c156c`.
The semantic export scan, not this document, is the authority for what was
reachable in that snapshot. This map records candidate native entry points and
explicit open obligations; it does not establish behavior equivalence or a
qualified parity cell. Native sources were dirty, and their file hashes below
identify only the inspected snapshot, not a release artifact.

## Consumer symbols

| Rust record and work item | Native outcome and candidate source | Still open |
| --- | --- | --- |
| `statelessness-macros:241`, crate root, N13 | Rust-specific packaging namespace; native packages expose their own modules and do not need a procedural-macro crate. | Confirm installed exports and package identity for each target. |
| `:234`, `model`, N13 | Python `ModelAdapter` in `modeling.py`; Go `AdaptModel` in `modeling.go`; TypeScript `adaptModel` in `modeling.ts`; Swift `adaptModel`/`defaultInitial` in `Modeling.swift`, together with each engine's application-owned callbacks. | Map each supported model attribute, generated method, validation diagnostic and property/default/generation behavior individually. Native runtime validation does not automatically reproduce compile-time diagnostics. |
| `:235`, `TraceEncode`, N13 | Python `ordered_record`/`tagged_enum` in `codec_declarations.py`; Go `OrderedRecord`/`TaggedEnum` in `value_derive.go`; TypeScript `orderedRecord`/`taggedEnum` in `value_codec.ts`; Swift `TraceRecords`/`TraceEnums` in `DeriveCodec.swift`. | Verify every supported shape, tag/length option, encode-only case, validation failure, and byte-for-byte fixture independently. Explicit declarations are not Rust derive syntax. |
| `:236`, `TraceDecode`, N13 | Same four declaration families as `:235`, with their decode directions. | Verify bounded decoding, malformed inputs, directional requirements, and all supported shape/attribute cases independently. Encoding evidence alone is insufficient. |
| `:237`, `Inspect`, N12/N13 | Python `FieldView`/`ObjectView`/`inspect` in `debugger_inspect.py`; Go `InspectAdapter`/`InspectContext` in `debug/inspect.go`; TypeScript `Inspect`/`InspectContext`/`projection` in `debug_inspect.ts`; Swift `InspectContext` in `StatelessDebug/Inspection.swift`. | Map derive schema/version, names, variant and field IDs, source metadata, redact-without-access, limits and diagnostics to explicit native projections; verify privacy and installed optional-debug use. A manual projection can diverge from generated traversal. |
| `:238`, `input_domain`, N13 | Explicit `Domain` construction in Python `modeling.py`, Go `modeling.go`, TypeScript `modeling.ts`, Swift `Modeling.swift`; application supplies conditional entries and products. | Verify bounds, ordering, conditions, duplicate behavior, sampling and invalid definitions; Rust compiler exhaustiveness diagnostics are not established for native declarations. |
| `:239`, `macro_build_id`, N13 | Rust macro implementation identity mechanism. Use each native package's own source/package/tool identity in receipts, not a fabricated Rust macro ID. Python has `runtime_build_id` in `modeling.py`; this observation does not establish equivalent identities for other packages. | Establish and verify reproducible identities for each packaged consumer. |
| `:240`, `ModelCodec`, N13 | Python `ModelCodec.bind`/`model_codec` in `codec_declarations.py`; Go `BindModelCodec` in `model_codec_bind.go`; TypeScript `bindModelCodec` in `value_codec.ts`; Swift `ModelCodecBinding` in `DeriveCodec.swift`. | Verify metadata-version gating, both state/input decode and encode, output encode, bounded decode and error behavior with installed models. Identity and property metadata are not inferred by the Rust derive. |

`#[trace]`, `#[inspect]` and `#[model_codec]` are helper attributes attached to
the listed derives rather than additional macro exports in this receipt. Their
individual options still need the contract mapping above. This file does not map
other public macros, core `modeling` symbols, debugger members, compiler-generated
implementations, tooling binaries, other `cfg` targets or future source changes.
See `N13-N14-MODELING-20261029.md` for broader behavioral context and
`INVENTORY.md`/`semantic-receipt.json` for inventory limitations. The full M0
ledger and independent review remain open.

## Native source identities at inspection

SHA-256, computed directly from the worktree; this is static source evidence only:

```text
fca4213028ee98a657823b092b1eb0f139f818d6d90135f94834c02ad9b979ea  python/src/statelessness/modeling.py
7e5c0bc9fb837121b88e6ec9728b6c787ee58c37cc22245bda18d1442e98034f  python/src/statelessness/codec_declarations.py
f90b839b8f8ecb87cf3d8d3d05a75e8e50a3e7a9ae9817deb2f7a946e661743f  python/src/statelessness/debugger_inspect.py
a1760eef80cfd7c794faab27edc5ab18fee8848d68af8aa9155f700e7cfef272  go/modeling.go
6d69ab76a6abecddabf4be455ec8e8e279e7ae09379fa30a82eaaa158bb531c3  go/value_derive.go
07f6571cf2260f7f4d5acdccc4aaa33960a5b781c38bee1b279f6ca7b73ef921  go/model_codec_bind.go
2438f05fc71a94004e03493eda7b8ca9bff54930383d12942cdbeccb3fffa25b  go/debug/inspect.go
6d7bf51b22e43c7eaaac4eed8c467f585c44322640c3d36ba72c3641f39ffe29  typescript/src/modeling.ts
f42623926009fef488763f810e9ec9a877a4d20856316e527ebe81816c155e5d  typescript/src/value_codec.ts
bb42720ee6f04d77183fa720f550e337bf5e9b9e25c6b0bc844b4fae5ca434c6  typescript/src/debug_inspect.ts
e1ced857c2a7017f1c44a1cb855ec77991beb53129e0f317a7ce49f72c09cdfe  swift/Sources/Statelessness/Modeling.swift
8acc14a3b5597c813f3f197775af04cb2c8a58ede2574e0cf3411cd54a7eb841  swift/Sources/Statelessness/DeriveCodec.swift
a2f0db4a20c75c17e0fee0c695f79e05886cb88bceb92085a4e7e63e46d879ec  swift/Sources/StatelessDebug/Inspection.swift
```
