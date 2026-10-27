import Foundation
import Statelessness

public struct EffectID: Hashable { public let run: UInt64; public let epoch: UInt64; public let serial: UInt64
    public init(run: UInt64,epoch: UInt64,serial: UInt64) { self.run = run; self.epoch = epoch; self.serial = serial }
}
public struct AttemptID: Hashable { public let effect: EffectID; public let serial: UInt64
    public init(effect: EffectID,serial: UInt64) { self.effect = effect; self.serial = serial }
}
public struct DeliveryID: Hashable { public let effect: EffectID; public let serial: UInt64
    public init(effect: EffectID,serial: UInt64) { self.effect = effect; self.serial = serial }
}
public struct RequestOrigin: Equatable { public let run: UInt64; public let epoch: UInt64; public let machine: UInt64; public let transitionSequence: UInt64; public let outputIndex: UInt32
    public init(run: UInt64,epoch: UInt64,machine: UInt64,transitionSequence: UInt64,outputIndex: UInt32) { self.run = run; self.epoch = epoch; self.machine = machine; self.transitionSequence = transitionSequence; self.outputIndex = outputIndex }
}
public enum RuntimeOutcome { case unclassified,success,failure,cancelled
    fileprivate var metric: MetricOutcome { switch self { case .unclassified: return .none; case .success: return .success; case .failure: return .failure; case .cancelled: return .cancelled } }
}
public enum DeliveryDisposition { case accepted,rejected,ignored
    fileprivate var metric: MetricOutcome { switch self { case .accepted: return .accepted; case .rejected: return .rejected; case .ignored: return .ignored } }
}
public enum LifecycleError: Error { case foreignToken,wrongEpoch,invalidLabels,wrongPhase,duplicateObservation,limit,exhausted }
public struct EffectOptions {
    public var run: UInt64 = 1
    public var epoch: UInt64 = 1
    public var origin: MeasurementOrigin = .live
    public var counters = true
    public var timings = true
    public var details = true
    public var maxDetails = 4096
    public var maxDetailBytes = 8*1024*1024
    public var maxEventsPerEffect = 128
    public var maxAttemptsPerEffect: UInt64 = 1024
    public var maxDeliveriesPerEffect: UInt64 = 1024
    public var maxPauseIntervals = 64
    public var gaugeScope: GaugeScope = .observedSinceAttach
    public var labels: LabelCatalog?
    public var metricLimits: MetricLimits?
    public init() {}
}
public enum EffectLifecycleKind {
    case requested,admission(Bool),attemptCreated(AttemptID),ready(AttemptID),readyRemoved(AttemptID)
    case retryScheduled(AttemptID,delayNS: UInt64),attemptStarted(AttemptID),attemptFinished(AttemptID,RuntimeOutcome)
    case resolved(RuntimeOutcome),cancellationRequested,cancellationAcknowledged
    case publicationReserved(DeliveryID),publicationFailed(DeliveryID),deliveryBegun(DeliveryID),deliveryObserved(DeliveryID,DeliveryDisposition)
    case settled,abandoned(AttemptID?)
    fileprivate var name: String { switch self {
    case .requested: return "requested"; case .admission: return "admission"; case .attemptCreated: return "attempt-created"; case .ready: return "ready"; case .readyRemoved: return "ready-removed"; case .retryScheduled: return "retry-scheduled"; case .attemptStarted: return "attempt-started"; case .attemptFinished: return "attempt-finished"; case .resolved: return "resolved"; case .cancellationRequested: return "cancellation-requested"; case .cancellationAcknowledged: return "cancellation-acknowledged"; case .publicationReserved: return "publication-reserved"; case .publicationFailed: return "publication-failed"; case .deliveryBegun: return "delivery-begun"; case .deliveryObserved: return "delivery-observed"; case .settled: return "settled"; case .abandoned: return "abandoned"
    } }
}
public struct EffectLifecycleEvent { public let sequence: UInt64; public let kind: String; public let detail: EffectLifecycleKind; public let time: LocalInstant?; public let debuggerAffected: Bool }
public struct EffectTimingObservation { public let family: MetricFamily; public let attempt: AttemptID?; public let delivery: DeliveryID?; public let durationNS: Result<UInt64,MeasurementError>; public let outcome: MetricOutcome; public let debuggerAffected: Bool }
public struct EffectDetails {
    public let id: EffectID
    public let origin: RequestOrigin?
    public let labels: MetricLabels
    public let events: [EffectLifecycleEvent]
    public let timings: [EffectTimingObservation]
    public let complete: Bool
    public let resolved: Bool
    public let settled: Bool
    public let requested: LocalInstant?
    public let resolvedAt: LocalInstant?
    public let admission: Bool?
    public let runningAttempts: UInt64
    public let deliveriesObserved: UInt64
    public let cancellationRequested: Bool
    public let cancellationAcknowledged: Bool
    public let debuggerAffected: Bool
    public let abandoned: Bool
    /// Pending age is not a completed latency sample; the caller supplies time.
    public func pendingAge(at instant: LocalInstant) throws -> UInt64? { guard !resolved else { return nil }; guard let requested else { throw MeasurementError.missingEndpoint }; return try instant.elapsed(since: requested) }
    public var estimatedBytes: Int { 512+events.count*192+timings.count*256 }
}
public typealias EffectTimeline = EffectDetails
public struct PauseInterval { public let generation: UInt64; public let start: LocalInstant?; public fileprivate(set) var end: LocalInstant? }
public struct TelemetryHealth {
    public var metric = MetricHealth()
    public var callbackFaults: UInt64 = 0
    public var clockFailures: UInt64 = 0
    public var omittedMeasurements: UInt64 = 0
    public var invalidMeasurements: UInt64 = 0
    public var detailEvictions: UInt64 = 0
    public var detailRejections: UInt64 = 0
    public var detailEventDrops: UInt64 = 0
    public var duplicateObservations: UInt64 = 0
    public var duplicateDeliveries: UInt64 = 0
    public var invalidLifecycle: UInt64 = 0
    public var abandoned: UInt64 = 0
    public var pauseIntervalEvictions: UInt64 = 0
    public var debuggerAffectedMeasurements: UInt64 = 0
    public var activeEffectHandles: UInt64 = 0
    public var destructorHookLosses: UInt64 = 0
    public var retainedDetailRecords: UInt64 = 0
    public var retainedDetailBytes: UInt64 = 0
    public var effectHandleBytes: UInt64 = 256
    public var attemptHandleBytes: UInt64 = 256
    public var deliveryHandleBytes: UInt64 = 192
    public var unobservedNativeDebuggerInterferencePossible = true
    /// Compatibility spelling; bounded detail events drop incoming records.
    public var eventEvictions: UInt64 { detailEventDrops }
    public init() {}
}
fileprivate final class EffectOwner { weak var observer: EffectObserver? }
fileprivate final class EffectToken {
    let owner: EffectOwner; let id: EffectID; let origin: RequestOrigin?; let labels: MetricLabels; let requested: LocalInstant?
    var pauseGeneration: UInt64; var affected: Bool; var admitted: Bool?; var resolved = false; var resolvedAt: LocalInstant?; var settled = false
    var firstReady = false; var firstDeliveryBegun = false; var observed: UInt64 = 0; var running: UInt64 = 0; var lastFinish: LocalInstant?
    var cancellationRequested = false; var cancellationAcknowledged = false; var abandoned = false; var nextAttempt: UInt64 = 0; var nextDelivery: UInt64 = 0
    init(owner: EffectOwner,id: EffectID,origin: RequestOrigin?,labels: MetricLabels,requested: LocalInstant?,generation: UInt64,affected: Bool) { self.owner = owner; self.id = id; self.origin = origin; self.labels = labels; self.requested = requested; pauseGeneration = generation; self.affected = affected }
    deinit { owner.observer?.releasedEffect(self) }
}
fileprivate final class AttemptToken {
    let effect: EffectToken; let id: AttemptID; let previousFinish: LocalInstant?
    var queued = false; var ready: LocalInstant?; var started = false; var start: LocalInstant?; var finished = false; var finish: LocalInstant?
    var hasSchedule = false; var scheduled: LocalInstant?; var abandoned = false
    init(effect: EffectToken,id: AttemptID) { self.effect = effect; self.id = id; previousFinish = effect.lastFinish }
    deinit { effect.owner.observer?.releasedAttempt(self) }
}
fileprivate final class DeliveryToken {
    let effect: EffectToken; let id: DeliveryID; let posted: LocalInstant?
    var confirmed = false; var failed = false; var begun = false; var begin: LocalInstant?; var observed = false
    init(effect: EffectToken,id: DeliveryID,posted: LocalInstant?) { self.effect = effect; self.id = id; self.posted = posted }
    deinit { effect.owner.observer?.releasedDelivery(self) }
}
public struct EffectHandle { fileprivate let token: EffectToken; public var id: EffectID { token.id } }
public struct AttemptHandle { fileprivate let token: AttemptToken; public var id: AttemptID { token.id } }
public struct DeliveryHandle { fileprivate let token: DeliveryToken; public var id: DeliveryID { token.id } }
private struct RetainedEffect {
    let id: EffectID; let origin: RequestOrigin?; let labels: MetricLabels; let requested: LocalInstant?
    var events: [EffectLifecycleEvent] = []; var timings: [EffectTimingObservation] = []; var complete = true
    var resolved = false; var settled = false; var resolvedAt: LocalInstant?; var admission: Bool?; var running: UInt64 = 0; var observed: UInt64 = 0
    var cancellationRequested = false; var cancellationAcknowledged = false; var debuggerAffected = false; var abandoned = false
    var bytes: Int { 512+events.count*192+timings.count*256 }
    func projection() -> EffectDetails { EffectDetails(id: id,origin: origin,labels: labels,events: events,timings: timings,complete: complete,resolved: resolved,settled: settled,requested: requested,resolvedAt: resolvedAt,admission: admission,runningAttempts: running,deliveriesObserved: observed,cancellationRequested: cancellationRequested,cancellationAcknowledged: cancellationAcknowledged,debuggerAffected: debuggerAffected,abandoned: abandoned) }
}
/// Host lifecycle accounting, never a reducer, effect scheduler or remote endpoint.
/// Mutation hooks and ARC cleanup are synchronized. The type deliberately has no
/// Sendable conformance: a supplied clock and direct metric access remain host-owned.
/// Callbacks are cooperative; locks do not impose callback time/RSS/preemption bounds.
public final class EffectObserver {
    public let options: EffectOptions
    public let metrics: MetricRegistry
    private let clock: DiagnosticClock
    private let owner = EffectOwner()
    private let lock = NSRecursiveLock()
    private var busy = false
    private var storedHealth = TelemetryHealth()
    private var serial: UInt64 = 0; private var eventSequence: UInt64 = 0
    private var records: [EffectID:RetainedEffect] = [:]; private var order: [EffectID] = []; private var detailBytes = 0
    private var paused = false; private var pauseGeneration: UInt64 = 0; private var pauses: [PauseInterval] = []
    public init(options: EffectOptions = EffectOptions(),clock: DiagnosticClock,schema: HistogramSchema) throws {
        guard options.run > 0,options.epoch > 0,options.maxDetails >= 0,options.maxDetailBytes >= 0,options.maxPauseIntervals > 0,options.maxEventsPerEffect > 0,options.maxAttemptsPerEffect > 0,options.maxDeliveriesPerEffect > 0 else { throw LifecycleError.limit }
        self.options = options; self.clock = clock
        let start: LocalInstant?; var failed = false
        if options.timings { do { start = try clock.now() } catch { start = nil; failed = true } } else { start = nil }
        metrics = try MetricRegistry(epoch: options.epoch,schema: schema,labels: options.labels,limits: options.metricLimits,start: start,gaugeScope: options.gaugeScope)
        if failed { storedHealth.clockFailures = 1 }
        owner.observer = self
    }
    private func increment(_ value: inout UInt64) { if value < UInt64.max { value += 1 } }
    private func mutate<T>(_ body: () throws -> T) throws -> T { lock.lock(); defer { lock.unlock() }; guard !busy else { throw DebugError.reentrant }; busy = true; defer { busy = false }; return try body() }
    private func read<T>(_ body: () -> T) -> T { lock.lock(); defer { lock.unlock() }; return body() }
    private func now() -> LocalInstant? { guard options.timings else { return nil }; do { return try clock.now() } catch { increment(&storedHealth.clockFailures); return nil } }
    private func invalid(_ error: LifecycleError = .wrongPhase) -> LifecycleError { increment(&storedHealth.invalidLifecycle); return error }
    private func duplicate(_ effect: EffectToken) -> LifecycleError { increment(&storedHealth.duplicateObservations); count(.duplicateObservations,effect.labels); return .duplicateObservation }
    private func validate(_ effect: EffectToken) throws { guard effect.owner === owner else { throw LifecycleError.foreignToken }; effect.affected = effect.affected || paused || effect.pauseGeneration != pauseGeneration }
    private func labels(_ effect: EffectToken,outcome: MetricOutcome = .none,measurement: Bool = false) -> MetricLabels { var value = effect.labels; value.outcome = outcome; value.debuggerAffected = measurement && effect.affected; return value }
    private func count(_ family: MetricFamily,_ labels: MetricLabels) { guard options.counters else { return }; do { try metrics.increment(family,labels: labels) } catch { metrics.markIncomplete() } }
    private func gauge(_ family: MetricFamily,_ delta: Int64,_ labels: MetricLabels) { guard options.counters else { return }; do { try metrics.gaugeDelta(family,delta: delta,labels: labels) } catch { metrics.markIncomplete() } }
    private func event(_ kind: EffectLifecycleKind,_ effect: EffectToken,_ time: LocalInstant?) {
        guard options.details else { return }; increment(&eventSequence)
        guard var value = records[effect.id] else { return }
        value.admission = effect.admitted; value.resolved = effect.resolved; value.resolvedAt = effect.resolvedAt; value.settled = effect.settled; value.running = effect.running; value.observed = effect.observed
        value.cancellationRequested = effect.cancellationRequested; value.cancellationAcknowledged = effect.cancellationAcknowledged; value.debuggerAffected = effect.affected; value.abandoned = effect.abandoned; value.complete = value.complete && !effect.abandoned
        if value.events.count >= options.maxEventsPerEffect || 192 > options.maxDetailBytes-detailBytes { value.complete = false; increment(&storedHealth.detailEventDrops) }
        else { value.events.append(EffectLifecycleEvent(sequence: eventSequence,kind: kind.name,detail: kind,time: time,debuggerAffected: effect.affected)); detailBytes += 192 }
        records[effect.id] = value
    }
    private func measurement(_ family: MetricFamily,_ effect: EffectToken,_ start: LocalInstant?,_ end: LocalInstant?,attempt: AttemptID? = nil,delivery: DeliveryID? = nil,outcome: MetricOutcome = .none) {
        guard options.timings else { return }
        let duration: Result<UInt64,MeasurementError>
        if let start,let end { do { duration = .success(try end.elapsed(since: start)) } catch let error as MeasurementError { duration = .failure(error) } catch { duration = .failure(.clockUnavailable) } } else { duration = .failure(.missingEndpoint) }
        let dimensions = labels(effect,outcome: outcome,measurement: true)
        switch duration {
        case let .success(nanos): do { try metrics.observe(family,quantity: nanos,labels: dimensions) } catch { metrics.markIncomplete() }; if effect.affected { increment(&storedHealth.debuggerAffectedMeasurements) }
        case .failure(.missingEndpoint): increment(&storedHealth.omittedMeasurements); metrics.noteOmittedMeasurement()
        case .failure: increment(&storedHealth.invalidMeasurements); metrics.noteInvalidMeasurement(); count(.invalidMeasurements,dimensions)
        }
        guard options.details,var value = records[effect.id] else { return }
        if value.timings.count >= options.maxEventsPerEffect || 256 > options.maxDetailBytes-detailBytes { value.complete = false; increment(&storedHealth.detailEventDrops) }
        else { value.timings.append(EffectTimingObservation(family: family,attempt: attempt,delivery: delivery,durationNS: duration,outcome: outcome,debuggerAffected: effect.affected)); detailBytes += 256 }
        records[effect.id] = value
    }
    public func requested(origin: RequestOrigin? = nil,labels: MetricLabels = MetricLabels()) throws -> EffectHandle { try mutate {
        if let origin { guard origin.run == options.run,origin.epoch == options.epoch else { throw invalid(.wrongEpoch) }; guard origin.transitionSequence > 0 else { throw invalid() } }
        var dimensions = labels; dimensions.origin = options.origin; dimensions.outcome = .none; dimensions.debuggerAffected = false
        guard metrics.labels.allows(dimensions) else { throw invalid(.invalidLabels) }; guard serial < UInt64.max else { throw invalid(.exhausted) }; serial += 1
        let id = EffectID(run: options.run,epoch: options.epoch,serial: serial),time = now()
        let effect = EffectToken(owner: owner,id: id,origin: origin,labels: dimensions,requested: time,generation: pauseGeneration,affected: paused); increment(&storedHealth.activeEffectHandles)
        if options.details {
            if options.maxDetails == 0 || options.maxDetailBytes < 512 { increment(&storedHealth.detailRejections) }
            else { while !order.isEmpty && (records.count >= options.maxDetails || 512 > options.maxDetailBytes-detailBytes) { let first = order.removeFirst(); if let removed = records.removeValue(forKey: first) { detailBytes -= removed.bytes; increment(&storedHealth.detailEvictions) } }; records[id] = RetainedEffect(id: id,origin: origin,labels: dimensions,requested: time); order.append(id); detailBytes += 512 }
        }
        count(.requests,dimensions); event(.requested,effect,time); return EffectHandle(token: effect)
    } }
    public func admission(_ handle: EffectHandle,accepted: Bool) throws { try mutate { let effect = handle.token; try validate(effect); guard effect.admitted == nil else { throw duplicate(effect) }; guard !effect.resolved else { throw invalid() }; effect.admitted = accepted; let time = now(); count(.admissions,labels(effect,outcome: accepted ? .accepted : .rejected)); if accepted { gauge(.unresolved,1,effect.labels) }; event(.admission(accepted),effect,time) } }
    public func attemptCreated(_ handle: EffectHandle) throws -> AttemptHandle { try mutate { let effect = handle.token; try validate(effect); guard effect.admitted == true,!effect.resolved else { throw invalid() }; guard effect.nextAttempt < options.maxAttemptsPerEffect else { throw invalid(.limit) }; effect.nextAttempt += 1; let attempt = AttemptToken(effect: effect,id: AttemptID(effect: effect.id,serial: effect.nextAttempt)); event(.attemptCreated(attempt.id),effect,nil); return AttemptHandle(token: attempt) } }
    public func retryScheduled(_ handle: AttemptHandle,delayNS: UInt64) throws { try mutate { let a = handle.token,e = a.effect; try validate(e); guard !a.started,!a.queued,!a.hasSchedule,!a.abandoned,!e.resolved else { throw invalid() }; a.hasSchedule = true; a.scheduled = now(); if options.timings { do { try metrics.observe(.scheduledBackoff,quantity: delayNS,labels: labels(e,measurement: true)) } catch { metrics.markIncomplete() }; if e.affected { increment(&storedHealth.debuggerAffectedMeasurements) } }; event(.retryScheduled(a.id,delayNS: delayNS),e,a.scheduled) } }
    public func readyQueued(_ handle: AttemptHandle) throws { try mutate { let a = handle.token,e = a.effect; try validate(e); guard !a.queued else { throw duplicate(e) }; guard !a.started,!a.finished,!a.abandoned,!e.resolved else { throw invalid() }; a.queued = true; a.ready = now(); gauge(.readyDepth,1,e.labels); if !e.firstReady { measurement(.admissionDelay,e,e.requested,a.ready,attempt: a.id); e.firstReady = true }; if a.hasSchedule { measurement(.eligibilityWait,e,a.scheduled,a.ready,attempt: a.id) }; event(.ready(a.id),e,a.ready) } }
    public func readyRemoved(_ handle: AttemptHandle) throws { try mutate { let a = handle.token,e = a.effect; try validate(e); guard a.queued,!a.started else { throw invalid() }; a.queued = false; gauge(.readyDepth,-1,e.labels) } }
    public func attemptStarted(_ handle: AttemptHandle) throws { try mutate { let a = handle.token,e = a.effect; try validate(e); guard !a.started else { throw duplicate(e) }; guard !a.finished,!a.abandoned,!e.resolved else { throw invalid() }; a.started = true; a.start = now(); increment(&e.running); count(.attempts,e.labels); gauge(.running,1,e.labels); if a.queued { a.queued = false; gauge(.readyDepth,-1,e.labels); measurement(.queueWait,e,a.ready,a.start,attempt: a.id) }; if a.id.serial > 1 { measurement(.interAttemptGap,e,a.previousFinish,a.start,attempt: a.id) }; event(.attemptStarted(a.id),e,a.start) } }
    public func attemptFinished(_ handle: AttemptHandle,outcome: RuntimeOutcome) throws { try mutate { let a = handle.token,e = a.effect; try validate(e); guard !a.finished else { throw duplicate(e) }; guard a.started else { throw invalid() }; a.finished = true; a.finish = now(); e.lastFinish = a.finish; if e.running > 0 { e.running -= 1 }; count(.attemptOutcomes,labels(e,outcome: outcome.metric)); gauge(.running,-1,e.labels); measurement(.attemptElapsed,e,a.start,a.finish,attempt: a.id,outcome: outcome.metric); event(.attemptFinished(a.id,outcome),e,a.finish) } }
    public func resolved(_ handle: EffectHandle,outcome: RuntimeOutcome,selectedAttempt: AttemptHandle? = nil) throws { try mutate { let e = handle.token; try validate(e); guard !e.resolved else { throw duplicate(e) }; guard e.admitted == true else { throw invalid() }; if let a = selectedAttempt?.token { guard a.effect === e else { throw invalid(.foreignToken) }; guard a.finished else { throw invalid() } }; e.resolved = true; e.resolvedAt = now(); count(.resolutions,labels(e,outcome: outcome.metric)); gauge(.unresolved,-1,e.labels); measurement(.resolution,e,e.requested,e.resolvedAt,outcome: outcome.metric); if let a = selectedAttempt?.token { measurement(.resolutionOverhead,e,a.finish,e.resolvedAt,attempt: a.id,outcome: outcome.metric) }; event(.resolved(outcome),e,e.resolvedAt) } }
    public func cancellationRequested(_ handle: EffectHandle) throws { try mutate { let e = handle.token; try validate(e); guard !e.cancellationRequested else { throw duplicate(e) }; e.cancellationRequested = true; count(.cancellationRequests,e.labels); event(.cancellationRequested,e,now()) } }
    public func cancellationAcknowledged(_ handle: EffectHandle) throws { try mutate { let e = handle.token; try validate(e); guard !e.cancellationAcknowledged else { throw duplicate(e) }; guard e.cancellationRequested else { throw invalid() }; e.cancellationAcknowledged = true; count(.cancellationAcknowledgements,e.labels); event(.cancellationAcknowledged,e,now()) } }
    public func reservePublication(_ handle: EffectHandle) throws -> DeliveryHandle { try mutate { let e = handle.token; try validate(e); guard e.resolved else { throw invalid() }; guard e.nextDelivery < options.maxDeliveriesPerEffect else { throw invalid(.limit) }; e.nextDelivery += 1; let d = DeliveryToken(effect: e,id: DeliveryID(effect: e.id,serial: e.nextDelivery),posted: now()); gauge(.completionDepth,1,e.labels); event(.publicationReserved(d.id),e,d.posted); return DeliveryHandle(token: d) } }
    private func confirm(_ d: DeliveryToken) { guard !d.confirmed else { return }; d.confirmed = true; measurement(.completionPreparation,d.effect,d.effect.resolvedAt,d.posted,delivery: d.id) }
    public func publicationCommitted(_ handle: DeliveryHandle) throws { try mutate { let d = handle.token; try validate(d.effect); guard !d.failed else { throw invalid() }; confirm(d) } }
    public func publicationFailed(_ handle: DeliveryHandle) throws { try mutate { let d = handle.token,e = d.effect; try validate(e); guard !d.failed else { throw duplicate(e) }; guard !d.begun,!d.confirmed else { throw invalid() }; d.failed = true; gauge(.completionDepth,-1,e.labels); event(.publicationFailed(d.id),e,now()) } }
    public func deliveryBegun(_ handle: DeliveryHandle) throws { try mutate { let d = handle.token,e = d.effect; try validate(e); guard !d.begun else { throw duplicate(e) }; guard !d.failed else { throw invalid() }; d.begun = true; d.begin = now(); confirm(d); gauge(.completionDepth,-1,e.labels); measurement(.completionQueueWait,e,d.posted,d.begin,delivery: d.id); measurement(.deliveryLag,e,e.resolvedAt,d.begin,delivery: d.id); if !e.firstDeliveryBegun { measurement(.endToEnd,e,e.requested,d.begin,delivery: d.id); e.firstDeliveryBegun = true }; event(.deliveryBegun(d.id),e,d.begin) } }
    public func deliveryObserved(_ handle: DeliveryHandle,disposition: DeliveryDisposition) throws { try mutate { let d = handle.token,e = d.effect; try validate(e); guard !d.observed else { throw duplicate(e) }; guard d.begun,!d.failed else { throw invalid() }; d.observed = true; let time = now(); count(.deliveries,labels(e,outcome: disposition.metric)); if e.observed > 0 { count(.duplicateDeliveries,e.labels); increment(&storedHealth.duplicateDeliveries) } else { measurement(.endToEndObserved,e,e.requested,time,delivery: d.id,outcome: disposition.metric) }; increment(&e.observed); measurement(.completionProcessing,e,d.begin,time,delivery: d.id,outcome: disposition.metric); event(.deliveryObserved(d.id,disposition),e,time) } }
    public func settled(_ handle: EffectHandle) throws { try mutate { let e = handle.token; try validate(e); guard !e.settled else { throw duplicate(e) }; e.settled = true; let time = now(); measurement(.settlement,e,e.requested,time); event(.settled,e,time) } }
    private func abandon(_ effect: EffectToken,attempt: AttemptToken? = nil) { if let attempt { guard !attempt.abandoned,!attempt.finished else { return }; attempt.abandoned = true }; effect.abandoned = true; increment(&storedHealth.abandoned); metrics.markIncomplete(); count(.abandoned,effect.labels); event(.abandoned(attempt?.id),effect,nil) }
    public func attemptAbandoned(_ handle: AttemptHandle) throws { try mutate { try validate(handle.token.effect); abandon(handle.token.effect,attempt: handle.token) } }
    public func debuggerPause(_ paused: Bool) throws { try mutate { guard self.paused != paused else { return }; let time = now(); self.paused = paused; if paused { increment(&pauseGeneration); if pauses.count >= options.maxPauseIntervals { pauses.removeFirst(); increment(&storedHealth.pauseIntervalEvictions) }; pauses.append(PauseInterval(generation: pauseGeneration,start: time,end: nil)) } else if !pauses.isEmpty { pauses[pauses.count-1].end = time } } }
    public var pauseIntervals: [PauseInterval] { read { pauses } }
    public var health: TelemetryHealth { read { var value = storedHealth; value.metric = metrics.current().health; value.retainedDetailRecords = UInt64(records.count); value.retainedDetailBytes = UInt64(detailBytes); return value } }
    public func effectDetails(_ id: EffectID) -> EffectDetails? { read { records[id]?.projection() } }
    public func effectTimeline(_ id: EffectID) -> EffectTimeline? { effectDetails(id) }
    /// Explicit endpoint overload is passive and reads no supplied runtime clock.
    public func metricSnapshot(at: LocalInstant) -> MetricSnapshot { read { metrics.snapshot(at: at) } }
    public func metricSnapshot() -> MetricSnapshot { read { let revision = metrics.current().revision; var at = now(); if at != nil && revision != metrics.current().revision { at = nil; metrics.noteOmittedMeasurement() }; return metrics.snapshot(at: at) } }
    public var availableWindows: [UInt64] { read { metrics.availableWindows } }
    public func metricWindow(from: UInt64,to: UInt64) throws -> MetricSnapshot { lock.lock(); defer { lock.unlock() }; return try metrics.window(from: from,to: to) }
    public var metricCatalog: [MetricDescriptor] { MetricFamily.allCases.map { $0.descriptor } }
    public func hostGaugeSnapshot(labels: MetricLabels = MetricLabels(),unresolved: UInt64,running: UInt64,ready: UInt64,completion: UInt64) throws { try mutate { var dimensions = labels; dimensions.origin = options.origin; dimensions.outcome = .none; dimensions.debuggerAffected = false; guard metrics.labels.allows(dimensions) else { throw invalid(.invalidLabels) }; guard options.counters else { return }; for (family,value) in [(MetricFamily.unresolved,unresolved),(.running,running),(.readyDepth,ready),(.completionDepth,completion)] { try metrics.setGauge(family,value: value,labels: dimensions) }; metrics.setGaugeScope(.hostSnapshot,labels: dimensions) } }
    /// Hook failures never suppress or replace the real operation's result. No
    /// lock/span is retained across await. Merely constructing this async call
    /// executes no work; the body begins when its caller actually runs it.
    /// The operation transfers its result across its async isolation boundary;
    /// T need not be Sendable, but a non-Sendable result must be disconnected,
    /// not a retained actor-isolated object.
    public func instrument<T>(_ attempt: AttemptHandle,operation: () async throws -> sending T,classify: (Result<T,Error>) -> RuntimeOutcome,isolation: isolated (any Actor)? = #isolation) async throws -> T {
        try? attemptStarted(attempt)
        let result: Result<T,Error>; do { result = .success(try await operation()) } catch { result = .failure(error) }
        try? attemptFinished(attempt,outcome: classify(result)); return try result.get()
    }
    /// A throwing classifier is an explicit Swift callback capability. Its fault
    /// becomes unclassified telemetry and cannot replace the host result.
    /// Operation results use the same sending transfer contract as instrument.
    public func instrumentClassifying<T>(_ attempt: AttemptHandle,operation: () async throws -> sending T,classify: (Result<T,Error>) throws -> RuntimeOutcome,isolation: isolated (any Actor)? = #isolation) async throws -> T {
        try? attemptStarted(attempt)
        let result: Result<T,Error>; do { result = .success(try await operation()) } catch { result = .failure(error) }
        let outcome: RuntimeOutcome; do { outcome = try classify(result) } catch { read { increment(&storedHealth.callbackFaults); metrics.markIncomplete() }; outcome = .unclassified }
        try? attemptFinished(attempt,outcome: outcome); return try result.get()
    }
    public func exportMetrics(_ sink: (MetricSnapshot) throws -> Void) throws { let snapshot = metricSnapshot(); do { try sink(snapshot) } catch { read { increment(&storedHealth.callbackFaults) }; throw error } }
    fileprivate func releasedEffect(_ effect: EffectToken) { lock.lock(); defer { lock.unlock() }; if storedHealth.activeEffectHandles > 0 { storedHealth.activeEffectHandles -= 1 }; guard !effect.resolved else { return }; guard !busy else { increment(&storedHealth.destructorHookLosses); metrics.noteOmittedMeasurement(); return }; busy = true; defer { busy = false }; abandon(effect) }
    fileprivate func releasedAttempt(_ attempt: AttemptToken) { lock.lock(); defer { lock.unlock() }; guard !attempt.finished,!attempt.abandoned else { return }; guard !busy else { increment(&storedHealth.destructorHookLosses); metrics.noteOmittedMeasurement(); return }; busy = true; defer { busy = false }; abandon(attempt.effect,attempt: attempt) }
    fileprivate func releasedDelivery(_ delivery: DeliveryToken) { lock.lock(); defer { lock.unlock() }; guard !delivery.failed,!delivery.observed else { return }; guard !busy else { increment(&storedHealth.destructorHookLosses); metrics.noteOmittedMeasurement(); return }; busy = true; defer { busy = false }; abandon(delivery.effect) }
}
public struct SlowTimingQuery {
    public var operation: UInt16; public var family: MetricFamily; public var thresholdNS: UInt64
    public var measurementOrigin: MeasurementOrigin? = .live; public var outcome: MetricOutcome?; public var includeDebuggerAffected = false
    public var maxResults = 64; public var maxResultBytes = 64*1024
    public init(operation: UInt16,family: MetricFamily,thresholdNS: UInt64) { self.operation = operation; self.family = family; self.thresholdNS = thresholdNS }
}
public enum SlowTimingAvailability { case available,timingDisabled,detailsDisabled }
public struct SlowTimingMatch { public let effect: EffectID; public let origin: RequestOrigin?; public let labels: MetricLabels; public let timing: EffectTimingObservation }
public struct SlowTimingResult { public let run: UInt64; public let epoch: UInt64; public let detailRevision: UInt64; public let query: SlowTimingQuery; public let availability: SlowTimingAvailability; public let matches: [SlowTimingMatch]; public let matched: UInt64; public let inspected: UInt64; public let unknownMeasurements: UInt64; public let resultBytes: Int; public let truncated: Bool; public let complete: Bool; public let retainedDetailOnly = true }
extension EffectObserver {
    /// Passive bounded selection over completed retained detail, not an aggregate
    /// latency population or pending-age query. It reads no clock or callback.
    public func slowTimings(_ query: SlowTimingQuery) throws -> SlowTimingResult { try mutate {
        guard Int(query.operation) < metrics.labels.operations.count else { throw LifecycleError.invalidLabels }; guard query.family.kind == .histogram else { throw LifecycleError.wrongPhase }; guard query.maxResults >= 0,query.maxResults <= 4096,query.maxResultBytes >= 256,query.maxResultBytes <= 4*1024*1024 else { throw LifecycleError.limit }
        let availability: SlowTimingAvailability = !options.timings ? .timingDisabled : !options.details ? .detailsDisabled : .available
        var matches: [SlowTimingMatch] = [],matched: UInt64 = 0,inspected: UInt64 = 0,unknown: UInt64 = 0,bytes = 256,truncated = false
        if availability == .available { for id in order { guard let value = records[id],value.labels.operation == query.operation,query.measurementOrigin == nil || value.labels.origin == query.measurementOrigin else { continue }; for timing in value.timings { guard timing.family == query.family,(query.includeDebuggerAffected || !timing.debuggerAffected),(query.outcome == nil || timing.outcome == query.outcome) else { continue }; increment(&inspected); guard case let .success(nanos) = timing.durationNS else { increment(&unknown); continue }; guard nanos >= query.thresholdNS else { continue }; increment(&matched); if matches.count >= query.maxResults || 384 > query.maxResultBytes-bytes { truncated = true } else { matches.append(SlowTimingMatch(effect: id,origin: value.origin,labels: value.labels,timing: timing)); bytes += 384 } } } }
        let complete = availability == .available && !truncated && storedHealth.detailEvictions == 0 && storedHealth.detailRejections == 0 && storedHealth.detailEventDrops == 0 && storedHealth.destructorHookLosses == 0
        return SlowTimingResult(run: options.run,epoch: options.epoch,detailRevision: eventSequence,query: query,availability: availability,matches: matches,matched: matched,inspected: inspected,unknownMeasurements: unknown,resultBytes: bytes,truncated: truncated,complete: complete)
    } }
}

