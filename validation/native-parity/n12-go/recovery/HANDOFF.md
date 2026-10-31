# Recovered Go N12 candidate handoff

Worktree `/Users/jagatranvo/.t3/worktrees/stateless/t3-ccd33c8a`, branch
`t3/recover-n12-debugger-parity`, base `732578595b16052a8f922f122b41d7091258b476`.
No commits were made by the Go subagent. No old tree or old job was modified.

## Source fixes

- `go/debug/session.go`: public state/observation/candidate clone projections
  guard their application callbacks against reentrant session mutation. Session
  and policy identities reject invalid UTF-8 rather than accepting identities
  Rust strings cannot represent.
- `go/debug/protocol.go`: revision/configuration checks follow the actual Rust
  per-command contract. Read-only telemetry/catalog/event commands do not depend
  on a model revision; checks/outputs bind handles; unwatch needs configuration;
  watch also needs model/baseline revision. Authorities precede mutation checks.
- Prepared protocol responses reserve widest revision/configuration fields before
  Commit. Built-in mutation acknowledgments also preflight before mutation.
  Decoder and read-only adapter Prepare callbacks cannot mutate the session.
- Tests cover these regressions and real OS-pipe duplicate frames/partial frames.
- `scripts/native-parity/qualify-n12-go*.sh` and manifest helper capture stable
  membership/type/hash identity, compare copied input with captured source, and
  fail the qualification on any live source change through command completion.

## Executed final qualification

All commands were submitted through default Kyoto from this worktree.

- `ad8aa61b6a6a42a4bb63734da417ca31`: SUCCESS, full `go test -count=1 ./...`,
  `go vet ./...`, then a separately compiled copied-module consumer in a fresh
  process. Includes Go debugger modules and OS-pipe regressions. Raw log 2758
  bytes, SHA-256 `58a1e51f392771f94e98634a74a586dc6d0d51ba4ef09aa1e74d92242737a579`.
  Frozen source, consumer, binary and manifests: `../staged/tmp.6jda5eGVoN/`.
- `3c36c33374fa4809ada97c6be7ed14d9`: SUCCESS, distinct fresh copied-module
  metrics/dashboard/cooperative-live-gate consumer. Raw log 1336 bytes, SHA-256
  `5694377c86d380f4304e4478951017c306c09a9227f01f6e3d4c1935c0e5863d`.
  Artifacts: `../telemetry-installed/tmp.TS9eAe4WEy/`.
- Both final jobs have identical live-before/live-after identities:
  `ed8f21f14761bbd88ff1e14ce40c92bc336857e5b1d796663aba62679d3ceba3`.
  All scheduler metadata and lossless raw log gzip files are retained here.
- Intermediate passes `5e8b7cceecf748eb924d625d9f71fba1`,
  `10bee655360049b8b4465b356cc583fb`, `66edb39b2f2e4f97b3483e8504d2028f`
  are retained, but precede final source fixes and do not qualify the final source.

No Go jobs remain live at handoff. This is a macOS arm64 Go 1.24.2 source-candidate
qualification, not a release, registry installation, platform matrix, complete
N12 closure or profile promotion. Cross-language real public Rust-reference
comparison and broader root integration qualification remain root-owned gates.
Independent reviewer statically re-read command-specific guards, response
preflight/reservation, decoder/Prepare ownership and public clone guards: no
remaining concrete finding within that finite scope. The reviewer did not
independently execute runtime tests. Root retains the review-final receipt.
