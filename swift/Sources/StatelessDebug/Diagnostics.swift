import Statelessness
import Foundation

public enum Severity: Int, Comparable { case trace,debug,info,warn,error
    public static func < (a: Self,b: Self) -> Bool { a.rawValue < b.rawValue }
}
public enum DiagnosticOrigin: String { case simulation,live,debugControlledLive,test,replay,imported }
public enum DiagnosticSiteKind: String { case probe,marker }
public enum ProbeTrigger: Equatable {
    case every,changed,equals(DisplayScalar)
    case aboveU128(threshold: TraceUInt128,hysteresis: TraceUInt128,cooldownTurns: UInt64)
}
public struct CaptureContext {
    // Identity fields support the older single-turn convenience API. Explicit
    // scoped producers supply these identities to beginTurn instead.
    public var producer: UInt64
    public var origin: DiagnosticOrigin
    public var sequence: UInt64
    public var labels: [ExactString:ExactString]
    public var model: String?, machine: String?, entity: String?, correlation: String?
    public var inputVariant: String?, outputVariant: String?, property: String?, effectKind: String?, sourceSite: String?
    public var propertyFailed: Bool?
    public init(producer: UInt64 = 0,origin: DiagnosticOrigin = .simulation,sequence: UInt64 = 0,labels: [ExactString:ExactString] = [:]) { self.producer = producer; self.origin = origin; self.sequence = sequence; self.labels = labels }
    fileprivate var namedLabels: [String?] { [model,machine,entity,correlation,inputVariant,outputVariant,property,effectKind,sourceSite] }
}
public struct ScopeSelector {
    public var producer: UInt64?
    public var origin: DiagnosticOrigin?
    public var labels: [ExactString:ExactString] = [:]
    public var model: String?, machine: String?, entity: String?, correlation: String?
    public var inputVariant: String?, outputVariant: String?, property: String?, effectKind: String?, sourceSite: String?
    public var propertyFailed: Bool?
    public init() {}
    fileprivate var namedLabels: [String?] { [model,machine,entity,correlation,inputVariant,outputVariant,property,effectKind,sourceSite] }
    fileprivate func matches(producer: UInt64,origin: DiagnosticOrigin,context: CaptureContext) -> Bool {
        (self.producer == nil || self.producer == producer) && (self.origin == nil || self.origin == origin) && (propertyFailed == nil || propertyFailed == context.propertyFailed) && labels.allSatisfy { context.labels[$0.key] == $0.value } && zip(namedLabels,context.namedLabels).allSatisfy { expected,actual in expected == nil || (actual != nil && exactText(expected!,actual!)) }
    }
}
public struct ProbeSubscription {
    public var sink: UInt64
    public var site: String
    public var kind: DiagnosticSiteKind
    public var path: [PathSegment]
    public var trigger: ProbeTrigger
    public var sampleEvery: UInt64 = 1
    public var minimumSeverity: Severity = .trace
    public var selector = ScopeSelector()
    public init(sink: UInt64,site: String,kind: DiagnosticSiteKind = .probe,path: [PathSegment] = [],trigger: ProbeTrigger = .every) { self.sink = sink; self.site = site; self.kind = kind; self.path = path; self.trigger = trigger }
}
public struct SinkPermissions {
    public let sites: [ExactString]
    public let paths: [[PathSegment]]
    public let localAll: Bool
    public init(sites: [String] = [],paths: [[PathSegment]] = [],localAll: Bool = false) { self.sites = sites.map(ExactString.init); self.paths = paths; self.localAll = localAll }
    public static var denyAll: Self { Self() }
    public static var allLocal: Self { Self(localAll: true) }
    fileprivate func allows(site: String,path: [PathSegment]) -> Bool { localAll || ((sites.contains(ExactString("*")) || sites.contains(ExactString(site))) && paths.contains { path.starts(with: $0) }) }
}
public enum DiagnosticDropPolicy { case newest,oldest }
public struct DiagnosticSinkLimits {
    public var records = 1024
    public var bytes = 4*1024*1024
    public var policy: DiagnosticDropPolicy = .newest
    public init() {}
}
public struct DiagnosticLimits {
    public var producers = 64, sinks = 16, subscriptions = 64, configBytes = 64*1024
    public var turnRecords = 256, turnBytes = 256*1024, eventBytes = 32*1024, baselineBytes = 8*1024*1024
    public init() {}
}
public enum DiagnosticError: Error,Equatable {
    case unauthorized,capacity,invalidConfiguration,staleRevision,unknownSink,unknownProducer,revisionExhausted,exporterFault,staleBoundary,pendingConfiguration,closed
}
public enum ProbeBaselineReason: String { case initial,observationGap,inspectionIncomplete,typeUnavailable,selectorGap,reconfigured,schemaChanged,sourceChanged,evicted }
public struct DiagnosticEvent {
    public let schemaVersion: UInt32
    public let runID: UInt64
    public let producer: UInt64
    public let epoch: UInt64
    public let producerSequence: UInt64
    public let captureRevision: UInt64
    public let transition: UInt64
    public let origin: DiagnosticOrigin
    public let site: String
    public let severity: Severity
    public let kind: DiagnosticSiteKind
    public let path: [PathSegment]
    public let value: InspectNode?
    public let displaySchema: DisplaySchema?
    public let baseline: Bool
    public let baselineReason: ProbeBaselineReason?
    public let changeUnknown: Bool
    public fileprivate(set) var incompleteTurn: Bool
    public var sequence: UInt64 { transition }
    public var marker: Bool { kind == .marker }
    public var retainedBytes: Int { diagnosticSum([256,site.utf8.count,diagnosticPathBytes(path),value.map(nodeRetainedPayloadBytes) ?? 0,displaySchema?.name.utf8.count ?? 0]) }
}
public struct DiagnosticHealth {
    public fileprivate(set) var selected: UInt64 = 0, captured: UInt64 = 0, filtered: UInt64 = 0, sampled: UInt64 = 0, dropped: UInt64 = 0, truncated: UInt64 = 0, exporterFaults: UInt64 = 0, abandoned: UInt64 = 0, inspectionErrors: UInt64 = 0, baselineEvicted: UInt64 = 0, observationGaps: UInt64 = 0
}
public struct ConfigurationAck { public let captureRevision: UInt64; public let pendingProducers: [UInt64] }
public struct ProducerAck { public let producer: UInt64; public let epoch: UInt64; public let captureRevision: UInt64; public let effectiveTransition: UInt64 }
private struct ActiveProbe {
    var subscription: ProbeSubscription
    var baseline: InspectNode?
    var baselineReason: ProbeBaselineReason? = .initial
    var schema: DisplaySchema?
    var high = false
    var lastAlert: UInt64?
    var eligible: UInt64 = 0
}
private struct ProducerSource: Equatable {
    let runID: UInt64
    let origin: DiagnosticOrigin
    let labels: [ExactString?]
    init(runID: UInt64,origin: DiagnosticOrigin,context: CaptureContext) { self.runID = runID; self.origin = origin; labels = Array(context.namedLabels.prefix(4)).map { $0.map(ExactString.init) } }
}
private struct DiagnosticProducer {
    var epoch: UInt64
    var revision: UInt64 = 0
    var lastTransition: UInt64?
    var lastSource: ProducerSource?
    var effective: UInt64 = 0
    var sequence: UInt64 = 0
    var config: [ActiveProbe] = []
}
private struct DiagnosticSink {
    let permissions: SinkPermissions
    let limits: DiagnosticSinkLimits
    var events: [(DiagnosticEvent,Int)] = []
    var bytes = 0
    var health = DiagnosticHealth()
}
private func diagnosticIncrement(_ n: inout UInt64) { if n < .max { n += 1 } }
private func diagnosticSameSchema(_ left: DisplaySchema?,_ right: DisplaySchema?) -> Bool {
    switch (left,right) {
    case (nil,nil): return true
    case let (left?,right?): return exactText(left.name,right.name) && left.version == right.version
    default: return false
    }
}
private func diagnosticSum(_ values: [Int]) -> Int { values.reduce(0) { a,b in b > Int.max-a ? Int.max : a+b } }
private func diagnosticPathBytes(_ path: [PathSegment]) -> Int {
    diagnosticSum(path.map { p in
        switch p {
        case let .field(s),let .variant(s),let .mapKey(s): return diagnosticSum([64,s.value.utf8.count])
        case let .typedMapKey(k): switch k { case let .string(s): return diagnosticSum([64,s.value.utf8.count]); case let .integer(k,d): return diagnosticSum([64,k.value.utf8.count,d.value.utf8.count]); default: return 64 }
        default: return 64
        }
    })
}
private func quantityLess(_ a: TraceUInt128,_ b: TraceUInt128) -> Bool { a.high < b.high || (a.high == b.high && a.low < b.low) }
private func quantitySubtract(_ a: TraceUInt128,_ b: TraceUInt128) -> TraceUInt128 {
    if quantityLess(a,b) { return TraceUInt128(high:0,low:0) }
    let (low,borrow) = a.low.subtractingReportingOverflow(b.low)
    return TraceUInt128(high:a.high-b.high-(borrow ? 1 : 0),low:low)
}
private func unsignedQuantity(_ node: InspectNode?) -> TraceUInt128? {
    guard let node,node.isComplete,case let .scalar(.integer(_,decimal)) = node.kind else { return nil }
    let text = decimal.value.hasPrefix("+") ? String(decimal.value.dropFirst()) : decimal.value
    return try? TraceUInt128(decimal:text)
}
/// Explicit host-owned value. The enclosing value factory is lazy, evaluated
/// exactly once per interested site; projection is cached once per selected path.
public struct DiagnosticValue {
    public let schema: DisplaySchema
    public let project: ([PathSegment],InspectLimits) throws -> InspectNode
    public init(schema: DisplaySchema,project: @escaping ([PathSegment],InspectLimits) throws -> InspectNode) { self.schema = schema; self.project = project }
}
/// Single-owner capture. No global hooks, clocks, model calls, or exporter I/O.
/// Callback allocations/execution remain cooperative, without RSS/preemption guarantees.
public final class DiagnosticHub {
    public let limits: DiagnosticLimits
    public var maxSubscriptions: Int { limits.subscriptions }
    public var maxEventBytes: Int { limits.eventBytes }
    public private(set) var captureRevision: UInt64 = 0
    private var config: [ProbeSubscription] = []
    private var producers: [UInt64:DiagnosticProducer] = [:]
    private var orderedProducerIDs: [UInt64] = []
    private var sinks: [UInt64:DiagnosticSink] = [:]
    private var scoped = false
    public var health: DiagnosticHealth {
        var result = DiagnosticHealth()
        for sink in sinks.values {
            func sum(_ a: UInt64,_ b: UInt64) -> UInt64 { let (v,o) = a.addingReportingOverflow(b); return o ? .max : v }
            let h = sink.health
            result.selected = sum(result.selected,h.selected); result.captured = sum(result.captured,h.captured); result.filtered = sum(result.filtered,h.filtered); result.sampled = sum(result.sampled,h.sampled); result.dropped = sum(result.dropped,h.dropped); result.truncated = sum(result.truncated,h.truncated); result.exporterFaults = sum(result.exporterFaults,h.exporterFaults); result.abandoned = sum(result.abandoned,h.abandoned); result.inspectionErrors = sum(result.inspectionErrors,h.inspectionErrors); result.baselineEvicted = sum(result.baselineEvicted,h.baselineEvicted); result.observationGaps = sum(result.observationGaps,h.observationGaps)
        }; return result
    }
    public init(limits: DiagnosticLimits) throws {
        guard [limits.producers,limits.sinks,limits.subscriptions,limits.configBytes,limits.turnRecords,limits.turnBytes,limits.eventBytes,limits.baselineBytes].allSatisfy({$0 >= 0}) else { throw DiagnosticError.invalidConfiguration }; self.limits = limits
    }
    public convenience init(maxSubscriptions: Int = 64,maxEventBytes: Int = 32*1024) throws {
        var limits = DiagnosticLimits(); limits.subscriptions = maxSubscriptions; limits.eventBytes = maxEventBytes; try self.init(limits:limits)
    }
    private func idle() throws { guard !scoped else { throw DebugError.reentrant } }
    public func assertAvailable() throws { try idle() }
    public func addSink(id: UInt64,permissions: SinkPermissions,limits: DiagnosticSinkLimits) throws {
        try idle()
        let bytes = diagnosticSum([64,diagnosticSum(permissions.sites.map {$0.value.utf8.count+32}),diagnosticSum(permissions.paths.map {diagnosticSum([32,diagnosticPathBytes($0)])})])
        guard sinks[id] == nil,sinks.count < self.limits.sinks,limits.records > 0,limits.bytes > 0,bytes <= self.limits.configBytes,permissions.sites.count <= self.limits.subscriptions,permissions.paths.count <= self.limits.subscriptions,permissions.paths.allSatisfy({$0.count <= 32}) else { throw DiagnosticError.capacity }
        for path in permissions.paths {try validateDiagnosticPath(path,maximumBytes:self.limits.configBytes)}
        sinks[id] = DiagnosticSink(permissions:permissions,limits:limits)
    }
    /// Compatibility convenience retains its former oldest-first overflow policy.
    public func addSink(id: UInt64,permissions: SinkPermissions,maxRecords: Int = 1000,maxBytes: Int = 4*1024*1024) throws {
        var limits = DiagnosticSinkLimits(); limits.records = maxRecords; limits.bytes = maxBytes; limits.policy = .oldest; try addSink(id:id,permissions:permissions,limits:limits)
    }
    public func registerProducer(id: UInt64,epoch: UInt64) throws {
        try idle(); guard epoch > 0,producers[id] == nil,producers.count < limits.producers else { throw DiagnosticError.capacity }
        var low = 0,high = orderedProducerIDs.count
        while low < high { let middle = low+(high-low)/2; if orderedProducerIDs[middle] < id { low = middle+1 } else { high = middle } }
        producers[id] = DiagnosticProducer(epoch:epoch); orderedProducerIDs.insert(id,at:low)
    }
    public var pendingProducers: [UInt64] { orderedProducerIDs.filter { producers[$0]!.revision != captureRevision } }
    public var producerStatus: [ProducerAck] { producerStatusPage(offset:0,limit:orderedProducerIDs.count) }
    var producerCount: Int { orderedProducerIDs.count }
    var pendingProducerCount: Int { orderedProducerIDs.reduce(0) { $0 + (producers[$1]!.revision != captureRevision ? 1 : 0) } }
    func producerIDs(limit: Int) -> [UInt64] { Array(orderedProducerIDs.prefix(limit)) }
    func producerStatusPage(offset: Int,limit: Int) -> [ProducerAck] {
        orderedProducerIDs.dropFirst(offset).prefix(limit).map { id in let p = producers[id]!; return ProducerAck(producer:id,epoch:p.epoch,captureRevision:p.revision,effectiveTransition:p.effective) }
    }
    public func validateSelected(expectedRevision: UInt64,subscriptions: [ProbeSubscription]) throws {
        try idle(); guard expectedRevision == captureRevision else { throw DiagnosticError.staleRevision }; guard subscriptions.count <= limits.subscriptions else { throw DiagnosticError.capacity }
        var bytes = 0
        for sub in subscriptions {
            let selector = sub.selector
            guard sub.sampleEvery > 0,!sub.site.isEmpty,sub.site.utf8.count <= 256,sub.path.count <= 32,selector.labels.count <= 16,selector.labels.allSatisfy({!$0.value.value.isEmpty && $0.key.value.utf8.count <= 256 && $0.value.value.utf8.count <= 256}),selector.namedLabels.compactMap({$0}).allSatisfy({!$0.isEmpty && $0.utf8.count <= 256}) else { throw DiagnosticError.invalidConfiguration }
            guard let sink = sinks[sub.sink] else { throw DiagnosticError.unknownSink }; guard sink.permissions.allows(site:sub.site,path:sub.path) else { throw DiagnosticError.unauthorized }
            try validateDiagnosticPath(sub.path,maximumBytes:limits.configBytes)
            var triggerBytes = 0
            if case let .equals(value) = sub.trigger { let expected = InspectNode(kind:.scalar(value)); try validateDiagnosticProjection(expected,maximumBytes:limits.configBytes); triggerBytes = nodeRetainedPayloadBytes(expected) }
            bytes = diagnosticSum([bytes,256,sub.site.utf8.count,diagnosticPathBytes(sub.path),triggerBytes,diagnosticSum(selector.namedLabels.compactMap {$0?.utf8.count}),diagnosticSum(selector.labels.map {diagnosticSum([$0.key.value.utf8.count,$0.value.value.utf8.count,64])})])
            guard bytes <= limits.configBytes else { throw DiagnosticError.capacity }
        }
        guard captureRevision < .max else { throw DiagnosticError.revisionExhausted }
    }
    public func configureSelected(expectedRevision: UInt64,subscriptions: [ProbeSubscription]) throws -> ConfigurationAck {
        try validateSelected(expectedRevision:expectedRevision,subscriptions:subscriptions)
        captureRevision += 1; config = subscriptions
        return ConfigurationAck(captureRevision:captureRevision,pendingProducers:orderedProducerIDs)
    }
    public func configure(expectedRevision: UInt64,subscriptions: [ProbeSubscription]) throws -> UInt64 { try configureSelected(expectedRevision:expectedRevision,subscriptions:subscriptions).captureRevision }
    public func acknowledge(producer: UInt64,revision: UInt64,effectiveTransition: UInt64) throws -> ProducerAck {
        try idle(); guard revision == captureRevision else { throw DiagnosticError.staleRevision }; guard var p = producers[producer] else { throw DiagnosticError.unknownProducer }; guard p.lastTransition.map({effectiveTransition > $0}) ?? true else { throw DiagnosticError.staleBoundary }
        let reason: ProbeBaselineReason = p.revision == 0 ? .initial : .reconfigured
        p.revision = revision; p.effective = effectiveTransition; p.lastSource = nil; p.config = config.map { var s = ActiveProbe(subscription:$0); s.baselineReason = reason; return s }; producers[producer] = p
        return ProducerAck(producer:producer,epoch:p.epoch,captureRevision:revision,effectiveTransition:effectiveTransition)
    }
    public func beginTurn(producer: UInt64,runID: UInt64,transition: UInt64,origin: DiagnosticOrigin,context: CaptureContext = CaptureContext()) throws -> ScopedDiagnostics {
        try idle(); guard context.namedLabels.compactMap({$0}).allSatisfy({$0.utf8.count <= 256}),context.labels.count <= 16,context.labels.allSatisfy({$0.key.value.utf8.count <= 256 && $0.value.value.utf8.count <= 256}) else { throw DiagnosticError.invalidConfiguration }
        guard var p = producers[producer] else { throw DiagnosticError.unknownProducer }; guard p.lastTransition.map({transition > $0}) ?? true else { throw DiagnosticError.staleBoundary }; guard transition >= p.effective else { throw DiagnosticError.pendingConfiguration }
        let selected = p.config.contains {$0.subscription.selector.matches(producer:producer,origin:origin,context:context)}
        let source = selected ? ProducerSource(runID:runID,origin:origin,context:context) : nil
        let sourceChanged = selected && p.lastSource != nil && p.lastSource != source
        let gap = p.lastTransition.map { $0 == .max || transition != $0+1 } ?? false
        var gapSinks = Set<UInt64>()
        for i in p.config.indices {
            if sourceChanged || gap { p.config[i].baseline = nil; p.config[i].baselineReason = sourceChanged ? .sourceChanged : .observationGap; p.config[i].high = false; p.config[i].lastAlert = nil; if gap { gapSinks.insert(p.config[i].subscription.sink) } }
            if !p.config[i].subscription.selector.matches(producer:producer,origin:origin,context:context) { p.config[i].baseline = nil; p.config[i].baselineReason = .selectorGap; p.config[i].high = false; p.config[i].lastAlert = nil }
        }
        for id in gapSinks { diagnosticIncrement(&sinks[id]!.health.observationGaps) }
        p.lastTransition = transition; if selected && (sourceChanged || p.lastSource == nil) { p.lastSource = source }; producers[producer] = p; scoped = true
        return ScopedDiagnostics(hub:self,producer:producer,runID:runID,transition:transition,origin:origin,context:context)
    }
    public func sinkHealth(_ id: UInt64) -> DiagnosticHealth? { sinks[id]?.health }
    public func queued(sink: UInt64) -> (records: Int,bytes: Int)? { sinks[sink].map {($0.events.count,$0.bytes)} }
    public func peekEvents(sink: UInt64,maximum: Int) throws -> [DiagnosticEvent] { try idle(); guard maximum >= 0 else { throw DiagnosticError.capacity }; guard let sink = sinks[sink] else { throw DiagnosticError.unknownSink }; return sink.events.prefix(maximum).map {$0.0} }
    public func pop(sink id: UInt64) throws -> DiagnosticEvent? {
        try idle(); guard var sink = sinks[id] else { throw DiagnosticError.unknownSink }; guard !sink.events.isEmpty else { return nil }; let item = sink.events.removeFirst(); sink.bytes -= item.1; sinks[id] = sink; return item.0
    }
    public func drain(sink id: UInt64,limit: Int) throws -> [DiagnosticEvent] {
        try idle(); guard limit > 0 else { throw DiagnosticError.capacity }; guard sinks[id] != nil else { throw DiagnosticError.unknownSink }; var events: [DiagnosticEvent] = []
        for _ in 0..<limit { guard let event = try pop(sink:id) else { break }; events.append(event) }; return events
    }
    /// Pops one record at a time. A failed writer loses that record; untouched
    /// records remain queued. Writer callbacks run outside the capture guard.
    @discardableResult public func drainTo(sink id: UInt64,maximum: Int,write: (DiagnosticEvent) throws -> Void) throws -> Int {
        try idle(); guard maximum >= 0 else { throw DiagnosticError.capacity }; guard sinks[id] != nil else { throw DiagnosticError.unknownSink }; var written = 0
        for _ in 0..<maximum {
            guard let event = try pop(sink:id) else { break }
            do { try write(event); written += 1 } catch { diagnosticIncrement(&sinks[id]!.health.exporterFaults); break }
        }; return written
    }
    public func export(sink: UInt64,limit: Int,write: (DiagnosticEvent) throws -> Void) throws {
        var failed = false
        _ = try drainTo(sink:sink,maximum:limit) { event in do {try write(event)} catch {failed = true; throw error} }; if failed {throw DiagnosticError.exporterFault}
    }
    @discardableResult public func abandon(sink id: UInt64) throws -> Int {
        try idle(); guard var sink = sinks[id] else { throw DiagnosticError.unknownSink }; let count = sink.events.count
        let (next,overflow) = sink.health.abandoned.addingReportingOverflow(UInt64(count)); sink.health.abandoned = overflow ? .max : next; sink.events.removeAll(); sink.bytes = 0; sinks[id] = sink; return count
    }
    private func legacyScope(site: String,kind: DiagnosticSiteKind,severity: Severity,context: CaptureContext) throws -> ScopedDiagnostics? {
        try idle()
        guard config.contains(where: {exactText($0.site,site) && $0.kind == kind && severity >= $0.minimumSeverity && $0.selector.matches(producer:context.producer,origin:context.origin,context:context)}) else { return nil }
        if producers[context.producer] == nil { try registerProducer(id:context.producer,epoch:1) }
        if producers[context.producer]!.revision != captureRevision { _ = try acknowledge(producer:context.producer,revision:captureRevision,effectiveTransition:context.sequence) }
        return try beginTurn(producer:context.producer,runID:0,transition:context.sequence,origin:context.origin,context:context)
    }
    /// Legacy single-site convenience explicitly owns registration, current-config
    /// acknowledgement and complete-turn finalization. Use beginTurn for host boundaries.
    public func probe(site: String,severity: Severity,context: CaptureContext,limits: InspectLimits = InspectLimits(),project: @escaping ([PathSegment],InspectLimits) throws -> InspectNode) throws {
        guard let scope = try legacyScope(site:site,kind:.probe,severity:severity,context:context) else { return }
        try scope.probe(site:site,severity:severity,limits:limits,value: {DiagnosticValue(schema:DisplaySchema(name:"legacy-projection"),project:project)})
        try scope.finish(completeTurn:true)
    }
    public func marker(site: String,severity: Severity,context: CaptureContext) throws {
        guard let scope = try legacyScope(site:site,kind:.marker,severity:severity,context:context) else { return }; try scope.marker(site:site,severity:severity); try scope.finish(completeTurn:true)
    }
    fileprivate func interested(producer: UInt64,site: String,kind: DiagnosticSiteKind,severity: Severity,origin: DiagnosticOrigin,context: CaptureContext) -> Bool {
        producers[producer]!.config.contains { exactText($0.subscription.site,site) && $0.subscription.kind == kind && severity >= $0.subscription.minimumSeverity && $0.subscription.selector.matches(producer:producer,origin:origin,context:context) }
    }
    fileprivate func capture(scope: ScopedDiagnostics,site: String,kind: DiagnosticSiteKind,severity: Severity,value: DiagnosticValue?,projectionLimits: InspectLimits) {
        var p = producers[scope.producer]!
        guard p.sequence < .max else { return }; p.sequence += 1
        var projections: [([PathSegment],InspectNode?)] = []
        for i in p.config.indices {
            var s = p.config[i]; let c = s.subscription
            guard exactText(c.site,site),c.kind == kind,severity >= c.minimumSeverity,c.selector.matches(producer:scope.producer,origin:scope.origin,context:scope.context) else { continue }
            diagnosticIncrement(&sinks[c.sink]!.health.selected)
            let node: InspectNode?
            if let found = projections.first(where: {$0.0 == c.path}) { node = found.1 }
            else {
                if let value { do { let projected = try value.project(c.path,projectionLimits); try validateProjection(projected,limits:projectionLimits); node = projected } catch {node = nil} } else {node = nil}
                projections.append((c.path,node))
            }
            if kind == .probe && node == nil { diagnosticIncrement(&sinks[c.sink]!.health.inspectionErrors) }
            if !diagnosticSameSchema(s.schema,value?.schema) { if s.schema != nil {s.baselineReason = .schemaChanged}; s.baseline = nil; s.high = false; s.lastAlert = nil; s.schema = value.map {DisplaySchema(name:$0.schema.name,version:$0.schema.version)} }
            let complete = node?.isComplete == true
            let numeric = unsignedQuantity(node)
            let thresholdUnavailable: Bool
            if case .aboveU128 = c.trigger {thresholdUnavailable = numeric == nil} else {thresholdUnavailable = false}
            let baseline = s.baseline == nil
            let reason: ProbeBaselineReason? = baseline ? (s.baselineReason ?? .initial) : nil
            if baseline {s.baselineReason = nil}
            let unknown = kind == .probe && (!complete || thresholdUnavailable || (reason != nil && reason != .initial))
            let changed = compareNodes(s.baseline,node) == .changed
            let emit: Bool
            switch c.trigger {
            case .every: emit = true
            case .changed: emit = baseline || unknown || changed
            case let .equals(expected): emit = complete && node?.kind == .scalar(expected)
            case let .aboveU128(threshold,hysteresis,cooldown):
                if let numeric {
                    if !quantityLess(quantitySubtract(threshold,hysteresis),numeric) {s.high = false}
                    let elapsed = s.lastAlert.map { scope.transition >= $0 ? scope.transition-$0 : 0 } ?? .max
                    emit = quantityLess(threshold,numeric) && !s.high && elapsed >= cooldown
                    if emit {s.high = true; s.lastAlert = scope.transition}
                } else {emit = false}
            }
            s.baseline = complete && !thresholdUnavailable ? node : nil
            if kind == .probe && (!complete || thresholdUnavailable) {s.high = false; s.lastAlert = nil; s.baselineReason = thresholdUnavailable && complete ? .typeUnavailable : .inspectionIncomplete}
            if !emit {diagnosticIncrement(&sinks[c.sink]!.health.filtered); p.config[i] = s; continue}
            diagnosticIncrement(&s.eligible)
            if (s.eligible-1) % c.sampleEvery != 0 {diagnosticIncrement(&sinks[c.sink]!.health.sampled); p.config[i] = s; continue}
            if unknown {diagnosticIncrement(&sinks[c.sink]!.health.truncated)}
            let schema = value.map {DisplaySchema(name:String(String.UnicodeScalarView($0.schema.name.unicodeScalars.prefix(256))),version:$0.schema.version)}
            let event = DiagnosticEvent(schemaVersion:1,runID:scope.runID,producer:scope.producer,epoch:p.epoch,producerSequence:p.sequence,captureRevision:p.revision,transition:scope.transition,origin:scope.origin,site:site,severity:severity,kind:kind,path:c.path,value:node,displaySchema:schema,baseline:baseline,baselineReason:reason,changeUnknown:unknown,incompleteTurn:false)
            let bytes = event.retainedBytes
            if bytes > limits.eventBytes || scope.staged.count >= limits.turnRecords || bytes > limits.turnBytes-scope.bytes {diagnosticIncrement(&sinks[c.sink]!.health.dropped)}
            else {scope.staged.append((c.sink,event)); scope.bytes += bytes}
            p.config[i] = s
        }
        producers[scope.producer] = p
        enforceBaselines()
    }
    private func enforceBaselines() {
        var bytes = diagnosticSum(producers.values.flatMap {$0.config}.map {$0.baseline.map(nodeRetainedPayloadBytes) ?? 0})
        for id in producers.keys.sorted() {
            var p = producers[id]!
            for i in p.config.indices where bytes > limits.baselineBytes {
                if let old = p.config[i].baseline {bytes -= nodeRetainedPayloadBytes(old); p.config[i].baseline = nil; p.config[i].baselineReason = .evicted; p.config[i].high = false; p.config[i].lastAlert = nil; diagnosticIncrement(&sinks[p.config[i].subscription.sink]!.health.baselineEvicted)}
            }; producers[id] = p
        }
    }
    fileprivate func finish(_ staged: [(UInt64,DiagnosticEvent)],completeTurn: Bool) {
        for (id,var event) in staged {
            event.incompleteTurn = !completeTurn
            var sink = sinks[id]!
            let bytes = event.retainedBytes
            if bytes > sink.limits.bytes {diagnosticIncrement(&sink.health.dropped); sinks[id] = sink; continue}
            var drop = false
            while sink.events.count >= sink.limits.records || bytes > sink.limits.bytes-sink.bytes {
                diagnosticIncrement(&sink.health.dropped)
                if sink.limits.policy == .newest {drop = true; break}
                guard !sink.events.isEmpty else {drop = true; break}; sink.bytes -= sink.events.removeFirst().1
            }
            if !drop {sink.events.append((event,bytes)); sink.bytes += bytes; diagnosticIncrement(&sink.health.captured)}
            sinks[id] = sink
        }; scoped = false
    }
}
public final class ScopedDiagnostics {
    fileprivate let hub: DiagnosticHub
    fileprivate let producer: UInt64,runID: UInt64,transition: UInt64
    fileprivate let origin: DiagnosticOrigin
    fileprivate let context: CaptureContext
    fileprivate var staged: [(UInt64,DiagnosticEvent)] = []
    fileprivate var bytes = 0
    private var finished = false,capturing = false
    fileprivate init(hub: DiagnosticHub,producer: UInt64,runID: UInt64,transition: UInt64,origin: DiagnosticOrigin,context: CaptureContext) {self.hub = hub; self.producer = producer; self.runID = runID; self.transition = transition; self.origin = origin; self.context = context}
    public func interested(site: String,kind: DiagnosticSiteKind = .probe,severity: Severity = .debug) -> Bool { !finished && hub.interested(producer:producer,site:site,kind:kind,severity:severity,origin:origin,context:context) }
    public func probe(site: String,severity: Severity = .debug,limits: InspectLimits? = nil,value: () throws -> DiagnosticValue) throws {
        guard !finished else {throw DiagnosticError.closed}; guard !capturing else {throw DebugError.reentrant}
        guard interested(site:site,severity:severity) else {return}
        capturing = true; defer {capturing = false}
        let selected = try value()
        // Rust schema names are static references; owned Swift names need an
        // explicit retention bound before they enter producer baseline state.
        guard selected.schema.name.utf8.count <= hub.limits.configBytes else {throw DiagnosticError.capacity}
        var projectionLimits = limits ?? InspectLimits(); projectionLimits.maxBytes = min(projectionLimits.maxBytes,max(0,hub.limits.eventBytes-1024))
        hub.capture(scope:self,site:site,kind:.probe,severity:severity,value:selected,projectionLimits:projectionLimits)
    }
    public func marker(site: String,severity: Severity = .debug) throws {
        guard !finished else {throw DiagnosticError.closed}; guard !capturing else {throw DebugError.reentrant}
        guard interested(site:site,kind:.marker,severity:severity) else {return}
        capturing = true; defer {capturing = false}; hub.capture(scope:self,site:site,kind:.marker,severity:severity,value:nil,projectionLimits:InspectLimits())
    }
    public func finish(completeTurn: Bool) throws {
        guard !finished else {throw DiagnosticError.closed}; guard !capturing else {throw DebugError.reentrant}; hub.finish(staged,completeTurn:completeTurn); staged.removeAll(); finished = true
    }
    deinit {if !finished {hub.finish(staged,completeTurn:false)}}
}

