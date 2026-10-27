# Finite direct N12 wire comparison

The five hosts use public APIs and the independently specified model in
[SPEC.md](SPEC.md). `prepare.py` copies the complete candidate source inventory,
builds fresh public consumers, installs Python/TypeScript distributions, and
runs the full native regression suites. `compare.py` drives each installed host
in a fresh process through real pipes. Both runners retain failed attempts.

Run from the candidate checkout through the default shared Kyoto queue. Use
new artifact and receipt directories for every attempt:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env PYTHONDONTWRITEBYTECODE=1 python3 scripts/native-parity/n12-wire/prepare.py --artifacts /private/tmp/n12-wire-ATTEMPT --output validation/native-parity/n12-wire-oct22/prepare-ATTEMPT
```

Inspect the job's effective command and terminal result. Once all five hosts
exist, submit the comparison using that preparation's `hosts.json`:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env PYTHONDONTWRITEBYTECODE=1 python3 scripts/native-parity/n12-wire/compare.py --hosts validation/native-parity/n12-wire-oct22/prepare-ATTEMPT/hosts.json --output /private/tmp/n12-wire-ATTEMPT-comparison
```

Source, installed artifact and launch-specification changes invalidate an
attempt. The only response normalization is field order and validated
process-local epoch identity. Each case must independently match literal
source-derived expectations and the actual Rust responses, callbacks and final
session. `requested.frames` records planned input; `stdin.frames` records bytes
successfully written. `stdout.frames` and `stderr.log` retain raw output.

`wire_passed` describes the finite direct comparison. Aggregate `passed` also
requires successful preparation/regressions and unchanged source/artifacts.
No result closes full optional N12 acceptance. See the source-linked
[acceptance ledger](../../../validation/native-parity/n12-wire-oct22/ACCEPTANCE.md)
and final receipt handoff for the actual executed scope and remaining gates.
