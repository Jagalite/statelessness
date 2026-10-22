import Foundation
@testable import Statelessness

// Rust-compatible test evidence within the native Int count domain only.
// Rust64 usize permits counts > Int.max; those require a separate wire model
// and are explicitly unsupported here, never narrowed. This is not queue resume.
// src/campaign/lifecycle/codec.rs defines the byte layout; the properties below
// independently inspect protocol facts without calling CampaignLifecycle.apply.
func campaignCheck(_ id: String, _ passed: Bool) -> Check {
    passed ? .passed(id) : .failed(id, "campaign lifecycle contract violated")
}

func campaignSummaryValid(_ summary: CampaignSummary) -> Bool {
    switch summary.outcome {
    case .factoryError, .modelError, .panicked:
        return !summary.failure && summary.transitions == 0 && summary.cases == 0 && summary.skippedChecks == 0
    default: return summary.failure == (summary.outcome == .failure)
    }
}

func campaignStateChecks(_ core: CampaignLifecycle, _ state: CampaignLifecycleState) -> [Check] {
    let r = state.report
    let callback: Bool
    let worker: Bool
    switch r.termination {
    case .callbackError: callback = true; worker = false
    case .workerError: callback = false; worker = true
    default: callback = false; worker = false
    }
    let reason: Bool
    if callback {
        reason = state.stop.map { [.sink, .worker, .cancelled, .deadline].contains($0) } ?? false
    } else if worker {
        reason = state.stop.map { [.worker, .cancelled, .deadline].contains($0) } ?? false
    } else if !state.finalized {
        reason = r.termination == .jobsCompleted && state.stop != .sink && state.stop != .worker
    } else {
        switch (state.stop, r.termination) {
        case (nil, .jobsCompleted), (.failure, .failureFound), (.cancelled, .cancelled), (.deadline, .deadline): reason = true
        default: reason = false
        }
    }
    let failureObserved = r.failures > 0 || state.pending.values.contains {
        if case let .ready(summary) = $0 { return summary.failure }; return false
    }
    func claimed(_ id: UInt64) -> Bool {
        id >= core.firstJob && r.started >= 0 && id - core.firstJob < UInt64(r.started)
    }
    let countsNonnegative = [r.requested, r.started, r.finished, r.notStarted, r.delivered, r.failures, r.errors].allSatisfy { $0 >= 0 }
    let accounting = countsNonnegative && r.requested == core.jobs && r.started <= r.requested
        && r.notStarted == r.requested - r.started && r.finished <= r.started
        && r.delivered <= r.finished && r.failures <= r.finished && r.errors <= r.finished - r.failures
    let pending = r.started >= r.finished && r.finished >= 0 && r.started - r.finished == state.pending.count
        && state.pending.allSatisfy { id, value in
            guard claimed(id) else { return false }
            if case let .ready(summary) = value { return campaignSummaryValid(summary) }
            return true
        }
    let deliveryCount = callback ? r.finished > r.delivered
        : (r.finished >= r.delivered && r.delivered >= 0 && r.finished - r.delivered == (state.delivering == nil ? 0 : 1))
    let deliveryID = state.delivering.map {
        state.pending[$0] == nil && claimed($0) && r.delivered < r.finished && !callback
    } ?? true
    return [
        campaignCheck("campaign.accounting", accounting),
        campaignCheck("campaign.pending", pending),
        campaignCheck("campaign.delivery", deliveryCount && deliveryID),
        campaignCheck("campaign.finalized", !state.finalized || (state.delivering == nil
            && (state.pending.isEmpty || (state.lostWorker && !r.accountingComplete))
            && (r.termination != .jobsCompleted || r.started == r.requested))),
        campaignCheck("campaign.termination", reason && (!state.lostWorker || ((callback || worker) && !r.accountingComplete))
            && (state.stop != .failure || (core.stopOnFailure && failureObserved)))
    ]
}

