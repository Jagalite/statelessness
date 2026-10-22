# Oct22 direct native/Rust wire continuation

Qualification is in progress. Full optional N12 acceptance remains open.
This file will be finalized against terminal receipts before handoff.

## Ownership and source

All edits are in the new isolated checkout
`/Users/jagatranvo/.t3/worktrees/stateless/n12-wire-resume-oct22`, branch
`t3/n12-wire-resume-oct22`, based on
`89f61d6666e2666fad65164dc5ab3e8f3e4e1fd5` (recovered runtime source
`f866f7552df77736297fec5bf515481730a23f4c`). The parent checkout and recovered
`t3-ccd33c8a` / interrupted `t3-6b2942d1` trees were not edited. Both old trees
were clean at the base; no interrupted uncommitted implementation existed.

- `06f3d3962756a687224ca682609fa7ed75a40bf3`: native protocol fixes, five public
  same-model consumers, strict comparator, preparation runner and acceptance ledger.
- `dd9ce859ed1fabb6b4d60eaf24d2fc73b6ca0ddb`: copied Swift dependency directory
  identity differs from the external consumer's identity.

The [base inventory](checkout-source-base.json) captures candidate membership,
types, modes, sizes and SHA-256 values. Every preparation and comparison captures
the complete candidate source inventory before and after; validation receipt data
is excluded, while validation executable sources remain included. Copied build
inputs, installed consumers, executable link targets and launch specifications
have separate manifests. See [review resolutions](review/RESOLUTION.md).

## Retained attempts

| Attempt | Kyoto job | Result |
| --- | --- | --- |
| Preparation r1 | `9ff64d83403c47b5927ba648bb1bc8db` | Failed at Swift consumer package resolution; Rust/Go/Python/TypeScript hosts built. Go suite and vet passed; Python 351 tests with 12 optional ABI skips; TypeScript 941 passed. All three source/copy guards passed. |
| Preparation r2 | `e8bf69e8a61c40399b99bbc6ec6817ab` | Passed: five hosts built; Go full suite and vet, Python installed suite (339 pass, 12 optional ABI skips), TypeScript 941/941 and Swift 218/218 passed. Source, frozen source and copied build-input guards all passed. |
| Direct comparison r2 | `47bc958139944ce49c458a294853308b` | Pending terminal evidence. |

Preparation r1's exact Swift failure was:

```
error: 'swift': product 'Statelessness' required by package 'swift' target 'n12-wire-swift' not found in package 'Statelessness'.
```

The consumer and dependency copy both had directory identity `swift`; r2 gives
the dependency the distinct name `statelessness-swift`. The failed receipt is
preserved. No direct comparison was attempted using r1's incomplete host set.

Preparation r2 used Rust 1.99.0 (commit `b940084d7eb6a299eb4bfeb8e34901bc051e7ac4`),
Go 1.24.2, Python 3.13.3, Node 23.5.0 / npm 10.9.2, and Apple Swift 6.3.3
(`swiftlang-6.3.3.1.3 clang-2100.1.1.101`) on this arm64 macOS host. Go's three
test-bearing packages passed; other packages reported no test files. Swift's
existing mutable `Sendable` test-fixture warning is retained in stderr. Python's
12 skipped optional ABI tests require `STATELESS_ABI2_LIBRARY`; native tests do
not substitute for those omitted ABI checks.

All qualification runs use the default shared Kyoto queue, with effective
commands and terminal metadata retained under [kyoto](kyoto/). Preparation is
an explicit multi-language build/install/test runner; Rust uses `cargo build`
for the external public consumer. Rust execution evidence must come from the
separate comparison. Native regression passes alone are not direct parity proof.

## Scope and remaining gates

The [fixture specification](../../../../scripts/native-parity/n12-wire/SPEC.md)
defines one source-authored model and independent expected assertions. Each case
runs fresh native and Rust processes over actual pipes. Comparison permits only
validated process-local epoch mapping and field ordering. Exact strings, integer
values, field presence, errors, callback order, binary outputs and final session
state remain observable. Raw input/output/error streams are retained losslessly.

The [full optional acceptance ledger](ACCEPTANCE.md) maps every audit family to
this finite fixture and remaining gates. Those include replay/minimization/forks,
structural inspection, attached watches/probes/live/effects/metrics/dashboard,
remaining successful protocol commands, full independent authority combinations,
surviving-process reconnect, limits/exhaustion and broader platform/distribution
qualification. No broad N12 closure, 144-cell closure, parent integration,
profile/default promotion, push, PR or release is asserted.

The third final static review failed because the selected model was at capacity;
its [failure record](review/final-r3-capacity-failure.json) is retained. A fourth
round used the same provider, model and options and returned
[no new concrete findings](review/final-r4.json) at `dd9ce859ed1fabb6b4d60eaf24d2fc73b6ca0ddb`.
It confirmed the prior four corrections and inspected the comparator assertions,
five hosts, provenance guards, Go/Python reconnect changes and acceptance
boundaries. Static review remains distinct from executable qualification.
