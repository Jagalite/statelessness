# M0 composition symbol map: static inventory, not qualification

This accounts for all 94 compiler-receipt records in `semantic-symbols.jsonl`
whose source is `src/composition.rs`, pinned to commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`. Both the pinned and current Rust
file have SHA-256 `dcef4c9ed0f1a64bba8834f2482261c02ea630d9666237d3b08cd5eb1f5b94ec`.
Receipt reachability and identities describe the pinned snapshot; cfg-specific
exports and generated APIs require separate inspection. Native names are
candidates from the dirty worktree, not claims of semantic equivalence.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `Address`, `Input`, state and aliases; clone, equality, hash and trace traits | `Address`, `PairInput`, `PairState`, `stateful_codec.pair_schemas` | `PairAddress`, `PairInput`, `PairState`, `PairAddressCodec`, `PairCodec` | `PairAddress`, `PairInput`, `PairState`, `pairSchemas` | `PairAddress`, `PairInput`, `PairState`, `pairAddressCodec`, `Pair.codec` | Compare side tags, pending queue identity, cloning/ownership and trace bytes across independent engines. Rust trait machinery and aliases need equivalent behavior rather than identical syntax. |
| `Wiring` metadata, messages, state and transition checks | `Pair.wiring` callbacks | `PairWiring` | `PairWiring` | `PairWiring` | Verify callback ordering, mutable alias isolation, error propagation, append-only checks and identity in all native engines. Wiring metadata is supplied differently across APIs. |
| `Pair` fields, construction, metadata, initial state and step | `Pair`, `metadata`, `initial_state`, `step` | `NewPair` model callbacks | `Pair` | `Pair`, `model()` | Independently verify delivery removal, pending saturation, routing order, rejection/error behavior, snapshot isolation and component identities. |
| `Pair` checks and enumeration | `check_state_into`, `check_transition_into`, `inputs` | model returned by `NewPair` | `checkStateInto`, `checkTransitionInto`, `inputs` | `checkState`, `checkTransition`, `inputs` | Verify child/global check order, wrong-side outputs, lazy iterator errors, bounded queue enumeration and cancellation across independent fixtures. |
| `Pair` model codec | `pair_schemas` and stateful codec wrapper | `PairCodec` | `pairSchemas`, `withPairCodec` | `Pair.codec` | Verify matching model/codec identities, sink error propagation, malformed and oversized data, buffered output and cross-engine replay. Exact build identities can differ. |
| Module and standard trait implementations | module/dataclass behavior | package/value and supplied hooks | type/module behavior | value/conditional protocol behavior | Rust-specific traits require equivalent observable outcomes; independently establish equality, hash/copy and error contracts rather than assuming a language syntax match. |

Candidate source identities observed for this inventory (SHA-256):

- `python/src/statelessness/composition.py`: `1d7d7889dc36ce75d05491094746da5367cf8fe7c26a1241c27fed4bdf27dbd2`
- `python/src/statelessness/stateful_codec.py`: `725a26d8cc6b801708466432fecf254ffc0381f5bdc57c9b70a126ca01c641b9`
- `go/composition.go`: `fec13b5bb07e06f685c522a7fe8ea7efd0e2d8d06e4ba08f15f44ef16661d02e`
- `go/composition_codec.go`: `4e97e0e4374a0eb197bb7ede22c7c6ad1d6e67134ca385bf0963e04a4dbc8575`
- `typescript/src/composition.ts`: `b76fb301f150f0c6b1ca388813a8f52f259aa3bb9350e762d712c066cdbed5e0`
- `swift/Sources/Statelessness/Composition.swift`: `4e5955ab9bace521fc641ac298eaaa8d87279167e9be2cb95ccf092f901c1531`
- `swift/Sources/Statelessness/StatefulCodec.swift`: `6f0c28c130c83b1815cae495c2ffb61e1d26af97a1bcd496934a17ead995ea12`

This source-file map is not evidence of installed-consumer, profile, behavioral,
cross-engine or performance qualification. Independent normative cases and
review of the complete M0 ledger remain open.

## Receipt records

Each row gives the first reachable export recorded by the compiler receipt;
other recorded aliases remain in the JSONL source of truth.

| Receipt ID | Role | Kind | First reachable Rust export |
| --- | --- | --- | --- |
| `statelessness:6685` | implementation_member | function | `<stateless::composition::Address as Clone>::clone` |
| `statelessness:6686` | public_std_trait_implementation | impl | `<stateless::composition::Address as Clone>::{impl}` |
| `statelessness:6687` | implementation_member | function | `<stateless::composition::Address as Debug>::fmt` |
| `statelessness:6688` | public_std_trait_implementation | impl | `<stateless::composition::Address as Debug>::{impl}` |
| `statelessness:6692` | public_std_trait_implementation | impl | `<stateless::composition::Address as Eq>::{impl}` |
| `statelessness:6693` | implementation_member | function | `<stateless::composition::Address as Hash>::hash` |
| `statelessness:6694` | public_std_trait_implementation | impl | `<stateless::composition::Address as Hash>::{impl}` |
| `statelessness:6690` | implementation_member | function | `<stateless::composition::Address as PartialEq>::eq` |
| `statelessness:6691` | public_std_trait_implementation | impl | `<stateless::composition::Address as PartialEq>::{impl}` |
| `statelessness:6689` | public_std_trait_implementation | impl | `<stateless::composition::Address as StructuralPartialEq>::{impl}` |
| `statelessness:6697` | implementation_member | function | `<stateless::composition::Address as TraceDecode>::trace_decode` |
| `statelessness:6698` | public_local_trait_implementation | impl | `<stateless::composition::Address as TraceDecode>::{impl}` |
| `statelessness:6695` | implementation_member | function | `<stateless::composition::Address as TraceEncode>::trace_encode` |
| `statelessness:6696` | public_local_trait_implementation | impl | `<stateless::composition::Address as TraceEncode>::{impl}` |
| `statelessness:6719` | implementation_member | function | `<stateless::composition::Input as Clone>::clone` |
| `statelessness:6720` | public_std_trait_implementation | impl | `<stateless::composition::Input as Clone>::{impl}` |
| `statelessness:6721` | implementation_member | function | `<stateless::composition::Input as Debug>::fmt` |
| `statelessness:6722` | public_std_trait_implementation | impl | `<stateless::composition::Input as Debug>::{impl}` |
| `statelessness:6726` | public_std_trait_implementation | impl | `<stateless::composition::Input as Eq>::{impl}` |
| `statelessness:6727` | implementation_member | function | `<stateless::composition::Input as Hash>::hash` |
| `statelessness:6728` | public_std_trait_implementation | impl | `<stateless::composition::Input as Hash>::{impl}` |
| `statelessness:6724` | implementation_member | function | `<stateless::composition::Input as PartialEq>::eq` |
| `statelessness:6725` | public_std_trait_implementation | impl | `<stateless::composition::Input as PartialEq>::{impl}` |
| `statelessness:6723` | public_std_trait_implementation | impl | `<stateless::composition::Input as StructuralPartialEq>::{impl}` |
| `statelessness:6731` | implementation_member | function | `<stateless::composition::Input as TraceDecode>::trace_decode` |
| `statelessness:6732` | public_local_trait_implementation | impl | `<stateless::composition::Input as TraceDecode>::{impl}` |
| `statelessness:6729` | implementation_member | function | `<stateless::composition::Input as TraceEncode>::trace_encode` |
| `statelessness:6730` | public_local_trait_implementation | impl | `<stateless::composition::Input as TraceEncode>::{impl}` |
| `statelessness:4490` | implementation_member | function | `<stateless::composition::Pair as Enumerate>::inputs` |
| `statelessness:4491` | public_local_trait_implementation | impl | `<stateless::composition::Pair as Enumerate>::{impl}` |
| `statelessness:4425` | implementation_member | assoc_type | `<stateless::composition::Pair as Model>::Input` |
| `statelessness:4427` | implementation_member | assoc_type | `<stateless::composition::Pair as Model>::Output` |
| `statelessness:4423` | implementation_member | assoc_type | `<stateless::composition::Pair as Model>::State` |
| `statelessness:4432` | implementation_member | function | `<stateless::composition::Pair as Model>::check_state` |
| `statelessness:4433` | implementation_member | function | `<stateless::composition::Pair as Model>::check_state_into` |
| `statelessness:4434` | implementation_member | function | `<stateless::composition::Pair as Model>::check_transition` |
| `statelessness:4435` | implementation_member | function | `<stateless::composition::Pair as Model>::check_transition_into` |
| `statelessness:4430` | implementation_member | function | `<stateless::composition::Pair as Model>::initial_state` |
| `statelessness:4429` | implementation_member | function | `<stateless::composition::Pair as Model>::metadata` |
| `statelessness:4431` | implementation_member | function | `<stateless::composition::Pair as Model>::step` |
| `statelessness:4436` | public_local_trait_implementation | impl | `<stateless::composition::Pair as Model>::{impl}` |
| `statelessness:4479` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::decode_input` |
| `statelessness:4478` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::decode_state` |
| `statelessness:4473` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_input` |
| `statelessness:4476` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_input_into` |
| `statelessness:4474` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_output` |
| `statelessness:4477` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_output_into` |
| `statelessness:4472` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_state` |
| `statelessness:4475` | implementation_member | function | `<stateless::composition::Pair as ModelCodec>::encode_state_into` |
| `statelessness:4480` | public_local_trait_implementation | impl | `<stateless::composition::Pair as ModelCodec>::{impl}` |
| `statelessness:6752` | implementation_member | function | `<stateless::composition::State as Clone>::clone` |
| `statelessness:6753` | public_std_trait_implementation | impl | `<stateless::composition::State as Clone>::{impl}` |
| `statelessness:6754` | implementation_member | function | `<stateless::composition::State as Debug>::fmt` |
| `statelessness:6755` | public_std_trait_implementation | impl | `<stateless::composition::State as Debug>::{impl}` |
| `statelessness:6759` | public_std_trait_implementation | impl | `<stateless::composition::State as Eq>::{impl}` |
| `statelessness:6760` | implementation_member | function | `<stateless::composition::State as Hash>::hash` |
| `statelessness:6761` | public_std_trait_implementation | impl | `<stateless::composition::State as Hash>::{impl}` |
| `statelessness:6757` | implementation_member | function | `<stateless::composition::State as PartialEq>::eq` |
| `statelessness:6758` | public_std_trait_implementation | impl | `<stateless::composition::State as PartialEq>::{impl}` |
| `statelessness:6756` | public_std_trait_implementation | impl | `<stateless::composition::State as StructuralPartialEq>::{impl}` |
| `statelessness:6764` | implementation_member | function | `<stateless::composition::State as TraceDecode>::trace_decode` |
| `statelessness:6765` | public_local_trait_implementation | impl | `<stateless::composition::State as TraceDecode>::{impl}` |
| `statelessness:6762` | implementation_member | function | `<stateless::composition::State as TraceEncode>::trace_encode` |
| `statelessness:6763` | public_local_trait_implementation | impl | `<stateless::composition::State as TraceEncode>::{impl}` |
| `statelessness:6779` | named_export | module | `stateless::composition` |
| `statelessness:4428` | named_export | enum | `stateless::composition::Address` |
| `statelessness:6666` | public_member | variant | `stateless::composition::Address::Left` |
| `statelessness:6665` | public_member | struct_field | `stateless::composition::Address::Left::0` |
| `statelessness:6668` | public_member | variant | `stateless::composition::Address::Right` |
| `statelessness:6667` | public_member | struct_field | `stateless::composition::Address::Right::0` |
| `statelessness:4426` | named_export | enum | `stateless::composition::Input` |
| `statelessness:6702` | public_member | variant | `stateless::composition::Input::Deliver` |
| `statelessness:6701` | public_member | struct_field | `stateless::composition::Input::Deliver::0` |
| `statelessness:6700` | public_member | variant | `stateless::composition::Input::Local` |
| `statelessness:6699` | public_member | struct_field | `stateless::composition::Input::Local::0` |
| `statelessness:6767` | named_export | type_alias | `stateless::composition::Message` |
| `statelessness:4438` | named_export | struct | `stateless::composition::Pair` |
| `statelessness:6774` | public_member | struct_field | `stateless::composition::Pair::left` |
| `statelessness:6777` | public_member | struct_field | `stateless::composition::Pair::max_pending` |
| `statelessness:6775` | public_member | struct_field | `stateless::composition::Pair::right` |
| `statelessness:6776` | public_member | struct_field | `stateless::composition::Pair::wiring` |
| `statelessness:6780` | public_inherent_implementation | impl | `stateless::composition::Pair::{impl}` |
| `statelessness:6768` | named_export | type_alias | `stateless::composition::PairInput` |
| `statelessness:6769` | named_export | type_alias | `stateless::composition::PairOutput` |
| `statelessness:6766` | named_export | type_alias | `stateless::composition::PairState` |
| `statelessness:4424` | named_export | struct | `stateless::composition::State` |
| `statelessness:6733` | public_member | struct_field | `stateless::composition::State::left` |
| `statelessness:6735` | public_member | struct_field | `stateless::composition::State::pending` |
| `statelessness:6734` | public_member | struct_field | `stateless::composition::State::right` |
| `statelessness:4437` | named_export | trait | `stateless::composition::Wiring` |
| `statelessness:6772` | trait_member | function | `stateless::composition::Wiring::check_state_into` |
| `statelessness:6773` | trait_member | function | `stateless::composition::Wiring::check_transition_into` |
| `statelessness:6771` | trait_member | function | `stateless::composition::Wiring::messages` |
| `statelessness:6770` | trait_member | function | `stateless::composition::Wiring::metadata` |
