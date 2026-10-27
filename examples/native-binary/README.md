# Installed binary audit consumers

These standalone applications exercise public native binary-v1 audit APIs from
installed/staged Python, Go, TypeScript, and Swift packages. The driver also
requires the actual Rust `native-binary` reference executable, built separately
through Kyoto. No Rust build command is hidden inside this driver.

The shared CLI is:

```text
generate CASE OUTPUT CORPUS
record-single OUTPUT
roundtrip INPUT OUTPUT
replay-single INPUT
```

For every structurally valid shared corpus case, native `generate` reads the
fixed golden through its public audit reader and writes it through its public
audit writer. Rust additionally constructs its audit trace from normalized
fixture fields. All output bytes must equal the corpus exactly, retaining full
run configuration, optional seed presence, ordered duplicate parameters,
variant reasons, metadata, checks, outputs, and termination text.

The separate `record-single` producer invokes each engine's actual public
application recorder with explicit manual run configuration and converts to
audit form. The common application starts at integer zero, accepts
one-byte integer input one, advances by twice the input, and emits an empty blob
plus `[before, 255]`. State checks pass `ok`; transition checks skip `later` with
`not run`. Metadata is `counter`, versions 1/2/3, build `b`. Native configurations
retain their explicit empty parameter list. Rust adds its actual reserved engine
version/build parameters automatically; those provenance fields remain intact.
These fresh artifacts are separate from the fixed corpus `single-step` golden.

Every ordered engine pair then performs fresh-process semantic replay of this
independently recorded artifact through its public audit reader, lossless
portable projection returning the retained configuration, and actual model
replayer. Each replay receipt must retain the full producer configuration exactly,
including Rust's ordered engine parameters, as independently decoded by the Rust
reader. Five producers by five consumers yield 25 semantic pairs plus 25 exact
roundtrips of the fresh artifacts against each producer's own bytes. Structural
roundtrip coverage separately spans every valid corpus case across all 25
pairs. Valid-CRC `property-failed` and `empty` artifacts serve as semantic negative
controls: each engine must report actual replay divergence or incompatibility.
Model-error corpus cases are structural-only; prefix replay does not reproduce
their terminal adapter error.

Submit after all native source implementations have frozen:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-binary-consumers.py --rust validation/corpus/target/debug/native-binary --output /tmp/native-binary-consumers-unique-run
```

The driver stages packages outside the checkout, builds a Python wheel with
isolated build tooling, installs it in a separate clean venv, packs/installs npm
and compiles public declarations, builds a separate Go module with cgo off, and
builds a separate SwiftPM application. It retains command logs, tool versions,
source and installed consumer fingerprints, executable identities, wheel/npm
hashes, every produced/transcoded binary, and machine-readable receipts. Output
must be new or empty; earlier attempts are preserved. `--without-swift` is an
explicit subset only. Passing is candidate binary-v1 evidence, not binary-v2,
publication, durability, performance, or profile-promotion qualification.
