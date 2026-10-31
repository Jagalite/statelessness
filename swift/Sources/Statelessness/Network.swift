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
private struct NetworkChannel: Hashable {
    let source: UInt32
    let destination: UInt32
    let channel: UInt32
}
public struct NetworkMessage<I> {
    public let source: UInt32
    public let route: NetworkRoute<I>
    public init(source: UInt32, route: NetworkRoute<I>) { self.source = source; self.route = route }
    fileprivate var channelIdentity: NetworkChannel { NetworkChannel(source:source,destination:route.destination,channel:route.channel) }
    fileprivate func sameChannel(_ other: Self) -> Bool { channelIdentity == other.channelIdentity }
}
public enum NetworkInput<I> { case local(NetworkAddress<I>), deliver(UInt32), drop(UInt32), duplicate(UInt32) }
public struct NetworkState<S,I> {
    public var machines: [NetworkAddress<S>]
    public var pending: [NetworkMessage<I>]
    public init(machines: [NetworkAddress<S>], pending: [NetworkMessage<I>] = []) { self.machines = machines; self.pending = pending }
    /// Looks up a machine in canonical, strictly increasing machine-ID order.
    /// Reference-valued states retain their normal Swift aliasing semantics.
    public func machine(_ id: UInt32) -> S? {
        var lower = 0, upper = machines.count
        while lower < upper {
            let middle = lower + (upper - lower) / 2
            if machines[middle].machine < id { lower = middle + 1 } else { upper = middle }
        }
        guard lower < machines.count, machines[lower].machine == id else { return nil }
        return machines[lower].value
    }
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
    /// Registered machine IDs in ascending order. The returned array is a value snapshot.
    public var machineIDs: [UInt32] { ids }
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
        var counts: [NetworkChannel:Int] = [:]
        for m in s.pending {
            _ = try child(m.source); _ = try child(m.route.destination)
            let channel = m.channelIdentity, count = counts[channel,default:0]
            guard count < policy.maxPerChannel else { throw ModelError("network channel bound exceeded; verification incomplete") }
            counts[channel] = count + 1
        }
    }
    public func cloneState(_ s: NetworkState<S,I>) throws -> NetworkState<S,I> {
        // Capture queued inputs before invoking machine-state clone hooks, which
        // may otherwise change externally aliased pending payloads.
        let pending = try s.pending.map { NetworkMessage(source:$0.source,route:NetworkRoute(destination:$0.route.destination,channel:$0.route.channel,input:try child($0.route.destination).cloneInput($0.route.input))) }
        let states = try s.machines.map { NetworkAddress($0.machine,try child($0.machine).cloneState($0.value)) }
        return NetworkState(machines:states, pending:pending)
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
        try validate(s)
        let addressedInput = try addressed(s,i)
        // Capture input and queue before machine state clone hooks can mutate
        // externally aliased payloads. Clone hooks must still cooperate across
        // the individual values in this snapshot.
        let a = try addressedInput.map { NetworkAddress($0.machine,try child($0.machine).cloneInput($0.value)) }
        let pending = try s.pending.map { message in
            NetworkMessage(source:message.source,route:NetworkRoute(destination:message.route.destination,channel:message.route.channel,input:try child(message.route.destination).cloneInput(message.route.input)))
        }
        var next = try cloneState(NetworkState(machines:s.machines,pending:[]))
        next.pending = pending
        switch i { case let .deliver(n), let .drop(n): next.pending.remove(at:Int(n)); case let .duplicate(n): let message = next.pending[Int(n)]; try enqueue(&next.pending,message); case .local: break }
        guard let a = a else { return Transition(state:next) }
        guard let position = ids.firstIndex(of:a.machine) else { throw ModelError("missing child state") }
        let m = try child(a.machine), t = try m.step(m.cloneState(next.machines[position].value),m.cloneInput(a.value))
        next.machines[position] = NetworkAddress(a.machine,try m.cloneState(t.state))
        let outputs = try t.outputs.map { try cloneOutput(NetworkAddress(a.machine,$0)) }
        for o in outputs { let routes = try wiring.routes(cloneOutput(o)); guard routes.count <= policy.maxRoutesPerOutput else { throw ModelError("network fanout bound exceeded; verification incomplete") }; for r in routes { try enqueue(&next.pending,NetworkMessage(source:a.machine,route:r)) } }
        return Transition(state:next,outputs:outputs,disposition:t.disposition)
    }
    public func inputs(_ s: NetworkState<S,I>) throws -> InputIterator<NetworkInput<I>> {
        try validate(s)
        // Scheduling uses channel identities, not queued payloads. Capture these
        // value fields before invoking machine clone hooks, without cloning or
        // retaining application payloads solely to enumerate queue actions.
        let pending = s.pending.map(\.channelIdentity)
        let snapshot = try s.machines.map { NetworkAddress($0.machine,try child($0.machine).cloneState($0.value)) }
        var channelCounts: [NetworkChannel:Int] = [:],firstChannelIndex: [NetworkChannel:Int] = [:]
        for (index,channel) in pending.enumerated() {
            channelCounts[channel,default:0] += 1
            if firstChannelIndex[channel] == nil { firstChannelIndex[channel] = index }
        }
        for a in snapshot { guard try child(a.machine).inputs != nil else { throw ModelError("network child lacks enumeration") } }
        var machineIndex = 0,pendingIndex = 0,action = 0
        var current: InputIterator<I>?
        return InputIterator {
            while machineIndex < snapshot.count {
                let a = snapshot[machineIndex], model = try child(a.machine)
                if current == nil { current = try model.inputs!(model.cloneState(a.value)) }
                if let value = try current!.next() { return .local(NetworkAddress(a.machine,try model.cloneInput(value))) }
                current = nil; machineIndex += 1
            }
            while pendingIndex < pending.count {
                let index = pendingIndex, channel = pending[index]
                defer { action += 1; if action == 3 { action = 0; pendingIndex += 1 } }
                switch action {
                case 0: if policy.delivery == .unordered || firstChannelIndex[channel] == index { return .deliver(UInt32(index)) }
                case 1: if policy.allowDrop { return .drop(UInt32(index)) }
                default: if policy.allowDuplicate && pending.count < policy.maxPending && channelCounts[channel,default:0] < policy.maxPerChannel { return .duplicate(UInt32(index)) }
                }
            }
            return nil
        }
    }
    private func namespaced(_ prefix: String, _ sink: CheckSink, _ body: (CheckSink) throws -> Void) throws {
        var checks: [Check] = []; var failure: Error?
        do { try CheckSink.withStorage(&checks,body) } catch { failure = error }
        for c in checks { let id = prefix+"::"+c.id; if try sink.snapshot().contains(where:{ exactText($0.id,id) }) { throw ModelError("duplicate network property id") }; try sink.push(Check(id,c.status,c.details)) }
        if let error = failure { throw error }
    }
    public func checkState(_ s: NetworkState<S,I>, into sink: CheckSink) throws {
        try validate(s)
        let snapshot = try cloneState(s)
        for a in snapshot.machines { try namespaced("machine/\(a.machine)",sink) { c in try appendStateChecks(child(a.machine),a.value,into:c) } }
        try namespaced("global",sink) { try wiring.checkState(cloneState(snapshot),$0) }
    }
    public func checkTransition(_ s: NetworkState<S,I>, _ i: NetworkInput<I>, _ t: Transition<NetworkState<S,I>,NetworkAddress<O>>, into sink: CheckSink) throws {
        try validate(s); try validate(t.state)
        // Capture outputs in routing order: an earlier output copy failure takes
        // precedence over a later wrong address, as in the sequential mapping.
        let target = try addressed(s,i)
        var outputs: [NetworkAddress<O>] = []
        if let target, let first = t.outputs.first, first.machine != target.machine {
            throw ModelError("wrong network child output address")
        }
        if case nil = target, !t.outputs.isEmpty { throw ModelError("queue action emitted child output") }
        if case nil = target, wiring.checkTransition == nil { return }
        // Snapshot the remaining observations before output hooks can mutate
        // aliases, but defer their failures until output mapping has succeeded.
        let capturedInput = Result { try cloneInput(i) }
        let capturedBefore = Result { try cloneState(s) }
        let capturedAfter = Result { try cloneState(t.state) }
        if let target {
            for output in t.outputs {
                guard output.machine == target.machine else { throw ModelError("wrong network child output address") }
                outputs.append(try cloneOutput(output))
            }
        }
        let input = try capturedInput.get(), before = try capturedBefore.get()
        let turn = try Transition(state:capturedAfter.get(),outputs:outputs,disposition:t.disposition)
        if let a = try addressed(before,input) {
            guard let position = ids.firstIndex(of:a.machine), turn.outputs.allSatisfy({ $0.machine == a.machine }) else { throw ModelError("wrong network child output address") }
            let m = try child(a.machine), ct = Transition(state:try m.cloneState(turn.state.machines[position].value),outputs:turn.outputs.map { $0.value },disposition:turn.disposition)
            try namespaced("machine/\(a.machine)",sink) { try appendTransitionChecks(m,before.machines[position].value,a.value,ct,into:$0) }
        } else if !turn.outputs.isEmpty { throw ModelError("queue action emitted child output") }
        if let callback = wiring.checkTransition { try namespaced("global",sink) { try callback(cloneState(before),cloneInput(input),Transition(state:cloneState(turn.state),outputs:turn.outputs.map(cloneOutput),disposition:turn.disposition),$0) } }
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
            // Capture queue payloads before application state clone/equality callbacks.
            // Capture both queues before any state clone can mutate the other input.
            func pending(_ s: NetworkState<S,I>) throws -> [NetworkMessage<I>] {
                try s.pending.map { m in NetworkMessage(source:m.source,route:NetworkRoute(destination:m.route.destination,channel:m.route.channel,input:try child(m.route.destination).cloneInput(m.route.input))) }
            }
            let ap = try pending(a), bp = try pending(b)
            let aa = try a.machines.map { NetworkAddress($0.machine,try child($0.machine).cloneState($0.value)) }
            let bb = try b.machines.map { NetworkAddress($0.machine,try child($0.machine).cloneState($0.value)) }
            for (x,y) in zip(aa,bb) { let model = try child(x.machine); guard let equal = model.equalStates else { throw ModelError("child equality required") }; if try !equal(copied(model.cloneState,x.value),copied(model.cloneState,y.value)) { return false } }
            guard ap.count == bp.count else { return false }
            for (x,y) in zip(ap,bp) { if !x.sameChannel(y) { return false }; let model = try child(x.route.destination); if try !equalInput(copied(model.cloneInput,x.route.input),copied(model.cloneInput,y.route.input)) { return false } }; return true
        }; return m
    }
}
