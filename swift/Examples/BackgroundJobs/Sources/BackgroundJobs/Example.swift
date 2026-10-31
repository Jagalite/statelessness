import Statelessness

// The reducer owns job identity. The async host owns execution and feeds its
// observed completion back as an input; verification never performs the work.
struct State: Equatable {
    var active: Int? = nil
    var used: Set<Int> = []
    var acceptedStaleCompletion = false
}
enum Input: Equatable, Sendable { case start(Int), cancel, completed(Int) }
enum Effect: Sendable { case run(Int), cancel(Int) }

func reduce(_ state: State, _ input: Input, acceptStale: Bool = false) -> Transition<State, Effect> {
    var next = state
    switch input {
    case let .start(id):
        guard state.active == nil, !state.used.contains(id) else { return Transition(state: state, disposition: .ignored("busy or reused identifier")) }
        next.active = id
        next.used.insert(id)
        return Transition(state: next, outputs: [.run(id)])
    case .cancel:
        guard let id = state.active else { return Transition(state: state, disposition: .ignored("idle")) }
        next.active = nil
        return Transition(state: next, outputs: [.cancel(id)])
    case let .completed(id):
        guard state.active == id || acceptStale else { return Transition(state: state, disposition: .ignored("stale completion")) }
        next.acceptedStaleCompletion = state.active != id
        next.active = nil
        return Transition(state: next)
    }
}

func model(acceptStale: Bool) -> Model<State, Input, Effect> {
    Model(initialState: { State() }, step: { reduce($0, $1, acceptStale: acceptStale) },
          checkState: { [$0.acceptedStaleCompletion ? .failed("job.current", "accepted stale completion") : .passed("job.current")] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          inputs: { _ in InputIterator(values: [.start(1), .start(2), .cancel, .completed(1), .completed(2)]) },
          equalStates: { $0 == $1 })
}

// A controlled asynchronous operation that can finish despite cancellation,
// like an external service that has already accepted a request. The gate makes
// ordering deterministic without sleeps or relying on a scheduler race.
actor WorkGate {
    private var started = false
    private var startWaiter: CheckedContinuation<Void, Never>?
    private var workWaiter: CheckedContinuation<Void, Never>?
    func work() async {
        started = true
        startWaiter?.resume(); startWaiter = nil
        await withCheckedContinuation { workWaiter = $0 }
    }
    func waitUntilStarted() async {
        if started { return }
        await withCheckedContinuation { startWaiter = $0 }
    }
    func release() { workWaiter?.resume(); workWaiter = nil }
}

func perform(_ id: Int, gate: WorkGate) async -> (Input, Bool) {
    await gate.work()
    return (.completed(id), Task.isCancelled)
}

@main struct Example {
    static func main() async throws {
        let broken = try enumerateStates(model(acceptStale: true))
        guard broken.failure != nil else { throw ModelError("negative control did not find stale completion") }
        let fixed = try enumerateStates(model(acceptStale: false))
        guard fixed.failure == nil, fixed.termination == "graph_exhausted", fixed.skippedChecks == 0 else {
            throw ModelError("job model did not exhaust without a failure")
        }
        var state = State()
        let start = reduce(state, .start(1)); state = start.state
        guard case let .run(firstID)? = start.outputs.first else { throw ModelError("missing run effect") }
        let firstGate = WorkGate()
        let firstTask = Task { await perform(firstID, gate: firstGate) }
        await firstGate.waitUntilStarted()
        let cancel = reduce(state, .cancel); state = cancel.state
        guard case .cancel(firstID)? = cancel.outputs.first else { throw ModelError("missing cancellation effect") }
        firstTask.cancel()
        let second = reduce(state, .start(2)); state = second.state
        guard case let .run(secondID)? = second.outputs.first else { throw ModelError("missing second run effect") }
        let secondGate = WorkGate()
        let secondTask = Task { await perform(secondID, gate: secondGate) }
        await secondGate.waitUntilStarted()
        await firstGate.release()
        let (late, wasCancelled) = await firstTask.value
        let ignored = reduce(state, late); state = ignored.state
        guard wasCancelled, ignored.disposition == .ignored("stale completion"), state.active == secondID else {
            throw ModelError("late cancelled completion disturbed the active job")
        }
        await secondGate.release()
        let (completion, secondCancelled) = await secondTask.value
        state = reduce(state, completion).state
        guard !secondCancelled, state.active == nil, !state.acceptedStaleCompletion else { throw ModelError("host completion failed") }
        print("negative control found; fixed graph exhausted (\(fixed.states) states); cancelled task's late completion ignored")
    }
}
