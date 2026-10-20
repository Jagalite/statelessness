# Installed bounded codecs and rich replay

These applications use public incremental codec hooks, full bounded audit
recording, rich replay, and bounded rich replay from installed/staged Python, Go,
TypeScript, Swift, and the actual Rust engine. Their legacy allocating encoders
throw if invoked. No runtime wrapper delegates native model execution to Rust.

The common model starts at zero, adds integer zero/one, emits `changed` for one,
and fails `threshold.bound` at state three. Codecs encode ASCII incrementally.
Metadata is `native-codec-replay-threshold`, versions 1/1/1, build
`application-v1`. [contract.json](contract.json) pins finite values for all nine
binary limits and the canonical observer/control receipt shape.

`record PREFIX` actually records `[1, 1, 1]` into `PREFIX.bin` using the bounded
public audit recorder and saves an explicit portable projection as `PREFIX.json`.
Actual engine provenance remains in full audit configuration. Native recorders
stamp their actual native version/build; Rust stamps its actual version/build.
The driver verifies every consumer retains the producer's ordered configuration.

`replay BIN` runs both rich and bounded replay. Receipts consume typed initial and
turn values, complete checks and state-check boundaries, every expected/actual
encoded value, all difference flags, and the report as seen by each observer.
The driver compares all fields against hand-authored expectations, rather than
only checking final success. All 25 ordered producer/consumer pairs must pass.
`replay-json JSON` independently verifies the 25 portable failed-witness replays
through the preferred codec hooks.

Each producer also verifies entry clearing, sticky limit failure, shared framing
budgets, nested framing, and native buffer lease expiration. A stored-artifact
limit rejects before any decoder executes. An actual output that exceeds its
encoding quota executes one reducer and retains its completed actual turn on
error. Altering only the expected failed-check details produces rich check
divergence while retaining same-property failure reproduction. These are finite
application controls; the shared engine corpora cover other boundaries.

After all engine sources are frozen, submit through Kyoto:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-codec-replay-consumers.py --rust validation/corpus/target/debug/native-codec-replay --rust-portable validation/corpus/target/debug/stateless-corpus --output /tmp/native-codec-replay-consumers-unique-run
```

The driver builds and installs local packages outside the checkout, compiles
public TypeScript declarations, and retains commands/logs, exact source and
artifact fingerprints, toolchains, and replay receipts. It also reruns the four
established profiles against independent goldens and cross-engine replay.
Rust executables are separately built through Kyoto; the driver runs no cargo
commands. Output must be new or empty. `--without-swift` and omission of
`--rust-portable` explicitly narrow coverage. Passing does not promote profiles,
prove bounded process memory or callback allocation, measure performance, or
reproduce a terminal model error merely by replaying its checked prefix.
