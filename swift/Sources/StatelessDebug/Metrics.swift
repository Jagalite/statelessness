import Statelessness

public enum MeasurementError: Error { case missingEndpoint, differentClockDomain, reversed, clockUnavailable }
public struct LocalInstant: Equatable {
    public let domain: UInt64
    public let nanos: UInt64
    public init(domain: UInt64,nanos: UInt64) { self.domain = domain; self.nanos = nanos }
    public func elapsed(since start: Self) throws -> UInt64 {
        guard domain == start.domain else { throw MeasurementError.differentClockDomain }
        guard nanos >= start.nanos else { throw MeasurementError.reversed }; return nanos-start.nanos
    }
}
/// Clock acquisition is an explicit host capability, absent from basic imports.
public struct DiagnosticClock { public let now: () throws -> LocalInstant; public init(now: @escaping () throws -> LocalInstant) { self.now = now } }
public struct HistogramSchema: Equatable {
    public let id: UInt64
    public let finiteBoundsNS: [UInt64]
    public init(id: UInt64 = 1,finiteBoundsNS: [UInt64] = [1000,10_000,100_000,500_000,1_000_000,2_000_000,5_000_000,10_000_000,20_000_000,50_000_000,100_000_000,200_000_000,500_000_000,1_000_000_000,2_000_000_000,5_000_000_000,10_000_000_000,30_000_000_000,60_000_000_000]) throws {
        guard id > 0,finiteBoundsNS.count <= 64,zip(finiteBoundsNS,finiteBoundsNS.dropFirst()).allSatisfy({ $0 < $1 }) else { throw MetricError.invalidSchema }
        self.id = id; self.finiteBoundsNS = finiteBoundsNS
    }
}
public enum MetricError: Error { case unknownWindow
    case wrongKind, invalidSchema, overflow, invalidQuantile, incompatibleWindow, capacity, unknownLabel, unknownFamily }
