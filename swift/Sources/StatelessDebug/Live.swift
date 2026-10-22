import Statelessness

public enum LiveError: Error,Equatable { case invalidLimits,wrongEpoch,staleRevision,sequenceMismatch,duplicate,gap,unauthorized,wrongPhase,disconnected,overflow,exhausted,unknownEffect,identityChanged,recordingFailed }
public enum ControllerPhase { case running,pauseRequested,paused,oneTurnAuthorized,delivering,terminated }
public enum EffectPhase { case dispatched,staged,dispatching }
public enum DetachPolicy { case remainPaused,resume,terminate }
public enum OverflowPolicy { case backpressure,disconnect }
public struct ControlAuthority { public var pauseAndStep = false; public var inject = false; public init() {} }
public struct ControllerOptions {
    public var authority = ControlAuthority()
    public var detach: DetachPolicy = .remainPaused
    public var overflow: OverflowPolicy = .backpressure
    public var maxArrivals = 1024
    public var maxArrivalBytes = 1024*1024
    public var maxOutputs = 4096
    public init() {}
}
public struct EffectOrigin: Hashable { public let epoch: UInt64; public let transition: UInt64; public let outputIndex: Int }
public struct PauseAck { public let generation: UInt64; public let epoch: UInt64; public let revision: UInt64; public let effectPhase: EffectPhase }
fileprivate final class PermitOwner {}
/// Private construction and controller bookkeeping enforce single consumption,
/// even though Swift permits copying the value. Dropped permits remain uncertain.
public struct DeliveryPermit { fileprivate let owner: PermitOwner; fileprivate let epoch: UInt64; fileprivate let revision: UInt64; fileprivate let serial: UInt64 }
public struct DispatchPermit {
    fileprivate let owner: PermitOwner
    public let epoch: UInt64
    public let revision: UInt64
    public let outputIndex: Int
    public var origin: EffectOrigin { EffectOrigin(epoch: epoch,transition: revision,outputIndex: outputIndex) }
}
public struct ArrivalRejected<I> { public let error: LiveError; public let input: I }
private enum DispatchState { case staged,inFlight,dispatched,uncertain }
/// Host-owned cooperative gate; no reducers, checkers, clocks or external effects
/// run here. A pause cannot preempt callbacks or work already dispatched.
public final class LiveController<I> {
    public let options: ControllerOptions
    public private(set) var epoch: UInt64
    public let originEpoch: UInt64
    public private(set) var revision: UInt64
    public private(set) var phase: ControllerPhase = .running
    public private(set) var effectPhase: EffectPhase = .dispatched
    public private(set) var connected = true
    public private(set) var overflowCount: UInt64 = 0
    public private(set) var pauseGeneration: UInt64 = 0
    public private(set) var bufferedBytes = 0
    public var bufferedArrivals: Int { arrivals.count }
    private var arrivals: [(I,Int)] = []
    private var staged: [DispatchState] = []
    private let owner = PermitOwner()
    private var serial: UInt64 = 0
    private var activeSerial: UInt64?
    private var repause = false
    private var terminatePending = false
    private var dispatchRemaining = 0
    private var dispatchInflight = 0
    public init(epoch: UInt64,revision: UInt64 = 0,options: ControllerOptions = ControllerOptions()) throws {
        guard epoch > 0,options.maxArrivals > 0,options.maxArrivalBytes > 0,options.maxOutputs > 0 else { throw LiveError.invalidLimits }
        self.epoch = epoch; originEpoch = epoch; self.revision = revision; self.options = options
    }
    private func command(epoch: UInt64,revision: UInt64,pauseAuthority: Bool = true) throws {
        guard connected else { throw LiveError.disconnected }; guard epoch == self.epoch else { throw LiveError.wrongEpoch }; guard revision == self.revision else { throw LiveError.staleRevision }
        guard pauseAuthority ? options.authority.pauseAndStep : options.authority.inject else { throw LiveError.unauthorized }
    }
    public func requestPause(epoch: UInt64,revision: UInt64) throws {
        try command(epoch: epoch,revision: revision)
        switch phase { case .running,.oneTurnAuthorized: phase = .pauseRequested; case .delivering: repause = true; case .terminated: throw LiveError.wrongPhase; default: break }
    }
    public func safePoint() throws -> PauseAck? {
        guard dispatchInflight == 0 else { throw LiveError.wrongPhase }
        switch phase {
        case .pauseRequested: guard pauseGeneration != UInt64.max else { throw LiveError.exhausted }; pauseGeneration += 1; phase = .paused
        case .paused: break
        case .running,.oneTurnAuthorized: return nil
        default: throw LiveError.wrongPhase
        }
        return PauseAck(generation: pauseGeneration,epoch: epoch,revision: revision,effectPhase: effectPhase)
    }
    public func authorizeOne(epoch: UInt64,revision: UInt64) throws { try command(epoch: epoch,revision: revision); guard phase == .paused else { throw LiveError.wrongPhase }; phase = .oneTurnAuthorized }
    public func resume(epoch: UInt64,revision: UInt64) throws {
        try command(epoch: epoch,revision: revision); guard phase == .paused || phase == .pauseRequested || phase == .oneTurnAuthorized else { throw LiveError.wrongPhase }; phase = .running
    }
    /// Rejection returns ownership so host backpressure can retry explicitly.
    public func externalArrival(_ input: I,ownedBytes: Int) -> ArrivalRejected<I>? {
        guard phase != .terminated else { return ArrivalRejected(error: .wrongPhase,input: input) }
        guard ownedBytes >= 0,arrivals.count < options.maxArrivals,ownedBytes <= options.maxArrivalBytes-bufferedBytes else {
            if overflowCount < UInt64.max { overflowCount += 1 }; if options.overflow == .disconnect { disconnect() }; return ArrivalRejected(error: .overflow,input: input)
        }
        arrivals.append((input,ownedBytes)); bufferedBytes += ownedBytes; return nil
    }
    public func inject(epoch: UInt64,revision: UInt64,input: I,ownedBytes: Int) -> ArrivalRejected<I>? {
        do { try command(epoch: epoch,revision: revision,pauseAuthority: false) } catch { return ArrivalRejected(error: error as? LiveError ?? .unauthorized,input: input) }
        return externalArrival(input,ownedBytes: ownedBytes)
    }
    public func beginNext() throws -> (DeliveryPermit,I)? {
        guard (phase == .running || phase == .oneTurnAuthorized),effectPhase == .dispatched else { throw LiveError.wrongPhase }
        guard !arrivals.isEmpty else { return nil }
        guard serial != UInt64.max,revision != UInt64.max else { throw LiveError.exhausted }
        let (input,bytes) = arrivals.removeFirst(); bufferedBytes -= bytes; repause = phase == .oneTurnAuthorized; phase = .delivering; serial += 1; activeSerial = serial
        return (DeliveryPermit(owner: owner,epoch: epoch,revision: revision,serial: serial),input)
    }
    private func validate(_ permit: DeliveryPermit) throws {
        guard permit.owner === owner,phase == .delivering,permit.serial == activeSerial,permit.revision == revision,permit.epoch == epoch else { throw LiveError.wrongPhase }
    }
    public func delivered(_ permit: DeliveryPermit,outputs: Int) throws {
        try validate(permit); activeSerial = nil; revision += 1
        guard outputs >= 0,outputs <= options.maxOutputs else { phase = .terminated; throw LiveError.overflow }
        staged = Array(repeating: .staged,count: outputs); dispatchRemaining = outputs; dispatchInflight = 0; effectPhase = outputs == 0 ? .dispatched : .staged
        phase = terminatePending ? .terminated : repause ? .pauseRequested : .running; repause = false
    }
    public func deliveryFailed(_ permit: DeliveryPermit) throws { try validate(permit); activeSerial = nil; phase = .terminated }
    public func takeDispatch(outputIndex: Int) throws -> DispatchPermit {
        guard phase == .running || phase == .pauseRequested || phase == .oneTurnAuthorized else { throw LiveError.wrongPhase }
        guard outputIndex >= 0,outputIndex < staged.count else { throw LiveError.unknownEffect }
        guard staged[outputIndex] == .staged else { throw LiveError.duplicate }
        staged[outputIndex] = .inFlight; dispatchInflight += 1; effectPhase = .dispatching
        return DispatchPermit(owner: owner,epoch: originEpoch,revision: revision,outputIndex: outputIndex)
    }
    private func validate(_ permit: DispatchPermit) throws {
        guard permit.owner === owner,permit.epoch == originEpoch,permit.revision == revision,permit.outputIndex >= 0,permit.outputIndex < staged.count,staged[permit.outputIndex] == .inFlight else { throw LiveError.wrongPhase }
    }
    public func dispatched(_ permit: DispatchPermit) throws {
        try validate(permit); staged[permit.outputIndex] = .dispatched; dispatchInflight -= 1; dispatchRemaining -= 1
        effectPhase = dispatchRemaining == 0 ? .dispatched : dispatchInflight > 0 ? .dispatching : .staged
    }
    public func dispatchFailed(_ permit: DispatchPermit) throws { try validate(permit); staged[permit.outputIndex] = .uncertain; phase = .terminated }
    public func terminate() { terminatePending = true; if phase != .delivering { phase = .terminated } }
    public func disconnect() {
        guard connected else { return }; connected = false
        switch options.detach {
        case .terminate: terminate()
        case .remainPaused: if phase == .delivering { repause = true } else if phase != .terminated && phase != .paused { phase = .pauseRequested }
        case .resume: if phase == .delivering { repause = false } else if phase != .terminated { phase = .running }
        }
    }
    public func reconnect() throws -> UInt64 {
        guard !connected,dispatchInflight == 0,phase != .delivering,phase != .terminated else { throw LiveError.wrongPhase }
        guard epoch != UInt64.max else { throw LiveError.exhausted }; epoch += 1; connected = true; return epoch
    }
}
