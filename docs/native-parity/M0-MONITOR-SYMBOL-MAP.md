# M0 monitor symbol map: static inventory, not qualification

This maps all 29 records in the existing compiler receipt
`semantic-symbols.jsonl` with source `src/monitor.rs`. The receipt belongs to
baseline commit `0876717bee85e335449c2c99ada41f6c6e6112d1`, whose monitor file
SHA-256 is `d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac`.
The current worktree differs, with SHA-256
`c5199163023e1414168859a6aed78e1071483cffbaf96a3369cd7b6ebf4d8c55`. Receipt
IDs, source locations and exports describe the pinned snapshot, not every current
or cfg-dependent export. Mapping candidates below are dirty-worktree source
observations, not claims of equivalent behavior or qualification. Reconcile the
Rust source drift before promoting this inventory.

| Rust family | Python candidate | Go candidate | TypeScript candidate | Swift candidate | Unresolved work |
| --- | --- | --- | --- | --- | --- |
| `RecorderOptions`, fields, `Default` | `monitor.RecorderOptions` | `RecorderOptions`, `DefaultRecorderOptions` | `RecorderOptions`, constructor defaults | `RecorderOptions` initializer | Compare defaults and validation, including byte accounting and binary-limit types. |
| `Recorder::new`, `with_options`, checked initial constructors | `Recorder` constructor, `with_options`, `with_checked_initial`, `with_checked_initial_at` | `NewRecorder`, `NewRecorderWithOptions`, `NewRecorderWithCheckedInitial`, `NewRecorderWithCheckedInitialAt` | `Recorder` constructor, `withCheckedInitial`, `withCheckedInitialAt` | `Recorder` initializers with model or checked initial | Compare initial failure, checkpoint and ownership behavior; verify identity and absolute sequence limits. |
| `observe`, `observe_checked`, `stop_with_error` | Same snake-case methods on `Recorder` | `ObserveRecorder`, `ObserveCheckedRecorder`, `Recorder.StopWithError` | `Recorder.observe`, `observeChecked`, `stopWithError` | `Recorder.observe`, `observeChecked`, `stopWithError` | Verify already executed turns never reexecute, checks and error precedence, stop/freeze and callback failures. |
| `snapshot`, `into_trace`, `write_to`, `write_with_limits` | Same snake-case methods on `Recorder` | `Recorder.Snapshot`, `IntoTrace`, `WriteTo`, `WriteWithLimits` | `Recorder.snapshot`, `intoTrace`, `writeTo`, `writeWithLimits` | `Recorder.snapshot`, `intoTrace`, `writeTo`, `writeWithLimits` | Verify ownership after transfer, serialization identity and sink failure; signatures and failure semantics differ. |
| `retained_bytes`, `retained_steps`, `observed_steps`, `evicted_steps`, `is_frozen` | Same snake-case methods on `Recorder` | PascalCase methods on `Recorder` | camelCase methods on `Recorder` | camelCase properties on `Recorder` | Compare accounting units and overflow, eviction, retained suffix and post-transfer behavior. Serialized bytes do not bound callback allocations or RSS. |
| Rust `Clone` and `Debug` trait implementations | Python object/data-model behavior | Go value behavior | TypeScript object behavior | Swift value behavior | Establish relevant observable outcomes; Rust trait machinery itself is not a native export requirement. |

Candidate paths and SHA-256 for this inspection:

- `python/src/statelessness/monitor.py`: `fc60901fb7a5516dd5543843536418e58dd524a0ae1c7c0e8ef600a31d8850a9`
- `go/recorder.go`: `9b8d16bc5fef28976624f18c4c030d449cc7d7104c463eebfd8c1db1ecf42b53`
- `typescript/src/runtime_recorder.ts`: `118d309e8a76fd07c15b51036f94fe013876734367587257b111ecadc9eaf118`
- `swift/Sources/Statelessness/Recorder.swift`: `012aaf737da3d04af34ad3bdb5972179e44ef5b515177032c986ebfb3ad6dc60`

Independent normative cases, cross-engine replay, installed consumers, resource
receipts and reviewed contracts remain required. Related observation and trace
exports have separate source locations and are outside this map.

## Receipt records

Each row resolves to a family above; the table records reachability from the
compiler receipt rather than certifying each native candidate.

| Receipt ID | Role | Kind | Rust reachable export |
| --- | --- | --- | --- |
| `statelessness:4550` | implementation_member | function | `<stateless::monitor::RecorderOptions as Clone>::clone` |
| `statelessness:4551` | public_std_trait_implementation | impl | `<stateless::monitor::RecorderOptions as Clone>::{impl}` |
| `statelessness:4552` | implementation_member | function | `<stateless::monitor::RecorderOptions as Debug>::fmt` |
| `statelessness:4553` | public_std_trait_implementation | impl | `<stateless::monitor::RecorderOptions as Debug>::{impl}` |
| `statelessness:4554` | implementation_member | function | `<stateless::monitor::RecorderOptions as Default>::default` |
| `statelessness:4555` | public_std_trait_implementation | impl | `<stateless::monitor::RecorderOptions as Default>::{impl}` |
| `statelessness:4530` | named_export | module | `stateless::monitor` |
| `statelessness:1881` | named_export | struct | `stateless::monitor::Recorder` |
| `statelessness:4617` | implementation_member | function | `stateless::monitor::Recorder::evicted_steps` |
| `statelessness:4609` | implementation_member | function | `stateless::monitor::Recorder::into_trace` |
| `statelessness:4618` | implementation_member | function | `stateless::monitor::Recorder::is_frozen` |
| `statelessness:4592` | implementation_member | function | `stateless::monitor::Recorder::new` |
| `statelessness:4598` | implementation_member | function | `stateless::monitor::Recorder::observe` |
| `statelessness:4600` | implementation_member | function | `stateless::monitor::Recorder::observe_checked` |
| `statelessness:4616` | implementation_member | function | `stateless::monitor::Recorder::observed_steps` |
| `statelessness:4613` | implementation_member | function | `stateless::monitor::Recorder::retained_bytes` |
| `statelessness:4614` | implementation_member | function | `stateless::monitor::Recorder::retained_steps` |
| `statelessness:4608` | implementation_member | function | `stateless::monitor::Recorder::snapshot` |
| `statelessness:4606` | implementation_member | function | `stateless::monitor::Recorder::stop_with_error` |
| `statelessness:4595` | implementation_member | function | `stateless::monitor::Recorder::with_checked_initial` |
| `statelessness:4596` | implementation_member | function | `stateless::monitor::Recorder::with_checked_initial_at` |
| `statelessness:4593` | implementation_member | function | `stateless::monitor::Recorder::with_options` |
| `statelessness:4610` | implementation_member | function | `stateless::monitor::Recorder::write_to` |
| `statelessness:4612` | implementation_member | function | `stateless::monitor::Recorder::write_with_limits` |
| `statelessness:4619` | public_inherent_implementation | impl | `stateless::monitor::Recorder::{impl}` |
| `statelessness:1892` | named_export | struct | `stateless::monitor::RecorderOptions` |
| `statelessness:4533` | public_member | struct_field | `stateless::monitor::RecorderOptions::limits` |
| `statelessness:4532` | public_member | struct_field | `stateless::monitor::RecorderOptions::max_retained_bytes` |
| `statelessness:4531` | public_member | struct_field | `stateless::monitor::RecorderOptions::max_steps` |
