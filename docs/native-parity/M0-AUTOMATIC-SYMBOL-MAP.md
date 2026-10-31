# M0 automatic generation symbol map: static inventory, not qualification

This accounts for all 54 compiler-receipt records in `semantic-symbols.jsonl`
whose source is `src/automatic.rs`, pinned to commit `0876717bee85e335449c2c99ada41f6c6e6112d1`.
The pinned file SHA-256 is `c1e2e2cfaade485a0c4a71d24f22368cb6564bdbeb5f7ab2241de9bffadf556f`; the current worktree file SHA-256 is
`c1e2e2cfaade485a0c4a71d24f22368cb6564bdbeb5f7ab2241de9bffadf556f`. Receipt exports describe the pinned snapshot; alternate aliases,
cfg configurations and generated APIs need separate review. Native candidates
refer to the current dirty worktree and are not evidence of semantic equivalence,
installed execution or qualification.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `AutoOptions`, defaults and candidate bound | `AutoOptions` | `AutoOptions64`, `DefaultAutoOptions64`; `AutoOptions` compatibility | `AutoOptions`, `DEFAULT_AUTO` | `AutoOptions64`; `AutoOptions` compatibility | Verify defaults, integer domain, invalid options, max-plus-one pull and error precedence across independent vectors. Native count widths and error types differ. |
| `Auto` construction and model access | `Auto` | `Auto64`; `Auto` compatibility | `automatic` | `Auto64`; `Auto` compatibility | Confirm wrapper ownership, returned model identity and codec delegation. APIs need equivalent outcomes, not Rust-specific wrapper shape. |
| `sample_enumerated`, `Generate::generate` | `Auto.generate` | `Auto64` generated model | `sampleEnumerated`, `automatic` | `sampleEnumerated64`, `Auto64.generated` | Independently verify reservoir order, RNG consumption and rollback, duplicates, empty domains, over-limit iterators, callback failures and cancellation boundaries. |
| `contains_enumerated`, `Generate::is_enabled` | `Auto.is_enabled` | `Auto64` generated model | `containsEnumerated`, `automatic` | `containsEnumerated64`, `Auto64.generated` | Compare first-match semantics, equality and clone hooks, lazy domain errors, oversized suffixes, and mutation isolation under independently authored cases. |
| `Auto` delegated `Model`, `Enumerate`, `ModelCodec` implementations | `Auto.__getattr__` and wrapped model | `Auto64` embedded `Model` | `automatic` wrapped model | `Auto64.model` and `generated.model` | Verify every forwarded state, transition, check and codec operation, especially append-only checks, byte sinks, errors and callback ownership; exposed APIs differ. |
| Standard trait implementations and `automatic` module | Python object and module `statelessness.search` | package `statelessness` | search module | Statelessness module | Review observable value/identity and clean-installed exports; Rust standard trait mechanisms need equivalent outcomes rather than identical interfaces. |

Candidate source identities observed during inventory:

- `python/src/statelessness/search.py`: `ce8c0a8f8345bbcde1ba38e7d3ef14f57e0fc7e4bc13873559db15178f9be269`
- `go/generate.go`: `9ddf9e6dafff75b9d29b0912c82df9e205e31727216ed38f3a70a3671fb4098b`
- `go/counts_compat.go`: `baee60b950249aee1a50cf0eeaa7da2162bd12e5d830da85f201efdb31501e91`
- `typescript/src/search.ts`: `bd5785b21797d617a9398b4b548a95dc9d16294905a7ead74bc399b5101fe0e1`
- `swift/Sources/Statelessness/Generate.swift`: `98df06bee323fde9bf437887c4b54f4fe32d1f94f60cd286a52c1152acaeaafe`
- `swift/Sources/Statelessness/CountCompatibility.swift`: `293f417e688ac4e1a1e65f76130c98bc77d8717728aa01a422f46156d44f38a8`

This mapping is source inventory. Cross-engine exact RNG vectors, hostile iterator
and clone controls, installed consumers, performance and platform receipts remain
necessary for N03 qualification. M0 remains open for other source modules and
semantic export resolution.

## Receipt records

Each row is a pinned compiler-receipt record; its first reachable export identifies
the family above. Other reachable aliases remain in the JSONL source of truth.

