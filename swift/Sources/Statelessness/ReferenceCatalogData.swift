// Exact copies of docs/requirements/controller-properties.txt and controller.json.
// The source-tree test checks for drift; these data travel with installed SwiftPM consumers.
enum ReferenceCatalogData {
    static let properties = #"""
state controller.state_valid
transition controller.publication_current
transition controller.success_published
transition controller.cleanup_required
transition controller.settlement_once
transition controller.cancellation_ack
transition controller.duplicate_inert
transition controller.release_after_settlement
transition controller.retry_deadline
transition controller.ignored_inert
transition controller.effect_order
transition controller.completion_progress
transition controller.cancellation_progress
transition controller.submission_progress
"""# + "\n"
    static let requirements = Array((#"""
{
  "schema_version": 1,
  "model": "reference-controller",
  "codec_version": 1,
  "properties_version": 1,
  "review": {"status": "awaiting_independent_review", "reviewer": null},
  "properties": {
    "controller.state_valid": "Generations and attempts are bounded and unique; pending jobs own resources; tombstones do not.",
    "controller.publication_current": "Only the current running generation's matching successful completion may publish.",
    "controller.success_published": "Every current matching successful completion publishes exactly once.",
    "controller.cleanup_required": "Every newly terminal request emits exactly one release.",
    "controller.settlement_once": "A request settles once, with a result matching its terminal state.",
    "controller.cancellation_ack": "A matching cancellation acknowledgment settles the cancelling request and releases it.",
    "controller.duplicate_inert": "Duplicate, unknown and stale-attempt completions are ignored without state or output changes.",
    "controller.release_after_settlement": "Release occurs after settlement, never while a dependent attempt is outstanding.",
    "controller.retry_deadline": "Advancing logical time through a retry deadline starts the next attempt exactly once.",
    "controller.ignored_inert": "Ignored inputs produce no effects and do not mutate state.",
    "controller.effect_order": "Superseding an active request cancels the old attempt before starting the replacement.",
    "controller.completion_progress": "A matching active completion must settle and release, or explicitly retain its resource while entering the next bounded retry deadline; a cancelling completion settles as cancelled.",
    "controller.cancellation_progress": "Cancelling running work must enter cancellation and request the matching worker cancellation; cancelling waiting work must settle and release immediately.",
    "controller.submission_progress": "Every in-bound submission creates and starts its next generation and discharges the prior current request through its required cancellation protocol."
  },
  "input_domains": {"generations": [0, 1, 2], "attempts": [1, 2], "time": [0, 1, 2], "outcomes": ["success", "retryable_failure"]},
  "bounds": {"max_generations": 2, "max_attempts": 2, "max_time": 2, "max_states": 100000, "max_transitions": 1000000, "max_depth": 100},
  "coverage_targets": ["supersede_running", "stale_success", "cancel_ack", "retry_started", "duplicate_completion", "settled_cleanup"],
  "assumptions": ["No generation identifier reuse", "Worker completion or cancellation acknowledgment must eventually be delivered for host progress", "The finite checker proves safety, not unbounded fairness", "Payload contents are abstracted to an outcome class", "The host has one ordered reducer owner", "Tombstones are retained; no timer-based retirement"],
  "mutations": {
    "obsolete-publication": "controller.publication_current",
    "missing-cleanup": "controller.cleanup_required",
    "double-settlement": "controller.settlement_once",
    "dropped-cancellation-ack": "controller.cancellation_ack",
    "duplicate-completion": "controller.duplicate_inert",
    "early-release": "controller.release_after_settlement",
    "skipped-retry-deadline": "controller.retry_deadline",
    "ignored-mutation": "controller.ignored_inert",
    "reordered-effects": "controller.effect_order"
  },
  "required_search_outcome": "graph_exhausted",
  "regression_corpus": "docs/requirements/controller-witnesses.txt"
}
"""# + "\n").utf8)
}
