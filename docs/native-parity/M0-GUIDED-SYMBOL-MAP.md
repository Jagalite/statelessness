# M0 guided exploration symbol map: static inventory, not qualification

This accounts for all 85 compiler-receipt records in `semantic-symbols.jsonl`
whose source is `src/guided.rs`, pinned to commit `0876717bee85e335449c2c99ada41f6c6e6112d1`.
The pinned file SHA-256 is `bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7`; the current worktree file SHA-256 is
`bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7`. Receipt reachability and exports describe the pinned snapshot.
Cfg-specific exports, generated APIs and alternate aliases need separate inspection.
Native APIs below are candidates in the current dirty worktree; they are not
claims of semantic equivalence, installed execution, or qualification.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `ALGORITHM`, feedback identity and `FeedbackMetadata` | `ALGORITHM`, `FeedbackMetadata` | `GuidanceAlgorithm`, `FeedbackMetadata` | `GUIDANCE_ALGORITHM`, `FeedbackMetadata` | `guidedAlgorithm`, `FeedbackMetadata` | Independently verify version, build identity, incompatibility behavior and report serialization; matching identifier strings do not establish matching policy. |
| `FeatureBuffer::push` and `Feedback` methods | `FeatureBuffer`, `Feedback` protocol | `FeatureBuffer`, `Feedback`, `FeatureKey` | `FeatureBuffer`, `Feedback`, `FeatureIdentity` | `FeatureBuffer`, `Feedback` | Compare feature equality and hashing, duplicate/collision behavior, limits, retained-buffer lifetime and callback failures; native key APIs differ. |
| `StateFeedback::new`, `Feedback` implementation and `exact_state` | `StateFeedback`, `exact_state` | `StateFeedback`, `ExactStateFeedback` | `StateFeedback`, `exactStateFeedback` | `StateFeedback`, `exactStateFeedback` | Verify initial and transition projections, cloning and feature identity against independent expected results, including mutable states. |
| `GuidanceConfig`, validation and default | `GuidanceConfig` | `GuidanceConfig64`, `DefaultGuidanceConfig64` | `GuidanceConfig`, `validateGuidanceConfig` | `GuidanceConfig64` | Compare defaults, integer bounds, invalid values and capacity interactions; typed widths and constructors differ. |
| `CorpusEntry` and `GuidanceReport` fields and standard traits | `CorpusEntry`, `GuidanceReport` | `CorpusEntry64`, `GuidanceReport64` | `CorpusEntry`, `GuidanceReport` | `CorpusEntry64`, `GuidanceReport64` | Verify ordering, ownership, exact counts and overflow, dropped/evicted prefix semantics and cross-engine serialization. Rust standard trait machinery requires equivalent observable outcomes rather than identical APIs. |
| `fuzz`, `fuzz_with_limits`, `fuzz_observed` | `guided_fuzz` | `GuidedFuzz64`, `GuidedFuzzWithLimits64`, `GuidedFuzzObserved64` | `fuzzGuided`, `fuzzGuidedObserved` | `guidedFuzz64` overloads | Verify admission, eviction, mutation scheduling, RNG consumption, cancellation, observer errors, replay and incomplete runs with independent vectors and adversarial callbacks. Signatures and error representations differ. |
| `guided` module | `statelessness.guided` | package `statelessness` | guided module | Statelessness module | Confirm public exports and optional dependencies from clean installed packages on every claimed platform. |

Candidate source identities observed during this inventory:

- `python/src/statelessness/guided.py`: `f43500fe8438ad130137f47595b28b5ed35eaccc8aacf2fb1988e50be17a3c47`
- `go/guided.go`: `91de967a3c037f894e0a274c4da8a3df68ed7d6ff2bfa0de1a528e9d1be530ea`
- `typescript/src/guided.ts`: `be6a579ba6e6d38ef8e324b76cb096ed5593d313c55770a84a7a9dea8c3ee2f4`
- `swift/Sources/Statelessness/Guided.swift`: `488aac0a816a33b1d0bb4999c222aeedd626e9b926d734f5f2d5d79054f4a68b`

Related campaign integration and fuzz driver internals span other source files and
are not exhaustively mapped here. This file records source candidates, not the
independent normative, mutation-control, retained-witness, performance, or platform
evidence required to qualify N05. M0 remains open for the rest of the baseline.

## Receipt records

Each row is a pinned compiler-receipt record; its first reachable export identifies
the family above. Other reachable aliases remain in the JSONL source of truth.

