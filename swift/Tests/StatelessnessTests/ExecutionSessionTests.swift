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
        let session = try ExecutionSession(model: model)
        let turn = try session.submit(5)
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
}
