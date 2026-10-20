# Go causal evidence slice

Source is frozen at the snapshot in `source.json`. Final execution receipts are
pending Kyoto jobs `5e59e98de1b44b2baa2445b385d86fc2` (full `go test -count=1
./...`) and `bd5a76feef90466eb538b22633efe290` (`go vet ./...`), both submitted
with `env CGO_ENABLED=0` from the Go module directory. Pending jobs are not passes.

The full suite consumes all 52 wire, 16 replay/upgrade, 20 evidence recorder, and
13 diagnostic corpus cases, plus native ownership, structural-budget, truncation,
reentry, file-permission, no-overwrite, sync/error, and legacy regression tests.
This is finite host qualification of an experimental candidate, not promotion or
a process-memory bound. Installed-consumer qualification is recorded separately.

`jobs/` preserves exact Kyoto requested/effective commands and terminal results.
`logs/` stores raw logs in deterministic gzip (`mtime=0`), including whitespace.
`log-ledger.json` records original and stored byte sizes and SHA-256 values;
decompressed bytes were verified identical. The failed intermediate runs retain
their diagnostics: the first had one reader-limit message mismatch; the second
exposed embedded-v1 quota error-category spellings. Both issues were corrected.
No exact source snapshot at those intermediate compilation times is claimed;
queued jobs could observe edits before the final source hold.
