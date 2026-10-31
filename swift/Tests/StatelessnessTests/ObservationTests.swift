import XCTest
import Statelessness

private final class ObservationCalls {
    var payloads: [String] = []
    var forbidden = 0
    var fault = ""
    var retained: ObservationBuffer?
    var encodedLease: EncodeBuffer?
    var formatterCallback: ((String, ObservationBuffer) throws -> Void)?
}
private struct DiagnosticFormatter: PayloadFormat {
    typealias State = UInt8
    typealias Input = UInt8
    typealias Output = UInt8
    let calls: ObservationCalls
    func render(_ kind: String, _ value: UInt8, _ out: ObservationBuffer) throws {
        calls.payloads.append("\(kind):\(value)"); calls.retained = out
        if calls.fault == "swallow" { try? out.write([UInt8](repeating: 120, count: 2048)); return }
        try out.write("v\(value)")
        try calls.formatterCallback?(kind, out)
        if calls.fault == kind { throw ModelError("hostile\nformatter") }
    }
    func state(_ model: Model<UInt8,UInt8,UInt8>, _ state: UInt8, into out: ObservationBuffer) throws { try render("state", state, out) }
    func input(_ model: Model<UInt8,UInt8,UInt8>, _ input: UInt8, into out: ObservationBuffer) throws { try render("input", input, out) }
    func output(_ model: Model<UInt8,UInt8,UInt8>, _ output: UInt8, into out: ObservationBuffer) throws { try render("output", output, out) }
}
private final class DiagnosticWriter: ObservationWriter {
    var bytes: [UInt8] = []
    var calls = 0
    var flushes = 0
    var failAfter: Int?
    var failFlush = false
    var short = false
    var invalidCount: Int?
    var onWrite: (() -> Void)?
    var thrown: (any Error)?
    func write(_ bytes: ArraySlice<UInt8>) throws -> Int {
        calls += 1; onWrite?()
        if let thrown { throw thrown }
        if let invalidCount { return invalidCount }
        if short && calls % 3 == 1 { throw ObservationWriteError.interrupted }
        if let failAfter, self.bytes.count >= failAfter { throw ModelError("write failed") }
        let remaining = failAfter.map { $0 - self.bytes.count } ?? bytes.count
        let count = min(min(bytes.count, remaining), short ? 3 : bytes.count)
        self.bytes.append(contentsOf: bytes.prefix(count)); return count
    }
    func flush() throws { flushes += 1; if failFlush { throw ModelError("flush failed") } }
}
private final class HostileDiagnosticError: Error, CustomStringConvertible, @unchecked Sendable {
    let render: () -> String
    init(_ render: @escaping () -> String) { self.render = render }
    var description: String { render() }
}

