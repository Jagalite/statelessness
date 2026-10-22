# Runtime observation v1 (source-derived draft)

Pinned Rust reference: `54ba3f32796fc5c71458fb4cda14b2ab9290ee83`,
`src/observation.rs`. This is diagnostic text, never a trace, continuity proof,
checked token, reducer execution, check execution, or effect execution.
Existing checked tokens and bounded Recorder remain authoritative and unchanged.
Fixtures are authored from source, not captured from implementation execution.
Execution/qualification is pending the parent's integrated source freeze.

## Consumer protocol (shared now)

`native-observation observe fixturePath` reads JSON and emits exactly one JSON
object on stdout. `self-test fixturePath` additionally compares the independently
authored expected cases and fails on any mismatch. Diagnostics go to stderr.

Input root: `{ "schema": "statelessness.observation.v1", "cases": [...] }`.
Each case has `id`, `stream`, `options`, `payloads`, `events`, and `expected`.
Optional `sink` injects a writer failure. Output root:

```json
{"schema":"statelessness.observation-results.v1","language":"rust","cases":[{"id":"example","record_hex":"","callbacks":[],"error_stage":null,"sink_poisoned":false}]}
```

No language field occurs inside compared case entries. `expected` has the four
result fields other than `id`; an optional `record_text` is explanatory only.
`record_hex` is lowercase hexadecimal of all bytes delivered, including LF.
`error_stage` is the FIRST failed operation's normalized stage (later operations
still run): `record_limit`, `format_input`, `format_output`, `format_state`,
`encode_input`, `encode_output`, `encode_state`, `sink_write`, `sink_flush`,
or `sink_poisoned`. Successful recovery does not erase the first error.
`sink_poisoned` describes final health, probed through a final Off observation
(no writer/formatter/check work); it is not inferred merely from any error.

Options: `level` is `Off|Error|Warn|Info|Debug|Trace` (default Off), `snapshots`
is `"None"`, `"EveryTransition"`, or `{ "every": "2" }` (default None),
`max_record_bytes` defaults to 1048576. All u64 quantities (sequence, intervals,
transition count, seed) are decimal STRINGS, preserving values through 2^64-1.
Transition sequence and Every interval must be nonzero. Checkpoint/message
sequence may be zero; message sequence may also be null. Small payload codes
are JSON integer bytes 0..255: state, before, input, and each output.

`payloads`: `{ "kind": "encoded", "max_blob_bytes": 16 }` uses the actual
public model codec (one byte per code); `none` uses NoPayloads; `custom` writes
`text` verbatim. Encoded callbacks are `encode_input:N`, `encode_output:N`,
`encode_state:N`; custom callbacks are `format_input:N`, etc. N is decimal.
None records no callback. Callback arrays exclude writer implementation details.
For `binding: "declaration"`, native consumers must use their real declaration
binding wrapper before observation; codec dispatch must reach that model's public
codec callbacks. The original model's direct codec hooks must trap in this case;
only the declaration adapters may supply bytes, so bypassing the wrapper cannot
satisfy the expected result. Rust has no equivalent declaration wrapper and uses
ModelCodec directly. Merely printing the fixture's codes is not a valid consumer.

Events use `kind`: `transition`, `checkpoint`, `message`, `started`, `finished`,
`set_level`, `set_snapshots`, or `flush`. Transition fields: `sequence`, `before`,
`input`, `state`, `outputs`, `disposition` (`{"kind":"Accepted"}` or
`{"kind":"Rejected|Ignored","reason":"..."}`), and `checks`.
Checkpoint: `sequence`, `state`, `checks`. Checks: `{ "id": "p", "status":
"Passed|Failed|Skipped", "message": "..." }` (message only for nonpassed).
Message: `level`, `sequence`, `message`. Finished: `transitions`, `reason`.
Started metadata: `name`, integer model/properties/codec versions, `build`;
config: `strategy`, `seed` (null/string), `parameters` (ordered arrays of two strings).
Setters carry `level` or `snapshots`. Optional per-event `fault` is one of
`encode_input|encode_output|encode_state|format_input|format_output|format_state`
or `swallow_input`: the latter deliberately ignores a rejected 1024-byte write.
Faults reset before each event. `sink` may contain `fail_after_bytes` (total
delivered prefix before a write failure), or `fail_flush: true`.

