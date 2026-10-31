# N13 derive/model-codec peer-review draft

This directory is new design material only. It does not change existing runtime,
package, test, fixture, driver or qualification inputs. No build or executable
codec was run to author these expectations, and no commit or promotion is made.

- [spec.md](spec.md) defines observable declaration and ModelCodec behavior while
  allowing idiomatic native builders or generators instead of Rust derive syntax.
- [cases.json](cases.json) contains 90 independently authored rows: 25 positive
  wire/budget examples, 12 budget controls, 9 runtime errors, 23 declaration/source
  negatives, 12 ModelCodec binding cases, 5 diagnostic cases and 4 public reader
  scripts. These are distinct evidence classes, not 90 claimed runtime executions.

The registry fixes ordered record fields, explicit enum tags, direct length
overrides and raw adapter contracts. Literal hex and work/depth/allocation values
were calculated from the Rust public trait call graph, not harvested from engine
output. Pair's declared collection storage weight is explicitly cooperative and
does not qualify an inferred generic record layout.

Peer review should first check field entry versus direct adapter/length guards,
bool's nested u8 guard, String byte work, all-variant direction requirements,
partial sink prefixes and the eight ModelCodec methods' version-check order.
Then settle the fixture vocabulary and construction/ownership requirements before
implementers consume it. Real Rust reference code should instantiate concrete
derives and independent length/custom-adapter types rather than replace the
derived algorithm with a dynamic fixture codec.

The future acceptance plan separates real Rust compile failures, native static or
builder admission, runtime wire/budget behavior, diagnostics and installed-model
interchange. Native host-specific ownership or callback failures require focused
native tests. Existing 186-row value corpus and pending installed qualification
remain unchanged.

## N13 continuation source accounting

The adjacent `rust-dispositions.json`, `python-dispositions.json`,
`typescript-dispositions.json`, `go-dispositions.json` and
`swift-dispositions.json` map each draft row to an authored assertion or an
explicit native construction/type boundary. They do not change the independently
authored expectations in `cases.json`. Execution receipts are separate; a source
reference or a static exclusion is not a runtime pass. The accounting checker is
`scripts/check-n13-dispositions.py`.

The separate `scripts/check-native-model-consumers.py` and
`examples/native-model/` add a real bound application with checked Recorder and
causal EvidenceRecorder paths, including a concrete Rust derive application.
Its stdlib-only artifact oracle specifies the scenario bytes independently.
The original three-fixture `check-native-derive-consumers.py` retains its own
scope and receipts. Pending/running/terminal continuation evidence is retained
under `validation/native-parity/derive-model-v2/`; see its handoff before making
an execution claim. Broad144, generated-model helpers, promotion and parent
acceptance remain separate gates.
