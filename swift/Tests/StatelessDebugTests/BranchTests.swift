import XCTest
import Statelessness
@testable import StatelessDebug
final class BranchTests: XCTestCase {
    func fixture() -> (EvidenceViewer,Model<Int,Int,Int>) {
        let metadata = Metadata(name: "branch",build: "old")
        let codec = Codec<Int,Int,Int>(metadata: { metadata },encodeState: { [UInt8($0)] },decodeState: { Int($0[0]) },encodeInput: { [UInt8($0)] },decodeInput: { Int($0[0]) },encodeOutput: { [UInt8($0)] })
        let model = Model(initialState: { 0 },step: { s,i in Transition(state: s+i,outputs: [s+i]) },checkState: { _ in [] },cloneState: {$0},cloneInput: {$0},cloneOutput: {$0},codec: codec)
        let trace = AuditTrace(metadata: metadata,config: RunConfig(strategy: "debug",parameters: [AuditParameter("stateless.debug.environment","original"),AuditParameter("stateless.debug.origin","simulation")]),initialState: [7],initialChecks: [],steps: [TraceStep(input: [1],disposition: .accepted,outputs: [[8]],postState: [8],checks: [])],termination: .completed)
        return (try! EvidenceViewer(.v1(trace)),model)
    }
    func testFixedWidthDigestParameter() { XCTAssertEqual(ArtifactIdentity(fnv1a64: 1,bytes: 0).digest,"fnv1a64:0000000000000001") }
    func testVerifiedForkIsolationAndProvenance() throws {
        let (viewer,model) = fixture()
        let branch = try viewer.fork(id: "child",model: model,sequence: 1,policy: .outOfDomainInjection,budget: VerificationLimits(maxTransitions: 1),equalInputs: ==,recording: RecorderOptions())
        try branch.provenance.validateParent(viewer.artifact.trace)
        XCTAssertEqual(branch.provenance.origin,"out-of-domain-injection"); XCTAssertEqual(try branch.session.state,8)
        _ = try branch.session.step(expectedRevision: 0,input: 2)
        XCTAssertEqual(try branch.session.state,10); XCTAssertEqual(viewer.boundary(1)?.after,[8])
        XCTAssertTrue(try branch.session.exportTrace().config.parameters.contains(where: { exactText($0.key,"stateless.debug.parent.digest") }))
        let policy = try InputPolicy<Int,Int,Int>(label: "different",permits: { _,_,_,_ in true })
        XCTAssertThrowsError(try viewer.fork(id: "mismatch",model: model,sequence: 1,policy: policy,budget: VerificationLimits(maxTransitions: 1),equalInputs: ==))
    }
    func testBuildMismatchMustBeExplicitAndFailureBoundaryRejects() throws {
        let (viewer,model) = fixture(); var newer = model
        var codec = try XCTUnwrap(model.codec); codec.metadata = { Metadata(name: "branch",build: "new") }; newer.codec = codec
        XCTAssertThrowsError(try viewer.fork(id: "new",model: newer,sequence: 0,policy: .outOfDomainInjection,budget: VerificationLimits(maxTransitions: 0),equalInputs: ==))
        let branch = try viewer.fork(id: "new",model: newer,sequence: 0,policy: .outOfDomainInjection,budget: VerificationLimits(maxTransitions: 0),equalInputs: ==,allowBuildMismatch: true)
        XCTAssertEqual(branch.provenance.modelMetadata.build,"new")
        let t = viewer.artifact.trace
        let failed = AuditTrace(metadata: t.metadata,config: t.config,initialState: [7],initialChecks: [.failed("initial","")],steps: [],termination: .propertyFailed)
        XCTAssertThrowsError(try EvidenceViewer(.v1(failed)).fork(id: "failed",model: model,sequence: 0,policy: .outOfDomainInjection,budget: VerificationLimits(maxTransitions: 0),equalInputs: ==))
    }


    func testForkRequiresCompleteAndAuthenticEnvironmentProvenance() throws {
        let (viewer,model) = fixture(),source = viewer.artifact.trace
        func parent(_ parameters: [AuditParameter]) throws -> EvidenceViewer {
            let config = RunConfig(strategy:source.config.strategy,parameters:parameters)
            return try EvidenceViewer(.v1(AuditTrace(metadata:source.metadata,config:config,initialState:source.initialState,initialChecks:source.initialChecks,steps:source.steps,termination:source.termination)))
        }
        let environment = AuditParameter("stateless.debug.environment","original"),simulation = AuditParameter("stateless.debug.origin","simulation")
        let restricted = try InputPolicy<Int,Int,Int>(label:"original",permits:{ _,_,_,_ in true })
        for bad in [[environment],[simulation],[environment,simulation,simulation],[environment,AuditParameter("stateless.debug.origin","unknown")]] {
            XCTAssertThrowsError(try parent(bad).fork(id:"bad",model:model,sequence:0,policy:.outOfDomainInjection,budget:VerificationLimits(maxTransitions:0),equalInputs: ==))
        }
        XCTAssertNoThrow(try parent([]).fork(id:"legacy",model:model,sequence:0,policy:restricted,budget:VerificationLimits(maxTransitions:0),equalInputs: ==))
        let unrestrictedParent = try parent([AuditParameter("stateless.debug.environment","original"),AuditParameter("stateless.debug.origin","out-of-domain-injection")])
        XCTAssertThrowsError(try unrestrictedParent.fork(id:"spoof",model:model,sequence:0,policy:restricted,budget:VerificationLimits(maxTransitions:0),equalInputs: ==))
        XCTAssertNoThrow(try unrestrictedParent.fork(id:"explicit",model:model,sequence:0,policy:.outOfDomainInjection,budget:VerificationLimits(maxTransitions:0),equalInputs: ==))
    }
    func testForkRejectsAmbiguousRecordedEnvironment() throws {
        let (viewer,model) = fixture(),source = viewer.artifact.trace
        let config = RunConfig(strategy:source.config.strategy,parameters:[AuditParameter("stateless.debug.environment","original"),AuditParameter("stateless.debug.environment","different")])
        let trace = AuditTrace(metadata:source.metadata,config:config,initialState:source.initialState,initialChecks:source.initialChecks,steps:source.steps,termination:source.termination)
        let ambiguous = try EvidenceViewer(.v1(trace))
        let policy = try InputPolicy<Int,Int,Int>(label:"original",permits:{ _,_,_,_ in true })
        XCTAssertThrowsError(try ambiguous.fork(id:"branch",model:model,sequence:1,policy:policy,budget:VerificationLimits(maxTransitions:1),equalInputs: ==)) { error in
            XCTAssertEqual(String(describing:error),"ambiguous recorded environmental policy")
        }
    }

}
