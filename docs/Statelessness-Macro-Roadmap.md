# Statelessness: Macro Design and Implementation Roadmap

**Status:** Proposed implementation plan; no macro implementation is claimed complete.
**Date:** October 8, 2026
**Repository baseline:** `Jagalite/statelessness`, commit `4628869ea232fe272d3f4982897463315ece3397`
**Decision:** Invest in a maintained, first-party procedural-macro layer over the existing engine.
**First supported release:** Milestones M0–M4. Advanced capabilities follow independently.

## Executive recommendation

Build macros as a real product surface, not merely a few temporary shortcuts. Use an attribute macro for model adapters and property registration, a domain declaration for environment modeling, and derives for structural trace codecs. Keep ordinary Rust types, application reducers, and handwritten trait implementations fully supported.

Willingness to maintain macros removes the main reason to restrict the design to `macro_rules!`. It does not remove the need for explicit modeling assumptions or automatically authorize third-party dependencies. This plan therefore assumes a first-party procedural-macro crate using Rust's compiler-provided `proc_macro` interface, preserving the repository's zero-third-party-dependency policy. A later parser-library decision can change the internals without changing the intended user-facing design. [R5, E1]

**Build the conveniences that make a correct model easier to express; do not infer away the differences between implementation, environment, and correctness.**

| Delivery boundary | Included | What it establishes |
|---|---|---|
| First supported release, M0–M4 | Model/check adapters, explicit domains, strict codecs, diagnostics, qualification | A maintained modeling layer with tested expansion and behavioral equivalence on qualified fixtures |
| Advanced, M5–M6 | Independent monitor templates and composition wiring | Reusable verification and multi-machine ergonomics under explicit contracts |
| Experimental, M7 | Optional transition-table authoring and deeper optimizations | Feasibility evidence only; not a prerequisite for the first release |

## 1. Product decisions and boundaries

### 1.1 Preserve the application-owned architecture

Generated code must implement the existing `Model`, `Enumerate`, `Generate`, `ModelCodec`, `Oracle`, and `OracleCodec` contracts where those capabilities are explicitly requested. The current API already separates basic execution from exploration and persistence. Preserve that separation rather than introducing a universal derive that imposes every capability. [R1, R3]

State, input, and output remain ordinary application types. Continue using standard Rust derives such as `Clone`, `Debug`, `PartialEq`, `Eq`, and, where required, `Hash`. A state type does not become a model merely by deriving a structural helper. A model adapter still identifies the reducer, initialization, metadata, checks, and optional environmental domain.

Every macro-enabled capability must have a documented handwritten equivalent. Mixed adoption is supported: an application may use a generated codec while retaining handwritten generation, or generated property registration around an existing reducer. Do not emit competing trait implementations for capabilities the application did not select.

### 1.2 Treat generated code as part of the trusted checker

A mistake in a domain generator, property registry, or codec can hide a real defect. Review and test macro implementations as verification infrastructure, not only as developer convenience. Maintain independent handwritten comparators and deliberately faulty fixtures.

Generated code must call the actual application transition exactly once. It must not execute effects, create hidden clocks or random sources, or introduce mutable global registration. Runtime observation wraps an already executed transition; it must not rerun the reducer. [R1, R3]

### 1.3 Keep the initial scope focused

The first release should make models easier to author and harder to wire incorrectly. It should not change the search engine's scheduling, exact-state equivalence, trace framing, CLI loading model, or native/browser ABI merely to support nicer syntax.

A full state-machine language is a separate optional product direction. Automatic purity proofs, inferred independent oracles, and automatic state-space reduction are not promises of the initial macro layer.

## 2. Architecture and maintenance strategy

### 2.1 Recommended package structure

Keep reusable runtime helpers in the existing dependency-free library and expansion code in a separate first-party package. Rust requires procedural macros to live in a `proc-macro` crate. [E1]

