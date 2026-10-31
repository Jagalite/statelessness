import XCTest
import Foundation
private final class Count86: @unchecked Sendable { let lock = NSLock(); var value = 0; func increment() { lock.lock(); value += 1; lock.unlock() }; func get() -> Int { lock.lock(); defer { lock.unlock() }; return value } }
private final class Barrier86: @unchecked Sendable { let condition = NSCondition(); var count = 0; func meet() { condition.lock(); count += 1; condition.broadcast(); let deadline = Date(timeIntervalSinceNow: 5); while count < 2 { if !condition.wait(until: deadline) { break } }; condition.unlock() } }
private final class Flag86: @unchecked Sendable { let lock = NSLock(); private var value = false; func set() { lock.lock(); value = true; lock.unlock() }; func get() -> Bool { lock.lock(); defer { lock.unlock() }; return value } }
@testable import Statelessness

final class FuzzAutoTests: XCTestCase {
    func testDeadlineDuringAutomaticScanReturnsReport() throws {
        var ticks: UInt64 = 0, pulls = 0
        let clock = IntegerDeadlineClock(now: { ticks })
        let limits = RunLimits(maxDurationNanoseconds: DurationNanoseconds(low: 10))
        let control = try RunControl(limits, integerClock: clock), scan = AutoScanControl(control)
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in
            InputIterator { pulls += 1; ticks = 10; return pulls }
        })
        let generated = autoControlled(model, options: AutoOptions64(), equalInputs: ==, scan: scan)
        let report = try fuzzKernel(generated, config: FuzzConfig64(cases: 1, maxSteps: 1), control: control, observer: nil, candidate: { _, _ in ([], nil) }, scanStop: { scan.reason })
        XCTAssertEqual(report.termination, "deadline")
        XCTAssertEqual(report.transitions, 0)
        XCTAssertEqual(pulls, 1)
    }

    func testGuidedCancellationDuringRetainedCorpusMembership() throws {
        // Find a deterministic seed that reuses the retained prefix without
        // deleting or replacing its first input; assert the scan is membership,
        // not a later fresh generation.
        var exercisedReuse = false
        for seed: UInt64 in 0..<64 {
            var cancelled = false, domains = 0, generatedCalls = 0
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in
                domains += 1
                if domains == 3 { return InputIterator { cancelled = true; return 1 } }
                return InputIterator(values: [1])
            })
            let control = try RunControl(RunLimits(isCancelled: { cancelled })), scan = AutoScanControl(control)
            var generated = autoControlled(model, options: AutoOptions64(), equalInputs: ==, scan: scan)
            let originalGenerate = generated.generate
            generated.generate = { state, rng in generatedCalls += 1; return try originalGenerate(state, &rng) }
            let feedback = Feedback<Int, Int, Int, Int>(metadata: { FeedbackMetadata(name: "test", build: "1") }, cloneFeature: { $0 }, transitionFeatures: { _, _, _, out in try out.push(1) })
            let report = try guidedFuzzKernel64(generated, config: FuzzConfig64(seed: seed, cases: 2, maxSteps: 1, mutationPercent: 100), guidance: GuidanceConfig64(), feedback: feedback, control: control, observer: nil, scanStop: { scan.reason })
            if report.termination == "cancelled" && generatedCalls == 1 {
                XCTAssertEqual(report.transitions, 1)
                XCTAssertEqual(report.guidance?.corpus.count, 1)
                XCTAssertEqual(domains, 3)
                exercisedReuse = true
                break
            }
        }
        XCTAssertTrue(exercisedReuse, "no seed exercised retained-prefix membership")
    }
    func testCancellationDuringScanReturnsReportWithoutTransition() throws {
        var cancelled = false, pulls = 0
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in
            InputIterator { pulls += 1; if pulls == 3 { cancelled = true }; return pulls }
        })
        let report = try fuzzAuto64(model, equalInputs: ==, config: FuzzConfig64(cases: 1, maxSteps: 1), limits: RunLimits(isCancelled: { cancelled }))
        XCTAssertEqual(report.termination, "cancelled")
        XCTAssertEqual(report.transitions, 0)
        XCTAssertEqual(pulls, 3)
    }
    func testInterruptedReservoirDoesNotCommitRNG() throws {
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [1, 2, 3]) })
        var rng = Rng(seed: 7), expected = Rng(seed: 7), polls = 0
        XCTAssertThrowsError(try sampleEnumeratedControlled(model, state: 0, rng: &rng, options: AutoOptions64(), poll: { polls += 1; if polls == 3 { throw ModelError("stop") } }))
        XCTAssertEqual(rng.nextU64(), expected.nextU64())
    }
    func testCancellationDuringMembershipAndCallbackErrorPrecedence() throws {
        var cancelled = false, domains = 0
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in
            domains += 1
            if domains == 1 { return InputIterator(values: [1]) }
            return InputIterator { cancelled = true; return 1 }
        })
        let report = try fuzzAuto64(model, equalInputs: ==, config: FuzzConfig64(cases: 1, maxSteps: 1), limits: RunLimits(isCancelled: { cancelled }))
        XCTAssertEqual(report.termination, "cancelled"); XCTAssertEqual(report.transitions, 0)
        cancelled = false
        var broken = model
        broken.inputs = { _ in InputIterator { cancelled = true; throw ModelError("iterator failure") } }
        XCTAssertThrowsError(try fuzzAuto64(broken, equalInputs: ==, config: FuzzConfig64(cases: 1, maxSteps: 1), limits: RunLimits(isCancelled: { cancelled }))) { XCTAssertTrue(String(describing: $0).contains("iterator failure")) }
    }
    func testCompletedRunMatchesExistingAuto() throws {
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [1, 2, 3]) })
        let config = FuzzConfig64(seed: 19, cases: 4, maxSteps: 4)
        var first: [Int] = [], second: [Int] = []
        let a = try fuzzAuto64(model, equalInputs: ==, config: config, observer: SearchObserver(transition: { _, input, _, _, _, _ in first.append(input) }))
        let b = try fuzz64(Auto64(model, equalInputs: ==).generated, config: config, observer: SearchObserver(transition: { _, input, _, _, _, _ in second.append(input) }))
        XCTAssertEqual(a.termination, b.termination); XCTAssertEqual(a.cases, b.cases); XCTAssertEqual(a.transitions, b.transitions); XCTAssertEqual(first, second)
    }
    func testGuidedCancellationDuringAutomaticScan() throws {
        var cancelled = false, pulls = 0
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator { pulls += 1; if pulls == 3 { cancelled = true }; return pulls } })
        let feedback = Feedback<Int, Int, Int, Int>(metadata: { FeedbackMetadata(name: "test", build: "1") }, cloneFeature: { $0 }, initialFeatures: { _, _ in }, transitionFeatures: { _, _, _, _ in })
        let result = try guidedFuzzAuto64(model, equalInputs: ==, config: FuzzConfig64(cases: 1, maxSteps: 1), feedback: feedback, limits: RunLimits(isCancelled: { cancelled }))
        XCTAssertEqual(result.termination, "cancelled"); XCTAssertEqual(result.transitions, 0); XCTAssertNotNil(result.guidance); XCTAssertEqual(pulls, 3)
    }
    func testAutoCampaignStopsDuringScanAndCleansUp() throws {
        let stop = Flag86(), closed = Flag86()
        let config = CampaignConfig64(jobs: 1, workers: 1, fuzz: FuzzConfig64(cases: 1, maxSteps: 1))
        let result = try runAutoCampaign64(config: config, limits: CampaignLimits(isCancelled: { stop.get() }), factory: { _ in
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in
                InputIterator { stop.set(); return 1 }
            })
            return CampaignJob(Generate(model: model, generate: { _, _ in 1 }, equalInputs: ==), metadata: { Metadata(name: "m", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "1") }, tearDown: { closed.set() })
        }, sink: { _ in })
        XCTAssertEqual(result.termination, .cancelled); XCTAssertTrue(closed.get())
    }
    func testGuidedAutoCampaignFeedbackFailureCleansUp() throws {
        let closed = Flag86()
        let result = try runGuidedAutoCampaign64(config: CampaignConfig64(jobs: 1, workers: 1), factory: { _ in
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [1]) })
            return CampaignJob(Generate(model: model, generate: { _, _ in 1 }, equalInputs: ==), metadata: { Metadata(name: "m", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "1") }, tearDown: { closed.set() })
        }, feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in throw ModelError("feedback failed") }, sink: { _ in })
        XCTAssertTrue(closed.get()); XCTAssertEqual(result.errors, 1); XCTAssertEqual(result.finished, 1)
    }
    func testAutoCampaignTwoWorkersCancelAndCleanUp() throws {
        let stop = Flag86(), barrier = Barrier86(), cleanups = Count86()
        let result = try runAutoCampaign64(config: CampaignConfig64(jobs: 2, workers: 2, fuzz: FuzzConfig64(cases: 1, maxSteps: 1)), limits: CampaignLimits(isCancelled: { stop.get() }), factory: { _ in
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator { barrier.meet(); stop.set(); return 1 } })
            return CampaignJob(Generate(model: model, generate: { _, _ in 1 }, equalInputs: ==), metadata: { Metadata(name: "m", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "1") }, tearDown: { cleanups.increment() })
        }, sink: { _ in })
        XCTAssertEqual(result.termination, .cancelled); XCTAssertEqual(result.started, 2); XCTAssertEqual(result.finished, 2); XCTAssertEqual(cleanups.get(), 2)
    }
    func testGuidedAutoCampaignCancellationInsideScan() throws {
        let stop = Flag86(), closed = Flag86()
        let result = try runGuidedAutoCampaign64(config: CampaignConfig64(jobs: 1, workers: 1, fuzz: FuzzConfig64(cases: 1, maxSteps: 1)), limits: CampaignLimits(isCancelled: { stop.get() }), factory: { _ in
            let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator { stop.set(); return 1 } })
            return CampaignJob(Generate(model: model, generate: { _, _ in 1 }, equalInputs: ==), metadata: { Metadata(name: "m", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "1") }, tearDown: { closed.set() })
        }, feedbackFactory: { _, _ in Feedback<Int, Int, Int, Int>(metadata: { FeedbackMetadata(name: "f", build: "1") }, cloneFeature: { $0 }, initialFeatures: { _, _ in }, transitionFeatures: { _, _, _, _ in }) }, sink: { _ in })
        XCTAssertEqual(result.termination, .cancelled); XCTAssertEqual(result.finished, 1); XCTAssertTrue(closed.get())
    }
}
