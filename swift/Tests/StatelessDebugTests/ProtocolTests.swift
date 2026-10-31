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
    func testBuiltInInspectionUsesDetachedStateAndProtocolBudgets() throws {
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let adapter = try adapter(authority:authority)
        var calls = 0
        try adapter.enableInspection { state,snapshot,path,page,limits in
            calls += 1
            XCTAssertEqual(path,[.field(ExactString("count"))]); XCTAssertEqual(page.offset,0); XCTAssertEqual(page.limit,1)
            XCTAssertEqual(limits.maxWork,10_000); XCTAssertEqual(limits.maxDepth,16); XCTAssertEqual(limits.maxNodes,128)
            XCTAssertEqual(limits.maxBytes,adapter.limits.maxFrameBytes/8); XCTAssertEqual(limits.maxValueBytes,adapter.limits.maxFieldBytes/4)
            let node = try InspectContext(limits:limits).scalar(.integer(kind:ExactString("i64"),decimal:ExactString(String(state))))
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:node)
        }
        XCTAssertEqual(adapter.handle(request(adapter,1,"status")).fields["capability.inspection"],"bool:true")
        XCTAssertEqual(adapter.handle(request(adapter,2,"snapshot")).fields["handle"],"u64:1")
        let response = adapter.handle(request(adapter,3,"inspect",["u64:1","u64:1","u64:0","u64:1","field:count"]))
        XCTAssertNil(response.error); XCTAssertEqual(response.fields["schema.name"],"string:counter"); XCTAssertEqual(response.fields["node.value"],"i64:0"); XCTAssertEqual(calls,1)
        XCTAssertEqual(response.fieldOrder,["schema.name","schema.version","snapshot.revision","snapshot.sequence","node.completeness","node.child_count","node.kind","node.value","node.children.returned"])
        let encoded = try ProtocolWire.encodeResponse(response)
        XCTAssertEqual(try ProtocolWire.encodeResponse(ProtocolWire.decodeResponse(encoded)),encoded)
        XCTAssertEqual(try ProtocolWire.encodeResponse(adapter.handle(request(adapter,3,"inspect",["u64:1","u64:1","u64:0","u64:1","field:count"]))),encoded)
        XCTAssertEqual(adapter.handle(request(adapter,4,"inspect",["u64:1","u64:2","u64:0","u64:1","field:count"])).error,.invalidInput)
        XCTAssertNil(adapter.handle(request(adapter,5,"step",["bytes:01"])).error)
        XCTAssertEqual(adapter.handle(request(adapter,6,"inspect",["u64:1","u64:1","u64:0","u64:1","field:count"],revision:1)).error,.staleHandle)
        XCTAssertEqual(adapter.handle(request(adapter,7,"inspect",["u64:1","u64:1","u64:0","u64:1","field:count"],revision:0)).error,.staleHandle)
        XCTAssertEqual(calls,2)
    }
    func testInspectionSchemaPreflightRejectsWithoutProjecting() throws {
        let adapter = try adapter(); var schemaCalls = 0, projections = 0
        try adapter.enableInspection(schema:{ _ in schemaCalls += 1; return DisplaySchema(name:"counter",version:1) }) { state,snapshot,path,_,limits in
            projections += 1
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter",version:1),path:path,node:try InspectContext(limits:limits).scalar(.integer(kind:ExactString("i64"),decimal:ExactString(String(state)))))
        }
        XCTAssertNil(adapter.handle(request(adapter,1,"snapshot")).error)
        XCTAssertEqual(adapter.handle(request(adapter,2,"inspect",["u64:1","u64:2","u64:0","u64:1"])).error,.invalidInput)
        XCTAssertEqual(schemaCalls,1); XCTAssertEqual(projections,0)
        XCTAssertNil(adapter.handle(request(adapter,3,"inspect",["u64:1","u64:1","u64:0","u64:1"])).error)
        XCTAssertEqual(schemaCalls,2); XCTAssertEqual(projections,1)
    }
    func testInspectionRejectsNegativePageMetadataBeforeWireEncoding() throws {
        let adapter = try adapter()
        try adapter.enableInspection { _,snapshot,path,_,_ in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"bad"),path:path,node:InspectNode(kind:.sequence(ExactString("bad")),page:PageInfo(offset:-1,returned:0,total:0,nextOffset:nil)))
        }
        XCTAssertNil(adapter.handle(request(adapter,1,"snapshot")).error)
        let response = adapter.handle(request(adapter,2,"inspect",["u64:1","u64:1","u64:0","u64:1"]))
        XCTAssertEqual(response.error,.invalidInput); XCTAssertNil(response.fields["node.page.offset"])
    }
    func testInspectionRejectsNegativeStringAndBytesTotalsBeforeWireEncoding() throws {
        let adapter = try adapter(); var bytes = false
        try adapter.enableInspection { _,snapshot,path,_,_ in
            let kind: NodeKind = bytes ? .bytes(preview:[],totalBytes:-1) : .string(preview:ExactString(""),totalBytes:-1)
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"bad"),path:path,node:InspectNode(kind:kind))
        }
        XCTAssertNil(adapter.handle(request(adapter,1,"snapshot")).error)
        XCTAssertEqual(adapter.handle(request(adapter,2,"inspect",["u64:1","u64:1","u64:0","u64:1"])).error,.invalidInput)
        bytes = true
        XCTAssertEqual(adapter.handle(request(adapter,3,"inspect",["u64:1","u64:1","u64:0","u64:1"])).error,.invalidInput)
    }
    func testInspectionErrorPrecedesOversizedSchemaResponseLimit() throws {
        let adapter = try adapter(); let schema = DisplaySchema(name:String(repeating:"s",count:adapter.limits.maxFieldBytes),version:1)
        var fail = true
        try adapter.enableInspection(schema:{ _ in schema }) { _,snapshot,path,_,limits in
            if fail { throw InspectError.pathNotFound }
            return InspectResult(snapshot:snapshot,schema:schema,path:path,node:try InspectContext(limits:limits).scalar(.bool(true)))
        }
        XCTAssertNil(adapter.handle(request(adapter,1,"snapshot")).error)
        XCTAssertEqual(adapter.handle(request(adapter,2,"inspect",["u64:1","u64:1","u64:0","u64:1"])).error,.invalidInput)
        fail = false
        XCTAssertEqual(adapter.handle(request(adapter,3,"inspect",["u64:1","u64:1","u64:0","u64:1"])).error,.limit)
    }
    func testOrderedHostFieldsAndBuiltInSnapshotWireOrder() throws {
        let base = try adapter()
        let host = ProtocolHostBindings(commands:["telemetry_health"]) { _ in try PreparedProtocolResponse(orderedFields:[("z","last"),("a","first")]) }
        let adapter = try ProtocolAdapter(session:base.session,host:host)
        let snapshot = adapter.handle(request(adapter,1,"snapshot"))
        XCTAssertEqual(snapshot.fieldOrder,["handle","snapshot.revision","snapshot.sequence"])
        let hostResponse = adapter.handle(request(adapter,2,"telemetry_health"))
        XCTAssertEqual(hostResponse.fieldOrder,["z","a"])
        let encoded = try ProtocolWire.encodeResponse(hostResponse)
        XCTAssertTrue(String(decoding:encoded,as:UTF8.self).hasSuffix("\tz\tlast\ta\tfirst"))
        XCTAssertEqual(try ProtocolWire.encodeResponse(ProtocolWire.decodeResponse(encoded)),encoded)
        XCTAssertThrowsError(try PreparedProtocolResponse(orderedFields:[("same","a"),("same","b")]))
    }
    func testUnencodableResponseWithMaximumCacheLimitDoesNotTrap() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"cache",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var limits = ProtocolLimits(); limits.maxCacheBytes = .max; limits.maxResponseFields = .max
        let adapter = try ProtocolAdapter(session:session,limits:limits)
        let request = ProtocolRequest(session:"cache",epoch:adapter.epoch,requestID:1,command:"status")
        XCTAssertEqual(adapter.handle(request).error,.limit)
        XCTAssertEqual(adapter.handle(request).error,.retiredRequest)
    }
    func testConcreteExactExportAndRangeChecks() throws {
        let codec = Codec<Int,Int,Int>(metadata: {Metadata(name:"window")},encodeState:{[UInt8($0)]},decodeState:{Int($0[0])},encodeInput:{[UInt8($0)]},decodeInput:{Int($0[0])},encodeOutput:{[UInt8($0)]})
        let model = Model(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[s+i])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},codec:codec)
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==,recording:RecorderOptions())
        var authority = ProtocolAuthority(); authority.exportExactTrace = true
        let p = try ProtocolAdapter(session:session,authority:authority)
        XCTAssertNil(p.handle(request(p,1,"snapshot")).error)
        let response = p.handle(request(p,2,"export_trace",["u64:1","u64:0","u64:12"]))
        XCTAssertNil(response.error)
        XCTAssertEqual(response.fields["export.bytes"],"bytes:53544c4553531a0a01000000")
        XCTAssertEqual(response.fields["export.retained_steps"],"u64:0")
        XCTAssertEqual(response.fields["export.redacted"],"bool:false")
        XCTAssertThrowsError(try session.traceWindow(offset:0,limit:-1))
        XCTAssertThrowsError(try session.traceWindow(offset:UInt64.max,limit:1))
        XCTAssertEqual(p.handle(request(p,3,"export_trace",["u64:1","u64:0","u64:0"])).error,.limit)
        try p.disconnect(); _ = try p.reconnect()
        XCTAssertEqual(p.handle(request(p,1,"export_trace",["u64:1","u64:0","u64:1"])).error,.staleHandle)
    }
    func testManualWireGoldenAndFullWidth() throws {
        let r = ProtocolRequest(session:"a\tπ",epoch:UInt64.max,requestID:UInt64.max,expectedRevision:9007199254740993,command:"status")
        let expected = "DDBG\tu32:1\trequest\tstring:a%09%CF%80\tu64:18446744073709551615\tu64:18446744073709551615\tu64:9007199254740993\tu64:0\tstatus"
        XCTAssertEqual(String(data:try ProtocolWire.encodeRequest(r),encoding:.utf8),expected)
        XCTAssertEqual(try ProtocolWire.decodeRequest(Data(expected.utf8)),r)
        XCTAssertThrowsError(try ProtocolWire.quantity("u64:18446744073709551616"))
        XCTAssertThrowsError(try ProtocolWire.unescape("%FF",maximum:8))
        XCTAssertThrowsError(try ProtocolWire.unescape("<",maximum:8))
    }
    func testWireEncodingAdmitsEscapedSizeAndFieldCount() throws {
        let r = ProtocolRequest(session:"π\t%",epoch:1,requestID:2,command:"status",arguments:["a b"])
        let encoded = try ProtocolWire.encodeRequest(r)
        XCTAssertTrue(String(decoding:encoded,as:UTF8.self).contains("string:%CF%80%09%25"))
        var limits = ProtocolLimits(); limits.maxFrameBytes = encoded.count
        XCTAssertEqual(try ProtocolWire.encodeRequest(r,limits:limits),encoded)
        limits.maxFrameBytes -= 1
        XCTAssertThrowsError(try ProtocolWire.encodeRequest(r,limits:limits)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        limits.maxFrameBytes = 65536; limits.maxRequestFields = 9
        XCTAssertThrowsError(try ProtocolWire.encodeRequest(r,limits:limits)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        limits.maxRequestFields = 64; limits.maxFieldBytes = 7
        XCTAssertThrowsError(try ProtocolWire.encodeRequest(r,limits:limits)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        XCTAssertThrowsError(try ProtocolWire.inputBytes("bytes:0102",maximum:1)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        XCTAssertThrowsError(try ProtocolWire.unescape(String(repeating:"%41",count:10),maximum:2)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        let response = ProtocolResponse(session:"π",epoch:1,requestID:2,revision:0,configuration:0,error:nil,fields:["k":"%"])
        let encodedResponse = try ProtocolWire.encodeResponse(response)
        limits = ProtocolLimits(); limits.maxFrameBytes = encodedResponse.count
        XCTAssertEqual(try ProtocolWire.encodeResponse(response,limits:limits),encodedResponse)
        limits.maxFrameBytes -= 1
        XCTAssertThrowsError(try ProtocolWire.encodeResponse(response,limits:limits)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
        limits = ProtocolLimits(); limits.maxResponseFields = Int.min
        XCTAssertThrowsError(try ProtocolWire.decodeResponse(encodedResponse,limits:limits)) { XCTAssertEqual($0 as? ProtocolError,.limit) }
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
        let a = try ProtocolAdapter(session:session,authority:authority,decodeInput:{ data in
            guard data.count == 1, let byte = data.first else { throw ProtocolError.invalidInput }
            return Int(byte)
        },encodeInput:{Data([UInt8($0)])})
        let page = a.handle(request(a,1,"inputs",["u64:0","u64:2"]))
        XCTAssertNil(page.error); XCTAssertEqual(page.fields["candidates.0.token"],"u64:1"); XCTAssertNil(page.fields["candidates.0.encoded"]); XCTAssertEqual(page.fields["candidates.returned"],"u64:2")
        XCTAssertNil(a.handle(request(a,2,"select",["u64:1"])).error)
        XCTAssertEqual(a.handle(request(a,3,"select",["u64:2"],revision:1)).error,.staleHandle)
        let fresh = a.handle(request(a,4,"inputs",["u64:0","u64:1"],revision:1)); XCTAssertNil(fresh.error)
        try a.disconnect(); _ = try a.reconnect()
        XCTAssertEqual(a.handle(request(a,1,"select",[try XCTUnwrap(fresh.fields["candidates.0.token"])],revision:1)).error,.staleHandle)
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
