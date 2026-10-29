# Focused N06 Go qualification

The current driver is **source-ready for future executions, not executed**.
The R2-3/R2-5 provenance changes and the later all-exit process-group cleanup
changes have only authored guard coverage. No preparation, job submission,
compilation or test execution is authorized by this edit.

The immutable r1 payload/job `9dc6574d3c8840f6b5cb7dd3d0e7ef3b`, frozen manifest
`0b053642eb9a341fcc9c8d99d309e5fbbddd3cd2794f0ac058ec27a0c2474238`, is unchanged.
Its historical queued `python3` invocation and sealed driver remain historical
inputs. Any result from r1 applies only to that payload. In particular, r1 lacks
the new executing-driver/tool binding and reaps a normal/nonzero child before
skipping group cleanup; its timeout/cancellation cleanup is a narrower boundary.
It cannot qualify the new provenance, retained-leader cleanup or guard code.
There is an explicit **post-r1 execution gap** until separately authorized fresh
preparation and Kyoto execution of a new sealed payload, following source review.

`qualify.py prepare <new-run-directory>` performs file copying and hashing only.
It records full file/directory membership, modes (including special permission
bits via `stat.S_IMODE`), sizes and SHA-256 hashes,
rejects symlinks/special files, and freezes a read-only snapshot of the Go module,
external example and runner. Conformance inputs are the 12 tracked baseline
fixture files explicitly listed in `SELECTIONS`, including the search history
JSON read beside `search-v1.tsv`. The unrelated, actively authored
`conformance/fixtures` tree is excluded. This is not execution evidence.

When a future run is authorized, submit exactly one execution through the
default shared Kyoto queue, using an explicit absolute Python executable,
the frozen runner, new run directory and returned frozen-manifest SHA-256:

```sh
<absolute-python-executable> /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  <absolute-python-executable> -I <run>/source/validation/native-parity/n06/go/qualify.py \
  execute <run> --expect-frozen-sha256 <sha256>
```

The queued SHA-256 argument is mandatory: missing or mismatched identity fails
before any command. Return the one submission receipt without waiting for
admission or starting a watcher. The runner does not submit or retry jobs.

Before any subprocess, `invocation.json` records `sys.argv`, `sys.orig_argv`
(where available), `__file__`, `sys.executable`, cwd, PID and queued manifest pin.
`driver-before.json` binds canonical executing `__file__` to the exact sealed
`validation/native-parity/n06/go/qualify.py` member, comparing its regular-file
type, full permission mode, size and SHA-256. An identical copy or hardlink at a
different canonical path is rejected. The literal sealed path and ancestors are
checked before resolution to reject substituted symlinks/special files.
`driver-after.json` repeats that comparison at the final receipt boundary.

Go and Python are selected once, with absolute selected and canonical paths,
selected link/type/mode and canonical executable type/mode/size/SHA-256 recorded.
On macOS a pinned absolute `/usr/bin/xcrun` performs queued `--find clang` and
`--show-sdk-path` discovery. The selected compiler must resolve outside
`/usr/bin`; a selector/shim result fails. The SDK directory becomes the explicit
`SDKROOT`. On other hosts `cc` is selected once from the controlled PATH. `CC`
contains the pinned absolute compiler, quoted for Go when its path has spaces;
paths containing quotes/newlines and inherited multiword compiler wrappers are
unsupported. Final macOS discovery must select the same compiler identity.
No discovery was executed while authoring this change.

All subsequent Go/Python/compiler commands use those canonical absolute paths.
The nested guard invocation uses pinned Python, `-I`, and the sealed inner driver
path. The runner checks driver/tool identities and the complete expected command
environment before Popen and after cleanup, recording both in each command
receipt. `toolchain-before.json`, `toolchain.json` and `toolchain-after.json`
retain pins/observations, with `DEVELOPER_DIR` and `SDKROOT` explicitly recorded.
Go configuration for both CGO-disabled and race (`CGO_ENABLED=1`) environments is
captured before tests and after installed execution, with matching absolute `CC`.
`toolchain-config-before.json`/`toolchain-config-after.json` include Go version,
GOROOT/GOTOOLDIR, architecture settings, environment/workspace/toolchain policy,
module/cache paths, proxy/checksum controls and CGO compiler/linker flags. A
configuration mismatch or identity mismatch fails the durable result, even
after otherwise successful commands.

