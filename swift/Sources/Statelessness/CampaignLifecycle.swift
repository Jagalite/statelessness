// The campaign's decision core, shared by the executor and the test self-model.
// Threads, clocks, callbacks, and application reports belong to the executor.
// This state contains only protocol summaries and is mutated under its lock.

enum CampaignExternalStop: Equatable, Sendable { case cancelled, deadline }
enum CampaignLifecycleStop: Equatable, Sendable { case failure, cancelled, deadline, sink, worker }
enum CampaignSummaryOutcome: Equatable, Sendable {
    case completed, failure, cancelled, deadline, factoryError, modelError, panicked
}

struct CampaignSummary: Equatable, Sendable {
    var outcome: CampaignSummaryOutcome
    var failure: Bool
    var transitions: UInt64
    var cases: UInt64
    var skippedChecks: UInt64

    init(outcome: CampaignSummaryOutcome, failure: Bool = false, transitions: UInt64 = 0,
         cases: UInt64 = 0, skippedChecks: UInt64 = 0) {
        self.outcome = outcome
        self.failure = failure
        self.transitions = transitions
        self.cases = cases
        self.skippedChecks = skippedChecks
    }

    var isValid: Bool {
        switch outcome {
        case .factoryError, .modelError, .panicked:
            return !failure && transitions == 0 && cases == 0 && skippedChecks == 0
        default:
            return failure == (outcome == .failure)
        }
    }
}

enum CampaignLifecyclePending: Equatable, Sendable {
    case running
    case ready(CampaignSummary)
}

struct CampaignLifecycleState: Equatable, Sendable {
    var report: CampaignReport
    var stop: CampaignLifecycleStop?
    var pending: [UInt64: CampaignLifecyclePending]
    var delivering: UInt64?
    var lostWorker: Bool
    var finalized: Bool
}

enum CampaignLifecycleInput: Equatable, Sendable {
    case claim(CampaignExternalStop?)
    case complete(UInt64, CampaignSummary)
    case receive(UInt64)
    case sinkReturned(UInt64, String?)
    case workerFailed(String, lostWork: Bool)
    case finish

    static func == (lhs: Self, rhs: Self) -> Bool {
        switch (lhs, rhs) {
        case let (.claim(a), .claim(b)): return a == b
        case let (.complete(a, x), .complete(b, y)): return a == b && x == y
        case let (.receive(a), .receive(b)): return a == b
        case let (.sinkReturned(a, x), .sinkReturned(b, y)):
            guard a == b else { return false }
            switch (x, y) {
            case (nil, nil): return true
            case let (.some(x), .some(y)): return exactText(x, y)
            default: return false
            }
        case let (.workerFailed(x, a), .workerFailed(y, b)): return a == b && exactText(x, y)
        case (.finish, .finish): return true
        default: return false
        }
    }
}

enum CampaignLifecycleOutput: Equatable, Sendable {
    case runJob(UInt64), deliver(UInt64), discard(UInt64)
}

struct CampaignLifecycle: Sendable {
    let firstJob: UInt64
    let jobs: Int
    let stopOnFailure: Bool

    init(config: CampaignConfig) {
        firstJob = config.firstJob
        jobs = config.jobs
        stopOnFailure = config.stopOnFailure
    }

    func initialState() throws -> CampaignLifecycleState {
        guard jobs > 0, let lastOffset = UInt64(exactly: jobs - 1),
              !firstJob.addingReportingOverflow(lastOffset).overflow else {
            throw ModelError("invalid campaign lifecycle job range")
        }
        return CampaignLifecycleState(report: CampaignReport(requested: jobs), stop: nil,
                                      pending: [:], delivering: nil, lostWorker: false, finalized: false)
    }

    // nil is an inert rejection. Every admission check precedes mutation.
    // Call only with a state initialized by this lifecycle (or its successors).
    func apply(_ state: inout CampaignLifecycleState, _ input: CampaignLifecycleInput)
        -> [CampaignLifecycleOutput]? {
        guard !state.finalized else { return nil }
        switch input {
        case let .claim(stop):
            // A late control sample cannot rewrite an already observed stop,
            // or a campaign whose entire range has already been claimed.
            guard state.stop == nil, state.report.started != jobs else { return nil }
            if let stop {
                state.stop = stop == .cancelled ? .cancelled : .deadline
                return []
            }
            let id = firstJob + UInt64(state.report.started)
            state.pending[id] = .running
            state.report.started += 1
            state.report.notStarted -= 1
            return [.runJob(id)]

        case let .complete(id, summary):
            guard summary.isValid, state.pending[id] == .running else { return nil }
            state.pending[id] = .ready(summary)
            switch summary.outcome {
            case .cancelled: state.stop = .cancelled
            case .deadline where state.stop != .cancelled: state.stop = .deadline
            case .failure where stopOnFailure && state.stop == nil: state.stop = .failure
            default: break
            }
            return []

        case let .receive(id):
            guard case let .ready(summary)? = state.pending[id], state.delivering == nil else { return nil }
            state.pending.removeValue(forKey: id)
            state.report.finished += 1
            if summary.failure { state.report.failures += 1 }
            switch summary.outcome {
            case .factoryError: state.report.errors += 1
            case .modelError, .panicked:
                state.report.errors += 1
                state.report.accountingComplete = false
            default: break
            }
            let transitions = state.report.reportedTransitions.addingReportingOverflow(summary.transitions)
            let cases = state.report.reportedCases.addingReportingOverflow(summary.cases)
            let skipped = state.report.reportedSkippedChecks.addingReportingOverflow(summary.skippedChecks)
            state.report.reportedTransitions = transitions.overflow ? .max : transitions.partialValue
            state.report.reportedCases = cases.overflow ? .max : cases.partialValue
            state.report.reportedSkippedChecks = skipped.overflow ? .max : skipped.partialValue
            if transitions.overflow || cases.overflow || skipped.overflow { state.report.accountingComplete = false }
            if case .callbackError = state.report.termination { return [.discard(id)] }
            state.delivering = id
            return [.deliver(id)]

        case let .sinkReturned(id, error):
            guard state.delivering == id else { return nil }
            state.delivering = nil
            if let error {
                state.report.termination = .callbackError(error)
                state.stop = .sink
            } else {
                state.report.delivered += 1
            }
            return []

        case let .workerFailed(error, lostWork):
            if case .callbackError = state.report.termination {} else {
                state.report.termination = .workerError(error)
            }
            state.stop = .worker
            state.lostWorker = state.lostWorker || lostWork
            if lostWork { state.report.accountingComplete = false }
            return []

        case .finish:
            guard state.delivering == nil,
                  state.pending.isEmpty || state.lostWorker,
                  state.stop != nil || state.report.started == jobs else { return nil }
            if state.report.started != state.report.finished { state.report.accountingComplete = false }
            switch state.report.termination {
            case .callbackError, .workerError: break
            default:
                switch state.stop {
                case .cancelled: state.report.termination = .cancelled
                case .deadline: state.report.termination = .deadline
                case .failure: state.report.termination = .failureFound
                default: state.report.termination = .jobsCompleted
                }
            }
            state.finalized = true
            return []
        }
    }
}