```text
statelessness / Rust library: stateless
  existing engine, traits, recording and replay
  ordinary domain, codec and registration helpers
  optional macro re-exports

crates/statelessness-macros/
  token parsing -> validated description -> Rust generation
  derives, attribute macros and domain declarations

qualification fixtures
  handwritten references + generated implementations
  compile-pass/fail consumers + behavioral comparisons
```

Use one optional feature, provisionally `macros`, for the first supported surface. Do not enable it by default. Ordinary users should not compile the procedural-macro crate unless they opt in. Avoid a feature for every small derive until a real dependency or build-cost difference justifies it. Cargo supports optional dependencies and feature-gated activation. [E2]

The macro crate should not depend on the engine at expansion time. It should generate references to engine traits. This avoids a normal dependency cycle when the engine re-exports the macros. Integration fixtures should exercise the actual engine and macro packages together.

### 2.2 Use a maintainable internal pipeline

Separate token handling, validation, and emission. Use a small internal representation for fields, variants, callback roles, properties, domain segments, and codec choices. Preserve source spans so errors point at the user's declaration.

Do not discover semantics by searching the filesystem, evaluating arbitrary user code during compilation, or comparing a type's spelling with strings such as `Vec`. Generate trait calls and bounds; let Rust resolve the actual types. The macro need not understand the bodies of reducers or predicates to forward them intact.

Start with a documented syntax envelope: named, tuple, and unit structs; ordinary enum variants; preserved generic parameters and where clauses; raw identifiers; and qualified paths. Qualify supported conditional-compilation patterns explicitly. Reject unsupported forms with actionable errors rather than accepting them partially. Owned decoding is the first codec target; borrowed decoding is not implied by preserving lifetime syntax.

Under the current policy, implement the parser with first-party code over `proc_macro`. Do not build a general Rust parser unnecessarily. If parser upkeep later dominates the work, adopting a maintained parsing library such as Syn is a reasonable separately recorded dependency-policy change, not a reason to redesign the public macros. [E1, E3]

### 2.3 Packaging and offline operation are release requirements

Retain the distinction between no third-party dependencies, no extra compiled packages by default, and completely cold offline resolution. They are different claims. Cargo's lockfile resolution considers optional dependencies; a disabled feature alone is not sufficient evidence that a packaged consumer can resolve without registry access. [E4]

M0 must test a clean checkout and extracted packages, not only a warm developer workspace. Qualify the optional-re-export design with the documented offline distribution. If it cannot preserve the required core-only offline experience, ship the macro package as an explicitly imported companion rather than weakening that promise silently. Keep both imports compatible with the same generated trait implementations.

Provide an explicit crate-path override for renamed dependencies and use absolute paths for generated standard-library references. Test the documented `stateless` library name rather than assuming the package name `statelessness` is the import path. [R5, E1]

## 3. Recommended user-facing surface

All syntax in this section is illustrative. Final syntax must be locked by compile-tested examples in M0–M3 before it is documented as supported.

### 3.1 Model adapter and property registration

Recommend `#[stateless::model(...)]` on an application-owned adapter's inherent `impl` block. The attribute assigns explicit roles to ordinary methods and emits trait implementations; it does not replace the methods' behavior.

```rust
#[stateless::model(state = State, input = Input, output = Output)]
impl RequestModel {
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata { application_metadata() }

    #[stateless(initial)]
    fn initial(&self) -> Result<State, ModelError> { app::initial() }

    #[stateless(step)]
    fn transition(&self, s: &State, i: &Input)
        -> Result<Transition<State, Output>, ModelError>
    {
        app::reduce(s, i)
    }

    #[stateless(state_check(id = "request.ready_requires_active"))]
    fn readiness(&self, s: &State) -> Result<CheckStatus, ModelError> {
        Ok(if !s.ready || s.active {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed("retired request became ready".into())
        })
    }
}
```

