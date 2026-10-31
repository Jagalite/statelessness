import XCTest
import Foundation
import Statelessness
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

// Relocated package tests receive the reviewed catalog through
// STATELESS_REQUIREMENTS_DIR; in-tree runs fall back to docs/requirements.
private func requirementsDirectory() -> URL {
    if let path = ProcessInfo.processInfo.environment["STATELESS_REQUIREMENTS_DIR"] { return URL(fileURLWithPath:path) }
    return URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("docs/requirements")
}

final class ReferenceControllerTests: XCTestCase {
    func testBuildIdentityTracksReferenceImplementationAndCatalog() throws {
        let root = URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let dir = root.appendingPathComponent("swift/Sources/Statelessness")
        let names = ["ReferenceController.swift","ReferenceControllerCodec.swift","ReferenceCoverage.swift","ReferenceCatalogData.swift"]
        var hash: UInt64 = 0xcbf29ce484222325
        for name in names { for byte in try Data(contentsOf:dir.appendingPathComponent(name)) { hash = (hash ^ UInt64(byte)) &* 0x100000001b3 } }
        let identity = String(format:"%016llx",hash)
        XCTAssertTrue(try ReferenceController().metadata.build.hasPrefix("swift-reference-\(identity);"))
        XCTAssertNotEqual(try ReferenceController().metadata.build,try ReferenceController(fault:.missingCleanup).metadata.build)
    }
    func testReferenceReducerAndIndependentFaultCatalog() throws {
        let config = SearchConfig(maxStates:20_000,maxTransitions:500_000,maxDepth:30)
        let normal = try ReferenceController()
        let baseline = try enumerateStates(normal.model,config:config)
        XCTAssertEqual(baseline.termination,"graph_exhausted"); XCTAssertNil(baseline.failure)
        for fault in ReferenceFault.allCases {
            let controller = try ReferenceController(fault:fault)
            let report = try enumerateStates(controller.model,config:config)
            guard let failure = report.failure else { XCTFail("fault \(fault.rawValue) not detected: \(report.termination)"); continue }
            XCTAssertTrue(failure.violations.contains { $0.check.id == fault.target },"fault \(fault.rawValue) missed target \(fault.target)")
        }
    }
    func testCancelAndRetryAreHostDriven() throws {
        let model = try ReferenceController()
        let start = try model.step(ReferenceState(),.submit)
        XCTAssertEqual(start.outputs,[.start(1,1)])
        let retry = try model.step(start.state,.complete(1,1,.retryableFailure))
        XCTAssertEqual(retry.outputs,[]); XCTAssertEqual(retry.state.job(1)?.phase,.waiting(1))
        let tick = try model.step(retry.state,.tick(1))
        XCTAssertEqual(tick.outputs,[.start(1,2)])
        let cancel = try model.step(tick.state,.cancel(1))
        XCTAssertEqual(cancel.outputs,[.cancel(1,2)])
        let ack = try model.step(cancel.state,.cancelled(1,2))
        XCTAssertEqual(ack.outputs,[.settle(1,.cancelled),.release(1)])
        XCTAssertTrue(model.checkTransition(cancel.state,.cancelled(1,2),ack).allSatisfy { $0.status == .passed })
    }
    func testOmittedStateAndEffectsFailProgressRequirements() throws {
        let controller = try ReferenceController(), initial = ReferenceState()
        func failed(_ state: ReferenceState,_ input: ReferenceInput) -> Set<String> {
            Set(controller.checkTransition(state,input,Transition(state:state,outputs:[],disposition:.accepted)).filter { $0.status == .failed }.map(\.id))
        }
        XCTAssertTrue(failed(initial,.submit).contains("controller.submission_progress"))
        let running = try controller.step(initial,.submit).state
        XCTAssertTrue(failed(running,.cancel(1)).contains("controller.cancellation_progress"))
        let failure = failed(running,.complete(1,1,.success))
        XCTAssertTrue(failure.contains("controller.completion_progress")); XCTAssertTrue(failure.contains("controller.success_published"))
    }
    func testPortableCodecAndPropertyInvalidEvidence() throws {
        let controller = try ReferenceController(),codec = controller.codec
        let running = try controller.step(ReferenceState(),.submit).state
        let expected: [UInt8] = [0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,1]
        XCTAssertEqual(try codec.encodeState(running),expected)
        XCTAssertEqual(try codec.decodeState(expected),running)
        XCTAssertEqual(try codec.encodeInput(.complete(2,1,.retryableFailure)),[2,2,0,0,0,0,0,0,0,1,1])
        XCTAssertEqual(try codec.decodeInput([2,2,0,0,0,0,0,0,0,1,1]),.complete(2,1,.retryableFailure))
        XCTAssertEqual(try codec.encodeOutput(.settle(1,.cancelled)),[3,1,0,0,0,0,0,0,0,0,1])
        var malformed = expected; malformed[30] = 7
        XCTAssertThrowsError(try codec.decodeState(malformed))
        XCTAssertThrowsError(try codec.decodeInput([0,1,0,0,0,0,0,0,0,0,0]))
        let completed = try controller.step(running,.complete(1,1,.success)).state
        var invalid = completed; invalid.jobs[0].resourceOwned = true
        XCTAssertEqual(try codec.decodeState(codec.encodeState(invalid)),invalid)
        XCTAssertEqual(controller.checkState(invalid).first?.status,.failed)
        let trace = try Statelessness.record(controller.model,inputs:InputIterator(values:[.submit,.complete(1,1,.success)]))
        XCTAssertEqual(try replay(controller.model,trace:trace).outcome,"exact")
    }
    func testIndependentCatalogAndCoverageDetectWeakenedChecks() throws {
        let catalog = try String(contentsOf:requirementsDirectory().appendingPathComponent("controller-properties.txt"),encoding:.utf8)
        let requirements = try Data(contentsOf:requirementsDirectory().appendingPathComponent("controller.json"))
        let contract = try ReferenceRequirements(propertyCatalog:catalog,requirementJSON:Array(requirements))
        let bundled = try ReferenceRequirements.bundled()
        XCTAssertEqual(bundled.propertyFingerprint,contract.propertyFingerprint)
        XCTAssertEqual(bundled.manifestFingerprint,contract.manifestFingerprint)
        XCTAssertEqual(bundled.reviewStatus,contract.reviewStatus)
        XCTAssertEqual(contract.reviewStatus,"awaiting_independent_review")
        XCTAssertEqual(contract.properties.count,14); XCTAssertEqual(contract.goals.count,6)
        XCTAssertThrowsError(try ReferenceRequirements(propertyCatalog:catalog.replacingOccurrences(of:"state controller.state_valid\n",with:""),requirementJSON:Array(requirements)))
        let controller = try ReferenceController(),manifest = try contract.manifest(for:controller)
        let complete = try assuredEnumerate(controller.model,manifest:manifest,coverage:referenceCoverage)
        XCTAssertEqual(try complete.search.get().termination,"graph_exhausted")
        XCTAssertTrue(complete.assurance.assess().adequate)
        XCTAssertEqual(complete.assurance.observedFamilies.count,5); XCTAssertEqual(complete.assurance.observedGoals.count,6)
        var weakened = controller.model; weakened.checkState = { _ in [] }
        let result = try assuredEnumerate(weakened,manifest:manifest,coverage:referenceCoverage)
        XCTAssertEqual(try result.search.get().termination,"graph_exhausted")
        XCTAssertFalse(result.assurance.assess().adequate)
        XCTAssertEqual(result.assurance.propertyStatus(AssuranceProperty("controller.state_valid",.state)),.missing)
    }
    func testFaultEvaluationPreservesAndReplaysTargetWitnesses() throws {
        let requirements = try ReferenceRequirements.bundled()
        let incomplete = evaluateReferenceFault(.missingCleanup,requirements:requirements,config:SearchConfig(maxStates:1))
        guard case .notEvaluated = incomplete.status else { return XCTFail("bounded search must remain incomplete") }
        for fault in ReferenceFault.allCases {
            let outcome = evaluateReferenceFault(fault,requirements:requirements)
            XCTAssertEqual(outcome.status,.killed,"\(fault.rawValue): \(outcome.status)")
            for witness in [outcome.original,outcome.minimized] {
                let failure = try XCTUnwrap(witness)
                let model = try ReferenceController(fault:fault).model
                let trace = try Statelessness.record(model,inputs:InputIterator(values:failure.inputs))
                let replayed = try replay(model,trace:trace)
                XCTAssertEqual(replayed.outcome,"exact"); XCTAssertTrue(replayed.failureReproduced)
                XCTAssertTrue(trace.steps.flatMap(\.checks).contains { $0.id == fault.target && $0.status == .failed })
            }
        }
    }
    func testReferenceGeneratorUsesIndexedDomainAndAdmission() throws {
        let controller = try ReferenceController(),state = ReferenceState(),iterator = try controller.inputs(state)
        var domain: [ReferenceInput] = []
        while let input = try iterator.next() { domain.append(input) }
        var expected = Rng(seed:42),actual = Rng(seed:42)
        let index = try XCTUnwrap(expected.index(UInt64(domain.count)))
        XCTAssertEqual(try controller.generated.generate(state,&actual),domain[Int(index)])
        XCTAssertEqual(actual.nextU64(),expected.nextU64())
        XCTAssertFalse(try controller.generated.isEnabled(state,.complete(99,1,.success)))
    }
    func testRetainedRegressionCorpusAndFixedControls() throws {
        let text = try String(contentsOf:requirementsDirectory().appendingPathComponent("controller-witnesses.txt"),encoding:.utf8)
        let cases = try bundledReferenceRegressionCorpus()
        XCTAssertEqual(cases,try parseReferenceRegressionCorpus(text)); XCTAssertEqual(cases.count,9)
        let checked = try checkReferenceRegressions(cases)
        XCTAssertEqual(checked.count,9)
        XCTAssertTrue(checked.allSatisfy { $0.mutant.termination == .propertyFailed && $0.fixed.termination == .completed })
        XCTAssertThrowsError(try parseReferenceRegressionCorpus("submit\n"+text))
        XCTAssertThrowsError(try parseReferenceRegressionCorpus(text+"\n[missing-cleanup]\nsubmit\n"))
        XCTAssertThrowsError(try parseReferenceRegressionCorpus(text.replacingOccurrences(of:"[ignored-mutation]",with:"[unknown-fault]")))
        XCTAssertThrowsError(try checkReferenceRegressions(Array(cases.dropLast())))
    }
    func testPortableWitnessRetainsFaultIdentityAndTarget() throws {
        let result = evaluateReferenceFault(.missingCleanup,requirements:try ReferenceRequirements.bundled())
        let audit = try recordReferenceWitness(result)
        var bytes: [UInt8] = []; try writeAuditTrace(audit) { bytes.append(contentsOf:$0) }
        var offset = 0
        let decoded = try readAuditTrace { count in let end = min(bytes.count,offset+count); defer { offset = end }; return Array(bytes[offset..<end]) }
        XCTAssertEqual(decoded,audit)
        XCTAssertEqual(try verifyReferenceWitness(decoded,fault:.missingCleanup).outcome,"exact")
        XCTAssertThrowsError(try verifyReferenceWitness(decoded,fault:.doubleSettlement))
        let foreignMetadata = Metadata(name:audit.metadata.name,modelVersion:audit.metadata.modelVersion,propertiesVersion:audit.metadata.propertiesVersion,codecVersion:audit.metadata.codecVersion,build:"foreign-build")
        let foreign = AuditTrace(metadata:foreignMetadata,config:audit.config,initialState:audit.initialState,initialChecks:audit.initialChecks,steps:audit.steps,termination:audit.termination)
        XCTAssertThrowsError(try verifyReferenceWitness(foreign,fault:.missingCleanup))
        XCTAssertFalse(try verifyReferenceWitness(foreign,fault:.missingCleanup,allowBuildMismatch:true).buildMatches)
        let bad = AuditTrace(metadata:audit.metadata,config:RunConfig(strategy:audit.config.strategy,parameters:[AuditParameter("mutation","missing-cleanup"),AuditParameter("target","wrong")]),initialState:audit.initialState,initialChecks:audit.initialChecks,steps:audit.steps,termination:audit.termination)
        XCTAssertThrowsError(try verifyReferenceWitness(bad,fault:.missingCleanup))
    }
    func testFaultSuiteRequiresBothRetainedWitnesses() throws {
        let requirements = try ReferenceRequirements.bundled()
        let suite = evaluateReferenceFaultSuite(requirements:requirements)
        XCTAssertEqual(suite.results.count,9); XCTAssertGreaterThan(suite.retainedBytes,0)
        XCTAssertTrue(suite.results.allSatisfy { $0.evaluation.status == .killed && $0.originalTrace != nil && $0.minimizedTrace != nil })
        let first = try XCTUnwrap(suite.results.first?.originalTrace)
        var firstBytes: UInt64 = 0; try writeAuditTrace(first) { firstBytes += UInt64($0.count) }
        let bounded = evaluateReferenceFaultSuite(requirements:requirements,maxEvidenceBytes:firstBytes)
        XCTAssertEqual(bounded.retainedBytes,firstBytes)
        XCTAssertNotNil(bounded.results.first?.originalTrace); XCTAssertNil(bounded.results.first?.minimizedTrace)
        guard case .notEvaluated = bounded.results[0].evaluation.status else { return XCTFail("partial evidence must not be a kill") }
        XCTAssertFalse(bounded.results.contains { $0.evaluation.status == .killed })
        let empty = evaluateReferenceFaultSuite(requirements:requirements,maxEvidenceBytes:0)
        XCTAssertEqual(empty.retainedBytes,0); XCTAssertFalse(empty.results.contains { $0.evaluation.status == .killed })
    }
    func testPersistedSuiteAndCorruption() throws {
        let parent = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString,isDirectory:true)
        try FileManager.default.createDirectory(at:parent,withIntermediateDirectories:false)
        defer { try? FileManager.default.removeItem(at:parent) }
        let requirements = try ReferenceRequirements.bundled()
        let suite = evaluateReferenceFaultSuite(requirements:requirements)
        let directory = parent.appendingPathComponent("suite")
        XCTAssertTrue(try writeReferenceFaultSuite(suite,to:directory).complete)
        let verified = try verifyReferenceFaultSuite(at:directory)
        XCTAssertEqual(verified.verifiedWitnesses,18); XCTAssertTrue(verified.buildMatches); XCTAssertTrue(verified.witnessKindsVerified)
        XCTAssertEqual(try verifyReferenceFaultSuite(at:directory,expectedProducerBuild:ReferenceController().metadata.build).verifiedWitnesses,18)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory,allowBuildMismatch:true,expectedProducerBuild:"wrong-build"))
        XCTAssertThrowsError(try writeReferenceFaultSuite(suite,to:directory))
        let summaryURL = directory.appendingPathComponent("mutations.json")
        let summaryBytes = try Data(contentsOf:summaryURL)
        let summaryText = try XCTUnwrap(String(data:summaryBytes,encoding:.utf8))
        let weakened = summaryText.replacingOccurrences(of:"\"shrink_complete\":true",with:"\"shrink_complete\":false")
        XCTAssertNotEqual(weakened,summaryText)
        // Rust keeps an attempt-limited but validated shrink as a kill.
        try Data(weakened.utf8).write(to:summaryURL)
        XCTAssertEqual(try verifyReferenceFaultSuite(at:directory).verifiedWitnesses,18)
        let nonBoolean = summaryText.replacingOccurrences(of:"\"shrink_complete\":true",with:"\"shrink_complete\":\"true\"")
        try Data(nonBoolean.utf8).write(to:summaryURL)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        try summaryBytes.write(to:summaryURL)
        #if canImport(Darwin) || canImport(Glibc)
        try FileManager.default.removeItem(at:summaryURL)
        XCTAssertEqual(mkfifo(summaryURL.path,mode_t(0o600)),0)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        try FileManager.default.removeItem(at:summaryURL)
        try FileManager.default.createSymbolicLink(at:summaryURL,withDestinationURL:directory.appendingPathComponent("missing-cleanup-original.sttrace"))
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        try FileManager.default.removeItem(at:summaryURL)
        try summaryBytes.write(to:summaryURL)
        let witnessURL = directory.appendingPathComponent("missing-cleanup-original.sttrace")
        let witnessBytes = try Data(contentsOf:witnessURL)
        try FileManager.default.removeItem(at:witnessURL)
        try FileManager.default.createSymbolicLink(at:witnessURL,withDestinationURL:summaryURL)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        try FileManager.default.removeItem(at:witnessURL)
        try witnessBytes.write(to:witnessURL)
        #endif
        guard case var .object(summary) = try parseJSON(Array(summaryBytes)) else { return XCTFail("invalid suite summary") }
        summary[ExactString("build")] = .string("swift-reference-different;generations=2;attempts=2;time=2;fault=none")
        try Data(renderJSON(.object(summary))).write(to:summaryURL)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory,allowBuildMismatch:true))
        try summaryBytes.write(to:summaryURL)
        let first = try XCTUnwrap(suite.results.first),trace = try XCTUnwrap(first.originalTrace)
        let unlabeled = AuditTrace(metadata:trace.metadata,config:RunConfig(strategy:trace.config.strategy,parameters:trace.config.parameters.filter { $0.key != "witness" }),initialState:trace.initialState,initialChecks:trace.initialChecks,steps:trace.steps,termination:trace.termination)
        var bytes: [UInt8] = []; try writeAuditTrace(unlabeled) { bytes.append(contentsOf:$0) }
        try Data(bytes).write(to:directory.appendingPathComponent("\(first.evaluation.fault.rawValue)-original.sttrace"))
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        XCTAssertFalse(try verifyReferenceFaultSuite(at:directory,allowUnlabeledWitnesses:true).witnessKindsVerified)
        try Data([0]).write(to:directory.appendingPathComponent("missing-cleanup-minimized.sttrace"))
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:directory))
        let limited = evaluateReferenceFaultSuite(requirements:requirements,maxEvidenceBytes:0)
        let incomplete = parent.appendingPathComponent("incomplete")
        XCTAssertFalse(try writeReferenceFaultSuite(limited,to:incomplete).complete)
        XCTAssertThrowsError(try verifyReferenceFaultSuite(at:incomplete))
        // A witness whose encoding fails must not leave a partial file behind.
        var tiny = BinaryTraceLimits(); tiny.maxTotalBytes = 11
        let unwritable = parent.appendingPathComponent("unwritable")
        let partial = try writeReferenceFaultSuite(suite,to:unwritable,traceLimits:tiny)
        XCTAssertFalse(partial.complete); XCTAssertTrue(partial.results.allSatisfy { $0.originalName == nil && $0.minimizedName == nil })
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:unwritable.path),["mutations.json"])
    }

}