final class ObservationTests: XCTestCase {
    private func model(_ calls: ObservationCalls, codec: Bool = true) -> Model<UInt8,UInt8,UInt8> {
        func forbidden<T>() throws -> T { calls.forbidden += 1; throw ModelError("diagnostics executed model work") }
        func encode(_ kind: String, _ value: UInt8, _ sink: EncodeBuffer) throws {
            calls.payloads.append("\(kind):\(value)"); calls.encodedLease = sink
            if calls.fault == "swallow-\(kind)" { try? sink.extend([1,2]); return }
            try sink.extend([value])
            if calls.fault == kind { throw ModelError("codec failed") }
        }
        return Model(initialState: { try forbidden() }, step: { _, _ in try forbidden() }, checkState: { _ in try forbidden() },
                     cloneState: { _ in try forbidden() }, cloneInput: { _ in try forbidden() }, cloneOutput: { _ in try forbidden() },
                     checkTransition: { _,_,_ in try forbidden() },
                     codec: codec ? Codec(metadata: { try forbidden() }, encodeState: { _ in try forbidden() }, decodeState: { _ in try forbidden() },
                                          encodeInput: { _ in try forbidden() }, decodeInput: { _ in try forbidden() }, encodeOutput: { _ in try forbidden() },
                                          encodeStateInto: { try encode("state", $0, $1) }, encodeInputInto: { try encode("input", $0, $1) }, encodeOutputInto: { try encode("output", $0, $1) }) : nil)
    }
    private func turn(_ sequence: UInt64 = 2, checks: [Check] = [], disposition: Disposition = .accepted) throws -> Event<UInt8,UInt8,UInt8> {
        .transition(sequence: try ObservationSequence(sequence), before: 99, input: 1,
                    transition: Transition(state: 7, outputs: [9,10], disposition: disposition), checks: checks)
    }
    private func text<F: PayloadFormat>(_ log: TextObserver<ObservationBytes,F>) throws -> String {
        String(decoding: try log.getRef().bytes, as: UTF8.self)
    }
    private func assertStage(_ stage: TextObservationError.Stage, _ body: () throws -> Void, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try body(), file: file, line: line) { error in
            XCTAssertEqual((error as? TextObservationError)?.stage, stage, "\(error)", file: file, line: line)
        }
    }
    func testDefaultsAndNonzeroUInt64Admission() throws {
        let defaults = ObservationOptions()
        XCTAssertEqual(defaults.level, .off); XCTAssertEqual(defaults.snapshots, .none); XCTAssertEqual(defaults.maxRecordBytes, 1_048_576)
        XCTAssertThrowsError(try ObservationSequence(0)); XCTAssertThrowsError(try SnapshotPolicy.every(0))
        XCTAssertEqual(try ObservationSequence(UInt64.max).value, UInt64.max)
        XCTAssertNotEqual(try SnapshotPolicy.every(1), .everyTransition)
        XCTAssertThrowsError(try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(maxRecordBytes: -1), payloads: NoPayloads<UInt8,UInt8,UInt8>()))
    }
    func testFilterBeforeCallbacksAndNoExecutionOrClone() throws {
        for level in LogLevel.allCases {
            let calls = ObservationCalls()
            let m = model(calls)
            let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: level, snapshots: .everyTransition), payloads: DiagnosticFormatter(calls: calls))
            try log.observe(m, event: turn(disposition: .rejected("ordinary")))
            try log.observe(m, event: .checkpoint(sequence: 2, state: 8, checks: []))
            XCTAssertEqual(calls.payloads, level == .trace ? ["input:1", "output:9", "output:10", "state:7", "state:8"] : [])
            XCTAssertEqual(try text(log).isEmpty, level < .debug)
            XCTAssertEqual(calls.forbidden, 0)
        }
        let calls = ObservationCalls(), m = model(calls)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
        try log.observe(m, event: turn())
        XCTAssertEqual(calls.forbidden, 0); XCTAssertEqual(calls.payloads, ["input:1", "output:9", "output:10", "state:7"])
    }
    func testSeverityFailureWinsOverSkipAndRejectedIsDebug() throws {
        let calls = ObservationCalls(), m = model(calls, codec: false)
        for checks in [[Check.skipped("skip", "policy"), .failed("bad", "failed")], [.failed("bad", "failed"), .skipped("skip", "policy")]] {
            let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .error), payloads: NoPayloads<UInt8,UInt8,UInt8>())
            try log.observe(m, event: turn(checks: checks))
            try log.observe(m, event: .checkpoint(sequence: 0, state: 0, checks: checks))
            XCTAssertEqual(try text(log).components(separatedBy: "Error stream=").count, 3)
        }
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .warn), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        try log.observe(m, event: turn(disposition: .rejected("reason")))
        try log.observe(m, event: turn(disposition: .ignored("reason")))
        XCTAssertEqual(try text(log), "")
        try log.observe(m, event: turn(checks: [.passed("hidden"), .skipped("s", "policy")]))
        XCTAssertTrue(try text(log).hasPrefix("Warn ")); XCTAssertFalse(try text(log).contains("hidden"))
        XCTAssertEqual(calls.forbidden, 0)
    }
    func testLifecycleAndEveryMessageThreshold() throws {
        let m = model(ObservationCalls(), codec: false)
        for level in LogLevel.allCases {
            let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: level), payloads: NoPayloads<UInt8,UInt8,UInt8>())
            try log.observe(m, event: .started(metadata: Metadata(name: "n"), config: RunConfig(strategy: "manual")))
            for severity in LogLevel.allCases { try log.observe(m, event: .message(level: severity, sequence: nil, message: "message")) }
            try log.observe(m, event: .finished(transitions: UInt64.max, reason: "done"))
            let output = try text(log)
            XCTAssertEqual(output.contains("event=started"), level >= .info)
            XCTAssertEqual(output.contains("event=finished"), level >= .info)
            XCTAssertEqual(output.components(separatedBy: "event=message").count - 1, level.rawValue)
            XCTAssertFalse(output.contains("Off stream="))
        }
    }
    func testSnapshotPolicyAndCheckpointIndependentOfPeriod() throws {
        let calls = ObservationCalls(), m = model(calls)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, snapshots: try .every(2)), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
        try log.observe(m, event: turn(1)); try log.observe(m, event: turn(2))
        try log.observe(m, event: .checkpoint(sequence: 3, state: 8, checks: []))
        log.setSnapshots(try .every(UInt64.max)); try log.observe(m, event: turn(UInt64.max))
        log.setSnapshots(.none); try log.observe(m, event: .checkpoint(sequence: 3, state: 9, checks: []))
        log.setLevel(.debug); log.setSnapshots(.everyTransition); try log.observe(m, event: turn(4))
        XCTAssertEqual(calls.payloads, ["input:1", "output:9", "output:10", "input:1", "output:9", "output:10", "state:7", "state:8", "input:1", "output:9", "output:10", "state:7"])
        XCTAssertFalse(calls.payloads.contains("state:99")); XCTAssertEqual(calls.forbidden, 0)
    }
    func testExactTextChecksPayloadOrderAndNoCodecFormatter() throws {
        let calls = ObservationCalls(), m = model(calls, codec: false)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        try log.observe(m, event: turn(checks: [.passed("first"), .skipped("second", "policy")], disposition: .ignored("duplicate")))
        XCTAssertEqual(try text(log), "Warn stream=\"s\" event=transition sequence=2 disposition=Ignored(\"duplicate\") outputs=2 checks=2 check=\"first\":Passed check=\"second\":Skipped(\"policy\") input=unavailable output[0]=unavailable output[1]=unavailable post_state=unavailable\n")
        XCTAssertEqual(calls.forbidden, 0)
    }
    func testLibraryEscapingAndUtf8ExactNewlineBound() throws {
        let m = model(ObservationCalls(), codec: false)
        let expected = "Info stream=\"s\\n\\\"\\\\\" event=message sequence=Some(0) message=\"é😀\\0\\t\\r\\n\\u{1}\\\"\\\\\"\n"
        for cap in [expected.utf8.count - 1, expected.utf8.count] {
            let log = try TextObserver(writer: ObservationBytes(), streamID: "s\n\"\\", options: ObservationOptions(level: .info, maxRecordBytes: cap), payloads: NoPayloads<UInt8,UInt8,UInt8>())
            let event = Event<UInt8,UInt8,UInt8>.message(level: .info, sequence: 0, message: "é😀\0\t\r\n\u{1}\"\\")
            if cap == expected.utf8.count { try log.observe(m, event: event); XCTAssertEqual(try text(log), expected) }
            else { assertStage(.record) { try log.observe(m, event: event) }; XCTAssertEqual(try text(log), "") }
            XCTAssertFalse(log.isPoisoned)
        }
    }
    func testStartedAggregateEscapesEveryLibraryString() throws {
        let m = model(ObservationCalls(), codec: false)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .info), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        try log.observe(m, event: .started(metadata: Metadata(name: "n\n", modelVersion: 2, propertiesVersion: 3, codecVersion: 4, build: "b\r"), config: RunConfig(strategy: "s\t", seed: UInt64.max, parameters: [AuditParameter("k\n", "v\r"), AuditParameter("k\n", "again") ])))
        XCTAssertEqual(try text(log), "Info stream=\"s\" event=started metadata=ModelMetadata { name: \"n\\n\", model_version: 2, properties_version: 3, codec_version: 4, build: \"b\\r\" } config=RunConfig { strategy: \"s\\t\", seed: Some(18446744073709551615), parameters: [(\"k\\n\", \"v\\r\"), (\"k\\n\", \"again\")] }\n")
    }
    func testFormatterPartialWritesDiscardAndRecovery() throws {
        for (kind, stage) in [("input", TextObservationError.Stage.formatInput), ("output", .formatOutput), ("state", .formatState)] {
            let calls = ObservationCalls(), m = model(calls, codec: false)
            let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: DiagnosticFormatter(calls: calls))
            calls.fault = kind; assertStage(stage) { try log.observe(m, event: turn()) }
            XCTAssertEqual(try text(log), ""); XCTAssertFalse(log.isPoisoned)
            calls.fault = ""; try log.observe(m, event: turn()); XCTAssertEqual(try text(log).filter { $0 == "\n" }.count, 1)
            XCTAssertThrowsError(try calls.retained!.write("late"))
        }
    }
    func testPublicStagedErrorsAreAttributedToTheCurrentFormatter() throws {
        let foreignStages: [TextObservationError.Stage] = [.write, .flush, .poisoned, .record, .encodeInput, .encodeOutput, .encodeState, .formatInput, .formatOutput, .formatState, .consumed, .reentrant]
        let expected = Array("Debug stream=\"s\" event=transition sequence=2 disposition=Accepted outputs=2 checks=0 input=v1 output[0]=v9 output[1]=v10 post_state=v7\n".utf8)
        for (kind, stage, prefix) in [("input", TextObservationError.Stage.formatInput, ["input:1"]),
                                      ("output", .formatOutput, ["input:1", "output:9"]),
                                      ("state", .formatState, ["input:1", "output:9", "output:10", "state:7"])] {
            for foreignStage in foreignStages {
                let calls = ObservationCalls(), m = model(calls, codec: false), writer = DiagnosticWriter()
                let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: DiagnosticFormatter(calls: calls))
                calls.formatterCallback = { current, _ in
                    if current == kind { throw TextObservationError(stage: foreignStage, detail: "foreign") }
                }
                assertStage(stage) { try log.observe(m, event: turn()) }
                XCTAssertEqual(calls.payloads, prefix)
                XCTAssertEqual(writer.calls, 0); XCTAssertEqual(writer.bytes, []); XCTAssertFalse(log.isPoisoned)
                log.setLevel(.off); try log.observe(m, event: turn())
                XCTAssertEqual(calls.payloads, prefix); XCTAssertEqual(writer.calls, 0)
                calls.formatterCallback = nil; calls.payloads = []; log.setLevel(.trace)
                try log.observe(m, event: turn())
                XCTAssertEqual(writer.bytes, expected); XCTAssertEqual(writer.calls, 1); XCTAssertEqual(writer.flushes, 0)
                XCTAssertEqual(calls.payloads, ["input:1", "output:9", "output:10", "state:7"])
                XCTAssertFalse(log.isPoisoned); XCTAssertEqual(calls.forbidden, 0)
            }
        }
    }
    func testNestedObserverErrorsDoNotEstablishOuterFailureStagesOrPoison() throws {
        let expected = Array("Debug stream=\"s\" event=transition sequence=2 disposition=Accepted outputs=2 checks=0 input=v1 output[0]=v9 output[1]=v10 post_state=v7\n".utf8)
        for (kind, stage, prefix) in [("input", TextObservationError.Stage.formatInput, ["input:1"]),
                                      ("output", .formatOutput, ["input:1", "output:9"]),
                                      ("state", .formatState, ["input:1", "output:9", "output:10", "state:7"])] {
            for nestedStage in [TextObservationError.Stage.write, .record, .encodeState] {
                let calls = ObservationCalls(), m = model(calls, codec: false)
                let writer = DiagnosticWriter(), innerWriter = DiagnosticWriter()
                if nestedStage == .write { innerWriter.failAfter = 0 }
                let inner = try TextObserver(writer: innerWriter, streamID: "inner", options: ObservationOptions(level: .trace, snapshots: .everyTransition, maxRecordBytes: nestedStage == .record ? 0 : 1024), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
                let outer = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: DiagnosticFormatter(calls: calls))
                var propagatedStage: TextObservationError.Stage?
                calls.formatterCallback = { current, _ in
                    guard current == kind else { return }
                    do {
                        // A checkpoint invokes the inner state codec; the other
                        // cases fail before any payload encoding is needed.
                        if nestedStage == .encodeState {
                            try inner.observe(m, event: .checkpoint(sequence: 0, state: 0, checks: []))
                        } else {
                            try inner.observe(m, event: .finished(transitions: 0, reason: "nested"))
                        }
                    } catch let error as TextObservationError {
                        propagatedStage = error.stage; throw error
                    }
                }
                assertStage(stage) { try outer.observe(m, event: turn()) }
                XCTAssertEqual(propagatedStage, nestedStage); XCTAssertEqual(calls.payloads, prefix)
                XCTAssertEqual(writer.calls, 0); XCTAssertEqual(writer.bytes, []); XCTAssertFalse(outer.isPoisoned)
                XCTAssertEqual(innerWriter.calls, nestedStage == .write ? 1 : 0)
                XCTAssertEqual(inner.isPoisoned, nestedStage == .write)
                outer.setLevel(.off); try outer.observe(m, event: turn())
                XCTAssertEqual(calls.payloads, prefix); XCTAssertEqual(writer.calls, 0)
                calls.formatterCallback = nil; calls.payloads = []; outer.setLevel(.trace)
                try outer.observe(m, event: turn())
                XCTAssertEqual(writer.bytes, expected); XCTAssertEqual(writer.calls, 1); XCTAssertEqual(writer.flushes, 0)
                XCTAssertEqual(calls.payloads, ["input:1", "output:9", "output:10", "state:7"])
                XCTAssertFalse(outer.isPoisoned); XCTAssertEqual(calls.forbidden, 0)
            }
        }
    }
    func testRetainedRecordErrorCannotAuthenticateALaterFormatterCall() throws {
        let calls = ObservationCalls(), m = model(calls, codec: false), writer = DiagnosticWriter()
        let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, maxRecordBytes: 512), payloads: DiagnosticFormatter(calls: calls))
        var retainedError: (any Error)?
        calls.formatterCallback = { _, out in
            do { try out.write([UInt8](repeating: 0xff, count: 1024)) }
            catch { retainedError = error; throw error }
        }
        assertStage(.record) { try log.observe(m, event: turn()) }
        let oldError = try XCTUnwrap(retainedError)
        calls.formatterCallback = { _, _ in throw oldError }
        assertStage(.formatInput) { try log.observe(m, event: turn()) }
        XCTAssertEqual(writer.calls, 0); XCTAssertEqual(writer.bytes, []); XCTAssertFalse(log.isPoisoned)
        calls.formatterCallback = nil
        try log.observe(m, event: turn())
        XCTAssertEqual(writer.bytes, Array("Debug stream=\"s\" event=transition sequence=2 disposition=Accepted outputs=2 checks=0 input=v1 output[0]=v9 output[1]=v10\n".utf8))
        XCTAssertEqual(writer.calls, 1); XCTAssertFalse(log.isPoisoned); XCTAssertEqual(calls.forbidden, 0)
    }
    func testSwallowedRecordFailureAndRetainedLeaseCannotPublish() throws {
        let calls = ObservationCalls(), m = model(calls, codec: false)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, maxRecordBytes: 512), payloads: DiagnosticFormatter(calls: calls))
        calls.fault = "swallow"; assertStage(.record) { try log.observe(m, event: turn()) }
        XCTAssertEqual(try text(log), ""); XCTAssertFalse(log.isPoisoned)
        let stale = calls.retained!
        calls.fault = ""; try log.observe(m, event: turn())
        XCTAssertThrowsError(try stale.write("late")); XCTAssertEqual(try text(log).filter { $0 == "\n" }.count, 1)
    }
    func testOverflowThenThrowPreservesRecordFailureAndRecovers() throws {
        for kind in ["input", "output", "state"] {
            let calls = ObservationCalls(), m = model(calls, codec: false)
            let writer = DiagnosticWriter()
            let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition, maxRecordBytes: 1024), payloads: DiagnosticFormatter(calls: calls))
            calls.formatterCallback = { current, out in
                if current == kind {
                    try? out.write([UInt8](repeating: 120, count: 2048))
                    throw ModelError("later formatter failure")
                }
            }
            assertStage(.record) { try log.observe(m, event: turn()) }
            XCTAssertEqual(writer.bytes, []); XCTAssertEqual(writer.calls, 0)
            XCTAssertFalse(log.isPoisoned)
            calls.formatterCallback = nil
            try log.observe(m, event: turn())
            XCTAssertEqual(writer.calls, 1); XCTAssertFalse(writer.bytes.isEmpty)
            XCTAssertEqual(calls.forbidden, 0)
        }
    }

    func testZeroBoundStillFiltersBeforeFormattingOrWriting() throws {
        let calls = ObservationCalls(), m = model(calls), writer = DiagnosticWriter()
        let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(maxRecordBytes: 0), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 0))
        try log.observe(m, event: turn(checks: [.failed("bad", "fail")]))
        log.setLevel(.trace)
        assertStage(.record) { try log.observe(m, event: turn()) }
        log.setLevel(.off); try log.observe(m, event: turn())
        XCTAssertEqual(calls.payloads, []); XCTAssertEqual(calls.forbidden, 0)
        XCTAssertEqual(writer.calls, 0); XCTAssertFalse(log.isPoisoned)
    }
    func testCustomFormatterOwnsItsEscaping() throws {
        struct Multiline: PayloadFormat {
            typealias State = UInt8; typealias Input = UInt8; typealias Output = UInt8
            func state(_ m: Model<UInt8,UInt8,UInt8>, _ s: UInt8, into out: ObservationBuffer) throws { try out.write("a\nb") }
            func input(_ m: Model<UInt8,UInt8,UInt8>, _ i: UInt8, into out: ObservationBuffer) throws { try out.write("a\nb") }
            func output(_ m: Model<UInt8,UInt8,UInt8>, _ o: UInt8, into out: ObservationBuffer) throws { try out.write("a\nb") }
        }
        let m = model(ObservationCalls(), codec: false)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace), payloads: Multiline())
        try log.observe(m, event: .transition(sequence: try ObservationSequence(1), before: 0, input: 1, transition: Transition(state: 1), checks: []))
        XCTAssertEqual(try text(log), "Debug stream=\"s\" event=transition sequence=1 disposition=Accepted outputs=0 checks=0 input=a\nb\n")
    }
    func testArbitraryPayloadBytesRespectExactRecordAndFinalNewlineBounds() throws {
        struct RawBytes: PayloadFormat {
            typealias State = UInt8; typealias Input = UInt8; typealias Output = UInt8
            let calls: ObservationCalls
            func render(_ kind: String, _ out: ObservationBuffer) throws {
                calls.payloads.append(kind); try out.write([0xff, 0x00, 0x0a])
            }
            func state(_ m: Model<UInt8,UInt8,UInt8>, _ s: UInt8, into out: ObservationBuffer) throws { try render("state", out) }
            func input(_ m: Model<UInt8,UInt8,UInt8>, _ i: UInt8, into out: ObservationBuffer) throws { try render("input", out) }
            func output(_ m: Model<UInt8,UInt8,UInt8>, _ o: UInt8, into out: ObservationBuffer) throws { try render("output", out) }
        }
        let prefix = Array("Debug stream=\"s\" event=transition sequence=1 disposition=Accepted outputs=0 checks=0 input=".utf8)
        // Literal payload bytes are not decoded into String or replacement UTF-8.
        let expected = prefix + [0xff, 0x00, 0x0a, 0x0a]
        for cap in [expected.count - 2, expected.count - 1, expected.count] {
            let calls = ObservationCalls(), m = model(calls, codec: false), writer = DiagnosticWriter()
            let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, maxRecordBytes: cap), payloads: RawBytes(calls: calls))
            let event = Event<UInt8,UInt8,UInt8>.transition(sequence: try ObservationSequence(1), before: 0, input: 1, transition: Transition(state: 1), checks: [])
            if cap == expected.count {
                try log.observe(m, event: event)
                XCTAssertEqual(writer.bytes, expected); XCTAssertEqual(writer.calls, 1)
            } else {
                // Two short rejects the atomic raw write; one short rejects only LF.
                assertStage(.record) { try log.observe(m, event: event) }
                XCTAssertEqual(writer.bytes, []); XCTAssertEqual(writer.calls, 0)
                log.setLevel(.off); try log.observe(m, event: event)
                log.setLevel(.info); try log.observe(m, event: .finished(transitions: 0, reason: "done"))
                XCTAssertEqual(writer.bytes, Array("Info stream=\"s\" event=finished transitions=0 reason=\"done\"\n".utf8))
                XCTAssertEqual(writer.calls, 1)
            }
            XCTAssertEqual(calls.payloads, ["input"]); XCTAssertEqual(calls.forbidden, 0)
            XCTAssertFalse(log.isPoisoned); XCTAssertEqual(writer.flushes, 0)
        }
    }
    func testEncodeFailuresStagesStickyBoundsAndRecovery() throws {
        for (kind, stage) in [("input", TextObservationError.Stage.encodeInput), ("output", .encodeOutput), ("state", .encodeState)] {
            for swallowed in [false, true] {
                let calls = ObservationCalls(), m = model(calls)
                let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
                calls.fault = swallowed ? "swallow-\(kind)" : kind
                assertStage(stage) { try log.observe(m, event: turn()) }; XCTAssertEqual(try text(log), ""); XCTAssertFalse(log.isPoisoned)
                calls.fault = ""; try log.observe(m, event: turn())
                XCTAssertTrue(try text(log).contains("input=hex:01 output[0]=hex:09 output[1]=hex:0a post_state=hex:07"))
                XCTAssertThrowsError(try calls.encodedLease!.extend([1])); XCTAssertEqual(calls.forbidden, 0)
            }
        }
    }
    func testLegacyCodecAndMissingCodecOnlyFailAtSelectedPayload() throws {
        let calls = ObservationCalls()
        var m = model(calls, codec: false)
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .debug), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
        try log.observe(m, event: turn()); log.setLevel(.trace)
        assertStage(.encodeInput) { try log.observe(m, event: turn()) }
        m.codec = Codec(metadata: { throw ModelError("unused") }, encodeState: { [$0] }, decodeState: { $0[0] }, encodeInput: { value in calls.payloads.append("legacy:\(value)"); return [value, value] }, decodeInput: { $0[0] }, encodeOutput: { [$0] })
        assertStage(.encodeInput) { try log.observe(m, event: turn()) }
        XCTAssertEqual(calls.payloads, ["legacy:1"]); XCTAssertFalse(log.isPoisoned)
        m.codec!.encodeInput = { [$0] }; try log.observe(m, event: turn())
        XCTAssertTrue(try text(log).contains("input=hex:01"))
    }
    func testShortInterruptedWritesDeliverAllBytesWithoutImplicitFlush() throws {
        let writer = DiagnosticWriter(); writer.short = true
        let m = model(ObservationCalls(), codec: false)
        let expected = "Info stream=\"s\" event=finished transitions=0 reason=\"done\"\n"
        let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .info, maxRecordBytes: expected.utf8.count), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        try log.observe(m, event: .finished(transitions: 0, reason: "done"))
        XCTAssertEqual(writer.bytes, Array(expected.utf8)); XCTAssertGreaterThan(writer.calls, 2)
        XCTAssertEqual(writer.flushes, 0); try log.flush(); XCTAssertEqual(writer.flushes, 1)
        XCTAssertTrue(try log.getRef() === writer); XCTAssertTrue(try log.intoInner() === writer)
        XCTAssertEqual(writer.flushes, 1); assertStage(.consumed) { _ = try log.intoInner() }
        assertStage(.consumed) { try log.observe(m, event: .message(level: .off, sequence: nil, message: "")) }
    }
    func testWriterAndFlushFailuresPermanentlyPoisonEvenOff() throws {
        for mode in ["partial", "zero", "negative", "oversized", "flush"] {
            let writer = DiagnosticWriter(), m = model(ObservationCalls(), codec: false)
            if mode == "partial" { writer.failAfter = 5 }
            if mode == "zero" { writer.invalidCount = 0 }
            if mode == "negative" { writer.invalidCount = -1 }
            if mode == "oversized" { writer.invalidCount = Int.max }
            if mode == "flush" { writer.failFlush = true }
            let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .info), payloads: NoPayloads<UInt8,UInt8,UInt8>())
            if mode == "flush" { assertStage(.flush) { try log.flush() } }
            else { assertStage(.write) { try log.observe(m, event: .finished(transitions: 0, reason: "done")) } }
            if mode == "partial" { XCTAssertEqual(writer.bytes, Array("Info ".utf8)) }
            XCTAssertTrue(log.isPoisoned); log.setLevel(.off)
            let count = writer.calls, flushes = writer.flushes
            assertStage(.poisoned) { try log.observe(m, event: turn()) }; assertStage(.poisoned) { try log.flush() }
            XCTAssertEqual(writer.calls, count); XCTAssertEqual(writer.flushes, flushes)
            XCTAssertTrue(try log.intoInner() === writer)
        }
    }
    func testReentrancyAndHostileWriterErrorDescriptionAreContained() throws {
        let writer = DiagnosticWriter(), m = model(ObservationCalls(), codec: false)
        let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .info), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        var nested = 0
        writer.onWrite = {
            nested += 1
            self.assertStage(.reentrant) { try log.observe(m, event: .finished(transitions: 0, reason: "nested")) }
            self.assertStage(.reentrant) { _ = try log.getRef() }
            self.assertStage(.reentrant) { _ = try log.intoInner() }
        }
        writer.thrown = HostileDiagnosticError {
            XCTAssertTrue(log.isPoisoned)
            self.assertStage(.poisoned) { try log.flush() }
            return "hostile failure"
        }
        assertStage(.write) { try log.observe(m, event: .finished(transitions: 0, reason: "outer")) }
        XCTAssertEqual(nested, 1); XCTAssertEqual(writer.bytes, [])
        writer.onWrite = nil; writer.thrown = nil
    }
    func testDeclarationCodecBindingIsPreferredAndVersionGatePreserved() throws {
        let calls = ObservationCalls()
        var m = model(calls, codec: false), metadataCalls = 0, version: UInt32 = 1
        let scalar = try TraceRecords.codec(schema: "Byte", fields: TraceFields.first(TraceField<UInt8,UInt8>.ordinary("value", ValueCodecs.u8, get: { $0 })), construct: { $0 })
        let output = TraceValueCodec<UInt8>(schema: "output", encodeOnly: { try $1.extend([$0]) })
        m.codec = try ModelCodecBinding(metadata: { metadataCalls += 1; return Metadata(name: "binding", codecVersion: version) }, state: scalar, input: scalar, output: output).codec()
        m.codec!.encodeInput = { _ in XCTFail("legacy bypassed preferred binding"); return [] }
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .debug, snapshots: .everyTransition), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
        try log.observe(m, event: turn()); XCTAssertEqual(metadataCalls, 0)
        log.setLevel(.trace); try log.observe(m, event: turn()); XCTAssertEqual(metadataCalls, 4)
        let prior = try text(log); version = 2
        assertStage(.encodeInput) { try log.observe(m, event: turn()) }; XCTAssertEqual(metadataCalls, 5); XCTAssertEqual(try text(log), prior)
        version = 1; try log.observe(m, event: turn()); XCTAssertEqual(metadataCalls, 9); XCTAssertEqual(calls.forbidden, 0)
    }
    func testDeclarationAdaptersAreRequiredWhenDirectCodecHooksFail() throws {
        let calls = ObservationCalls(), writer = DiagnosticWriter()
        var m = model(calls, codec: false)
        var directCalls: [String] = [], adapterCalls: [String] = []
        func direct(_ kind: String) throws {
            directCalls.append(kind); throw ModelError("declaration binding bypassed")
        }
        m.codec = Codec(metadata: { throw ModelError("direct metadata") }, encodeState: { _ in throw ModelError("legacy state") }, decodeState: { _ in throw ModelError("direct decode") },
                        encodeInput: { _ in throw ModelError("legacy input") }, decodeInput: { _ in throw ModelError("direct decode") }, encodeOutput: { _ in throw ModelError("legacy output") },
                        encodeStateInto: { _, _ in try direct("state") }, encodeInputInto: { _, _ in try direct("input") }, encodeOutputInto: { _, _ in try direct("output") })
        let unbound = m
        let log = try TextObserver(writer: writer, streamID: "s", options: ObservationOptions(level: .trace, snapshots: .everyTransition), payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: 1))
        let event = Event<UInt8,UInt8,UInt8>.transition(sequence: try ObservationSequence(1), before: 0, input: 17, transition: Transition(state: 34, outputs: [51]), checks: [])
        // This is the deliberate binder-omission control: no adapters can run.
        assertStage(.encodeInput) { try log.observe(unbound, event: event) }
        XCTAssertEqual(directCalls, ["input"]); XCTAssertEqual(adapterCalls, [])
        XCTAssertEqual(writer.calls, 0); XCTAssertEqual(writer.bytes, []); XCTAssertFalse(log.isPoisoned)
        func declared(_ kind: String) throws -> TraceValueCodec<UInt8> {
            let byte = TraceValueCodec<UInt8>(schema: "observation.byte.\(kind)", encode: { value, sink in
                adapterCalls.append("\(kind):\(value)"); try sink.extend([value])
            }, decode: { _ in throw ModelError("unused decode") })
            return try TraceRecords.codec(schema: "Observation\(kind)", fields: TraceFields.first(TraceField<UInt8,UInt8>.ordinary("value", byte, get: { $0 })), construct: { $0 }, require: .both)
        }
        let output = TraceValueCodec<UInt8>(schema: "observation.output", encodeOnly: { value, sink in
            adapterCalls.append("output:\(value)"); try sink.extend([value])
        })
        m.codec = try ModelCodecBinding(metadata: { Metadata(name: "observation-binding", build: "source-derived") }, state: declared("state"), input: declared("input"), output: output).codec()
        try log.observe(m, event: event)
        XCTAssertEqual(directCalls, ["input"]); XCTAssertEqual(adapterCalls, ["input:17", "output:51", "state:34"])
        XCTAssertEqual(writer.bytes, Array("Debug stream=\"s\" event=transition sequence=1 disposition=Accepted outputs=1 checks=0 input=hex:11 output[0]=hex:33 post_state=hex:22\n".utf8))
        XCTAssertEqual(writer.calls, 1); XCTAssertFalse(log.isPoisoned); XCTAssertEqual(calls.forbidden, 0)
    }
    func testForgedDiagnosticsCannotReplaceCheckedRecorderAuthority() throws {
        var checks = 0, effects = 0
        let m = Model<UInt8,UInt8,UInt8>(initialState: { XCTFail("initial"); return 0 }, step: { _,_ in effects += 1; return Transition(state: 0) },
            checkState: { _ in checks += 1; return [.passed("state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { _,_,_ in checks += 1; return [.passed("edge")] },
            codec: Codec(metadata: { Metadata(name: "checked") }, encodeState: { [$0] }, decodeState: { $0[0] }, encodeInput: { [$0] }, decodeInput: { $0[0] }, encodeOutput: { [$0] }))
        let initial = try checkInitial(m, state: 0)
        let recorder = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "manual"))
        let log = try TextObserver(writer: ObservationBytes(), streamID: "s", options: ObservationOptions(level: .trace), payloads: NoPayloads<UInt8,UInt8,UInt8>())
        let forged = TurnObservation(sequence: 99, before: UInt8(0), input: UInt8(1), transition: Transition<UInt8,UInt8>(state: 8), checks: [.passed("fake")], stateCheckCount: 1)
        let forgedEvent = Event<UInt8,UInt8,UInt8>.transition(sequence: try ObservationSequence(forged.sequence), before: forged.before, input: forged.input, transition: forged.transition, checks: forged.checks)
        XCTAssertNil((forged as Any) as? CheckedTurn<UInt8,UInt8,UInt8>)
        XCTAssertNil((forgedEvent as Any) as? CheckedTurn<UInt8,UInt8,UInt8>)
        try log.observe(m, event: forgedEvent)
        XCTAssertEqual(checks, 1); XCTAssertEqual(recorder.observedSteps, 0)
        let token = try checkTurn(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1)
        let view = try token.observation()
        try log.observe(m, event: .transition(sequence: try ObservationSequence(view.sequence), before: view.before, input: view.input, transition: view.transition, checks: view.checks))
        _ = try recorder.observeChecked(token); XCTAssertEqual(checks, 3)
        XCTAssertThrowsError(try recorder.observeChecked(token)) // Duplicate checked sequence still fails.
        XCTAssertEqual(checks, 3); XCTAssertEqual(effects, 0)
        let sampledRecorder = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "manual"))
        let sampled = try checkTurn(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1, policy: CheckPolicy(stateEvery: 2))
        XCTAssertThrowsError(try sampledRecorder.observeChecked(sampled)); XCTAssertFalse(sampled.fullChecking)
        // Passing Event or TurnObservation to observeChecked is unrepresentable:
        // that API accepts only the existing engine-constructed CheckedTurn.
    }
}
