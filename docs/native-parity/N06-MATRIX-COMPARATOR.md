# N06 five-engine comparison component

`scripts/qualify-n06-matrix.py` compares five caller-prepared consumer commands
against the independently authored, SHA-256-pinned 49-row N06 oracle. It requires
all five engine names and exact ordered, type-sensitive JSON rows. A missing row,
extra row, changed value, duplicate JSON key, timeout, nonzero exit, oversized
output, or changed pinned artifact fails the run. It retains command metadata,
stdout and stderr, hashes and a result JSON outside the checkout.

The command manifest is caller-authored JSON with schema
`n06-installed-commands/1` and an `engines` object containing exactly `rust`,
`python`, `go`, `typescript`, and `swift`. Each entry has:

- `argv`: a nonempty argument array with an absolute executable; the command must
  output the 49 rows as JSONL and exit zero.
- `cwd`: an absolute directory outside this checkout.
- `consumer`: the absolute canonical path of the consumer file, also an argv
  member. All five consumer file hashes must differ.
- `artifacts`: a mapping of absolute canonical regular-file paths to lowercase
  SHA-256 hashes. It must include the executable, consumer, and any file supplied
  as an argv member. Include installed package files and other execution inputs.

For example, an entry might use `{"argv":["/absolute/venv/bin/python",
"/absolute/consumer.py","--all-reference"],"cwd":"/absolute/work",
"consumer":"/absolute/consumer.py","artifacts":{"/absolute/venv/bin/python":
"<sha256>","/absolute/consumer.py":"<sha256>"}}`; these paths and hashes are
placeholders, not a complete manifest. Resolve symlinks before recording paths.
Inline command flags are rejected. The runner removes Python/Node import overrides
and disables Python optimization; stdout and stderr are captured with a bound and
nonzero, timeout or malformed output cannot pass.

After preparing all five commands and independently choosing the manifest digest,
submit without nested scheduling:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/qualify-n06-matrix.py \
  --commands /absolute/commands.json --commands-sha256 REVIEWED_SHA256 \
  --output /absolute/new-output-directory
```

Inspect the terminal Kyoto job and retained `result.json` and logs. The status
`passed_49_case_comparison` establishes only that the supplied commands emitted
those rows. The caller attests that they are distinct installed engines and that
the artifacts cover all execution inputs; the comparator does not prove which
code ran, source-to-binary provenance, complete installed package membership or
resistance to hostile processes that escape their process group. It does not
replace the host-fault, worker teardown, mutation, resource, platform and
independent-review gates in the N06 plan. Do not call this component a full N06
qualification or use candidate output to modify the oracle.

The snapshot workflow `scripts/run-n06-snapshot.py` additionally requires
`--setuptools-wheel` pointing to an external `setuptools-80.9.0-py3-none-any.whl`
whose SHA-256 is
`062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922`.
Acquire and verify that exact artifact before running the workflow. It is copied
into the receipt, rehashed, and installed with `--no-index --no-deps` into a fresh
virtual environment. The project wheel is then built with `--no-index
--no-build-isolation --no-deps`. The receipt records the interpreter hash and
pip version; those tools and source-to-artifact linkage are not pinned or proven
by this backend pin. A missing or altered backend wheel fails the workflow.

The Rust build must also be bracketed by `scripts/n06-build-window.py`: run its
`before --snapshot SNAPSHOT --checkpoint BEFORE.json` command, submit the direct
Cargo build from `SNAPSHOT/source` through Kyoto, then run `after --checkpoint
BEFORE.json --job JOB_ID --output WINDOW.json`. Pass `--rust-build-checkpoint
BEFORE.json --rust-build-window WINDOW.json` to `run-n06-snapshot.py`. The
workflow verifies the checkpoint and window against the snapshot manifest,
helper, exact Kyoto job, log and Rust executable hashes. The window detects
persistent selected-source drift; it is caller-attested and cannot rule out a
transient change between checks or replacement of an executable after build.
