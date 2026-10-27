# Go N12 pipe fixture

The host supplies the SPEC model through the public Go model/session APIs and
passes stdin/stdout directly to `ProtocolSession.Serve`. It does not parse
requests, construct responses, or infer state from callback logs.

After `Serve` returns on clean EOF or a transport error, the host prints exactly
one `FINAL <state> <revision> <sequence>` line to stderr, reading all three values
from the actual public session. It then exits zero on clean EOF or nonzero on a
transport error. The fixture ignores SIGPIPE so a closed real stdout OS reader
produces a write error that returns through `Serve`; this allows the final line
to expose a turn already committed before the failed response write. This is
fixture signal policy, not a library signal-handling guarantee.

Supported flags are `--read-only`, `--exact`, and `--cache-one`. Initial-state,
decode, reducer, state-check, and transition-check callbacks log the exact `CALL`
lines specified in `../SPEC.md`; encoding callbacks are silent. No special flag
enables the final-state observation.

For a fresh isolated consumer, copy `main.go` and `go.mod` into its own directory
and copy the candidate's Go module into a frozen package directory. Through the
root-owned Kyoto job, replace the module path in the consumer copy and build:

```sh
go -C "$consumer" mod edit "-replace=github.com/Jagalite/statelessness/go=$frozen/go"
GOWORK=off go -C "$consumer" build -mod=readonly -o "$artifacts/go-host" .
```

Keep the configured Go toolchain and compiler options. All build and runtime
qualification is root-owned; this file does not record an executed result.
