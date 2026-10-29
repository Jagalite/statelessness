public struct NetworkAddress<T> {
    public let machine: UInt32
    public let value: T
    public init(_ machine: UInt32, _ value: T) { self.machine = machine; self.value = value }
}
public struct NetworkRoute<I> {
    public let destination: UInt32
    public let channel: UInt32
    public let input: I
    public init(destination: UInt32, channel: UInt32, input: I) { self.destination = destination; self.channel = channel; self.input = input }
}
public struct NetworkMessage<I> {
    public let source: UInt32
    public let route: NetworkRoute<I>
    public init(source: UInt32, route: NetworkRoute<I>) { self.source = source; self.route = route }
    fileprivate func sameChannel(_ other: Self) -> Bool { source == other.source && route.destination == other.route.destination && route.channel == other.route.channel }
}
public enum NetworkInput<I> { case local(NetworkAddress<I>), deliver(UInt32), drop(UInt32), duplicate(UInt32) }
public struct NetworkState<S,I> {
    public var machines: [NetworkAddress<S>]
    public var pending: [NetworkMessage<I>]
    public init(machines: [NetworkAddress<S>], pending: [NetworkMessage<I>] = []) { self.machines = machines; self.pending = pending }
}
public enum DeliveryOrder { case unordered, fifoPerChannel }
public struct QueuePolicy {
    public let delivery: DeliveryOrder
    public let maxPending: Int
    public let maxPerChannel: Int
    public let maxRoutesPerOutput: Int
    public let allowDrop: Bool
    public let allowDuplicate: Bool
    public init(delivery: DeliveryOrder = .fifoPerChannel, maxPending: Int = 32, maxPerChannel: Int = 32, maxRoutesPerOutput: Int = 8, allowDrop: Bool = false, allowDuplicate: Bool = false) {
        self.delivery = delivery; self.maxPending = maxPending; self.maxPerChannel = maxPerChannel; self.maxRoutesPerOutput = maxRoutesPerOutput; self.allowDrop = allowDrop; self.allowDuplicate = allowDuplicate
    }
}
public struct NetworkWiring<S,I,O> {
    public let routes: (NetworkAddress<O>) throws -> [NetworkRoute<I>]
    public let checkState: (NetworkState<S,I>, CheckSink) throws -> Void
    public let checkTransition: ((NetworkState<S,I>, NetworkInput<I>, Transition<NetworkState<S,I>,NetworkAddress<O>>, CheckSink) throws -> Void)?
    public init(routes: @escaping (NetworkAddress<O>) throws -> [NetworkRoute<I>], checkState: @escaping (NetworkState<S,I>,CheckSink) throws -> Void, checkTransition: ((NetworkState<S,I>,NetworkInput<I>,Transition<NetworkState<S,I>,NetworkAddress<O>>,CheckSink) throws -> Void)? = nil) { self.routes = routes; self.checkState = checkState; self.checkTransition = checkTransition }
}
/// Stable machine identity; one explicit action per step. Routing is data only.
public struct Network<S,I,O> {
    public let machines: [UInt32:Model<S,I,O>]
    public let wiring: NetworkWiring<S,I,O>
    public let policy: QueuePolicy
    private let ids: [UInt32]
    public init(machines: [NetworkAddress<Model<S,I,O>>], wiring: NetworkWiring<S,I,O>, policy: QueuePolicy = QueuePolicy()) throws {
        guard !machines.isEmpty, policy.maxPending >= 0, policy.maxPending <= Int(UInt32.max), policy.maxPerChannel >= 0, policy.maxRoutesPerOutput >= 0 else { throw ModelError("invalid network configuration") }
        var registered: [UInt32:Model<S,I,O>] = [:]
        for a in machines { guard registered.updateValue(a.value,forKey:a.machine) == nil else { throw ModelError("duplicate network machine id") } }
        self.machines = registered; self.ids = registered.keys.sorted(); self.wiring = wiring; self.policy = policy
    }
    private func child(_ id: UInt32) throws -> Model<S,I,O> { guard let m = machines[id] else { throw ModelError("unknown network machine") }; return m }
    public func validate(_ s: NetworkState<S,I>) throws {
        guard s.machines.map({ $0.machine }) == ids else { throw ModelError("network state machine identity mismatch") }
        guard s.pending.count <= policy.maxPending else { throw ModelError("network pending bound exceeded; verification incomplete") }
        for m in s.pending { _ = try child(m.source); _ = try child(m.route.destination); if s.pending.filter({ $0.sameChannel(m) }).count > policy.maxPerChannel { throw ModelError("network channel bound exceeded; verification incomplete") } }
    }
    public func cloneState(_ s: NetworkState<S,I>) throws -> NetworkState<S,I> {
        NetworkState(machines: try s.machines.map { NetworkAddress($0.machine,try child($0.machine).cloneState($0.value)) }, pending: try s.pending.map { NetworkMessage(source:$0.source,route:NetworkRoute(destination:$0.route.destination,channel:$0.route.channel,input:try child($0.route.destination).cloneInput($0.route.input))) })
    }
    public func cloneInput(_ i: NetworkInput<I>) throws -> NetworkInput<I> { if case let .local(a) = i { return .local(NetworkAddress(a.machine,try child(a.machine).cloneInput(a.value))) }; return i }
    public func cloneOutput(_ o: NetworkAddress<O>) throws -> NetworkAddress<O> { NetworkAddress(o.machine,try child(o.machine).cloneOutput(o.value)) }
    public func initialState() throws -> NetworkState<S,I> { NetworkState(machines: try ids.map { id in let m = try child(id); return NetworkAddress(id,try m.cloneState(m.initialState())) }) }
    private func deliverable(_ s: NetworkState<S,I>, _ n: Int) -> Bool { policy.delivery == .unordered || !s.pending.prefix(n).contains { $0.sameChannel(s.pending[n]) } }
    private func addressed(_ s: NetworkState<S,I>, _ i: NetworkInput<I>) throws -> NetworkAddress<I>? {
        if case let .local(a) = i { _ = try child(a.machine); return a }
        let index: UInt32
        switch i { case let .deliver(n), let .drop(n), let .duplicate(n): index = n; case .local: return nil }
        guard Int(index) < s.pending.count else { throw ModelError("network pending index out of range") }
        switch i {
        case .deliver: guard deliverable(s,Int(index)) else { throw ModelError("network FIFO channel order violation") }; let m = s.pending[Int(index)]; return NetworkAddress(m.route.destination,m.route.input)
        case .drop: guard policy.allowDrop else { throw ModelError("network dropping disabled") }
        case .duplicate: guard policy.allowDuplicate else { throw ModelError("network duplication disabled") }
        case .local: break
        }; return nil
    }
    private func enqueue(_ q: inout [NetworkMessage<I>], _ m: NetworkMessage<I>) throws {
        let child = try child(m.route.destination)
        guard q.count < policy.maxPending, q.filter({ $0.sameChannel(m) }).count < policy.maxPerChannel else { throw ModelError("network queue bound exceeded; verification incomplete") }
        q.append(NetworkMessage(source:m.source,route:NetworkRoute(destination:m.route.destination,channel:m.route.channel,input:try child.cloneInput(m.route.input))))
    }
    public func step(_ s: NetworkState<S,I>, _ i: NetworkInput<I>) throws -> Transition<NetworkState<S,I>,NetworkAddress<O>> {
        try validate(s); let a = try addressed(s,i); var next = try cloneState(s)
        switch i { case let .deliver(n), let .drop(n): next.pending.remove(at:Int(n)); case let .duplicate(n): try enqueue(&next.pending,s.pending[Int(n)]); case .local: break }
        guard let a = a else { return Transition(state:next) }
        guard let position = ids.firstIndex(of:a.machine) else { throw ModelError("missing child state") }
        let m = try child(a.machine), t = try m.step(m.cloneState(s.machines[position].value),m.cloneInput(a.value))
        next.machines[position] = NetworkAddress(a.machine,try m.cloneState(t.state))
        let outputs = try t.outputs.map { try cloneOutput(NetworkAddress(a.machine,$0)) }
        for o in outputs { let routes = try wiring.routes(cloneOutput(o)); guard routes.count <= policy.maxRoutesPerOutput else { throw ModelError("network fanout bound exceeded; verification incomplete") }; for r in routes { try enqueue(&next.pending,NetworkMessage(source:a.machine,route:r)) } }
        return Transition(state:next,outputs:outputs,disposition:t.disposition)
    }
    public func inputs(_ s: NetworkState<S,I>) throws -> InputIterator<NetworkInput<I>> {
        try validate(s); var result: [NetworkInput<I>] = []
        for a in s.machines { let m = try child(a.machine); guard let inputs = m.inputs else { throw ModelError("network child lacks enumeration") }; let it = try inputs(m.cloneState(a.value)); while let i = try it.next() { result.append(.local(NetworkAddress(a.machine,try m.cloneInput(i)))) } }
        for (n,m) in s.pending.enumerated() { if deliverable(s,n) { result.append(.deliver(UInt32(n))) }; if policy.allowDrop { result.append(.drop(UInt32(n))) }; if policy.allowDuplicate && s.pending.count < policy.maxPending && s.pending.filter({ $0.sameChannel(m) }).count < policy.maxPerChannel { result.append(.duplicate(UInt32(n))) } }
        return InputIterator(values:result)
    }
    private func namespaced(_ prefix: String, _ sink: CheckSink, _ body: (CheckSink) throws -> Void) throws {
        var checks: [Check] = []; var failure: Error?
        do { try CheckSink.withStorage(&checks,body) } catch { failure = error }
        for c in checks { let id = prefix+"::"+c.id; if try sink.snapshot().contains(where:{ exactText($0.id,id) }) { throw ModelError("duplicate network property id") }; try sink.push(Check(id,c.status,c.details)) }
        if let error = failure { throw error }
    }
    public func checkState(_ s: NetworkState<S,I>, into sink: CheckSink) throws {
        try validate(s)
        for a in s.machines { try namespaced("machine/\(a.machine)",sink) { c in try appendStateChecks(child(a.machine),a.value,into:c) } }
        try namespaced("global",sink) { try wiring.checkState(cloneState(s),$0) }
    }
    public func checkTransition(_ s: NetworkState<S,I>, _ i: NetworkInput<I>, _ t: Transition<NetworkState<S,I>,NetworkAddress<O>>, into sink: CheckSink) throws {
        try validate(s); try validate(t.state)
        if let a = try addressed(s,i) {
            guard let position = ids.firstIndex(of:a.machine), t.outputs.allSatisfy({ $0.machine == a.machine }) else { throw ModelError("wrong network child output address") }
            let m = try child(a.machine), ct = Transition(state:try m.cloneState(t.state.machines[position].value),outputs:try t.outputs.map { try m.cloneOutput($0.value) },disposition:t.disposition)
            try namespaced("machine/\(a.machine)",sink) { try appendTransitionChecks(m,s.machines[position].value,a.value,ct,into:$0) }
        } else if !t.outputs.isEmpty { throw ModelError("queue action emitted child output") }
        if let callback = wiring.checkTransition { try namespaced("global",sink) { try callback(cloneState(s),cloneInput(i),Transition(state:cloneState(t.state),outputs:t.outputs.map(cloneOutput),disposition:t.disposition),$0) } }
    }
    public func model() -> Model<NetworkState<S,I>,NetworkInput<I>,NetworkAddress<O>> {
        Model(initialState:initialState,step:step,checkState:{ s in var c: [Check] = []; try CheckSink.withStorage(&c) { try checkState(s,into:$0) }; return c },cloneState:cloneState,cloneInput:cloneInput,cloneOutput:cloneOutput,inputs:inputs,checkStateInto:checkState,checkTransitionInto:checkTransition)
    }
}
public extension Network {
    func model(equalInput: @escaping (I,I) throws -> Bool) -> Model<NetworkState<S,I>,NetworkInput<I>,NetworkAddress<O>> {
        var m = model()
        m.equalStates = { a,b in
            try validate(a); try validate(b)
            for (x,y) in zip(a.machines,b.machines) { guard let equal = machines[x.machine]?.equalStates else { throw ModelError("child equality required") }; if try !equal(x.value,y.value) { return false } }
            guard a.pending.count == b.pending.count else { return false }
            for (x,y) in zip(a.pending,b.pending) { if !x.sameChannel(y) { return false }; if try !equalInput(x.route.input,y.route.input) { return false } }; return true
        }; return m
    }
}
