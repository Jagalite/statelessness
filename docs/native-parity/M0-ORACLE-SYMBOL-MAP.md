# M0 oracle symbol map: static inventory, not qualification

This accounts for all 78 compiler-receipt records in `semantic-symbols.jsonl`
whose source is `src/oracle.rs`. The receipt is pinned to commit `0876717bee85e335449c2c99ada41f6c6e6112d1`.
The pinned file SHA-256 is `8f252af5945c81b236a19caf326ab626d060702791675d918cea2ef9663e2fdf`; the current file SHA-256 is
`8f252af5945c81b236a19caf326ab626d060702791675d918cea2ef9663e2fdf`. Receipt reachability and identities describe that pinned snapshot;
cfg-specific exports and generated APIs require separate inspection. Native names
below are dirty-worktree candidates, not established semantic equivalence.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `OracleState`, model/history fields, equality, hash and copy | `OracleState`, `WithOracle` copy behavior | `OracleState`, `OracleModel` clone/equality hooks | `OracleState`, `WithOracle` | `OracleState` conditional `Equatable`/`Hashable`, model hooks | Verify identity includes both histories, cloning ownership, collision behavior and callback mutation across engines. Rust trait machinery is not itself a required native export. |
| `Oracle`, state and transition checks, `advance`, estimator | `Oracle` protocol and `WithOracle` | `Oracle` callbacks and `OracleModel` | `Oracle` interface and `WithOracle` | `Oracle` callbacks and `WithOracle` | Compare ordered append checks, disposition and outputs, callback failure/stop ordering, snapshot ownership and size estimates with independent expected results. |
| `OracleCodec` encode/decode history and buffered encoding | oracle history methods used by `WithOracle` | `Oracle.EncodeHistory`, `EncodeHistoryInto`, `DecodeHistory` | `Oracle` history codec hooks | `Oracle` history codec hooks | Verify codec identity, malformed inputs, bounded sinks, history-only differences and cross-engine replay. Hook availability and signatures differ. |
| `WithOracle` construction, model access, attachment and observation | `WithOracle` constructor, `attach`, `observe_transition` | `WithOracle`, `OracleModel.Attach`, `ObserveTransition` | `WithOracle` constructor, `attach`, `observeTransition` | `WithOracle` initializer, `attach`, `observeTransition` | Verify snapshot isolation and retained completed transition on error; compare component access and ownership semantics rather than assuming matching accessors. |
| `WithOracle` model, enumeration, generation, and codec trait implementations | `WithOracle` step/checks/inputs/generate/codec methods | `OracleModel` model hooks, `WithOracleGenerate` | `WithOracle` model/codec/generation methods | `WithOracle` model/codec/generation adapters | Verify delegation, capability absence, ordering, failure propagation and history-aware state encoding. Some native operations are optional hooks rather than statically constrained traits. |
| `ObservationError`, `ObservationResult`, standard error traits | `ObservationError` | `ObservationError` | `ObservationError` | `ObservationError` | Compare error classification and retained transition ownership; native error/result representations differ. |
| Module export and standard `Clone`, `Copy`, `Debug`, `Eq`, `Hash` implementations | module/object behavior | package/value and callbacks | module/object behavior | value/conditional protocol behavior | Rust-specific trait interfaces require equivalent outcomes where observable, not identical syntax. Independently validate the actual native contracts. |

Candidate source identities observed during this inventory:

- `python/src/statelessness/oracle.py`: `06a23411c48e2d23e628e390163018d35f6620047ddd9c27615d098608ddc8a3`
- `go/oracle.go`: `ca66c3cb5549df13dc4c854f34f7aeb9a0c136c3bfbdcfbd1f729460b7b21314`
- `typescript/src/oracle.ts`: `6fe37161a33f090f6c3e4fe69468529d88963b0182548c96b97ef153bb0fc071`
- `swift/Sources/Statelessness/Oracle.swift`: `babb4070dd136af7a31db5847aeaa8c9ce4e3dd220526120e8baf389f4495f9a`

