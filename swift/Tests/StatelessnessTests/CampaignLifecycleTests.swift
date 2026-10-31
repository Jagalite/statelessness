import Foundation
import XCTest
@testable import Statelessness

private let campaignOutcomes: [CampaignSummaryOutcome] = [
    .completed, .failure, .cancelled, .deadline, .factoryError, .modelError, .panicked
]

private func campaignSummary(_ outcome: CampaignSummaryOutcome) -> CampaignSummary {
    let error = [.factoryError, .modelError, .panicked].contains(outcome)
    return CampaignSummary(outcome: outcome, failure: outcome == .failure,
                           transitions: error ? 0 : 2, cases: error ? 0 : 1, skippedChecks: error ? 0 : 1)
}

private func campaignComplete(_ id: UInt64, _ outcome: CampaignSummaryOutcome) -> CampaignLifecycleInput {
    .complete(id, campaignSummary(outcome))
}

private struct CampaignLedger: Equatable {
    var claims: Set<UInt64> = []
    var completions: [UInt64: CampaignSummary] = [:]
    var receipts: Set<UInt64> = []
    var attempts: Set<UInt64> = []
    var returns: Set<UInt64> = []
    var successes: Set<UInt64> = []
    var discards: Set<UInt64> = []
    var firstSinkError: ExactString?
    var lastWorkerError: ExactString?
    var cancelled = false
    var deadline = false
    var failureStop = false
    var lostWork = false
    var finalized = false
    var invalidProtocol = false
    var observedStop: Bool {
        cancelled || deadline || failureStop || firstSinkError != nil || lastWorkerError != nil
    }

    // A received report still blocks Finish until its sink call returns. Only
    // an observed lost-work event permits claims without corresponding receipts.
    // Neither an incomplete work total nor any core flag proves worker loss.
    func readyToFinish(jobs: UInt64) -> Bool {
        attempts == returns && (claims == receipts || lostWork)
            && (observedStop || UInt64(claims.count) == jobs)
    }
}

private struct CampaignAuditedState: Equatable {
    var core: CampaignLifecycleState
    var ledger = CampaignLedger()
}

private struct CampaignFixture {
    let core: CampaignLifecycle
    var duplicateDelivery = false
    var outcomes = campaignOutcomes

    init(jobs: Int = 2, stopOnFailure: Bool = false, firstJob: UInt64 = 50, duplicateDelivery: Bool = false) {
        core = CampaignLifecycle(config: CampaignConfig(firstJob: firstJob, jobs: jobs, stopOnFailure: stopOnFailure))
        self.duplicateDelivery = duplicateDelivery
    }
    var wire: CampaignLifecycleTestCodec { CampaignLifecycleTestCodec(core: core, duplicateDelivery: duplicateDelivery) }

    func alphabet() -> [CampaignLifecycleInput] {
        var inputs: [CampaignLifecycleInput] = [
            .claim(nil), .claim(.cancelled), .claim(.deadline),
            .workerFailed("worker", lostWork: false), .workerFailed("worker", lostWork: true), .finish
        ]
        for offset in 0...core.jobs {
            let id = core.firstJob.addingReportingOverflow(UInt64(offset))
            if id.overflow { continue }
            inputs += outcomes.map { campaignComplete(id.partialValue, $0) }
            inputs += [.receive(id.partialValue), .sinkReturned(id.partialValue, nil), .sinkReturned(id.partialValue, "sink")]
            // Syntactically valid but semantically incoherent Complete.
            inputs.append(.complete(id.partialValue, CampaignSummary(outcome: .failure)))
        }
        return inputs
    }

    func rejectionReason(_ before: CampaignLifecycleState, _ input: CampaignLifecycleInput) -> String {
        if before.finalized { return "campaign is finalized" }
        switch input {
        case .claim: return "campaign cannot claim more jobs"
        case let .complete(_, summary): return campaignSummaryValid(summary) ? "job is not running" : "inconsistent job summary"
        case let .receive(id):
            if case .ready? = before.pending[id] { return "sink is already delivering a report" }
            return "job has no completed report"
        case .sinkReturned: return "job is not awaiting a sink result"
        case .finish: return "campaign has unfinished work"
        case .workerFailed: return "unreachable worker rejection"
        }
    }

    func step(_ before: CampaignLifecycleState, _ input: CampaignLifecycleInput)
        -> Transition<CampaignLifecycleState, CampaignLifecycleOutput> {
        var after = before
        guard var effects = core.apply(&after, input) else {
            return Transition(state: after, disposition: .rejected(rejectionReason(before, input)))
        }
        if duplicateDelivery, let first = effects.first, case .deliver = first { effects.append(first) }
        return Transition(state: after, outputs: effects)
    }

    func faultChoices(_ state: CampaignLifecycleState) -> [CampaignLifecycleInput] {
        var choices: [CampaignLifecycleInput] = [.claim(nil)]
        for id in state.pending.keys.sorted() {
            switch state.pending[id]! {
            case .running: choices.append(campaignComplete(id, .completed))
            case .ready: choices.append(.receive(id))
            }
        }
        return choices
    }

    var model: Model<CampaignLifecycleState, CampaignLifecycleInput, CampaignLifecycleOutput> {
        Model(initialState: core.initialState, step: step, checkState: { campaignStateChecks(core, $0) },
              cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
              checkTransition: { campaignTransitionChecks(core, $0, $1, $2) },
              inputs: { state in InputIterator(values: duplicateDelivery ? faultChoices(state) : (state.finalized ? [] : alphabet())) },
              equalStates: { $0 == $1 }, hashState: { state in
                  var hash = Hasher(); hash.combine(try wire.encodeState(state))
                  return UInt64(bitPattern: Int64(hash.finalize()))
              }, codec: wire.codec)
    }

    var faultGenerator: Generate<CampaignLifecycleState, CampaignLifecycleInput, CampaignLifecycleOutput> {
        Generate(model: model, generate: { state, rng in
            let choices = faultChoices(state)
            return rng.index(UInt64(choices.count)).map { choices[Int($0)] }
        }, equalInputs: { $0 == $1 })
    }

    // Record accepted input facts and all output facts; never call a second
    // reducer to manufacture expected state. Sets keep inert inputs self-loops.
    func observe(_ before: CampaignAuditedState, _ input: CampaignLifecycleInput,
                 _ actual: Transition<CampaignLifecycleState, CampaignLifecycleOutput>)
        -> Transition<CampaignAuditedState, CampaignLifecycleOutput> {
        var ledger = before.ledger
        if actual.disposition == .accepted {
            switch input {
            case .claim(.cancelled): ledger.cancelled = true
            case .claim(.deadline): ledger.deadline = true
            case let .complete(id, summary):
                let duplicate = ledger.completions.updateValue(summary, forKey: id) != nil
                ledger.invalidProtocol = ledger.invalidProtocol || !ledger.claims.contains(id) || duplicate
                ledger.cancelled = ledger.cancelled || summary.outcome == .cancelled
                ledger.deadline = ledger.deadline || summary.outcome == .deadline
                ledger.failureStop = ledger.failureStop || (core.stopOnFailure && summary.outcome == .failure)
            case let .receive(id):
                let inserted = ledger.receipts.insert(id).inserted
                ledger.invalidProtocol = ledger.invalidProtocol || ledger.completions[id] == nil || !inserted
            case let .sinkReturned(id, error):
                let inserted = ledger.returns.insert(id).inserted
                ledger.invalidProtocol = ledger.invalidProtocol || !ledger.attempts.contains(id) || !inserted
                if let error {
                    if ledger.firstSinkError == nil { ledger.firstSinkError = ExactString(error) }
                } else {
                    let inserted = ledger.successes.insert(id).inserted
                    ledger.invalidProtocol = ledger.invalidProtocol || !inserted
                }
            case let .workerFailed(error, lostWork):
                ledger.lastWorkerError = ExactString(error)
                ledger.lostWork = ledger.lostWork || lostWork
            case .finish: ledger.finalized = true
            default: break
            }
        }
        for effect in actual.outputs {
            switch effect {
            case let .runJob(id):
                let inserted = ledger.claims.insert(id).inserted
                ledger.invalidProtocol = ledger.invalidProtocol || !inserted || before.ledger.observedStop || before.ledger.finalized
            case let .deliver(id):
                let inserted = ledger.attempts.insert(id).inserted
                ledger.invalidProtocol = ledger.invalidProtocol || !ledger.receipts.contains(id) || !inserted
                    || ledger.firstSinkError != nil || ledger.discards.contains(id)
            case let .discard(id):
                let inserted = ledger.discards.insert(id).inserted
                ledger.invalidProtocol = ledger.invalidProtocol || !ledger.receipts.contains(id) || !inserted
                    || ledger.firstSinkError == nil || ledger.attempts.contains(id)
            }
        }
        return Transition(state: CampaignAuditedState(core: actual.state, ledger: ledger),
                          outputs: actual.outputs, disposition: actual.disposition)
    }