Generate both vector-returning compatibility callbacks and reusable-buffer callbacks from one implementation. Registered property methods should return `Result<CheckStatus, ModelError>`; the registry supplies the stable ID. An explicit low-level hook may append existing `Check` values for applications with dynamic or batched properties.

Property order is declaration order within each check phase. IDs are explicit literals, not derived from function names. Reject duplicate literal IDs within one declaration, duplicate callback roles, and malformed attributes. Validate collisions across composed registries when they are assembled. Do not stop after the first failed property unless the underlying API explicitly requests that behavior.

Also provide a small `check!` helper for handwritten implementations. It should evaluate its predicate once, format details only on failure, and return an ordinary `Check`. An ordinary lazy helper function underneath it is preferable to duplicating the logic inside the expander.

### 3.2 A shared, explicitly environmental input domain

Recommend `input_domain!` over a reusable ordinary-Rust domain abstraction. Define it independently of the input enum so the same events can participate in different bounds, configurations, and fault profiles.

```rust
stateless::input_domain! {
    RequestDomain(model: &RequestModel, state: &State) -> Input {
        one Input::Start if state.generation < model.max_generation;
        one Input::Cancel if state.active;
        many Input::Complete(g)
            for g in state.pending.iter().copied();
    }
}
```

The shared definition supplies stable enumeration and membership. Initially, use the existing `Auto` path for generic sampling where appropriate. Add a separate indexed capability for domains with trustworthy cardinality and direct selection; do not replace `Auto`'s existing behavior invisibly. Current `Auto` performs reservoir sampling and has specific candidate-limit and RNG-error semantics. [R4]

**Deliverable is not the same as accepted.** Stale completions, rejected commands, duplicate deliveries, and other faults must remain expressible when the environment can produce them. Do not derive the domain from the reducer's acceptance guard. The request fixture deliberately allows pending completions after cancellation. [R2]

Require explicit accounting for input variants using generated exhaustive matches where possible. An exclusion must state a reason. For external non-exhaustive enums, require a documented explicit fallback strategy or reject exhaustive-domain derivation. Variant coverage does not establish completeness of payload values.

Domains must declare assumptions and bounds in reportable metadata. Preserve deterministic order and duplicate-entry weighting. Check cardinality arithmetic, reject oversized domains rather than sample an undisclosed prefix, and distinguish empty domains from generator errors. A structural shrinker may propose simplifications, but state-aware causal validation remains mandatory; do not automatically shrink correlation IDs as ordinary integers.

### 3.3 Structural trace derives and a thin codec adapter

Recommend separate `TraceEncode` and `TraceDecode` derives on values, with a model adapter that delegates to those traits. Do not force output decoding when the model interface only requires output encoding. [R1]

```rust
#[derive(Clone, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
struct State {
    generation: u8,
    active: bool,
    ready: bool,
    #[trace(length = "u8")]
    pending: Vec<u8>,
}

#[derive(Clone, PartialEq, Eq, TraceEncode, TraceDecode)]
#[trace(tag_type = "u8")]
enum Input {
    #[trace(tag = 0)] Start,
    #[trace(tag = 1)] Cancel,
    #[trace(tag = 2)] Complete(u8),
}
```

Specify the wire contract before implementing the derives. Initial defaults should use fixed-width integers with explicit byte order, strict booleans, explicit enum tags, declaration-order struct fields, and documented length prefixes. Allow explicit width overrides to preserve existing bytes. Adding or reordering fields is not automatically backward compatible.

Write directly into `EncodeBuffer`. Vector-returning methods delegate to the same implementation. Add a bounded decoder with aggregate allocation, element-count, nesting, and work limits. Top-level decoding rejects trailing bytes; nested values consume the same bounded reader correctly. Byte limits alone do not bound work on collections of zero-sized elements.

Support a small practical owned-type set first: primitive integers and booleans, tuples, arrays, strings, vectors, options, and qualified user-defined types. Other types use explicit adapters or handwritten traits. Unordered collections, pointers, shared mutable handles, native-width integers, and floating-point semantics need deliberate policies rather than implicit acceptance.

