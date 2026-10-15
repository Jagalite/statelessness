# Merge qualification

This directory refreshes the additive ABI v2 candidate after runtime testing.
`build-inputs.json` records the tested implementation commit and complete source
inventory. `build-receipt.json` binds Kyoto native and separate v1/v2 Wasm builds
to that inventory. Subsequent documentation/evidence commits do not alter those
inputs. Historical prior-build receipts are retained explicitly.

`package-manifest.json`, native/bootstrap-negative-loading and browser inspection
reports identify final staged bytes. `archive-inspection.json` checks npm/wheel
member bytes against the staged sources. `installed-consumers.json` records the
wheel and npm installation outside the checkout, actual typed model tests, and
fresh-process binary replay. Go and Swift build/test receipts are separate;
`installed-compiler-input-reuse.json` verifies exact compiler input bytes after
the CI-only helper-build correction. `installed-executable-identities.json`
identifies the compiled programs. Installed Python/Go/Swift plus the source Node
fixture runner pass all 717 corpus cases each and 550 cross-producer replays in
`installed-conformance-report.json`; Node package installation is separately
qualified by its typed-model consumer.

The first hosted ABI v2 check lacked the TypeScript helper build needed by the
fixture adapter. Its failure log is retained. The corrected workflow builds the
helpers. Current-head hosted results are tracked directly on PR #6, including
the new Linux/macOS ABI v2 check and the existing native/portable platform checks.
A skipped or failed check cannot establish a merge pass.

These are merge tests for an additive opt-in implementation. They do not declare
all migration phases complete. Default adoption, performance/memory budgets,
remaining deployment cells, publication and retirement remain separate gates.
