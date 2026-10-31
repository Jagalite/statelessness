# N13 execution handoff

Implementation: `9d05ca0`; Rust test setup correction: `a5c388a`.
Branch: `t3/native-model-codecs`.
Worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-b71b6b32`.
Canonical and parent worktrees were not modified. No push, PR or parent merge.

Independent static review found no remaining concrete correctness issue in the
inspected implementation. Qualification is incomplete: Kyoto's OS memory
pressure warning held the final reruns. Do not replace these pending handles:

- TypeScript full suite: `e7f3c0b1ecef41bd8d8a34cd58f7dd52`.
- Installed finite interchange attempt 2: `a7c82f5f392546ac915cb473c60ee24f`.
- Corrected Rust reference compilation: `5d323f2e18e54516858dbbf1be73a5b5`.

Passed: Go tests and vet; Python 259 discovered tests with 12 ABI skips; Swift
140 tests; 22 focused Rust compiler-rejection declarations; Rust finite
interchange producer/golden checks. Exact handles and effective commands are in
`summary.json`; terminal logs and metadata are archived under `jobs/` with
SHA256/size ledgers. Failed and superseded results remain present.

The initial TypeScript and installed runs failed because the isolated checkout
lacked tsc. The pinned npm dependency install subsequently passed via Kyoto.
The first Rust execution passed five tests and failed one setup assertion:
EncodeBuffer::new clears its destination. The corrected test appends its prefix
after initialization. That failed execution is preserved, not called a pass.

From this worktree, inspect each pending handle using Kyoto show/wait/logs.
The Rust compilation job has effective `--no-run`; after it succeeds, read its
log's exact `Executable` path and submit that executable with `--nocapture`
through Kyoto. Require all six tests to pass. Compilation is not execution.

Once installed attempt 2 passes, run the additional finite Rust/native
interchange through Kyoto:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 validation/native-parity/derive-model/rust/interchange_installed.py --installed validation/native-parity/derive-model/installed/attempt-2 --rust /Users/jagatranvo/.t3/worktrees/stateless/t3-b71b6b32/target/debug/n13-interchange --output validation/native-parity/derive-model/rust/installed-attempt-1
```

This uses four installed native producer bundles for 12 fresh Rust consumer
processes, and one actual Rust producer bundle for four fresh native consumer
processes. The native driver itself requires four producers and 16 native
interchange pairs over three finite fixtures. No installed binder interchange
has been qualified.

Archive every new result with `archive-jobs.py JOB_ID ...`, update summary and
source identity evidence, and commit final proof. A failed installed attempt
must remain intact; retries need a new output directory. No scheduler policy
change, other-job cancellation, profile promotion or broad144 claim is authorized
by these receipts. Scope remains macOS arm64 and the independent 90 mixed-class
draft cases are not 90 runtime executions.
