# Publishing releases

The GitHub repository is https://github.com/Jagalite/statelessness.
The crates.io package is `statelessness`. The Rust library import remains
`stateless`.

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

Update the package version and lockfile, commit the release, and push a matching
`vVERSION` tag. The workflow is deliberately manual: pushing commits or tags does
not build or publish anything by itself.

Run **Publish to crates.io** against the version tag, or use:

```sh
gh workflow run release.yml --repo Jagalite/statelessness --ref vVERSION
```

The workflow rejects non-tag runs and tags that disagree with `Cargo.toml`, runs
formatting, tests, Clippy and package verification, then authenticates and
publishes. A run on `main` is skipped. Do not rerun publication for a version
already published, including the initial version published during bootstrap.

See the [official trusted publishing documentation](https://crates.io/docs/trusted-publishing).
