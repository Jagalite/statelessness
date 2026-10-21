import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class ProtocolTests: XCTestCase {
    func adapter(authority: ProtocolAuthority = ProtocolAuthority(), limits: ProtocolLimits = ProtocolLimits(), decoded: @escaping () -> Void = {}) throws -> ProtocolAdapter<Int,Int,Int> {
        let model = Model<Int,Int,Int>(initialState: {0},step: {s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        return try ProtocolAdapter(session:session,authority:authority,limits:limits,decodeInput:{ data in decoded(); return Int(data.first ?? 0) })
    }
    func request(_ a: ProtocolAdapter<Int,Int,Int>, _ id: UInt64, _ command: String, _ arguments: [String] = [], revision: UInt64 = 0) -> ProtocolRequest { ProtocolRequest(session:"counter",epoch:a.epoch,requestID:id,expectedRevision:revision,command:command,arguments:arguments) }
    func testManualWireGoldenAndFullWidth() throws {
        let r = ProtocolRequest(session:"a\tπ",epoch:UInt64.max,requestID:UInt64.max,expectedRevision:9007199254740993,command:"status")
        let expected = "DDBG\tu32:1\trequest\tstring:a%09%CF%80\tu64:18446744073709551615\tu64:18446744073709551615\tu64:9007199254740993\tu64:0\tstatus"
        XCTAssertEqual(String(data:try ProtocolWire.encodeRequest(r),encoding:.utf8),expected)
        XCTAssertEqual(try ProtocolWire.decodeRequest(Data(expected.utf8)),r)
        XCTAssertThrowsError(try ProtocolWire.quantity("u64:18446744073709551616"))
        XCTAssertThrowsError(try ProtocolWire.unescape("%FF",maximum:8))
        XCTAssertThrowsError(try ProtocolWire.unescape("<",maximum:8))
    }
    func testPartialFramesAndBoundBeforeDispatch() throws {
        let reader = try ProtocolFrameReader(maximum:16); var seen: [Data] = []
        let frame = try ProtocolWire.frame(Data("abc".utf8))
        try reader.receive(frame.prefix(5)) { seen.append($0) }; XCTAssertTrue(seen.isEmpty)
        try reader.receive(frame.suffix(2)) { seen.append($0) }; XCTAssertEqual(seen,[Data("abc".utf8)])
        try reader.finish()
        XCTAssertThrowsError(try reader.receive(Data([0,0,0,17])) { _ in XCTFail() })
        XCTAssertThrowsError(try reader.receive(frame) { _ in XCTFail() })
        let truncated = try ProtocolFrameReader(); try truncated.receive(Data([0,0])) { _ in XCTFail() }; XCTAssertThrowsError(try truncated.finish())
    }
    func testAuthorityDedupRetirementAndCallbackOrder() throws {
        var calls = 0
        let denied = try adapter(decoded:{calls += 1})
        XCTAssertEqual(denied.handle(request(denied,1,"step",["bytes:01"])).error,.unauthorized); XCTAssertEqual(calls,0)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        var limits = ProtocolLimits(); limits.maxCacheEntries = 1
        let a = try adapter(authority:authority,limits:limits,decoded:{calls += 1})
        let step = request(a,1,"step",["bytes:01"])
        let response = a.handle(step); XCTAssertNil(response.error); XCTAssertEqual(a.session.revision,1); XCTAssertEqual(calls,1)
        XCTAssertEqual(a.handle(step),response); XCTAssertEqual(calls,1)
        XCTAssertEqual(a.handle(request(a,1,"status")).error,.duplicateConflict)
        XCTAssertEqual(a.handle(request(a,2,"step",["bytes:01"])).error,.staleRevision); XCTAssertEqual(calls,1)
        XCTAssertEqual(a.handle(step).error,.retiredRequest)
        XCTAssertNil(a.handle(request(a,3,"cancel",revision:1)).error); XCTAssertEqual(a.session.phase,.ended)
    }
    func testSnapshotStalenessEpochAndIndependentAuthority() throws {
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let a = try adapter(authority:authority)
        let snapshot = a.handle(request(a,1,"snapshot")); XCTAssertEqual(snapshot.fields["handle"],"u64:1"); try a.validateHandle(1)
        XCTAssertNil(a.handle(request(a,2,"step",["bytes:01"])).error)
        XCTAssertThrowsError(try a.validateHandle(1))
        XCTAssertEqual(a.handle(request(a,3,"watch",["u64:1","u64:1"])).error,.unauthorized)
        let old = a.epoch; try a.disconnect(); XCTAssertNotEqual(try a.reconnect(),old)
        var stale = request(a,1,"status"); stale.epoch = old; XCTAssertEqual(a.handle(stale).error,.wrongEpoch)
        var discovery = request(a,1,"status"); discovery.epoch = 0; XCTAssertNil(a.handle(discovery).error)
        XCTAssertFalse(ProtocolAuthority().readExactValues); XCTAssertFalse(ProtocolAuthority().exportExactTrace)
    }
    func testTypedHostAuthorityAndConfigurationBeforeCallbacks() throws {
        let base = try adapter(); var calls = 0
        var authority = ProtocolAuthority(); authority.configureWatches = true
        let bindings = ProtocolHostBindings(commands:["watch","telemetry_health"]) { command in
            switch command { case .watch(let schema,let baseline,let path): XCTAssertEqual(schema,1); XCTAssertEqual(baseline,0); XCTAssertEqual(path,["field:count"])
            case .telemetryHealth: break
            default: XCTFail("unexpected command") }
            return PreparedProtocolResponse(fields:["host":"bool:true"]) { calls += 1 }
        }
        let a = try ProtocolAdapter(session:base.session,authority:authority,host:bindings)
        let watch = request(a,1,"watch",["u64:1","u64:0","field:count"])
        XCTAssertNil(a.handle(watch).error); XCTAssertEqual(calls,1); XCTAssertEqual(a.configuration,1)
        XCTAssertEqual(a.handle(request(a,2,"watch",watch.arguments)).error,.staleConfiguration); XCTAssertEqual(calls,1)
        XCTAssertNil(a.handle(request(a,3,"telemetry_health")).error); XCTAssertEqual(calls,2)
        XCTAssertEqual(a.handle(request(a,4,"probe_configure",["u64:0"])).error,.unauthorized); XCTAssertEqual(calls,2)
        XCTAssertEqual(a.handle(request(a,5,"metric_catalog",["u64:0","u64:101"])).error,.limit); XCTAssertEqual(calls,2)
    }

    func testBoundedOSPipeRoundTripAndPartialEOF() throws {
        let a = try adapter(); let incoming = Pipe(), outgoing = Pipe()
        let payload = try ProtocolWire.encodeRequest(request(a,1,"status"))
        try incoming.fileHandleForWriting.write(contentsOf:ProtocolWire.frame(payload)); try incoming.fileHandleForWriting.close()
        XCTAssertEqual(try a.serve(input:incoming.fileHandleForReading,output:outgoing.fileHandleForWriting,maxRequests:2),1)
        try outgoing.fileHandleForWriting.close()
        let bytes = try outgoing.fileHandleForReading.readToEnd() ?? Data(); var bodies: [Data] = []
        let reader = try ProtocolFrameReader(); try reader.receive(bytes) { bodies.append($0) }; try reader.finish()
        XCTAssertEqual(bodies.count,1)
        let response = try ProtocolWire.decodeResponse(bodies[0]); XCTAssertNil(response.error); XCTAssertEqual(response.requestID,1)
        let partial = Pipe(); try partial.fileHandleForWriting.write(contentsOf:Data([0,0,0,4,65])); try partial.fileHandleForWriting.close()
        XCTAssertThrowsError(try a.serve(input:partial.fileHandleForReading,output:FileHandle.nullDevice,maxRequests:1))
        XCTAssertEqual(a.session.sequence,0)
    }

    func testConcreteWatchProbeAndInspectionHostBindings() throws {
        let base = try adapter()
        let watches = try WatchRegistry(authorization:.localAll)
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal)
        var authority = ProtocolAuthority(); authority.configureWatches = true; authority.configureDiagnostics = true
        let host = ProtocolHostBindings(commands:["watch","probe_configure","probe_events","inspect"],exposedProbeSinks:[9],approvedProbeProfiles:[7]) { command in
            let fields: [String:String]
            switch command {
            case .watch: fields = ["watch.id":"u64:1"]
            case .probeConfigure: fields = ["capture.revision":"u64:1"]
            case .probeEvents: fields = ["events.returned":"u64:1"]
            case .inspect: fields = ["inspection.complete":"bool:true"]
            default: throw ProtocolError.unsupported
            }
            return PreparedProtocolResponse(fields:fields) {
            switch command {
            case let .watch(schema,baseline,path):
                var config = WatchConfig(); config.schemaVersion = schema
                config.path = try path.map { text in
                    guard text.hasPrefix("field:") else { throw ProtocolError.unsupported }
                    return .field(ExactString(String(text.dropFirst(6))))
                }
                let ack = try watches.add(config)
                try watches.observe(snapshot:SnapshotID(session:base.session.epoch,revision:baseline,sequence:base.session.sequence),origin:.simulation) { selected,limits in
                    let context = try InspectContext(limits:limits)
                    return InspectResult(snapshot:SnapshotID(session:base.session.epoch,revision:baseline,sequence:base.session.sequence),schema:DisplaySchema(name:"counter"),path:selected,node:try context.scalar(.integer(kind:ExactString("i64"),decimal:ExactString("0"))))
                }
                XCTAssertEqual(ack.watchID,1)
            case let .probeConfigure(expected,profiles):
                guard profiles.allSatisfy({$0 == 7}) else { throw ProtocolError.unauthorized }
                let revision = try hub.configure(expectedRevision:expected,subscriptions:profiles.map { _ in ProbeSubscription(sink:9,site:"turn",kind:.marker) })
                XCTAssertEqual(revision,1)
            case let .probeEvents(sink,limit,_): XCTAssertEqual(try hub.drain(sink:sink,limit:Int(limit)).count,1)
            case let .inspect(_,schema,_,_,path):
                let context = try InspectContext()
                let result = InspectResult(snapshot:SnapshotID(session:base.session.epoch,revision:base.session.revision,sequence:base.session.sequence),schema:DisplaySchema(name:"counter",version:schema),node:try context.scalar(.integer(kind:ExactString("i64"),decimal:ExactString(String(try base.session.state)))))
                XCTAssertTrue(path.isEmpty); XCTAssertTrue(result.node.isComplete)
                break
            default: throw ProtocolError.unsupported
            }
            }
        }
        let a = try ProtocolAdapter(session:base.session,authority:authority,host:host)
        XCTAssertNil(a.handle(request(a,1,"watch",["u64:1","u64:0","field:count"])).error)
        XCTAssertEqual(try watches.drain(limit:1).count,1)
        XCTAssertNil(a.handle(request(a,2,"probe_configure",["u64:0","u64:7"])).error)
        try hub.marker(site:"turn",severity:.info,context:CaptureContext(producer:1,origin:.simulation,sequence:1))
        let events = a.handle(request(a,3,"probe_events",["u64:9","u64:10","bool:false"]))
        XCTAssertEqual(events.fields["events.returned"],"u64:1")
        XCTAssertNil(a.handle(request(a,4,"snapshot")).error)
        XCTAssertEqual(a.handle(request(a,5,"inspect",["u64:1","u64:1","u64:0","u64:10"])).fields["inspection.complete"],"bool:true")
    }

    func testNativeCandidatesInvalidateAcrossRevisionsAndReconnect() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{_ in InputIterator(values:[1,2])})
        let session = try DebugSession(id:"counter",model:model,policy:InputPolicy.enumerated(equalInputs: ==),equalInputs: ==)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let a = try ProtocolAdapter(session:session,authority:authority,encodeInput:{Data([UInt8($0)])})
        let page = a.handle(request(a,1,"inputs",["u64:0","u64:2"]))
        XCTAssertNil(page.error); XCTAssertEqual(page.fields["inputs.0.token"],"u64:1"); XCTAssertEqual(page.fields["inputs.0.payload"],"none")
        XCTAssertNil(a.handle(request(a,2,"select",["u64:1"])).error)
        XCTAssertEqual(a.handle(request(a,3,"select",["u64:2"],revision:1)).error,.staleHandle)
        let fresh = a.handle(request(a,4,"inputs",["u64:0","u64:1"],revision:1)); XCTAssertNil(fresh.error)
        try a.disconnect(); _ = try a.reconnect()
        XCTAssertEqual(a.handle(request(a,1,"select",[fresh.fields["inputs.0.token"]!],revision:1)).error,.staleHandle)
    }

    func testHostMutationWaitsForCompleteReplyPreflight() throws {
        let base = try adapter(); var committed = 0
        var authority = ProtocolAuthority(); authority.configureWatches = true
        var limits = ProtocolLimits(); limits.maxFieldBytes = 64
        let host = ProtocolHostBindings(commands:["watch"]) { _ in
            PreparedProtocolResponse(fields:["ack":String(repeating:"x",count:65)]) { committed += 1 }
        }
        let a = try ProtocolAdapter(session:base.session,authority:authority,limits:limits,host:host)
        let r = request(a,1,"watch",["u64:1","u64:0"])
        XCTAssertEqual(a.handle(r).error,.limit); XCTAssertEqual(committed,0); XCTAssertEqual(a.configuration,0)
        XCTAssertEqual(a.handle(r).error,.limit); XCTAssertEqual(committed,0)
    }

}
