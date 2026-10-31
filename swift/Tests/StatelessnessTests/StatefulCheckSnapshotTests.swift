import XCTest
@testable import Statelessness

final class StatefulCheckSnapshotTests: XCTestCase {
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }
    func child(onState: @escaping () -> Void = {}, onTransition: @escaping () -> Void = {}) -> Model<Box,Int,Int> {
        Model(initialState:{ Box(0) },step:{s,_ in Transition(state:Box(s.value))},checkState:{_ in onState(); return []},cloneState:{Box($0.value)},cloneInput:{$0},cloneOutput:{$0},checkTransition:{_,_,_ in onTransition(); return []})
    }
    func testPairChecksCaptureBeforeChildCallbacks() throws {
        let before = PairState<Box,Box,Int,Int>(left:Box(1),right:Box(2)), after = PairState<Box,Box,Int,Int>(left:Box(3),right:Box(4))
        var stateSeen = 0, transitionSeen = 0
        let left = child(onState:{before.right.value = 99},onTransition:{before.right.value = 88; after.right.value = 77})
        let pair = try Pair(left:left,right:child(),wiring:PairWiring<Box,Box,Int,Int,Int,Int>(messages:{_ in []},checkState:{s,_ in stateSeen = s.right.value},checkTransition:{s,_,t,_ in transitionSeen = s.right.value * 10 + t.state.right.value}))
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try pair.checkState(before,into:$0) }
        XCTAssertEqual(stateSeen,2)
        before.right.value = 2
        try CheckSink.withStorage(&checks) { try pair.checkTransition(before,.local(.left(0)),Transition(state:after),into:$0) }
        XCTAssertEqual(transitionSeen,24)
    }
    func testPairTransitionCapturesInputBeforeStateClone() throws {
        let query = Box(7)
        var observed = -1
        let left = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{ state in query.value = 99; return Box(state.value) },cloneInput:{Box($0.value)},cloneOutput:{$0},checkTransition:{_,input,_ in observed = input.value; return []})
        let right = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{Box($0.value)},cloneOutput:{$0})
        let pair = try Pair(left:left,right:right,wiring:PairWiring<Box,Box,Box,Box,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let state = PairState<Box,Box,Box,Box>(left:Box(1),right:Box(2))
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try pair.checkTransition(state,.local(.left(query)),Transition(state:state),into:$0) }
        XCTAssertEqual(observed,7)
    }
    func testPairTransitionRejectsWrongOutputBeforeCloneHooks() throws {
        var copies = 0
        let left = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{ copies += 1; return $0 },cloneInput:{ copies += 1; return $0 },cloneOutput:{ copies += 1; return $0 })
        let pair = try Pair(left:left,right:left,wiring:PairWiring<Int,Int,Int,Int,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let state = PairState<Int,Int,Int,Int>(left:1,right:2)
        var checks: [Check] = []
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try pair.checkTransition(state,.local(.left(1)),Transition(state:state,outputs:[.right(2)]),into:$0) })
        XCTAssertEqual(copies,0)
    }
    func testPairTransitionMapsOutputsBeforeLaterWrongAddress() throws {
        var copies = 0
        let child = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{ _ in copies += 1; throw ModelError("first output copy") })
        let pair = try Pair(left:child,right:child,wiring:PairWiring<Int,Int,Int,Int,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let state = PairState<Int,Int,Int,Int>(left:1,right:2)
        var checks: [Check] = []
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try pair.checkTransition(state,.local(.left(1)),Transition(state:state,outputs:[.left(10),.right(20)]),into:$0) }) { error in
            XCTAssertTrue(String(describing:error).contains("first output copy"))
        }
        XCTAssertEqual(copies,1)
        copies = 0
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try pair.checkTransition(state,.local(.left(1)),Transition(state:state,outputs:[.right(20),.left(10)]),into:$0) }) { error in
            XCTAssertTrue(String(describing:error).contains("wrong child output address"))
        }
        XCTAssertEqual(copies,0)
    }
    func testNetworkChecksCaptureBeforeChildCallbacks() throws {
        let before = NetworkState<Box,Int>(machines:[NetworkAddress(1,Box(1)),NetworkAddress(2,Box(2))]), after = NetworkState<Box,Int>(machines:[NetworkAddress(1,Box(3)),NetworkAddress(2,Box(4))])
        var stateSeen = 0, transitionSeen = 0
        let first = child(onState:{before.machines[1].value.value = 99},onTransition:{before.machines[1].value.value = 88; after.machines[1].value.value = 77})
        let network = try Network(machines:[NetworkAddress(1,first),NetworkAddress(2,child())],wiring:NetworkWiring<Box,Int,Int>(routes:{_ in []},checkState:{s,_ in stateSeen = s.machines[1].value.value},checkTransition:{s,_,t,_ in transitionSeen = s.machines[1].value.value * 10 + t.state.machines[1].value.value}))
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try network.checkState(before,into:$0) }
        XCTAssertEqual(stateSeen,2)
        before.machines[1].value.value = 2
        try CheckSink.withStorage(&checks) { try network.checkTransition(before,.local(NetworkAddress(1,0)),Transition(state:after),into:$0) }
        XCTAssertEqual(transitionSeen,24)
    }
    func testNetworkTransitionCapturesInputBeforeStateClone() throws {
        let query = Box(7)
        var observed = -1
        let machine = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{ state in query.value = 99; return Box(state.value) },cloneInput:{Box($0.value)},cloneOutput:{$0},checkTransition:{_,input,_ in observed = input.value; return []})
        let network = try Network(machines:[NetworkAddress(1,machine)],wiring:NetworkWiring<Box,Box,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState<Box,Box>(machines:[NetworkAddress(1,Box(1))])
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,query)),Transition(state:state),into:$0) }
        XCTAssertEqual(observed,7)
    }
    func testNetworkTransitionRejectsWrongOutputBeforeCloneHooks() throws {
        var copies = 0
        let machine = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{ copies += 1; return $0 },cloneInput:{ copies += 1; return $0 },cloneOutput:{ copies += 1; return $0 })
        let network = try Network(machines:[NetworkAddress(1,machine),NetworkAddress(2,machine)],wiring:NetworkWiring<Int,Int,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState<Int,Int>(machines:[NetworkAddress(1,1),NetworkAddress(2,2)])
        var checks: [Check] = []
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,1)),Transition(state:state,outputs:[NetworkAddress(2,2)]),into:$0) })
        XCTAssertEqual(copies,0)
    }
    func testNetworkTransitionMapsOutputsInOrderBeforeLaterWrongAddress() throws {
        var copies = 0
        let machine = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{ _ in copies += 1; throw ModelError("first output copy") })
        let network = try Network(machines:[NetworkAddress(1,machine),NetworkAddress(2,machine)],wiring:NetworkWiring<Int,Int,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState<Int,Int>(machines:[NetworkAddress(1,1),NetworkAddress(2,2)])
        var checks: [Check] = []
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,1)),Transition(state:state,outputs:[NetworkAddress(1,10),NetworkAddress(2,20)]),into:$0) }) { error in
            XCTAssertTrue(String(describing:error).contains("first output copy"))
        }
        XCTAssertEqual(copies,1)
        copies = 0
        XCTAssertThrowsError(try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,1)),Transition(state:state,outputs:[NetworkAddress(2,20),NetworkAddress(1,10)]),into:$0) }) { error in
            XCTAssertTrue(String(describing:error).contains("wrong network child output address"))
        }
        XCTAssertEqual(copies,0)
    }
    func testNetworkTransitionCapturesInputBeforeOutputClone() throws {
        let query = Box(7)
        var observed = -1
        let machine = Model<Int,Box,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{Box($0.value)},cloneOutput:{ value in query.value = 99; return value },checkTransition:{_, input, _ in observed = input.value; return []})
        let network = try Network(machines:[NetworkAddress(1,machine)],wiring:NetworkWiring<Int,Box,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState<Int,Box>(machines:[NetworkAddress(1,1)])
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,query)),Transition(state:state,outputs:[NetworkAddress(1,10)]),into:$0) }
        XCTAssertEqual(observed,7)
    }
    func testNetworkTransitionDoesNotCopyMappedOutputAgainWithoutChildCheck() throws {
        var copies = 0
        let machine = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{ value in copies += 1; if copies > 1 { throw ModelError("duplicate output copy") }; return value })
        let network = try Network(machines:[NetworkAddress(1,machine)],wiring:NetworkWiring<Int,Int,Int>(routes:{_ in []},checkState:{_,_ in}))
        let state = NetworkState<Int,Int>(machines:[NetworkAddress(1,1)])
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try network.checkTransition(state,.local(NetworkAddress(1,1)),Transition(state:state,outputs:[NetworkAddress(1,10)]),into:$0) }
        XCTAssertEqual(copies,1)
    }
    func testNetworkQueueTransitionWithoutGlobalCheckDoesNotClone() throws {
        let machine = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{ _ in throw ModelError("unexpected state clone") },cloneInput:{ _ in throw ModelError("unexpected input clone") },cloneOutput:{ _ in throw ModelError("unexpected output clone") })
        let network = try Network(machines:[NetworkAddress(1,machine)],wiring:NetworkWiring<Int,Int,Int>(routes:{_ in []},checkState:{_,_ in}),policy:QueuePolicy(allowDrop:true,allowDuplicate:true))
        let state = NetworkState<Int,Int>(machines:[NetworkAddress(1,1)],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:0,input:1))])
        var checks: [Check] = []
        try CheckSink.withStorage(&checks) { try network.checkTransition(state,.drop(0),Transition(state:state),into:$0) }
        try CheckSink.withStorage(&checks) { try network.checkTransition(state,.duplicate(0),Transition(state:state),into:$0) }
        XCTAssertTrue(checks.isEmpty)
    }
}
