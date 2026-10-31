# Go native campaign consumer

This external-module example uses the public Go campaign APIs; its `go.mod` has
no checkout replacement. `--case NAME` emits one `native-campaign-v1` JSON row,
`--all-reference` emits all 49 rows in Rust fixture order, and `--list-cases`
prints the case names. These modes exercise 15 seed vectors, 15 configuration
validation cases and 19 deterministic one-worker campaign cases. The inputs are
transcribed from `conformance/fixtures/native-campaign-public.rs`; the consumer
never reads expected output. Callback order, actual job reports and observed
errors come from the Go engine.

Run the consumer against a separately installed, source-bound Go module through
Kyoto for qualification. A local `replace` or source-tree execution is development
evidence only. The ordinary/default example and `--host-check` continue to
exercise worker lifecycle behavior separately. Neither a matching row nor a local
build proves the platform, installed-consumer or full N06 qualification gates.
