# M0 legacy lifecycle symbol map: static inventory, not qualification

This maps all 66 compiler-receipt records in `semantic-symbols.jsonl` whose
source is `src/lifecycle.rs`, pinned to commit `0876717bee85e335449c2c99ada41f6c6e6112d1`.
The pinned file SHA-256 is `e13a66e70a6b29b692e95093b4d7cc722bcc6983c709a9575a9df8ef7907cc6f`; the current worktree file SHA-256 is
`e13a66e70a6b29b692e95093b4d7cc722bcc6983c709a9575a9df8ef7907cc6f`. Receipt exports describe the pinned snapshot. Current native
candidates are drawn from a dirty worktree and do not establish semantic parity,
installed execution or qualification. `src/lifecycle/indexed.rs` is a separate
source and is not inventoried by this map.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `Event<K>` | `Event` | `LifecycleEvent` | `LifecycleEvent` | `LifecycleEvent` | Independently verify each event's ordering, deadline boundaries, malformed events, reference ownership and per-language key semantics. |
| `History<K>`, `Default`, retained fields | `History`, `LifecycleMonitor.initial_state` | `LifecycleHistory`, `LifecycleOracle` | `LifecycleHistory`, `LifecycleMonitor.initialState` | `LifecycleHistory`, `LifecycleMonitor.oracle` | Validate default identity, cloned histories, equality, bounded growth and first violation order; candidate types also represent native indexed state. |
| `History` begin/cancel/settle/publish/acquire/release/advance/check helpers | `LifecycleMonitor._apply`, `advance`, `check_state` | `LifecycleMonitor.Advance`, `Checks` | `LifecycleMonitor.advance`, `checkState` | `LifecycleMonitor.advance`, `checks` | Compare explicit event streams including missing and duplicate keys, cancellation, late completion, deadlines and error/overflow precedence with independent expected results. |
| `LifecycleSpec`, `LifecycleMonitor` and `Oracle` implementation | `LifecycleMonitor` with supplied spec | `LifecycleOracle` classifiers | `LifecycleSpec`, `LifecycleMonitor` | `LifecycleMonitor.oracle` classifier closures | Verify classifier ordering, clone isolation and failures, callback ownership, metadata identity and complete paired integration. Swift reference output isolation requires a supplied clone hook; Go and TypeScript output snapshots are shallow. |
| `TraceEncode`, `TraceDecode`, `OracleCodec` for `History` | `LifecycleMonitor.encode_history`, `decode_history` | `LifecycleHistoryCodec` | `lifecycleHistorySchema`, `withLifecycleCodec` | `LifecycleMonitor.historyCodec` | Prove historical byte compatibility and cross-engine replay with independently authored fixtures, model and key codec identities, malformed data, bounds and fresh processes. |
| Rust standard trait implementations and module export | Python lifecycle module | Go statelessness package | TypeScript lifecycle module | Swift Statelessness module | Review observable equality, cloning and export outcomes; Rust trait shape itself is not a native API requirement. |

Candidate source identities observed during inventory:

- `python/src/statelessness/lifecycle.py`: `5d9702e6ef1e5220d2cae1390075713f09823c389f6612238b2b13d08633bf64`
- `go/lifecycle.go`: `2784faf668ab9aaa9a0eb063974f8b4d5344aae5b5873583f13217e109741c27`
- `go/lifecycle_codec.go`: `898c7dadf6931949fc0ef6cb4375789ff97f250700dd6e8cbcb0eefd4d491371`
- `typescript/src/lifecycle.ts`: `7c978701cf5525027ad666bc9b7c3d9f621a89d27def23ed6f8a3f0c97db7bb9`
- `swift/Sources/Statelessness/Lifecycle.swift`: `b1df2f5a64da229dbfcfed1a43b047162d064456f21f5147e303c9ba9afa1a55`
- `swift/Sources/Statelessness/LifecycleCodec.swift`: `f032d9080134a06fb64d885641c529c08862a9475c58c030b12ed68ebe33b4a3`

This is a static symbol inventory. Independent semantic contracts, historical
codec vectors, callback and retention stress, real application faults, installed
consumers, performance and platform receipts remain open. M0 also remains open for
other modules, aliases and semantic export resolution.

## Receipt records

Each row is a pinned compiler-receipt record. The first reachable export is shown;
other reachable aliases remain in the JSONL source of truth.

