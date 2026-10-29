import Foundation
import Statelessness

let campaignCaseNames = [
    "ordinary-basic", "guided-basic", "factory-error", "feedback-error", "factory-cancelled",
    "pre-cancelled", "deadline-zero", "cases-zero", "steps-zero", "transitions-zero",
    "initial-error", "step-error", "check-error", "metadata-panic", "feedback-metadata-panic", "drop-panic",
    "sink-error", "sink-panic", "property-stop"
]

func seedInputs() -> [(UInt64, UInt64)] {
    let half: UInt64 = 1 << 63
    let exactDoubleLimit: UInt64 = 1 << 53
    return [(0, 0), (0, 1), (0, 2), (0, 7), (0, 42), (0, .max),
            (.max, 0), (.max, 1), (1, .max), (.max, 2), (2, .max),
            (half, half), (half, half + 1),
            (exactDoubleLimit, UInt64.max - exactDoubleLimit + 1),
            (exactDoubleLimit + 1, UInt64.max - exactDoubleLimit)]
}

func seedName(_ index: Int) -> String { String(format: "seed-%02d", index) }

func seedRow(_ index: Int, masterSeed: UInt64, jobID: UInt64) -> [String: Any] {
    ["schema": "native-campaign-v1", "kind": "seed", "case": seedName(index),
     "master_seed": String(masterSeed), "job_id": String(jobID),
     "seed": String(jobSeed(masterSeed: masterSeed, jobID: jobID))]
}

func validationCases() -> [(String, CampaignConfig)] {
    let portableMax = 9_007_199_254_740_991
    return [("jobs-zero", fixtureConfig(jobs: 0)),
     ("workers-zero", fixtureConfig(workers: 0)),
     ("workers-257", fixtureConfig(workers: 257)),
     ("buffer-1025", fixtureConfig(resultBuffer: 1025)),
     ("workers-256", fixtureConfig(workers: 256)),
     ("buffer-1024", fixtureConfig(resultBuffer: 1024)),
     ("max-id", fixtureConfig(firstJob: .max)),
     ("id-overflow", fixtureConfig(firstJob: .max, jobs: 2)),
     ("mutation-101", fixtureConfig(mutationPercent: 101)),
     ("mutation-100", fixtureConfig(mutationPercent: 100)),
     ("aggregate-cases", fixtureConfig(jobs: 2049, cases: portableMax)),
     ("aggregate-transitions", fixtureConfig(jobs: 2049, maxSteps: portableMax, maxTransitions: .max)),
     ("wide-product-capped", fixtureConfig(jobs: 2, cases: portableMax, maxSteps: portableMax, maxTransitions: 1)),
     ("max-job-count-valid", fixtureConfig(firstJob: 0, jobs: portableMax, maxSteps: 1, maxTransitions: 1)),
     ("validation-first-error", fixtureConfig(jobs: 0, workers: 0, resultBuffer: 1025, mutationPercent: 101))]
}

func validationRow(_ name: String, config: CampaignConfig) throws -> [String: Any] {
    var validationError: String?
    do { try config.validate() }
    catch { validationError = String(describing: error) }
    if let validationError {
        let audit = CallbackAudit()
        var runnerError: String?
        do {
            _ = try runCampaign(config: config, factory: { id -> CampaignJob<CounterState, UInt64, CounterOutput> in
                audit.note(id, "factory")
                throw AdapterError("invalid campaign invoked factory")
            }, sink: { job in
                audit.note(job.jobID, "sink")
                throw AdapterError("invalid campaign invoked sink")
            })
        } catch { runnerError = String(describing: error) }
        try require(runnerError == validationError, "campaign admission disagrees with config validation")
        try require(audit.snapshot().events.isEmpty, "invalid campaign invoked application callbacks")
    }
    return ["schema": "native-campaign-v1", "kind": "validation", "case": name,
            "config": campaignConfigJSON(config), "error": nullable(validationError)]
}

func campaignCase(_ name: String) throws -> [String: Any] {
    try require(campaignCaseNames.contains(name), "unknown campaign case: \(name)")
    let audit = CallbackAudit()
    if name == "pre-cancelled" { audit.cancel() }
    let guided = name.hasPrefix("guided") || name == "feedback-error" || name == "factory-cancelled"
        || name == "feedback-metadata-panic"
    let factoryError = name == "factory-error" || name == "feedback-error"
    let config = fixtureConfig(jobs: factoryError ? 2 : name == "property-stop" ? 3 : 1,
        cases: name == "cases-zero" ? 0 : name == "steps-zero" ? 2 : 1,
        maxSteps: name == "steps-zero" ? 0 : 2,
        maxTransitions: name == "steps-zero" || name == "transitions-zero" ? 0 : 2,
        stopOnFailure: factoryError || name == "property-stop")
    let mode: CounterMode
    switch name {
    case "property-stop": mode = .property
    case "initial-error": mode = .initialError
    case "step-error": mode = .stepError
    case "check-error": mode = .checkError
    case "metadata-panic": mode = .metadataPanic
    case "drop-panic": mode = .dropPanic
    default: mode = .pass
    }
    let limits = CampaignLimits(maxDuration: name == "deadline-zero" ? 0 : nil,
                                isCancelled: { audit.isCancelled })
    let factory: @Sendable (UInt64) throws -> CampaignJob<CounterState, UInt64, CounterOutput> = { id in
        audit.note(id, "factory")
        if name == "factory-error" { throw ModelError("factory error") }
        if name == "factory-cancelled" { audit.cancel() }
        return counterJob(id, mode: mode, audit: audit)
    }
    var jobs: [JobReport<UInt64>] = []
    let sink: (JobReport<UInt64>) throws -> Void = { job in
        audit.note(job.jobID, "sink")
        jobs.append(job)
        if name == "sink-error" { throw ModelError("first sink error") }
        if name == "sink-panic" { throw CampaignPanic("sink panic") }
    }
    let report: CampaignReport
    if guided {
        report = try runGuidedCampaign(config: config, limits: limits, guidance: fixtureGuidance(),
            factory: factory, feedbackFactory: { id, _ in
                audit.note(id, "feedback_factory")
                if name == "feedback-error" { throw ModelError("feedback factory error") }
                return counterFeedback(id, audit: audit, metadataPanic: name == "feedback-metadata-panic")
            }, sink: sink)
    } else {
        report = try runCampaign(config: config, limits: limits, factory: factory, sink: sink)
    }
    // No sort: the sink owns this append order, including a report whose sink
    // later throws. Such a receipt remains absent from report.delivered.
    return ["schema": "native-campaign-v1", "kind": "campaign", "case": name,
            "config": campaignConfigJSON(config), "guided": guided,
            "jobs": try jobs.map(jobJSON), "callbacks": canonicalCallbacks(audit.snapshot()),
            "report": reportJSON(report)]
}

func allCaseNames() -> [String] {
    seedInputs().indices.map(seedName) + validationCases().map { $0.0 } + campaignCaseNames
}

func runCase(_ name: String) throws -> [String: Any] {
    for (index, input) in seedInputs().enumerated() where name == seedName(index) {
        return seedRow(index, masterSeed: input.0, jobID: input.1)
    }
    if let (_, config) = validationCases().first(where: { $0.0 == name }) {
        return try validationRow(name, config: config)
    }
    return try campaignCase(name)
}