func campaignTransitionChecks(_ core: CampaignLifecycle, _ before: CampaignLifecycleState,
                              _ input: CampaignLifecycleInput,
                              _ transition: Transition<CampaignLifecycleState, CampaignLifecycleOutput>) -> [Check] {
    let after = transition.state, accepted = transition.disposition == .accepted
    let enabled: Bool
    switch input {
    case .claim: enabled = before.stop == nil && before.report.started < core.jobs
    case let .complete(id, summary): enabled = campaignSummaryValid(summary) && before.pending[id] == .running
    case let .receive(id):
        if case .ready? = before.pending[id] { enabled = before.delivering == nil } else { enabled = false }
    case let .sinkReturned(id, _): enabled = before.delivering == id
    case .workerFailed: enabled = true
    case .finish:
        // This wire-state check has no event history. The audited fixture also
        // checks Finish against its independent claim/receipt/sink-return ledger
        // and observed lost-work events, including when this flag is corrupted.
        enabled = before.delivering == nil && (before.pending.isEmpty || before.lostWorker)
            && (before.stop != nil || before.report.started == core.jobs)
    }
    let observesStop: Bool
    switch input {
    case .claim(.some), .sinkReturned(_, .some), .workerFailed: observesStop = true
    case let .complete(_, summary):
        observesStop = summary.outcome == .cancelled || summary.outcome == .deadline
            || (core.stopOnFailure && summary.outcome == .failure)
    default: observesStop = false
    }
    let claim: Bool, received: Bool, delivered: Bool
    if case .claim(nil) = input { claim = accepted } else { claim = false }
    if case .receive = input { received = accepted } else { received = false }
    if case .sinkReturned(_, nil) = input { delivered = accepted } else { delivered = false }
    var expected: [CampaignLifecycleOutput] = []
    if claim, let started = UInt64(exactly: before.report.started) {
        let id = core.firstJob.addingReportingOverflow(started)
        if !id.overflow { expected = [.runJob(id.partialValue)] }
    } else if received, case let .receive(id) = input {
        if case .callbackError = before.report.termination { expected = [.discard(id)] }
        else { expected = [.deliver(id)] }
    }
    func incremented(_ old: Int, _ delta: Bool, _ new: Int) -> Bool {
        let sum = old.addingReportingOverflow(delta ? 1 : 0)
        return !sum.overflow && sum.partialValue == new
    }
    let firstSinkError: Bool
    if case .callbackError = before.report.termination { firstSinkError = after.report.termination == before.report.termination }
    else { firstSinkError = true }
    return [
        campaignCheck("campaign.admission", accepted == (!before.finalized && enabled)),
        campaignCheck("campaign.stop_is_observed", (after.stop != nil) == (before.stop != nil || (accepted && observesStop))),
        campaignCheck("campaign.rejection_is_inert", accepted || (after == before && transition.outputs.isEmpty)),
        campaignCheck("campaign.claim_once", incremented(before.report.started, claim, after.report.started)
            && (!claim || (before.stop == nil && before.report.started < core.jobs))),
        campaignCheck("campaign.receive_once", incremented(before.report.finished, received, after.report.finished)),
        campaignCheck("campaign.deliver_once", incremented(before.report.delivered, delivered, after.report.delivered)),
        campaignCheck("campaign.effects", transition.outputs == expected),
        campaignCheck("campaign.first_sink_error", firstSinkError),
        campaignCheck("campaign.accounting_loss_is_sticky", before.report.accountingComplete || !after.report.accountingComplete),
        campaignCheck("campaign.finalization_is_terminal", !before.finalized || after == before)
    ]
}

struct CampaignLifecycleTestCodec {
    let core: CampaignLifecycle
    var duplicateDelivery = false

    var metadata: Metadata {
        Metadata(name: "stateless-campaign:\(core.firstJob):\(core.jobs):\(core.stopOnFailure)",
                 modelVersion: 1, propertiesVersion: 2, codecVersion: 1,
                 build: "native-swift-campaign-fixture-v1" + (duplicateDelivery ? ":duplicate-delivery" : ""))
    }

    var codec: Codec<CampaignLifecycleState, CampaignLifecycleInput, CampaignLifecycleOutput> {
        Codec(metadata: { metadata }, encodeState: encodeState, decodeState: decodeState,
              encodeInput: encodeInput, decodeInput: decodeInput, encodeOutput: encodeOutput)
    }