public struct Quantile: Equatable {
    /// nil upper bound is a present overflow-bucket estimate.
    public let upperBoundNS: UInt64?
    public let observations: UInt64
    public let lowSampleWarning: Bool
}
public struct Histogram: Equatable {
    public private(set) var buckets: [UInt64] = Array(repeating: 0,count: 65)
    public private(set) var count: UInt64 = 0
    public private(set) var sumNS = TraceUInt128(high: 0,low: 0)
    public init() {}
    public mutating func add(_ nanos: UInt64,schema: HistogramSchema) throws {
        let index = schema.finiteBoundsNS.firstIndex(where: { $0 >= nanos }) ?? schema.finiteBoundsNS.count
        let (count,overflow1) = self.count.addingReportingOverflow(1), (bucket,overflow2) = buckets[index].addingReportingOverflow(1)
        let (low,carry) = sumNS.low.addingReportingOverflow(nanos), (high,overflow3) = sumNS.high.addingReportingOverflow(carry ? 1 : 0)
        guard !overflow1,!overflow2,!overflow3 else { throw MetricError.overflow }
        self.count = count; buckets[index] = bucket; sumNS = TraceUInt128(high: high,low: low)
    }
    public func quantile(schema: HistogramSchema,numerator: UInt64,denominator: UInt64) -> Quantile? {
        guard count > 0,denominator > 0,numerator <= denominator else { return nil }
        let product = count.multipliedFullWidth(by: numerator)
        let division = denominator.dividingFullWidth(product)
        let rank = max(1,division.quotient + (division.remainder == 0 ? 0 : 1))
        var total: UInt64 = 0
        for i in 0...schema.finiteBoundsNS.count {
            let (next,overflow) = total.addingReportingOverflow(buckets[i]); if overflow { return nil }; total = next
            if total >= rank { return Quantile(upperBoundNS: i < schema.finiteBoundsNS.count ? schema.finiteBoundsNS[i] : nil,observations: count,lowSampleWarning: count < 100) }
        }
        return nil
    }
    public func subtracting(_ start: Histogram) throws -> Histogram {
        guard count >= start.count else { throw MetricError.incompatibleWindow }
        var value = Histogram(); value.count = count-start.count
        for i in buckets.indices { guard buckets[i] >= start.buckets[i] else { throw MetricError.incompatibleWindow }; value.buckets[i] = buckets[i]-start.buckets[i] }
        let (low,borrow) = sumNS.low.subtractingReportingOverflow(start.sumNS.low)
        let (h1,o1) = sumNS.high.subtractingReportingOverflow(start.sumNS.high), (high,o2) = h1.subtractingReportingOverflow(borrow ? 1 : 0)
        guard !o1,!o2 else { throw MetricError.incompatibleWindow }; value.sumNS = TraceUInt128(high: high,low: low); return value
    }
}
public enum MeasurementOrigin: String, Hashable { case live, debugControlledLive, testClock, replayHarness, importedLive
    public var ingestsLive: Bool { self != .replayHarness && self != .importedLive }
}
public enum MetricOutcome: String, Hashable { case none,accepted,rejected,success,failure,cancelled,abandoned,ignored }
public struct MetricLabels: Hashable {
    public var operation: UInt16 = 0
    public var component: UInt16 = 0
    public var workerPool: UInt16 = 0
    public var outcome: MetricOutcome = .none
    public var origin: MeasurementOrigin = .live
    public var debuggerAffected: Bool = false
    public init() {}
}
public enum MetricKind: Equatable { case counter,gauge,histogram }
public enum MetricFamily: String, CaseIterable, Hashable, Sendable {
    case requests,admissions,attempts,attemptOutcomes,resolutions,deliveries,unresolved,running,readyDepth,completionDepth,queueWait,attemptElapsed,resolution,deliveryLag,endToEnd,completionProcessing,admissionDelay,resolutionOverhead,completionPreparation,completionQueueWait,endToEndObserved,settlement,scheduledBackoff,eligibilityWait,interAttemptGap,duplicateObservations,duplicateDeliveries,cancellationRequests,cancellationAcknowledgements,abandoned,dropped,invalidMeasurements
    public var kind: MetricKind {
        switch self { case .unresolved,.running,.readyDepth,.completionDepth: return .gauge
        case .queueWait,.attemptElapsed,.resolution,.deliveryLag,.endToEnd,.completionProcessing,.admissionDelay,.resolutionOverhead,.completionPreparation,.completionQueueWait,.endToEndObserved,.settlement,.scheduledBackoff,.eligibilityWait,.interAttemptGap: return .histogram
        default: return .counter }
    }
}
public enum MetricValue: Equatable { case counter(UInt64),gauge(UInt64),histogram(Histogram) }
public struct SeriesKey: Hashable {
    public let family: MetricFamily
    public let labels: MetricLabels
    public init(_ family: MetricFamily,labels: MetricLabels = MetricLabels()) { self.family = family; self.labels = labels }
}
public struct MetricSnapshot {
    public let limits: MetricLimits
    public let epoch: UInt64
    public let revision: UInt64
    public let timestamp: LocalInstant?
    public let start: LocalInstant?
    public let labels: LabelCatalog
    public let gaugeScope: GaugeScope
    public let gaugeScopes: [SeriesKey:GaugeScope]
    public let health: MetricHealth
    public let complete: Bool
    public let temporality: Temporality
    public let schema: HistogramSchema
    public let series: [SeriesKey:MetricValue]
    public let completeParticipation: Bool
    public func window(since start: MetricSnapshot) throws -> [SeriesKey:MetricValue] {
        guard epoch == start.epoch,schema == start.schema,labels == start.labels,self.start == start.start,gaugeScope == start.gaugeScope,start.gaugeScopes.allSatisfy({ gaugeScopes[$0.key] == $0.value }),temporality == .cumulative,start.temporality == .cumulative,revision >= start.revision else { throw MetricError.incompatibleWindow }
        if let timestamp,let previous = start.timestamp { _ = try timestamp.elapsed(since: previous) }
        guard Set(start.series.keys).isSubset(of: Set(series.keys)) else { throw MetricError.incompatibleWindow }
        var result: [SeriesKey:MetricValue] = [:]
        for (key,end) in series {
            guard start.series[key] != nil else { result[key] = end; continue }
            switch (start.series[key],end) {
            case let (.counter(a)?,.counter(b)): guard b >= a else { throw MetricError.incompatibleWindow }; result[key] = .counter(b-a)
            case let (.histogram(a)?,.histogram(b)): result[key] = try .histogram(b.subtracting(a))
            case let (.gauge(_)?,.gauge(b)): result[key] = .gauge(b)
            default: throw MetricError.incompatibleWindow
            }
        }
        return result
    }
}
/// Bounded cumulative accounting. Byte limits bound retained representation,
/// not process RSS. Hosts serialize access; no clock or recorder is installed.
private final class MetricSnapshotGeneration {}
public final class MetricRegistry {
    public let schema: HistogramSchema
    public let epoch: UInt64
    public let maxSeries: Int
    public let labels: LabelCatalog
    public let limits: MetricLimits
    private let start: LocalInstant?
    private var endpoint: LocalInstant?
    public private(set) var gaugeScope: GaugeScope
    private var defaultGaugeScope: GaugeScope
    private var gaugeScopes: [SeriesKey:GaugeScope] = [:]
    public private(set) var health = MetricHealth()
    private var complete = true
    private var lastValidEnd: LocalInstant?
    private var history: [MetricSnapshot] = []
    // A reference identity cannot saturate like the public revision/counters.
    // It detects an intervening or reentrant snapshot commit even when retained
    // history revisions and all saturating counters remain unchanged.
    private var snapshotGeneration = MetricSnapshotGeneration()
    public private(set) var revision: UInt64 = 0
    public var importedIgnored: UInt64 { health.importedIgnored }
    private var series: [SeriesKey:MetricValue] = [:]
    #if DEBUG
    // Reach saturation boundaries in tests without executing 2^64 operations.
    func seedSnapshotSaturationForTesting() {
        revision = .max
        health.snapshotsEvicted = .max
    }
    #endif
    public init(epoch: UInt64,schema: HistogramSchema,maxSeries: Int = 2048,labels: LabelCatalog? = nil,limits: MetricLimits? = nil,start: LocalInstant? = nil,gaugeScope: GaugeScope = .unknown) throws {
        var bounds = limits ?? MetricLimits(); if limits == nil { bounds.maxSeries = maxSeries }
        guard epoch > 0,schema.id > 0,bounds.maxSeries > 0,bounds.maxFamilies > 0,bounds.maxFamilies <= 128,bounds.maxBytes >= 0,bounds.maxSnapshots > 0,bounds.maxSnapshotBytes >= 0 else { throw MetricError.capacity }
        let catalog = try labels ?? LabelCatalog()
        guard catalog.retainedBytes+schema.finiteBoundsNS.count*8+1024 <= bounds.maxBytes else { throw MetricError.capacity }
        self.epoch = epoch; self.schema = schema; self.maxSeries = bounds.maxSeries; self.labels = catalog; self.limits = bounds; self.start = start; endpoint = start; lastValidEnd = start; self.gaugeScope = gaugeScope; defaultGaugeScope = gaugeScope
    }
    private func tick() { if revision < UInt64.max { revision += 1 } }
    private func refreshScope() {
        let scopes = Set(gaugeScopes.values); gaugeScope = scopes.count == 1 ? scopes.first! : scopes.isEmpty ? defaultGaugeScope : .unknown
    }
    private func value(_ family: MetricFamily,labels: MetricLabels) throws -> SeriesKey? {
        guard labels.origin.ingestsLive else { health.bump(\.importedIgnored); return nil }
        guard self.labels.allows(labels) else { health.bump(\.invalidLabels); complete = false; throw MetricError.unknownLabel }
        let key = SeriesKey(family,labels: labels)
        if series[key] == nil {
            let families = Set(series.keys.map { $0.family }),extra = family.kind == .histogram ? 768 : 256
            guard series.count < maxSeries,(families.contains(family) || families.count < limits.maxFamilies),extra <= limits.maxBytes-current().estimatedBytes else { health.bump(\.rejectedSeries); complete = false; throw MetricError.capacity }
            switch family.kind { case .counter: series[key] = .counter(0); case .gauge: series[key] = .gauge(0); gaugeScopes[key] = defaultGaugeScope; refreshScope(); case .histogram: series[key] = .histogram(Histogram()) }
        }
        tick(); endpoint = nil; return key
    }
    /// Compatibility dispatcher: counters add, gauges set, histograms observe.
    public func observe(_ family: MetricFamily,quantity: UInt64,labels: MetricLabels = MetricLabels()) throws {
        guard let key = try value(family,labels: labels) else { return }
        do {
            switch series[key]! {
            case let .counter(old): let (n,overflow) = old.addingReportingOverflow(quantity); guard !overflow else { throw MetricError.overflow }; series[key] = .counter(n)
            case .gauge: series[key] = .gauge(quantity)
            case let .histogram(value): var h = value; try h.add(quantity,schema: schema); series[key] = .histogram(h)
            }
        } catch { noteArithmeticFailure(); throw error }
    }
    public func increment(_ family: MetricFamily,by quantity: UInt64 = 1,labels: MetricLabels = MetricLabels()) throws {
        guard family.kind == .counter else { noteArithmeticFailure(); throw MetricError.wrongKind }; try observe(family,quantity: quantity,labels: labels)
    }
    public func setGauge(_ family: MetricFamily,value: UInt64,labels: MetricLabels = MetricLabels()) throws {
        guard family.kind == .gauge else { noteArithmeticFailure(); throw MetricError.wrongKind }; try observe(family,quantity: value,labels: labels)
    }
    public func gaugeDelta(_ family: MetricFamily,delta: Int64,labels: MetricLabels = MetricLabels()) throws {
        guard family.kind == .gauge else { noteArithmeticFailure(); throw MetricError.wrongKind }
        guard let key = try value(family,labels: labels),case let .gauge(old)? = series[key] else { return }
        let pair = delta >= 0 ? old.addingReportingOverflow(UInt64(delta)) : old.subtractingReportingOverflow(delta.magnitude)
        let (n,overflow) = pair
        guard !overflow else { noteArithmeticFailure(family: family,labels: labels); throw MetricError.overflow }; series[key] = .gauge(n)
    }
    public func current(completeParticipation: Bool = false) -> MetricSnapshot {
        MetricSnapshot(limits: limits,epoch: epoch,revision: revision,timestamp: endpoint,start: start,labels: labels,gaugeScope: gaugeScope,gaugeScopes: gaugeScopes,health: health,complete: complete,temporality: .cumulative,schema: schema,series: series,completeParticipation: completeParticipation)
    }
    /// Stage endpoint validation and history eviction before external validation.
    /// A rejected transport/query leaves all acknowledged snapshots untouched.
    public func trySnapshot(at timestamp: LocalInstant?,completeParticipation: Bool = false,validate: (MetricSnapshot) throws -> Void) throws -> MetricSnapshot {
        let (staged,commit) = try prepareSnapshot(at:timestamp,completeParticipation:completeParticipation)
        try validate(staged)
        try commit()
        return staged
    }
    func prepareSnapshot(at timestamp: LocalInstant?,completeParticipation: Bool = false) throws -> (MetricSnapshot,() throws -> Void) {
        let originalRevision = revision
        let originalSeries = series, originalHealth = health, originalComplete = complete
        let originalEndpoint = endpoint, originalLastValidEnd = lastValidEnd
        let originalGaugeScope = gaugeScope, originalGaugeScopes = gaugeScopes
        let originalHistory = history.map(\.revision), originalGeneration = snapshotGeneration
        var nextHealth = health,last = lastValidEnd,end = timestamp,nextComplete = complete
        let nextRevision = revision == UInt64.max ? revision : revision+1
        if let timestamp {
            if [start,lastValidEnd].contains(where: { if let at = $0 { return at.domain != timestamp.domain || at.nanos > timestamp.nanos }; return false }) { end = nil; nextHealth.bump(\.invalidMeasurements); nextComplete = false }
            else { last = timestamp }
        }
        let size = current().estimatedBytes
        let retain = size <= limits.maxSnapshotBytes
        var remove = 0,retainedBytes = history.reduce(0) { $0+$1.estimatedBytes }
        if retain {
            while remove < history.count && (history.count-remove >= limits.maxSnapshots || size > limits.maxSnapshotBytes-retainedBytes) { retainedBytes -= history[remove].estimatedBytes; remove += 1; nextHealth.bump(\.snapshotsEvicted) }
        } else { nextHealth.bump(\.snapshotRejections) }
        let staged = MetricSnapshot(limits: limits,epoch: epoch,revision: nextRevision,timestamp: end,start: start,labels: labels,gaugeScope: gaugeScope,gaugeScopes: gaugeScopes,health: nextHealth,complete: nextComplete,temporality: .cumulative,schema: schema,series: series,completeParticipation: completeParticipation)
        return (staged,{ [self] in
            guard snapshotGeneration === originalGeneration,revision == originalRevision,series == originalSeries,health == originalHealth,complete == originalComplete,endpoint == originalEndpoint,lastValidEnd == originalLastValidEnd,gaugeScope == originalGaugeScope,gaugeScopes == originalGaugeScopes,history.map(\.revision) == originalHistory else { throw MetricError.incompatibleWindow }
            revision = nextRevision; endpoint = end; lastValidEnd = last; health = nextHealth; complete = nextComplete
            if retain { if remove > 0 { history.removeFirst(remove) }; history.append(staged) }
            snapshotGeneration = MetricSnapshotGeneration()
        })
    }
    public func snapshot(at timestamp: LocalInstant?,completeParticipation: Bool = false) -> MetricSnapshot {
        // The no-op validator cannot throw.
        return try! trySnapshot(at: timestamp,completeParticipation: completeParticipation,validate: { _ in })
    }
    public func retainSnapshot(at timestamp: LocalInstant?,completeParticipation: Bool = false) throws -> MetricSnapshot { snapshot(at: timestamp,completeParticipation: completeParticipation) }
    public var availableWindows: [UInt64] { history.map { $0.revision } }
    public func window(from: UInt64,to: UInt64) throws -> MetricSnapshot {
        guard let start = history.first(where: { $0.revision == from }),let end = history.first(where: { $0.revision == to }) else { throw MetricError.unknownWindow }; return try end.delta(since: start)
    }
    public func markIncomplete() { complete = false }
    public func noteOmittedMeasurement() { health.bump(\.omittedMeasurements); complete = false }
    public func noteInvalidMeasurement() { health.bump(\.invalidMeasurements); complete = false }
    public func noteArithmeticFailure(family: MetricFamily? = nil,labels: MetricLabels = MetricLabels()) {
        health.bump(\.arithmeticErrors); complete = false
        if let family,family.kind == .gauge,series[SeriesKey(family,labels: labels)] != nil { gaugeScopes[SeriesKey(family,labels: labels)] = .unknown; refreshScope() }
    }
    public func setGaugeScope(_ scope: GaugeScope,labels selected: MetricLabels? = nil) {
        for key in gaugeScopes.keys where selected == nil || key.labels == selected { gaugeScopes[key] = scope }
        if selected == nil { defaultGaugeScope = scope }; refreshScope()
    }
}

