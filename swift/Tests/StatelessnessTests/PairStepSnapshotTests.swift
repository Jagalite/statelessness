import XCTest
@testable import Statelessness

final class PairStepSnapshotTests: XCTestCase {
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }

    func testCloneStateCapturesPendingBeforeChildStateHooks() throws {
        let queued = Box(7)
        let child = Model<Int,Box,Int>(initialState:{0},step:{s,_ in Transition(state:s)},
            checkState:{_ in []},cloneState:{s in queued.value = 99; return s},
            cloneInput:{Box($0.value)},cloneOutput:{$0})
        var observed = 0
        let pair = try Pair(left:child,right:child,wiring:PairWiring<Int,Int,Box,Box,Int,Int>(
            messages:{_ in []},checkState:{s,_ in
                if case let .left(value) = s.pending[0] { observed = value.value }
            }))
        let state = PairState<Int,Int,Box,Box>(left:0,right:0,pending:[.left(queued)])
        let snapshot = try pair.cloneState(state)
        guard case let .left(value) = snapshot.pending[0] else { return XCTFail("address changed") }
        XCTAssertEqual(value.value,7)
        queued.value = 7
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try pair.checkState(state,into:$0) }
        XCTAssertEqual(observed,7)
    }

    func testStepCapturesSelectedInputAndQueueBeforeStateClones() throws {
        let selected = Box(7), first = Box(11), second = Box(12)
        var seen = 0
        let child = Model<Int,Box,Int>(initialState:{0},step:{ state,input in
            seen = input.value; return Transition(state:state)
        },checkState:{_ in []},cloneState:{ state in
            selected.value = 90; first.value = 91; second.value = 92; return state
        },cloneInput:{Box($0.value)},cloneOutput:{$0})
        let pair = try Pair(left:child,right:child,wiring:PairWiring<Int,Int,Box,Box,Int,Int>(messages:{_ in []},checkState:{_,_ in}),maxPending:4)
        let state = PairState<Int,Int,Box,Box>(left:0,right:0,pending:[.left(first),.right(second)])
        let local = try pair.step(state,.local(.left(selected)))
        XCTAssertEqual(seen,7)
        guard case let .left(l) = local.state.pending[0], case let .right(r) = local.state.pending[1] else { return XCTFail("addresses changed") }
        XCTAssertEqual([l.value,r.value],[11,12])

        first.value = 11; second.value = 12
        let delivered = try pair.step(state,.deliver(0))
        XCTAssertEqual(seen,11)
        guard case let .right(remaining) = delivered.state.pending[0] else { return XCTFail("address changed") }
        XCTAssertEqual(remaining.value,12)
    }
}
