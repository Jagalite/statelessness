# N13 implementation review

Independent static review of the additive Go, Python, TypeScript and Swift
ordered-record, explicit-tag enum and ModelCodec binder implementation, its
focused tests, concrete Rust derive reference and installed consumer driver.
Reviewed against DERIVE-MODEL-CODEC-AUDIT.md and the reviewed spec/API drafts.
This report is not runtime qualification: the reviewer ran no builds or tests.
Kyoto jobs were queued under the existing pressure policy during this review.

The reviewed implementation preserves independent directional capabilities,
all-field/all-case capability intersections, eager requirements without filtering,
ordinary guarded fields, direct length/adapter fields, raw selected enum payloads
with guarded tags, and construction after successful field decoding. Binders
provide eight version-gated methods, decode-only fresh limits and encode-only
Output admission. Layouts are explicit rather than inferred from wire widths.
The inspected changes do not promote profiles or replace model defaults.

Concrete findings corrected during review:

- Python public Field/Case construction bypassed factory validation. Frozen
  declarations now validate and normalize their direct construction paths.
- Python preferred encode hooks treated an explicit None sink as a legacy
  encoding request. A distinct omitted-argument sentinel now preserves the
  preferred hook's sink admission.
- TypeScript enum tuple construction retained a caller field array and invoked
  its mutable map method while decoding. It now captures an owned indexed key
  list, with a mutation regression.
- Go unavailable direction checks occurred after sink admission or reader guard
  work. Current preflight checks reject unavailable operations before those
  effects while retaining existing reader failure precedence.
- TypeScript binder Output capture installed an unused fabricated decoder
  closure. The capture now installs a decoder only for State/Input.
- Installed consumer timeouts lost captured command output; the driver now
  retains timeout bytes with size/hash receipts. A changed source snapshot now
  fails qualification instead of merely recording the mismatch.
- Rust missing-trait compile-fail checks accepted overly broad trait-name
  matches. They now require E0277 plus the concrete No trait-bound failure;
  missing adapter decoding requires E0425 and the function/path diagnostics.
  Macro syntax cases retain specific SM diagnostic expectations. Sources and
  compiler logs are retained. These 22 cases are compiler rejections, not runtime
  corpus rows.

No remaining concrete correctness finding in the final static paths inspected.
Go uses DefaultEnumOptions/DefaultModelCodecOptions to request width/version1;
zero option values reject rather than silently interpreting an explicit zero.
That native convention is documented and tested separately from Rust syntax.

Execution remains a required gate. Focused tests author guard budgets, constructor
failure witnesses, immutable capture, unavailable directions, version ordering,
raw callback versus sticky writer errors and actual encode-only Output engine
composition. Their presence is not a pass claim. Root must inspect effective
Kyoto commands and actual test results, preserve every failed/superseded receipt,
and associate the final committed source with fresh qualification evidence.
The installed driver covers three concrete fixtures among four native packages;
it explicitly excludes binder interchange and does not substitute for the Rust
reference, the independently authored 90 mixed-class rows or broad144. macOS
arm64 is the intended current scope; broad144 and profile promotion remain open
for root review.
