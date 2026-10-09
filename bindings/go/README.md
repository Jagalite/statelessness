# Experimental Go SDK and cross-language conformance

From the Statelessness root, one command builds and tests the SDK, runs the
actual correct/faulty Go reducers against the native Rust reference, saves both
witnesses, and replays the reduced disagreement in a fresh process:

```sh
scripts/check-go.sh
```

Requirements: Go 1.24+ with cgo enabled, Rust 1.90+, Python 3, a C compiler and
linker (Xcode Command Line Tools on macOS; GCC/Clang on Linux). The script builds
matching host libraries; this is a local source package, with no publication or
Go/cgo dependency in ordinary Rust builds. Only macOS arm64 has been qualified.
Windows, cross compilation and ASan qualification are not established.
The fixture uses the `stateless_jobs` Go build tag to link both its bridge and SDK
against one static native engine. The standalone SDK uses the shared ABI library;
never pass opaque Rust handles between independently built engine copies.

For the SDK alone, run `cargo build --lib`, configure `CGO_LDFLAGS` with
`-L/absolute/path/to/target/debug`, and configure the OS library loader path to
that directory (`DYLD_LIBRARY_PATH` on macOS, `LD_LIBRARY_PATH` on Linux). Import
`github.com/Jagalite/statelessness/bindings/go` using a local Go module `replace`
directive pointing to `bindings/go`. Link against the native library built from
the matching header/source for the same architecture. `go test ./...` tests the SDK and reducer. The example command requires
`go build -tags stateless_jobs ./cmd/jobs` and its matching static library.
After rebuilding an external native library, use `go test -a -count=1 ./...`
(or `go build -a`) to bypass Go's external-library cache blind spot. The conformance
script instead binds cgo flags to its content identity and disables test caching.

Implement `Model`, then call `WithSession(model, func(s *Session) error {...})`.
`Record`, `Replay` and `Enumerate` execute the existing Rust engine. Returned
bytes are owned copies. Enumeration returns the authoritative termination report:
status OK alone is not evidence of graph exhaustion. Record's step limit can
produce a passing prefix. Replay enforces historical state/effect/check/identity
bytes and returns `Diverged` or `Incompatible` with an error; a replay mismatch
alone never means a behavioral bug is fixed.

Sessions and callbacks are synchronous, thread confined and non-reentrant. Use
the session only within its closure on that goroutine, never from callbacks or
other goroutines. The SDK locks the OS thread, rejects calls on a different thread and concurrent
or reentrant operations before entering native code, copies borrowed callback spans,
contains Go panics, and frees model, C context and cgo.Handle in reverse order.
Only a numeric cgo.Handle is retained in a malloc-owned C context; ordinary Go
pointers are never retained by C/Rust. Callbacks contain configuration only;
logical state and history must be encoded in every checkpoint. Errors include
the callback operation and original panic/error text. `WithNative` provides a
scoped borrowed handle for application-owned Rust bridges, with the same rules.
Native callback response buffers are borrowed and are never freed by Go. Aggregate
callback packet sizes are checked before copying or allocating payloads.

## Paired harness boundary

`src/conformance.rs` supplies reusable `Paired<R,P>` and `Projection<M,R>` using
`WithOracle`. Rust composes a borrowed `StatelessModel::host()` with an arbitrary
native reference accepting the same explicit inputs. Both reducers advance
independently. The reference ignores actual effects/disposition when advancing;
its state lives in the oracle checkpoint. Pure reference recomputation supplies
expected effects/outcome without adding unbounded history. Existing independent
checks from both models are retained. The example reports independent property
failures separately (status 5), including failures before any input. Reserve the
three `pair.*` comparison IDs for the harness. Reference independence and correct
projections remain the application's responsibility.

Projection metadata versions semantic state/effect comparisons independently
of model codecs. Named checks are `pair.state`, `pair.effects`, `pair.outcome`.
This initial outcome projection compares the full disposition (including reason).
Applications with different reason conventions should adapt that normalization
before using the harness. State/effect projection types need not have identical
layouts or serialized representations. Checkpoint codecs still retain each
implementation's exact historical bytes for replay.

The generic Go SDK exposes no fuzz/shrink API. Rust can wrap the paired model in the existing `Auto` finite-domain generator
and use existing enumeration/fuzz/shrink/record/replay functions and `WithOracle`. `examples/go-jobs` is a narrow example bridge
exposing paired enumeration/shrink/replay only. No second engine, translator or
motion checkout is involved. Adapter I/O is limited to evidence storage; reducers
perform no I/O, read no clocks and have no hidden mutable history.

## Job semantics v1

State projection v1 is `(phase, generation)`: phases 0 empty, 1 queued, 2 running,
3 canceled, 4 complete; generation is 0 initially and 1 or 2 after submit. No
optional fields: zero generation means absent job and is valid only for empty
phase. Submit from empty/canceled/complete increments generation; generation 2
cannot overflow or submit again. Start requires queued; cancel requires queued
or running; completion requires running and the current token. Stale completion
is an explicit completion event with a mismatching token and is rejected.

Event codec v1 is exactly two unsigned bytes `(kind, token)`: kind 0 submit,
1 start, 2 cancel, 3 complete, 4 stale-completion request; token 0..2. Kinds 3/4
have identical token validation, so even an event labeled stale is evaluated
from explicit token data. All 15 events are supplied at every state, including
invalid requests. Noncompletion tokens are ignored. Rejections preserve state
and emit no effects. Ordered effect projection v1: 0 submitted, 1 started,
2 canceled, 3 completed. State codec v1 is two bytes `(phase,generation)`.
Unknown tags, out-of-range values, truncation and trailing bytes are errors;
all arithmetic stays within the declared bounds. ABI framing uses u32 LE lengths,
UTF-8 strings and ordered lists; no map iteration or implicit clocks.

Both Go and Rust retain independent bound and rejection-preserves-state checks.
Agreement may preserve a shared/reference bug; these checks are not a full job
specification proof. The faulty Go reducer accepts stale completions while
running. The script deliberately prefixes the BFS witness with two harmless
rejected requests to demonstrate causal sequence reduction, preserving the
original failure target. Shrinking uses the existing validity/phase/check-ID
machinery, not an assertion that the witness is globally shortest.

Artifacts include event/model/projection/codec identities, both implementation
build identities, seed `none` for deterministic BFS, and bounds (10,000 states,
100,000 transitions, depth 100). The script computes a SHA-256 content/toolchain
build identity. Builds made outside that script use an explicitly unqualified
identity; use the script for durable evidence. Changed schemas or build identities
are rejected by exact replay. The report prints termination, counters, shrink
termination, first input index (zero based), event, independent before/after
checkpoints, effects, outcomes and named failures. Initial failures use
`input_index=none`; they are not attributed to a nonexistent event. `target/go-jobs.original.trace`
and `.reduced.trace` preserve evidence.

With the loader paths exported as in the script, rerun:

```sh
target/go-jobs correct target/go-jobs
target/go-jobs faulty target/go-jobs
target/go-jobs replay-faulty target/go-jobs.reduced.trace
```

A correct implementation must be explored again under its own build identity;
replaying faulty historical evidence with it is incompatible, not proof of a fix.
Budget exhaustion/interruption is incomplete agreement. Finite graph exhaustion
covers only the declared event domain. Bounded agreement is not universal
equivalence; database, filesystem and process adapters remain outside this core
comparison. Other architectures/platforms need their own native qualification.

Exact local commands, toolchain versions, results and platform limits are recorded
in [the validation note](../../docs/GO-CONFORMANCE-VALIDATION.md).
