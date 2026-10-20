# Installed checked-result and runtime Recorder consumers

These small applications execute their own threshold transitions, check them once
through sealed public results, and hand those results to the real runtime Recorder.
The main artifact fails at state three. Complete ordered state checks precede the
transition check; failed checks still produce a valid token.

Run the driver through Kyoto after all package sources are frozen:

```
python3 scripts/check-native-checked-consumers.py --rust validation/corpus/target/debug/native-checked --rust-portable validation/corpus/target/debug/stateless-corpus --output /private/tmp/NEW-OUTPUT
```

The driver builds local installed Python wheel/npm tarball consumers and separate
Go/Swift applications against staged sources. Each of 25 ordered producer/consumer
pairs independently loads a full binary AuditTrace and compares every rich and
bounded replay observation with hand-authored expectations. Another 25 fresh
processes replay portable JSON projections. Engine stamps and ordered monitor
parameters remain truthful and are checked without normalization.

Controls exercise checkpoint attachment at host origin ten, token reuse in two
recorders, exact check-once counters, sampled/duplicate/gap rejection and frozen
prefixes, one-step ring eviction promoting only state checks, and an oversized
encoded output that freezes a readable, structurally roundtrippable earlier prefix.
Ring snapshots also undergo full rich/bounded semantic replay. Native accessors
must preserve ownership. Python/TypeScript reject diagnostic forgery at runtime;
Go rejects a zero-value token, and Swift’s installed compiler rejects passing a
public diagnostic to the checked-token method.

This is finite application qualification. Native sealing is an API integrity
boundary, not protection against hostile reflection or incorrect clone callbacks.
Rust tokens borrow context; native payload snapshots are detached according to
public clone contracts. Bounds cover serialized evidence, not arbitrary callback
allocations or process RSS. Structural roundtrip of a model-error prefix does not
claim terminal error reproduction. This does not promote profiles or claim full
recorder/evidence parity, cross-platform support, registry publication, or speed.
