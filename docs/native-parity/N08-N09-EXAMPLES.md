# Native stateful applications

The `examples/native-stateful` applications use ordinary installed native
packages. They never initialize Rust, Wasm, cgo, or a debugger package.

- Python imports `statelessness.lifecycle`, `.network`, `.composition`, and
  `.stateful_codec`. Use `StatefulCodec(model, network_schemas(...))` or
  `StatefulCodec(pair, pair_schemas(...))` to attach canonical payload schemas.
- Go uses `NewNetwork`, `NewPair`, `NewLifecycleMonitor`, and
  `NewIndexedLifecycleMonitor`. `Network.BindCodec` and `PairCodec` attach
  ordinary model codecs. `LifecycleOracle` and `BindLifecycleCodec` reuse the
  existing independent oracle adapter. `WithInputEquality` enables network BFS
  with explicit queued-payload equality.
- TypeScript exports `Network`, `Pair`, `LifecycleMonitor`,
  `IndexedLifecycleMonitor`, `withNetworkCodec`, `withPairCodec`, and
  `withLifecycleCodec` from the normal package entry. Bigint logical times cover
  the full u64 domain without number rounding. The library has no Node imports;
  only the standalone consumer uses Node filesystem APIs.
- Swift exposes the same native structs and builders through `Statelessness`.
  Attach `network.codec(...)` or `pair.codec(...)` to the returned native model.
  Use `model(equalInput:)` or the pair's two input equality closures for BFS.
  Use `historyCodec(key:)` for explicit legacy/indexed persistence. Keys needing
  Rust String identity use `LifecycleTextKey` instead of Swift String equality.

The lifecycle consumer begins an epoch/generation-scoped operation, observes its
settlement, explicitly fences the epoch, persists/restores history, then receives
a stale completion. The expected result is a retained `retired_epoch_reference`
violation. All four print the canonical indexed failure history bytes. Equal
history bytes alone do not qualify a failure replay.

The network consumer records a three-machine ring whose global sum fails after
three observed transitions. Each child describes one outgoing effect; routing
retains one message and only an explicit deliver input executes the next child.
The unchanged native trace recorder captures the failure. Separate fresh-process
replayers assert `outcome=exact` and `failure_reproduced=true`. The qualification
runner attempts all 16 directed native producer/consumer combinations. This is
one compatible application identity, not a claim about arbitrary application
serialization or pinned Rust binary-format interchange.

Run the source-bound runner only through the shared Kyoto queue, against an
immutable source copy, with output outside that copy:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 /absolute/frozen/source/scripts/qualify-native-stateful.py \
  --source /absolute/frozen/source --output /absolute/new/receipt-directory
```

The runner records every command, exit status, elapsed wall time, log hash, source
manifest and package/executable hashes. It builds ordinary wheel, Go module, npm
package and SwiftPM consumers. A rejected compiler invocation is never counted as
a detected semantic mutant. The explicit Python mutation removes stale-reference
rejection in a separate source copy and must produce the independently expected
assertion failure. That single control does not qualify mutations in other engines.

Current qualification state and unresolved scope belong in the lane handoff,
not in an inferred pass based on these commands merely existing.
