# Rust reference work receipts

Working directory:
`/Users/jagatranvo/.t3/worktrees/stateless/t3-cb476da7/scripts/native-guided-consumers/rust`.

The actual initial submission was:

```sh
CARGO_INCREMENTAL=0 python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit --user-id 4f1ede2b-716b-4d8e-b73b-331efcd57509 -- cargo build --manifest-path Cargo.toml
```

Job ID: `717c02a2dfd84bd49c7da261f0055201`. Requested/effective commands are in
`build-01-submit.json`; final/current status is in `build-01-show.json` and the
accumulated raw job output is `build-01.log`. Retrieve them with:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py show 717c02a2dfd84bd49c7da261f0055201
python3 /Volumes/seed1/Projects/kyoto/kyoto.py logs 717c02a2dfd84bd49c7da261f0055201
```

The first attempt fetched dependency metadata, wrote `Cargo.lock`, and was stopped
by Kyoto for sustained memory/swap pressure. It remains the same queued/deferred
job, not a second submission. The captured deferral is retained separately in
`build-01-pressure-deferred-show.json` and `build-01-pressure-deferred.log`.

Corrections before any successful build/runtime comparison:

- Corrected the Cargo dependency relative path from four parents to three while
  the job was still initially queued; the production dependency is the repo root.
- Followed independent schema clarifications: public helper pushes are opaque,
  replay checks are phase-tagged, model codecs are little endian, and shrink is
  selected exclusively by input `shrink_config`.
- Followed revision 3: actual dispositions in callbacks/replay and selected
  original/minimized failure replay evidence with actual public replay results.

The source manifests retain those revision boundaries; `source-ready.sha256` is
the current consumer identity. `authority-head.txt` and `authority-source.sha256`
identify the inspected Rust authority. These are metadata receipts, not proof of
successful compilation or execution.

There has been no candidate comparison, so no older expected result was used or
rewritten. After this existing build succeeds and the current independent freeze
exists with matching hashes, submit the focused runtime command in the consumer
README. Its `verify.py` always creates a new output directory, retains the raw
host/corpus execution logs and commands, checks the actual engine output against
all six independent expected fields, and records binary/freeze hashes.
