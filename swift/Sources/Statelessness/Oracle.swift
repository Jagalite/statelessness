/// Candidate APIs only; these do not expand the qualified portable profiles.
public let experimentalProfiles = ["paired-v1", "search-v1", "native-binary-v1", "bfs-controls-v1", "append-checks-v1", "encode-buffers-v1", "replay-evidence-v1", "checked-results-v1", "causal-evidence-v2", "canonical-value-codec-v1", "research-equivalence-v1", "observation-v1"]

public struct OracleState<S, H> {
    public let model: S
    public let oracle: H
    public init(model: S, oracle: H) { self.model = model; self.oracle = oracle }
}

/// Clone callbacks must deeply snapshot mutable references, as for Model.
public struct Oracle<S, H, I, O> {
    public var metadata: () throws -> Metadata
    public var initialState: () throws -> H
    public var advance: (H, I, [O], Disposition) throws -> H
    public var checkState: (H, S) throws -> [Check]
    public var cloneHistory: (H) throws -> H
    public var equalHistory: ((H, H) throws -> Bool)?
    public var checkTransition: ((OracleState<S, H>, I, Transition<OracleState<S, H>, O>) throws -> [Check])?
    public var encodeHistory: ((H) throws -> [UInt8])?
    public var decodeHistory: (([UInt8]) throws -> H)?
    public var estimatedStateBytes: ((H) throws -> UInt64?)?
    public var encodeHistoryInto: ((H, EncodeBuffer) throws -> Void)?
    public var checkStateInto: ((H, S, CheckSink) throws -> Void)?
    public var checkTransitionInto: ((OracleState<S, H>, I, Transition<OracleState<S, H>, O>, CheckSink) throws -> Void)?
    public init(metadata: @escaping () throws -> Metadata, initialState: @escaping () throws -> H,
                advance: @escaping (H, I, [O], Disposition) throws -> H,
                checkState: @escaping (H, S) throws -> [Check], cloneHistory: @escaping (H) throws -> H,
                equalHistory: ((H, H) throws -> Bool)? = nil,
                checkTransition: ((OracleState<S, H>, I, Transition<OracleState<S, H>, O>) throws -> [Check])? = nil,
                encodeHistory: ((H) throws -> [UInt8])? = nil, decodeHistory: (([UInt8]) throws -> H)? = nil,
                estimatedStateBytes: ((H) throws -> UInt64?)? = nil,
                checkStateInto: ((H, S, CheckSink) throws -> Void)? = nil,
                checkTransitionInto: ((OracleState<S, H>, I, Transition<OracleState<S, H>, O>, CheckSink) throws -> Void)? = nil,
                encodeHistoryInto: ((H, EncodeBuffer) throws -> Void)? = nil) {
        self.metadata = metadata; self.initialState = initialState; self.advance = advance
        self.checkState = checkState; self.cloneHistory = cloneHistory; self.equalHistory = equalHistory
        self.checkTransition = checkTransition; self.encodeHistory = encodeHistory; self.decodeHistory = decodeHistory
        self.estimatedStateBytes = estimatedStateBytes
        self.checkStateInto = checkStateInto; self.checkTransitionInto = checkTransitionInto; self.encodeHistoryInto = encodeHistoryInto
    }
}

/// Carries a defensive snapshot of completed application execution. Generic model
/// values are synchronous, caller-owned data; the error must not cross tasks.
public struct ObservationError<S, O>: Error, CustomStringConvertible, @unchecked Sendable {
    public let error: Error
    public let transition: Transition<S, O>
    public var description: String { String(describing: error) }
}