## Semantics

Health is checked before Off/filtering. A healthy Off observer invokes nothing.
This describes `observe`: an explicit `flush` still invokes the writer at Off.
Explicit Off messages are never printed. Started/finished are Info, ordinary
transition Debug, ordinary checkpoint Trace. Any Failed check promotes to Error;
otherwise any Skipped check promotes to Warn, including when preceded by Passed.
Failure wins over skip in either order. Rejected/ignored disposition alone does
not promote severity. Filtering uses the event severity against configured level.
Only configured Trace invokes payloads or includes Passed check details. The
record severity can still be Error/Warn when payloads are present at Trace.

Transition payload order is input, every output in supplied order, then post-state
when selected. Before-state is never formatted. Every(n) selects absolute sequence
divisibility, without maintaining continuity. At Trace an explicit checkpoint
includes state for ANY non-None snapshot policy, independent of divisibility.
Changing level/snapshots preserves no omitted history and implies no checkpoint.

Each record is buffered before the first sink write. The UTF-8 byte bound includes
the final LF. Overflow remains sticky inside that record even when a formatter
swallows the write error. Format/codec/bound errors discard the entire current
record and permit later recovery; sink write/flush errors permanently poison the
observer, can expose a prefix, and still fail after setting Off. `into_inner`
transfers the Rust writer without flushing. Native ownership/extraction must be
explicit; borrowed caller writers are not implicitly closed/disposed.

Record overflow stops subsequent payload callbacks at the failed field write.
The Trace cutoff cases use stream `s`, sequence 1, Accepted, no checks, and input
1/output 2/post-state 3 with EveryTransition snapshots. Bound zero reaches no
codec. Bound 97 fits through `input=hex:01` exactly and fails while writing the
following output field, before calling its codec; only `encode_input:1` occurs.
Neither failing transition publishes bytes or poisons the sink. The zero-bound
case follows with an explicit flush and an Off transition; no record can fit
that bound. The 97-byte case switches to Debug, publishes the 85-byte sequence-2
transition including LF, and flushes. Its expected bytes contain only that
recovery record; its first error remains `record_limit`.

Writer contracts have a native boundary: Rust's `Write::write_all` retries valid
short writes and interrupted writes, as exercised in `tests/observation.rs`.
Python likewise completes valid short writes and retries `InterruptedError`.
Go's `io.Writer` contract requires an error when `n < len(p)`: the Go observer
treats a short nil-error write as `io.ErrShortWrite` and poisons the sink. It does
not retry partial bytes accompanied by an error. Shared failing-prefix fixtures
therefore do not establish complete cross-language writer behavior equivalence.

Exact shared text uses Rust's displayed variant names and field order. Strings
use double quotes, escape quote/backslash and LF/CR/TAB, and preserve common
printable UTF-8 (including é and emoji) as UTF-8. This bounded fixture profile
does NOT claim stable cross-language bytes for every Rust Debug spelling,
arbitrary Unicode control/format characters, or future aggregate Debug changes.
NoPayloads renders `unavailable`; encoded bytes render `hex:` plus lowercase hex.
Custom formatters write bytes directly: the observer does not sanitize them or
guarantee a single line. Single-line custom output is the formatter's obligation.
Custom callbacks must also preserve model purity; observation does not sandbox
application code or prevent a callback from performing its own side effects.

The shared fixture currently contains 36 compact cases. Expected text was authored
directly from the source format strings and mechanically transcribed to UTF-8 hex;
no Rust/native implementation was executed to produce any golden.
