import Foundation

/// Independent declarations of what a run must establish, not generated checks.
public enum AssurancePhase: Hashable { case state, transition }
public struct AssuranceProperty: Hashable {
    public let id: ExactString
    public let phase: AssurancePhase
    public init(_ id: String, _ phase: AssurancePhase) { self.id = ExactString(id); self.phase = phase }
}
public enum AssuranceFamilyCoverage { case required, excluded(String) }
public struct AssuranceFamily {
    public let name: String
    public let coverage: AssuranceFamilyCoverage
    public init(_ name: String, _ coverage: AssuranceFamilyCoverage = .required) { self.name = name; self.coverage = coverage }
}
public enum AssuranceGoalScope { case transition, history }
public struct AssuranceGoal {
    public let id: String
    public let scope: AssuranceGoalScope
    public init(_ id: String, _ scope: AssuranceGoalScope) { self.id = id; self.scope = scope }
}
public enum AssuranceRequiredOutcome { case graphExhausted, completed }
public struct AssuranceManifest {
    public var version: UInt32 = 1
    public var model: String
    public var provenance: (manifest: String, model: String, properties: String)
    public var properties: [AssuranceProperty]
    public var families: [AssuranceFamily]
    public var goals: [AssuranceGoal]
    public var bounds: [String:String]
    public var requiredOutcome: AssuranceRequiredOutcome
    public init(model: String, provenance: (manifest: String, model: String, properties: String), properties: [AssuranceProperty], families: [AssuranceFamily], goals: [AssuranceGoal] = [], bounds: [String:String] = [:], requiredOutcome: AssuranceRequiredOutcome = .graphExhausted) {
        self.model = model; self.provenance = provenance; self.properties = properties; self.families = families; self.goals = goals; self.bounds = bounds; self.requiredOutcome = requiredOutcome
    }
    public func validate() throws {
        func nonempty(_ s: String) -> Bool { !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard version == 1, [model,provenance.manifest,provenance.model,provenance.properties].allSatisfy(nonempty) else { throw ModelError("invalid assurance identity or version") }
        guard properties.allSatisfy({ nonempty($0.id.value) }),Set(properties).count == properties.count else { throw ModelError("empty or duplicate assurance property") }
        guard families.allSatisfy({ family in
            guard nonempty(family.name) else { return false }
            if case .excluded(let reason) = family.coverage { return nonempty(reason) }; return true
        }),Set(families.map { ExactString($0.name) }).count == families.count else { throw ModelError("invalid assurance family") }
        guard goals.allSatisfy({ nonempty($0.id) }),Set(goals.map { ExactString($0.id) }).count == goals.count else { throw ModelError("invalid assurance goal") }
        guard bounds.allSatisfy({ nonempty($0.key) && nonempty($0.value) }) else { throw ModelError("invalid assurance bounds") }
    }
    /// Copy declared assumptions; this does not prove the domain was exercised.
    public mutating func addDomain(_ descriptor: DomainDescriptor) {
        bounds["domain.\(descriptor.name).assumptions"] = descriptor.assumptions
        bounds["domain.\(descriptor.name).max_entries"] = String(descriptor.maxEntries)
    }
    /// Property declarations remain independent of checks observed at runtime.
    public mutating func require(_ metadata: PropertyMetadata) {
        properties.append(AssuranceProperty(metadata.id,metadata.phase == .state ? .state : .transition))
    }
}
/// Limits retained observer evidence, not callback allocations or process RSS.
public struct AssuranceObservationLimits: Sendable {
    public var maxProperties: Int = 100_000
    public var maxErrors: Int = 1024
    public var maxCoverageHits: Int = 4096
    public var maxCoverageIdentities: Int = 100_000
    public var maxIdentityBytes: Int = 4096
    public var maxManifestStringBytes: Int = 4096
    public var maxManifestBytes: Int = 16*1024*1024
    public var maxBounds: Int = 100_000
    public var maxBudgetEntries: Int = 1024
    public var maxBudgetStringBytes: Int = 4096
    public var maxBudgetBytes: Int = 1024*1024
    public init() {}
    fileprivate func validate() throws {
        guard maxProperties >= 0,maxErrors >= 0,maxCoverageHits >= 0,maxCoverageIdentities >= 0,maxIdentityBytes >= 0,maxManifestStringBytes >= 0,maxManifestBytes >= 0,maxBounds >= 0,maxBudgetEntries >= 0,maxBudgetStringBytes >= 0,maxBudgetBytes >= 0 else { throw ConfigError("negative assurance observation limit") }
    }
}
private final class AssuranceHitsIdentity { var overflow = false }
public struct AssuranceHits {
    fileprivate let identity = AssuranceHitsIdentity()
    fileprivate var families: Set<ExactString> = []
    fileprivate var goals: Set<ExactString> = []
    fileprivate let maximum: Int
    fileprivate let maxIdentityBytes: Int
    public init() { maximum = 4096; maxIdentityBytes = 4096 }
    fileprivate init(limits: AssuranceObservationLimits) { maximum = limits.maxCoverageHits; maxIdentityBytes = limits.maxIdentityBytes }
    public mutating func family(_ name: String) {
        guard name.utf8.count <= maxIdentityBytes else { identity.overflow = true; return }
        let key = ExactString(name); if families.contains(key) { return }
        guard families.count < maximum,goals.count < maximum-families.count else { identity.overflow = true; return }; families.insert(key)
    }
    public mutating func goal(_ id: String) {
        guard id.utf8.count <= maxIdentityBytes else { identity.overflow = true; return }
        let key = ExactString(id); if goals.contains(key) { return }
        guard goals.count < maximum,families.count < maximum-goals.count else { identity.overflow = true; return }; goals.insert(key)
    }
}
public struct AssuranceCheckCounts {
    public fileprivate(set) var passed: UInt64 = 0
    public fileprivate(set) var failed: UInt64 = 0
    public fileprivate(set) var skipped: UInt64 = 0
    public fileprivate(set) var missing: UInt64 = 0
}
public enum AssurancePropertyStatus { case passed, failed, skipped, missing }
public enum AssuranceCoverageStatus { case observed, unreachableUnderDeclaredModel, notReachedBeforeStop }
public enum AssuranceFamilyStatus { case observed, missing, excluded, exclusionViolated }
/// Rust `assurance::SearchOutcome`; the raw value is its Rust Debug spelling.
public enum AssuranceSearchOutcome: String, Sendable {
    case graphExhausted = "GraphExhausted", casesCompleted = "CasesCompleted", depthLimited = "DepthLimited"
    case budgetLimited = "BudgetLimited", interrupted = "Interrupted", timedOut = "TimedOut", failed = "Failed", notStarted = "NotStarted"
    var completed: Bool { self == .graphExhausted || self == .casesCompleted }
}
/// Rust `{:?}` spelling of a string: quoted with Rust's escape_debug escapes.
/// Control and C1 scalars use `\u{hex}`; other non-printable Unicode classes
/// that Rust also escapes are not reproduced.
func assuranceRustDebug(_ value: String) -> String {
    var out = "\""
    for scalar in value.unicodeScalars {
        switch scalar {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        case "\0": out += "\\0"
        default:
            if scalar.value < 0x20 || (0x7F...0x9F).contains(scalar.value) { out += "\\u{" + String(scalar.value,radix:16) + "}" }
            else { out.unicodeScalars.append(scalar) }
        }
    }
    return out + "\""
}
/// Maps Swift's native snake-case search terminations to Rust's assurance
/// outcome and the Rust Debug spelling of the per-run termination.
func assuranceRunEvidence(_ native: String) -> (AssuranceSearchOutcome,String) {
    switch native {
    case "graph_exhausted": return (.graphExhausted,"GraphExhausted")
    case "cases_completed": return (.casesCompleted,"CasesCompleted")
    case "depth_bound": return (.depthLimited,"DepthBound")
    case "state_limit": return (.budgetLimited,"StateLimit")
    case "transition_limit": return (.budgetLimited,"TransitionLimit")
    case "estimated_state_byte_limit": return (.budgetLimited,"EstimatedStateByteLimit")
    case "failure_found": return (.failed,"FailureFound")
    case "deadline": return (.timedOut,"Deadline")
    case "cancelled": return (.interrupted,"Cancelled")
    case "model_error": return (.failed,"ModelError")
    default: return (.notStarted,native)
    }
}
public struct AssuranceAssessment {
    public let adequate: Bool
    public let reasons: [String]
    public let confidence: String
}
/// Ordinary search APIs do not implicitly make an assurance claim.
public let adequacyNotAssessed = "adequacy not assessed"
public struct AssuranceReport {
    public let manifest: AssuranceManifest
    public let metadata: Metadata
    /// Rust `search.outcome`.
    public let outcome: AssuranceSearchOutcome
    /// Rust `search.termination`: the Debug spelling of the run termination
    /// (for example `GraphExhausted` or `ModelError`), `MergedJobs` for merged
    /// reports and `Campaign::<termination>` for campaign aggregates.
    public let termination: String
    /// Rust `search.budgets`; contains only keys Rust defines for the run.
    public let budgets: [String:String]
    /// Swift-only configuration and accounting (for example
    /// `max_duration_seconds`, `assurance.*` observation limits,
    /// `auto.max_candidates` and campaign retention limits). Never exported in
    /// the Rust-shaped assurance JSON.
    public let nativeBudgets: [String:String]
    public let checkedTransitions: UInt64
    public let unclassifiedTransitions: UInt64
    public let observedProperties: [AssuranceProperty:AssuranceCheckCounts]
    public let observedFamilies: Set<ExactString>
    public let observedGoals: Set<ExactString>
    public let callbackErrors: [String]
    init(manifest: AssuranceManifest,metadata: Metadata,outcome: AssuranceSearchOutcome,termination: String,budgets: [String:String],nativeBudgets: [String:String] = [:],checkedTransitions: UInt64,unclassifiedTransitions: UInt64,observedProperties: [AssuranceProperty:AssuranceCheckCounts],observedFamilies: Set<ExactString>,observedGoals: Set<ExactString>,callbackErrors: [String]) {
        self.manifest = manifest; self.metadata = metadata; self.outcome = outcome; self.termination = termination; self.budgets = budgets; self.nativeBudgets = nativeBudgets
        self.checkedTransitions = checkedTransitions; self.unclassifiedTransitions = unclassifiedTransitions; self.observedProperties = observedProperties
        self.observedFamilies = observedFamilies; self.observedGoals = observedGoals; self.callbackErrors = callbackErrors
    }
    public func propertyStatus(_ property: AssuranceProperty) -> AssurancePropertyStatus {
        let counts = observedProperties[property] ?? AssuranceCheckCounts()
        if counts.failed > 0 { return .failed }; if counts.skipped > 0 { return .skipped }; if counts.missing > 0 || counts.passed == 0 { return .missing }; return .passed
    }
    public func familyStatus(_ family: AssuranceFamily) -> AssuranceFamilyStatus {
        let seen = observedFamilies.contains(ExactString(family.name))
        switch family.coverage { case .required: return seen ? .observed : .missing; case .excluded: return seen ? .exclusionViolated : .excluded }
    }
    public func goalStatus(_ goal: AssuranceGoal) -> AssuranceCoverageStatus {
        if observedGoals.contains(ExactString(goal.id)) { return .observed }
        if outcome == .graphExhausted,callbackErrors.isEmpty,goal.scope == .transition { return .unreachableUnderDeclaredModel }
        return .notReachedBeforeStop
    }
    /// Rust `AssuranceReport::validate`: internal evidence consistency only,
    /// not provenance or callback honesty. Swift derives the property, family
    /// and goal catalogs on demand, so they cannot diverge from the evidence.
    public func validate() throws {
        try manifest.validate()
        guard exactText(manifest.model,metadata.name),!termination.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ModelError("assurance report identity or termination is inconsistent") }
        let knownFamilies = Set(manifest.families.map { ExactString($0.name) }),knownGoals = Set(manifest.goals.map { ExactString($0.id) })
        guard observedFamilies.isSubset(of:knownFamilies),observedGoals.isSubset(of:knownGoals) else { throw ModelError("assurance coverage evidence contains an undeclared identity") }
        if checkedTransitions == 0 && (!observedFamilies.isEmpty || !observedGoals.isEmpty) { throw ModelError("assurance coverage has no checked transition evidence") }
        if unclassifiedTransitions > checkedTransitions { throw ModelError("assurance unclassified count exceeds checked transitions") }
    }
    /// Rust `AssuranceReport::assess(AssurancePolicy::Strict)`; reasons use
    /// Rust's Debug spellings and sorted catalog order.
    public func assess() -> AssuranceAssessment {
        do { try validate() } catch {
            return AssuranceAssessment(adequate:false,reasons:["inconsistent assurance report: \(error)"],confidence:"The report is internally inconsistent; no adequacy claim is established.")
        }
        var reasons: [String] = []
        if manifest.properties.isEmpty { reasons.append("no expected properties declared") }
        if !manifest.families.contains(where: { if case .required = $0.coverage { return true }; return false }) { reasons.append("no required input families declared") }
        for property in manifest.properties.sorted(by:assurancePropertyLess) {
            let status: String
            switch propertyStatus(property) { case .passed: continue; case .failed: status = "Failed"; case .skipped: status = "Skipped"; case .missing: status = "Missing" }
            reasons.append("property \(property.id.value) \(property.phase == .state ? "State" : "Transition"): \(status)")
        }
        if observedProperties.values.contains(where: { $0.failed > 0 || $0.skipped > 0 || $0.missing > 0 }) { reasons.append("observed failed, skipped, or omitted required checks") }
        for family in manifest.families.sorted(by: { $0.name.utf8.lexicographicallyPrecedes($1.name.utf8) }) {
            switch familyStatus(family) {
            case .missing: reasons.append("input family \(family.name): Missing")
            case .exclusionViolated:
                let reason: String; if case let .excluded(value) = family.coverage { reason = value } else { reason = "" }
                reasons.append("input family \(family.name): ExclusionViolated { reason: \(assuranceRustDebug(reason)) }")
            case .observed,.excluded: break
            }
        }
        for goal in manifest.goals.sorted(by: { $0.id.utf8.lexicographicallyPrecedes($1.id.utf8) }) {
            switch goalStatus(goal) {
            case .observed: break
            case .unreachableUnderDeclaredModel: reasons.append("cover goal \(goal.id): UnreachableUnderDeclaredModel")
            case .notReachedBeforeStop: reasons.append("cover goal \(goal.id): NotReachedBeforeStop")
            }
        }
        if unclassifiedTransitions > 0 { reasons.append("some checked inputs had no declared family classification") }
        if !callbackErrors.isEmpty { reasons.append("model or observation callback error") }
        let completed = manifest.requiredOutcome == .graphExhausted ? outcome == .graphExhausted : outcome.completed
        if !completed { reasons.append("required search outcome not met: \(outcome.rawValue)") }
        let adequate = reasons.isEmpty
        let confidence: String
        if adequate && outcome == .graphExhausted { confidence = "The declared properties held for the exhausted finite model and declared adequacy goals were observed." }
        else if adequate { confidence = "The declared properties held for the completed sampled cases and declared adequacy goals were observed." }
        else { confidence = "Declared assurance requirements are not fully satisfied; no broader adequacy claim is established." }
        return AssuranceAssessment(adequate: adequate,reasons: reasons,confidence:confidence)
    }
}
/// Caller-owned observer survives failed and interrupted runs. Use one per run.
public final class AssuranceObserver<S,I,O> {
    public let manifest: AssuranceManifest
    public let metadata: Metadata
    private let coverage: (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void
    private let limits: AssuranceObservationLimits
    private var counts: [AssuranceProperty:AssuranceCheckCounts] = [:]
    private var families: Set<ExactString> = [], goals: Set<ExactString> = []
    private var errors: Set<ExactString> = []
    private var transitions: UInt64 = 0,unclassified: UInt64 = 0
    private var evidenceLimited = false
    private let ownership = NSLock(), violationLock = NSLock()
    private var ownershipViolated = false
    private func acquire() throws {
        violationLock.lock(); defer { violationLock.unlock() }
        guard ownership.try() else {
            ownershipViolated = true
            throw ModelError("overlapping assurance observer use")
        }
    }
    public init(manifest: AssuranceManifest,metadata: Metadata,limits: AssuranceObservationLimits = AssuranceObservationLimits(),coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws {
        try limits.validate()
        guard manifest.properties.count <= limits.maxProperties,manifest.families.count <= limits.maxCoverageIdentities,manifest.goals.count <= limits.maxCoverageIdentities-manifest.families.count else { throw ModelError("assurance manifest exceeds observation limits") }
        guard manifest.bounds.count <= limits.maxBounds else { throw ModelError("assurance manifest exceeds observation limits") }
        var bytes = 0
        func retain(_ value: String,identity: Bool = false) throws {
            let count = value.utf8.count
            guard count <= (identity ? limits.maxIdentityBytes : limits.maxManifestStringBytes),count <= limits.maxManifestBytes-bytes else { throw ModelError("assurance manifest exceeds observation limits") }
            bytes += count
        }
        for value in [manifest.model,manifest.provenance.manifest,manifest.provenance.model,manifest.provenance.properties,metadata.name,metadata.build] { try retain(value) }
        for property in manifest.properties { try retain(property.id.value,identity:true) }
        for family in manifest.families { try retain(family.name,identity:true); if case let .excluded(reason) = family.coverage { try retain(reason) } }
        for goal in manifest.goals { try retain(goal.id,identity:true) }
        for (key,value) in manifest.bounds { try retain(key); try retain(value) }
        try manifest.validate(); guard exactText(manifest.model,metadata.name) else { throw ModelError("assurance manifest model mismatch") }
        self.manifest = manifest; self.metadata = metadata; self.coverage = coverage; self.limits = limits
    }
    private func increment(_ n: inout UInt64) { if n < UInt64.max { n += 1 } }
    private func recordError(_ message: String) {
        guard message.utf8.count <= limits.maxIdentityBytes else { evidenceLimited = true; return }
        let key = ExactString(message); if errors.contains(key) { return }
        guard errors.count < limits.maxErrors else { evidenceLimited = true; return }; errors.insert(key)
    }
    private func checks(_ phase: AssurancePhase,_ checks: [Check],_ complete: Bool) {
        var emitted: Set<ExactString> = []
        for check in checks {
            guard check.id.utf8.count <= limits.maxIdentityBytes else { evidenceLimited = true; continue }
            let id = ExactString(check.id)
            if emitted.contains(id) { recordError("ambiguous duplicate property ID in \(phase): \(check.id)") }
            else { guard emitted.count < limits.maxProperties else { evidenceLimited = true; continue }; emitted.insert(id) }
            let property = AssuranceProperty(check.id,phase); var count = counts[property] ?? AssuranceCheckCounts()
            if counts[property] == nil,counts.count >= limits.maxProperties { evidenceLimited = true; continue }
            switch check.status { case .passed: increment(&count.passed); case .failed: increment(&count.failed); case .skipped: increment(&count.skipped) }; counts[property] = count
        }
        if complete { for property in manifest.properties where property.phase == phase && !emitted.contains(property.id) { if counts[property] == nil,counts.count >= limits.maxProperties { evidenceLimited = true; continue }; var count = counts[property] ?? AssuranceCheckCounts(); increment(&count.missing); counts[property] = count } }
    }
    public func observer() -> SearchObserver<S,I,O> {
        SearchObserver(initial: { [self] _,checks in
            try self.acquire(); defer { self.ownership.unlock() }; self.checks(.state,checks,true)
        },checkError: { [self] phase,checks,complete in
            try self.acquire(); defer { self.ownership.unlock() }
            // Rust Observer::check_error records the batch only; the search
            // error itself reaches finish(error:).
            self.checks(phase == "transition" ? .transition : .state,checks,complete)
        },transition: { [self] before,input,turn,stateChecks,edgeChecks,history in
            try self.acquire(); defer { self.ownership.unlock() }
            self.checks(.state,stateChecks,true); self.checks(.transition,edgeChecks,true); self.increment(&self.transitions)
            var hits = AssuranceHits(limits:self.limits)
            let identity = hits.identity
            do { try self.coverage(before,input,turn,history,&hits) } catch { self.recordError("coverage callback: \(error)"); self.increment(&self.unclassified); return }
            if hits.identity !== identity || identity.overflow || hits.families.count > self.limits.maxCoverageHits || hits.goals.count > self.limits.maxCoverageHits-hits.families.count || hits.families.contains(where:{ $0.value.utf8.count > self.limits.maxIdentityBytes }) || hits.goals.contains(where:{ $0.value.utf8.count > self.limits.maxIdentityBytes }) { self.evidenceLimited = true; self.increment(&self.unclassified); return }
            let knownFamilies = Set(self.manifest.families.map { ExactString($0.name) }),knownGoals = Set(self.manifest.goals.map { ExactString($0.id) })
            var classified = false
            for hit in hits.families { if knownFamilies.contains(hit) { self.families.insert(hit); classified = true } else { self.recordError("undeclared input family: \(hit.value)") } }
            if !classified { self.increment(&self.unclassified) }
            for hit in hits.goals { if knownGoals.contains(hit) { self.goals.insert(hit) } else { self.recordError("undeclared cover goal: \(hit.value)") } }
        },needsHistory: { [self] in self.manifest.goals.contains { $0.scope == .history } })
    }
    /// Convenience for Swift's native snake-case search terminations; maps them
    /// to Rust's outcome and Debug termination spelling. Unknown values are
    /// retained verbatim with outcome `NotStarted`.
    public func finish(termination: String,budgets: [String:String] = [:],nativeBudgets: [String:String] = [:],error: Error? = nil) throws -> AssuranceReport {
        let (outcome,spelling) = assuranceRunEvidence(termination)
        return try finish(outcome:outcome,termination:spelling,budgets:budgets,nativeBudgets:nativeBudgets,error:error)
    }
    /// Rust `Observer::finish(outcome, termination, budgets, error)`.
    public func finish(outcome: AssuranceSearchOutcome,termination: String,budgets: [String:String] = [:],nativeBudgets: [String:String] = [:],error: Error? = nil) throws -> AssuranceReport {
        try acquire()
        guard !termination.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { ownership.unlock(); throw ModelError("assurance termination must not be empty") }
        if let error { recordError(String(describing:error)) }
        var budgetBytes = 0, budgetsFit = budgets.count <= limits.maxBudgetEntries && nativeBudgets.count <= limits.maxBudgetEntries-budgets.count
        if budgetsFit {
            for (key,value) in Array(budgets) + Array(nativeBudgets) {
                for item in [key,value] {
                    let size = item.utf8.count
                    guard size <= limits.maxBudgetStringBytes,size <= limits.maxBudgetBytes-budgetBytes else { budgetsFit = false; break }
                    budgetBytes += size
                }
                if !budgetsFit { break }
            }
        }
        if !budgetsFit || termination.utf8.count > limits.maxBudgetStringBytes { evidenceLimited = true }
        let retainedBudgets = budgetsFit ? budgets : [:], retainedNative = budgetsFit ? nativeBudgets : [:]
        let terminationFits = termination.utf8.count <= limits.maxBudgetStringBytes
        let retainedTermination = terminationFits ? termination : "ModelError", retainedOutcome = terminationFits ? outcome : .failed
        violationLock.lock()
        if ownershipViolated { recordError("overlapping assurance observer use") }
        var allErrors = errors; if evidenceLimited { allErrors.insert(ExactString("assurance evidence limit exceeded")) }
        let report = AssuranceReport(manifest:manifest,metadata:metadata,outcome:retainedOutcome,termination:retainedTermination,budgets:retainedBudgets,nativeBudgets:retainedNative,checkedTransitions:transitions,unclassifiedTransitions:unclassified,observedProperties:counts,observedFamilies:families,observedGoals:goals,callbackErrors:allErrors.map(\.value).sorted())
        ownership.unlock(); violationLock.unlock()
        return report
    }
}

/// Search failures remain available alongside the partial assurance evidence.
public struct AssuredRun<Search> {
    public let search: Result<Search,Error>
    public let assurance: AssuranceReport
}
/// Rust `control_budgets`: `max_duration_ns` and `cancellation_enabled`.
/// A TimeInterval duration adds Rust's nanosecond key only when scaling yields
/// an exactly representable whole integer; fractional or large values are
/// never silently rounded. The Swift-only `max_duration_seconds` spelling is
/// kept in the native budgets.
func assuranceControlBudgets(_ duration: TimeInterval?,cancellationEnabled: Bool,into budgets: inout [String:String],native: inout [String:String]) {
    native.removeValue(forKey: "max_duration_seconds")
    budgets.removeValue(forKey: "max_duration_seconds")
    budgets.removeValue(forKey: "max_duration_ns")
    budgets["cancellation_enabled"] = String(cancellationEnabled)
    guard let duration else { return }
    native["max_duration_seconds"] = String(duration)
    let nanos = duration * 1_000_000_000
    guard duration.isFinite,duration >= 0,nanos.isFinite,nanos >= 0,nanos <= 9_007_199_254_740_991,nanos.rounded(.towardZero) == nanos,Double(UInt64(nanos))/1_000_000_000 == duration else { return }
    budgets["max_duration_ns"] = String(UInt64(nanos))
}
func assuranceControlBudgets(_ limits: RunLimits,into budgets: inout [String:String],native: inout [String:String]) {
    assuranceControlBudgets(limits.maxDuration,cancellationEnabled:limits.cancellationEnabled,into:&budgets,native:&native)
    if let duration = limits.maxDurationNanoseconds { budgets["max_duration_ns"] = duration.decimal }
}
/// Swift-only observation limits; retained outside Rust's budgets map.
func assuranceObservationBudgets(_ limits: AssuranceObservationLimits,into native: inout [String:String]) {
    native["assurance.max_properties"] = String(limits.maxProperties)
    native["assurance.max_errors"] = String(limits.maxErrors)
    native["assurance.max_coverage_hits"] = String(limits.maxCoverageHits)
    native["assurance.max_coverage_identities"] = String(limits.maxCoverageIdentities)
    native["assurance.max_identity_bytes"] = String(limits.maxIdentityBytes)
    native["assurance.max_manifest_string_bytes"] = String(limits.maxManifestStringBytes)
    native["assurance.max_manifest_bytes"] = String(limits.maxManifestBytes)
    native["assurance.max_bounds"] = String(limits.maxBounds)
    native["assurance.max_budget_entries"] = String(limits.maxBudgetEntries)
    native["assurance.max_budget_string_bytes"] = String(limits.maxBudgetStringBytes)
    native["assurance.max_budget_bytes"] = String(limits.maxBudgetBytes)
}
private func assuranceMetadata<S,I,O>(_ model: Model<S,I,O>,_ declared: (() throws -> Metadata)?) throws -> Metadata {
    let explicit = try declared.map { try call("assurance metadata",$0) }
    if let codec = model.codec {
        let actual = try call("assurance metadata",codec.metadata)
        guard explicit == nil || explicit == actual else { throw ModelError("assurance metadata differs from model codec") }
        return actual
    }
    guard let explicit else { throw ModelError("assurance requires model metadata") }
    return explicit
}
/// Executes BFS and derives evidence identity and termination from that run.
public func assuredEnumerate<S,I,O>(_ model: Model<S,I,O>,manifest: AssuranceManifest,metadata: (() throws -> Metadata)? = nil,observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(),
    config: SearchConfig = SearchConfig(),limits: SearchLimits = SearchLimits(),
    coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws -> AssuredRun<SearchReport<I>> {
    let observer = try AssuranceObserver(manifest:manifest,metadata:assuranceMetadata(model,metadata),limits:observationLimits,coverage:coverage)
    var budgets = ["max_states":String(config.maxStates),"max_transitions":String(config.maxTransitions),"max_depth":String(config.maxDepth)]
    var native: [String:String] = [:]
    assuranceControlBudgets(limits.control,into:&budgets,native:&native)
    assuranceObservationBudgets(observationLimits,into:&native)
    if let bytes = limits.maxEstimatedStateBytes { budgets["max_estimated_state_bytes"] = String(bytes) }
    let search = Result { try enumerateStates(model,config:config,limits:limits,observer:observer.observer()) }
    let assurance: AssuranceReport
    switch search { case let .success(report): assurance = try observer.finish(termination:report.termination,budgets:budgets,nativeBudgets:native)
    case let .failure(error): assurance = try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets,nativeBudgets:native,error:error) }
    return AssuredRun(search:search,assurance:assurance)
}
/// Executes seeded fuzzing with a fresh observer and retains partial failure evidence.
public func assuredFuzz64<S,I,O>(_ generated: Generate<S,I,O>,manifest: AssuranceManifest,metadata: (() throws -> Metadata)? = nil,observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(),
    config: FuzzConfig64 = FuzzConfig64(),limits: RunLimits = RunLimits(),
    coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws -> AssuredRun<FuzzReport64<I>> {
    let observer = try AssuranceObserver(manifest:manifest,metadata:assuranceMetadata(generated.model,metadata),limits:observationLimits,coverage:coverage)
    var budgets = ["seed":String(config.seed),"cases":String(config.cases),"max_steps":String(config.maxSteps),"max_transitions":String(config.maxTransitions),"mutation_percent":String(config.mutationPercent)]
    var native: [String:String] = [:]
    assuranceControlBudgets(limits,into:&budgets,native:&native)
    assuranceObservationBudgets(observationLimits,into:&native)
    let search = Result { try fuzz64(generated,config:config,limits:limits,observer:observer.observer()) }
    let assurance: AssuranceReport
    switch search { case let .success(report): assurance = try observer.finish(termination:report.termination,budgets:budgets,nativeBudgets:native)
    case let .failure(error): assurance = try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets,nativeBudgets:native,error:error) }
    return AssuredRun(search:search,assurance:assurance)
}

private func sameAssuranceManifest(_ a: AssuranceManifest,_ b: AssuranceManifest) -> Bool {
    guard a.version == b.version,exactText(a.model,b.model),exactText(a.provenance.manifest,b.provenance.manifest),exactText(a.provenance.model,b.provenance.model),exactText(a.provenance.properties,b.provenance.properties),a.properties == b.properties,a.requiredOutcome == b.requiredOutcome,a.families.count == b.families.count,a.goals.count == b.goals.count,a.bounds.count == b.bounds.count else { return false }
    for (left,right) in zip(a.families,b.families) {
        guard exactText(left.name,right.name) else { return false }
        switch (left.coverage,right.coverage) { case (.required,.required): break; case let (.excluded(x),.excluded(y)): if !exactText(x,y) { return false }; default: return false }
    }
    for (left,right) in zip(a.goals,b.goals) { if !exactText(left.id,right.id) || left.scope != right.scope { return false } }
    let rightBounds = Dictionary(uniqueKeysWithValues:b.bounds.map { (ExactString($0.key),ExactString($0.value)) })
    for (key,value) in a.bounds { if rightBounds[ExactString(key)] != ExactString(value) { return false } }
    return true
}
private func assuranceSatAdd(_ a: UInt64,_ b: UInt64) -> UInt64 { let (sum,overflow) = a.addingReportingOverflow(b); return overflow ? UInt64.max : sum }
public struct MergedAssuranceReport {
    public let report: AssuranceReport
    public let jobs: [UInt64:AssuranceReport]
    public let missingJobs: [UInt64]
}
/// Merges only matching manifest and executable identities; absent jobs remain incomplete.
public func mergeAssuranceReports(manifest: AssuranceManifest,metadata: Metadata,expectedJobs: [UInt64],reports: [(UInt64,AssuranceReport)]) throws -> MergedAssuranceReport {
    try manifest.validate()
    guard exactText(manifest.model,metadata.name) else { throw ModelError("merge model identity mismatch") }
    let expected = Set(expectedJobs)
    guard expected.count == expectedJobs.count else { throw ModelError("duplicate expected assurance job") }
    var jobs: [UInt64:AssuranceReport] = [:]
    for (id,report) in reports {
        try report.validate()
        guard expected.contains(id) else { throw ModelError("unexpected assurance job") }
        guard sameAssuranceManifest(manifest,report.manifest),report.metadata == metadata else { throw ModelError("cannot merge different assurance manifests or executable identities") }
        guard jobs[id] == nil else { throw ModelError("duplicate assurance job report") }
        jobs[id] = report
    }
    let missing = expected.subtracting(jobs.keys).sorted()
    var properties: [AssuranceProperty:AssuranceCheckCounts] = [:],families: Set<ExactString> = [],goals: Set<ExactString> = [],errors: Set<ExactString> = []
    var transitions: UInt64 = 0,unclassified: UInt64 = 0
    for id in jobs.keys.sorted() {
        let job = jobs[id]!
        for (property,c) in job.observedProperties {
            var count = properties[property] ?? AssuranceCheckCounts()
            count.passed = assuranceSatAdd(count.passed,c.passed); count.failed = assuranceSatAdd(count.failed,c.failed)
            count.skipped = assuranceSatAdd(count.skipped,c.skipped); count.missing = assuranceSatAdd(count.missing,c.missing); properties[property] = count
        }
        families.formUnion(job.observedFamilies); goals.formUnion(job.observedGoals)
        for error in job.callbackErrors { errors.insert(ExactString("job \(id): \(error)")) }
        transitions = assuranceSatAdd(transitions,job.checkedTransitions); unclassified = assuranceSatAdd(unclassified,job.unclassifiedTransitions)
    }
    // Rust merge_reports outcome precedence.
    let outcomes = jobs.values.map(\.outcome)
    let outcome: AssuranceSearchOutcome
    if expected.isEmpty || jobs.isEmpty { outcome = .notStarted }
    else if outcomes.contains(.failed) { outcome = .failed }
    else if !missing.isEmpty { outcome = .interrupted }
    else if outcomes.allSatisfy({ $0 == .graphExhausted }) { outcome = .graphExhausted }
    else if outcomes.allSatisfy(\.completed) { outcome = .casesCompleted }
    else if outcomes.contains(.timedOut) { outcome = .timedOut }
    else if outcomes.contains(where: { $0 == .interrupted || $0 == .notStarted }) { outcome = .interrupted }
    else if outcomes.contains(.budgetLimited) { outcome = .budgetLimited }
    else { outcome = .depthLimited }
    let combined = AssuranceReport(manifest:manifest,metadata:metadata,outcome:outcome,termination:"MergedJobs",budgets:["requested_jobs":String(expected.count),"reported_jobs":String(jobs.count)],checkedTransitions:transitions,unclassifiedTransitions:unclassified,observedProperties:properties,observedFamilies:families,observedGoals:goals,callbackErrors:errors.map(\.value).sorted())
    return MergedAssuranceReport(report:combined,jobs:jobs,missingJobs:missing)
}

/// Guided runs use the same observer and capture the feedback identity actually
/// requested by the guided engine, without invoking its metadata callback twice.
public func assuredGuidedFuzz64<S,I,O,K:Hashable>(_ generated: Generate<S,I,O>,manifest: AssuranceManifest,metadata: (() throws -> Metadata)? = nil,observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(),
    config: FuzzConfig64 = FuzzConfig64(),guidance: GuidanceConfig64 = GuidanceConfig64(),
    feedback: Feedback<S,I,O,K>,limits: RunLimits = RunLimits(),
    coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws -> AssuredRun<FuzzReport64<I>> {
    let observer = try AssuranceObserver(manifest:manifest,metadata:assuranceMetadata(generated.model,metadata),limits:observationLimits,coverage:coverage)
    var budgets = ["seed":String(config.seed),"cases":String(config.cases),"max_steps":String(config.maxSteps),"max_transitions":String(config.maxTransitions),"mutation_percent":String(config.mutationPercent),"guidance.algorithm":guidedAlgorithm,"guidance.max_corpus_entries":String(guidance.maxCorpusEntries),"guidance.max_corpus_inputs":String(guidance.maxCorpusInputs),"guidance.max_features":String(guidance.maxFeatures),"guidance.max_features_per_observation":String(guidance.maxFeaturesPerObservation)]
    var native: [String:String] = [:]
    assuranceControlBudgets(limits,into:&budgets,native:&native)
    assuranceObservationBudgets(observationLimits,into:&native)
    var actualFeedback: FeedbackMetadata?
    var instrumented = feedback
    instrumented.metadata = { let identity = try feedback.metadata(); actualFeedback = identity; return identity }
    let search = Result { try guidedFuzz64(generated,config:config,guidance:guidance,feedback:instrumented,limits:limits,observer:observer.observer()) }
    if let identity = actualFeedback { budgets["guidance.feedback.name"] = identity.name; budgets["guidance.feedback.version"] = String(identity.version); budgets["guidance.feedback.build"] = identity.build }
    let assurance: AssuranceReport
    switch search { case let .success(report): assurance = try observer.finish(termination:report.termination,budgets:budgets,nativeBudgets:native)
    case let .failure(error): assurance = try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets,nativeBudgets:native,error:error) }
    return AssuredRun(search:search,assurance:assurance)
}

/// Assured automatic fuzzing shares the run control with domain scans.
public func assuredFuzzAuto64<S,I,O>(_ model: Model<S,I,O>, options: AutoOptions64 = AutoOptions64(), equalInputs: @escaping (I,I) throws -> Bool, manifest: AssuranceManifest, metadata: (() throws -> Metadata)? = nil, observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(), config: FuzzConfig64 = FuzzConfig64(), limits: RunLimits = RunLimits(), coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws -> AssuredRun<FuzzReport64<I>> {
    let observer = try AssuranceObserver(manifest:manifest,metadata:assuranceMetadata(model,metadata),limits:observationLimits,coverage:coverage)
    var budgets = ["seed":String(config.seed),"cases":String(config.cases),"max_steps":String(config.maxSteps),"max_transitions":String(config.maxTransitions),"mutation_percent":String(config.mutationPercent)]
    var native = ["auto.max_candidates":String(options.maxCandidates)]
    assuranceControlBudgets(limits,into:&budgets,native:&native); assuranceObservationBudgets(observationLimits,into:&native)
    let search = Result { try fuzzAuto64(model,options:options,equalInputs:equalInputs,config:config,limits:limits,observer:observer.observer()) }
    let assurance: AssuranceReport
    switch search { case let .success(report): assurance = try observer.finish(termination:report.termination,budgets:budgets,nativeBudgets:native)
    case let .failure(error): assurance = try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets,nativeBudgets:native,error:error) }
    return AssuredRun(search:search,assurance:assurance)
}

/// Guided counterpart retaining the actual feedback identity and partial evidence.
public func assuredGuidedFuzzAuto64<S,I,O,K:Hashable>(_ model: Model<S,I,O>, options: AutoOptions64 = AutoOptions64(), equalInputs: @escaping (I,I) throws -> Bool, manifest: AssuranceManifest, metadata: (() throws -> Metadata)? = nil, observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(), config: FuzzConfig64 = FuzzConfig64(), guidance: GuidanceConfig64 = GuidanceConfig64(), feedback: Feedback<S,I,O,K>, limits: RunLimits = RunLimits(), coverage: @escaping (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) throws -> AssuredRun<FuzzReport64<I>> {
    let observer = try AssuranceObserver(manifest:manifest,metadata:assuranceMetadata(model,metadata),limits:observationLimits,coverage:coverage)
    var budgets = ["seed":String(config.seed),"cases":String(config.cases),"max_steps":String(config.maxSteps),"max_transitions":String(config.maxTransitions),"mutation_percent":String(config.mutationPercent),"guidance.algorithm":guidedAlgorithm,"guidance.max_corpus_entries":String(guidance.maxCorpusEntries),"guidance.max_corpus_inputs":String(guidance.maxCorpusInputs),"guidance.max_features":String(guidance.maxFeatures),"guidance.max_features_per_observation":String(guidance.maxFeaturesPerObservation)]
    var native = ["auto.max_candidates":String(options.maxCandidates)]
    assuranceControlBudgets(limits,into:&budgets,native:&native); assuranceObservationBudgets(observationLimits,into:&native)
    var actual: FeedbackMetadata?; var instrumented = feedback
    instrumented.metadata = { let identity = try feedback.metadata(); actual = identity; return identity }
    let search = Result { try guidedFuzzAuto64(model,options:options,equalInputs:equalInputs,config:config,guidance:guidance,feedback:instrumented,limits:limits,observer:observer.observer()) }
    if let actual { budgets["guidance.feedback.name"] = actual.name; budgets["guidance.feedback.version"] = String(actual.version); budgets["guidance.feedback.build"] = actual.build }
    let assurance: AssuranceReport
    switch search { case let .success(report): assurance = try observer.finish(termination:report.termination,budgets:budgets,nativeBudgets:native)
    case let .failure(error): assurance = try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets,nativeBudgets:native,error:error) }
    return AssuredRun(search:search,assurance:assurance)
}

private func assurancePropertyJSON(_ p: AssuranceProperty) -> JSON { .obj(["id":.string(p.id.value),"phase":.string(p.phase == .state ? "state" : "transition")]) }
private func assuranceCountsJSON(_ c: AssuranceCheckCounts) -> JSON { .obj(["passed":.uint(c.passed),"failed":.uint(c.failed),"skipped":.uint(c.skipped),"missing":.uint(c.missing)]) }
private func assuranceGoalJSON(_ g: AssuranceGoal) -> JSON { .obj(["id":.string(g.id),"scope":.string(g.scope == .transition ? "transition" : "history")]) }
private func assuranceStrings(_ values: [String:String]) -> JSON { .object(Dictionary(uniqueKeysWithValues:values.map { (ExactString($0.key),JSON.string($0.value)) })) }
private func assurancePropertyLess(_ a: AssuranceProperty,_ b: AssuranceProperty) -> Bool {
    if a.id != b.id { return a.id.value.utf8.lexicographicallyPrecedes(b.id.value.utf8) }
    return a.phase == .state && b.phase == .transition
}
extension AssuranceManifest {
    /// Rust assurance-v1 JSON shape; declarations retain their original order.
    public func assuranceJSON() -> JSON {
        let familyJSON: [JSON] = families.map { family in
            switch family.coverage {
            case .required: return .obj(["name":.string(family.name),"coverage":.string("required")])
            case let .excluded(reason): return .obj(["name":.string(family.name),"coverage":.string("excluded"),"reason":.string(reason)])
            }
        }
        return .obj(["assurance_version":.uint(UInt64(version)),"model":.string(model),"required_properties":.array(properties.map(assurancePropertyJSON)),"input_families":.array(familyJSON),"bounds":assuranceStrings(bounds),"cover_goals":.array(goals.map(assuranceGoalJSON)),"required_search_outcome":.string(requiredOutcome == .graphExhausted ? "exhausted_within_declared_bounds" : "completed"),"provenance":.obj(["manifest":.string(provenance.manifest),"model":.string(provenance.model),"properties":.string(provenance.properties)])])
    }
}
extension AssuranceReport {
    /// Structured export preserves raw observations as well as derived verdicts.
    /// `nativeBudgets` are Swift-only and are not exported.
    public func assuranceJSON() -> JSON {
        let sortedProperties = manifest.properties.sorted(by:assurancePropertyLess)
        let properties: [JSON] = sortedProperties.map { property in
            let status: String
            switch propertyStatus(property) { case .passed: status = "Passed"; case .failed: status = "Failed"; case .skipped: status = "Skipped"; case .missing: status = "Missing" }
            return .obj(["property":assurancePropertyJSON(property),"status":.string(status),"counts":assuranceCountsJSON(observedProperties[property] ?? AssuranceCheckCounts())])
        }
        let families: [JSON] = manifest.families.sorted { $0.name.utf8.lexicographicallyPrecedes($1.name.utf8) }.map { family in
            let status: String
            switch familyStatus(family) { case .observed: status = "Observed"; case .missing: status = "Missing"; case .excluded: status = "Excluded"; case .exclusionViolated: status = "ExclusionViolated" }
            let reason: JSON; if case let .excluded(value) = family.coverage { reason = .string(value) } else { reason = .null }
            return .obj(["name":.string(family.name),"status":.string(status),"reason":reason])
        }
        let coverage: [JSON] = manifest.goals.sorted { $0.id.utf8.lexicographicallyPrecedes($1.id.utf8) }.map { goal in
            let status: String
            switch goalStatus(goal) { case .observed: status = "Observed"; case .unreachableUnderDeclaredModel: status = "UnreachableUnderDeclaredModel"; case .notReachedBeforeStop: status = "NotReachedBeforeStop" }
            return .obj(["goal":assuranceGoalJSON(goal),"status":.string(status)])
        }
        let observed = observedProperties.keys.sorted(by:assurancePropertyLess).map { property in JSON.obj(["property":assurancePropertyJSON(property),"counts":assuranceCountsJSON(observedProperties[property]!)]) }
        func strings(_ values: Set<ExactString>) -> [JSON] { values.sorted { $0.value.utf8.lexicographicallyPrecedes($1.value.utf8) }.map { .string($0.value) } }
        return .obj(["assurance_version":.uint(1),"manifest":manifest.assuranceJSON(),"metadata":.obj(["name":.string(metadata.name),"model_version":.uint(UInt64(metadata.modelVersion)),"properties_version":.uint(UInt64(metadata.propertiesVersion)),"codec_version":.uint(UInt64(metadata.codecVersion)),"build":.string(metadata.build)]),"search":.obj(["outcome":.string(outcome.rawValue),"termination":.string(termination),"budgets":assuranceStrings(budgets),"checked_transitions":.uint(checkedTransitions)]),"properties":.array(properties),"families":.array(families),"coverage":.array(coverage),"observed_properties":.array(observed),"observed_families":.array(strings(observedFamilies)),"observed_goals":.array(strings(observedGoals)),"callback_errors":.array(callbackErrors.map { .string($0) }),"unclassified_transitions":.uint(unclassifiedTransitions)])
    }
}

extension MergedAssuranceReport {
    public func assuranceJSON() -> JSON {
        let entries = Dictionary(uniqueKeysWithValues:jobs.map { (ExactString(String($0.key)),$0.value.assuranceJSON()) })
        return .obj(["assurance_version":.uint(1),"report":report.assuranceJSON(),"jobs":.object(entries),"missing_jobs":.array(missingJobs.map { .uint($0) })])
    }
}

/// Import retains the original document and the producer's exact termination
/// spelling; assessment uses its separately validated outcome.
public struct ImportedAssuranceReport {
    public let report: AssuranceReport
    public let original: JSON
}
private func assuranceReadStringMap(_ value: JSON?) throws -> [String:String] {
    guard case let .object(fields) = value else { throw FormatError("expected assurance string map") }
    var result: [String:String] = [:]
    for (key,value) in fields { result[key.value] = try readString(value) }
    guard result.count == fields.count else { throw FormatError("assurance map keys are not distinguishable in Swift") }
    return result
}
private func assuranceReadProperty(_ value: JSON) throws -> AssuranceProperty {
    let o = try objectFields(value,required:["id","phase"]), id = try readString(o["id"]),phase = try readString(o["phase"])
    switch phase { case "state": return AssuranceProperty(id,.state); case "transition": return AssuranceProperty(id,.transition); default: throw FormatError("invalid assurance property phase") }
}
private func assuranceReadGoal(_ value: JSON) throws -> AssuranceGoal {
    let o = try objectFields(value,required:["id","scope"]), id = try readString(o["id"]),scope = try readString(o["scope"])
    switch scope { case "transition": return AssuranceGoal(id,.transition); case "history": return AssuranceGoal(id,.history); default: throw FormatError("invalid assurance goal scope") }
}
private func assuranceReadCounts(_ value: JSON?) throws -> AssuranceCheckCounts {
    let o = try objectFields(value,required:["passed","failed","skipped","missing"])
    var count = AssuranceCheckCounts()
    count.passed = try readInteger(o["passed"],maximum:UInt64.max); count.failed = try readInteger(o["failed"],maximum:UInt64.max)
    count.skipped = try readInteger(o["skipped"],maximum:UInt64.max); count.missing = try readInteger(o["missing"],maximum:UInt64.max)
    return count
}
extension AssuranceManifest {
    public static func fromAssuranceJSON(_ value: JSON) throws -> AssuranceManifest {
        let o = try objectFields(value,required:["assurance_version","model","required_properties","input_families","bounds","cover_goals","required_search_outcome","provenance"])
        let version = try readInteger(o["assurance_version"],maximum:UInt64(UInt32.max))
        let properties = try readArray(o["required_properties"]).map(assuranceReadProperty)
        let families = try readArray(o["input_families"]).map { value -> AssuranceFamily in
            let fields = try objectFields(value,required:["name","coverage"],optional:["reason"]), name = try readString(fields["name"])
            switch try readString(fields["coverage"]) {
            case "required": guard fields["reason"] == nil else { throw FormatError("required family has reason") }; return AssuranceFamily(name)
            case "excluded": return AssuranceFamily(name,.excluded(try readString(fields["reason"])))
            default: throw FormatError("invalid family coverage")
            }
        }
        let provenance = try objectFields(o["provenance"],required:["manifest","model","properties"])
        let outcome: AssuranceRequiredOutcome
        switch try readString(o["required_search_outcome"]) { case "completed": outcome = .completed; case "exhausted_within_declared_bounds": outcome = .graphExhausted; default: throw FormatError("invalid required search outcome") }
        var manifest = AssuranceManifest(model:try readString(o["model"]),provenance:(try readString(provenance["manifest"]),try readString(provenance["model"]),try readString(provenance["properties"])),properties:properties,families:families,goals:try readArray(o["cover_goals"]).map(assuranceReadGoal),bounds:try assuranceReadStringMap(o["bounds"]),requiredOutcome:outcome)
        manifest.version = UInt32(version); try manifest.validate(); return manifest
    }
}
extension AssuranceReport {
    /// Rejects inconsistent derived catalogs; this validates internal consistency,
    /// not the honesty or authenticity of the producer's observations.
    public static func fromAssuranceJSON(_ value: JSON) throws -> ImportedAssuranceReport {
        let o = try objectFields(value,required:["assurance_version","manifest","metadata","search","properties","families","coverage","observed_properties","observed_families","observed_goals","callback_errors","unclassified_transitions"])
        guard try readInteger(o["assurance_version"],maximum:UInt64.max) == 1 else { throw FormatError("unsupported assurance version") }
        let manifest = try AssuranceManifest.fromAssuranceJSON(o["manifest"]!)
        let meta = try objectFields(o["metadata"],required:["name","model_version","properties_version","codec_version","build"])
        let metadata = Metadata(name:try readString(meta["name"]),modelVersion:UInt32(try readInteger(meta["model_version"],maximum:UInt64(UInt32.max))),propertiesVersion:UInt32(try readInteger(meta["properties_version"],maximum:UInt64(UInt32.max))),codecVersion:UInt32(try readInteger(meta["codec_version"],maximum:UInt64(UInt32.max))),build:try readString(meta["build"]))
        guard exactText(manifest.model,metadata.name) else { throw FormatError("assurance model identity mismatch") }
        let search = try objectFields(o["search"],required:["outcome","termination","budgets","checked_transitions"])
        let termination = try readString(search["termination"])
        guard !termination.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { throw FormatError("empty assurance termination") }
        guard let outcome = AssuranceSearchOutcome(rawValue: try readString(search["outcome"])) else { throw FormatError("unknown assurance search outcome") }
        var counts: [AssuranceProperty:AssuranceCheckCounts] = [:]
        for value in try readArray(o["observed_properties"]) {
            let item = try objectFields(value,required:["property","counts"]),property = try assuranceReadProperty(item["property"]!)
            guard counts[property] == nil else { throw FormatError("duplicate assurance property evidence") }
            counts[property] = try assuranceReadCounts(item["counts"])
        }
        func stringSet(_ value: JSON?) throws -> Set<ExactString> {
            let values = try readArray(value).map { ExactString(try readString($0)) },set = Set(values)
            guard set.count == values.count else { throw FormatError("duplicate assurance coverage evidence") }; return set
        }
        let families = try stringSet(o["observed_families"]),goals = try stringSet(o["observed_goals"])
        guard families.isSubset(of:Set(manifest.families.map { ExactString($0.name) })),goals.isSubset(of:Set(manifest.goals.map { ExactString($0.id) })) else { throw FormatError("undeclared assurance coverage evidence") }
        let checked = try readInteger(search["checked_transitions"],maximum:UInt64.max),unclassified = try readInteger(o["unclassified_transitions"],maximum:UInt64.max)
        guard unclassified <= checked,checked > 0 || (families.isEmpty && goals.isEmpty) else { throw FormatError("inconsistent assurance transition counts") }
        let report = AssuranceReport(manifest:manifest,metadata:metadata,outcome:outcome,termination:termination,budgets:try assuranceReadStringMap(search["budgets"]),checkedTransitions:checked,unclassifiedTransitions:unclassified,observedProperties:counts,observedFamilies:families,observedGoals:goals,callbackErrors:try readArray(o["callback_errors"]).map { try readString($0) })
        guard case let .object(derived) = report.assuranceJSON(),derived["properties"] == o["properties"],derived["families"] == o["families"],derived["coverage"] == o["coverage"] else { throw FormatError("assurance derived catalogs do not match evidence") }
        return ImportedAssuranceReport(report:report,original:value)
    }
}

public struct ImportedMergedAssuranceReport {
    public let report: MergedAssuranceReport
    public let original: JSON
}
extension MergedAssuranceReport {
    /// Rebuilds the aggregate from individual reports before admitting an import.
    /// Campaign-level failures may make an aggregate more conservative.
    public static func fromAssuranceJSON(_ value: JSON) throws -> ImportedMergedAssuranceReport {
        let root = try objectFields(value,required:["assurance_version","report","jobs","missing_jobs"])
        guard try readInteger(root["assurance_version"],maximum:UInt64.max) == 1 else { throw FormatError("unsupported assurance version") }
        let aggregate = try AssuranceReport.fromAssuranceJSON(root["report"]!).report
        guard case let .object(rawJobs) = root["jobs"],rawJobs.count <= 250_000 else { throw FormatError("invalid assurance jobs") }
        var reports: [(UInt64,AssuranceReport)] = []
        reports.reserveCapacity(rawJobs.count)
        for (key,job) in rawJobs {
            let name = key.value
            guard !name.isEmpty,(name.count == 1 || name.first != "0"),name.utf8.allSatisfy({ (48...57).contains($0) }),let id = UInt64(name) else { throw FormatError("invalid assurance job id") }
            reports.append((id,try AssuranceReport.fromAssuranceJSON(job).report))
        }
        let missing = try readArray(root["missing_jobs"]).map { try readInteger($0,maximum:UInt64.max) }
        guard missing == missing.sorted(),Set(missing).count == missing.count else { throw FormatError("invalid missing assurance jobs") }
        let expected = reports.map(\.0) + missing
        guard Set(expected).count == expected.count else { throw FormatError("duplicate assurance job identity") }
        if let requested = aggregate.budgets["requested_jobs"],requested != String(expected.count) { throw FormatError("merged assurance requested job count mismatch") }
        if let reported = aggregate.budgets["reported_jobs"],reported != String(reports.count) { throw FormatError("merged assurance reported job count mismatch") }
        let derived = try mergeAssuranceReports(manifest:aggregate.manifest,metadata:aggregate.metadata,expectedJobs:expected,reports:reports)
        guard derived.missingJobs == missing,aggregate.checkedTransitions == derived.report.checkedTransitions,aggregate.unclassifiedTransitions == derived.report.unclassifiedTransitions,aggregate.observedFamilies == derived.report.observedFamilies,aggregate.observedGoals == derived.report.observedGoals else { throw FormatError("merged assurance evidence does not match jobs") }
        guard case let .object(expectedJSON) = derived.report.assuranceJSON(),case let .object(actualJSON) = aggregate.assuranceJSON(),expectedJSON["observed_properties"] == actualJSON["observed_properties"] else { throw FormatError("merged assurance property counts do not match jobs") }
        let aggregateErrors = Set(aggregate.callbackErrors.map(ExactString.init))
        guard Set(derived.report.callbackErrors.map(ExactString.init)).isSubset(of:aggregateErrors) else { throw FormatError("merged assurance omitted job errors") }
        if aggregate.outcome == .graphExhausted,derived.report.outcome != .graphExhausted { throw FormatError("merged assurance outcome exceeds job evidence") }
        if aggregate.outcome == .casesCompleted,!derived.report.outcome.completed { throw FormatError("merged assurance outcome exceeds job evidence") }
        return ImportedMergedAssuranceReport(report:MergedAssuranceReport(report:aggregate,jobs:derived.jobs,missingJobs:missing),original:value)
    }
}
