# Swift installed observation consumer

This executable imports only the installed native `Statelessness` product. The
parent qualification driver copies the frozen repository-root `Package.swift`
and `swift/` tree to a sibling `swift-package/`, then copies this consumer into
its own directory. It uses no Rust, ABI library, implementation fixture generator,
checkout-relative source import, or third-party package.

`ObservationConsumer observe fixturePath` emits the shared JSON result envelope.
`ObservationConsumer self-test fixturePath` also compares every result with the
independently authored fixture expectations and exits unsuccessfully on mismatch.
The consumer refuses an empty corpus. Diagnostic errors go to stderr; only the
result JSON goes to stdout. The parser preserves exact decimal UInt64 values.
Declaration-bound rows use `TraceRecords` and `ModelCodecBinding`; callbacks,
payload bytes, bounds, writer failures and the final Off health probe go through
public APIs. Initial/reducer/check/clone/decode callbacks fail if invoked.

Commands for the parent to include in the one shared Kyoto qualification after
source freeze (not executed during implementation):

```sh
swift test --package-path "$FROZEN_ROOT" --scratch-path "$SWIFT_TEST_BUILD"
swift build --package-path "$INSTALLED_CONSUMER" --scratch-path "$SWIFT_CONSUMER_BUILD" -j 2
"$SWIFT_CONSUMER_BUILD/debug/ObservationConsumer" self-test "$FIXTURE"
"$SWIFT_CONSUMER_BUILD/debug/ObservationConsumer" observe "$FIXTURE"
```

For a focused run, append `--filter ObservationTests` to the first command. The
full suite must also retain existing checked-result and Recorder coverage. These
commands describe future execution only; source test counts are not passed-test
counts, and installed Swift/runtime/platform qualification remains pending.
