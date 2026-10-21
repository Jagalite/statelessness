import XCTest
import Statelessness
@testable import StatelessDebug

final class EvidenceViewerTests: XCTestCase {
    let metadata = Metadata(name: "debug-counter", build: "manual-golden")
    func fixture(checkpoint: UInt64 = 9, policy: Bool = false) -> TraceV2 {
        let checks = [Check.failed("limit", "")]
        let parameters = policy ? [AuditParameter("stateless.debug.environment","manual"), AuditParameter("stateless.debug.origin","simulation")] : []
        let trace = AuditTrace(metadata: metadata, config: RunConfig(strategy: "debug", parameters: parameters), initialState: [7], initialChecks: [], steps: [TraceStep(input: [1], disposition: .accepted, outputs: [[8]], postState: [8], checks: checks)], termination: .propertyFailed)
        return TraceV2(trace: trace, checkpointAfter: checkpoint, provenance: Provenance(run: "golden", epoch: UInt64.max), steps: [StepEvidence(sequence: checkpoint + 1, stateCheckCount: 1)])
    }
    func model(calls: @escaping () -> Void = {}) -> Model<Int,Int,Int> {
        let codec = Codec<Int,Int,Int>(metadata: { self.metadata }, encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) }, encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] })
        return Model(initialState: { calls(); return 0 }, step: { state,input in calls(); return Transition(state: state+input, outputs: [state+input]) }, checkState: { state in calls(); return state >= 8 ? [.failed("limit", "")] : [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec)
    }
    func testPassiveManualGoldenAndFullWidthCheckpoint() throws {
        let value = fixture(checkpoint: UInt64.max-1), viewer = try EvidenceViewer(.v2(value))
        XCTAssertEqual(viewer.retainedRange, (UInt64.max-1)...UInt64.max)
        XCTAssertEqual(viewer.boundary(0)?.after,[7]); XCTAssertEqual(viewer.boundary(1)?.before,[7])
        XCTAssertEqual(viewer.boundary(1)?.after,[8]); XCTAssertEqual(viewer.boundary(1)?.outputs,[[8]])
        XCTAssertNil(viewer.boundary(-1)); XCTAssertNil(viewer.boundary(2))
        XCTAssertEqual(try viewer.failure(sequence: 1, check: 0).phase,"state")
        XCTAssertEqual(try EncodedPreview([1,2,3], maximum: 2).bytes,[1,2])
    }
    func testLegacyUnknownAndSemanticValidation() throws {
        let viewer = try EvidenceViewer(.v1(fixture().trace))
        XCTAssertThrowsError(try viewer.failure(sequence: 1, check: 0))
        XCTAssertEqual(try viewer.failure(sequence: 1, check: 0, legacyPhase: .state).phase,"state")
        XCTAssertThrowsError(try viewer.failure(sequence: 1, check: -1))
        let t = fixture().trace
        let invalid = AuditTrace(metadata: t.metadata, config: t.config, initialState: t.initialState, initialChecks: [], steps: t.steps, termination: .completed)
        XCTAssertThrowsError(try EvidenceViewer(.v1(invalid)))
    }
    func testExactVerificationBudgetsAndPhase() throws {
        var calls = 0
        let viewer = try EvidenceViewer(.v2(fixture()))
        XCTAssertThrowsError(try viewer.verify(model { calls += 1 }, budget: VerificationLimits(maxTransitions: 0)))
        XCTAssertEqual(calls,0)
        XCTAssertEqual(try viewer.verify(model(), budget: VerificationLimits(maxTransitions: 1)).outcome,"exact")
        let f = fixture(), wrong = TraceV2(trace: f.trace, checkpointAfter: 9, steps: [StepEvidence(sequence: 10, stateCheckCount: 0)])
        XCTAssertThrowsError(try EvidenceViewer(.v2(wrong)).verify(model(), budget: VerificationLimits(maxTransitions: 1)))
    }
    func testCheckpointShrinkChargesVerificationAndPolicy() throws {
        let viewer = try EvidenceViewer(.v2(fixture(policy: true)))
        let generator = Generate(model: model(), generate: { _,_ in nil }, equalInputs: ==)
        XCTAssertThrowsError(try viewer.minimize(generator, sequence: 1, check: 0, limits: ShrinkLimits(maxReplayedTransitions: 10)))
        let policy = try InputPolicy<Int,Int,Int>(label: "manual", permits: { _,_,_,_ in true })
        let report = try viewer.minimize(generator, sequence: 1, check: 0, policy: policy, limits: ShrinkLimits(maxReplayedTransitions: 10))
        XCTAssertTrue(report.validatedOriginal); XCTAssertEqual(report.minimized.inputs,[1]); XCTAssertGreaterThanOrEqual(report.replayedTransitions,2)
        XCTAssertThrowsError(try viewer.minimize(generator, sequence: 1, check: 0, policy: policy, limits: ShrinkLimits(maxReplayedTransitions: 0)))
    }
    func testV2InsufficientWorkbenchBudgetStillChargesPhasePass() throws {
        let viewer = try EvidenceViewer(.v2(fixture()))
        var calls = 0
        let generator = Generate(model: model { calls += 1 },generate: { _,_ in nil },equalInputs: ==)
        XCTAssertThrowsError(try viewer.minimize(generator,sequence: 1,check: 0,limits: ShrinkLimits(maxReplayedTransitions: 1)))
        XCTAssertGreaterThan(calls,0)
        let afterFirst = calls
        XCTAssertThrowsError(try viewer.minimize(generator,sequence: 1,check: 0,limits: ShrinkLimits(maxReplayedTransitions: 2)))
        XCTAssertEqual(calls-afterFirst,afterFirst)
    }
    func testLegacyCheckpointAcceptsRustUnsignedTextGrammar() throws {
        let trace = AuditTrace(metadata: metadata,config: RunConfig(strategy: "host",parameters: [AuditParameter("stateless.monitor.checkpoint_after_sequence","+018446744073709551615")]),initialState: [7],initialChecks: [],steps: [],termination: .completed)
        XCTAssertEqual(try EvidenceViewer(.v1(trace)).checkpointAfter,UInt64.max)
    }
    func testPassiveCursorPreviewsAndIndependentOutputDecoder() throws {
        let source = fixture().trace
        var cursor = try TraceViewer(source)
        XCTAssertEqual(cursor.position,0); XCTAssertEqual(cursor.retainedRange,0...1)
        try cursor.seek(1)
        XCTAssertEqual(cursor.position,1); XCTAssertEqual(try cursor.stateBytes(maximum: 0).bytes,[])
        let outputs = try cursor.outputs(limit: 1,maxBytesEach: 0)
        XCTAssertEqual(outputs.values.first?.totalBytes,1); XCTAssertEqual(outputs.values.first?.complete,false)
        XCTAssertEqual(try cursor.historicalOutput(index: 0) { Int($0[0]) },8)
        XCTAssertEqual(try cursor.decodeState(model(),sequence: 0),7)
        XCTAssertThrowsError(try cursor.seek(2)); XCTAssertEqual(cursor.position,1)
        XCTAssertThrowsError(try cursor.outputs(limit: 0))
    }
}
