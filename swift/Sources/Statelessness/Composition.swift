public enum PairAddress<L,R> { case left(L), right(R) }
public enum PairInput<L,R> { case local(PairAddress<L,R>), deliver(UInt32) }
public struct PairState<L,R,LI,RI> {
    public var left: L
    public var right: R
    public var pending: [PairAddress<LI,RI>]
    public init(left:L,right:R,pending:[PairAddress<LI,RI>] = []) { self.left = left; self.right = right; self.pending = pending }
}
public struct PairWiring<L,R,LI,RI,LO,RO> {
    public let messages: (PairAddress<LO,RO>) throws -> [PairAddress<LI,RI>]
    public let checkState: (PairState<L,R,LI,RI>,CheckSink) throws -> Void
    public let checkTransition: ((PairState<L,R,LI,RI>,PairInput<LI,RI>,Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,CheckSink) throws -> Void)?
    public init(messages:@escaping (PairAddress<LO,RO>) throws -> [PairAddress<LI,RI>],checkState:@escaping (PairState<L,R,LI,RI>,CheckSink) throws -> Void,checkTransition:((PairState<L,R,LI,RI>,PairInput<LI,RI>,Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,CheckSink) throws -> Void)? = nil) { self.messages = messages; self.checkState = checkState; self.checkTransition = checkTransition }
}
public struct Pair<L,R,LI,RI,LO,RO> {
    public let left: Model<L,LI,LO>
    public let right: Model<R,RI,RO>
    public let wiring: PairWiring<L,R,LI,RI,LO,RO>
    public let maxPending: Int
    public init(left:Model<L,LI,LO>,right:Model<R,RI,RO>,wiring:PairWiring<L,R,LI,RI,LO,RO>,maxPending:Int = 32) throws { guard maxPending >= 0 else { throw ModelError("invalid pending bound") }; self.left = left; self.right = right; self.wiring = wiring; self.maxPending = maxPending }
    private func cloneMessage(_ a:PairAddress<LI,RI>) throws -> PairAddress<LI,RI> { switch a { case let .left(v): return .left(try left.cloneInput(v)); case let .right(v): return .right(try right.cloneInput(v)) } }
    public func cloneState(_ s:PairState<L,R,LI,RI>) throws -> PairState<L,R,LI,RI> { PairState(left:try left.cloneState(s.left),right:try right.cloneState(s.right),pending:try s.pending.map(cloneMessage)) }
    public func cloneInput(_ i:PairInput<LI,RI>) throws -> PairInput<LI,RI> { switch i { case let .local(a): return .local(try cloneMessage(a)); case .deliver: return i } }
    public func cloneOutput(_ o:PairAddress<LO,RO>) throws -> PairAddress<LO,RO> { switch o { case let .left(v): return .left(try left.cloneOutput(v)); case let .right(v): return .right(try right.cloneOutput(v)) } }
    public func initialState() throws -> PairState<L,R,LI,RI> { try cloneState(PairState(left:left.initialState(),right:right.initialState())) }
    private func addressed(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>) throws -> PairAddress<LI,RI> { switch i { case let .local(a): return a; case let .deliver(n): guard Int(n) < s.pending.count else { throw ModelError("pending message index out of range") }; return s.pending[Int(n)] } }
    public func step(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>) throws -> Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>> {
        guard s.pending.count <= maxPending else { throw ModelError("pending bound exceeded; verification incomplete") }; let a = try addressed(s,i); var next = try cloneState(s)
        if case let .deliver(n) = i { next.pending.remove(at:Int(n)) }
        let outputs:[PairAddress<LO,RO>], disposition:Disposition
        switch a {
        case let .left(v): let t = try left.step(left.cloneState(s.left),left.cloneInput(v)); next.left = try left.cloneState(t.state); outputs = try t.outputs.map { .left(try left.cloneOutput($0)) }; disposition = t.disposition
        case let .right(v): let t = try right.step(right.cloneState(s.right),right.cloneInput(v)); next.right = try right.cloneState(t.state); outputs = try t.outputs.map { .right(try right.cloneOutput($0)) }; disposition = t.disposition
        }
        for o in outputs { for message in try wiring.messages(cloneOutput(o)) { guard next.pending.count < maxPending else { throw ModelError("pending bound exceeded; verification incomplete") }; next.pending.append(try cloneMessage(message)) } }
        return Transition(state:next,outputs:outputs,disposition:disposition)
    }
    public func inputs(_ s:PairState<L,R,LI,RI>) throws -> InputIterator<PairInput<LI,RI>> {
        guard let li = left.inputs, let ri = right.inputs else { throw ModelError("pair child lacks enumeration") }; var result:[PairInput<LI,RI>] = []
        let l = try li(left.cloneState(s.left)); while let i = try l.next() { result.append(.local(.left(try left.cloneInput(i)))) }
        let r = try ri(right.cloneState(s.right)); while let i = try r.next() { result.append(.local(.right(try right.cloneInput(i)))) }
        for n in s.pending.indices { guard let index = UInt32(exactly:n) else { throw ModelError("message index overflow") }; result.append(.deliver(index)) }; return InputIterator(values:result)
    }
    private func namespaced(_ prefix:String,_ sink:CheckSink,_ body:(CheckSink)throws->Void) throws {
        var checks:[Check] = []; var failure:Error?
        do { try CheckSink.withStorage(&checks,body) } catch { failure = error }
        for c in checks { let id = prefix+"::"+c.id; if try sink.snapshot().contains(where:{exactText($0.id,id)}) { throw ModelError("duplicate composed property id") }; try sink.push(Check(id,c.status,c.details)) }; if let error = failure { throw error }
    }
    public func checkState(_ s:PairState<L,R,LI,RI>,into sink:CheckSink) throws {
        try namespaced("left",sink) { try appendStateChecks(left,s.left,into:$0) }; try namespaced("right",sink) { try appendStateChecks(right,s.right,into:$0) }; try namespaced("global",sink) { try wiring.checkState(cloneState(s),$0) }
    }
    public func checkTransition(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>,_ t:Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,into sink:CheckSink) throws {
        switch try addressed(s,i) {
        case let .left(v):
            let outputs:[LO] = try t.outputs.map { guard case let .left(o) = $0 else { throw ModelError("wrong child output address") }; return try left.cloneOutput(o) }
            try namespaced("left",sink) { try appendTransitionChecks(left,s.left,v,Transition(state:t.state.left,outputs:outputs,disposition:t.disposition),into:$0) }
        case let .right(v):
            let outputs:[RO] = try t.outputs.map { guard case let .right(o) = $0 else { throw ModelError("wrong child output address") }; return try right.cloneOutput(o) }
            try namespaced("right",sink) { try appendTransitionChecks(right,s.right,v,Transition(state:t.state.right,outputs:outputs,disposition:t.disposition),into:$0) }
        }
        if let callback = wiring.checkTransition { try namespaced("global",sink) { try callback(cloneState(s),cloneInput(i),Transition(state:cloneState(t.state),outputs:t.outputs.map(cloneOutput),disposition:t.disposition),$0) } }
    }
    public func model() -> Model<PairState<L,R,LI,RI>,PairInput<LI,RI>,PairAddress<LO,RO>> {
        Model(initialState:initialState,step:step,checkState:{ s in var c:[Check] = []; try CheckSink.withStorage(&c) { try checkState(s,into:$0) }; return c },cloneState:cloneState,cloneInput:cloneInput,cloneOutput:cloneOutput,inputs:inputs,checkStateInto:checkState,checkTransitionInto:checkTransition)
    }
}
public extension Pair {
    func model(equalLeftInput:@escaping (LI,LI) throws -> Bool,equalRightInput:@escaping (RI,RI) throws -> Bool) -> Model<PairState<L,R,LI,RI>,PairInput<LI,RI>,PairAddress<LO,RO>> {
        var m = model(); m.equalStates = { a,b in
            guard let le = left.equalStates, let re = right.equalStates else { throw ModelError("child equality required") }
            guard try le(a.left,b.left) && re(a.right,b.right), a.pending.count == b.pending.count else { return false }
            for (x,y) in zip(a.pending,b.pending) { switch (x,y) { case let (.left(i),.left(j)): if try !equalLeftInput(i,j) { return false }; case let (.right(i),.right(j)): if try !equalRightInput(i,j) { return false }; default: return false } }; return true
        }; return m
    }
}