| Receipt ID | Role | Kind | First reachable Rust export |
| --- | --- | --- | --- |
| `statelessness:7102` | implementation_member | function | `<stateless::lifecycle::Event as Clone>::clone` |
| `statelessness:7103` | public_std_trait_implementation | impl | `<stateless::lifecycle::Event as Clone>::{impl}` |
| `statelessness:7104` | implementation_member | function | `<stateless::lifecycle::Event as Debug>::fmt` |
| `statelessness:7105` | public_std_trait_implementation | impl | `<stateless::lifecycle::Event as Debug>::{impl}` |
| `statelessness:7109` | public_std_trait_implementation | impl | `<stateless::lifecycle::Event as Eq>::{impl}` |
| `statelessness:7107` | implementation_member | function | `<stateless::lifecycle::Event as PartialEq>::eq` |
| `statelessness:7108` | public_std_trait_implementation | impl | `<stateless::lifecycle::Event as PartialEq>::{impl}` |
| `statelessness:7106` | public_std_trait_implementation | impl | `<stateless::lifecycle::Event as StructuralPartialEq>::{impl}` |
| `statelessness:7135` | implementation_member | function | `<stateless::lifecycle::History as Clone>::clone` |
| `statelessness:7136` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as Clone>::{impl}` |
| `statelessness:7137` | implementation_member | function | `<stateless::lifecycle::History as Debug>::fmt` |
| `statelessness:7138` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as Debug>::{impl}` |
| `statelessness:7145` | implementation_member | function | `<stateless::lifecycle::History as Default>::default` |
| `statelessness:7146` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as Default>::{impl}` |
| `statelessness:7142` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as Eq>::{impl}` |
| `statelessness:7143` | implementation_member | function | `<stateless::lifecycle::History as Hash>::hash` |
| `statelessness:7144` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as Hash>::{impl}` |
| `statelessness:7140` | implementation_member | function | `<stateless::lifecycle::History as PartialEq>::eq` |
| `statelessness:7141` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as PartialEq>::{impl}` |
| `statelessness:7139` | public_std_trait_implementation | impl | `<stateless::lifecycle::History as StructuralPartialEq>::{impl}` |
| `statelessness:7149` | implementation_member | function | `<stateless::lifecycle::History as TraceDecode>::trace_decode` |
| `statelessness:7150` | public_local_trait_implementation | impl | `<stateless::lifecycle::History as TraceDecode>::{impl}` |
| `statelessness:7147` | implementation_member | function | `<stateless::lifecycle::History as TraceEncode>::trace_encode` |
| `statelessness:7148` | public_local_trait_implementation | impl | `<stateless::lifecycle::History as TraceEncode>::{impl}` |
| `statelessness:5447` | implementation_member | assoc_type | `<stateless::lifecycle::LifecycleMonitor as Oracle>::State` |
| `statelessness:5452` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as Oracle>::advance` |
| `statelessness:5453` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as Oracle>::check_state_into` |
| `statelessness:5451` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as Oracle>::initial_state` |
| `statelessness:5450` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as Oracle>::metadata` |
| `statelessness:5454` | public_local_trait_implementation | impl | `<stateless::lifecycle::LifecycleMonitor as Oracle>::{impl}` |
| `statelessness:5475` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as OracleCodec>::decode_history` |
| `statelessness:5473` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as OracleCodec>::encode_history` |
| `statelessness:5474` | implementation_member | function | `<stateless::lifecycle::LifecycleMonitor as OracleCodec>::encode_history_into` |
| `statelessness:5476` | public_local_trait_implementation | impl | `<stateless::lifecycle::LifecycleMonitor as OracleCodec>::{impl}` |
| `statelessness:7115` | named_export | module | `stateless::lifecycle` |
| `statelessness:6835` | named_export | enum | `stateless::lifecycle::Event` |
| `statelessness:7081` | public_member | variant | `stateless::lifecycle::Event::Acquire` |
| `statelessness:7080` | public_member | struct_field | `stateless::lifecycle::Event::Acquire::0` |
| `statelessness:7073` | public_member | variant | `stateless::lifecycle::Event::Begin` |
| `statelessness:7072` | public_member | struct_field | `stateless::lifecycle::Event::Begin::deadline` |
| `statelessness:7071` | public_member | struct_field | `stateless::lifecycle::Event::Begin::key` |
| `statelessness:7075` | public_member | variant | `stateless::lifecycle::Event::Cancel` |
| `statelessness:7074` | public_member | struct_field | `stateless::lifecycle::Event::Cancel::0` |
| `statelessness:7079` | public_member | variant | `stateless::lifecycle::Event::Publish` |
| `statelessness:7078` | public_member | struct_field | `stateless::lifecycle::Event::Publish::0` |
| `statelessness:7083` | public_member | variant | `stateless::lifecycle::Event::Release` |
| `statelessness:7082` | public_member | struct_field | `stateless::lifecycle::Event::Release::0` |
| `statelessness:7077` | public_member | variant | `stateless::lifecycle::Event::Settle` |
| `statelessness:7076` | public_member | struct_field | `stateless::lifecycle::Event::Settle::0` |
| `statelessness:7085` | public_member | variant | `stateless::lifecycle::Event::Tick` |
| `statelessness:7084` | public_member | struct_field | `stateless::lifecycle::Event::Tick::0` |
| `statelessness:5448` | named_export | struct | `stateless::lifecycle::History` |
| `statelessness:7110` | public_member | struct_field | `stateless::lifecycle::History::now` |
| `statelessness:7111` | public_member | struct_field | `stateless::lifecycle::History::obligations` |
| `statelessness:7117` | implementation_member | function | `stateless::lifecycle::History::pending` |
| `statelessness:7112` | public_member | struct_field | `stateless::lifecycle::History::resources` |
| `statelessness:7113` | public_member | struct_field | `stateless::lifecycle::History::violations` |
| `statelessness:7118` | public_inherent_implementation | impl | `stateless::lifecycle::History::{impl}` |
| `statelessness:5455` | named_export | struct | `stateless::lifecycle::LifecycleMonitor` |
| `statelessness:7156` | public_member | struct_field | `stateless::lifecycle::LifecycleMonitor::max_history` |
| `statelessness:7155` | public_member | struct_field | `stateless::lifecycle::LifecycleMonitor::spec` |
| `statelessness:5449` | named_export | trait | `stateless::lifecycle::LifecycleSpec` |
| `statelessness:7151` | trait_member | assoc_type | `stateless::lifecycle::LifecycleSpec::Key` |
| `statelessness:7153` | trait_member | function | `stateless::lifecycle::LifecycleSpec::input_events` |
| `statelessness:7152` | trait_member | function | `stateless::lifecycle::LifecycleSpec::metadata` |
| `statelessness:7154` | trait_member | function | `stateless::lifecycle::LifecycleSpec::output_events` |
