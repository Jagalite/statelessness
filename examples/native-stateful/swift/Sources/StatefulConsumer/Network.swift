import Foundation
import Statelessness
func networkMain() throws {
    let scalar = ValueCodecs.u8
    let childCodec = Codec<UInt8,UInt8,UInt8>(metadata:{Metadata(name:"ring-child",build:"fixture-v1")},encodeState:{try scalar.traceBytes($0,maximum:1)},decodeState:{try scalar.fromTrace($0)},encodeInput:{try scalar.traceBytes($0,maximum:1)},decodeInput:{try scalar.fromTrace($0)},encodeOutput:{try scalar.traceBytes($0,maximum:1)})
    let child = Model<UInt8,UInt8,UInt8>(initialState:{0},step:{Transition(state:$0+$1,outputs:[$1])},checkState:{_ in [.passed("safe")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},codec:childCodec)
    let wiring = NetworkWiring<UInt8,UInt8,UInt8>(routes:{[NetworkRoute(destination:($0.machine+1)%3,channel:7,input:$0.value)]},checkState:{ s,c in try c.push(s.machines.reduce(0){$0+Int($1.value)}>2 ? .failed("total","total exceeds two") : .passed("total")) })
    let n = try Network(machines:[NetworkAddress(0,child),NetworkAddress(1,child),NetworkAddress(2,child)],wiring:wiring)
    var model = n.model(); model.codec = try n.codec(state:scalar,input:scalar,output:scalar,wiringMetadata:{Metadata(name:"ring-global",build:"fixture-v1")})
    let mode = CommandLine.arguments[1], path = CommandLine.arguments[2]
    if mode == "record" { let trace = try record(model,inputs:InputIterator(values:[.local(NetworkAddress(0,1)),.deliver(0),.deliver(0)])); try Data(encodeTrace(trace)).write(to:URL(fileURLWithPath:path)); print("recorded") }
    else { let trace = try parseTrace(Array(Data(contentsOf:URL(fileURLWithPath:path)))); let r = try replay(model,trace:trace); guard r.outcome == "exact", r.failureReproduced else { throw ModelError("failure not reproduced") }; print("exact failure reproduced") }
}
