The optional `@jagalite/statelessness/debug` export provides an offline evidence
viewer. Constructing, seeking, previewing, and selecting stored checks do not run
models. Views contain detached exact bytes, including potentially sensitive data;
previews carry truncation and encoding provenance. No output decoder is inferred.
A retained checkpoint can have a nonzero absolute host sequence. All absolute
sequences and budgets use bigint, including values above JavaScript's safe integer.

Verification is explicit and requires a finite transition bound. It preserves the
viewer cursor and exposes the native rich replay report/observations. V2 check
splits are verified independently of matching combined check bytes. Legacy v1
noninitial phases remain unknown; a caller can explicitly supply a legacy phase
to minimization. This declaration is checked against actual candidate failures.

Minimization restores the retained checkpoint for every candidate, preserves
property and phase, and combines environmental admission with Generate causality.
Declared policies require an explicit matching policy; labels do not authenticate
callbacks. An enumerated policy bounds iterator admission with one unmatched
lookahead; a match returns immediately. V2 phase replay, exact workbench replay,
and candidate replay consume the same overall transition and duration budget.
Results are tested reproducers, without a global minimality claim.

The separate `@jagalite/statelessness/debug/session` export provides typed
single-owner simulation, checked turns, revision guards, breakpoints and private
snapshot-bound candidate tokens. Models need no codec. Callbacks receive cloned
values. Outputs describe effects and are never dispatched. Reducer errors do not
commit a turn; completed transitions, including rejected/ignored turns, increment
revision and sequence. Checker errors retain the completed transition; observer
errors occur after commit. Post predicates observe completed detached turns before state commit. Throwing
breakpoint callbacks leave the session terminal and a diagnostic, without a
rollback promise. Iterator callbacks cannot reenter
the session. Declared predicate policies do not require candidate enumeration.

These modules do not attach transports, host clocks, watchers, or process
debuggers when importing the basic engine. Duration controls consult the runtime
clock only during explicit bounded operations. Application callbacks are
cooperative: no preemption, OS resource restoration, hard RSS bound, remote
authentication, or new default is provided. Full N12 qualification remains open;
see the plan and archived Kyoto receipts for the exact tested scope.

Additional optional subpaths are `/debug/fork`, `/debug/inspect`,
`/debug/watches`, `/debug/diagnostic`, `/debug/protocol`, `/debug/live`,
`/debug/effects`, `/debug/metrics`, and `/debug/dashboard`. Import these from
`@jagalite/statelessness` only when the host opts into that facility.

The `telemetry_health` protocol command emits exactly the Rust
`append_telemetry_health` field list, in Rust order. Because JavaScript effect
handles are released only by explicit disposal (no destructor hook),
`telemetry.destructor_hook_losses` and `telemetry.active_effect_handles` are
reported as `unknown:explicit_disposal_only`, never as zero.
`telemetry.retained_detail_bytes` is a fixed logical estimate of retained detail
records, not a heap or RSS measurement; this disclosure is documented here and
in `EffectObserver.telemetryHealth().retainedBytesAreLogicalEstimates` rather
than sent as an extra wire field.

`DebugSession.recording` uses canonical checked tokens to capture initial and
turn checks once. It rejects caller-created `stateless.debug.*` provenance.
Verified forks retain accidental-mismatch FNV identities for parent/checkpoint
bytes, verify the selected prefix before decoding its checkpoint, and start
independent recording sessions. These identities do not authenticate content.
The supported package exports do not expose the internal branch factory symbol.
Typed stepping and candidate selection are separate: candidate tokens must still
belong to the current model input domain even with unrestricted environmental
injection. An application may supply explicit input equality/domain adapters.

Inspection requires explicit `Inspect` adapters and structural field/variant/
index/typed-map-key paths. Display projections are never model equality keys.
The native JavaScript logical retained-byte budget charges 128 bytes per node,
64 per child, and encoded metadata/payload bytes; it does not estimate JS object
headers or hard RSS. String UTF-8 counting consumes bounded work. Incomplete,
redacted, opaque, paginated, unstable-map and unavailable projections compare as
unknown before equality. Redacted fields do not evaluate a value adapter.

Watch registries belong to one ordered producer. They support bounded metadata
selectors, local path authorization, configuration revisions/generations,
effective sequence acknowledgements, schema/source/gap baselines, separate
inspection and delivery sampling, sequence/observation TTL, baseline eviction,
and bounded event rings with explicit loss health. Local acknowledgements do
not acknowledge a remote producer. Duplicate/stale observations do not evaluate
projections. Matching queries share one projection per boundary.

Watch configuration accounting uses logical 296-byte configuration and 32-byte
path-segment headers, plus UTF-8 selector text and equality scalar text. These
headers match the checked Rust fixture on the current 64-bit platform; Rust uses
native type sizes, which may differ by target or toolchain. The standard `f32`
and `f64` float kinds use fixed literals; other dynamic float type strings are
also charged. Configuration capture reads bounded declared fields rather than
cloning unknown caller properties. This accounting bounds the retained declared
configuration, not JavaScript object overhead or the caller's input allocation.

Scoped diagnostics keep producer acknowledgement/effective transitions separate
from desired capture revisions. All supplied scope selectors must match before
materializing a probe value. A selected value thunk is attempted once across
sinks even if it throws. Trigger evaluation updates baselines before delivery
sampling; above-u128 thresholds use exact bigint hysteresis/cooldown. Each sink
has independent record/byte limits, oldest/newest drop policy, health counters,
explicit draining and abandonment. Exporter failures remove the attempted record
and increment health; an exporter may already have performed partial I/O.

Use `hub.withTurn(..., scope => { ... })` to ensure an exception publishes staged
records with `incompleteTurn: true`. If using `beginTurn` directly, call `finish`
explicitly; JavaScript finalizers do not provide a deterministic scope exit.
A live scope owns the producer until finished. Probe capture performs no sink
I/O; explicit `drainTo` invokes the host exporter cooperatively.

`LiveController` detaches admitted input graphs with `structuredClone` by default.
For custom input classes, supply the fourth constructor argument `cloneInput` to
preserve the application's value semantics. Clone callbacks are synchronous host
code and cannot reenter controller mutation. Rejected arrivals return the original
host input. The host's `ownedBytes` estimate bounds logical queue accounting.
Authorized protocol output reads use the session model's optional
`encodeOutputInto` bounded sink (or legacy `encodeOutput` adapter); no output
decoder is needed. Legacy encoder source allocations remain host-owned.

A watch observation can supply `outputVariants` for all outputs from a
transition; an output selector matches any entry. The singular `outputVariant`
remains supported, and either field can satisfy a selector when both are supplied.
The collection must be a dense array of valid Unicode strings, with at most
4096 entries and 1 MiB of UTF-8 text; invalid or oversized observations fail
before the registry advances its boundary.

Watch failure metadata retains the first 16 property IDs, each truncated at a
UTF-8 boundary to 256 bytes. An observed event with truncated IDs is marked
partial when its projected value would otherwise be complete; explicit snapshots
retain their projection completeness. The first 16 supplied IDs must be valid
Unicode strings of at most 1 MiB in JavaScript string length; these host
admission limits are separate from Rust's typed string inputs.

Watch model, machine, and schema names are retained in events as UTF-8 prefixes
of at most 1024 bytes. Truncation marks an otherwise complete observation partial;
explicit snapshots preserve projection completeness. Full schema names are used
for identity checks. JavaScript metadata inputs must be valid Unicode strings
of at most 1,048,576 UTF-16 code units; this is a host admission limit.
