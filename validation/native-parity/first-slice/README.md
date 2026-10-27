# First native parity slice evidence

`source-inputs.json` fingerprints the initial 226 source, contract, fixture and test
files. `summary.json` records Kyoto requested/effective commands and final first-slice
job results. All corrected first-slice suites executed successfully. This source
snapshot predates later native search changes; their qualification is separate.

Python discovery passed 50 tests with 12 opt-in ABI-v2 skips. Go tests passed without
cgo, Swift passed 42 tests, TypeScript passed 47 tests, inventory tests passed 4,
contract tests passed 2, and qualifier regressions passed 18. The original Swift
XCTest method-name compilation failure remains in the logs.

Kyoto compiled the Rust paired-profile tests with `--no-run`. The produced test
executable was then run separately per the Kyoto skill and passed all 4 tests;
`rust-paired-runtime.json` identifies the exact executable and build job. The
shared queue policy was unchanged. No installed-consumer/platform/performance
qualification or profile promotion is implied by these finite results.

Inspect or wait without changing policy:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs JOB_ID
python3 /Volumes/seed1/Projects/kyoto/kyoto.py wait JOB_ID
```

Before qualification, rerun any job whose source inputs changed after it started,
retain final logs and refresh the source-bound summary. Keep the failed first Swift
attempt as historical evidence. Source hashes cover engine/tooling inputs, not
these receipt files; validation output is excluded to avoid a recursive manifest.
