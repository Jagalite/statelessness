import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class ProtocolWatchAttachmentTests: XCTestCase {
    func testProtocolWatchUsesRegistryAndFrameEventLimits() throws {
        for (eventLimit, frameLimit, expected) in [(80_000, 1_000_000, 80_000), (100_000, 512_000, 64_000)] {
            let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
            let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
            var authority = ProtocolAuthority(); authority.configureWatches = true
            var limits = ProtocolLimits(); limits.maxFrameBytes = frameLimit
            let adapter = try ProtocolAdapter(session:session,authority:authority,limits:limits)
            let registry = try WatchRegistry(authorization:.localAll,maxEventBytes:eventLimit)
            try adapter.enableWatches(registry) { _,snapshot,path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
            let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"watch",arguments:["u64:1","u64:0"]))
            XCTAssertNil(response.error)
            XCTAssertEqual(try registry.list().first?.config.limits.maxBytes,expected)
        }
    }
    func testProtocolWatchUsesConfiguredValueAndPageLimits() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.configureWatches = true
        var limits = ProtocolLimits(); limits.maxFieldBytes = 40_000; limits.maxPageSize = 250
        let adapter = try ProtocolAdapter(session:session,authority:authority,limits:limits)
        let registry = try WatchRegistry(authorization:.localAll)
        var observedPage = 0, observedValue = 0
        try adapter.enablePagedWatches(registry) { _,snapshot,path,page,projectionLimits in
            observedPage = page.limit; observedValue = projectionLimits.maxValueBytes
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"watch",arguments:["u64:1","u64:0"]))
        XCTAssertNil(response.error)
        let config = try XCTUnwrap(registry.list().first?.config)
        XCTAssertEqual(config.limits.maxValueBytes,10_000); XCTAssertEqual(config.limits.maxPageSize,250)
        XCTAssertEqual(observedPage,250); XCTAssertEqual(observedValue,10_000)
    }
    func testNonpagedProtocolWatchAcceptsLargerPageLimit() throws {
        for declaredSchema in [false,true] {
            let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
            let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
            var authority = ProtocolAuthority(); authority.configureWatches = true
            var limits = ProtocolLimits(); limits.maxPageSize = 250
            let adapter = try ProtocolAdapter(session:session,authority:authority,limits:limits)
            let registry = try WatchRegistry(authorization:.localAll)
            let project: ProtocolWatchProjection<Int> = { _,snapshot,path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
            if declaredSchema { try adapter.enableWatches(registry,schema:{ _ in DisplaySchema(name:"x") },project:project) }
            else { try adapter.enableWatches(registry,project:project) }
            let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"watch",arguments:["u64:1","u64:0"]))
            XCTAssertNil(response.error)
            let config = try XCTUnwrap(registry.list().first?.config)
            XCTAssertEqual(config.limits.maxPageSize,250); XCTAssertEqual(config.page.limit,100)
        }
    }
    func testFilteredWatchesDoNotInvokeSchemaOrProjection() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let adapter = try ProtocolAdapter(session:session)
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.model = "other"; _ = try registry.add(config)
        var schemas = 0,projections = 0
        try adapter.enableWatches(registry,schema:{ _ in schemas += 1; return DisplaySchema(name:"x") }) { _,snapshot,path,_ in
            projections += 1; return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(schemas,0); XCTAssertEqual(projections,0); XCTAssertEqual(registry.health.filtered,1)
    }
    func testProtocolWatchDeclaredSchemaPreflightIsAtomic() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.configureWatches = true
        let adapter = try ProtocolAdapter(session:session,authority:authority)
        let registry = try WatchRegistry(authorization:.localAll)
        var declared = DisplaySchema(name:"old",version:2), projections = 0
        try adapter.enableWatches(registry,schema:{ _ in declared }) { _,snapshot,path,_ in
            projections += 1; return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"new"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        func request(_ id: UInt64) -> ProtocolResponse { adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:id,command:"watch",arguments:["u64:1","u64:0"])) }
        XCTAssertEqual(request(1).error,.invalidInput); XCTAssertEqual(projections,0)
        declared = DisplaySchema(name:"old")
        XCTAssertEqual(request(2).error,.invalidInput); XCTAssertEqual(projections,1)
        XCTAssertEqual(registry.captureRevision,0); XCTAssertTrue(try registry.list().isEmpty)
        XCTAssertTrue(try registry.drain(limit:10).isEmpty)
    }
    func testDeclaredWatchSchemaSurvivesFirstProjectionError() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let adapter = try ProtocolAdapter(session:session)
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let declared = DisplaySchema(name:"counter",source:SourceSite(file:"counter.swift",line:1,column:1))
        try adapter.enableWatches(registry,schema:{ _ in declared }) { _,_,_,_ in throw InspectError.pathNotFound }
        XCTAssertEqual(try registry.latestEvent(1)?.kind,.inspectorError)
        XCTAssertEqual(try registry.latestEvent(1)?.schema,declared)
        XCTAssertEqual(try registry.list().first?.validatedSchema,declared)
    }
    func testWatchCurrentReportsEventSamplingCompleteness() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let adapter = try ProtocolAdapter(session:session,authority:authority,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll); var config = WatchConfig(); config.evaluationInterval = 2; config.trigger = .everyObservation
        _ = try registry.add(config)
        try adapter.enableWatches(registry) { state,snapshot,path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(state > 0)))) }
        for index: UInt64 in 1...2 {
            let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:index,expectedRevision:index-1,command:"step",arguments:["bytes:01"]))
            XCTAssertNil(response.error)
        }
        let current = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:3,expectedRevision:2,command:"watch_current",arguments:["u64:1"]))
        XCTAssertEqual(current.fields["watch.completeness"],"enum:Partial(Sampled)")
        XCTAssertEqual(current.fields["watch.node.completeness"],"enum:Complete")
    }
    func testPropertyFailureWatchReceivesOrderedRealCheckIDs() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{ s in s == 0 ? [.passed("initial")] : [.passed("ok"),.failed("state-a","private"),.failed("state-b","private")] },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},checkTransition:{ _,_,_ in [.failed("transition-c","private")] })
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let adapter = try ProtocolAdapter(session:session,authority:authority,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        try adapter.enableWatches(registry) { _,snapshot,path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertEqual(try registry.latestEvent(1)?.failingProperties,[])
        let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"step",arguments:["bytes:01"]))
        XCTAssertNil(response.error)
        let event = try XCTUnwrap(registry.latestEvent(1))
        XCTAssertEqual(event.kind,.propertyFailure); XCTAssertEqual(event.failingProperties,["state-a","state-b","transition-c"])
        XCTAssertEqual(registry.failureSnapshot?.events.last?.failingProperties,event.failingProperties)
    }
    func testPropertyFailureWatchMarksBoundedCheckScanIncomplete() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{ s in
            s == 0 ? [] : (0..<1024).map { .passed("ok-\($0)") } + [.failed("late", "private")]
        },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        let adapter = try ProtocolAdapter(session:session,authority:authority,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        try adapter.enableWatches(registry) { _,snapshot,path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertNil(adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"step",arguments:["bytes:01"])).error)
        let event = try XCTUnwrap(registry.latestEvent(1))
        XCTAssertEqual(event.kind,.propertyFailure)
        XCTAssertEqual(event.failingProperties,[])
        XCTAssertEqual(event.completeness,.partial(.valueLimit))
    }
    func testPagedWatchAdapterPassesConfiguredPages() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let adapter = try ProtocolAdapter(session:session),registry = try WatchRegistry(authorization:.localAll)
        var first = WatchConfig(); first.page = PageRequest(offset:0,limit:1)
        var second = WatchConfig(); second.page = PageRequest(offset:1,limit:1)
        let one = try registry.add(first),two = try registry.add(second); var seen: [Int] = []
        try adapter.enablePagedWatches(registry) { _,snapshot,path,page,limits in
            seen.append(page.offset)
            let node = try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { index,c in try c.scalar(.integer(kind:ExactString("u8"),decimal:ExactString(String(index)))) }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"items"),path:path,node:node)
        }
        XCTAssertEqual(seen,[0,1]); XCTAssertEqual(try registry.current(one.watchID)?.node.page?.offset,0); XCTAssertEqual(try registry.current(two.watchID)?.node.page?.offset,1)
    }
    func testRustGeneratedBoundedWatchAndProbeWire() throws {
        let root = URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_SWIFT_WATCH_PROBE_WIRE"].map { URL(fileURLWithPath:$0) } ?? root.appendingPathComponent("conformance/swift-watch-probe-wire-v1.tsv")
        let fixture = try String(contentsOf:path,encoding:.utf8)
        let expected = Dictionary(uniqueKeysWithValues:fixture.split(separator:"\n").map { line -> (String,[Substring]) in let f = line.split(separator:"\t",omittingEmptySubsequences:false); return (String(f[0]),Array(f.dropFirst(9))) })
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"test",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub()
        for sink in [UInt64(1),2] { try hub.addSink(id:sink,permissions:.allLocal) }
        try hub.registerProducer(id:10,epoch:5); try hub.registerProducer(id:20,epoch:6)
        let profiles = [UInt64(7),8].map { ProtocolProbeProfile(id:$0,label:"profile-\($0)",subscription:ProbeSubscription(sink:1,site:"counter")) }
        var limits = ProtocolLimits(); limits.maxResponseFields = 32
        let host = try diagnosticProbeHost(hub:hub,profiles:profiles,sinks:[1,2],limits:limits)
        let adapter = try ProtocolAdapter(session:session,limits:limits,host:host)
        let watches = try WatchRegistry(authorization:.localAll)
        for _ in 0..<8 { _ = try watches.add(WatchConfig()) }
        try adapter.enableWatches(watches) { _,snapshot,path,limits in
            let node = try InspectContext(limits:limits).object("request-lifecycle.State") { _ in [] }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"request-lifecycle.State"),path:path,node:node)
        }
        for (index,item) in [("watches","watch_status",[]),("probes","probe_status",["u64:0","u64:1"])].enumerated() {
            let response = adapter.handle(ProtocolRequest(session:"test",epoch:adapter.epoch,requestID:UInt64(index+1),command:item.1,arguments:item.2))
            let wire = String(decoding:try ProtocolWire.encodeResponse(response,limits:limits),as:UTF8.self)
            XCTAssertEqual(Array(wire.split(separator:"\t",omittingEmptySubsequences:false).dropFirst(8)),expected[item.0],item.0)
        }
    }
    func testWatchStatusCanReturnMoreThanOneThousandConfiguredEntries() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var limits = ProtocolLimits(); limits.maxPageSize = 1001; limits.maxResponseFields = 5010; limits.maxFrameBytes = 2*1024*1024
        let p = try ProtocolAdapter(session:session,limits:limits)
        let registry = try WatchRegistry(authorization:.localAll,maxWatches:1001)
        for _ in 0..<1001 { _ = try registry.add(WatchConfig()) }
        try p.enableWatches(registry) { _,snapshot,path,limits in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:try InspectContext(limits:limits).scalar(.unit))
        }
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"watch_status"))
        XCTAssertNil(response.error); XCTAssertEqual(response.fields["watches.total"],"u64:1001")
        XCTAssertEqual(response.fields["watches.truncated"],"bool:false"); XCTAssertEqual(response.fields["watches.1000.id"],"u64:1001")
    }
    func make(_ limits: ProtocolLimits = ProtocolLimits()) throws -> (ProtocolAdapter<Int,Int,Int>,WatchRegistry) {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.configureWatches = true; authority.deliverInputs = true
        let adapter = try ProtocolAdapter(session:session,authority:authority,limits:limits,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll)
        try adapter.enableWatches(registry) { state,snapshot,path,limits in
            guard path.isEmpty else { throw InspectError.pathNotFound }
            let node = try InspectContext(limits:limits).scalar(.integer(kind:ExactString("i64"),decimal:ExactString(String(state))))
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:node)
        }
        return (adapter,registry)
    }
    func testWatchAttachmentTracksCommittedTurnsAndConfiguration() throws {
        let (p,registry) = try make()
        func send(_ id: UInt64,_ command: String,_ args: [String],revision: UInt64 = 0,configuration: UInt64 = 0) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,expectedRevision:revision,expectedConfiguration:configuration,command:command,arguments:args)) }
        let add = send(1,"watch",["u64:1","u64:0"])
        XCTAssertNil(add.error); XCTAssertEqual(add.fields["watch.id"],"u64:1"); XCTAssertEqual(add.fields["watch.effective_sequence"],"u64:1")
        XCTAssertEqual(add.fieldOrder,["watch.id","watch.generation","watch.effective_sequence","watch.installed_at_revision","watch.baseline","watch.metadata_complete","watch.display_schema_version","watch.display_schema","watch.resolved_type"])
        XCTAssertEqual(send(2,"watch_current",["u64:1"]).fields["watch.value"],"enum:unavailable")
        XCTAssertEqual(send(3,"watch",["u64:1","u64:0"],configuration:0).error,.staleConfiguration)
        XCTAssertNil(send(4,"step",["bytes:01"]).error)
        let current = send(5,"watch_current",["u64:1"])
        XCTAssertNil(current.error); XCTAssertEqual(current.fields["watch.snapshot_sequence"],"u64:1"); XCTAssertEqual(current.fields["watch.node.value"],"i64:1"); XCTAssertEqual(current.fields["watch.origin"],"enum:OutOfDomainInjection")
        XCTAssertEqual(Array(current.fieldOrder!.prefix(8)),["watch.id","watch.generation","watch.capture_revision","watch.snapshot_revision","watch.snapshot_sequence","watch.kind","watch.completeness","watch.origin"])
        let status = send(6,"watch_status",[])
        XCTAssertEqual(status.fields["watches.total"],"u64:1"); XCTAssertEqual(status.fields["watches.0.has_baseline"],"bool:true")
        // Exact Rust Command::WatchStatus sequence (statelessness-debug protocol.rs).
        XCTAssertEqual(status.fieldOrder,["watches.total","watches.truncated","watches.dropped_events","watches.observation_gaps","watches.inspection_errors","watches.0.id","watches.0.generation","watches.0.enabled","watches.0.has_baseline","watches.0.effective_sequence"])
        let removed = send(7,"unwatch",["u64:1"],revision:1,configuration:1)
        XCTAssertNil(removed.error); XCTAssertEqual(registry.captureRevision,2)
        XCTAssertEqual(send(8,"unwatch",["u64:1"],revision:1,configuration:2).error,.staleHandle)
        let events = send(9,"events",["u64:3"],revision:1,configuration:2)
        XCTAssertNil(events.error); XCTAssertEqual(events.fields["events.returned"],"u64:3")
        XCTAssertEqual(events.fields["events.0.kind"],"enum:watch_configured"); XCTAssertEqual(events.fields["events.0.configuration"],"u64:1")
        XCTAssertEqual(events.fields["events.1.kind"],"enum:transition"); XCTAssertEqual(events.fields["events.1.revision"],"u64:1")
        XCTAssertEqual(events.fields["events.2.kind"],"enum:watch_removed"); XCTAssertEqual(events.fields["events.2.configuration"],"u64:2")
    }
    // Rust statelessness-debug protocol.rs decode_segment.
    func testWatchPathDecodingMatchesRustDecodeSegment() throws {
        func malformed(_ segment: String,file: StaticString = #filePath,line: UInt = #line) {
            XCTAssertThrowsError(try watchPath([segment]),file:file,line:line) { XCTAssertEqual($0 as? ProtocolError,.malformed,file:file,line:line) }
        }
        func integer(_ segment: String) throws -> String? {
            guard case let .typedMapKey(.integer(_,decimal))? = try watchPath([segment]).first else { return nil }
            return decimal.value
        }
        XCTAssertEqual(try watchPath(["key.bool:true","key.bool:false"]),[.typedMapKey(.bool(true)),.typedMapKey(.bool(false))])
        for bad in ["key.bool:yes","key.bool:True","key.bool:","key.bool:1"] { malformed(bad) }
        XCTAssertEqual(try integer("key.u8:255"),"255"); malformed("key.u8:256"); malformed("key.u8:300"); malformed("key.u8:-1"); malformed("key.u8:-0")
        XCTAssertEqual(try integer("key.u8:007"),"7"); XCTAssertEqual(try integer("key.u8:+5"),"5"); XCTAssertEqual(try integer("key.u8:000"),"0")
        XCTAssertEqual(try integer("key.i8:-128"),"-128"); XCTAssertEqual(try integer("key.i8:127"),"127"); malformed("key.i8:128"); malformed("key.i8:-129")
        XCTAssertEqual(try integer("key.i8:-0"),"0"); XCTAssertEqual(try integer("key.i16:-00032768"),"-32768"); malformed("key.i16:32768")
        XCTAssertEqual(try integer("key.u64:18446744073709551615"),"18446744073709551615"); malformed("key.u64:18446744073709551616")
        XCTAssertEqual(try integer("key.u128:340282366920938463463374607431768211455"),"340282366920938463463374607431768211455"); malformed("key.u128:340282366920938463463374607431768211456")
        XCTAssertEqual(try integer("key.i128:-170141183460469231731687303715884105728"),"-170141183460469231731687303715884105728"); malformed("key.i128:170141183460469231731687303715884105728")
        for bad in ["key.u32:","key.u32:+","key.i32:-","key.u32:1 ","key.u32:0x10","key.u32:1_000","key.i32:++1","key.u32:١"] { malformed(bad) }
        malformed("key.u7:1"); malformed("key.integer:1"); malformed("key.U8:1")
        XCTAssertEqual(try watchPath(["index:0042"]),[.index(42)]); XCTAssertEqual(try watchPath(["index:+1"]),[.index(1)])
        malformed("index:-1"); malformed("index:18446744073709551616"); malformed("index:")
        XCTAssertEqual(try watchPath(["key.char:é"]),[.typedMapKey(.character(0xE9))]); malformed("key.char:ab"); malformed("key.char:")
        malformed("missing-colon")
    }
    func testWatchStatusTruncatesToAvailableFieldBudget() throws {
        func setup(_ maximum: Int,_ watchCount: Int,frame: Int = 65536) throws -> ProtocolAdapter<Int,Int,Int> {
            let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
            let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
            var limits = ProtocolLimits(); limits.maxResponseFields = maximum; limits.maxFrameBytes = frame
            let adapter = try ProtocolAdapter(session:session,limits:limits)
            let registry = try WatchRegistry(authorization:.localAll)
            for _ in 0..<watchCount { _ = try registry.add(WatchConfig()) }
            try adapter.enableWatches(registry) { _,snapshot,path,limits in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:try InspectContext(limits:limits).scalar(.unit)) }
            return adapter
        }
        let p = try setup(10,2)
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"watch_status"))
        XCTAssertNil(response.error); XCTAssertEqual(response.fields["watches.total"],"u64:2")
        XCTAssertEqual(response.fields["watches.truncated"],"bool:true"); XCTAssertEqual(response.fields["watches.0.id"],"u64:1")
        XCTAssertNil(response.fields["watches.1.id"]); XCTAssertEqual(response.fields.count,10)
        let emptyPage = try setup(5,1)
        let summary = emptyPage.handle(ProtocolRequest(session:"counter",epoch:emptyPage.epoch,requestID:1,command:"watch_status"))
        XCTAssertNil(summary.error); XCTAssertEqual(summary.fields["watches.total"],"u64:1"); XCTAssertEqual(summary.fields["watches.truncated"],"bool:true")
        let byteBounded = try setup(100,5,frame:450)
        let bounded = byteBounded.handle(ProtocolRequest(session:"counter",epoch:byteBounded.epoch,requestID:1,command:"watch_status"))
        XCTAssertNil(bounded.error); XCTAssertEqual(bounded.fields["watches.total"],"u64:5")
        XCTAssertEqual(bounded.fields["watches.truncated"],"bool:true"); XCTAssertNil(bounded.fields["watches.4.id"])
    }
    func testWatchResponsePreflightDoesNotInstall() throws {
        var limits = ProtocolLimits(); limits.maxResponseFields = 5
        let (p,registry) = try make(limits)
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"watch",arguments:["u64:1","u64:0"]))
        XCTAssertEqual(response.error,.limit); XCTAssertEqual(registry.captureRevision,0); XCTAssertEqual(p.configuration,0)
    }
    func testProjectionFailureIsVisibleAndCannotReenterSession() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.configureWatches = true; authority.deliverInputs = true
        let p = try ProtocolAdapter(session:session,authority:authority,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll)
        var rejected = 0
        try p.enableWatches(registry) { state,snapshot,path,limits in
            do { try session.cancel(expectedRevision:snapshot.revision); XCTFail("projection reentered session") }
            catch DebugError.reentrant { rejected += 1 }
            if state == 1 { throw InspectError.pathNotFound }
            let node = try InspectContext(limits:limits).scalar(.unit)
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:node)
        }
        let add = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"watch",arguments:["u64:1","u64:0"]))
        XCTAssertNil(add.error)
        let step = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:2,command:"step",arguments:["bytes:01"]))
        XCTAssertNil(step.error); XCTAssertEqual(step.fields["delivered"],"bool:true"); XCTAssertEqual(try session.state,1)
        let current = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:3,command:"watch_current",arguments:["u64:1"]))
        XCTAssertEqual(current.fields["watch.kind"],"enum:InspectorError(PathNotFound)")
        XCTAssertEqual(registry.health.inspectionErrors,1); XCTAssertEqual(rejected,2)
    }
    func testSchemaChangeDoesNotExposePreviousWatchValue() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,i in Transition(state:s+i,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var authority = ProtocolAuthority(); authority.configureWatches = true; authority.deliverInputs = true
        let p = try ProtocolAdapter(session:session,authority:authority,decodeInput:{Int($0.first ?? 0)})
        let registry = try WatchRegistry(authorization:.localAll)
        try p.enableWatches(registry) { state,snapshot,path,limits in
            let node = try InspectContext(limits:limits).scalar(.unit)
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:state < 2 ? "counter" : "changed"),path:path,node:node)
        }
        func send(_ id: UInt64,_ command: String,_ args: [String],revision: UInt64 = 0) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,expectedRevision:revision,command:command,arguments:args)) }
        XCTAssertNil(send(1,"watch",["u64:1","u64:0"]).error)
        XCTAssertNil(send(2,"step",["bytes:01"]).error)
        XCTAssertNil(send(3,"step",["bytes:01"],revision:1).error)
        let current = send(4,"watch_current",["u64:1"],revision:2)
        XCTAssertEqual(current.fields["watch.value"],"enum:unavailable"); XCTAssertNil(current.fields["watch.kind"]); XCTAssertNil(current.fields["watch.node.value"])
        XCTAssertEqual(try registry.drain(limit:100).last?.kind,.schemaChanged)
    }
}
