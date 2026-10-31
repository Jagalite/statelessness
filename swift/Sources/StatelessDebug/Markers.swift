/// A post-turn diagnostic classification; it must not affect application behavior.
public struct DiagnosticMarker: Equatable {
    public let id: String
    public let detail: String
    public init(id: String, detail: String) { self.id = id; self.detail = detail }
}

public struct MarkerBatch: Equatable {
    public let markers: [DiagnosticMarker]
    public let dropped: UInt64
    public let truncated: UInt64
    /// Logical retained byte accounting, not a bound on Swift heap allocations.
    public let retainedBytes: Int
}

/// Collects bounded post-turn markers. `detail` is evaluated only when the marker
/// can be admitted. The callback must not perform application behavior.
/// The collector is single-threaded; callback allocations are outside the budget.
public final class MarkerCollector {
    // Rust's two String fields occupy 48 bytes on the supported 64-bit profile.
    // This fixed logical charge is not Swift's actual object size.
    public static let markerOverhead = 48
    private let enabled: Bool
    private let maxRecords: Int
    private let maxBytes: Int
    private var markers: [DiagnosticMarker] = []
    private var dropped: UInt64 = 0
    private var truncated: UInt64 = 0
    private var retainedBytes = 0
    private var collecting = false
    private var finished = false

    public init(enabled: Bool, maxRecords: Int, maxBytes: Int) throws {
        guard maxRecords >= 0, maxBytes >= 0 else { throw WatchError.invalidConfig }
        self.enabled = enabled; self.maxRecords = maxRecords; self.maxBytes = maxBytes
    }

    public static func disabled() -> MarkerCollector {
        // These constants are valid by construction.
        try! MarkerCollector(enabled: false, maxRecords: 256, maxBytes: 32 * 1024)
    }

    public var interested: Bool {
        enabled && !finished && !collecting && markers.count < maxRecords && retainedBytes < maxBytes
    }

    public func marker(_ id: String, detail: () -> String) {
        guard enabled, !finished else { return }
        let remaining = maxBytes - retainedBytes
        let idBytes = id.utf8.count
        guard interested, idBytes <= remaining, Self.markerOverhead <= remaining - idBytes else {
            if dropped < .max { dropped += 1 }; return
        }
        collecting = true
        let value = detail()
        collecting = false
        let available = remaining - idBytes - Self.markerOverhead
        var retained = "", count = 0
        for scalar in value.unicodeScalars {
            let text = String(scalar), bytes = text.utf8.count
            guard bytes <= available - count else { break }
            retained.append(text); count += bytes
        }
        if count < value.utf8.count, truncated < .max { truncated += 1 }
        retainedBytes += Self.markerOverhead + idBytes + count
        markers.append(DiagnosticMarker(id: id, detail: retained))
    }

    /// Finishes collection; subsequent marker calls are ignored.
    public func finish() throws -> MarkerBatch {
        guard !collecting else { throw DebugError.reentrant }
        finished = true
        return MarkerBatch(markers: markers, dropped: dropped, truncated: truncated, retainedBytes: retainedBytes)
    }
}
