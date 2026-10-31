import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class ProtocolEventStreamTests: XCTestCase {
    func make(_ limits: ProtocolLimits = ProtocolLimits()) throws -> ProtocolAdapter<Int,Int,Int> {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        return try ProtocolAdapter(session:session,authority:authority,limits:limits,decodeInput:{Int($0.first ?? 0)})
    }
    func request(_ p: ProtocolAdapter<Int,Int,Int>,_ id: UInt64,_ command: String,_ args: [String] = [],revision: UInt64 = 0) -> ProtocolRequest {
        ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,expectedRevision:revision,command:command,arguments:args)
    }
    func testEventPollPreflightAndDeduplication() throws {
        var limits = ProtocolLimits(); limits.maxResponseFields = 9
        let p = try make(limits)
        XCTAssertNil(p.handle(request(p,1,"step",["bytes:01"])).error)
        XCTAssertNil(p.handle(request(p,2,"step",["bytes:01"],revision:1)).error)
        let tooLarge = request(p,3,"events",["u64:2"])
        XCTAssertEqual(p.handle(tooLarge).error,.limit); XCTAssertEqual(p.handle(tooLarge).error,.limit)
        let one = request(p,4,"events",["u64:1"]), first = p.handle(one)
        XCTAssertNil(first.error); XCTAssertEqual(first.fields["events.0.kind"],"enum:transition"); XCTAssertEqual(first.fields["events.0.sequence"],"u64:1")
        XCTAssertEqual(p.handle(one),first)
        let second = p.handle(request(p,5,"events",["u64:1"]))
        XCTAssertNil(second.error); XCTAssertEqual(second.fields["events.0.sequence"],"u64:2"); XCTAssertEqual(second.fields["events.0.revision"],"u64:2")
        XCTAssertEqual(p.handle(request(p,6,"events",["u64:1"])).fields["events.returned"],"u64:0")
    }
    func testBoundedLossAndReconnectPreserveDropCount() throws {
        var limits = ProtocolLimits(); limits.maxEventEntries = 1
        let p = try make(limits)
        XCTAssertNil(p.handle(request(p,1,"step",["bytes:01"])).error)
        XCTAssertNil(p.handle(request(p,2,"step",["bytes:01"],revision:1)).error)
        let status = p.handle(request(p,3,"status")); XCTAssertEqual(status.fields["events.dropped"],"u64:1")
        let first = p.handle(request(p,4,"events",["u64:1"]))
        XCTAssertEqual(first.fields["events.dropped"],"u64:1"); XCTAssertEqual(first.fields["events.0.sequence"],"u64:1")
        let oldEpoch = p.epoch; try p.disconnect(); _ = try p.reconnect()
        let after = p.handle(request(p,1,"events",["u64:1"]))
        XCTAssertNil(after.error); XCTAssertEqual(after.fields["events.dropped"],"u64:1")
        XCTAssertEqual(after.fields["events.0.kind"],"enum:reconnected"); XCTAssertEqual(after.fields["events.0.sequence"],"u64:1")
        XCTAssertNotEqual(p.epoch,oldEpoch); XCTAssertEqual(after.fields["events.0.epoch"],"u64:\(p.epoch)")
    }
}
