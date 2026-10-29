import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

private struct CampaignControl: Sendable {
    let limits: CampaignLimits
    let started: TimeInterval?
    init(_ limits: CampaignLimits) throws {
        if let n = limits.maxDuration, !n.isFinite || n < 0 { throw ConfigError("invalid run duration") }
        self.limits = limits
        started = limits.maxDuration == nil ? nil : ProcessInfo.processInfo.systemUptime
    }
    func stop() -> CampaignExternalStop? {
        if limits.isCancelled() { return .cancelled }
        if let started, let n = limits.maxDuration, ProcessInfo.processInfo.systemUptime - started >= n { return .deadline }
        return nil
    }
    func remaining() -> RunLimits {
        let duration = started.flatMap { start in
            limits.maxDuration.map { max(0, $0 - (ProcessInfo.processInfo.systemUptime - start)) }
        }
        return RunLimits(maxDuration: duration, isCancelled: limits.isCancelled)
    }
}

/// Only protocol summaries enter this mutex. All application callbacks and
/// report/input releases happen outside it. Unchecked denotes lock protection,
/// never permission to transfer a model, feedback, or observer between workers.
private final class CampaignCoordinator: @unchecked Sendable {
    private let mutex = NSLock()
    private let lifecycle: CampaignLifecycle
    private var state: CampaignLifecycleState
    init(_ config: CampaignConfig64) throws {
        lifecycle = CampaignLifecycle(config: config)
        state = try lifecycle.initialState()
    }
    func apply(_ input: CampaignLifecycleInput, claim: Bool = false) -> [CampaignLifecycleOutput] {
        mutex.lock(); defer { mutex.unlock() }
        guard let outputs = lifecycle.apply(&state, input) else {
            precondition(claim, "invalid campaign lifecycle event")
            return []
        }
        return outputs
    }
    func claim(_ control: CampaignControl) -> UInt64? {
        // Cancellation is an application-provided Sendable closure; never invoke
        // it while the coordinator is locked. The sampled event is then atomic.
        let sampled = control.stop()
        let outputs = apply(.claim(sampled), claim: true)
        if case let .runJob(id)? = outputs.first { return id }
        return nil
    }
    func complete<I>(_ job: JobReport64<I>) {
        let summary: CampaignSummary
        switch job.outcome {
        case let .completed(report):
            let outcome: CampaignSummaryOutcome
            switch report.termination {
            case "failure_found": outcome = .failure
            case "cancelled": outcome = .cancelled
            case "deadline": outcome = .deadline
            default: outcome = .completed
            }
            summary = CampaignSummary(outcome: outcome, failure: report.failure != nil,
                                      transitions: report.transitions, cases: UInt64(report.cases), skippedChecks: report.skippedChecks)
        case .factoryError: summary = CampaignSummary(outcome: .factoryError)
        case .modelError: summary = CampaignSummary(outcome: .modelError)
        case .panicked: summary = CampaignSummary(outcome: .panicked)
        }
        _ = apply(.complete(job.jobID, summary))
    }
    func finish() -> CampaignReport64 {
        _ = apply(.finish)
        mutex.lock(); defer { mutex.unlock() }
        return state.report
    }
}

/// Internal host fault/ordering seams, not a public scheduling or resume API.
/// These hooks are absent from the public entry points and never change the law.
struct CampaignWorkerHooks: Sendable {
    var beforeStart: @Sendable (Int) throws -> Void = { _ in }
    var afterClaim: @Sendable (UInt64) throws -> Void = { _ in }
    var afterComplete: @Sendable (UInt64) -> Void = { _ in }
    var beforeExit: @Sendable (Int) throws -> Void = { _ in }
    var channelProbe: CampaignChannelProbe? = nil
}

/// Test-only transport observations, unavailable through the public entry points.
/// The channel uses this same condition, so a snapshot cannot acquire its lock
/// between recording a full/rendezvous wait and the actual condition.wait().
/// Only IDs and event kinds are retained; no application value or callback enters
/// this storage. No test callback is invoked while the channel is locked.
final class CampaignChannelProbe: @unchecked Sendable {
    struct Event: Equatable, Sendable {
        enum Kind: Equatable, Sendable {
            case claimed, enqueued, received, sendReturned
            case waitingForCapacity, waitingForSlot, waitingForReceipt
        }
        let kind: Kind
        let workerID: Int
        let jobID: UInt64
    }
    struct Snapshot: Sendable {
        let events: [Event]
        let waiting: [Int: Event]
        let joinedWorkers: [Int]
    }
    fileprivate let condition = NSCondition()
    private var events: [Event] = []
    private var waiting: [Int: Event] = [:]
    private var joinedWorkers: [Int] = []

