# M0 research symbol map: static inventory, not qualification

This maps the 120 compiler-receipt records sourced from `src/research.rs` in
`semantic-symbols.jsonl`. IDs and reachable exports belong to that existing
rustdoc snapshot, not a stable API identifier. The baseline is commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`; `src/research.rs` matches its
baseline content, SHA-256 `c3e436ae05e7b7a08279af9447679b021a893a33196e844f432962bb13648bb7`.
This is source evidence of candidates, not a behavior review or qualified cell.
Native files are dirty and their hashes identify the inspected worktree only.
Other cfg targets, reexports whose source is elsewhere, external trait members,
and Rust symbols without this source location are outside this map.

## Candidate families and unresolved obligations

| Rust family | Python | Go | TypeScript | Swift | Remaining work |
| --- | --- | --- | --- | --- | --- |
| `Assumptions`, `Proposal`, `ExperimentConfig`, `ExperimentStatus` | `research.Assumptions`, `Proposal`, `ExperimentConfig`, `ExperimentStatus` | `ResearchAssumptions`, `ResearchProposal`, `ResearchConfig`, `ExperimentStatus` | `Assumptions`, `Proposal`, `ExperimentConfig`, `ExperimentStatus` | `Research.Assumptions`, `Proposal`, `ExperimentConfig`, `ExperimentStatus` | Review validation, defaults, bounds, and status transitions; native comparison limits add decisions not present in this Rust config. |
| `ViolationWitness`, `Reachability`, `EquivalenceCounterexample`, `ExperimentReport` | Same named classes in `research.py` | Same names except `ResearchReachability` | Same named interfaces in `research.ts` | Same names nested in `Research` | Independently compare all fields, ordering, shortest witnesses, skipped checks, and partial reports; types and mutability differ. |
| `compare_equivalence`, `qualification_parameters` | `compare_equivalence`, `qualification_parameters` | `CompareEquivalence`, `QualificationParameters` | `compareEquivalence`, `qualificationParameters` | `Research.compareEquivalence`, `Research.qualificationParameters` | Run shared independent finite-graph, incomplete, and unsound-projection controls; verify callback errors and canonical qualification parameters. No production pruning is implied. |
| Standard trait implementations and members | Python object/data-model behavior | Go values and explicit APIs | TypeScript object/interface behavior | Swift value types and conformance | Rust traits have no automatic one-to-one counterpart. Verify observable cloning, equality, formatting and default outcomes as applicable; do not treat trait names as a native API mapping. |

Every row below resolves only to the relevant candidate family above. Fields and
variants are listed individually to make omissions visible; snake_case and native
camelCase names are not evidence that their behavior matches. `Default` is an
observable contract, while implementation-member `clone`, `eq`, and `fmt` rows
require outcomes rather than reproducing Rust language machinery. Research
property identity uses separate assurance APIs; map those exports separately.

## Receipt records

| Receipt ID | Role | Kind | Rust reachable export |
| --- | --- | --- | --- |
| `statelessness:6055` | implementation_member | function | `<stateless::research::Assumptions as Clone>::clone` |
| `statelessness:6056` | public_std_trait_implementation | impl | `<stateless::research::Assumptions as Clone>::{impl}` |
| `statelessness:6057` | implementation_member | function | `<stateless::research::Assumptions as Debug>::fmt` |
| `statelessness:6058` | public_std_trait_implementation | impl | `<stateless::research::Assumptions as Debug>::{impl}` |
| `statelessness:6062` | public_std_trait_implementation | impl | `<stateless::research::Assumptions as Eq>::{impl}` |
| `statelessness:6060` | implementation_member | function | `<stateless::research::Assumptions as PartialEq>::eq` |
| `statelessness:6061` | public_std_trait_implementation | impl | `<stateless::research::Assumptions as PartialEq>::{impl}` |
| `statelessness:6059` | public_std_trait_implementation | impl | `<stateless::research::Assumptions as StructuralPartialEq>::{impl}` |
| `statelessness:6232` | implementation_member | function | `<stateless::research::EquivalenceCounterexample as Clone>::clone` |
| `statelessness:6233` | public_std_trait_implementation | impl | `<stateless::research::EquivalenceCounterexample as Clone>::{impl}` |
| `statelessness:6234` | implementation_member | function | `<stateless::research::EquivalenceCounterexample as Debug>::fmt` |
| `statelessness:6235` | public_std_trait_implementation | impl | `<stateless::research::EquivalenceCounterexample as Debug>::{impl}` |
| `statelessness:6239` | public_std_trait_implementation | impl | `<stateless::research::EquivalenceCounterexample as Eq>::{impl}` |
| `statelessness:6237` | implementation_member | function | `<stateless::research::EquivalenceCounterexample as PartialEq>::eq` |
| `statelessness:6238` | public_std_trait_implementation | impl | `<stateless::research::EquivalenceCounterexample as PartialEq>::{impl}` |
| `statelessness:6236` | public_std_trait_implementation | impl | `<stateless::research::EquivalenceCounterexample as StructuralPartialEq>::{impl}` |
| `statelessness:6114` | implementation_member | function | `<stateless::research::ExperimentConfig as Clone>::clone` |
| `statelessness:6115` | public_std_trait_implementation | impl | `<stateless::research::ExperimentConfig as Clone>::{impl}` |
| `statelessness:6116` | public_std_trait_implementation | impl | `<stateless::research::ExperimentConfig as Copy>::{impl}` |
| `statelessness:6117` | implementation_member | function | `<stateless::research::ExperimentConfig as Debug>::fmt` |
| `statelessness:6118` | public_std_trait_implementation | impl | `<stateless::research::ExperimentConfig as Debug>::{impl}` |
| `statelessness:6119` | implementation_member | function | `<stateless::research::ExperimentConfig as Default>::default` |
| `statelessness:6120` | public_std_trait_implementation | impl | `<stateless::research::ExperimentConfig as Default>::{impl}` |
| `statelessness:6270` | implementation_member | function | `<stateless::research::ExperimentReport as Clone>::clone` |
| `statelessness:6271` | public_std_trait_implementation | impl | `<stateless::research::ExperimentReport as Clone>::{impl}` |
| `statelessness:6272` | implementation_member | function | `<stateless::research::ExperimentReport as Debug>::fmt` |
| `statelessness:6273` | public_std_trait_implementation | impl | `<stateless::research::ExperimentReport as Debug>::{impl}` |
| `statelessness:6143` | implementation_member | function | `<stateless::research::ExperimentStatus as Clone>::clone` |
| `statelessness:6144` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as Clone>::{impl}` |
| `statelessness:6145` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as Copy>::{impl}` |
| `statelessness:6146` | implementation_member | function | `<stateless::research::ExperimentStatus as Debug>::fmt` |
| `statelessness:6147` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as Debug>::{impl}` |
| `statelessness:6151` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as Eq>::{impl}` |
| `statelessness:6149` | implementation_member | function | `<stateless::research::ExperimentStatus as PartialEq>::eq` |
| `statelessness:6150` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as PartialEq>::{impl}` |
| `statelessness:6148` | public_std_trait_implementation | impl | `<stateless::research::ExperimentStatus as StructuralPartialEq>::{impl}` |
| `statelessness:6085` | implementation_member | function | `<stateless::research::Proposal as Clone>::clone` |
| `statelessness:6086` | public_std_trait_implementation | impl | `<stateless::research::Proposal as Clone>::{impl}` |
| `statelessness:6087` | implementation_member | function | `<stateless::research::Proposal as Debug>::fmt` |
| `statelessness:6088` | public_std_trait_implementation | impl | `<stateless::research::Proposal as Debug>::{impl}` |
| `statelessness:6092` | public_std_trait_implementation | impl | `<stateless::research::Proposal as Eq>::{impl}` |
| `statelessness:6090` | implementation_member | function | `<stateless::research::Proposal as PartialEq>::eq` |
| `statelessness:6091` | public_std_trait_implementation | impl | `<stateless::research::Proposal as PartialEq>::{impl}` |
| `statelessness:6089` | public_std_trait_implementation | impl | `<stateless::research::Proposal as StructuralPartialEq>::{impl}` |
| `statelessness:6202` | implementation_member | function | `<stateless::research::Reachability as Clone>::clone` |
| `statelessness:6203` | public_std_trait_implementation | impl | `<stateless::research::Reachability as Clone>::{impl}` |
| `statelessness:6204` | implementation_member | function | `<stateless::research::Reachability as Debug>::fmt` |
| `statelessness:6205` | public_std_trait_implementation | impl | `<stateless::research::Reachability as Debug>::{impl}` |
| `statelessness:6209` | public_std_trait_implementation | impl | `<stateless::research::Reachability as Eq>::{impl}` |
| `statelessness:6207` | implementation_member | function | `<stateless::research::Reachability as PartialEq>::eq` |
| `statelessness:6208` | public_std_trait_implementation | impl | `<stateless::research::Reachability as PartialEq>::{impl}` |
| `statelessness:6206` | public_std_trait_implementation | impl | `<stateless::research::Reachability as StructuralPartialEq>::{impl}` |
| `statelessness:6172` | implementation_member | function | `<stateless::research::ViolationWitness as Clone>::clone` |
| `statelessness:6173` | public_std_trait_implementation | impl | `<stateless::research::ViolationWitness as Clone>::{impl}` |
| `statelessness:6174` | implementation_member | function | `<stateless::research::ViolationWitness as Debug>::fmt` |
| `statelessness:6175` | public_std_trait_implementation | impl | `<stateless::research::ViolationWitness as Debug>::{impl}` |
| `statelessness:6179` | public_std_trait_implementation | impl | `<stateless::research::ViolationWitness as Eq>::{impl}` |
| `statelessness:6177` | implementation_member | function | `<stateless::research::ViolationWitness as PartialEq>::eq` |
| `statelessness:6178` | public_std_trait_implementation | impl | `<stateless::research::ViolationWitness as PartialEq>::{impl}` |
| `statelessness:6176` | public_std_trait_implementation | impl | `<stateless::research::ViolationWitness as StructuralPartialEq>::{impl}` |
| `statelessness:6037` | named_export | module | `stateless::research` |
| `statelessness:6035` | named_export | struct | `stateless::research::Assumptions` |
| `statelessness:6030` | public_member | struct_field | `stateless::research::Assumptions::effects` |
| `statelessness:6029` | public_member | struct_field | `stateless::research::Assumptions::equivalence` |
| `statelessness:6027` | public_member | struct_field | `stateless::research::Assumptions::established` |
| `statelessness:6028` | public_member | struct_field | `stateless::research::Assumptions::finite_domain` |
| `statelessness:6033` | public_member | struct_field | `stateless::research::Assumptions::oracle_history` |
| `statelessness:6032` | public_member | struct_field | `stateless::research::Assumptions::queue_order` |
| `statelessness:6031` | public_member | struct_field | `stateless::research::Assumptions::transition_properties` |
| `statelessness:6034` | public_member | struct_field | `stateless::research::Assumptions::visible_timing` |
| `statelessness:6038` | public_inherent_implementation | impl | `stateless::research::Assumptions::{impl}` |
| `statelessness:6215` | named_export | struct | `stateless::research::EquivalenceCounterexample` |
| `statelessness:6212` | public_member | struct_field | `stateless::research::EquivalenceCounterexample::left_prefix` |
| `statelessness:6210` | public_member | struct_field | `stateless::research::EquivalenceCounterexample::left_state` |
| `statelessness:6214` | public_member | struct_field | `stateless::research::EquivalenceCounterexample::reason` |
| `statelessness:6213` | public_member | struct_field | `stateless::research::EquivalenceCounterexample::right_prefix` |
| `statelessness:6211` | public_member | struct_field | `stateless::research::EquivalenceCounterexample::right_state` |
| `statelessness:6097` | named_export | struct | `stateless::research::ExperimentConfig` |
| `statelessness:6093` | public_member | struct_field | `stateless::research::ExperimentConfig::enabled` |
| `statelessness:6095` | public_member | struct_field | `stateless::research::ExperimentConfig::max_edges` |
| `statelessness:6096` | public_member | struct_field | `stateless::research::ExperimentConfig::max_equivalence_pairs` |
| `statelessness:6094` | public_member | struct_field | `stateless::research::ExperimentConfig::max_states` |
| `statelessness:6253` | named_export | struct | `stateless::research::ExperimentReport` |
| `statelessness:6246` | public_member | struct_field | `stateless::research::ExperimentReport::compared_pairs` |
| `statelessness:6242` | public_member | struct_field | `stateless::research::ExperimentReport::config` |
| `statelessness:6249` | public_member | struct_field | `stateless::research::ExperimentReport::counterexamples` |
| `statelessness:6244` | public_member | struct_field | `stateless::research::ExperimentReport::declared_states` |
| `statelessness:6247` | public_member | struct_field | `stateless::research::ExperimentReport::exact` |
| `statelessness:6252` | public_member | struct_field | `stateless::research::ExperimentReport::limitation` |
| `statelessness:6245` | public_member | struct_field | `stateless::research::ExperimentReport::materialized_edges` |
| `statelessness:6251` | public_member | struct_field | `stateless::research::ExperimentReport::missing_properties` |
| `statelessness:6250` | public_member | struct_field | `stateless::research::ExperimentReport::missing_violations` |
| `statelessness:6240` | public_member | struct_field | `stateless::research::ExperimentReport::model` |
| `statelessness:6241` | public_member | struct_field | `stateless::research::ExperimentReport::proposal` |
| `statelessness:6248` | public_member | struct_field | `stateless::research::ExperimentReport::reduced` |
| `statelessness:6243` | public_member | struct_field | `stateless::research::ExperimentReport::status` |
| `statelessness:6126` | named_export | enum | `stateless::research::ExperimentStatus` |
| `statelessness:6125` | public_member | variant | `stateless::research::ExperimentStatus::AgreesWithinDeclaredFixture` |
| `statelessness:6122` | public_member | variant | `stateless::research::ExperimentStatus::AssumptionsNotEstablished` |
| `statelessness:6124` | public_member | variant | `stateless::research::ExperimentStatus::Counterexample` |
| `statelessness:6121` | public_member | variant | `stateless::research::ExperimentStatus::Disabled` |
| `statelessness:6123` | public_member | variant | `stateless::research::ExperimentStatus::Incomplete` |
| `statelessness:6068` | named_export | struct | `stateless::research::Proposal` |
| `statelessness:6066` | public_member | struct_field | `stateless::research::Proposal::assumptions` |
| `statelessness:6065` | public_member | struct_field | `stateless::research::Proposal::build` |
| `statelessness:6063` | public_member | struct_field | `stateless::research::Proposal::name` |
| `statelessness:6067` | public_member | struct_field | `stateless::research::Proposal::required_properties` |
| `statelessness:6064` | public_member | struct_field | `stateless::research::Proposal::version` |
| `statelessness:6185` | named_export | struct | `stateless::research::Reachability` |
| `statelessness:6181` | public_member | struct_field | `stateless::research::Reachability::edges` |
| `statelessness:6183` | public_member | struct_field | `stateless::research::Reachability::observed_properties` |
| `statelessness:6184` | public_member | struct_field | `stateless::research::Reachability::skipped_checks` |
| `statelessness:6180` | public_member | struct_field | `stateless::research::Reachability::states` |
| `statelessness:6182` | public_member | struct_field | `stateless::research::Reachability::violations` |
| `statelessness:6155` | named_export | struct | `stateless::research::ViolationWitness` |
| `statelessness:6153` | public_member | struct_field | `stateless::research::ViolationWitness::details` |
| `statelessness:6154` | public_member | struct_field | `stateless::research::ViolationWitness::inputs` |
| `statelessness:6152` | public_member | struct_field | `stateless::research::ViolationWitness::property` |
| `statelessness:6312` | named_export | function | `stateless::research::compare_equivalence` |
| `statelessness:6317` | named_export | function | `stateless::research::qualification_parameters` |

## Inspected source identities

```text
1e402d6a4aeb016621aa90a9e3c0971fe1e1efc2b5e9020ddf928159b30b68c5  python/src/statelessness/research.py
87d04f1a54017516b7066b378fd08ed333d109c5976d23530333c3e8bc31d281  go/research.go
c11557f0e7650eeedb4f16d8ab56cad4fd4340210aa660493eb27063eb9790c5  typescript/src/research.ts
330f0938cc57bea3e54f299386f58959f9440575c3b6ce3e3ac9310261a19277  swift/Sources/Statelessness/Research.swift
```

Independent review, per-field behavioral mapping, installed consumers and shared
cross-engine qualification remain open. This is not the full M0 symbol ledger.
