# N13 native declaration implementation

This additive implementation follows the reviewed
[declaration contract](derive-model-draft/spec.md). Go, Python, TypeScript and
Swift expose explicit ordered record, tuple/unit record, tagged enum and opt-in
ModelCodec construction. Rust procedural macro syntax is not emulated.

Ordinary field decode uses the shared reader's value guard. Length fields and
adapters decode directly; enum payloads decode directly after a guarded tag.
Constructors run after successful field decoding. Capability requirements admit
the complete declaration, including unselected enum cases, and do not filter
otherwise available directions. Record and enum collection storage requires an
explicit logical weight; no generic Rust layout is inferred.

The binder requires State and Input encode/decode and Output encode only. Its
eight hooks gate current metadata version before codec callbacks, use fresh
limits for each decode, and retain the existing model and engine behavior.
Go and Swift attach a codec to the existing model; Python and TypeScript forward
the existing model through a binding wrapper. No Output decoder is added.

Native regression suites cover callback ownership, raw returned-error priority,
sticky actual write failures, construction timing, directional admission,
guard budgets and binder gates. Concrete Rust derive tests and focused
compile-fail declarations are separate evidence classes. Installed consumers
exercise a smaller finite record/enum interchange surface.

Execution receipts and final qualification scope are recorded under
`validation/native-parity/derive-model/`. The 90 independently authored mixed
draft cases are not a claim of 90 executed runtime cases. This slice does not
promote a profile or establish the broad 144-row native parity matrix. Platform
qualification is limited to the recorded macOS arm64 host.