| Receipt ID | Role | Kind | First reachable Rust export |
| --- | --- | --- | --- |
| `statelessness:920` | implementation_member | function | `<stateless::automatic::Auto as Clone>::clone` |
| `statelessness:921` | public_std_trait_implementation | impl | `<stateless::automatic::Auto as Clone>::{impl}` |
| `statelessness:922` | implementation_member | function | `<stateless::automatic::Auto as Debug>::fmt` |
| `statelessness:923` | public_std_trait_implementation | impl | `<stateless::automatic::Auto as Debug>::{impl}` |
| `statelessness:950` | implementation_member | function | `<stateless::automatic::Auto as Enumerate>::input_iter` |
| `statelessness:949` | implementation_member | function | `<stateless::automatic::Auto as Enumerate>::inputs` |
| `statelessness:953` | public_local_trait_implementation | impl | `<stateless::automatic::Auto as Enumerate>::{impl}` |
| `statelessness:954` | implementation_member | function | `<stateless::automatic::Auto as Generate>::generate` |
| `statelessness:956` | implementation_member | function | `<stateless::automatic::Auto as Generate>::is_enabled` |
| `statelessness:957` | public_local_trait_implementation | impl | `<stateless::automatic::Auto as Generate>::{impl}` |
| `statelessness:925` | implementation_member | assoc_type | `<stateless::automatic::Auto as Model>::Input` |
| `statelessness:926` | implementation_member | assoc_type | `<stateless::automatic::Auto as Model>::Output` |
| `statelessness:924` | implementation_member | assoc_type | `<stateless::automatic::Auto as Model>::State` |
| `statelessness:931` | implementation_member | function | `<stateless::automatic::Auto as Model>::check_state` |
| `statelessness:932` | implementation_member | function | `<stateless::automatic::Auto as Model>::check_state_into` |
| `statelessness:934` | implementation_member | function | `<stateless::automatic::Auto as Model>::check_transition` |
| `statelessness:935` | implementation_member | function | `<stateless::automatic::Auto as Model>::check_transition_into` |
| `statelessness:936` | implementation_member | function | `<stateless::automatic::Auto as Model>::estimated_state_bytes` |
| `statelessness:928` | implementation_member | function | `<stateless::automatic::Auto as Model>::initial_state` |
| `statelessness:927` | implementation_member | function | `<stateless::automatic::Auto as Model>::metadata` |
| `statelessness:929` | implementation_member | function | `<stateless::automatic::Auto as Model>::step` |
| `statelessness:937` | public_local_trait_implementation | impl | `<stateless::automatic::Auto as Model>::{impl}` |
| `statelessness:941` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::decode_input` |
| `statelessness:939` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::decode_state` |
| `statelessness:940` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_input` |
| `statelessness:945` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_input_into` |
| `statelessness:942` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_output` |
| `statelessness:946` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_output_into` |
| `statelessness:938` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_state` |
| `statelessness:943` | implementation_member | function | `<stateless::automatic::Auto as ModelCodec>::encode_state_into` |
| `statelessness:947` | public_local_trait_implementation | impl | `<stateless::automatic::Auto as ModelCodec>::{impl}` |
| `statelessness:883` | implementation_member | function | `<stateless::automatic::AutoOptions as Clone>::clone` |
| `statelessness:884` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as Clone>::{impl}` |
| `statelessness:885` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as Copy>::{impl}` |
| `statelessness:886` | implementation_member | function | `<stateless::automatic::AutoOptions as Debug>::fmt` |
| `statelessness:887` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as Debug>::{impl}` |
| `statelessness:892` | implementation_member | function | `<stateless::automatic::AutoOptions as Default>::default` |
| `statelessness:893` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as Default>::{impl}` |
| `statelessness:891` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as Eq>::{impl}` |
| `statelessness:889` | implementation_member | function | `<stateless::automatic::AutoOptions as PartialEq>::eq` |
| `statelessness:890` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as PartialEq>::{impl}` |
| `statelessness:888` | public_std_trait_implementation | impl | `<stateless::automatic::AutoOptions as StructuralPartialEq>::{impl}` |
| `statelessness:895` | named_export | module | `stateless::automatic` |
| `statelessness:897` | named_export | struct | `stateless::automatic::Auto` |
| `statelessness:901` | implementation_member | function | `stateless::automatic::Auto::into_inner` |
| `statelessness:900` | implementation_member | function | `stateless::automatic::Auto::model` |
| `statelessness:898` | implementation_member | function | `stateless::automatic::Auto::new` |
| `statelessness:902` | implementation_member | function | `stateless::automatic::Auto::options` |
| `statelessness:899` | implementation_member | function | `stateless::automatic::Auto::with_options` |
| `statelessness:903` | public_inherent_implementation | impl | `stateless::automatic::Auto::{impl}` |
| `statelessness:866` | named_export | struct | `stateless::automatic::AutoOptions` |
| `statelessness:865` | public_member | struct_field | `stateless::automatic::AutoOptions::max_candidates` |
| `statelessness:959` | named_export | function | `stateless::automatic::contains_enumerated` |
| `statelessness:958` | named_export | function | `stateless::automatic::sample_enumerated` |
