import XCTest
@testable import Statelessness

private final class EnabledInputBox {
    var value: Int
    init(_ value: Int) { self.value = value }
}

private final class FuzzStateBox {
    var value: Int
    init(_ value: Int) { self.value = value }
}

final class FuzzEnabledSnapshotTests: XCTestCase {
    func testFailedSuccessorSnapshotDoesNotCheckMutatedLiveState() throws {
        var returned: FuzzStateBox?
        var live: FuzzStateBox?
        var checked: [Int] = []
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { FuzzStateBox(0) },
            step: { _, _ in let next = FuzzStateBox(7); returned = next; return Transition(state: next) },
            checkState: { state in checked.append(state.value); return [] },
            cloneState: { state in
                if state === live { throw ModelError("successor copy failed") }
                let copy = FuzzStateBox(state.value)
                if state === returned { live = copy }
                return copy
            }, cloneInput: { value in live?.value = 99; return value }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        XCTAssertThrowsError(try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1)))
        XCTAssertFalse(checked.contains(99))
    }

    func testHistoryCopyCannotChangeTransitionCheckInput() throws {
        var armed = false
        var liveInput: EnabledInputBox?
        var observed: Int?
        let model = Model<Int, EnabledInputBox, Int>(
            initialState: { 0 }, step: { _, _ in armed = true; return Transition(state: 1) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { value in
                if armed { liveInput?.value = 99 } else { liveInput = value }
                return EnabledInputBox(value.value)
            }, cloneOutput: { $0 },
            checkTransition: { _, input, _ in observed = input.value; return [] })
        let generator = Generate(model: model, generate: { _, _ in EnabledInputBox(7) },
            isEnabled: { _, _ in true }, equalInputs: { $0.value == $1.value })
        _ = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1))
        XCTAssertEqual(observed, 7)
    }
    func testHistoryCopyCannotChangeTransitionChecks() throws {
        var produced: EnabledInputBox?
        var observed: Int?
        let model = Model<Int, Int, EnabledInputBox>(
            initialState: { 0 },
            step: { _, _ in let value = EnabledInputBox(7); produced = value; return Transition(state: 1, outputs: [value]) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { value in produced?.value = 99; return value },
            cloneOutput: { EnabledInputBox($0.value) },
            checkTransition: { _, _, transition in observed = transition.outputs.first?.value; return [] })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        _ = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1))
        XCTAssertEqual(observed, 7)
    }
    func testFuzzRetainsExecutionStateAcrossAdmission() throws {
        let initial = FuzzStateBox(3)
        var stateAlias: FuzzStateBox?
        var stepped: Int?
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { initial },
            step: { state, _ in stepped = state.value; return Transition(state: state) },
            checkState: { _ in [] },
            cloneState: { state in
                let copy = FuzzStateBox(state.value)
                if state === initial { stateAlias = copy }
                return copy
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in stateAlias?.value = 99; return true }, equalInputs: { $0 == $1 })
        let report = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1))
        XCTAssertEqual(report.transitions, 1)
        XCTAssertEqual(stepped, 3)
    }
    func testFuzzCapturesSuccessorBeforeCheckMutatesExternalAlias() throws {
        var returned: FuzzStateBox?
        var transitionAlias: FuzzStateBox?
        var seen: [Int] = []
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { FuzzStateBox(0) },
            step: { state, _ in
                seen.append(state.value)
                let next = FuzzStateBox(state.value + 1)
                returned = next
                return Transition(state: next)
            },
            checkState: { state in
                if state.value == 1 { transitionAlias?.value = 99 }
                return []
            },
            cloneState: { state in
                let copy = FuzzStateBox(state.value)
                if state === returned { transitionAlias = copy }
                return copy
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let report = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 2, maxTransitions: 2))
        XCTAssertEqual(report.transitions, 2)
        XCTAssertEqual(seen, [0, 1])
    }

    // Edge snapshots are taken once and only for consumers that use them.
    func testFuzzEdgeSnapshotsAreLazyAndTakenOncePerStep() throws {
        func outputCopies(observer: SearchObserver<Int,Int,EnabledInputBox>?,edgeChecks: Bool) throws -> Int {
            var copies = 0
            let edge: ((Int,Int,Transition<Int,EnabledInputBox>) throws -> [Check])? = edgeChecks ? { _, _, _ in [] } : nil
            let model = Model<Int,Int,EnabledInputBox>(
                initialState: { 0 }, step: { _, _ in Transition(state: 1, outputs: [EnabledInputBox(7)]) },
                checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 },
                cloneOutput: { copies += 1; return EnabledInputBox($0.value) },
                checkTransition: edge)
            let generator = Generate(model: model, generate: { _, _ in 1 }, isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
            _ = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1), observer: observer)
            return copies
        }
        let observer = SearchObserver<Int,Int,EnabledInputBox>(transition: { _, _, turn, _, _, _ in turn.outputs[0].value = 99 })
        XCTAssertEqual(try outputCopies(observer: nil, edgeChecks: false), 1)
        XCTAssertEqual(try outputCopies(observer: observer, edgeChecks: false), 2)
        // Transition checks receive their own transition copy in addition to the
        // pre-check edge snapshot; an observer then gets one more detached copy.
        XCTAssertEqual(try outputCopies(observer: nil, edgeChecks: true), 3)
        XCTAssertEqual(try outputCopies(observer: observer, edgeChecks: true), 4)
    }
    func testObserverHistoryIsDetachedFromFailureEvidence() throws {
        let model = Model<Int,EnabledInputBox,Int>(
            initialState: { 0 }, step: { s, _ in Transition(state: s + 1) },
            checkState: { s in s >= 2 ? [.failed("bad", "target")] : [] },
            cloneState: { $0 }, cloneInput: { EnabledInputBox($0.value) }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in EnabledInputBox(7) }, isEnabled: { _, _ in true }, equalInputs: { $0.value == $1.value })
        var observedPrefixes: [[Int]] = []
        let observer = SearchObserver<Int,EnabledInputBox,Int>(transition: { _, input, _, _, _, history in
            observedPrefixes.append(history.map(\.value)); input.value = 50; for item in history { item.value = 99 }
        })
        let report = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 3, maxTransitions: 3), observer: observer)
        XCTAssertEqual(observedPrefixes, [[7], [7, 7]])
        XCTAssertEqual(report.failure?.inputs.map(\.value), [7, 7])
    }

    func testFuzzDoesNotSurfaceUnusedFinalSuccessorCopyFailure() throws {
        var successorCopies = 0
        let model = Model<Int, Int, Int>(
            initialState: { 0 }, step: { _, _ in Transition(state: 1) },
            checkState: { _ in [] },
            cloneState: { state in
                if state == 1 {
                    successorCopies += 1
                    if successorCopies == 2 { throw ModelError("unused successor") }
                }
                return state
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let report = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1))
        XCTAssertEqual(report.transitions, 1)
        XCTAssertGreaterThanOrEqual(successorCopies, 2)
    }

    func testFuzzFeedbackCapturesTransitionBeforeChecks() throws {
        var produced: EnabledInputBox?
        var transitionAlias: EnabledInputBox?
        var observed: Int?
        let model = Model<Int, Int, EnabledInputBox>(
            initialState: { 0 },
            step: { _, _ in
                let output = EnabledInputBox(7)
                produced = output
                return Transition(state: 1, outputs: [output])
            },
            checkState: { state in
                if state == 1 { transitionAlias?.value = 99 }
                return []
            },
            cloneState: { $0 }, cloneInput: { $0 },
            cloneOutput: { value in
                let copy = EnabledInputBox(value.value)
                if value === produced { transitionAlias = copy }
                return copy
            })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let config = FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1)
        _ = try fuzzKernel(generator, config: config, control: RunControl(RunLimits()),
            observer: nil, candidate: { _, _ in ([], nil) },
            transitionFeedback: { _, _, transition, _, _, _ in observed = transition.outputs.first?.value })
        XCTAssertEqual(observed, 7)
    }

    func testFuzzFeedbackCapturesStateBeforeEnabledCallback() throws {
        let initial = FuzzStateBox(3)
        var stateAlias: FuzzStateBox?
        var observed: Int?
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { initial }, step: { state, _ in Transition(state: state) },
            checkState: { _ in [] },
            cloneState: { state in
                let copy = FuzzStateBox(state.value)
                if state === initial { stateAlias = copy }
                return copy
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in stateAlias?.value = 99; return true }, equalInputs: { $0 == $1 })
        let config = FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1)
        _ = try fuzzKernel(generator, config: config, control: RunControl(RunLimits()),
            observer: nil, candidate: { _, _ in ([], nil) },
            transitionFeedback: { state, _, _, _, _, _ in observed = state.value })
        XCTAssertEqual(observed, 3)
    }

    func testFuzzRetainsInitialStateAcrossInitialCheck() throws {
        let initial = FuzzStateBox(3)
        var stateAlias: FuzzStateBox?
        var stepped: Int?
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { initial },
            step: { state, _ in stepped = state.value; return Transition(state: state) },
            checkState: { _ in stateAlias?.value = 99; return [] },
            cloneState: { state in
                let copy = FuzzStateBox(state.value)
                if state === initial { stateAlias = copy }
                return copy
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        _ = try fuzz64(generator, config: FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1))
        XCTAssertEqual(stepped, 3)
    }

    func testFuzzRetainsInitialStateAcrossFeedback() throws {
        var stepped: Int?
        let model = Model<FuzzStateBox, Int, Int>(
            initialState: { FuzzStateBox(3) },
            step: { state, _ in stepped = state.value; return Transition(state: state) },
            checkState: { _ in [] }, cloneState: { FuzzStateBox($0.value) },
            cloneInput: { $0 }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in 1 },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let config = FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1)
        _ = try fuzzKernel(generator, config: config, control: RunControl(RunLimits()),
            observer: nil, candidate: { _, _ in ([], nil) },
            initialFeedback: { state, _ in state.value = 99 })
        XCTAssertEqual(stepped, 3)
    }
    func testReusedCandidateIsCapturedBeforeStateCloneAndUsedForStep() throws {
        let candidate = EnabledInputBox(7)
        var armed = false
        var checked: [Int] = []
        var stepped: [Int] = []
        let model = Model<Int, EnabledInputBox, Int>(
            initialState: { 0 },
            step: { state, input in stepped.append(input.value); return Transition(state: state) },
            checkState: { _ in [] },
            cloneState: { state in if armed { candidate.value = 99 }; return state },
            cloneInput: { EnabledInputBox($0.value) }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in nil },
            isEnabled: { _, input in checked.append(input.value); return true },
            equalInputs: { $0.value == $1.value })
        let config = FuzzConfig64(cases: 1, maxSteps: 1, maxTransitions: 1)
        let report = try fuzzKernel(generator, config: config, control: RunControl(RunLimits()),
            observer: nil, candidate: { _, _ in armed = true; return ([candidate], nil) })
        XCTAssertEqual(report.transitions, 1)
        XCTAssertEqual(checked, [7])
        XCTAssertEqual(stepped, [7])
    }

    func testShrinkUsesCapturedInputForEnabledStepChecksAndWitness() throws {
        var saved: EnabledInputBox?
        var copies = 0
        var armed = false
        var checked: [Int] = []
        var stepped: [Int] = []
        var transitionInputs: [Int] = []
        let model = Model<Int, EnabledInputBox, Int>(
            initialState: { 0 },
            step: { _, input in stepped.append(input.value); return Transition(state: 1) },
            checkState: { state in state == 1 ? [.failed("target", "failure")] : [] },
            cloneState: { state in if armed { saved?.value = 99 }; return state },
            cloneInput: { input in
                copies += 1
                let result = EnabledInputBox(input.value)
                if copies == 1 { saved = result }
                if copies == 2 { armed = true }
                return result
            }, cloneOutput: { $0 },
            checkTransition: { _, input, _ in transitionInputs.append(input.value); return [] })
        let generator = Generate(model: model, generate: { _, _ in nil },
            isEnabled: { _, input in checked.append(input.value); return input.value == 7 },
            equalInputs: { $0.value == $1.value })
        let original = Failure(inputs: [EnabledInputBox(7)],
            violations: [Violation(phase: "state", check: .failed("target", "failure"))])
        let report = try shrink64(generator, original: original, config: ShrinkConfig64(maxAttempts: 1))
        XCTAssertTrue(report.validatedOriginal)
        XCTAssertEqual(checked.first, 7)
        XCTAssertEqual(stepped.first, 7)
        XCTAssertEqual(transitionInputs.first, 7)
        XCTAssertEqual(report.minimized.inputs.first?.value, 7)
    }

    func testShrinkDefersUnusedWitnessCopyFailureOnPassingReplay() throws {
        var copies = 0
        let model = Model<Int, Int, Int>(
            initialState: { 0 }, step: { state, _ in Transition(state: state) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { input in
                copies += 1
                if copies == 5 { throw ModelError("unused witness copy") }
                return input
            }, cloneOutput: { $0 })
        let generator = Generate(model: model, generate: { _, _ in nil },
            isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let original = Failure(inputs: [7],
            violations: [Violation(phase: "state", check: .failed("target", "failure"))])
        XCTAssertThrowsError(try shrink64(generator, original: original, config: ShrinkConfig64(maxAttempts: 1))) { error in
            XCTAssertTrue(String(describing: error).contains("does not reproduce"), "\(error)")
        }
        XCTAssertGreaterThanOrEqual(copies, 5)
    }
}
