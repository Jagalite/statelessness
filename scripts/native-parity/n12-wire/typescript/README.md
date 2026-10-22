# TypeScript N12 pipe consumer

`host.mjs` imports only the installed package's public entrypoints. The public
`serve` API owns request decoding, frame parsing, admission and response emission.
The host supplies the SPEC model and a stdout writer, and logs callback entry
synchronously to stderr. It accepts only `--read-only`, `--exact`, `--cache-one`.

After public `serve` resolves or throws, the fixture emits exactly one stderr line
`FINAL <state> <revision> <sequence>` from the public session getters. A SIGPIPE
handler prevents signal termination; synchronous complete-frame stdout writes
propagate genuine OS write errors through `serve`, followed by `FINAL` and a
nonzero exit. Closing the stdout reader after discovery and delivering one
accepted turn should therefore produce `FINAL 1 1 1` even though its response
cannot be written. These values are never inferred from requests or callback logs.

Root owns all builds and execution through shared-default Kyoto. No commands
below were executed by the implementation agent. Preserve `typescript/tsconfig.json`
and the pinned TypeScript dependency from `typescript/package-lock.json`.

For a new source snapshot `$SOURCE`, empty artifact directory `$ARTIFACTS`, and
fresh consumer directory `$CONSUMER`, use these argv/cwd pairs:

1. cwd `$SOURCE/typescript`: `npm ci --ignore-scripts --no-audit --no-fund`
2. cwd `$SOURCE/typescript`: `npm run build`
3. cwd `$SOURCE/typescript`: `node --test test/debug-wire-parity.test.mjs test/debug-protocol.test.mjs test/debug-session.test.mjs`
4. cwd `$SOURCE/typescript`: `npm pack --json --ignore-scripts --pack-destination $ARTIFACTS`
5. Copy `host.mjs` and `package.json` from this directory into `$CONSUMER`.
6. cwd `$CONSUMER`: `npm install --ignore-scripts --no-audit --no-fund $ARTIFACTS/jagalite-statelessness-0.1.0.tgz`
7. cwd `$CONSUMER`: `node host.mjs` (append scenario flags as specified).

Use the filename returned by `npm pack --json` if the package version changes.
Retain the package, source identity, stdout, stderr and exit status. Never run the
fixture via a checkout-relative `dist` import for installed-consumer qualification.
