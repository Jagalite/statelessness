import Foundation
import XCTest
@testable import Statelessness

private func guidedCounter() -> Generate<Int, Int, Int> {
    Generate(model: Model(initialState: { 0 }, step: { s, i in Transition(state: s + i, outputs: [s + i]) },
                          checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }),
             generate: { _, _ in 1 }, equalInputs: ==)
}
private let guidedIdentity = FeedbackMetadata(name: "test", build: "v1")
private func counterFeedback() -> StateFeedback<Int, Int> {
    StateFeedback(metadata: { guidedIdentity }, cloneFeature: { $0 }, project: { $0 })
}

final class GuidedTests: XCTestCase {
    func testDefaultsAndDefaultEmptyInitialFeedback() throws {
        let config = GuidanceConfig()
        XCTAssertEqual(config.maxCorpusEntries, 256); XCTAssertEqual(config.maxCorpusInputs, 25_600)
        XCTAssertEqual(config.maxFeatures, 100_000); XCTAssertEqual(config.maxFeaturesPerObservation, 256)
        let feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 },
            transitionFeatures: { _, _, t, sink in try sink.push(t.state) })
        let r = try guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 2, maxSteps: 0, maxTransitions: 0), feedback: feedback)
        XCTAssertEqual(r.cases, 2); XCTAssertEqual(r.transitions, 0); XCTAssertEqual(r.termination, "cases_completed")
        XCTAssertEqual(r.guidance?.uniqueFeatures, 0); XCTAssertEqual(r.guidance?.algorithm, "feature-corpus-v2")
        XCTAssertNil(try fuzz(guidedCounter(), config: FuzzConfig(cases: 0)).guidance)
    }
    func testValidationMetadataAndPreCancellationOrder() throws {
        var metadataCalls = 0, calls = 0
        var g = guidedCounter(); g.model.initialState = { calls += 1; return 0 }
        let feedback = Feedback<Int, Int, Int, Int>(metadata: {
            metadataCalls += 1; return FeedbackMetadata(name: "", version: 0, build: "")
        }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, _ in calls += 1 })
        let stop = RunLimits(isCancelled: { true })
        XCTAssertThrowsError(try guidedFuzz(g, config: FuzzConfig(mutationPercent: 101), guidance: GuidanceConfig(maxFeatures: 0), feedback: feedback, limits: stop)) { XCTAssertEqual(String(describing: $0), "guidance limits must be positive") }
        XCTAssertThrowsError(try guidedFuzz(g, config: FuzzConfig(mutationPercent: 101), feedback: feedback, limits: stop)) {
            XCTAssertEqual(String(describing: $0), "mutation_percent must be in 0..=100")
        }
        XCTAssertThrowsError(try fuzz(g, config: FuzzConfig(mutationPercent: 101), limits: stop)) {
            XCTAssertEqual(String(describing: $0), "invalid fuzz configuration")
        }
        XCTAssertThrowsError(try guidedFuzz(g, feedback: feedback, limits: RunLimits(maxDuration: .nan)))
        XCTAssertEqual(metadataCalls, 0)
        let report = try guidedFuzz(g, feedback: feedback, limits: stop)
        XCTAssertEqual(metadataCalls, 1); XCTAssertEqual(calls, 0); XCTAssertEqual(report.termination, "cancelled")
        XCTAssertEqual(report.guidance?.feedback.version, 0)
    }
    func testMetadataTimeCountsAndMetadataThrowWinsPreCancellation() throws {
        let feedback = Feedback<Int, Int, Int, Int>(metadata: {
            Thread.sleep(forTimeInterval: 0.02); return guidedIdentity
        }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, _ in })
        let r = try guidedFuzz(guidedCounter(), feedback: feedback, limits: RunLimits(maxDuration: 0.001))
        XCTAssertEqual(r.termination, "deadline"); XCTAssertEqual(r.cases, 0)
        var throwing = feedback; throwing.metadata = { throw ModelError("metadata failed") }
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), feedback: throwing, limits: RunLimits(isCancelled: { true }))) {
            XCTAssertTrue(String(describing: $0).contains("metadata failed"))
        }
    }
    func testFIFOEvictionKeepsWitnessedBitsAndExactCapacityDoesNotSaturate() throws {
        let exactly = try guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 1, maxSteps: 2),
            guidance: GuidanceConfig(maxCorpusEntries: 2, maxCorpusInputs: 3, maxFeatures: 3), feedback: counterFeedback())
        XCTAssertFalse(try XCTUnwrap(exactly.guidance).corpusLimitReached)
        XCTAssertFalse(try XCTUnwrap(exactly.guidance).featureLimitReached)
        let r = try guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 2, maxSteps: 3, mutationPercent: 0),
            guidance: GuidanceConfig(maxCorpusEntries: 2, maxCorpusInputs: 3), feedback: counterFeedback())
        let h = try XCTUnwrap(r.guidance)
        XCTAssertEqual(h.uniqueFeatures, 4); XCTAssertEqual(h.corpusInputs, 3); XCTAssertEqual(h.evictedPrefixes, 2)
        XCTAssertTrue(h.corpusLimitReached); XCTAssertEqual(h.droppedPrefixes, 0)
        XCTAssertEqual(h.corpus.map(\.inputs), [[1,1,1]])
        XCTAssertEqual(h.corpus.first?.discoveredCase, 1); XCTAssertEqual(h.corpus.first?.discoveredTransition, 3)
    }
    func testOverlongDuplicateDropAndRecoveryAtFeatureCap() throws {
        var caseNumber = 0
        var g = guidedCounter(); g.model.initialState = { caseNumber += 1; return 0 }
        let feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 },
            transitionFeatures: { _, _, t, sink in
                if caseNumber == 1 && t.state >= 2 { try sink.push(7); try sink.push(7) }
                if caseNumber == 2 && t.state == 1 { try sink.push(7); try sink.push(8) }
            })
        let r = try guidedFuzz(g, config: FuzzConfig(cases: 2, maxSteps: 3, mutationPercent: 0),
            guidance: GuidanceConfig(maxCorpusInputs: 1, maxFeatures: 1), feedback: feedback)
        let h = try XCTUnwrap(r.guidance)
        XCTAssertEqual(h.uniqueFeatures, 1); XCTAssertEqual(h.droppedPrefixes, 1)
        XCTAssertEqual(h.corpus.map(\.inputs), [[1]]); XCTAssertEqual(h.corpus.first?.newFeatures, 0)
        XCTAssertEqual(h.corpus.first?.discoveredCase, 2); XCTAssertEqual(h.corpus.first?.discoveredTransition, 4)
        XCTAssertTrue(h.featureLimitReached); XCTAssertTrue(h.corpusLimitReached)
    }
    func testSinkDuplicateCapStickyFailureOwnErrorPrecedenceAndRevocation() throws {
        var escaped: FeatureBuffer<Int>?
        var feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, initialFeatures: { _, out in
            escaped = out; try out.push(1); try? out.push(1)
        }, transitionFeatures: { _, _, _, _ in })
        let limits = GuidanceConfig(maxFeaturesPerObservation: 1)
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), guidance: limits, feedback: feedback))
        XCTAssertThrowsError(try XCTUnwrap(escaped).push(2)) { XCTAssertTrue(String(describing: $0).contains("lease expired")) }
        feedback.initialFeatures = { _, out in
            escaped = out; try out.push(1); try? out.push(1); throw ModelError("callback owns error")
        }
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), guidance: limits, feedback: feedback)) {
            XCTAssertEqual(String(describing: $0), "initial feedback: callback owns error")
        }
        feedback.initialFeatures = { _, out in escaped = out; try out.push(1) }
        XCTAssertEqual(try guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 1, maxSteps: 0), guidance: limits, feedback: feedback).guidance?.uniqueFeatures, 1)
        XCTAssertThrowsError(try XCTUnwrap(escaped).push(2))
    }
    func testFeatureCloneErrorIsStickyButCallbackErrorWins() throws {
        var feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { _ in throw ModelError("clone failed") },
            initialFeatures: { _, out in try? out.push(1) }, transitionFeatures: { _, _, _, _ in })
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), feedback: feedback)) { XCTAssertTrue(String(describing: $0).contains("clone failed")) }
        feedback.initialFeatures = { _, out in try? out.push(1); throw ModelError("own error") }
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), feedback: feedback)) { XCTAssertEqual(String(describing: $0), "initial feedback: own error") }
    }
    func testCloneReentryIsStickyWithoutAppendingAndCallbackErrorWins() throws {
        for callbackThrows in [false, true] {
            var escaped: FeatureBuffer<Int>?, cloneCalls = 0
            var nestedErrors: [String] = []
            let feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { value in
                cloneCalls += 1
                do { try XCTUnwrap(escaped).push(value + 1) }
                catch { nestedErrors.append(String(describing: error)) }
                return value
            }, initialFeatures: { _, sink in
                escaped = sink
                // Ignoring both clone reentry and the outer push cannot admit a key.
                XCTAssertThrowsError(try sink.push(1)) {
                    XCTAssertEqual(String(describing: $0), "feature buffer push is reentrant")
                }
                XCTAssertThrowsError(try sink.push(2)) {
                    XCTAssertEqual(String(describing: $0), "feature buffer push is reentrant")
                }
                if callbackThrows { throw ModelError("callback owns reentry error") }
            }, transitionFeatures: { _, _, _, _ in XCTFail("failed initial observation must abort") })
            XCTAssertThrowsError(try guidedFuzz(guidedCounter(), guidance: GuidanceConfig(maxFeaturesPerObservation: 1), feedback: feedback)) {
                XCTAssertEqual(String(describing: $0), callbackThrows
                    ? "initial feedback: callback owns reentry error"
                    : "initial feedback: feature buffer push is reentrant")
            }
            XCTAssertEqual(cloneCalls, 1)
            XCTAssertEqual(nestedErrors, ["feature buffer push is reentrant"])
            XCTAssertThrowsError(try XCTUnwrap(escaped).push(3)) {
                XCTAssertEqual(String(describing: $0), "feature buffer lease expired")
            }
        }
    }
    func testExpiredSinkReleasesCloneCapturesOnSuccessAndError() throws {
        final class CloneCapture { var visits = 0 }
        for callbackThrows in [false, true] {
            weak var released: CloneCapture?
            var escaped: FeatureBuffer<Int>?
            func exercise() throws {
                let capture = CloneCapture()
                released = capture
                let feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { [capture] value in
                    capture.visits += 1; return value
                }, initialFeatures: { _, sink in
                    escaped = sink; try sink.push(1)
                    if callbackThrows { throw ModelError("callback failed") }
                }, transitionFeatures: { _, _, _, _ in })
                _ = try guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 1, maxSteps: 0), feedback: feedback)
                XCTAssertEqual(capture.visits, 1)
            }
            if callbackThrows { XCTAssertThrowsError(try exercise()) }
            else { try exercise() }
            XCTAssertNotNil(escaped)
            XCTAssertNil(released, "an escaped expired sink must not retain application clone captures")
            XCTAssertThrowsError(try XCTUnwrap(escaped).push(2))
        }
    }
    func testCollisionAndMutableReferenceKeysCopyAtEmission() throws {
        final class Key: Hashable {
            var value: Int
            init(_ value: Int) { self.value = value }
            static func == (a: Key, b: Key) -> Bool { a.value == b.value }
            func hash(into hasher: inout Hasher) { hasher.combine(0) }
        }
        let key = Key(0)
        let feedback = Feedback<Int, Int, Int, Key>(metadata: { guidedIdentity }, cloneFeature: { Key($0.value) },
            transitionFeatures: { _, _, t, out in
                key.value = t.state; try out.push(key); key.value = 999
            })
        let h = try XCTUnwrap(guidedFuzz(guidedCounter(), config: FuzzConfig(cases: 2, maxSteps: 3), feedback: feedback).guidance)
        XCTAssertEqual(h.uniqueFeatures, 3); XCTAssertEqual(h.corpus.map(\.newFeatures), [1,1,1])
        XCTAssertEqual(key.value, 999)
    }
    func testChecksObserversFailureAndControlPrecedeFeedback() throws {
        var events: [String] = [], cancelled = false
        var g = guidedCounter()
        g.model.checkState = { s in events.append("check\(s)"); return [.skipped("skip", "reason")] + (s == 2 ? [.failed("bad", "two")] : []) }
        g.model.step = { s, i in events.append("step\(s)"); if s == 1 { cancelled = true }; return Transition(state: s + i) }
        let feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 },
            initialFeatures: { s, out in events.append("feature\(s)"); try out.push(s) },
            transitionFeatures: { _, _, t, out in events.append("feature\(t.state)"); try out.push(t.state) })
        let observer = SearchObserver<Int, Int, Int>(initial: { s, _ in events.append("observe\(s)") }, transition: { _, _, t, _, _, _ in events.append("observe\(t.state)") })
        let r = try guidedFuzz(g, config: FuzzConfig(cases: 1, maxSteps: 3), feedback: feedback, limits: RunLimits(isCancelled: { cancelled }), observer: observer)
        XCTAssertEqual(r.termination, "failure_found"); XCTAssertEqual(r.transitions, 2); XCTAssertEqual(r.skippedChecks, 3)
        XCTAssertEqual(r.guidance?.uniqueFeatures, 2)
        XCTAssertEqual(events, ["check0","observe0","feature0","step0","check1","observe1","feature1","step1","check2","observe2"])
    }
    func testSuccessfulFeedbackLearnsBeforeCancellationAndOverrunWins() throws {
        var cancelled = false
        var feedback = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, transitionFeatures: { _, _, t, out in
            try out.push(t.state); cancelled = true
        })
        let r = try guidedFuzz(guidedCounter(), feedback: feedback, limits: RunLimits(isCancelled: { cancelled }))
        XCTAssertEqual(r.termination, "cancelled"); XCTAssertEqual(r.guidance?.corpus.map(\.inputs), [[1]])
        cancelled = false
        feedback.transitionFeatures = { _, _, _, out in try out.push(1); try? out.push(2); cancelled = true }
        XCTAssertThrowsError(try guidedFuzz(guidedCounter(), guidance: GuidanceConfig(maxFeaturesPerObservation: 1), feedback: feedback, limits: RunLimits(isCancelled: { cancelled }))) {
            XCTAssertTrue(String(describing: $0).contains("transition feedback: feedback exceeds"))
        }
    }
    func testPartialCheckerErrorsPreserveObserverBatchesAndSkipFeedback() throws {
        var g = guidedCounter(), events: [String] = [], feedbackCalls = 0
        g.model.checkStateInto = { s, out in if s > 0 { try out.push(.failed("state-fail", "bad")) } }
        g.model.checkTransitionInto = { _, _, _, out in try out.push(.passed("edge")); throw ModelError("checker own error") }
        let f = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, _ in feedbackCalls += 1 })
        let observer = SearchObserver<Int, Int, Int>(checkError: { phase, checks, complete in events.append("\(phase):\(checks.count):\(complete)") }, transition: { _, _, _, _, _, _ in XCTFail("no normal observer after checker error") })
        XCTAssertThrowsError(try guidedFuzz(g, feedback: f, observer: observer)) { XCTAssertTrue(String(describing: $0).contains("checker own error")) }
        XCTAssertEqual(events, ["state:1:true", "transition:1:false"]); XCTAssertEqual(feedbackCalls, 0)
    }
    func testBudgetEdgesAndExactStateUseHashableNotModelEquality() throws {
        var g = guidedCounter(); g.model.equalStates = { _, _ in true }; g.model.hashState = { _ in 0 }
        let f = exactStateFeedback(g.model)
        let r = try guidedFuzz(g, config: FuzzConfig(cases: 1, maxSteps: 2, maxTransitions: 2), feedback: f)
        XCTAssertEqual(r.termination, "cases_completed"); XCTAssertEqual(r.guidance?.uniqueFeatures, 3)
        XCTAssertEqual(r.guidance?.feedback, FeedbackMetadata(name: "exact-state", build: "clone-eq-hash-v1"))
        let zero = try guidedFuzz(g, config: FuzzConfig(cases: 3, maxSteps: 1, maxTransitions: 0), feedback: f)
        XCTAssertEqual(zero.cases, 1); XCTAssertEqual(zero.guidance?.uniqueFeatures, 1); XCTAssertEqual(zero.termination, "transition_limit")
        let next = try guidedFuzz(g, config: FuzzConfig(cases: 2, maxSteps: 2, maxTransitions: 2, mutationPercent: 100), feedback: f)
        XCTAssertEqual(next.cases, 2); XCTAssertEqual(next.guidance?.mutationCases, 1); XCTAssertEqual(next.termination, "transition_limit")
    }
    func testSkippedCheckSaturationDoesNotWrap() throws {
        var skipped = UInt64.max - 1
        _ = try searchFindings("state", [.skipped("a", ""), .skipped("b", "")], skipped: &skipped)
        XCTAssertEqual(skipped, UInt64.max)
    }
    func testReferenceSnapshotsIsolateFeedbackObserversCorpusAndFailure() throws {
        final class Box {
            var value: Int
            init(_ value: Int) { self.value = value }
        }
        let generatedInput = Box(1)
        let model = Model<Box, Box, Box>(initialState: { Box(0) }, step: { s, i in Transition(state: Box(s.value + i.value), outputs: [Box(i.value)]) },
            checkState: { $0.value == 3 ? [.failed("three", "limit")] : [] },
            cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) })
        let g = Generate(model: model, generate: { _, _ in generatedInput }, equalInputs: { $0.value == $1.value })
        var beforeValues: [Int] = []
        let f = Feedback<Box, Box, Box, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, transitionFeatures: { before, input, after, out in
            beforeValues.append(before.value); try out.push(after.state.value)
            before.value = 99; input.value = 99; after.state.value = 99; after.outputs[0].value = 99
        })
        let observer = SearchObserver<Box, Box, Box>(transition: { before, input, after, _, _, history in
            before.value = 88; input.value = 88; after.state.value = 88; after.outputs[0].value = 88
            history.forEach { $0.value = 88 }
        })
        let r = try guidedFuzz(g, config: FuzzConfig(cases: 1, maxSteps: 5), feedback: f, observer: observer)
        generatedInput.value = 77
        XCTAssertEqual(beforeValues, [0,1]); XCTAssertEqual(r.failure?.inputs.map(\.value), [1,1,1])
        let corpus = try XCTUnwrap(r.guidance).corpus
        XCTAssertEqual(corpus.map { $0.inputs.map(\.value) }, [[1],[1,1]])
        corpus[0].inputs[0].value = 66
        XCTAssertEqual(corpus[1].inputs.map(\.value), [1,1]); XCTAssertEqual(r.failure?.inputs.map(\.value), [1,1,1])
    }
    func testExactStateIncludesOracleHistory() throws {
        var g = guidedCounter(); g.model.step = { _, _ in Transition(state: 0) }
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "history") }, initialState: { 0 },
            advance: { history, _, _, _ in history + 1 }, checkState: { _, _ in [] }, cloneHistory: { $0 })
        let paired = g.withOracle(oracle)
        let exact = try guidedFuzz(paired, config: FuzzConfig(cases: 1, maxSteps: 3), feedback: exactStateFeedback(paired.model))
        let projected = StateFeedback<OracleState<Int, Int>, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, project: { $0.model })
        let compact = try guidedFuzz(paired, config: FuzzConfig(cases: 1, maxSteps: 3), feedback: projected)
        XCTAssertEqual(exact.guidance?.uniqueFeatures, 4); XCTAssertEqual(compact.guidance?.uniqueFeatures, 1)
        XCTAssertEqual(exact.transitions, compact.transitions)
    }
    func testThrowingObserverPreventsFeedbackAndRejectedTransitionStillLearns() throws {
        var g = guidedCounter(); g.model.step = { s, _ in Transition(state: s + 1, disposition: .rejected("still checked")) }
        let r = try guidedFuzz(g, config: FuzzConfig(cases: 1, maxSteps: 1), feedback: counterFeedback())
        XCTAssertEqual(r.transitions, 1); XCTAssertEqual(r.guidance?.uniqueFeatures, 2)
        var calls = 0
        let f = Feedback<Int, Int, Int, Int>(metadata: { guidedIdentity }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, _ in calls += 1 })
        let observer = SearchObserver<Int, Int, Int>(transition: { _, _, _, _, _, _ in throw ModelError("observer abort") })
        XCTAssertThrowsError(try guidedFuzz(g, feedback: f, observer: observer)) { XCTAssertTrue(String(describing: $0).contains("observer abort")) }
        XCTAssertEqual(calls, 0)
    }
}
