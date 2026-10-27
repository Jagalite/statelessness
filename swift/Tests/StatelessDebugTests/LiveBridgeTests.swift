import XCTest
import Statelessness
// Keep this an ordinary import so observation-only hosts use only public APIs.
import StatelessDebug
final class LiveBridgeTests: XCTestCase {
    func model() -> Model<Int,Int,Int> {
        let metadata = Metadata(name: "live",build: "manual")
        let codec = Codec<Int,Int,Int>(metadata: { metadata },encodeState: { [UInt8($0)] },decodeState: { Int($0[0]) },encodeInput: { [UInt8($0)] },decodeInput: { Int($0[0]) },encodeOutput: { [UInt8($0)] })
        return Model(initialState: { 0 },step: { s,i in Transition(state: s+i,outputs: [s+i]) },checkState: { _ in [] },cloneState: {$0},cloneInput: {$0},cloneOutput: {$0},codec: codec)
    }
    func testPublicEffectOriginForObservationOnlyHost() throws {
        var model = model()
        model.initialState = { throw ModelError("observation must not initialize the host") }
        model.step = { _,_ in throw ModelError("observation must not execute the reducer") }
        let epoch = UInt64.max,sequence = UInt64.max
        let initial = try checkInitial(model,state: 7)
        let bridge = try LiveBridge(initial: initial,hostSequence: sequence-1,epoch: epoch,config: RunConfig(strategy: "host"))
        let origin = EffectOrigin(epoch: epoch,transition: sequence,outputIndex: 0)
        XCTAssertEqual(origin.epoch,epoch)
        XCTAssertEqual(origin.transition,sequence)
        XCTAssertEqual(origin.outputIndex,0)
        XCTAssertThrowsError(try bridge.effectDispatched(origin)) { XCTAssertEqual($0 as? LiveError,.unknownEffect) }

        // The app supplies its completed turn without opting into LiveController.
        let checked = try checkTurn(model,before: 7,input: 1,transition: Transition(state: 8,outputs: [8]),sequence: sequence)
        try bridge.observe(hostSequence: sequence,checked: checked)
        for unknown in [
            EffectOrigin(epoch: epoch-1,transition: sequence,outputIndex: 0),
            EffectOrigin(epoch: epoch,transition: sequence-1,outputIndex: 0),
            EffectOrigin(epoch: epoch,transition: sequence,outputIndex: -1),
            EffectOrigin(epoch: epoch,transition: sequence,outputIndex: 1)
        ] {
            XCTAssertThrowsError(try bridge.effectDispatched(unknown)) { XCTAssertEqual($0 as? LiveError,.unknownEffect) }
        }
        try bridge.effectDispatched(origin)
        let duplicate = EffectOrigin(epoch: epoch,transition: sequence,outputIndex: 0)
        XCTAssertThrowsError(try bridge.effectDispatched(duplicate)) { XCTAssertEqual($0 as? LiveError,.duplicate) }
        var dispatched: [EffectOrigin] = []
        while let event = bridge.popEvent() {
            if case .effectDispatched(let observed) = event.kind { dispatched.append(observed) }
        }
        XCTAssertEqual(dispatched,[origin])
        XCTAssertEqual(bridge.health.recordedSteps,1)
        XCTAssertTrue(bridge.health.captureComplete)
        XCTAssertEqual(bridge.health.eventDrops,0)
        XCTAssertEqual(try bridge.export().steps.count,1)
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
