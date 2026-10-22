import Statelessness

public enum ObservationOrigin: String { case diagnosticControl,recordedLive,exactReplay,simulation,outOfDomainInjection,counterfactualRestore,editedStateExperiment }
public enum WatchTrigger: Equatable { case changed,everyObservation,equals(DisplayScalar),propertyFailure }
public struct WatchConfig {
    public var model: String?
    public var machine: String?
    public var inputVariant: String?
    public var outputVariant: String?
    public var origin: ObservationOrigin?
    public var path: [PathSegment] = []
    public var schemaVersion: UInt32 = 1
    public var trigger: WatchTrigger = .changed
    public var enabled = true
    public var evaluationInterval: UInt64 = 1
    public var deliveryInterval: UInt64 = 1
    public var baselineTTLSequences: UInt64?
    public var ttlObservations: UInt64?
    public var limits = InspectLimits()
    public init() {}
}
public struct WatchAuthorization {
    private let prefixes: [[PathSegment]]
    public init(allowedPaths: [[PathSegment]]) { prefixes = allowedPaths }
    public static var denyAll: Self { Self(allowedPaths: []) }
    public static var localAll: Self { Self(allowedPaths: [[]]) }
    public func allows(_ path: [PathSegment]) -> Bool { prefixes.contains { path.starts(with: $0) } }
}
public enum WatchError: Error { case capacity,unauthorized,invalidConfig,unknownWatch,revisionExhausted,redactedPath }
public struct WatchAck {
    public let watchID: UInt64
    public let generation: UInt64
    public let captureRevision: UInt64
    public let effectiveSequence: UInt64
}
public enum WatchEventKind: Equatable { case schemaChanged,enabled,disabled,removed,expired,baseline,changed,unchanged,changeSinceLastSample(Bool),unknown,propertyFailure,valueMatched,inspectorError,observationGap }
public enum BaselineReason: String { case initial,reenabled,sourceChanged,expired,observationGap,baselineEvicted }
public struct WatchObservationContext {
    public let snapshot: SnapshotID
    public let origin: ObservationOrigin
    public var model: String?
    public var machine: String?
    public var inputVariant: String?
    public var outputVariants: [String] = []
    public var propertyFailure: Bool = false
    public init(snapshot: SnapshotID,origin: ObservationOrigin) { self.snapshot = snapshot; self.origin = origin }
}
public struct WatchEvent {
    public let watchID: UInt64
    public let generation: UInt64
    public let captureRevision: UInt64
    public let snapshot: SnapshotID
    public let origin: ObservationOrigin
    public let kind: WatchEventKind
    public let value: InspectNode?
    public let difference: DiffKind
    public let baselineReason: BaselineReason?
    public init(watchID: UInt64,generation: UInt64,captureRevision: UInt64,snapshot: SnapshotID,origin: ObservationOrigin,kind: WatchEventKind,value: InspectNode?,difference: DiffKind,baselineReason: BaselineReason? = nil) {
        self.watchID = watchID; self.generation = generation; self.captureRevision = captureRevision; self.snapshot = snapshot; self.origin = origin; self.kind = kind; self.value = value; self.difference = difference; self.baselineReason = baselineReason
    }
}
public struct WatchHealth {
    public fileprivate(set) var evaluated: UInt64 = 0
    public fileprivate(set) var filtered: UInt64 = 0
    public fileprivate(set) var evaluationSampled: UInt64 = 0
    public fileprivate(set) var deliverySampled: UInt64 = 0
    public fileprivate(set) var droppedEvents: UInt64 = 0
    public fileprivate(set) var observationGaps: UInt64 = 0
    public fileprivate(set) var inspectionErrors: UInt64 = 0
}
private struct WatchEntry {
    var id: UInt64
    var generation: UInt64
    var config: WatchConfig
    var selected: UInt64 = 0
    var emissions: UInt64 = 0
    var baseline: InspectResult?
    var baselineSequence: UInt64?
    var resetReason: BaselineReason = .initial
    var validatedSchema: DisplaySchema?
    var sourceModel: ExactString?
    var sourceMachine: ExactString?
    var sourceOrigin: ObservationOrigin?
}
private func satIncrement(_ value: inout UInt64) { if value < UInt64.max { value += 1 } }
private func nodePayloadBytes(_ node: InspectNode) -> Int { nodeRetainedPayloadBytes(node) }
/// Single ordered producer, independent revisioned configuration and bounded ring.
/// Local acknowledgements do not imply multi-producer or remote acknowledgement.
public final class WatchRegistry {
    private let authorization: WatchAuthorization
    private let maxWatches: Int
    private let maxRecords: Int
    private let maxBytes: Int
    private let maxBaselineBytes: Int
    private var entries: [UInt64:WatchEntry] = [:]
    private var nextID: UInt64 = 1
    public private(set) var captureRevision: UInt64 = 0
    public private(set) var health = WatchHealth()
    private var events: [(WatchEvent,Int)] = []
    private var ringBytes = 0
    private var lastSnapshot: SnapshotID?
    private var observing = false
    public init(authorization: WatchAuthorization,maxWatches: Int = 64,maxRecords: Int = 1000,maxBytes: Int = 4*1024*1024,maxBaselineBytes: Int = 8*1024*1024) throws {
        guard maxWatches >= 0,maxRecords >= 0,maxBytes >= 0,maxBaselineBytes >= 0 else { throw WatchError.invalidConfig }
        self.authorization = authorization; self.maxWatches = maxWatches; self.maxRecords = maxRecords; self.maxBytes = maxBytes; self.maxBaselineBytes = maxBaselineBytes
    }
    private func mutate() throws {
        guard !observing else { throw DebugError.reentrant }
        guard captureRevision != UInt64.max else { throw WatchError.revisionExhausted }; captureRevision += 1
    }
    private func validateConfig(_ config: WatchConfig) throws {
        guard authorization.allows(config.path) else { throw WatchError.unauthorized }
        guard config.evaluationInterval > 0,config.deliveryInterval > 0,config.ttlObservations != 0,config.path.count <= config.limits.maxDepth,config.limits.maxBytes > 0,config.limits.maxBytes <= maxBytes,config.limits.maxDepth >= 0,config.limits.maxDepth <= 32,config.limits.maxNodes >= 0,config.limits.maxWork >= 0,config.limits.maxValueBytes >= 0,config.path.count <= 32 else { throw WatchError.invalidConfig }
        var bytes = 0
        for label in [config.model,config.machine,config.inputVariant,config.outputVariant].compactMap({ $0 }) { guard !label.isEmpty,label.utf8.count <= 1024,label.utf8.count <= 4096-bytes else { throw WatchError.invalidConfig }; bytes += label.utf8.count }
        func text(_ value: ExactString) throws { guard value.value.utf8.count <= 4096-bytes else { throw WatchError.invalidConfig }; bytes += value.value.utf8.count }
        for segment in config.path {
            switch segment {
            case let .field(s),let .variant(s),let .mapKey(s): try text(s)
            case let .typedMapKey(key): switch key { case let .string(s): try text(s); case let .integer(k,d): try text(k); try text(d); default: break }
            default: break
            }
        }
    }
    public func add(_ config: WatchConfig) throws -> WatchAck {
        try validateConfig(config)
        guard entries.count < maxWatches,nextID != UInt64.max else { throw WatchError.capacity }
        try mutate()
        let id = nextID; nextID += 1
        entries[id] = WatchEntry(id: id,generation: 1,config: config)
        let sequence = lastSnapshot.map { $0.sequence == UInt64.max ? UInt64.max : $0.sequence+1 } ?? 0
        return WatchAck(watchID: id,generation: 1,captureRevision: captureRevision,effectiveSequence: sequence)
    }
    public func setEnabled(_ id: UInt64,enabled: Bool) throws -> WatchAck {
        guard var entry = entries[id] else { throw WatchError.unknownWatch }
        guard entry.generation != UInt64.max else { throw WatchError.revisionExhausted }
        try mutate(); entry.generation += 1; entry.config.enabled = enabled; entry.baseline = nil; entry.baselineSequence = nil; entry.resetReason = .reenabled; entry.selected = 0; entries[id] = entry
        return WatchAck(watchID: id,generation: entry.generation,captureRevision: captureRevision,effectiveSequence: lastSnapshot?.sequence ?? 0)
    }
    public func remove(_ id: UInt64) throws { guard entries[id] != nil else { throw WatchError.unknownWatch }; try mutate(); entries[id] = nil }
    public func observe(snapshot: SnapshotID,origin: ObservationOrigin,propertyFailure: Bool = false,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws {
        var context = WatchObservationContext(snapshot: snapshot,origin: origin); context.propertyFailure = propertyFailure
        try observe(context: context,project: project)
    }
    public func observe(context: WatchObservationContext,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws {
        let snapshot = context.snapshot,origin = context.origin,propertyFailure = context.propertyFailure
        guard context.outputVariants.count <= 1024,[context.model,context.machine,context.inputVariant].compactMap({ $0 }).allSatisfy({ $0.utf8.count <= 1024 }),context.outputVariants.allSatisfy({ $0.utf8.count <= 1024 }) else { throw WatchError.invalidConfig }
        guard !observing else { throw DebugError.reentrant }; observing = true; defer { observing = false }
        let gap: Bool
        if let lastSnapshot {
            guard snapshot.session != lastSnapshot.session || snapshot.sequence > lastSnapshot.sequence else { satIncrement(&health.filtered); return }
            gap = snapshot.session != lastSnapshot.session || lastSnapshot.sequence == UInt64.max || snapshot.sequence != lastSnapshot.sequence+1
        } else { gap = false }
        if gap { satIncrement(&health.observationGaps) }
        lastSnapshot = snapshot
        var projections: [([PathSegment],InspectLimits,Result<InspectResult,Error>)] = []
        for id in entries.keys.sorted() {
            guard var entry = entries[id],entry.config.enabled else { continue }
            func matches(_ expected: String?,_ actual: String?) -> Bool { expected == nil || (actual.map { exactText(expected!,$0) } ?? false) }
            guard matches(entry.config.model,context.model),matches(entry.config.machine,context.machine),matches(entry.config.inputVariant,context.inputVariant),entry.config.outputVariant == nil || context.outputVariants.prefix(1024).contains(where: { exactText(entry.config.outputVariant!,$0) }),entry.config.origin == nil || entry.config.origin == origin else { satIncrement(&health.filtered); continue }
            satIncrement(&entry.selected)
            if let ttl = entry.config.ttlObservations,entry.selected > ttl { entry.config.enabled = false; entry.baseline = nil; entries[id] = entry; continue }
            if !propertyFailure && (entry.selected-1) % entry.config.evaluationInterval != 0 {
                satIncrement(&health.evaluationSampled)
                if let ttl = entry.config.ttlObservations,entry.selected >= ttl { entry.config.enabled = false; entry.baseline = nil; entry.baselineSequence = nil; append(WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: .expired,value: nil,difference: .unknown)) }
                entries[id] = entry; continue
            }
            satIncrement(&health.evaluated)
            if gap { entry.baseline = nil; entry.resetReason = .observationGap }
            if let ttl = entry.config.baselineTTLSequences,let sequence = entry.baselineSequence,snapshot.sequence > sequence,snapshot.sequence-sequence > ttl { entry.baseline = nil; entry.resetReason = .expired }
            let value: InspectResult
            do {
                if let cached = projections.first(where: { $0.0 == entry.config.path && $0.1 == entry.config.limits }) { value = try cached.2.get() }
                else {
                    let result = Result { try project(entry.config.path,entry.config.limits) }; projections.append((entry.config.path,entry.config.limits,result)); value = try result.get()
                }
                try validateProjection(value.node,limits: entry.config.limits)
                guard !value.schema.name.isEmpty,value.schema.name.utf8.count <= 1024,(value.schema.source?.file.utf8.count ?? 0) <= 4096,value.path == entry.config.path,value.snapshot == snapshot else { throw InspectError.schemaMismatch }
            } catch {
                satIncrement(&health.inspectionErrors); entry.baseline = nil
                append(WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: .inspectorError,value: nil,difference: .unknown)); entries[id] = entry; continue
            }
            if value.schema.version != entry.config.schemaVersion || (entry.validatedSchema.map { !exactText($0.name,value.schema.name) || $0.version != value.schema.version } ?? false) {
                entry.config.enabled = false; entry.baseline = nil; entry.resetReason = .observationGap; entries[id] = entry
                append(WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: .schemaChanged,value: nil,difference: .unknown)); continue
            }
            entry.validatedSchema = value.schema
            if entry.baseline != nil && (entry.sourceModel != context.model.map(ExactString.init) || entry.sourceMachine != context.machine.map(ExactString.init) || entry.sourceOrigin != origin) { entry.baseline = nil; entry.resetReason = .sourceChanged }
            let difference = entry.baseline.map { diff($0,value) } ?? .unknown
            let first = entry.baseline == nil
            var kind: WatchEventKind
            if first { kind = .baseline } else if difference == .unknown { kind = .unknown } else if entry.config.evaluationInterval > 1 { kind = .changeSinceLastSample(difference != .unchanged) } else { kind = difference == .unchanged ? .unchanged : .changed }
            var emit: Bool
            switch entry.config.trigger {
            case .changed: emit = first || difference != .unchanged
            case .everyObservation: emit = true
            case let .equals(scalar): emit = value.node.isComplete && value.node.kind == .scalar(scalar); if emit { kind = .valueMatched }
            case .propertyFailure: emit = propertyFailure; if emit { kind = .propertyFailure }
            }
            if propertyFailure { emit = true; kind = .propertyFailure }
            let reason = first ? entry.resetReason : nil
            let size = nodePayloadBytes(value.node)+value.schema.name.utf8.count+(context.model?.utf8.count ?? 0)+(context.machine?.utf8.count ?? 0)
            let otherBytes = entries.values.reduce(0) { sum,item in
                let size = item.id == id ? 0 : item.baseline.map { nodePayloadBytes($0.node)+$0.schema.name.utf8.count+(item.sourceModel?.value.utf8.count ?? 0)+(item.sourceMachine?.value.utf8.count ?? 0) } ?? 0
                return size > Int.max-sum ? Int.max : sum+size
            }
            if size <= maxBaselineBytes,otherBytes <= maxBaselineBytes-size { entry.baseline = value; entry.baselineSequence = snapshot.sequence; entry.sourceModel = context.model.map(ExactString.init); entry.sourceMachine = context.machine.map(ExactString.init); entry.sourceOrigin = origin } else { entry.baseline = nil; entry.baselineSequence = nil; entry.resetReason = .baselineEvicted }
            if emit {
                satIncrement(&entry.emissions)
                if propertyFailure || (entry.emissions-1) % entry.config.deliveryInterval == 0 { append(WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: kind,value: value.node,difference: (first || entry.config.evaluationInterval > 1) ? .unknown : difference,baselineReason: reason)) }
                else { satIncrement(&health.deliverySampled) }
            }
            if let ttl = entry.config.ttlObservations,entry.selected >= ttl {
                entry.config.enabled = false; entry.baseline = nil; entry.baselineSequence = nil
                append(WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: .expired,value: nil,difference: .unknown))
            }
            entries[id] = entry
        }
    }
    private func append(_ event: WatchEvent) {
        let payload = event.value.map(nodePayloadBytes) ?? 0
        let (size,overflow) = payload.addingReportingOverflow(128)
        guard !overflow,size <= maxBytes,maxRecords > 0 else { satIncrement(&health.droppedEvents); return }
        while events.count >= maxRecords || ringBytes > maxBytes-size { let removed = events.removeFirst(); ringBytes -= removed.1; satIncrement(&health.droppedEvents) }
        events.append((event,size)); ringBytes += size
    }
    public func drain(limit: Int) throws -> [WatchEvent] {
        guard !observing,limit > 0,limit <= maxRecords else { throw WatchError.invalidConfig }
        let count = min(limit,events.count), result = events.prefix(count).map { $0.0 }
        for item in events.prefix(count) { ringBytes -= item.1 }; events.removeFirst(count); return result
    }
}

