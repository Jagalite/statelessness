/// Application-shaped synchronous reducer used for assurance fault controls.
/// Effects are values; executing them remains the host's responsibility.
public enum ReferenceOutcome: Equatable { case success, retryableFailure }
public enum ReferenceInput: Equatable {
    case submit, cancel(UInt64), complete(UInt64,UInt8,ReferenceOutcome), cancelled(UInt64,UInt8), tick(UInt64)
}
public enum ReferenceSettlement: Equatable { case succeeded, cancelled, failed }
public enum ReferenceEffect: Equatable {
    case start(UInt64,UInt8), cancel(UInt64,UInt8), publish(UInt64), settle(UInt64,ReferenceSettlement), release(UInt64)
}
public enum ReferencePhase: Equatable { case running, cancelling, waiting(UInt64), settled(ReferenceSettlement) }
public struct ReferenceJob: Equatable {
    public var generation: UInt64
    public var attempt: UInt8
    public var phase: ReferencePhase
    public var resourceOwned: Bool
}
public struct ReferenceState: Equatable {
    public var now: UInt64 = 0
    public var current: UInt64?
    public var jobs: [ReferenceJob] = []
    public init() {}
    public func job(_ generation: UInt64) -> ReferenceJob? { jobs.first { $0.generation == generation } }
}
public struct ReferenceBounds {
    public var generations: UInt64 = 2
    public var attempts: UInt8 = 2
    public var time: UInt64 = 2
    public init(generations: UInt64 = 2,attempts: UInt8 = 2,time: UInt64 = 2) { self.generations = generations; self.attempts = attempts; self.time = time }
    func validate() throws { guard generations > 0,attempts > 0,time < UInt64.max else { throw ModelError("positive generations/attempts and finite retry-clock headroom required") } }
}
/// These controls are opt-in; normal reduction never injects a fault.
public enum ReferenceFault: String, CaseIterable {
    case obsoletePublication = "obsolete-publication", missingCleanup = "missing-cleanup", doubleSettlement = "double-settlement"
    case droppedCancellationAck = "dropped-cancellation-ack", duplicateCompletion = "duplicate-completion", earlyRelease = "early-release"
    case skippedRetryDeadline = "skipped-retry-deadline", ignoredMutation = "ignored-mutation", reorderedEffects = "reordered-effects"
    public var target: String {
        switch self {
        case .obsoletePublication: return "controller.publication_current"
        case .missingCleanup: return "controller.cleanup_required"
        case .doubleSettlement: return "controller.settlement_once"
        case .droppedCancellationAck: return "controller.cancellation_ack"
        case .duplicateCompletion: return "controller.duplicate_inert"
        case .earlyRelease: return "controller.release_after_settlement"
        case .skippedRetryDeadline: return "controller.retry_deadline"
        case .ignoredMutation: return "controller.ignored_inert"
        case .reorderedEffects: return "controller.effect_order"
        }
    }
}
public struct ReferenceController {
    public let bounds: ReferenceBounds
    public let fault: ReferenceFault?
    public init(bounds: ReferenceBounds = ReferenceBounds(),fault: ReferenceFault? = nil) throws { try bounds.validate(); self.bounds = bounds; self.fault = fault }
    public var metadata: Metadata { Metadata(name:"reference-controller",build:"swift-reference-\(ReferenceControllerBuildID.value);generations=\(bounds.generations);attempts=\(bounds.attempts);time=\(bounds.time);fault=\(fault?.rawValue ?? "none")") }
    private func index(_ state: ReferenceState,_ generation: UInt64) -> Int? { state.jobs.firstIndex { $0.generation == generation } }
    private func finish(_ state: inout ReferenceState,_ generation: UInt64,_ result: ReferenceSettlement,_ effects: inout [ReferenceEffect]) {
        guard let i = index(state,generation) else { return }
        state.jobs[i].phase = .settled(result); state.jobs[i].resourceOwned = false
        effects.append(.settle(generation,result)); effects.append(.release(generation))
        if state.current == generation { state.current = nil }
    }
    private func cancel(_ state: inout ReferenceState,_ generation: UInt64,_ effects: inout [ReferenceEffect]) -> Bool {
        guard let i = index(state,generation) else { return false }
        switch state.jobs[i].phase {
        case .running:
            let attempt = state.jobs[i].attempt; state.jobs[i].phase = .cancelling; effects.append(.cancel(generation,attempt))
            if state.current == generation { state.current = nil }; return true
        case .waiting: finish(&state,generation,.cancelled,&effects); return true
        default: return false
        }
    }
    public func step(_ before: ReferenceState,_ input: ReferenceInput) throws -> Transition<ReferenceState,ReferenceEffect> {
        try bounds.validate(); var state = before,effects: [ReferenceEffect] = []; var accepted = false
        switch input {
        case .submit:
            if UInt64(state.jobs.count) < bounds.generations {
                if let old = state.current { _ = cancel(&state,old,&effects) }
                let generation = UInt64(state.jobs.count)+1
                state.jobs.append(ReferenceJob(generation:generation,attempt:1,phase:.running,resourceOwned:true)); state.current = generation
                effects.append(.start(generation,1)); accepted = true
            }
        case let .cancel(generation): accepted = cancel(&state,generation,&effects)
        case let .complete(generation,attempt,outcome):
            if let i = index(state,generation),state.jobs[i].attempt == attempt {
                switch state.jobs[i].phase {
                case .cancelling: finish(&state,generation,.cancelled,&effects); accepted = true
                case .running:
                    if outcome == .success { if state.current == generation { effects.append(.publish(generation)) }; finish(&state,generation,.succeeded,&effects) }
                    else if attempt < bounds.attempts && state.now < bounds.time { state.jobs[i].phase = .waiting(state.now+1) }
                    else { finish(&state,generation,.failed,&effects) }; accepted = true
                default: break
                }
            }
        case let .cancelled(generation,attempt):
            if let i = index(state,generation),state.jobs[i].attempt == attempt,state.jobs[i].phase == .cancelling { finish(&state,generation,.cancelled,&effects); accepted = true }
        case let .tick(now):
            if now > state.now,now <= bounds.time {
                state.now = now
                for i in state.jobs.indices {
                    if case let .waiting(deadline) = state.jobs[i].phase,deadline <= now {
                        let (attempt,overflow) = state.jobs[i].attempt.addingReportingOverflow(1); guard !overflow else { throw ModelError("retry attempt counter overflow") }
                        state.jobs[i].attempt = attempt; state.jobs[i].phase = .running; effects.append(.start(state.jobs[i].generation,attempt))
                    }
                }; accepted = true
            }
        }
        var disposition: Disposition = accepted ? .accepted : .ignored("outside active request/attempt/time domain")
        switch fault {
        case .obsoletePublication:
            if case let .complete(g,_,.success) = input,before.job(g)?.phase == .cancelling,before.current != g { effects.insert(.publish(g),at:0) }
        case .missingCleanup: effects.removeAll { if case .release = $0 { return true }; return false }
        case .doubleSettlement: if let effect = effects.first(where:{ if case .settle = $0 { return true }; return false }) { effects.append(effect) }
        case .droppedCancellationAck: if case .cancelled = input,accepted { state = before; effects = [] }
        case .duplicateCompletion:
            if case let .complete(g,_,_) = input,let job = before.job(g),case .settled = job.phase { effects.append(.settle(g,.succeeded)); disposition = .accepted }
        case .earlyRelease: if let effect = effects.first(where:{ if case .cancel = $0 { return true }; return false }),case let .cancel(g,_) = effect { effects.append(.release(g)) }
        case .skippedRetryDeadline: if case .tick = input,effects.contains(where:{ if case .start = $0 { return true }; return false }) { state.jobs = before.jobs; effects = [] }
        case .ignoredMutation: if case .ignored = disposition,state.now < bounds.time { state.now += 1 }
        case .reorderedEffects:
            if effects.contains(where:{ if case .cancel = $0 { return true }; return false }) && effects.contains(where:{ if case .start = $0 { return true }; return false }) { effects.reverse() }
        case nil: break
        }
        return Transition(state:state,outputs:effects,disposition:disposition)
    }
    private func check(_ name: String,_ condition: Bool) -> Check { condition ? .passed(name) : .failed(name,"independent controller obligation was violated") }
    public func checkState(_ state: ReferenceState) -> [Check] {
        let valid = state.now <= bounds.time && UInt64(state.jobs.count) <= bounds.generations && state.jobs.enumerated().allSatisfy { index,job in
            guard job.generation == UInt64(index)+1,job.attempt > 0,job.attempt <= bounds.attempts else { return false }
            if case .settled = job.phase { if job.resourceOwned { return false } } else if !job.resourceOwned { return false }
            if case let .waiting(deadline) = job.phase { return deadline > state.now && deadline <= bounds.time && job.attempt < bounds.attempts }; return true
        } && (state.current.map { generation in guard let job = state.job(generation) else { return false }; switch job.phase { case .running,.waiting: return true; default: return false } } ?? true)
        return [check("controller.state_valid",valid)]
    }
    /// Independent predicates inspect the observed turn and never execute step().
    public func checkTransition(_ before: ReferenceState,_ input: ReferenceInput,_ turn: Transition<ReferenceState,ReferenceEffect>) -> [Check] {
        func count(_ effect: ReferenceEffect) -> Int { turn.outputs.filter { $0 == effect }.count }
        func settled(_ job: ReferenceJob) -> Bool { if case .settled = job.phase { return true }; return false }
        let publication = turn.outputs.allSatisfy { effect in
            guard case let .publish(g) = effect else { return true }
            guard case let .complete(id,attempt,.success) = input,id == g,before.current == g,let job = before.job(g) else { return false }
            return job.phase == .running && job.attempt == attempt
        }
        var successful = true
        if case let .complete(g,attempt,.success) = input,before.current == g,let job = before.job(g),job.phase == .running,job.attempt == attempt { successful = count(.publish(g)) == 1 }
        let cleanup = turn.state.jobs.allSatisfy { job in guard settled(job),let old = before.job(job.generation),!settled(old) else { return true }; return count(.release(job.generation)) == 1 }
        let settlement = turn.outputs.allSatisfy { effect in
            guard case let .settle(g,result) = effect else { return true }
            return before.job(g).map { !settled($0) } == true && turn.state.job(g)?.phase == .settled(result) && turn.outputs.filter { if case let .settle(id,_) = $0 { return id == g }; return false }.count == 1
        } && turn.state.jobs.allSatisfy { job in guard case let .settled(result) = job.phase,let old = before.job(job.generation),!settled(old) else { return true }; return count(.settle(job.generation,result)) == 1 }
        var ack = true,duplicate = true,retry = true,ordering = true
        if case let .cancelled(g,attempt) = input,let job = before.job(g),job.phase == .cancelling,job.attempt == attempt { ack = turn.state.job(g)?.phase == .settled(.cancelled) && count(.settle(g,.cancelled)) == 1 }
        if case let .complete(g,attempt,_) = input {
            let valid = before.job(g).map { $0.attempt == attempt && ($0.phase == .running || $0.phase == .cancelling) } ?? false
            if !valid { if case .ignored = turn.disposition { duplicate = before == turn.state && turn.outputs.isEmpty } else { duplicate = false } }
        }
        let release = turn.outputs.enumerated().allSatisfy { i,e in
            guard case let .release(g) = e else { return true }
            return turn.outputs.prefix(i).contains { if case let .settle(id,_) = $0 { return id == g }; return false } && turn.state.job(g).map { settled($0) && !$0.resourceOwned } == true
        }
        if case let .tick(now) = input,now > before.now,now <= bounds.time {
            retry = before.jobs.allSatisfy { job in
                guard case let .waiting(deadline) = job.phase,deadline <= now else { return true }
                let (attempt,overflow) = job.attempt.addingReportingOverflow(1); guard !overflow,let next = turn.state.job(job.generation) else { return false }
                return next.attempt == attempt && next.phase == .running && count(.start(job.generation,attempt)) == 1
            }
        }
        var ignored = true; if case .ignored = turn.disposition { ignored = before == turn.state && turn.outputs.isEmpty }
        if case .submit = input,let old = before.current,let job = before.job(old),job.phase == .running,turn.state.jobs.count > before.jobs.count {
            let a = turn.outputs.firstIndex { if case let .cancel(id,_) = $0 { return id == old }; return false }
            let b = turn.outputs.firstIndex { if case let .start(id,_) = $0 { return id == turn.state.current }; return false }
            ordering = a != nil && b != nil && a! < b!
        }
        func terminal(_ generation: UInt64,_ result: ReferenceSettlement) -> Bool {
            turn.state.job(generation).map { $0.phase == .settled(result) && !$0.resourceOwned } == true && turn.state.current != generation && count(.settle(generation,result)) == 1 && count(.release(generation)) == 1
        }
        func cancellation(_ generation: UInt64) -> Bool {
            guard let old = before.job(generation) else { return true }
            switch old.phase {
            case .running:
                return turn.state.job(generation).map { $0.phase == .cancelling && $0.attempt == old.attempt && $0.resourceOwned } == true && turn.state.current != generation && count(.cancel(generation,old.attempt)) == 1
            case .waiting: return terminal(generation,.cancelled)
            default: return true
            }
        }
        var completionProgress = true,cancellationProgress = true,submissionProgress = true
        if case let .complete(g,attempt,outcome) = input,let old = before.job(g),old.attempt == attempt {
            switch old.phase {
            case .cancelling: completionProgress = turn.disposition == .accepted && terminal(g,.cancelled)
            case .running:
                let progress: Bool
                if outcome == .success { progress = terminal(g,.succeeded) }
                else if attempt < bounds.attempts && before.now < bounds.time {
                    progress = turn.state.job(g).map { $0.phase == .waiting(before.now+1) && $0.attempt == attempt && $0.resourceOwned } == true && turn.state.current == before.current
                } else { progress = terminal(g,.failed) }
                completionProgress = turn.disposition == .accepted && progress
            default: break
            }
        }
        if case let .cancel(g) = input,let old = before.job(g) {
            switch old.phase { case .running,.waiting: cancellationProgress = turn.disposition == .accepted && cancellation(g); default: break }
        }
        if case .submit = input,UInt64(before.jobs.count) < bounds.generations {
            let generation = UInt64(before.jobs.count)+1
            submissionProgress = turn.disposition == .accepted && turn.state.jobs.count == before.jobs.count+1 && turn.state.current == generation && turn.state.job(generation).map { $0.attempt == 1 && $0.phase == .running && $0.resourceOwned } == true && count(.start(generation,1)) == 1 && (before.current.map(cancellation) ?? true)
        }
        return [check("controller.publication_current",publication),check("controller.success_published",successful),check("controller.cleanup_required",cleanup),check("controller.settlement_once",settlement),check("controller.cancellation_ack",ack),check("controller.duplicate_inert",duplicate),check("controller.release_after_settlement",release),check("controller.retry_deadline",retry),check("controller.ignored_inert",ignored),check("controller.effect_order",ordering),check("controller.completion_progress",completionProgress),check("controller.cancellation_progress",cancellationProgress),check("controller.submission_progress",submissionProgress)]
    }
    public func inputs(_ state: ReferenceState) throws -> InputIterator<ReferenceInput> {
        guard UInt64(state.jobs.count) <= bounds.generations else { throw ModelError("reference generation bound exceeded") }
        var inputs: [ReferenceInput] = [.submit]
        for g in 0...UInt64(state.jobs.count) {
            inputs.append(.cancel(g))
            for attempt in 1...bounds.attempts { inputs.append(.cancelled(g,attempt)); inputs.append(.complete(g,attempt,.success)); inputs.append(.complete(g,attempt,.retryableFailure)) }
        }
        // Enumeration is explicitly bounded by the caller's modeled clock domain.
        guard bounds.time <= 1_000_000 else { throw ModelError("reference clock domain exceeds enumeration bound") }
        for now in 0...bounds.time { inputs.append(.tick(now)) }
        return InputIterator(values:inputs)
    }
    public var model: Model<ReferenceState,ReferenceInput,ReferenceEffect> {
        Model(initialState:{ ReferenceState() },step:step,checkState:checkState,cloneState:{ $0 },cloneInput:{ $0 },cloneOutput:{ $0 },checkTransition:checkTransition,inputs:inputs,equalStates:==,codec:codec)
    }
    /// Indexed sampling matches the Rust controller's ordered finite domain.
    public var generated: Generate<ReferenceState,ReferenceInput,ReferenceEffect> {
        Generate(model:model,generate:{ state,rng in
            let iterator = try self.inputs(state); var values: [ReferenceInput] = []
            while let input = try iterator.next() { values.append(input) }
            guard let index = rng.index(UInt64(values.count)) else { return nil }; return values[Int(index)]
        },isEnabled:{ state,input in
            let iterator = try self.inputs(state)
            while let value = try iterator.next() { if value == input { return true } }; return false
        },equalInputs:==)
    }
}

