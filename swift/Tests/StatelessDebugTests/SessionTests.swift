import XCTest
import Statelessness
@testable import StatelessDebug

final class SessionTests: XCTestCase {
    func model() -> Model<Int,Int,Int> {
        Model(initialState: { 0 },step: { s,i in Transition(state: s+i,outputs: [s+i]) },checkState: { s in s >= 3 ? [.failed("limit","")] : [] },cloneState: {$0},cloneInput: {$0},cloneOutput: {$0},inputs: { _ in InputIterator(values: [1,2]) })
    }
    func testAdmissionBreakpointRevisionAndCommittedObserverFailure() throws {
        let session = try DebugSession(id: "counter",model: model(),policy: InputPolicy.enumerated(equalInputs: ==),equalInputs: ==)
        XCTAssertThrowsError(try session.step(expectedRevision: 0,input: 7)); XCTAssertEqual(session.sequence,0)
        let id = try session.addPreBreakpoint { _,i in i == 1 }
        let first = try session.step(expectedRevision: 0,input: 1)
        XCTAssertFalse(first.delivered); XCTAssertEqual(first.stop,.preBreakpoint(id)); XCTAssertEqual(session.revision,0)
        XCTAssertTrue(try session.step(expectedRevision: 0,input: 1).delivered)
        XCTAssertEqual(session.revision,1); XCTAssertEqual(try session.state,1)
        XCTAssertThrowsError(try session.step(expectedRevision: 0,input: 1))
        let result = try session.step(expectedRevision: 1,input: 2) { _ in throw ModelError("observer failed") }
        XCTAssertTrue(result.delivered); XCTAssertEqual(result.stop,.propertyFailure)
        XCTAssertEqual(try session.state,3); XCTAssertEqual(session.phase,.ended); XCTAssertEqual(session.issues.count,1)
    }
    func testCandidateSessionBindingAndLookaheadBudget() throws {
        let session = try DebugSession(id: "counter",model: model(),policy: .outOfDomainInjection,equalInputs: ==)
        let page = try session.inputs(expectedRevision: 0,offset: 0,limit: 1)
        XCTAssertFalse(page.complete); XCTAssertEqual(page.nextOffset,1)
        let other = try DebugSession(id: "counter",model: model(),policy: .outOfDomainInjection,equalInputs: ==)
        XCTAssertThrowsError(try other.step(page.candidates[0]))
        XCTAssertTrue(try session.step(page.candidates[0]).delivered)
        XCTAssertThrowsError(try session.step(page.candidates[0]))
        var limits = SessionLimits(); limits.maxCandidates = 1
        let bounded = try DebugSession(id: "bounded",model: model(),policy: InputPolicy.enumerated(equalInputs: ==),limits: limits,equalInputs: ==)
        XCTAssertThrowsError(try bounded.step(expectedRevision: 0,input: 2)); XCTAssertEqual(bounded.sequence,0)
    }
    func testReentrantMutationRejectedAndInitialCheckFailure() throws {
        let session = try DebugSession(id: "counter",model: model(),policy: .outOfDomainInjection,equalInputs: ==)
        var rejected = false
        _ = try session.step(expectedRevision: 0,input: 1) { _ in
            do { _ = try session.step(expectedRevision: 1,input: 1) } catch DebugError.reentrant { rejected = true }
        }
        XCTAssertTrue(rejected); XCTAssertEqual(session.sequence,1)
        var broken = model(); broken.checkState = { _ in throw ModelError("check") }
        let initial = try DebugSession(id: "broken",model: broken,policy: .outOfDomainInjection,equalInputs: ==)
        XCTAssertEqual(initial.phase,.ended); XCTAssertFalse(initial.checksComplete)
    }
    func testContinueIteratorCannotReenterOwner() throws {
        let session = try DebugSession(id: "counter",model: model(),policy: .outOfDomainInjection,equalInputs: ==)
        var selected = false,rejected = false
        let iterator = InputIterator<Int> {
            guard !selected else { return nil }; selected = true
            do { _ = try session.step(expectedRevision: 0,input: 1) } catch DebugError.reentrant { rejected = true }
            return 1
        }
        let results = try session.runBounded(expectedRevision: 0,inputs: iterator,maxTurns: 2)
        XCTAssertTrue(rejected); XCTAssertEqual(results.count,1); XCTAssertEqual(session.sequence,1)
    }
}
