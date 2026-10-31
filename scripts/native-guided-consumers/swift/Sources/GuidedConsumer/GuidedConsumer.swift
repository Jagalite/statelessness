import Foundation
import Statelessness

func runFixture(_ spec: CaseSpec) throws -> [String: Any] {
    let events = Events(control: spec.control)
    let constantHash = spec.feedback.hash == .constant
    let fixture = TableFixture(spec: spec.model, events: events, constantHash: constantHash)
    try fixture.validate()
    if spec.mode == .guided, spec.feedback.kind == .state, !(0...1).contains(spec.feedback.project) {
        throw AdapterError("state feedback project must be 0 or 1")
    }
    let config = try spec.config.native()
    let limits = RunLimits(maxDuration: spec.control.zero_duration ? 0 : nil, isCancelled: { events.cancelled })
    let generated = fixture.generated
    let observer = fixture.observer
    let result: FuzzReport<Int64>
    // Only the public search invocation supplies the normalized engine error.
    // Decoding, witness replay, shrink, and adapter failures exit nonzero.
    do {
        switch spec.mode {
        case .ordinary:
            result = try fuzz(generated, config: config, limits: limits, observer: observer)
        case .guided:
            switch spec.feedback.kind {
            case .custom:
                result = try guidedFuzz(generated, config: config, guidance: spec.guidance.native,
                                        feedback: customFeedback(spec.feedback, events: events), limits: limits, observer: observer)
            case .state:
                result = try guidedFuzz(generated, config: config, guidance: spec.guidance.native,
                                        feedback: projectedFeedback(spec.feedback, events: events), limits: limits, observer: observer)
            case .exact:
                let feedback = tracedFeedback(exactStateFeedback(generated.model), events: events)
                result = try guidedFuzz(generated, config: config, guidance: spec.guidance.native,
                                        feedback: feedback, limits: limits, observer: observer)
            }
        }
    } catch {
        return ["report": NSNull(), "error": normalizedEngineError(error, checkErrorPhase: events.lastCheckErrorPhase), "trace": events.trace,
                "generator_rng": events.generatorRng, "witness_replays": [], "shrink": NSNull()]
    }

    var witnesses: [[String: Any]] = []
    for entry in result.guidance?.corpus ?? [] {
        witnesses.append(try replayWitness(spec.model, inputs: entry.inputs, failing: false, constantHash: constantHash))
    }
    var shrunk: Any = NSNull()
    if let failure = result.failure {
        let originalWitness = try replayWitness(spec.model, inputs: failure.inputs, failing: true, constantHash: constantHash)
        if let config = spec.shrink_config {
            witnesses.append(originalWitness)
            let fresh = TableFixture(spec: spec.model, constantHash: constantHash)
            let report = try shrink(fresh.generated, original: failure, config: ShrinkConfig(maxAttempts: config.max_attempts))
            witnesses.append(try replayWitness(spec.model, inputs: report.minimized.inputs, failing: true, constantHash: constantHash))
            shrunk = try normalizedShrink(report)
        }
    } else if spec.shrink_config != nil {
        throw AdapterError("shrink_config requires a search failure")
    }
    return ["report": try normalizedReport(result), "error": NSNull(), "trace": events.trace,
            "generator_rng": events.generatorRng, "witness_replays": witnesses, "shrink": shrunk]
}

