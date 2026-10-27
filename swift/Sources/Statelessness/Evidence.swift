import Foundation

public enum CheckPhase: String { case initialState = "initial_state", state, transition, unknownLegacy = "unknown_legacy" }
private func evidenceOptionalText(_ a: String?, _ b: String?) -> Bool {
    switch (a,b) { case (nil,nil): return true; case let (a?,b?): return exactText(a,b); default: return false }
}
public struct Provenance: Equatable {
    public let run: String
    public let epoch: UInt64
    public let manifest: String?
    public let source: String?
    public let environment: [AuditParameter]
    public init(run: String = "", epoch: UInt64 = 0, manifest: String? = nil, source: String? = nil, environment: [AuditParameter] = []) {
        self.run = run; self.epoch = epoch; self.manifest = manifest; self.source = source; self.environment = environment
    }
    public static func == (a: Self,b: Self) -> Bool { exactText(a.run,b.run) && a.epoch == b.epoch && evidenceOptionalText(a.manifest,b.manifest) && evidenceOptionalText(a.source,b.source) && a.environment == b.environment }
}
public struct OutputOrigin: Equatable, Hashable {
    public let sequence: UInt64
    public let output: UInt32
    public init(sequence: UInt64, output: UInt32) { self.sequence = sequence; self.output = output }
}
public struct CausalContext: Equatable {
    public let machine: String?
    public let generation: UInt64?
    public let correlation: String?
    public let logicalTime: UInt64?
    public let causes: [OutputOrigin]
    public init(machine: String? = nil, generation: UInt64? = nil, correlation: String? = nil, logicalTime: UInt64? = nil, causes: [OutputOrigin] = []) {
        self.machine = machine; self.generation = generation; self.correlation = correlation; self.logicalTime = logicalTime; self.causes = causes
    }
    public static func == (a: Self,b: Self) -> Bool { evidenceOptionalText(a.machine,b.machine) && a.generation == b.generation && evidenceOptionalText(a.correlation,b.correlation) && a.logicalTime == b.logicalTime && a.causes == b.causes }
}
public struct StepEvidence: Equatable {
    public let sequence: UInt64
    public let stateCheckCount: UInt32
    public let causal: CausalContext
    public init(sequence: UInt64, stateCheckCount: UInt32, causal: CausalContext = CausalContext()) { self.sequence = sequence; self.stateCheckCount = stateCheckCount; self.causal = causal }
}
public struct EvidenceLimits {
    public var trace = BinaryTraceLimits()
    public var maxMetadataBytes: UInt64 = 4 * 1024 * 1024
    public var maxLabels: UInt64 = 1024
    public var maxLabelBytes: UInt64 = 4096
    public var maxCausesPerStep: UInt64 = 32
    public var maxTotalCauses: UInt64 = 100_000
    public init() {}
}
func evidenceLabel(_ text: String, _ limits: EvidenceLimits) throws {
    guard UInt64(text.utf8.count) <= limits.maxLabelBytes else { throw ModelError("evidence label byte limit") }
}
func evidenceProvenance(_ value: Provenance, _ limits: EvidenceLimits) throws {
    try evidenceLabel(value.run, limits)
    for value in [value.manifest,value.source].compactMap({$0}) { try evidenceLabel(value, limits) }
    guard UInt64(value.environment.count) <= limits.maxLabels else { throw ModelError("evidence environment label limit") }
    var names = Set<ExactString>()
    for pair in value.environment {
        try evidenceLabel(pair.key, limits); try evidenceLabel(pair.value, limits)
        guard !pair.key.isEmpty, names.insert(ExactString(pair.key)).inserted else { throw ModelError("empty or duplicate evidence environment name") }
    }
}
func evidenceContext(_ value: CausalContext, sequence: UInt64, limits: EvidenceLimits) throws {
    for label in [value.machine,value.correlation].compactMap({$0}) { try evidenceLabel(label, limits) }
    guard UInt64(value.causes.count) <= limits.maxCausesPerStep else { throw ModelError("causal links per step limit") }
    var seen = Set<OutputOrigin>()
    for origin in value.causes {
        guard origin.sequence > 0, origin.sequence < sequence, seen.insert(origin).inserted else { throw ModelError("causal origin must be unique and precede its input") }
    }
}
func evidenceError(_ error: Error) -> Error {
    guard let error = error as? BinaryTraceError else { return error }
    switch error {
    case .truncated: return ModelError("trace is truncated (missing record data or footer)")
    case .checksumMismatch: return ModelError("trace record checksum mismatch")
    case let .unsupportedVersion(version): return ModelError("unsupported trace version \(version)")
    case let .invalidData(message): return ModelError("invalid trace: \(message)")
    case let .limitExceeded(name): return ModelError("trace exceeds \(name) limit")
    }
}
func evidenceTrace<T>(_ body: () throws -> T) throws -> T { do { return try body() } catch { throw evidenceError(error) } }
func evidenceAdd(_ a: UInt64, _ b: UInt64, _ message: String) throws -> UInt64 {
    let (value,overflow) = a.addingReportingOverflow(b)
    guard !overflow else { throw ModelError(message) }; return value
}
public struct TraceV2: Equatable {
    public let trace: AuditTrace
    public let checkpointAfter: UInt64
    public let provenance: Provenance
    public let steps: [StepEvidence]
    public init(trace: AuditTrace, checkpointAfter: UInt64 = 0, provenance: Provenance = Provenance(), steps: [StepEvidence]) { self.trace = trace; self.checkpointAfter = checkpointAfter; self.provenance = provenance; self.steps = steps }
    public func validate(limits: EvidenceLimits = EvidenceLimits()) throws {
        try evidenceProvenance(provenance, limits)
        try validateRecording(trace.portable().trace)
        var measured: UInt64 = 0
        try evidenceTrace { try writeAuditTrace(trace, limits: limits.trace) { measured = try evidenceAdd(measured, UInt64($0.count), "evidence size overflow") } }
        let metadata = try evidenceMetadataBytes(self, maximum: limits.maxMetadataBytes)
        let total = try evidenceAdd(try evidenceAdd(measured, UInt64(metadata.count), "evidence total byte limit"), 32, "evidence total byte limit")
        guard total <= limits.trace.maxTotalBytes else { throw ModelError("evidence total byte limit") }
        guard steps.count == trace.steps.count else { throw ModelError("phase evidence does not cover every recorded step") }
        _ = try evidenceAdd(checkpointAfter, UInt64(steps.count), "evidence sequence overflow")
        var causes: UInt64 = 0
        for (index,pair) in zip(steps,trace.steps).enumerated() {
            let (row,step) = pair
            guard row.sequence == checkpointAfter + UInt64(index) + 1, UInt64(row.stateCheckCount) <= UInt64(step.checks.count) else { throw ModelError("invalid phase evidence sequence or check boundary") }
            try evidenceContext(row.causal, sequence: row.sequence, limits: limits)
            causes = try evidenceAdd(causes, UInt64(row.causal.causes.count), "causal count overflow")
            guard causes <= limits.maxTotalCauses else { throw ModelError("total causal links limit") }
            for origin in row.causal.causes where origin.sequence > checkpointAfter {
                let position = origin.sequence - checkpointAfter - 1
                guard position < UInt64(trace.steps.count), UInt64(origin.output) < UInt64(trace.steps[Int(position)].outputs.count) else { throw ModelError("causal origin output index outside retained evidence") }
            }
        }
    }
    public func checkPhase(sequence: UInt64, checkIndex: Int) -> CheckPhase? {
        guard checkIndex >= 0 else { return nil }
        if sequence == 0 { return checkIndex < trace.initialChecks.count ? .initialState : nil }
        guard sequence <= UInt64(trace.steps.count), sequence <= UInt64(steps.count), checkIndex < trace.steps[Int(sequence-1)].checks.count else { return nil }
        return UInt64(checkIndex) < UInt64(steps[Int(sequence-1)].stateCheckCount) ? .state : .transition
    }
    public func replay<S,I,O>(_ model: Model<S,I,O>, allowBuildMismatch: Bool = false, limits: EvidenceLimits = EvidenceLimits()) throws -> ReplayReport {
        try validate(limits: limits)
        var mismatch: UInt64?
        do {
            return try replayWithObservationsBounded(model, trace: trace, allowBuildMismatch: allowBuildMismatch, limits: limits.trace) { observation, report in
                if case let .turn(turn) = observation, report.outcome == "exact", UInt64(self.steps[Int(turn.sequence-1)].stateCheckCount) != UInt64(turn.stateCheckCount) {
                    mismatch = turn.sequence; throw ModelError("persisted check phase differs from replay")
                }
            }
        } catch let error as ReplayError<S,I,O> {
            if let sequence = mismatch {
                var report = error.report; report.outcome = "diverged"; report.step = Int(sequence); report.field = "check_phase"; report.stepsVerified = Int(sequence-1); report.failureReproduced = false; return report
            }
            throw evidenceError(error.error)
        }
    }
    public static func verifyUpgrade<S,I,O>(_ model: Model<S,I,O>, trace: AuditTrace, provenance: Provenance = Provenance(), checkpointAfter: UInt64 = 0, allowBuildMismatch: Bool = false, limits: EvidenceLimits = EvidenceLimits()) throws -> TraceV2 {
        var rows: [StepEvidence] = []
        let report: ReplayReport
        do {
            report = try replayWithObservationsBounded(model, trace: trace, allowBuildMismatch: allowBuildMismatch, limits: limits.trace) { observation,_ in
                if case let .turn(turn) = observation {
                    let sequence = try evidenceAdd(checkpointAfter, turn.sequence, "evidence sequence overflow")
                    guard let count = UInt32(exactly: turn.stateCheckCount) else { throw ModelError("phase check count overflow") }
                    rows.append(StepEvidence(sequence: sequence, stateCheckCount: count))
                }
            }
        } catch let error as ReplayError<S,I,O> { throw evidenceError(error.error) }
        guard report.outcome == "exact" else { throw ModelError("v1 upgrade requires exact compatible replay") }
        let result = TraceV2(trace: trace, checkpointAfter: checkpointAfter, provenance: provenance, steps: rows)
        try result.validate(limits: limits); return result
    }
    /// Validates the entire envelope before the first write callback.
    public func write(limits: EvidenceLimits = EvidenceLimits(), to write: ([UInt8]) throws -> Void) throws {
        try validate(limits: limits)
        let metadata = try evidenceMetadataBytes(self, maximum: limits.maxMetadataBytes), embedded = try evidenceTrace { try encodeAuditTrace(trace, limits: limits.trace) }
        var header: [UInt8] = [83,84,76,69,83,83,26,10,2,0,0,0]
        header += evidenceInteger(UInt64(embedded.count), width: 8); header += evidenceInteger(UInt64(metadata.count), width: 8)
        let checksum = auditCRC32(header + embedded + metadata)
        try write(header); try write(embedded); try write(metadata); try write(evidenceInteger(UInt64(checksum), width: 4))
    }
}
public enum Artifact: Equatable {
    case v1(AuditTrace), v2(TraceV2)
    public var trace: AuditTrace { switch self { case let .v1(trace): return trace; case let .v2(evidence): return evidence.trace } }
    public func checkPhase(sequence: UInt64, checkIndex: Int) -> CheckPhase? {
        switch self { case let .v2(evidence): return evidence.checkPhase(sequence: sequence, checkIndex: checkIndex); case let .v1(trace): return sequence == 0 && checkIndex >= 0 && checkIndex < trace.initialChecks.count ? .initialState : nil }
    }
    public static func read(limits: EvidenceLimits = EvidenceLimits(), from read: (Int) throws -> [UInt8]) throws -> Artifact {
        var bytes: [UInt8] = []
        while true {
            let remaining = limits.trace.maxTotalBytes >= UInt64(bytes.count) ? limits.trace.maxTotalBytes - UInt64(bytes.count) : 0
            let request = Int(min(8192, remaining == UInt64.max ? UInt64.max : remaining + 1))
            let chunk = try read(request)
            guard chunk.count <= request else { throw ModelError("evidence reader exceeded requested bytes") }
            if chunk.isEmpty { break }
            guard UInt64(chunk.count) <= remaining else { throw ModelError("evidence byte limit") }
            bytes.append(contentsOf: chunk)
        }
        return try decodeArtifact(bytes, limits: limits)
    }
}
public func encodeEvidence(_ value: TraceV2, limits: EvidenceLimits = EvidenceLimits()) throws -> [UInt8] {
    var bytes: [UInt8] = []; try value.write(limits: limits) { bytes.append(contentsOf: $0) }; return bytes
}
func evidenceInteger(_ value: UInt64, width: Int) -> [UInt8] { (0..<width).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) } }