public enum ReferenceFaultStatus: Equatable { case killed, survives, notEvaluated(String) }
public struct ReferenceFaultResult {
    public let fault: ReferenceFault
    public let status: ReferenceFaultStatus
    public let searchTermination: String?
    public let original: Failure<ReferenceInput>?
    public let minimized: Failure<ReferenceInput>?
    public let shrinkComplete: Bool
}
/// A kill requires a specific catalog-declared transition check to survive fresh
/// causal replay and shrinking. Incomplete searches remain not evaluated.
public func evaluateReferenceFault(_ fault: ReferenceFault,requirements: ReferenceRequirements,config: SearchConfig = SearchConfig(),shrinkConfig: ShrinkConfig64 = ShrinkConfig64(),shrinkLimits: ShrinkLimits = ShrinkLimits()) -> ReferenceFaultResult {
    var termination: String?,original: Failure<ReferenceInput>?,minimized: Failure<ReferenceInput>?,complete = false
    func result(_ status: ReferenceFaultStatus) -> ReferenceFaultResult { ReferenceFaultResult(fault:fault,status:status,searchTermination:termination,original:original,minimized:minimized,shrinkComplete:complete) }
    do {
        guard requirements.properties.contains(AssuranceProperty(fault.target,.transition)) else { throw ModelError("mutation target missing from independent property catalog") }
        let controller = try ReferenceController(fault:fault)
        let report = try enumerateStates(controller.model,config:config); termination = report.termination
        guard let failure = report.failure else {
            if exactText(report.termination,"graph_exhausted"),report.skippedChecks == 0 { return result(.survives) }
            return result(.notEvaluated("incomplete search: \(report.termination); skipped=\(report.skippedChecks)"))
        }
        guard let target = failure.violations.firstIndex(where:{ exactText($0.check.id,fault.target) && exactText($0.phase,"transition") }) else { throw ModelError("search found unrelated failure before required target") }
        var violations = failure.violations; violations.swapAt(0,target)
        let selected = Failure(inputs:failure.inputs,violations:violations); original = selected
        let reduced = try shrink64(controller.generated,original:selected,config:shrinkConfig,limits:shrinkLimits)
        guard reduced.validatedOriginal,reduced.minimized.violations.contains(where:{ exactText($0.check.id,fault.target) && exactText($0.phase,"transition") }) else { throw ModelError("target property did not survive causal replay/shrinking") }
        complete = exactText(reduced.termination,"search_complete"); minimized = reduced.minimized
        return result(.killed)
    } catch { return result(.notEvaluated(String(describing:error))) }
}