Do not offer a general `skip` or automatic-default attribute in the first version. Do not enforce application invariants inside decoding: a well-formed snapshot of a failing state must still be replayable. The fixture's semantic pending limit and its wire length representation are separate constraints. [R2]

### 3.4 Diagnostics, not a second state representation

Useful supporting features include stable variant labels, property catalogs, codec schema descriptions, and bounded field-level differences. Start with descriptors already needed for validation and diagnostics; add a public structural-diff derive only when real failure reports benefit.

Never use a diagnostic or guidance projection as the definition of exact equality or persistence. Describing fewer fields for display does not establish that the omitted fields are irrelevant to future behavior. Generated memory estimates must remain labeled estimates and return unknown where ownership cannot be accounted for reliably.

## 4. Contracts that every milestone must preserve

| Concern | Required contract |
|---|---|
| Application execution | The real transition executes once; effects remain data; macro code does not create hidden runtime behavior. |
| Observations | Preserve state, ordered outputs, disposition, property IDs, property order, and Passed/Failed/Skipped/error distinctions. |
| Environment | Inputs describe possible external delivery, not only successful reducer paths; exclusions and bounds remain visible. |
| Exact state | Retain every behaviorally relevant field, pending event, and verification-history component. No automatic projection-based pruning. |
| Persistence | Canonical, complete, bounded encoding/decoding; malformed bytes fail; property-failing snapshots remain representable. |
| Identity | Keep model semantics, properties, codec format, generator algorithm, and actual build provenance distinguishable. |
| Adoption | Handwritten implementations remain usable; generate only requested capabilities; preserve existing trait entry points. |
| Claims | Passing bounded checks establishes only the supplied properties within the declared model and bounds. |

### 4.1 Include the macro implementation in build provenance

The current root `build.rs` fingerprints `src`, the manifest, its own source, compiler version, and selected build settings. A new sibling macro crate is not automatically included by that file traversal. [R6]

Give the macro package its own content-derived build stamp and include it in generated-adapter provenance. Combine that with the application-supplied build identity; a macro cannot generally discover all source and dependencies that determine an application's behavior. Do not rely on a package version alone, or on reading sibling source directories that will not exist inside a published package.

Treat this as a reproducibility label, not a cryptographic authenticity guarantee. Semantic changes still require the appropriate version decision. A structural schema fingerprint cannot detect a changed reducer in an external function.

### 4.2 Domain semantics must survive build-mismatch overrides

Domain bounds and fault profiles change the modeled system. Do not store their identity only in a build string that replay may explicitly disregard. M0 should specify a canonical, versioned way to include semantic identity using the existing metadata contract, or propose a separately reviewed format extension. Do not quietly add fields to the trace format.

Changing sampling algorithms may change same-seed fuzz sequences without changing concrete-input replay. Record and test those compatibility axes separately. Existing `Auto` remains a stable baseline; a new indexed sampler gets a distinct declared algorithm identity. [R1, R4]

## 5. Delivery milestones

Milestones are ordered by dependency and evidence, not calendar estimates. All are proposed and uncompleted in this document. M0–M4 define the first supported release; M5–M7 are not required to ship it.

| ID | Deliverable | Depends on | Exit evidence |
|---|---|---|---|
| M0 | Architecture, grammar, provenance, fixture harness | None | Compile-tested design spikes and documented package/offline contracts |
| M1 | Model adapter and check registration | M0 | Generated and handwritten observations agree |
| M2 | Shared input-domain machinery | M0; M1 for integration | Enumeration, membership, sampling, and shrink contracts qualified |
| M3 | Bounded value codecs and derives | M0; M1 for integration | Independent golden bytes, malformed-input tests, and replay checks |
| M4 | First supported macro release | M1, M2, M3 | Cross-platform consumers, regression evidence, documentation, measurements |
| M5 | Independent lifecycle monitors | M4 | Missing, duplicate, stale, and overdue behavior detected |
| M6 | Explicit multi-machine composition | M4, M5 | Handwritten and generated composed systems agree |
| M7 | Optional authoring and optimization experiments | Separate review after M4 | Bounded experiment with an explicit go/no-go decision |

