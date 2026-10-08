# Modeling macros and runtime helpers

This is the first implementation of the [macro roadmap](Statelessness-Macro-Roadmap.md).
The source is usable and locally qualified; it has not been published as a new
release. Cross-platform release qualification runs in the existing verification
workflow. Passing these fixtures does not establish completeness of an application
model or correctness outside its declared bounds.

## Installation and compatibility

Use the first-party **companion package**, explicitly. The engine still has zero
third-party dependencies and no additional compiled package by default. An optional
registry dependency would compromise cold-offline core resolution before the macro
package is available in the registry. The companion avoids that dependency entirely.

```toml
[dependencies]
stateless = { package = "statelessness", path = "/path/to/stateless" }
statelessness-macros = { path = "/path/to/stateless/crates/statelessness-macros" }
```

```rust,ignore
use statelessness_macros::{model, input_domain, TraceEncode, TraceDecode};
```

No `macros` feature is necessary. `cargo build --offline` at the repository root
builds only the engine. `cargo test --offline --workspace` includes macro fixtures.
Both packages use Rust 1.90 or later. Generated code references
`stateless::modeling::MACRO_API_V1`; older runtimes without that protocol fail to
compile rather than silently selecting another implementation. Breaking protocol
changes require a new token and coordinated release notes. Public codec changes
still require the application's codec version to change.

For renamed imports use `#[model(crate = "::engine", ...)]`,
`#[trace(crate = "::engine")]`, and the domain prefix `crate = "::engine";`.
Absolute standard-library paths are used for generated types. Callbacks remain
ordinary inherent methods; reducers are invoked exactly once by `Model::step`.
The existing `WithOracle::observe_transition` never invokes a reducer again.

Generated model metadata appends content-derived macro and runtime build stamps. The macro
package build script fingerprints its own source, manifest, compiler, and build
settings, including from extracted packages. The application must still fingerprint
its own code and configuration. A handwritten adapter using only derives can append
`statelessness_macros::macro_build_id!()` and `stateless::modeling::RUNTIME_BUILD_ID`
to its build identity. This stamp is a
reproducibility label, not a cryptographic authenticity guarantee. Honest generated
and handwritten builds have different identities; compare semantics separately.

## Adapter surface

A complete small example is [the qualification counter](../crates/macro-qualification/src/lib.rs).
The [job lifecycle](../crates/macro-qualification/src/jobs.rs) models application-owned
jobs, cancellation, duplicate/delayed delivery, and required cleanup effects.
The original public [handwritten starter](../examples/application.rs) remains intact.

`#[model(state = State, input = Input, output = Output)]` applies to an inherent impl.
Required roles are `#[stateless(metadata)]`, `#[stateless(initial)]`, and
`#[stateless(step)]`. Signatures match the existing `Model` trait. Optional roles:

| Role | Callback result / arguments after `&self` |
|---|---|
| `state_check(id = "literal")` | `(&State) -> Result<CheckStatus, ModelError>` |
| `transition_check(id = "literal")` | `(&State, &Input, &TransitionRef<State, Output>) -> Result<CheckStatus, ModelError>` |
| `state_checks` | `(&State, &mut CheckSink) -> Result<(), ModelError>` |
| `transition_checks` | `(&State, &Input, &TransitionRef<State, Output>, &mut CheckSink) -> Result<(), ModelError>` |
| `inputs` | `(&State) -> Result<Vec<Input>, ModelError>`; emits `Enumerate` |
| `estimated_state_bytes` | `(&State) -> Option<usize>`; unknown remains the default |

Literal properties run in declaration order within each phase. A low-level batched
hook runs after that phase's literal properties. Failures and skips do not stop
later checks; callback errors return immediately and preserve already appended
checks in `CheckSink`. Vector methods delegate to the same append implementation.
Duplicate literal IDs across either phase and duplicate roles are errors.
`STATELESS_PROPERTIES` lists registered literal `(phase, id)` pairs; batched hooks
own their dynamic IDs and are not included in that static catalog.
A model without registered or batched checks must explicitly select `unchecked`.

The `codec` option emits only `ModelCodec` delegation. State and input need both
value traits; output needs encoding only. `decode_limits = expression` optionally
supplies reader limits (otherwise `DecodeLimits::default()`). For example the
expression may be `self.decode_limits()`. No generator is emitted implicitly;
wrap an enumerated model in the existing `Auto` or implement `Generate` yourself.

A handwritten equivalent is an ordinary `impl Model` forwarding `step` to the same
reducer, and a `check_state_into` that pushes `Check { id, status: callback()? }`.
The generated vector wrapper constructs a sink and calls that append method.
`stateless::check!("id", predicate, "details {}", value)` evaluates the predicate
once and formats details only on failure, via `modeling::check_lazy`.

