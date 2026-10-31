/// Reference controller's portable Rust-compatible value layout. Decoding checks
/// structural domain membership but preserves property-invalid evidence.
extension ReferenceController {
    private func stateSize(_ state: ReferenceState) throws -> Int {
        let (jobs,overflow) = state.jobs.count.multipliedReportingOverflow(by:19)
        let (total,overflow2) = jobs.addingReportingOverflow(13+(state.current == nil ? 0 : 8))
        guard !overflow,!overflow2,total <= 1 << 20,state.jobs.count <= Int(UInt32.max) else { throw ModelError("reference state encoding exceeds byte limit") }
        return total
    }
    public func encodeState(_ state: ReferenceState,into sink: EncodeBuffer) throws {
        _ = try stateSize(state)
        try ValueCodecs.u64.encodeRaw(state.now,into:sink)
        switch state.current { case .none: try ValueCodecs.u8.encodeRaw(0,into:sink); case let .some(g): try ValueCodecs.u8.encodeRaw(1,into:sink); try ValueCodecs.u64.encodeRaw(g,into:sink) }
        try ValueCodecs.u32.encodeRaw(UInt32(state.jobs.count),into:sink)
        for job in state.jobs {
            try ValueCodecs.u64.encodeRaw(job.generation,into:sink); try ValueCodecs.u8.encodeRaw(job.attempt,into:sink)
            let tag: UInt8,deadline: UInt64
            switch job.phase { case .running: (tag,deadline) = (0,0); case .cancelling: (tag,deadline) = (1,0); case let .waiting(value): (tag,deadline) = (2,value); case .settled(.succeeded): (tag,deadline) = (3,0); case .settled(.cancelled): (tag,deadline) = (4,0); case .settled(.failed): (tag,deadline) = (5,0) }
            try ValueCodecs.u8.encodeRaw(tag,into:sink); try ValueCodecs.u64.encodeRaw(deadline,into:sink); try ValueCodecs.bool.encodeRaw(job.resourceOwned,into:sink)
        }
    }
    public func decodeState(_ bytes: [UInt8]) throws -> ReferenceState {
        let reader = try ValueDecoder(bytes,limits:DecodeLimits(bytes:1 << 20,allocation:1 << 20,elements:32_768,depth:64,work:200_000))
        let now = try reader.value(ValueCodecs.u64),tag = try reader.value(ValueCodecs.u8)
        let current: UInt64?
        switch tag { case 0: current = nil; case 1: current = try reader.value(ValueCodecs.u64); default: throw ModelError("invalid option tag") }
        let count = try reader.value(ValueCodecs.u32)
        try reader.collection(UInt64(count),elementSize:40)
        guard UInt64(count) <= bounds.generations,now <= bounds.time,current.map({ $0 > 0 && $0 <= UInt64(count) }) ?? true else { throw ModelError("state exceeds declared domain") }
        var state = ReferenceState(); state.now = now; state.current = current
        for index in 0..<count {
            let job = try reader.value { reader -> ReferenceJob in
                let generation = try reader.value(ValueCodecs.u64),attempt = try reader.value(ValueCodecs.u8),phaseTag = try reader.value(ValueCodecs.u8),deadline = try reader.value(ValueCodecs.u64),owned = try reader.value(ValueCodecs.bool)
                guard generation == UInt64(index)+1,attempt > 0,attempt <= bounds.attempts else { throw ModelError("invalid generation/attempt") }
                let phase: ReferencePhase
                switch (phaseTag,deadline) {
                case (0,0): phase = .running
                case (1,0): phase = .cancelling
                case (2,let value): guard value <= bounds.time else { throw ModelError("deadline outside domain") }; phase = .waiting(value)
                case (3,0): phase = .settled(.succeeded)
                case (4,0): phase = .settled(.cancelled)
                case (5,0): phase = .settled(.failed)
                default: throw ModelError("invalid/noncanonical phase")
                }
                return ReferenceJob(generation:generation,attempt:attempt,phase:phase,resourceOwned:owned)
            }
            state.jobs.append(job)
        }
        try reader.finish(); return state
    }
    private func encodeWire(_ tag: UInt8,_ generation: UInt64,_ attempt: UInt8,_ outcome: UInt8,into sink: EncodeBuffer) throws {
        try ValueCodecs.u8.encodeRaw(tag,into:sink); try ValueCodecs.u64.encodeRaw(generation,into:sink); try ValueCodecs.u8.encodeRaw(attempt,into:sink); try ValueCodecs.u8.encodeRaw(outcome,into:sink)
    }
    public func encodeInput(_ input: ReferenceInput,into sink: EncodeBuffer) throws {
        switch input { case .submit: try encodeWire(0,0,0,0,into:sink); case let .cancel(g): try encodeWire(1,g,0,0,into:sink); case let .complete(g,a,o): try encodeWire(2,g,a,o == .success ? 0 : 1,into:sink); case let .cancelled(g,a): try encodeWire(3,g,a,0,into:sink); case let .tick(now): try encodeWire(4,now,0,0,into:sink) }
    }
    public func decodeInput(_ bytes: [UInt8]) throws -> ReferenceInput {
        let r = try ValueDecoder(bytes),tag = try r.value(ValueCodecs.u8),value = try r.value(ValueCodecs.u64),attempt = try r.value(ValueCodecs.u8),outcome = try r.value(ValueCodecs.u8)
        try r.finish()
        switch (tag,value,attempt,outcome) {
        case (0,0,0,0): return .submit
        case (1,let g,0,0) where g <= bounds.generations: return .cancel(g)
        case (2,let g,let a,let o) where g <= bounds.generations && a > 0 && a <= bounds.attempts && o <= 1: return .complete(g,a,o == 0 ? .success : .retryableFailure)
        case (3,let g,let a,0) where g <= bounds.generations && a > 0 && a <= bounds.attempts: return .cancelled(g,a)
        case (4,let now,0,0) where now <= bounds.time: return .tick(now)
        default: throw ModelError("invalid/noncanonical input")
        }
    }
    public func encodeOutput(_ output: ReferenceEffect,into sink: EncodeBuffer) throws {
        switch output {
        case let .start(g,a): try encodeWire(0,g,a,0,into:sink)
        case let .cancel(g,a): try encodeWire(1,g,a,0,into:sink)
        case let .publish(g): try encodeWire(2,g,0,0,into:sink)
        case let .settle(g,result):
            let code: UInt8; switch result { case .succeeded: code = 0; case .cancelled: code = 1; case .failed: code = 2 }
            try encodeWire(3,g,0,code,into:sink)
        case let .release(g): try encodeWire(4,g,0,0,into:sink)
        }
    }
    public var codec: Codec<ReferenceState,ReferenceInput,ReferenceEffect> {
        func encoded<T>(_ value: T,maximum: Int,_ body: (T,EncodeBuffer) throws -> Void) throws -> [UInt8] {
            var bytes: [UInt8] = []; try EncodeBuffer.withBuffer(&bytes,maximum:maximum) { try body(value,$0) }; return bytes
        }
        return Codec(metadata:{ self.metadata },encodeState:{ try encoded($0,maximum:1 << 20,self.encodeState) },decodeState:decodeState,encodeInput:{ try encoded($0,maximum:32,self.encodeInput) },decodeInput:decodeInput,encodeOutput:{ try encoded($0,maximum:32,self.encodeOutput) },encodeStateInto:encodeState,encodeInputInto:encodeInput,encodeOutputInto:encodeOutput)
    }
}
