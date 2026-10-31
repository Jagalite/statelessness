import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

public struct ReferencePersistedResult {
    public let fault: ReferenceFault
    public let status: ReferenceFaultStatus
    public let originalName: String?
    public let minimizedName: String?
    public let searchTermination: String?
    public let shrinkComplete: Bool
}
public struct ReferencePersistedSuite {
    public let results: [ReferencePersistedResult]
    public var complete: Bool { results.count == ReferenceFault.allCases.count && results.allSatisfy { $0.status == .killed } }
}
public struct ReferenceSuiteVerification {
    public let verifiedWitnesses: Int
    public let buildMatches: Bool
    /// False when interoperating with traces that do not carry witness-kind provenance.
    public let witnessKindsVerified: Bool
}
private func createReferenceDirectory(_ url: URL) throws {
    #if canImport(Darwin) || canImport(Glibc)
    guard mkdir(url.path,mode_t(0o700)) == 0 else { throw POSIXError(POSIXErrorCode(rawValue:errno) ?? .EIO) }
    #else
    throw ModelError("exclusive reference suite creation unavailable")
    #endif
}
private func writeReferenceFile(_ url: URL,_ body: (FileHandle) throws -> Void) throws {
    #if canImport(Darwin) || canImport(Glibc)
    let fd = open(url.path,O_WRONLY|O_CREAT|O_EXCL,mode_t(0o600))
    guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue:errno) ?? .EIO) }
    let file = FileHandle(fileDescriptor:fd,closeOnDealloc:true)
    do { try body(file); try file.synchronize(); try file.close() }
    catch {
        // Never leave a partial exclusively-created artifact behind.
        try? file.close(); _ = unlink(url.path)
        throw error
    }
    #else
    throw ModelError("exclusive reference witness creation unavailable")
    #endif
}
private func openReferenceFile(_ url: URL) throws -> FileHandle {
    #if canImport(Darwin) || canImport(Glibc)
    // Opening a FIFO in blocking mode could hang before its type can be checked.
    let fd = open(url.path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK)
    guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue:errno) ?? .EIO) }
    var info = stat()
    guard fstat(fd,&info) == 0, (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG) else {
        _ = close(fd)
        throw ModelError("reference evidence must be a regular file")
    }
    return FileHandle(fileDescriptor:fd,closeOnDealloc:true)
    #else
    throw ModelError("safe reference evidence reading unavailable")
    #endif
}
/// Creates a new private directory and never replaces an existing artifact.
/// Individual write failures remain notEvaluated in the saved suite summary.
public func writeReferenceFaultSuite(_ suite: ReferenceFaultSuite,to directory: URL,traceLimits: BinaryTraceLimits = BinaryTraceLimits()) throws -> ReferencePersistedSuite {
    let names = suite.results.map { $0.evaluation.fault.rawValue }
    guard names.count == ReferenceFault.allCases.count,Set(names) == Set(ReferenceFault.allCases.map(\.rawValue)) else { throw ModelError("incomplete or duplicate reference fault suite") }
    try createReferenceDirectory(directory)
    var results: [ReferencePersistedResult] = []
    for item in suite.results {
        let evaluation = item.evaluation, fault = evaluation.fault
        var status = evaluation.status,originalName: String?,minimizedName: String?
        if status == .killed {
            do {
                guard let original = item.originalTrace,let minimized = item.minimizedTrace else { throw ModelError("missing reference witness") }
                _ = try verifyReferenceWitness(original,fault:fault); _ = try verifyReferenceWitness(minimized,fault:fault)
                let first = "\(fault.rawValue)-original.sttrace",second = "\(fault.rawValue)-minimized.sttrace"
                try writeReferenceFile(directory.appendingPathComponent(first)) { file in try writeAuditTrace(original,limits:traceLimits) { try file.write(contentsOf:Data($0)) } }; originalName = first
                try writeReferenceFile(directory.appendingPathComponent(second)) { file in try writeAuditTrace(minimized,limits:traceLimits) { try file.write(contentsOf:Data($0)) } }; minimizedName = second
            } catch { status = .notEvaluated("evidence preservation: \(error)") }
        }
        results.append(ReferencePersistedResult(fault:fault,status:status,originalName:originalName,minimizedName:minimizedName,searchTermination:evaluation.searchTermination,shrinkComplete:evaluation.shrinkComplete))
    }
    let rows: [JSON] = results.map { item in
        let status: String,reason: JSON
        switch item.status { case .killed: status = "killed"; reason = .null; case .survives: status = "survives"; reason = .null; case let .notEvaluated(value): status = "not_evaluated"; reason = .string(String(decoding:value.utf8.prefix(4096),as:UTF8.self)) }
        let names = ["graph_exhausted":"GraphExhausted","failure_found":"FailureFound","state_limit":"StateLimit","transition_limit":"TransitionLimit","estimated_state_byte_limit":"EstimatedStateByteLimit","depth_bound":"DepthBound","deadline":"Deadline","cancelled":"Cancelled"]
        let search = item.searchTermination.map { "Some(\(names[$0] ?? $0))" } ?? "None"
        return .obj(["mutation":.string(item.fault.rawValue),"target":.string(item.fault.target),"status":.string(status),"reason":reason,"search":.string(search),"shrink_complete":.bool(item.shrinkComplete),"original":item.originalName.map(JSON.string) ?? .null,"minimized":item.minimizedName.map(JSON.string) ?? .null])
    }
    let report = JSON.obj(["schema_version":.uint(1),"model":.string("reference-controller"),"build":.string(try ReferenceController().metadata.build),"review_status":.string(try ReferenceRequirements.bundled().reviewStatus),"results":.array(rows)])
    let encoded = try renderJSON(report,maximum:1_048_576)
    try writeReferenceFile(directory.appendingPathComponent("mutations.json")) { try $0.write(contentsOf:Data(encoded)) }
    return ReferencePersistedSuite(results:results)
}
/// Requires the full suite, canonical file names, matching targets, and exact
/// replay of both witnesses. Build mismatch is explicit and remains observable.
public func verifyReferenceFaultSuite(at directory: URL,allowBuildMismatch: Bool = false,allowUnlabeledWitnesses: Bool = false,expectedProducerBuild: String? = nil,traceLimits: BinaryTraceLimits = BinaryTraceLimits()) throws -> ReferenceSuiteVerification {
    let summary = try openReferenceFile(directory.appendingPathComponent("mutations.json")); defer { try? summary.close() }
    var bytes: [UInt8] = []
    while let chunk = try summary.read(upToCount:65_536),!chunk.isEmpty {
        guard chunk.count <= 1_048_576-bytes.count else { throw ModelError("reference suite summary limit") }; bytes.append(contentsOf:chunk)
    }
    let root = try objectFields(parseJSON(bytes,maximum:1_048_576),required:["schema_version","model","build","review_status","results"])
    guard try readInteger(root["schema_version"]) == 1,exactText(try readString(root["model"]),"reference-controller") else { throw ModelError("invalid reference suite summary") }
    let build = try readString(root["build"]),current = try ReferenceController().metadata.build
    guard !build.isEmpty,!(try readString(root["review_status"])).isEmpty else { throw ModelError("invalid reference suite provenance") }
    if let expectedProducerBuild { guard !expectedProducerBuild.isEmpty,exactText(build,expectedProducerBuild) else { throw ModelError("reference producer build pin mismatch") } }
    guard allowBuildMismatch || exactText(build,current) else { throw ModelError("reference suite build mismatch") }
    let rows = try readArray(root["results"],maximum:ReferenceFault.allCases.count)
    guard rows.count == ReferenceFault.allCases.count else { throw ModelError("incomplete reference suite") }
    var seen: Set<String> = [],matches = exactText(build,current),kindsVerified = true,verified = 0
    for value in rows {
        let row = try objectFields(value,required:["mutation","target","status","reason","original","minimized"],optional:["search","shrink_complete"])
        guard (row["search"] == nil) == (row["shrink_complete"] == nil) else { throw ModelError("incomplete reference search metadata") }
        if row["search"] != nil {
            let search = try readString(row["search"])
            // Rust write_mutation_suite/evaluate_mutation keep a validated,
            // target-preserving kill even when shrinking stopped early, and the
            // reference executable gates only on every mutation being killed;
            // shrink_complete is informative but must be a boolean.
            _ = try readBool(row["shrink_complete"])
            guard exactText(search,"Some(FailureFound)") || exactText(search,"failure_found") else { throw ModelError("reference suite search not established") }
        }
        let name = try readString(row["mutation"])
        guard let fault = ReferenceFault(rawValue:name),seen.insert(name).inserted,exactText(try readString(row["target"]),fault.target),exactText(try readString(row["status"]),"killed"),row["reason"] == .null else { throw ModelError("reference suite fault not established") }
        for (key,suffix) in [("original","original"),("minimized","minimized")] {
            let expected = "\(name)-\(suffix).sttrace"
            guard exactText(try readString(row[key]),expected) else { throw ModelError("invalid reference witness filename") }
            let file = try openReferenceFile(directory.appendingPathComponent(expected)); defer { try? file.close() }
            let trace = try readAuditTrace(limits:traceLimits) { count in Array(try file.read(upToCount:count) ?? Data()) }
            let expectedBuild: String
            if build.hasPrefix("swift-reference-"),build.hasSuffix(";fault=none") {
                expectedBuild = String(build.dropLast("none".count)) + name
            } else if build.hasPrefix("reference-"),build.hasSuffix("-None") {
                let variant = name.split(separator:"-").map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined()
                expectedBuild = String(build.dropLast("None".count)) + "Some(\(variant))"
            } else if build.hasPrefix("python-controller-v1:"),build.hasSuffix(":None"),expectedProducerBuild != nil {
                expectedBuild = String(build.dropLast("None".count)) + "'\(name)'"
            } else { throw ModelError("unrecognized reference suite build identity") }
            guard exactText(trace.metadata.build,expectedBuild) else { throw ModelError("reference suite and witness builds differ") }
            let provenance = trace.config.parameters.filter { exactText($0.key,"witness") }
            if provenance.isEmpty && allowUnlabeledWitnesses { kindsVerified = false }
            else { guard provenance.count == 1,exactText(provenance[0].value,suffix) else { throw ModelError("reference witness kind mismatch") } }
            let report = try verifyReferenceWitness(trace,fault:fault,allowBuildMismatch:allowBuildMismatch)
            matches = matches && report.buildMatches; verified += 1
        }
    }
    return ReferenceSuiteVerification(verifiedWitnesses:verified,buildMatches:matches,witnessKindsVerified:kindsVerified)
}
