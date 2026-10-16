# Local runtime qualification

The user authorized runtime execution after the earlier compile-only draft.
Rust compilation uses Kyoto; Cargo test jobs record the policy-added `--no-run`.
Direct test executable receipts are separate from build receipts.

`summary.json` is the current bounded result. `initial-inputs.json` records the
starting checkout and `final-inputs.json` records the tested fixes and subsequent
documentation edits. Their commit is the parent commit: these manifests were
captured with the listed working-tree edits present, before committing evidence.
Use the file inventory digest to identify those bytes. `input-reuse.json` shows
that Rust compiler inputs did not change during this runtime pass.

`artifacts.json` identifies the engine binaries; each Rust executable also has
its own hash/compiler-artifact receipt. `swift-input-reuse.json` verifies all 25
staged inputs against their earlier compilation manifest. Staged Swift inputs
are unchanged; subsequent source README edits do not affect those executables.
This reuse does not attest a newly packaged distribution.

Initial failures remain under `before-*`, `*-before-fix`, `*-launcher-failure`
and `browser-first-result.json`. They include two product/corpus mismatches,
a macOS path assertion, loader configuration, and a browser fixture missing the
JSONL framing newline. The final browser fixture matches the normal harness.
The corrected Go depth-bound runner now passes both source and installed
five-runner reruns. Initial failures remain under the before-go-label-fix prefix.

The browser result is from the shared T3 Chromium/Electron browser, not a Node
browser mock. Its 717 requests include 694 semantic and 23 transport cases.
The corpus compares two engine families across five wrappers, and separately
checks 550 producer/consumer fresh-process replays.

No package publication, default switch, platform-wide pass or performance budget
is implied. Hosted workflows skip this draft PR. Final local packages and installed consumers now pass; their receipts are in
../merge-qualification. Other deployment targets and repeated measurements remain
adoption gates. Current-head hosted checks are tracked on PR #6.

The first installed-consumer attempt used the system Python without setuptools.
It failed before loading the engine. The packaging-tool virtual environment
then built the wheels and completed both qualified and final consumer runs.
The original doctest launcher omitted Cargo compile-time environment values;
the corrected rustdoc job preserves those values and passes all eight doctests.
