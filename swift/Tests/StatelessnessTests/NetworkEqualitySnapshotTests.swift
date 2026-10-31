import XCTest
@testable import Statelessness

final class NetworkEqualitySnapshotTests: XCTestCase {
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }
    func testQueueCapturedBeforeMachineClone() throws {
        let queued = Box(7)
        let child = Model<Int,Box,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{ value in queued.value = 99; return value },cloneInput:{ Box($0.value) },cloneOutput:{$0},equalStates:{$0 == $1})
        let wiring = NetworkWiring<Int,Box,Int>(routes:{_ in []},checkState:{_,_ in})
        let network = try Network(machines:[NetworkAddress(1,child)],wiring:wiring)
        let first = NetworkState(machines:[NetworkAddress(1,0)],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:0,input:queued))])
        let second = NetworkState(machines:[NetworkAddress(1,0)],pending:[NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:0,input:Box(7)))])
        let model = network.model(equalInput:{$0.value == $1.value})
        XCTAssertTrue(try model.equalStates!(first,second))
        XCTAssertEqual(queued.value,99)
    }
}
