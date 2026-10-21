# N13 focused Rust correction qualification

Scope: correction of two focused Rust fixture assertions, not full N13 acceptance.

Isolated worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-ea05e2fb`, branch `t3/n13-test-qualification`, fast-forwarded from `0876717` to required `eaf87d8bedfe6ec903ea12e96ba189099945ba30` before editing. No AGENTS.md found in checkout or ancestor instruction locations. Reviewed correction source commit: `c26b8a0333c454c681990e4a08e77195ef1fa744`.

The authored `cases.json` callback `decode_only_u8` delegates raw `u8::trace_decode`. The enum now uses explicit `DecodeOnlyByte` implementing only that direction; it no longer accidentally nests a derived record. Guarded enum admission + guarded tag + guarded ordinary raw payload costs work 3, peak depth 2. Added exact/one-under work and depth checks retain independent expectations. The existing derived `DecodeOnly(u8)` record still costs work 2/depth 2. The unit enum row remains work 2/depth 2; the payload test is supplemental. No independent JSON goldens, budgets, production sources, or native core changed.

`EncodeBuffer::extend_from_slice` reports direct `encoded blob exceeds byte limit`; `finish()` independently reports sticky `encoded blob exceeds byte or allocation limit`. Both assertions are literal source-derived expectations.

All five disposition ledgers and relevant native fixtures were inspected. Python, TypeScript, and Go directional callbacks delegate raw scalar reads. Swift's scalar callback is raw, but its directional enum fixture uses a record payload wrapper and exercises the unit row; no claim of identical native payload callgraphs is made. Rust row notes now explain the actual source accounting. `scripts/check-n13-dispositions.py` passes all five 90-row ledgers (source accounting only). Independent static review: `review.md`.

## Exact execution evidence

* Failed V2 actual-execution job `49f42348946c4330853422464c9db7c6`: preserved terminal metadata and exact raw log/receipt/source snapshots under `failed-v2-*`. 12 passed, 2 failed; this historical failure remains intact.
* New compile job `8dc9526b3a83438ab5c5cfadfb369279`: succeeded, effective Cargo command includes shared-policy `--no-run`. This is compilation only. `compile-job.json`, `compile.log`, and `compiler-artifact.json` identify the exact emitted focused test executable.
* New runtime job `aae06807db774063b61593a6fd0940a7`: separately submitted exact emitted executable through Kyoto and ACTUALLY executed all 14 tests: 14 passed, 0 failed/ignored/filtered. See `runtime/raw.log`, `runtime/receipt.json`, and `runtime-job.json`. Exit 0; before/after source snapshots identical.
* `executable-identity.json` pins executable SHA-256; `corrected-source-before.json` and `compile-source-comparison.json` establish stable source through compilation, and runtime snapshots establish stable source through execution.
* Passed 25-case compiler-rejection job `cfe860eae13e4c88942e4f7b8079f5a2` is inherited only for unchanged compiler/runtime/harness sources. Exact wrapper evidence and all case logs/report are retained. `compile-fail-source-comparison.json` records the only differences: focused runtime fixture and Rust disposition notes. No redundant compile-fail run.

No pending correction jobs. Parent full integration and requalification remain required, including the broader installed five-engine binder/interchange and workspace qualification. Old V1/V2 jobs remain owned by root and were not cancelled, deduplicated, or modified. No push, PR, or parent merge.
