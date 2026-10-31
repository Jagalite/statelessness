import Foundation

/// Caller-supplied independent requirement artifacts. The review status is
/// reported as declared; loading them does not confer independent review.
public struct ReferenceRequirements {
    public let properties: [AssuranceProperty]
    public let goals: [String]
    public let reviewStatus: String
    public let manifestFingerprint: String
    public let propertyFingerprint: String
    /// Exact requirement-catalog artifacts embedded in the Swift package; their
    /// declared review status is retained and does not imply completed review.
    public static func bundled() throws -> Self {
        try Self(propertyCatalog:ReferenceCatalogData.properties,requirementJSON:ReferenceCatalogData.requirements)
    }
    private static func fingerprint(_ bytes: [UInt8]) -> String {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in bytes { hash = (hash ^ UInt64(byte)) &* 0x100000001b3 }
        return String(format:"fnv1a64:%016llx",hash)
    }
    public init(propertyCatalog: String,requirementJSON: [UInt8]) throws {
        guard propertyCatalog.utf8.count <= 1_048_576,requirementJSON.count <= 1_048_576 else { throw ModelError("reference requirement artifact limit") }
        var parsed: [AssuranceProperty] = []
        for line in propertyCatalog.split(whereSeparator: { $0.isNewline }) {
            let parts = line.split(whereSeparator: { $0.isWhitespace })
            guard parts.count == 2 else { throw ModelError("invalid independent property declaration") }
            let phase: AssurancePhase
            switch parts[0] { case "state": phase = .state; case "transition": phase = .transition; default: throw ModelError("invalid independent property phase") }
            parsed.append(AssuranceProperty(String(parts[1]),phase))
        }
        guard !parsed.isEmpty,Set(parsed).count == parsed.count,Set(parsed.map(\.id)).count == parsed.count else { throw ModelError("duplicate or empty independent property catalog") }
        let json = try parseJSON(requirementJSON,maximum:1_048_576)
        guard case let .object(root) = json,try readInteger(root["schema_version"]) == 1,exactText(try readString(root["model"]),"reference-controller"),case let .object(catalog)? = root["properties"],case let .object(mutations)? = root["mutations"],case let .object(review)? = root["review"] else { throw ModelError("invalid reference requirement catalog") }
        guard Set(catalog.keys) == Set(parsed.map(\.id)) else { throw ModelError("independent property catalogs differ") }
        for value in catalog.values { guard !(try readString(value)).trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { throw ModelError("empty property requirement") } }
        let goals = try readArray(root["coverage_targets"],maximum:100).map { try readString($0) }
        let expectedGoals = ["supersede_running","stale_success","cancel_ack","retry_started","duplicate_completion","settled_cleanup"]
        guard Set(goals.map(ExactString.init)) == Set(expectedGoals.map(ExactString.init)),goals.count == expectedGoals.count else { throw ModelError("invalid reference coverage catalog") }
        guard mutations.count == ReferenceFault.allCases.count else { throw ModelError("incomplete reference mutation catalog") }
        for fault in ReferenceFault.allCases { guard exactText(try readString(mutations[ExactString(fault.rawValue)]),fault.target) else { throw ModelError("reference mutation target mismatch") } }
        self.properties = parsed; self.goals = goals; self.reviewStatus = try readString(review["status"])
        self.manifestFingerprint = Self.fingerprint(requirementJSON); self.propertyFingerprint = Self.fingerprint(Array(propertyCatalog.utf8))
    }
    public func manifest(for model: ReferenceController) throws -> AssuranceManifest {
        let b = model.bounds
        let result = AssuranceManifest(model:"reference-controller",provenance:(manifestFingerprint,model.metadata.build,propertyFingerprint),properties:properties,families:["submit","cancel","complete","cancelled","tick"].map { AssuranceFamily($0) },goals:goals.map { AssuranceGoal($0,.transition) },bounds:["max_generations":String(b.generations),"max_attempts":String(b.attempts),"max_time":String(b.time),"payload":"outcome abstraction; no real file/network effects during verification","delivery":"unknown/stale/duplicate deliveries included; eventual delivery not proven"],requiredOutcome:.graphExhausted)
        try result.validate(); return result
    }
}
/// Derived solely from the actual checked transition, not a second reducer run.
public func referenceCoverage(_ before: ReferenceState,_ input: ReferenceInput,_ after: Transition<ReferenceState,ReferenceEffect>,_ history: [ReferenceInput],_ hits: inout AssuranceHits) {
    switch input {
    case .submit:
        hits.family("submit")
        if let current = before.current,before.job(current)?.phase == .running,after.state.jobs.count > before.jobs.count { hits.goal("supersede_running") }
    case .cancel: hits.family("cancel")
    case let .complete(g,_,outcome):
        hits.family("complete")
        if outcome == .success,before.current != g,before.job(g)?.phase == .cancelling { hits.goal("stale_success") }
        if let job = before.job(g),case .settled = job.phase { hits.goal("duplicate_completion") }
    case let .cancelled(g,attempt):
        hits.family("cancelled")
        if let job = before.job(g),job.phase == .cancelling,job.attempt == attempt { hits.goal("cancel_ack") }
    case .tick:
        hits.family("tick")
        if after.outputs.contains(where:{ if case let .start(_,attempt) = $0 { return attempt > 1 }; return false }) { hits.goal("retry_started") }
    }
    if after.outputs.contains(where:{ if case .release = $0 { return true }; return false }) { hits.goal("settled_cleanup") }
}
