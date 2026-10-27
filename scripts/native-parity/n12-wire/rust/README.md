# N12 external Rust pipe consumer

This standalone Cargo package uses the public `Model`, `ModelCodec`, `Enumerate`,
`DebugSession`, `ProtocolSession`, and `serve` APIs. Only model behavior and startup
options are fixture code. `serve` owns reading, decoding, admission, response
serialization, flushing, and disconnect. The binary neither reads golden files
nor constructs protocol responses. No library source is changed.

`expected-core.json` contains independently source-authored expectations, written
before execution. Its field maps are complete for the named scenarios, and its
status replacements describe the specified option/state changes. These are
assertion inputs for root's comparison, never replacement host output. Callback
lists are appended after the startup prefix, with one LF after every entry; the
empty-input callback intentionally ends in a space before LF. Epoch and request
ID must be verified against discovery and each request by the driver. Error
responses have no command fields. Configuration stays zero for this fixture.

After `serve` returns, on either clean EOF or a transport error, the host emits
and flushes exactly one `FINAL <state> <revision> <sequence>` line to stderr. It
reads all three values through `ProtocolSession::session()` and the public
`DebugSession` accessors, without reconstructing them from requests or callback
logs. It then propagates the original serve result. Unknown startup options or
initialization errors occur before serve and therefore have no `FINAL` line.

The standalone Rust executable uses normal `std` startup, which sets SIGPIPE to
ignored on this Unix host. This was checked read-only in installed stable and
1.95.0 standard-library sources (`std/src/sys/pal/unix/mod.rs`, `reset_sigpipe`).
No compiler option, extra FFI dependency, or library signal policy is added.
A closed real stdout OS pipe therefore produces an I/O error through `serve`.
For discovery followed by closing the reader and delivering input 1, the
source-derived expectation is nonzero exit, one increment's callback sequence,
and `FINAL 1 1 1`. This checks the committed session after the actual write fails;
the fixture does not replace stdout with a synthetic failing writer.

No builds, tests, installs, formatters, or host executions were run by this
implementation agent. Root owns all qualification through shared-default Kyoto.
All commands below are proposed job payloads, not completed operations. Preserve
root's compiler, toolchain, target, and model options; no toolchain is selected or
changed by this package. The source declares the library's Rust 1.90 minimum.

## Build and install argv for root

Relative dependencies resolve from this manifest to the enclosing repository.
The empty `[workspace]` makes the fixture an independent consumer, outside the
repository workspace membership. A frozen copy of the repository retains those
paths without a dependency rewrite. No registry dependency is needed.

Example fresh installation, submitted by root as one Kyoto job payload (replace
the three output directories with fresh, job-owned absolute directories):

```sh
env CARGO_INCREMENTAL=0 CARGO_HOME=/absolute/fresh-cargo-home \
  cargo install --offline \
  --path /Users/jagatranvo/.t3/worktrees/stateless/n12-wire-resume-oct22/scripts/native-parity/n12-wire/rust \
  --root /absolute/fresh-install-root \
  --target-dir /absolute/fresh-target-dir \
  --debug --bin n12-wire-rust
```

Expected installed executable: `/absolute/fresh-install-root/bin/n12-wire-rust`.
Root's later comparison job starts that installed binary directly with any
combination of `--read-only`, `--exact`, and `--cache-one`. No `cargo run` is needed
in the pipe test. Unknown options fail before initialization callbacks.

Build-only alternative, also root-submitted through Kyoto:

```sh
env CARGO_INCREMENTAL=0 CARGO_HOME=/absolute/fresh-cargo-home \
  cargo build --offline \
  --manifest-path /Users/jagatranvo/.t3/worktrees/stateless/n12-wire-resume-oct22/scripts/native-parity/n12-wire/rust/Cargo.toml \
  --target-dir /absolute/fresh-target-dir --bin n12-wire-rust
```

For a consumer copied to an arbitrary directory outside the frozen repository,
copy `src/main.rs` and the manifest, retain `[workspace]`, and set the two manifest
dependency `path` values to the absolute frozen repository root and its
`crates/statelessness-debug` directory. Run the same install/build argv with that
consumer's path. No library response generation or runtime-source edit is part of
this setup. Root should retain the generated consumer lockfile and source/binary
identities with its qualification receipts.

## Source-derived contract notes for the root comparison

Authority is commit `89f61d6666e2666fad65164dc5ab3e8f3e4e1fd5`, read from the recovered
baseline `t3-ccd33c8a`. The four source hashes in `expected-core.json` were also
read in the new checkout and matched. Below, `session.rs` and `protocol.rs` refer
to `crates/statelessness-debug/src/` at that baseline; other paths are relative to
the repository root.