public struct WithOracle<S, H, I, O> {
    public let actual: Model<S, I, O>
    public let oracle: Oracle<S, H, I, O>
    public init(_ actual: Model<S, I, O>, oracle: Oracle<S, H, I, O>) { self.actual = actual; self.oracle = oracle }
    /// Actual Swift inline padding only. Payload estimates remain application
    /// supplied; heap objects, allocator headers and process RSS are not measured.
    public var estimatedWrapperBytes: UInt64? {
        let (components, overflow) = MemoryLayout<S>.size.addingReportingOverflow(MemoryLayout<H>.size)
        guard !overflow else { return nil }
        return UInt64(max(0, MemoryLayout<OracleState<S, H>>.size - components))
    }
    public func attach(model: S, oracle history: H) throws -> OracleState<S, H> {
        OracleState(model: try copied(actual.cloneState, model), oracle: try copied(oracle.cloneHistory, history))
    }
    public func observeTransition(before: OracleState<S, H>, input: I, transition: Transition<S, O>) throws -> Transition<OracleState<S, H>, O> {
        var retained = transition
        do {
            retained = try cloneTransition(actual, transition)
            let history = try call("advance history") {
                try oracle.advance(copied(oracle.cloneHistory, before.oracle), copied(actual.cloneInput, input), transition.outputs.map { try copied(actual.cloneOutput, $0) }, transition.disposition)
            }
            return Transition(state: try attach(model: transition.state, oracle: history), outputs: retained.outputs, disposition: retained.disposition)
        } catch { throw ObservationError(error: error, transition: retained) }
    }
    public func metadata() throws -> Metadata {
        guard let c = actual.codec else { throw ModelError("unsupported oracle adapter operation: metadata") }
        let m = try c.metadata(), o = try oracle.metadata()
        return Metadata(name: "with-oracle-v1:\(m.name.utf8.count):\(m.name):\(m.modelVersion):\(m.propertiesVersion):\(m.codecVersion):\(o.name.utf8.count):\(o.name):\(o.modelVersion):\(o.propertiesVersion):\(o.codecVersion)", modelVersion: m.modelVersion, propertiesVersion: m.propertiesVersion, codecVersion: m.codecVersion, build: "\(m.build.utf8.count):\(m.build):\(o.build.utf8.count):\(o.build)")
    }
    public func encodeState(_ state: OracleState<S, H>) throws -> [UInt8] {
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: Int.max) { try self.encodeStateInto(state, $0) }
        return bytes
    }
    public func encodeStateInto(_ state: OracleState<S, H>, _ sink: EncodeBuffer) throws {
        guard let c = actual.codec, oracle.encodeHistory != nil || oracle.encodeHistoryInto != nil else { throw ModelError("unsupported oracle adapter operation: encode state") }
        try sink.extend([79, 82, 67, 49])
        try sink.framed { child in
            let snapshot = try copied(actual.cloneState, state.model)
            if let incremental = c.encodeStateInto { try incremental(snapshot, child) }
            else { try child.extendFromVec(c.encodeState(snapshot)) }
        }
        try sink.framed { child in
            let snapshot = try copied(oracle.cloneHistory, state.oracle)
            if let incremental = oracle.encodeHistoryInto { try incremental(snapshot, child) }
            else if let encode = oracle.encodeHistory { try child.extendFromVec(encode(snapshot)) }
        }
    }
    public func decodeState(_ bytes: [UInt8]) throws -> OracleState<S, H> {
        guard let c = actual.codec, let decode = oracle.decodeHistory else { throw ModelError("unsupported oracle adapter operation: decode state") }
        guard bytes.count >= 4, Array(bytes.prefix(4)) == [79, 82, 67, 49] else { throw ModelError("invalid oracle checkpoint format") }
        var offset = 4
        func component() throws -> [UInt8] {
            guard bytes.count - offset >= 8 else { throw ModelError("truncated oracle checkpoint length") }
            var size: UInt64 = 0
            for i in 0..<8 { size |= UInt64(bytes[offset + i]) << (i * 8) }; offset += 8
            guard size <= UInt64(bytes.count - offset) else { throw ModelError("truncated oracle checkpoint component") }
            let end = offset + Int(size), part = Array(bytes[offset..<end]); offset = end; return part
        }
        let a = try component(), h = try component()
        guard offset == bytes.count else { throw ModelError("trailing oracle checkpoint bytes") }
        return try attach(model: c.decodeState(a), oracle: decode(h))
    }
    private func appendState(_ state: OracleState<S, H>, into sink: CheckSink) throws {
        try appendStateChecks(actual, state.model, into: sink)
        try call("oracle state check") {
            try sink.sublease { child in
                let history = try copied(oracle.cloneHistory, state.oracle), model = try copied(actual.cloneState, state.model)
                if let callback = oracle.checkStateInto { try callback(history, model, child) }
                else { try child.extend(oracle.checkState(history, model)) }
            }
        }
    }
    private func appendTransition(_ before: OracleState<S, H>, _ input: I, _ t: Transition<OracleState<S, H>, O>, into sink: CheckSink) throws {
        try appendTransitionChecks(actual, before.model, input, Transition(state: t.state.model, outputs: t.outputs, disposition: t.disposition), into: sink)
        guard oracle.checkTransitionInto != nil || oracle.checkTransition != nil else { return }
        try call("oracle transition check") {
            try sink.sublease { child in
                let b = try attach(model: before.model, oracle: before.oracle), i = try copied(actual.cloneInput, input)
                let turn = Transition(state: try attach(model: t.state.model, oracle: t.state.oracle), outputs: try t.outputs.map { try copied(actual.cloneOutput, $0) }, disposition: t.disposition)
                if let callback = oracle.checkTransitionInto { try callback(b, i, turn, child) }
                else if let callback = oracle.checkTransition { try child.extend(callback(b, i, turn)) }
            }
        }
    }
    /// Use this ordinary native model with recording, replay and finite BFS.
    public var model: Model<OracleState<S, H>, I, O> {
        let clone: (OracleState<S, H>) throws -> OracleState<S, H> = { try self.attach(model: $0.model, oracle: $0.oracle) }
        var result = Model<OracleState<S, H>, I, O>(
            initialState: { try self.attach(model: self.actual.initialState(), oracle: self.oracle.initialState()) },
            step: { before, input in try self.observeTransition(before: before, input: input, transition: perform(self.actual, before.model, input)) },
            checkState: { state in
                var checks: [Check] = []
                try CheckSink.withStorage(&checks) { try self.appendState(state, into: $0) }; return checks
            }, cloneState: clone, cloneInput: actual.cloneInput, cloneOutput: actual.cloneOutput,
            checkTransition: { before, input, t in
                var checks: [Check] = []
                try CheckSink.withStorage(&checks) { try self.appendTransition(before, input, t, into: $0) }; return checks
            })
        result.checkStateInto = { try self.appendState($0, into: $1) }
        result.checkTransitionInto = { try self.appendTransition($0, $1, $2, into: $3) }
        if let inputs = actual.inputs { result.inputs = { try inputs(copied(self.actual.cloneState, $0.model)) } }
        if let equal = actual.equalStates, let historyEqual = oracle.equalHistory {
            result.equalStates = { a, b in
                let sameActual = try equal(copied(self.actual.cloneState, a.model), copied(self.actual.cloneState, b.model))
                guard sameActual else { return false }
                return try historyEqual(copied(self.oracle.cloneHistory, a.oracle), copied(self.oracle.cloneHistory, b.oracle))
            }
        }
        if let c = actual.codec, oracle.encodeHistory != nil || oracle.encodeHistoryInto != nil, oracle.decodeHistory != nil {
            result.codec = Codec(metadata: self.metadata, encodeState: self.encodeState, decodeState: self.decodeState, encodeInput: c.encodeInput, decodeInput: c.decodeInput, encodeOutput: c.encodeOutput, encodeStateInto: self.encodeStateInto, encodeInputInto: c.encodeInputInto, encodeOutputInto: c.encodeOutputInto)
        }
        result.estimatedStateBytes = { state in
            guard let actualEstimate = self.actual.estimatedStateBytes,
                  let a = try actualEstimate(copied(self.actual.cloneState, state.model)),
                  let historyEstimate = self.oracle.estimatedStateBytes,
                  let b = try historyEstimate(copied(self.oracle.cloneHistory, state.oracle)),
                  let overhead = self.estimatedWrapperBytes else { return nil }
            let (payload, payloadOverflow) = a.addingReportingOverflow(b)
            let (total, totalOverflow) = payload.addingReportingOverflow(overhead)
            return payloadOverflow || totalOverflow ? nil : total
        }
        return result
    }
}

