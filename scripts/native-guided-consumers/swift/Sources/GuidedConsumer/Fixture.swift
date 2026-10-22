import Foundation
import Statelessness

// Deliberately no expected/rng_audit fields: candidate execution cannot consume
// an expected report, trace, witness, or shrink result.
struct CorpusSpec: Decodable {
    let schema: String
    let source_commit: String
    let cases: [CaseSpec]
}

struct CaseSpec: Decodable {
    enum Mode: String, Decodable { case guided, ordinary }
    let id: String
    let mode: Mode
    let config: ConfigSpec
    let guidance: GuidanceSpec
    let model: ModelSpec
    let feedback: FeedbackSpec
    let control: ControlSpec
    let shrink_config: ShrinkSpec?
}

struct ConfigSpec: Decodable {
    let seed: String
    let cases: Int
    let max_steps: Int
    let max_transitions: UInt64
    let mutation_percent: Int

    func native() throws -> FuzzConfig {
        guard let parsed = UInt64(seed), !seed.isEmpty,
              seed.utf8.allSatisfy({ (48...57).contains($0) }) else {
            throw AdapterError("seed must be an unsigned decimal u64 string")
        }
        return FuzzConfig(seed: parsed, cases: cases, maxSteps: max_steps,
                          maxTransitions: max_transitions, mutationPercent: mutation_percent)
    }
}

struct GuidanceSpec: Decodable {
    let max_corpus_entries: Int
    let max_corpus_inputs: Int
    let max_features: Int
    let max_features_per_observation: Int
    var native: GuidanceConfig {
        GuidanceConfig(maxCorpusEntries: max_corpus_entries, maxCorpusInputs: max_corpus_inputs,
                       maxFeatures: max_features, maxFeaturesPerObservation: max_features_per_observation)
    }
}

struct ShrinkSpec: Decodable { let max_attempts: Int }

struct CheckSpec: Decodable {
    enum Status: String, Decodable { case passed = "Passed", failed = "Failed", skipped = "Skipped" }
    let id: String
    let status: Status
    let message: String?
    var native: Check {
        switch status {
        case .passed: return .passed(id)
        case .failed: return .failed(id, message ?? "")
        case .skipped: return .skipped(id, message ?? "")
        }
    }
}

struct CheckRule: Decodable {
    enum Phase: String, Decodable { case initial = "InitialState", state = "State", transition = "Transition" }
    let phase: Phase
    let `case`: Int?
    let depth: Int64?
    let checks: [CheckSpec]
    let error: String?
}

struct FaultSpec: Decodable {
    let callback: String
    let call: Int
    let message: String
}

struct DispositionSpec: Decodable {
    enum Kind: String, Decodable { case accepted = "Accepted", rejected = "Rejected", ignored = "Ignored" }
    let kind: Kind
    let reason: String?
    var native: Disposition {
        get throws {
            switch kind {
            case .accepted:
                guard reason == nil else { throw AdapterError("Accepted disposition must have a null reason") }
                return .accepted
            case .rejected:
                guard let reason else { throw AdapterError("Rejected disposition requires a reason") }
                return .rejected(reason)
            case .ignored:
                guard let reason else { throw AdapterError("Ignored disposition requires a reason") }
                return .ignored(reason)
            }
        }
    }
}

struct ModelSpec: Decodable {
    enum Enabled: String, Decodable { case nonnegative, successor }
    let generate: [[Int64?]]
    let fallback: Int64?
    let draws: Int
    let rng_mod: UInt64?
    let enabled: Enabled
    let checks: [CheckRule]
    let fail_total: Int64?
    let fault: FaultSpec?
    let disposition: DispositionSpec?
}

struct CancelSpec: Decodable { let callback: String; let call: Int }
struct ControlSpec: Decodable {
    let cancelled: Bool
    let zero_duration: Bool
    let cancel_after: CancelSpec?
}

enum FeatureValue: Hashable, Decodable {
    case text(ExactString)
    case number(Decimal)
    case integers([Int64])

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let text = try? value.decode(String.self) { self = .text(ExactString(text)); return }
        if let values = try? value.decode([Int64].self) { self = .integers(values); return }
        self = .number(try value.decode(Decimal.self))
    }

    var json: Any {
        switch self {
        case let .text(value): return value.value
        case let .number(value): return NSDecimalNumber(decimal: value)
        case let .integers(value): return value
        }
    }
}

struct FeatureKey: Hashable {
    let value: FeatureValue
    let constantHash: Bool
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.value == rhs.value }
    func hash(into hasher: inout Hasher) {
        if constantHash { hasher.combine(0 as UInt8) } else { hasher.combine(value) }
    }
}

