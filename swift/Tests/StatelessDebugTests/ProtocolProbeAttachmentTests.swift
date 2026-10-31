import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class ProtocolProbeAttachmentTests: XCTestCase {
    func testProfileCatalogueIsBoundedBeforeAttachment() throws {
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal)
        let profile = ProtocolProbeProfile(id:2,label:"marker",subscription:ProbeSubscription(sink:9,site:String(repeating:"x",count:128),kind:.marker))
        var limits = ProtocolLimits(); limits.maxCandidateBytes = 300
        XCTAssertThrowsError(try diagnosticProbeHost(hub:hub,profiles:[profile],sinks:[9],limits:limits)) { error in
            XCTAssertEqual(error as? ProtocolError,.limit)
        }
        XCTAssertEqual(hub.captureRevision,0)
        XCTAssertEqual(hub.queued(sink:9)?.records,0)
    }
    func testProducerPagesPreserveSortedIdentityAndPendingCount() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal)
        for id in [UInt64(30),10,20] { try hub.registerProducer(id:id,epoch:1) }
        let host = try diagnosticProbeHost(hub:hub,profiles:[],sinks:[9])
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let p = try ProtocolAdapter(session:session,authority:authority,host:host)
        let configured = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"probe_configure",arguments:["u64:0"]))
        XCTAssertNil(configured.error); XCTAssertEqual(configured.fields["probes.pending_producers.0"],"u64:10"); XCTAssertEqual(configured.fields["probes.pending_producers.2"],"u64:30")
        _ = try hub.acknowledge(producer:20,revision:1,effectiveTransition:1)
        let page = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:2,command:"probe_status",arguments:["u64:1","u64:1"]))
        XCTAssertNil(page.error); XCTAssertEqual(page.fields["probes.pending_producers.total"],"u64:2")
        XCTAssertEqual(page.fields["probes.producers.0.id"],"u64:20"); XCTAssertEqual(page.fields["probes.producers.0.pending"],"bool:false")
        XCTAssertEqual(page.fields["probes.next_offset"],"u64:2")
    }
    func testProbeHostRejectsNegativeLimits() throws {
        let hub = try DiagnosticHub(); var limits = ProtocolLimits(); limits.maxFieldBytes = .min
        XCTAssertThrowsError(try diagnosticProbeHost(hub:hub,profiles:[],sinks:[],limits:limits))
    }
    func testPendingProducerListFitsResponseBeforeConfiguration() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal)
        for id in 0..<10 { try hub.registerProducer(id:UInt64(id),epoch:1) }
        let profile = ProtocolProbeProfile(id:2,label:"marker",subscription:ProbeSubscription(sink:9,site:"site",kind:.marker))
        var limits = ProtocolLimits(); limits.maxResponseFields = 5
        let host = try diagnosticProbeHost(hub:hub,profiles:[profile],sinks:[9],limits:limits)
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let p = try ProtocolAdapter(session:session,authority:authority,limits:limits,host:host)
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"probe_configure",arguments:["u64:0","u64:2"]))
        XCTAssertNil(response.error); XCTAssertEqual(response.fields["probes.pending_producers.total"],"u64:10")
        XCTAssertEqual(response.fields["probes.pending_producers.truncated"],"bool:true")
        XCTAssertEqual(response.fields["probes.pending_producers.0"],"u64:0"); XCTAssertNil(response.fields["probes.pending_producers.1"])
        XCTAssertEqual(hub.pendingProducers.count,10); XCTAssertEqual(hub.captureRevision,1)
    }
    func testConfigurePendingProducerPageFitsFrameBeforeCommit() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var hubLimits = DiagnosticLimits(); hubLimits.producers = 200
        let hub = try DiagnosticHub(limits:hubLimits); try hub.addSink(id:9,permissions:.allLocal)
        for id in 0..<200 { try hub.registerProducer(id:UInt64(id),epoch:1) }
        var limits = ProtocolLimits(); limits.maxFrameBytes = 450; limits.maxPageSize = 200
        let host = try diagnosticProbeHost(hub:hub,profiles:[],sinks:[9],limits:limits)
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let adapter = try ProtocolAdapter(session:session,authority:authority,limits:limits,host:host)
        let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:1,command:"probe_configure",arguments:["u64:0"]))
        XCTAssertNil(response.error); XCTAssertEqual(hub.captureRevision,1)
        XCTAssertEqual(response.fields["probes.pending_producers.total"],"u64:200")
        XCTAssertEqual(response.fields["probes.pending_producers.truncated"],"bool:true")
        XCTAssertEqual(response.fields["probes.pending_producers.0"],"u64:0"); XCTAssertNil(response.fields["probes.pending_producers.199"])
    }
    func testHubAttachmentPreflightsAndPreservesProducerAcknowledgement() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub()
        try hub.addSink(id:9,permissions:.allLocal)
        try hub.registerProducer(id:4,epoch:7)
        let profile = ProtocolProbeProfile(id:2,label:"marker",subscription:ProbeSubscription(sink:9,site:"site",kind:.marker))
        let host = try diagnosticProbeHost(hub:hub,profiles:[profile],sinks:[9])
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let p = try ProtocolAdapter(session:session,authority:authority,host:host)
        func send(_ id: UInt64,_ command: String,_ args: [String]) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args)) }
        let configured = send(1,"probe_configure",["u64:0","u64:2"])
        XCTAssertNil(configured.error); XCTAssertEqual(configured.fields["probes.capture_revision"],"u64:1")
        XCTAssertEqual(configured.fieldOrder,["probes.capture_revision","probes.selected_count","probes.pending_producers.total","probes.pending_producers.truncated","probes.pending_producers.0"])
        XCTAssertEqual(hub.pendingProducers,[4]); XCTAssertEqual(hub.producerStatus.first?.captureRevision,0)
        _ = try hub.acknowledge(producer:4,revision:1,effectiveTransition:1)
        let scope = try hub.beginTurn(producer:4,runID:42,transition:1,origin:.simulation)
        try scope.marker(site:"site",severity:.info)
        XCTAssertEqual(send(2,"probe_events",["u64:9","u64:1","bool:false"]).error,.busy)
        try scope.finish(completeTurn:false)
        let status = send(3,"probe_status",["u64:0","u64:1"])
        XCTAssertNil(status.error); XCTAssertEqual(status.fields["probes.sinks.0.health.captured"],"u64:1")
        XCTAssertEqual(Array(status.fieldOrder!.prefix(5)),["probes.capture_revision","probes.producers.total","probes.producers.truncated","probes.pending_producers.total","probes.producers.0.id"])
        let result = send(4,"probe_events",["u64:9","u64:1","bool:true"])
        XCTAssertNil(result.error); XCTAssertEqual(result.fields["probes.events.0.run"],"u64:42")
        XCTAssertEqual(result.fields["probes.events.0.incomplete_turn"],"bool:true")
        XCTAssertEqual(result.fields["probes.events.0.payload"],"none"); XCTAssertEqual(hub.queued(sink:9)?.records,0)
        XCTAssertEqual(Array(result.fieldOrder!.prefix(5)),["probes.sink","probes.capture_revision","probes.sink.health.dropped","probes.sink.health.exporter_failed","probes.events.0.schema_version"])
        XCTAssertEqual(result.fieldOrder!.suffix(2),["probes.events.0.payload","probes.events.returned"])
        let repeated = send(4,"probe_events",["u64:9","u64:1","bool:true"])
        XCTAssertEqual(repeated.fieldOrder,result.fieldOrder)
    }

    func testResponseLimitDoesNotConsumeEvents() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal); try hub.registerProducer(id:4,epoch:7)
        let profile = ProtocolProbeProfile(id:2,label:"marker",subscription:ProbeSubscription(sink:9,site:String(repeating:"x",count:200),kind:.marker))
        var limits = ProtocolLimits(); limits.maxFrameBytes = 600
        let host = try diagnosticProbeHost(hub:hub,profiles:[profile],sinks:[9],limits:limits)
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let p = try ProtocolAdapter(session:session,authority:authority,limits:limits,host:host)
        func send(_ id: UInt64,_ command: String,_ args: [String]) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args)) }
        XCTAssertNil(send(1,"probe_configure",["u64:0","u64:2"]).error)
        _ = try hub.acknowledge(producer:4,revision:1,effectiveTransition:1)
        let scope = try hub.beginTurn(producer:4,runID:1,transition:1,origin:.test)
        try scope.marker(site:String(repeating:"x",count:200)); try scope.finish(completeTurn:true)
        XCTAssertEqual(send(2,"probe_events",["u64:9","u64:1","bool:true"]).error,.limit)
        XCTAssertEqual(hub.queued(sink:9)?.records,1)
    }
}
