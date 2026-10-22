# N12 direct wire fixture (source-authored, before execution)

This is a finite direct comparison of actual public optional debugger APIs.
All five hosts implement the same model and serve real stdin/stdout pipes.
Do not replace library wire emission with host-authored responses.

Session ID: `wire\té😀` (an actual TAB, e acute, grinning face).
Model metadata: name `n12-wire-counter`, model/properties/codec versions 1,
build `n12-wire-v1`. State is a u64 initially 0. Input is one byte:

- 0: ignored with reason `fixture ignored`, state unchanged, no outputs.
- 1: accepted, state + 1, one u64 output equal to the new state.
- 2: rejected with reason `fixture rejection`, state unchanged, no outputs.
- 3: reducer returns an error with detail `private reducer detail`.
- Other bytes or a non-single-byte encoding: input decode error.

State checks contain exactly `bounded`: passed for state <= 2; otherwise
failed with detail `private state detail`. Transition checks contain exactly
`transition`: always passed. Initial checks run once. Use unrestricted input
policy with Rust's literal default label. Do not attach observers, recorder,
inspection, watches, probes or telemetry. Use native exact output encoding
where the public API supports it. Encode state/output as eight little-endian
bytes; input as one byte. Metadata is not a callback-order assertion.

Hosts accept only `--read-only` (delivery false), `--exact` (exact values true),
and `--cache-one` (dedup entry budget 1); default delivery is true and all
other authority is false. Other protocol limits use Rust defaults, including
page size 100, frame bytes 65536, dedup bytes 4194304, telemetry snapshot bytes
8388608. Unrestricted policy label is `out-of-domain-injection`.
Host exits nonzero on transport errors, zero on clean EOF.

For source-derived callback assertions, each host appends these UTF-8 lines to
stderr immediately on callback entry, including on errors:

```
CALL initial
CALL state <decimal-state>
CALL decode <lowercase-input-hex>
CALL step <decimal-before> <decimal-input>
CALL transition <decimal-before> <decimal-input> <decimal-after>
```

Do not log codec encode callbacks. Diagnostic error lines may follow, but must
not use the `CALL ` prefix. Logs and wire frames are retained byte-for-byte.
Public serve API must own decoding/framing/admission; a host must not repair
responses, intercept commands or fake successful framing.

After serve returns, including on a transport error, read the actual public
session and emit `FINAL <state> <revision> <sequence>` once to stderr. The fixture
ignores SIGPIPE where necessary so a closed real stdout pipe yields a write
error through serve and allows observation of the already committed session.
This is an explicit host signal policy, not a library signal-handling guarantee.

Comparison normalizes only response field ordering and process-local epoch
identity (validated against discovery). No field renaming, omission, integer
coercion, error rewriting or string folding. Duplicate response keys fail.
Compare every response envelope, field, callback sequence, transport disposition,
and exact binary output where enabled. Preserve raw frames separately.

Planned scenarios: initial status/snapshot/checks/outputs; accepted, ignored,
rejected, failed state-check and reducer error; cancellation; authorization and
command-specific revision ordering; stale snapshot; identical/conflicting and
evicted admitted IDs; wrong session/epoch without retiring IDs; full-width u64
request IDs/revisions; strict UTF-8, malformed percent escapes, invalid byte
hex, command arity and unknown commands; clean EOF and split header/payload;
partial frame EOF before/after a committed turn; zero/oversized frame lengths.

This does not establish all optional N12 module parity, all native platform
qualification, 144-cell closure, or promotion.
