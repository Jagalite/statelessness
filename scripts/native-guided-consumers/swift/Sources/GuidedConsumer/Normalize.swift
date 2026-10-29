import Foundation
import Statelessness

struct AdapterError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func nullable<T>(_ value: T?) -> Any { value.map { $0 as Any } ?? NSNull() }

func normalizedCheck(_ check: Check) -> [String: Any] {
    let status: String
    switch check.status {
    case .passed: status = "Passed"
    case .failed: status = "Failed"
    case .skipped: status = "Skipped"
    }
    return ["id": check.id, "status": status,
            "message": nullable(check.status == .passed ? nil : check.details)]
}

func normalizedChecks(_ checks: [Check]) -> [[String: Any]] { checks.map(normalizedCheck) }

func normalizedDisposition(_ disposition: Disposition) -> [String: Any] {
    switch disposition {
    case .accepted: return ["kind": "Accepted", "reason": NSNull()]
    case let .rejected(reason): return ["kind": "Rejected", "reason": reason]
    case let .ignored(reason): return ["kind": "Ignored", "reason": reason]
    }
}

func normalizedPhase(_ phase: String) throws -> String {
    switch phase {
    case "initial_state": return "InitialState"
    case "state": return "State"
    case "transition": return "Transition"
    default: throw AdapterError("unknown native check phase: \(phase)")
    }
}

func normalizedTermination(_ termination: String) throws -> String {
    switch termination {
    case "cases_completed": return "CasesCompleted"
    case "transition_limit": return "TransitionLimit"
    case "failure_found": return "FailureFound"
    case "deadline": return "Deadline"
    case "cancelled": return "Cancelled"
    case "search_complete": return "SearchComplete"
    case "attempt_limit": return "AttemptLimit"
    default: throw AdapterError("unknown native search termination: \(termination)")
    }
}

func normalizedFailure(_ failure: Failure<Int64>) throws -> [String: Any] {
    ["inputs": failure.inputs, "violations": try failure.violations.map {
        ["phase": try normalizedPhase($0.phase), "check": normalizedCheck($0.check)] as [String: Any]
    }]
}

func normalizedGuidance(_ report: GuidanceReport<Int64>) -> [String: Any] {
    ["algorithm": report.algorithm,
     "config": ["max_corpus_entries": report.config.maxCorpusEntries,
                "max_corpus_inputs": report.config.maxCorpusInputs,
                "max_features": report.config.maxFeatures,
                "max_features_per_observation": report.config.maxFeaturesPerObservation],
     "feedback": ["name": report.feedback.name, "version": report.feedback.version, "build": report.feedback.build] as [String: Any],
     "unique_features": report.uniqueFeatures, "corpus_inputs": report.corpusInputs,
     "corpus": report.corpus.map {
         ["inputs": $0.inputs, "new_features": $0.newFeatures, "discovered_case": $0.discoveredCase,
          "discovered_transition": $0.discoveredTransition] as [String: Any]
     },
     "mutation_cases": report.mutationCases, "dropped_prefixes": report.droppedPrefixes,
     "evicted_prefixes": report.evictedPrefixes, "feature_limit_reached": report.featureLimitReached,
     "corpus_limit_reached": report.corpusLimitReached]
}

func normalizedReport(_ report: FuzzReport<Int64>) throws -> [String: Any] {
    let failure = try report.failure.map(normalizedFailure)
    return ["termination": try normalizedTermination(report.termination), "seed": String(report.seed),
            "cases": report.cases, "transitions": report.transitions, "skipped_checks": report.skippedChecks,
            "failure": nullable(failure), "guidance": nullable(report.guidance.map(normalizedGuidance))]
}

func normalizedShrink(_ report: ShrinkReport<Int64>) throws -> [String: Any] {
    let history: [[String: Any]] = report.history.map { attempt in
        let operation: [String: Any]
        switch attempt.operation {
        case .validateOriginal: operation = ["kind": "ValidateOriginal"]
        case let .remove(start, end): operation = ["kind": "Remove", "start": start, "end": end]
        case let .simplify(index, alternative): operation = ["kind": "Simplify", "index": index, "alternative": alternative]
        }
        let outcome: String
        switch attempt.outcome {
        case .retained: outcome = "Retained"
        case .invalidCausality: outcome = "InvalidCausality"
        case .differentFailure: outcome = "DifferentFailure"
        case .noFailure: outcome = "NoFailure"
        case .alreadySeen: outcome = "AlreadySeen"
        case .resourceLimit: outcome = "ResourceLimit"
        }
        return ["attempt": attempt.attempt, "candidate_length": attempt.candidateLength,
                "operation": operation, "outcome": outcome]
    }
    return ["original": try normalizedFailure(report.original), "minimized": try normalizedFailure(report.minimized),
            "attempts": report.attempts, "termination": try normalizedTermination(report.termination),
            "replayed_transitions": report.replayedTransitions, "validated_original": report.validatedOriginal,
            "history": history]
}

func normalizedEngineError(_ error: Error, checkErrorPhase: String?) -> String {
    let text = String(describing: error)
    // These are engine-added leading contexts documented in README.md. Preserve
    // arbitrary callback text verbatim, including similar words inside messages.
    if text.hasPrefix("state check: ") {
        if checkErrorPhase == "initial_state" {
            return "initial check_state: " + String(text.dropFirst("state check: ".count))
        }
        if checkErrorPhase == "state" {
            return "check_state: " + String(text.dropFirst("state check: ".count))
        }
    }
    for (native, rust) in [("transition check: ", "check_transition: "),
                           ("transition: ", "step: ")] {
        if text.hasPrefix(native) { return rust + String(text.dropFirst(native.count)) }
    }
    return text
}
