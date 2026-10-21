import XCTest
import Statelessness
@testable import StatelessDebug
final class LiveBridgeTests: XCTestCase {
    func model() -> Model<Int,Int,Int> {
        let metadata = Metadata(name: "live",build: "manual")
        let codec = Codec<Int,Int,Int>(metadata: { metadata },encodeState: { [UInt8($0)] },decodeState: { Int($0[0]) },encodeInput: { [UInt8($0)] },decodeInput: { Int($0[0]) },encodeOutput: { [UInt8($0)] })
        return Model(initialState: { 0 },step: { s,i in Transition(state: s+i,outputs: [s+i]) },checkState: { _ in [] },cloneState: {$0},cloneInput: {$0},cloneOutput: {$0},codec: codec)
    }
    func testSealedHostObservationGapAndDispatchFacts() throws {
        let model = model(),initial = try checkInitial(model,state: 7)
        var limits = LiveLimits(); limits.maxEvents = 2; limits.maxEventBytes = 256
        let bridge = try LiveBridge(initial: initial,hostSequence: 9,epoch: 1,config: RunConfig(strategy: "host"),limits: limits)
        let checked = try checkTurn(model,before: 7,input: 1,transition: Transition(state: 8,outputs: [8]),sequence: 10)
        try bridge.observe(hostSequence: 10,checked: checked)
        XCTAssertEqual(bridge.health.recordedSteps,1); XCTAssertTrue(bridge.health.captureComplete)
        let origin = EffectOrigin(epoch: 1,transition: 10,outputIndex: 0)
        try bridge.effectDispatched(origin); XCTAssertThrowsError(try bridge.effectDispatched(origin))
        XCTAssertThrowsError(try bridge.observe(hostSequence: 10,checked: checked)); XCTAssertEqual(bridge.health.duplicateObservations,1)
        let gap = try checkTurn(model,before: 8,input: 1,transition: Transition(state: 9,outputs: [9]),sequence: 12)
        XCTAssertThrowsError(try bridge.observe(hostSequence: 12,checked: gap)); XCTAssertFalse(bridge.health.captureComplete)
        XCTAssertEqual(try bridge.export().steps.count,1); XCTAssertGreaterThan(bridge.health.eventDrops,0)
    }
    func testCausalJoinRetainedAndBeforeCheckpoint() throws {
        let model = model(),meta = try XCTUnwrap(model.codec).metadata()
        let trace = AuditTrace(metadata: meta,config: RunConfig(strategy: "host"),initialState: [7],initialChecks: [],steps: [TraceStep(input: [1],disposition: .accepted,outputs: [[8]],postState: [8],checks: [])],termination: .completed)
        let evidence = TraceV2(trace: trace,checkpointAfter: 9,provenance: Provenance(run: "1",epoch: 1),steps: [StepEvidence(sequence: 10,stateCheckCount: 0,causal: CausalContext(machine: "9"))])
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let effect = try observer.requested(origin: RequestOrigin(run: 1,epoch: 1,machine: 9,transitionSequence: 10,outputIndex: 0))
        XCTAssertEqual(correlate(evidence,try XCTUnwrap(observer.effectDetails(effect.id))),.retained(localSequence: 1,output: 0,detailComplete: true))
        let oldOrigin = RequestOrigin(run: 1,epoch: 1,machine: 9,transitionSequence: 9,outputIndex: 0)
        let old = try observer.requested(origin: oldOrigin)
        XCTAssertEqual(correlate(evidence,try XCTUnwrap(observer.effectDetails(old.id))),.beforeCheckpoint(oldOrigin))
    }
}
