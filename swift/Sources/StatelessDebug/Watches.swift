import Statelessness

// Source locations are display metadata, not part of watch schema identity.
private func sameWatchSchema(_ a: DisplaySchema,_ b: DisplaySchema) -> Bool {
    exactText(a.name,b.name) && a.version == b.version
}

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
    public var limits: InspectLimits = { var limits = InspectLimits(); limits.maxBytes = 32 * 1024; return limits }()
    public var page = PageRequest()
    public init() {}
}
public struct WatchAuthorization {
    private let prefixes: [[PathSegment]]
    public init(allowedPaths: [[PathSegment]]) { prefixes = allowedPaths }
    public static var denyAll: Self { Self(allowedPaths: []) }
    public static var localAll: Self { Self(allowedPaths: [[]]) }
    public func allows(_ path: [PathSegment]) -> Bool { prefixes.contains { path.starts(with: $0) } }
}
public enum WatchError: Error { case capacity,unauthorized,invalidConfig,unknownWatch,revisionExhausted,redactedPath; case schemaMismatch(expected: UInt32,actual: UInt32) }
public struct WatchAck {
    public let watchID: UInt64
    public let generation: UInt64
    public let captureRevision: UInt64
    public let effectiveSequence: UInt64
    public let displaySchema: DisplaySchema?
    public let resolvedType: String?
}
public enum WatchEventKind: Equatable { case schemaChanged,enabled,disabled,paused,removed,expired,eventsDropped(UInt64),baseline,changed,unchanged,changeSinceLastSample(Bool),unknown,propertyFailure,valueMatched,inspectorError,observationGap,snapshot }
public enum WatchState { case active,paused,expired,disabled,removed }
public enum BaselineReason: String { case initial,reenabled,sourceChanged,expired,observationGap,baselineEvicted }
public struct WatchObservationContext {
    public let snapshot: SnapshotID
    public let origin: ObservationOrigin
    public var model: String?
    public var machine: String?
    public var inputVariant: String?
    public var outputVariants: [String] = []
    public var propertyFailure: Bool = false
    /// Host-supplied identifiers only; event retention bounds their count and size.
    public var failingProperties: [String] = []
    /// False when the source omitted identifiers before passing them to the registry.
    public var failingPropertiesComplete: Bool = true
    /// Optional schema known independently of the projection callback.
    public var declaredSchema: DisplaySchema?
    public init(snapshot: SnapshotID,origin: ObservationOrigin) { self.snapshot = snapshot; self.origin = origin }
}
public enum WatchObservationPhase { case initial,postTurn,control }
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
    public let inspectionError: String?
    public let path: [PathSegment]
    public let trigger: WatchTrigger
    public let schema: DisplaySchema?
    public let phase: WatchObservationPhase
    public let model: String?
    public let machine: String?
    public let previousSnapshot: SnapshotID?
    public let propertyFailure: Bool
    public let failingProperties: [String]
    public let completeness: Completeness
    public init(watchID: UInt64,generation: UInt64,captureRevision: UInt64,snapshot: SnapshotID,origin: ObservationOrigin,kind: WatchEventKind,value: InspectNode?,difference: DiffKind,baselineReason: BaselineReason? = nil,inspectionError: String? = nil,path: [PathSegment] = [],trigger: WatchTrigger = .everyObservation,schema: DisplaySchema? = nil,phase: WatchObservationPhase = .control,model: String? = nil,machine: String? = nil,previousSnapshot: SnapshotID? = nil,propertyFailure: Bool = false,failingProperties: [String] = [],completeness: Completeness? = nil) {
        self.watchID = watchID; self.generation = generation; self.captureRevision = captureRevision; self.snapshot = snapshot; self.origin = origin; self.kind = kind; self.value = value; self.difference = difference; self.baselineReason = baselineReason; self.inspectionError = inspectionError
        self.path = path; self.trigger = trigger; self.schema = schema.map { DisplaySchema(name: boundedWatchText($0.name,limit:1024),version:$0.version,source:$0.source) }; self.phase = phase
        self.model = model; self.machine = machine; self.previousSnapshot = previousSnapshot; self.propertyFailure = propertyFailure
        self.failingProperties = failingProperties
        let originalCompleteness = completeness ?? value?.completeness ?? .complete
        self.completeness = (schema?.name.utf8.prefix(1025).count ?? 0) > 1024 && originalCompleteness == .complete ? .partial(.valueLimit) : originalCompleteness
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
    /// Evaluated projections whose node is incomplete, even if not delivered.
    public fileprivate(set) var truncated: UInt64 = 0
    public fileprivate(set) var baselineEvictions: UInt64 = 0
}
/// A bounded diagnostic ring snapshot; this is not replayable exact evidence.
public struct WatchSnapshot {
    public let captureRevision: UInt64
    public let at: SnapshotID?
    public let events: [WatchEvent]
    public let retainedBytes: Int
    public let health: WatchHealth
}
private struct WatchEntry {
    var id: UInt64
    var generation: UInt64
    var config: WatchConfig
    var state: WatchState = .active
    var selected: UInt64 = 0
    var sampled = false
    var emissions: UInt64 = 0
    var baseline: InspectResult?
    var baselineSequence: UInt64?
    var resetReason: BaselineReason = .initial
    var validatedSchema: DisplaySchema?
    var resolvedType: String?
    var sourceModel: ExactString?
    var sourceMachine: ExactString?
    var sourceOrigin: ObservationOrigin?
    var currentEvent: WatchEvent?
    var effectiveSequence: UInt64 = 0
}
private func satIncrement(_ value: inout UInt64) { if value < UInt64.max { value += 1 } }
private func normalizedWatchInspectionError(_ error: Error) -> String {
    guard let error = error as? InspectError else { return "InvalidMetadata" }
    switch error {
    case .pathNotFound: return "PathNotFound"
    case .invalidPage: return "InvalidPage"
    case .budgetExceeded: return "BudgetExceeded"
    default: return "InvalidMetadata"
    }
}
private func nodePayloadBytes(_ node: InspectNode) -> Int { nodeRetainedPayloadBytes(node) }
private func resolvedWatchType(_ node: InspectNode) -> String {
    switch node.kind {
    case .scalar(.unit): return "unit"
    case .scalar(.bool): return "bool"
    case .scalar(.character): return "char"
    case let .scalar(.integer(kind,_)),let .scalar(.float(kind,_,_)): return boundedWatchText(kind.value,limit:1024)
    case .string: return "string"
    case .bytes: return "bytes"
    case .object: return "object"
    case .enumeration: return "enum"
    case .sequence: return "sequence"
    case .map: return "map"
    case .opaque: return "opaque"
    case .redacted: return "redacted"
    case .unavailable: return "unavailable"
    case .truncated: return "truncated"
    }
}
private func watchAdd(_ a: Int,_ b: Int) -> Int { let (sum,overflow) = a.addingReportingOverflow(b); return overflow ? Int.max : sum }
private func boundedWatchText(_ id: String,limit: Int) -> String {
        var result = String.UnicodeScalarView(), bytes = 0
        for scalar in id.unicodeScalars {
            let length = scalar.utf8.count
            if length > limit-bytes { break }
            result.append(scalar); bytes += length
        }
        return String(result)
}
private func boundedPropertyIDs(_ ids: [String]) -> [String] { ids.prefix(16).map { boundedWatchText($0,limit:256) } }
private func incompleteWatchMetadata(_ context: WatchObservationContext) -> Bool {
    !context.failingPropertiesComplete || context.failingProperties.count > 16 || context.failingProperties.prefix(16).contains { $0.utf8.prefix(257).count > 256 } || (context.model?.utf8.prefix(1025).count ?? 0) > 1024 || (context.machine?.utf8.prefix(1025).count ?? 0) > 1024
}
private func watchPathBytes(_ path: [PathSegment]) -> Int {
    path.reduce(0) { total,segment in
        let text: Int
        switch segment {
        case let .field(s),let .variant(s),let .mapKey(s): text = s.value.utf8.count
        case let .typedMapKey(key):
            switch key { case let .string(s): text = s.value.utf8.count; case let .integer(k,d): text = watchAdd(k.value.utf8.count,d.value.utf8.count); default: text = 0 }
        case .index: text = 0
        }
        return watchAdd(total,watchAdd(64,text))
    }
}
/// Single ordered producer, independent revisioned configuration and bounded ring.
/// Local acknowledgements do not imply multi-producer or remote acknowledgement.
public final class WatchRegistry {
    private let authorization: WatchAuthorization
    private let maxWatches: Int
    private let maxRecords: Int
    private let maxBytes: Int
    private let maxEventBytes: Int
    public let schemaVersion: UInt32
    var protocolMaxBytes: Int { maxEventBytes }
    private let maxBaselineBytes: Int
    private var entries: [UInt64:WatchEntry] = [:]
    // IDs are assigned monotonically, so this remains sorted without sorting
    // the entire registry for each bounded page or observation.
    private var orderedIDs: [UInt64] = []
    private var nextID: UInt64 = 1
    public private(set) var captureRevision: UInt64 = 0
    public private(set) var health = WatchHealth()
    /// Hosts can skip projection work when no watch is enabled.
    public var interested: Bool { entries.values.contains { $0.config.enabled } }
    private var events: [(WatchEvent,Int)] = []
    private var ringBytes = 0
    private var reportedDrops: UInt64 = 0
    private var lastSnapshot: SnapshotID?
    private var lastGapSnapshot: SnapshotID?
    public private(set) var failureSnapshot: WatchSnapshot?
    private var observing = false
    private func eventBytes(_ event: WatchEvent) -> Int {
        // Typed parts keep this within Swift 6.0's type-checking budget.
        let value: Int = event.value.map(nodePayloadBytes) ?? 0
        let schema: Int = event.schema?.name.utf8.count ?? 0
        let source: Int = event.schema?.source?.file.utf8.count ?? 0
        let error: Int = event.inspectionError?.utf8.count ?? 0
        let model: Int = event.model?.utf8.count ?? 0
        let machine: Int = event.machine?.utf8.count ?? 0
        let parts: [Int] = [128, value, watchPathBytes(event.path), schema, source, error, model, machine]
        var size = parts.reduce(0, watchAdd)
        for id in event.failingProperties { size = watchAdd(size,watchAdd(24,id.utf8.count)) }
        if case let .equals(scalar) = event.trigger {
            switch scalar {
            case let .integer(kind,decimal),let .float(kind,decimal,_): size = watchAdd(size,watchAdd(kind.value.utf8.count,decimal.value.utf8.count))
            default: break
            }
        }
        return size
    }
    private func projectionLimits(_ entry: WatchEntry,snapshot: SnapshotID,origin: ObservationOrigin,schema: DisplaySchema?,model: String?,machine: String?,failureIDs: [String]) -> InspectLimits {
        let metadata = WatchEvent(watchID:entry.id,generation:entry.generation,captureRevision:captureRevision,snapshot:snapshot,origin:origin,kind:.snapshot,value:nil,difference:.unknown,path:entry.config.path,trigger:entry.config.trigger,schema:schema,model:model,machine:machine,failingProperties:failureIDs)
        let overhead = eventBytes(metadata)
        var limits = entry.config.limits
        limits.maxBytes = min(limits.maxBytes,max(0,maxEventBytes-min(maxEventBytes,overhead)))
        return limits
    }
    // Validated schema identity is separately bounded by maxWatches and the
    // 4096-byte metadata limit. Account for the additional full name retained
    // by a baseline beyond the truncated name in its current event.
    private func baselineBytes(_ entry: WatchEntry) -> Int {
        guard let current = entry.currentEvent else { return 0 }
        let fullName = entry.baseline?.schema.name.utf8.count ?? 0
        let eventName = current.schema?.name.utf8.count ?? 0
        return watchAdd(eventBytes(current),max(0,fullName-eventName))
    }
    private func enforceBaselines() {
        var used = entries.values.reduce(0) { watchAdd($0,baselineBytes($1)) }
        while used > maxBaselineBytes {
            let oldest = orderedIDs.compactMap { id -> (UInt64,UInt64)? in
                guard let event = entries[id]?.currentEvent else { return nil }
                return (id,event.snapshot.sequence)
            }.min { a,b in a.1 == b.1 ? a.0 < b.0 : a.1 < b.1 }
            guard let id = oldest?.0,var entry = entries[id],entry.currentEvent != nil else { break }
            used = max(0,used-baselineBytes(entry))
            entry.currentEvent = nil; entry.baseline = nil; entry.baselineSequence = nil
            entry.sourceModel = nil; entry.sourceMachine = nil; entry.sourceOrigin = nil; entry.resetReason = .baselineEvicted
            entries[id] = entry; satIncrement(&health.baselineEvictions)
        }
    }
    /// `maxBytes` bounds the ring; `maxEventBytes` independently bounds each event.
    /// Passing nil explicitly for `maxEventBytes` retains the legacy ring-sized event limit.
    public init(authorization: WatchAuthorization,maxWatches: Int = 64,maxRecords: Int = 1000,maxBytes: Int = 4*1024*1024,maxBaselineBytes: Int = 8*1024*1024,maxEventBytes: Int? = 32*1024,schemaVersion: UInt32 = 1) throws {
        let eventLimit = maxEventBytes ?? maxBytes
        guard maxWatches >= 0,maxRecords >= 0,maxBytes >= 0,maxBaselineBytes >= 0,eventLimit >= 0 else { throw WatchError.invalidConfig }
        self.authorization = authorization; self.maxWatches = maxWatches; self.maxRecords = maxRecords; self.maxBytes = maxBytes; self.maxEventBytes = eventLimit; self.maxBaselineBytes = maxBaselineBytes; self.schemaVersion = schemaVersion
    }
    private func mutate() throws {
        guard !observing else { throw DebugError.reentrant }
        guard captureRevision != UInt64.max,lastSnapshot?.sequence != UInt64.max else { throw WatchError.revisionExhausted }; captureRevision += 1
    }
    private func preflightAdd() throws {
        guard entries.count < maxWatches else { throw WatchError.capacity }
        guard nextID != UInt64.max,captureRevision != UInt64.max,lastSnapshot?.sequence != UInt64.max else { throw WatchError.revisionExhausted }
    }
    private var nextBoundary: UInt64 { lastSnapshot.map { $0.sequence == .max ? .max : $0.sequence+1 } ?? 0 }
    private func lifecycle(_ entry: WatchEntry,_ kind: WatchEventKind) {
        let snapshot = lastSnapshot ?? SnapshotID(session:0,revision:0,sequence:0)
        append(WatchEvent(watchID:entry.id,generation:entry.generation,captureRevision:captureRevision,snapshot:snapshot,origin:.diagnosticControl,kind:kind,value:nil,difference:.unknown,path:entry.config.path,trigger:entry.config.trigger,schema:entry.validatedSchema,phase:.control,model:entry.config.model,machine:entry.config.machine))
    }
    private func validateConfig(_ config: WatchConfig) throws {
        guard authorization.allows(config.path) else { throw WatchError.unauthorized }
        guard config.schemaVersion == schemaVersion else { throw WatchError.schemaMismatch(expected:schemaVersion,actual:config.schemaVersion) }
        guard config.evaluationInterval > 0,config.deliveryInterval > 0,config.ttlObservations != 0,config.path.count <= config.limits.maxDepth,config.limits.maxBytes > 0,config.limits.maxBytes <= maxEventBytes,config.limits.maxDepth >= 0,config.limits.maxDepth <= 32,config.limits.maxNodes >= 0,config.limits.maxWork >= 0,config.limits.maxValueBytes >= 0,config.page.offset >= 0,config.page.limit > 0,config.page.limit <= config.limits.maxPageSize,config.path.count <= 32 else { throw WatchError.invalidConfig }
        var bytes = 0
        for label in [config.model,config.machine,config.inputVariant,config.outputVariant].compactMap({ $0 }) { let size = label.utf8.prefix(1025).count; guard !label.isEmpty,size <= 1024,size <= 4096-bytes else { throw WatchError.invalidConfig }; bytes += size }
        func text(_ value: ExactString) throws { let size = value.value.utf8.prefix(4097-bytes).count; guard size <= 4096-bytes else { throw WatchError.invalidConfig }; bytes += size }
        for segment in config.path {
            switch segment {
            case let .field(s),let .variant(s),let .mapKey(s): try text(s)
            case let .typedMapKey(key):
                switch key { case let .string(s): try text(s); case let .integer(k,d): try text(k); try text(d); default: break }
                switch key {
                case let .integer(kind,_):
                    let kinds = ["i8","i16","i32","i64","i128","isize","u8","u16","u32","u64","u128","usize"]
                    guard kinds.contains(where:{ exactText($0,kind.value) }) else { throw WatchError.invalidConfig }
                case let .character(value): guard UnicodeScalar(value) != nil else { throw WatchError.invalidConfig }
                default: break
                }
            default: break
            }
        }
        if case let .equals(scalar) = config.trigger {
            switch scalar {
            case let .integer(kind,decimal):
                let kinds = ["i8","i16","i32","i64","i128","isize","u8","u16","u32","u64","u128","usize"]
                guard kinds.contains(where:{ exactText($0,kind.value) }) else { throw WatchError.invalidConfig }
                try text(kind); try text(decimal)
            case let .float(kind,decimal,_): try text(kind); try text(decimal)
            case let .character(value): guard UnicodeScalar(value) != nil else { throw WatchError.invalidConfig }
            default: break
            }
        }
    }
    public func add(_ config: WatchConfig) throws -> WatchAck {
        try addPrepared(config,schema:nil,resolvedType:nil)
    }
    private func addPrepared(_ config: WatchConfig,schema: DisplaySchema?,resolvedType: String?) throws -> WatchAck {
        try validateConfig(config)
        try preflightAdd()
        try mutate()
        let id = nextID; nextID += 1
        let sequence = lastSnapshot.map { $0.sequence == UInt64.max ? UInt64.max : $0.sequence+1 } ?? 0
        entries[id] = WatchEntry(id: id,generation: 1,config: config,effectiveSequence:sequence); orderedIDs.append(id)
        entries[id]!.state = config.enabled ? .active : .disabled
        entries[id]!.validatedSchema = schema
        entries[id]!.resolvedType = resolvedType
        lifecycle(entries[id]!,config.enabled ? .enabled : .disabled)
        return WatchAck(watchID: id,generation: 1,captureRevision: captureRevision,effectiveSequence: sequence,displaySchema:schema,resolvedType:resolvedType)
    }
    public func setEnabled(_ id: UInt64,enabled: Bool) throws -> WatchAck {
        try setState(id,enabled:enabled,kind:enabled ? .enabled : .disabled)
    }
    private func setState(_ id: UInt64,enabled: Bool,kind: WatchEventKind) throws -> WatchAck {
        guard var entry = entries[id] else { throw WatchError.unknownWatch }
        guard entry.generation != UInt64.max else { throw WatchError.revisionExhausted }
        try mutate(); entry.generation += 1; entry.config.enabled = enabled; entry.state = enabled ? .active : (kind == .paused ? .paused : .disabled); entry.baseline = nil; entry.baselineSequence = nil; entry.currentEvent = nil; entry.resetReason = .reenabled; entry.selected = 0; entry.emissions = 0; entry.sampled = false; entry.effectiveSequence = nextBoundary; entries[id] = entry
        lifecycle(entry,kind)
        return WatchAck(watchID: id,generation: entry.generation,captureRevision: captureRevision,effectiveSequence: entry.effectiveSequence,displaySchema:entry.validatedSchema,resolvedType:entry.resolvedType)
    }
    public func remove(_ id: UInt64) throws { guard var entry = entries[id] else { throw WatchError.unknownWatch }; guard entry.generation != .max else { throw WatchError.revisionExhausted }; try mutate(); entry.generation += 1; lifecycle(entry,.removed); entries[id] = nil; if let index = orderedIDs.firstIndex(of:id) { orderedIDs.remove(at:index) } }
    public func observe(snapshot: SnapshotID,origin: ObservationOrigin,propertyFailure: Bool = false,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws {
        var context = WatchObservationContext(snapshot: snapshot,origin: origin); context.propertyFailure = propertyFailure
        try observe(context: context,project: project)
    }
    public func observePaged(snapshot: SnapshotID,origin: ObservationOrigin,propertyFailure: Bool = false,project: ([PathSegment],PageRequest,InspectLimits) throws -> InspectResult) throws {
        var context = WatchObservationContext(snapshot:snapshot,origin:origin); context.propertyFailure = propertyFailure
        try observePaged(context:context,project:project)
    }
    func recordObservationGap(snapshot: SnapshotID,origin: ObservationOrigin) {
        guard !observing else { return }
        guard lastGapSnapshot != snapshot else { return }
        invalidateForObservationGap(snapshot:snapshot)
    }
    private func invalidateForObservationGap(snapshot: SnapshotID) {
        satIncrement(&health.observationGaps); lastGapSnapshot = snapshot
        for id in orderedIDs {
            guard var entry = entries[id] else { continue }
            entry.baseline = nil; entry.baselineSequence = nil; entry.currentEvent = nil; entry.resetReason = .observationGap
            entries[id] = entry; lifecycle(entry,.observationGap)
        }
        lastSnapshot = snapshot
    }
    private func invalidateAfterSchemaError(snapshot: SnapshotID) {
        if lastGapSnapshot != snapshot { invalidateForObservationGap(snapshot:snapshot); return }
        // A gap may already have been emitted before earlier watches advanced.
        for id in orderedIDs {
            guard var entry = entries[id] else { continue }
            entry.baseline = nil; entry.baselineSequence = nil; entry.currentEvent = nil; entry.resetReason = .observationGap
            entries[id] = entry
        }
    }
    public func observe(context: WatchObservationContext,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws {
        guard entries.values.allSatisfy({ $0.config.page.offset == 0 && $0.config.page.limit == min(100,$0.config.limits.maxPageSize) }) else { throw WatchError.invalidConfig }
        try observePaged(context:context) { path,_,limits in try project(path,limits) }
    }
    /// A page-aware projection receives each watch's configured page.
    public func observePaged(context: WatchObservationContext,schema: (() throws -> DisplaySchema)? = nil,project: ([PathSegment],PageRequest,InspectLimits) throws -> InspectResult) throws {
        let snapshot = context.snapshot,origin = context.origin,propertyFailure = context.propertyFailure
        guard context.outputVariants.count <= 1024,(context.inputVariant?.utf8.prefix(1025).count ?? 0) <= 1024,context.outputVariants.allSatisfy({ $0.utf8.prefix(1025).count <= 1024 }) else { throw WatchError.invalidConfig }
        if let schema = context.declaredSchema {
            guard !schema.name.isEmpty,schema.name.utf8.prefix(4097).count <= 4096,(schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.invalidMetadata }
        }
        guard !observing else { throw DebugError.reentrant }; observing = true; defer { observing = false }
        let gap: Bool
        if let lastSnapshot {
            guard snapshot.session != lastSnapshot.session || snapshot.sequence > lastSnapshot.sequence else { return }
            gap = snapshot.session != lastSnapshot.session || lastSnapshot.sequence == UInt64.max || snapshot.sequence != lastSnapshot.sequence+1
        } else { gap = false }
        if !interested { lastSnapshot = snapshot; return }
        if gap { invalidateForObservationGap(snapshot:snapshot) }
        lastSnapshot = snapshot
        let failureIDs = boundedPropertyIDs(context.failingProperties)
        let incompleteIDs = incompleteWatchMetadata(context)
        let eventModel = context.model.map { boundedWatchText($0,limit:1024) }, eventMachine = context.machine.map { boundedWatchText($0,limit:1024) }
        var projections: [([PathSegment],PageRequest,InspectLimits,Result<InspectResult,Error>)] = []
        for id in orderedIDs {
            guard var entry = entries[id],entry.config.enabled else { continue }
            // Rust WatchRegistry::observe checks TTL before selector matching
            // and emits WatchExpired; filtered counts never include it.
            if let ttl = entry.config.ttlObservations,entry.selected >= ttl {
                entry.config.enabled = false; entry.state = .expired; entry.currentEvent = nil; entry.baseline = nil; entry.baselineSequence = nil
                entries[id] = entry; lifecycle(entry,.expired); continue
            }
            func matches(_ expected: String?,_ actual: String?) -> Bool { expected == nil || (actual.map { exactText(expected!,$0) } ?? false) }
            guard matches(entry.config.model,context.model),matches(entry.config.machine,context.machine),matches(entry.config.inputVariant,context.inputVariant),entry.config.outputVariant == nil || context.outputVariants.prefix(1024).contains(where: { exactText(entry.config.outputVariant!,$0) }),entry.config.origin == nil || entry.config.origin == origin else { satIncrement(&health.filtered); continue }
            let resolvedSchema: DisplaySchema?
            do {
                if let declared = context.declaredSchema { resolvedSchema = declared }
                else { resolvedSchema = try schema?() }
                if let declared = resolvedSchema {
                    guard !declared.name.isEmpty,declared.name.utf8.prefix(4097).count <= 4096,(declared.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.invalidMetadata }
                }
            } catch { invalidateAfterSchemaError(snapshot:snapshot); throw error }
            if let declared = resolvedSchema {
                if declared.version != entry.config.schemaVersion || (entry.validatedSchema.map { !exactText($0.name,declared.name) || $0.version != declared.version } ?? false) {
                    entry.config.enabled = false; entry.state = .paused; entry.baseline = nil; entry.currentEvent = nil; entry.resetReason = .observationGap
                    entries[id] = entry; lifecycle(entry,.schemaChanged); continue
                }
                entry.validatedSchema = declared
            }
            // Source changes must invalidate the baseline even on observations
            // skipped by evaluation sampling, before the next projection.
            if let previous = entry.currentEvent,
               (previous.model.map(ExactString.init) != context.model.map(ExactString.init) ||
                previous.machine.map(ExactString.init) != context.machine.map(ExactString.init) || previous.origin != origin) {
                entry.baseline = nil; entry.resetReason = .sourceChanged
            }
            satIncrement(&entry.selected)
            if !propertyFailure && (entry.selected-1) % entry.config.evaluationInterval != 0 {
                entry.sampled = true
                satIncrement(&health.evaluationSampled)
                if let ttl = entry.config.ttlObservations,entry.selected >= ttl { entry.config.enabled = false; entry.state = .expired; lifecycle(entry,.expired) }
                entries[id] = entry; continue
            }
            satIncrement(&health.evaluated)
            if gap { entry.baseline = nil; entry.resetReason = .observationGap }
            if let ttl = entry.config.baselineTTLSequences,let sequence = entry.baselineSequence,snapshot.sequence > sequence,snapshot.sequence-sequence > ttl { entry.baseline = nil; entry.resetReason = .expired }
            let effectiveLimits = projectionLimits(entry,snapshot:snapshot,origin:origin,schema:resolvedSchema ?? entry.validatedSchema,model:eventModel,machine:eventMachine,failureIDs:failureIDs)
            let value: InspectResult
            do {
                if let cached = projections.first(where: { $0.0 == entry.config.path && $0.1 == entry.config.page && $0.2 == effectiveLimits }) {
                    let shared = try cached.3.get()
                    if let declared = resolvedSchema, !sameWatchSchema(declared,shared.schema) { throw InspectError.schemaMismatch }
                    value = shared
                }
                else {
                    let result = Result { try project(entry.config.path,entry.config.page,effectiveLimits) }
                    projections.append((entry.config.path,entry.config.page,effectiveLimits,result)); value = try result.get()
                    if let declared = resolvedSchema, !sameWatchSchema(declared,value.schema) { throw InspectError.schemaMismatch }
                }
                try validateProjection(value.node,limits: effectiveLimits)
                guard !value.schema.name.isEmpty,value.schema.name.utf8.prefix(4097).count <= 4096,(value.schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096,value.path == entry.config.path,value.snapshot == snapshot else { throw InspectError.schemaMismatch }
            } catch {
                satIncrement(&health.inspectionErrors); entry.baseline = nil; entry.baselineSequence = nil; entry.resetReason = .observationGap; entry.sampled = false
                let failed = WatchEvent(watchID: id,generation: entry.generation,captureRevision: captureRevision,snapshot: snapshot,origin: origin,kind: .inspectorError,value: nil,difference: .unknown,inspectionError:normalizedWatchInspectionError(error),path:entry.config.path,trigger:entry.config.trigger,schema:entry.validatedSchema,phase:snapshot.sequence == 0 ? .initial : .postTurn,model:eventModel,machine:eventMachine,previousSnapshot:entry.currentEvent?.snapshot,propertyFailure:propertyFailure,failingProperties:failureIDs,completeness:.partial(.unavailable))
                entry.currentEvent = eventBytes(failed) <= maxEventBytes ? failed : nil
                // TTL measures the retained observation, including inspection errors.
                entry.baselineSequence = entry.currentEvent == nil ? nil : snapshot.sequence
                let emit: Bool
                switch entry.config.trigger { case .changed,.everyObservation: emit = true; case .equals,.propertyFailure: emit = propertyFailure }
                if emit {
                    satIncrement(&entry.emissions)
                    if propertyFailure || (entry.emissions-1) % entry.config.deliveryInterval == 0 { append(failed) }
                    else { satIncrement(&health.deliverySampled) }
                }
                entries[id] = entry; enforceBaselines(); entry = entries[id]!
                if let ttl = entry.config.ttlObservations,entry.selected >= ttl {
                    entry.config.enabled = false; entry.state = .expired; entry.baselineSequence = nil
                    lifecycle(entry,.expired)
                }
                entries[id] = entry; continue
            }
            if value.schema.version != entry.config.schemaVersion || (entry.validatedSchema.map { !exactText($0.name,value.schema.name) || $0.version != value.schema.version } ?? false) {
                entry.config.enabled = false; entry.state = .paused; entry.baseline = nil; entry.resetReason = .observationGap
                let changed = WatchEvent(watchID:id,generation:entry.generation,captureRevision:captureRevision,snapshot:snapshot,origin:.diagnosticControl,kind:.schemaChanged,value:nil,difference:.unknown,path:entry.config.path,trigger:entry.config.trigger,schema:entry.validatedSchema,phase:.control,model:entry.config.model,machine:entry.config.machine)
                entry.currentEvent = nil; entries[id] = entry; append(changed); continue
            }
            entry.validatedSchema = value.schema
            entry.resolvedType = resolvedWatchType(value.node)
            if entry.baseline != nil && (entry.sourceModel != context.model.map(ExactString.init) || entry.sourceMachine != context.machine.map(ExactString.init) || entry.sourceOrigin != origin) { entry.baseline = nil; entry.resetReason = .sourceChanged }
            // Watch schema identity is name/version; source locations are metadata.
            let difference = entry.baseline.map { compareNodes($0.node,value.node) } ?? .unknown
            let first = entry.baseline.map { !$0.node.isComplete } ?? true
            let complete = value.node.isComplete
            if !complete { satIncrement(&health.truncated) }
            var kind: WatchEventKind
            if !complete { kind = .unknown } else if first { kind = .baseline } else if difference == .unknown { kind = .unknown } else if entry.sampled { kind = .changeSinceLastSample(difference != .unchanged) } else { kind = difference == .unchanged ? .unchanged : .changed }
            entry.sampled = false
            let sampled: Bool
            if case .changeSinceLastSample = kind { sampled = true } else { sampled = false }
            var emit: Bool
            switch entry.config.trigger {
            case .changed: emit = kind != .unchanged
            case .everyObservation: emit = true
            case let .equals(scalar): emit = value.node.isComplete && value.node.kind == .scalar(scalar); if emit { kind = .valueMatched }
            case .propertyFailure: emit = propertyFailure; if emit { kind = .propertyFailure }
            }
            if propertyFailure { emit = true; kind = .propertyFailure }
            let reason = first && complete ? entry.resetReason : nil
            let projectedCompleteness: Completeness = sampled ? .partial(.sampled) : value.node.completeness
            let eventCompleteness: Completeness = incompleteIDs && projectedCompleteness == .complete ? .partial(.valueLimit) : projectedCompleteness
            let current = WatchEvent(watchID:id,generation:entry.generation,captureRevision:captureRevision,snapshot:snapshot,origin:origin,kind:kind,value:value.node,difference:(first || sampled) ? .unknown : difference,baselineReason:reason,path:entry.config.path,trigger:entry.config.trigger,schema:value.schema,phase:snapshot.sequence == 0 ? .initial : .postTurn,model:eventModel,machine:eventMachine,previousSnapshot:entry.currentEvent?.snapshot,propertyFailure:propertyFailure,failingProperties:failureIDs,completeness:eventCompleteness)
            if eventBytes(current) > maxEventBytes {
                // Legacy projection APIs may reveal schema metadata only after projection.
                // Do not retain an oversized current value or reuse its baseline.
                entry.currentEvent = nil; entry.baseline = nil; entry.baselineSequence = nil
                entry.sourceModel = nil; entry.sourceMachine = nil; entry.sourceOrigin = nil; entry.resetReason = .observationGap
                if emit {
                    satIncrement(&entry.emissions)
                    if propertyFailure || (entry.emissions-1) % entry.config.deliveryInterval == 0 { satIncrement(&health.droppedEvents) }
                    else { satIncrement(&health.deliverySampled) }
                }
                if let ttl = entry.config.ttlObservations,entry.selected >= ttl { entry.config.enabled = false; entry.state = .expired; lifecycle(entry,.expired) }
                entries[id] = entry; continue
            }
            entry.currentEvent = current; entry.baseline = value; entry.sourceModel = eventModel.map(ExactString.init); entry.sourceMachine = eventMachine.map(ExactString.init)
            entry.baselineSequence = snapshot.sequence; entry.sourceOrigin = origin
            if !complete { entry.resetReason = .observationGap }
            if emit {
                satIncrement(&entry.emissions)
                if propertyFailure || (entry.emissions-1) % entry.config.deliveryInterval == 0 { append(current) }
                else { satIncrement(&health.deliverySampled) }
            }
            entries[id] = entry; enforceBaselines(); entry = entries[id]!
            if let ttl = entry.config.ttlObservations,entry.selected >= ttl {
                entry.config.enabled = false
                entry.state = .expired
                lifecycle(entry,.expired)
            }
            entries[id] = entry
        }
        reportDrops()
        if propertyFailure { failureSnapshot = captureSnapshot() }
    }
    private func reportDrops() {
        guard health.droppedEvents > reportedDrops else { return }
        let event = WatchEvent(watchID:0,generation:0,captureRevision:captureRevision,snapshot:lastSnapshot ?? SnapshotID(session:0,revision:0,sequence:0),origin:.diagnosticControl,kind:.eventsDropped(health.droppedEvents-reportedDrops),value:nil,difference:.unknown,completeness:.partial(.observationGap))
        let size = eventBytes(event)
        guard events.count < maxRecords,size <= maxEventBytes,size <= maxBytes,ringBytes <= maxBytes-size else { return }
        events.append((event,size)); ringBytes += size; reportedDrops = health.droppedEvents
    }
    private func append(_ event: WatchEvent) {
        let size = eventBytes(event)
        guard size <= maxEventBytes,size <= maxBytes,maxRecords > 0 else { satIncrement(&health.droppedEvents); return }
        while events.count >= maxRecords || ringBytes > maxBytes-size { let removed = events.removeFirst(); ringBytes -= removed.1; satIncrement(&health.droppedEvents) }
        events.append((event,size)); ringBytes += size
    }
    public func drain(limit: Int) throws -> [WatchEvent] {
        guard !observing,limit > 0,limit <= maxRecords else { throw WatchError.invalidConfig }
        let count = min(limit,events.count), result = events.prefix(count).map { $0.0 }
        for item in events.prefix(count) { ringBytes -= item.1 }; events.removeFirst(count); return result
    }
    private func captureSnapshot() -> WatchSnapshot {
        WatchSnapshot(captureRevision:captureRevision,at:lastSnapshot,events:events.map { $0.0 },retainedBytes:ringBytes,health:health)
    }
    public func snapshot() throws -> WatchSnapshot {
        guard !observing else { throw DebugError.reentrant }
        return captureSnapshot()
    }
    /// Capture a safe-point projection without advancing watch observation or changing its baseline.
    public func snapshotNowPaged(_ id: UInt64,context: WatchObservationContext,project: ([PathSegment],PageRequest,InspectLimits) throws -> InspectResult) throws -> WatchEvent {
        guard !observing else { throw DebugError.reentrant }
        guard let entry = entries[id] else { throw WatchError.unknownWatch }
        try validateConfig(entry.config)
        let limits = projectionLimits(entry,snapshot:context.snapshot,origin:context.origin,schema:entry.validatedSchema ?? context.declaredSchema,model:context.model.map { boundedWatchText($0,limit:1024) },machine:context.machine.map { boundedWatchText($0,limit:1024) },failureIDs:boundedPropertyIDs(context.failingProperties))
        observing = true; defer { observing = false }
        let result = try project(entry.config.path,entry.config.page,limits)
        try validateProjection(result.node,limits:limits)
        guard result.snapshot == context.snapshot,result.path == entry.config.path,result.schema.version == entry.config.schemaVersion,!result.schema.name.isEmpty,result.schema.name.utf8.prefix(4097).count <= 4096,(result.schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.schemaMismatch }
        if let declared = context.declaredSchema, !sameWatchSchema(declared,result.schema) { throw InspectError.schemaMismatch }
        if let known = entry.validatedSchema,(!exactText(known.name,result.schema.name) || known.version != result.schema.version) { throw InspectError.schemaMismatch }
        guard result.node.kind != .redacted else { throw WatchError.redactedPath }
        let completeness: Completeness = incompleteWatchMetadata(context) && result.node.completeness == .complete ? .partial(.valueLimit) : result.node.completeness
        let event = WatchEvent(watchID:id,generation:entry.generation,captureRevision:captureRevision,snapshot:context.snapshot,origin:context.origin,kind:.snapshot,value:result.node,difference:.unknown,path:entry.config.path,trigger:entry.config.trigger,schema:result.schema,phase:.control,model:context.model.map { boundedWatchText($0,limit:1024) },machine:context.machine.map { boundedWatchText($0,limit:1024) },propertyFailure:context.propertyFailure,failingProperties:boundedPropertyIDs(context.failingProperties),completeness:completeness)
        guard eventBytes(event) <= maxEventBytes else { throw WatchError.capacity }
        append(event)
        return event
    }
    public func snapshotNow(_ id: UInt64,context: WatchObservationContext,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws -> WatchEvent {
        guard !observing else { throw DebugError.reentrant }
        guard let entry = entries[id] else { throw WatchError.unknownWatch }
        guard entry.config.page.offset == 0,entry.config.page.limit == min(100,entry.config.limits.maxPageSize) else { throw WatchError.invalidConfig }
        return try snapshotNowPaged(id,context:context) { path,_,limits in try project(path,limits) }
    }
    public func clearRing() throws {
        guard !observing else { throw DebugError.reentrant }
        events.removeAll(); ringBytes = 0
    }
    public func clearFailureSnapshot() throws {
        guard !observing else { throw DebugError.reentrant }
        failureSnapshot = nil
    }
}

public struct WatchStatus {
    public let id: UInt64
    public let generation: UInt64
    public let enabled: Bool
    public let state: WatchState
    public let config: WatchConfig
    public let hasBaseline: Bool
    public let effectiveSequence: UInt64
    public let validatedSchema: DisplaySchema?
    public let resolvedType: String?
}
extension WatchRegistry {
    /// Resolve authorized paths on a current explicit boundary before installing
    /// for an external consumer; redacted nodes never become readable watches.
    public func addValidated(_ config: WatchConfig,snapshot: SnapshotID,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws -> WatchAck {
        guard !observing else { throw DebugError.reentrant }
        guard config.page.offset == 0,config.page.limit == min(100,config.limits.maxPageSize) else { throw WatchError.invalidConfig }
        return try addValidatedPaged(config,snapshot:snapshot) { path,_,limits in try project(path,limits) }
    }
    /// Validate the exact configured page before installing the watch.
    public func addValidatedPaged(_ config: WatchConfig,snapshot: SnapshotID,project: ([PathSegment],PageRequest,InspectLimits) throws -> InspectResult) throws -> WatchAck {
        guard !observing else { throw DebugError.reentrant }
        try validateConfig(config)
        guard entries.count < maxWatches else { throw WatchError.capacity }
        observing = true
        let result: InspectResult
        do { result = try project(config.path,config.page,config.limits); observing = false }
        catch { observing = false; throw error }
        try validateProjection(result.node,limits: config.limits)
        guard result.snapshot == snapshot,result.path == config.path,result.schema.version == config.schemaVersion,!result.schema.name.isEmpty,result.schema.name.utf8.prefix(4097).count <= 4096,(result.schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.schemaMismatch }
        guard result.node.kind != .redacted else { throw WatchError.redactedPath }
        return try addPrepared(config,schema:result.schema,resolvedType:resolvedWatchType(result.node))
    }
    public func updateValidated(_ id: UInt64,config: WatchConfig,snapshot: SnapshotID,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws -> WatchAck {
        guard !observing else { throw DebugError.reentrant }
        guard config.page.offset == 0,config.page.limit == min(100,config.limits.maxPageSize) else { throw WatchError.invalidConfig }
        return try updateValidatedPaged(id,config:config,snapshot:snapshot) { path,_,limits in try project(path,limits) }
    }
    /// Validate before replacing the old watch; failed validation leaves it intact.
    public func updateValidatedPaged(_ id: UInt64,config: WatchConfig,snapshot: SnapshotID,project: ([PathSegment],PageRequest,InspectLimits) throws -> InspectResult) throws -> WatchAck {
        guard !observing else { throw DebugError.reentrant }
        try validateConfig(config)
        guard entries[id] != nil else { throw WatchError.unknownWatch }
        observing = true
        let result: InspectResult
        do { result = try project(config.path,config.page,config.limits); observing = false }
        catch { observing = false; throw error }
        try validateProjection(result.node,limits:config.limits)
        guard result.snapshot == snapshot,result.path == config.path,result.schema.version == config.schemaVersion,!result.schema.name.isEmpty,result.schema.name.utf8.prefix(4097).count <= 4096,(result.schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.schemaMismatch }
        guard result.node.kind != .redacted else { throw WatchError.redactedPath }
        return try updatePrepared(id,config:config,schema:result.schema,resolvedType:resolvedWatchType(result.node))
    }
    public func update(_ id: UInt64,config: WatchConfig) throws -> WatchAck {
        try updatePrepared(id,config:config,schema:nil,resolvedType:nil)
    }
    private func updatePrepared(_ id: UInt64,config: WatchConfig,schema: DisplaySchema?,resolvedType: String?) throws -> WatchAck {
        guard var entry = entries[id] else { throw WatchError.unknownWatch }
        try validateConfig(config)
        guard entry.generation != UInt64.max else { throw WatchError.revisionExhausted }
        try mutate(); entry.generation += 1; entry.config = config; entry.state = config.enabled ? .active : .disabled; entry.selected = 0; entry.emissions = 0; entry.sampled = false; entry.baseline = nil; entry.baselineSequence = nil; entry.currentEvent = nil; entry.validatedSchema = schema; entry.resolvedType = resolvedType; entry.resetReason = .reenabled; entry.effectiveSequence = nextBoundary; entries[id] = entry
        lifecycle(entry,config.enabled ? .enabled : .disabled)
        return WatchAck(watchID: id,generation: entry.generation,captureRevision: captureRevision,effectiveSequence: entry.effectiveSequence,displaySchema:schema,resolvedType:resolvedType)
    }
    public func pause(_ id: UInt64) throws -> WatchAck { try setState(id,enabled:false,kind:.paused) }
    public func resume(_ id: UInt64) throws -> WatchAck { try setEnabled(id,enabled: true) }
    public func current(_ id: UInt64) throws -> InspectResult? { guard let entry = entries[id] else { throw WatchError.unknownWatch }; return entry.baseline }
    /// Latest retained observation event, including inspection failures.
    /// Lifecycle-only ring events do not replace it. `current` exposes the comparison baseline.
    public func latestEvent(_ id: UInt64) throws -> WatchEvent? { guard let entry = entries[id] else { throw WatchError.unknownWatch }; return entry.currentEvent }
    public func list(offset: Int = 0,limit: Int = 100) throws -> [WatchStatus] {
        guard offset >= 0,limit > 0,limit <= 1000 else { throw WatchError.invalidConfig }
        return orderedIDs.dropFirst(offset).prefix(limit).compactMap { id in entries[id].map { WatchStatus(id: id,generation: $0.generation,enabled: $0.config.enabled,state:$0.state,config: $0.config,hasBaseline: $0.baseline?.node.isComplete == true,effectiveSequence:$0.effectiveSequence,validatedSchema:$0.validatedSchema,resolvedType:$0.resolvedType) } }
    }
    func currentEvent(_ id: UInt64) -> WatchEvent? { entries[id]?.currentEvent }
    var watchCount: Int { entries.count }
    func protocolStatuses(limit: Int) -> [WatchStatus] {
        orderedIDs.prefix(limit).compactMap { id in
            entries[id].map { WatchStatus(id:id,generation:$0.generation,enabled:$0.config.enabled,state:$0.state,config:$0.config,hasBaseline:$0.baseline?.node.isComplete == true,effectiveSequence:$0.effectiveSequence,validatedSchema:$0.validatedSchema,resolvedType:$0.resolvedType) }
        }
    }
    func prepareProtocolAdd(_ config: WatchConfig,snapshot: SnapshotID,schema: (() throws -> DisplaySchema)? = nil,project: ([PathSegment],InspectLimits) throws -> InspectResult) throws -> (WatchAck,InspectResult,() throws -> Void) {
        guard !observing else { throw DebugError.reentrant }
        try validateConfig(config)
        guard entries.count < maxWatches else { throw WatchError.capacity }
        observing = true
        let result: InspectResult
        do {
            let declared = try schema?()
            if let declared {
                guard !declared.name.isEmpty,declared.name.utf8.prefix(4097).count <= 4096,(declared.source?.file.utf8.prefix(4097).count ?? 0) <= 4096,declared.version == config.schemaVersion else { throw InspectError.schemaMismatch }
            }
            result = try project(config.path,config.limits)
            if let declared, !sameWatchSchema(declared,result.schema) { throw InspectError.schemaMismatch }
            observing = false
        } catch { observing = false; throw error }
        try validateProjection(result.node,limits:config.limits)
        guard result.snapshot == snapshot,result.path == config.path,result.schema.version == config.schemaVersion,!result.schema.name.isEmpty,result.schema.name.utf8.prefix(4097).count <= 4096,(result.schema.source?.file.utf8.prefix(4097).count ?? 0) <= 4096 else { throw InspectError.schemaMismatch }
        guard result.node.kind != .redacted else { throw WatchError.redactedPath }
        // Check exhaustion after inspection, before forming the staged ack.
        try preflightAdd()
        let revision = captureRevision,id = nextID,sequence = lastSnapshot.map { $0.sequence == UInt64.max ? UInt64.max : $0.sequence+1 } ?? 0
        let ack = WatchAck(watchID:id,generation:1,captureRevision:revision+1,effectiveSequence:sequence,displaySchema:result.schema,resolvedType:resolvedWatchType(result.node))
        return (ack,result,{ [self] in
            guard captureRevision == revision,nextID == id else { throw ProtocolError.staleConfiguration }
            let actual = try addPrepared(config,schema:result.schema,resolvedType:resolvedWatchType(result.node))
            guard actual.watchID == id,actual.effectiveSequence == sequence else { throw ProtocolError.staleConfiguration }
        })
    }
    func prepareProtocolRemove(_ id: UInt64) throws -> (UInt64,UInt64,() throws -> Void) {
        guard !observing else { throw DebugError.reentrant }
        guard let entry = entries[id] else { throw WatchError.unknownWatch }
        guard entry.generation < UInt64.max,captureRevision < UInt64.max else { throw WatchError.revisionExhausted }
        let revision = captureRevision, sequence = lastSnapshot.map { $0.sequence == UInt64.max ? UInt64.max : $0.sequence+1 } ?? 0
        return (entry.generation+1,sequence,{ [self] in
            guard captureRevision == revision else { throw ProtocolError.staleConfiguration }
            try remove(id)
        })
    }
}
