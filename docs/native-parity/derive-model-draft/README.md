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