public struct WatchStatus {
    public let id: UInt64
    public let generation: UInt64
    public let enabled: Bool
    public let config: WatchConfig
    public let hasBaseline: Bool
}
extension WatchRegistry {
    /// Resolve authorized paths on a current explicit boundary before installing
    /// for an external consumer; redacted nodes never become readable watches.
    public func addValidated(_ config: WatchConfig,snapshot: SnapshotID,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws -> WatchAck {
        guard !observing else { throw DebugError.reentrant }
        try validateConfig(config)
        observing = true
        let result: InspectResult
        do { result = try project(config.path,config.limits); observing = false }
        catch { observing = false; throw error }
        try validateProjection(result.node,limits: config.limits)
        guard result.snapshot == snapshot,result.path == config.path,result.schema.version == config.schemaVersion else { throw InspectError.schemaMismatch }
        guard result.node.kind != .redacted else { throw WatchError.redactedPath }
        let ack = try add(config)
        if var entry = entries[ack.watchID] { entry.validatedSchema = result.schema; entries[ack.watchID] = entry }
        return ack
    }
    public func update(_ id: UInt64,config: WatchConfig) throws -> WatchAck {
        guard var entry = entries[id] else { throw WatchError.unknownWatch }
        try validateConfig(config)
        guard entry.generation != UInt64.max else { throw WatchError.revisionExhausted }
        try mutate(); entry.generation += 1; entry.config = config; entry.selected = 0; entry.emissions = 0; entry.baseline = nil; entry.baselineSequence = nil; entry.resetReason = .reenabled; entries[id] = entry
        return WatchAck(watchID: id,generation: entry.generation,captureRevision: captureRevision,effectiveSequence: lastSnapshot?.sequence ?? 0)
    }
    public func pause(_ id: UInt64) throws -> WatchAck { try setEnabled(id,enabled: false) }
    public func resume(_ id: UInt64) throws -> WatchAck { try setEnabled(id,enabled: true) }
    public func current(_ id: UInt64) throws -> InspectResult? { guard let entry = entries[id] else { throw WatchError.unknownWatch }; return entry.baseline }
    public func list(offset: Int = 0,limit: Int = 100) throws -> [WatchStatus] {
        guard offset >= 0,limit > 0,limit <= 1000 else { throw WatchError.invalidConfig }
        let keys = entries.keys.sorted()
        return keys.dropFirst(offset).prefix(limit).compactMap { id in entries[id].map { WatchStatus(id: id,generation: $0.generation,enabled: $0.config.enabled,config: $0.config,hasBaseline: $0.baseline != nil) } }
    }
}
