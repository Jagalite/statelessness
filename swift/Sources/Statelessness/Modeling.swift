/// Explicit environmental bounds; descriptive assumptions are not proof of completeness.
public struct DomainDescriptor: Sendable {
    public let name: String
    public let assumptions: String
    public let maxEntries: Int
    public init(name: String, assumptions: String, maxEntries: Int) throws {
        guard maxEntries >= 0 else { throw ConfigError("domain limit must be nonnegative") }
        self.name = name; self.assumptions = assumptions; self.maxEntries = maxEntries
    }
    public func parameters() -> [(String, String)] {
        [("model.domain.name", name), ("model.domain.assumptions", assumptions), ("model.domain.max_entries", String(maxEntries))]
    }
}
/// Clone must detach reference-containing inputs. Instances stay actor/thread confined.
public struct Domain<I> {
    public let descriptor: DomainDescriptor
    private var storage: [I] = []
    private let clone: (I) throws -> I
    public init(_ descriptor: DomainDescriptor, clone: @escaping (I) throws -> I) { self.descriptor = descriptor; self.clone = clone }
    public mutating func push(_ value: I) throws {
        guard storage.count < descriptor.maxEntries else { throw ModelError("domain entry limit exceeded") }
        storage.append(try clone(value))
    }
    /// Preserve the successful prefix on iterator or capacity failure.
    public mutating func extend(_ inputs: InputIterator<I>) throws { while let value = try inputs.next() { try push(value) } }
    public func entries() throws -> [I] { try storage.map(clone) }
    public mutating func intoEntries() -> [I] { let out = storage; storage = []; return out }
    /// Entry copies are captured eagerly; unused copy failures are deferred, but
    /// copy hooks can have side effects even when an earlier entry matches.
    public func contains(_ value: I, equal: (I, I) throws -> Bool) throws -> Bool {
        guard !storage.isEmpty else { return false }
        // Capture the query before an equality callback can mutate its external alias.
        // Clone hooks must detach reference-backed values and can have side effects.
        let query = try clone(value)
        // Capture entries before equality can mutate a later reference-backed entry.
        // Defer a later clone failure so an earlier match still short-circuits.
        let entries = storage.map { entry in Result { try clone(entry) } }
        for entry in entries { if try equal(entry.get(), clone(query)) { return true } }; return false
    }
    public func sampleIndexed(_ rng: inout Rng) throws -> I? {
        guard let index = rng.index(UInt64(storage.count)) else { return nil }; return try clone(storage[Int(index)])
    }
    /// Inputs and mapped outputs are caller-owned, like ordinary Array.map.
    public static func product<A, B>(_ a: [A], _ b: [B], maximum: Int, map: (A, B) throws -> I) throws -> [I] {
        guard maximum >= 0, b.isEmpty || a.count <= maximum / b.count else { throw ModelError("domain product limit exceeded") }
        var out: [I] = []; out.reserveCapacity(a.count * b.count)
        for x in a { for y in b { out.append(try map(x, y)) } }; return out
    }
}
public func checkLazy(_ id: String, _ passed: Bool, details: () throws -> String) rethrows -> Check {
    passed ? .passed(id) : .failed(id, try details())
}
public struct PropertyMetadata: Sendable {
    public enum Phase: Sendable { case state, transition }
    public let id: String
    public let phase: Phase
    public init(_ id: String, phase: Phase = .state) { self.id = id; self.phase = phase }
}
public func declaredProperties(_ properties: [PropertyMetadata]) throws -> [PropertyMetadata] {
    var seen: Set<ExactString> = []
    for p in properties { guard seen.insert(ExactString(p.id)).inserted else { throw ConfigError("duplicate property id") } }
    return properties
}
/// Native typed callbacks replace procedural syntax. Existing Model requires its
/// initial callback statically; use defaultInitial for an explicit default factory.
public func defaultInitial<S>(_ factory: @escaping () throws -> S, clone: @escaping (S) throws -> S) -> () throws -> S {
    { try clone(factory()) }
}
public func adaptModel<S,I,O>(_ model: Model<S,I,O>, properties: [PropertyMetadata], unchecked: Bool = false) throws -> Model<S,I,O> {
    _ = try declaredProperties(properties)
    guard !properties.isEmpty || unchecked else { throw ConfigError("no properties; select unchecked") }
    return model
}