    func encodeState(_ state: CampaignLifecycleState) throws -> [UInt8] {
        var out = CampaignWireWriter()
        let r = state.report
        for count in [r.requested, r.started, r.finished, r.notStarted, r.delivered, r.failures, r.errors] { try out.count(count) }
        out.number(r.reportedTransitions); out.number(r.reportedCases); out.number(r.reportedSkippedChecks)
        out.boolean(r.accountingComplete)
        switch r.termination {
        case .jobsCompleted: out.tag(0)
        case .failureFound: out.tag(1)
        case .cancelled: out.tag(2)
        case .deadline: out.tag(3)
        case let .callbackError(error): out.tag(4); try out.string(error)
        case let .workerError(error): out.tag(5); try out.string(error)
        }
        switch state.stop {
        case nil: out.tag(0)
        case .failure: out.tag(1)
        case .cancelled: out.tag(2)
        case .deadline: out.tag(3)
        case .sink: out.tag(4)
        case .worker: out.tag(5)
        }
        try out.count(state.pending.count)
        for id in state.pending.keys.sorted() {
            out.number(id)
            switch state.pending[id]! {
            case .running: out.tag(0)
            case let .ready(summary): out.tag(1); out.summary(summary)
            }
        }
        if let id = state.delivering { out.tag(1); out.number(id) } else { out.tag(0) }
        out.boolean(state.lostWorker); out.boolean(state.finalized)
        return out.bytes
    }

    func decodeState(_ bytes: [UInt8]) throws -> CampaignLifecycleState {
        var input = CampaignWireReader(bytes: bytes)
        var r = CampaignReport(requested: try input.count())
        r.started = try input.count(); r.finished = try input.count(); r.notStarted = try input.count()
        r.delivered = try input.count(); r.failures = try input.count(); r.errors = try input.count()
        r.reportedTransitions = try input.number(); r.reportedCases = try input.number(); r.reportedSkippedChecks = try input.number()
        r.accountingComplete = try input.boolean()
        switch try input.tag() {
        case 0: r.termination = .jobsCompleted
        case 1: r.termination = .failureFound
        case 2: r.termination = .cancelled
        case 3: r.termination = .deadline
        case 4: r.termination = .callbackError(try input.string())
        case 5: r.termination = .workerError(try input.string())
        default: throw campaignWireError()
        }
        let stop: CampaignLifecycleStop?
        switch try input.tag() {
        case 0: stop = nil
        case 1: stop = .failure
        case 2: stop = .cancelled
        case 3: stop = .deadline
        case 4: stop = .sink
        case 5: stop = .worker
        default: throw campaignWireError()
        }
        let count = try input.count()
        guard count <= core.jobs, count <= input.remaining / 9 else { throw campaignWireError() }
        var pending: [UInt64: CampaignLifecyclePending] = [:]
        var previous: UInt64?
        for _ in 0..<count {
            let id = try input.number()
            if let previous, id <= previous { throw campaignWireError() }
            previous = id
            switch try input.tag() {
            case 0: pending[id] = .running
            case 1: pending[id] = .ready(try input.summary())
            default: throw campaignWireError()
            }
        }
        let delivering: UInt64?
        switch try input.tag() {
        case 0: delivering = nil
        case 1: delivering = try input.number()
        default: throw campaignWireError()
        }
        let state = CampaignLifecycleState(report: r, stop: stop, pending: pending, delivering: delivering,
                                           lostWorker: try input.boolean(), finalized: try input.boolean())
        try input.finish()
        guard !campaignStateChecks(core, state).contains(where: { $0.status == .failed }) else {
            throw ModelError("decoded campaign state violates lifecycle properties")
        }
        return state
    }

    func encodeInput(_ input: CampaignLifecycleInput) throws -> [UInt8] {
        var out = CampaignWireWriter()
        switch input {
        case let .claim(stop): out.tag(0); out.tag(stop == nil ? 0 : (stop == .cancelled ? 1 : 2))
        case let .complete(id, summary): out.tag(1); out.number(id); out.summary(summary)
        case let .receive(id): out.tag(2); out.number(id)
        case let .sinkReturned(id, error):
            out.tag(3); out.number(id)
            if let error { out.tag(1); try out.string(error) } else { out.tag(0) }
        case let .workerFailed(error, lostWork): out.tag(4); try out.string(error); out.boolean(lostWork)
        case .finish: out.tag(5)
        }
        return out.bytes
    }

