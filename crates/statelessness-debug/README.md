# statelessness-debug

Optional dependency-free, headless transition debugging for Statelessness.

- Typed one-input simulation, exact replay, bounded differences and verified forks
- Read-only structural inspection, optional derives, runtime watches and lazy probes
- Versioned stdio protocol with explicit authority, revisions and bounded retries
- Host-owned effect timing, bounded metrics and opt-in cooperative live safe points

Effects remain application-owned. Replay never executes them. Diagnostics are
redacted projections, separate from sensitive raw exact trace evidence.

From the repository checkout:

```sh
cargo run --offline -p statelessness-debug --example debug_request -- interactive
python3 scripts/check-debugger.py
```

See `docs/DEBUGGER.md` and `docs/debugger/ACCEPTANCE.md` in the repository for
integration, limitations and reproducible qualification. This implementation has
not been published to a package registry as part of its local qualification.
