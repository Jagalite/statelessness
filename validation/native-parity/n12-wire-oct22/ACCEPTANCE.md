# N12 acceptance boundary for the Oct22 wire continuation

Base: `89f61d6666e2666fad65164dc5ab3e8f3e4e1fd5`, recovered runtime source
`f866f7552df77736297fec5bf515481730a23f4c`. This continuation uses only the new
`n12-wire-resume-oct22` checkout. The interrupted `t3-6b2942d1` and recovered
`t3-ccd33c8a` trees were inspected read-only and were clean at the base commit.
No uncommitted interrupted wire implementation was present to recover.

This ledger reviews the full optional acceptance proposal in
[`DEBUGGER-DIAGNOSTICS-AUDIT.md`](../../../../docs/native-parity/DEBUGGER-DIAGNOSTICS-AUDIT.md)
and the [historical contract matrix](../debugger-diagnostics/reference/contract-matrix.json).
The audit's original "surface absent" table describes its historical source
snapshot. The recovered optional packages now exist; their implementation and
earlier language-local tests do not establish current direct wire equivalence.

The new finite fixture's authority is public Rust `protocol.rs` and `session.rs`,
plus independently authored expectations in
[`SPEC.md`](../../../../scripts/native-parity/n12-wire/SPEC.md),
[`compare.py`](../../../../scripts/native-parity/n12-wire/compare.py), and
[`rust/expected-core.json`](../../../../scripts/native-parity/n12-wire/rust/expected-core.json).
Qualification status must be read from the final HANDOFF and exact receipt,
not inferred from the existence of these files.

| Acceptance family | This continuation's direct fixture | Still required for full optional acceptance |
| --- | --- | --- |
| Passive v1/v2 evidence | None | Zero callback viewing; structural versus semantic v1 admission; retained checkpoint and ordered boundary values; passed/nonexistent failure selection; persisted phase split and duplicate property IDs. |
| Verification and minimization | None | All replay mismatch flags and partial results; unchanged cursor/artifact; phase-verification/shared shrink budgets; cancellation before/during verification; policy provenance/omission; bounded candidate lookahead; retained-root causality. |
| Typed simulation | Initial state/check order; accepted, ignored and rejected returns; reducer error; state-check failure; exact callbacks, sequence/revision and final state; cancellation | No-codec sessions; initial checker failure; pre/post breakpoint bypass; checker/recorder/observer errors and precedence; callback panic/unwind boundaries; output retention isolation. |
| Candidate selection and verified forks | No candidate or fork comparison | Token/session/domain ownership, revalidation, candidate limits, exact-prefix verification, provenance and branch isolation. |
| Structural inspection/diff | UTF-8 strings are exercised in the protocol, not structural inspection | Typed field/variant/index/map paths; exact u128/i128; display schemas; redaction with zero evaluation; independent depth/work/value/byte/page budgets; unstable maps and incomplete Unknown comparisons. |
| Watches | Denied configuration with no watch attachment | Authorized concrete adapters; validated paths; configuration revisions; baseline reset; disable/re-enable; TTL/sampling/cooldown; stale observations/gaps/schema changes; ring loss and failure retention. |
| Scoped diagnostics | Denied configuration with no probe attachment | Disabled closure count zero; selected closure once across sinks; selector conjunction; producer acknowledgement; redaction before queueing; independent sink health/drop/loss; abandonment and exporter failure outside reducer. |
| Protocol core schemas | Complete status, snapshot, checks, outputs, step and cancel response envelopes/fields against actual Rust emissions | Remaining successful command families with actual native bindings, including inspection, candidates, watches, events, probes, exact trace export and telemetry. |
| Protocol identities/admission | Exact UTF-8/BOM/canonical-equivalence distinctions; u64 values above 2^53 and at maximum; malformed decode before retirement; request zero; discovery canonicalization; duplicate/conflict/evicted-ID behavior; snapshot eviction and stale handles | Exhaustion injection for epochs/revisions/handles; every mixed-error precedence; all typed path key forms and response byte/field limits. |
| Authority and revisions | Delivery denial before decoder/revision callbacks; exact-output authorization; read-only watch/probe/export denials; current-handle checks/outputs ignore envelope execution revision | Full independent authority combinations with attached services; separate watch/probe/telemetry revisions; exact export chunk boundaries; precommit budgets and reentrant callback ownership across every adapter. |
| Real process pipes | Fresh hosts; fragmented and coalesced messages; partial header/payload EOF; zero/oversized lengths; committed turn then transport failure; real closed-output write failure with actual final session read | Reconnect in the same surviving process; all partial prefixes; broader backpressure/bounded write failures; other transports/platforms. Host SIGPIPE policy is explicit, not a library-wide guarantee. |
| Live observation/control | None | One checked handoff without a second reducer/check pass; missing/duplicate sequence; freeze/loss; safe-point pause acknowledgement; controller-bound single-use delivery/dispatch permits; dispatch reservation versus confirmation; uncertainty and terminate-during-turn. |
| Effect lifecycle | None | Independent host lifecycle reference; explicit dispatch/publication/delivery/settlement; cancellation with late completion; future first-poll/abandonment; no telemetry-driven retry or result replacement. |
| Metrics/catalogs/windows | None | Injected-clock arithmetic; missing/reversed/cross-domain endpoints; zero versus unknown; production filtering; pause labels; gauge participation; catalog/series/detail/window bounds and losses; histogram overflow/low samples/delta compatibility. |
| Dashboards and causal joins | None | Payload-free bounded rows; null versus zero; completeness/truncation; exact report byte caps; run/machine/epoch/sequence/output joins; conflicts, before-checkpoint and missing identity. |
| Distribution and baseline | Fresh copied Go/Swift packages, Python wheel installation and TypeScript tarball installation are prepared for the direct hosts; full native regression suites are separate recorded stages | Registry publication, independent platform/toolchain matrices, browser transport, reproducible release packaging and measured performance/resource costs. |

The Rust consumer imports the actual public crates from a fresh frozen source
copy. Each native host imports its public optional package; it does not fabricate
responses or implement a second framing/parser layer. Expectations are not
regenerated from Rust output. Comparison permits only field ordering and a
validated process-local epoch mapping; exact strings, typed values, field
presence, errors, callback order and session outcomes remain observable.

All five hosts use the same finite model, with only delivery, exact-read and
cache-entry switches. This deliberately does not exercise every optional service
attachment. Language-local regression passes remain distinct from the four
native-to-Rust direct comparisons. No broad N12 closure, 144-cell qualification,
capability/profile/default promotion, parent integration, push, PR or release
is asserted by this ledger.