- `session.rs:221-225,260-300` initializes once and performs one state check.
  Startup is exactly `CALL initial`, `CALL state 0`; metadata is not logged.
- `session.rs:491-504` handles reducer failure by returning **success** with
  `delivered=false`, retaining sequence/revision/state and the prior observation.
  The phase is Ended and stop is `model_error`; no state/transition check runs.
  `protocol.rs:1025-1043` therefore emits `ok`, `none`, with only `delivered` and
  `sequence` fields. It does not emit a `callback` protocol error or private detail.
  If a turn preceded the failure, outputs/checks still describe that prior turn;
  if no turn preceded it, outputs/checks still describe the initial observation.
- `session.rs:789-805` checks revision, then decodes, then calls typed `step`.
  `session.rs:447-452` checks ended only in that typed call. A current-revision
  valid step after cancellation, reducer failure, or property failure logs decode
  once and returns `ended`. Invalid model input logs decode once and returns
  `invalid_input` even when ended. A stale step returns `stale_revision` before
  decode. A read-only step returns `unauthorized` before revision/decode.
- `session.rs:503-588` commits any returned transition, including ignored or
  rejected, and increments sequence/revision once. `src/execution.rs:155-190`
  checks state before transition; a failed state property still runs the
  transition checker. The third increment therefore logs state 3 and transition
  2/1/3, commits output 3, and ends with `property_failure`. Its response remains
  `ok` with `delivered=true`; exact output is `0300000000000000`.
- `protocol.rs:622-685` defines all status fields, including case-sensitive
  phases `Ready`, `Paused`, `Ended`, lowercase mode/stop, no metadata fields, and
  no private error details. All declared fixture defaults match the public Rust
  defaults. The SPEC does not need a runtime change to enforce them.
- `protocol.rs:686-705,806-918` defines snapshot/check/output fields. Snapshot
  contains only handle/revision/sequence, not encoded state. Initial outputs
  contain only `outputs.total=u64:0`, even with exact authority. Turn output
  queries include disposition/provenance/payload policy, and exact mode adds
  `outputs.truncated` even for zero outputs. Default mode omits that key.
  First failure uses id/index/phase/sequence fields instead of
  `checks.first_failure=none`; private state/rejection details are omitted.
- `protocol.rs:806-862` and `:863-918` bind a checks/outputs handle but do not check
  the request's expected revision. `status` likewise ignores expected revision.
  `snapshot` checks expected revision. `inspect` binds the handle before checking
  revision, then budgets, then unsupported inspection. Do not introduce a common
  revision guard in front of these distinct command contracts.
- `session.rs:619-627` cancellation changes phase/stop without advancing revision
  or sequence, so valid snapshot handles remain valid. A reducer error likewise
  leaves such handles valid. `protocol.rs:1079-1085` emits no fields on successful
  cancel, publishes `cancelled`, and permits queries after cancellation.
- `protocol.rs:522-618` admits positive IDs only after version/session/epoch and
  request encoding checks. Accepted IDs retire even when command execution
  fails. Identical parsed requests replay cached original envelopes, revisions,
  and fields, with no callbacks; conflicting cached IDs fail
  `duplicate_conflict`. After eviction, old IDs fail `retired_request`.
  Wrong session/epoch does not retire an ID. Discovery status at epoch zero is
  canonicalized to the connection epoch before cache comparison.
- `protocol.rs:2022-2059,2067-2095,2124-2140,2284-2507` requires strict escaped
  UTF-8 and valid byte-hex/command arity. Unknown commands return `unsupported`,
  not `malformed`. Empty `bytes:` is syntactically valid and reaches model decode;
  malformed byte hex does not. Percent and byte hex accept either hex case.
  Callback hex is always lowercase; response escaping uses uppercase percent
  hex. Envelope u64 fields are decimal strings, never floating-point values.
- `protocol.rs:2636-2700` returns an ID-zero error frame for a complete request
  that fails decoding, without retiring the embedded request ID. It continues
  serving afterward. Clean EOF succeeds; partial header/payload, zero-length,
  and oversized frames fail transport without a response to that frame. Earlier
  completed frames and committed callbacks remain. A split complete frame is
  equivalent to an unsplit frame. Diagnostic stderr text is separate from
  `CALL ` assertions and should remain preserved verbatim.

`ProtocolSession` also requires `Enumerate` (`protocol.rs:307,335`), so the model
returns the finite input list `[0,1,2,3]`; it retains unrestricted delivery policy.
The SPEC does not prescribe an ignored-reason string; the host uses
`fixture ignored`, which this public protocol does not export. No additional
callback log kind is invented for enumeration or codecs.

These expectations cover the specified pipe fixture only. They establish neither
execution success nor full optional-module, platform, 144-cell, or release parity.
