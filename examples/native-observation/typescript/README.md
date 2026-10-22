This consumer imports only the installed `@jagalite/statelessness` public package.
It implements `observe fixturePath` and `self-test fixturePath` from
`spec/observation-v1.md`. The latter compares every case against the independent
shared golden; neither mode generates expected data.

The parent qualification driver must install a package built from frozen sources,
compile `consumer.ts` as ES2022/NodeNext with strict checking, and run:

```sh
node consumer.js self-test /absolute/path/to/conformance/observation-v1.json
node consumer.js observe /absolute/path/to/conformance/observation-v1.json
```

Run compilation and execution through Kyoto. This implementation handoff has
performed neither. Node is used only here to read the fixture. The library's
observation implementation has no Node dependency. Reducers, initial-state and
check callbacks throw if called; codes are encoded by actual public bounded codec
hooks, including the real declaration binder for its dedicated case.

For declaration cases, every original model codec hook increments the forbidden
callback counter and throws. Only schemas installed by `bindModelCodec` can
encode payloads, so bypassing the binder fails even in `observe` mode. Native
custom codec cases retain their instrumented `encode*Into` hooks; expected bytes
and callback order are unchanged.

The self-test also constructs the installed generic oracle `ObservationError`
and diagnostic `TextObservationError` separately, checking the oracle cause and
retained transition without executing a model. Their declarations must both
compile; the observation unit regression additionally exercises the real
`WithOracle.observeTransition` failure path.
