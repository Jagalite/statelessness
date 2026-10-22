# Finite installed N13 model application

This is a separate binder/Recorder/EvidenceRecorder scenario. The earlier
`native-derive` three-fixture driver keeps its original scope and receipts.
It does not close broad144, all 90 draft rows, generated model macro helpers,
production/default/profile promotion, or platforms beyond finite macOS arm64.

Each application implements an ordered `State { count: u32 }`, explicit tagged
`Input::Add(u8)` with tag 1, and custom encode-only Output containing the new u32
count. There is deliberately no Output decoder. The Rust application derives
its actual `ModelCodec`, `State` and `Input` implementations.

`produce DEST` checks an initial count of zero, then executes three Add(1)
turns. The state property `threshold.bound` fails at three with details
`at least three`; each turn also appends `edge.checked`. The same sealed initial
and turn checks feed a Recorder and an actual causal EvidenceRecorder. Handoff
asserts that reducers and checkers do not run again. Output bytes are captured
from those actual transitions. Producer RunConfig is manual/seed7/region=test;
producer engine and monitor stamps remain in the persisted artifacts. V1 retains
16384 bytes; the evidence inner Recorder retains 15328 after its 1024+32 reserve.

Capture callback counts are independently specified: initial_state 0, reducer 3,
state_check 4, transition_check 3, state_encode 14, input_encode 6, output_encode 6,
state_decode 0, input_decode 0, decode_limit_provider 0. State encoding includes
both before and after state per recorder, plus two initial checkpoints.

`transcode SOURCE DEST` passively reads both actual bounded binary artifacts,
asserts the literal application bytes/metadata/config/failed property, then
replays each with a fresh bound model and rewrites byte-identical artifacts.
Each replay requires reducer 3/state_check 4/transition_check 3/initial_state 0,
state_encode 4/input_encode 3/output_encode 3/state_decode 1/input_decode 3 and four
fresh provider calls. V2 also checks provenance, absolute sequence, state-phase
boundary 1, and actual previous-output causal links. Python/TypeScript/Swift
bounded replay observers additionally inspect the initial and three turn
observations. Rust v2 replay uses its causal engine composition.

The JSON envelope contains only lowercase hex `v1` and `v2`; stdout carries
assertion receipts. `expected.py` independently constructs all application,
frame, config, phase, provenance and CRC bytes using the Python standard
library. It never imports the engines or updates goldens from engine outputs.
The Rust build identity is independently recomputed from staged source and
captured compiler inputs and compared against the retained compiler build log.
Native engine stamps intentionally differ among producers.

Every process runs separate negative controls: all eight version hooks precede
hostile values/readers/sinks and provider callbacks, default/custom/fresh decode
admission, malformed and trailing State/Input, and all encoder hooks avoiding
decode providers. Rust uses typed values and codec counters at its static type
boundary. These controls remain separate from the success artifact prefixes.
Separate Recorder and EvidenceRecorder controls retain one successful turn,
then fail the actual bound Output encoder during the next checked handoff.
They check the public error type/stage, retained trace and causal/phase journal,
exact callback counts, and an inert retry after the recorder freezes.

Stdout includes measured `capture_counts` and `replay_counts` using the same ten
counter names in every engine. The driver checks every field against the
independent oracle and rejects missing fields, unexpected keys, and non-integer
counts. TypeScript replay binds a counted decode-limit provider; the omitted
provider path remains a separate control. `capture_failure_controls: true` is
emitted only after the separate failing-capture assertions complete.

Run only through Kyoto after the source candidate freezes. The driver requires
`--typescript-compiler` pointing to the prepared `node_modules/typescript`
directory and `--typescript-compiler-manifest` pointing to preparation's
`typescript-compiler-before.json`. Pass its literal pre-submission SHA-256 as
`--typescript-compiler-manifest-sha256`; never compute that pin at job startup.
There is no global `tsc` fallback. The pinned
version, package/lock inputs, complete compiler tree and Node identity must match
before compilation and remain stable afterward.

Use the exact source/tool manifests and fresh output directories in the
[combined qualification plan](../../validation/native-parity/derive-model-v2/integration-oct22/combined-n12/qualification-plan.json).
Its command wraps this driver with `qualify-native-integration.py --scope command`
and `--expected-source`/`--expected-tools` to reject pre-start queue drift.

The new driver builds wheels/tarballs, installs native packages outside the
checkout, and installs the staged Rust binary with Cargo. It launches five
fresh producers and all 25 ordered fresh consumer processes, comparing every
artifact against the independent oracle and checking exact rewrites. Commands,
timeout partial bytes, failed logs, package bytes and artifacts retain SHA256
and byte sizes. The Rust engine build stamp is selected from its own package's
compiler log entry; the macro crate's distinct stamp is retained in the raw log.
Executed native binaries, interpreters, and application scripts retain path,
resolved path, SHA256 and size before execution and are checked again afterward.
Before/after source membership and hashes are compared even
on failure. A queued job is pending evidence; compile-only success is not a
test pass. Qualification receipts, not this authored README, establish execution.