The embedded driver guards run within the same focused job, after macOS compiler
and SDK discovery where needed. They cover membership/type/special modes,
hash/parse byte identity, same-byte wrong-path driver rejection, driver/tool
content/mode/missing/type drift, same-byte tool alias retargeting, source/frozen/
origin drift, actual launch-bound absolute argv and race CC/configuration,
configuration drift and rejection before Popen, unsupported wait primitives,
and normal/nonzero/cancel/timeout group ownership/order. Full mocked successful
executions are positive controls; final drift must fail despite zero-exit command
receipts. They keep real driver/command/receipt code and fake subprocess creation,
wait observations and group signals. They do not execute Go/Python/compiler
fixtures or signal real process groups. `guard-tests` is an internal job phase,
not an additional local or Kyoto invocation. These guards remain unexecuted.

The runner then executes all module tests with CGO_ENABLED=0, fresh-process replay of
both fault traces, focused campaign race tests, go vet, and an external example
installed from a private source-bound Go module proxy without a replace rule.
The example exercises real guided callbacks and a separate worker/rendezvous/
out-of-order path. Each command records exact argv, cwd, controlled environment,
timing, status and complete raw output. Commands run in their own sessions.
POSIX `waitid(WEXITED | WNOWAIT | WNOHANG)` observes exit without reaping; the
leader PID reserves the group identity through every group signal, including
after normal and nonzero exits. A live-or-unreaped-child observation precedes
each TERM/KILL; loss of child ownership prevents further group signals and
fails the receipt. Cleanup uses TERM, a bounded grace period, KILL, then leader
reaping. Timeout, `KeyboardInterrupt`, other `BaseException` failures and
catchable SIGINT/SIGTERM/SIGHUP use the same owned cleanup. Signals are deferred during
spawn handoff and cleanup so the process handle and final receipts are retained.
Raw logs are never truncated, and each command's `finally` writes
`command-<name>.json` plus an atomically replaced `commands.json` aggregate.
Failures stop subsequent phases. The OS/Python boundary requires working POSIX
waitid/WNOWAIT and process groups; missing support or a failed non-child syscall
preflight rejects before spawning. There is no reaping `process.wait(timeout)`
or `poll()` before group signalling, nor a later probe/signal of a reaped PGID.
This assumes the driver is the sole child reaper; child descendants that escape
its process group are outside that boundary. SIGKILL or machine loss cannot run
cleanup. None of this new cleanup has runtime qualification yet.

Installed consumer/module/cache/proxy/executable inputs are observed before and
after, including failures. Startup hashes and parses the same `frozen.json`
bytes and retains the queued expected hash and parsed manifest. Completion
rechecks both `frozen.json` and `origin.json` against their pinned and startup
identities, plus full snapshot membership, before recording success.
`identity-before.json` and `identity-after.json` preserve observed/expected hashes,
errors and changed source members. Every detected identity failure makes
`result.json.status` failed as well as returning nonzero, including startup
rejection and errors after otherwise successful commands.

The identity claim is bounded: these are on-disk path/type/mode/content
observations at startup, command boundaries and final receipt. Hashing
`sys.executable` does **not** cryptographically prove the interpreter image or
Python code already loaded in memory, nor which bytes an earlier launcher loaded.
The absolute initial Python invocation records intent and removes PATH lookup;
it is not a queue-time interpreter-content pin. Dynamic libraries, Python imports,
Go's helper tree under GOROOT/GOTOOLDIR, SDK contents, linker/assembler and other
transitive dependencies are outside this content seal. Compiler/SDK paths and
Go configuration are recorded, not proof of full toolchain closure. The checks
also cannot exclude changes restored between observations or a race between
checking a file and loading it. These limits must accompany any future pass.

This is one-host, focused Go evidence. It does not execute the public Rust
cross-language comparator, close the N05 corpus qualification, qualify full-u64
job-count wire states, publish a module, promote a profile, or establish platform
parity. Root-owned integration retains those separate gates. A submission or
admission is pending evidence; a pass requires a successful `result.json`, identity
receipts and complete command logs.
Never reuse a run directory, alter a queued snapshot or restart another owner's
job. Source changes require a separately coordinated identity after existing
live work has finished; preserve failed and superseded receipts.
