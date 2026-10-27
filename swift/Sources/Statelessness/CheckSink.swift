/// Private storage is moved back to its owner after dispatch, including on error.
/// Invalid active operations poison this batch even if the checker catches them.
private final class CheckStorage {
    var values: [Check] = []
    var failure: Error?
}
/// Synchronous append-only leased view. Never retain or use concurrently.
/// Every operation throws after its callback returns; no Sendable/thread-safety
/// guarantee is provided. Inspection returns detached copy-on-write value arrays.
public final class CheckSink {
    private var owner: CheckStorage?
    private var storage: CheckStorage { owner! }
    private var active = true
    private init(_ storage: CheckStorage) { self.owner = storage }
    private func requireActive() throws {
        guard active else { throw ModelError("check sink lease expired") }
        if let failure = storage.failure { throw failure }
    }
    private func reject(_ error: Error) throws -> Never {
        if storage.failure == nil { storage.failure = error }
        let failure = storage.failure!
        active = false; owner = nil
        throw failure
    }
    public func push(_ check: Check) throws {
        try requireActive()
        do { try check.validate() } catch { try reject(error) }
        storage.values.append(check)
    }
    public func extend<Values: Sequence>(_ values: Values) throws where Values.Element == Check {
        try requireActive()
        for value in values { try push(value) }
    }
    public func reserve(_ additional: Int) throws {
        try requireActive()
        guard additional >= 0 else { try reject(ModelError("check sink reserve must be nonnegative")) }
        let (capacity, overflow) = storage.values.count.addingReportingOverflow(additional)
        guard !overflow else { try reject(ModelError("check sink reserve overflow")) }
        storage.values.reserveCapacity(capacity)
    }
    public var count: Int { get throws { try requireActive(); return storage.values.count } }
    public var isEmpty: Bool { get throws { try requireActive(); return storage.values.isEmpty } }
    public func snapshot() throws -> [Check] { try requireActive(); return storage.values }
    /// Each nested model/oracle/reference callback receives a separate lease.
    /// Ending a successful child does not revoke the still-active outer view.
    func sublease(_ callback: (CheckSink) throws -> Void) throws {
        try requireActive()
        let child = CheckSink(storage)
        defer { child.active = false; child.owner = nil }
        do { try callback(child) } catch { throw storage.failure ?? error }
        if let failure = storage.failure { throw failure }
    }
    static func withStorage(_ checks: inout [Check], _ callback: (CheckSink) throws -> Void) throws {
        let storage = CheckStorage()
        swap(&checks, &storage.values)
        let root = CheckSink(storage)
        defer { root.active = false; root.owner = nil; swap(&checks, &storage.values) }
        do { try callback(root) } catch { throw storage.failure ?? error }
        if let failure = storage.failure { throw failure }
    }
}
func appendStateChecks<S, I, O>(_ model: Model<S, I, O>, _ state: S, into sink: CheckSink, stage: String = "state check") throws {
    try call(stage) {
        try sink.sublease { child in
            let snapshot = try copied(model.cloneState, state)
            if let callback = model.checkStateInto { try callback(snapshot, child) }
            else { try child.extend(model.checkState(snapshot)) }
        }
    }
}
func appendTransitionChecks<S, I, O>(_ model: Model<S, I, O>, _ before: S, _ input: I, _ transition: Transition<S, O>, into sink: CheckSink) throws {
    guard model.checkTransitionInto != nil || model.checkTransition != nil else { return }
    try call("transition check") {
        try sink.sublease { child in
            let state = try copied(model.cloneState, before), input = try copied(model.cloneInput, input), turn = try cloneTransition(model, transition)
            if let callback = model.checkTransitionInto { try callback(state, input, turn, child) }
            else if let callback = model.checkTransition { try child.extend(callback(state, input, turn)) }
        }
    }
}
func stateChecksInto<S, I, O>(_ model: Model<S, I, O>, _ state: S, checks: inout [Check], stage: String = "state check") throws {
    try CheckSink.withStorage(&checks) { try appendStateChecks(model, state, into: $0, stage: stage) }
}
func edgeChecksInto<S, I, O>(_ model: Model<S, I, O>, _ before: S, _ input: I, _ transition: Transition<S, O>, checks: inout [Check]) throws {
    try CheckSink.withStorage(&checks) { try appendTransitionChecks(model, before, input, transition, into: $0) }
}
/// Append one state-check batch to existing storage without clearing its prefix.
public func checkStateInto<S, I, O>(_ model: Model<S, I, O>, state: S, checks: inout [Check]) throws {
    try stateChecksInto(model, state, checks: &checks)
}
/// Append one transition-check batch; never executes the reducer.
public func checkTransitionInto<S, I, O>(_ model: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, checks: inout [Check]) throws {
    try edgeChecksInto(model, before, input, transition, checks: &checks)
}
