This consumer imports only `github.com/Jagalite/statelessness/go`. The shared
qualification driver stages the package and this directory into separate module
roots, supplies the module dependency, builds with `CGO_ENABLED=0`, and invokes:

```text
native-observation observe /absolute/path/to/conformance/observation-v1.json
native-observation self-test /absolute/path/to/conformance/observation-v1.json
```

`observe` emits actual destination bytes, payload callback order, first error
stage, and final sink health in `statelessness.observation-results.v1`. It never
reads expected fields to construct results. `self-test` additionally compares all
four result fields to the independently authored fixture. Both preserve case
order and exact decimal u64 values. All executable qualification belongs in the
parent's Kyoto job after the integrated source freeze.

The declaration case uses `OrderedRecord` and `BindModelCodec` through the public
encoding dispatch. Ordinary cases supply incremental codec callbacks alongside
legacy callbacks that fail if invoked. Reducer, initial-state and checker calls
are counted and rejected. Sink faults use Go's `io.Writer` contract: a partial
write returns its delivered byte count and a nonnil error; every such failure
permanently poisons the observer. Extraction never flushes implicitly.

Diagnostic errors are `TextObserverError`; the existing generic oracle
`ObservationError[S,O]` retains its original meaning. The consumer normalizes the
observer's actual stage and first cause, including sticky record-limit failures.
Callback-supplied public errors cannot claim a different active observer stage.
The candidate uses default Go 1.23 panic behavior; `GODEBUG=panicnil=1` is outside
the observation profile because an existing raw value-codec boundary can swallow
a nil panic under that compatibility setting.
