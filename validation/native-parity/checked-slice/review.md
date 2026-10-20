# Checked-result and runtime recorder review

Independent review compared the native implementations and shared fixtures with
the pinned public Rust observation, execution and monitor APIs. Runtime results
are separate receipts; a static no-findings review is not execution evidence.

Concrete findings addressed during implementation include:

- A recorder must accept compatible models with different state/input/output
  representations. Go uses nongeneric storage and generic free functions;
  TypeScript observation methods have independent type parameters. Metadata and
  encoded continuity remain mandatory; pointer identity is not required.
- Validate borrowed trusted check batches before allocating recorder copies or
  output collections. Mutable context snapshots remain a native ownership cost,
  rather than a reason to repeat checks or weaken the wire quotas.
- Preserve Rust's envelope-inclusive temporary frame allowance and validate an
  impossible disposition reason before later input/check callbacks.
- Keep format quotas separate from retained-byte quotas. Size each frame first,
  then admit global total bytes, aggregate items, format steps, retained bytes and
  retained steps in that order. Retention defaults remain independent of custom
  format limits. Specific shared error categories prevent an unrelated failure
  from masquerading as quota enforcement.
- A stop inside a codec/checker must preserve the old coherent prefix and prevent
  outer admission. Nested observation and ownership transfer cannot bypass the
  active borrow. Error formatting can itself call application code; guard it too
  and preserve the first terminal reason. Python cannot reinitialize an existing
  recorder to bypass these lifecycle rules.
- Writer callbacks cannot change an artifact between validation and publication.
  Go holds an export borrow around direct writing; Python/TypeScript/Swift export
  stable trace views or value snapshots. Read-only inspection is distinguished
  from mutation.
- Diagnostic observations do not certify checks. Native token constructors are
  private, check/context accessors do not leak mutable certified storage, and
  handoffs do not globally consume tokens. Full checking is policy coverage,
  not a claim that every model-emitted check passed.

The Rust reference uses actual distinct associated types for cross-representation
handoff. Native tests additionally exercise runtime forgery, mutable aliases,
snapshot failures, callback reentry and formatting/writer callbacks that Rust's
borrow/type rules cannot express. Faithful clone hooks and model purity remain
application contracts, not a security boundary against hostile reflection.