### M0 — Establish the macro platform and qualification baseline

**Deliverables.** Create the first-party macro package, its parser/validation/emission modules, and a written macro-to-runtime compatibility contract. Define supported syntax, crate-path overrides, feature activation, package layout, provenance, and metadata responsibilities. Build minimal real Rust spikes for the impl attribute and a structural derive before freezing their spelling.

Create a dependency-free compile-fixture driver and handwritten reference models. Keep at least one public model completely handwritten. Add an expansion inspection artifact or test harness that makes representative generated code reviewable without making expansion text a public stable API.

**Acceptance tests.** Compile pass/fail consumers on Rust 1.90 and stable, including renamed imports and a minimal generic type. Test clean checkout and packaged-consumer resolution with the advertised offline setup. Verify that core-only builds do not compile the macro package, that expansion is deterministic for the same supported input/build, and that incompatible macro/runtime versions fail clearly.

**Done when.** An implementation contributor can add a macro without inventing new parsing, diagnostics, provenance, or fixture conventions. No production reducer has changed.

### M1 — Ship thin adapters and ordered property registration

**Deliverables.** Implement the model impl attribute, `check!`, property registration, and explicit callback delegation. Add opt-in support for the existing estimated-state-size hook rather than deriving inaccurate memory accounting. Generate reusable-buffer and compatibility methods from one source.

Create macro-backed counter and request examples alongside their handwritten counterparts. Allow a no-check adapter only through a visible explicit declaration or documented configuration; do not accidentally present an unverified model as checked.

**Acceptance tests.** Compare initialization, successors, ordered outputs, dispositions, check IDs/order/statuses, and errors. Exercise failing and skipped checks, checker errors after partial output, duplicate IDs, malformed signatures, and callbacks with expensive failure details. Use a counting test double to verify exactly one application transition per step or observation path. Test that renaming a property method does not change its explicit ID.

**Done when.** Users can adopt generated adapter/check plumbing without moving their reducer or changing its behavior. Existing checks, shrinking, and replay continue to work through ordinary engine traits.

### M2 — Unify enumeration, generation, and causal membership

**Deliverables.** Implement ordinary domain primitives first, then `input_domain!`. Cover singletons, bounded ranges, state-derived sequences, sums, and modest Cartesian products. Make enumeration order, duplicates, exclusions, bounds, and error behavior explicit. Integrate existing `Auto`; add indexed sampling only as an independently selected and versioned capability.

Provide an exhaustive variant-accounting diagnostic and reportable domain descriptors. Keep custom handwritten `Generate` available. Do not implement blanket trait relationships that conflict with application-defined generators.

**Acceptance tests.** Differentially compare input sequences and membership against independent references across every state of small bounded models. Cover empty domains, non-contiguous IDs, duplicate entries, cardinality overflow, limits, delayed results, rejected/ignored inputs, and causal shrinking. Preserve the counterexample `Start -> Cancel -> Complete(old_generation)` when the domain permits stale completion. Test index-to-entry equivalence and duplicate weighting structurally; statistical sampling checks are supplementary, not the main proof.

**Done when.** Enumeration and membership cannot drift through duplicated generated rules, and sampling has a documented distribution and reproducibility contract. No failure disappears merely because an acceptance guard was reused as the environment.

### M3 — Deliver canonical codecs and structural derives

**Deliverables.** Specify and implement value-level `TraceEncode`/`TraceDecode`, a bounded reader, a small owned-type support set, and explicit custom-adapter hooks. Then implement derives and the model codec adapter. Add schema descriptors, explicit wire tags, and codec-version guidance.

