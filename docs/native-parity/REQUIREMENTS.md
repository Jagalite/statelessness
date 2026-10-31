# Completion evidence requirements (M0/N12 continuation)

`requirements.json` is a separate semantic/evidence disposition ledger. It does
not modify the lexical inventory's intentionally `not_started` rows or the
compiler inventory's `pending_individual_api_review` rows. The 5,864 compiler
records retain their native/default/aarch64-apple-darwin scope. Four named debugger
types now have source-bound per-language correspondence entries; their members
and behavioral equivalence are still unqualified. All other compiler identities
have the explicit default pending disposition. No declaration is silently dropped.

The five stale debugger/derive feature groups in `capabilities.json` now say
`partial` with real source paths instead of `missing`. Hashes are scoped to the
new reconciliation; the historical source snapshot is not rewritten. Remaining
watch/probe, candidate/export and telemetry protocol integrations are named.
The N13 lane owns the remaining derive/model/domain/inspection decisions.

Each of 36 capabilities retains four pending language cells. The requirements
ledger expands them to 4,032 evidence obligations over eight gates and 14
language/platform/runtime contexts. These are evidence obligations, not feature
counts or claimed coverage. Conditional mobile/other targets and exact supported
runtime versions still require the parent's explicit matrix review. Other lanes
are recorded as dependencies, never inferred qualified.

Run the checker through Kyoto:

```
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native-parity/completion-gate.py --check
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native-parity/test_completion_gate.py
```

`--check` validates ledger structure and current correspondence hashes. It does
not assert completion. Without `--check`, missing requirements cause exit 1;
invalid or changed evidence causes exit 2. A report lists every missing tuple.
Neither this lane nor a finite direct wire pass closes broad144.

A `native-parity-evidence/1` receipt names one exact
`[capability, language, platform/runtime, gate]` tuple and the pinned Rust baseline.
It binds a `native-parity-source/1` manifest by path/hash. That manifest enumerates
`roots`, `inputs` and exact SHA-256 `files`; the checker independently enumerates
membership and rejects omitted, added, missing, changed or symlinked inputs.
Required roots include that language's source/tests/examples, conformance, all
scripts/examples and parity docs; packaging metadata and lockfiles are explicit
inputs. Mandatory semantic/runtime/interchange/inventory blockers keep completion
open independently of receipt count. Keep generated source
manifests outside those roots (for example in validation receipts).

Receipts preserve explicit runtime argv, Kyoto job, exit/skips, platform/runtime,
nonempty tool/package/corpus/log identities, independent expected/observed
assertions and author/reviewer/contract attribution. `--no-run`, nonzero exits,
skips, missing artifacts and self-review reject. This is a local consistency and
provenance checker, not cryptographic attestation or a substitute for reviewing
assertion authorship and contract adequacy. Producers must retain raw artifacts;
hashes alone cannot replace unavailable evidence. Historical lane receipts are
not automatically converted into acceptance receipts.

The N12 integration runner additionally accepts `sealed_inputs` in its spec for
source-authored corpora under validation. These exact contained regular paths
participate in before/after snapshots even though mutable receipt JSON is normally
excluded. The overall integration runner's excluded receipt-family guard remains
unchanged. Final combined suites, platform/runtime evidence, performance budgets,
release compatibility and acceptance belong to parent integration.