public struct Projection<S, H, O, R, V, E> {
    public let metadata: () throws -> Metadata
    public let actualState: (S) throws -> V
    public let referenceState: (H) throws -> V
    public let actualEffect: (O) throws -> E
    public let referenceEffect: (R) throws -> E
    public let equalState: (V, V) throws -> Bool
    public let equalEffect: (E, E) throws -> Bool
    public init(metadata: @escaping () throws -> Metadata, actualState: @escaping (S) throws -> V,
                referenceState: @escaping (H) throws -> V, actualEffect: @escaping (O) throws -> E,
                referenceEffect: @escaping (R) throws -> E, equalState: @escaping (V, V) throws -> Bool,
                equalEffect: @escaping (E, E) throws -> Bool) {
        self.metadata = metadata; self.actualState = actualState; self.referenceState = referenceState
        self.actualEffect = actualEffect; self.referenceEffect = referenceEffect; self.equalState = equalState; self.equalEffect = equalEffect
    }
}

/// Independent reference advancement accepts only its own state and the input.
public struct Paired<S, H, I, O, R, V, E> {
    public let reference: Model<H, I, R>
    public let projection: Projection<S, H, O, R, V, E>
    public init(reference: Model<H, I, R>, projection: Projection<S, H, O, R, V, E>) { self.reference = reference; self.projection = projection }
    public var oracle: Oracle<S, H, I, O> {
        let r = reference, p = projection
        let stateInto: (H, S, CheckSink) throws -> Void = { history, actual, sink in
            try appendStateChecks(r, history, into: sink)
            let same = try p.equalState(p.actualState(actual), p.referenceState(history))
            try sink.push(same ? .passed("pair.state") : .failed("pair.state", "projected states differ"))
        }
        let transitionInto: (OracleState<S, H>, I, Transition<OracleState<S, H>, O>, CheckSink) throws -> Void = { before, input, actual, sink in
            let expected = try perform(r, before.oracle, input)
            try appendTransitionChecks(r, before.oracle, input, expected, into: sink)
            let a = try actual.outputs.map(p.actualEffect), b = try expected.outputs.map(p.referenceEffect)
            var same = a.count == b.count
            if same { for i in a.indices { if try !p.equalEffect(a[i], b[i]) { same = false; break } } }
            try sink.push(same ? .passed("pair.effects") : .failed("pair.effects", "projected ordered effects differ"))
            try sink.push(actual.disposition == expected.disposition ? .passed("pair.outcome") : .failed("pair.outcome", "dispositions differ"))
        }
        return Oracle(metadata: {
            guard let c = r.codec else { throw ModelError("unsupported oracle adapter operation: reference metadata") }
            let m = try c.metadata(), q = try p.metadata()
            return Metadata(name: "paired-v1:\(m.name.utf8.count):\(m.name):\(m.modelVersion):\(m.propertiesVersion):\(m.codecVersion):\(q.name.utf8.count):\(q.name):\(q.modelVersion)", modelVersion: q.modelVersion, propertiesVersion: q.propertiesVersion, codecVersion: q.codecVersion, build: "\(m.build.utf8.count):\(m.build):\(q.build.utf8.count):\(q.build)")
        }, initialState: r.initialState, advance: { h, i, _, _ in try perform(r, h, i).state }, checkState: { h, a in
            var checks: [Check] = []; try CheckSink.withStorage(&checks) { try stateInto(h, a, $0) }; return checks
        }, cloneHistory: r.cloneState, equalHistory: r.equalStates, checkTransition: { before, input, actual in
            var checks: [Check] = []; try CheckSink.withStorage(&checks) { try transitionInto(before, input, actual, $0) }; return checks
        }, encodeHistory: r.codec?.encodeState, decodeHistory: r.codec?.decodeState,
           estimatedStateBytes: { history in
               guard let estimate = r.estimatedStateBytes else { return nil }
               return try estimate(copied(r.cloneState, history))
           }, checkStateInto: stateInto, checkTransitionInto: transitionInto, encodeHistoryInto: r.codec?.encodeStateInto)
    }
}
