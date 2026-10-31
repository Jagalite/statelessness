# Installed canonical value consumers

The closed application contract contains the 83 independently specified positive
value fixtures and the finite measured Rust64 layout registry. Producers encode
the actual native typed values. Fresh consumers decode each producer’s bytes,
return canonical JSON views of the decoded values, and reencode the same bytes.
The JSON bundle is a test transport, not a new value wire format or schema ID.

Submit the driver through Kyoto only after all native/reference sources freeze:

```
python3 scripts/check-native-value-consumers.py --rust validation/corpus/target/debug/native-values --rust-portable validation/corpus/target/debug/stateless-corpus --output /private/tmp/NEW-OUTPUT
```

Each application exercises 477 proper encoding prefixes, 83 trailing-byte rejects,
83 admitted subranges and owned-result checks, 83 exact encode caps, and 80 one-under
caps. The three zero-width encodings have no invented positive-width prefix/cap
requirement. Separate public controls cover a huge zero-sized vector count rejected
at element admission, a decode-only custom descriptor swallowing a recorded read
failure, direct versus guarded length work, and encoding into a caller’s prefix.
Swift’s public byte-array API uses an owned subrange; other runtimes use native
views/slices. These tests do not claim a borrowed or zero-copy decoder interface.

The model explicitly wires descriptors into existing codec hooks: state is a
vector of optional u64, input is optional u64, and output is a dedicated encode-only
code/string value. Three Some(1) inputs append to the state and fail its sum bound.
Output’s custom descriptor delegates u8 and a BOM-prefixed String encoding and has
no decoder callback. Actual checked EvidenceRecorder capture and phase-aware
replay must work with that direction absent, preserving all producer configuration
and provenance. No output-decoder requirement or package-generated ModelCodec glue
is introduced. Twenty-five typed-value pairs and twenty-five evidence replay pairs
run through local installed/staged applications outside the checkout; the existing
717-check portable runner and 550 fresh-process replays remain regression gates.

Collection allocation weights are explicit logical measurements for the recorded
Rust64 target/compiler, including Option<u64> size16/alignment8. They do not infer
arbitrary Rust layouts or measure native heap/RSS. Decoder limits, trace quotas,
and model callbacks remain separate boundaries. Native admission, hostile
reflection, fatal allocation failures, browser execution, registry publication,
other Rust targets, derive support, profile promotion, and performance are outside
this finite installed-consumer proof.
