import XCTest
import Statelessness

private final class SessionBox { var value: Int; init(_ value: Int) { self.value = value } }

private struct SessionFailure: Error {}

/// Counter model: input is added to the state; a negative input is rejected
/// but still counts as a turn (state.rejections increases).
private struct Counter: Equatable { var total = 0; var rejections = 0 }

private func counterModel(steps: SessionBox, failAbove: Int = .max, throwOn: Int? = nil,
                          checkThrowsOn: Int? = nil) -> Model<Counter, Int, String> {
    Model(initialState: { Counter() },
          step: { state, input in
              steps.value += 1
              if input == throwOn { throw SessionFailure() }
              var next = state
              if input < 0 { next.rejections += 1; return Transition(state: next, outputs: [], disposition: .rejected("negative")) }
              next.total += input
              return Transition(state: next, outputs: ["added \(input)"])
          },
          checkState: { state in [state.total > failAbove ? Check("total.bounded", .failed, "\(state.total)") : Check("total.bounded")] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          checkTransition: { _, input, _ in
              if input == checkThrowsOn { throw SessionFailure() }
              return [Check("transition.seen")]
          })
}

final class ExecutionSessionTests: XCTestCase {
    func testOneInputInvokesStepOnceAndCommitsStateAndSequence() throws {
        let steps = SessionBox(0)
        let session = try ExecutionSession(model: counterModel(steps: steps))
        XCTAssertEqual(session.sequence, 0)
        XCTAssertEqual(session.initialChecks.map(\.id), ["total.bounded"])
        let turn = try session.submit(3)
        XCTAssertEqual(steps.value, 1, "checks and recording never rerun the step")
        XCTAssertEqual(turn.sequence, 1)
        XCTAssertEqual(turn.outputs, ["added 3"])
        XCTAssertEqual(turn.disposition, .accepted)
        XCTAssertEqual(turn.checks.map(\.id), ["total.bounded", "transition.seen"])
        XCTAssertEqual(try session.snapshot(), Counter(total: 3))
        XCTAssertEqual(session.sequence, 1)
    }

    func testThrowingStepCommitsNothingAndExposesNoOutputs() throws {
        let steps = SessionBox(0)
        let session = try ExecutionSession(model: counterModel(steps: steps, throwOn: 7))
        try session.submit(2)
        XCTAssertThrowsError(try session.submit(7)) { error in
            guard case .model = error as? ExecutionFault else { return XCTFail("\(error)") }
        }
        XCTAssertEqual(try session.snapshot(), Counter(total: 2))
        XCTAssertEqual(session.sequence, 1)
        XCTAssertEqual(try session.submit(1).sequence, 2, "the failed attempt consumed no sequence")
    }

    func testThrowingCheckCommitsNothing() throws {
        let session = try ExecutionSession(model: counterModel(steps: SessionBox(0), checkThrowsOn: 5))
        XCTAssertThrowsError(try session.submit(5))
        XCTAssertEqual(try session.snapshot(), Counter())
        XCTAssertEqual(session.sequence, 0)
    }

    func testThrowingCloneCommitsNothing() throws {
        let fail = SessionBox(0)
        var model = counterModel(steps: SessionBox(0))
        model.cloneInput = { input in if fail.value == 1 { throw SessionFailure() }; return input }
        let session = try ExecutionSession(model: model)
        fail.value = 1
        XCTAssertThrowsError(try session.submit(4))
        XCTAssertEqual(try session.snapshot(), Counter())
        XCTAssertEqual(session.sequence, 0)
    }

    func testRejectedInputCommitsItsReturnedState() throws {
        let session = try ExecutionSession(model: counterModel(steps: SessionBox(0)))
        let turn = try session.submit(-1)
        XCTAssertEqual(turn.disposition, .rejected("negative"))
        XCTAssertEqual(try session.snapshot(), Counter(total: 0, rejections: 1), "rejection is not rollback")
        XCTAssertEqual(session.sequence, 1)
    }

    func testFailedChecksAreACommittedTurnNotAFault() throws {
        let session = try ExecutionSession(model: counterModel(steps: SessionBox(0), failAbove: 5))
        let turn = try session.submit(9)
        XCTAssertEqual(turn.failedChecks.map(\.id), ["total.bounded"])
        XCTAssertEqual(try session.snapshot().total, 9)
        XCTAssertEqual(session.sequence, 1)
    }

    func testSequenceRestoreAndExhaustion() throws {
        let steps = SessionBox(0)
        let session = try ExecutionSession(model: counterModel(steps: steps), checkpoint: Counter(total: 4), sequence: UInt64.max - 1)
        XCTAssertEqual(try session.submit(1).sequence, UInt64.max)
        XCTAssertThrowsError(try session.submit(1)) { XCTAssertEqual($0 as? ExecutionFault, .sequenceExhausted) }
        XCTAssertEqual(steps.value, 1, "an exhausted session never invokes the step")
        XCTAssertEqual(try session.snapshot().total, 5)
        XCTAssertEqual(session.sequence, UInt64.max, "no silent wrap or repeated terminal sequence")
    }

    func testRecursiveSubmissionIsRejectedWithoutDisturbingTheOuterTurn() throws {
        let holder = SessionBox(0)
        var inner: ExecutionFault?
        var session: ExecutionSession<Counter, Int, String>!
        let model = Model<Counter, Int, String>(
            initialState: { Counter() },
            step: { state, input in
                holder.value += 1
                do { try session.submit(100) } catch { inner = error as? ExecutionFault }
                do { _ = try session.read { $0 } } catch { XCTAssertEqual(error as? ExecutionFault, .reentrantSubmission) }
                return Transition(state: Counter(total: state.total + input))
            },
            checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        session = try ExecutionSession(model: model)
        try session.submit(2)
        XCTAssertEqual(inner, .reentrantSubmission)
        XCTAssertEqual(holder.value, 1)
        XCTAssertEqual(try session.snapshot(), Counter(total: 2))
        XCTAssertEqual(session.sequence, 1)
    }

    func testMutableReferencesAreIsolatedFromCommittedStateAndOutputs() throws {
        let model = Model<SessionBox, Int, SessionBox>(
            initialState: { SessionBox(0) },
            step: { state, input in state.value += input; return Transition(state: state, outputs: [SessionBox(state.value)]) },
            checkState: { _ in [] },
            cloneState: { SessionBox($0.value) }, cloneInput: { $0 }, cloneOutput: { SessionBox($0.value) })
        let certifying = try ExecutionSession(model: model, certifiesTurns: true)
        let certified = try certifying.submit(5)
        XCTAssertEqual(try certified.checked?.before.value, 0, "an in-place mutating step cannot alter the certified before state")
        XCTAssertEqual(try certified.checked?.transition.state.value, 5)
        certified.outputs[0].value = 999
        XCTAssertEqual(try certifying.snapshot().value, 5)
        let session = try ExecutionSession(model: model)
        let turn = try session.submit(5)
        XCTAssertNil(turn.checked)
        turn.outputs[0].value = 999
        let snapshot = try session.snapshot()
        snapshot.value = -1
        XCTAssertEqual(try session.snapshot().value, 5, "neither outputs nor snapshots alias committed state")
        XCTAssertEqual(try session.read { $0.value }, 5)
        XCTAssertEqual(try session.submit(1).outputs[0].value, 6)
    }

    func testCommittedOutputsAreReturnedOncePerSubmission() throws {
        let session = try ExecutionSession(model: counterModel(steps: SessionBox(0)))
        let first = try session.submit(1)
        let second = try session.submit(2)
        XCTAssertEqual(first.outputs, ["added 1"])
        XCTAssertEqual(second.outputs, ["added 2"], "earlier outputs are never re-delivered")
    }

    func testRecorderFailureAfterCommitIsDiagnosticOnly() throws {
        let steps = SessionBox(0)
        var model = counterModel(steps: steps)
        model.codec = Codec(metadata: { Metadata(name: "counter") },
                            encodeState: { Array("\($0.total),\($0.rejections)".utf8) },
                            decodeState: { bytes in
                                let parts = String(decoding: bytes, as: UTF8.self).split(separator: ",").map { Int($0)! }
                                return Counter(total: parts[0], rejections: parts[1])
                            },
                            encodeInput: { Array(String($0).utf8) },
                            decodeInput: { Int(String(decoding: $0, as: UTF8.self))! },
                            encodeOutput: { output in
                                if output == "added 3" { throw SessionFailure() }
                                return Array(output.utf8)
                            })
        let recorder = try Recorder(checkedInitial: checkInitial(model, state: Counter()), config: RunConfig(strategy: "session"))
        let session = try ExecutionSession(model: model, recorder: recorder)
        let turn = try session.submit(3)
        XCTAssertNotNil(turn.recorderFault, "a failing recorder reports instead of rolling back")
        XCTAssertEqual(turn.outputs, ["added 3"], "committed outputs are still handed off once")
        XCTAssertEqual(try session.snapshot().total, 3)
        XCTAssertEqual(session.sequence, 1)
        XCTAssertEqual(steps.value, 1, "the effect-free recorder never replays the step")
        XCTAssertEqual(try session.submit(1).sequence, 2)
        XCTAssertEqual(try session.snapshot().total, 4)
    }

    func testRecorderObservesCommittedTurnsInOrder() throws {
        var model = counterModel(steps: SessionBox(0))
        model.codec = Codec(metadata: { Metadata(name: "counter") },
                            encodeState: { Array("\($0.total),\($0.rejections)".utf8) },
                            decodeState: { bytes in
                                let parts = String(decoding: bytes, as: UTF8.self).split(separator: ",").map { Int($0)! }
                                return Counter(total: parts[0], rejections: parts[1])
                            },
                            encodeInput: { Array(String($0).utf8) },
                            decodeInput: { Int(String(decoding: $0, as: UTF8.self))! },
                            encodeOutput: { Array($0.utf8) })
        let recorder = try Recorder(checkedInitial: checkInitial(model, state: Counter()), config: RunConfig(strategy: "session"))
        let session = try ExecutionSession(model: model, recorder: recorder)
        for value in [1, -1, 2] { XCTAssertNil(try session.submit(value).recorderFault) }
        XCTAssertEqual(recorder.observedSteps, 3)
    }

    func testInvalidPolicyIsAConfigurationError() {
        XCTAssertThrowsError(try ExecutionSession(model: counterModel(steps: SessionBox(0)), policy: CheckPolicy(stateEvery: 0)))
    }

    // MARK: Both modes (certified and uncertified) share one contract.

    private func modes<S, I, O>(_ model: @autoclosure () -> Model<S, I, O>, _ body: (ExecutionSession<S, I, O>, String) throws -> Void) throws {
        for certified in [false, true] {
            try body(try ExecutionSession(model: model(), certifiesTurns: certified), certified ? "certified" : "uncertified")
        }
    }

    func testChecksSeeThePristineInputEvenWhenTheStepConsumesIt() throws {
        // The step consumes a reference input (amount -> 0); a conservation
        // check must still see the submitted amount in both modes.
        let model = Model<Counter, SessionBox, String>(
            initialState: { Counter() },
            step: { state, input in
                var next = state; next.total += input.value; input.value = 0
                return Transition(state: next)
            },
            checkState: { _ in [] },
            cloneState: { $0 }, cloneInput: { SessionBox($0.value) }, cloneOutput: { $0 },
            checkTransition: { before, input, t in
                [t.state.total == before.total + input.value ? Check("conserved") : Check("conserved", .failed, "\(input.value)")]
            })
        try modes(model) { session, mode in
            let submitted = SessionBox(5)
            let turn = try session.submit(submitted)
            XCTAssertEqual(turn.failedChecks.map(\.id), [], mode)
            XCTAssertEqual(submitted.value, 5, "the caller's input is never mutated (\(mode))")
        }
    }

    func testCommittedStateIsDetachedFromReferencesTheStepRetains() throws {
        // The model keeps the state object it returns in its own cache and
        // mutates it later; committed state, outputs and certificates must not change.
        let cache = SessionBox(0)
        var retained: [SessionBox] = []
        let model = Model<SessionBox, Int, SessionBox>(
            initialState: { SessionBox(0) },
            step: { state, input in
                let next = SessionBox(state.value + input); retained.append(next); cache.value += 1
                return Transition(state: next, outputs: [next])
            },
            checkState: { _ in [] },
            cloneState: { SessionBox($0.value) }, cloneInput: { $0 }, cloneOutput: { SessionBox($0.value) })
        try modes(model) { session, mode in
            retained.removeAll()
            let turn = try session.submit(3)
            for box in retained { box.value = -100 }
            XCTAssertEqual(try session.snapshot().value, 3, mode)
            XCTAssertEqual(try session.read { $0.value }, 3, mode)
            XCTAssertEqual(turn.outputs[0].value, 3, mode)
            if let checked = turn.checked { XCTAssertEqual(try checked.transition.state.value, 3, mode) }
        }
    }

    func testSnapshotIsExclusive() throws {
        var session: ExecutionSession<Counter, Int, String>!
        var fromClone: ExecutionFault?
        var fromStep: ExecutionFault?
        var cloning = false
        let model = Model<Counter, Int, String>(
            initialState: { Counter() },
            step: { state, input in
                do { _ = try session.snapshot() } catch { fromStep = error as? ExecutionFault }
                return Transition(state: Counter(total: state.total + input))
            },
            checkState: { _ in [] },
            cloneState: { state in
                if cloning { cloning = false; do { try session.submit(9) } catch { fromClone = error as? ExecutionFault } }
                return state
            },
            cloneInput: { $0 }, cloneOutput: { $0 })
        session = try ExecutionSession(model: model)
        try session.submit(1)
        XCTAssertEqual(fromStep, .reentrantSubmission, "no snapshot during a turn")
        cloning = true
        XCTAssertEqual(try session.snapshot(), Counter(total: 1))
        XCTAssertEqual(fromClone, .reentrantSubmission, "a clone callback cannot submit during a snapshot")
        XCTAssertEqual(session.sequence, 1)
    }

    func testFaultMatrixCommitsNothingInEitherMode() throws {
        enum Stage: CaseIterable { case step, check, inputClone, stateClone, outputClone }
        for stage in Stage.allCases {
            let armed = SessionBox(0)
            var model = counterModel(steps: SessionBox(0), throwOn: stage == .step ? 7 : nil, checkThrowsOn: stage == .check ? 7 : nil)
            switch stage {
            case .inputClone: model.cloneInput = { input in if armed.value == 1 && input == 7 { throw SessionFailure() }; return input }
            case .stateClone: model.cloneState = { state in if armed.value == 1 { throw SessionFailure() }; return state }
            case .outputClone: model.cloneOutput = { output in if output == "added 7" { throw SessionFailure() }; return output }
            case .step, .check: break
            }
            try modes(model) { session, mode in
                try session.submit(2)
                armed.value = 1
                XCTAssertThrowsError(try session.submit(7), "\(stage) \(mode)") { error in
                    guard case .model = error as? ExecutionFault else { return XCTFail("\(stage) \(mode): \(error)") }
                }
                armed.value = 0
                XCTAssertEqual(try session.snapshot(), Counter(total: 2), "\(stage) \(mode)")
                XCTAssertEqual(session.sequence, 1, "\(stage) \(mode)")
            }
        }
    }

    func testRecorderCapacityNeverRollsBackCommittedTurns() throws {
        var model = counterModel(steps: SessionBox(0))
        model.codec = Codec(metadata: { Metadata(name: "counter") },
                            encodeState: { Array("\($0.total),\($0.rejections)".utf8) },
                            decodeState: { bytes in
                                let parts = String(decoding: bytes, as: UTF8.self).split(separator: ",").map { Int($0)! }
                                return Counter(total: parts[0], rejections: parts[1])
                            },
                            encodeInput: { Array(String($0).utf8) },
                            decodeInput: { Int(String(decoding: $0, as: UTF8.self))! },
                            encodeOutput: { Array($0.utf8) })
        let recorder = try Recorder(checkedInitial: checkInitial(model, state: Counter()), config: RunConfig(strategy: "session"),
                                    options: RecorderOptions(maxSteps: 1))
        let session = try ExecutionSession(model: model, recorder: recorder)
        for value in 1...4 { _ = try session.submit(value) }
        XCTAssertEqual(session.sequence, 4)
        XCTAssertEqual(try session.snapshot().total, 10, "every turn stays committed whatever the recorder retains")
        XCTAssertLessThanOrEqual(recorder.retainedSteps, 1)
    }
}
