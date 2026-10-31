import Statelessness

public struct LiveLimits {
    public var maxEvents = 1024
    public var maxEventBytes = 256*1024
    public var maxEffectOrigins = 4096
    public init() {}
}
public enum LiveEventKind {
    case checkpoint(failedChecks: Int,checksOmitted: Int)
    case turn(disposition: DeliveryDisposition,failedChecks: Int,checksOmitted: Int,fullChecking: Bool,outputsRequested: Int)
    case gap(expected: UInt64,actual: UInt64),recordingFrozen,effectDispatched(EffectOrigin)
}
public struct LiveEvent { public let epoch: UInt64; public let streamSequence: UInt64; public let hostSequence: UInt64; public let kind: LiveEventKind }
public struct LiveHealth {
    public let epoch: UInt64
    public let attachedAfter: UInt64
    public let hostSequence: UInt64
    public let captureComplete: Bool
    public let recorderFrozen: Bool
    public let recordedSteps: UInt64
    public let retainedSteps: Int
    public let evictedSteps: UInt64
    public let eventDrops: UInt64
    public let originEvictions: UInt64
    public let duplicateObservations: UInt64
}
/// Observation-only attachment to application-executed sealed checks. Independent
/// exact capture and lossy metadata events never call reducer/checker/effect/clock.
public final class LiveBridge<S,I,O> {
    public let epoch: UInt64
    public let attachedAfter: UInt64
    public let limits: LiveLimits
    private let metadata: Metadata
    private let recorder: Recorder
    private let maxCheckSummary: UInt64
    private var hostSequence: UInt64
    private var streamSequence: UInt64 = 0
    private var captureComplete = true
    private var events: [LiveEvent] = []
    private var eventDrops: UInt64 = 0
    private var originEvictions: UInt64 = 0
    private var duplicates: UInt64 = 0
    private var origins: [(EffectOrigin,Bool)] = []
    private var busy = false
    public init(initial: CheckedInitial<S,I,O>,hostSequence: UInt64,epoch: UInt64,config: RunConfig,recorderOptions: RecorderOptions = RecorderOptions(),limits: LiveLimits = LiveLimits()) throws {
        guard epoch > 0,limits.maxEvents > 0,limits.maxEventBytes >= 128,limits.maxEffectOrigins > 0 else { throw LiveError.invalidLimits }
        guard !config.parameters.contains(where: { $0.key.hasPrefix("stateless.debug.") }) else { throw ModelError("reserved debug provenance") }
        guard let codec = initial.model.codec else { throw ModelError("live bridge requires codec") }
        metadata = try codec.metadata(); self.epoch = epoch; attachedAfter = hostSequence; self.hostSequence = hostSequence; self.limits = limits; maxCheckSummary = recorderOptions.limits.maxChecksPerStep
        let parameters = config.parameters+[AuditParameter("stateless.debug.origin","recorded-live-observation"),AuditParameter("stateless.debug.host_epoch",String(epoch)),AuditParameter("stateless.debug.live.max_events",String(limits.maxEvents)),AuditParameter("stateless.debug.live.max_event_bytes",String(limits.maxEventBytes)),AuditParameter("stateless.debug.live.max_effect_origins",String(limits.maxEffectOrigins))]
        recorder = try Recorder(checkedInitial: initial,sequenceOrigin: hostSequence,config: RunConfig(strategy: config.strategy,seed: config.seed,parameters: parameters),options: recorderOptions)
        let count = Int(min(UInt64(initial.checks.count),maxCheckSummary))
        publish(.checkpoint(failedChecks: initial.checks.prefix(count).filter { $0.status == .failed }.count,checksOmitted: initial.checks.count-count))
    }
    public var health: LiveHealth { LiveHealth(epoch: epoch,attachedAfter: attachedAfter,hostSequence: hostSequence,captureComplete: captureComplete,recorderFrozen: recorder.isFrozen,recordedSteps: recorder.observedSteps,retainedSteps: recorder.retainedSteps,evictedSteps: recorder.evictedSteps,eventDrops: eventDrops,originEvictions: originEvictions,duplicateObservations: duplicates) }
    private func publish(_ kind: LiveEventKind) {
        guard streamSequence != UInt64.max else { if eventDrops < UInt64.max { eventDrops += 1 }; return }; streamSequence += 1
        let maximum = min(limits.maxEvents,limits.maxEventBytes/128)
        guard events.count < maximum else { if eventDrops < UInt64.max { eventDrops += 1 }; return }
        events.append(LiveEvent(epoch: epoch,streamSequence: streamSequence,hostSequence: hostSequence,kind: kind))
    }
    public func stopRecording(_ error: Error) { captureComplete = false; _ = try? recorder.stopWithError(error) }
    public func observe(hostSequence: UInt64,checked: CheckedTurn<S,I,O>) throws {
        guard !busy else { throw DebugError.reentrant }; busy = true; defer { busy = false }
        guard try checked.model.codec?.metadata() == metadata else { stopRecording(ModelError("live identity changed")); throw LiveError.identityChanged }
        guard checked.sequence == hostSequence else { throw LiveError.sequenceMismatch }
        guard hostSequence > self.hostSequence else { if duplicates < UInt64.max { duplicates += 1 }; throw LiveError.duplicate }
        guard self.hostSequence != UInt64.max else { throw LiveError.exhausted }
        let expected = self.hostSequence+1,gap = hostSequence != expected; self.hostSequence = hostSequence
        if gap { stopRecording(ModelError("host sequence gap")); publish(.gap(expected: expected,actual: hostSequence)) }
        var recordingFailed = false
        if captureComplete && !recorder.isFrozen { do { _ = try recorder.observeChecked(checked) } catch { captureComplete = false; recordingFailed = true } }
        if recorder.isFrozen || !captureComplete { publish(.recordingFrozen) }
        let transition = try checked.transition
        let disposition: DeliveryDisposition
        switch transition.disposition { case .accepted: disposition = .accepted; case .rejected: disposition = .rejected; case .ignored: disposition = .ignored }
        let count = Int(min(UInt64(checked.checks.count),maxCheckSummary))
        publish(.turn(disposition: disposition,failedChecks: checked.checks.prefix(count).filter { $0.status == .failed }.count,checksOmitted: checked.checks.count-count,fullChecking: checked.fullChecking,outputsRequested: transition.outputs.count))
        let retained = min(transition.outputs.count,limits.maxEffectOrigins),skipped = transition.outputs.count-retained,evict = max(0,origins.count-(limits.maxEffectOrigins-retained))
        let increment = UInt64(skipped)+UInt64(evict), (total,overflow) = originEvictions.addingReportingOverflow(increment); originEvictions = overflow ? UInt64.max : total
        origins.removeFirst(evict)
        for index in skipped..<transition.outputs.count { origins.append((EffectOrigin(epoch: epoch,transition: hostSequence,outputIndex: index),false)) }
        if gap { throw LiveError.gap }; if recordingFailed { throw LiveError.recordingFailed }
    }
    public func effectDispatched(_ origin: EffectOrigin) throws {
        guard !busy else { throw DebugError.reentrant }
        guard let index = origins.firstIndex(where: { $0.0 == origin }) else { throw LiveError.unknownEffect }
        guard !origins[index].1 else { throw LiveError.duplicate }; origins[index].1 = true; publish(.effectDispatched(origin))
    }
    public func popEvent() -> LiveEvent? { events.isEmpty ? nil : events.removeFirst() }
    public func export() throws -> AuditTrace { guard !busy else { throw DebugError.reentrant }; return recorder.snapshot() }
}
