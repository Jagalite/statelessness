import XCTest
import Statelessness
import StatelessDebug

final class SessionReadOwnershipTests: XCTestCase {
    private enum Hook: CaseIterable, Equatable { case state, input, output }
    private enum CloneFailure: Error { case expected }
    private final class Hooks {
        var callback: ((Hook) throws -> Void)?
        private var inCallback = false

        func copy(_ value: Int, hook: Hook) throws -> Int {
            // Bound recursion even if a regression allows the attempted step.
            if !inCallback, let callback {
                inCallback = true; defer { inCallback = false }
                try callback(hook)
            }
            return value
        }
    }

    private func fixture() throws -> (DebugSession<Int,Int,Int>, Hooks) {
        let hooks = Hooks()
        let codec = Codec<Int,Int,Int>(
            metadata: { Metadata(name: "read-ownership") },
            encodeState: { [UInt8(truncatingIfNeeded: $0)] }, decodeState: { Int($0[0]) },
            encodeInput: { [UInt8(truncatingIfNeeded: $0)] }, decodeInput: { Int($0[0]) },
            encodeOutput: { [UInt8(truncatingIfNeeded: $0)] }
        )
        let model = Model<Int,Int,Int>(
            initialState: { 0 }, step: { Transition(state: $0 + $1, outputs: [$0 + $1]) },
            checkState: { _ in [.passed("state")] },
            cloneState: { try hooks.copy($0, hook: .state) },
            cloneInput: { try hooks.copy($0, hook: .input) },
            cloneOutput: { try hooks.copy($0, hook: .output) },
            codec: codec
        )
        return (try DebugSession(id: "read-ownership", model: model, policy: .outOfDomainInjection, equalInputs: ==), hooks)
    }

    private func assertMutationsRejected(_ session: DebugSession<Int,Int,Int>, file: StaticString = #filePath, line: UInt = #line) {
        let sequence = session.sequence, revision = session.revision, phase = session.phase
        XCTAssertThrowsError(try session.step(expectedRevision: revision, input: 10), file: file, line: line) {
            XCTAssertEqual($0 as? DebugError, .reentrant, file: file, line: line)
        }
        XCTAssertThrowsError(try session.cancel(expectedRevision: revision), file: file, line: line) {
            XCTAssertEqual($0 as? DebugError, .reentrant, file: file, line: line)
        }
        XCTAssertEqual(session.sequence, sequence, file: file, line: line)
        XCTAssertEqual(session.revision, revision, file: file, line: line)
        XCTAssertEqual(session.phase, phase, file: file, line: line)
    }

    func testDirectReadsHoldOwnershipAcrossAllCloneHooksAndNestedReads() throws {
        let (session, hooks) = try fixture()
        XCTAssertNil(try session.observation())
        XCTAssertTrue(try session.step(expectedRevision: 0, input: 1).delivered)
        var seen: [Hook] = []
        hooks.callback = { hook in
            seen.append(hook)
            XCTAssertEqual(try session.state, 1)
            XCTAssertEqual(try session.observation()?.sequence, 1)
            self.assertMutationsRejected(session)
        }
        XCTAssertEqual(try session.state, 1)
        XCTAssertEqual(seen, [.state])
        seen.removeAll()
        let observation = try XCTUnwrap(session.observation())
        XCTAssertEqual(seen, [.state, .input, .state, .output])
        XCTAssertEqual(observation.sequence, 1)
        XCTAssertEqual(observation.before, 0)
        XCTAssertEqual(observation.input, 1)
        XCTAssertEqual(observation.transition.state, 1)
        XCTAssertEqual(observation.transition.outputs, [1])
        XCTAssertEqual(session.sequence, 1); XCTAssertEqual(session.revision, 1)
        XCTAssertEqual(session.phase, .paused); XCTAssertNil(session.stop)
        hooks.callback = nil
        XCTAssertTrue(try session.step(expectedRevision: 1, input: 2).delivered)
        XCTAssertEqual(try session.state, 3)
        try session.cancel(expectedRevision: 2)
    }

    func testThrowingDirectReadsReleaseTheirOwnership() throws {
        for failedHook in Hook.allCases {
            let (session, hooks) = try fixture()
            XCTAssertTrue(try session.step(expectedRevision: 0, input: 1).delivered)
            hooks.callback = { hook in
                self.assertMutationsRejected(session)
                if hook == failedHook { throw CloneFailure.expected }
            }
            if failedHook == .state {
                XCTAssertThrowsError(try session.state) { XCTAssertTrue($0 is CloneFailure) }
            }
            XCTAssertThrowsError(try session.observation()) { XCTAssertTrue($0 is CloneFailure) }
            hooks.callback = nil
            XCTAssertEqual(try session.state, 1)
            XCTAssertEqual(try session.observation()?.transition.outputs, [1])
            XCTAssertEqual(session.sequence, 1); XCTAssertEqual(session.revision, 1)
            XCTAssertEqual(session.phase, .paused); XCTAssertNil(session.stop)
            XCTAssertTrue(try session.step(expectedRevision: 1, input: 2).delivered)
            try session.cancel(expectedRevision: 2)
        }
    }

