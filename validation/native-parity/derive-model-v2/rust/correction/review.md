Independent static review by /root/review_correction: No findings.

DecodeOnlyByte matches cases.json fixture_callbacks.decode_only_u8: raw u8::trace_decode, no encoder. Macro-generated guarded enum tag and ordinary payload plus from_trace outer guard require work 3/depth 2. One-under witnesses retain strict limits. The derived record and enum-unit row mappings remain valid across all five ledgers. Direct sink write versus sticky finish errors independently match src/model.rs. No goldens or budgets weakened. Static review only.

Follow-up review of the actual two Rust ledger notes: No findings. Swift wraps its raw scalar callback in decodeRecord for Value, so its payload call graph differs. Only the unchanged unit-row mapping is compared; this proof does not establish cross-native payload parity or parent integration qualification.
