# Go and Swift causal evidence static review

Review date: 2026-10-20. This records a read-only source review against
`spec/causal-evidence-v2.md`, `src/evidence.rs`, and `src/value_codec.rs`.
The reviewer ran no builds, tests, application replay, or filesystem exports.
Test source was inspected; reported Kyoto results were not independently checked
by this reviewer. This is not a runtime or broader parity qualification.

## Reviewed scope

- Exact v2 envelope framing, CRC, strict lengths/options/UTF-8, v1 preservation,
  semantic validation, and metadata budgets independent of embedded trace items.
- Rust64 logical allocation weights, decoder work/elements/depth accounting,
  sticky failure, and quota checks before owned copies.
- Go upgrade entry capture and deferred artifact/provenance errors: identity
  callback once, artifact admission before decoding, provenance failure after
  exact replay. Swift value ownership and phase-aware replay/explicit upgrade.
- Current-checkpoint causal source validation before planned eviction, private
  runtime-recorder retention bridges, trace-first journal commit, snapshots,
  first-error preservation, and callback reentry boundaries.
- Exact diagnostic field order, escaping, redaction and whole-output caps;
  exclusive new-file creation, private permissions, file sync and incomplete-file
  errors. No claim of rename atomicity or parent-directory durability.

## Findings and disposition

Go initially copied unvalidated provenance/cause collections before quota checks.
The author moved bounded borrowed validation before those copies. A subsequent
constructor snapshot occurred after application callbacks; it now captures
admitted provenance before constructing the underlying recorder. Upgrade capture
was revised to avoid copying rejected inputs while retaining Rust's identity,
artifact and replay-before-provenance error order. These fixes were re-read.

No remaining concrete source findings were identified in the reviewed scope at
the identities below. Runtime qualification and any later changes require their
own evidence. Native allocations, fatal OOM, platform behavior and host callback
contract violations are not established by this static review.

## Source identities

SHA-256 values captured after the final review pass. Concurrent authors may make
later fixes; these hashes identify the reviewed source snapshot rather than an
unqualified claim about future worktree contents.

```text
672720503ca45bdc32580004c1e1552d2733895d90231ec7dd472db1ea7baacc  go/evidence.go
3485515ddde313f4d0097974a919168d961364b745f6214734a4e76582dc2e84  go/evidence_metadata.go
7932bd357772bfcbcaa98bbaa6b142eb73930a431a5333de76d2d067845fec5b  go/evidence_recorder.go
6edefec03deb67679bcfe54a50f00d08864bb836494c8fdb2d32407b71286fa2  go/evidence_export.go
910f44bbc0e63830a4867efc57fa7cfc022a8140a050c5b9be5abfea4bee19d7  go/recorder.go
b635cc997f3fd50e45a4802c5c341a9799381740d269307e51f0b30c9a4d1e8b  go/replay_evidence.go
98c5f966b49c6989098d406123516de3b8e4c4ffcffa81c251cec118d2bbfa04  swift/Sources/Statelessness/Evidence.swift
a1c391661e35362580d2b52f7368b93adea98c963e0181250727bf2d5564ef83  swift/Sources/Statelessness/EvidenceCodec.swift
4d9cb07e8af56f04202cffde9de0e2bff363366aaf011f59b1d65d2d9fdc723f  swift/Sources/Statelessness/EvidenceRecorder.swift
4916bf5adfa108dc3e294dd0b9d4c6786d0b456a1e643bd5afaebe3bb4a25fff  swift/Sources/Statelessness/EvidenceExports.swift
459c44a770785540e60b0280e3a3f212a683fcbfccd41f6193b8bad790602ec9  swift/Sources/Statelessness/Recorder.swift
c4b22b63aebc8e94dfe83e0228f0bc0aeb7abce899eb18987154bd98e7a203e7  spec/causal-evidence-v2.md
```
