import Statelessness

private enum PolicyMode: String, Sendable {
    case stepError = "step-error", checkError = "check-error"
    case stepMarker = "n05-reducer-marker", observerMarker = "n05-observer-marker"
    case feedbackMetadataMarker = "n05-feedback-metadata-marker"
    case feedbackMetadataPanic = "feedback-metadata-panic"
    case factoryMarker = "factory-marker", feedbackFactoryMarker = "feedback-factory-marker"
    case metadataMarker = "metadata-marker", metadataError = "metadata-ordinary-error"
    case teardownMarker = "teardown-marker", teardownError = "teardown-ordinary-error"
    case feedbackLifetime = "feedback-arc-release-order"
}

private func policyJob(_ id: UInt64, mode: PolicyMode, audit: CallbackAudit)
    -> CampaignJob<CounterState, UInt64, CounterOutput> {
    let base = counterJob(id, mode: mode == .stepError ? .stepError : mode == .checkError ? .checkError : .pass,
                          audit: audit)
    var generated = base.generated
    if mode == .stepMarker {
        generated.model.step = { before, input in
            audit.note(id, "step:\(before.value):\(input)", input: input)
            throw CampaignPanic("inside reducer")
        }
    }
    let observer: SearchObserver<CounterState, UInt64, CounterOutput>?
    if mode == .observerMarker {
        observer = SearchObserver(initial: { _, _ in
            audit.note(id, "observer_marker")
            throw CampaignPanic("inside observer")
        })
    } else { observer = base.observer }
    return CampaignJob(generated, metadata: {
        let value = try base.metadata()
        if mode == .metadataMarker { throw CampaignPanic("metadata marker") }
        if mode == .metadataError { throw ModelError("metadata error") }
        return value
    }, observer: observer, tearDown: {
        try base.tearDown()
        if mode == .teardownMarker { throw CampaignPanic("teardown marker") }
        if mode == .teardownError { throw ModelError("teardown error") }
    })
}

private func policyCase(_ mode: PolicyMode, expectedOutcome: String, expectedError: String?,
                        hasMetadata: Bool) throws -> [String: Any] {
    let audit = CallbackAudit()
    let config = fixtureConfig()
    let factory: @Sendable (UInt64) throws -> CampaignJob<CounterState, UInt64, CounterOutput> = { id in
        audit.note(id, "factory")
        if mode == .factoryMarker { throw CampaignPanic("factory marker") }
        return policyJob(id, mode: mode, audit: audit)
    }
    var jobs: [JobReport<UInt64>] = []
    let sink: (JobReport<UInt64>) throws -> Void = { job in
        audit.note(job.jobID, "sink"); jobs.append(job)
    }
    let guided = mode == .feedbackMetadataMarker || mode == .feedbackMetadataPanic
        || mode == .feedbackFactoryMarker || mode == .feedbackLifetime
    let report: CampaignReport
    if guided {
        report = try runGuidedCampaign(config: config, guidance: fixtureGuidance(), factory: factory,
            feedbackFactory: { id, _ in
                audit.note(id, "feedback_factory")
                if mode == .feedbackFactoryMarker { throw CampaignPanic("feedback factory marker") }
                var feedback = counterFeedback(id, audit: audit, metadataPanic: mode == .feedbackMetadataPanic)
                if mode == .feedbackMetadataMarker {
                    let metadata = feedback.metadata
                    feedback.metadata = {
                        _ = try metadata()
                        throw CampaignPanic("inside feedback metadata")
                    }
                }
                return feedback
            }, sink: sink)
    } else { report = try runCampaign(config: config, factory: factory, sink: sink) }
    try require(jobs.count == 1 && report.delivered == 1, "native policy case did not deliver one job")
    guard let job = jobs.first else { throw AdapterError("native policy job missing") }
    let rawOutcome: String
    let rawError: String?
    switch job.outcome {
    case .completed: rawOutcome = "Completed"; rawError = nil
    case let .factoryError(error): rawOutcome = "FactoryError"; rawError = error.description
    case let .modelError(error): rawOutcome = "ModelError"; rawError = error.description
    case let .panicked(error): rawOutcome = "Panicked"; rawError = error
    }
    try require(rawOutcome == expectedOutcome && rawError == expectedError,
                "native policy changed: \(mode.rawValue), \(rawOutcome), \(rawError ?? "null")")
    try require((job.metadata != nil) == hasMetadata, "native policy lost metadata boundary: \(mode.rawValue)")
    try require(report.accountingComplete == (rawOutcome == "Completed"),
                "native policy incorrectly retained hidden work accounting")
    let snapshot = audit.snapshot()
    if mode == .feedbackLifetime || mode == .feedbackMetadataMarker || mode == .feedbackMetadataPanic {
        guard let feedbackDrop = snapshot.events.firstIndex(where: { $0.name == "feedback_drop" }),
              let modelDrop = snapshot.events.firstIndex(where: { $0.name == "model_drop" }),
              let sink = snapshot.events.firstIndex(where: { $0.name == "sink" }) else {
            throw AdapterError("native feedback/model cleanup event missing")
        }
        try require(feedbackDrop < modelDrop && modelDrop < sink,
                    "native ARC feedback release did not precede model teardown and delivery")
    }
    let projected = try jobJSON(job)
    let projectedOutcome = projected["outcome"] as? String
    let projectedError = projected["error"] as? String
    try require(projectedOutcome == rawOutcome && projectedError == rawError,
                "native policy projection changed outcome or diagnostic: \(mode.rawValue)")
    return ["case": mode.rawValue, "guided": guided,
            "raw_outcome": rawOutcome, "raw_native_error": nullable(rawError),
            "metadata": nullable(job.metadata.map(campaignMetadataJSON)),
            "canonical_projection": projected,
            "error_spelling_mapped": rawError != projectedError,
            // Keep native observer events too; this output describes Swift policy.
            "callbacks": snapshot.events.map { "\($0.jobID):\($0.name)" },
            "report": reportJSON(report)]
}

func nativePolicyChecks() throws -> [String: Any] {
    let specifications: [(PolicyMode, String, String?, Bool)] = [
        (.stepError, "ModelError", "step: step error", true),
        (.checkError, "ModelError", "check_state: check error", true),
        (.stepMarker, "ModelError", "step: inside reducer", true),
        (.observerMarker, "ModelError", "search observer initial: inside observer", true),
        (.feedbackMetadataMarker, "Panicked", "inside feedback metadata", true),
        (.feedbackMetadataPanic, "Panicked", "feedback metadata panic", true),
        (.factoryMarker, "Panicked", "factory marker", false),
        (.feedbackFactoryMarker, "Panicked", "feedback factory marker", true),
        (.metadataMarker, "Panicked", "metadata marker", false),
        (.metadataError, "Panicked", "metadata error", false),
        (.teardownMarker, "Panicked", "teardown marker", true),
        (.teardownError, "Panicked", "teardown error", true),
        (.feedbackLifetime, "Completed", nil, true)
    ]
    var cases: [[String: Any]] = []
    for (mode, outcome, error, metadata) in specifications {
        cases.append(try policyCase(mode, expectedOutcome: outcome, expectedError: error, hasMetadata: metadata))
    }
    return ["schema": "native-campaign-swift-policy-v1", "engine": "swift", "cases": cases]
}
