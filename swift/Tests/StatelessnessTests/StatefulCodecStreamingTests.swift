import XCTest
@testable import Statelessness

final class StatefulCodecStreamingTests: XCTestCase {
    func testPairAndNetworkEncodeThroughBoundedSinks() throws {
        let child = Model<UInt8,UInt8,UInt8>(initialState:{ 0 },step:{ s,_ in Transition(state:s,outputs:[]) },checkState:{ _ in [] },cloneState:{ $0 },cloneInput:{ $0 },cloneOutput:{ $0 })
        var encodedRight = 0
        let right = TraceValueCodec<UInt8>(schema:"tracked",layout:ValueCodecs.u8.layout,encode:{ value,sink in encodedRight += 1; try ValueCodecs.u8.encodeRaw(value,into:sink) },decode:{ try $0.value(ValueCodecs.u8) })
        let pair = try Pair(left:child,right:child,wiring:PairWiring<UInt8,UInt8,UInt8,UInt8,UInt8,UInt8>(messages:{_ in []},checkState:{_,_ in}))
        let pairCodec = try pair.codec(leftState:ValueCodecs.u8,rightState:right,leftInput:ValueCodecs.u8,rightInput:ValueCodecs.u8,leftOutput:ValueCodecs.u8,rightOutput:ValueCodecs.u8,wiringMetadata:{Metadata(name:"w")})
        let pairState = PairState<UInt8,UInt8,UInt8,UInt8>(left:1,right:2)
        XCTAssertNotNil(pairCodec.encodeStateInto); XCTAssertNotNil(pairCodec.encodeInputInto); XCTAssertNotNil(pairCodec.encodeOutputInto)
        var bytes: [UInt8] = []
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:0) { try encodeStateInto(pairCodec,state:pairState,into:$0) })
        XCTAssertEqual(encodedRight,0)
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeStateInto(pairCodec,state:pairState,into:$0) }
        XCTAssertEqual(bytes,try pairCodec.encodeState(pairState))
        let pairInput = PairInput<UInt8,UInt8>.local(.right(9))
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeInputInto(pairCodec,input:pairInput,into:$0) }
        XCTAssertEqual(bytes,try pairCodec.encodeInput(pairInput))
        let pairOutput = PairAddress<UInt8,UInt8>.left(7)
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeOutputInto(pairCodec,output:pairOutput,into:$0) }
        XCTAssertEqual(bytes,try pairCodec.encodeOutput(pairOutput))

        let network = try Network(machines:[NetworkAddress(1,child)],wiring:NetworkWiring<UInt8,UInt8,UInt8>(routes:{_ in []},checkState:{_,_ in}))
        let networkCodec = try network.codec(state:ValueCodecs.u8,input:ValueCodecs.u8,output:ValueCodecs.u8,wiringMetadata:{Metadata(name:"w")})
        let networkState = NetworkState<UInt8,UInt8>(machines:[NetworkAddress(1,1)])
        XCTAssertNotNil(networkCodec.encodeStateInto); XCTAssertNotNil(networkCodec.encodeInputInto); XCTAssertNotNil(networkCodec.encodeOutputInto)
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:0) { try encodeStateInto(networkCodec,state:networkState,into:$0) })
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeStateInto(networkCodec,state:networkState,into:$0) }
        XCTAssertEqual(bytes,try networkCodec.encodeState(networkState))
        let networkInput = NetworkInput<UInt8>.local(NetworkAddress(1,2))
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeInputInto(networkCodec,input:networkInput,into:$0) }
        XCTAssertEqual(bytes,try networkCodec.encodeInput(networkInput))
        let networkOutput = NetworkAddress<UInt8>(1,3)
        try EncodeBuffer.withBuffer(&bytes,maximum:100) { try encodeOutputInto(networkCodec,output:networkOutput,into:$0) }
        XCTAssertEqual(bytes,try networkCodec.encodeOutput(networkOutput))
    }
    func testNetworkStateDecodeRejectsNoncanonicalAndUnknownMachineIDs() throws {
        let codec = try networkStateCodec(ValueCodecs.u8,ValueCodecs.u8)
        func state(_ ids: [UInt32],source: UInt32,destination: UInt32) -> NetworkState<UInt8,UInt8> {
            NetworkState(machines:ids.map { NetworkAddress($0,0) },pending:[NetworkMessage(source:source,route:NetworkRoute(destination:destination,channel:0,input:0))])
        }
        let good = state([1,3,5,7],source:1,destination:7)
        let decoded = try codec.fromTrace(codec.traceBytes(good,maximum:1024))
        XCTAssertEqual(decoded.machines.map(\.machine),[1,3,5,7])
        for bad in [state([1,3,3,7],source:1,destination:7),state([1,5,3,7],source:1,destination:7),state([1,3,5,7],source:2,destination:7),state([1,3,5,7],source:1,destination:8)] {
            XCTAssertThrowsError(try codec.fromTrace(codec.traceBytes(bad,maximum:1024)))
        }
    }

}