    // Caller holds the channel condition. Repeated spurious wakes do not add
    // duplicate observations or continually wake the other blocked senders.
    fileprivate func recordLocked(_ kind: Event.Kind, workerID: Int, jobID: UInt64) {
        let event = Event(kind: kind, workerID: workerID, jobID: jobID)
        switch kind {
        case .waitingForCapacity, .waitingForSlot, .waitingForReceipt:
            if waiting[workerID] == event { return }
            waiting[workerID] = event
        case .received:
            // Receipt changes the channel predicates. Require fresh observations
            // after the broadcast, not stale waits from before that receipt.
            waiting.removeAll()
        case .enqueued, .sendReturned:
            waiting.removeValue(forKey: workerID)
        case .claimed: break
        }
        events.append(event)
        condition.broadcast()
    }
    fileprivate func record(_ kind: Event.Kind, workerID: Int, jobID: UInt64) {
        condition.lock(); defer { condition.unlock() }
        recordLocked(kind, workerID: workerID, jobID: jobID)
    }
    fileprivate func joined(_ workerID: Int) {
        condition.lock(); defer { condition.unlock() }
        joinedWorkers.append(workerID)
    }
    private func snapshotLocked() -> Snapshot {
        Snapshot(events: events, waiting: waiting, joinedWorkers: joinedWorkers)
    }
    func snapshot() -> Snapshot {
        condition.lock(); defer { condition.unlock() }
        return snapshotLocked()
    }
    func waitForBlockedSenders(_ count: Int, timeout: TimeInterval = 10) throws -> Snapshot {
        let deadline = Date(timeIntervalSinceNow: timeout)
        condition.lock(); defer { condition.unlock() }
        while waiting.count < count {
            guard condition.wait(until: deadline) || waiting.count >= count else {
                throw ModelError("test handshake timed out: blocked campaign senders")
            }
        }
        return snapshotLocked()
    }
}

private final class CampaignEnvelope<I: Sendable>: Sendable {
    let report: JobReport64<I>
    let workerID: Int
    init(_ report: JobReport64<I>, workerID: Int) { self.report = report; self.workerID = workerID }
}

/// Every mutable field is protected by `condition`. At capacity zero the slot
/// belongs to the blocked sending worker: send returns only after receipt.
/// Positive capacity permits send to return after enqueue, as sync_channel does.
private final class CampaignChannel<I: Sendable>: @unchecked Sendable {
    private let condition: NSCondition
    private let probe: CampaignChannelProbe?
    private let capacity: Int
    private var queue: [CampaignEnvelope<I>] = []
    private var rendezvous: CampaignEnvelope<I>?
    private var producers = 1 // main owner prevents early EOF during startup
    init(capacity: Int, probe: CampaignChannelProbe?) {
        self.capacity = capacity; self.probe = probe
        condition = probe?.condition ?? NSCondition()
    }
    func addProducer() { condition.lock(); producers += 1; condition.unlock() }
    func producerDone() {
        condition.lock(); producers -= 1; condition.broadcast(); condition.unlock()
    }
    func send(_ report: JobReport64<I>, workerID: Int) {
        let envelope = CampaignEnvelope(report, workerID: workerID)
        condition.lock()
        if capacity == 0 {
            while rendezvous != nil {
                probe?.recordLocked(.waitingForSlot, workerID: workerID, jobID: report.jobID)
                condition.wait()
            }
            rendezvous = envelope; condition.broadcast()
            probe?.recordLocked(.enqueued, workerID: workerID, jobID: report.jobID)
            while rendezvous === envelope {
                probe?.recordLocked(.waitingForReceipt, workerID: workerID, jobID: report.jobID)
                condition.wait()
            }
        } else {
            while queue.count == capacity {
                probe?.recordLocked(.waitingForCapacity, workerID: workerID, jobID: report.jobID)
                condition.wait()
            }
            queue.append(envelope); condition.broadcast()
            probe?.recordLocked(.enqueued, workerID: workerID, jobID: report.jobID)
        }
        condition.unlock()
    }
    func receive() -> JobReport64<I>? {
        condition.lock()
        while queue.isEmpty && rendezvous == nil && producers != 0 { condition.wait() }
        let envelope: CampaignEnvelope<I>?
        if capacity == 0 { envelope = rendezvous; rendezvous = nil }
        else { envelope = queue.isEmpty ? nil : queue.removeFirst() }
        if let envelope {
            probe?.recordLocked(.received, workerID: envelope.workerID, jobID: envelope.report.jobID)
        }
        condition.broadcast(); condition.unlock()
        return envelope?.report
    }
}