    func auditTransitionChecks(_ before: CampaignAuditedState, _ input: CampaignLifecycleInput,
                               _ turn: Transition<CampaignAuditedState, CampaignLifecycleOutput>) -> [Check] {
        var checks = campaignTransitionChecks(core, before.core, input,
            Transition(state: turn.state.core, outputs: turn.outputs, disposition: turn.disposition))
        if case .finish = input {
            let enabled = !before.ledger.finalized && before.ledger.readyToFinish(jobs: core.jobs)
            checks.append(campaignCheck("audit.finish_admission", (turn.disposition == .accepted) == enabled))
        }
        return checks
    }

    func auditChecks(_ state: CampaignAuditedState) -> [Check] {
        let ledger = state.ledger, report = state.core.report
        let received = ledger.receipts.compactMap { ledger.completions[$0] }
        let failures = received.filter(\.failure).count
        let errors = received.filter { [.factoryError, .modelError, .panicked].contains($0.outcome) }.count
        // Two limbs sum exposed counters independently of the reducer's
        // saturating update. At most Int.max receipts fit this fixture's state.
        func wideSum(_ key: KeyPath<CampaignSummary, UInt64>) -> (total: UInt64, overflow: Bool) {
            var low: UInt64 = 0, high: UInt64 = 0
            for summary in received {
                let count = summary[keyPath: key]
                let old = low; low &+= count
                if low < old { high += 1 }
            }
            return (high == 0 ? low : .max, high != 0)
        }
        let transitions = wideSum(\.transitions), cases = wideSum(\.cases), skipped = wideSum(\.skippedChecks)
        let complete = !ledger.lostWork && !received.contains { $0.outcome == .modelError || $0.outcome == .panicked }
            && !transitions.overflow && !cases.overflow && !skipped.overflow
            && (!ledger.finalized || ledger.claims == ledger.receipts)
        let termination: CampaignTermination
        if let error = ledger.firstSinkError { termination = .callbackError(error.value) }
        else if let error = ledger.lastWorkerError { termination = .workerError(error.value) }
        else if ledger.cancelled { termination = .cancelled }
        else if ledger.deadline { termination = .deadline }
        else if ledger.failureStop { termination = .failureFound }
        else { termination = .jobsCompleted }
        let active = Set(state.core.delivering.map { [$0] } ?? [])
        return campaignStateChecks(core, state.core) + [
            campaignCheck("audit.protocol", !ledger.invalidProtocol),
            campaignCheck("audit.stop_observed", (state.core.stop != nil) == ledger.observedStop),
            campaignCheck("audit.lost_work", state.core.lostWorker == ledger.lostWork),
            campaignCheck("audit.finalized", state.core.finalized == ledger.finalized
                && (!ledger.finalized || ledger.readyToFinish(jobs: core.jobs))),
            campaignCheck("audit.active_sink", ledger.returns.isSubset(of: ledger.attempts)
                && ledger.attempts.subtracting(ledger.returns) == active),
            campaignCheck("audit.identity_accounting", report.started == UInt64(ledger.claims.count)
                && report.finished == UInt64(ledger.receipts.count) && report.delivered == UInt64(ledger.successes.count)
                && report.failures == UInt64(failures) && report.errors == UInt64(errors)),
            campaignCheck("audit.work_accounting", report.reportedTransitions == transitions.total
                && report.reportedCases == cases.total && report.reportedSkippedChecks == skipped.total
                && report.accountingComplete == complete),
            campaignCheck("audit.receipt_has_one_destination", ledger.attempts.isDisjoint(with: ledger.discards)
                && ledger.attempts.union(ledger.discards) == ledger.receipts),
            campaignCheck("audit.stop_precedence", !ledger.finalized || report.termination == termination)
        ]
    }

    var auditedModel: Model<CampaignAuditedState, CampaignLifecycleInput, CampaignLifecycleOutput> {
        Model(initialState: { CampaignAuditedState(core: try core.initialState()) },
              step: { before, input in observe(before, input, step(before.core, input)) }, checkState: auditChecks,
              cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
              checkTransition: auditTransitionChecks,
              inputs: { InputIterator(values: $0.core.finalized ? [] : alphabet()) },
              equalStates: { $0 == $1 }, hashState: { state in
                  var hash = Hasher(); hash.combine(try wire.encodeState(state.core))
                  let ledger = state.ledger
                  for ids in [ledger.claims, ledger.receipts, ledger.attempts, ledger.returns, ledger.successes, ledger.discards] {
                      hash.combine(ids)
                  }
                  for id in ledger.completions.keys.sorted() { hash.combine(try wire.encodeInput(.complete(id, ledger.completions[id]!))) }
                  hash.combine(ledger.firstSinkError); hash.combine(ledger.lastWorkerError)
                  for flag in [ledger.cancelled, ledger.deadline, ledger.failureStop, ledger.lostWork,
                               ledger.finalized, ledger.invalidProtocol] { hash.combine(flag) }
                  return UInt64(bitPattern: Int64(hash.finalize()))
              })
    }

    var auditedGenerator: Generate<CampaignAuditedState, CampaignLifecycleInput, CampaignLifecycleOutput> {
        Generate(model: auditedModel, generate: { state, rng in
            var choices: [CampaignLifecycleInput] = []
            let s = state.core
            if !s.finalized && rng.index(4) != 0 {
                if s.stop == nil && s.report.started < core.jobs { choices.append(.claim(nil)) }
                for id in s.pending.keys.sorted() {
                    switch s.pending[id]! {
                    case .running: choices += outcomes.map { campaignComplete(id, $0) }
                    case .ready where s.delivering == nil: choices.append(.receive(id))
                    default: break
                    }
                }
                if let id = s.delivering { choices += [.sinkReturned(id, nil), .sinkReturned(id, "sink")] }
                else if s.pending.isEmpty { choices.append(.finish) }
            }
            if choices.isEmpty { choices = alphabet() }
            return rng.index(UInt64(choices.count)).map { choices[Int($0)] }
        }, equalInputs: { $0 == $1 })
    }
}

