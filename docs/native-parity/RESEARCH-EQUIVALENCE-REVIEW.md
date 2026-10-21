# Independent finite-equivalence research review

Static source review only; no builds/tests/model execution were performed. This
is a final static review snapshot of the four candidate implementations and
adapters, not runtime qualification. Root-authorized source freeze and Kyoto
execution evidence remain separate gates. Later changes invalidate their hash
scope until re-reviewed.

## Findings and fix status

- **P1, corrected native finite-termination defect.** Go comparable NaN, Swift
  Equatable Double.nan, Python NaN and custom TypeScript equality could be
  nonreflexive; reduced BFS then repeatedly enqueued the same exact cyclic node.
  All four now reject nonreflexive captured keys before traversals and use an
  unconditional exact-index seen guard after observations before admission.
  These are native host-only controls: Rust Eq excludes float NaN. Equality
  laws/purity remain cooperative obligations; no callback preemption is implied.
- **P2, corrected Python edge count.** Successful reducer results are counted
  before returned-transition validation/snapshot, target lookup and edge checks,
  rather than after the old helper's clone. This now matches raw Rust return
  ordering and the other native implementations.
- **P2, corrected Unicode validation.** Python and TypeScript now use the exact
  Rust White_Space scalar set instead of host strip/trim. Swift's explicit set
  and Go TrimSpace match. U+001C/FEFF remain nonblank;0085 is blank. Stored
  proposal/property strings remain unchanged.
- **P2, corrected report errors and panic boundary.** Go uses research-local raw
  invocation, snapshots and preferred/legacy sink dispatch; Python preserves
  typed ModelError and uses local raw checker dispatch; TypeScript likewise
  avoids contextual engine wrappers. Intrinsic Rust-equivalent callback error
  messages remain exact. Go panic diagnostics no longer format arbitrary panic
  payloads. Existing engine helpers were not changed.
- **P2, corrected Swift framework/direct-string identity.** Direct String
  convenience comparisons now use exact UTF-8. Check IDs/details, disposition
  reasons and RequiredProperty equality/hash/order already use exact identity.
  Synthesized compound/custom Equatable still needs an explicitly faithful
  comparator when native semantics differ; this limitation is documented.

No unresolved concrete semantic defect found in the revised four core sources
and adapters at this snapshot. All67 shared adapters call public APIs and
normalize actual report fields, callback logs and qualification strings, not
expected fixture reports. Go/Swift five actual native controls and five typed
exclusions, Python nine actual/one language-inapplicable, and TypeScript native
controls remain separate from shared Rust counts. Shared adapters do not mirror
the verdict algorithm. All fixes above are source-reviewed, not runtime passes
by this reviewer.

## Source-algorithm checks

Reviewed callback admission order, closed materialization once over all declared
states including unreachable rows, per-edge accounting, projection after complete
materialization, and exact/reduced BFS over the owned table. The traversals
observe successor state and transition checks before dedup and retain deterministic
first shortest property-ID/phase witnesses. Pair checks cover exact-reachable
members only. Observable comparison priority is state checks, input count/order,
ordered outputs, disposition, transition checks, successor classes. A concrete
counterexample outranks pair cutoff and skipped/absent-property incompleteness.
Qualification parameters never enable production pruning. Explicit metadata and
model equality paths support non-codec models.

Model/clone/equality/projection callbacks remain cooperative and require faithful
snapshots, lawful stable equality and synchronous execution. Native malformed
arguments/exceptions are separately documented controls, not Rust typed cases.
No symmetry renaming, partial-order pruning, performance, arbitrary allocation,
process RSS, global minimum or production-equivalence proof follows.

## Independent fixture review

Read the frozen candidate spec and all67 shared case summaries/scenarios plus
10 native-only admissions. Manual source-derived expectations are consistent
for validation precedence/Unicode, materialization callback logs, ordered pair
mismatch priority, declared indexes versus BFS discovery, collapsed target
checks, skipped re-observation, property/phase witness order, missing-list order,
exact pair/edge limits, lost-failure/counterexample precedence and callback error
counts. Notable checks: skipped toggle observes3 exact/2 reduced; oracle-history
lost transition witness is tick,tick; three states require3 same-key pairs;
NFC/NFD IDs remain distinct and byte-sorted. Native-only exceptions/key laws are
not fabricated Rust signature cases. Real Rust execution remains separate.

## Reviewed snapshot hashes

Snapshot time: 2026-10-21T00:10:44.289409+00:00.

| File | SHA-256 |
| --- | --- |
| `src/research.rs` | `c3e436ae05e7b7a08279af9447679b021a893a33196e844f432962bb13648bb7` |
| `tests/research.rs` | `265ffa49db72889b499f5588dfc5791932978b7a0b331da6b3f9304db169751f` |
| `docs/REDUCTION_RESEARCH.md` | `fbe74188c6b38db1cbb7512c363922b7850bf56e4db0a5cce55f0d4c50d46ff4` |
| `spec/research-equivalence-v1.md` | `bbaffbcb09f24e5e85bfa3ca59ed7ff5b3d242f8c086b91af43f7373ca022649` |
| `conformance/research-equivalence-v1.json` | `a1dd99679878eae28bd1203a4dc51b2c8a307e058d043ce5567172db566f39aa` |
| `go/research.go` | `87d04f1a54017516b7066b378fd08ed333d109c5976d23530333c3e8bc31d281` |
| `go/research_test.go` | `48a457f6d71cdeba15eeb957aee6552fdc8dbe3c2dd9736159ce43fb441c3060` |
| `python/src/statelessness/research.py` | `cf1700faf152f28c0d27967b880bc6fe87b4ed6a152f0d4a1c8b89bf09a345bf` |
| `typescript/src/research.ts` | `19d83be8f4f0ca0bd181201c8e71bf87cbc97738b7baf12378332ea6b50994be` |
| `swift/Sources/Statelessness/Research.swift` | `30dda4aea30d12d2bc15b88a300c0909abae7c6599aa539a214515eaa7c5067d` |
| `go/research_corpus_test.go` | `4f3aee384e2fca79682eac0f3a483c5a01bc3ecbd44b2e1341ca6b383254db73` |
| `python/tests/test_research.py` | `8496474f7aa3a9d6d9fc81ebc4592bbeb9408001ba4eb82adecdd3355fc87d93` |
| `typescript/test/research.test.mjs` | `900f3ae5fbeb0ee08940c6831f556ae8e000fc91ae64a64147189eca90af4959` |
| `swift/Tests/StatelessnessTests/ResearchTests.swift` | `9bc3d857910dc6ba0a5f628ed2629dea87c87130284f19414a832d41d00788db` |
| `python/src/statelessness/__init__.py` | `cef793916954de35d232926f1c72f1125088645f4cc783771a0fdaf65b61c03f` |
| `typescript/src/index.ts` | `25609fd95d008a9a3632eedadbd8ef417c308239726fbfddf301e4a3c9c3c1df` |
