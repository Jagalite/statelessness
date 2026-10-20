# Native append-check candidate receipts

This finite macOS/arm64 slice passed through Kyoto. It does not close N01,
promote a profile, publish packages, establish allocation/performance parity, or
qualify other platforms/runtime floors/browser TypeScript.

The real public Rust reference matched all 52 independently specified cases:
legacy/default and append dispatch, full-prefix inspection, observed checking,
BFS, fuzz, shrink, recording, replay and oracle/paired ordering/error boundaries.
Rust was built and then executed as separate Kyoto jobs. Native lease expiration,
malformed-value validation and synchronous-return enforcement are explicit
extensions to Rust's static borrow/type guarantees, not fabricated Rust tests.

Final native suites passed: Python ran 124 tests with 12 ABI opt-in skips, Go ran
uncached tests with cgo disabled, Swift ran 73 XCTest methods, and TypeScript ran
281 tests. The suite includes shared append/BFS/binary/search cases and native
nested/escaped lease, storage reuse, sticky error, panic/exception, async-return,
source-value/snapshot ownership and partial diagnostic tests. Final Go vet is
recorded separately in summary.json.

`installed-r2/qualification.json` identifies packages staged/installed outside the
checkout. Five applications use append hooks while legacy checkers fail if
called. Their pipelines execute observed-into controls, BFS, seeded fuzzing,
shrinking, recording and replay. Each found six-step witnesses and reduced them
to three steps in 15 attempts. Actual callback totals match the real Rust
application: 89 state and 70 transition append callbacks, with zero legacy calls.
All 25 fresh-process witness replays passed and matched Rust's four state/three
transition callbacks. Buffer clearing, no reducer re-execution, full-prefix
inspection and native escaped/nested lease controls passed.

The separate portable-baseline.json verifies all four unchanged promoted profiles
across five independent engines: 717 cases per engine (94 goldens, 530 generated
graphs, 32 metamorphic, 38 portability, 23 transport) and 550 fresh-process replay
combinations, with zero failures. Candidate APIs do not alter the runner schema.

Append review uncovered a Python binary-writer validation issue: custom string
or equality behavior could spoof variants or hide forbidden reasons. The fix
uses intrinsic variant comparisons and retains payload strings until quota
checks. All 54 binary goldens remain covered. A targeted 8 MiB string-subclass
regression checks bounded traced Python allocation before rejection; this is not
an RSS or general codec-allocation guarantee.

`installed-r1` passed on an earlier Python identity and is retained historically;
its later narrow variant canonicalization still copied string subclasses before
quota checks. R2 includes the corrected source and compares callback totals to
actual Rust receipts. Earlier source failures and intermediate passes remain in
logs and summary.json; they do not qualify later source implicitly. The initial
TypeScript diagnostic is gzip-compressed to retain its exact whitespace; its
uncompressed SHA-256 is recorded in summary.json. One npm
attempt used the wrong working directory and failed before compilation. A Swift
fixture was updated to select a legacy override explicitly because built-in
adapters now provide preferred append hooks; migration notes are in package docs.

Source/package/executable/artifact hashes and exact command logs are retained.
Final installed native/reference inputs were compared again and remained
unchanged. source-inputs-final.json identifies the final uncommitted source bytes
relative to the preceding BFS implementation commit. Qualification remains
separate from implementation in the semantic ledger.

Unfinished requirements include EncodeBuffer/bounded codec callbacks, richer and
bounded replay, checked-result handoffs, binary-budgeted recorder capture and
causal evidence, remaining N01–N14 APIs, and wider release gates. Mutable caller
buffers require exclusive synchronous ownership; expired-lease checks cannot
sandbox raw aliases or unsupported concurrent application code.
