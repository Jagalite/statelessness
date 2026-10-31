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
    /// Clone queued inputs before child state callbacks. Clone hooks must detach
    /// reference-backed values and cooperate across aliases; order is observable.
    public func cloneState(_ s:PairState<L,R,LI,RI>) throws -> PairState<L,R,LI,RI> {
        let pending = try s.pending.map(cloneMessage)
        return try PairState(left:left.cloneState(s.left),right:right.cloneState(s.right),pending:pending)
    }
    public func cloneInput(_ i:PairInput<LI,RI>) throws -> PairInput<LI,RI> { switch i { case let .local(a): return .local(try cloneMessage(a)); case .deliver: return i } }
    public func cloneOutput(_ o:PairAddress<LO,RO>) throws -> PairAddress<LO,RO> { switch o { case let .left(v): return .left(try left.cloneOutput(v)); case let .right(v): return .right(try right.cloneOutput(v)) } }
    public func initialState() throws -> PairState<L,R,LI,RI> { try cloneState(PairState(left:left.initialState(),right:right.initialState())) }
    private func addressed(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>) throws -> PairAddress<LI,RI> { switch i { case let .local(a): return a; case let .deliver(n): guard Int(n) < s.pending.count else { throw ModelError("pending message index out of range") }; return s.pending[Int(n)] } }
    public func step(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>) throws -> Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>> {
        guard s.pending.count <= maxPending else { throw ModelError("pending bound exceeded; verification incomplete") }
        let a = try cloneMessage(addressed(s,i))
        let pending = try s.pending.map(cloneMessage)
        var next = try PairState(left:left.cloneState(s.left),right:right.cloneState(s.right),pending:pending)
        if case let .deliver(n) = i { next.pending.remove(at:Int(n)) }
        let outputs:[PairAddress<LO,RO>], disposition:Disposition
        switch a {
        case let .left(v): let t = try left.step(left.cloneState(next.left),left.cloneInput(v)); next.left = try left.cloneState(t.state); outputs = try t.outputs.map { .left(try left.cloneOutput($0)) }; disposition = t.disposition
        case let .right(v): let t = try right.step(right.cloneState(next.right),right.cloneInput(v)); next.right = try right.cloneState(t.state); outputs = try t.outputs.map { .right(try right.cloneOutput($0)) }; disposition = t.disposition
        }
        for o in outputs { for message in try wiring.messages(cloneOutput(o)) { guard next.pending.count < maxPending else { throw ModelError("pending bound exceeded; verification incomplete") }; next.pending.append(try cloneMessage(message)) } }
        return Transition(state:next,outputs:outputs,disposition:disposition)
    }
    public func inputs(_ s:PairState<L,R,LI,RI>) throws -> InputIterator<PairInput<LI,RI>> {
        guard let li = left.inputs, let ri = right.inputs else { throw ModelError("pair child lacks enumeration") }
        guard s.pending.count <= maxPending else { throw ModelError("pending bound exceeded; verification incomplete") }
        let leftState = try left.cloneState(s.left),rightState = try right.cloneState(s.right),pendingCount = s.pending.count
        var phase = 0,index = 0
        var l: InputIterator<LI>?, r: InputIterator<RI>?
        return InputIterator {
            if phase == 0 {
                if l == nil { l = try li(left.cloneState(leftState)) }
                if let value = try l!.next() { return .local(.left(try left.cloneInput(value))) }
                phase = 1; l = nil
            }
            if phase == 1 {
                if r == nil { r = try ri(right.cloneState(rightState)) }
                if let value = try r!.next() { return .local(.right(try right.cloneInput(value))) }
                phase = 2; r = nil
            }
            guard index < pendingCount else { return nil }
            guard let n = UInt32(exactly:index) else { throw ModelError("message index overflow") }
            index += 1; return .deliver(n)
        }
    }
    private func namespaced(_ prefix:String,_ sink:CheckSink,_ body:(CheckSink)throws->Void) throws {
        var checks:[Check] = []; var failure:Error?
        do { try CheckSink.withStorage(&checks,body) } catch { failure = error }
        for c in checks { let id = prefix+"::"+c.id; if try sink.snapshot().contains(where:{exactText($0.id,id)}) { throw ModelError("duplicate composed property id") }; try sink.push(Check(id,c.status,c.details)) }; if let error = failure { throw error }
    }
    public func checkState(_ s:PairState<L,R,LI,RI>,into sink:CheckSink) throws {
        let snapshot = try cloneState(s)
        try namespaced("left",sink) { try appendStateChecks(left,snapshot.left,into:$0) }; try namespaced("right",sink) { try appendStateChecks(right,snapshot.right,into:$0) }; try namespaced("global",sink) { try wiring.checkState(cloneState(snapshot),$0) }
    }
    public func checkTransition(_ s:PairState<L,R,LI,RI>,_ i:PairInput<LI,RI>,_ t:Transition<PairState<L,R,LI,RI>,PairAddress<LO,RO>>,into sink:CheckSink) throws {
        // Validate the address before invoking application clone hooks, then
        // capture the selected input before state cloning can mutate its alias.
        let target = try addressed(s,i)
        if let first = t.outputs.first {
            switch (target,first) {
            case (.left,.left),(.right,.right): break
            default: throw ModelError("wrong child output address")
            }
        }
        // Preserve the observations before output callbacks, but surface their
        // failures only after the sequential child output mapping succeeds.
        let capturedInput = Result { try cloneInput(i) }
        let capturedBefore = Result { try cloneState(s) }
        let capturedAfter = Result { try cloneState(t.state) }
        var outputs: [PairAddress<LO,RO>] = []
        for output in t.outputs {
            switch (target,output) {
            case (.left,.left),(.right,.right): outputs.append(try cloneOutput(output))
            default: throw ModelError("wrong child output address")
            }
        }
        let input = try capturedInput.get(), before = try capturedBefore.get()
        let turn = try Transition(state:capturedAfter.get(),outputs:outputs,disposition:t.disposition)
        switch try addressed(before,input) {
        case let .left(v):
            let outputs:[LO] = try turn.outputs.map { guard case let .left(o) = $0 else { throw ModelError("wrong child output address") }; return o }
            try namespaced("left",sink) { try appendTransitionChecks(left,before.left,v,Transition(state:turn.state.left,outputs:outputs,disposition:turn.disposition),into:$0) }
        case let .right(v):
            let outputs:[RO] = try turn.outputs.map { guard case let .right(o) = $0 else { throw ModelError("wrong child output address") }; return o }
            try namespaced("right",sink) { try appendTransitionChecks(right,before.right,v,Transition(state:turn.state.right,outputs:outputs,disposition:turn.disposition),into:$0) }
        }
        if let callback = wiring.checkTransition { try namespaced("global",sink) { try callback(cloneState(before),cloneInput(input),Transition(state:cloneState(turn.state),outputs:turn.outputs.map(cloneOutput),disposition:turn.disposition),$0) } }
    }
    public func model() -> Model<PairState<L,R,LI,RI>,PairInput<LI,RI>,PairAddress<LO,RO>> {
        Model(initialState:initialState,step:step,checkState:{ s in var c:[Check] = []; try CheckSink.withStorage(&c) { try checkState(s,into:$0) }; return c },cloneState:cloneState,cloneInput:cloneInput,cloneOutput:cloneOutput,inputs:inputs,checkStateInto:checkState,checkTransitionInto:checkTransition)
    }
}
public extension Pair {
    func model(equalLeftInput:@escaping (LI,LI) throws -> Bool,equalRightInput:@escaping (RI,RI) throws -> Bool) -> Model<PairState<L,R,LI,RI>,PairInput<LI,RI>,PairAddress<LO,RO>> {
        var m = model(); m.equalStates = { a,b in
            guard let le = left.equalStates, let re = right.equalStates else { throw ModelError("child equality required") }
            // Capture later observations before an earlier child callback can mutate
            // their external aliases. Defer copy failures until that comparison is due.
            let ap = Result { try a.pending.map { try copied(cloneMessage,$0) } }, bp = Result { try b.pending.map { try copied(cloneMessage,$0) } }
            let al = Result { try copied(left.cloneState,a.left) }, bl = Result { try copied(left.cloneState,b.left) }
            let ar = Result { try copied(right.cloneState,a.right) }, br = Result { try copied(right.cloneState,b.right) }
            guard try le(al.get(),bl.get()) else { return false }
            guard try re(ar.get(),br.get()) else { return false }
            guard a.pending.count == b.pending.count else { return false }
            for (x,y) in zip(try ap.get(),try bp.get()) { switch (x,y) { case let (.left(i),.left(j)): if try !equalLeftInput(i,j) { return false }; case let (.right(i),.right(j)): if try !equalRightInput(i,j) { return false }; default: return false } }; return true
        }; return m
    }
}