    func testNestedReadsPreserveOuterOwnershipAfterSuccessAndThrow() throws {
        for failedHook in Hook.allCases {
            let (session, hooks) = try fixture()
            let result = try session.step(expectedRevision: 0, input: 1) { _ in
                hooks.callback = { _ in self.assertMutationsRejected(session) }
                XCTAssertEqual(try session.state, 1)
                XCTAssertEqual(try session.observation()?.transition.outputs, [1])
                self.assertMutationsRejected(session)
                hooks.callback = { hook in
                    self.assertMutationsRejected(session)
                    if hook == failedHook { throw CloneFailure.expected }
                }
                if failedHook == .state {
                    XCTAssertThrowsError(try session.state) { XCTAssertTrue($0 is CloneFailure) }
                }
                XCTAssertThrowsError(try session.observation()) { XCTAssertTrue($0 is CloneFailure) }
                hooks.callback = nil
                self.assertMutationsRejected(session)
            }
            XCTAssertTrue(result.delivered); XCTAssertNil(result.stop)
            XCTAssertEqual(try session.state, 1)
            XCTAssertEqual(session.sequence, 1); XCTAssertEqual(session.revision, 1)
            XCTAssertTrue(try session.step(expectedRevision: 1, input: 2).delivered)
            try session.cancel(expectedRevision: 2)
        }
    }

    func testProtocolChecksAndExactOutputsKeepValidatedHandleStable() throws {
        let (session, hooks) = try fixture()
        XCTAssertTrue(try session.step(expectedRevision: 0, input: 1).delivered)
        var authority = ProtocolAuthority(); authority.readExactValues = true
        let adapter = try ProtocolAdapter(session: session, authority: authority)
        var requestID: UInt64 = 0
        func request(_ command: String, _ arguments: [String] = [], revision: UInt64 = .max) -> ProtocolResponse {
            requestID += 1
            return adapter.handle(ProtocolRequest(session: session.id, epoch: adapter.epoch, requestID: requestID,
                                                  expectedRevision: revision, command: command, arguments: arguments))
        }
        let snapshot = request("snapshot", revision: 1)
        XCTAssertNil(snapshot.error); XCTAssertEqual(snapshot.fields["handle"], "u64:1")
        let expected: [(String, [String:String])] = [
            ("checks", ["checks.complete":"bool:true", "checks.total":"u64:1", "checks.returned":"u64:1",
                        "checks.truncated":"bool:false", "checks.first_failure":"none",
                        "checks.0.id":"string:state", "checks.0.status":"enum:passed"]),
            ("outputs", ["outputs.total":"u64:1", "disposition":"enum:accepted", "outputs.provenance":"enum:simulated",
                         "outputs.payloads":"enum:exact_sensitive", "outputs.0.encoded":"bytes:01", "outputs.truncated":"bool:false"])
        ]
        for (command, fields) in expected {
            var seen: [Hook] = []
            hooks.callback = { hook in seen.append(hook); self.assertMutationsRejected(session) }
            let response = request(command, ["u64:1"])
            XCTAssertNil(response.error); XCTAssertEqual(response.revision, 1)
            XCTAssertEqual(response.fields, fields)
            XCTAssertEqual(seen, [.state, .input, .state, .output])
            try adapter.validateHandle(1)
            for failedHook in Hook.allCases {
                hooks.callback = { hook in
                    self.assertMutationsRejected(session)
                    if hook == failedHook { throw CloneFailure.expected }
                }
                let failure = request(command, ["u64:1"])
                XCTAssertEqual(failure.error, .callback); XCTAssertEqual(failure.revision, 1)
                XCTAssertTrue(failure.fields.isEmpty)
                hooks.callback = nil
                XCTAssertEqual(try session.state, 1)
                XCTAssertEqual(session.sequence, 1); XCTAssertEqual(session.revision, 1)
                XCTAssertEqual(session.phase, .paused); XCTAssertNil(session.stop)
                try adapter.validateHandle(1)
                let retry = request(command, ["u64:1"])
                XCTAssertNil(retry.error); XCTAssertEqual(retry.fields, fields)
            }
        }
        XCTAssertTrue(try session.step(expectedRevision: 1, input: 2).delivered)
        hooks.callback = { _ in XCTFail("stale handles must reject before cloning") }
        for (command, _) in expected { XCTAssertEqual(request(command, ["u64:1"]).error, .staleHandle) }
        hooks.callback = nil
        XCTAssertEqual(try session.state, 3)
        try session.cancel(expectedRevision: 2)
    }
}
