import XCTest
@testable import Statelessness

final class StatefulLazyInputTests: XCTestCase {
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }
    func child(_ inputs: @escaping (Box) throws -> InputIterator<Int>) -> Model<Box,Int,Int> {
        Model(initialState:{Box(0)},step:{ s,i in Transition(state:Box(s.value+i),outputs:[]) },checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{$0},cloneOutput:{$0},inputs:inputs,equalStates:{$0.value == $1.value})
    }
    func testInfiniteChildEnumerationRespectsSearchTransitionLimit() throws {
        var pulls = 0
        let m = child { _ in InputIterator { pulls += 1; if pulls > 10 { throw ModelError("eagerly drained unbounded iterator") }; return 1 } }
        let pair = try Pair(left:m,right:m,wiring:PairWiring<Box,Box,Int,Int,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let pairInputs = try pair.inputs(pair.initialState()); XCTAssertEqual(pulls,0)
        guard case .local(.left(1))? = try pairInputs.next() else { return XCTFail("wrong first pair input") }
        XCTAssertEqual(pulls,1); pulls = 0
        let result = try enumerateStates(pair.model(equalLeftInput:==,equalRightInput:==),config:SearchConfig(maxStates:10,maxTransitions:2,maxDepth:4))
        XCTAssertEqual(result.termination,"transition_limit"); XCTAssertEqual(result.transitions,2); XCTAssertEqual(pulls,3)
        pulls = 0
        let network = try Network(machines:[NetworkAddress(1,m)],wiring:NetworkWiring<Box,Int,Int>(routes:{_ in []},checkState:{_,_ in}))
        let networkInputs = try network.inputs(network.initialState()); XCTAssertEqual(pulls,0)
        guard case let .local(address)? = try networkInputs.next() else { return XCTFail("wrong first network input") }
        XCTAssertEqual(address.machine,1); XCTAssertEqual(address.value,1); XCTAssertEqual(pulls,1); pulls = 0
        let result2 = try enumerateStates(network.model(equalInput:==),config:SearchConfig(maxStates:10,maxTransitions:2,maxDepth:4))
        XCTAssertEqual(result2.termination,"transition_limit"); XCTAssertEqual(result2.transitions,2); XCTAssertEqual(pulls,3)
    }
    func testInputOrderAndSnapshotIsolation() throws {
        let m = child { state in InputIterator(values:[state.value,state.value+10]) }
        let pair = try Pair(left:m,right:m,wiring:PairWiring<Box,Box,Int,Int,Int,Int>(messages:{_ in []},checkState:{_,_ in}),maxPending:2)
        var pairState = PairState<Box,Box,Int,Int>(left:Box(4),right:Box(7),pending:[.left(99)])
        let pairInputs = try pair.inputs(pairState)
        pairState.left.value = 40; pairState.right.value = 70; pairState.pending.removeAll()
        var pairSeen: [String] = []
        while let next = try pairInputs.next() { switch next { case let .local(.left(v)): pairSeen.append("L\(v)"); case let .local(.right(v)): pairSeen.append("R\(v)"); case let .deliver(i): pairSeen.append("D\(i)") } }
        XCTAssertEqual(pairSeen,["L4","L14","R7","R17","D0"])
        let network = try Network(machines:[NetworkAddress(2,m),NetworkAddress(1,m)],wiring:NetworkWiring<Box,Int,Int>(routes:{_ in []},checkState:{_,_ in}),policy:QueuePolicy(maxPending:2,maxPerChannel:2,allowDrop:true,allowDuplicate:true))
        var state = NetworkState(machines:[NetworkAddress(1,Box(4)),NetworkAddress(2,Box(7))],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:2,channel:3,input:99))])
        let iterator = try network.inputs(state)
        state.machines[0].value.value = 40; state.machines[1].value.value = 70; state.pending.removeAll()
        var seen: [String] = []
        while let next = try iterator.next() { switch next { case let .local(a): seen.append("L\(a.machine):\(a.value)"); case let .deliver(i): seen.append("D\(i)"); case let .drop(i): seen.append("X\(i)"); case let .duplicate(i): seen.append("U\(i)") } }
        XCTAssertEqual(seen,["L1:4","L1:14","L2:7","L2:17","D0","X0","U0"])
    }
    func testNetworkLazyInputsRespectChannelSpecificLimitsAndFIFO() throws {
        let m = child { _ in InputIterator(values:[]) }
        let network = try Network(machines:[NetworkAddress(1,m)],wiring:NetworkWiring<Box,Int,Int>(routes:{_ in []},checkState:{_,_ in}),policy:QueuePolicy(maxPending:5,maxPerChannel:2,allowDrop:true,allowDuplicate:true))
        func message(_ channel: UInt32) -> NetworkMessage<Int> { NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:channel,input:0)) }
        let state = NetworkState(machines:[NetworkAddress(1,Box(0))],pending:[message(3),message(3),message(4)])
        let iterator = try network.inputs(state); var actual: [String] = []
        while let input = try iterator.next() { switch input { case let .deliver(i): actual.append("D\(i)"); case let .drop(i): actual.append("X\(i)"); case let .duplicate(i): actual.append("U\(i)"); case .local: XCTFail("unexpected local input") } }
        XCTAssertEqual(actual,["D0","X0","X1","D2","X2","U2"])
    }
    func testLaterChildFactoryErrorIsLazyAndVisible() throws {
        let left = child { _ in InputIterator(values:[1]) }
        let right = child { _ in throw ModelError("right enumeration failed") }
        let pair = try Pair(left:left,right:right,wiring:PairWiring<Box,Box,Int,Int,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let p = try pair.inputs(pair.initialState())
        guard case .local(.left(1))? = try p.next() else { return XCTFail("missing first input") }
        XCTAssertThrowsError(try p.next()) { XCTAssertTrue(String(describing:$0).contains("right enumeration failed")) }
        let network = try Network(machines:[NetworkAddress(1,left),NetworkAddress(2,right)],wiring:NetworkWiring<Box,Int,Int>(routes:{_ in []},checkState:{_,_ in}))
        let n = try network.inputs(network.initialState())
        guard case .local? = try n.next() else { return XCTFail("missing first input") }
        XCTAssertThrowsError(try n.next()) { XCTAssertTrue(String(describing:$0).contains("right enumeration failed")) }
    }
    func testCompositionEqualityCallbacksReceiveDetachedValues() throws {
        let child = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{Box($0.value)},cloneOutput:{$0},equalStates:{a,b in a.value = 90; b.value = 91; return true})
        let equalInputs: (Box,Box) -> Bool = { a,b in a.value = 80; b.value = 81; return true }
        let pair = try Pair(left:child,right:child,wiring:PairWiring<Box,Box,Box,Box,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let left = PairState<Box,Box,Box,Box>(left:Box(1),right:Box(2),pending:[.left(Box(3)),.right(Box(4))])
        let right = PairState<Box,Box,Box,Box>(left:Box(1),right:Box(2),pending:[.left(Box(3)),.right(Box(4))])
        let pairEqual = try XCTUnwrap(pair.model(equalLeftInput:equalInputs,equalRightInput:equalInputs).equalStates)
        XCTAssertTrue(try pairEqual(left,right)); XCTAssertEqual(left.left.value,1); XCTAssertEqual(right.right.value,2)
        guard case let .left(first) = left.pending[0],case let .right(second) = right.pending[1] else { return XCTFail("unexpected pair inputs") }
        XCTAssertEqual(first.value,3); XCTAssertEqual(second.value,4)

        let network = try Network(machines:[NetworkAddress(1,child)],wiring:NetworkWiring<Box,Box,Int>(routes:{_ in []},checkState:{_,_ in}))
        let a = NetworkState(machines:[NetworkAddress(1,Box(5))],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:0,input:Box(6)))])
        let b = NetworkState(machines:[NetworkAddress(1,Box(5))],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:0,input:Box(6)))])
        let networkEqual = try XCTUnwrap(network.model(equalInput:equalInputs).equalStates)
        XCTAssertTrue(try networkEqual(a,b)); XCTAssertEqual(a.machines[0].value.value,5); XCTAssertEqual(b.machines[0].value.value,5)
        XCTAssertEqual(a.pending[0].route.input.value,6); XCTAssertEqual(b.pending[0].route.input.value,6)
    }
    func testPairEqualityCapturesLaterValuesBeforeLeftCallback() throws {
        let later = Box(2), queued = Box(3)
        let left = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{Box($0.value)},cloneOutput:{$0},equalStates:{_,_ in later.value = 99; queued.value = 99; return true})
        let right = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{Box($0.value)},cloneOutput:{$0},equalStates:{$0.value == $1.value})
        let pair = try Pair(left:left,right:right,wiring:PairWiring<Box,Box,Box,Box,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let a = PairState<Box,Box,Box,Box>(left:Box(1),right:later,pending:[.left(queued)])
        let b = PairState<Box,Box,Box,Box>(left:Box(1),right:Box(2),pending:[.left(Box(3))])
        let equal = try XCTUnwrap(pair.model(equalLeftInput:{$0.value == $1.value},equalRightInput:{$0.value == $1.value}).equalStates)
        XCTAssertTrue(try equal(a,b))
    }
    func testPairEqualityDefersUnusedPendingCloneError() throws {
        let child = Model<Box,Box,Int>(initialState:{Box(0)},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{Box($0.value)},cloneInput:{_ in throw ModelError("pending clone")},cloneOutput:{$0},equalStates:{$0.value == $1.value})
        let pair = try Pair(left:child,right:child,wiring:PairWiring<Box,Box,Box,Box,Int,Int>(messages:{_ in []},checkState:{_,_ in}))
        let a = PairState<Box,Box,Box,Box>(left:Box(1),right:Box(2),pending:[.left(Box(3))])
        let b = PairState<Box,Box,Box,Box>(left:Box(9),right:Box(2),pending:[.left(Box(3))])
        let equal = try XCTUnwrap(pair.model(equalLeftInput:{$0.value == $1.value},equalRightInput:{$0.value == $1.value}).equalStates)
        XCTAssertFalse(try equal(a,b))
        XCTAssertThrowsError(try equal(a,a)) { error in XCTAssertTrue((error as? ModelError)?.snapshotFailure == true) }
    }
}
