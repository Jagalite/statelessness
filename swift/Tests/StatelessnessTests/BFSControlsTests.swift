import Foundation
import XCTest
import Statelessness
import StatelessConformance

private func bfsVectorCases() throws -> [[String: Any]] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let path = ProcessInfo.processInfo.environment["STATELESS_BFS_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/bfs-controls-v1.json")
    return (try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any])["cases"] as! [[String: Any]]
}
private func bfsCounter() -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { state, input in Transition(state: state + input) }, checkState: { state in [state >= 3 ? .failed("threshold.bound", "broken") : .passed("ok")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [0, 1]) }, equalStates: ==, hashState: { _ in 0 }, estimatedStateBytes: { _ in 5 })
}
private final class BFSBox { var n: Int; init(_ n: Int) { self.n = n } }
final class BFSControlsTests: XCTestCase {
    func testSharedBFSControlsCorpus() throws {
        for c in try bfsVectorCases() {
            let name = c["case"] as! String, fixture = c["fixture"] as! String
            let cancelAt = c["cancel_at"] as? String, errorAt = c["error_at"] as? String
            let entry = c["entry_control"] as! String
            var cancelled = entry == "cancelled" || entry == "both", events: [String] = []
            func event(_ text: String) throws {
                events.append(text)
                if text == cancelAt { cancelled = true }
                if text == errorAt { throw ModelError("fixture check error") }
            }
            let estimates = c["estimates"] as! [Any]
            let m = Model<Int, Int, Int>(initialState: { try event("initial"); return 0 }, step: { state, input in
                try event("step:\(state):\(input)"); return Transition(state: fixture == "diamond" ? input : state + input)
            }, checkState: { state in
                try event("state:\(state)")
                var checks = [Check.passed("ok")]
                if c["skipped"] as! Bool { checks.append(.skipped("skip", "not run")) }
                if (state == 0 && c["fail_initial"] as! Bool) || state == c["fail_state"] as? Int { checks.append(.failed("bad", "broken")) }
                return checks
            }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { before, input, t in
                try event("transition:\(before):\(input):\(t.state)"); return []
            }, inputs: { state in
                try event("inputs:\(state)")
                let values: [Int]
                switch fixture {
                case "chain": values = state < 2 ? [1] : []
                case "loop": values = state == 0 ? [0, 0] : []
                case "diamond": values = state == 0 ? [1, 2, 1] : state == 1 || state == 2 ? [3] : []
                default: values = []
                }
                var index = 0
                return InputIterator {
                    if index == values.count { try event("next:\(state):end"); return nil }
                    let value = values[index]; index += 1; try event("next:\(state):\(value)"); return value
                }
            }, equalStates: { a, b in try event("equal:\(a):\(b)"); return a == b }, hashState: { state in try event("hash:\(state)"); return 0 }, estimatedStateBytes: { state in
                try event("estimate:\(state)"); return (estimates[state] as? String).flatMap(UInt64.init)
            })
            let observer = SearchObserver<Int, Int, Int>(initial: { state, _ in try event("observe_initial:\(state)") }, checkError: { phase, checks, complete in
                try event("check_error:\(phase):\(complete):\(checks.count)")
            }, transition: { before, input, t, _, _, history in
                try event("observe:\(before):\(input):\(t.state):\(history.isEmpty ? "-" : history.map(String.init).joined(separator: ","))")
            }, needsHistory: { c["needs_history"] as! Bool })
            let control = RunLimits(maxDuration: entry == "deadline" || entry == "both" ? 0 : nil, isCancelled: { cancelled })
            let limits = SearchLimits(control: control, maxEstimatedStateBytes: (c["max_estimated_state_bytes"] as? String).flatMap(UInt64.init))
            let config = SearchConfig(maxStates: c["max_states"] as! Int, maxTransitions: (c["max_transitions"] as! NSNumber).uint64Value, maxDepth: c["max_depth"] as! Int)
            if let message = c["error_contains"] as? String {
                XCTAssertThrowsError(try enumerateObserved(m, config: config, limits: limits, observer: observer), name) { XCTAssertTrue(String(describing: $0).contains(message), "\(name): \($0)") }
            } else {
                let r = try enumerateObserved(m, config: config, limits: limits, observer: observer), expected = c["expected"] as! [String: Any]
                XCTAssertEqual(r.termination, expected["termination"] as! String, name)
                XCTAssertEqual(r.states, expected["states"] as! Int, name)
                XCTAssertEqual(r.transitions, (expected["transitions"] as! NSNumber).uint64Value, name)
                XCTAssertEqual(r.maxDepthReached, expected["max_depth_reached"] as! Int, name)
                XCTAssertEqual(r.skippedChecks, (expected["skipped_checks"] as! NSNumber).uint64Value, name)
                XCTAssertEqual(r.estimatedRetainedStateBytes.map(String.init), expected["estimated_retained_state_bytes"] as? String, name)
                XCTAssertEqual(r.failure?.inputs, expected["witness"] as? [Int], name)
                XCTAssertEqual(r.failure?.violations.first?.phase, expected["failed_phase"] as? String, name)
                XCTAssertEqual(r.failure?.violations.first?.check.id, expected["failed_id"] as? String, name)
            }
            XCTAssertEqual(events, c["events"] as! [String], name)
        }
    }
    func testAdmittedByteTotalsAndInitialCap() throws {
        let model = bfsCounter()
        let normal = try enumerateStates(model)
        XCTAssertEqual(normal.states, 3); XCTAssertEqual(normal.transitions, 6); XCTAssertEqual(normal.estimatedRetainedStateBytes, 15)
        XCTAssertEqual(normal.failure?.inputs, [1, 1, 1])
        let capped = try enumerateStates(model, limits: SearchLimits(maxEstimatedStateBytes: 14))
        XCTAssertEqual(capped.termination, "estimated_state_byte_limit"); XCTAssertEqual(capped.states, 2)
        XCTAssertEqual(capped.transitions, 4); XCTAssertEqual(capped.estimatedRetainedStateBytes, 10)
        let exact = try enumerateStates(model, limits: SearchLimits(maxEstimatedStateBytes: 15))
        XCTAssertEqual(exact.termination, "failure_found"); XCTAssertEqual(exact.estimatedRetainedStateBytes, 15)
        let small = try enumerateStates(model, limits: SearchLimits(maxEstimatedStateBytes: 4))
        XCTAssertEqual(small.states, 0); XCTAssertEqual(small.transitions, 0); XCTAssertEqual(small.estimatedRetainedStateBytes, 0)
    }
    func testDetachedDiamondHistoriesAndOptionalHistory() throws {
        let model = Model<BFSBox, BFSBox, BFSBox>(initialState: { BFSBox(0) }, step: { _, i in Transition(state: BFSBox(i.n), outputs: [BFSBox(i.n)]) }, checkState: { _ in [.passed("ok")] }, cloneState: { BFSBox($0.n) }, cloneInput: { BFSBox($0.n) }, cloneOutput: { BFSBox($0.n) }, inputs: { s in InputIterator(values: (s.n == 0 ? [1, 2] : s.n == 1 || s.n == 2 ? [3] : []).map(BFSBox.init)) }, equalStates: { $0.n == $1.n })
        var histories: [[Int]] = []
        let observer = SearchObserver<BFSBox, BFSBox, BFSBox>(initial: { state, _ in state.n = 100 }, transition: { before, input, t, _, _, history in
            histories.append(history.map(\.n)); before.n = 100; input.n = 100; t.state.n = 100
            for output in t.outputs { output.n = 100 }; for input in history { input.n = 100 }
        }, needsHistory: { true })
        let r = try enumerateObserved(model, observer: observer)
        XCTAssertEqual(r.termination, "graph_exhausted"); XCTAssertEqual(r.states, 4); XCTAssertEqual(r.transitions, 4)
        XCTAssertEqual(histories, [[1], [2], [1, 3], [2, 3]])
        var lengths: [Int] = []
        _ = try enumerateObserved(model, observer: SearchObserver(transition: { _, _, _, _, _, history in lengths.append(history.count) }))
        XCTAssertEqual(lengths, [0, 0, 0, 0])
    }
    func testPrimaryCheckerErrorAndValidatedPrefix() throws {
        var model = bfsCounter(), batches: [String] = []
        model.checkTransition = { _, _, _ in [Check.passed("prefix"), Check("invalid", .passed, "illegal")] }
        let observer = SearchObserver<Int, Int, Int>(checkError: { phase, checks, complete in
            batches.append("\(phase):\(complete):\(checks.map(\.id).joined(separator: ","))")
            throw ModelError("secondary observer error")
        })
        XCTAssertThrowsError(try enumerateObserved(model, observer: observer)) { error in
            let text = String(describing: error); XCTAssertTrue(text.contains("passing checks have no details")); XCTAssertTrue(text.contains("secondary observer error"))
        }
        XCTAssertEqual(batches, ["state:true:ok", "transition:false:prefix"])
    }
    func testRealDeadlineDuringChecksPreservesFailure() throws {
        var model = bfsCounter()
        model.checkState = { _ in Thread.sleep(forTimeInterval: 1.01); return [.failed("deadline-boundary", "broken")] }
        let report = try enumerateStates(model, limits: SearchLimits(control: RunLimits(maxDuration: 1)))
        XCTAssertEqual(report.termination, "failure_found"); XCTAssertEqual(report.states, 1)
        XCTAssertNil(report.estimatedRetainedStateBytes)
    }
    func testOracleNativePaddingUnknownOverflowAndPairedForwarding() throws {
        func tiny<T: Equatable>(_ initial: T, _ estimate: UInt64?) -> Model<T, Int, Int> {
            Model(initialState: { initial }, step: { s, _ in Transition(state: s) }, checkState: { _ in [.passed("ok")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: []) }, equalStates: ==, estimatedStateBytes: { _ in estimate })
        }
        var oracle = Oracle<UInt8, UInt64, Int, Int>(metadata: { Metadata(name: "history") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { $0 }, equalHistory: ==, estimatedStateBytes: { _ in 20 })
        let wrapped = WithOracle(tiny(UInt8(0), 10), oracle: oracle)
        let padding = try XCTUnwrap(wrapped.estimatedWrapperBytes)
        XCTAssertEqual(padding, UInt64(max(0, MemoryLayout<OracleState<UInt8, UInt64>>.size - MemoryLayout<UInt8>.size - MemoryLayout<UInt64>.size)))
        let report = try enumerateStates(wrapped.model)
        XCTAssertEqual(report.estimatedRetainedStateBytes, 30 + padding)
        XCTAssertEqual(try enumerateStates(wrapped.model, limits: SearchLimits(maxEstimatedStateBytes: 30 + padding)).states, 1)
        oracle.estimatedStateBytes = { _ in nil }
        XCTAssertNil(try enumerateStates(WithOracle(tiny(UInt8(0), 10), oracle: oracle).model).estimatedRetainedStateBytes)
        oracle.estimatedStateBytes = { _ in 1 }
        XCTAssertNil(try enumerateStates(WithOracle(tiny(UInt8(0), UInt64.max), oracle: oracle).model).estimatedRetainedStateBytes)
        let reference = tiny(UInt64(0), 12)
        let projection = Projection<UInt8, UInt64, Int, Int, UInt64, Int>(metadata: { Metadata(name: "projection") }, actualState: { UInt64($0) }, referenceState: { $0 }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: ==, equalEffect: ==)
        let paired = Paired(reference: reference, projection: projection)
        XCTAssertEqual(try paired.oracle.estimatedStateBytes?(0), 12)
        let pairedWrapper = WithOracle(tiny(UInt8(0), 3), oracle: paired.oracle)
        XCTAssertEqual(try enumerateStates(pairedWrapper.model).estimatedRetainedStateBytes, 15 + (pairedWrapper.estimatedWrapperBytes ?? 0))
    }
    func testPromotedConformanceProjectionStaysStable() throws {
        let model = Model<String, String, String>(initialState: { "0" }, step: { state, _ in Transition(state: state) }, checkState: { _ in [.passed("ok")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: []) }, equalStates: ==, estimatedStateBytes: { _ in 42 })
        XCTAssertEqual(try enumerateStates(model).estimatedRetainedStateBytes, 42)
        let reply = try ExecutionBackend.independent.enumerate(model, SearchConfig())
        let fields = try objectFields(reply, required: ["termination", "states", "transitions", "max_depth_reached", "skipped_checks", "failure"])
        XCTAssertEqual(fields.count, 6)
        XCTAssertFalse(conformanceProfiles.contains("bfs-controls-v1"))
    }
}
