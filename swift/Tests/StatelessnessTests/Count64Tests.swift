import XCTest
@testable import Statelessness

private func count64Model(failureAt: Int? = nil, empty: Bool = false) -> Generate<Int, Int, Int> {
    Generate(model: Model(initialState: { 0 }, step: { s, i in Transition(state: s + i) },
        checkState: { s in failureAt == s ? [.failed("bounded", "witness")] : [] },
        cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }),
        generate: { _, _ in empty ? nil : 1 }, equalInputs: ==)
}
private func count64Job(failureAt: Int? = nil) -> CampaignJob<Int, Int, Int> {
    CampaignJob(count64Model(failureAt: failureAt), metadata: {
        Metadata(name: "u64", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "bounded")
    })
}

final class Count64Tests: XCTestCase {
    func testOrdinaryMaximumCountsPreCancellationAndZeroWork() throws {
        var g = count64Model()
        g.model.initialState = { XCTFail("unexpected materialization"); return 0 }
        let stopped = try fuzz64(g, config: FuzzConfig64(cases: .max, maxSteps: .max), limits: RunLimits(isCancelled: { true }))
        XCTAssertEqual(stopped.termination, "cancelled"); XCTAssertEqual(stopped.cases, 0); XCTAssertEqual(stopped.transitions, 0)
        let zero = try fuzz64(g, config: FuzzConfig64(cases: 0, maxSteps: .max))
        XCTAssertEqual(zero.termination, "cases_completed"); XCTAssertEqual(zero.cases, 0)
        let zeroCancelled = try fuzz64(g, config: FuzzConfig64(cases: 0, maxSteps: .max), limits: RunLimits(isCancelled: { true }))
        XCTAssertEqual(zeroCancelled.termination, "cancelled")
    }
    func testActualBoundedWorkUnderMaximumBudgets() throws {
        let empty = try fuzz64(count64Model(empty: true), config: FuzzConfig64(cases: 1, maxSteps: .max))
        XCTAssertEqual(empty.cases, 1); XCTAssertEqual(empty.transitions, 0); XCTAssertEqual(empty.termination, "cases_completed")
        for initial in [true, false] {
            let report = try fuzz64(count64Model(failureAt: initial ? 0 : 1), config: FuzzConfig64(cases: .max, maxSteps: .max, maxTransitions: .max))
            XCTAssertEqual(report.cases, 1); XCTAssertEqual(report.transitions, initial ? 0 : 1)
            XCTAssertEqual(report.termination, "failure_found"); XCTAssertEqual(report.failure?.inputs, initial ? [] : [1])
        }
    }
    func testGuidedMaximumLimitsMetadataAndBoundedRetention() throws {
        var metadata = 0, features = 0
        let feedback = Feedback<Int, Int, Int, Int>(metadata: { metadata += 1; return FeedbackMetadata(name: "wide", build: "1") },
            cloneFeature: { $0 }, initialFeatures: { state, sink in features += 1; try sink.push(state) },
            transitionFeatures: { _, _, after, sink in features += 1; try sink.push(after.state) })
        let guidance = GuidanceConfig64(maxCorpusEntries: .max, maxCorpusInputs: .max, maxFeatures: .max, maxFeaturesPerObservation: .max)
        let stopped = try guidedFuzz64(count64Model(), config: FuzzConfig64(cases: .max, maxSteps: .max), guidance: guidance, feedback: feedback, limits: RunLimits(isCancelled: { true }))
        XCTAssertEqual(metadata, 1); XCTAssertEqual(features, 0); XCTAssertEqual(stopped.cases, 0)
        XCTAssertEqual(stopped.guidance?.config.maxCorpusInputs, .max)
        let report = try guidedFuzz64(count64Model(), config: FuzzConfig64(cases: 1, maxSteps: 2), guidance: guidance, feedback: feedback)
        XCTAssertEqual(report.guidance?.uniqueFeatures, 3); XCTAssertEqual(report.guidance?.corpusInputs, 3)
        XCTAssertEqual(report.guidance?.corpus.count, 2); XCTAssertEqual(report.guidance?.corpus.last?.discoveredCase, 1)
        XCTAssertThrowsError(try guidedFuzz64(count64Model(), config: FuzzConfig64(mutationPercent: 101), guidance: GuidanceConfig64(maxCorpusEntries: 0), feedback: feedback)) {
            XCTAssertEqual(String(describing: $0), "guidance limits must be positive")
        }
        XCTAssertThrowsError(try guidedFuzz64(count64Model(), config: FuzzConfig64(mutationPercent: 101), feedback: feedback)) {
            XCTAssertEqual(String(describing: $0), "mutation_percent must be in 0..=100")
        }
        XCTAssertEqual(metadata, 2)
    }
    func testCampaignMaximumRequestedCancellationAndOneFailure() throws {
        let config = CampaignConfig64(firstJob: 1, jobs: .max, fuzz: FuzzConfig64(cases: 1, maxSteps: .max, maxTransitions: 1), stopOnFailure: true)
        let stopped = try runCampaign64(config: config, limits: CampaignLimits(isCancelled: { true }),
            factory: { _ -> CampaignJob<Int, Int, Int> in XCTFail("unexpected factory"); return count64Job() }, sink: { _ in XCTFail("unexpected sink") })
        XCTAssertEqual(stopped.requested, .max); XCTAssertEqual(stopped.notStarted, .max)
        XCTAssertEqual(stopped.started, 0); XCTAssertEqual(stopped.finished, 0); XCTAssertEqual(stopped.delivered, 0)
        XCTAssertEqual(stopped.termination, .cancelled)
        var deliveries: [UInt64] = []
        let failed = try runCampaign64(config: config, factory: { _ in count64Job(failureAt: 0) }, sink: { deliveries.append($0.jobID) })
        XCTAssertEqual(deliveries, [1]); XCTAssertEqual(failed.requested, .max); XCTAssertEqual(failed.notStarted, UInt64.max - 1)
        XCTAssertEqual(failed.started, 1); XCTAssertEqual(failed.finished, 1); XCTAssertEqual(failed.delivered, 1); XCTAssertEqual(failed.failures, 1)
        XCTAssertEqual(failed.reportedCases, 1); XCTAssertEqual(failed.reportedTransitions, 0); XCTAssertEqual(failed.termination, .failureFound)
    }
    func testCampaignAdmissionOrderAndWideProductBeforeCap() throws {
        let zero = FuzzConfig64(cases: 0, maxSteps: .max)
        try CampaignConfig64(firstJob: 1, jobs: .max, fuzz: zero).validate()
        try CampaignConfig64(jobs: 1, fuzz: FuzzConfig64(cases: .max, maxSteps: .max, maxTransitions: 1)).validate()
        let tests: [(CampaignConfig64, String)] = [
            (CampaignConfig64(jobs: 0, workers: 0, resultBuffer: .max, fuzz: FuzzConfig64(mutationPercent: 101)), "campaign jobs must be positive"),
            (CampaignConfig64(jobs: .max, workers: .max, resultBuffer: .max), "campaign workers must be in 1..=256"),
            (CampaignConfig64(jobs: .max, resultBuffer: .max, fuzz: FuzzConfig64(mutationPercent: 101)), "campaign result_buffer must be in 0..=1024"),
            (CampaignConfig64(firstJob: 2, jobs: .max, fuzz: FuzzConfig64(mutationPercent: 101)), "mutation_percent must be in 0..=100"),
            (CampaignConfig64(firstJob: 2, jobs: .max, fuzz: zero), "campaign job ID range overflows u64"),
            (CampaignConfig64(jobs: 2, fuzz: FuzzConfig64(cases: .max, maxSteps: 0)), "campaign aggregate cases overflow u64"),
            (CampaignConfig64(jobs: 2, fuzz: FuzzConfig64(cases: 1, maxSteps: .max, maxTransitions: .max)), "campaign aggregate transitions overflow u64")
        ]
        for (config, expected) in tests {
            XCTAssertThrowsError(try runCampaign64(config: config, limits: CampaignLimits(isCancelled: { true }), factory: { _ in count64Job() }, sink: { _ in XCTFail("unexpected sink") })) {
                XCTAssertEqual(String(describing: $0), expected)
            }
        }
    }
    func testGuidedCampaignWideCountsAndMetadataPanicProvenance() throws {
        let guidance = GuidanceConfig64(maxCorpusEntries: .max, maxCorpusInputs: .max, maxFeatures: .max, maxFeaturesPerObservation: .max)
        var outcomes: [JobReport64<Int>] = []
        let result = try runGuidedCampaign64(config: CampaignConfig64(jobs: 1, fuzz: FuzzConfig64(cases: .max, maxSteps: .max, maxTransitions: 1)), guidance: guidance,
            factory: { _ in count64Job() }, feedbackFactory: { _, _ in
                Feedback<Int, Int, Int, Int>(metadata: { throw CampaignPanic("metadata panic") }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, _ in })
            }, sink: { outcomes.append($0) })
        XCTAssertEqual(result.errors, 1); XCTAssertFalse(result.accountingComplete)
        XCTAssertEqual(outcomes.first?.config.cases, .max); XCTAssertNotNil(outcomes.first?.metadata)
        guard case .panicked("metadata panic")? = outcomes.first?.outcome else { return XCTFail("panic provenance lost") }
    }
    func testLifecycleMaximumCountsCodecRestartAcknowledgementAndSaturation() throws {
        let core = CampaignLifecycle(config: CampaignConfig64(firstJob: 1, jobs: .max, fuzz: FuzzConfig64(cases: 0)))
        let codec = CampaignLifecycleTestCodec(core: core)
        var state = try core.initialState()
        XCTAssertEqual(state.report.requested, .max)
        state = try codec.decodeState(codec.encodeState(state))
        XCTAssertEqual(core.apply(&state, .claim(nil)), [.runJob(1)])
        XCTAssertEqual(core.apply(&state, .claim(nil)), [.runJob(2)])
        let summary = CampaignSummary(outcome: .completed, transitions: .max, cases: .max, skippedChecks: .max)
        for id: UInt64 in 1...2 {
            XCTAssertEqual(core.apply(&state, .complete(id, summary)), [])
            XCTAssertEqual(state.report.delivered, id - 1)
            XCTAssertEqual(core.apply(&state, .receive(id)), [.deliver(id)])
            state = try codec.decodeState(codec.encodeState(state))
            XCTAssertEqual(state.report.delivered, id - 1)
            XCTAssertNil(core.apply(&state, .sinkReturned(id + 1, nil)))
            XCTAssertEqual(core.apply(&state, .sinkReturned(id, nil)), [])
            XCTAssertNil(core.apply(&state, .sinkReturned(id, nil)))
        }
        XCTAssertEqual(state.report.delivered, 2); XCTAssertEqual(state.report.notStarted, UInt64.max - 2)
        XCTAssertEqual(state.report.reportedCases, .max); XCTAssertEqual(state.report.reportedTransitions, .max)
        XCTAssertEqual(state.report.reportedSkippedChecks, .max); XCTAssertFalse(state.report.accountingComplete)
        XCTAssertEqual(core.apply(&state, .claim(.cancelled)), [])
        XCTAssertEqual(core.apply(&state, .finish), [])
        XCTAssertEqual(state.report.termination, .cancelled)
        XCTAssertEqual(try codec.decodeState(codec.encodeState(state)), state)
    }
    func testLegacyConversionsAreCheckedAndShareKernel() throws {
        XCTAssertThrowsError(try FuzzConfig64(cases: .max).checkedInt())
        XCTAssertThrowsError(try GuidanceConfig64(maxFeatures: .max).checkedInt())
        XCTAssertThrowsError(try CampaignConfig64(jobs: .max).checkedInt())
        XCTAssertThrowsError(try CampaignReport64(requested: .max).checkedInt())
        let old = try fuzz(count64Model(failureAt: 1), config: FuzzConfig(cases: 3, maxSteps: 4))
        let exact = try fuzz64(count64Model(failureAt: 1), config: FuzzConfig64(cases: 3, maxSteps: 4))
        XCTAssertEqual(old.cases, try exact.checkedInt().cases); XCTAssertEqual(old.failure?.inputs, exact.failure?.inputs)
        XCTAssertEqual(saturatingCountAdd(.max, 1), .max)
    }
}