private final class CampaignWorkerResult: @unchecked Sendable {
    private let mutex = NSLock()
    private var message: String?
    func failed(_ error: String) { mutex.lock(); message = error; mutex.unlock() }
    func failure() -> String? { mutex.lock(); defer { mutex.unlock() }; return message }
}

#if canImport(Darwin) || canImport(Glibc)
private final class CampaignThreadContext: Sendable {
    let body: @Sendable () -> Void
    init(_ body: @escaping @Sendable () -> Void) { self.body = body }
}
private func campaignThreadEntry(_ raw: UnsafeMutableRawPointer?) -> UnsafeMutableRawPointer? {
    let context = Unmanaged<CampaignThreadContext>.fromOpaque(raw!).takeRetainedValue()
    context.body()
    return nil
}
// Pass the retained context through an integer address across the C shim.
// The context contains only an immutable Sendable closure. Successful creation
// transfers its retain to campaignThreadEntry; failure releases it in the caller.
// Separating pointer conversion avoids Swift 6.3.3's region-analysis crash in
// the previous function with the raw pointer shared across both control paths.
private func campaignSpawnRetained(_ address: UInt) throws -> pthread_t {
    #if canImport(Darwin)
    var thread: pthread_t?
    #else
    var thread = pthread_t()
    #endif
    let code = pthread_create(&thread, nil, campaignThreadEntry, UnsafeMutableRawPointer(bitPattern: address))
    guard code == 0 else { throw ModelError("pthread_create failed (\(code))") }
    #if canImport(Darwin)
    return thread!
    #else
    return thread
    #endif
}
private func campaignStartThread(_ body: @escaping @Sendable () -> Void) throws -> pthread_t {
    let address = UInt(bitPattern: Unmanaged.passRetained(CampaignThreadContext(body)).toOpaque())
    do { return try campaignSpawnRetained(address) }
    catch {
        Unmanaged<CampaignThreadContext>.fromOpaque(UnsafeMutableRawPointer(bitPattern: address)!).release()
        throw error
    }
}

#endif

private func campaignExecuteJob<S, I, O>(config: CampaignConfig64, control: CampaignControl,
    factory: (UInt64) throws -> CampaignJob<S, I, O>, id: UInt64,
    runner: (UInt64, CampaignJob<S, I, O>, FuzzConfig64, RunLimits) throws -> FuzzReport64<I>) -> JobReport64<I> {
    let seed = jobSeed(masterSeed: config.masterSeed, jobID: id)
    let fuzz = FuzzConfig64(seed: seed, cases: config.fuzz.cases, maxSteps: config.fuzz.maxSteps,
                          maxTransitions: config.fuzz.maxTransitions, mutationPercent: config.fuzz.mutationPercent)
    var metadata: Metadata?
    var outcome: JobOutcome64<I>
    let job: CampaignJob<S, I, O>
    do { job = try factory(id) }
    catch let panic as CampaignPanic {
        return JobReport64(jobID: id, seed: seed, config: fuzz, metadata: nil, outcome: .panicked(panic.description))
    } catch {
        return JobReport64(jobID: id, seed: seed, config: fuzz, metadata: nil, outcome: .factoryError(ModelError(String(describing: error))))
    }
    do {
        metadata = try job.metadata()
        do {
            let report = try runner(id, job, fuzz, control.remaining())
            outcome = .completed(report)
        }
        catch let panic as CampaignPanic { outcome = .panicked(panic.description) }
        catch { outcome = .modelError(ModelError(String(describing: error))) }
    } catch {
        // Rust metadata is infallible except for unwinding. Swift's explicit
        // throwing metadata callback maps this recoverable failure to Panicked.
        outcome = .panicked(String(describing: error))
    }
    do { try job.tearDown() }
    catch { outcome = .panicked(String(describing: error)) }
    withExtendedLifetime(job) {}
    return JobReport64(jobID: id, seed: seed, config: fuzz, metadata: metadata, outcome: outcome)
}

