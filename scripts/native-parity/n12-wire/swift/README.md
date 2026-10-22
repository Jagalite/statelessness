# Swift public-API pipe consumer

The executable implements `../SPEC.md` with `Model`, its native `Codec`,
`DebugSession`, and `ProtocolAdapter.serve`. It does not parse frames or compose
responses. Callback entries go immediately to stderr; stdout belongs exclusively
to the library's framed transport.

After public `serve` returns on clean EOF or throws a transport error, the host
reads the public session state, revision, and sequence and emits exactly one
stderr line `FINAL <state> <revision> <sequence>`. It then returns normally or
rethrows the original transport error for a nonzero exit. The fixture ignores
SIGPIPE so closing the real stdout pipe reader produces a write error that can
return through `serve`. After one committed increment, that failure must retain
`FINAL 1 1 1`; this is actual session state, not a reconstruction from callbacks.

Root owns all Kyoto submission and qualification. No build or execution was
performed while authoring this fixture.

For a fresh isolated consumer, copy the repository `Package.swift` and `swift/`
tree into `$N12_SWIFT_LIBRARY`, and this directory into `$N12_SWIFT_CONSUMER`.
Both paths and `$N12_SWIFT_BUILD` must be new private paths selected by root.
Submit the following argv through shared-default Kyoto (retain any compiler
options used by root's qualification):

```sh
env N12_SWIFT_PACKAGE_PATH="$N12_SWIFT_LIBRARY" swift build \
  --package-path "$N12_SWIFT_CONSUMER" --scratch-path "$N12_SWIFT_BUILD" \
  --configuration debug --jobs 2 --product n12-wire-swift
```

The resulting host is `$N12_SWIFT_BUILD/debug/n12-wire-swift`; launch it with no
arguments, `--read-only`, `--exact`, or `--cache-one` as required by the harness.
Flags may be combined. SwiftPM resolves only the copied library path; no registry
installation, network dependency, or source checkout mutation is required.

Focused library regression execution argv, also root-owned via Kyoto:

```sh
swift test --package-path "$N12_SWIFT_LIBRARY" \
  --scratch-path "$N12_SWIFT_TEST_BUILD" --configuration debug --jobs 2 \
  --filter WireParityTests
```

Compilation, local tests, and direct native-versus-Rust pipe comparison are
separate gates. This fixture and its source assertions alone qualify none of
those gates.
