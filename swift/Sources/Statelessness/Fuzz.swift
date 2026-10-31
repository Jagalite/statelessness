import Foundation
import Dispatch

/// An unsigned 128-bit duration in nanoseconds, represented without floating-point rounding.
public struct DurationNanoseconds: Equatable, Sendable {
    public let high: UInt64
    public let low: UInt64
    public init(high: UInt64 = 0, low: UInt64) { self.high = high; self.low = low }
    public var decimal: String {
        var high = self.high, low = self.low, digits: [UInt8] = []
        repeat {
            let quotientHigh = high / 10, remainder = high % 10
            let divided = UInt64(10).dividingFullWidth((high: remainder, low: low))
            digits.append(UInt8(divided.remainder) + 48)
            high = quotientHigh; low = divided.quotient
        } while high != 0 || low != 0
        return String(decoding: digits.reversed(), as: UTF8.self)
    }
}

/// Cooperative limits checked between callbacks. Callbacks cannot be interrupted
/// and their hidden allocations are outside these bounds. Cancellation wins over
/// a simultaneous deadline. A nil duration never accesses the clock.
public struct RunLimits {
    public let maxDuration: TimeInterval?
    public let maxDurationNanoseconds: DurationNanoseconds?
    public let isCancelled: () -> Bool
    public let cancellationEnabled: Bool
    public init(maxDuration: TimeInterval? = nil, isCancelled: (() -> Bool)? = nil) { self.maxDuration = maxDuration; self.maxDurationNanoseconds = nil; self.cancellationEnabled = isCancelled != nil; self.isCancelled = isCancelled ?? { false } }
    /// Integer monotonic deadline. Clock resolution and callback duration remain platform dependent.
    public init(maxDurationNanoseconds: DurationNanoseconds, isCancelled: (() -> Bool)? = nil) { self.maxDuration = nil; self.maxDurationNanoseconds = maxDurationNanoseconds; self.cancellationEnabled = isCancelled != nil; self.isCancelled = isCancelled ?? { false } }
}
/// Tracks elapsed monotonic ticks across a single UInt64 clock wrap between polls.
public final class IntegerDeadlineClock: @unchecked Sendable {
    private let lock = NSLock()
    private let now: () -> UInt64
    private var last: UInt64
    private var high: UInt64 = 0
    private var low: UInt64 = 0
    private var saturated = false
    public convenience init() { self.init(now: { DispatchTime.now().uptimeNanoseconds }) }
    init(now: @escaping () -> UInt64) { self.now = now; last = now() }
    private func sample() {
        let current = now(), delta = current &- last
        last = current
        let (next, carry) = low.addingReportingOverflow(delta)
        low = next
        if carry { let (newHigh, overflow) = high.addingReportingOverflow(1); high = newHigh; saturated = saturated || overflow }
    }
    public func remaining(_ duration: DurationNanoseconds) -> DurationNanoseconds {
        lock.lock(); defer { lock.unlock() }
        sample()
        if saturated || high > duration.high || (high == duration.high && low >= duration.low) { return DurationNanoseconds(low: 0) }
        let (difference, borrow) = duration.low.subtractingReportingOverflow(low)
        return DurationNanoseconds(high: duration.high - high - (borrow ? 1 : 0), low: difference)
    }
    public func reached(_ duration: DurationNanoseconds) -> Bool {
        let left = remaining(duration)
        return left.high == 0 && left.low == 0
    }
}
struct RunControl {
    let limits: RunLimits
    let started: TimeInterval?
    let integerClock: IntegerDeadlineClock?
    init(_ limits: RunLimits, integerClock overrideClock: IntegerDeadlineClock? = nil) throws {
        if let n = limits.maxDuration, !n.isFinite || n < 0 { throw ConfigError("invalid run duration") }
        self.limits = limits; started = limits.maxDuration == nil ? nil : ProcessInfo.processInfo.systemUptime
        integerClock = limits.maxDurationNanoseconds == nil ? nil : (overrideClock ?? IntegerDeadlineClock())
    }
    func stop() -> String? {
        if limits.isCancelled() { return "cancelled" }
        if let integerClock, let duration = limits.maxDurationNanoseconds, integerClock.reached(duration) { return "deadline" }
        if let started, let n = limits.maxDuration, ProcessInfo.processInfo.systemUptime - started >= n { return "deadline" }
        return nil
    }
    func remainingLimits() -> RunLimits {
        if let clock = integerClock, let duration = limits.maxDurationNanoseconds {
            return RunLimits(maxDurationNanoseconds: clock.remaining(duration), isCancelled: limits.cancellationEnabled ? limits.isCancelled : nil)
        }
        let duration = started.flatMap { start in limits.maxDuration.map { max(0, $0 - (ProcessInfo.processInfo.systemUptime - start)) } }
        return RunLimits(maxDuration: duration, isCancelled: limits.cancellationEnabled ? limits.isCancelled : nil)
    }
}
func searchFindings(_ phase: String, _ checks: [Check], skipped: inout UInt64) throws -> [Violation] {
    let n = UInt64(checks.filter { $0.status == .skipped }.count)
    let (total, overflow) = skipped.addingReportingOverflow(n)
    skipped = overflow ? UInt64.max : total
    return checks.filter { $0.status == .failed }.map { Violation(phase: phase, check: $0) }
}
func searchInitial<S, I, O>(_ m: Model<S, I, O>) throws -> S {
    let value = try call("initial_state", m.initialState)
    return try copied(m.cloneState, value)
}
/// Observer payloads are defensive snapshots. Throwing observers abort the run
/// with context; they cannot replace model results. A list-returning checker
/// cannot expose checks emitted internally before throwing: its failed batch is
/// empty/incomplete. The preceding completed state batch remains observable.
public struct SearchObserver<S, I, O> {
    /// BFS requests full predecessor histories only when true. Existing fuzz
    /// observations always include their executed prefix, preserving search-v1.
    public var needsHistory: () -> Bool
    public var initial: (S, [Check]) throws -> Void
    public var checkError: (String, [Check], Bool) throws -> Void
    public var transition: (S, I, Transition<S, O>, [Check], [Check], [I]) throws -> Void
    public init(initial: @escaping (S, [Check]) throws -> Void = { _, _ in },
                checkError: @escaping (String, [Check], Bool) throws -> Void = { _, _, _ in },
                transition: @escaping (S, I, Transition<S, O>, [Check], [Check], [I]) throws -> Void = { _, _, _, _, _, _ in },
                needsHistory: @escaping () -> Bool = { false }) {
        self.initial = initial; self.checkError = checkError; self.transition = transition
        self.needsHistory = needsHistory
    }
}
private func observerCheckError<S, I, O>(_ observer: SearchObserver<S, I, O>?, phase: String, checks: [Check], complete: Bool, checkerError: Error) throws {
    guard let observer else { return }
    do { try observer.checkError(phase, checks, complete) }
    catch { throw ModelError("\(checkerError); search observer check_error: \(error)") }
}
final class SearchCheckStorage { var checks: [Check] = [] }
func searchInitialChecks<S, I, O>(_ m: Model<S, I, O>, state: S, skipped: inout UInt64, observer: SearchObserver<S, I, O>? = nil, storage: SearchCheckStorage? = nil) throws -> [Violation] {
    let storage = storage ?? SearchCheckStorage()
    // Preserve the observer's initial observation before user checks can mutate
    // an external alias. State-check errors retain precedence over capture errors.
    let checked = observer.map { _ in Result { try copied(m.cloneState, state) } }
    let observed = observer.map { _ in Result { try copied(m.cloneState, state) } }
    storage.checks.removeAll(keepingCapacity: true)
    do { try stateChecksInto(m, try checked?.get() ?? state, checks: &storage.checks, stage: "initial check_state") }
    catch { try observerCheckError(observer, phase: "initial_state", checks: storage.checks, complete: false, checkerError: error); throw error }
    if let observer, let observed { try notifyInitialObserver(observer, clone: m.cloneState, observed: observed, checks: storage.checks) }
    return try searchFindings("initial_state", storage.checks, skipped: &skipped)
}
// A separate function keeps the closure from capturing the whole model and a
// force-unwrapped optional, which crashes the Swift 6.0.3 SIL ownership verifier.
private func notifyInitialObserver<S, I, O>(_ observer: SearchObserver<S, I, O>, clone: (S) throws -> S, observed: Result<S, Error>, checks: [Check]) throws {
    try call("search observer initial") { try observer.initial(copied(clone, observed.get()), checks) }
}
func searchEdge<S, I, O>(_ m: Model<S, I, O>, state: S, input: I, transition: Transition<S, O>, skipped: inout UInt64, observer: SearchObserver<S, I, O>? = nil, history: [I] = [], storage: SearchCheckStorage? = nil, capturedTransition: Result<Transition<S,O>,Error>? = nil, capturedState: Result<S,Error>? = nil, capturedInput: Result<I,Error>? = nil, capturedBefore: Result<S,Error>? = nil) throws -> [Violation] {
    let storage = storage ?? SearchCheckStorage()
    // Capture the later edge observation before state-check callbacks can mutate
    // application aliases. Defer capture errors so state-check failures retain
    // their existing precedence. The no-edge/no-observer path needs no capture.
    // Captured values are detached snapshots owned by this call, taken by the
    // caller before any check callback; use them without another copy.
    let hasEdgeChecks = m.checkTransitionInto != nil || m.checkTransition != nil
    let edge: Result<(S,I,Transition<S,O>),Error>?
    if hasEdgeChecks || observer != nil {
        edge = Result {
            let i = try capturedInput.map { try $0.get() } ?? copied(m.cloneInput,input)
            let s = try capturedBefore.map { try $0.get() } ?? copied(m.cloneState,state)
            let t = try capturedTransition.map { try $0.get() } ?? cloneTransition(m,transition)
            return (s,i,t) }
    } else { edge = nil }
    // One detached prefix snapshot, taken before checks and handed to the
    // observer; no other consumer receives it.
    let prefix = observer.map { _ in Result { try history.map { try copied(m.cloneInput,$0) } } }
    storage.checks.removeAll(keepingCapacity: true)
    do {
        // A failed pre-history snapshot cannot fall back to the live value:
        // a history-copy callback may already have changed that alias.
        let checkState = try capturedState?.get() ?? transition.state
        try stateChecksInto(m, checkState, checks: &storage.checks, stage: "check_state")
    }
    catch { try observerCheckError(observer, phase: "state", checks: storage.checks, complete: false, checkerError: error); throw error }
    let stateCount = storage.checks.count
    do { if m.checkTransitionInto != nil || m.checkTransition != nil, let edge {
        let (s,i,t) = try edge.get(); try edgeChecksInto(m, s, i, t, checks: &storage.checks)
    } }
    catch {
        var notificationErrors: [String] = []
        if let observer {
            do { try observer.checkError("state", Array(storage.checks.prefix(stateCount)), true) }
            catch { notificationErrors.append(String(describing: error)) }
            do { try observer.checkError("transition", Array(storage.checks.dropFirst(stateCount)), false) }
            catch { notificationErrors.append(String(describing: error)) }
        }
        if !notificationErrors.isEmpty { throw ModelError("\(error); search observer check_error: \(notificationErrors.joined(separator: "; "))") }
        throw error
    }
    let sc = Array(storage.checks.prefix(stateCount)), ec = Array(storage.checks.dropFirst(stateCount))
    if let observer {
        try call("search observer transition") {
            let (s,i,t) = try edge!.get()
            // Transition checks received the edge values; give the observer its
            // own copies then. Otherwise the edge snapshot is unshared.
            if hasEdgeChecks { try observer.transition(copied(m.cloneState, s), copied(m.cloneInput, i), cloneTransition(m, t), sc, ec, try prefix!.get()) }
            else { try observer.transition(s, i, t, sc, ec, try prefix!.get()) }
        }
    }
    return try searchFindings("state", sc, skipped: &skipped) + searchFindings("transition", ec, skipped: &skipped)
}
public struct FuzzConfig64: Sendable {
    public let seed: UInt64
    public let cases: UInt64
    public let maxSteps: UInt64
    public let maxTransitions: UInt64
    public let mutationPercent: Int
    public init(seed: UInt64 = 0, cases: UInt64 = 1_000, maxSteps: UInt64 = 100, maxTransitions: UInt64 = 100_000, mutationPercent: Int = 50) {
        self.seed = seed; self.cases = cases; self.maxSteps = maxSteps; self.maxTransitions = maxTransitions; self.mutationPercent = mutationPercent
    }
}
public struct FuzzReport64<I> {
    public var termination: String
    public let seed: UInt64
    public var cases: UInt64
    public var transitions: UInt64
    public var skippedChecks: UInt64
    public var failure: Failure<I>?
    public var guidance: GuidanceReport64<I>? = nil
}
extension FuzzReport64: Sendable where I: Sendable {}
func validateFuzzConfig(_ config: FuzzConfig64) throws {
    guard (0...100).contains(config.mutationPercent) else { throw ConfigError("invalid fuzz configuration") }
}
/// Seeded, state-aware sequence fuzzing. The sole mutation corpus is the previous
/// passing case. Completion establishes only absence of findings in these cases.
public func fuzz64<S, I, O>(_ g: Generate<S, I, O>, config: FuzzConfig64 = FuzzConfig64(), limits: RunLimits = RunLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport64<I> {
    try validateFuzzConfig(config)
    let control = try RunControl(limits)
    return try fuzzKernel(g, config: config, control: control, observer: observer, candidate: { previous, rng in
        guard !previous.isEmpty, rng.index(100)! < UInt64(config.mutationPercent) else { return ([], nil) }
        var mutated = try previous.map { try copied(g.model.cloneInput, $0) }
        let start = Int(rng.index(UInt64(mutated.count))!)
        let length = 1 + Int(rng.index(UInt64(mutated.count - start))!)
        mutated.removeSubrange(start..<(start + length))
        return (mutated, Int(rng.index(UInt64(mutated.count) + 1)!))
    })
}
/// One reducer/check/observer/control path. Ordinary fuzz keeps its established
/// polling behavior; feedback hooks add their own post-callback boundary.
func fuzzKernel<S, I, O>(_ g: Generate<S, I, O>, config: FuzzConfig64, control: RunControl,
    observer: SearchObserver<S, I, O>?, candidate: ([I], inout Rng) throws -> ([I], Int?),
    initialFeedback: ((S, UInt64) throws -> Void)? = nil,
    transitionFeedback: ((S, I, Transition<S, O>, [I], UInt64, UInt64) throws -> Void)? = nil,
    scanStop: (() -> String?)? = nil) throws -> FuzzReport64<I> {
    let m = g.model
    let checkStorage = SearchCheckStorage()
    var rng = Rng(seed: config.seed), previous: [I] = []
    var report = FuzzReport64<I>(termination: "cases_completed", seed: config.seed, cases: 0, transitions: 0, skippedChecks: 0, failure: nil)
    if let stop = control.stop() { report.termination = stop; return report }
    for _ in 0..<config.cases {
        if let stop = control.stop() { report.termination = stop; return report }
        var state = try searchInitial(m)
        report.cases += 1
        let retainedInitial = Result { try copied(m.cloneState, state) }
        let bad = try searchInitialChecks(m, state: state, skipped: &report.skippedChecks, observer: observer, storage: checkStorage)
        if !bad.isEmpty { report.termination = "failure_found"; report.failure = Failure(inputs: [], violations: bad); return report }
        if let stop = control.stop() { report.termination = stop; return report }
        if initialFeedback != nil || (config.maxSteps > 0 && report.transitions < config.maxTransitions) {
            state = try retainedInitial.get()
        }
        if let initialFeedback {
            let nextState = Result { try copied(m.cloneState, state) }
            try call("initial feedback") { try initialFeedback(state, report.cases) }
            if let stop = control.stop() { report.termination = stop; return report }
            if config.maxSteps > 0 && report.transitions < config.maxTransitions {
                state = try nextState.get()
            }
        }
        let (mutated, replacement) = try candidate(previous, &rng)
        var history: [I] = []
        for step in 0..<config.maxSteps {
            if report.transitions == config.maxTransitions { report.termination = "transition_limit"; return report }
            if let stop = control.stop() { report.termination = stop; return report }
            // Preserve a reused candidate before state clone hooks can mutate it.
            let reusedCapture: Result<I,Error>?
            if step < UInt64(mutated.count), replacement.map({ UInt64($0) }) != step {
                reusedCapture = Result { try copied(m.cloneInput, mutated[Int(step)]) }
            } else { reusedCapture = nil }
            // Keep execution state separate from admission callbacks, which may
            // mutate an external alias. Defer a failed copy until state is needed.
            let executionState = Result { try copied(m.cloneState, state) }
            let feedbackState = transitionFeedback.map { _ in Result { try copied(m.cloneState, state) } }
            var reused: I?
            if let reusedCapture {
                let retained = try reusedCapture.get()
                let enabled: Bool
                do { enabled = try call("is_enabled") {
                    let query = try copied(m.cloneInput, retained)
                    return try g.isEnabled(copied(m.cloneState, executionState.get()), query)
                }
                } catch { if let stop = scanStop?() { report.termination = stop; return report }; throw error }
                if enabled { reused = retained }
            }
            if let stop = control.stop() { report.termination = stop; return report }
            let next: I?
            if let reused { next = reused }
            else { do { next = try call("generate") { try g.generate(copied(m.cloneState, executionState.get()), &rng) } } catch { if let stop = scanStop?() { report.termination = stop; return report }; throw error } }
            if let stop = control.stop() { report.termination = stop; return report }
            guard let value = next else { break }
            let input = try copied(m.cloneInput, value)
            // Feedback runs only after enabled, step, checks and observers succeed.
            // Capture before those callbacks; defer an unused failure to feedback.
            let feedbackInput = transitionFeedback.map { _ in Result { try copied(m.cloneInput, input) } }
            var enabled = reused != nil
            if !enabled { do { enabled = try call("is_enabled") {
                let query = try copied(m.cloneInput, input)
                return try g.isEnabled(copied(m.cloneState, executionState.get()), query)
            }
            } catch { if let stop = scanStop?() { report.termination = stop; return report }; throw error } }
            if let stop = control.stop() { report.termination = stop; return report }
            guard enabled else { throw ModelError("generate returned a causally disabled input") }
            // Edge snapshots are taken only when transition checks or an
            // observer consume them.
            let needsEdge = observer != nil || m.checkTransitionInto != nil || m.checkTransition != nil
            let checkInput: Result<I,Error>? = needsEdge ? Result(catching: { try copied(m.cloneInput, input) }) : nil
            let checkBefore: Result<S,Error>? = needsEdge ? Result(catching: { try copied(m.cloneState, executionState.get()) }) : nil
            let t = try perform(m, executionState.get(), input, stage: "step")
            // Checks and observers may hold application aliases to the returned
            // state. Preserve the next iteration's state before invoking them,
            // but do not let an unused copy failure hide a violation or stop.
            let successor = Result { try copied(m.cloneState, t.state) }
            // State checking and later continuation are distinct consumers: a
            // failed, unused continuation copy must not stop the state check.
            let checkSuccessor = Result { try copied(m.cloneState, t.state) }
            // History input cloning can mutate an external transition alias.
            // Preserve the edge before invoking it; failures are consumed only
            // if transition checks or an observer require the edge.
            let checkTransition: Result<Transition<S,O>,Error>? = needsEdge ? Result(catching: { try cloneTransition(m, t) }) : nil
            let feedbackTransition = transitionFeedback.map { _ in Result { try cloneTransition(m, t) } }
            report.transitions += 1
            try requireNativeGrowth(history.count, elementStride: MemoryLayout<I>.stride, context: "fuzz history")
            history.append(try copied(m.cloneInput, input))
            let feedbackHistory = transitionFeedback.map { _ in Result { try history.map { try copied(m.cloneInput, $0) } } }
            let violations = try searchEdge(m, state: executionState.get(), input: input, transition: t, skipped: &report.skippedChecks, observer: observer, history: history, storage: checkStorage, capturedTransition: checkTransition, capturedState: checkSuccessor, capturedInput: checkInput, capturedBefore: checkBefore)
            if !violations.isEmpty { report.termination = "failure_found"; report.failure = Failure(inputs: history, violations: violations); return report }
            if let stop = control.stop() { report.termination = stop; return report }
            if let transitionFeedback {
                try call("transition feedback") { try transitionFeedback(feedbackState!.get(), feedbackInput!.get(), feedbackTransition!.get(), feedbackHistory!.get(), report.cases, report.transitions) }
                if let stop = control.stop() { report.termination = stop; return report }
            }
            // The next iteration checks the transition limit before using state.
            // Do not surface an unused copy failure at the end of the run.
            if step < config.maxSteps - 1 && report.transitions < config.maxTransitions {
                state = try successor.get()
            }
        }
        previous = history
    }
    return report
}
