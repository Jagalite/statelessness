# Installed Python observation consumer

This application imports the installed `statelessness` package using public APIs.
It does not adjust `sys.path`, load Rust/FFI, or derive expectations from its own
output. The shared contract and independent cases are in
[`spec/observation-v1.md`](../../../spec/observation-v1.md) and
[`conformance/observation-v1.json`](../../../conformance/observation-v1.json).

After the parent freezes and installs the integrated sources, run from the
repository root, using the installed environment's Python executable:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- /absolute/venv/bin/python examples/native-observation/python/consumer.py self-test conformance/observation-v1.json
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- /absolute/venv/bin/python examples/native-observation/python/consumer.py observe conformance/observation-v1.json
```

Both modes emit the same `statelessness.observation-results.v1` JSON; `self-test`
also compares every case against the shared expected bytes, callback order, first
error stage, and final sink health. Unexpected execution/format failures fail the
process. Cases continue after expected observation errors to exercise recovery
and poison at Off. The injected writer counts every write attempt, including empty
or failed writes, and every flush attempt. Each final Off health probe asserts
zero additional payload callbacks, writes, and flushes on healthy and poisoned
sinks. Writer extraction also asserts zero additional writes and flushes.

Declaration cases use the public `model_codec(...).bind(model)` path with explicit
field adapters that alone emit the payload bytes and record the ordered callbacks.
The original model's bounded and legacy encoder hooks trap, so bypassing the
binding fails the shared declaration case. The regression source also deliberately
bypasses that binding and checks the resulting codec error and empty output.

These events describe supplied transitions and checks. Initializer, reducer,
checker, and effect hooks trap; diagnostics grant no checked-token or continuity
authority. Existing checked results, periodic checking, and bounded Recorder
handoffs remain separate APIs.

No execution or qualification was performed while authoring this consumer.
