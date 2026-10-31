import Foundation

/// A requirements-derived input sequence, independently retained from search.
public struct ReferenceRegressionCase: Equatable {
    public let fault: ReferenceFault
    public let inputs: [ReferenceInput]
}
public func parseReferenceRegressionCorpus(_ text: String) throws -> [ReferenceRegressionCase] {
    guard text.utf8.count <= 1_048_576 else { throw ModelError("reference regression corpus limit") }
    var sections: [(ReferenceFault,[ReferenceInput])] = [],seen: Set<String> = [],count = 0
    for line in text.split(whereSeparator:{ $0.isNewline }) {
        let trimmed = line.trimmingCharacters(in:.whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed.hasPrefix("#") { continue }
        if trimmed.hasPrefix("["),trimmed.hasSuffix("]") {
            let name = String(trimmed.dropFirst().dropLast())
            guard let fault = ReferenceFault(rawValue:name),seen.insert(name).inserted else { throw ModelError("unknown or duplicate regression mutation") }
            sections.append((fault,[])); continue
        }
        guard !sections.isEmpty else { throw ModelError("regression input before section") }
        let tokens = trimmed.split(whereSeparator:{ $0.isWhitespace }).map(String.init)
        func number(_ index: Int) throws -> UInt64 { guard index < tokens.count,let n = UInt64(tokens[index]) else { throw ModelError("bad regression integer") }; return n }
        func attempt(_ index: Int) throws -> UInt8 { guard index < tokens.count,let n = UInt8(tokens[index]) else { throw ModelError("bad regression attempt") }; return n }
        let input: ReferenceInput
        switch tokens.first {
        case "submit" where tokens.count == 1: input = .submit
        case "cancel" where tokens.count == 2: input = .cancel(try number(1))
        case "cancelled" where tokens.count == 3: input = .cancelled(try number(1),try attempt(2))
        case "complete" where tokens.count == 4 && (tokens[3] == "success" || tokens[3] == "retry"):
            input = .complete(try number(1),try attempt(2),tokens[3] == "success" ? .success : .retryableFailure)
        case "tick" where tokens.count == 2: input = .tick(try number(1))
        default: throw ModelError("bad regression input")
        }
        guard count < 100_000 else { throw ModelError("reference regression input limit") }
        count += 1; sections[sections.count-1].1.append(input)
    }
    guard sections.count == ReferenceFault.allCases.count,sections.allSatisfy({ !$0.1.isEmpty }) else { throw ModelError("missing/empty required regression cases") }
    return sections.map { ReferenceRegressionCase(fault:$0.0,inputs:$0.1) }
}
public func bundledReferenceRegressionCorpus() throws -> [ReferenceRegressionCase] { try parseReferenceRegressionCorpus(ReferenceRegressionData.corpus) }
public struct ReferenceRegressionResult {
    public let fault: ReferenceFault
    public let mutant: Trace
    public let fixed: Trace
}
/// Every preserved sequence must reproduce the named fault exactly while the
/// ordinary reducer executes all inputs with only passing checks.
public func checkReferenceRegressions(_ cases: [ReferenceRegressionCase]) throws -> [ReferenceRegressionResult] {
    guard cases.count == ReferenceFault.allCases.count,Set(cases.map { $0.fault.rawValue }).count == cases.count,cases.allSatisfy({ !$0.inputs.isEmpty }) else { throw ModelError("missing/duplicate reference regressions") }
    var results: [ReferenceRegressionResult] = []
    for item in cases {
        let mutant = try ReferenceController(fault:item.fault).model
        let bad = try record(mutant,inputs:InputIterator(values:item.inputs),maxSteps:item.inputs.count)
        let replayed = try replay(mutant,trace:bad)
        guard bad.termination == .propertyFailed,replayed.outcome == "exact",replayed.failureReproduced,bad.steps.flatMap(\.checks).contains(where:{ exactText($0.id,item.fault.target) && $0.status == .failed }) else { throw ModelError("regression did not exactly reproduce named target") }
        let fixed = try record(ReferenceController().model,inputs:InputIterator(values:item.inputs),maxSteps:item.inputs.count)
        guard fixed.termination == .completed,fixed.steps.count == item.inputs.count,(fixed.initialChecks+fixed.steps.flatMap(\.checks)).allSatisfy({ $0.status == .passed }) else { throw ModelError("fixed reducer did not pass complete retained regression") }
        results.append(ReferenceRegressionResult(fault:item.fault,mutant:bad,fixed:fixed))
    }
    return results
}
