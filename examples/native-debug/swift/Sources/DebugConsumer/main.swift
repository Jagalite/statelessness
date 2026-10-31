import Statelessness
import StatelessDebug

func require(_ value: Bool,_ message: String) throws { if !value { throw ModelError(message) } }
let metadata = Metadata(name: "installed-debug",build: "manual-v1")
let codec = Codec<Int,Int,Int>(metadata: { metadata },encodeState: { [UInt8($0)] },decodeState: { Int($0[0]) },encodeInput: { [UInt8($0)] },decodeInput: { Int($0[0]) },encodeOutput: { [UInt8($0)] })
let model = Model<Int,Int,Int>(initialState: { 0 },step: { s,i in Transition(state: s+i,outputs: [s+i]) },checkState: { _ in [] },cloneState: {$0},cloneInput: {$0},cloneOutput: {$0},codec: codec)
let session = try DebugSession(id: "installed-fresh-process",model: model,policy: .outOfDomainInjection,equalInputs: ==,recording: RecorderOptions())
let result = try session.step(expectedRevision: 0,input: 1)
try require(result.delivered && session.revision == 1,"typed session")
try require(try session.state == 1,"detached state")
try require(!session.capabilities.effectsDispatched && session.capabilities.exactRecording,"optional capabilities")
let viewer = try EvidenceViewer(.v1(session.exportTrace()))
try require(viewer.boundary(1)?.after == [1],"passive exact boundary")
try require(try viewer.verify(model,budget: VerificationLimits(maxTransitions: 1)).outcome == "exact","explicit exact verification")
let branch = try viewer.fork(id: "fork",model: model,sequence: 1,policy: .outOfDomainInjection,budget: VerificationLimits(maxTransitions: 1),equalInputs: ==)
_ = try branch.session.step(expectedRevision: 0,input: 2)
try require(try branch.session.state == 3,"independent fork")
try require(viewer.boundary(1)?.after == [1],"parent evidence stable")
let node = try InspectContext().scalar(.integer(kind: ExactString("u64"),decimal: ExactString(String(UInt64.max))))
try require(compareNodes(node,node) == .unchanged,"full-width display")
let schema = try HistogramSchema(finiteBoundsNS: [10,100])
var histogram = Histogram(); for n: UInt64 in [0,10,11,101] { try histogram.add(n,schema: schema) }
try require(histogram.quantile(schema: schema,numerator: 95,denominator: 100)?.upperBoundNS == nil,"overflow histogram quantile")
let gate = try LiveController<Int>(epoch: 1)
try require(gate.externalArrival(1,ownedBytes: 8) == nil,"host arrival")
if let (permit,input) = try gate.beginNext() { try require(input == 1,"arrival identity"); try gate.delivered(permit,outputs: 0) } else { throw ModelError("missing host permit") }
try require(gate.revision == 1,"cooperative completed revision")
print("installed optional StatelessDebug fresh-process proof passed: viewer, verification, typed session, isolated fork, full-width inspection, histogram, cooperative host permit")