Preserve the fixture's existing bytes through explicit attributes where feasible. Any intentional byte-format change needs a compatibility decision rather than being hidden as a refactor.

**Acceptance tests.** Compare generated codecs with independent golden cases and handwritten codecs, not only with each other. Test exact state/input round trips, output bytes, unknown tags, invalid booleans/UTF-8, truncation, trailing data, overflowing lengths, aggregate allocation, excessive depth, and zero-sized-element work limits. Retain snapshots that violate application properties. Exercise encoding limits before growth and decoder allocation failures without converting errors into successful partial values.

**Done when.** A generated-codec model can record, persist, read, and replay qualified traces, including failing checkpoints. Bounded resource guarantees are documented precisely; callback allocations are not represented as a hard process-memory bound.

### M4 — Qualify and release the first supported macro layer

**Deliverables.** Publish a capability matrix, migration guide, handwritten/generated examples, diagnostics catalog, and macro/runtime version policy. Extend existing CI and packaged-consumer checks. The current verification workflow already targets Rust 1.90 and stable across Linux, macOS, and Windows; expand that actual matrix rather than creating an unrelated qualification claim. [R7]

Add at least one realistic application-owned reducer adapter beyond the tiny counter. Keep a reusable evidence bundle containing the source revision, compiler, commands, configurations, summaries, and important golden outputs. Include preserved native/browser binding smoke checks so macro packaging does not disturb unrelated distributions.

**Acceptance tests.** Run exact enumeration, fuzzing, shrinking, replay, guided fuzzing, and campaigns where the selected model supports them. Check search termination and skipped-check reporting, not only the absence of failures. Exercise feature combinations, renamed consumers, separate-process replay, package extraction, macro/runtime incompatibility, and build-mismatch policy. Measure performance against both simple and optimized handwritten baselines.

**Done when.** The first release has no unexplained semantic discrepancies on its qualification corpus, a clear supported syntax/type matrix, and reproducible evidence. No claim of general model completeness, purity proof, or application speedup is made from those tests alone.

### M5 — Add independently specified lifecycle monitor templates

**Deliverables.** Start with a small catalog: at-most-once settlement, no stale publication, resource acquisition/release, and bounded response obligations. Generate ordinary oracle history and bookkeeping from explicit independent event classifiers and expectations. Reuse M3 for history persistence.

Decide correlation keys, generation reuse, invalid deliveries, cancellation, rejected/ignored input handling, and bounded history retention for every template. Dropping retained obligations at a cap must surface a verification gap or error, never a pass.

**Acceptance tests.** Inject missing required outputs, duplicated settlements, stale publication, early release, resource reuse, and overdue obligations. Demonstrate that missing output is detected even when no forbidden output was emitted. Verify oracle advancement on every observed transition and correct preservation of the application's result when observation fails. Test saved-history attachment and replay; do not reconstruct unknown history from current application state. [R3]

**Done when.** Each template has a specification, independent faulty examples, history limits, and a clear statement of what it cannot establish. Finite runs with pending obligations must not be presented as proof of unbounded eventuality.

### M6 — Generate composition wiring without hiding schedules

**Deliverables.** First write a qualified two-machine example with explicit addressed inputs, pending messages, resource ownership, and local/global checks. Extract repeated routing into a composition helper only after the handwritten step boundaries are settled. Include child model identities and histories in the composite identity and state.

Separate one-machine execution from message delivery. Never introduce automatic queue draining or a scheduler chosen by the macro. Preserve explicit cross-machine invariants and a path for applications with their own aggregate state types.

**Acceptance tests.** Compare generated and handwritten composed models under the same ordering and bounds. Exercise cross-machine cancellation, delayed delivery, ownership conflicts, errors, different delivery orders, namespaced check collisions, and replay of the aggregate snapshot. Confirm that queues and monitor histories affect equality when they affect future behavior.

**Done when.** The helper removes routing boilerplate without reducing modeled interleavings or changing atomicity. It does not claim that global correctness follows from local checks alone.

