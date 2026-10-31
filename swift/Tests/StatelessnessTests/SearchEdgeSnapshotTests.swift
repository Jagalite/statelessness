import XCTest
@testable import Statelessness

private final class EdgeBox { var value: Int; init(_ value: Int) { self.value = value } }

final class SearchEdgeSnapshotTests: XCTestCase {
    func testInitialCheckCannotChangeObserverState() throws {
        let initial = EdgeBox(1)
        var observed = 0
        let model = Model<EdgeBox,Int,Int>(initialState: { initial }, step: { s,_ in Transition(state:s) },
            checkState: { _ in initial.value = 9; return [] }, cloneState: { EdgeBox($0.value) },
            cloneInput: { $0 }, cloneOutput: { $0 })
        let observer = SearchObserver<EdgeBox,Int,Int>(initial: { state,_ in observed = state.value })
        var skipped: UInt64 = 0
        _ = try searchInitialChecks(model,state:initial,skipped:&skipped,observer:observer)
        XCTAssertEqual(observed,1)
    }

    func testInitialCheckErrorPrecedesObserverCaptureFailure() throws {
        var copies = 0
        let model = Model<Int,Int,Int>(initialState: { 0 }, step: { s,_ in Transition(state:s) },
            checkState: { _ in throw ModelError("state failure") }, cloneState: { value in
                copies += 1; if copies == 2 { throw ModelError("observer capture") }; return value
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        var skipped: UInt64 = 0
        XCTAssertThrowsError(try searchInitialChecks(model,state:0,skipped:&skipped,observer:SearchObserver<Int,Int,Int>())) {
            XCTAssertTrue(String(describing:$0).contains("state failure"))
        }
    }

    func testObserverCaptureCannotChangeInitialCheckState() throws {
        let initial = EdgeBox(1)
        var captures = 0, checked = 0
        let model = Model<EdgeBox,Int,Int>(initialState: { initial }, step: { s,_ in Transition(state:s) },
            checkState: { checked = $0.value; return [] }, cloneState: { value in
                captures += 1
                let result = EdgeBox(value.value)
                if captures == 2 { initial.value = 9 }
                return result
            }, cloneInput: { $0 }, cloneOutput: { $0 })
        var skipped: UInt64 = 0
        _ = try searchInitialChecks(model,state:initial,skipped:&skipped,observer:SearchObserver<EdgeBox,Int,Int>())
        XCTAssertEqual(checked,1)
    }

    func testStateCheckCannotChangeLaterTransitionObservation() throws {
        let before = EdgeBox(1), input = EdgeBox(2), after = EdgeBox(3), output = EdgeBox(4)
        var observed: [Int] = []
        let model = Model<EdgeBox,EdgeBox,EdgeBox>(
            initialState: { before }, step: { s,_ in Transition(state:s) },
            checkState: { _ in before.value = 11; input.value = 12; after.value = 13; output.value = 14; return [] },
            cloneState: { EdgeBox($0.value) }, cloneInput: { EdgeBox($0.value) }, cloneOutput: { EdgeBox($0.value) },
            checkTransition: { b,i,t in observed = [b.value,i.value,t.state.value,t.outputs[0].value]; return [] })
        var skipped: UInt64 = 0
        _ = try searchEdge(model, state:before, input:input, transition:Transition(state:after,outputs:[output]), skipped:&skipped)
        XCTAssertEqual(observed,[1,2,3,4])
    }

    func testStateCheckErrorPrecedesCapturedEdgeFailure() throws {
        let model = Model<Int,Int,Int>(initialState: { 0 }, step: { s,_ in Transition(state:s) },
            checkState: { _ in throw ModelError("state failure") }, cloneState: { $0 },
            cloneInput: { _ in throw ModelError("input copy failure") }, cloneOutput: { $0 },
            checkTransition: { _,_,_ in [] })
        var skipped: UInt64 = 0
        XCTAssertThrowsError(try searchEdge(model,state:0,input:1,transition:Transition(state:1),skipped:&skipped)) {
            XCTAssertTrue(String(describing:$0).contains("state failure"))
        }
    }
    func testHistoryCopyFailureDoesNotPreemptTransitionCheck() throws {
        var calls = 0
        let model = Model<Int,Int,Int>(initialState: { 0 }, step: { s,_ in Transition(state:s) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { value in if value == 99 { throw ModelError("history copy") }; return value },
            cloneOutput: { $0 }, checkTransition: { _,_,_ in calls += 1; throw ModelError("edge failure") })
        var skipped: UInt64 = 0
        let observer = SearchObserver<Int,Int,Int>()
        XCTAssertThrowsError(try searchEdge(model,state:0,input:1,transition:Transition(state:1),skipped:&skipped,observer:observer,history:[99])) {
            XCTAssertTrue(String(describing:$0).contains("edge failure"))
        }
        XCTAssertEqual(calls,1)
    }

    func testObserverOnlyCopyFailureIsNotReportedAsCheckFailure() throws {
        var notifications = 0
        let model = Model<Int,Int,Int>(initialState: { 0 }, step: { s,_ in Transition(state:s) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { _ in throw ModelError("input copy") }, cloneOutput: { $0 })
        var skipped: UInt64 = 0
        let observer = SearchObserver<Int,Int,Int>(checkError: { _,_,_ in notifications += 1 })
        XCTAssertThrowsError(try searchEdge(model,state:0,input:1,transition:Transition(state:1),skipped:&skipped,observer:observer))
        XCTAssertEqual(notifications,0)
    }

}
