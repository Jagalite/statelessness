# Python campaign application

`consumer.py` uses only the installed public `statelessness` API and the Python
standard library. Source-only implementation; no build or execution receipt is
claimed. Submit all execution through the shared Kyoto queue. This example does
not use the private lifecycle adapter or replace the campaign scheduler.

The default `--demo` entry runs ordinary and guided campaigns for `(workers,
jobs)` values `(1, 3)`, `(2, 1)` and `(2, 3)`, each with a zero-capacity result
buffer. Top-level callable factory objects are compatible with real
`multiprocessing` spawn. The `__main__` guard prevents recursive CLI execution.
Model metadata is required; optional `campaign_close` hooks show feedback
teardown before model teardown and both before delivery to the caller's sink.

The application defines `Command`, `CounterState`, `CounterOutput` and
`DepthFeature`. They support faithful deep copies and deliberately reject
pickling. Models and feedback also reject pickling and own local locks. Only
report inputs cross workers, using `InputTransfer` with the spawnable
`CommandTransfer.encode` and a caller-local decoder closure wrapping
`CommandTransfer.decode`, with a strict eight-byte application format. This is
separate from `ModelCodec`. Ordinary failures and guided corpus entries both
exercise real report-input transfer. Observed process/thread identities, callback
logs and transfer events appear in the demo's JSON. Delivery order is retained.
`--evidence /absolute/new/directory` retains callback/transfer logs and each
completed run even after failure; without it, a retained temporary directory is
printed on stderr. Both paths check input values and corpus prefix boundaries.
The example does not establish deterministic multiworker order, two-worker
overlap, slow-sink bounds, lost-worker recovery, or all lifecycle laws; the N06
host qualification harness owns those checks.

The consumer requires `-I -B`, a virtual environment and a noneditable archive
installation. It checks that the package and distribution are inside that
environment and verifies loaded package modules against wheel RECORD hashes.
It never adds the checkout to `sys.path` or reads expected output. The installed
runtime has no additional package dependency.

## Rust public fixture adapter

`--case ID` emits one `native-campaign-v1` JSON object. `--all-reference` emits
49 rows in Rust fixture order: `seed-00` through `seed-14`, the 15 public
validation cases, and the 19 ordinary/guided campaign cases. Input scenarios and
projection are transcribed from
[`native-campaign-public.rs`](../../../conformance/fixtures/native-campaign-public.rs)
and [`N06-RUST-CONTRACT.md`](../../../docs/native-parity/N06-RUST-CONTRACT.md).
Every counter/config integer is a decimal string; nulls and callback/delivery
order are preserved. Case names unknown to this source fail argument parsing.
This is the current 49-case adapter slice on a 64-bit reference host. If the Rust
author expands or changes its protocol, reconcile the inventory before freezing;
the optional comparison reports missing required cases and never treats this
slice as full coverage of a later fixture revision.

The Q1 operand reconciliation follows the current independent Rust fixture with
the common count value `S = 9007199254740991` (`2^53 - 1`, `PORTABLE_MAX`):

- `aggregate-cases`: `jobs=2049`, `fuzz.cases=S`.
- `aggregate-transitions`: `jobs=2049`, `fuzz.cases=1`, `fuzz.max_steps=S`,
  `fuzz.max_transitions=18446744073709551615` (`u64::MAX`).
- `wide-product-capped`: `jobs=2`, `fuzz.cases=S`, `fuzz.max_steps=S`,
  `fuzz.max_transitions=1`.
- `max-job-count-valid`: `first_job=0`, `jobs=S`, `fuzz.cases=1`,
  `fuzz.max_steps=1`, `fuzz.max_transitions=1`.

These are exact input operands; outputs are neither clamped nor normalized.
Full-width native count-boundary qualification remains separate from these
shared rows. The exact 49-case membership and full-width seed/job-ID inputs are
unchanged. Post-r1 build, installed execution and exact Rust comparison remain
pending for this source revision. Frozen r1 artifacts are unchanged and do not
qualify the operand reconciliation.

Current native policy differences are intentionally visible in the actual rows:

