import XCTest
@testable import Statelessness

final class NetworkStepSnapshotTests: XCTestCase {
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }

    func testCloneStateCapturesQueueBeforeMachineClone() throws {
        let pending = Box(7)
        let child = Model<Int,Box,Int>(initialState:{0},step:{ state,_ in Transition(state:state) },
            checkState:{_ in []},cloneState:{ state in pending.value = 99; return state },
            cloneInput:{Box($0.value)},cloneOutput:{$0})
        let network = try Network(machines:[NetworkAddress(1,child)],wiring:NetworkWiring<Int,Box,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState(machines:[NetworkAddress(1,0)],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:1,input:pending))])
        let snapshot = try network.cloneState(state)
        XCTAssertEqual(snapshot.pending[0].route.input.value,7)
        XCTAssertEqual(pending.value,99)
    }

    func testStepCapturesInputAndEntireQueueBeforeStateClone() throws {
        let selected = Box(7), first = Box(11), second = Box(12)
        var seen = 0
        let child = Model<Int,Box,Int>(initialState:{0},step:{ state,input in
            seen = input.value; return Transition(state:state)
        },checkState:{_ in []},cloneState:{ state in
            selected.value = 90; first.value = 91; second.value = 92; return state
        },cloneInput:{Box($0.value)},cloneOutput:{$0})
        let network = try Network(machines:[NetworkAddress(1,child)],wiring:NetworkWiring<Int,Box,Int>(routes:{_ in []},checkState:{_,_ in}),policy:QueuePolicy(maxPending:4,maxPerChannel:4,allowDuplicate:true))
        let state = NetworkState(machines:[NetworkAddress(1,0)],pending:[
            NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:1,input:first)),
            NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:1,input:second))
        ])
        let local = try network.step(state,.local(NetworkAddress(1,selected)))
        XCTAssertEqual(seen,7)
        XCTAssertEqual(local.state.pending.map { $0.route.input.value },[11,12])

        first.value = 11; second.value = 12
        let duplicate = try network.step(state,.duplicate(0))
        XCTAssertEqual(duplicate.state.pending.map { $0.route.input.value },[11,12,11])
    }
}
