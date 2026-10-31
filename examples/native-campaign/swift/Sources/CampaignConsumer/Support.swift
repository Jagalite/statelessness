import Foundation
import Statelessness
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw AdapterError(message) }
}

func currentThreadID() -> UInt64 {
    #if canImport(Darwin)
    return UInt64(pthread_mach_thread_np(pthread_self()))
    #else
    return UInt64(pthread_self())
    #endif
}

struct CallbackEvent: Sendable {
    let jobID: UInt64
    let name: String
    let role: String
    let input: UInt64?
}

struct AuditSnapshot: Sendable {
    var events: [CallbackEvent] = []
    var cancelled = false
}

// The only shared application reference. Every access to its mutable storage
// holds the lock; snapshots contain value-only Sendable records. Model state,
// outputs, feature keys and their closures are never stored here or transferred.
final class CallbackAudit: @unchecked Sendable {
    private let lock = NSLock()
    private let caller = currentThreadID()
    private var state = AuditSnapshot()

    func note(_ jobID: UInt64, _ name: String, input: UInt64? = nil) {
        let role = currentThreadID() == caller ? "calling" : "spawned"
        lock.lock(); defer { lock.unlock() }
        state.events.append(CallbackEvent(jobID: jobID, name: name, role: role, input: input))
    }

    func cancel() {
        lock.lock(); defer { lock.unlock() }
        state.cancelled = true
    }

    var isCancelled: Bool {
        lock.lock(); defer { lock.unlock() }
        return state.cancelled
    }

    func snapshot() -> AuditSnapshot {
        lock.lock(); defer { lock.unlock() }
        return state
    }
}

// Deliberately worker-local reference types. Faithful clone closures isolate
// snapshots, while only UInt64 inputs cross the campaign's report channel.
final class CounterState {
    let value: UInt64
    init(_ value: UInt64) { self.value = value }
}

final class CounterOutput {
    let value: UInt64
    init(_ value: UInt64) { self.value = value }
}

final class CounterFeature: Hashable {
    let value: UInt64
    init(_ value: UInt64) { self.value = value }
    static func == (lhs: CounterFeature, rhs: CounterFeature) -> Bool { lhs.value == rhs.value }
    func hash(into hasher: inout Hasher) { hasher.combine(value) }
}

func emit(_ row: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
    data.append(10)
    FileHandle.standardOutput.write(data)
}
