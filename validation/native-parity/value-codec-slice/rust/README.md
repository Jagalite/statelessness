# Rust canonical value-codec qualification

Candidate `canonical-value-codec-v1`, real public Rust execution on the measured
64-bit target. [final-result.json](final-result.json) records the final reference
execution after the focused independent-length-trait control. No profile promotion,
arbitrary compound-layout inference or Rust32 qualification is claimed.

## Final evidence

- Build `10cf0553837042e1a7f3ed637841a54e`: `cargo build --manifest-path validation/corpus/Cargo.toml --bin native-values` passed.
- Execution `dd28f779ef86442e91c6ddd93bfbdf1c`: `validation/corpus/target/debug/native-values --corpus conformance/canonical-value-codec-v1.json` passed.
- Actual coverage: 83 positive values, 30 decode errors, 8 encode-cap errors, 19 public reader scripts, 6 raw encoder scripts, and 3 public unsupported-length calls.
- Additional automatic coverage: 477 rejected proper prefixes and 83 each of trailing-byte rejection, bounded subviews and independent owned results.
- Focused controls: a raw encoder still runs on an already failed sink and preserves its own callback error; `LengthOnly` implements only `LengthEncode`/`LengthDecode`, and its independent direct and guarded generic wrapper encode/decode paths pass without full trace-trait supertraits.
- Six private-length arithmetic rows and 31 native-only admission rows are explicitly excluded from Rust execution. Together with the 149 public executed rows, these account for all 186 frozen corpus rows. No multi-billion-element encoder loop was run.

[source-and-binary.json](source-and-binary.json) pins the final reference, public
core dependencies, manifests, executable and input identities. Corpus SHA-256 is
`bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c`.
The approved specification prose SHA-256 is
`c94ea1d77a51d66c1da3e7ff73d146fd35011d2c5519ef8060bd9f2d89393867`.
The final binary SHA-256 is
`0dccbad837682591f4b84bf772a4ef13743a2bdecf54c0abd8f17a47452b7682`.

## Measured finite layouts

[measured-layouts.json](measured-layouts.json) contains actual `size_of` and
`align_of` results from the final execution for all 41 exact finite schema
registry entries, 21 named ledger entries and 11 explicitly requested additional
shapes. Rust type spellings are diagnostic labels, not compatibility identities.

The 11 additions measured size/alignment as follows: `Option<i8>` 2/1;
`Option<u16>` and `Option<i16>` 4/2; `Option<u32>` and `Option<i32>` 8/4;
`Option<i64>` 16/8; `Option<u128>` and `Option<i128>` 32/16; `((),)` 0/1;
`(u8,u8)` 2/1; `((),())` 0/1. Unknown shapes remain unqualified.

Toolchain queries were separately executed through Kyoto:
`0aff63b71b584ad3b1229184553a7176` for `rustc -vV` and
`940c5b4e759f484eb4f78f112760cfbb` for `cargo -V`. Their exact commands,
output and target/compiler identity are retained with the other jobs.

## Preserved execution history

| Job | Scope | Result |
| --- | --- | --- |
| `bfa2033c1bea4b109bb94c0470ef57d7` | Initial reference build | Passed |
| `f0351a737e3f43758f30430d8a92d4c4` | Initial 41-schema and 21-name layout measurement | Passed |
| `caea082a923e481a9c53eea68a6956b1` | Original 180-row reference scope | Passed |
| `a68a6d42c0084db7a14a5e4a5d05d780` | Build adding four raw encoder witnesses | Passed |
| `ae159a8569fc456f9c1036970e3aafea` | Expanded 184-row reference scope | Passed |
| `db35295ae4bb4b028134cb1ebdf2834d` | Full app/reference build | Failed on a missing `Disposition` import; compiler log and preceding memory-pressure retry retained |
| `af47e70b99724f8891c7bdac57498e0e` | Corrected full app/reference build | Passed after Kyoto memory-pressure retry |
| `4b9fd11a1102436a8883200114ade29f` | 41 schemas, 21 names and 11 additional layouts | Passed |
| `53b4860fc7cd4a0eab9db5b2df48c855` | Frozen 186 rows plus failed-sink callback control | Passed |
| `10cf0553837042e1a7f3ed637841a54e` | Independent `LengthOnly` trait-bounds control build | Passed after Kyoto memory-pressure retry |
| `dd28f779ef86442e91c6ddd93bfbdf1c` | Final 186 rows, failed-sink callback and `LengthOnly` control | Passed |

Exact working directories, absolute arguments, requested/effective commands,
policies, attempt/retry state and exit status are in [jobs](jobs). Builds and
executable qualification are separate jobs; compilation is not reported as a test
pass. Intermediate JSON summaries retain their earlier coverage scope. No exact
intermediate source snapshot is reconstructed or claimed.

All 13 original logs, including failed and retried attempts, are preserved as
deterministic gzip archives under [logs](logs). [log-ledger.json](log-ledger.json)
records original and compressed SHA-256/size, original path and raw trailing
whitespace detection. Every archive was decompressed and checked byte-for-byte
against its original; Cargo progress carriage returns and whitespace are intact.

The independent installed-app smoke is archived separately in
[../rust-app](../rust-app). It qualifies the earlier full app executable with its
own exact source hashes and covers all 83 typed bundle rows plus the checked
`EvidenceRecorder` application with an encode-only output type. The final focused
change broadens the fixture wrapper's independent length-trait bounds and adds a
control; this directory does not relabel the earlier app executable as the final
binary.
