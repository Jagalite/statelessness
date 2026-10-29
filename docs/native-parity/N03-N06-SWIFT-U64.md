# Swift exact Rust64 counts

The `64` API family preserves unsigned Rust64 counts end to end:
`AutoOptions64`, `Auto64`, `sampleEnumerated64`, `containsEnumerated64`,
`FuzzConfig64`, `FuzzReport64`, `fuzz64`, `GuidanceConfig64`, `GuidanceReport64`,
`CorpusEntry64`, `guidedFuzz64`, `ShrinkConfig64`, `ShrinkOperation64`,
`ShrinkAttempt64`, `ShrinkReport64`, `shrink64`, `CampaignConfig64`,
`CampaignReport64`, `JobReport64`, `JobOutcome64`, `runCampaign64`, and
`runGuidedCampaign64`. Integer count properties use `UInt64`. Mutation percentage
remains the existing checked `Int` with semantic admission 0...100.

The existing `Int` APIs remain source compatible and call these same kernels.
Their configuration `exact64()` conversions validate legacy inputs; the exact
configuration/report `checkedInt()` adapters throw on unrepresentable values.
No adapter rounds, clamps, or hides requested work. Existing model, feedback,
observer, campaign factory/sink ownership and CampaignPanic handling persist.

Counts do not reserve storage. UInt64 fuzz loop positions convert to Int only
when bounded by an existing mutated-input array. Guidance limits compare actual
collection lengths in UInt64. Actual array/map growth checks native capacity.
Campaign workers and channel capacity narrow only after the existing 256/1024
validation bounds; requested/started/finished/delivered counts and the lifecycle
remain UInt64. Lifecycle test codec encodes/decode report counts as u64, bounds
pending/string sizes by available bytes before conversion/allocation, and resumes
the real reducer. It is private qualification support, not a public durable queue.

Installed N06 consumers should use `CampaignConfig64`, `FuzzConfig64` and
`GuidanceConfig64` with UInt64 parsing, call the corresponding `64` entrypoints,
and serialize report fields directly as decimal strings. Existing bounded Int
consumers need no source changes. Full-width lifecycle consumers must copy the
updated private test codec together with its source identity.

The pthread retain now crosses a narrow integer-address shim: only an immutable
Sendable closure is retained; successful pthread creation transfers one retain to
the entrypoint, failure releases it in the caller. This source restructuring
addresses the Swift 6.3.3 compiler crash observed in historical r3; a new compile
receipt is required before claiming the workaround succeeds.

Boundary expectations are independently specified in
[N03-N06-U64-CONTRACT.md](N03-N06-U64-CONTRACT.md). `Count64Tests.swift` covers
pre-cancelled maxima, zero work, empty domains, one-step/initial failure,
guidance metadata/admission, actual bounded retention, campaign validation,
wide product/cap arithmetic, one-job stopping, panic provenance, lifecycle
restart/acknowledgements/saturation, and checked Int conversion. Existing
lifecycle tests now accept valid report counts above Int.max.

Source completion and execution qualification remain distinct; queue receipts
are provided to the parent for final integration and independent review.

## Frozen qualification receipt (2026-10-29)

Final source/package/corpus snapshot: `/tmp/stateless-swift-u64-r2/source`.
All 211 file identities are retained in `source-manifest.json` (SHA256
`aa51f81fde8e48ba531c2c1d0ea6cbaaf66bdc01c3efee084b0c24c6bd18e62c`).
The manifest includes `Package.swift`, every Swift source/test, and the copied
conformance inputs; files were made read-only before submission.

`tool-identities.json` has SHA256
`2118843a19c917cb0ee204e21bf2160fa1737b5ab79f7a588653c7b420e5d857`.
The actual Xcode compiler `/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift-frontend`
(resolved from `xcrun --find swiftc`) has SHA256
`2ed38571e92c0283091838c1649e27650ad9c99950288e883c7b2dc6c4ce89fb`.
The Python driver and `/usr/bin/swift` shim hashes are separately retained.

Final build-only Kyoto job: `0ed248044a9442328b9d667ec2e87db6`, invoking
`python3 /tmp/stateless-swift-u64-r2/qualification.py build` (driver SHA256
`b51f0b7ae52237f767b33efe378b6beaf35a6081236f979bb3e9fb844a28ce73`).
It verifies every frozen input, records Swift's reported version, and invokes
`swift build --package-path /tmp/stateless-swift-u64-r2/source --build-tests --jobs 2`.
At handoff it remains QUEUED, never started: `effective_max_running=0`,
`admission_reason="OS memory pressure: warning"`. Compilation and runtime are
both unverified; no emitted executable identity exists yet.

After successful compilation, separately submit:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 /tmp/stateless-swift-u64-r2/qualification.py runtime
```

That command rechecks the frozen inputs, hashes the actual XCTest executable,
then runs it directly. It never submits/waits for another Kyoto job.

Superseded r1 build job `c39473d75f5146849a07cbb4cc47a488` was cancelled before
starting; its immutable `/tmp/stateless-swift-u64-r1/source` and manifest SHA256
`d49f2d1ec11aeaeb94da70c9505a4c61c34a5c0bff0a2ff3cf405c76b829b7bf` remain.
A cancellation attempt without the matching owner identity was denied (exit 2);
the matching-owner cancellation succeeded. `attempts.json`, both Kyoto receipts,
and the admission snapshot retain these outcomes. Historical parent r2/r3/r4
snapshots/jobs were not changed, cancelled, or duplicated.

Final materialization guards check element stride as well as native index count;
failed feature pushes remain sticky even if a callback ignores the error.
Independent review requested that sticky-error correction; it is included in r2.
