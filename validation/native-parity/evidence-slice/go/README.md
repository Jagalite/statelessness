# Go causal evidence slice

Final module qualification passed against source candidate `d8ba663` with unchanged
scoped source SHA-256 `2cfc671f191a504ff9f65ed85628a9f00d236e92dc78d0b2c2d681bdeceface0`:

- `5e59e98de1b44b2baa2445b385d86fc2`: `env CGO_ENABLED=0 go test -count=1 ./...`, exit 0.
- `bd5a76feef90466eb538b22633efe290`: `env CGO_ENABLED=0 go vet ./...`, exit 0.

Requested and effective commands match, from the Go module directory. Tests
executed; the log reports the root package and internal portable JSON package
passing. The suite consumes all 52 wire, 16 replay/upgrade, 20 evidence recorder,
and 13 diagnostic corpus cases, plus native ownership, structural-budget,
truncation, reentry, file-permission, no-overwrite, sync/error, and legacy tests.
Go's ordinary test output does not enumerate a total test count.

This is finite host qualification of an experimental candidate, not promotion or
a process-memory bound. Rust64 metadata charges are logical schema weights.
Installed-consumer qualification is recorded separately.

`source.json` records exact source/input hashes and the pre-execution snapshot;
its final reconciliation confirms the Go fingerprint remained unchanged through
both successful jobs. `results.json` summarizes terminal receipts and scope.
`jobs/` preserves exact Kyoto requested/effective commands and results. `logs/`
stores raw logs in deterministic gzip (`mtime=0`), including whitespace.
`log-ledger.json` records original/stored byte sizes and SHA-256 values;
decompressed bytes were verified identical.

The two failed intermediate runs retain their diagnostics: the first had one
reader-limit message mismatch; the second exposed embedded-v1 quota category
spellings. Both were corrected. No exact source snapshot at those intermediate
compilation times is claimed; queued jobs could observe edits before the final
source hold. Final source was held while Kyoto delayed admission for OS memory
pressure; queue policy was not changed or bypassed.
