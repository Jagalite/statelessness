# N07 final driver corrections

Source-only implementation handoff, 2026-10-22, shared isolated worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.
During review the parent committed the API/fixture/consumer layer as
`c5ab108940254d60a840ac47616d7819c1a4af79`; these four driver-owned files
remain untracked and uncommitted. Neither commit is the final integrated
qualification pin.

This author changed only `scripts/check-native-observation-consumers.py`,
`scripts/qualify-native-observation.py`, `scripts/test_observation_receipts.py`,
and this new note. Shared native implementations, fixtures, process/provenance
helpers, other tests and the parent-owned consumer README were preserved.
No applicable ancestor or in-tree `AGENTS.md` was found. The required
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read. No Python
driver/helper/implementation, syntax check, test, compiler, build or Kyoto job
was executed or submitted. Nothing was staged, committed, pushed or opened as
a PR. The parent owns the integrated freeze and single shared qualification.

## F1: isolated generated Python runners

Both generated unittest runners now live in separate directories below
`<attempt.work>/python-runners/`. Their source is explicitly registered;
directory membership and bytes are watched before the first launch, after each
command including failure, and at finalization. The two directories have
independent baselines, so creating the second runner cannot rebaseline the first.

The exact executable prefix is **`python3 -I -B`**, including both documented
entry points and the generated Go `--go-exec` Python wrapper. The two entry
points check those settings before importing standard-library modules other
than builtin `sys`. The parent updated its four README continuation commands
to the same flags. For example, the actual command submitted through Kyoto is
`python3 -I -B scripts/qualify-native-observation.py --scope native ...`.
The alternate check-driver entry uses
`python3 -I -B scripts/check-native-observation-consumers.py ...`.

`-I` removes cwd, script-directory, environment-supplied Python paths and user
site from the startup import search; `-B` disables bytecode writes. The generated
runner imports unittest under that isolated startup configuration, then adds
only its explicit sealed test directory and, for native Python, sealed
`python/src`. It uses an absolute discovery root and top-level test directory.
It does not rely on `PYTHONPATH`, which isolated mode ignores. Existing validator
module/name/count checks and rejection of skipped/expected-failure validator
tests remain. Python's explicitly tracked native-suite skips remain separate.
All APIs used support Python 3.11+.

## F3: source-byte driver and helper loading

Dynamic loading now uses `compile(path.read_bytes(), filename, 'exec')` and
executes that code in the module namespace. `module_from_spec` supplies module
metadata only; cache-aware `exec_module` is not used for helper loading, the
qualifier's initial check-driver bootstrap, or the check-driver reverse entry.
Thus valid timestamp/size caches and unchecked-hash caches cannot supply
different helper/driver code while the `.py` identity stays unchanged. The
receipt test's own driver bootstrap uses the same source-byte rule.

The isolated staged suites retain complete tree watches on their Python sources
and tests, including cache-file additions. Fresh staging copies sealed inputs
and never reuses ignored checkout bytecode. This is not a replacement import
system for the host standard library or trusted installed tooling.

## F2 and first consumption of installed/build outputs

The installed driver watches these outputs immediately after production or
identification, before their first consumer:

- Python installed `venv/lib`, before importing `statelessness` to inspect its
  installed location.
- TypeScript package `dist`, before `npm pack`; installed package after install,
  before declaration compilation or JavaScript graph inspection; generated
  consumer JavaScript immediately after declaration compilation.
- Go consumer executable immediately after build; Swift consumer immediately
  after identifying its output directory and before authority controls.

Later runtime and suffix watches reuse these identities; they cannot silently
accept changed bytes as new baselines. Existing source/config registration,
generated authority API controls and the staged suffix hook remain in place.
The separate, parent-owned `observation_suffix.py` was found to have the same
late-watch interval for its additional Go/TypeScript/Swift consumers. Immediate
watches there were reported to the parent; this author did not edit that helper.
Its disposition remains a parent integration item before freezing sources.

## Nested Go capture and scoped environment

`Attempt.finish` first inventories all raw log paths and every nested Go wrapper
directory, including incomplete directories. Before hashing any aggregate raw
log, it reconciles outer and nested `started.json`, `finished.json` and receipt
command entries, explicit stream paths, complete journal/log membership, nested
wrapper status and both stability/termination flags. An outer command's stable
capture cannot certify an uncertain nested writer. Missing or conflicting
evidence fails finalization and retains paths without a final aggregate hash.
An original timeout/error remains the primary error; reconciliation failure is
also retained. No growing uncertain raw file is hashed merely to discard its
hash afterward.

