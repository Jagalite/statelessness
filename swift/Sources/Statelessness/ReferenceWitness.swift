/// Record a catalog-targeted fault witness as portable binary-v1 audit evidence.
/// V1 stores flat checks and does not preserve check-phase boundaries.
public func recordReferenceWitness(_ result: ReferenceFaultResult,minimized: Bool = true,limits: BinaryTraceLimits = BinaryTraceLimits()) throws -> AuditTrace {
    guard result.status == .killed,let failure = minimized ? result.minimized : result.original else { throw ModelError("reference fault has no killed witness") }
    guard failure.violations.contains(where:{ exactText($0.phase,"transition") && exactText($0.check.id,result.fault.target) && $0.check.status == .failed }) else { throw ModelError("reference witness does not contain target transition failure") }
    let controller = try ReferenceController(fault:result.fault)
    let config = RunConfig(strategy:"reference-fault-control",parameters:[AuditParameter("mutation",result.fault.rawValue),AuditParameter("target",result.fault.target),AuditParameter("witness",minimized ? "minimized" : "original")])
    let trace = try recordAudit(controller.model,inputs:InputIterator(values:failure.inputs),config:config,maxSteps:failure.inputs.count,limits:limits)
    _ = try verifyReferenceWitness(trace,fault:result.fault)
    return trace
}
/// Requires exact local model identity, a matching declared fault and an exact
/// reproduced target failure. Build mismatches require explicit opt-in and are
/// retained in the returned report; model and codec identities still must match.
public func verifyReferenceWitness(_ trace: AuditTrace,fault: ReferenceFault,allowBuildMismatch: Bool = false) throws -> ReplayReport {
    func unique(_ key: String) throws -> String {
        let values = trace.config.parameters.filter { exactText($0.key,key) }
        guard values.count == 1 else { throw ModelError("missing or ambiguous reference witness provenance") }
        return values[0].value
    }
    guard exactText(trace.config.strategy,"reference-fault-control"),exactText(try unique("mutation"),fault.rawValue),exactText(try unique("target"),fault.target) else { throw ModelError("reference witness provenance mismatch") }
    guard trace.termination == .propertyFailed,trace.steps.flatMap(\.checks).contains(where:{ exactText($0.id,fault.target) && $0.status == .failed }) else { throw ModelError("reference witness target failure missing") }
    let report = try replay(ReferenceController(fault:fault).model,trace:trace.portable().trace,allowBuildMismatch:allowBuildMismatch)
    guard exactText(report.outcome,"exact"),report.failureReproduced,(report.buildMatches || allowBuildMismatch) else { throw ModelError("reference witness did not reproduce exactly") }
    return report
}

public struct ReferenceFaultEvidence {
    public let evaluation: ReferenceFaultResult
    public let originalTrace: AuditTrace?
    public let minimizedTrace: AuditTrace?
}
public struct ReferenceFaultSuite {
    public let results: [ReferenceFaultEvidence]
    /// Binary-v1 logical bytes retained across successful recordings, not RSS.
    public let retainedBytes: UInt64
}
/// Every killed fault must retain and exactly replay both witnesses. A failed
/// recording remains not evaluated; already retained earlier evidence survives.
public func evaluateReferenceFaultSuite(requirements: ReferenceRequirements,config: SearchConfig = SearchConfig(),shrinkConfig: ShrinkConfig64 = ShrinkConfig64(),shrinkLimits: ShrinkLimits = ShrinkLimits(),traceLimits: BinaryTraceLimits = BinaryTraceLimits(),maxEvidenceBytes: UInt64 = 64*1024*1024) -> ReferenceFaultSuite {
    var used: UInt64 = 0,results: [ReferenceFaultEvidence] = []
    for fault in ReferenceFault.allCases {
        let evaluated = evaluateReferenceFault(fault,requirements:requirements,config:config,shrinkConfig:shrinkConfig,shrinkLimits:shrinkLimits)
        guard evaluated.status == .killed else { results.append(ReferenceFaultEvidence(evaluation:evaluated,originalTrace:nil,minimizedTrace:nil)); continue }
        var original: AuditTrace?,minimized: AuditTrace?
        do {
            func retain(_ reduced: Bool) throws -> AuditTrace {
                var bounded = traceLimits; bounded.maxTotalBytes = min(bounded.maxTotalBytes,maxEvidenceBytes-used)
                let trace = try recordReferenceWitness(evaluated,minimized:reduced,limits:bounded)
                var bytes: UInt64 = 0
                try writeAuditTrace(trace,limits:bounded) { chunk in
                    let (next,overflow) = bytes.addingReportingOverflow(UInt64(chunk.count))
                    guard !overflow,next <= maxEvidenceBytes-used else { throw ModelError("reference suite evidence byte limit") }
                    bytes = next
                }
                used += bytes; return trace
            }
            original = try retain(false); minimized = try retain(true)
            results.append(ReferenceFaultEvidence(evaluation:evaluated,originalTrace:original,minimizedTrace:minimized))
        } catch {
            let incomplete = ReferenceFaultResult(fault:fault,status:.notEvaluated("evidence preservation/replay: \(error)"),searchTermination:evaluated.searchTermination,original:evaluated.original,minimized:evaluated.minimized,shrinkComplete:evaluated.shrinkComplete)
            results.append(ReferenceFaultEvidence(evaluation:incomplete,originalTrace:original,minimizedTrace:minimized))
        }
    }
    return ReferenceFaultSuite(results:results,retainedBytes:used)
}
