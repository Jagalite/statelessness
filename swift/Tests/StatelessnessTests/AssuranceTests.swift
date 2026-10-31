import XCTest
import Foundation
@testable import Statelessness

private final class AssuranceMutableBox: @unchecked Sendable {
    var value: Int
    init(_ value: Int) { self.value = value }
}
private final class AssuranceObserved: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [Int] = []
    func record(_ values: [Int]) { lock.lock(); defer { lock.unlock() }; stored = values }
    func read() -> [Int] { lock.lock(); defer { lock.unlock() }; return stored }
}
final class AssuranceTests: XCTestCase {
    private func manifest() -> AssuranceManifest {
        AssuranceManifest(model:"lamp",provenance:("manifest-v1","model-v1","properties-v1"),properties:[AssuranceProperty("safe",.state),AssuranceProperty("edge",.transition)],families:[AssuranceFamily("toggle")],goals:[AssuranceGoal("lit",.transition)])
    }
    private func model() -> Model<Bool,Bool,Bool> {
        Model(initialState:{ false },step:{ state,_ in Transition(state:!state,outputs:[]) },checkState:{ _ in [.passed("safe")] },cloneState:{ $0 },cloneInput:{ $0 },cloneOutput:{ $0 },checkTransition:{ _,_,_ in [.passed("edge")] },inputs:{ _ in InputIterator(values:[true]) },equalStates:==)
    }
    func testExhaustiveObserverRecordsActualSearch() throws {
        let observer = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,transition,_,hits in
            hits.family("toggle"); if transition.state { hits.goal("lit") }
        }
        let search = try enumerateStates(model(),observer:observer.observer())
        let report = try observer.finish(termination:search.termination)
        XCTAssertEqual(search.termination,"graph_exhausted"); XCTAssertEqual(report.checkedTransitions,2)
        XCTAssertTrue(report.assess().adequate); XCTAssertEqual(report.goalStatus(manifest().goals[0]),.observed)
    }
    func testMissingDuplicateAndIncompleteCoverageNeverPass() throws {
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,_,_,hits in
            hits.family("toggle"); hits.goal("lit"); throw ModelError("failed coverage")
        }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe"),.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[],[.skipped("edge","not applicable")],[])
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertFalse(report.assess().adequate); XCTAssertEqual(report.propertyStatus(AssuranceProperty("safe",.state)),.missing)
        XCTAssertEqual(report.propertyStatus(AssuranceProperty("edge",.transition)),.skipped)
        XCTAssertEqual(report.unclassifiedTransitions,1); XCTAssertTrue(report.observedGoals.isEmpty)
        XCTAssertEqual(report.goalStatus(manifest().goals[0]),.notReachedBeforeStop)
    }
    func testReplacingHitsCannotEraseOverflowOrLimits() throws {
        var limits = AssuranceObservationLimits(); limits.maxCoverageHits = 1
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,hits in
            hits.family("toggle"); hits.goal("lit")
            hits = AssuranceHits(); hits.family("toggle"); hits.goal("lit")
        }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertFalse(report.assess().adequate); XCTAssertEqual(report.unclassifiedTransitions,1)
        XCTAssertTrue(report.observedFamilies.isEmpty); XCTAssertTrue(report.observedGoals.isEmpty)
        XCTAssertTrue(report.callbackErrors.contains("assurance evidence limit exceeded"))
    }
    func testCopiedHitsCannotEraseOverflow() throws {
        var limits = AssuranceObservationLimits(); limits.maxCoverageHits = 1
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,hits in
            let saved = hits
            hits.family("toggle"); hits.goal("lit")
            hits = saved; hits.family("toggle")
        }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertFalse(report.assess().adequate); XCTAssertEqual(report.unclassifiedTransitions,1)
        XCTAssertTrue(report.observedFamilies.isEmpty)
        XCTAssertTrue(report.callbackErrors.contains("assurance evidence limit exceeded"))
    }
    func testReentrantObserverCannotInjectEvidence() throws {
        var nested: SearchObserver<Bool,Bool,Bool>?
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,_,_,hits in
            XCTAssertThrowsError(try nested?.initial(false,[.passed("safe")]))
            hits.family("toggle"); hits.goal("lit")
        }
        let observer = collector.observer(); nested = observer
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertFalse(report.assess().adequate)
        XCTAssertTrue(report.callbackErrors.contains("overlapping assurance observer use"))
        XCTAssertEqual(report.observedProperties[AssuranceProperty("safe",.state)]?.passed,2)
    }
    func testManifestAndMetadataAreBoundedBeforeRetention() throws {
        var limits = AssuranceObservationLimits(); limits.maxManifestStringBytes = 12
        var oversized = manifest(); oversized.provenance.manifest = String(repeating:"x",count:13)
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:oversized,metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,_ in })
        oversized = manifest(); oversized.families = [AssuranceFamily("toggle",.excluded(String(repeating:"x",count:13)))]
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:oversized,metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,_ in })
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp",build:String(repeating:"x",count:13)),limits:limits) { _,_,_,_,_ in })
        limits = AssuranceObservationLimits(); limits.maxBounds = 0
        oversized = manifest(); oversized.bounds = ["bound":"1"]
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:oversized,metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,_ in })
        limits = AssuranceObservationLimits(); limits.maxManifestBytes = 20
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,_ in })
    }
    func testBudgetOverflowRetainsEvidenceAndFailsClosed() throws {
        var limits = AssuranceObservationLimits(); limits.maxBudgetEntries = 1; limits.maxBudgetStringBytes = 32; limits.maxBudgetBytes = 32
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp"),limits:limits) { _,_,_,_,hits in hits.family("toggle"); hits.goal("lit") }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let oversized = try collector.finish(termination:"graph_exhausted",budgets:["a":"1","b":"2"])
        XCTAssertFalse(oversized.assess().adequate); XCTAssertTrue(oversized.budgets.isEmpty)
        XCTAssertEqual(oversized.checkedTransitions,1); XCTAssertTrue(oversized.observedGoals.contains(ExactString("lit")))
        XCTAssertTrue(oversized.callbackErrors.contains("assurance evidence limit exceeded"))
        let valid = try collector.finish(termination:"graph_exhausted",budgets:["a":"1"])
        XCTAssertFalse(valid.assess().adequate); XCTAssertEqual(valid.budgets["a"],"1")
    }
    func testManifestIdentityAndHistoryReachability() throws {
        var m = manifest(); m.goals = [AssuranceGoal("history",.history),AssuranceGoal("transition",.transition)]
        XCTAssertThrowsError(try AssuranceObserver<Bool,Bool,Bool>(manifest:m,metadata:Metadata(name:"other")) { _,_,_,_,_ in })
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:m,metadata:Metadata(name:"lamp")) { _,_,_,_,_ in }
        XCTAssertTrue(collector.observer().needsHistory())
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertEqual(report.goalStatus(m.goals[0]),.notReachedBeforeStop); XCTAssertEqual(report.goalStatus(m.goals[1]),.unreachableUnderDeclaredModel)
        XCTAssertFalse(report.assess().adequate)
        m.properties.append(m.properties[0]); XCTAssertThrowsError(try m.validate())
    }
    // Rust Observer::check_error only records the batch; the search error
    // reaches finish(error:) separately.
    func testIncompleteCheckBatchRecordsChecksWithoutSyntheticError() throws {
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,_,_,_ in }
        try collector.observer().checkError("state",[.passed("safe")],false)
        let report = try collector.finish(termination:"graph_exhausted")
        XCTAssertTrue(report.callbackErrors.isEmpty)
        XCTAssertEqual(report.observedProperties[AssuranceProperty("safe",.state)]?.passed,1)
        XCTAssertEqual(report.observedProperties[AssuranceProperty("safe",.state)]?.missing,0)
        XCTAssertFalse(report.assess().adequate)
        let failed = try collector.finish(outcome:.failed,termination:"ModelError",error:ModelError("checker failed"))
        XCTAssertEqual(failed.callbackErrors,["checker failed"])
    }
    func testAssessmentReasonsUseRustDebugSpellingAndSortedOrder() throws {
        var m = manifest(); m.families = [AssuranceFamily("toggle"),AssuranceFamily("legacy",.excluded("not \"modeled\""))]
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:m,metadata:Metadata(name:"lamp")) { _,_,_,_,hits in hits.family("legacy") }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.failed("safe","x")],[],[])
        let report = try collector.finish(termination:"deadline")
        XCTAssertEqual(report.outcome,.timedOut); XCTAssertEqual(report.termination,"Deadline")
        XCTAssertEqual(report.assess().reasons,[
            "property edge Transition: Missing",
            "property safe State: Failed",
            "observed failed, skipped, or omitted required checks",
            "input family legacy: ExclusionViolated { reason: \"not \\\"modeled\\\"\" }",
            "input family toggle: Missing",
            "cover goal lit: NotReachedBeforeStop",
            "required search outcome not met: TimedOut",
        ])
        let unclassified = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,_,_,_ in }
        try unclassified.observer().transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        XCTAssertTrue(try unclassified.finish(termination:"graph_exhausted").assess().reasons.contains("some checked inputs had no declared family classification"))
        XCTAssertEqual(assuranceRustDebug("a\\b\n\u{1}\u{7f}é"),"\"a\\\\b\\n\\u{1}\\u{7f}é\"")
    }
    func testValidateGatesAssessmentAndMerge() throws {
        let metadata = Metadata(name:"lamp")
        let inconsistent = AssuranceReport(manifest:manifest(),metadata:metadata,outcome:.graphExhausted,termination:"GraphExhausted",budgets:[:],checkedTransitions:0,unclassifiedTransitions:1,observedProperties:[:],observedFamilies:[],observedGoals:[],callbackErrors:[])
        XCTAssertThrowsError(try inconsistent.validate()) { XCTAssertEqual(String(describing:$0),"assurance unclassified count exceeds checked transitions") }
        let assessment = inconsistent.assess()
        XCTAssertFalse(assessment.adequate)
        XCTAssertEqual(assessment.reasons,["inconsistent assurance report: assurance unclassified count exceeds checked transitions"])
        XCTAssertEqual(assessment.confidence,"The report is internally inconsistent; no adequacy claim is established.")
        XCTAssertThrowsError(try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:[0],reports:[(0,inconsistent)]))
        let uncovered = AssuranceReport(manifest:manifest(),metadata:metadata,outcome:.graphExhausted,termination:"GraphExhausted",budgets:[:],checkedTransitions:0,unclassifiedTransitions:0,observedProperties:[:],observedFamilies:[ExactString("toggle")],observedGoals:[],callbackErrors:[])
        XCTAssertThrowsError(try uncovered.validate()) { XCTAssertEqual(String(describing:$0),"assurance coverage has no checked transition evidence") }
        let blank = AssuranceReport(manifest:manifest(),metadata:metadata,outcome:.graphExhausted,termination:" ",budgets:[:],checkedTransitions:0,unclassifiedTransitions:0,observedProperties:[:],observedFamilies:[],observedGoals:[],callbackErrors:[])
        XCTAssertThrowsError(try blank.validate())
    }
    func testRunEvidenceUsesRustTerminationSpellingAndBudgetKeys() throws {
        let metadata = Metadata(name:"lamp")
        let run = try assuredEnumerate(model(),manifest:manifest(),metadata:{ metadata },limits:SearchLimits(control:RunLimits(maxDuration:0.5))) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertEqual(run.assurance.outcome,.graphExhausted); XCTAssertEqual(run.assurance.termination,"GraphExhausted")
        XCTAssertEqual(run.assurance.budgets["max_duration_ns"],"500000000")
        XCTAssertFalse(run.assurance.budgets.keys.contains { $0 == "max_duration_seconds" || $0.hasPrefix("assurance.") })
        XCTAssertEqual(run.assurance.nativeBudgets["max_duration_seconds"],"0.5")
        XCTAssertEqual(run.assurance.nativeBudgets["assurance.max_errors"],"1024")
        guard case let .object(fields) = run.assurance.assuranceJSON(),case let .object(search)? = fields[ExactString("search")],case let .object(budgets)? = search[ExactString("budgets")] else { return XCTFail("missing search evidence") }
        XCTAssertEqual(search[ExactString("termination")],.string("GraphExhausted")); XCTAssertEqual(search[ExactString("outcome")],.string("GraphExhausted"))
        XCTAssertEqual(Set(budgets.keys.map(\.value)),["max_states","max_transitions","max_depth","max_duration_ns","cancellation_enabled"])
        var bad = model(); bad.checkState = { _ in throw ModelError("checker failed") }
        let failed = try assuredEnumerate(bad,manifest:manifest(),metadata:{ metadata }) { _,_,_,_,_ in }
        XCTAssertEqual(failed.assurance.outcome,.failed); XCTAssertEqual(failed.assurance.termination,"ModelError")
        let merged = try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:[0],reports:[(0,run.assurance)])
        XCTAssertEqual(merged.report.termination,"MergedJobs"); XCTAssertEqual(merged.report.outcome,.graphExhausted)
        XCTAssertEqual(merged.report.budgets,["requested_jobs":"1","reported_jobs":"1"])
    }
    func testObservationLimitsFailClosedForTruncatedEvidence() throws {
        let metadata = Metadata(name:"lamp"),source = model()
        var limits = AssuranceObservationLimits(); limits.maxCoverageHits = 1
        let hitLimited = try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,_,_,hits in hits.family("toggle"); hits.goal("lit") }
        XCTAssertFalse(hitLimited.assurance.assess().adequate)
        XCTAssertTrue(hitLimited.assurance.observedFamilies.isEmpty); XCTAssertTrue(hitLimited.assurance.observedGoals.isEmpty)
        XCTAssertGreaterThan(hitLimited.assurance.unclassifiedTransitions,0)
        XCTAssertTrue(hitLimited.assurance.callbackErrors.contains("assurance evidence limit exceeded"))
        XCTAssertEqual(hitLimited.assurance.nativeBudgets["assurance.max_coverage_hits"],"1")
        XCTAssertNil(hitLimited.assurance.budgets["assurance.max_coverage_hits"])
        let resetHits = try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,_,_,hits in
            hits = AssuranceHits(); hits.family("toggle"); hits.goal("lit")
        }
        XCTAssertFalse(resetHits.assurance.assess().adequate)
        XCTAssertTrue(resetHits.assurance.callbackErrors.contains("assurance evidence limit exceeded"))
        XCTAssertTrue(resetHits.assurance.observedFamilies.isEmpty)

        limits = AssuranceObservationLimits(); limits.maxProperties = 2
        var extra = source; extra.checkState = { _ in [.passed("safe"),.passed("extra")] }
        let propertyLimited = try assuredEnumerate(extra,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertFalse(propertyLimited.assurance.assess().adequate)
        XCTAssertLessThanOrEqual(propertyLimited.assurance.observedProperties.count,2)
        XCTAssertTrue(propertyLimited.assurance.callbackErrors.contains("assurance evidence limit exceeded"))

        limits = AssuranceObservationLimits(); limits.maxErrors = 0
        let errors = try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,_,_,hits in hits.family("unknown") }
        XCTAssertFalse(errors.assurance.assess().adequate)
        XCTAssertEqual(errors.assurance.callbackErrors,["assurance evidence limit exceeded"])
        limits = AssuranceObservationLimits(); limits.maxIdentityBytes = 8
        var oversized = source; oversized.checkState = { _ in [.passed("safe"),.passed("123456789")] }
        let longID = try assuredEnumerate(oversized,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertFalse(longID.assurance.assess().adequate)
        XCTAssertTrue(longID.assurance.callbackErrors.contains("assurance evidence limit exceeded"))
        limits = AssuranceObservationLimits(); limits.maxCoverageIdentities = 1
        XCTAssertThrowsError(try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,_,_,_ in })
        limits.maxIdentityBytes = -1
        XCTAssertThrowsError(try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },observationLimits:limits) { _,_,_,_,_ in })
    }
    func testAssuredRunDerivesIdentityTerminationAndRetainsErrors() throws {
        var source = model()
        XCTAssertThrowsError(try assuredEnumerate(source,manifest:manifest()) { _,_,_,_,_ in })
        source.codec = Codec(metadata:{ Metadata(name:"lamp",build:"fixture") },encodeState:{ [$0 ? 1 : 0] },decodeState:{ _ in false },encodeInput:{ [$0 ? 1 : 0] },decodeInput:{ _ in true },encodeOutput:{ [$0 ? 1 : 0] })
        let result = try assuredEnumerate(source,manifest:manifest()) { _,_,transition,_,hits in hits.family("toggle"); if transition.state { hits.goal("lit") } }
        XCTAssertEqual(try result.search.get().termination,"graph_exhausted")
        XCTAssertEqual(result.assurance.metadata.build,"fixture"); XCTAssertTrue(result.assurance.assess().adequate)
        var fuzzManifest = manifest(); fuzzManifest.requiredOutcome = .completed
        let generator = Generate(model:source,generate:{ _,_ in true },equalInputs:==)
        let fuzz = try assuredFuzz64(generator,manifest:fuzzManifest,config:FuzzConfig64(seed:9,cases:2,maxSteps:1)) { _,_,transition,_,hits in hits.family("toggle"); if transition.state { hits.goal("lit") } }
        XCTAssertEqual(try fuzz.search.get().termination,"cases_completed")
        XCTAssertEqual(fuzz.assurance.budgets["seed"],"9"); XCTAssertTrue(fuzz.assurance.assess().adequate)
        source.checkState = { _ in throw ModelError("checker failed") }
        let failed = try assuredEnumerate(source,manifest:manifest()) { _,_,_,_,_ in }
        XCTAssertThrowsError(try failed.search.get()); XCTAssertEqual(failed.assurance.termination,"ModelError"); XCTAssertEqual(failed.assurance.outcome,.failed)
        XCTAssertFalse(failed.assurance.callbackErrors.isEmpty); XCTAssertFalse(failed.assurance.assess().adequate)
    }
    func testCodecFreeAssuranceAndManifestDeclarations() throws {
        let source = model()
        var m = AssuranceManifest(model:"lamp",provenance:("catalog","model","checks"),properties:[],families:[AssuranceFamily("toggle")],goals:[AssuranceGoal("lit",.transition)])
        m.require(PropertyMetadata("safe")); m.require(PropertyMetadata("edge",phase:.transition))
        m.addDomain(try DomainDescriptor(name:"signals",assumptions:"two finite inputs",maxEntries:2))
        XCTAssertEqual(m.bounds["domain.signals.assumptions"],"two finite inputs")
        XCTAssertEqual(m.bounds["domain.signals.max_entries"],"2")
        let metadata = Metadata(name:"lamp",build:"metadata-only")
        let result = try assuredEnumerate(source,manifest:m,metadata:{ metadata }) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertEqual(try result.search.get().termination,"graph_exhausted"); XCTAssertTrue(result.assurance.assess().adequate)
        XCTAssertTrue(result.assurance.assess().confidence.contains("exhausted finite model"))
        XCTAssertEqual(result.assurance.budgets["cancellation_enabled"],"false")
        m.requiredOutcome = .completed
        let generator = Generate(model:source,generate:{ _,_ in true },equalInputs:==)
        let run = try assuredFuzz64(generator,manifest:m,metadata:{ metadata },config:FuzzConfig64(cases:1,maxSteps:1),limits:RunLimits(maxDuration:60,isCancelled:{ false })) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertTrue(run.assurance.assess().adequate)
        XCTAssertTrue(run.assurance.assess().confidence.contains("sampled cases"))
        XCTAssertEqual(run.assurance.budgets["cancellation_enabled"],"true")
        XCTAssertEqual(run.assurance.budgets["max_duration_ns"],"60000000000")
        let feedback = Feedback<Bool,Bool,Bool,Bool>(metadata:{ FeedbackMetadata(name:"state",build:"test") },cloneFeature:{ $0 },transitionFeatures:{ _,_,turn,buffer in try buffer.push(turn.state) })
        let guided = try assuredGuidedFuzz64(generator,manifest:m,metadata:{ metadata },config:FuzzConfig64(cases:1,maxSteps:1),feedback:feedback,limits:RunLimits(maxDuration:60)) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertTrue(guided.assurance.assess().adequate)
        XCTAssertEqual(guided.assurance.budgets["max_duration_ns"],"60000000000")
        var withCodec = source
        withCodec.codec = Codec(metadata:{ Metadata(name:"lamp",build:"codec") },encodeState:{ [$0 ? 1 : 0] },decodeState:{ _ in false },encodeInput:{ [$0 ? 1 : 0] },decodeInput:{ _ in true },encodeOutput:{ [$0 ? 1 : 0] })
        XCTAssertThrowsError(try assuredEnumerate(withCodec,manifest:m,metadata:{ metadata }) { _,_,_,_,_ in })
    }
    func testDurationBudgetKeepsSecondsAndAddsExactNanoseconds() throws {
        let source = model(),metadata = Metadata(name:"lamp")
        func run(_ duration: TimeInterval?) throws -> AssuranceReport {
            try assuredEnumerate(source,manifest:manifest(),metadata:{ metadata },limits:SearchLimits(control:RunLimits(maxDuration:duration))) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }.assurance
        }
        let exact = try run(0.125)
        XCTAssertEqual(exact.nativeBudgets["max_duration_seconds"],"0.125"); XCTAssertNil(exact.budgets["max_duration_seconds"])
        XCTAssertEqual(exact.budgets["max_duration_ns"],"125000000")
        let subnanosecond = try run(0.0000000005)
        XCTAssertNotNil(subnanosecond.nativeBudgets["max_duration_seconds"]); XCTAssertNil(subnanosecond.budgets["max_duration_ns"])
        let large = try run(100_000_000)
        XCTAssertNotNil(large.nativeBudgets["max_duration_seconds"]); XCTAssertNil(large.budgets["max_duration_ns"])
        let absent = try run(nil)
        XCTAssertNil(absent.nativeBudgets["max_duration_seconds"]); XCTAssertNil(absent.budgets["max_duration_ns"])
    }
    func testReplacingDurationBudgetRemovesStaleValues() {
        var budgets: [String:String] = [:], native: [String:String] = [:]
        assuranceControlBudgets(60,cancellationEnabled:false,into:&budgets,native:&native)
        XCTAssertEqual(budgets["max_duration_ns"],"60000000000")
        assuranceControlBudgets(0.0000000005,cancellationEnabled:true,into:&budgets,native:&native)
        XCTAssertNil(budgets["max_duration_ns"])
        XCTAssertNotNil(native["max_duration_seconds"]); XCTAssertNil(budgets["max_duration_seconds"])
        XCTAssertEqual(budgets["cancellation_enabled"],"true")
        assuranceControlBudgets(nil,cancellationEnabled:false,into:&budgets,native:&native)
        XCTAssertNil(budgets["max_duration_ns"]); XCTAssertNil(native["max_duration_seconds"])
    }
    func testExactIntegerDurationControlsAndBudget() throws {
        let source = model(), metadata = Metadata(name: "lamp")
        let maximum = DurationNanoseconds(high: .max, low: .max)
        XCTAssertEqual(maximum.decimal, "340282366920938463463374607431768211455")
        XCTAssertEqual(DurationNanoseconds(high: 1, low: 0).decimal, "18446744073709551616")
        let run = try assuredEnumerate(source, manifest: manifest(), metadata: { metadata }, limits: SearchLimits(control: RunLimits(maxDurationNanoseconds: maximum))) { _,_,_,_,_ in }
        XCTAssertEqual(run.assurance.budgets["max_duration_ns"], maximum.decimal)
        XCTAssertNil(run.assurance.budgets["max_duration_seconds"]); XCTAssertNil(run.assurance.nativeBudgets["max_duration_seconds"])
        XCTAssertEqual(try run.search.get().termination, "graph_exhausted")
        let zero = try assuredEnumerate(source, manifest: manifest(), metadata: { metadata }, limits: SearchLimits(control: RunLimits(maxDurationNanoseconds: DurationNanoseconds(low: 0)))) { _,_,_,_,_ in }
        XCTAssertEqual(try zero.search.get().termination, "deadline")
        XCTAssertEqual(zero.assurance.budgets["max_duration_ns"], "0")
        let cancelled = try assuredEnumerate(source, manifest: manifest(), metadata: { metadata }, limits: SearchLimits(control: RunLimits(maxDurationNanoseconds: DurationNanoseconds(low: 0), isCancelled: { true }))) { _,_,_,_,_ in }
        XCTAssertEqual(try cancelled.search.get().termination, "cancelled")
    }
    func testIntegerDeadlineAccumulatesAcrossClockWrap() {
        var ticks: [UInt64] = [.max - 4, 3, .max - 4, 3]
        let clock = IntegerDeadlineClock { ticks.removeFirst() }
        let deadline = DurationNanoseconds(high: 1, low: 7)
        XCTAssertFalse(clock.reached(deadline))
        XCTAssertFalse(clock.reached(deadline))
        XCTAssertTrue(clock.reached(deadline))
    }
    func testMergePreservesMissingJobsAndRejectsIdentityMismatch() throws {
        let metadata = Metadata(name:"lamp",build:"fixture")
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:metadata) { _,_,_,_,hits in hits.family("toggle"); hits.goal("lit") }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let report = try collector.finish(termination:"cases_completed")
        let merged = try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:[1,2],reports:[(1,report)])
        XCTAssertEqual(merged.missingJobs,[2]); XCTAssertEqual(merged.report.termination,"MergedJobs"); XCTAssertEqual(merged.report.outcome,.interrupted); XCTAssertFalse(merged.report.assess().adequate)
        let json = merged.assuranceJSON(), imported = try MergedAssuranceReport.fromAssuranceJSON(json)
        XCTAssertEqual(imported.original,json); XCTAssertEqual(imported.report.missingJobs,[2]); XCTAssertEqual(imported.report.jobs.count,1)
        guard case var .object(forged) = json else { return XCTFail("expected merged object") }
        forged[ExactString("missing_jobs")] = .array([])
        XCTAssertThrowsError(try MergedAssuranceReport.fromAssuranceJSON(.object(forged)))
        forged[ExactString("missing_jobs")] = .array([.uint(1),.uint(2)])
        XCTAssertThrowsError(try MergedAssuranceReport.fromAssuranceJSON(.object(forged)))
        XCTAssertThrowsError(try mergeAssuranceReports(manifest:manifest(),metadata:Metadata(name:"lamp",build:"different"),expectedJobs:[1],reports:[(1,report)]))
        XCTAssertThrowsError(try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:[1,1],reports:[(1,report)]))
        XCTAssertThrowsError(try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:[1],reports:[(1,report),(1,report)]))
    }
    func testMergeRetainsIncompleteSearchOutcomePrecedence() throws {
        let metadata = Metadata(name:"lamp",build:"fixture")
        let observer = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:metadata) { _,_,_,_,_ in }
        func merge(_ terms: [String],missing: Bool = false) throws -> String {
            let reports = try terms.enumerated().map { (UInt64($0.offset),try observer.finish(termination:$0.element)) }
            let expected = Array(0..<UInt64(terms.count+(missing ? 1 : 0)))
            let merged = try mergeAssuranceReports(manifest:manifest(),metadata:metadata,expectedJobs:expected,reports:reports).report
            XCTAssertEqual(merged.termination,"MergedJobs")
            return merged.outcome.rawValue
        }
        // Rust merge_reports precedence.
        XCTAssertEqual(try merge(["graph_exhausted","graph_exhausted"]),"GraphExhausted")
        XCTAssertEqual(try merge(["graph_exhausted","cases_completed"]),"CasesCompleted")
        XCTAssertEqual(try merge(["graph_exhausted","depth_bound"]),"DepthLimited")
        XCTAssertEqual(try merge(["depth_bound","state_limit"]),"BudgetLimited")
        XCTAssertEqual(try merge(["state_limit","deadline"]),"TimedOut")
        XCTAssertEqual(try merge(["cancelled","deadline"]),"TimedOut")
        XCTAssertEqual(try merge(["cancelled","state_limit"]),"Interrupted")
        XCTAssertEqual(try merge(["deadline"],missing:true),"Interrupted")
        XCTAssertEqual(try merge(["failure_found"],missing:true),"Failed")
        XCTAssertEqual(try merge(["unknown-stop"]),"Interrupted")
        XCTAssertEqual(try merge([]),"NotStarted")
    }
    func testGuidedRunCapturesActualFeedbackIdentityAndExportsEvidence() throws {
        var source = model()
        source.codec = Codec(metadata:{ Metadata(name:"lamp",build:"fixture") },encodeState:{ [$0 ? 1 : 0] },decodeState:{ _ in false },encodeInput:{ [$0 ? 1 : 0] },decodeInput:{ _ in true },encodeOutput:{ [$0 ? 1 : 0] })
        var calls = 0
        let feedback = Feedback<Bool,Bool,Bool,Bool>(metadata:{ calls += 1; return FeedbackMetadata(name:"lamp-state",version:3,build:"fixture") },cloneFeature:{ $0 },transitionFeatures:{ _,_,turn,buffer in try buffer.push(turn.state) })
        var m = manifest(); m.requiredOutcome = .completed
        let generator = Generate(model:source,generate:{ _,_ in true },equalInputs:==)
        let run = try assuredGuidedFuzz64(generator,manifest:m,config:FuzzConfig64(seed:7,cases:1,maxSteps:1),feedback:feedback) { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        XCTAssertEqual(try run.search.get().termination,"cases_completed"); XCTAssertEqual(calls,1)
        XCTAssertEqual(run.assurance.budgets["guidance.feedback.name"],"lamp-state")
        XCTAssertEqual(run.assurance.budgets["guidance.feedback.version"],"3")
        let exported = run.assurance.assuranceJSON()
        XCTAssertEqual(try parseJSON(renderJSON(exported)),exported)
        guard case let .object(fields) = exported,case let .object(search)? = fields[ExactString("search")] else { return XCTFail("missing search evidence") }
        XCTAssertEqual(search[ExactString("outcome")],.string("CasesCompleted"))
        XCTAssertEqual(search[ExactString("checked_transitions")],.uint(1))
        XCTAssertTrue(run.assurance.assess().adequate)
    }
    func testImportValidatesDerivedEvidenceAndPreservesOriginal() throws {
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:manifest(),metadata:Metadata(name:"lamp")) { _,_,_,_,hits in hits.family("toggle"); hits.goal("lit") }
        let observer = collector.observer()
        try observer.initial(false,[.passed("safe")])
        try observer.transition(false,true,Transition(state:true,outputs:[]),[.passed("safe")],[.passed("edge")],[])
        let original = try collector.finish(termination:"graph_exhausted").assuranceJSON()
        let imported = try AssuranceReport.fromAssuranceJSON(original)
        XCTAssertEqual(imported.original,original); XCTAssertTrue(imported.report.assess().adequate)
        guard case var .object(changed) = original else { return XCTFail("expected object") }
        changed[ExactString("properties")] = .array([])
        XCTAssertThrowsError(try AssuranceReport.fromAssuranceJSON(.object(changed)))
        changed = { if case let .object(fields) = original { return fields }; return [:] }()
        changed[ExactString("observed_goals")] = .array([.string("undeclared")])
        XCTAssertThrowsError(try AssuranceReport.fromAssuranceJSON(.object(changed)))
        changed = { if case let .object(fields) = original { return fields }; return [:] }()
        changed[ExactString("unclassified_transitions")] = .uint(2)
        XCTAssertThrowsError(try AssuranceReport.fromAssuranceJSON(.object(changed)))
    }
    func testCampaignTeardownChargesCompleteReplacementAtBoundary() throws {
        let m = manifest(),metadata = Metadata(name:"lamp")
        let collector = try AssuranceObserver<Bool,Bool,Bool>(manifest:m,metadata:metadata) { _,_,_,_,_ in }
        let original = try collector.finish(termination:"deadline")
        let factory: @Sendable (UInt64,Generate<Bool,Bool,Bool>) throws -> (Bool,Bool,Transition<Bool,Bool>,[Bool],inout AssuranceHits) throws -> Void = { _,_ in { _,_,_,_,_ in } }
        func box(_ limit: UInt64) -> AssuranceCampaignReports<Bool,Bool,Bool> {
            AssuranceCampaignReports(manifest:m,metadata:metadata,observationLimits:AssuranceObservationLimits(),maximumBytes:limit,coverageFactory:factory)
        }
        let probe = box(UInt64.max); probe.put(0,original); let initialSize = probe.snapshot().1
        probe.fail(0,ModelError("failure")); let completeSize = probe.snapshot().1
        XCTAssertEqual(completeSize-initialSize,UInt64("ModelError".utf8.count-"Deadline".utf8.count+"job teardown: failure".utf8.count+16))
        let limited = box(completeSize-1); limited.put(0,original); limited.fail(0,ModelError("failure"))
        let rejected = limited.snapshot(); XCTAssertTrue(rejected.2); XCTAssertEqual(rejected.1,initialSize); XCTAssertEqual(rejected.0.first?.1.termination,"Deadline")
        let exact = box(completeSize); exact.put(0,original); exact.fail(0,ModelError("failure"))
        let retained = exact.snapshot(); XCTAssertFalse(retained.2); XCTAssertEqual(retained.1,completeSize); XCTAssertEqual(retained.0.first?.1.termination,"ModelError"); XCTAssertEqual(retained.0.first?.1.outcome,.failed)
    }
    func testCampaignRetainsJobEvidenceAndSinkFailure() throws {
        var m = manifest(); m.requiredOutcome = .completed
        let factory: @Sendable (UInt64) throws -> CampaignJob<Bool,Bool,Bool> = { _ in
            let model = Model<Bool,Bool,Bool>(initialState:{ false },step:{ state,_ in Transition(state:!state,outputs:[]) },checkState:{ _ in [.passed("safe")] },cloneState:{ $0 },cloneInput:{ $0 },cloneOutput:{ $0 },checkTransition:{ _,_,_ in [.passed("edge")] })
            let generated = Generate(model:model,generate:{ _,_ in true },equalInputs:==)
            return CampaignJob(generated,metadata:{ Metadata(name:"lamp",build:"fixture") })
        }
        let coverage: @Sendable (UInt64,Generate<Bool,Bool,Bool>) throws -> (Bool,Bool,Transition<Bool,Bool>,[Bool],inout AssuranceHits) throws -> Void = { _,_ in
            { _,_,turn,_,hits in hits.family("toggle"); if turn.state { hits.goal("lit") } }
        }
        let config = CampaignConfig64(jobs:2,workers:2,fuzz:FuzzConfig64(cases:1,maxSteps:1))
        let mustNotRun: @Sendable (UInt64) throws -> CampaignJob<Bool,Bool,Bool> = { _ in throw ModelError("factory ran") }
        let deadline = try runCampaign64(config:config,limits:CampaignLimits(maxDurationNanoseconds:DurationNanoseconds(low:0)),factory:mustNotRun) { _ in XCTFail("deadline delivered a job") }
        XCTAssertEqual(deadline.termination,.deadline); XCTAssertEqual(deadline.started,0)
        let cancelled = try runCampaign64(config:config,limits:CampaignLimits(maxDurationNanoseconds:DurationNanoseconds(low:0),isCancelled:{ true }),factory:mustNotRun) { _ in XCTFail("cancelled campaign delivered a job") }
        XCTAssertEqual(cancelled.termination,.cancelled); XCTAssertEqual(cancelled.started,0)
        var invalidLimits = AssuranceObservationLimits(); invalidLimits.maxProperties = 1
        XCTAssertThrowsError(try assuredCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:mustNotRun,coverageFactory:coverage,observationLimits:invalidLimits) { _ in }) {
            XCTAssertTrue(String(describing:$0).contains("manifest exceeds observation limits"))
        }
        XCTAssertThrowsError(try assuredCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:mustNotRun,coverageFactory:coverage,maxRetainedJobs:1) { _ in }) {
            XCTAssertTrue(String(describing:$0).contains("retained job limit"))
        }
        let success = try assuredCampaign64(config:config,limits:CampaignLimits(maxDuration:60),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage) { _ in }
        XCTAssertEqual(success.assurance.jobs.count,2); XCTAssertTrue(success.assurance.missingJobs.isEmpty)
        XCTAssertEqual(success.assurance.report.nativeBudgets["campaign.max_retained_jobs"],"100000")
        XCTAssertEqual(success.assurance.report.termination,"Campaign::JobsCompleted"); XCTAssertEqual(success.assurance.report.outcome,.casesCompleted)
        XCTAssertEqual(Set(success.assurance.report.budgets.keys),["requested_jobs","reported_jobs","campaign.master_seed","campaign.first_job","campaign.workers","campaign.result_buffer","campaign.stop_on_failure","max_duration_ns","cancellation_enabled","campaign.started","campaign.finished","campaign.delivered","campaign.accounting_complete"])
        XCTAssertEqual(success.assurance.jobs[0]?.termination,"CasesCompleted")
        // Jobs record the remaining campaign budget; a fractional remaining
        // TimeInterval has no exact max_duration_ns.
        XCTAssertEqual(Set(try XCTUnwrap(success.assurance.jobs[0]).budgets.keys).subtracting(["max_duration_ns"]),["seed","cases","max_steps","max_transitions","mutation_percent","cancellation_enabled"])
        XCTAssertNotNil(success.assurance.jobs[0]?.nativeBudgets["max_duration_seconds"])
        XCTAssertEqual(success.assurance.report.budgets["cancellation_enabled"],"false")
        XCTAssertEqual(success.assurance.report.budgets["max_duration_ns"],"60000000000")
        XCTAssertEqual(success.assurance.jobs[0]?.budgets["cancellation_enabled"],"false")
        XCTAssertEqual(success.assurance.report.checkedTransitions,2); XCTAssertTrue(success.assurance.report.assess().adequate)
        let integerDuration = DurationNanoseconds(high: 1, low: 0)
        let exactCampaign = try assuredCampaign64(config:config,limits:CampaignLimits(maxDurationNanoseconds:integerDuration),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage) { _ in }
        XCTAssertEqual(exactCampaign.assurance.report.budgets["max_duration_ns"], integerDuration.decimal)
        XCTAssertNotNil(exactCampaign.assurance.jobs[0]?.budgets["max_duration_ns"])
        XCTAssertEqual(exactCampaign.assurance.report.checkedTransitions,2)
        let feedback: @Sendable (UInt64,Generate<Bool,Bool,Bool>) throws -> Feedback<Bool,Bool,Bool,Bool> = { id,_ in
            Feedback(metadata:{ FeedbackMetadata(name:"feedback-\(id)",version:2,build:"fixture") },cloneFeature:{ $0 },transitionFeatures:{ _,_,turn,buffer in try buffer.push(turn.state) })
        }
        let guidedInteger = try assuredGuidedCampaign64(config:config,limits:CampaignLimits(maxDurationNanoseconds:integerDuration),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,feedbackFactory:feedback,coverageFactory:coverage) { _ in }
        XCTAssertEqual(guidedInteger.assurance.report.budgets["max_duration_ns"], integerDuration.decimal)
        XCTAssertNotNil(guidedInteger.assurance.jobs[0]?.budgets["max_duration_ns"])
        XCTAssertEqual(guidedInteger.assurance.report.checkedTransitions,2)
        XCTAssertThrowsError(try assuredGuidedCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:mustNotRun,feedbackFactory:feedback,coverageFactory:coverage,maxRetainedJobs:1) { _ in }) {
            XCTAssertTrue(String(describing:$0).contains("retained job limit"))
        }
        let one = try assuredCampaign64(config:CampaignConfig64(jobs:1,workers:1,fuzz:FuzzConfig64(cases:1,maxSteps:1)),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage) { _ in }
        let oneSize = try XCTUnwrap(UInt64(try XCTUnwrap(one.assurance.report.nativeBudgets["campaign.retained_evidence_bytes"])))
        let partialBudget = try assuredCampaign64(config:CampaignConfig64(jobs:2,workers:1,fuzz:FuzzConfig64(cases:1,maxSteps:1)),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage,maxRetainedEvidenceBytes:oneSize) { _ in }
        XCTAssertEqual(partialBudget.assurance.jobs.count,1); XCTAssertEqual(partialBudget.assurance.missingJobs.count,1)
        XCTAssertFalse(partialBudget.assurance.report.assess().adequate)
        XCTAssertTrue(partialBudget.assurance.report.callbackErrors.contains { $0.contains("retained evidence limit exceeded") })
        let none = try assuredGuidedCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,feedbackFactory:feedback,coverageFactory:coverage,maxRetainedEvidenceBytes:0) { _ in }
        XCTAssertTrue(none.assurance.jobs.isEmpty); XCTAssertEqual(none.assurance.missingJobs.count,2)
        XCTAssertFalse(none.assurance.report.assess().adequate)
        let guided = try assuredGuidedCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,feedbackFactory:feedback,coverageFactory:coverage) { _ in }
        XCTAssertTrue(guided.assurance.report.assess().adequate)
        XCTAssertEqual(guided.assurance.jobs[0]?.budgets["guidance.feedback.name"],"feedback-0")
        XCTAssertEqual(guided.assurance.jobs[1]?.budgets["guidance.feedback.name"],"feedback-1")
        let failingFeedback: @Sendable (UInt64,Generate<Bool,Bool,Bool>) throws -> Feedback<Bool,Bool,Bool,Bool> = { _,_ in
            Feedback(metadata:{ FeedbackMetadata(name:"fails",build:"fixture") },cloneFeature:{ $0 },transitionFeatures:{ _,_,_,_ in throw ModelError("feedback failed") })
        }
        let partial = try assuredGuidedCampaign64(config:config,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,feedbackFactory:failingFeedback,coverageFactory:coverage) { _ in }
        XCTAssertEqual(partial.assurance.jobs.count,2); XCTAssertEqual(partial.assurance.report.checkedTransitions,2)
        XCTAssertFalse(partial.assurance.report.assess().adequate)
        XCTAssertEqual(partial.assurance.jobs[0]?.budgets["guidance.feedback.name"],"fails")
        XCTAssertFalse(partial.assurance.report.callbackErrors.isEmpty)
        let failing = CampaignConfig64(jobs:2,workers:1,fuzz:FuzzConfig64(cases:1,maxSteps:1))
        let stopped = try assuredCampaign64(config:failing,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage) { _ in throw ModelError("sink failed") }
        XCTAssertEqual(stopped.assurance.jobs.count,1); XCTAssertEqual(stopped.assurance.missingJobs.count,1)
        XCTAssertFalse(stopped.assurance.report.callbackErrors.isEmpty); XCTAssertFalse(stopped.assurance.report.assess().adequate)
        let mismatch: @Sendable (UInt64) throws -> CampaignJob<Bool,Bool,Bool> = { id in
            let job = try factory(id); return CampaignJob(job.generated,metadata:{ Metadata(name:"lamp",build:"different") })
        }
        let wrongIdentity = try assuredCampaign64(config:failing,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:mismatch,coverageFactory:coverage) { _ in }
        XCTAssertEqual(wrongIdentity.assurance.missingJobs.count,2); XCTAssertFalse(wrongIdentity.assurance.report.assess().adequate)
        let badTeardown: @Sendable (UInt64) throws -> CampaignJob<Bool,Bool,Bool> = { id in
            let job = try factory(id); return CampaignJob(job.generated,metadata:job.metadata,tearDown:{ throw ModelError("teardown failed") })
        }
        let failed = try assuredCampaign64(config:failing,manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:badTeardown,coverageFactory:coverage) { _ in }
        XCTAssertEqual(failed.assurance.jobs.count,2); XCTAssertEqual(failed.assurance.report.outcome,.failed); XCTAssertTrue(failed.assurance.report.termination.hasPrefix("Campaign::"))
        XCTAssertFalse(failed.assurance.report.callbackErrors.isEmpty); XCTAssertFalse(failed.assurance.report.assess().adequate)
    }
    func testCampaignCoverageCannotMutateExistingObserverDelivery() throws {
        var m = manifest(); m.requiredOutcome = .completed
        let captured = AssuranceObserved()
        let factory: @Sendable (UInt64) throws -> CampaignJob<AssuranceMutableBox,AssuranceMutableBox,AssuranceMutableBox> = { _ in
            let model = Model<AssuranceMutableBox,AssuranceMutableBox,AssuranceMutableBox>(initialState:{ AssuranceMutableBox(0) },step:{ s,i in Transition(state:AssuranceMutableBox(s.value+i.value),outputs:[AssuranceMutableBox(7)]) },checkState:{ _ in [.passed("safe")] },cloneState:{ AssuranceMutableBox($0.value) },cloneInput:{ AssuranceMutableBox($0.value) },cloneOutput:{ AssuranceMutableBox($0.value) },checkTransition:{ _,_,_ in [.passed("edge")] })
            let generated = Generate(model:model,generate:{ _,_ in AssuranceMutableBox(1) },equalInputs:{ $0.value == $1.value })
            let existing = SearchObserver<AssuranceMutableBox,AssuranceMutableBox,AssuranceMutableBox>(transition:{ before,input,turn,_,_,history in captured.record([before.value,input.value,turn.state.value,turn.outputs[0].value,history[0].value]) })
            return CampaignJob(generated,metadata:{ Metadata(name:"lamp",build:"fixture") },observer:existing)
        }
        let coverage: @Sendable (UInt64,Generate<AssuranceMutableBox,AssuranceMutableBox,AssuranceMutableBox>) throws -> (AssuranceMutableBox,AssuranceMutableBox,Transition<AssuranceMutableBox,AssuranceMutableBox>,[AssuranceMutableBox],inout AssuranceHits) throws -> Void = { _,_ in
            { before,input,turn,history,hits in
                before.value = 90; input.value = 91; turn.state.value = 92; turn.outputs[0].value = 93; history[0].value = 94
                hits.family("toggle"); hits.goal("lit")
            }
        }
        let run = try assuredCampaign64(config:CampaignConfig64(jobs:1,fuzz:FuzzConfig64(cases:1,maxSteps:1)),manifest:m,metadata:Metadata(name:"lamp",build:"fixture"),factory:factory,coverageFactory:coverage) { _ in }
        XCTAssertEqual(captured.read(),[0,1,1,7,1]); XCTAssertTrue(run.assurance.report.assess().adequate)
    }
}