/// A native lazy wrapper: construction reads no clock and runs no host work.
/// The wrapper can be run once. Dropping it reports only observation abandonment,
/// never termination of remote work. It deliberately has no Sendable conformance.
public final class ObservedEffectOperation<T> {
    private let observer: EffectObserver
    private let attempt: AttemptHandle
    // A unit argument avoids Swift 6.0.3's zero-parameter sending-function
    // field-metadata assertion without weakening the checked result transfer.
    private let operation: (()) async throws -> sending T
    private let classify: (Result<T,Error>) throws -> RuntimeOutcome
    private let claimLock = NSLock()
    private var claimed = false
    fileprivate init(observer: EffectObserver,attempt: AttemptHandle,operation: @escaping () async throws -> sending T,classify: @escaping (Result<T,Error>) throws -> RuntimeOutcome) { self.observer = observer; self.attempt = attempt; self.operation = { _ in try await operation() }; self.classify = classify }
    private func claim() throws { claimLock.lock(); defer { claimLock.unlock() }; guard !claimed else { throw LifecycleError.duplicateObservation }; claimed = true }
    public func run(isolation: isolated (any Actor)? = #isolation) async throws -> T { try claim(); return try await observer.instrumentClassifying(attempt,operation: { try await self.operation(()) },classify: classify,isolation: isolation) }
    deinit { claimLock.lock(); let unused = !claimed; claimLock.unlock(); if unused { try? observer.attemptAbandoned(attempt) } }
}
extension EffectObserver {
    /// Lazy instrumentation with the same sending result contract as instrument.
    public func instrumentOperation<T>(_ attempt: AttemptHandle,operation: @escaping () async throws -> sending T,classify: @escaping (Result<T,Error>) throws -> RuntimeOutcome) -> ObservedEffectOperation<T> { ObservedEffectOperation(observer: self,attempt: attempt,operation: operation,classify: classify) }
}
