/// Coordinates exact runtime evidence and its causal phase journal. All access
/// is synchronous; callback reentry cannot commit a competing observation.
public final class EvidenceRecorder {
    private let recorder: Recorder
    private let provenance: Provenance
    private let origin: UInt64
    private let limits: EvidenceLimits
    private var journal: [(step: StepEvidence, bytes: UInt64)] = []
    private var metadataBytes: UInt64
    private var mutationInProgress = false
    private var transferred = false
    public init<S,I,O>(checkedInitial: CheckedInitial<S,I,O>, origin: UInt64 = 0, config: RunConfig, options: RecorderOptions = RecorderOptions(), provenance: Provenance = Provenance(), limits: EvidenceLimits = EvidenceLimits()) throws {
        let overhead = try evidenceAdd(32, limits.maxMetadataBytes, "evidence reservation overflow")
        let budget = min(options.maxRetainedBytes, limits.trace.maxTotalBytes)
        guard budget >= overhead else { throw ModelError("evidence metadata reservation exceeds total limit") }
        var options = options; options.maxRetainedBytes = budget - overhead; options.limits = limits.trace
        let base = try evidenceAdd(UInt64(try evidenceProvenanceBytes(provenance, maximum: limits.maxMetadataBytes).count), 12, "metadata size overflow")
        guard base <= limits.maxMetadataBytes else { throw ModelError("metadata budget exceeded") }
        try evidenceProvenance(provenance, limits)
        self.provenance = provenance; self.origin = origin; self.limits = limits; self.metadataBytes = base
        self.recorder = try Recorder(checkedInitial: checkedInitial, sequenceOrigin: origin, config: config, options: options)
    }
    public var isFrozen: Bool { transferred || recorder.isFrozen }
    public func snapshot() -> TraceV2 {
        // The checkpoint cannot overflow: every committed token's exact host
        // sequence was validated before any local eviction.
        TraceV2(trace: recorder.snapshot(), checkpointAfter: origin + recorder.evictedSteps, provenance: provenance, steps: journal.map { $0.step })
    }
    /// Logical ownership transfer. Existing detached snapshots remain usable.
    public func intoTrace() throws -> TraceV2 {
        guard !transferred else { throw ModelError("runtime evidence recorder has transferred its trace") }
        guard !mutationInProgress else {
            throw try recorder.stopWithError(ModelError("runtime evidence recorder transfer is reentrant"))
        }
        let result = snapshot()
        _ = try recorder.intoTrace()
        transferred = true
        return result
    }
    public func observe<S,I,O>(_ turn: CheckedTurn<S,I,O>, causal: CausalContext = CausalContext()) throws {
        guard !transferred else { throw ModelError("runtime evidence recorder has transferred its trace") }
        guard !isFrozen else { throw ModelError("runtime evidence recorder is frozen; export its retained trace") }
        guard !mutationInProgress else {
            throw try recorder.stopWithError(ModelError("runtime evidence recorder mutation is reentrant"))
        }
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            try evidenceContext(causal, sequence: turn.sequence, limits: limits)
            guard let boundary = UInt32(exactly: turn.stateCheckCount) else { throw ModelError("phase boundary overflow") }
            let step = StepEvidence(sequence: turn.sequence, stateCheckCount: boundary, causal: causal)
            let size = UInt64(try evidenceStepBytes(step, maximum: limits.maxMetadataBytes).count)
            var bytes = try evidenceAdd(metadataBytes, size, "phase metadata size overflow")
            var links = UInt64(causal.causes.count)
            for old in journal { links = try evidenceAdd(links, UInt64(old.step.causal.causes.count), "phase causal count overflow") }
            var minimum = 0
            for old in journal {
                if bytes <= limits.maxMetadataBytes && links <= limits.maxTotalCauses { break }
                bytes -= old.bytes; links -= UInt64(old.step.causal.causes.count); minimum += 1
            }
            guard bytes <= limits.maxMetadataBytes, links <= limits.maxTotalCauses else { throw ModelError("candidate phase metadata exceeds retention limits") }
            let checkpoint = try evidenceAdd(origin, recorder.evictedSteps, "checkpoint overflow")
            // Resolve against the current journal before planned eviction: an
            // invalid retained source never becomes an omitted-prefix link.
            for cause in causal.causes where cause.sequence > checkpoint {
                guard let count = recorder.retainedOutputCount(sequence: cause.sequence), UInt64(cause.output) < UInt64(count) else { throw ModelError("causal origin outside retained output range") }
            }
            journal.reserveCapacity(journal.count + 1)
            let previous = recorder.evictedSteps
            _ = try recorder.observeCheckedRetaining(turn, minimumEvictions: minimum)
            let dropped = Int(recorder.evictedSteps - previous)
            for old in journal.prefix(dropped) { metadataBytes -= old.bytes }
            journal.removeFirst(dropped)
            metadataBytes += size; journal.append((step, size))
        } catch { throw try recorder.stopWithError(error) }
    }
}
