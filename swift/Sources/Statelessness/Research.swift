/// Research property identity uses exact UTF-8 ID bytes, then state/transition.
public enum ResearchPropertyPhase: Int, Comparable {
    case state, transition
    public static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }
}
public struct ResearchRequiredProperty: Hashable, Comparable {
    public let id: String
    public let phase: ResearchPropertyPhase
    public init(id: String, phase: ResearchPropertyPhase) { self.id = id; self.phase = phase }
    public static func == (a: Self, b: Self) -> Bool { exactText(a.id,b.id) && a.phase == b.phase }
    public func hash(into h: inout Hasher) { h.combine(ExactString(id)); h.combine(phase.rawValue) }
    public static func < (a: Self, b: Self) -> Bool {
        if exactText(a.id,b.id) { return a.phase < b.phase }
        return a.id.utf8.lexicographicallyPrecedes(b.id.utf8)
    }
}
/// Opt-in finite research only. No report enables production pruning.
public enum Research {
    public struct Assumptions {
        public let established: Bool
        public let finiteDomain, equivalence, effects, transitionProperties, queueOrder, oracleHistory, visibleTiming: String
        public init(established: Bool, finiteDomain: String, equivalence: String, effects: String, transitionProperties: String, queueOrder: String, oracleHistory: String, visibleTiming: String) {
            self.established = established; self.finiteDomain = finiteDomain; self.equivalence = equivalence; self.effects = effects
            self.transitionProperties = transitionProperties; self.queueOrder = queueOrder; self.oracleHistory = oracleHistory; self.visibleTiming = visibleTiming
        }
    }
    public struct Proposal {
        public let name: String
        public let version: UInt32
        public let build: String
        public let assumptions: Assumptions
        public let requiredProperties: [ResearchRequiredProperty]
        public init(name: String, version: UInt32, build: String, assumptions: Assumptions, requiredProperties: [ResearchRequiredProperty]) {
            self.name = name; self.version = version; self.build = build; self.assumptions = assumptions; self.requiredProperties = requiredProperties
        }
    }
    public struct ExperimentConfig {
        public let enabled: Bool
        public let maxStates, maxEdges, maxEquivalencePairs: UInt64
        /// Bounds application equality calls, including symmetry and transitivity checks.
        public let maxProjectionComparisons: UInt64
        /// Bounds exact-state equality calls used to materialize the graph.
        public let maxStateComparisons: UInt64
        public init(enabled: Bool = false, maxStates: UInt64 = 256, maxEdges: UInt64 = 4096, maxEquivalencePairs: UInt64 = 65_536, maxProjectionComparisons: UInt64 = 1_000_000, maxStateComparisons: UInt64 = 1_000_000) {
            self.enabled = enabled; self.maxStates = maxStates; self.maxEdges = maxEdges; self.maxEquivalencePairs = maxEquivalencePairs; self.maxProjectionComparisons = maxProjectionComparisons; self.maxStateComparisons = maxStateComparisons
        }
    }
    public enum ExperimentStatus: String, Equatable {
        case disabled = "Disabled", assumptionsNotEstablished = "AssumptionsNotEstablished", incomplete = "Incomplete"
        case counterexample = "Counterexample", agreesWithinDeclaredFixture = "AgreesWithinDeclaredFixture"
    }
    public struct ViolationWitness<I> {
        public let property: ResearchRequiredProperty
        public let details: String
        public let inputs: [I]
    }
    public struct Reachability<I> {
        public fileprivate(set) var states: [Int] = []
        public fileprivate(set) var edges: UInt64 = 0
        public fileprivate(set) var violations: [ViolationWitness<I>] = []
        public fileprivate(set) var observedProperties: [ResearchRequiredProperty] = []
        public fileprivate(set) var skippedChecks: UInt64 = 0
    }
    public struct EquivalenceCounterexample<I> {
        public let leftState, rightState: Int
        public let leftPrefix, rightPrefix: [I]
        public let reason: String
    }
    public struct ExperimentReport<I> {
        public let model: Metadata
        public let proposal: Proposal
        public let config: ExperimentConfig
        public fileprivate(set) var status: ExperimentStatus = .disabled
        public let declaredStates: UInt64
        public fileprivate(set) var materializedEdges: UInt64 = 0
        public fileprivate(set) var comparedPairs: UInt64 = 0
        public fileprivate(set) var projectionComparisons: UInt64 = 0
        public fileprivate(set) var stateComparisons: UInt64 = 0
        public fileprivate(set) var exact: Reachability<I>?
        public fileprivate(set) var reduced: Reachability<I>?
        public fileprivate(set) var counterexamples: [EquivalenceCounterexample<I>] = []
        public fileprivate(set) var missingViolations: [ViolationWitness<I>] = []
        public fileprivate(set) var missingProperties: [ResearchRequiredProperty] = []
        public fileprivate(set) var limitation = "No production reduction is enabled. Evidence applies only to the explicit finite fixture and its assumptions."
    }
    private struct Edge<I,O> { let input: I; let target: Int; let outputs: [O]; let disposition: Disposition; let checks: [Check] }
    private struct Graph<I,O> { let initial: Int; let checks: [[Check]]; let edges: [[Edge<I,O>]] }
    private static func blank(_ text: String) -> Bool {
        text.unicodeScalars.allSatisfy { s in
            switch s.value { case 9...13,32,133,160,5760,8192...8202,8232,8233,8239,8287,12288: return true; default: return false }
        }
    }
    private static func validate(_ p: Proposal) throws {
        let a = p.assumptions
        guard ![a.finiteDomain,a.equivalence,a.effects,a.transitionProperties,a.queueOrder,a.oracleHistory,a.visibleTiming].contains(where:blank) else { throw ModelError("every reduction assumption needs an explicit statement") }
        guard !blank(p.name), !blank(p.build), p.version > 0 else { throw ModelError("reduction proposal needs a name, positive version, and build identity") }
        var unique = Set<ResearchRequiredProperty>()
        for p in p.requiredProperties {
            guard !blank(p.id), unique.insert(p).inserted else { throw ModelError("empty or duplicate expected research property") }
        }
    }
    private static func nativeEqual<T: Equatable>(_ a: T,_ b: T) -> Bool {
        if let a = a as? String, let b = b as? String { return exactText(a,b) }
        return a == b
    }
    /// Immutable/value-semantic projection keys may use the identity clone. For
    /// mutable reference keys supply a faithful deep clone; equality must be pure.
    public static func compareEquivalence<S: Equatable,I: Equatable,O: Equatable,K: Equatable>(
        _ model: Model<S,I,O>, states: [S], proposal: Proposal, config: ExperimentConfig = ExperimentConfig(),
        metadata: (() throws -> Metadata)? = nil, projection: (S) throws -> K,
        cloneKey: (K) throws -> K = { $0 }
    ) throws -> ExperimentReport<I> {
        try compareEquivalence(model,states:states,proposal:proposal,config:config,metadata:metadata,
            equalStates:model.equalStates ?? { nativeEqual($0,$1) },equalInputs:{ nativeEqual($0,$1) },equalOutputs:{ nativeEqual($0,$1) },equalKeys:{ nativeEqual($0,$1) },cloneKey:cloneKey,projection:projection)
    }
    /// Explicit equality supports non-Equatable application payloads. Inputs,
    /// outputs and state arguments are detached before application comparisons.
    public static func compareEquivalence<S,I,O,K>(
        _ model: Model<S,I,O>, states: [S], proposal: Proposal, config: ExperimentConfig = ExperimentConfig(),
        metadata: (() throws -> Metadata)? = nil,
        equalStates: (S,S) throws -> Bool, equalInputs: (I,I) throws -> Bool,
        equalOutputs: (O,O) throws -> Bool, equalKeys: (K,K) throws -> Bool,
        cloneKey: (K) throws -> K, projection: (S) throws -> K
    ) throws -> ExperimentReport<I> {
        try validate(proposal)
        guard let metadata = metadata ?? model.codec?.metadata else { throw ModelError("research requires metadata for a model without a codec") }
        var report = ExperimentReport<I>(model:try metadata(),proposal:proposal,config:config,declaredStates:UInt64(states.count))
        if !config.enabled { return report }
        if !proposal.assumptions.established { report.status = .assumptionsNotEstablished; return report }
        report.status = .incomplete
        guard !states.isEmpty, UInt64(states.count) <= config.maxStates else {
            report.limitation = "The explicit state domain is empty or exceeds the state budget."; return report
        }
        func stateComparison(_ a: S,_ b: S) throws -> Bool {
            guard report.stateComparisons < config.maxStateComparisons else { throw ModelError("research state comparison budget reached") }
            report.stateComparisons += 1
            return try equalStates(model.cloneState(a),model.cloneState(b))
        }
        func sameState(_ a: S,_ b: S) throws -> Bool {
            let forward = try stateComparison(a,b),reverse = try stateComparison(b,a)
            guard forward == reverse else { throw ModelError("research state equality must be symmetric") }
            return forward
        }
        var domain: [S]
        do { domain = try states.map(model.cloneState) }
        catch { report.limitation = String(describing:error); return report }
        // Rust Eq requires reflexivity; native callbacks may violate it.
        do { for state in domain { guard try stateComparison(state,state) else { throw ModelError("research state equality must be reflexive") } } }
        catch { report.limitation = String(describing:error); return report }
        // Duplicate exact states are an invalid declaration, not partial evidence.
        for i in domain.indices { for j in 0..<i {
            let same: Bool
            do { same = try sameState(domain[i],domain[j]) }
            catch { report.limitation = String(describing:error); return report }
            if same { throw ModelError("declared research domain contains duplicate exact states") }
        } }
        do {
            guard let inputs = model.inputs else { throw ModelError("research requires input enumeration") }
            let initialValue = try model.cloneState(model.initialState())
            var initial: Int?
            for i in domain.indices { if try sameState(domain[i],initialValue) { initial = i; break } }
            guard let initial else { throw ModelError("initial state is outside the declared finite domain") }
            var allChecks: [[Check]] = [], allEdges: [[Edge<I,O>]] = []
            for state in domain {
                var checks: [Check] = []
                try CheckSink.withStorage(&checks) { sink in try sink.sublease { child in
                    let copy = try model.cloneState(state)
                    if let check = model.checkStateInto { try check(copy,child) }
                    else { try child.extend(model.checkState(copy)) }
                } }
                allChecks.append(checks)
                let iterator = try inputs(model.cloneState(state))
                var outgoing: [Edge<I,O>] = []
                while let yielded = try iterator.next() {
                    guard report.materializedEdges < config.maxEdges else { throw ModelError("edge materialization budget reached; finite graph not completed") }
                    let input = try model.cloneInput(yielded)
                    // Count successful reducer returns before target/clone/check errors.
                    let raw = try model.step(model.cloneState(state),model.cloneInput(input))
                    report.materializedEdges += 1
                    let turn = try Transition(state:model.cloneState(raw.state),outputs:raw.outputs.map(model.cloneOutput),disposition:raw.disposition)
                    var target: Int?
                    for i in domain.indices { if try sameState(domain[i],turn.state) { target = i; break } }
                    guard let target else { throw ModelError("successor is outside the declared finite domain") }
                    var checks: [Check] = []
                    try CheckSink.withStorage(&checks) { sink in try sink.sublease { child in
                        if model.checkTransitionInto != nil || model.checkTransition != nil {
                            let before = try model.cloneState(state), input = try model.cloneInput(input)
                            let copy = try Transition(state:model.cloneState(turn.state),outputs:turn.outputs.map(model.cloneOutput),disposition:turn.disposition)
                            if let check = model.checkTransitionInto { try check(before,input,copy,child) }
                            else if let check = model.checkTransition { try child.extend(check(before,input,copy)) }
                        }
                    } }
                    outgoing.append(Edge(input:input,target:target,outputs:turn.outputs,disposition:turn.disposition,checks:checks))
                }
                allEdges.append(outgoing)
            }
            let graph = Graph(initial:initial,checks:allChecks,edges:allEdges)
            // Projection is invoked exactly once per declared state after full materialization.
            let keys = try domain.map { try cloneKey(projection(model.cloneState($0))) }
            func sameKey(_ a: Int,_ b: Int) throws -> Bool {
                func compare(_ left: Int,_ right: Int) throws -> Bool {
                    guard report.projectionComparisons < config.maxProjectionComparisons else { throw ModelError("research projection comparison budget reached") }
                    report.projectionComparisons += 1
                    return try equalKeys(cloneKey(keys[left]),cloneKey(keys[right]))
                }
                let forward = try compare(a,b)
                if a != b {
                    let reverse = try compare(b,a)
                    guard forward == reverse else { throw ModelError("research projection equality must be symmetric") }
                }
                return forward
            }
            // Rust Eq excludes non-reflexive host values such as NaN. Reject
            // those at the native boundary before any projected traversal.
            for i in keys.indices {
                guard try sameKey(i,i) else { throw ModelError("research projection equality must be reflexive") }
            }
            let (exact,paths) = try reach(graph,clone:model.cloneInput,equivalent:{ $0 == $1 })
            // A callable Swift equality can violate Eq's transitivity. Partition
            // reachable keys and verify every pair against the same partition;
            // an inconsistent relation must never qualify the reduction.
            var classes: [Int] = []
            for (position,state) in exact.states.enumerated() {
                var matches: [Bool] = []; matches.reserveCapacity(position)
                var matchedClass: Int?
                for earlier in 0..<position {
                    let equal = try sameKey(state,exact.states[earlier]); matches.append(equal)
                    if equal {
                        if let matchedClass,matchedClass != classes[earlier] { throw ModelError("research projection equality must be transitive") }
                        matchedClass = classes[earlier]
                    }
                }
                if let matchedClass {
                    for earlier in 0..<position where matches[earlier] != (classes[earlier] == matchedClass) { throw ModelError("research projection equality must be transitive") }
                    classes.append(matchedClass)
                } else { classes.append(position) }
            }
            let (reduced,_) = try reach(graph,clone:model.cloneInput,equivalent:sameKey)
            let reducedFailures = Set(reduced.violations.map(\.property))
            report.missingViolations = try exact.violations.filter { !reducedFailures.contains($0.property) }.map {
                ViolationWitness(property:$0.property,details:$0.details,inputs:try $0.inputs.map(model.cloneInput))
            }
            report.missingProperties = proposal.requiredProperties.filter { !exact.observedProperties.contains($0) }
            var pairLimited = false
            outer: for position in exact.states.indices {
                let left = exact.states[position]
                for right in exact.states.dropFirst(position+1) {
                    if try !sameKey(left,right) { continue }
                    if report.comparedPairs == config.maxEquivalencePairs { pairLimited = true; break outer }
                    report.comparedPairs += 1
                    if let reason = try compare(graph,left:left,right:right,sameKey:sameKey,
                        equalInput:{ try equalInputs(model.cloneInput($0),model.cloneInput($1)) },
                        equalOutput:{ try equalOutputs(model.cloneOutput($0),model.cloneOutput($1)) }) {
                        report.counterexamples.append(EquivalenceCounterexample(leftState:left,rightState:right,
                            leftPrefix:try paths[left]!.map(model.cloneInput),rightPrefix:try paths[right]!.map(model.cloneInput),reason:reason))
                    }
                }
            }
            var projectedAgrees = true
            for state in exact.states {
                var found = false
                for other in reduced.states { if try sameKey(state,other) { found = true; break } }
                if !found { projectedAgrees = false; break }
            }
            let reducedOnly = reduced.violations.contains { violation in !exact.violations.contains { $0.property == violation.property } }
            if !report.counterexamples.isEmpty || !report.missingViolations.isEmpty || !projectedAgrees || reducedOnly {
                report.status = .counterexample
                report.limitation = "The proposed equivalence changes observable behavior or property reachability in this finite fixture; it must not be used for pruning."
            } else if pairLimited { report.limitation = "Equivalence-pair budget reached; unchecked pairs cannot be assumed equivalent." }
            else if !report.missingProperties.isEmpty || exact.skippedChecks > 0 || reduced.skippedChecks > 0 || proposal.requiredProperties.isEmpty {
                report.limitation = "Expected properties were absent or checks were skipped; equivalence qualification is incomplete."
            } else { report.status = .agreesWithinDeclaredFixture }
            report.exact = exact; report.reduced = reduced
        } catch { report.status = .incomplete; report.limitation = String(describing:error) }
        return report
    }
    private static func compare<I,O>(_ graph: Graph<I,O>, left: Int, right: Int,
        sameKey: (Int,Int) throws -> Bool, equalInput: (I,I) throws -> Bool, equalOutput: (O,O) throws -> Bool) throws -> String? {
        if graph.checks[left] != graph.checks[right] { return "state checks differ" }
        if graph.edges[left].count != graph.edges[right].count { return "enabled input counts differ" }
        for (a,b) in zip(graph.edges[left],graph.edges[right]) {
            if try !equalInput(a.input,b.input) { return "enabled input labels or order differ" }
            if a.outputs.count != b.outputs.count { return "ordered effects differ" }
            for (a,b) in zip(a.outputs,b.outputs) { if try !equalOutput(a,b) { return "ordered effects differ" } }
            if a.disposition != b.disposition { return "transition disposition differs" }
            if a.checks != b.checks { return "transition checks differ" }
            if try !sameKey(a.target,b.target) { return "successor equivalence classes differ" }
        }
        return nil
    }
    private static func reach<I,O>(_ graph: Graph<I,O>, clone: (I) throws -> I,
        equivalent: (Int,Int) throws -> Bool) throws -> (Reachability<I>,[[I]?]) {
        var report = Reachability<I>(), paths = [[I]?](repeating:nil,count:graph.edges.count)
        report.states = [graph.initial]; paths[graph.initial] = []
        var queue = [graph.initial], head = 0, seen = Set<ResearchRequiredProperty>(), observed = Set<ResearchRequiredProperty>()
        func observe(_ checks: [Check],_ phase: ResearchPropertyPhase,_ inputs: [I]) throws {
            for check in checks {
                let property = ResearchRequiredProperty(id:check.id,phase:phase); observed.insert(property)
                if check.status == .failed, seen.insert(property).inserted {
                    report.violations.append(ViolationWitness(property:property,details:check.details,inputs:try inputs.map(clone)))
                } else if check.status == .skipped { report.skippedChecks = report.skippedChecks == UInt64.max ? UInt64.max : report.skippedChecks+1 }
            }
        }
        try observe(graph.checks[graph.initial],.state,[])
        while head < queue.count {
            let source = queue[head]; head += 1
            for edge in graph.edges[source] {
                report.edges += 1
                var path = try paths[source]!.map(clone); path.append(try clone(edge.input))
                try observe(graph.checks[edge.target],.state,path); try observe(edge.checks,.transition,path)
                var existing = false
                for old in report.states {
                    if old == edge.target { existing = true; break }
                    if try equivalent(old,edge.target) { existing = true; break }
                }
                if existing { continue }
                paths[edge.target] = try path.map(clone); report.states.append(edge.target); queue.append(edge.target)
            }
        }
        report.observedProperties = observed.sorted(); return (report,paths)
    }
    public static func qualificationParameters<I>(_ report: ExperimentReport<I>) -> [String:String] {
        let a = report.proposal.assumptions
        var parameters = ["research.proposal":report.proposal.name,"research.proposal_version":String(report.proposal.version),
            "research.proposal_build":report.proposal.build,"research.model_build":report.model.build,
            "research.status":report.status.rawValue,"research.declared_states":String(report.declaredStates),
            "research.materialized_edges":String(report.materializedEdges),"research.compared_pairs":String(report.comparedPairs),
            "research.finite_domain":a.finiteDomain,"research.equivalence":a.equivalence,"research.effects":a.effects,
            "research.transition_properties":a.transitionProperties,"research.queue_order":a.queueOrder,
            "research.oracle_history":a.oracleHistory,"research.visible_timing":a.visibleTiming,
            "research.limitation":report.limitation,"research.production_pruning_enabled":"false"]
        if report.config.maxProjectionComparisons != 1_000_000 {
            parameters["research.max_projection_comparisons"] = String(report.config.maxProjectionComparisons)
            parameters["research.projection_comparisons"] = String(report.projectionComparisons)
        }
        if report.config.maxStateComparisons != 1_000_000 {
            parameters["research.max_state_comparisons"] = String(report.config.maxStateComparisons)
            parameters["research.state_comparisons"] = String(report.stateComparisons)
        }
        return parameters
    }
}