    func decodeInput(_ bytes: [UInt8]) throws -> CampaignLifecycleInput {
        var input = CampaignWireReader(bytes: bytes)
        let result: CampaignLifecycleInput
        switch try input.tag() {
        case 0:
            switch try input.tag() {
            case 0: result = .claim(nil)
            case 1: result = .claim(.cancelled)
            case 2: result = .claim(.deadline)
            default: throw campaignWireError()
            }
        case 1: result = .complete(try input.number(), try input.summary())
        case 2: result = .receive(try input.number())
        case 3:
            let id = try input.number()
            switch try input.tag() {
            case 0: result = .sinkReturned(id, nil)
            case 1: result = .sinkReturned(id, try input.string())
            default: throw campaignWireError()
            }
        case 4: result = .workerFailed(try input.string(), lostWork: try input.boolean())
        case 5: result = .finish
        default: throw campaignWireError()
        }
        try input.finish()
        return result
    }

    func encodeOutput(_ output: CampaignLifecycleOutput) throws -> [UInt8] {
        var out = CampaignWireWriter()
        switch output {
        case let .runJob(id): out.tag(0); out.number(id)
        case let .deliver(id): out.tag(1); out.number(id)
        case let .discard(id): out.tag(2); out.number(id)
        }
        return out.bytes
    }
}

private func campaignWireError() -> ModelError { ModelError("invalid campaign lifecycle encoding") }

private struct CampaignWireWriter {
    var bytes: [UInt8] = []
    mutating func tag(_ value: UInt8) { bytes.append(value) }
    mutating func boolean(_ value: Bool) { tag(value ? 1 : 0) }
    mutating func number(_ value: UInt64) {
        for shift in stride(from: 0, to: 64, by: 8) { tag(UInt8(truncatingIfNeeded: value >> shift)) }
    }
    mutating func count(_ value: Int) throws {
        guard let number = UInt64(exactly: value) else { throw campaignWireError() }
        self.number(number)
    }
    mutating func string(_ value: String) throws { try count(value.utf8.count); bytes.append(contentsOf: value.utf8) }
    mutating func summary(_ value: CampaignSummary) {
        switch value.outcome {
        case .completed: tag(0)
        case .failure: tag(1)
        case .cancelled: tag(2)
        case .deadline: tag(3)
        case .factoryError: tag(4)
        case .modelError: tag(5)
        case .panicked: tag(6)
        }
        boolean(value.failure); number(value.transitions); number(value.cases); number(value.skippedChecks)
    }
}

private struct CampaignWireReader {
    let bytes: [UInt8]
    var offset = 0
    var remaining: Int { bytes.count - offset }
    mutating func take(_ count: Int) throws -> ArraySlice<UInt8> {
        guard count >= 0, count <= remaining else { throw campaignWireError() }
        let begin = offset; offset += count
        return bytes[begin..<offset]
    }
    mutating func tag() throws -> UInt8 { try take(1).first! }
    mutating func boolean() throws -> Bool {
        switch try tag() { case 0: return false; case 1: return true; default: throw campaignWireError() }
    }
    mutating func number() throws -> UInt64 {
        let part = try take(8)
        var value: UInt64 = 0
        for (index, byte) in part.enumerated() { value |= UInt64(byte) << (index * 8) }
        return value
    }
    mutating func count() throws -> Int {
        guard let count = Int(exactly: try number()) else {
            throw ModelError("campaign lifecycle count exceeds native Int")
        }
        return count
    }
    mutating func string() throws -> String {
        let count = try count()
        let bytes = try take(count)
        guard let string = String(bytes: bytes, encoding: .utf8) else { throw campaignWireError() }
        return string
    }
    mutating func summary() throws -> CampaignSummary {
        let outcome: CampaignSummaryOutcome
        switch try tag() {
        case 0: outcome = .completed
        case 1: outcome = .failure
        case 2: outcome = .cancelled
        case 3: outcome = .deadline
        case 4: outcome = .factoryError
        case 5: outcome = .modelError
        case 6: outcome = .panicked
        default: throw campaignWireError()
        }
        return CampaignSummary(outcome: outcome, failure: try boolean(), transitions: try number(),
                               cases: try number(), skippedChecks: try number())
    }
    func finish() throws { guard remaining == 0 else { throw campaignWireError() } }
}
