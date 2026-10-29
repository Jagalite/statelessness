import Foundation

/// Cooperative limits checked between callbacks. Callbacks cannot be interrupted
/// and their hidden allocations are outside these bounds. Cancellation wins over
/// a simultaneous deadline. A nil duration never accesses the clock.
public struct RunLimits {
    public let maxDuration: TimeInterval?
    public let isCancelled: () -> Bool
    public init(maxDuration: TimeInterval? = nil, isCancelled: @escaping () -> Bool = { false }) { self.maxDuration = maxDuration; self.isCancelled = isCancelled }
}
struct RunControl {
    let limits: RunLimits
    let started: TimeInterval?
    init(_ limits: RunLimits) throws {
        if let n = limits.maxDuration, !n.isFinite || n < 0 { throw ConfigError("invalid run duration") }
        self.limits = limits; started = limits.maxDuration == nil ? nil : ProcessInfo.processInfo.systemUptime
    }
    func stop() -> String? {
        if limits.isCancelled() { return "cancelled" }
        if let started, let n = limits.maxDuration, ProcessInfo.processInfo.systemUptime - started >= n { return "deadline" }
        return nil
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
    storage.checks.removeAll(keepingCapacity: true)
    do { try stateChecksInto(m, state, checks: &storage.checks, stage: "initial check_state") }
    catch { try observerCheckError(observer, phase: "initial_state", checks: storage.checks, complete: false, checkerError: error); throw error }
    if let observer { try call("search observer initial") { try observer.initial(copied(m.cloneState, state), storage.checks) } }
    return try searchFindings("initial_state", storage.checks, skipped: &skipped)
}
func searchEdge<S, I, O>(_ m: Model<S, I, O>, state: S, input: I, transition: Transition<S, O>, skipped: inout UInt64, observer: SearchObserver<S, I, O>? = nil, history: [I] = [], storage: SearchCheckStorage? = nil) throws -> [Violation] {
    let storage = storage ?? SearchCheckStorage()
    storage.checks.removeAll(keepingCapacity: true)
    do { try stateChecksInto(m, transition.state, checks: &storage.checks, stage: "check_state") }
    catch { try observerCheckError(observer, phase: "state", checks: storage.checks, complete: false, checkerError: error); throw error }
    let stateCount = storage.checks.count
    do { try edgeChecksInto(m, state, input, transition, checks: &storage.checks) }
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
            let prefix = try history.map { try copied(m.cloneInput, $0) }
            try observer.transition(copied(m.cloneState, state), copied(m.cloneInput, input), cloneTransition(m, transition), sc, ec, prefix)
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
    transitionFeedback: ((S, I, Transition<S, O>, [I], UInt64, UInt64) throws -> Void)? = nil) throws -> FuzzReport64<I> {
    let m = g.model
    let checkStorage = SearchCheckStorage()
    var rng = Rng(seed: config.seed), previous: [I] = []
    var report = FuzzReport64<I>(termination: "cases_completed", seed: config.seed, cases: 0, transitions: 0, skippedChecks: 0, failure: nil)
    if let stop = control.stop() { report.termination = stop; return report }
    for _ in 0..<config.cases {
        if let stop = control.stop() { report.termination = stop; return report }
        var state = try searchInitial(m)
        report.cases += 1
        let bad = try searchInitialChecks(m, state: state, skipped: &report.skippedChecks, observer: observer, storage: checkStorage)
        if !bad.isEmpty { report.termination = "failure_found"; report.failure = Failure(inputs: [], violations: bad); return report }
        if let stop = control.stop() { report.termination = stop; return report }
        if let initialFeedback {
            try call("initial feedback") { try initialFeedback(state, report.cases) }
            if let stop = control.stop() { report.termination = stop; return report }
        }
        let (mutated, replacement) = try candidate(previous, &rng)
        var history: [I] = []
        for step in 0..<config.maxSteps {
            if report.transitions == config.maxTransitions { report.termination = "transition_limit"; return report }
            if let stop = control.stop() { report.termination = stop; return report }
            var reused: I?
            if step < UInt64(mutated.count), replacement.map({ UInt64($0) }) != step {
                let index = Int(step) // bounded by the materialized array
                let enabled = try call("is_enabled") { try g.isEnabled(copied(m.cloneState, state), copied(m.cloneInput, mutated[index])) }
                if enabled { reused = try copied(m.cloneInput, mutated[index]) }
            }
            if let stop = control.stop() { report.termination = stop; return report }
            let next: I?
            if let reused { next = reused }
            else { next = try call("generate") { try g.generate(copied(m.cloneState, state), &rng) } }
            if let stop = control.stop() { report.termination = stop; return report }
            guard let value = next else { break }
            let input = try copied(m.cloneInput, value)
            var enabled = reused != nil
            if !enabled { enabled = try call("is_enabled") { try g.isEnabled(copied(m.cloneState, state), copied(m.cloneInput, input)) } }
            if let stop = control.stop() { report.termination = stop; return report }
            guard enabled else { throw ModelError("generate returned a causally disabled input") }
            let t = try perform(m, state, input, stage: "step")
            report.transitions += 1
            try requireNativeGrowth(history.count, elementStride: MemoryLayout<I>.stride, context: "fuzz history")
            history.append(try copied(m.cloneInput, input))
            let violations = try searchEdge(m, state: state, input: input, transition: t, skipped: &report.skippedChecks, observer: observer, history: history, storage: checkStorage)
            if !violations.isEmpty { report.termination = "failure_found"; report.failure = Failure(inputs: history, violations: violations); return report }
            if let stop = control.stop() { report.termination = stop; return report }
            if let transitionFeedback {
                try call("transition feedback") { try transitionFeedback(state, input, t, history, report.cases, report.transitions) }
                if let stop = control.stop() { report.termination = stop; return report }
            }
            state = t.state
        }
        previous = history
    }
    return report
}