public struct MetricDescriptor {
    public let family: MetricFamily
    public let name: String
    public let kind: MetricKind
    public let unit: String
    public let population: String
    public let allowedLabels = ["operation","component","worker_pool","outcome","measurement_origin","debugger_affected"]
    public let reset = "cumulative per process epoch; never sum repeated snapshots"
}
extension MetricFamily {
    public var descriptor: MetricDescriptor {
        let name: String,population: String
        switch self {
        case .requests: name = "effect_requests_total"; population = "observed host output requests"
        case .admissions: name = "effect_admissions_total"; population = "host admission decisions"
        case .attempts: name = "effect_attempts_total"; population = "actual first execution starts"
        case .attemptOutcomes: name = "effect_attempt_outcomes_total"; population = "known attempt terminations"
        case .resolutions: name = "effect_resolutions_total"; population = "logical effect resolutions"
        case .deliveries: name = "effect_deliveries_total"; population = "observed completion inputs including ignored/rejected/duplicates"
        case .unresolved: name = "effects_unresolved"; population = "admitted unresolved logical effects"
        case .running: name = "effect_attempts_running"; population = "started attempts without known termination"
        case .readyDepth: name = "effect_ready_queue_depth"; population = "synchronized ready entries"
        case .completionDepth: name = "completion_queue_depth"; population = "reserved visible completion entries"
        case .queueWait: name = "effect_queue_wait_seconds"; population = "ready to actual start per attempt"
        case .attemptElapsed: name = "effect_attempt_elapsed_seconds"; population = "known terminated attempts, including async waits"
        case .resolution: name = "effect_resolution_seconds"; population = "request to logical resolution"
        case .deliveryLag: name = "effect_delivery_lag_seconds"; population = "resolution to each completion delivery start"
        case .endToEnd: name = "effect_end_to_end_seconds"; population = "request to first completion delivery start"
        case .completionProcessing: name = "completion_processing_seconds"; population = "delivery begin to immediately after reducer, excluding checks/recording"
        case .admissionDelay: name = "effect_admission_delay_seconds"; population = "request to first ready queue boundary"
        case .resolutionOverhead: name = "effect_resolution_overhead_seconds"; population = "selected finished attempt to logical resolution"
        case .completionPreparation: name = "completion_preparation_seconds"; population = "resolution to completion publication reservation"
        case .completionQueueWait: name = "completion_queue_wait_seconds"; population = "publication reservation to delivery begin"
        case .endToEndObserved: name = "effect_end_to_end_observed_seconds"; population = "request to first observed completion"
        case .settlement: name = "effect_settlement_seconds"; population = "request to explicit application acknowledgement"
        case .scheduledBackoff: name = "effect_scheduled_backoff_seconds"; population = "host-declared scheduled retry delay"
        case .eligibilityWait: name = "effect_eligibility_wait_seconds"; population = "retry schedule observation to ready eligibility"
        case .interAttemptGap: name = "effect_inter_attempt_gap_seconds"; population = "previous attempt finish to next actual start; not just backoff"
        case .duplicateObservations: name = "effect_duplicate_observations_total"; population = "repeated notification for the same token boundary"
        case .duplicateDeliveries: name = "effect_duplicate_deliveries_total"; population = "additional observed delivery identities for one effect"
        case .cancellationRequests: name = "effect_cancellation_requests_total"; population = "host cancellation requests; no running-gauge decrement"
        case .cancellationAcknowledgements: name = "effect_cancellation_acknowledgements_total"; population = "confirmed logical cancellation, distinct from attempt termination"
        case .abandoned: name = "effect_abandoned_total"; population = "local observation/future abandonment; remote termination unknown"
        case .dropped: name = "telemetry_dropped_total"; population = "diagnostic loss; aggregate health remains independent"
        case .invalidMeasurements: name = "measurement_invalid_total"; population = "unknown/reversed/cross-domain measurements"
        }
        return MetricDescriptor(family: self,name: name,kind: kind,unit: kind == .histogram ? "seconds" : "count",population: population)
    }
}
public func metricCatalog() -> [MetricDescriptor] { MetricFamily.allCases.map { $0.descriptor } }
public struct LabelCatalog: Equatable {
    public let operations: [String]
    public let components: [String]
    public let workerPools: [String]
    public init(operations: [String] = ["unspecified"],components: [String] = ["host"],workerPools: [String] = ["default"],maxLabels: Int = 256,maxLabelBytes: Int = 64) throws {
        guard maxLabels > 0,maxLabels <= 256,maxLabelBytes > 0,maxLabelBytes <= 64 else { throw MetricError.unknownLabel }
        for group in [operations,components,workerPools] {
            guard !group.isEmpty,group.count <= maxLabels,group.count <= Int(UInt16.max)+1,Set(group.map(ExactString.init)).count == group.count,group.allSatisfy({ !$0.isEmpty && $0.utf8.count <= maxLabelBytes }) else { throw MetricError.unknownLabel }
        }
        self.operations = operations; self.components = components; self.workerPools = workerPools
    }
    public var retainedBytes: Int { [operations,components,workerPools].reduce(0) { total,group in group.reduce(total) { $0+24+$1.utf8.count } } }
    public static func == (a: Self,b: Self) -> Bool { a.operations.map(ExactString.init) == b.operations.map(ExactString.init) && a.components.map(ExactString.init) == b.components.map(ExactString.init) && a.workerPools.map(ExactString.init) == b.workerPools.map(ExactString.init) }
    public func allows(_ labels: MetricLabels) -> Bool { Int(labels.operation) < operations.count && Int(labels.component) < components.count && Int(labels.workerPool) < workerPools.count }
}

