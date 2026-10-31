import XCTest
import Statelessness
@testable import StatelessDebug
final class WatchTests: XCTestCase {
    func testLongSchemaNameRetainsIdentityButBoundsEventMetadata() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .everyObservation
        let id = try registry.add(config).watchID
        let name = String(repeating:"é",count:513)
        let schema = DisplaySchema(name:name)
        let first = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:first,origin:.simulation)
        context.declaredSchema = schema
        try registry.observePaged(context:context) { path,_,_ in
            InspectResult(snapshot:first,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        let event = try XCTUnwrap(registry.latestEvent(id))
        XCTAssertEqual(event.schema?.name,String(repeating:"é",count:512))
        XCTAssertEqual(event.completeness,.partial(.valueLimit))
        let snap = try registry.snapshotNowPaged(id,context:context) { path,_,_ in
            InspectResult(snapshot:first,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(snap.completeness,.partial(.valueLimit))
        XCTAssertEqual(snap.schema?.name,String(repeating:"é",count:512))
        let other = DisplaySchema(name:name + "x")
        XCTAssertThrowsError(try registry.snapshotNowPaged(id,context:context) { path,_,_ in
            InspectResult(snapshot:first,schema:other,path:path,node:InspectNode(kind:.scalar(.unit)))
        })
    }
    func testLongSchemaBaselineAccountsForFullIdentity() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxBaselineBytes:2048)
        let id = try registry.add(WatchConfig()).watchID
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        let schema = DisplaySchema(name:String(repeating:"x",count:4096))
        try registry.observePaged(context:WatchObservationContext(snapshot:snapshot,origin:.simulation)) { path,_,_ in
            InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertNil(try registry.current(id))
        XCTAssertEqual(try registry.snapshot().health.baselineEvictions,1)
    }
    func testRegistrySchemaVersionAdmissionIsAtomic() throws {
        let registry = try WatchRegistry(authorization:.localAll,schemaVersion:2)
        let mismatch = WatchConfig()
        XCTAssertThrowsError(try registry.add(mismatch)) { error in
            guard case let WatchError.schemaMismatch(expected,actual) = error else { return XCTFail("expected schema mismatch: \(error)") }
            XCTAssertEqual(expected,2); XCTAssertEqual(actual,1)
        }
        XCTAssertEqual(registry.captureRevision,0)
        XCTAssertTrue(try registry.list().isEmpty)
        var valid = WatchConfig(); valid.schemaVersion = 2
        let id = try registry.add(valid).watchID
        XCTAssertThrowsError(try registry.update(id,config:mismatch))
        XCTAssertEqual(registry.captureRevision,1)
        XCTAssertEqual(try registry.list().first?.generation,1)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observePaged(context:WatchObservationContext(snapshot:snapshot,origin:.simulation)) { path,_,_ in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"model",version:2),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(try registry.latestEvent(id)?.kind,.baseline)
    }
    func testBaselineTTLUsesRetainedInspectionErrorSnapshot() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .everyObservation; config.baselineTTLSequences = 0
        let id = try registry.add(config).watchID
        let first = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observePaged(context:WatchObservationContext(snapshot:first,origin:.simulation)) { _,_,_ in
            throw InspectError.pathNotFound
        }
        XCTAssertEqual(try registry.latestEvent(id)?.kind,.inspectorError)
        let second = SnapshotID(session:1,revision:2,sequence:2)
        try registry.observePaged(context:WatchObservationContext(snapshot:second,origin:.simulation)) { path,_,_ in
            InspectResult(snapshot:second,schema:DisplaySchema(name:"model"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(try registry.latestEvent(id)?.baselineReason,.expired)
    }
    func testDeclaredSchemaIdentityIgnoresSourceLocation() throws {
        let declared = DisplaySchema(name:"model",source:SourceSite(file:"before",line:1,column:1))
        let actual = DisplaySchema(name:"model",source:SourceSite(file:"after",line:2,column:3))
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        let config = WatchConfig()
        let registry = try WatchRegistry(authorization:.localAll)
        let id = try registry.add(config).watchID
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        context.declaredSchema = declared
        try registry.observePaged(context:context) { path,_,_ in
            InspectResult(snapshot:snapshot,schema:actual,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(try registry.latestEvent(id)?.kind,.baseline)
        let event = try registry.snapshotNowPaged(id,context:context) { path,_,_ in
            InspectResult(snapshot:snapshot,schema:actual,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(event.schema,actual)
        let second = try WatchRegistry(authorization:.localAll)
        let (ack,_,commit) = try second.prepareProtocolAdd(config,snapshot:snapshot,schema:{ declared }) { path,_ in
            InspectResult(snapshot:snapshot,schema:actual,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        try commit()
        XCTAssertEqual(ack.displaySchema,actual)
    }
    func testCachedProjectionDoesNotHideDeclaredSchemaMismatch() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        let first = try registry.add(WatchConfig()).watchID
        let second = try registry.add(WatchConfig()).watchID
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        context.declaredSchema = DisplaySchema(name:"declared")
        var calls = 0
        try registry.observePaged(context:context) { path,_,_ in
            calls += 1
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"other"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(calls,1)
        XCTAssertEqual(try registry.latestEvent(first)?.kind,.inspectorError)
        XCTAssertEqual(try registry.latestEvent(second)?.kind,.inspectorError)
    }
    func testSampledSourceChangeInvalidatesBaselineEvenIfSourceReturns() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 2; config.trigger = .everyObservation
        _ = try registry.add(config); _ = try registry.drain(limit:10)
        var projections = 0
        for sequence: UInt64 in 1...3 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            context.model = sequence == 2 ? "other" : "first"
            try registry.observePaged(context:context) { path,_,_ in
                projections += 1
                return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"model"),path:path,node:InspectNode(kind:.scalar(.unit)))
            }
        }
        let events = try registry.drain(limit:10)
        XCTAssertEqual(projections,2)
        XCTAssertEqual(events.map(\.kind),[.baseline,.baseline])
        XCTAssertEqual(events.last?.baselineReason,.sourceChanged)
    }
    func testExpiredBaselineTakesPrecedenceOverSampledSourceChange() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 2; config.baselineTTLSequences = 1; config.trigger = .everyObservation
        _ = try registry.add(config); _ = try registry.drain(limit:10)
        for sequence: UInt64 in 1...3 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            context.model = sequence == 2 ? "other" : "first"
            try registry.observePaged(context:context) { path,_,_ in
                InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"model"),path:path,node:InspectNode(kind:.scalar(.unit)))
            }
        }
        XCTAssertEqual(try registry.latestEvent(1)?.baselineReason,.expired)
    }
    func testEqualsTriggerRejectsInvalidScalarBeforeAdmission() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .equals(.integer(kind:ExactString("u7"),decimal:ExactString("1")))
        XCTAssertThrowsError(try registry.add(config)) { guard case WatchError.invalidConfig = $0 else { return XCTFail("unexpected \($0)") } }
        config.trigger = .equals(.character(0xD800))
        XCTAssertThrowsError(try registry.add(config)) { guard case WatchError.invalidConfig = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(registry.captureRevision,0); XCTAssertTrue(try registry.list().isEmpty)
        config.trigger = .equals(.integer(kind:ExactString("u8"),decimal:ExactString("255")))
        XCTAssertNoThrow(try registry.add(config))
        config.trigger = .equals(.integer(kind:ExactString("u8"),decimal:ExactString("01")))
        XCTAssertNoThrow(try registry.add(config))
        config.trigger = .equals(.float(kind:ExactString("f64"),decimal:ExactString("1"),bits:0))
        XCTAssertNoThrow(try registry.add(config))
    }
    func testWatchPathRejectsInvalidTypedMapKeysBeforeAdmission() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig()
        for key: InspectMapKey in [.integer(kind:ExactString("u7"),decimal:ExactString("1")), .character(0xD800)] {
            config.path = [.typedMapKey(key)]
            XCTAssertThrowsError(try registry.add(config)) { guard case WatchError.invalidConfig = $0 else { return XCTFail("unexpected \($0)") } }
        }
        XCTAssertEqual(registry.captureRevision,0)
        XCTAssertTrue(try registry.list().isEmpty)
        config.path = [.typedMapKey(.integer(kind:ExactString("u8"),decimal:ExactString("255")))]
        XCTAssertNoThrow(try registry.add(config))
        config.path = [.typedMapKey(.integer(kind:ExactString("u8"),decimal:ExactString("256")))]
        XCTAssertNoThrow(try registry.add(config))
    }
    func testOversizedObservationClearsCurrentAndRecovers() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxBytes:1000,maxEventBytes:300)
        var config = WatchConfig(); config.limits.maxBytes = 300; config.trigger = .everyObservation
        _ = try registry.add(config)
        for sequence: UInt64 in 1...3 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            let file = sequence == 2 ? String(repeating:"f",count:100) : "f"
            let schema = DisplaySchema(name:"x",source:SourceSite(file:file,line:1,column:1))
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            if sequence == 3 { context.declaredSchema = schema }
            try registry.observe(context:context) { path,_ in
                InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
            }
            if sequence == 2 { XCTAssertNil(try registry.latestEvent(1)); XCTAssertNil(try registry.current(1)) }
        }
        XCTAssertEqual(try registry.latestEvent(1)?.baselineReason,.observationGap)
        XCTAssertEqual(registry.health.droppedEvents,1); XCTAssertEqual(registry.health.truncated,0)
    }
    func testSnapshotRejectsDeclaredMismatchAndUnknownSchemaOverhead() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxBytes:1000,maxEventBytes:300)
        var config = WatchConfig(); config.limits.maxBytes = 300
        let id = try registry.add(config).watchID; _ = try registry.drain(limit:10)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation); context.declaredSchema = DisplaySchema(name:"declared")
        XCTAssertThrowsError(try registry.snapshotNowPaged(id,context:context) { path,_,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"other"),path:path,node:InspectNode(kind:.scalar(.unit))) }) { guard case InspectError.schemaMismatch = $0 else { return XCTFail("unexpected \($0)") } }
        context.declaredSchema = nil
        XCTAssertThrowsError(try registry.snapshotNowPaged(id,context:context) { path,_,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:String(repeating:"x",count:100)),path:path,node:InspectNode(kind:.scalar(.unit))) }) { guard case WatchError.capacity = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertTrue(try registry.drain(limit:10).isEmpty); XCTAssertEqual(registry.health.droppedEvents,0)
        let other = try WatchRegistry(authorization:.localAll,maxBytes:1000,maxEventBytes:300)
        let known = DisplaySchema(name:"x",source:SourceSite(file:"a",line:1,column:1))
        let knownID = try other.addValidated(config,snapshot:snapshot) { path,_ in InspectResult(snapshot:snapshot,schema:known,path:path,node:InspectNode(kind:.scalar(.unit))) }.watchID
        _ = try other.drain(limit:10)
        let grown = DisplaySchema(name:"x",source:SourceSite(file:String(repeating:"b",count:100),line:1,column:1))
        XCTAssertThrowsError(try other.snapshotNowPaged(knownID,context:context) { path,_,_ in InspectResult(snapshot:snapshot,schema:grown,path:path,node:InspectNode(kind:.scalar(.unit))) }) { guard case WatchError.capacity = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertTrue(try other.drain(limit:10).isEmpty)
    }
    func testDefaultEventLimitMatchesWatchConfigAndIsIndependentOfRing() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxBytes:100)
        var config = WatchConfig()
        XCTAssertEqual(config.limits.maxBytes,32 * 1024)
        XCTAssertNoThrow(try registry.add(config))
        config.limits.maxBytes += 1
        XCTAssertThrowsError(try registry.add(config))
        let legacy = try WatchRegistry(authorization:.localAll,maxBytes:64 * 1024,maxEventBytes:nil)
        XCTAssertNoThrow(try legacy.add(config))
    }
    func testEventAndRingByteLimitsAreIndependent() throws {
        let eventBound = try WatchRegistry(authorization:.localAll,maxBytes:1000,maxEventBytes:300)
        var config = WatchConfig(); config.limits.maxBytes = 300
        _ = try eventBound.add(config); _ = try eventBound.drain(limit:10)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        try eventBound.observe(snapshot:snapshot,origin:.simulation) { path,_ in
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:String(repeating:"x",count:100)),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(eventBound.health.droppedEvents,1); XCTAssertNil(try eventBound.latestEvent(1)); XCTAssertEqual(eventBound.health.truncated,0)
        let partial = try WatchRegistry(authorization:.localAll,maxBytes:1000,maxEventBytes:300)
        _ = try partial.add(config); _ = try partial.drain(limit:10)
        try partial.observe(snapshot:snapshot,origin:.simulation) { path,_ in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:String(repeating:"x",count:100)),path:path,node:InspectNode(kind:.scalar(.unit),completeness:.partial(.valueLimit)))
        }
        XCTAssertEqual(partial.health.droppedEvents,1); XCTAssertEqual(partial.health.truncated,1); XCTAssertNil(try partial.latestEvent(1))
        let ringBound = try WatchRegistry(authorization:.localAll,maxBytes:200,maxEventBytes:1000)
        config.limits.maxBytes = 500
        _ = try ringBound.add(config); _ = try ringBound.drain(limit:10)
        try ringBound.observe(snapshot:snapshot,origin:.simulation) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertEqual(ringBound.health.droppedEvents,1); XCTAssertNotNil(try ringBound.latestEvent(1))
        XCTAssertThrowsError(try WatchRegistry(authorization:.localAll,maxEventBytes:-1))
    }
    func testProjectionLimitsReserveKnownEventMetadata() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxBytes:2000,maxEventBytes:1000)
        var config = WatchConfig(); config.limits.maxBytes = 1000
        let id = try registry.add(config).watchID
        let snapshot = SnapshotID(session:1,revision:1,sequence:1), schema = DisplaySchema(name:String(repeating:"s",count:80))
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        context.declaredSchema = schema; context.model = String(repeating:"m",count:10); context.failingProperties = ["id"]
        var budgets: [Int] = []
        try registry.observePaged(context:context) { path,_,limits in
            budgets.append(limits.maxBytes); return InspectResult(snapshot:snapshot,schema:schema,path:path,node:try InspectContext(limits:limits).scalar(.unit))
        }
        _ = try registry.snapshotNowPaged(id,context:context) { path,_,limits in
            budgets.append(limits.maxBytes); return InspectResult(snapshot:snapshot,schema:schema,path:path,node:try InspectContext(limits:limits).scalar(.unit))
        }
        XCTAssertEqual(budgets,[756,756]); XCTAssertEqual(registry.health.droppedEvents,0)
    }
    func testWatchDropMarkerWaitsForSpaceWithoutEviction() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxRecords:2)
        _ = try registry.add(WatchConfig()); _ = try registry.add(WatchConfig())
        func observe(_ sequence: UInt64) throws {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        }
        try observe(1)
        XCTAssertEqual(registry.health.droppedEvents,2)
        XCTAssertEqual(try registry.drain(limit:2).map(\.kind),[.baseline,.baseline])
        try observe(2)
        let events = try registry.drain(limit:2)
        XCTAssertEqual(events.map(\.kind),[.eventsDropped(2)]); XCTAssertEqual(events[0].origin,.diagnosticControl)
        XCTAssertEqual(registry.health.droppedEvents,2)
        try observe(3); XCTAssertTrue(try registry.drain(limit:2).isEmpty)
    }
    func testWatchStatusDistinguishesDisabledPausedAndExpired() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var off = WatchConfig(); off.enabled = false
        let disabled = try registry.add(off).watchID, paused = try registry.add(WatchConfig()).watchID
        var finite = WatchConfig(); finite.ttlObservations = 1
        let expired = try registry.add(finite).watchID
        _ = try registry.pause(paused)
        XCTAssertEqual(try registry.list().map(\.state),[.disabled,.paused,.active])
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertEqual(try registry.list().map(\.state),[.disabled,.paused,.expired])
        _ = try registry.resume(expired); _ = try registry.setEnabled(paused,enabled:false); _ = try registry.resume(disabled)
        XCTAssertEqual(try registry.list().map(\.state),[.active,.disabled,.active])
    }
    func testLaterSchemaFailureInvalidatesEveryWatchAndRecovers() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig()); _ = try registry.add(WatchConfig())
        let first = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:first,origin:.simulation) { path,_ in InspectResult(snapshot:first,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        let second = SnapshotID(session:1,revision:2,sequence:2)
        var calls = 0
        XCTAssertThrowsError(try registry.observePaged(context:WatchObservationContext(snapshot:second,origin:.simulation),schema:{ calls += 1; if calls == 2 { throw InspectError.invalidMetadata }; return DisplaySchema(name:"x") }) { path,_,_ in InspectResult(snapshot:second,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) })
        registry.recordObservationGap(snapshot:second,origin:.simulation)
        XCTAssertEqual(registry.health.observationGaps,1); XCTAssertNil(try registry.latestEvent(1)); XCTAssertNil(try registry.latestEvent(2))
        let third = SnapshotID(session:1,revision:3,sequence:3)
        try registry.observe(snapshot:third,origin:.simulation) { path,_ in InspectResult(snapshot:third,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertEqual(try registry.latestEvent(1)?.baselineReason,.observationGap); XCTAssertEqual(try registry.latestEvent(2)?.baselineReason,.observationGap)
    }
    func testSharedProjectionKeepsEachDeclaredSchema() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig()); _ = try registry.add(WatchConfig())
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var schemas = 0,projections = 0
        try registry.observePaged(context:WatchObservationContext(snapshot:snapshot,origin:.simulation),schema:{ schemas += 1; return DisplaySchema(name:schemas == 1 ? "one" : "two") }) { path,_,_ in
            projections += 1; return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"one"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(schemas,2); XCTAssertEqual(projections,1)
        XCTAssertEqual(try registry.latestEvent(1)?.schema?.name,"one"); XCTAssertEqual(try registry.latestEvent(2)?.schema?.name,"two")
    }
    func testSchemaCallbackRunsPerSelectedWatchWhileProjectionIsShared() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        _ = try registry.add(WatchConfig()); _ = try registry.add(WatchConfig())
        var filtered = WatchConfig(); filtered.model = "other"; _ = try registry.add(filtered)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        let context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        var callbacks: [String] = []
        try registry.observePaged(context:context,schema:{ callbacks.append("schema"); return DisplaySchema(name:"x") }) { path,_,_ in
            callbacks.append("projection"); return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        XCTAssertEqual(callbacks,["schema","projection","schema"])
        XCTAssertEqual(try registry.list().prefix(2).map(\.hasBaseline),[true,true])
        let next = SnapshotID(session:1,revision:2,sequence:2)
        callbacks.removeAll()
        try registry.observePaged(context:WatchObservationContext(snapshot:next,origin:.simulation),schema:{ callbacks.append("schema"); return DisplaySchema(name:"x") }) { _,_,_ in
            callbacks.append("projection"); throw InspectError.pathNotFound
        }
        XCTAssertEqual(callbacks,["schema","projection","schema"])
        XCTAssertEqual(try registry.latestEvent(1)?.kind,.inspectorError); XCTAssertEqual(try registry.latestEvent(2)?.kind,.inspectorError)
    }
    func testFailedSchemaRecordsAtMostOneGapPerBoundary() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        func snapshot(_ sequence: UInt64) -> SnapshotID { SnapshotID(session:1,revision:sequence,sequence:sequence) }
        let first = snapshot(1)
        try registry.observe(snapshot:first,origin:.simulation) { path,_ in InspectResult(snapshot:first,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        _ = try registry.drain(limit:10)
        for sequence: UInt64 in [3,4] {
            let current = snapshot(sequence), context = WatchObservationContext(snapshot:current,origin:.simulation)
            XCTAssertThrowsError(try registry.observePaged(context:context,schema:{ throw InspectError.invalidMetadata }) { _,_,_ in XCTFail("unexpected projection"); throw InspectError.pathNotFound })
            registry.recordObservationGap(snapshot:current,origin:.simulation)
        }
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.kind),[.observationGap,.observationGap])
        XCTAssertEqual(registry.health.observationGaps,2)
    }
    func testTruncatedHealthCountsIncompleteEvaluationEvenWhenSuppressed() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .equals(.bool(true))
        _ = try registry.add(config); _ = try registry.drain(limit:10)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.string(preview:ExactString("a"),totalBytes:2),completeness:.partial(.valueLimit)))
        }
        XCTAssertEqual(registry.health.truncated,1); XCTAssertTrue(try registry.drain(limit:10).isEmpty)
        XCTAssertEqual(try registry.latestEvent(1)?.kind,.unknown)
    }
    func testDeclaredSchemaMismatchStopsBeforeProjection() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let first = SnapshotID(session:1,revision:1,sequence:1), second = SnapshotID(session:1,revision:2,sequence:2)
        var context = WatchObservationContext(snapshot:first,origin:.simulation); context.declaredSchema = DisplaySchema(name:"old")
        try registry.observe(context:context) { path,_ in InspectResult(snapshot:first,schema:DisplaySchema(name:"old"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        context = WatchObservationContext(snapshot:second,origin:.simulation); context.declaredSchema = DisplaySchema(name:"new")
        var calls = 0
        try registry.observe(context:context) { _,_ in calls += 1; throw InspectError.pathNotFound }
        XCTAssertEqual(calls,0); XCTAssertNil(try registry.latestEvent(1))
        let event = try XCTUnwrap(registry.drain(limit:10).last)
        XCTAssertEqual(event.kind,.schemaChanged); XCTAssertEqual(event.schema?.name,"old")
    }
    func testValidatedAdmissionPreflightsCapacityButInspectsBeforeRevision() throws {
        let snapshot = SnapshotID(session:1,revision:1,sequence:1), registry = try WatchRegistry(authorization:.localAll,maxWatches:1)
        _ = try registry.add(WatchConfig())
        var calls = 0
        func project(_ path: [PathSegment],_ page: PageRequest,_ limits: InspectLimits) throws -> InspectResult {
            calls += 1; throw InspectError.pathNotFound
        }
        XCTAssertThrowsError(try registry.addValidatedPaged(WatchConfig(),snapshot:snapshot,project:project)) { guard case WatchError.capacity = $0 else { return XCTFail("unexpected \($0)") } }
        var invalid = WatchConfig(); invalid.evaluationInterval = 0
        XCTAssertThrowsError(try registry.addValidatedPaged(invalid,snapshot:snapshot,project:project)) { guard case WatchError.invalidConfig = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(calls,0)
        let exhausted = try WatchRegistry(authorization:.localAll)
        try exhausted.observe(snapshot:SnapshotID(session:1,revision:1,sequence:.max),origin:.simulation) { _,_ in XCTFail("unexpected projection"); throw InspectError.pathNotFound }
        XCTAssertThrowsError(try exhausted.addValidatedPaged(WatchConfig(),snapshot:snapshot,project:project)) { guard case InspectError.pathNotFound = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(calls,1)
        let schema = DisplaySchema(name:"example")
        XCTAssertThrowsError(try exhausted.addValidatedPaged(WatchConfig(),snapshot:snapshot) { path,_,_ in
            calls += 1; return InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }) { guard case WatchError.revisionExhausted = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(calls,2); XCTAssertTrue(try exhausted.list().isEmpty)
        XCTAssertThrowsError(try exhausted.prepareProtocolAdd(WatchConfig(),snapshot:snapshot) { _,_ in
            calls += 1; throw InspectError.pathNotFound
        }) { guard case InspectError.pathNotFound = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertThrowsError(try exhausted.prepareProtocolAdd(WatchConfig(),snapshot:snapshot) { path,_ in
            calls += 1; return InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }) { guard case WatchError.revisionExhausted = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(calls,4); XCTAssertTrue(try exhausted.list().isEmpty)
        let updating = try WatchRegistry(authorization:.localAll)
        let ack = try updating.add(WatchConfig())
        try updating.observe(snapshot:SnapshotID(session:1,revision:1,sequence:.max),origin:.simulation) { path,_ in
            InspectResult(snapshot:SnapshotID(session:1,revision:1,sequence:.max),schema:schema,path:path,node:InspectNode(kind:.scalar(.unit)))
        }
        let generation = try XCTUnwrap(updating.list().first).generation
        XCTAssertThrowsError(try updating.updateValidatedPaged(ack.watchID,config:WatchConfig(),snapshot:snapshot,project:project)) { guard case InspectError.pathNotFound = $0 else { return XCTFail("unexpected \($0)") } }
        XCTAssertEqual(calls,5); XCTAssertEqual(try updating.list().first?.generation,generation)
    }
    func testValidatedLifecycleIncludesAdmittedSchema() throws {
        let registry = try WatchRegistry(authorization:.localAll), snapshot = SnapshotID(session:1,revision:1,sequence:1)
        let old = DisplaySchema(name:"old",source:SourceSite(file:"old.swift",line:1,column:1)), new = DisplaySchema(name:"new",source:SourceSite(file:"new.swift",line:2,column:1))
        let config = WatchConfig()
        let ack = try registry.addValidated(config,snapshot:snapshot) { path,_ in InspectResult(snapshot:snapshot,schema:old,path:path,node:InspectNode(kind:.scalar(.unit))) }
        XCTAssertEqual(ack.displaySchema,old); XCTAssertEqual(ack.resolvedType,"unit")
        XCTAssertEqual(try registry.list().first?.resolvedType,"unit")
        let updated = try registry.updateValidated(ack.watchID,config:config,snapshot:snapshot) { path,_ in InspectResult(snapshot:snapshot,schema:new,path:path,node:InspectNode(kind:.scalar(.bool(true)))) }
        XCTAssertEqual(updated.displaySchema,new); XCTAssertEqual(updated.resolvedType,"bool")
        XCTAssertEqual(try registry.list().first?.validatedSchema,new); XCTAssertEqual(try registry.list().first?.resolvedType,"bool")
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.kind),[.enabled,.enabled]); XCTAssertEqual(events.map(\.schema),[old,new])
    }
    func testWatchComparisonIgnoresSchemaSourceOnlyChanges() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let one = SnapshotID(session:1,revision:1,sequence:1), two = SnapshotID(session:1,revision:2,sequence:2)
        let first = InspectResult(snapshot:one,schema:DisplaySchema(name:"x",source:SourceSite(file:"old.swift",line:1,column:1)),node:InspectNode(kind:.scalar(.bool(true))))
        let second = InspectResult(snapshot:two,schema:DisplaySchema(name:"x",source:SourceSite(file:"new.swift",line:2,column:1)),node:InspectNode(kind:.scalar(.bool(true))))
        XCTAssertEqual(diff(first,second),.unknown)
        try registry.observe(snapshot:one,origin:.simulation) { _,_ in first }
        try registry.observe(snapshot:two,origin:.simulation) { _,_ in second }
        XCTAssertEqual(try registry.latestEvent(1)?.kind,.unchanged)
        XCTAssertEqual(try registry.latestEvent(1)?.difference,.unchanged)
        XCTAssertEqual(try registry.drain(limit:10).map(\.kind),[.enabled,.baseline])
    }
    func testChangedWatchEmitsUnchangedValueAfterSkippedObservation() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 2
        _ = try registry.add(config)
        for sequence: UInt64 in 1...3 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in
                InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true))))
            }
        }
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.changeSinceLastSample(false)])
        XCTAssertEqual(events.last?.completeness,.partial(.sampled)); XCTAssertEqual(events.last?.difference,.unknown)
    }
    func testInspectionErrorsHonorTriggersDeliveryAndRecovery() throws {
        for trigger: WatchTrigger in [.changed,.everyObservation,.equals(.bool(true)),.propertyFailure] {
            let registry = try WatchRegistry(authorization:.localAll)
            var config = WatchConfig(); config.trigger = trigger; config.deliveryInterval = 2; config.ttlObservations = 3
            let id = try registry.add(config).watchID
            func observe(_ sequence: UInt64,_ failure: Bool,_ projectionError: Bool) throws {
                let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
                try registry.observe(snapshot:snapshot,origin:.simulation,propertyFailure:failure) { path,_ in
                    if projectionError { throw InspectError.pathNotFound }
                    return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true))))
                }
            }
            try observe(1,false,false); _ = try registry.drain(limit:20)
            try observe(2,false,true)
            XCTAssertEqual(try registry.latestEvent(id)?.kind,.inspectorError)
            let errorEvents = try registry.drain(limit:20)
            switch trigger {
            case .changed,.everyObservation:
                XCTAssertTrue(errorEvents.isEmpty); XCTAssertEqual(registry.health.deliverySampled,1)
            case .equals,.propertyFailure:
                XCTAssertTrue(errorEvents.isEmpty); XCTAssertEqual(registry.health.deliverySampled,0)
            }
            try observe(3,true,false)
            let events = try registry.drain(limit:20)
            XCTAssertEqual(events.map(\.kind),[.propertyFailure,.expired])
            XCTAssertEqual(events[0].baselineReason,.observationGap)
        }
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .propertyFailure; config.ttlObservations = 1
        let id = try registry.add(config).watchID
        try registry.observe(snapshot:SnapshotID(session:1,revision:1,sequence:1),origin:.simulation,propertyFailure:true) { _,_ in throw InspectError.pathNotFound }
        XCTAssertEqual(try registry.drain(limit:20).map(\.kind),[.enabled,.inspectorError,.expired])
        XCTAssertEqual(try registry.latestEvent(id)?.kind,.inspectorError)
    }
    func testConsecutiveFailureBypassesDoNotImplySampling() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 3; config.trigger = .everyObservation
        _ = try registry.add(config)
        for sequence: UInt64 in 1...2 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            try registry.observe(snapshot:snapshot,origin:.simulation,propertyFailure:true) { path,_ in
                InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(sequence == 2))))
            }
        }
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.suffix(2).map(\.kind),[.propertyFailure,.propertyFailure])
        XCTAssertEqual(events.last?.completeness,.complete); XCTAssertEqual(events.last?.difference,.changed)
        XCTAssertEqual(registry.health.evaluationSampled,0)
    }
    func testEventCompletenessPrioritizesSamplingAndDeclaredNodeCompleteness() throws {
        let sampled = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 2; config.trigger = .everyObservation
        _ = try sampled.add(config)
        for sequence: UInt64 in 1...3 {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            if sequence == 3 { context.propertyFailure = true; context.failingProperties = Array(repeating:"id",count:17) }
            try sampled.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(sequence == 3)))) }
        }
        let events = try sampled.drain(limit:20)
        XCTAssertEqual(events.last?.kind,.propertyFailure); XCTAssertEqual(events.last?.completeness,.partial(.sampled))
        let partial = try WatchRegistry(authorization:.localAll); _ = try partial.add(WatchConfig())
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation); context.failingProperties = Array(repeating:"id",count:17)
        try partial.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.opaque(ExactString("opaque")),completeness:.complete)) }
        XCTAssertEqual(try partial.latestEvent(1)?.completeness,.partial(.valueLimit))
    }
    func testFailingPropertyIDsAreBoundedAndAccounted() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        _ = try registry.add(WatchConfig()); _ = try registry.drain(limit:10)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        context.propertyFailure = true
        context.failingProperties = [String(repeating:"é",count:129)] + (1...16).map { "property-\($0)" }
        try registry.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true)))) }
        let event = try XCTUnwrap(registry.latestEvent(1))
        XCTAssertEqual(event.failingProperties.count,16); XCTAssertEqual(event.failingProperties[0],String(repeating:"é",count:128))
        XCTAssertEqual(event.failingProperties.last,"property-15"); XCTAssertEqual(event.completeness,.partial(.valueLimit))
        XCTAssertGreaterThan(try registry.snapshot().retainedBytes,256)
    }
    func testControlSnapshotReportsIncompleteMetadata() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        for omitted in [false,true] {
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            context.failingPropertiesComplete = !omitted
            if !omitted { context.failingProperties = Array(repeating:"id",count:17) }
            let event = try registry.snapshotNow(1,context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
            XCTAssertEqual(event.completeness,.partial(.valueLimit))
        }
    }
    func testLongObservationLabelsAreBoundedAndPartial() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
        context.model = String(repeating:"é",count:513); context.machine = String(repeating:"m",count:1025)
        try registry.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        let event = try XCTUnwrap(registry.latestEvent(1))
        XCTAssertEqual(event.model,String(repeating:"é",count:512)); XCTAssertEqual(event.machine,String(repeating:"m",count:1024))
        XCTAssertEqual(event.completeness,.partial(.valueLimit))
    }
    func testNoActiveWatchesAdvanceWithoutGapAndRejectStale() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        let id = try registry.add(WatchConfig()).watchID
        _ = try registry.pause(id); _ = try registry.drain(limit:10)
        var calls = 0
        func observe(_ sequence: UInt64) throws {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in
                calls += 1; return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true))))
            }
        }
        try observe(1); try observe(3); try observe(2)
        XCTAssertEqual(calls,0); XCTAssertEqual(registry.health.observationGaps,0); XCTAssertEqual(registry.health.filtered,0)
        XCTAssertTrue(try registry.drain(limit:10).isEmpty)
        XCTAssertEqual(try registry.resume(id).effectiveSequence,4)
        try observe(4)
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline]); XCTAssertEqual(calls,1)
        try observe(4); XCTAssertEqual(calls,1); XCTAssertEqual(registry.health.filtered,0)
    }
    func testGapLifecycleIncludesPausedWatchesAndPriorBoundary() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.model = "configured"; config.machine = "machine"
        let active = try registry.add(config).watchID, paused = try registry.add(config).watchID
        _ = try registry.pause(paused)
        func observe(_ sequence: UInt64) throws {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation); context.model = "configured"; context.machine = "machine"
            try registry.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true)))) }
        }
        try observe(1); _ = try registry.drain(limit:20)
        try observe(3)
        let events = try registry.drain(limit:20)
        XCTAssertEqual(events.map(\.kind),[.observationGap,.observationGap,.baseline])
        XCTAssertEqual(events.prefix(2).map(\.watchID),[active,paused])
        for event in events.prefix(2) {
            XCTAssertEqual(event.snapshot,SnapshotID(session:1,revision:1,sequence:1))
            XCTAssertEqual(event.origin,.diagnosticControl); XCTAssertEqual(event.phase,.control)
            XCTAssertEqual(event.model,"configured"); XCTAssertEqual(event.machine,"machine")
        }
        XCTAssertNil(events.last?.previousSnapshot)
    }
    func testObservationMetadataAndSchemaChangeRetainsPriorSchema() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.trigger = .everyObservation; config.model = "model"; config.machine = "machine"
        let id = try registry.add(config).watchID
        let first = SnapshotID(session:1,revision:1,sequence:1), second = SnapshotID(session:1,revision:2,sequence:2), third = SnapshotID(session:1,revision:3,sequence:3)
        let old = DisplaySchema(name:"old"), new = DisplaySchema(name:"new")
        func observe(_ snapshot: SnapshotID,_ schema: DisplaySchema,_ failure: Bool) throws {
            var context = WatchObservationContext(snapshot:snapshot,origin:.simulation)
            context.model = "model"; context.machine = "machine"; context.propertyFailure = failure
            try registry.observe(context:context) { path,_ in InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.bool(true)))) }
        }
        try observe(first,old,false); try observe(second,old,true); try observe(third,new,false)
        let events = try registry.drain(limit:20)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.propertyFailure,.schemaChanged])
        XCTAssertEqual(events[2].model,"model"); XCTAssertEqual(events[2].machine,"machine")
        XCTAssertEqual(events[2].previousSnapshot,first); XCTAssertTrue(events[2].propertyFailure)
        XCTAssertEqual(events[3].schema,old); XCTAssertEqual(events[3].origin,.diagnosticControl); XCTAssertEqual(events[3].phase,.control)
        XCTAssertNil(try registry.latestEvent(id))
    }
    func testEventMetadataAndRetentionAccounting() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.path = [.field(ExactString("field"))]; config.trigger = .everyObservation
        let id = try registry.add(config).watchID
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        let schema = DisplaySchema(name:"example",source:SourceSite(file:"model.swift",line:1,column:2))
        try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in
            InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.bool(true))))
        }
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.phase),[.control,.postTurn])
        XCTAssertEqual(events.map(\.path),[config.path,config.path])
        XCTAssertEqual(events.map(\.trigger),[.everyObservation,.everyObservation])
        XCTAssertNil(events[0].schema); XCTAssertEqual(events[1].schema,schema)
        let captured = try registry.snapshotNow(id,context:WatchObservationContext(snapshot:snapshot,origin:.simulation)) { path,_ in
            InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.bool(true))))
        }
        XCTAssertEqual(captured.phase,.control); XCTAssertEqual(captured.schema,schema)
        XCTAssertGreaterThan(try registry.snapshot().retainedBytes,128)
    }
    func testLifecycleEventsAndEffectiveBoundaries() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        let added = try registry.add(WatchConfig()), initial = SnapshotID(session:1,revision:7,sequence:7)
        try registry.observe(snapshot:initial,origin:.simulation) { path,_ in InspectResult(snapshot:initial,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) }
        let paused = try registry.pause(added.watchID); XCTAssertEqual(paused.effectiveSequence,8)
        let resumed = try registry.resume(added.watchID); XCTAssertEqual(resumed.effectiveSequence,8)
        var disabled = WatchConfig(); disabled.enabled = false
        let updated = try registry.update(added.watchID,config:disabled); XCTAssertEqual(updated.effectiveSequence,8)
        try registry.remove(added.watchID)
        let events = try registry.drain(limit:100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.paused,.enabled,.disabled,.removed])
        XCTAssertEqual(events.map(\.generation),[1,1,2,3,4,5])
        XCTAssertEqual(events[0].snapshot,SnapshotID(session:0,revision:0,sequence:0))
        XCTAssertEqual(events.last?.snapshot,initial); XCTAssertEqual(events.last?.origin,.diagnosticControl)
    }
    func testIncompleteObservationIsUnknownAndNextCompleteIsFreshBaseline() throws {
        let registry = try WatchRegistry(authorization:.localAll); _ = try registry.add(WatchConfig())
        let first = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:first,origin:.simulation) { path,_ in
            InspectResult(snapshot:first,schema:DisplaySchema(name:"items"),path:path,node:InspectNode(kind:.string(preview:ExactString("x"),totalBytes:2),childCount:0,completeness:.partial(.valueLimit)))
        }
        XCTAssertFalse(try registry.list()[0].hasBaseline); XCTAssertFalse(registry.protocolStatuses(limit:1)[0].hasBaseline)
        let next = SnapshotID(session:1,revision:2,sequence:2)
        try registry.observe(snapshot:next,origin:.simulation) { path,_ in
            InspectResult(snapshot:next,schema:DisplaySchema(name:"items"),path:path,node:InspectNode(kind:.string(preview:ExactString("xy"),totalBytes:2),childCount:0))
        }
        XCTAssertTrue(try registry.list()[0].hasBaseline); XCTAssertTrue(registry.protocolStatuses(limit:1)[0].hasBaseline)
        let events = try registry.drain(limit:100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.unknown,.baseline]); XCTAssertEqual(events.last?.baselineReason,.observationGap)
    }
    func testFinalObservationInspectionFailureExpiresAndRetainsFailureEvent() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.ttlObservations = 1
        let ack = try registry.add(config), snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var calls = 0
        try registry.observe(snapshot:snapshot,origin:.simulation) { _,_ in calls += 1; throw InspectError.pathNotFound }
        XCTAssertFalse(registry.interested); XCTAssertFalse(try registry.list()[0].enabled)
        XCTAssertFalse(try registry.list()[0].hasBaseline); XCTAssertNil(try registry.current(ack.watchID))
        XCTAssertEqual(try registry.latestEvent(ack.watchID)?.kind,.inspectorError)
        let events = try registry.drain(limit:100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.inspectorError,.expired])
        XCTAssertEqual(events[1].origin,.simulation); XCTAssertEqual(events[2].origin,.diagnosticControl); XCTAssertEqual(events[2].snapshot,snapshot)
        try registry.observe(snapshot:SnapshotID(session:1,revision:2,sequence:2),origin:.simulation) { _,_ in calls += 1; throw InspectError.pathNotFound }
        XCTAssertEqual(calls,1)
    }
    func testInterestAndLatestEventExposeInspectionFailureWithoutBaseline() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        XCTAssertFalse(registry.interested)
        let ack = try registry.add(WatchConfig()); XCTAssertTrue(registry.interested)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:snapshot,origin:.simulation) { _,_ in throw InspectError.pathNotFound }
        XCTAssertNil(try registry.current(ack.watchID))
        let event = try XCTUnwrap(registry.latestEvent(ack.watchID))
        XCTAssertEqual(event.kind,.inspectorError); XCTAssertEqual(event.inspectionError,"PathNotFound")
        XCTAssertThrowsError(try registry.latestEvent(999))
        _ = try registry.pause(ack.watchID); XCTAssertFalse(registry.interested)
        _ = try registry.resume(ack.watchID); XCTAssertTrue(registry.interested)
    }
    func testSafePointSnapshotDoesNotChangeBaselineOrObservationOrder() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.page = PageRequest(offset:1,limit:1)
        let ack = try registry.add(config), initial = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observePaged(snapshot:initial,origin:.simulation) { path,page,limits in
            let node = try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { _,c in try c.scalar(.bool(false)) }
            return InspectResult(snapshot:initial,schema:DisplaySchema(name:"items"),path:path,node:node)
        }
        let before = try XCTUnwrap(registry.current(ack.watchID))
        let selected = SnapshotID(session:1,revision:99,sequence:99), context = WatchObservationContext(snapshot:selected,origin:.diagnosticControl)
        let event = try registry.snapshotNowPaged(ack.watchID,context:context) { path,page,limits in
            XCTAssertEqual(page,config.page)
            let node = try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { _,c in try c.scalar(.bool(true)) }
            return InspectResult(snapshot:selected,schema:DisplaySchema(name:"items"),path:path,node:node)
        }
        XCTAssertEqual(event.kind,.snapshot); XCTAssertEqual(event.snapshot,selected)
        XCTAssertEqual(try registry.current(ack.watchID)?.node,before.node)
        XCTAssertEqual(try registry.snapshot().at,initial)
        let next = SnapshotID(session:1,revision:2,sequence:2)
        try registry.observePaged(snapshot:next,origin:.simulation) { path,page,limits in
            InspectResult(snapshot:next,schema:DisplaySchema(name:"items"),path:path,node:try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { _,c in try c.scalar(.bool(false)) })
        }
        XCTAssertEqual(registry.health.observationGaps,0)
        _ = try registry.pause(ack.watchID)
        XCTAssertNoThrow(try registry.snapshotNowPaged(ack.watchID,context:context) { path,_,_ in InspectResult(snapshot:selected,schema:DisplaySchema(name:"items"),path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) })
    }
    func testFailureSnapshotIsBoundedAndSurvivesRingDrain() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxRecords:2,maxBytes:4096)
        var config = WatchConfig(); config.trigger = .propertyFailure; config.limits.maxBytes = 4096
        _ = try registry.add(config)
        let snapshot = SnapshotID(session:1,revision:1,sequence:1)
        var reentrantRejected = false
        try registry.observe(snapshot:snapshot,origin:.simulation,propertyFailure:true) { path,_ in
            do { _ = try registry.snapshot(); XCTFail("snapshot should reject an in-progress observation") }
            catch DebugError.reentrant { reentrantRejected = true }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"counter"),path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0))
        }
        XCTAssertTrue(reentrantRejected)
        let saved = try XCTUnwrap(registry.failureSnapshot)
        XCTAssertEqual(saved.at,snapshot); XCTAssertEqual(saved.events.map(\.kind),[.enabled,.propertyFailure])
        XCTAssertGreaterThan(saved.retainedBytes,0); XCTAssertLessThanOrEqual(saved.retainedBytes,4096)
        XCTAssertEqual(try registry.drain(limit:2).count,2); XCTAssertTrue(try registry.snapshot().events.isEmpty)
        XCTAssertEqual(registry.failureSnapshot?.events.count,2)
        try registry.clearFailureSnapshot(); XCTAssertNil(registry.failureSnapshot)
    }
    func testValidatedWatchRejectsInvalidSchemaBeforeAddOrUpdate() throws {
        let registry = try WatchRegistry(authorization:.localAll),snapshot = SnapshotID(session:1,revision:0,sequence:0)
        let invalid = [DisplaySchema(name:""),DisplaySchema(name:String(repeating:"x",count:4097)),DisplaySchema(name:"valid",source:SourceSite(file:String(repeating:"x",count:4097),line:1,column:1))]
        for schema in invalid {
            XCTAssertThrowsError(try registry.addValidatedPaged(WatchConfig(),snapshot:snapshot) { path,_,_ in InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) })
            XCTAssertEqual(registry.captureRevision,0)
        }
        let ack = try registry.add(WatchConfig())
        for schema in invalid {
            XCTAssertThrowsError(try registry.updateValidatedPaged(ack.watchID,config:WatchConfig(),snapshot:snapshot) { path,_,_ in InspectResult(snapshot:snapshot,schema:schema,path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) })
            XCTAssertEqual(registry.captureRevision,1); XCTAssertEqual(try registry.list()[0].generation,1)
        }
    }
    func testValidatedUpdateIsAtomicAndClearsOldSchemaOnUnvalidatedUpdate() throws {
        let registry = try WatchRegistry(authorization:.localAll), snapshot = SnapshotID(session:1,revision:0,sequence:0)
        let ack = try registry.addValidated(WatchConfig(),snapshot:snapshot) { path,_ in InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"old"),path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) }
        var config = WatchConfig(); config.page = PageRequest(offset:1,limit:1)
        XCTAssertThrowsError(try registry.updateValidatedPaged(ack.watchID,config:config,snapshot:snapshot) { _,_,_ in throw InspectError.pathNotFound })
        XCTAssertEqual(registry.captureRevision,1); XCTAssertEqual(try registry.list()[0].generation,1)
        var called = false
        XCTAssertThrowsError(try registry.updateValidated(ack.watchID,config:config,snapshot:snapshot) { _,_ in called = true; throw InspectError.pathNotFound })
        XCTAssertFalse(called); XCTAssertEqual(registry.captureRevision,1)
        let updated = try registry.updateValidatedPaged(ack.watchID,config:config,snapshot:snapshot) { path,page,limits in
            XCTAssertEqual(page,config.page)
            let node = try InspectContext(limits:limits).sequence(name:"new",total:2,page:page) { _,c in try c.scalar(.bool(true)) }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"new"),path:path,node:node)
        }
        XCTAssertEqual(updated.generation,2); XCTAssertEqual(registry.captureRevision,2)
        try registry.observePaged(snapshot:snapshot,origin:.simulation) { path,page,limits in
            InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"new"),path:path,node:try InspectContext(limits:limits).sequence(name:"new",total:2,page:page) { _,c in try c.scalar(.bool(true)) })
        }
        XCTAssertNotNil(try registry.current(ack.watchID))
        _ = try registry.update(ack.watchID,config:WatchConfig())
        let next = SnapshotID(session:1,revision:1,sequence:1)
        try registry.observe(snapshot:next,origin:.simulation) { path,_ in InspectResult(snapshot:next,schema:DisplaySchema(name:"different"),path:path,node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) }
        XCTAssertTrue(try registry.list()[0].enabled); XCTAssertNotNil(try registry.current(ack.watchID))
    }
    func testValidatedWatchRequiresPageAwareProjectionForCustomPage() throws {
        let registry = try WatchRegistry(authorization:.localAll), snapshot = SnapshotID(session:1,revision:0,sequence:0)
        var config = WatchConfig(); config.page = PageRequest(offset:1,limit:1)
        var calls = 0
        XCTAssertThrowsError(try registry.addValidated(config,snapshot:snapshot) { _,_ in calls += 1; throw InspectError.invalidMetadata }) { error in
            guard case WatchError.invalidConfig = error else { return XCTFail("expected invalid config") }
        }
        XCTAssertEqual(calls,0); XCTAssertEqual(registry.captureRevision,0)
        let ack = try registry.addValidatedPaged(config,snapshot:snapshot) { path,page,limits in
            calls += 1; XCTAssertEqual(page,config.page)
            let node = try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { index,c in try c.scalar(.integer(kind:ExactString("u8"),decimal:ExactString(String(index)))) }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"items"),path:path,node:node)
        }
        XCTAssertEqual(calls,1); XCTAssertEqual(ack.watchID,1); XCTAssertEqual(registry.captureRevision,1)
    }
    func testWatchProjectionCacheIncludesConfiguredPage() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var first = WatchConfig(); first.page = PageRequest(offset:0,limit:1)
        var second = WatchConfig(); second.page = PageRequest(offset:1,limit:1)
        let one = try registry.add(first),two = try registry.add(second),three = try registry.add(first)
        let snapshot = SnapshotID(session:1,revision:0,sequence:0); var projected: [Int] = []
        XCTAssertThrowsError(try registry.observe(snapshot:snapshot,origin:.simulation) { _,_ in throw InspectError.invalidMetadata })
        try registry.observePaged(snapshot:snapshot,origin:.simulation) { path,page,limits in
            projected.append(page.offset)
            let node = try InspectContext(limits:limits).sequence(name:"items",total:2,page:page) { index,c in try c.scalar(.integer(kind:ExactString("u8"),decimal:ExactString(String(index)))) }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"items"),path:path,node:node)
        }
        XCTAssertEqual(projected,[0,1]); XCTAssertEqual(try registry.current(one.watchID)?.node.page?.offset,0)
        XCTAssertEqual(try registry.current(two.watchID)?.node.page?.offset,1); XCTAssertEqual(try registry.current(three.watchID)?.node.page?.offset,0)
        var invalid = WatchConfig(); invalid.page = PageRequest(offset:0,limit:0); XCTAssertThrowsError(try registry.add(invalid))
    }
    func testMapKeyAliasesShareAuthorizationWithoutUnicodeNormalization() throws {
        let allowed = PathSegment.mapKey(ExactString("clé"))
        let same = PathSegment.typedMapKey(.string(ExactString("clé")))
        let other = PathSegment.typedMapKey(.string(ExactString("cle\u{301}")))
        let authorization = WatchAuthorization(allowedPaths:[[allowed]])
        XCTAssertTrue(authorization.allows([same,.field(ExactString("child"))]))
        XCTAssertFalse(authorization.allows([other]))
        let registry = try WatchRegistry(authorization:authorization)
        var config = WatchConfig(); config.path = [same]
        XCTAssertNoThrow(try registry.add(config))
        config.path = [other]; XCTAssertThrowsError(try registry.add(config))
    }
    func testCurrentAndBaselinesShareRetentionBudget() throws {
        let registry = try WatchRegistry(authorization: .localAll,maxBaselineBytes: 300)
        let first = try registry.add(WatchConfig()),second = try registry.add(WatchConfig())
        let snapshot = SnapshotID(session: 1,revision: 1,sequence: 1)
        try registry.observe(snapshot: snapshot,origin: .simulation) { path,_ in
            InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true))))
        }
        // Tied snapshot sequences evict the lower watch ID first.
        XCTAssertNil(try registry.current(first.watchID))
        XCTAssertNotNil(try registry.current(second.watchID))
        XCTAssertNil(registry.currentEvent(first.watchID)); XCTAssertNotNil(registry.currentEvent(second.watchID))
        XCTAssertEqual(registry.health.baselineEvictions,1)
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.enabled,.enabled,.baseline,.baseline])

        let zero = try WatchRegistry(authorization: .localAll,maxBaselineBytes: 0)
        let ack = try zero.add(WatchConfig())
        try zero.observe(snapshot: snapshot,origin: .simulation) { _,_ in throw InspectError.pathNotFound }
        XCTAssertNil(zero.currentEvent(ack.watchID))
        XCTAssertEqual(zero.health.baselineEvictions,1)
        XCTAssertEqual(try zero.drain(limit: 100).map(\.kind),[.enabled,.inspectorError])
    }
    func testNoInterestSkipsProjectionAndConfigurationAuthority() throws {
        let registry = try WatchRegistry(authorization: .denyAll)
        var called = 0
        try registry.observe(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),origin: .simulation) { _,_ in called += 1; throw ModelError("unexpected") }
        XCTAssertEqual(called,0)
        XCTAssertThrowsError(try registry.add(WatchConfig()))
    }
    func testRevisionSamplingBaselinesAndBoundedLoss() throws {
        let registry = try WatchRegistry(authorization: .localAll,maxRecords: 1)
        var config = WatchConfig(); config.evaluationInterval = 2
        let ack = try registry.add(config); XCTAssertEqual(ack.captureRevision,1)
        var calls = 0
        for n: UInt64 in 1...5 {
            let snapshot = SnapshotID(session: UInt64.max,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .simulation) { path,limits in
                calls += 1
                return InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "counter"),path: path,node: try InspectContext(limits: limits).scalar(.integer(kind: ExactString("u64"),decimal: ExactString(String(n)))))
            }
        }
        XCTAssertEqual(calls,3); XCTAssertEqual(registry.health.evaluationSampled,2); XCTAssertEqual(registry.health.droppedEvents,3)
        let event = try registry.drain(limit: 1).first
        XCTAssertEqual(event?.kind,.changeSinceLastSample(true)); XCTAssertEqual(event?.captureRevision,1)
        let updated = try registry.setEnabled(ack.watchID,enabled: false); XCTAssertEqual(updated.captureRevision,2); XCTAssertEqual(updated.generation,2)
    }
    func testIncompleteProjectionNeverEqualAndGapResetsBaseline() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        _ = try registry.add(WatchConfig())
        for n: UInt64 in [1,2,4] {
            let snapshot = SnapshotID(session: 1,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .exactReplay) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "redacted"),path: path,node: InspectNode(kind: .redacted,completeness: .partial(.redacted))) }
        }
        let events = try registry.drain(limit: 100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.unknown,.unknown,.observationGap,.unknown]); XCTAssertEqual(registry.health.observationGaps,1)
    }
    func testWatchOrderingSurvivesRemovalAndReaddition() throws {
        let registry = try WatchRegistry(authorization:.localAll,maxWatches:4)
        let first = try registry.add(WatchConfig()),second = try registry.add(WatchConfig()),third = try registry.add(WatchConfig())
        try registry.remove(second.watchID)
        let fourth = try registry.add(WatchConfig())
        XCTAssertEqual(try registry.list().map(\.id),[first.watchID,third.watchID,fourth.watchID])
        XCTAssertEqual(try registry.list(offset:1,limit:1).map(\.id),[third.watchID])
        XCTAssertThrowsError(try registry.remove(second.watchID))
        XCTAssertEqual(try registry.list().map(\.id),[first.watchID,third.watchID,fourth.watchID])
    }
    func testGapInvalidatesFilteredAndSampledWatches() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.model = "selected"; config.evaluationInterval = 2
        let id = try registry.add(config).watchID
        func observe(_ sequence: UInt64,_ model: String) throws {
            var context = WatchObservationContext(snapshot:SnapshotID(session:1,revision:sequence,sequence:sequence),origin:.simulation); context.model = model
            try registry.observe(context:context) { path,_ in InspectResult(snapshot:context.snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true)))) }
        }
        try observe(1,"selected")
        try observe(3,"other") // Gap occurs while selector does not match.
        XCTAssertNil(registry.currentEvent(id))
        try observe(4,"selected") // Selected but skipped by evaluation sampling.
        try observe(5,"selected")
        let events = try registry.drain(limit:100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.observationGap,.baseline])
        XCTAssertEqual(events.last?.baselineReason,.observationGap)
        XCTAssertNil(events.last?.previousSnapshot)
        XCTAssertEqual(registry.health.observationGaps,1)
    }
    // Rust watches.rs observe: TTL is checked before selector matching, so an
    // expired watch emits one WatchExpired and is never counted as filtered.
    func testTTLExpiryPrecedesSelectorMatching() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var finite = WatchConfig(); finite.ttlObservations = 1; finite.model = "m"
        let id = try registry.add(finite).watchID
        var other = WatchConfig(); other.model = "other"; _ = try registry.add(other)
        for sequence: UInt64 in [1,2,3] {
            var context = WatchObservationContext(snapshot:SnapshotID(session:1,revision:sequence,sequence:sequence),origin:.simulation)
            context.model = sequence == 1 ? "m" : "different"
            try registry.observe(context:context) { path,_ in InspectResult(snapshot:context.snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.unit))) }
        }
        XCTAssertEqual(registry.health.filtered,3)
        XCTAssertEqual(try registry.list().first?.state,.expired)
        let events = try registry.drain(limit:100).filter { $0.watchID == id }
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.expired])
    }
    func testSampledOutFinalObservationExpiresImmediately() throws {
        let registry = try WatchRegistry(authorization:.localAll)
        var config = WatchConfig(); config.evaluationInterval = 2; config.ttlObservations = 2
        let id = try registry.add(config).watchID
        var projections = 0
        for sequence: UInt64 in [1,2] {
            let snapshot = SnapshotID(session:1,revision:sequence,sequence:sequence)
            try registry.observe(snapshot:snapshot,origin:.simulation) { path,_ in
                projections += 1
                return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"x"),path:path,node:InspectNode(kind:.scalar(.bool(true))))
            }
        }
        XCTAssertEqual(projections,1); XCTAssertEqual(registry.health.evaluationSampled,1)
        XCTAssertEqual(try registry.list().first?.enabled,false); XCTAssertNotNil(try registry.current(id)); XCTAssertTrue(try XCTUnwrap(registry.list().first).hasBaseline)
        let events = try registry.drain(limit:10)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.expired]); XCTAssertEqual(events.last?.snapshot.sequence,2)
        XCTAssertEqual(events.last?.origin,.diagnosticControl)
    }
    func testValidatedRedactionTTLAndUpdate() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        let snapshot = SnapshotID(session: 1,revision: 1,sequence: 1)
        XCTAssertThrowsError(try registry.addValidated(WatchConfig(),snapshot: snapshot) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "secret"),path: path,node: InspectNode(kind: .redacted)) })
        var config = WatchConfig(); config.ttlObservations = 0
        XCTAssertThrowsError(try registry.add(config))
        config.ttlObservations = 1
        let ack = try registry.add(config)
        try registry.observe(snapshot: snapshot,origin: .simulation) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        let events = try registry.drain(limit: 100)
        XCTAssertEqual(events.map(\.kind),[.enabled,.baseline,.expired])
        XCTAssertEqual(events[1].origin,.simulation); XCTAssertEqual(events[2].origin,.diagnosticControl); XCTAssertEqual(events[2].snapshot,snapshot)
        XCTAssertEqual(try registry.list().first?.enabled,false); XCTAssertNotNil(try registry.current(ack.watchID)); XCTAssertTrue(try XCTUnwrap(registry.list().first).hasBaseline)
        config.ttlObservations = nil
        XCTAssertEqual(try registry.update(ack.watchID,config: config).generation,2)
    }
    func testSelectorSharedProjectionSourceAndSchemaBoundaries() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        var config = WatchConfig(); config.model = "selected"
        _ = try registry.add(config); _ = try registry.add(config)
        var calls = 0
        let ignored = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),origin: .simulation)
        try registry.observe(context: ignored) { _,_ in calls += 1; throw ModelError("filtered") }
        XCTAssertEqual(calls,0)
        var selected = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 2,sequence: 2),origin: .simulation); selected.model = "selected"
        try registry.observe(context: selected) { path,_ in calls += 1; return InspectResult(snapshot: selected.snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        XCTAssertEqual(calls,1)
        var next = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 3,sequence: 3),origin: .exactReplay); next.model = "selected"
        try registry.observe(context: next) { path,_ in InspectResult(snapshot: next.snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        let events = try registry.drain(limit: 100)
        XCTAssertEqual(events.suffix(2).map(\.baselineReason),[.sourceChanged,.sourceChanged])
        var changed = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 4,sequence: 4),origin: .exactReplay); changed.model = "selected"
        try registry.observe(context: changed) { path,_ in InspectResult(snapshot: changed.snapshot,schema: DisplaySchema(name: "y"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.schemaChanged,.schemaChanged])
    }
    func testPropertyFailureBypassesEvaluationAndDeliverySampling() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        var config = WatchConfig(); config.evaluationInterval = 10; config.deliveryInterval = 10
        _ = try registry.add(config)
        for n: UInt64 in 1...2 {
            let snapshot = SnapshotID(session: 1,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .simulation,propertyFailure: n == 2) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        }
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.enabled,.baseline,.propertyFailure])
    }
}