| Receipt ID | Role | Kind | First reachable Rust export |
| --- | --- | --- | --- |
| `statelessness:3980` | implementation_member | function | `<stateless::guided::CorpusEntry as Clone>::clone` |
| `statelessness:3981` | public_std_trait_implementation | impl | `<stateless::guided::CorpusEntry as Clone>::{impl}` |
| `statelessness:3982` | implementation_member | function | `<stateless::guided::CorpusEntry as Debug>::fmt` |
| `statelessness:3983` | public_std_trait_implementation | impl | `<stateless::guided::CorpusEntry as Debug>::{impl}` |
| `statelessness:3987` | public_std_trait_implementation | impl | `<stateless::guided::CorpusEntry as Eq>::{impl}` |
| `statelessness:3985` | implementation_member | function | `<stateless::guided::CorpusEntry as PartialEq>::eq` |
| `statelessness:3986` | public_std_trait_implementation | impl | `<stateless::guided::CorpusEntry as PartialEq>::{impl}` |
| `statelessness:3984` | public_std_trait_implementation | impl | `<stateless::guided::CorpusEntry as StructuralPartialEq>::{impl}` |
| `statelessness:3868` | implementation_member | function | `<stateless::guided::FeedbackMetadata as Clone>::clone` |
| `statelessness:3869` | public_std_trait_implementation | impl | `<stateless::guided::FeedbackMetadata as Clone>::{impl}` |
| `statelessness:3870` | implementation_member | function | `<stateless::guided::FeedbackMetadata as Debug>::fmt` |
| `statelessness:3871` | public_std_trait_implementation | impl | `<stateless::guided::FeedbackMetadata as Debug>::{impl}` |
| `statelessness:3875` | public_std_trait_implementation | impl | `<stateless::guided::FeedbackMetadata as Eq>::{impl}` |
| `statelessness:3873` | implementation_member | function | `<stateless::guided::FeedbackMetadata as PartialEq>::eq` |
| `statelessness:3874` | public_std_trait_implementation | impl | `<stateless::guided::FeedbackMetadata as PartialEq>::{impl}` |
| `statelessness:3872` | public_std_trait_implementation | impl | `<stateless::guided::FeedbackMetadata as StructuralPartialEq>::{impl}` |
| `statelessness:3948` | implementation_member | function | `<stateless::guided::GuidanceConfig as Clone>::clone` |
| `statelessness:3949` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as Clone>::{impl}` |
| `statelessness:3950` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as Copy>::{impl}` |
| `statelessness:3951` | implementation_member | function | `<stateless::guided::GuidanceConfig as Debug>::fmt` |
| `statelessness:3952` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as Debug>::{impl}` |
| `statelessness:3957` | implementation_member | function | `<stateless::guided::GuidanceConfig as Default>::default` |
| `statelessness:3958` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as Default>::{impl}` |
| `statelessness:3956` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as Eq>::{impl}` |
| `statelessness:3954` | implementation_member | function | `<stateless::guided::GuidanceConfig as PartialEq>::eq` |
| `statelessness:3955` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as PartialEq>::{impl}` |
| `statelessness:3953` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceConfig as StructuralPartialEq>::{impl}` |
| `statelessness:4015` | implementation_member | function | `<stateless::guided::GuidanceReport as Clone>::clone` |
| `statelessness:4016` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceReport as Clone>::{impl}` |
| `statelessness:4017` | implementation_member | function | `<stateless::guided::GuidanceReport as Debug>::fmt` |
| `statelessness:4018` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceReport as Debug>::{impl}` |
| `statelessness:4022` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceReport as Eq>::{impl}` |
| `statelessness:4020` | implementation_member | function | `<stateless::guided::GuidanceReport as PartialEq>::eq` |
| `statelessness:4021` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceReport as PartialEq>::{impl}` |
| `statelessness:4019` | public_std_trait_implementation | impl | `<stateless::guided::GuidanceReport as StructuralPartialEq>::{impl}` |
| `statelessness:3901` | implementation_member | assoc_type | `<stateless::guided::StateFeedback as Feedback>::Feature` |
| `statelessness:3903` | implementation_member | function | `<stateless::guided::StateFeedback as Feedback>::initial_features` |
| `statelessness:3902` | implementation_member | function | `<stateless::guided::StateFeedback as Feedback>::metadata` |
| `statelessness:3904` | implementation_member | function | `<stateless::guided::StateFeedback as Feedback>::transition_features` |
| `statelessness:3905` | public_local_trait_implementation | impl | `<stateless::guided::StateFeedback as Feedback>::{impl}` |
| `statelessness:3877` | named_export | module | `stateless::guided` |
| `statelessness:3847` | named_export | constant | `stateless::guided::ALGORITHM` |
| `statelessness:3963` | named_export | struct | `stateless::guided::CorpusEntry` |
| `statelessness:3961` | public_member | struct_field | `stateless::guided::CorpusEntry::discovered_case` |
| `statelessness:3962` | public_member | struct_field | `stateless::guided::CorpusEntry::discovered_transition` |
| `statelessness:3959` | public_member | struct_field | `stateless::guided::CorpusEntry::inputs` |
| `statelessness:3960` | public_member | struct_field | `stateless::guided::CorpusEntry::new_features` |
| `statelessness:3880` | named_export | struct | `stateless::guided::FeatureBuffer` |
| `statelessness:3881` | implementation_member | function | `stateless::guided::FeatureBuffer::push` |
| `statelessness:3882` | public_inherent_implementation | impl | `stateless::guided::FeatureBuffer::{impl}` |
| `statelessness:801` | named_export | trait | `stateless::guided::Feedback` |
| `statelessness:3897` | trait_member | assoc_type | `stateless::guided::Feedback::Feature` |
| `statelessness:3899` | trait_member | function | `stateless::guided::Feedback::initial_features` |
| `statelessness:3898` | trait_member | function | `stateless::guided::Feedback::metadata` |
| `statelessness:3900` | trait_member | function | `stateless::guided::Feedback::transition_features` |
| `statelessness:3851` | named_export | struct | `stateless::guided::FeedbackMetadata` |
| `statelessness:3850` | public_member | struct_field | `stateless::guided::FeedbackMetadata::build` |
| `statelessness:3848` | public_member | struct_field | `stateless::guided::FeedbackMetadata::name` |
| `statelessness:3849` | public_member | struct_field | `stateless::guided::FeedbackMetadata::version` |
| `statelessness:800` | named_export | struct | `stateless::guided::GuidanceConfig` |
| `statelessness:3926` | public_member | struct_field | `stateless::guided::GuidanceConfig::max_corpus_entries` |
| `statelessness:3927` | public_member | struct_field | `stateless::guided::GuidanceConfig::max_corpus_inputs` |
| `statelessness:3928` | public_member | struct_field | `stateless::guided::GuidanceConfig::max_features` |
| `statelessness:3929` | public_member | struct_field | `stateless::guided::GuidanceConfig::max_features_per_observation` |
| `statelessness:3930` | implementation_member | function | `stateless::guided::GuidanceConfig::validate` |
| `statelessness:3931` | public_inherent_implementation | impl | `stateless::guided::GuidanceConfig::{impl}` |
| `statelessness:2883` | named_export | struct | `stateless::guided::GuidanceReport` |
| `statelessness:3988` | public_member | struct_field | `stateless::guided::GuidanceReport::algorithm` |
| `statelessness:3989` | public_member | struct_field | `stateless::guided::GuidanceReport::config` |
| `statelessness:3993` | public_member | struct_field | `stateless::guided::GuidanceReport::corpus` |
| `statelessness:3992` | public_member | struct_field | `stateless::guided::GuidanceReport::corpus_inputs` |
| `statelessness:3998` | public_member | struct_field | `stateless::guided::GuidanceReport::corpus_limit_reached` |
| `statelessness:3995` | public_member | struct_field | `stateless::guided::GuidanceReport::dropped_prefixes` |
| `statelessness:3996` | public_member | struct_field | `stateless::guided::GuidanceReport::evicted_prefixes` |
| `statelessness:3997` | public_member | struct_field | `stateless::guided::GuidanceReport::feature_limit_reached` |
| `statelessness:3990` | public_member | struct_field | `stateless::guided::GuidanceReport::feedback` |
| `statelessness:3994` | public_member | struct_field | `stateless::guided::GuidanceReport::mutation_cases` |
| `statelessness:3991` | public_member | struct_field | `stateless::guided::GuidanceReport::unique_features` |
| `statelessness:3906` | named_export | struct | `stateless::guided::StateFeedback` |
| `statelessness:3909` | implementation_member | function | `stateless::guided::StateFeedback::new` |
| `statelessness:3910` | public_inherent_implementation | impl | `stateless::guided::StateFeedback::{impl}` |
| `statelessness:3925` | named_export | function | `stateless::guided::exact_state` |
| `statelessness:4023` | named_export | function | `stateless::guided::fuzz` |
| `statelessness:4025` | named_export | function | `stateless::guided::fuzz_observed` |
| `statelessness:4024` | named_export | function | `stateless::guided::fuzz_with_limits` |
