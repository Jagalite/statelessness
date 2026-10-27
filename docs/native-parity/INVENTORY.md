# Pinned Rust source inventory

`rust-baseline.json` bootstraps the N01–N14 ledger against commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`. It is a conservative lexical candidate
inventory, **not a completed semantic export audit or a parity claim**. M0 remains
open until the pending reviews below are complete.

Every Rust file under `src`, `crates/statelessness-macros/src`, and
`crates/statelessness-debug/src` is pinned by SHA-256, including private helpers,
FFI alternatives, CLI/demo/conformance/research code and embedded tests. Standalone
integration tests, examples, build scripts and other crates are outside this source
inventory and remain N14 tooling audit work. File-set and content drift fail the
checker rather than silently expanding the baseline.

Each entry contains a source locator, declaration kind, lexical visibility, gap
mapping, mapping-review state and explicit Python/Go/TypeScript/Swift states.
`not_started` means the **parity audit of this candidate** has not started; it does
not mean the language has no implementation of the behavior. Existing portable
features remain as described in the main plan. No implementation or qualification
is inferred from Rust declarations.

## Scanner boundary and remaining review

The standard-library-only scanner blanks line comments, nested block comments,
quoted strings, raw strings and character literals while preserving locators. It
records public declarations, public named fields and `pub use` declaration sites.
Reexports are one candidate per declaration site: grouped names, aliases and glob
expansions still require semantic resolution. It also records all `fn`, `type` and
`const` candidates without explicit visibility as `implicit_or_private`, so trait
members and trait implementation methods are accounted for without claiming that
private helpers are exported APIs. These counts must never become a public API or
native gap count.

The scanner does not resolve module reachability, Rust namespaces, `cfg` activation,
macro expansion, generated methods, blanket implementations, enum variants, tuple
fields, associated-type reachability, or attribute/procedural macro external names.
Those are explicit pending semantic audit work. Source hashes cover changes to
these constructs even where there is no separate lexical entry. Function-local
items, test-only declarations and private module contents may be included. Mapping
by module is provisional; multi-feature operations and shared model traits need
reviewed secondary mappings and language API/receipt links before milestone closure.

Edit `GAPS`/`gap_for` in `scripts/native_parity_inventory.py` to review the source
module-to-work-item policy. Unknown modules fail generation. `explore` uses N03 for
names containing `fuzz`, N04 for names containing `shrink`, and N01 otherwise;
review the remaining helpers and shared report types individually. Macro source
maps to N13 and debugger source to N12. FFI surfaces map to N14 for explicit
accounting; calling them cannot satisfy independent-engine parity.

## Commands

Run from the worktree, through the shared Kyoto queue:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native_parity_inventory.py
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 -m unittest discover -s scripts -p test_native_parity_inventory.py
```

The first command validates the committed inventory against pinned Git content
and the working Rust source tree. It detects duplicate IDs, missing/invalid gap
mappings and missing language cells. It deliberately rejects implementation or
qualification assertions in this bootstrap artifact; reviewed capability evidence
belongs in the capability ledger with API mappings and receipts.

To regenerate after reviewing scanner/mapping changes, submit the inventory command
with `--write`. It always reads the explicit baseline commit, never silently adopts
HEAD. Changing the baseline requires a reviewed source-identity update. Regeneration
resets provisional statuses and is not a mechanism for retaining qualification
records.