final class CampaignLifecycleTests: XCTestCase {
    private func assertPassing(_ checks: [Check], file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertFalse(checks.contains { $0.status == .failed }, "\(checks)", file: file, line: line)
    }
    private func assertFailed(_ checks: [Check], _ id: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(checks.contains { $0.id == id && $0.status == .failed }, "missing \(id): \(checks)", file: file, line: line)
    }
    @discardableResult
    private func apply(_ fixture: CampaignFixture, _ state: inout CampaignLifecycleState,
                       _ input: CampaignLifecycleInput, file: StaticString = #filePath, line: UInt = #line)
        -> [CampaignLifecycleOutput] {
        let turn = fixture.step(state, input)
        XCTAssertEqual(turn.disposition, .accepted, "\(input)", file: file, line: line)
        assertPassing(campaignStateChecks(fixture.core, turn.state), file: file, line: line)
        assertPassing(campaignTransitionChecks(fixture.core, state, input, turn), file: file, line: line)
        state = turn.state
        return turn.outputs
    }
    private func reject(_ fixture: CampaignFixture, _ state: CampaignLifecycleState,
                        _ input: CampaignLifecycleInput, file: StaticString = #filePath, line: UInt = #line) {
        var copy = state
        XCTAssertNil(fixture.core.apply(&copy, input), "\(input)", file: file, line: line)
        XCTAssertEqual(copy, state, file: file, line: line)
        assertPassing(campaignTransitionChecks(fixture.core, state, input, fixture.step(state, input)), file: file, line: line)
    }
    private func auditedPrefix(_ fixture: CampaignFixture, _ inputs: [CampaignLifecycleInput]) throws -> CampaignAuditedState {
        let model = fixture.auditedModel
        var state = try model.initialState()
        for input in inputs {
            let turn = try model.step(state, input)
            XCTAssertEqual(turn.disposition, .accepted)
            assertPassing(try model.checkState(turn.state))
            assertPassing(try XCTUnwrap(model.checkTransition)(state, input, turn))
            state = turn.state
        }
        return state
    }

    func testFiniteProtocolGraphAndFixedSeedFuzzUseIndependentLedger() throws {
        for stop in [false, true] {
            let graph = try enumerateStates(CampaignFixture(jobs: 2, stopOnFailure: stop).auditedModel,
                config: SearchConfig(maxStates: 200_000, maxTransitions: 8_000_000, maxDepth: 20))
            XCTAssertNil(graph.failure); XCTAssertEqual(graph.termination, "graph_exhausted")
            XCTAssertEqual(graph.skippedChecks, 0); XCTAssertGreaterThan(graph.states, 1)
            let fuzzed = try fuzz(CampaignFixture(jobs: 4, stopOnFailure: stop).auditedGenerator,
                config: FuzzConfig(seed: 0x5354_4154_454c_4553, cases: 256, maxSteps: 64,
                                   maxTransitions: 16_384, mutationPercent: 50))
            XCTAssertNil(fuzzed.failure); XCTAssertEqual(fuzzed.termination, "cases_completed")
            XCTAssertEqual(fuzzed.skippedChecks, 0)
        }
    }

    func testHostileEventsAndFinalizationAreInert() throws {
        let fixture = CampaignFixture(jobs: 1)
        var state = try fixture.core.initialState()
        for input: CampaignLifecycleInput in [campaignComplete(50, .completed), .receive(50), .sinkReturned(50, nil), .finish] {
            reject(fixture, state, input)
        }
        XCTAssertEqual(apply(fixture, &state, .claim(nil)), [.runJob(50)])
        for input: CampaignLifecycleInput in [.claim(nil), campaignComplete(51, .completed), .receive(50)] { reject(fixture, state, input) }
        for summary in [CampaignSummary(outcome: .factoryError, failure: true),
                        CampaignSummary(outcome: .panicked, transitions: 1),
                        CampaignSummary(outcome: .modelError, cases: 1),
                        CampaignSummary(outcome: .factoryError, skippedChecks: 1),
                        CampaignSummary(outcome: .failure), CampaignSummary(outcome: .completed, failure: true)] {
            reject(fixture, state, .complete(50, summary))
        }
        apply(fixture, &state, campaignComplete(50, .completed))
        reject(fixture, state, campaignComplete(50, .panicked))
        XCTAssertEqual(apply(fixture, &state, .receive(50)), [.deliver(50)])
        for input: CampaignLifecycleInput in [.receive(50), .sinkReturned(51, nil), .finish] { reject(fixture, state, input) }
        apply(fixture, &state, .sinkReturned(50, nil))
        reject(fixture, state, .sinkReturned(50, nil))
        apply(fixture, &state, .finish)
        for input in fixture.alphabet() { reject(fixture, state, input) }
        XCTAssertEqual(state.report.started, 1); XCTAssertEqual(state.report.finished, 1); XCTAssertEqual(state.report.delivered, 1)
    }

    func testStopPermutationsAndLateClaims() throws {
        let permutations = [[0,1,2], [0,2,1], [1,0,2], [1,2,0], [2,0,1], [2,1,0]]
        let outcomes: [CampaignSummaryOutcome] = [.failure, .deadline, .cancelled]
        for order in permutations {
            let fixture = CampaignFixture(jobs: 4, stopOnFailure: true)
            var state = try fixture.core.initialState()
            for _ in 0..<3 { apply(fixture, &state, .claim(nil)) }
            for index in order {
                apply(fixture, &state, campaignComplete(50 + UInt64(index), outcomes[index]))
                reject(fixture, state, .claim(nil)); reject(fixture, state, .claim(.cancelled))
            }
            for id: UInt64 in 50...52 {
                apply(fixture, &state, .receive(id)); apply(fixture, &state, .sinkReturned(id, nil))
            }
            apply(fixture, &state, .finish)
            XCTAssertEqual(state.report.termination, .cancelled)
            XCTAssertEqual(state.report.started, 3); XCTAssertEqual(state.report.finished, 3); XCTAssertEqual(state.report.notStarted, 1)
        }
        for stop: CampaignExternalStop in [.cancelled, .deadline] {
            let fixture = CampaignFixture(jobs: 1)
            var state = try fixture.core.initialState()
            XCTAssertEqual(apply(fixture, &state, .claim(stop)), [])
            apply(fixture, &state, .finish)
            XCTAssertEqual(state.report.termination, stop == .cancelled ? .cancelled : .deadline)
            XCTAssertEqual(state.report.started, 0)
            state = try fixture.core.initialState()
            for input: CampaignLifecycleInput in [.claim(nil), campaignComplete(50, .completed), .receive(50), .sinkReturned(50, nil)] {
                apply(fixture, &state, input)
            }
            reject(fixture, state, .claim(stop)); apply(fixture, &state, .finish)
            XCTAssertEqual(state.report.termination, .jobsCompleted)
        }
    }

    func testSinkErrorSurvivesDrainCompletionsAndWorkerErrors() throws {
        for workerFirst in [false, true] {
            let fixture = CampaignFixture(jobs: 3)
            var state = try fixture.core.initialState()
            apply(fixture, &state, .claim(nil)); apply(fixture, &state, .claim(nil))
            apply(fixture, &state, campaignComplete(50, .completed))
            apply(fixture, &state, campaignComplete(51, .cancelled))
            XCTAssertEqual(apply(fixture, &state, .receive(50)), [.deliver(50)])
            reject(fixture, state, .receive(51))
            if workerFirst { apply(fixture, &state, .workerFailed("worker failure", lostWork: false)) }
            apply(fixture, &state, .sinkReturned(50, "first sink error"))
            if !workerFirst { apply(fixture, &state, .workerFailed("worker failure", lostWork: false)) }
            reject(fixture, state, .sinkReturned(50, "replacement")); reject(fixture, state, .claim(nil))
            XCTAssertEqual(apply(fixture, &state, .receive(51)), [.discard(51)])
            reject(fixture, state, .sinkReturned(51, nil)); apply(fixture, &state, .finish)
            XCTAssertEqual(state.report.termination, .callbackError("first sink error"))
            XCTAssertEqual(state.report.finished, 2); XCTAssertEqual(state.report.delivered, 0)
            XCTAssertEqual(state.report.errors, 0); XCTAssertTrue(state.report.accountingComplete)
        }
        let fixture = CampaignFixture(jobs: 2)
        var state = try fixture.core.initialState()
        apply(fixture, &state, .claim(nil)); apply(fixture, &state, .claim(nil))
        apply(fixture, &state, campaignComplete(50, .completed)); apply(fixture, &state, .receive(50))
        apply(fixture, &state, .sinkReturned(50, "sink"))
        apply(fixture, &state, campaignComplete(51, .cancelled))
        XCTAssertEqual(state.stop, .cancelled)
        XCTAssertEqual(apply(fixture, &state, .receive(51)), [.discard(51)])
        apply(fixture, &state, .finish)
        XCTAssertEqual(state.report.termination, .callbackError("sink"))
    }

    func testWorkerErrorsAllowDeliveryAndLossMustBeExplicit() throws {
        let fixture = CampaignFixture(jobs: 2)
        var state = try fixture.core.initialState()
        apply(fixture, &state, .claim(nil))
        apply(fixture, &state, .workerFailed("spawn", lostWork: false))
        reject(fixture, state, .finish)
        apply(fixture, &state, campaignComplete(50, .deadline))
        XCTAssertEqual(state.stop, .deadline)
        XCTAssertEqual(apply(fixture, &state, .receive(50)), [.deliver(50)])
        apply(fixture, &state, .workerFailed("later", lostWork: false))
        apply(fixture, &state, .sinkReturned(50, nil)); apply(fixture, &state, .finish)
        XCTAssertEqual(state.report.termination, .workerError("later"))
        XCTAssertEqual(state.report.delivered, 1); XCTAssertTrue(state.report.accountingComplete)
        state = try fixture.core.initialState()
        apply(fixture, &state, .claim(nil)); apply(fixture, &state, .workerFailed("spawn", lostWork: false))
        reject(fixture, state, .finish)
        apply(fixture, &state, .workerFailed("lost", lostWork: true)); apply(fixture, &state, .finish)
        XCTAssertEqual(state.report.termination, .workerError("lost")); XCTAssertFalse(state.report.accountingComplete)
        XCTAssertEqual(state.pending, [50: .running]); XCTAssertEqual(state.report.finished, 0)
        XCTAssertEqual(state.report.notStarted, 1)
    }

    func testIndependentLedgerDetectsForgedOrForgottenLossAfterModelError() throws {
        let fixture = CampaignFixture(jobs: 2)
        let model = fixture.auditedModel
        let checkTransition = try XCTUnwrap(model.checkTransition)
        let before = try auditedPrefix(fixture, [
            .claim(nil), .claim(nil), campaignComplete(50, .modelError), .receive(50), .sinkReturned(50, nil)
        ])
        XCTAssertFalse(before.core.report.accountingComplete)
        XCTAssertEqual(before.ledger.claims, Set([UInt64(50), 51]))
        XCTAssertEqual(before.ledger.receipts, Set([UInt64(50)]))
        XCTAssertEqual(before.ledger.attempts, before.ledger.returns)
        XCTAssertEqual(before.core.pending, [51: .running])

        for lostWork in [false, true] {
            let input = CampaignLifecycleInput.workerFailed("worker", lostWork: lostWork)
            let correct = fixture.step(before.core, input)
            XCTAssertEqual(correct.disposition, .accepted)
            let healthy = fixture.observe(before, input, correct)
            assertPassing(try model.checkState(healthy.state))
            assertPassing(try checkTransition(before, input, healthy))
            let healthyFinish = try model.step(healthy.state, .finish)
            XCTAssertEqual(healthyFinish.disposition == .accepted, lostWork)
            assertPassing(try model.checkState(healthyFinish.state))
            assertPassing(try checkTransition(healthy.state, .finish, healthyFinish))

            // ModelError already made accounting incomplete, so an accounting
            // check alone cannot distinguish a forged or forgotten loss flag.
            var corrupted = correct.state
            corrupted.lostWorker = !lostWork
            let faulty = fixture.observe(before, input, Transition(state: corrupted, outputs: correct.outputs))
            XCTAssertEqual(faulty.state.ledger.lostWork, lostWork)
            XCTAssertFalse(faulty.state.core.report.accountingComplete)
            assertPassing(campaignStateChecks(fixture.core, faulty.state.core))
            let checks = try model.checkState(faulty.state)
            assertFailed(checks, "audit.lost_work")
            XCTAssertTrue(checks.contains { $0.id == "audit.work_accounting" && $0.status == .passed })

            let finish = try model.step(faulty.state, .finish)
            XCTAssertEqual(finish.disposition == .accepted, !lostWork)
            // The wire-only predecessor check trusts the corrupted flag. The
            // ledger independently rejects both early acceptance and rejection
            // despite genuine loss, even with accounting already incomplete.
            assertPassing(campaignTransitionChecks(fixture.core, faulty.state.core, .finish,
                Transition(state: finish.state.core, outputs: finish.outputs, disposition: finish.disposition)))
            assertFailed(try checkTransition(faulty.state, .finish, finish), "audit.finish_admission")
            if !lostWork {
                XCTAssertTrue(finish.state.core.finalized)
                assertFailed(try model.checkState(finish.state), "audit.finalized")
            } else {
                XCTAssertEqual(finish.state, faulty.state)
            }
        }
    }

    func testIndependentFinishAdmissionTracksDrainStopsAndFinalization() throws {
        let fixture = CampaignFixture(jobs: 2)
        let model = fixture.auditedModel
        let checkTransition = try XCTUnwrap(model.checkTransition)
        let claimed: [CampaignLifecycleInput] = [.claim(nil), .claim(nil)]
        let ready = claimed + [campaignComplete(50, .completed)]
        let delivering = ready + [.receive(50)]
        let returned = delivering + [.sinkReturned(50, nil)]
        let drained = returned + [campaignComplete(51, .completed), .receive(51), .sinkReturned(51, nil)]
        let cases: [([CampaignLifecycleInput], Bool)] = [
            ([], false),
            ([.claim(nil), campaignComplete(50, .completed), .receive(50), .sinkReturned(50, nil)], false),
            ([.claim(.cancelled)], true),
            ([.claim(nil), campaignComplete(50, .completed), .receive(50), .sinkReturned(50, "sink")], true),
            (claimed + [.workerFailed("worker", lostWork: false)], false),
            (claimed + [.workerFailed("lost", lostWork: true)], true),
            (ready + [.workerFailed("worker", lostWork: false)], false),
            (ready + [.workerFailed("lost", lostWork: true)], true),
            (delivering + [.workerFailed("lost", lostWork: true)], false),
            (delivering + [.workerFailed("lost", lostWork: true), .sinkReturned(50, nil)], true),
            (returned + [.workerFailed("lost", lostWork: true), .workerFailed("later", lostWork: false)], true),
            (delivering + [.sinkReturned(50, "sink"), campaignComplete(51, .completed), .receive(51)], true),
            (drained, true), (drained + [.finish], false)
        ]
        for (prefix, enabled) in cases {
            let before = try auditedPrefix(fixture, prefix)
            let correct = try model.step(before, .finish)
            XCTAssertEqual(correct.disposition == .accepted, enabled, "\(prefix)")
            assertPassing(try model.checkState(correct.state))
            assertPassing(try checkTransition(before, .finish, correct))

            if enabled {
                let rejected = fixture.observe(before, .finish,
                    Transition(state: before.core, disposition: .rejected("injected incorrect rejection")))
                assertFailed(try checkTransition(before, .finish, rejected), "audit.finish_admission")
                let unchanged = fixture.observe(before, .finish, Transition(state: before.core))
                assertPassing(try checkTransition(before, .finish, unchanged))
                assertFailed(try model.checkState(unchanged.state), "audit.finalized")
            } else {
                var premature = before.core
                premature.finalized = true
                let accepted = fixture.observe(before, .finish, Transition(state: premature))
                assertFailed(try checkTransition(before, .finish, accepted), "audit.finish_admission")
            }
        }
    }

    func testErrorOutcomesAndSaturationAccounting() throws {
        for outcome: CampaignSummaryOutcome in [.factoryError, .modelError, .panicked, .failure] {
            let fixture = CampaignFixture(jobs: 2, stopOnFailure: outcome != .failure)
            let state = try auditedPrefix(fixture, [
                .claim(nil), campaignComplete(50, outcome), .receive(50), .sinkReturned(50, nil),
                .claim(nil), campaignComplete(51, .completed), .receive(51), .sinkReturned(51, nil), .finish
            ]).core
            XCTAssertEqual(state.report.termination, .jobsCompleted)
            XCTAssertEqual(state.report.failures, outcome == .failure ? 1 : 0)
            XCTAssertEqual(state.report.errors, outcome == .failure ? 0 : 1)
            XCTAssertEqual(state.report.accountingComplete, outcome == .factoryError || outcome == .failure)
        }
        let fixture = CampaignFixture(jobs: 2)
        for counter in 0..<3 {
            let first = CampaignSummary(outcome: .completed, transitions: counter == 0 ? .max : 0,
                                        cases: counter == 1 ? .max : 0, skippedChecks: counter == 2 ? .max : 0)
            let second = CampaignSummary(outcome: .completed, transitions: counter == 0 ? 1 : 0,
                                         cases: counter == 1 ? 1 : 0, skippedChecks: counter == 2 ? 1 : 0)
            let prefix: [CampaignLifecycleInput] = [.claim(nil), .complete(50, first), .receive(50), .sinkReturned(50, nil)]
            XCTAssertTrue(try auditedPrefix(fixture, prefix).core.report.accountingComplete)
            let report = try auditedPrefix(fixture, prefix + [
                .claim(nil), .complete(51, second), .receive(51), .sinkReturned(51, nil), .finish
            ]).core.report
            XCTAssertFalse(report.accountingComplete)
            XCTAssertEqual([report.reportedTransitions, report.reportedCases, report.reportedSkippedChecks][counter], .max)
        }
    }

    func testRangeEdgesAndBadClaimCheckerDoNotOverflow() throws {
        for fixture in [CampaignFixture(jobs: 0), CampaignFixture(jobs: -1), CampaignFixture(jobs: 2, firstJob: .max)] {
            XCTAssertThrowsError(try fixture.core.initialState())
        }
        let fixture = CampaignFixture(jobs: 1, firstJob: .max)
        var state = try fixture.core.initialState()
        XCTAssertEqual(apply(fixture, &state, .claim(nil)), [.runJob(.max)])
        reject(fixture, state, .claim(nil))
        let faulty = Transition(state: state, outputs: [CampaignLifecycleOutput.runJob(.max)])
        let checks = campaignTransitionChecks(fixture.core, state, .claim(nil), faulty)
        assertFailed(checks, "campaign.claim_once"); assertFailed(checks, "campaign.effects")
    }

    func testIncorrectRejectionAndStaleAckAreDetectedIndependently() throws {
        let fixture = CampaignFixture(jobs: 1)
        let running: [CampaignLifecycleInput] = [.claim(nil)]
        let ready = running + [campaignComplete(50, .completed)]
        let delivering = ready + [.receive(50)]
        let delivered = delivering + [.sinkReturned(50, nil)]
        let cases: [([CampaignLifecycleInput], CampaignLifecycleInput)] = [
            ([], .claim(nil)), ([], .claim(.cancelled)), ([], .claim(.deadline)),
            (running, campaignComplete(50, .completed)), (ready, .receive(50)),
            (delivering, .sinkReturned(50, nil)), (delivering, .sinkReturned(50, "sink")),
            ([], .workerFailed("worker", lostWork: false)), (delivered, .finish)
        ]
        for (prefix, input) in cases {
            let before = try auditedPrefix(fixture, prefix)
            XCTAssertEqual(fixture.step(before.core, input).disposition, .accepted)
            let faulty = Transition<CampaignLifecycleState, CampaignLifecycleOutput>(state: before.core,
                disposition: .rejected("injected incorrect rejection"))
            assertFailed(campaignTransitionChecks(fixture.core, before.core, input, faulty), "campaign.admission")
        }
        let two = CampaignFixture(jobs: 2)
        for anotherActive in [false, true] {
            let prefix = delivered + (anotherActive ? [.claim(nil), campaignComplete(51, .completed), .receive(51)] : [])
            let before = try auditedPrefix(two, prefix)
            let input = CampaignLifecycleInput.sinkReturned(50, "stale sink error")
            reject(two, before.core, input)
            var core = before.core
            core.report.termination = .callbackError("stale sink error"); core.stop = .sink; core.delivering = nil
            let actual = Transition<CampaignLifecycleState, CampaignLifecycleOutput>(state: core)
            let faulty = two.observe(before, input, actual)
            assertFailed(campaignTransitionChecks(two.core, before.core, input, actual), "campaign.admission")
            assertFailed(two.auditChecks(faulty.state), "audit.protocol")
            if anotherActive { assertFailed(two.auditChecks(faulty.state), "audit.active_sink") }
        }
    }

    func testIndependentLedgerDetectsForgottenStopAndDuplicateDeliver() throws {
        let fixture = CampaignFixture(jobs: 2, stopOnFailure: true)
        let cases: [([CampaignLifecycleInput], CampaignLifecycleInput)] = [
            ([], .claim(.cancelled)), ([], .claim(.deadline)),
            ([.claim(nil)], campaignComplete(50, .failure)), ([.claim(nil)], campaignComplete(50, .cancelled)),
            ([.claim(nil)], campaignComplete(50, .deadline)), ([], .workerFailed("worker", lostWork: false)),
            ([.claim(nil), campaignComplete(50, .completed), .receive(50)], .sinkReturned(50, "sink"))
        ]
        for (prefix, input) in cases {
            let before = try auditedPrefix(fixture, prefix)
            let correct = fixture.step(before.core, input)
            XCTAssertEqual(correct.disposition, .accepted)
            var broken = correct.state; broken.stop = nil
            let faulty = fixture.observe(before, input, Transition(state: broken, outputs: correct.outputs))
            assertFailed(fixture.auditChecks(faulty.state), "audit.stop_observed")
            let extra = fixture.observe(faulty.state, .claim(nil), fixture.step(faulty.state.core, .claim(nil)))
            XCTAssertEqual(extra.disposition, .accepted)
            assertFailed(fixture.auditChecks(extra.state), "audit.protocol")
        }
        let faulty = CampaignFixture(duplicateDelivery: true)
        let before = try auditedPrefix(faulty, [.claim(nil), campaignComplete(50, .completed)])
        let turn = faulty.observe(before, .receive(50), faulty.step(before.core, .receive(50)))
        assertFailed(faulty.auditChecks(turn.state), "audit.protocol")
    }

    func testDuplicateDeliveryBFSFuzzShrinkRecordReadReplayAndHealthyControls() throws {
        let faulty = CampaignFixture(duplicateDelivery: true)
        let found = try enumerateStates(faulty.model, config: SearchConfig(maxStates: 100, maxTransitions: 1_000, maxDepth: 10))
        XCTAssertEqual(found.termination, "failure_found")
        let failure = try XCTUnwrap(found.failure)
        XCTAssertTrue(failure.violations.contains { $0.phase == "transition" && $0.check.id == "campaign.effects" })
        let fuzzed = try fuzz(faulty.faultGenerator, config: FuzzConfig(seed: 0x5354_4154_454c_4553,
            cases: 256, maxSteps: 64, maxTransitions: 16_384, mutationPercent: 50))
        XCTAssertEqual(fuzzed.termination, "failure_found")
        XCTAssertTrue(try XCTUnwrap(fuzzed.failure).violations.contains { $0.check.id == "campaign.effects" })
        let original = Failure(inputs: [.sinkReturned(999, nil), .claim(nil)] + failure.inputs, violations: failure.violations)
        let shrunk = try shrink(faulty.faultGenerator, original: original, config: ShrinkConfig(maxAttempts: 1_000))
        XCTAssertTrue(shrunk.validatedOriginal); XCTAssertEqual(shrunk.termination, "search_complete")
        XCTAssertEqual(shrunk.minimized.inputs, [.claim(nil), campaignComplete(50, .completed), .receive(50)])
        XCTAssertLessThan(shrunk.minimized.inputs.count, original.inputs.count)
        let config = RunConfig(strategy: "campaign-self-model-fault-control", parameters: [AuditParameter("injected_fault", "duplicate Deliver")])
        for inputs in [original.inputs, shrunk.minimized.inputs] {
            let trace = try recordAudit(faulty.model, inputs: InputIterator(values: inputs), config: config, maxSteps: 100)
            XCTAssertEqual(trace.termination, .propertyFailed)
            let bytes = try encodeAuditTrace(trace)
            var cursor = 0
            let recovered = try readAuditTrace { requested in
                let end = min(bytes.count, cursor + min(requested, 7))
                defer { cursor = end }
                return Array(bytes[cursor..<end])
            }
            XCTAssertEqual(recovered, trace)
            let replayed = try replayWithObservationsBounded(faulty.model, trace: recovered) { _, _ in }
            XCTAssertEqual(replayed.outcome, "exact"); XCTAssertTrue(replayed.failureReproduced)
            XCTAssertEqual(replayed.stepsVerified, trace.steps.count)
            let healthy = CampaignFixture().model
            let strict = try replayWithObservationsBounded(healthy, trace: recovered) { _, _ in }
            XCTAssertEqual(strict.outcome, "incompatible"); XCTAssertFalse(strict.buildMatches); XCTAssertFalse(strict.failureReproduced)
            let relaxed = try replayWithObservationsBounded(healthy, trace: recovered, allowBuildMismatch: true) { _, _ in }
            XCTAssertEqual(relaxed.outcome, "diverged"); XCTAssertFalse(relaxed.buildMatches); XCTAssertFalse(relaxed.failureReproduced)
        }
    }

    func testHealthyBinaryTraceRoundtripAndReplay() throws {
        let fixture = CampaignFixture()
        let inputs: [CampaignLifecycleInput] = [.claim(nil), .claim(nil), campaignComplete(51, .factoryError),
            campaignComplete(50, .failure), .receive(51), .sinkReturned(51, nil), .receive(50), .sinkReturned(50, nil), .finish]
        let trace = try recordAudit(fixture.model, inputs: InputIterator(values: inputs), config: RunConfig(strategy: "campaign-self-model-healthy-control"), maxSteps: 100)
        XCTAssertEqual(trace.termination, .completed)
        let recovered = try decodeAuditTrace(encodeAuditTrace(trace))
        XCTAssertEqual(recovered, trace)
        let replayed = try replayWithObservationsBounded(fixture.model, trace: recovered) { _, _ in }
        XCTAssertEqual(replayed.outcome, "exact"); XCTAssertEqual(replayed.stepsVerified, 9)
        XCTAssertTrue(replayed.buildMatches); XCTAssertFalse(replayed.failureReproduced)
    }

    // These two separately selected tests support a real process boundary. They
    // skip in the ordinary suite unless the control supplies explicit paths.
    func testPersistedFaultProduce() throws {
        guard let directory = ProcessInfo.processInfo.environment["STATELESS_CAMPAIGN_PRODUCE_DIR"] else {
            throw XCTSkip("persisted fault producer requires an output directory")
        }
        let faulty = CampaignFixture(duplicateDelivery: true)
        let found = try enumerateStates(faulty.model, config: SearchConfig(maxStates: 100, maxTransitions: 1_000, maxDepth: 10))
        let failure = try XCTUnwrap(found.failure)
        XCTAssertEqual(found.termination, "failure_found")
        XCTAssertTrue(failure.violations.contains { $0.phase == "transition" && $0.check.id == "campaign.effects" })
        let original = Failure(inputs: [.sinkReturned(999, nil), .claim(nil)] + failure.inputs, violations: failure.violations)
        let reduced = try shrink(faulty.faultGenerator, original: original, config: ShrinkConfig(maxAttempts: 1_000))
        XCTAssertTrue(reduced.validatedOriginal)
        XCTAssertEqual(reduced.termination, "search_complete")
        XCTAssertEqual(reduced.minimized.inputs, [.claim(nil), campaignComplete(50, .completed), .receive(50)])
        XCTAssertEqual(original.inputs.count, 5)
        let config = RunConfig(strategy: "campaign-self-model-fault-control", parameters: [AuditParameter("injected_fault", "duplicate Deliver")])
        for (name, inputs) in [("original.bin", original.inputs), ("reduced.bin", reduced.minimized.inputs)] {
            let trace = try recordAudit(faulty.model, inputs: InputIterator(values: inputs), config: config, maxSteps: 100)
            XCTAssertEqual(trace.termination, .propertyFailed)
            try Data(encodeAuditTrace(trace)).write(to: URL(fileURLWithPath: directory).appendingPathComponent(name), options: .withoutOverwriting)
        }
    }

    func testPersistedFaultReplay() throws {
        guard let path = ProcessInfo.processInfo.environment["STATELESS_CAMPAIGN_REPLAY_PATH"] else {
            throw XCTSkip("persisted fault replay requires a trace path")
        }
        let faulty = CampaignFixture(duplicateDelivery: true)
        let trace = try decodeAuditTrace(Array(Data(contentsOf: URL(fileURLWithPath: path))))
        let inputs = try trace.steps.map { try faulty.wire.decodeInput($0.input) }
        guard inputs == [.claim(nil), campaignComplete(50, .completed), .receive(50)] else {
            XCTFail("unexpected persisted witness")
            return
        }
        XCTAssertEqual(trace.termination, .propertyFailed)
        XCTAssertTrue(trace.steps.last?.checks.contains { $0.id == "campaign.effects" && $0.status == .failed } == true)
        let replayed = try replayWithObservationsBounded(faulty.model, trace: trace) { _, _ in }
        XCTAssertEqual(replayed.outcome, "exact"); XCTAssertTrue(replayed.failureReproduced)
        XCTAssertTrue(replayed.buildMatches); XCTAssertEqual(replayed.stepsVerified, 3)
        let healthy = CampaignFixture().model
        let strict = try replayWithObservationsBounded(healthy, trace: trace) { _, _ in }
        XCTAssertEqual(strict.outcome, "incompatible"); XCTAssertFalse(strict.buildMatches); XCTAssertFalse(strict.failureReproduced)
        let relaxed = try replayWithObservationsBounded(healthy, trace: trace, allowBuildMismatch: true) { _, _ in }
        XCTAssertEqual(relaxed.outcome, "diverged"); XCTAssertFalse(relaxed.buildMatches); XCTAssertFalse(relaxed.failureReproduced)
    }
}

extension CampaignLifecycleTests {
    func testCanonicalWireGoldenBytesAndIdentity() throws {
        let fixture = CampaignFixture()
        let wire = fixture.wire
        XCTAssertEqual(wire.metadata, Metadata(name: "stateless-campaign:50:2:false", modelVersion: 1,
            propertiesVersion: 2, codecVersion: 1, build: "native-swift-campaign-fixture-v1"))
        var initial = [UInt8](repeating: 0, count: 94)
        initial[0] = 2; initial[24] = 2; initial[80] = 1
        XCTAssertEqual(try wire.encodeState(fixture.core.initialState()), initial)
        XCTAssertEqual(try wire.decodeState(initial), try fixture.core.initialState())
        XCTAssertEqual(try wire.encodeInput(.claim(nil)), [0,0])
        XCTAssertEqual(try wire.encodeInput(.claim(.cancelled)), [0,1])
        XCTAssertEqual(try wire.encodeInput(.claim(.deadline)), [0,2])
        XCTAssertEqual(try wire.encodeInput(.finish), [5])
        let id: [UInt8] = [50,0,0,0,0,0,0,0]
        let summary: [UInt8] = [0,0, 2,0,0,0,0,0,0,0, 1,0,0,0,0,0,0,0, 1,0,0,0,0,0,0,0]
        XCTAssertEqual(try wire.encodeInput(campaignComplete(50, .completed)), [1] + id + summary)
        XCTAssertEqual(try wire.encodeInput(.receive(50)), [2] + id)
        XCTAssertEqual(try wire.encodeInput(.sinkReturned(50, nil)), [3] + id + [0])
        // UTF-8 byte length, not Swift Character count; embedded NUL is payload.
        XCTAssertEqual(try wire.encodeInput(.workerFailed("x\0é", lostWork: true)),
                       [4,4,0,0,0,0,0,0,0,120,0,195,169,1])
        for (tag, output) in [CampaignLifecycleOutput.runJob(50), .deliver(50), .discard(50)].enumerated() {
            XCTAssertEqual(try wire.encodeOutput(output), [UInt8(tag)] + id)
        }
        XCTAssertEqual(try wire.encodeOutput(.deliver(.max)), [1] + [UInt8](repeating: 255, count: 8))
        var claimed = try fixture.core.initialState()
        apply(fixture, &claimed, .claim(nil)); apply(fixture, &claimed, .claim(nil))
        let bytes = try wire.encodeState(claimed)
        XCTAssertEqual(Array(bytes[91..<109]), [50,0,0,0,0,0,0,0,0, 51,0,0,0,0,0,0,0,0])
        let composed = CampaignLifecycleInput.workerFailed("é", lostWork: false)
        let decomposed = CampaignLifecycleInput.workerFailed("e\u{301}", lostWork: false)
        XCTAssertNotEqual(composed, decomposed)
        XCTAssertNotEqual(try wire.encodeInput(composed), try wire.encodeInput(decomposed))
    }