- `step-error` and `check-error` retain Python's existing `transition:` and
  `state check:` prefixes, where Rust reports `step:` and `check_state:`.
  `initial-error` uses `initial_state:` in both engines.
- `mutation-101` and `validation-first-error` encounter the existing public
  `FuzzConfig` constructor guard before campaign validation. The adapter does
  not bypass that guard or rewrite its error to the Rust string.
- `feedback-metadata-panic` retains the public guided wrapper's ModelError
  classification for a RuntimeError from feedback metadata, with model metadata
  present. Rust records Panicked. The native callback policy is unchanged.

These rows are observations, not passing cross-language receipts. An exact
comparison must fail until independently approved policy dispositions exist;
this example neither edits N05 nor normalizes away the differences. The private
lifecycle protocol and guidance-validation constructor differences are outside
this public fixture inventory.

## Fresh wheel and sdist qualification

From this checkout, the later authorized execution command is:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 -I -B python/tests/qualify_campaign_install.py --driver-sha256 "$N06_PYTHON_DRIVER_SHA256" --output /tmp/n06-python-installed-attempt-01
```

Before submitting, the root must select the reviewed driver from its new source
seal and set `N06_PYTHON_DRIVER_SHA256` to that authenticated SHA-256. Do not derive
the expected hash from the executing candidate as part of the invocation. The
driver requires the explicit value, captures its original regular-file identity
before commands, rejects symlink ancestors and parent-traversal spellings, and
rechecks the original file in `finally`. Its copied bytes are archival evidence,
not proof of what the interpreter loaded. These file checks do not attest
interpreter memory or hostile concurrent source replacement; the root's reviewed
launch identity and immutable source selection remain prerequisites.

Use a new output directory for every attempt. The driver snapshots the package
and consumer, builds a wheel and sdist in an external builder venv, rebuilds the
sdist to a wheel using that builder, then installs each wheel in its own fresh
runtime venv. Both run the six application scenarios and emit every reference
row in a fresh process. Neither runtime uses source imports or builder dependencies. The driver
retains exact commands, separate stdout/stderr, exit status, tool versions,
source/artifact/install hashes, failed receipts and raw actual rows.

The standalone driver requires POSIX `waitid(WNOWAIT)`, descriptor-relative
no-follow filesystem operations and `ps`. It retains the command leader until
owned-group checks/signalling finish on every exit path, then reaps it. Lingering
group members fail qualification. Inventories include root/directory/type/mode
and file content identity, reject symlinks and special entries, and retain partial
failures. Only descriptor-relative lookup can establish an allowed missing path;
invalid or inaccessible ancestors fail the observation. Independently guarded
final captures cover the original driver, package metadata, package source,
example, authorities and attempted source/build/archive/installation/consumer
stages even after interruption. A final receipt-write failure triggers a failure
receipt correction; if correction also fails, the driver attempts to retain any
bytes under `qualification-unconfirmed.json` rather than the authoritative path.
These post-r1
corrections and their regression source remain unexecuted; the immutable focused
r1 job does not cover them or the corrected example operands.

By default this qualifies only those installed applications; Rust equality
remains `not_requested`. Once the independent Rust author publishes a sealed
JSONL fixture, append `--expected /absolute/path/to/expected.jsonl --freeze
/absolute/path/to/FREEZE.json`. Both files must be in the checkout. The seal must
use the shared `n06-independent-spec/1` contract and bind the rows, Rust contract,
source authority, public Rust fixture and case inventory. Missing or changed expectations fail before
building. Only the driver reads them; it compares each required row exactly,
preserves additional actual cases, and retains all mismatches. It never authors
or regenerates expected values from Python output.

After qualification has prepared a runtime and copied consumer, an individual
case can also be submitted through Kyoto, for example:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- /tmp/n06-python-installed-attempt-01/work/runtime-wheel/bin/python -I -B /tmp/n06-python-installed-attempt-01/work/consumer-wheel/consumer.py --case guided-basic
```

No staging, commits, baseline edits, N05 corpus changes, N05 job operations,
aggregate submission or qualification execution accompanies this source change.