struct FeedbackRow: Decodable {
    let `case`: Int?
    let depth: Int64?
    let keys: [FeatureValue]
    let error: String?
}

struct FeedbackSpec: Decodable {
    enum Kind: String, Decodable { case custom, state, exact }
    enum HashMode: String, Decodable { case constant, value }
    let kind: Kind
    let initial: [FeatureValue]?
    let initial_error: String?
    let transitions: [FeedbackRow]
    let project: Int
    let hash: HashMode
    let ignore_overrun: Bool
}

struct TableState: Hashable {
    let depth: Int64
    let total: Int64
    // A run uses one hash policy for every state, including decoded checkpoints.
    // The policy is not a fixture state field and does not enter value equality.
    let constantHash: Bool
    var json: [Int64] { [depth, total] }
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.depth == rhs.depth && lhs.total == rhs.total }
    func hash(into hasher: inout Hasher) {
        if constantHash { hasher.combine(0 as UInt8) }
        else { hasher.combine(depth); hasher.combine(total) }
    }
}

final class Events {
    var caseNumber = 0
    var trace: [[Any]] = []
    var generatorRng: [[String: Any]] = []
    var lastCheckErrorPhase: String?
    var cancelled: Bool
    private var calls: [String: Int] = [:]
    private let cancelAfter: CancelSpec?

    init(control: ControlSpec? = nil) {
        cancelled = control?.cancelled ?? false
        cancelAfter = control?.cancel_after
    }

    @discardableResult
    func done(_ callback: String) -> Int {
        let count = (calls[callback] ?? 0) + 1
        calls[callback] = count
        if let cancelAfter, cancelAfter.callback == callback, cancelAfter.call == count { cancelled = true }
        return count
    }
}

final class TableFixture {
    let spec: ModelSpec
    let events: Events
    let constantHash: Bool

    init(spec: ModelSpec, events: Events = Events(), constantHash: Bool = false) {
        self.spec = spec; self.events = events; self.constantHash = constantHash
    }

    func validate() throws {
        _ = try spec.disposition?.native
        guard spec.draws >= 0 else { throw AdapterError("draws must be nonnegative") }
        if let modulus = spec.rng_mod, modulus == 0 || spec.draws == 0 {
            throw AdapterError("rng_mod requires a positive modulus and at least one draw")
        }
    }

    private func done(_ callback: String) throws {
        let count = events.done(callback)
        if let fault = spec.fault, fault.callback == callback, fault.call == count {
            throw ModelError(fault.message)
        }
    }

    private func checks(_ phase: CheckRule.Phase, state: TableState) -> ([Check], String?) {
        let rule = spec.checks.first {
            $0.phase == phase && ($0.case == nil || $0.case == events.caseNumber)
                && ($0.depth == nil || $0.depth == state.depth)
        }
        var result = rule?.checks.map(\.native) ?? []
        if phase != .transition, rule?.error == nil, let limit = spec.fail_total, state.total >= limit {
            result.append(.failed("limit", "total reached limit"))
        }
        return (result, rule?.error)
    }

    private func stateChecks(_ state: TableState, sink: CheckSink?) throws -> [Check] {
        let (batch, error) = checks(state.depth == 0 ? .initial : .state, state: state)
        events.trace.append(["check_state", events.caseNumber, state.json, normalizedChecks(batch), nullable(error)])
        defer { events.done("check_state") }
        if let sink { try sink.extend(batch) }
        if let error { throw ModelError(error) }
        return batch
    }

    private func transitionChecks(_ before: TableState, _ input: Int64,
                                  _ after: Transition<TableState, Int64>, sink: CheckSink) throws {
        let (batch, error) = checks(.transition, state: after.state)
        events.trace.append(["check_transition", events.caseNumber, before.json, input,
                             after.state.json, normalizedChecks(batch), nullable(error)])
        defer { events.done("check_transition") }
        try sink.extend(batch)
        if let error { throw ModelError(error) }
    }