    func testEveryInputCodecRoundtripAndStructuralFailures() throws {
        let wire = CampaignFixture().wire
        var inputs: [CampaignLifecycleInput] = [
            .claim(nil), .claim(.cancelled), .claim(.deadline), .receive(.max),
            .sinkReturned(7, nil), .sinkReturned(0, ""), .sinkReturned(8, "sink\0錯誤"),
            .workerFailed("worker\nerror", lostWork: false), .workerFailed("", lostWork: true), .finish
        ]
        inputs += campaignOutcomes.map { outcome in
            var summary = campaignSummary(outcome)
            if ![CampaignSummaryOutcome.factoryError, .modelError, .panicked].contains(outcome) {
                summary.transitions = .max; summary.cases = 17; summary.skippedChecks = 19
            }
            return .complete(9, summary)
        }
        for input in inputs {
            let bytes = try wire.encodeInput(input)
            XCTAssertEqual(try wire.decodeInput(bytes), input)
            for end in 0..<bytes.count { XCTAssertThrowsError(try wire.decodeInput(Array(bytes.prefix(end)))) }
            XCTAssertThrowsError(try wire.decodeInput(bytes + [0]))
        }
        // A decoder validates syntax; the runtime owns summary admission.
        let invalid = CampaignLifecycleInput.complete(50, CampaignSummary(outcome: .failure))
        XCTAssertEqual(try wire.decodeInput(wire.encodeInput(invalid)), invalid)
        var state = try wire.core.initialState()
        XCTAssertNotNil(wire.core.apply(&state, .claim(nil)))
        let before = state
        XCTAssertNil(wire.core.apply(&state, invalid)); XCTAssertEqual(state, before)
        for bytes: [UInt8] in [[255], [0,3]] { XCTAssertThrowsError(try wire.decodeInput(bytes)) }
        let complete = try wire.encodeInput(campaignComplete(50, .completed))
        for index in [9,10] {
            var bytes = complete; bytes[index] = 255
            XCTAssertThrowsError(try wire.decodeInput(bytes))
        }
        var sink = try wire.encodeInput(.sinkReturned(50, nil)); sink[9] = 2
        XCTAssertThrowsError(try wire.decodeInput(sink))
        let worker = try wire.encodeInput(.workerFailed("x", lostWork: false))
        var hostile = worker; hostile.replaceSubrange(1..<9, with: repeatElement(UInt8(255), count: 8))
        XCTAssertThrowsError(try wire.decodeInput(hostile))
        var utf8 = worker; utf8[9] = 255
        XCTAssertThrowsError(try wire.decodeInput(utf8))
        var flag = worker; flag[flag.count - 1] = 2
        XCTAssertThrowsError(try wire.decodeInput(flag))
        // Overlong and lone-surrogate UTF-8 must also reject, not repair.
        XCTAssertThrowsError(try wire.decodeInput([4,2,0,0,0,0,0,0,0,0xc0,0x80,0]))
        XCTAssertThrowsError(try wire.decodeInput([4,3,0,0,0,0,0,0,0,0xed,0xa0,0x80,0]))
    }

