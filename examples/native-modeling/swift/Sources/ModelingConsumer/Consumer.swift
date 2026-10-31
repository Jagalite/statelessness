import Statelessness

func require(_ value: Bool) { precondition(value, "assertion failed") }
func rejects(_ text: String, _ callback: () throws -> Void) { do { try callback(); preconditionFailure("expected rejection") } catch { require(String(describing: error).contains(text)) } }
@main struct Consumer {
 static func main() async throws {
    var d = Domain<Int>(try DomainDescriptor(name: "actions", assumptions: "duplicates carry weight", maxEntries: 4), clone: { $0 })
    try d.extend(InputIterator(values: [3,1,3,7])); rejects("limit") { try d.push(9) }
    var a = Rng(seed: 42), b = Rng(seed: 42)
    let entries = try d.entries()
    for _ in 0..<100 { let v = try d.sampleIndexed(&a); require(v == entries[Int(b.index(4)!)]) }
    let product = try Domain<Int>.product([1,2], [4,5], maximum: 4, map: +); require(product == [5,6,6,7])
    rejects("limit") { _ = try Domain<Int>.product([1,2], [4,5], maximum: 3) { _,_ in preconditionFailure("eager map") } }
    require(checkLazy("lazy", true) { preconditionFailure("eager") }.status == .passed)
    require(checkLazy("lazy", false) { "detail" }.details == "detail")
    rejects("details") { _ = try checkLazy("lazy", false) { throw ModelError("details") } }
    rejects("duplicate") { _ = try declaredProperties([PropertyMetadata("p"), PropertyMetadata("p", phase: .transition)]) }
    // Exact UTF-8 IDs stay distinct, despite Swift canonical String equality.
    _ = try declaredProperties([PropertyMetadata("é"), PropertyMetadata("e\u{301}")])
    final class Box { var value: Int; init(_ value: Int) { self.value = value } }
    var owned = Domain<Box>(try DomainDescriptor(name: "owned", assumptions: "", maxEntries: 2), clone: { Box($0.value) })
    let value = Box(1); try owned.push(value); value.value = 2; let escaped = try owned.entries(); escaped[0].value = 3; require(try owned.entries()[0].value == 1)
    let codec = try ModelCodecBinding(metadata: { Metadata(name: "native-modeling-application", build: "teaching-v1") }, state: ValueCodecs.u8, input: ValueCodecs.u8, output: ValueCodecs.u8).codec()
    let raw = Model<UInt8,UInt8,UInt8>(initialState: defaultInitial({ 0 }, clone: { $0 }), step: { state,event in
        if event == 1 { return Transition(state: 1, outputs: [1]) }
        if event == 3 { return Transition(state: 0, outputs: [3]) }
        if event == 2 && state == 1 { return Transition(state: 2, outputs: [2]) }
        return Transition(state: state, disposition: .rejected("not busy"))
    }, checkState: { state in [checkLazy("state.range", state <= 2) { "invalid" }] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [1,2,3]) }, codec: codec)
    rejects("no properties") { _ = try adaptModel(raw, properties: []) }
    let m = try adaptModel(raw, properties: [PropertyMetadata("state.range")])
    let gen = try Auto(m, options: AutoOptions(maxCandidates: 2), equalInputs: ==).generated
    var rng = Rng(seed: 42), copy = Rng(seed: 42)
    rejects("max_candidates") { _ = try gen.generate(0, &rng) }; require(rng.nextU64() == copy.nextU64())
    require(try codec.encodeState(2) == [2]); rejects("trailing") { _ = try codec.decodeState([2,0]) }
    for events: [UInt8] in [[1,3,1,2],[1,2]] {
        var state = try m.initialState(); var observed: [UInt8] = []
        for event in events { let turn = try m.step(state,event); state = turn.state; observed.append(state) }
        let trace = try record(m, inputs: InputIterator(values: events)); require(try replay(m, trace: trace).outcome == "exact")
        require(try trace.steps.map { try codec.decodeState($0.postState) } == observed)
    }
    let start = try m.step(0,1)
    await Task.yield() // Host scheduler stays outside the synchronous reducer.
    let completion: UInt8 = 2
    let finish = try m.step(start.state,completion); require(finish.state == 2)
    require(try replay(m, trace: record(m, inputs: InputIterator(values: [1,completion]))).outcome == "exact")
    print(#"{"engine":"swift","controls":"passed","hosts":["ui","background","async"],"turns":8}"#)
 }
}