    var model: Model<TableState, Int64, Int64> {
        Model(initialState: {
            self.events.caseNumber += 1
            self.events.trace.append(["initial_state", self.events.caseNumber])
            try self.done("initial_state")
            return TableState(depth: 0, total: 0, constantHash: self.constantHash)
        }, step: { state, input in
            self.events.trace.append(["step", self.events.caseNumber, state.json, input])
            try self.done("step")
            let (depth, depthOverflow) = state.depth.addingReportingOverflow(1)
            let (total, totalOverflow) = state.total.addingReportingOverflow(input)
            guard !depthOverflow, !totalOverflow else { throw ModelError("fixture integer overflow") }
            return Transition(state: TableState(depth: depth, total: total, constantHash: self.constantHash), outputs: [total],
                              disposition: try self.spec.disposition?.native ?? .accepted)
        }, checkState: { state in try self.stateChecks(state, sink: nil) },
        cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
        equalStates: { $0 == $1 }, codec: codec,
        checkStateInto: { state, sink in _ = try self.stateChecks(state, sink: sink) },
        checkTransitionInto: { before, input, after, sink in
            try self.transitionChecks(before, input, after, sink: sink)
        })
    }

    var generated: Generate<TableState, Int64, Int64> {
        Generate(model: model, generate: { state, rng in
            let raw = (0..<self.spec.draws).map { _ in rng.nextU64() }
            let values = raw.map(String.init)
            let rowIndex = max(0, self.events.caseNumber - 1)
            let row = rowIndex < self.spec.generate.count ? self.spec.generate[rowIndex] : self.spec.generate.last
            var input = self.spec.fallback
            if let row, state.depth >= 0, state.depth < Int64(row.count) { input = row[Int(state.depth)] }
            if input != nil, let modulus = self.spec.rng_mod, let last = raw.last {
                guard let replacement = Int64(exactly: 1 + last % modulus) else {
                    throw ModelError("fixture generated input overflow")
                }
                input = replacement
            }
            self.events.trace.append(["generate", self.events.caseNumber, state.json, nullable(input), values])
            self.events.generatorRng.append(["case": self.events.caseNumber, "state": state.json, "values": values])
            try self.done("generate")
            return input
        }, isEnabled: { state, input in
            let successor = state.total.addingReportingOverflow(1)
            let enabled = input >= 0 && (self.spec.enabled == .nonnegative || (!successor.overflow && input == successor.partialValue))
            self.events.trace.append(["is_enabled", self.events.caseNumber, state.json, input, enabled])
            try self.done("is_enabled")
            return enabled
        }, equalInputs: { $0 == $1 })
    }

    var observer: SearchObserver<TableState, Int64, Int64> {
        SearchObserver(initial: { state, checks in
            self.events.trace.append(["observer_initial", self.events.caseNumber, state.json, normalizedChecks(checks)])
            self.events.done("observer_initial")
        }, checkError: { phase, checks, complete in
            self.events.lastCheckErrorPhase = phase
            self.events.trace.append(["observer_check_error", self.events.caseNumber, try normalizedPhase(phase),
                                      normalizedChecks(checks), complete])
            self.events.done("observer_check_error")
        }, transition: { before, input, after, stateChecks, transitionChecks, history in
            self.events.trace.append(["observer_transition", self.events.caseNumber, before.json, input,
                                      after.state.json, after.outputs, normalizedChecks(stateChecks),
                                      normalizedChecks(transitionChecks), history, normalizedDisposition(after.disposition)])
            self.events.done("observer_transition")
        }, needsHistory: { true })
    }

    private var codec: Codec<TableState, Int64, Int64> {
        Codec(metadata: { Metadata(name: "guided-table-walk", build: "table-walk-v1") },
              encodeState: { state in
                  guard let depth = UInt64(exactly: state.depth) else { throw ModelError("negative depth") }
                  return littleEndian(depth) + littleEndian(UInt64(bitPattern: state.total))
              }, decodeState: { bytes in
                  guard bytes.count == 16 else { throw ModelError("state requires 16 bytes") }
                  let rawDepth = try readU64(Array(bytes.prefix(8)))
                  guard let depth = Int64(exactly: rawDepth) else { throw ModelError("fixture depth overflow") }
                  let total = Int64(bitPattern: try readU64(Array(bytes.suffix(8))))
                  return TableState(depth: depth, total: total, constantHash: self.constantHash)
              }, encodeInput: { littleEndian(UInt64(bitPattern: $0)) },
              decodeInput: { Int64(bitPattern: try readU64($0)) },
              encodeOutput: { littleEndian(UInt64(bitPattern: $0)) })
    }
}

private func littleEndian(_ value: UInt64) -> [UInt8] {
    (0..<8).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) }
}

private func readU64(_ bytes: [UInt8]) throws -> UInt64 {
    guard bytes.count == 8 else { throw ModelError("integer requires 8 bytes") }
    return bytes.enumerated().reduce(0) { $0 | (UInt64($1.element) << ($1.offset * 8)) }
}
