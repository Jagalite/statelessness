# TypeScript N14 qualification

Kyoto `01bccfed63da4d7f9cfc05fff4bb9ec0` succeeded: full TypeScript build and
928 tests passed; zero failed, skipped or cancelled. Requested and effective
commands are the same explicit absolute-corpus environment plus `npm test`;
`jobs/` and `ledger.json` retain the full envelopes and terminal result.

The 44-file source scope is
`3ff372a770c5f6a03049c3fa9825864155ccd0d5d46c548f242b202f289e2c18`.
`source-before-r2.json` was captured before submission and actual start.
`final-source.json` reconciles every source file, complete source file set and
shared fixture/spec/reference input after execution. Logs are complete lossless
gzip files with original/compressed byte sizes and SHA-256 in the ledger.

The first run `e88c7597c3b4489b97189f51e9e9afa8` is preserved independently:
928 tests, 927 passed, one faulty focused expectation. Its label-order fixture
declares state 0 inputs `[0,1]`, and its toggle reducer reaches state 1 via input
0; the independently correct shortest prefix is `[0]`, not the default unit/null
fixture prefix. Only that focused assertion was corrected with an explicit
per-row expected prefix. No engine or shared corpus/golden changed. The original
source manifest and full failure log remain archived.

This receipt qualifies the executed TypeScript candidate suite, including the
67 shared research rows, 10 separately labelled native admission cases and
focused regressions. It is not installed cross-language execution evidence,
a general reduction proof, profile promotion or production pruning approval.
