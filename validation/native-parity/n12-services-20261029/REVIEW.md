# Independent static review

One permitted native read-only reviewer inspected the changed handlers, standalone
services, pinned Rust contracts, and newly authored expected outputs. It executed
no tests, edited no files, and started no children.

The review rejected early service candidates. Corrections addressed:

- encoded candidate ownership/quota accounting and canonical decode/revalidation;
- callback and exception-formatting ownership through watch projection/admission;
- disabled-watch lazy projection while advancing observation boundaries;
- per-observation current-watch retention with accounting separate from ring sampling;
- installation/removal boundaries, unknown-watch reads and error classifications;
- probe profile identity, byte/count/capacity bounds and active-hub admission;
- actual diagnostic counters and marker baseline semantics;
- exact recorder counters, public export-window range guards and meaningful tests
  using real recorders instead of unavailable-recording errors.

The last reviewer message found no compile-level blocker statically but identified
TS maximum-candidate and inspection-error-message mappings still needing changes.
The final source adds a read-only candidate work limit and prechecks offset+limit,
and maps the actual native WorkLimit/BudgetExceeded messages. These last small
corrections were statically inspected by the implementation worker, not separately
executed or re-reviewed.

This is not independent acceptance of N12 or the full requested scope. Missing
integrations and missing attached-service direct-wire coverage are enumerated in
HANDOFF.md. Compiler acceptance, executed tests, semantic mutants and installed
comparison results remain distinct gates; none is inferred from this review.