### M7 — Evaluate advanced authoring and optimization separately

**Deliverables.** Consider an optional transition-table or statechart declaration that generates the production reducer itself, typed dispatch, adapters, and a structural overview. Do not generate a second approximation of an existing reducer and call it equivalent without validation.

Treat structural shrinkers, incremental checks, symmetry reduction, and partial-order reduction as separate experiments. Read/write annotations may begin as diagnostics; they must not prune exact search until the relevant semantic conditions and property preservation are established.

**Acceptance tests.** Compare against a handwritten application of the same behavior, measure authoring/build/runtime costs, and identify the supported language subset. A generated diagram is not a reachable-state proof. Every optimization needs a clearly scoped correctness argument and adversarial tests in addition to favorable benchmarks.

**Decision gate.** Continue only when a demonstrated use case justifies a separate maintained surface. M7 is not a reason to delay M0–M4.

## 6. Cross-cutting qualification and measurement

### 6.1 Compare semantics and provenance separately

For differential fixtures, use shared test-only metadata where needed to compare generated and handwritten observations or trace payloads. Separately exercise honest production build identities and the expected mismatch behavior. Do not suppress real provenance changes merely to make a byte comparison pass.

Compare concrete inputs, states, ordered outputs, dispositions, checks, termination, and replay outcomes. Wall-clock measurements and intentionally changed build stamps are not required to be identical. Same-seed equality is required only for a declared compatible generator algorithm; concrete recorded-input replay has its own contract.

The prior research used Python semantic transcriptions. Those are useful design examples, not evidence that a Rust macro compiles or that the engine's full reports match. M0–M4 must establish those results in Rust. No new macro compilation or benchmark result is claimed by this roadmap. [P1]

### 6.2 Maintain an adversarial fixture corpus

Include stale completion after cancellation, omitted cleanup, duplicate settlement, a missing required effect, invalid resource reuse, an omitted state field, altered wire tags, changed domain bounds, generator overflow, and a checker error after partial observations. For each, assert the expected failure or explicit verification error rather than only snapshotting whatever the implementation currently returns.

Compile-failure cases should cover duplicate IDs/tags, unknown attributes, malformed callback roles, unsupported fields, conflicting implementations, missing variants, generic bounds, renamed imports, and supported `cfg` combinations. Assertions should emphasize stable diagnostic identifiers and relevant source spans, not every compiler-dependent punctuation detail.

### 6.3 Measure the benefits that are actually possible

| Measurement | Comparison and interpretation |
|---|---|
| Modeling effort | Count duplicated rules and run repeatable tasks: add an input variant, property, field, and fault profile. Record the edits and failures caught. |
| Input generation | Compare reservoir and indexed strategies at several domain sizes. Report their different algorithmic contracts. |
| Runtime work | Measure transitions, checks, allocations, clones, and retained history against optimized handwritten equivalents. |
| Persistence | Measure bytes, encode/decode work, allocations, initialization, and bounded-failure paths. |
| Build/tooling | Measure clean and incremental builds, representative expansion size, binary size, and core-only versus macro-enabled builds. |

Do not set a marketing speedup target before establishing the baselines. Require no unexplained overhead in equivalent generated hot paths; where overhead is justified, document the tradeoff. A faster comparison against naive handwritten code does not establish superiority to optimized handwritten code.

## 7. Repository change map and execution order

These are proposed locations, not claims that the files already exist. Keep the current core modules and add helpers only when their contracts are needed.

