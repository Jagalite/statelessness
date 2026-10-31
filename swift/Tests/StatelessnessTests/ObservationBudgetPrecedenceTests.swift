import XCTest
import Statelessness

final class ObservationBudgetPrecedenceTests: XCTestCase {
    func testInputFrameLimitPrecedesOutputItems() throws {
        var stateChecks = 0, outputEncodes = 0, reducerCalls = 0
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { _, _ in
            reducerCalls += 1
            return Transition(state: 1, outputs: Array(repeating: 1, count: 64))
        }, checkState: { _ in stateChecks += 1; return [] },
        cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
        checkTransition: { _, _, _ in [] }, codec: Codec(
            metadata: { Metadata(name: "frame-order", build: "v1") },
            encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) },
            encodeInput: { _ in Array(repeating: 1, count: 512) },
            decodeInput: { Int($0[0]) },
            encodeOutput: { value in outputEncodes += 1; return [UInt8(value)] }))
        var limits = BinaryTraceLimits()
        limits.maxFrameBytes = 512; limits.maxItems = 64; limits.maxOutputsPerStep = 128
        let recorder = try Recorder(model: model, state: 0,
            config: RunConfig(strategy: "manual"),
            options: RecorderOptions(maxSteps: 1, limits: limits))
        let checkpoint = recorder.snapshot()
        let transition = try model.step(0, 1)
        XCTAssertThrowsError(try recorder.observe(model: model, before: 0, input: 1, transition: transition)) { error in
            XCTAssertTrue(String(describing: error).contains("trace exceeds frame bytes limit"))
        }
        XCTAssertEqual(reducerCalls, 1)
        XCTAssertEqual(stateChecks, 2)
        XCTAssertEqual(outputEncodes, 0)
        XCTAssertEqual(recorder.observedSteps, 0)
        XCTAssertEqual(recorder.retainedSteps, 0)
        XCTAssertTrue(recorder.isFrozen)
        XCTAssertEqual(recorder.snapshot().initialState, checkpoint.initialState)
        XCTAssertEqual(recorder.snapshot().initialChecks, checkpoint.initialChecks)
        XCTAssertEqual(recorder.snapshot().config, checkpoint.config)
        XCTAssertEqual(recorder.snapshot().metadata, checkpoint.metadata)
        XCTAssertTrue(recorder.snapshot().steps.isEmpty)
    }

    func testStepItemAdmissionPrecedesOversizedCheckText() throws {
        // Source-derived competing limits: STEP + 63 outputs + one check is 65
        // items. At 64 the item limit wins before the 65-byte property ID; at
        // 65 the ID is the next failure. Neither path encodes output payloads.
        for (maximum, expected) in [(UInt64(64), "aggregate items"), (UInt64(65), "string bytes")] {
            var stateChecks = 0, outputEncodes = 0, reducerCalls = 0
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { _, _ in
                reducerCalls += 1
                return Transition(state: 1, outputs: Array(repeating: 1, count: 63))
            }, checkState: { state in
                stateChecks += 1
                return state == 0 ? [] : [.passed(String(repeating: "p", count: 65))]
            }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { _, _, _ in [] }, codec: Codec(
                metadata: { Metadata(name: "step-item-order", build: "v1") },
                encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) },
                encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) },
                encodeOutput: { value in outputEncodes += 1; return [UInt8(value)] }))
            var limits = BinaryTraceLimits()
            limits.maxItems = maximum; limits.maxOutputsPerStep = 128
            limits.maxStringBytes = 64
            let recorder = try Recorder(model: model, state: 0,
                config: RunConfig(strategy: "manual"),
                options: RecorderOptions(maxSteps: 1, limits: limits))
            let transition = try model.step(0, 1)
            XCTAssertThrowsError(try recorder.observe(model: model, before: 0, input: 1, transition: transition)) { error in
                XCTAssertTrue(String(describing: error).contains("trace exceeds \(expected) limit"))
            }
            XCTAssertEqual(reducerCalls, 1)
            XCTAssertEqual(stateChecks, 2)
            XCTAssertEqual(outputEncodes, 0)
            XCTAssertEqual(recorder.observedSteps, 0)
            XCTAssertEqual(recorder.retainedSteps, 0)
            XCTAssertTrue(recorder.isFrozen)
        }
    }

    func testReplayUsesIsolatedCheckBatchBeforeCombinedStepItems() throws {
        struct Scenario {
            let name: String
            var outputs = 63
            var checks: [Check] = [.passed(String(repeating: "p", count: 65))]
            var disposition: Disposition = .accepted
            var items: UInt64 = 64
            var outputLimit: UInt64 = 128
            var checkLimit: UInt64 = 4096
            var frameLimit: UInt64 = 512
            var parameters: [AuditParameter] = []
            let category: String
        }
        let longText = String(repeating: "p", count: 65)
        let scenarios = [
            Scenario(name: "output cap before check text", outputs: 65, outputLimit: 64, category: "outputs per step"),
            Scenario(name: "check count before check text", checkLimit: 0, category: "checks per step"),
            Scenario(name: "isolated batch items before check text", outputs: 0,
                     checks: [.passed(longText), .passed(longText)], items: 1, category: "aggregate items"),
            Scenario(name: "R1 check text before STEP plus output items", category: "string bytes"),
            // Batch count (4) + ID prefix (4) + ID (56) = 64. Its status
            // byte must overflow a 64-byte frame before the 65-byte detail.
            Scenario(name: "batch status before detail", checks: [.failed(String(repeating: "p", count: 56), longText)],
                     frameLimit: 64, category: "frame bytes"),
            Scenario(name: "batch status fits then detail", checks: [.failed(String(repeating: "p", count: 56), longText)],
                     frameLimit: 65, category: "string bytes"),
            Scenario(name: "batch status before next ID", checks: [.passed(String(repeating: "p", count: 56)), .passed(longText)],
                     frameLimit: 64, category: "frame bytes"),
            Scenario(name: "batch status fits then next ID", checks: [.passed(String(repeating: "p", count: 56)), .passed(longText)],
                     frameLimit: 65, category: "string bytes"),
            Scenario(name: "combined items before reason", checks: [.passed("p")],
                     disposition: .rejected(longText), category: "aggregate items"),
            Scenario(name: "combined items fit then reason", checks: [.passed("p")],
                     disposition: .rejected(longText), items: 65, category: "string bytes"),
            Scenario(name: "reason before fixed frame", outputs: 9, checks: [],
                     disposition: .ignored(longText), frameLimit: 64, category: "string bytes"),
            // Fixed payload: 21 + one input byte + five reason bytes + 36
            // output-prefix bytes + four empty-check-count bytes = 67.
            Scenario(name: "fixed frame after reason", outputs: 9, checks: [],
                     disposition: .ignored("x"), frameLimit: 64, category: "frame bytes"),
            Scenario(name: "cumulative run plus step items", outputs: 1, checks: [], items: 2,
                     parameters: [AuditParameter("k", "v")], category: "aggregate items")
        ]
        for scenario in scenarios {
            var calls: [String] = [], reducerCalls = 0, stateChecks = 0, edgeChecks = 0, outputEncodes = 0
            let metadata = Metadata(name: "budget", build: "v1")
            let model = Model<Int, Int, Int>(initialState: {
                XCTFail("replay called initialState", file: #filePath, line: #line); return 0
            }, step: { before, input in
                calls.append("step"); reducerCalls += 1
                XCTAssertEqual(before, 0); XCTAssertEqual(input, 1)
                return Transition(state: 1, outputs: Array(repeating: 7, count: scenario.outputs), disposition: scenario.disposition)
            }, checkState: { state in
                calls.append("check-state:\(state)"); stateChecks += 1
                return state == 0 ? [] : scenario.checks
            }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { _, _, _ in calls.append("check-transition"); edgeChecks += 1; return [] },
            codec: Codec(metadata: { calls.append("metadata"); return metadata },
                encodeState: { value in calls.append("encode-state:\(value)"); return [UInt8(value)] },
                decodeState: { bytes in calls.append("decode-state"); return Int(bytes[0]) },
                encodeInput: { value in calls.append("encode-input"); return [UInt8(value)] },
                decodeInput: { bytes in calls.append("decode-input"); return Int(bytes[0]) },
                encodeOutput: { value in outputEncodes += 1; calls.append("encode-output"); return [UInt8(value)] }))
            var limits = BinaryTraceLimits()
            limits.maxItems = scenario.items; limits.maxOutputsPerStep = scenario.outputLimit
            limits.maxChecksPerStep = scenario.checkLimit; limits.maxFrameBytes = scenario.frameLimit
            limits.maxStringBytes = 64
            // Literal expected artifact, never generated by executing a model.
            // Its 47-byte RUN payload (57 with one parameter) and 27-byte STEP
            // payload fit the smallest frame cap. It has no recorded checks.
            let trace = AuditTrace(metadata: metadata,
                config: RunConfig(strategy: "x", parameters: scenario.parameters),
                initialState: [0], initialChecks: [],
                steps: [TraceStep(input: [1], disposition: .accepted, outputs: [], postState: [1], checks: [])],
                termination: .completed)
            func assertActual(_ actual: ReplayActual<Int, Int, Int>?) {
                guard case let .turn(sequence, before, input, turn, checks, count) = actual else {
                    return XCTFail("missing actual turn: \(scenario.name)")
                }
                XCTAssertEqual(sequence, 1); XCTAssertEqual(before, 0); XCTAssertEqual(input, 1)
                XCTAssertEqual(turn.state, 1); XCTAssertEqual(turn.outputs, Array(repeating: 7, count: scenario.outputs))
                XCTAssertEqual(turn.disposition, scenario.disposition)
                XCTAssertEqual(checks, scenario.checks); XCTAssertEqual(count, scenario.checks.count)
            }
            XCTAssertThrowsError(try replayWithObservationsBounded(model, trace: trace, limits: limits) { observation, report in
                XCTAssertEqual(report.outcome, "exact"); XCTAssertEqual(report.stepsVerified, 0)
                XCTAssertFalse(report.failureReproduced)
                switch observation {
                case let .initial(initial):
                    calls.append("observe-initial")
                    XCTAssertEqual(initial.state, 0); XCTAssertEqual(initial.checks, [])
                case let .error(sequence, actual, error, unavailable):
                    calls.append("observe-error")
                    XCTAssertEqual(sequence, 1); XCTAssertNil(unavailable)
                    XCTAssertEqual(error as? BinaryTraceError, .limitExceeded(scenario.category), scenario.name)
                    assertActual(actual)
                case .turn: XCTFail("preflight failure published a turn: \(scenario.name)")
                }
            }) { error in
                guard let error = error as? ReplayError<Int, Int, Int> else { return XCTFail("missing replay error") }
                XCTAssertEqual(error.error as? BinaryTraceError, .limitExceeded(scenario.category), scenario.name)
                XCTAssertNil(error.observerError); XCTAssertEqual(error.report.stepsVerified, 0)
                XCTAssertEqual(error.report.outcome, "exact"); XCTAssertFalse(error.report.failureReproduced)
                assertActual(error.actual)
            }
            XCTAssertEqual(reducerCalls, 1, scenario.name); XCTAssertEqual(stateChecks, 2, scenario.name)
            XCTAssertEqual(edgeChecks, 1, scenario.name); XCTAssertEqual(outputEncodes, 0, scenario.name)
            XCTAssertEqual(calls, ["metadata", "decode-state", "encode-state:0", "check-state:0", "observe-initial",
                                   "decode-input", "encode-input", "step", "check-state:1", "check-transition", "observe-error"], scenario.name)
        }
    }

    func testAuditRecordingAdmitsCumulativeItemsBeforeCheckText() throws {
        // Two stamped RUN parameters + STEP + 63 outputs + one check = 67.
        // A preceding empty-output STEP raises the cumulative boundary to 68.
        for (prefix, maximum, category) in [(false, UInt64(64), "aggregate items"), (false, UInt64(67), "string bytes"),
                                             (true, UInt64(67), "aggregate items"), (true, UInt64(68), "string bytes")] {
            var calls: [String] = [], outputEncodes = 0, reducerCalls = 0, stateChecks = 0, edgeChecks = 0
            let metadata = Metadata(name: "budget", build: "v1")
            let model = Model<Int, Int, Int>(initialState: { calls.append("initial"); return 0 }, step: { _, input in
                calls.append("step:\(input)"); reducerCalls += 1
                return Transition(state: input, outputs: input == 2 ? Array(repeating: 7, count: 63) : [])
            }, checkState: { state in
                calls.append("check-state:\(state)"); stateChecks += 1
                return state == 2 ? [.passed(String(repeating: "p", count: 65))] : []
            }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { _, input, _ in calls.append("check-transition:\(input)"); edgeChecks += 1; return [] },
            codec: Codec(metadata: { calls.append("metadata"); return metadata },
                encodeState: { value in calls.append("encode-state:\(value)"); return [UInt8(value)] },
                decodeState: { Int($0[0]) },
                encodeInput: { value in calls.append("encode-input:\(value)"); return [UInt8(value)] },
                decodeInput: { Int($0[0]) },
                encodeOutput: { value in outputEncodes += 1; calls.append("encode-output"); return [UInt8(value)] }))
            var limits = BinaryTraceLimits()
            limits.maxItems = maximum; limits.maxOutputsPerStep = 128; limits.maxStringBytes = 64
            let trace = try recordAudit(model, inputs: InputIterator(values: prefix ? [1, 2, 3] : [2, 3]),
                                        config: RunConfig(strategy: "x"), limits: limits)
            let expectedSteps = prefix ? [TraceStep(input: [1], disposition: .accepted, outputs: [], postState: [1], checks: [])] : []
            XCTAssertEqual(trace.steps, expectedSteps)
            XCTAssertEqual(trace.initialState, [0]); XCTAssertEqual(trace.initialChecks, [])
            XCTAssertEqual(trace.metadata, metadata)
            XCTAssertEqual(trace.config, RunConfig(strategy: "x", parameters: [
                AuditParameter("stateless.engine.version", statelessnessVersion),
                AuditParameter("stateless.engine.build", "native-swift-v1")]))
            XCTAssertEqual(trace.termination, .modelError("limitExceeded(\"\(category)\")"))
            XCTAssertEqual(outputEncodes, 0); XCTAssertEqual(reducerCalls, prefix ? 2 : 1)
            XCTAssertEqual(stateChecks, prefix ? 3 : 2); XCTAssertEqual(edgeChecks, prefix ? 2 : 1)
            var expectedCalls = ["initial", "check-state:0", "encode-state:0", "metadata"]
            if prefix { expectedCalls += ["encode-input:1", "step:1", "check-state:1", "check-transition:1", "encode-state:1"] }
            expectedCalls += ["encode-input:2", "step:2", "check-state:2", "check-transition:2"]
            XCTAssertEqual(calls, expectedCalls)
        }
    }
}
