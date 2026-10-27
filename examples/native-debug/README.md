# Optional native debugger consumers

The Swift `DebugConsumer` is a finite local-package consumer of the separate
`StatelessDebug` product. The other native optional APIs and examples are tested
through their language test suites and installed qualifiers:

- Go: `go/debug`, `scripts/native-parity/qualify-n12-go.sh`, and the telemetry consumer qualifier.
- Python: explicit `statelessness.debugger*` modules, `python/qualify_debugger_installed.py`.
- TypeScript: `@jagalite/statelessness/debug` and explicit `debug/*` package subpaths; `typescript/test/debug-installed.mjs`.
- Swift: `StatelessDebug`, `scripts/qualify-swift-debug-installed.py`.

Run compilation, tests and consumer execution through Kyoto from the repository
checkout. Installed local candidates do not establish registry publication or
platform-wide support. Read each language's `DEBUGGER.md` for public contracts;
`docs/native-parity/DEBUGGER-DIAGNOSTICS-IMPLEMENTATION.md` records historical
qualification boundaries. Recovery evidence lives under
`validation/native-parity/n12-recovery`; historical job results do not qualify
later source edits.
