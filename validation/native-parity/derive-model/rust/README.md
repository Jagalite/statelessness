# N13 concrete Rust authority

`crates/macro-qualification/tests/n13_derive_reference.rs` instantiates concrete
Rust derives and invokes actual public value traits. It contains six test
functions, including 19 independently written round-trip byte witnesses (with
all proper prefixes, trailing input and one-under output budget checks),
encode-only/decode-only witnesses, independent length traits, direct/raw versus
guarded budget and sticky-error behavior, raw encoder callback error priority,
and all eight ModelCodec version gates with fresh decode-only limits.

`crates/macro-qualification/src/bin/n13-interchange.rs` is a finite three-shape
producer and optional consumer. It uses concrete derives rather than interpreting
a schema registry. Run without arguments to emit independent Point/Empty/EventPoint
bytes; run with `<fixture> <hex>` to consume native producer bytes using derived
decoders and compare the finite expected value.

`compile_fail.py` compiles 22 separate negative declarations, requires actual
compiler failure and case-specific diagnostics, and retains each source and full
stdout/stderr. Each invocation writes a separate timestamp directory so failed
attempts cannot be overwritten. These are **compiler rejections**, not runtime
cases: tags/options/discriminants, ordinary and independent length directions,
unselected enum case requirements, missing adapter decoder and ModelCodec options.
They do not claim every possible Rust syntax or generic bound is qualified.

All compilation and execution is submitted through Kyoto from the isolated
worktree. A successful cargo `--no-run` receipt is compilation only; actual test
binary execution is a separate job. The 90 draft rows mix declaration, runtime,
directional and reader-script classes, and are not a 90-runtime-pass claim.
This finite reference does not promote any profile, qualify the broad 144-case
matrix, or prove other platforms. The current host scope is macOS arm64.
