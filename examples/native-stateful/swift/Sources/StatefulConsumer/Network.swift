import Foundation
import Statelessness

// This expectation is independent of the reducer: the fixed input script must
// visit each machine once, leave one route queued, and exercise global::total.
private func expectNetwork(_ trace: Trace, codec: Codec<NetworkState<UInt8,UInt8>,NetworkInput<UInt8>,NetworkAddress<UInt8>>, weak: Bool) throws {
    guard trace.steps.count == 3, trace.termination == (weak ? .completed : .propertyFailed) else { throw ModelError("unexpected network termination or step count") }
    let last = trace.steps[2], state = try codec.decodeState(last.postState)
    guard (0..<3).allSatisfy({ state.machine(UInt32($0)) == 1 }), state.pending.count == 1 else { throw ModelError("unexpected network state or queue") }
    let checks = last.checks.filter { exactText($0.id, "global::total") }
    guard checks.count == 1, checks[0].status == (weak ? .passed : .failed),
          exactText(checks[0].details, weak ? "" : "total exceeds two") else { throw ModelError("unexpected global property observation") }
}

func networkMain() throws {
    let scalar = ValueCodecs.u8
    let childCodec = Codec<UInt8,UInt8,UInt8>(metadata:{Metadata(name:"ring-child",build:"fixture-v1")},encodeState:{try scalar.traceBytes($0,maximum:1)},decodeState:{try scalar.fromTrace($0)},encodeInput:{try scalar.traceBytes($0,maximum:1)},decodeInput:{try scalar.fromTrace($0)},encodeOutput:{try scalar.traceBytes($0,maximum:1)})
    let child = Model<UInt8,UInt8,UInt8>(initialState:{0},step:{Transition(state:$0+$1,outputs:[$1])},checkState:{_ in [.passed("safe")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},codec:childCodec)
    let weak = CommandLine.arguments[1] == "control"
    let wiring = NetworkWiring<UInt8,UInt8,UInt8>(routes:{[NetworkRoute(destination:($0.machine+1)%3,channel:7,input:$0.value)]},checkState:{ s,c in try c.push(s.machines.reduce(0){$0+Int($1.value)} > (weak ? 3 : 2) ? .failed("total","total exceeds two") : .passed("total")) })
    let n = try Network(machines:[NetworkAddress(0,child),NetworkAddress(1,child),NetworkAddress(2,child)],wiring:wiring)
    var model = n.model(); model.codec = try n.codec(state:scalar,input:scalar,output:scalar,wiringMetadata:{Metadata(name:"ring-global",build:"fixture-v1")})
    let mode = CommandLine.arguments[1], path = CommandLine.arguments[2]
    if mode == "record-audit" { let trace = try recordAudit(model, inputs:InputIterator(values:[.local(NetworkAddress(0,1)),.deliver(0),.deliver(0)]), config:RunConfig(strategy:"stateful-fixture")); try expectNetwork(trace.portable().trace, codec:model.codec!, weak:false); try Data(encodeAuditTrace(trace)).write(to:URL(fileURLWithPath:path)); print("audit recorded") }
    else if mode == "record" { let trace = try record(model,inputs:InputIterator(values:[.local(NetworkAddress(0,1)),.deliver(0),.deliver(0)])); try expectNetwork(trace, codec:model.codec!, weak:false); try Data(encodeTrace(trace)).write(to:URL(fileURLWithPath:path)); print("recorded") }
    else if mode == "control" { let trace = try record(model,inputs:InputIterator(values:[.local(NetworkAddress(0,1)),.deliver(0),.deliver(0)])); try expectNetwork(trace, codec:model.codec!, weak:true); print("network-control-detected") }
    else if mode == "compare-cross-build" {
        let trace = try parseTrace(Array(Data(contentsOf:URL(fileURLWithPath:path))))
        try expectNetwork(trace, codec:model.codec!, weak:false)
        let own = try model.codec!.metadata(), env = ProcessInfo.processInfo.environment
        guard trace.metadata.build == env["STATEFUL_PRODUCER_BUILD"], own.build == env["STATEFUL_CONSUMER_BUILD"], trace.metadata.build != own.build else { throw ModelError("build identity mismatch") }
        let strict = try replay(model,trace:trace)
        guard strict.outcome == "incompatible", !strict.buildMatches, strict.stepsVerified == 0, !strict.failureReproduced else { throw ModelError("strict replay did not reject before execution") }
        let r = try replay(model,trace:trace,allowBuildMismatch:true)
        guard r.outcome == "exact", !r.buildMatches, r.stepsVerified == 3, r.failureReproduced else { throw ModelError("cross-build comparison failed") }
        print("STATEFUL_COMPARISON {\"strict_outcome\":\"incompatible\",\"strict_build_matches\":false,\"strict_steps_verified\":0,\"strict_failure_reproduced\":false,\"observation_outcome\":\"exact\",\"observation_build_matches\":false,\"observation_steps_verified\":3,\"observation_failure_reproduced\":true}")
    }
    else if mode == "replay" { let trace = try parseTrace(Array(Data(contentsOf:URL(fileURLWithPath:path)))); try expectNetwork(trace, codec:model.codec!, weak:false); let r = try replay(model,trace:trace); guard r.outcome == "exact", r.stepsVerified == 3, r.failureReproduced else { throw ModelError("failure not reproduced") }; print("exact failure reproduced") }
    else { throw ModelError("unknown mode") }
}
