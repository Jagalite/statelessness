# Independent N06 specification author: queued

Job `1be30f3a8bea47758dd063d93a8970cb` is queued in the shared default Kyoto.
Owner `998c92d7-4b7a-4c1f-9028-6686060d0661`.
No expected JSONL or FREEZE exists until this job actually executes. Its output
will be specification materialization, not native/Rust runtime qualification.

Immutable author source manifest:
`31fc562b38c012eb6712a81dea1aebbbfef5e8a2ce9d75d4c51bcae22e4271d4`.
Sealed contract source SHA-256:
`6c232b3961e4c4ed52d49f55ed1182f22be9080e7c1bbc37b7445bdd370c2082`.
The five input files are copied read-only under `source/`; the compact manifest
records exact paths, modes, sizes and SHA-256 values. Do not edit those inputs or
the corresponding contract/author/schema in place while this author is pending.
The author compares live source identities with its immutable inputs before
materialization and fails on drift. New source requires a distinct author seal.

Exact requested/effective command (cwd is the assigned checkout):

```text
python3 validation/native-parity/n06/rust-goldens-r1/source/conformance/fixtures/native-campaign-author.py --workspace /Users/jagatranvo/.t3/worktrees/stateless/t3-25250336 --output /Users/jagatranvo/.t3/worktrees/stateless/t3-25250336/validation/native-parity/n06/rust-goldens-r1/artifacts
```

Environment: `PYTHONDONTWRITEBYTECODE=1`.
Resource: absolute `.../validation/native-parity/n06/rust-goldens-r1/artifacts`.
Kyoto log:
`/Users/jagatranvo/.local/state/kyoto/1be30f3a8bea47758dd063d93a8970cb.log`.
Complete submission and last show metadata are retained as `submit.json` and
`show.json`. No duplicate author job has been submitted.

After successful execution, the aggregate preparer can use source-relative paths:

```text
--expected validation/native-parity/n06/rust-goldens-r1/artifacts/expected.jsonl
--freeze validation/native-parity/n06/rust-goldens-r1/artifacts/FREEZE.json
```

The author prints both artifact SHA-256 values for the required preparer flags.
The `n06-independent-spec/1` seal includes all 49 ordered case IDs and separate
15-seed/15-validation/19-campaign inventories. The seed inventory uses independent
masked-integer SplitMix arithmetic; validation errors/bounds and callback cases
are authored without invoking or importing any native/Rust engine or reading
their output. Exact string/numeric types, metadata presence, control/error
classification, callbacks and array order remain significant.

Known Swift feedback-metadata panic classification remains an exposed open N05
dependency; the Rust expected Panicked row is retained. N05's author90/FREEZE
status remains independently pending. No default/profile/144-cell promotion.