    private func assertStateRoundtrip(_ fixture: CampaignFixture, _ state: CampaignLifecycleState,
                                      file: StaticString = #filePath, line: UInt = #line) throws {
        let wire = fixture.wire, bytes = try wire.encodeState(state)
        let decoded = try wire.decodeState(bytes)
        XCTAssertEqual(decoded, state, file: file, line: line)
        XCTAssertEqual(try wire.encodeState(decoded), bytes, file: file, line: line)
        for end in 0..<bytes.count {
            XCTAssertThrowsError(try wire.decodeState(Array(bytes.prefix(end))), "prefix \(end)", file: file, line: line)
        }
        XCTAssertThrowsError(try wire.decodeState(bytes + [0]), file: file, line: line)
    }

    func testStateCodecRoundtripsEveryFieldAndTermination() throws {
        let fixture = CampaignFixture(jobs: 4, stopOnFailure: true, firstJob: 7)
        try assertStateRoundtrip(fixture, fixture.core.initialState())
        var report = CampaignReport64(requested: 4)
        report.started = 4; report.finished = 3; report.notStarted = 0; report.delivered = 2
        report.failures = 1; report.errors = 1; report.reportedTransitions = .max
        report.reportedCases = 17; report.reportedSkippedChecks = 19; report.accountingComplete = false
        var state = CampaignLifecycleState(report: report, stop: nil, pending: [10: .running],
                                           delivering: 9, lostWorker: false, finalized: false)
        try assertStateRoundtrip(fixture, state)
        for outcome in campaignOutcomes {
            state.pending[10] = .ready(campaignSummary(outcome))
            try assertStateRoundtrip(fixture, state)
        }
        for stop: CampaignLifecycleStop in [.failure, .cancelled, .deadline] {
            state.stop = stop; try assertStateRoundtrip(fixture, state)
        }
        state.delivering = nil; state.pending = [:]; state.report.finished = 4; state.report.delivered = 4; state.finalized = true
        let stops: [(CampaignLifecycleStop?, CampaignTermination)] = [
            (nil, .jobsCompleted), (.failure, .failureFound), (.cancelled, .cancelled), (.deadline, .deadline),
            (.sink, .callbackError("sink\0錯誤")), (.worker, .workerError(""))
        ]
        for (stop, termination) in stops {
            state.stop = stop; state.report.termination = termination
            if case .callbackError = termination { state.report.delivered = 3 } else { state.report.delivered = 4 }
            try assertStateRoundtrip(fixture, state)
        }
        state.lostWorker = true; state.report.finished = 3; state.report.delivered = 3; state.pending[10] = .running
        try assertStateRoundtrip(fixture, state)
    }

