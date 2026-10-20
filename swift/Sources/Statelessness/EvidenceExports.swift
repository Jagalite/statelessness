import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// Diagnostic output is deliberately nonreplayable. Property IDs require an
/// exact UTF-8 allowlist match; payloads, details and host labels are omitted.
public struct ExportPolicy {
    public var maxSteps: Int
    public var maxBytes: Int
    public var propertyIDs: [String]
    public init(maxSteps: Int = 1000, maxBytes: Int = 1_048_576, propertyIDs: [String] = []) {
        self.maxSteps = maxSteps; self.maxBytes = maxBytes; self.propertyIDs = propertyIDs
    }
}
public func diagnosticJSON(_ artifact: Artifact, policy: ExportPolicy = ExportPolicy()) throws -> [UInt8] {
    guard policy.maxSteps >= 0, policy.maxBytes >= 0 else { throw ConfigError("negative diagnostic export limit") }
    var bytes: [UInt8] = []
    func append(_ text: String) throws {
        guard text.utf8.count <= policy.maxBytes - bytes.count else { throw ModelError("evidence byte limit") }
        bytes.append(contentsOf: text.utf8)
    }
    func string(_ value: String) throws {
        try append("\"")
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 34: try append("\\\"")
            case 92: try append("\\\\")
            case 0...31:
                let hex = String(scalar.value, radix: 16)
                try append("\\u" + String(repeating: "0", count: 4 - hex.count) + hex)
            default: try append(String(scalar))
            }
        }
        try append("\"")
    }
    let trace = artifact.trace, checkpoint: String, omitted: String
    switch artifact {
    case .v1: checkpoint = "null"; omitted = "null"
    case let .v2(value): checkpoint = String(value.checkpointAfter); omitted = value.checkpointAfter == 0 ? "false" : "true"
    }
    let termination: String
    switch trace.termination {
    case .completed: termination = "completed_sequence"
    case .propertyFailed: termination = "property_failure"
    case .stepLimit: termination = "step_limit"
    case .interrupted: termination = "interrupted"
    case .modelError: termination = "model_error"
    }
    try append("{\"schema\":\"statelessness.diagnostic-export/1\",\"replayable\":false,\"redaction_policy\":\"omit-payloads-v1\",\"raw_payloads_included\":false,\"checkpoint_after\":\(checkpoint),\"omitted_prefix\":\(omitted),\"termination\":\"\(termination)\",\"truncated\":\(trace.steps.count > policy.maxSteps),\"observations\":[")
    let allowed = Set(policy.propertyIDs.map(ExactString.init))
    for sequence in 0...min(trace.steps.count, policy.maxSteps) {
        if sequence != 0 { try append(",") }
        let checks = sequence == 0 ? trace.initialChecks : trace.steps[sequence - 1].checks
        try append("{\"sequence\":\(sequence),\"checks\":[")
        for (index, check) in checks.enumerated() {
            if index != 0 { try append(",") }
            let phase = artifact.checkPhase(sequence: UInt64(sequence), checkIndex: index)?.rawValue ?? "unknown_legacy"
            try append("{\"status\":\"\(check.status.rawValue)\",\"phase\":\"\(phase)\",\"id\":")
            if allowed.contains(ExactString(check.id)) { try string(check.id) } else { try append("null") }
            try append("}")
        }
        try append("]}")
    }
    try append("]}")
    return bytes
}

/// Creates exclusively with mode 0600, writes all bytes, then synchronizes the
/// file. A failure after creation may leave an incomplete new file. This does
/// not rename atomically or synchronize its parent directory.
func evidencePrivateNew(path: String, bytes: [UInt8], sync: (Int32) -> Int32 = fsync) throws {
    guard !path.utf8.contains(0) else { throw ModelError("evidence path contains NUL") }
    let fd = path.withCString { open($0, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, mode_t(0o600)) }
    guard fd >= 0 else { throw ModelError("evidence file creation failed: \(String(cString: strerror(errno)))") }
    defer { _ = close(fd) }
    do {
        try bytes.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                #if canImport(Darwin)
                let written = Darwin.write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
                #else
                let written = Glibc.write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
                #endif
                if written < 0 && errno == EINTR { continue }
                guard written > 0 else { throw ModelError(String(cString: strerror(errno))) }
                offset += written
            }
        }
        guard sync(fd) == 0 else { throw ModelError(String(cString: strerror(errno))) }
    } catch { throw ModelError("incomplete newly created evidence file: \(error)") }
}
extension TraceV2 {
    public func writeRawNew(path: String, limits: EvidenceLimits = EvidenceLimits()) throws {
        let bytes = try encodeEvidence(self, limits: limits)
        try evidencePrivateNew(path: path, bytes: bytes)
    }
}
public func exportDiagnosticNew(_ artifact: Artifact, path: String, policy: ExportPolicy = ExportPolicy()) throws {
    let bytes = try diagnosticJSON(artifact, policy: policy)
    try evidencePrivateNew(path: path, bytes: bytes)
}
