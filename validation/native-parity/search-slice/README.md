# Native search candidate evidence

Final native source suites passed on Darwin arm64: Python 74 tests with 12 opt-in
ABI-v2 skips, TypeScript 92 tests, Swift 53 tests, Go package tests and vet. The
Rust shared reference test was compiled through Kyoto and its produced executable
was separately run: all 25 golden rows and exact shrink histories passed.

`summary.json` preserves requested/effective Kyoto commands and intermediate
failures. `rust-search-runtime.json` records the separate Rust execution. The
initial `source-inputs.json` snapshot predates the nullable fixture correction and
consumer-driver refinements; use the final source snapshot for current inputs.

The installed `r5` scenario stages native packages outside the checkout. Every
producer found six inputs, reduced to three in 15 attempts, retained original and
minimized artifacts, and reproduced both in fresh processes across all four
engines. All 32 artifact replays passed. `installed-r5/qualification.json` binds
commands, tool versions, package/trace/executable hashes and staged source inputs.
Earlier r1/r2 setup failures and r3/r4 successful narrower scenarios are retained
without replacing their results.

These are finite, host-specific candidate results. Remaining platform, complete
profile, performance and N01–N14 gates remain open. No package was published and
no implementation default changed.