Supported adapter declarations have ordinary methods with `&self` callback receivers,
qualified types, generic impl parameters and where clauses. Method-level generic
callbacks, associated items, conditional methods, trait impls, and exotic method
syntax are not supported. Normal method documentation and bodies are preserved.
Rust checks callback argument and return types. Macro diagnostics check role wiring.
Forwarders and annotated callbacks receive inline hints; an explicit callback
inline policy is preserved.

## Environmental domains

```rust,ignore
input_domain! {
    pub fn deliveries(model: &RequestModel, state: &State) -> Input {
        assumptions = "Two generations; cancellation does not withdraw completion";
        limit = 4;
        variants {
            Input::Start => "bounded generation",
            Input::Cancel => "active cancellation",
            Input::Complete(_) => "all pending generations, including stale ones"
        };
        one Input::Start if state.generation < model.max_generation;
        one Input::Cancel if state.active;
        many Input::Complete(g) for g in state.pending.iter().copied();
    }
}
```

The function returns `Result<modeling::Domain<Input>, ModelError>`. Its handwritten
equivalent creates a `Domain` with a descriptor, then calls `push`/`extend` in the
same order. Membership and indexed selection read those exact entries. A `variants`
block emits an exhaustive Rust match; each pattern requires a nonempty reason.
An excluded variant must still be accounted for with its exclusion reason. An
external non-exhaustive enum needs an explicit fallback pattern and reason. This
accounts for variants, **not completeness of their payload domains**.

`one`, guarded `one`, and `many ... for ... in ...` support ordinary iterators,
bounded ranges, and state-derived sequences. Multiple segments form ordered sums.
Use `Domain::product` for a checked, bounded Cartesian product (left-major order).
Duplicates remain entries and therefore carry sampling weight. The entry limit
is checked before growth; macro enumeration propagates the error instead of
returning an oversized domain's prefix. Iterator work and iterator allocations
remain application-owned. Use deterministic, bounded iterators.

`Domain::sample_indexed` is explicitly indexed-v1: `Rng::index(entries.len())`.
It costs constant time **after materialization** and has a different seed contract
from `Auto` reservoir sampling. `Auto` itself is unchanged. No automatic structural
shrinking of correlation IDs is performed. `Auto::is_enabled` checks the environmental
domain when validating shrink candidates.

Put `domain.descriptor().parameters()` in a trace's `RunConfig.parameters`, and
include configuration bounds in model identity. Descriptor keys are application
keys (`model.domain.*`), not reserved engine keys. There is no hidden global registry.

## Value wire contract

Encoding writes directly into `EncodeBuffer`; `trace_bytes(maximum)` delegates to
that same implementation. Top-level `from_trace(bytes, limits)` rejects trailing
bytes. Nested decoding uses one shared `Decoder` and its aggregate budgets. Reader errors
are sticky: catching a failed read, nested value, or budget check cannot turn the
top-level decode into success.

| Value | Bytes |
|---|---|
| Fixed integers u8–u128 / i8–i128 | Fixed width, little endian |
| bool | Exactly 0 or 1 |
| unit | No bytes |
| tuples (1–4 members), fixed arrays | Ordered members, no count |
| String | u32 byte count then strict UTF-8 |
| Vec | u32 element count then ordered elements |
| Option | 0 for None, 1 followed by the value for Some |
| struct | Declaration-order fields |
| enum | Required explicit `#[trace(tag = N)]`, default u8 tag |

`#[trace(tag_type = "u16")]` or `"u32"` changes enum tag width. Tags are decimal,
unique and range-checked. `#[trace(length = "u8")]` (also u16/u32) explicitly
changes a vector or string field's prefix through `LengthEncode`/`LengthDecode`
traits, without guessing semantics from its type spelling. Length overflow is an
error. `#[trace(with = "path::adapter")]` calls `adapter::encode(&value, out)` and
`adapter::decode(reader)`; handwritten traits are also supported. Adapters are
trusted callbacks and must honor the shared limits.

Named, tuple, and unit structs; unit/tuple/named enum variants; generic parameters,
where bounds, raw identifiers, arrays and qualified field types are covered by
fixtures. Decode is owned-only. Native-width integers, floats, pointers, borrowed
references, unordered collections and shared mutable handles have no default
codec. `skip`, default-value attributes, and implicit enum discriminants are errors.
String-valued macro options use ordinary unescaped string literals.