    func testStateCodecRejectsHostileLengthsOrderTagsAndImpossibleProperties() throws {
        let fixture = CampaignFixture(jobs: 4, stopOnFailure: true, firstJob: 7)
        let wire = fixture.wire, initial = try fixture.core.initialState()
        let initialBytes = try wire.encodeState(initial)
        for index in [80,81,82,91,92,93] {
            var bytes = initialBytes; bytes[index] = 255
            XCTAssertThrowsError(try wire.decodeState(bytes), "tag \(index)")
        }
        for count: UInt8 in [5,255] {
            var bytes = initialBytes; bytes[83] = count
            XCTAssertThrowsError(try wire.decodeState(bytes))
        }
        var hugeCount = initialBytes; hugeCount.replaceSubrange(83..<91, with: repeatElement(UInt8(255), count: 8))
        XCTAssertThrowsError(try wire.decodeState(hugeCount))
        var claimed = initial; apply(fixture, &claimed, .claim(nil))
        var twice = claimed; apply(fixture, &twice, .claim(nil))
        let bytes = try wire.encodeState(twice)
        var duplicate = bytes; duplicate[100] = 7
        XCTAssertThrowsError(try wire.decodeState(duplicate))
        var reverse = bytes; reverse[91] = 8; reverse[100] = 7
        XCTAssertThrowsError(try wire.decodeState(reverse))
        var pendingTag = bytes; pendingTag[99] = 2
        XCTAssertThrowsError(try wire.decodeState(pendingTag))
        var invalid: [CampaignLifecycleState] = []
        var state = initial; state.report.started = 5; invalid.append(state)
        state = claimed; state.pending[7] = .ready(CampaignSummary(outcome: .factoryError, transitions: 1)); invalid.append(state)
        state = initial; state.finalized = true; state.report.termination = .cancelled; invalid.append(state)
        state = initial; state.lostWorker = true; state.stop = .worker; state.report.termination = .workerError("lost"); invalid.append(state)
        state = initial; state.stop = .sink; state.report.termination = .callbackError("never called"); invalid.append(state)
        state = initial; state.stop = .failure; state.finalized = true; state.report.termination = .failureFound; invalid.append(state)
        state = claimed; state.delivering = 7; invalid.append(state)
        state = claimed; state.pending[7] = nil; state.pending[99] = .running; invalid.append(state)
        for state in invalid { XCTAssertThrowsError(try wire.decodeState(wire.encodeState(state))) }
        var negative = initial; negative.report.started = .max
        XCTAssertThrowsError(try wire.decodeState(wire.encodeState(negative)))
        let disabled = CampaignFixture(jobs: 4, firstJob: 7)
        state = claimed; state.stop = .failure; state.pending[7] = .ready(campaignSummary(.failure))
        XCTAssertThrowsError(try disabled.wire.decodeState(wire.encodeState(state)))
        let otherRange = CampaignFixture(jobs: 4, stopOnFailure: true, firstJob: 70)
        XCTAssertThrowsError(try otherRange.wire.decodeState(wire.encodeState(claimed)))
    }

