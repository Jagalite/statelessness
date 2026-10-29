# N07 qualification runner repair — not acceptance

The frozen N07 follow-through exposed two runner defects. Its source remains
unchanged at `/Users/jagatranvo/.t3/worktrees/stateless/t3-329ad6a6`, HEAD
`b478e67f1668385d120cef2e8debb64042d32cb3`, source pin
`0b6f39862b701b89f82e973790cba84dadcaf769`. Full evidence is in that checkout at
`validation/native-parity/observation-slice/followthrough-20261029T134619Z/`.

| Previous job | Terminal result and limit |
| --- | --- |
| `18278524e9a346fe86e034db36c44ba0` | Failed: 99 validator tests, one failing helper-mock subtest; native language suites never started. |
| `9d33619f5f9f465ea74de559e913761e` | Compilation succeeded, but compile-proof admission rejected the Homebrew-versus-rustup tool identity mismatch. No selected Rust runtime execution. |
| `960c0c23ba574c6c86f99a30880c6356` | Passed on the earlier frozen source: three Go race executable processes, three runnable Rust doctests, five compile-fail doctest controls and two release-monitor tests. Individual Go test counts were not captured. |

The parent repaired the installed-consumer mock to resolve browser and authority
helpers separately and added a browser first-consumption mutation boundary.
The qualification runner now selects matching Cargo/Rustc/Rustdoc rustup proxies
before recording compiler commands. Missing, nonexecutable or unrelated proxies
fail before the environment changes. The compile-proof verifier is unchanged.

Repair source SHA-256 identities:

- `scripts/qualify-native-observation.py`:
  `70440d4de2beb6398259873121ae7336a228a5b944717da7e24e493f59d2b380`
- `scripts/test_observation_receipts.py`:
  `e4107808791ac0bb86bbe33249452114cdd7c8ff7b86f552f049de7403c1a9b3`

Kyoto regression job `43415fbf900d4d38bdbc01a97e8e3d56` is **queued**, not passed,
at this checkpoint. Submitted from `/Volumes/seed2/Projects/stateless`, its
requested and effective command is:

```sh
python3 -I -B -m unittest discover -s scripts -p 'test_observation_*.py' -v
```

`policy_message` is null. Kyoto reports `OS memory pressure: warning` and zero
admission capacity. Do not duplicate this job or edit its script inputs while it
can consume them. Three new test methods exercise tool selection using inert
fixture files; they are not real compiler execution evidence.

The corrections are integrated source candidates. Fresh sealed native suites,
identity-admitted compilation, Rust runtime, installed observation/authority
controls, suffix/replay pairs, Chromium and equivalent-work measurements remain
required. Earlier extended execution does not qualify this integrated source.
N07 and full parity remain open; no push or merge follows from this checkpoint.
