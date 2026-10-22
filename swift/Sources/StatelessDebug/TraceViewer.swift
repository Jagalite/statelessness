import Statelessness

/// Passive cursor over exact legacy-v1 recording bytes. Seeking never executes
/// application code. The retained checkpoint can be later than run start.
public struct TraceViewer {
    private let viewer: EvidenceViewer
    public let identity: ArtifactIdentity
    public private(set) var position: Int = 0
    public var trace: AuditTrace { viewer.artifact.trace }
    public var retainedRange: ClosedRange<UInt64> { viewer.retainedRange }
    public init(_ trace: AuditTrace,limits: EvidenceLimits = EvidenceLimits()) throws {
        viewer = try EvidenceViewer(.v1(trace),limits: limits); identity = try ArtifactIdentity(trace: trace)
    }
    public static func read(limits: EvidenceLimits = EvidenceLimits(),from read: (Int) throws -> [UInt8]) throws -> Self {
        let artifact = try Artifact.read(limits: limits,from: read)
        guard case let .v1(trace) = artifact else { throw ModelError("TraceViewer requires legacy-v1; use EvidenceViewer for v2") }
        return try Self(trace,limits: limits)
    }
    public mutating func seek(_ sequence: Int) throws {
        guard viewer.boundary(sequence) != nil else { throw ModelError("snapshot outside retained history") }; position = sequence
    }
    public func snapshotBytes(_ sequence: Int) throws -> [UInt8] {
        guard let boundary = viewer.boundary(sequence) else { throw ModelError("snapshot outside retained history") }; return boundary.after
    }
    public func stateBytes(maximum: Int) throws -> EncodedPreview { try EncodedPreview(snapshotBytes(position),maximum: maximum) }
    public var checks: [Check] { viewer.boundary(position)!.checks }
    public func outputs(offset: Int = 0,limit: Int = 100,maxBytesEach: Int = 4096) throws -> (values: [EncodedPreview],complete: Bool) {
        guard offset >= 0,limit > 0,limit <= 1000,maxBytesEach >= 0,maxBytesEach <= 4096 else { throw ModelError("output preview budget exceeded") }
        let outputs = viewer.boundary(position)!.outputs
        guard offset <= outputs.count else { throw ModelError("output offset outside snapshot") }
        let end = offset+min(limit,outputs.count-offset)
        return (try outputs[offset..<end].map { try EncodedPreview($0,maximum: maxBytesEach) },end == outputs.count)
    }
    public func decodeState<S,I,O>(_ model: Model<S,I,O>,sequence: Int) throws -> S {
        guard let codec = model.codec,try codec.metadata() == trace.metadata else { throw ModelError("snapshot decoder identity differs") }
        return try codec.decodeState(snapshotBytes(sequence))
    }
    /// Historical output decoding is an independently supplied application
    /// capability. Model.Codec's encodeOutput does not imply a decoder.
    public func historicalOutput<O>(index: Int,decode: ([UInt8]) throws -> O) throws -> O {
        let outputs = viewer.boundary(position)!.outputs
        guard index >= 0,index < outputs.count else { throw ModelError("output index outside snapshot") }; return try decode(outputs[index])
    }
    public func verify<S,I,O>(_ model: Model<S,I,O>,budget: VerificationLimits,allowBuildMismatch: Bool = false) throws -> ReplayReport {
        try viewer.verify(model,budget: budget,allowBuildMismatch: allowBuildMismatch)
    }
    public func forkVerified<S,I,O>(id: String,model: Model<S,I,O>,sequence: Int,policy: InputPolicy<S,I,O>,budget: VerificationLimits,sessionLimits: SessionLimits = SessionLimits(),equalInputs: @escaping (I,I) throws -> Bool,recording: RecorderOptions? = nil,allowBuildMismatch: Bool = false) throws -> DebugBranch<S,I,O> {
        try viewer.fork(id: id,model: model,sequence: sequence,policy: policy,budget: budget,sessionLimits: sessionLimits,equalInputs: equalInputs,recording: recording,allowBuildMismatch: allowBuildMismatch)
    }
}