public enum GaugeScope: Hashable { case completeParticipation,hostSnapshot,observedSinceAttach,unknown }
public enum Temporality: Equatable { case cumulative,delta }
public struct MetricHealth: Equatable {
    public fileprivate(set) var rejectedSeries: UInt64 = 0
    public fileprivate(set) var invalidLabels: UInt64 = 0
    public fileprivate(set) var arithmeticErrors: UInt64 = 0
    public fileprivate(set) var omittedMeasurements: UInt64 = 0
    public fileprivate(set) var invalidMeasurements: UInt64 = 0
    public fileprivate(set) var importedIgnored: UInt64 = 0
    public fileprivate(set) var snapshotsEvicted: UInt64 = 0
    public fileprivate(set) var snapshotRejections: UInt64 = 0
    public var isComplete: Bool { rejectedSeries == 0 && invalidLabels == 0 && arithmeticErrors == 0 && omittedMeasurements == 0 && invalidMeasurements == 0 && importedIgnored == 0 && snapshotsEvicted == 0 && snapshotRejections == 0 }
}
public struct MetricLimits: Equatable {
    public var maxFamilies = 128
    public var maxSeries = 2048
    public var maxBytes = 8*1024*1024
    public var maxSnapshots = 12
    public var maxSnapshotBytes = 16*1024*1024
    public init() {}
}
extension MetricValue {
    fileprivate var isHistogram: Bool { if case .histogram = self { return true }; return false }
}
extension MetricSnapshot {
    public var estimatedBytes: Int { series.reduce(1024+labels.retainedBytes+schema.finiteBoundsNS.count*8) { $0 + ($1.value.isHistogram ? 768 : 256) } }
    public func delta(since start: MetricSnapshot) throws -> MetricSnapshot {
        let values = try window(since: start)
        return MetricSnapshot(limits: limits,epoch: epoch,revision: revision,timestamp: timestamp,start: start.timestamp,labels: labels,gaugeScope: gaugeScope,gaugeScopes: gaugeScopes,health: health,complete: complete && start.complete,temporality: .delta,schema: schema,series: values,completeParticipation: completeParticipation && start.completeParticipation)
    }
}