private func diagnosticQuoted(_ text: String) -> String {
    var result = "\""
    for c in text.unicodeScalars {
        switch c.value {
        case 34: result += "\\\""
        case 92: result += "\\\\"
        case 0...31,127...159: result += String(format:"\\u%04x",c.value)
        default: result.unicodeScalars.append(c)
        }
    }; return result + "\""
}
private func diagnosticCase(_ text: String) -> String {text.prefix(1).uppercased()+text.dropFirst()}
private func validateDiagnosticProjection(_ node: InspectNode,maximumBytes: Int) throws {
    guard maximumBytes >= 0 else {throw DiagnosticError.capacity}
    var limits = InspectLimits(); limits.maxBytes = maximumBytes; limits.maxValueBytes = maximumBytes
    try validateProjection(node,limits:limits)
}
private func validateDiagnosticPath(_ path: [PathSegment],maximumBytes: Int) throws {
    guard path.count <= 32 else {throw DiagnosticError.capacity}
    let children = path.map {InspectChild(segment:$0,label:ExactString(""),node:InspectNode(kind:.scalar(.unit)))}
    try validateDiagnosticProjection(InspectNode(kind:.object(ExactString("path")),children:children,childCount:children.count),maximumBytes:maximumBytes)
}
public func diagnosticPathJSON(_ path: [PathSegment],maximumBytes: Int = 32*1024) throws -> String {
    try validateDiagnosticPath(path,maximumBytes:maximumBytes); return diagnosticPathJSONUnchecked(path)
}
public func diagnosticNodeJSON(_ node: InspectNode,maximumBytes: Int = 32*1024) throws -> String {
    try validateDiagnosticProjection(node,maximumBytes:maximumBytes); return diagnosticNodeJSONUnchecked(node)
}
private func diagnosticPathJSONUnchecked(_ path: [PathSegment]) -> String {
    func key(_ key: InspectMapKey) -> String {
        switch key {
        case let .string(s): return "{\"kind\":\"string\",\"value\":\(diagnosticQuoted(s.value))}"
        case let .integer(k,d): return "{\"kind\":\"integer\",\"type\":\(diagnosticQuoted(diagnosticCase(k.value))),\"decimal\":\(diagnosticQuoted(d.value))}"
        case let .bool(b): return "{\"kind\":\"bool\",\"value\":\(b)}"
        case let .character(c): return "{\"kind\":\"char\",\"value\":\(diagnosticQuoted(UnicodeScalar(c).map(String.init) ?? "�"))}"
        }
    }
    return "["+path.map { segment in
        switch segment {
        case let .field(s): return "{\"field\":\(diagnosticQuoted(s.value))}"
        case let .variant(s): return "{\"variant\":\(diagnosticQuoted(s.value))}"
        case let .index(n): return "{\"index\":\"\(n)\"}"
        case let .mapKey(s): return "{\"map_key\":\(key(.string(s)))}"
        case let .typedMapKey(k): return "{\"map_key\":\(key(k))}"
        }
    }.joined(separator:",")+"]"
}
private func diagnosticNodeJSONUnchecked(_ node: InspectNode) -> String {
    let kind: String,value: String
    switch node.kind {
    case let .scalar(.integer(k,d)): kind = "integer"; value = "{\"type\":\(diagnosticQuoted(diagnosticCase(k.value))),\"decimal\":\(diagnosticQuoted(d.value))}"
    case let .scalar(.float(k,d,b)): kind = "float"; value = "{\"type\":\(diagnosticQuoted(k.value)),\"decimal\":\(diagnosticQuoted(d.value)),\"bits\":\"\(b)\"}"
    case let .scalar(.bool(b)): kind = "bool"; value = String(b)
    case let .scalar(.character(c)): kind = "char"; value = diagnosticQuoted(UnicodeScalar(c).map(String.init) ?? "�")
    case .scalar(.unit): kind = "unit"; value = "null"
    case let .string(s,n): kind = "string"; value = "{\"preview\":\(diagnosticQuoted(s.value)),\"total_bytes\":\"\(n)\"}"
    case let .bytes(b,n): kind = "bytes"; value = "{\"hex\":\"\(b.map {String(format:"%02x",$0)}.joined())\",\"total_bytes\":\"\(n)\"}"
    case let .object(s): kind = "object"; value = diagnosticQuoted(s.value)
    case let .enumeration(n,id,label): kind = "enum"; value = "{\"name\":\(diagnosticQuoted(n.value)),\"variant_id\":\(diagnosticQuoted(id.value)),\"label\":\(diagnosticQuoted(label.value))}"
    case let .sequence(s): kind = "sequence"; value = diagnosticQuoted(s.value)
    case let .map(order): kind = "map"; value = diagnosticQuoted(order == .stable ? "Stable" : "Unstable")
    case let .opaque(s): kind = "opaque"; value = diagnosticQuoted(s.value)
    case .redacted: kind = "redacted"; value = "null"
    case .unavailable: kind = "unavailable"; value = "null"
    case let .truncated(reason): kind = "truncated"; value = diagnosticQuoted(diagnosticCase(reason.rawValue))
    }
    let completeness: String
    switch node.completeness {
    case .complete: completeness = "{\"kind\":\"complete\",\"reason\":null}"
    case let .partial(reason): completeness = "{\"kind\":\"partial\",\"reason\":\(diagnosticQuoted(diagnosticCase(reason.rawValue)))}"
    }
    let page = node.page.map {p in "{\"offset\":\"\(p.offset)\",\"returned\":\"\(p.returned)\",\"total\":\"\(p.total)\",\"next_offset\":\(p.nextOffset.map {"\"\($0)\""} ?? "null")}"} ?? "null"
    let children = node.children.map {"{\"segment\":\(diagnosticPathJSONUnchecked([$0.segment])),\"label\":\(diagnosticQuoted($0.label.value)),\"node\":\(diagnosticNodeJSONUnchecked($0.node))}"}.joined(separator:",")
    return "{\"kind\":\(diagnosticQuoted(kind)),\"value\":\(value),\"completeness\":\(completeness),\"child_count\":\(node.childCount.map {"\"\($0)\""} ?? "null"),\"page\":\(page),\"children\":[\(children)]}"
}
/// Stable Rust-compatible JSON envelope. Integral identities and values are
/// decimal strings. Escaped external text cannot become terminal controls.
public func diagnosticEventJSON(_ event: DiagnosticEvent,maximumPayloadBytes: Int = 32*1024) throws -> String {
    guard event.retainedBytes <= maximumPayloadBytes else {throw DiagnosticError.capacity}
    try validateDiagnosticPath(event.path,maximumBytes:maximumPayloadBytes)
    if let node = event.value {try validateDiagnosticProjection(node,maximumBytes:maximumPayloadBytes)}
    let schema = event.displaySchema.map {"{\"name\":\(diagnosticQuoted($0.name)),\"version\":\"\($0.version)\"}"} ?? "null"
    return "{\"schema_version\":\"\(event.schemaVersion)\",\"run_id\":\"\(event.runID)\",\"producer\":\"\(event.producer)\",\"epoch\":\"\(event.epoch)\",\"producer_sequence\":\"\(event.producerSequence)\",\"capture_revision\":\"\(event.captureRevision)\",\"transition\":\"\(event.transition)\",\"origin\":\(diagnosticQuoted(diagnosticCase(event.origin.rawValue))),\"severity\":\(diagnosticQuoted(diagnosticCase(String(describing:event.severity)))),\"site\":\(diagnosticQuoted(event.site)),\"kind\":\(diagnosticQuoted(diagnosticCase(event.kind.rawValue))),\"path\":\(diagnosticPathJSONUnchecked(event.path)),\"display_schema\":\(schema),\"baseline\":\(event.baseline),\"baseline_reason\":\(event.baselineReason.map {diagnosticQuoted(diagnosticCase($0.rawValue))} ?? "null"),\"change_unknown\":\(event.changeUnknown),\"incomplete_turn\":\(event.incompleteTurn),\"payload\":\(event.value.map(diagnosticNodeJSONUnchecked) ?? "null")}"
}
public struct DiagnosticJSONLSink {
    public let write: (Data) throws -> Void
    public init(write: @escaping (Data) throws -> Void) {self.write = write}
    public func emit(_ event: DiagnosticEvent) throws {try write(Data((diagnosticEventJSON(event)+"\n").utf8))}
}
public struct DiagnosticTextSink {
    public let write: (Data) throws -> Void
    public init(write: @escaping (Data) throws -> Void) {self.write = write}
    public func emit(_ event: DiagnosticEvent) throws {
        _ = try diagnosticEventJSON(event)
        let line = "\(diagnosticCase(String(describing:event.severity))) producer=\(event.producer) epoch=\(event.epoch) sequence=\(event.producerSequence) capture=\(event.captureRevision) site=\(diagnosticQuoted(event.site)) value=\(event.value.map(diagnosticNodeJSONUnchecked) ?? "null") incomplete=\(event.incompleteTurn)\n"
        try write(Data(line.utf8))
    }
}
/// Exact-artifact sidecar binding detects accidental mismatch, without implying
/// authentication or making diagnostic projections exact replay evidence.
public struct DiagnosticSidecarManifest {
    public let version: UInt32
    public let runID: UInt64
    public let trace: ArtifactIdentity
    public init(trace: AuditTrace,runID: UInt64) throws {version = 1; self.runID = runID; self.trace = try ArtifactIdentity(trace:trace)}
    private init(runID: UInt64,trace: ArtifactIdentity) {version = 1; self.runID = runID; self.trace = trace}
    public func validate(trace: AuditTrace) throws {guard version == 1,try ArtifactIdentity(trace:trace) == self.trace else {throw ModelError("diagnostic sidecar is incompatible or bound to another exact artifact")}}
    public func jsonl(events: [DiagnosticEvent],maxEvents: Int,maxBytes: Int) throws -> Data {
        guard version == 1,maxEvents >= 0,maxBytes >= 0,events.count <= maxEvents else {throw DiagnosticError.capacity}
        let digest = String(format:"%016llx",trace.fnv1a64)
        var output = "{\"schema\":\"stateless.debug.sidecar\",\"version\":\"1\",\"trace_bytes\":\"\(trace.bytes)\",\"trace_fnv1a64\":\"\(digest)\",\"run_id\":\"\(runID)\"}\n"
        guard output.utf8.count <= maxBytes else {throw DiagnosticError.capacity}
        for event in events {
            guard event.runID == runID,event.retainedBytes <= 32*1024 else {throw DiagnosticError.invalidConfiguration}
            let line = try diagnosticEventJSON(event)+"\n"
            guard line.utf8.count <= maxBytes-output.utf8.count else {throw DiagnosticError.capacity}; output += line
        }; return Data(output.utf8)
    }
    public func writeJSONL(events: [DiagnosticEvent],maxEvents: Int,maxBytes: Int,write: (Data) throws -> Void) throws {try write(jsonl(events:events,maxEvents:maxEvents,maxBytes:maxBytes))}
    /// The caller supplies only a bounded first line, never an unbounded file read.
    public static func readHeader(_ bytes: Data) throws -> DiagnosticSidecarManifest {
        guard bytes.count <= 4096,let text = String(data:bytes,encoding:.utf8),text.hasSuffix("\n") else {throw DiagnosticError.invalidConfiguration}
        let prefix = "{\"schema\":\"stateless.debug.sidecar\",\"version\":\"1\",\"trace_bytes\":\""
        guard text.hasPrefix(prefix) else {throw DiagnosticError.invalidConfiguration}
        var rest = String(text.dropFirst(prefix.count))
        func take(_ delimiter: String) throws -> String {guard let range = rest.range(of:delimiter) else {throw DiagnosticError.invalidConfiguration}; let part = String(rest[..<range.lowerBound]); rest = String(rest[range.upperBound...]); return part}
        let count = try take("\",\"trace_fnv1a64\":\""); let digest = try take("\",\"run_id\":\"")
        guard rest.hasSuffix("\"}\n") else {throw DiagnosticError.invalidConfiguration}; let run = String(rest.dropLast(3))
        func decimal(_ value: String) -> UInt64? {let digits = value.hasPrefix("+") ? value.dropFirst() : value[...]; guard !digits.isEmpty,digits.utf8.allSatisfy({(48...57).contains($0)}) else {return nil}; return UInt64(value)}
        guard let count = decimal(count),let run = decimal(run),digest.count == 16,let hash = UInt64(digest,radix:16) else {throw DiagnosticError.invalidConfiguration}
        return DiagnosticSidecarManifest(runID:run,trace:ArtifactIdentity(fnv1a64:hash,bytes:count))
    }
}