A complete stable nested nonzero exit retains its exact binary logs and causes
qualification failure. Stable logs are compared with their already-recorded
stream identities, not silently rehashed into a replacement baseline. A nested
wrapper exception can legitimately have a different wrapper exit code from its
command. Go's temporary test executable need not still exist at aggregation:
its before/after identities come from the finished wrapper's own capture.

The environment now sets exactly `GODEBUG=panicnil=0` and includes it in every
command's recorded environment. Inherited `panicnil=1` cannot silently alter the
default-Go profile; unrelated inherited GODEBUG switches are also removed by
replacement. This control does not claim qualification of the legacy panicnil
profile. Darwin `proc_pidinfo` corrections remain owned by the process author.

## Authored controls and retained boundaries

All 82 pre-existing validator source methods remain. This patch adds 14 methods
to the receipt suite (33 to 47), with parameterized cases for:

- Valid poisoned timestamp and unchecked-hash caches, both CLI bootstrap
  directions, required entry flags, workspace/PYTHONPATH shadows, explicit
  sealed source imports and runner membership before/after execution.
- Actual installed-driver watch ordering using literal inert build outputs,
  an unchanged positive control and persistent mutation before each relevant
  first consumer. No compiler or package implementation generates these inputs.
- Stable successful/failed nested captures, absent/false stability, interrupted
  publication, journal/status disagreement, changed bytes, orphan logs, mixed
  children, complete early-failure inventory and preservation of original errors.
  Unstable controls explicitly detect any attempted raw hashing.
- Inherited `GODEBUG=panicnil=1` replacement and command receipt recording.

The source inventory at drafting is 47 receipt + 30 compile-proof + 21 process
methods = 98; the process author's two new methods are outside this patch.
These are authored method counts, never executed/pass counts. Existing compile
proof and process controls were not rewritten. The required rustup resolution,
compiler-artifact binding, original source pin and receipt-only HEAD advance
rules remain intact.

The pinned Rust observer/monitor sources and observation/monitor tests were
inspected alongside N07, the historical checked-results audit, the prior PROOF
review and FIXES-PROVENANCE/PROCESS notes. Existing checked tokens, periodic
checking and bounded Recorders remain authoritative. Diagnostics receive
already executed values/checks; no diagnostic path runs reducers, checks or
effects, and an event confers no continuity or checked authority.

This remains nonmalicious source/artifact provenance on the documented host,
not cryptographic attestation or OS containment. Trusted standard-library and
toolchain installations, sampled foreground process ownership and the process
helper's documented race/daemonization limits still apply. Source-byte loading
does not establish an atomic source-read versus concurrent replacement proof.
Compilation, native suites, installed consumers and suffix matrices all remain
unexecuted for this source revision. No N07 completion claim follows.

## Independent review

A separate source-only nested-receipt audit completed as T3 task
`node:delegated-task:command%3Amcp%3A368e84e3-7187-433b-8b97-40626cd1e996%3Adelegate-task%3An07-driver-nested-advice-20261022-r1`.
Its recommendations are incorporated above. It used `codex`, `gpt-6-astra`,
`reasoningEffort=ultra`, `serviceTier=default` and ran no candidate code.
The required fresh final independent source review completed as T3 task
`node:delegated-task:command%3Amcp%3A368e84e3-7187-433b-8b97-40626cd1e996%3Adelegate-task%3An07-driver-final-source-review-20261022-r2`,
with the same explicitly supplied provider/model/options, confirmed in the
delegation response. It reported **no actionable findings in these four owned
files**, verified the three code hashes below, and found the synthetic controls
consistent on source inspection. Its parallel fixture reviewer also reported
no actionable findings. No reviewer edited files or executed candidate code.

The reviewer confirmed one **P1 parent integration item** outside this author's
ownership: `observation_suffix.py` builds the additional Go and TypeScript
consumers before their first identity watches, allowing an intervening persistent
modification to become the accepted baseline. Add immediate output watches and
reuse them in its final runner/application inventory before the parent freeze.
Swift should likewise be watched as soon as its executable is identified.
This review does not authorize qualification while that item remains unresolved.
Python 3.11/macOS behavior, all 98 authored validator methods, installed consumers
and suffix matrices remain runtime-unverified; Darwin internals were excluded.

The source bytes submitted to that review have these SHA-256 identities:

```text
1b6f5db4ba98cc6e296a52a18535f61f784894e14e02ae1069e42ef16c877602  scripts/check-native-observation-consumers.py
3c9032afbe8eb136b18c80797ec70b58688de7d49ec8168597ceb42f1dc5344e  scripts/qualify-native-observation.py
1e252031d42d6f885b412138428656e1965d86308d49d7f8db34e65a8fa93286  scripts/test_observation_receipts.py
```