func replayWitness(_ spec: ModelSpec, inputs: [Int64], failing: Bool, constantHash: Bool) throws -> [String: Any] {
    let recordingFixture = TableFixture(spec: spec, constantHash: constantHash)
    let recorded = try record(recordingFixture.model, inputs: InputIterator(values: inputs), maxSteps: inputs.count)
    let termination: Termination = failing ? .propertyFailed : .completed
    guard recorded.termination == termination, recorded.steps.count == inputs.count else {
        throw AdapterError("witness recording did not finish the supplied prefix with \(termination)")
    }
    let replayFixture = TableFixture(spec: spec, constantHash: constantHash)
    // Replay restores the initial checkpoint without invoking initialState.
    // Callback counters and controls are fresh, while check selectors use case 1.
    replayFixture.events.caseNumber = 1
    var states: [[Int64]] = []
    var outputs: [[Int64]] = []
    var dispositions: [[String: Any]] = []
    var checks: [[String: Any]] = []
    let replayed = try replayWithObservations(replayFixture.model, trace: recorded) { observation, _ in
        switch observation {
        case let .initial(initial):
            states.append(initial.state.json)
            checks.append(["phase": "InitialState", "checks": normalizedChecks(initial.checks)])
        case let .turn(turn):
            states.append(turn.transition.state.json)
            outputs.append(turn.transition.outputs)
            dispositions.append(normalizedDisposition(turn.transition.disposition))
            checks.append(["phase": "State", "checks": normalizedChecks(Array(turn.checks.prefix(turn.stateCheckCount)))])
            checks.append(["phase": "Transition", "checks": normalizedChecks(Array(turn.checks.dropFirst(turn.stateCheckCount)))])
        case let .error(_, _, error, _): throw error
        }
    }
    guard replayed.outcome == "exact", replayed.failureReproduced == failing,
          replayed.stepsVerified == inputs.count, replayed.buildMatches,
          states.count == inputs.count + 1, outputs.count == inputs.count else {
        throw AdapterError("witness replay did not reproduce recorded evidence: \(replayed)")
    }
    return ["inputs": inputs, "states": states, "outputs": outputs, "dispositions": dispositions, "checks": checks,
            "termination": failing ? "PropertyFailed" : "Completed",
            "replay": ["outcome": replayed.outcome == "exact" ? "Exact" : replayed.outcome,
                       "failure_reproduced": replayed.failureReproduced, "steps_verified": replayed.stepsVerified]]
}

@main
enum GuidedConsumer {
    static func main() {
        do { try command() }
        catch {
            FileHandle.standardError.write(Data("native-guided-swift: \(error)\n".utf8))
            exit(1)
        }
    }

    static func command() throws {
        var arguments = CommandLine.arguments.dropFirst().makeIterator()
        var corpusPath: String?
        var outputPath: String?
        var selected: String?
        while let argument = arguments.next() {
            switch argument {
            case "--corpus":
                guard corpusPath == nil, let value = arguments.next() else { throw AdapterError("--corpus requires one path") }
                corpusPath = value
            case "--output":
                guard outputPath == nil, let value = arguments.next() else { throw AdapterError("--output requires one path") }
                outputPath = value
            case "--case":
                guard selected == nil, let value = arguments.next() else { throw AdapterError("--case requires one ID") }
                selected = value
            case "--help", "-h":
                print("GuidedConsumer [--corpus] PATH [--case ID] [--output PATH]")
                return
            default:
                guard !argument.hasPrefix("-"), corpusPath == nil else { throw AdapterError("unexpected argument: \(argument)") }
                corpusPath = argument
            }
        }
        guard let corpusPath else { throw AdapterError("a corpus path is required") }
        let corpusURL = URL(fileURLWithPath: corpusPath).standardizedFileURL.resolvingSymlinksInPath()
        if let outputPath {
            let outputURL = URL(fileURLWithPath: outputPath).standardizedFileURL.resolvingSymlinksInPath()
            guard outputURL != corpusURL else { throw AdapterError("output must not overwrite the input corpus") }
        }
        let corpus = try JSONDecoder().decode(CorpusSpec.self, from: Data(contentsOf: corpusURL))
        guard corpus.schema == "stateless-guided-v2/1" else { throw AdapterError("unsupported corpus schema") }
        guard corpus.source_commit == "a4cc11eea8db62115b452eee3c6610c04fa06b96" else {
            throw AdapterError("unexpected corpus source authority")
        }
        var ids = Set<String>()
        var cases: [[String: Any]] = []
        for spec in corpus.cases {
            guard ids.insert(spec.id).inserted else { throw AdapterError("duplicate fixture ID: \(spec.id)") }
            if selected == nil || selected == spec.id {
                do { cases.append(["id": spec.id, "actual": try runFixture(spec)]) }
                catch { throw AdapterError("case \(spec.id): \(error)") }
            }
        }
        guard !cases.isEmpty else { throw AdapterError("no selected cases") }
        let response: [String: Any] = ["schema": "stateless-guided-v2-results/1", "engine": "swift", "cases": cases]
        var data = try JSONSerialization.data(withJSONObject: response, options: [.prettyPrinted, .sortedKeys])
        data.append(10)
        if let outputPath { try data.write(to: URL(fileURLWithPath: outputPath), options: .atomic) }
        else { FileHandle.standardOutput.write(data) }
    }
}