`Paired` and `Projection` are related candidates but their Rust declarations are
outside this source-file map. The table does not assert installed-consumer,
profile, behavioral, cross-engine or performance qualification. These remain
open alongside independent normative cases and review of the complete M0 ledger.

## Receipt records

Each row links to a family above and lists the first reachable export recorded by
the compiler receipt. Other recorded aliases remain in the JSONL source of truth.

| Receipt ID | Role | Kind | First reachable Rust export |
| --- | --- | --- | --- |
| `statelessness:5532` | implementation_member | function | `<stateless::ObservationError as Debug>::fmt` |
| `statelessness:5533` | public_std_trait_implementation | impl | `<stateless::ObservationError as Debug>::{impl}` |
| `statelessness:5534` | implementation_member | function | `<stateless::ObservationError as Display>::fmt` |
| `statelessness:5535` | public_std_trait_implementation | impl | `<stateless::ObservationError as Display>::{impl}` |
| `statelessness:5536` | implementation_member | function | `<stateless::ObservationError as Error>::source` |
| `statelessness:5537` | public_std_trait_implementation | impl | `<stateless::ObservationError as Error>::{impl}` |
| `statelessness:5417` | implementation_member | function | `<stateless::OracleState as Clone>::clone` |
| `statelessness:5418` | public_std_trait_implementation | impl | `<stateless::OracleState as Clone>::{impl}` |
| `statelessness:5419` | implementation_member | function | `<stateless::OracleState as Debug>::fmt` |
| `statelessness:5420` | public_std_trait_implementation | impl | `<stateless::OracleState as Debug>::{impl}` |
| `statelessness:5424` | public_std_trait_implementation | impl | `<stateless::OracleState as Eq>::{impl}` |
| `statelessness:5425` | implementation_member | function | `<stateless::OracleState as Hash>::hash` |
| `statelessness:5426` | public_std_trait_implementation | impl | `<stateless::OracleState as Hash>::{impl}` |
| `statelessness:5422` | implementation_member | function | `<stateless::OracleState as PartialEq>::eq` |
| `statelessness:5423` | public_std_trait_implementation | impl | `<stateless::OracleState as PartialEq>::{impl}` |
| `statelessness:5421` | public_std_trait_implementation | impl | `<stateless::OracleState as StructuralPartialEq>::{impl}` |
| `statelessness:5509` | implementation_member | function | `<stateless::WithOracle as Clone>::clone` |
| `statelessness:5510` | public_std_trait_implementation | impl | `<stateless::WithOracle as Clone>::{impl}` |
| `statelessness:5511` | public_std_trait_implementation | impl | `<stateless::WithOracle as Copy>::{impl}` |
| `statelessness:5512` | implementation_member | function | `<stateless::WithOracle as Debug>::fmt` |
| `statelessness:5513` | public_std_trait_implementation | impl | `<stateless::WithOracle as Debug>::{impl}` |
| `statelessness:4486` | implementation_member | function | `<stateless::WithOracle as Enumerate>::input_iter` |
| `statelessness:4485` | implementation_member | function | `<stateless::WithOracle as Enumerate>::inputs` |
| `statelessness:4487` | public_local_trait_implementation | impl | `<stateless::WithOracle as Enumerate>::{impl}` |
| `statelessness:4495` | implementation_member | function | `<stateless::WithOracle as Generate>::generate` |
| `statelessness:4496` | implementation_member | function | `<stateless::WithOracle as Generate>::is_enabled` |
| `statelessness:4497` | implementation_member | function | `<stateless::WithOracle as Generate>::simpler_inputs` |
| `statelessness:4498` | public_local_trait_implementation | impl | `<stateless::WithOracle as Generate>::{impl}` |
| `statelessness:4402` | implementation_member | assoc_type | `<stateless::WithOracle as Model>::Input` |
| `statelessness:4403` | implementation_member | assoc_type | `<stateless::WithOracle as Model>::Output` |
| `statelessness:4399` | implementation_member | assoc_type | `<stateless::WithOracle as Model>::State` |
| `statelessness:4407` | implementation_member | function | `<stateless::WithOracle as Model>::check_state` |
| `statelessness:4408` | implementation_member | function | `<stateless::WithOracle as Model>::check_state_into` |
| `statelessness:4409` | implementation_member | function | `<stateless::WithOracle as Model>::check_transition` |
| `statelessness:4410` | implementation_member | function | `<stateless::WithOracle as Model>::check_transition_into` |
| `statelessness:4411` | implementation_member | function | `<stateless::WithOracle as Model>::estimated_state_bytes` |
| `statelessness:4405` | implementation_member | function | `<stateless::WithOracle as Model>::initial_state` |
| `statelessness:4404` | implementation_member | function | `<stateless::WithOracle as Model>::metadata` |
| `statelessness:4406` | implementation_member | function | `<stateless::WithOracle as Model>::step` |
| `statelessness:4412` | public_local_trait_implementation | impl | `<stateless::WithOracle as Model>::{impl}` |
| `statelessness:4460` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::decode_input` |
| `statelessness:4458` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::decode_state` |
| `statelessness:4459` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_input` |
| `statelessness:4462` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_input_into` |
| `statelessness:4461` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_output` |
| `statelessness:4463` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_output_into` |
| `statelessness:4456` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_state` |
| `statelessness:4457` | implementation_member | function | `<stateless::WithOracle as ModelCodec>::encode_state_into` |
| `statelessness:4464` | public_local_trait_implementation | impl | `<stateless::WithOracle as ModelCodec>::{impl}` |
| `statelessness:5516` | named_export | struct | `stateless::ObservationError` |
| `statelessness:5514` | public_member | struct_field | `stateless::ObservationError::error` |
| `statelessness:5515` | public_member | struct_field | `stateless::ObservationError::transition` |
| `statelessness:5491` | named_export | type_alias | `stateless::ObservationResult` |
| `statelessness:4401` | named_export | trait | `stateless::Oracle` |
| `statelessness:5431` | trait_member | assoc_type | `stateless::Oracle::State` |
| `statelessness:5434` | trait_member | function | `stateless::Oracle::advance` |
| `statelessness:5435` | trait_member | function | `stateless::Oracle::check_state_into` |
| `statelessness:5436` | trait_member | function | `stateless::Oracle::check_transition_into` |
| `statelessness:5437` | trait_member | function | `stateless::Oracle::estimated_state_bytes` |
| `statelessness:5433` | trait_member | function | `stateless::Oracle::initial_state` |
| `statelessness:5432` | trait_member | function | `stateless::Oracle::metadata` |
| `statelessness:4465` | named_export | trait | `stateless::OracleCodec` |
| `statelessness:5467` | trait_member | function | `stateless::OracleCodec::decode_history` |
| `statelessness:5466` | trait_member | function | `stateless::OracleCodec::encode_history` |
| `statelessness:5468` | trait_member | function | `stateless::OracleCodec::encode_history_into` |
| `statelessness:4400` | named_export | struct | `stateless::OracleState` |
| `statelessness:5399` | public_member | struct_field | `stateless::OracleState::model` |
| `statelessness:5400` | public_member | struct_field | `stateless::OracleState::oracle` |
| `statelessness:4413` | named_export | struct | `stateless::WithOracle` |
| `statelessness:5489` | implementation_member | function | `stateless::WithOracle::attach` |
| `statelessness:5487` | implementation_member | function | `stateless::WithOracle::into_parts` |
| `statelessness:5485` | implementation_member | function | `stateless::WithOracle::model` |
| `statelessness:5484` | implementation_member | function | `stateless::WithOracle::new` |
| `statelessness:5490` | implementation_member | function | `stateless::WithOracle::observe_transition` |
| `statelessness:5486` | implementation_member | function | `stateless::WithOracle::oracle` |
| `statelessness:5488` | public_inherent_implementation | impl | `stateless::WithOracle::{impl}` |
| `statelessness:5492` | public_inherent_implementation | impl | `stateless::WithOracle::{impl}` |
| `statelessness:5482` | named_export | module | `stateless::oracle` |