| Area | Suggested changes |
|---|---|
| Root `Cargo.toml` / `Cargo.lock` | Workspace/package wiring, optional macro access, qualified version coupling, packaging and offline policy |
| `src/lib.rs` | Re-exports and public documentation; preserve existing engine entry points |
| New `src/modeling/` | Ordinary domain/registration helpers usable without macros; avoid duplicating `Model` semantics |
| New value-codec module | Bounded reader, value traits, owned-type implementations, explicit adapters; reuse `EncodeBuffer` |
| `crates/statelessness-macros/` | Parse, validate, emit, diagnostics, syntax contracts, macro build provenance |
| `examples/` and `tests/` | Side-by-side adapters, compile fixtures, semantic comparisons, golden codecs, injected bugs |
| `build.rs` / macro build script | Honest source/build identities that work from packaged sources |
| `scripts/check-consumer.py` and verification workflow | Real external consumers, packaged/offline tests, feature and compiler matrix |
| `README.md`, `VALIDATION.md`, `RELEASING.md` | Adoption path, supported surface, qualification limits, release order and compatibility |

**Recommended first implementation change:** M0's package skeleton, written contracts, and a tiny real attribute/derive spike with a compile-fixture harness. Do not convert the entire repository in that change.

Then deliver independently reviewable changes in this order: adapter/check generation; domain primitives; domain syntax and sampling integration; codec primitives; codec derives/adapters; integrated qualification and documentation. M3's codec primitives can proceed alongside M2 after M0's contracts are fixed. M4 is the first supported release gate. M5 and M6 should each have their own specifications and evidence.

Maintain one grammar and one validation pipeline per public macro family. Avoid offering two parallel spellings of the same full model DSL in the first release. Small `macro_rules!` helpers remain appropriate when they are genuinely simpler; accepting procedural-macro maintenance is not a requirement to use procedural macros for everything.

## 8. Final scope recommendation

**Commit to the maintained macro layer.** The most valuable first product is a combination of model/check adapters, explicit reusable input domains, and strict structural codecs, supported by readable diagnostics and reproducible qualification.

**Keep the advanced ambitions, but give them separate gates.** Independent monitors and composition wiring are meaningful extensions. A full authoring language or search-pruning system should earn its place through evidence, not become the price of basic modeling ergonomics.

**Preserve three independently reviewable artifacts:** what the application does, what the environment may do, and what correctness requires. Macros should connect them consistently, not collapse them into one self-validating declaration.

## Sources and provenance

Repository links are pinned to the baseline commit. The default-branch head was rechecked while preparing this plan and remained at that revision. External documents were consulted on October 8, 2026. Recommendations, proposed syntax, milestone scope, and acceptance gates are design judgments, not existing features or measured outcomes.

- **R0 — Baseline revision:** [Commit 4628869](https://github.com/Jagalite/statelessness/commit/4628869ea232fe272d3f4982897463315ece3397).
- **R1 — Core contracts:** [src/model.rs](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/src/model.rs).
- **R2 — Request-lifecycle fixture:** [src/demo.rs](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/src/demo.rs).
- **R3 — Independent verification and observation:** [src/oracle.rs](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/src/oracle.rs).
- **R4 — Automatic input sampling:** [src/automatic.rs](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/src/automatic.rs).
- **R5 — Package and dependency policy:** [Cargo.toml](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/Cargo.toml) and [README.md](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/README.md).
- **R6 — Current source/build fingerprint:** [build.rs](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/build.rs).
- **R7 — Current qualification workflow:** [.github/workflows/verify.yml](https://github.com/Jagalite/statelessness/blob/4628869ea232fe272d3f4982897463315ece3397/.github/workflows/verify.yml).
- **E1 — Rust Reference:** [Procedural macros](https://doc.rust-lang.org/reference/procedural-macros.html).
- **E2 — Cargo Book:** [Features and optional dependencies](https://doc.rust-lang.org/cargo/reference/features.html).
- **E3 — Syn primary documentation:** [Parsing infrastructure](https://docs.rs/syn/latest/syn/). An alternative implementation option, not an adopted dependency.
- **E4 — Cargo Book:** [Dependency resolution](https://doc.rust-lang.org/cargo/reference/resolver.html).
- **P1 — Prior supplied research:** `Statelessness-Macros-Research.md`, read in this conversation. Its Python probes are not Rust macro qualification.
