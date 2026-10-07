# StatelessNative Swift package

Experimental synchronous byte-model recording, replay and enumeration through
native ABI 1. No third-party Swift packages are required. Qualified on macOS;
other Swift platforms need separate validation and matching Rust builds.

Add the generated Swift package as a local dependency:

```swift
// In your application's Package.swift:
dependencies: [.package(path: "/path/to/target/bindings/swift")],
// In the target that uses it:
dependencies: [.product(name: "StatelessNative", package: "swift")],
```

Link the matching native bundle when building or testing:

```sh
swift test -Xlinker -L -Xlinker /path/to/target/bindings/native/lib \
  -Xlinker -rpath -Xlinker /path/to/target/bindings/native/lib
```

Implement `ByteModel` and create `try Session(model)`. Its callbacks are
`initial`, `step(state:input:)`, `checkState`, optional
`checkTransition(before:input:transition:)` and `inputs(state:)`. Payloads are
canonical `[UInt8]` values; `Transition` and `Check` handle ABI framing for you.
See `swift/Counter.swift` in this package for a complete implementation.

`record` returns `(status, artifact)`, `replay` returns `(status, detail)`, and
`enumerate` returns `(status, report, artifact)`. Status 1 means a property
failure (or its exact reproduction), 2 divergence, 3 incompatible identity,
and 12 invalid trace. Record/enumeration throw `BindingError` for engine errors;
replay returns its ABI status for inspection. Read search termination and trace
limits: successful replay can verify only a recorded prefix.

Keep sessions on their creation thread and do not reenter from callbacks.
Callbacks may throw: errors are contained at the C boundary and reported as
model errors. Session lifetime owns the native model. Returned byte arrays are
copies and remain valid after later calls or session destruction. Do not put
real side effects or hidden logical state in callbacks. No native async, foreign
fuzz/shrink, or live recorder interface is provided in this release.
