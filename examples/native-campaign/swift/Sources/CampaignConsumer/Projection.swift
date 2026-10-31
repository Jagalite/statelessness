import Foundation
import Statelessness

struct AdapterError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func nullable<T>(_ value: T?) -> Any { value.map { $0 as Any } ?? NSNull() }

func fuzzConfigJSON(_ value: FuzzConfig) -> [String: Any] {
    ["seed": String(value.seed), "cases": String(value.cases),
     "max_steps": String(value.maxSteps), "max_transitions": String(value.maxTransitions),
     "mutation_percent": String(value.mutationPercent)]
}

func campaignConfigJSON(_ value: CampaignConfig) -> [String: Any] {
    ["master_seed": String(value.masterSeed), "first_job": String(value.firstJob),
     "jobs": String(value.jobs), "workers": String(value.workers),
     "result_buffer": String(value.resultBuffer), "fuzz": fuzzConfigJSON(value.fuzz),
     "stop_on_failure": value.stopOnFailure]
}

func guidanceConfigJSON(_ value: GuidanceConfig) -> [String: Any] {
    ["max_corpus_entries": String(value.maxCorpusEntries),
     "max_corpus_inputs": String(value.maxCorpusInputs),
     "max_features": String(value.maxFeatures),
     "max_features_per_observation": String(value.maxFeaturesPerObservation)]
}

func campaignMetadataJSON(_ value: Metadata) -> [String: Any] {
    ["name": value.name, "model_version": String(value.modelVersion),
     "properties_version": String(value.propertiesVersion),
     "codec_version": String(value.codecVersion), "build": value.build]
}

func checkStatusJSON(_ value: Check) -> String {
    // The reference serializes Rust's Debug variant, including its message.
    // These fixture messages are ASCII; Swift reflection quotes/escapes them
    // identically. This is a projection of the actual returned check details.
    switch value.status {
    case .passed: return "Passed"
    case .failed: return "Failed(\(String(reflecting: value.details)))"
    case .skipped: return "Skipped(\(String(reflecting: value.details)))"
    }
}

func phaseJSON(_ value: String) throws -> String {
    switch value {
    case "initial_state": return "InitialState"
    case "state": return "State"
    case "transition": return "Transition"
    default: throw AdapterError("unknown native check phase: \(value)")
    }
}

func failureJSON(_ value: Failure<UInt64>) throws -> [String: Any] {
    ["inputs": value.inputs.map(String.init), "violations": try value.violations.map {
        ["phase": try phaseJSON($0.phase), "id": $0.check.id,
         "status": checkStatusJSON($0.check)] as [String: Any]
    }]
}

func guidanceJSON(_ value: GuidanceReport<UInt64>) -> [String: Any] {
    ["algorithm": value.algorithm, "config": guidanceConfigJSON(value.config),
     "feedback": ["name": value.feedback.name, "version": String(value.feedback.version),
                  "build": value.feedback.build],
     "unique_features": String(value.uniqueFeatures), "corpus_inputs": String(value.corpusInputs),
     "corpus": value.corpus.map {
         ["inputs": $0.inputs.map(String.init), "new_features": String($0.newFeatures),
          "discovered_case": String($0.discoveredCase),
          "discovered_transition": String($0.discoveredTransition)] as [String: Any]
     },
     "mutation_cases": String(value.mutationCases), "dropped_prefixes": String(value.droppedPrefixes),
     "evicted_prefixes": String(value.evictedPrefixes),
     "feature_limit_reached": value.featureLimitReached, "corpus_limit_reached": value.corpusLimitReached]
}

func fuzzJSON(_ value: FuzzReport<UInt64>) throws -> [String: Any] {
    let termination: String
    switch value.termination {
    case "cases_completed": termination = "CasesCompleted"
    case "transition_limit": termination = "TransitionLimit"
    case "failure_found": termination = "FailureFound"
    case "cancelled": termination = "Cancelled"
    case "deadline": termination = "Deadline"
    default: throw AdapterError("unknown native fuzz termination: \(value.termination)")
    }
    return ["termination": termination, "seed": String(value.seed), "cases": String(value.cases),
            "transitions": String(value.transitions), "skipped_checks": String(value.skippedChecks),
            "failure": nullable(try value.failure.map(failureJSON)),
            "guidance": nullable(value.guidance.map(guidanceJSON))]
}

func reportJSON(_ value: CampaignReport) -> [String: Any] {
    let termination: String
    let error: String?
    switch value.termination {
    case .jobsCompleted: termination = "JobsCompleted"; error = nil
    case .failureFound: termination = "FailureFound"; error = nil
    case .cancelled: termination = "Cancelled"; error = nil
    case .deadline: termination = "Deadline"; error = nil
    case let .callbackError(message): termination = "CallbackError"; error = message
    case let .workerError(message): termination = "WorkerError"; error = message
    }
    return ["requested": String(value.requested), "started": String(value.started),
            "finished": String(value.finished), "not_started": String(value.notStarted),
            "delivered": String(value.delivered), "failures": String(value.failures),
            "errors": String(value.errors), "reported_transitions": String(value.reportedTransitions),
            "reported_cases": String(value.reportedCases),
            "reported_skipped_checks": String(value.reportedSkippedChecks),
            "accounting_complete": value.accountingComplete, "termination": termination,
            "error": nullable(error)]
}

func jobJSON(_ value: JobReport<UInt64>) throws -> [String: Any] {
    let outcome: String
    let error: String?
    let fuzz: [String: Any]?
    switch value.outcome {
    case let .completed(report): outcome = "Completed"; error = nil; fuzz = try fuzzJSON(report)
    case let .factoryError(message): outcome = "FactoryError"; error = message.description; fuzz = nil
    case let .modelError(message): outcome = "ModelError"; error = message.description; fuzz = nil
    case let .panicked(message): outcome = "Panicked"; error = message; fuzz = nil
    }
    return ["job_id": String(value.jobID), "seed": String(value.seed),
            "config": fuzzConfigJSON(value.config), "metadata": nullable(value.metadata.map(campaignMetadataJSON)),
            "outcome": outcome, "error": nullable(error), "fuzz": nullable(fuzz)]
}
