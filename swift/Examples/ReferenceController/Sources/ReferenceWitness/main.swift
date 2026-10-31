import Foundation
import Statelessness

do {
let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.count == 3,arguments[0] == "verify-suite-pinned" {
    let result = try verifyReferenceFaultSuite(at:URL(fileURLWithPath:arguments[1]),allowBuildMismatch:true,expectedProducerBuild:arguments[2])
    guard result.witnessKindsVerified else { throw ModelError("witness kinds not verified") }
    print("verified=\(result.verifiedWitnesses) build_matches=\(result.buildMatches) witness_kinds_verified=\(result.witnessKindsVerified)")
} else if arguments.count == 2,arguments[0] == "suite" {
    let suite = evaluateReferenceFaultSuite(requirements:try ReferenceRequirements.bundled())
    let persisted = try writeReferenceFaultSuite(suite,to:URL(fileURLWithPath:arguments[1]))
    print("complete=\(persisted.complete) faults=\(persisted.results.count)")
    guard persisted.complete else { throw ModelError("reference fault suite incomplete") }
} else if arguments.count == 2,["verify-suite","verify-suite-compatible"].contains(arguments[0]) {
    let compatible = arguments[0] == "verify-suite-compatible"
    let result = try verifyReferenceFaultSuite(at:URL(fileURLWithPath:arguments[1]),allowBuildMismatch:compatible,allowUnlabeledWitnesses:compatible)
    print("verified=\(result.verifiedWitnesses) build_matches=\(result.buildMatches) witness_kinds_verified=\(result.witnessKindsVerified)")
} else if arguments.count == 2,arguments[0] == "assess-report" {
    let handle = try FileHandle(forReadingFrom:URL(fileURLWithPath:arguments[1])); defer { try? handle.close() }
    var bytes: [UInt8] = []
    while let chunk = try handle.read(upToCount:65_536),!chunk.isEmpty {
        guard chunk.count <= 64*1024*1024-bytes.count else { throw ModelError("assurance report byte limit") }
        bytes.append(contentsOf:chunk)
    }
    let imported = try AssuranceReport.fromAssuranceJSON(parseJSON(bytes))
    let assessment = imported.report.assess()
    print("adequate=\(assessment.adequate) termination=\(imported.report.termination) transitions=\(imported.report.checkedTransitions) properties=\(imported.report.manifest.properties.count) goals=\(imported.report.manifest.goals.count)")
    if !assessment.adequate { throw ModelError("assurance requirements not satisfied") }
} else {
guard arguments.count == 3,let fault = ReferenceFault(rawValue:arguments[1]) else { throw ModelError("usage: ReferenceWitness write|write-original|verify|verify-compatible FAULT TRACE | assess-report REPORT") }
let url = URL(fileURLWithPath:arguments[2])
switch arguments[0] {
case "write", "write-original":
    let result = evaluateReferenceFault(fault,requirements:try ReferenceRequirements.bundled())
    let trace = try recordReferenceWitness(result,minimized:arguments[0] == "write")
    var bytes: [UInt8] = []
    try writeAuditTrace(trace) { bytes.append(contentsOf:$0) }
    try Data(bytes).write(to:url,options:.withoutOverwriting)
    print("written \(fault.rawValue) \(fault.target) \(trace.steps.count) steps")
case "verify", "verify-compatible":
    let handle = try FileHandle(forReadingFrom:url); defer { try? handle.close() }
    let trace = try readAuditTrace { count in Array(try handle.read(upToCount:count) ?? Data()) }
    let report = try verifyReferenceWitness(trace,fault:fault,allowBuildMismatch:arguments[0] == "verify-compatible")
    print("verified \(fault.rawValue) \(fault.target) \(report.stepsVerified) steps build_matches=\(report.buildMatches)")
default: throw ModelError("usage: ReferenceWitness write|write-original|verify|verify-compatible FAULT TRACE | assess-report REPORT")
}
}
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}
