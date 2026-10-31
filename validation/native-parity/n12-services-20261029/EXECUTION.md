# Frozen-source execution receipt

Source commit: `40373af92212545d2d913ea26f706ae281008fb4`.
Kyoto job: `fb546b4e3e604394924bd4d0b2b77660` — QUEUED, NOT RUN at submission.
Requested and effective commands match; `policy_message` is null. The explicit
preparation runner performs real native suite execution and builds five installed
hosts. This is not a Cargo `--no-run` result or a runtime pass.

Do not edit source in this worktree while the queued preparation consumes it.
Receipt-only JSON/Markdown under validation is excluded by the existing source
inventory. The preparation records original and frozen source inventories,
installed package/consumer/tool identities and complete command logs externally.

Outputs:
- `/private/tmp/n12-services-81e57409-artifacts-1`
- `/private/tmp/n12-services-81e57409-receipts/prepare-1`

After preparation actually passes, submit the following separately through Kyoto
(no nested waiting queue submissions):

```
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env PYTHONDONTWRITEBYTECODE=1 python3 scripts/native-parity/n12-wire/compare.py --hosts /private/tmp/n12-services-81e57409-receipts/prepare-1/hosts.json --output /private/tmp/n12-services-81e57409-receipts/compare-1
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native-parity/n12-wire/service_negative.py --source /private/tmp/n12-services-81e57409-artifacts-1/source --output /private/tmp/n12-services-81e57409-receipts/negative-1
```

Neither follow-on was submitted/executed at handoff because preparation has not
passed. Comparison includes candidate commands; successful attached-service
five-engine comparison remains unimplemented, as HANDOFF.md explains. The
semantic mutant runner requires a passing control and a specific assertion failure.

The interim frozen-copy job `05491d141aa74a17939711e9da054741` remains distinct,
queued and source-stale. It was not cancelled or relabeled as final validation.
Old checkout jobs remain untouched. Its preparation failed the old Go default-label
expectation; original receipts are preserved. No old-source compare was submitted.