extension MetricHealth {
    fileprivate mutating func bump(_ key: WritableKeyPath<MetricHealth,UInt64>) { if self[keyPath: key] < UInt64.max { self[keyPath: key] += 1 } }
}

extension Histogram {
    /// Transactional addition for caller-established disjoint populations.
    public func adding(_ other: Histogram) throws -> Histogram {
        var result = self
        let (count,oc) = self.count.addingReportingOverflow(other.count)
        let (low,carry) = sumNS.low.addingReportingOverflow(other.sumNS.low)
        let (h1,o1) = sumNS.high.addingReportingOverflow(other.sumNS.high)
        let (high,o2) = h1.addingReportingOverflow(carry ? 1 : 0)
        guard !oc,!o1,!o2 else { throw MetricError.overflow }
        for i in buckets.indices { let (n,overflow) = buckets[i].addingReportingOverflow(other.buckets[i]); guard !overflow else { throw MetricError.overflow }; result.buckets[i] = n }
        result.count = count; result.sumNS = TraceUInt128(high: high,low: low); return result
    }
}
extension MetricSnapshot {
    public var end: LocalInstant? { timestamp }
    public func get(_ family: MetricFamily,labels: MetricLabels = MetricLabels()) -> MetricValue? { series[SeriesKey(family,labels: labels)] }
    /// Returns the histogram for this series, or nil if absent or another kind.
    public func histogram(_ family: MetricFamily,labels: MetricLabels = MetricLabels()) -> Histogram? {
        guard case let .histogram(value)? = get(family,labels: labels) else { return nil }
        return value
    }
    public func production() -> MetricSnapshot {
        let values = series.filter { $0.key.labels.origin == .live && !$0.key.labels.debuggerAffected }
        let scopes = gaugeScopes.filter { values[$0.key] != nil }
        return MetricSnapshot(limits: limits,epoch: epoch,revision: revision,timestamp: timestamp,start: start,labels: labels,gaugeScope: gaugeScope,gaugeScopes: scopes,health: health,complete: complete,temporality: temporality,schema: schema,series: values,completeParticipation: completeParticipation)
    }
    /// No deduplication: callers establish that producer populations are disjoint.
    public mutating func mergeDisjoint(_ other: MetricSnapshot) throws {
        guard epoch == other.epoch,start == other.start,timestamp == other.timestamp,schema == other.schema,labels == other.labels,temporality == other.temporality,gaugeScope == other.gaugeScope else { throw MetricError.incompatibleWindow }
        var values = series,scopes = gaugeScopes,mergedHealth = health
        for (key,scope) in other.gaugeScopes { if let prior = scopes[key],prior != scope { throw MetricError.incompatibleWindow }; scopes[key] = scope }
        for (key,value) in other.series {
            guard let prior = values[key] else { values[key] = value; continue }
            switch (prior,value) {
            case let (.counter(a),.counter(b)): let (n,overflow) = a.addingReportingOverflow(b); guard !overflow else { throw MetricError.overflow }; values[key] = .counter(n)
            case let (.gauge(a),.gauge(b)): let (n,overflow) = a.addingReportingOverflow(b); guard !overflow else { throw MetricError.overflow }; values[key] = .gauge(n)
            case let (.histogram(a),.histogram(b)): values[key] = try .histogram(a.adding(b))
            default: throw MetricError.incompatibleWindow
            }
        }
        for key in metricHealthKeys { let (n,overflow) = health[keyPath: key].addingReportingOverflow(other.health[keyPath: key]); guard !overflow else { throw MetricError.overflow }; mergedHealth[keyPath: key] = n }
        let staged = MetricSnapshot(limits: limits,epoch: epoch,revision: revision,timestamp: timestamp,start: start,labels: labels,gaugeScope: gaugeScope,gaugeScopes: scopes,health: mergedHealth,complete: complete && other.complete,temporality: temporality,schema: schema,series: values,completeParticipation: completeParticipation && other.completeParticipation)
        guard values.count <= limits.maxSeries,Set(values.keys.map { $0.family }).count <= limits.maxFamilies,staged.estimatedBytes <= limits.maxBytes else { throw MetricError.overflow }
        self = staged
    }
}
fileprivate var metricHealthKeys: [WritableKeyPath<MetricHealth,UInt64>] { [\.rejectedSeries,\.invalidLabels,\.arithmeticErrors,\.omittedMeasurements,\.invalidMeasurements,\.importedIgnored,\.snapshotsEvicted,\.snapshotRejections] }

/// Hosts own exporter initialization. Exporting a cumulative endpoint must not
/// replay lifecycle observations into another live accounting population.
public protocol MetricSink {
    mutating func export(_ snapshot: MetricSnapshot) throws
}
