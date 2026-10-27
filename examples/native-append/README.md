# Installed append-only checker consumers

These applications exercise the public preferred append-checker callbacks from
installed Python/TypeScript packages, separate Go/Swift package consumers, and
the actual Rust engine. Legacy checkers deliberately fail if invoked.

The common model starts at integer zero, enumerates/generates zero or one, adds
the input, and fails `threshold.bound` at three with `at least three`. Input zero
emits nothing; input one emits `changed`. State and input codecs use decimal
ASCII, with strict zero/one input decoding. Metadata is
`native-append-threshold`, versions 1/1/1, build `application-v1`.

State append hooks inspect empty root storage, reserve additional room, push the
bound check, and extend with `state.appended`. Transition hooks must see the
completed two-check state prefix, preserve it, and append `edge.checked` followed
by `edge.appended`. Public length/empty/snapshot inspection and exact check order
are verified while the callback is active. Reserve is tested for logical
preservation; no portable allocation/capacity claim is made.

`record PATH` first checks an already-executed transition into caller storage
containing a stale entry. Storage must clear at entry, state and transition hooks
must each run once, and `step` must never run. A transition-checker error after
successful appends must clear the entire output, again without re-execution.
Native consumers additionally retain successful and failing callback leases and
require every public operation to reject expired use. A nested check proves the
child lease expires while its outer callback's lease remains usable. Rust uses
static borrowing for these ownership restrictions; its receipt does not pretend
to execute the native runtime escape controls.

The same preferred callbacks are then used by BFS, seeded fuzzing, actual witness
shrinking from six inputs to three, recording, and local replay. Each engine
persists its append-checked witness as ordinary portable JSON. `replay PATH`
reopens that file in a fresh process and uses the preferred append hooks to
reproduce the failure exactly. The driver requires all 25 ordered engine pairs
to pass and then runs the established four-profile conformance baseline across
the installed/staged engines against independent goldens and cross replay.
Preferred state/transition invocation counts for the complete pipeline and each
fresh replay must also match the actual Rust reference receipts.

After all engine sources are frozen, submit through Kyoto:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-append-consumers.py --rust validation/corpus/target/debug/native-append --rust-portable validation/corpus/target/debug/stateless-corpus --output /tmp/native-append-consumers-unique-run
```

The driver builds and installs local packages outside the checkout, retains
toolchain/source/package/executable/artifact identities and command logs, and
requires a new or empty output directory. Rust executables are built separately
through Kyoto; this driver runs no cargo commands. `--without-swift` and omission
of `--rust-portable` produce explicit narrower coverage. Passing is candidate
append-check evidence, not profile promotion, zero-allocation proof, bounded
process memory, asynchronous sink support, or complete language parity.