Rust removes disabled fields/variants before derives run. The codec encodes the
selected declaration. Changing feature/cfg-selected fields is a format change;
record that configuration in application provenance and codec version. Method cfg
inside `model` is rejected. No claim is made that macros can inspect fields already
removed by the compiler. Complex higher-ranked and associated-const syntax is not
part of the qualification envelope.

Defaults: 16 MiB input bytes, 32 MiB requested aggregate collection allocation,
1,000,000 aggregate collection elements, depth 64 and 2,000,000 decode operations.
Zero-sized elements still consume element/work budget. Array decoding uses a
bounded temporary vector; allocation accounting includes that temporary. Stock
collections use fallible reservations. These limits do not bound allocator rounding,
application callback allocations, stack frame size, or total process memory.
Property-failing but structurally valid snapshots remain decodable.

`TraceEncode::trace_schema()` is diagnostic declaration text, and `trace_variant()`
gives enum labels. Neither defines exact equality, compatibility, reachability or
search pruning. Reordering fields or changing tags requires an explicit format
migration. Generated memory estimates and projection-based pruning are not provided.

## Lifecycle templates and composition

`lifecycle::LifecycleMonitor<Spec>` implements `Oracle` and, for encodable keys,
`OracleCodec`. The independent classifier maps delivered inputs/dispositions and
observed outputs to `Begin`, `Cancel`, `Settle`, `Publish`, `Acquire`, `Release`, and
explicit `Tick` events. It does not inspect the application's next state.
Classifiers must specify rejected/ignored delivery handling independently.

`Begin { key, deadline }` requires exactly one settlement by that logical deadline;
missing output is detected even if no forbidden effect appears. A late settlement,
duplicate settlement, stale/premature publication, release without acquisition,
early release, or active resource reuse fails. Cancellation retires publication and
settlement and discharges the response obligation. Completed keys remain retained;
include generation in keys and never reuse an operation key. Resources may be
reacquired after release. Equal-time settlement or cancellation is allowed; advancing past the
deadline records a failure that a later cancellation cannot erase. Restored histories
are checked for overdue obligations even without a recorded violation event. Input events precede output events within one observation.

History caps return verification errors; they never evict obligations and report
success. Pending finite-run obligations are `Skipped`, not evidence of unbounded
liveness. Histories persist through value codecs and can be explicitly attached via
`WithOracle::attach`. `observe_transition` retains the real application result if
monitor advancement fails. Each template detects only the events its independent
classifier specifies; it does not infer a correct specification from the reducer.

`composition::Pair<L, R, Wiring>` removes repetitive routing without a second DSL.
`Input::Local(Address::Left/Right(input))` executes one child; `Input::Deliver(index)`
removes and delivers exactly one pending message. Outputs route to new queued
messages through the explicit `Wiring` callback. Queue bounds return errors, and
all queued messages belong to exact state and persistence. Nothing auto-drains.
Local checks use `left::` and `right::` prefixes; required global checks use `global::`.
Duplicate assembled IDs produce an error, including collisions with earlier checks
in the same sink. Wiring can also append global transition checks. The routing policy and queue bound,
both child identities, and embedded oracle histories remain part of identity/state.

The composite codec uses the value-codec contract (not child ModelCodec bytes);
its codec version is separate. Child value types need value traits, or the
application can use its own aggregate state and handwritten adapter. Child transition
checks currently clone selected output values; this is a documented cost, not a
claim of zero-allocation composition. The checked two-machine reference is in
`tests/composition.rs`, including cancellation, different delivery orders, and replay.

## Diagnostics and qualification

| Identifier family | Meaning |
|---|---|
| SM000–SM006 | Token/options syntax and expansion protocol |
| SM100–SM115 | Codec shape, tags, field policy and duplicate options |
| SM200–SM212 | Adapter options, roles, receivers and property registry |
| SM300–SM306 | Domain syntax, coverage and explicit descriptors |

Rust diagnostics handle unsupported trait bounds, malformed callback types,
conflicting impls, and non-exhaustive variant accounting. Representative wiring
errors retain declaration spans. Stable diagnostic identifiers, rather than full
compiler punctuation, are asserted by `scripts/check-macros.py`.

Run `python3 scripts/check-macros.py --allow-dirty --evidence /tmp/macro-evidence.json`
for cold-offline package consumers and compile fixtures. Omit `--allow-dirty` in a
clean checkout. It records compiler, revision, per-source SHA-256, commands and
outcomes. Package checks do not contact the registry. Runtime fixtures compare
handwritten and generated states/inputs/checks/outputs, fault paths and golden
bytes. `jobs record NEW_TRACE` and `jobs replay TRACE` provide separate-process replay.

See [implementation evidence and remaining release gates](MACRO-STATUS.md) before
making a release claim.
