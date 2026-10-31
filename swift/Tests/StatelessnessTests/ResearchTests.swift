import Foundation
import XCTest
import Statelessness

private final class ResearchCounts {
    var initial = 0, steps = 0, metadata = 0, projections = 0
    var events: [String] = []
}
private func researchProposal(established: Bool = true, required: [ResearchRequiredProperty]? = nil) -> Research.Proposal {
    Research.Proposal(name:"finite-toggle",version:1,build:"proposal-v1",assumptions:Research.Assumptions(
        established:established,finiteDomain:"explicit closed fixture",equivalence:"exact label preserving behavior",
        effects:"ordered outputs",transitionProperties:"ordered named checks",queueOrder:"no hidden queue",
        oracleHistory:"history included in state",visibleTiming:"no hidden wall clock"),requiredProperties:required ?? [
            ResearchRequiredProperty(id:"state",phase:.state),ResearchRequiredProperty(id:"transition",phase:.transition)])
}
private func researchModel(_ counts: ResearchCounts, reversed: Bool = false, skipped: Bool = false,
                           failStep: Int? = nil, failState: Int? = nil, failTransition: Bool = false) -> Model<Int,String,String> {
    Model(initialState:{ counts.initial += 1; counts.events.append("initial"); return 0 },step:{ state,_ in
        counts.steps += 1; counts.events.append("step:\(state)")
        if state == failStep { throw ModelError("fixture step error") }
        return Transition(state:1-state,outputs:reversed && state == 1 ? ["b","a"]:["a","b"])
    },checkState:{ state in
        counts.events.append("state:\(state)")
        if state == failState { throw ModelError("fixture state error") }
        return [skipped ? Check.skipped("state","not evaluated") : Check.passed("state")]
    },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},checkTransition:{ state,_,_ in
        counts.events.append("transition:\(state)")
        if failTransition { throw ModelError("fixture transition error") }
        return [Check.passed("transition")]
    },inputs:{ state in counts.events.append("inputs:\(state)"); return InputIterator(values:["flip"]) })
}
private func researchMetadata(_ c: ResearchCounts) -> () throws -> Metadata {
    { c.metadata += 1; c.events.append("metadata"); return Metadata(name:"research",build:"model-v1") }
}
final class ResearchTests: XCTestCase {
    func testSharedFiniteResearchCorpusThroughPublicAPI() throws {
        let corpus = try ResearchFixture.corpus(), defaults = corpus["defaults"] as! ResearchFixture.Object
        let cases = corpus["cases"] as! [ResearchFixture.Object]
        for row in cases {
            var actual = ResearchFixture.runCase(defaults,row)
            actual.removeValue(forKey:"case")
            XCTAssertEqual(ResearchFixture.json(actual),ResearchFixture.json(row["expected"]!),row["case"] as! String)
        }
        print("research shared executable rows: \(cases.count); native-only rows separately classified: \((corpus["native_admission_cases"] as! [Any]).count)")
    }
    func testDisabledAndAssumptionsModesValidateMetadataWithoutExecutingModel() throws {
        let c = ResearchCounts(), m = researchModel(c)
        let disabled = try Research.compareEquivalence(m,states:[],proposal:researchProposal(),metadata:researchMetadata(c),projection:{ value in c.projections += 1; return value })
        XCTAssertEqual(disabled.status,.disabled); XCTAssertEqual(c.events,["metadata"])
        let assumed = try Research.compareEquivalence(m,states:[0,1],proposal:researchProposal(established:false),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ value in c.projections += 1; return value })
        XCTAssertEqual(assumed.status,.assumptionsNotEstablished)
        XCTAssertEqual(c.initial,0); XCTAssertEqual(c.steps,0); XCTAssertEqual(c.projections,0); XCTAssertEqual(c.metadata,2)
        let invalid = Research.Proposal(name:"",version:0,build:"",assumptions:researchProposal().assumptions,requiredProperties:[])
        XCTAssertThrowsError(try Research.compareEquivalence(m,states:[0],proposal:invalid,metadata:researchMetadata(c),projection:{$0}))
        XCTAssertEqual(c.metadata,2)
        XCTAssertThrowsError(try Research.compareEquivalence(m,states:[0],proposal:researchProposal(),projection:{$0}))
    }
    func testEquivalentToggleMaterializesOnceAndRecordsEveryQualificationParameter() throws {
        let c = ResearchCounts()
        let r = try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ _ in c.projections += 1; return 0 })
        XCTAssertEqual(r.status,.agreesWithinDeclaredFixture); XCTAssertEqual(c.steps,2); XCTAssertEqual(c.projections,2)
        XCTAssertEqual(r.materializedEdges,2); XCTAssertEqual(r.comparedPairs,1)
        XCTAssertEqual(r.exact?.states,[0,1]); XCTAssertEqual(r.reduced?.states,[0])
        XCTAssertEqual(r.exact?.edges,2); XCTAssertEqual(r.reduced?.edges,1)
        XCTAssertEqual(c.events,["metadata","initial","state:0","inputs:0","step:0","transition:0","state:1","inputs:1","step:1","transition:1"])
        let p = Research.qualificationParameters(r)
        XCTAssertEqual(p.count,17); XCTAssertEqual(p["research.status"],"AgreesWithinDeclaredFixture")
        XCTAssertEqual(p["research.production_pruning_enabled"],"false"); XCTAssertEqual(p["research.model_build"],"model-v1")
    }
    func testLimitsDomainAndCallbackErrorPrecedence() throws {
        for config in [Research.ExperimentConfig(enabled:true,maxStates:1),.init(enabled:true,maxEdges:1),.init(enabled:true,maxEquivalencePairs:0)] {
            let c = ResearchCounts()
            let r = try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(),config:config,metadata:researchMetadata(c),projection:{ _ in 0 })
            XCTAssertEqual(r.status,.incomplete)
        }
        let c = ResearchCounts()
        XCTAssertThrowsError(try Research.compareEquivalence(researchModel(c),states:[0,0],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{$0}))
        XCTAssertEqual(c.initial,0)
        let unclosed = try Research.compareEquivalence(researchModel(c),states:[0],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{$0})
        XCTAssertEqual(unclosed.materializedEdges,1); XCTAssertTrue(unclosed.limitation.contains("successor is outside")); XCTAssertNil(unclosed.exact)
        let step = try Research.compareEquivalence(researchModel(ResearchCounts(),failStep:1),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:{Metadata(name:"fixture")},projection:{$0})
        XCTAssertEqual(step.materializedEdges,1); XCTAssertEqual(step.limitation,"fixture step error"); XCTAssertNil(step.exact)
        let transition = try Research.compareEquivalence(researchModel(ResearchCounts(),failTransition:true),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:{Metadata(name:"fixture")},projection:{$0})
        XCTAssertEqual(transition.materializedEdges,1); XCTAssertEqual(transition.limitation,"fixture transition error")
        let state = try Research.compareEquivalence(researchModel(ResearchCounts(),failState:0),states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxEdges:0),metadata:{Metadata(name:"fixture")},projection:{$0})
        XCTAssertEqual(state.materializedEdges,0); XCTAssertEqual(state.limitation,"fixture state error")
    }
    func testEffectsMissingAndSkippedObservationsNeverQualify() throws {
        let c = ResearchCounts()
        let reversed = try Research.compareEquivalence(researchModel(c,reversed:true),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ _ in 0 })
        XCTAssertEqual(reversed.status,.counterexample); XCTAssertEqual(reversed.counterexamples.first?.reason,"ordered effects differ")
        let skipped = try Research.compareEquivalence(researchModel(c,skipped:true),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ _ in 0 })
        XCTAssertEqual(skipped.status,.incomplete); XCTAssertEqual(skipped.exact?.skippedChecks,3)
        let missing = try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(required:[.init(id:"forgotten",phase:.state)]),config:.init(enabled:true),metadata:researchMetadata(c),projection:{$0})
        XCTAssertEqual(missing.status,.incomplete); XCTAssertEqual(missing.missingProperties.map(\.id),["forgotten"])
    }
    func testUnreachableDeclaredEdgesAreMaterializedBeforeProjection() throws {
        let c = ResearchCounts()
        let m = Model<Int,String,String>(initialState:{0},step:{ s,_ in c.events.append("step:\(s)"); return Transition(state:s) },checkState:{_ in [Check.passed("state")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{_ in InputIterator(values:["stay"])})
        let r = try Research.compareEquivalence(m,states:[0,1,2],proposal:researchProposal(required:[.init(id:"state",phase:.state)]),config:.init(enabled:true),metadata:{Metadata(name:"unreachable")},projection:{ s in c.events.append("key:\(s)"); return s })
        XCTAssertEqual(r.status,.agreesWithinDeclaredFixture); XCTAssertEqual(r.materializedEdges,3); XCTAssertEqual(r.exact?.states,[0])
        XCTAssertEqual(c.events,["step:0","step:1","step:2","key:0","key:1","key:2"])
    }
    func testPopulationProjectionLosesShortestTransitionFailureAndWinsPairCutoff() throws {
        let m = Model<Int,String,String>(initialState:{0},step:{ s,i in Transition(state:i == "left" ? s|1 : i == "right" ? s|2 : s) },checkState:{_ in [Check.passed("state")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},checkTransition:{ s,i,_ in [s == 2 && i == "observe" ? Check.failed("transition","right before dependency") : Check.passed("transition")] },inputs:{_ in InputIterator(values:["left","right","observe"])})
        let r = try Research.compareEquivalence(m,states:[0,1,2,3],proposal:researchProposal(),config:.init(enabled:true,maxEquivalencePairs:0),metadata:{Metadata(name:"population")},projection:{$0.nonzeroBitCount})
        XCTAssertEqual(r.status,.counterexample); XCTAssertEqual(r.comparedPairs,0)
        XCTAssertEqual(r.missingViolations.first?.inputs,["right","observe"]); XCTAssertEqual(r.missingViolations.first?.property.phase,.transition)
        let identity = try Research.compareEquivalence(m,states:[0,1,2,3],proposal:researchProposal(),config:.init(enabled:true),metadata:{Metadata(name:"population")},projection:{$0})
        XCTAssertEqual(identity.status,.agreesWithinDeclaredFixture); XCTAssertEqual(identity.exact?.violations.first?.inputs,["right","observe"])
    }
    func testPreferredAppendChecksAndHostOnlyCallbackFailures() throws {
        let c = ResearchCounts()
        var m = researchModel(c)
        var stateCalls = 0, transitionCalls = 0
        m.checkState = { _ in throw ModelError("legacy state must not execute") }
        m.checkTransition = { _,_,_ in throw ModelError("legacy transition must not execute") }
        m.checkStateInto = { _,sink in stateCalls += 1; try sink.push(.passed("state")) }
        m.checkTransitionInto = { _,_,_,sink in transitionCalls += 1; try sink.push(.passed("transition")) }
        let append = try Research.compareEquivalence(m,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{_ in 0})
        XCTAssertEqual(append.status,.agreesWithinDeclaredFixture)
        XCTAssertEqual(stateCalls,2); XCTAssertEqual(transitionCalls,2)
        let projection = try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ (_:Int) throws -> Int in throw ModelError("native projection error") })
        XCTAssertEqual(projection.status,.incomplete); XCTAssertEqual(projection.materializedEdges,2)
        XCTAssertEqual(projection.limitation,"native projection error"); XCTAssertNil(projection.exact)
        let equality = try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),
            equalStates:{$0 == $1},equalInputs:{$0 == $1},equalOutputs:{$0 == $1},equalKeys:{ (_:Int,_:Int) throws -> Bool in throw ModelError("native equality error") },cloneKey:{$0},projection:{$0})
        XCTAssertEqual(equality.status,.incomplete); XCTAssertEqual(equality.materializedEdges,2)
        XCTAssertEqual(equality.limitation,"native equality error"); XCTAssertNil(equality.exact)
        var nextModel = researchModel(c)
        nextModel.inputs = { _ in InputIterator { throw ModelError("native next error") } }
        let next = try Research.compareEquivalence(nextModel,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{$0})
        XCTAssertEqual(next.status,.incomplete); XCTAssertEqual(next.materializedEdges,0)
        XCTAssertEqual(next.limitation,"native next error"); XCTAssertNil(next.exact)
        let beforeInitial = c.initial
        XCTAssertThrowsError(try Research.compareEquivalence(researchModel(c),states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:{throw ModelError("native metadata error")},projection:{$0})) { XCTAssertEqual(String(describing:$0),"native metadata error") }
        XCTAssertEqual(c.initial,beforeInitial)
        let corpus = try ResearchFixture.corpus(), native = corpus["native_admission_cases"] as! [ResearchFixture.Object]
        let runtime = Set(["projection-nonreflexive-key","projection-callback-throws","equality-callback-throws","iterator-next-throws","metadata-supplier-throws"])
        let staticOnly = Set(["negative-config-budget","config-over-u64","unsafe-number-config","proposal-version-over-u32","asynchronous-custom-callback"])
        XCTAssertEqual(Set(native.map { $0["case"] as! String }),runtime.union(staticOnly))
        print("research native-only: 5 public host-failure controls (including NaN test), 5 typed exclusions/language-inapplicable arguments; no Rust Eq/iterator panic claim")
    }
    func testInvalidExactStateEqualityCannotQualify() throws {
        let counts = ResearchCounts(),model = researchModel(counts)
        func run(_ equality: @escaping (Int,Int) -> Bool) throws -> Research.ExperimentReport<String> {
            try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(counts),equalStates:equality,equalInputs:{$0 == $1},equalOutputs:{$0 == $1},equalKeys:{$0 == $1},cloneKey:{$0},projection:{$0})
        }
        let nonreflexive = try run { _,_ in false }
        XCTAssertEqual(nonreflexive.status,.incomplete); XCTAssertEqual(nonreflexive.limitation,"research state equality must be reflexive")
        let asymmetric = try run { $0 <= $1 }
        XCTAssertEqual(asymmetric.status,.incomplete); XCTAssertEqual(asymmetric.limitation,"research state equality must be symmetric")
    }
    func testExactStateComparisonBudgetIsIndependent() throws {
        let counts = ResearchCounts(),model = researchModel(counts)
        let baseline = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(counts),projection:{$0})
        let exact = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxStateComparisons:baseline.stateComparisons),metadata:researchMetadata(counts),projection:{$0})
        XCTAssertEqual(exact.status,baseline.status); XCTAssertEqual(exact.stateComparisons,baseline.stateComparisons)
        let short = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxStateComparisons:baseline.stateComparisons-1),metadata:researchMetadata(counts),projection:{$0})
        XCTAssertEqual(short.status,.incomplete); XCTAssertEqual(short.limitation,"research state comparison budget reached")
        XCTAssertEqual(short.stateComparisons,baseline.stateComparisons-1)
        XCTAssertEqual(Research.qualificationParameters(short)["research.state_comparisons"],String(short.stateComparisons))
    }
    func testProjectionEqualityCallsRespectIndependentBudget() throws {
        let counts = ResearchCounts(),model = researchModel(counts)
        let result = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxProjectionComparisons:3),metadata:researchMetadata(counts),projection:{$0})
        XCTAssertEqual(result.status,.incomplete)
        XCTAssertEqual(result.limitation,"research projection comparison budget reached")
        XCTAssertEqual(result.projectionComparisons,3)
        XCTAssertEqual(Research.qualificationParameters(result)["research.max_projection_comparisons"],"3")
        XCTAssertEqual(Research.qualificationParameters(result)["research.projection_comparisons"],"3")
        let baseline = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(counts),projection:{$0})
        let exact = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxProjectionComparisons:baseline.projectionComparisons),metadata:researchMetadata(counts),projection:{$0})
        XCTAssertEqual(exact.status,baseline.status); XCTAssertEqual(exact.projectionComparisons,baseline.projectionComparisons)
        let short = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true,maxProjectionComparisons:baseline.projectionComparisons-1),metadata:researchMetadata(counts),projection:{$0})
        XCTAssertEqual(short.status,.incomplete); XCTAssertEqual(short.projectionComparisons,baseline.projectionComparisons-1)
    }
    func testNontransitiveProjectionCannotQualify() throws {
        let model = Model<Int,Int,Int>(initialState:{ 0 },step:{ state,_ in Transition(state:(state+1)%3,outputs:[]) },checkState:{ _ in [.passed("state")] },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{ _ in InputIterator(values:[0]) })
        let result = try Research.compareEquivalence(model,states:[0,1,2],proposal:researchProposal(required:[.init(id:"state",phase:.state)]),config:.init(enabled:true),metadata:{Metadata(name:"nontransitive")},
            equalStates:{$0 == $1},equalInputs:{$0 == $1},equalOutputs:{$0 == $1},
            equalKeys:{ a,b in a == b || a == 0 || b == 0 },cloneKey:{$0},projection:{$0})
        XCTAssertEqual(result.status,.incomplete)
        XCTAssertEqual(result.limitation,"research projection equality must be transitive")
        XCTAssertNil(result.exact); XCTAssertNil(result.reduced)
    }
    func testAsymmetricProjectionComparatorCannotQualify() throws {
        let counts = ResearchCounts(),model = researchModel(counts)
        let result = try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(counts),
            equalStates:{$0 == $1},equalInputs:{$0 == $1},equalOutputs:{$0 == $1},
            equalKeys:{$0 <= $1},cloneKey:{$0},projection:{$0})
        XCTAssertEqual(result.status,.incomplete)
        XCTAssertEqual(result.limitation,"research projection equality must be symmetric")
        XCTAssertNil(result.exact); XCTAssertNil(result.reduced)
    }
    func testExactPropertyByteIdentityAndPhaseOrdering() throws {
        let a = ResearchRequiredProperty(id:"\u{e9}",phase:.state), b = ResearchRequiredProperty(id:"e\u{301}",phase:.state)
        XCTAssertNotEqual(a,b); XCTAssertEqual(Set([a,b]).count,2)
        XCTAssertEqual([ResearchRequiredProperty(id:"a",phase:.transition),.init(id:"a",phase:.state),.init(id:"z",phase:.state)].sorted().map(\.phase),[.state,.transition,.state])
    }
    func testNonReflexiveHostKeysRejectAndChangingComparatorCannotLoop() throws {
        let c = ResearchCounts(), m = researchModel(c)
        let nan = try Research.compareEquivalence(m,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),projection:{ _ in Double.nan })
        XCTAssertEqual(nan.status,.incomplete); XCTAssertNil(nan.exact)
        XCTAssertEqual(nan.limitation,"research projection equality must be reflexive")
        XCTAssertEqual(nan.materializedEdges,2)
        var comparisons = 0
        let changing = try Research.compareEquivalence(m,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:researchMetadata(c),
            equalStates:{$0 == $1},equalInputs:{$0 == $1},equalOutputs:{$0 == $1},
            equalKeys:{ _,_ in comparisons += 1; return comparisons <= 2 },cloneKey:{$0},projection:{$0})
        XCTAssertNotEqual(changing.status,.agreesWithinDeclaredFixture)
        XCTAssertLessThan(comparisons,32); XCTAssertEqual(changing.materializedEdges,2)
        XCTAssertLessThanOrEqual(changing.reduced?.states.count ?? 0,2)
    }

    func testDirectStringsAndFrameworkObservationsKeepExactUTF8Identity() throws {
        let nfc = "\u{e9}", nfd = "e\u{301}"
        func run(_ mode: String) throws -> Research.ExperimentReport<String> {
            let model = Model<Int,String,String>(initialState:{0},step:{ state,_ in
                Transition(state:1-state,outputs:mode == "output" ? [state == 0 ? nfc:nfd] : [],
                    disposition:mode == "disposition" ? .ignored(state == 0 ? nfc:nfd) : .accepted)
            },checkState:{ state in
                if mode == "id" { return [Check.passed(state == 0 ? nfc:nfd)] }
                if mode == "reason" { return [Check.failed("state",state == 0 ? nfc:nfd)] }
                if mode == "dedup" { return [Check.failed(nfc,"one"),Check.failed(nfd,"two")] }
                return [Check.passed("state")]
            },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{ state in InputIterator(values:[mode == "input" ? (state == 0 ? nfc:nfd):"flip"]) })
            return try Research.compareEquivalence(model,states:[0,1],proposal:researchProposal(required:[.init(id:"state",phase:.state)]),config:.init(enabled:true),metadata:{Metadata(name:"unicode")},projection:{ state in mode == "key" ? (state == 0 ? nfc:nfd):"same" })
        }
        XCTAssertEqual(try run("input").counterexamples.first?.reason,"enabled input labels or order differ")
        XCTAssertEqual(try run("output").counterexamples.first?.reason,"ordered effects differ")
        XCTAssertEqual(try run("id").counterexamples.first?.reason,"state checks differ")
        XCTAssertEqual(try run("reason").counterexamples.first?.reason,"state checks differ")
        XCTAssertEqual(try run("disposition").counterexamples.first?.reason,"transition disposition differs")
        let keys = try run("key")
        XCTAssertEqual(keys.comparedPairs,0); XCTAssertEqual(keys.reduced?.states,[0,1])
        let dedup = try run("dedup")
        XCTAssertEqual(dedup.exact?.observedProperties.count,2)
        XCTAssertEqual(dedup.exact?.violations.count,2)
        XCTAssertEqual(dedup.exact?.observedProperties.map { Array($0.id.utf8) },[Array(nfd.utf8),Array(nfc.utf8)])
    }

    func testExplicitCompoundComparatorsPreserveByteDistinctPayloads() throws {
        struct Label: Equatable { let text: String }
        let m = Model<Int,Label,Label>(initialState:{0},step:{ state,_ in Transition(state:1-state) },checkState:{_ in [Check.passed("state")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{ state in InputIterator(values:[Label(text:state == 0 ? "\u{e9}":"e\u{301}")]) })
        let r = try Research.compareEquivalence(m,states:[0,1],proposal:researchProposal(),config:.init(enabled:true),metadata:{Metadata(name:"compound")},
            equalStates:{$0 == $1},equalInputs:{exactText($0.text,$1.text)},equalOutputs:{exactText($0.text,$1.text)},equalKeys:{$0 == $1},cloneKey:{$0},projection:{_ in 0})
        XCTAssertEqual(r.status,.counterexample); XCTAssertEqual(r.counterexamples.first?.reason,"enabled input labels or order differ")
    }
    func testReferenceSnapshotsDetachGraphAndWitnessCopies() throws {
        final class Box { var text: String; init(_ text: String) { self.text = text } }
        let sharedInput = Box("go"), sharedOutput = Box("initial")
        let m = Model<Int,Box,Box>(initialState:{0},step:{ state,input in
            input.text = "reducer changed detached input"
            sharedOutput.text = state == 0 ? "one":"two"
            return Transition(state:min(state+1,2),outputs:[sharedOutput])
        },checkState:{_ in [Check.passed("state")]},cloneState:{$0},cloneInput:{Box($0.text)},cloneOutput:{Box($0.text)},checkTransition:{ state,input,turn in
            input.text = "checker changed detached input"; turn.outputs[0].text = "checker changed detached output"
            return [state == 1 ? Check.failed("transition","failure"):Check.passed("transition")]
        },inputs:{_ in InputIterator(values:[sharedInput]) })
        let r = try Research.compareEquivalence(m,states:[0,1,2],proposal:researchProposal(),config:.init(enabled:true),metadata:{Metadata(name:"references")},
            equalStates:{$0 == $1},equalInputs:{exactText($0.text,$1.text)},equalOutputs:{exactText($0.text,$1.text)},equalKeys:{exactText($0.text,$1.text)},cloneKey:{Box($0.text)},projection:{_ in Box("same")})
        XCTAssertEqual(r.status,.counterexample); XCTAssertEqual(r.materializedEdges,3)
        XCTAssertEqual(r.counterexamples.first?.reason,"ordered effects differ")
        XCTAssertEqual(r.missingViolations.first?.inputs.map(\.text),["go","go"])
        r.exact!.violations[0].inputs[0].text = "caller changed one report witness"
        XCTAssertEqual(r.missingViolations.first?.inputs.map(\.text),["go","go"])
        XCTAssertEqual(r.exact!.violations[0].inputs[1].text,"go")
        XCTAssertEqual(sharedInput.text,"go")
    }

}

private enum ResearchFixture {
typealias Object = [String:Any]
static func ownedJSON(_ data:Data)throws->Any {
 func convert(_ value:JSON)->Any {switch value {
 case .null:return NSNull()
 case let .bool(value):return value
 case let .number(value):return Int(value)!
 case let .string(value):return value
 case let .array(values):return values.map(convert)
 case let .object(values):return Dictionary(uniqueKeysWithValues:values.map{($0.key.value,convert($0.value))})
 }}
 return try convert(parseJSON(Array(data)))
}
static func text(_ value:Any?) -> String { value as? String ?? "" }
static func u64(_ value:Any?) -> UInt64 { if let value=value as? String {return UInt64(value)!};return (value as! NSNumber).uint64Value }
static func merge(_ base:Object,_ overrides:Object)->Object {var result=base;for(k,v)in overrides {if let child=v as? Object {result[k]=merge(result[k] as! Object,child)}else{result[k]=v}};return result}
static func checks(_ values:Any)->[Check] {(values as! [Object]).map{Check(text($0["id"]),CheckStatus(rawValue:text($0["status"]))!,text($0["details"]))}}
static func property(_ value:ResearchRequiredProperty)->Object {["id":value.id,"phase":value.phase == .state ? "state":"transition"]}
static func violations(_ values:[Research.ViolationWitness<String>])->[Object] {values.map{["property":property($0.property),"details":$0.details,"inputs":$0.inputs]}}
static func reachability(_ value:Research.Reachability<String>?)->Any {guard let value else{return NSNull()};return ["states":value.states,"edges":String(value.edges),"violations":violations(value.violations),"observed_properties":value.observedProperties.map(property),"skipped_checks":String(value.skippedChecks)] as Object}
static func normalized(_ value:Research.ExperimentReport<String>,_ inputProposal:Object,_ inputConfig:Object,_ inputMetadata:Object)->Object {
 let p=value.proposal,a=p.assumptions,m=value.model,c=value.config
 let proposal:Object=["name":p.name,"version":p.version,"build":p.build,"required_properties":p.requiredProperties.map(property),"assumptions":["established":a.established,"finite_domain":a.finiteDomain,"equivalence":a.equivalence,"effects":a.effects,"transition_properties":a.transitionProperties,"queue_order":a.queueOrder,"oracle_history":a.oracleHistory,"visible_timing":a.visibleTiming] as Object]
 let config:Object=["enabled":c.enabled,"max_states":String(c.maxStates),"max_edges":String(c.maxEdges),"max_equivalence_pairs":String(c.maxEquivalencePairs)]
 let metadata:Object=["name":m.name,"model_version":m.modelVersion,"properties_version":m.propertiesVersion,"codec_version":m.codecVersion,"build":m.build]
 return ["model":metadataJSON(value.model),"proposal":proposalJSON(value.proposal),"config":configJSON(value.config),"status":value.status.rawValue,"declared_states":String(value.declaredStates),"materialized_edges":String(value.materializedEdges),"compared_pairs":String(value.comparedPairs),"exact":reachability(value.exact),"reduced":reachability(value.reduced),"counterexamples":value.counterexamples.map{["left_state":$0.leftState,"right_state":$0.rightState,"left_prefix":$0.leftPrefix,"right_prefix":$0.rightPrefix,"reason":$0.reason] as Object},"missing_violations":violations(value.missingViolations),"missing_properties":value.missingProperties.map(property),"limitation":value.limitation]
}
static func runCase(_ defaults:Object,_ row:Object)->Object {
 var log:[String]=[]
 func event(_ name:String)throws {log.append(name);if exactText(name,text(row["error_at"])){throw ModelError("fixture error at "+name)}}
 do {
  let scenario=row["scenario"] as! Object, metadata=defaults["metadata"] as! Object
  let p=merge(defaults["proposal"] as! Object,row["proposal_overrides"] as? Object ?? [:]), c=merge(defaults["config"] as! Object,row["config_overrides"] as? Object ?? [:])
  let a=p["assumptions"] as! Object
  let assumptions=Research.Assumptions(established:a["established"] as! Bool,finiteDomain:text(a["finite_domain"]),equivalence:text(a["equivalence"]),effects:text(a["effects"]),transitionProperties:text(a["transition_properties"]),queueOrder:text(a["queue_order"]),oracleHistory:text(a["oracle_history"]),visibleTiming:text(a["visible_timing"]))
  let required=(p["required_properties"] as! [Object]).map{ResearchRequiredProperty(id:text($0["id"]),phase:text($0["phase"]) == "state" ? .state:.transition)}
  let proposal=Research.Proposal(name:text(p["name"]),version:UInt32(u64(p["version"])),build:text(p["build"]),assumptions:assumptions,requiredProperties:required)
  let config=Research.ExperimentConfig(enabled:c["enabled"] as! Bool,maxStates:u64(c["max_states"]),maxEdges:u64(c["max_edges"]),maxEquivalencePairs:u64(c["max_equivalence_pairs"]))
  let rows=Dictionary(uniqueKeysWithValues:(scenario["rows"] as! [Object]).map{($0["state"] as! Int,$0)})
  func edge(_ state:Int,_ input:String)->Object {(rows[state]!["inputs"] as! [Object]).first{exactText(text($0["input"]),input)}!}
  let model=Model<Int,String,String>(initialState:{try event("initial");return scenario["initial"] as! Int},step:{state,input in
   try event("step:\(state):\(input)");let e=edge(state,input),d=e["disposition"] as! Object;let kind=text(d["kind"]);let disposition:Disposition=kind == "accepted" ? .accepted : kind == "rejected" ? .rejected(text(d["reason"])):.ignored(text(d["reason"]))
   return Transition(state:e["target"] as! Int,outputs:e["outputs"] as! [String],disposition:disposition)
  },checkState:{state in try event("state:\(state)");return checks(rows[state]!["checks"]!)},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},checkTransition:{state,input,_ in try event("transition:\(state):\(input)");return checks(edge(state,input)["checks"]!)},inputs:{state in
   try event("inputs:\(state)");let edges=rows[state]!["inputs"] as! [Object];var index=0
   return InputIterator {try event("next:\(state):\(index)");guard index<edges.count else{return nil};defer{index+=1};return text(edges[index]["input"])}
  },equalStates:{$0==$1})
  let report=try Research.compareEquivalence(model,states:scenario["states"] as! [Int],proposal:proposal,config:config,metadata:{try event("metadata");return Metadata(name:text(metadata["name"]),modelVersion:UInt32(u64(metadata["model_version"])),propertiesVersion:UInt32(u64(metadata["properties_version"])),codecVersion:UInt32(u64(metadata["codec_version"])),build:text(metadata["build"]))},projection:{state in try event("projection:\(state)");return (scenario["keys"] as! [String])[(scenario["states"] as! [Int]).firstIndex(of:state)!]})
  return ["case":row["case"]!,"error":NSNull(),"report":normalized(report,p,c,metadata),"callback_log":log,"qualification_parameters":Research.qualificationParameters(report)]
 }catch{return ["case":row["case"]!,"error":String(describing:error),"report":NSNull(),"callback_log":log,"qualification_parameters":NSNull()]}
}

static func metadataJSON(_ m:Metadata)->Object {["name":m.name,"model_version":m.modelVersion,"properties_version":m.propertiesVersion,"codec_version":m.codecVersion,"build":m.build]}
static func proposalJSON(_ p:Research.Proposal)->Object {let a=p.assumptions;return ["name":p.name,"version":p.version,"build":p.build,"assumptions":["established":a.established,"finite_domain":a.finiteDomain,"equivalence":a.equivalence,"effects":a.effects,"transition_properties":a.transitionProperties,"queue_order":a.queueOrder,"oracle_history":a.oracleHistory,"visible_timing":a.visibleTiming],"required_properties":p.requiredProperties.map(property)]}
static func configJSON(_ c:Research.ExperimentConfig)->Object {["enabled":c.enabled,"max_states":String(c.maxStates),"max_edges":String(c.maxEdges),"max_equivalence_pairs":String(c.maxEquivalencePairs)]}
static func json(_ v:Any)->JSON {
 if v is NSNull{return .null}
 if let d=v as? Object{return .obj(d.mapValues(json))}
 if let a=v as? [Any]{return .array(a.map(json))}
 if let s=v as? String{return .string(s)}
 if type(of:v)==Bool.self{return .bool(v as! Bool)}
 if let n=v as? UInt64{return .number(String(n))}
 if let n=v as? UInt32{return .number(String(n))}
 if let n=v as? Int{return .number(String(n))}
 preconditionFailure("unsupported fixture normalization value")
}
static func owned(_ j:JSON)->Any {
 switch j {case .null:return NSNull();case let .bool(v):return v;case let .number(v):return Int(v)!;case let .string(v):return v;case let .array(a):return a.map(owned);case let .object(d):return Dictionary(uniqueKeysWithValues:d.map{($0.key.value,owned($0.value))})}
}
static func corpus()throws->Object {
 let fallback=URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/research-equivalence-v1.json").path
 let path=ProcessInfo.processInfo.environment["STATELESS_RESEARCH_CORPUS"] ?? fallback
 return try owned(parseJSON(Array(Data(contentsOf:URL(fileURLWithPath:path))))) as! Object
}

}