extension Count64Tests {
    func testAutomaticGenerationAndShrinkMaximumCounts() throws {
        var model = count64Model().model
        model.inputs = { _ in InputIterator(values: [7, 8]) }
        var rng = Rng(seed: 1)
        XCTAssertNotNil(try sampleEnumerated64(model, state: 0, rng: &rng, options: AutoOptions64(maxCandidates: .max)))
        XCTAssertTrue(try containsEnumerated64(model, state: 0, input: 8, equalInputs: ==, options: AutoOptions64(maxCandidates: .max)))
        let automatic = try Auto64(model, options: AutoOptions64(maxCandidates: .max), equalInputs: ==)
        XCTAssertNotNil(try automatic.generated.generate(0, &rng))
        model.inputs = { _ in InputIterator(values: []) }
        XCTAssertNil(try sampleEnumerated64(model, state: 0, rng: &rng, options: AutoOptions64(maxCandidates: .max)))
        let original = Failure(inputs: [1], violations: [Violation(phase: "state", check: .failed("bounded", "witness"))])
        let stopped = try shrink64(count64Model(failureAt: 1), original: original, config: ShrinkConfig64(maxAttempts: .max), limits: ShrinkLimits(control: RunLimits(isCancelled: { true })))
        XCTAssertEqual(stopped.termination, "cancelled"); XCTAssertEqual(stopped.attempts, 1); XCTAssertFalse(stopped.validatedOriginal)
        let report = try shrink64(count64Model(failureAt: 1), original: original, config: ShrinkConfig64(maxAttempts: .max))
        XCTAssertEqual(report.termination, "search_complete"); XCTAssertEqual(report.minimized.inputs, [1]); XCTAssertEqual(report.attempts, 2)
        XCTAssertThrowsError(try ShrinkConfig64(maxAttempts: .max).checkedInt())
    }
}


extension Count64Tests {
    func testNativeGrowthCheckRequiresOnlyActualMaterialization() throws {
        try requireNativeGrowth(0, context: "fixture")
        try requireNativeGrowth(Int.max - 1, context: "fixture")
        XCTAssertThrowsError(try requireNativeGrowth(Int.max, context: "fixture"))
        XCTAssertThrowsError(try requireNativeGrowth(-1, context: "fixture"))
        try requireNativeGrowth(Int.max / 2 - 1, elementStride: 2, context: "fixture")
        XCTAssertThrowsError(try requireNativeGrowth(Int.max / 2, elementStride: 2, context: "fixture"))
        XCTAssertThrowsError(try requireNativeGrowth(0, elementStride: -1, context: "fixture"))
    }
}
