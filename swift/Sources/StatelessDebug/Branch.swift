import Statelessness

/// Accidental sidecar mismatch detection; not authentication.
public struct ArtifactIdentity: Equatable {
    public let fnv1a64: UInt64
    public let bytes: UInt64
    public var digest: String { let text = String(fnv1a64,radix: 16); return "fnv1a64:"+String(repeating: "0",count: 16-text.count)+text }
    public init(fnv1a64: UInt64,bytes: UInt64) { self.fnv1a64 = fnv1a64; self.bytes = bytes }
    public init(bytes: [UInt8]) {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in bytes { hash = (hash ^ UInt64(byte)) &* 0x100000001b3 }
        fnv1a64 = hash; self.bytes = UInt64(bytes.count)
    }
    public init(trace: AuditTrace) throws {
        var hash: UInt64 = 0xcbf29ce484222325, count: UInt64 = 0
        try writeAuditTrace(trace) { chunk in
            for byte in chunk { hash = (hash ^ UInt64(byte)) &* 0x100000001b3 }
            let (next,overflow) = count.addingReportingOverflow(UInt64(chunk.count))
            guard !overflow else { throw ModelError("identity byte counter overflow") }; count = next
        }
        fnv1a64 = hash; bytes = count
    }
}
public struct BranchProvenance {
    public let version: UInt32 = 1
    public let parent: ArtifactIdentity
    public let forkSequence: Int
    public let checkpoint: ArtifactIdentity
    public let parentMetadata: Metadata
    public let modelMetadata: Metadata
    public let parentPrefixVerified = true
    public let environment: String
    public let origin: String
    public func validateParent(_ trace: AuditTrace) throws {
        guard try ArtifactIdentity(trace: trace) == parent, trace.metadata == parentMetadata, forkSequence >= 0, forkSequence <= trace.steps.count else { throw ModelError("branch sidecar does not match parent") }
        let state = forkSequence == 0 ? trace.initialState : trace.steps[forkSequence-1].postState
        guard ArtifactIdentity(bytes: state) == checkpoint else { throw ModelError("fork checkpoint binding differs") }
    }
}
public struct DebugBranch<S,I,O> {
    public let session: DebugSession<S,I,O>
    public let provenance: BranchProvenance
}
extension EvidenceViewer {
    /// Replays the prefix before decoding an independent checkpoint. Branch
    /// simulation neither restores a live host nor dispatches external effects.
    public func fork<S,I,O>(id: String, model: Model<S,I,O>, sequence: Int, policy: InputPolicy<S,I,O>, budget: VerificationLimits, sessionLimits: SessionLimits = SessionLimits(), equalInputs: @escaping (I,I) throws -> Bool, recording: RecorderOptions? = nil, allowBuildMismatch: Bool = false) throws -> DebugBranch<S,I,O> {
        guard let boundary = boundary(sequence), let codec = model.codec else { throw ModelError("fork requires retained boundary and codec") }
        let identity = try codec.metadata(), parentIdentity = artifact.trace.metadata
        guard exactText(identity.name,parentIdentity.name),identity.modelVersion == parentIdentity.modelVersion,identity.propertiesVersion == parentIdentity.propertiesVersion,identity.codecVersion == parentIdentity.codecVersion,allowBuildMismatch || exactText(identity.build,parentIdentity.build) else { throw ModelError("fork model identity differs") }
        let trace = artifact.trace
        guard !trace.initialChecks.contains(where: { $0.status == .failed }), !boundary.checks.contains(where: { $0.status == .failed }) else { throw ModelError("normal branches must start before property failure") }
        if let (environment,unrestricted) = try branchParentPolicy(trace), !policy.unrestricted, (!exactText(environment,policy.label) || unrestricted) { throw ModelError("branch environment differs; retain policy or explicitly select unrestricted injection") }
        let termination: AuditTermination
        if sequence == trace.steps.count { termination = .completed }
        else if sequence == 0 && trace.initialChecks.contains(where: { $0.status == .failed }) { termination = .propertyFailed }
        else { termination = .completed }
        let prefix = AuditTrace(metadata: trace.metadata, config: trace.config, initialState: trace.initialState, initialChecks: trace.initialChecks, steps: Array(trace.steps.prefix(sequence)), termination: termination)
        let prefixArtifact: Artifact
        switch artifact {
        case .v1: prefixArtifact = .v1(prefix)
        case let .v2(v): prefixArtifact = .v2(TraceV2(trace: prefix, checkpointAfter: v.checkpointAfter, provenance: v.provenance, steps: Array(v.steps.prefix(sequence))))
        }
        let report = try EvidenceViewer(prefixArtifact, limits: limits).verify(model,budget: budget,allowBuildMismatch: allowBuildMismatch)
        guard report.outcome == "exact" else { throw ModelError("fork requires exact prefix replay") }
        let state = try codec.decodeState(boundary.after)
        let provenance = try BranchProvenance(parent: ArtifactIdentity(trace: trace), forkSequence: sequence, checkpoint: ArtifactIdentity(bytes: boundary.after), parentMetadata: trace.metadata,modelMetadata: codec.metadata(),environment: policy.label,origin: policy.unrestricted ? "out-of-domain-injection" : "verified-simulation-branch")
        let parameters = [AuditParameter("stateless.debug.parent.digest", provenance.parent.digest),AuditParameter("stateless.debug.parent.bytes",String(provenance.parent.bytes)),AuditParameter("stateless.debug.fork.sequence",String(sequence)),AuditParameter("stateless.debug.fork.checkpoint",provenance.checkpoint.digest),AuditParameter("stateless.debug.parent.build",provenance.parentMetadata.build),AuditParameter("stateless.debug.parent_prefix_verified","true"),AuditParameter("stateless.debug.branch.origin",provenance.origin)]
        let session = try DebugSession(id: id,model: model,state: state,policy: policy,limits: sessionLimits,equalInputs: equalInputs,recording: recording,recordingParameters: parameters)
        return DebugBranch(session: session,provenance: provenance)
    }
}