    func testConsistentInitialCountBoundaryExtendsNativeIntDomain() throws {
        guard Int.bitWidth == 64 else { throw XCTSkip("Rust64 versus signed-64 count boundary") }
        let configuration = CampaignConfig(firstJob: 0, jobs: Int.max,
                                           fuzz: FuzzConfig(cases: 0, maxSteps: 0, maxTransitions: 0))
        XCTAssertNoThrow(try configuration.validate())
        let fixture = CampaignFixture(jobs: Int.max, firstJob: 0)
        // Authored initial wire state: requested == not_started; every started,
        // received and work count zero; accounting=true; no pending/delivery.
        // No allocation is proportional to requested jobs.
        var nativeMaximum = [UInt8](repeating: 0, count: 94)
        let signedMaximum: [UInt8] = [255,255,255,255,255,255,255,127]
        nativeMaximum.replaceSubrange(0..<8, with: signedMaximum)
        nativeMaximum.replaceSubrange(24..<32, with: signedMaximum)
        nativeMaximum[80] = 1
        let initial = try fixture.core.initialState()
        XCTAssertEqual(try fixture.wire.encodeState(initial), nativeMaximum)
        XCTAssertEqual(try fixture.wire.decodeState(nativeMaximum), initial)
        XCTAssertEqual(initial.report.requested, UInt64(Int.max))
        XCTAssertEqual(initial.report.notStarted, UInt64(Int.max))
        XCTAssertTrue(initial.pending.isEmpty)

        // Both report counts cross the signed boundary, with the lifecycle
        // configuration preserving the same full-width requested count.
        var rust64 = nativeMaximum
        let beyondSigned: [UInt8] = [0,0,0,0,0,0,0,128]
        rust64.replaceSubrange(0..<8, with: beyondSigned)
        rust64.replaceSubrange(24..<32, with: beyondSigned)
        let core64 = CampaignLifecycle(config: CampaignConfig64(jobs: UInt64(1) << 63, fuzz: FuzzConfig64(cases: 0)))
        let wire64 = CampaignLifecycleTestCodec(core: core64)
        XCTAssertEqual(try wire64.decodeState(rust64), try core64.initialState())
        XCTAssertEqual(try wire64.encodeState(core64.initialState()), rust64)
        // IDs and work totals are numbers, not counts, and retain full u64.
        let full = CampaignLifecycleInput.complete(.max,
            CampaignSummary(outcome: .completed, transitions: .max, cases: .max, skippedChecks: .max))
        XCTAssertEqual(try fixture.wire.decodeInput(fixture.wire.encodeInput(full)), full)
    }
}

extension CampaignLifecycleTests {
    func testRustPersistedCrossEngineProbe() throws {
        guard let path = ProcessInfo.processInfo.environment["STATELESS_RUST_CAMPAIGN_TRACE"] else {
            throw XCTSkip("cross-engine trace not supplied")
        }
        let model = CampaignFixture(duplicateDelivery: true)
        let trace = try decodeAuditTrace(Array(Data(contentsOf: URL(fileURLWithPath: path))))
        XCTAssertEqual(trace.metadata.name, "stateless-campaign:50:2:false")
        XCTAssertEqual(trace.metadata.modelVersion, 1)
        XCTAssertEqual(trace.metadata.propertiesVersion, 2)
        XCTAssertEqual(trace.metadata.codecVersion, 1)
        XCTAssertEqual(try trace.steps.map { try model.wire.decodeInput($0.input) }, [.claim(nil), campaignComplete(50, .completed), .receive(50)])
        XCTAssertEqual(trace.termination, .propertyFailed)
        XCTAssertTrue(trace.steps.last?.checks.contains { $0.id == "campaign.effects" && $0.status == .failed } == true)
        let strict = try replayWithObservationsBounded(model.model, trace: trace) { _, _ in }
        XCTAssertEqual(strict.outcome, "incompatible")
        XCTAssertFalse(strict.buildMatches)
        XCTAssertEqual(strict.stepsVerified, 0)
        XCTAssertFalse(strict.failureReproduced)
        let observed = try replayWithObservationsBounded(model.model, trace: trace, allowBuildMismatch: true) { _, _ in }
        XCTAssertEqual(observed.outcome, "exact")
        XCTAssertFalse(observed.buildMatches)
        XCTAssertEqual(observed.stepsVerified, 3)
        XCTAssertTrue(observed.failureReproduced)
        let healthy = try replayWithObservationsBounded(CampaignFixture().model, trace: trace, allowBuildMismatch: true) { _, _ in }
        XCTAssertEqual(healthy.outcome, "diverged")
        XCTAssertFalse(healthy.buildMatches)
        XCTAssertFalse(healthy.failureReproduced)
    }
}
