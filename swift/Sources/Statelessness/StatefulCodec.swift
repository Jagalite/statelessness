/// Typed recipes use the existing canonical value-codec engine and its bounds.
public func networkAddressCodec<T>(_ value:TraceValueCodec<T>) throws -> TraceValueCodec<NetworkAddress<T>> {
    guard let l = value.layout, l.size <= UInt64.max-64 else { throw ModelError("payload allocation layout required") }
    return TraceValueCodec(schema:"NetworkAddress",layout:Rust64ValueLayout(size:l.size+64,identity:"network-conservative"),encode:{ v,b in try ValueCodecs.u32.encodeRaw(v.machine,into:b); try value.encodeRaw(v.value,into:b) },decode:{ r in NetworkAddress(try r.value(ValueCodecs.u32),try r.value(value)) })
}
public func networkInputCodec<I>(_ input:TraceValueCodec<I>) throws -> TraceValueCodec<NetworkInput<I>> {
    let local = try networkAddressCodec(input)
    return TraceValueCodec(schema:"NetworkInput",encode:{ i,b in
        switch i {
        case let .local(a): try ValueCodecs.u8.encodeRaw(0,into:b); try local.encodeRaw(a,into:b)
        case let .deliver(n): try ValueCodecs.u8.encodeRaw(1,into:b); try ValueCodecs.u32.encodeRaw(n,into:b)
        case let .drop(n): try ValueCodecs.u8.encodeRaw(2,into:b); try ValueCodecs.u32.encodeRaw(n,into:b)
        case let .duplicate(n): try ValueCodecs.u8.encodeRaw(3,into:b); try ValueCodecs.u32.encodeRaw(n,into:b)
        }
    },decode:{ r in
        switch try r.value(ValueCodecs.u8) { case 0: return .local(try r.value(local)); case 1: return .deliver(try r.value(ValueCodecs.u32)); case 2: return .drop(try r.value(ValueCodecs.u32)); case 3: return .duplicate(try r.value(ValueCodecs.u32)); default: throw ModelError("network action tag") }
    })
}
public func networkStateCodec<S,I>(_ state:TraceValueCodec<S>,_ input:TraceValueCodec<I>) throws -> TraceValueCodec<NetworkState<S,I>> {
    let entry = try networkAddressCodec(state)
    guard let layout = input.layout, layout.size <= UInt64.max-128 else { throw ModelError("input allocation layout required") }
    let message = TraceValueCodec<NetworkMessage<I>>(schema:"NetworkMessage",layout:Rust64ValueLayout(size:layout.size+128,identity:"network-conservative"),encode:{ v,b in try ValueCodecs.u32.encodeRaw(v.source,into:b); try ValueCodecs.u32.encodeRaw(v.route.destination,into:b); try ValueCodecs.u32.encodeRaw(v.route.channel,into:b); try input.encodeRaw(v.route.input,into:b) },decode:{ r in
        let source = try r.value(ValueCodecs.u32), destination = try r.value(ValueCodecs.u32), channel = try r.value(ValueCodecs.u32), value = try r.value(input)
        return NetworkMessage(source:source,route:NetworkRoute(destination:destination,channel:channel,input:value))
    })
    let entries = ValueCodecs.vector(entry), messages = ValueCodecs.vector(message)
    return TraceValueCodec(schema:"NetworkState",encode:{ s,b in try ValueCodecs.u8.encodeRaw(1,into:b); try entries.encodeRaw(s.machines,into:b); try messages.encodeRaw(s.pending,into:b) },decode:{ r in
        guard try r.value(ValueCodecs.u8) == 1 else { throw ModelError("network state codec version") }
        let machines = try r.value(entries), pending = try r.value(messages)
        for i in machines.indices.dropFirst() { if machines[i-1].machine >= machines[i].machine { throw ModelError("noncanonical network machine order") } }
        // Entries are strictly ordered; use binary search without allocating an
        // uncharged identifier array or repeatedly scanning all machines.
        func known(_ id: UInt32) -> Bool {
            var low = 0,high = machines.count
            while low < high { let middle = low + (high-low)/2; if machines[middle].machine < id { low = middle+1 } else { high = middle } }
            return low < machines.count && machines[low].machine == id
        }
        if pending.contains(where:{ !known($0.source) || !known($0.route.destination) }) { throw ModelError("unknown network route machine") }
        return NetworkState(machines:machines,pending:pending)
    })
}
public extension Network {
    func codec(state:TraceValueCodec<S>,input:TraceValueCodec<I>,output:TraceValueCodec<O>,wiringMetadata:@escaping () throws -> Metadata) throws -> Codec<NetworkState<S,I>,NetworkInput<I>,NetworkAddress<O>> {
        let s = try networkStateCodec(state,input), i = try networkInputCodec(input), o = try networkAddressCodec(output)
        func metadata() throws -> Metadata {
            func identity(_ m:Metadata) -> String { "\(m.name.utf8.count):\(m.name)/\(m.modelVersion)/\(m.propertiesVersion)/\(m.codecVersion)" }
            var names = "", builds = ""
            for id in machines.keys.sorted() { guard let c = machines[id]?.codec else { throw ModelError("child metadata required") }; let m = try c.metadata(), n = identity(m); names += "\(id)=\(n.utf8.count):\(n);"; builds += "\(m.build.utf8.count):\(m.build)" }
            let w = try wiringMetadata(), n = identity(w); names += "w=\(n.utf8.count):\(n);"; builds += "\(w.build.utf8.count):\(w.build)"
            let p = policy, delivery = p.delivery == .unordered ? "Unordered" : "FifoPerChannel"
            return Metadata(name:"network-v1:\(names)policy=\(delivery),\(p.maxPending),\(p.maxPerChannel),\(p.maxRoutesPerOutput),\(p.allowDrop),\(p.allowDuplicate)",build:builds+";native")
        }
        return Codec(metadata:metadata,encodeState:{ v in try validate(v); return try s.traceBytes(v,maximum:Int.max) },decodeState:{ b in let v = try s.fromTrace(b); try validate(v); return v },encodeInput:{ try i.traceBytes($0,maximum:Int.max) },decodeInput:{ b in
            let v = try i.fromTrace(b)
            switch v { case let .local(a): guard machines[a.machine] != nil else { throw ModelError("unknown local machine") }; case .drop: guard policy.allowDrop else { throw ModelError("network dropping disabled") }; case .duplicate: guard policy.allowDuplicate else { throw ModelError("network duplication disabled") }; case .deliver: break }; return v
        },encodeOutput:{ try o.traceBytes($0,maximum:Int.max) },
        encodeStateInto:{ value,sink in try validate(value); try s.encodeRaw(value,into:sink) },
        encodeInputInto:{ value,sink in try i.encodeRaw(value,into:sink) },
        encodeOutputInto:{ value,sink in try o.encodeRaw(value,into:sink) })
    }
}
public func pairAddressCodec<L,R>(_ left:TraceValueCodec<L>,_ right:TraceValueCodec<R>) throws -> TraceValueCodec<PairAddress<L,R>> {
    guard let a = left.layout, let b = right.layout, a.size <= UInt64.max-64, b.size <= UInt64.max-64-a.size else { throw ModelError("pair payload layouts required") }
    return TraceValueCodec(schema:"PairAddress",layout:Rust64ValueLayout(size:a.size+b.size+64,identity:"pair-conservative"),encode:{ v,b in switch v { case let .left(x): try ValueCodecs.u8.encodeRaw(0,into:b); try left.encodeRaw(x,into:b); case let .right(x): try ValueCodecs.u8.encodeRaw(1,into:b); try right.encodeRaw(x,into:b) } },decode:{ r in switch try r.value(ValueCodecs.u8) { case 0: return .left(try r.value(left)); case 1: return .right(try r.value(right)); default: throw ModelError("invalid pair address tag") } })
}
public extension Pair {
    func codec(leftState:TraceValueCodec<L>,rightState:TraceValueCodec<R>,leftInput:TraceValueCodec<LI>,rightInput:TraceValueCodec<RI>,leftOutput:TraceValueCodec<LO>,rightOutput:TraceValueCodec<RO>,wiringMetadata:@escaping () throws -> Metadata) throws -> Codec<PairState<L,R,LI,RI>,PairInput<LI,RI>,PairAddress<LO,RO>> {
        let msg = try pairAddressCodec(leftInput,rightInput), output = try pairAddressCodec(leftOutput,rightOutput), queue = ValueCodecs.vector(msg)
        let state = TraceValueCodec<PairState<L,R,LI,RI>>(schema:"PairState",encode:{ s,b in try leftState.encodeRaw(s.left,into:b); try rightState.encodeRaw(s.right,into:b); try queue.encodeRaw(s.pending,into:b) },decode:{ r in PairState(left:try r.value(leftState),right:try r.value(rightState),pending:try r.value(queue)) })
        let input = TraceValueCodec<PairInput<LI,RI>>(schema:"PairInput",encode:{ i,b in switch i { case let .local(a): try ValueCodecs.u8.encodeRaw(0,into:b); try msg.encodeRaw(a,into:b); case let .deliver(n): try ValueCodecs.u8.encodeRaw(1,into:b); try ValueCodecs.u32.encodeRaw(n,into:b) } },decode:{ r in switch try r.value(ValueCodecs.u8) { case 0: return .local(try r.value(msg)); case 1: return .deliver(try r.value(ValueCodecs.u32)); default: throw ModelError("invalid composition input tag") } })
        func metadata() throws -> Metadata {
            guard let lc = left.codec, let rc = right.codec else { throw ModelError("pair child metadata required") }
            let ms = try [lc.metadata(),rc.metadata(),wiringMetadata()]
            let names = ms.map { m in "\(m.name.utf8.count):\(m.name):\(m.modelVersion)/\(m.propertiesVersion)/\(m.codecVersion)" }.map { "\($0.utf8.count):\($0)" }.joined(), builds = ms.map { "\($0.build.utf8.count):\($0.build)" }.joined()
            return Metadata(name:"pair-v1:\(names);pending=\(maxPending)",build:builds+";native")
        }
        return Codec(metadata:metadata,encodeState:{try state.traceBytes($0,maximum:Int.max)},decodeState:{try state.fromTrace($0)},encodeInput:{try input.traceBytes($0,maximum:Int.max)},decodeInput:{try input.fromTrace($0)},encodeOutput:{try output.traceBytes($0,maximum:Int.max)},encodeStateInto:{ try state.encodeRaw($0,into:$1) },encodeInputInto:{ try input.encodeRaw($0,into:$1) },encodeOutputInto:{ try output.encodeRaw($0,into:$1) })
    }
}
