Python candidate canonical value codec validation

Prior qualified Kyoto 5042d1522493430a89d09ecb311427f6 succeeded, exit 0: 247 tests, with 12 ABI skips. This includes independent encode/decode and length-only capability propagation through all composites, empty/None/zero-length rejection in unavailable directions, preferred record/bounded replay with genuinely encode-only output codecs, and captured collection allocation weights. Independent contract review closed with no remaining scoped findings at module SHA256 2033feabba16cea05a94d65a7e27ce014fd374cbd7f5fc853e8dad4236a5084e and unchanged codec SHA256 d2763cf6b78423d8abfcab6efe84c68ebced60cacc22a92f5c3556b8fa75a02a.

The prior 247-test receipt is preserved under prior-247*. A later custom length-width boundary fix requires the new final run below. Independent installed application qualification is separate and pending. Experimental APIs do not promote qualified profiles.

Corpus accounting: 186 rows comprise 179 actual public Python runtime cases, six explicitly arithmetic-only private-length checks, and one TypeScript Number policy case explicitly not applicable to Python. Earlier adapter conversions of the TypeScript row to a Python float are not TypeScript runtime qualification. Separate Python float rejection and exact large-integer acceptance regressions replace that proxy. The 83 value rows additionally exercise prefixes, trailing bytes, subviews and owned results.

Preserved history:

- Focused 87819be26145424ab6e7fb8e39bf19bf passed 15 tests before later fixes.
- Diagnostic 0f2ee4484cb5427fb914c6143d44d202 failed six raw-encoder cases before raw finish/callback-entry fixes and during shared corpus/adapter extension. It is not final-source qualification.
- Intermediate b76d3aad8c9e4b619736199f7f167a23 passed 237 tests before raw-string prefix and hostile metaclass fixes.
- Intermediate 21d8c50fb6d7437e9c724d27945a55ab passed 240 tests before the final language-policy adapter correction.
- Prior qualified 241-test source c25cb2f8b92a401e8419c9c62dc0c523 remains intact under prior-241*. It predates direction and captured allocation-weight fixes.
- Intermediate direction job 188700ef795c468097be298e4d744ae2 passed 246 tests before the captured-weight fix/regression.

All logs use lossless gzip. log-ledger.json records original and compressed SHA256 values and byte lengths, preserving trailing whitespace exactly.

FINAL WIDTH SOURCE: Kyoto 2f7e702f48c440769c2ba54bd67f12fc succeeded, exit 0: 248 tests, 12 ABI skips. encode_length_raw and independent decode_length_raw validate exact integer widths 0..255 before custom callbacks; custom width3 remains valid, built-ins retain 1/2/4. Independent review closed without findings at module c85edbe7133374af8cbe01b50ba6256fd34ff897819a82abd461e7fb61fe5735. source-and-runtime.json verifies all seven held source/corpus mtimes precede actual execution start and current hashes match the candidate manifest. This job started immediately; the manifest capture itself occurred after startup, recorded explicitly rather than described as pre-start. Module/test hashes were separately reported before submission, and source remained held. Installed application qualification remains separate.