@discardableResult
private func campaignDeliver<I>(_ job: JobReport64<I>, coordinator: CampaignCoordinator,
                                 sink: (JobReport64<I>) throws -> Void) -> Bool {
    let effects = coordinator.apply(.receive(job.jobID))
    switch effects.first {
    case let .discard(id) where id == job.jobID: return false
    case let .deliver(id) where id == job.jobID:
        let error: String?
        do { try sink(job); error = nil }
        catch let panic as CampaignPanic { error = "campaign callback panicked: \(panic.description)" }
        catch let failure { error = String(describing: failure) }
        _ = coordinator.apply(.sinkReturned(id, error))
        return error == nil
    default: preconditionFailure("invalid campaign receipt effect")
    }
}

func runCampaignUsing64<S, I: Sendable, O>(config: CampaignConfig64, limits: CampaignLimits,
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>,
    sink: (JobReport64<I>) throws -> Void, hooks: CampaignWorkerHooks = CampaignWorkerHooks(),
    runner: @escaping @Sendable (UInt64, CampaignJob<S, I, O>, FuzzConfig64, RunLimits) throws -> FuzzReport64<I>) throws -> CampaignReport64 {
    try config.validate()
    #if !canImport(Darwin) && !canImport(Glibc)
    guard config.workers == 1 else { throw ModelError("multiple campaign workers are unavailable on this Swift host") }
    #endif
    let control = try CampaignControl(limits)
    let coordinator = try CampaignCoordinator(config)
    if let stop = control.stop() { _ = coordinator.apply(.claim(stop)) }
    else if config.workers == 1 {
        while let id = coordinator.claim(control) {
            let job = campaignExecuteJob(config: config, control: control, factory: factory, id: id, runner: runner)
            coordinator.complete(job)
            if !campaignDeliver(job, coordinator: coordinator, sink: sink) { break }
        }
    } else {
        #if canImport(Darwin) || canImport(Glibc)
        let channel = CampaignChannel<I>(capacity: Int(config.resultBuffer), probe: hooks.channelProbe)
        var workers: [(pthread_t, CampaignWorkerResult)] = []
        for index in 0..<Int(min(config.workers, config.jobs)) {
            let result = CampaignWorkerResult()
            channel.addProducer()
            do {
                try hooks.beforeStart(index)
                let thread = try campaignStartThread {
                    defer { channel.producerDone() }
                    do {
                        while let id = coordinator.claim(control) {
                            hooks.channelProbe?.record(.claimed, workerID: index, jobID: id)
                            try hooks.afterClaim(id)
                            let job = campaignExecuteJob(config: config, control: control, factory: factory, id: id, runner: runner)
                            coordinator.complete(job) // before any blocking send
                            hooks.afterComplete(id)
                            channel.send(job, workerID: index)
                            hooks.channelProbe?.record(.sendReturned, workerID: index, jobID: id)
                        }
                        try hooks.beforeExit(index)
                    } catch { result.failed("campaign worker failed: \(error)") }
                }
                workers.append((thread, result))
            } catch {
                channel.producerDone()
                _ = coordinator.apply(.workerFailed("unable to start campaign worker: \(error)", lostWork: false))
                break
            }
        }
        channel.producerDone()
        while let job = channel.receive() { campaignDeliver(job, coordinator: coordinator, sink: sink) }
        // A worker's missing outcome is observed after draining, not eagerly;
        // surviving workers may have claimed further jobs in the meantime.
        for (index, worker) in workers.enumerated() {
            let (thread, result) = worker
            let code = pthread_join(thread, nil)
            if code == 0 { hooks.channelProbe?.joined(index) }
            if code != 0 {
                _ = coordinator.apply(.workerFailed("unable to join campaign worker (\(code))", lostWork: true))
            } else if let error = result.failure() {
                _ = coordinator.apply(.workerFailed(error, lostWork: true))
            }
        }
        #endif
    }
    return coordinator.finish()
}
