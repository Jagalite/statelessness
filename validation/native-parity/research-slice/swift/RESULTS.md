# Swift N14 finite research candidate qualification

Kyoto `cecb24161e1a4d72b070ca0383a135eb` succeeded, exit0. Requested and effective
command were identical:

```
env STATELESS_RESEARCH_CORPUS=/Volumes/seed2/Projects/stateless-native-parity/conformance/research-equivalence-v1.json swift test -j 2
```

Actual XCTest execution: **147 tests, zero failures**, including **13 ResearchTests
methods**. The XCTest suite completed in2.235s; Kyoto execution wall time was
18.139s. The separate Swift Testing footer reports0 tests and is not the XCTest
count. The log records compilation of Research.swift and actual ResearchTests
execution on arm64e-apple-macos14.0. Compiler identity is retained in toolchain.json.

All67 shared research fixture rows executed through the public API, comparing the
actual full reports, callback order and17 qualification strings. Ten native-only
rows remain separately classified:5 public host-failure controls (nonreflexive
keys, projection/equality/iterator-next/metadata exceptions), and5 typed exclusions
or language-inapplicable arguments. Those are not fabricated public Rust calls.
The suite also tests exact Unicode identities, owned reference payloads/witnesses,
preferred append checks, shortest witnesses and budget/counterexample precedence.

The complete77-file source manifest was captured before submission. Post-execution
manifest hashes and path sets match exactly, including all Swift runtime/tests,
root experimental marker, package/README, conformance JSON and specification MD.
HEAD moved during the queue/archival interval; head-reconciliation.json records
the intervening commits. All77 manifested source inputs are unchanged, so this
is source-hash qualification rather than an assertion of an identical HEAD.
The exact requested/effective command, attempt and terminal metadata, plus raw
log, establish actual execution; matching bracketed manifests under global freeze
establish the audited source boundary. There was no in-process source fingerprint
instrumentation. Post-run test binary identity is additionally recorded without
claiming that it replaces source/command evidence.

Logs are lossless gzip with original byte length/SHA256 in logs.json. No failed or
cancelled N14 Swift attempt occurred, no rerun was performed, and no runtime source
was edited after the frozen submission. Summary and corpus/spec identities are in
summary.json. Independent static review remains separate from runtime evidence.

This qualifies the observed finite candidate/test scope, not all deployment OS
versions, physical memory bounds, arbitrary user comparator laws, general
reductions or production pruning. No report changes existing production explorer,
replay equality, input order, default or promoted profiles. No commits were made
by the Swift implementation agent.
