# Publishing releases

The GitHub repository is https://github.com/Jagalite/statelessness.
The crates.io package is `statelessness`. The Rust library import remains
`stateless`.

Versions 0.1.0 and 0.1.1 were previously published. Registry metadata and archive
checksums for core and macro **0.2.0** were verified on 2026-10-12; both published
archives name commit `2af92dfdc0b0d0b0a76848a67bf9c4804b3bb116`. The debugger crate
was absent from crates.io at that check. The
[registry receipt](validation/releases/2026-10-12-registry.json) is availability
and identity evidence, not a new installed-consumer test. This checkout includes
newer changes under the same manifest version, so publication requires new versions.
See [capabilities](docs/CAPABILITIES.md), [CHANGELOG.md](CHANGELOG.md) and
[SUPPORT.md](SUPPORT.md) for source and qualification boundaries.

The representative `verify.yml` gate runs on pull requests and pushes to `main`.
Nightly, manual and reusable workflow runs use the full Rust 1.90/stable ×
Linux/macOS/Windows matrix, binding jobs and native/portable conformance workflows.
`release.yml` requires that full reusable verification workflow on the exact tag
before publication. Record each job's actual outcome and retain its qualification
manifest and logs. These gates being configured is not evidence that M0's new
matrix has run or passed. A representative PR pass is not the full release gate.
Nightly Linux/MSRV benchmark collection retains baseline samples separately; it
has no regression threshold and is not a PR or release qualification gate.
For local package verification, run `python3 scripts/check-consumer.py` from a
clean checkout. Build local bindings with `python3 scripts/build-bindings.py`;
retain its hash manifest with distributed native/Wasm artifacts. Crates.io
publishing does not also publish an npm package or distribute native binaries.

## ABI v2 distribution gate

ABI v2 remains opt-in while runtime qualification is pending. Follow
[the distribution guide](docs/ABI-V2-DISTRIBUTION.md) to stage the matching
headers, SDK sources, native/Wasm artifacts and source-bound receipts. The manual
`abi-v2.yml` workflow is compile/package inspection only; it is not a release or
runtime qualification gate. The local workflow continues to use Kyoto and
`--no-run` test/benchmark compilation.

Before changing any language default, require matching installed-consumer,
negative-loading, lifetime/error, complete portable-profile, fresh-process replay
and measured performance evidence for each claimed deployment mode. Use
`scripts/check-abi-v2-gates.py --require-ready` with the actual receipts; missing
gates fail. Preserve the old backend through a documented migration release and
retain rollback instructions. ABI 1 removal requires a separate breaking release
after its supported consumers migrate. Neither packaging command publishes or
switches a backend, and no retirement is authorized by a compile-only result.

## First publication

crates.io requires the crate to exist before configuring trusted publishing.
The initial publication uses a crates.io API token belonging to the crate owner.
Authenticate locally with `cargo login`; never commit the token or put it in a
workflow file.

After release verification is authorized, run against the final clean commit:

```sh
cargo fmt --check
cargo test --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
cargo publish --locked --dry-run
cargo publish --locked
```

Publication is permanent for that package version. The dry run verifies the
packaged crate as well as the local checkout. Record the published commit and
checks in the release notes.

## Trusted publisher configuration

After the first publication, open the crate's Settings → Trusted Publishing on
crates.io and add a GitHub publisher with these exact fields:

| Field | Value |
| --- | --- |
| Repository owner | `Jagalite` |
| Repository name | `statelessness` |
| Workflow filename | `release.yml` |
| Environment | `release` |

The GitHub `release` environment must permit version tags (`v*`). The workflow
requests `id-token: write` and exchanges its OIDC identity for a temporary
crates.io token using `rust-lang/crates-io-auth-action`. No permanent publishing
secret is needed in GitHub. Once a trusted publication succeeds, revoke any
bootstrap token no longer needed and optionally enable trusted-publishing-only
mode on crates.io.

## Subsequent releases

Update package versions and lockfiles, regenerate/check
`docs/CAPABILITIES.md` with `scripts/check-capabilities.py`, commit the release,
and push a matching `vVERSION` tag. Publishing remains manually dispatched;
ordinary PRs and pushes to `main` run verification without publishing.

Run **Publish to crates.io** against the version tag, or use:

```sh
gh workflow run release.yml --repo Jagalite/statelessness --ref vVERSION
```

The workflow requires full candidate qualification, rejects publication on non-tag
runs and tags that disagree with `Cargo.toml`, then performs registry package
dry runs before authenticating and publishing. Formatting, tests and Clippy run
in the required qualification workflow.
Publication on `main` is skipped. Do not rerun publication for a version
already published, including the initial version published during bootstrap.

See the [official trusted publishing documentation](https://crates.io/docs/trusted-publishing).

## Macro companion release gate

The macro companion is explicitly imported and does not add a dependency to the
core package. Before a supported macro release, run the existing Rust 1.90/stable
Linux/macOS/Windows matrix with workspace tests and `scripts/check-macros.py`.
The latter tests extracted core and macro packages with an empty Cargo home.
Review `docs/MACRO-STATUS.md`, macro/runtime protocol compatibility, codec migration
and the source-bound evidence bundle. Assign coordinated release versions and
publish the core exposing the protocol before the companion. Publishing is not
part of implementation qualification. Keep the native/browser binding jobs intact.

## Candidate evidence checklist

- Retain the exact source revision, dirty-tree status, toolchain/platform,
  dependency locks, command outcomes and artifact hashes. Failed, skipped and
  unavailable checks stay visible; a compile-only target is not runtime qualification.
- Run core, macro, debugger and packaged-consumer checks for the candidate. Include
  debugger compiled-out semantic parity, real stdio checks, native-port conformance
  and fault controls where the matrix declares support. Evidence from older source
  hashes does not qualify changed code.
- Review model/property/codec compatibility, binary trace version, macro protocol,
  callback ABI and portable profiles separately from package versions. Retain
  fresh-process replay and matching consumer results for the intended upgrade path.
- After publishing, read back the exact registry version and archive checksum and
  run the registry consumer check before claiming installation success. Update the
  dated availability receipt explicitly; the documentation check is offline.
