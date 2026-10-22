import Foundation
import Statelessness
import StatelessDebug
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

enum WireFixture {
    static func call(_ message: String) throws {
        try FileHandle.standardError.write(contentsOf: Data("CALL \(message)\n".utf8))
    }

    static func littleEndian(_ value: UInt64) -> [UInt8] {
        (0..<8).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) }
    }

    static func model() -> Model<UInt64, UInt8, UInt64> {
        let codec = Codec<UInt64, UInt8, UInt64>(
            metadata: { Metadata(name: "n12-wire-counter", build: "n12-wire-v1") },
            encodeState: littleEndian,
            decodeState: { bytes in
                guard bytes.count == 8 else { throw ModelError("invalid state encoding") }
                return bytes.enumerated().reduce(UInt64(0)) { $0 | (UInt64($1.element) << ($1.offset * 8)) }
            },
            encodeInput: { [$0] },
            decodeInput: { bytes in
                try call("decode " + bytes.map { String(format: "%02x", $0) }.joined())
                guard bytes.count == 1, bytes[0] <= 3 else { throw ModelError("invalid input encoding") }
                return bytes[0]
            },
            encodeOutput: littleEndian,
            encodeStateInto: { value, sink in try sink.extend(littleEndian(value)) },
            encodeInputInto: { value, sink in try sink.extend([value]) },
            encodeOutputInto: { value, sink in try sink.extend(littleEndian(value)) }
        )
        return Model(
            initialState: { try call("initial"); return 0 },
            step: { before, input in
                try call("step \(before) \(input)")
                switch input {
                case 0: return Transition(state: before, disposition: .ignored("fixture ignored"))
                case 1:
                    let after = before + 1
                    return Transition(state: after, outputs: [after])
                case 2: return Transition(state: before, disposition: .rejected("fixture rejection"))
                default: throw ModelError("private reducer detail")
                }
            },
            checkState: { state in
                try call("state \(state)")
                return [state <= 2 ? .passed("bounded") : .failed("bounded", "private state detail")]
            },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { before, input, transition in
                try call("transition \(before) \(input) \(transition.state)")
                return [.passed("transition")]
            },
            codec: codec
        )
    }

    static func run() throws {
        // Let the public transport return a real broken-pipe write error so its
        // already committed session can still be inspected before process exit.
        _ = signal(SIGPIPE, SIG_IGN)
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard arguments.allSatisfy({ ["--read-only", "--exact", "--cache-one"].contains($0) }) else {
            throw ModelError("expected only --read-only, --exact, or --cache-one")
        }
        var authority = ProtocolAuthority()
        authority.deliverInputs = !arguments.contains("--read-only")
        authority.readExactValues = arguments.contains("--exact")
        var limits = ProtocolLimits()
        if arguments.contains("--cache-one") { limits.maxCacheEntries = 1 }
        let session = try DebugSession(
            id: "wire\té😀", model: model(), policy: .outOfDomainInjection, equalInputs: ==
        )
        let adapter = try ProtocolAdapter(session: session, authority: authority, limits: limits)
        let outcome = Result { try adapter.serve(input: .standardInput, output: .standardOutput, maxRequests: Int.max) }
        let state = try session.state
        try FileHandle.standardError.write(contentsOf: Data("FINAL \(state) \(session.revision) \(session.sequence)\n".utf8))
        _ = try outcome.get()
    }
}

do { try WireFixture.run() }
catch {
    try? FileHandle.standardError.write(contentsOf: Data("n12-wire-swift: \(error)\n".utf8))
    exit(1)
}
