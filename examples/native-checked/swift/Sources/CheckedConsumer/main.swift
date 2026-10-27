import Foundation
import Statelessness

func require(_ condition: Bool, _ message: String) throws { if !condition { throw ModelError(message) } }
final class Counts {
 var state = 0, input = 0, output = 0, legacy = 0, decode = 0, step = 0, initial = 0, stateChecks = 0, edgeChecks = 0
 var encodes: Int { state + input + output }
}
func ascii(_ value: Int) -> [UInt8] { Array(String(value).utf8) }
func integer(_ bytes: [UInt8]) throws -> Int {
 guard !bytes.isEmpty, bytes.allSatisfy({ (48...57).contains($0) }), let value = Int(String(decoding: bytes, as: UTF8.self)) else { throw ModelError("invalid ASCII integer") }
 return value
}
func application(overrun: Bool = false, divergent: Bool = false) -> (Model<Int, Int, String>, Counts) {
 let counts = Counts()
 let codec = Codec<Int, Int, String>(metadata: { Metadata(name: "native-checked-threshold", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "application-v1") },
  encodeState: { _ in counts.legacy += 1; throw ModelError("legacy state encoder called") },
  decodeState: { counts.decode += 1; return try integer($0) },
  encodeInput: { _ in counts.legacy += 1; throw ModelError("legacy input encoder called") },
  decodeInput: { counts.decode += 1; let value = try integer($0); try require(value == 0 || value == 1, "expected zero or one"); return value },
  encodeOutput: { _ in counts.legacy += 1; throw ModelError("legacy output encoder called") },
  encodeStateInto: { value, sink in counts.state += 1; try sink.extend(ascii(value)) },
  encodeInputInto: { value, sink in counts.input += 1; try sink.extend(ascii(value)) },
  encodeOutputInto: { value, sink in counts.output += 1; try sink.extend(Array(value.utf8)) })
 return (Model(initialState: { counts.initial += 1; return 0 }, step: { state, input in
  counts.step += 1; guard input == 0 || input == 1 else { throw ModelError("expected zero or one") }
  return Transition(state: state + input, outputs: input == 1 ? [overrun ? String(repeating:"x",count:65) : divergent ? "different" : "changed"] : [])
 }, checkState: { _ in throw ModelError("legacy state check") },
 cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec,
 checkStateInto: { state,sink in counts.stateChecks += 1; try sink.push(state >= 3 ? .failed("threshold.bound","at least three") : .passed("threshold.bound")); try sink.push(.passed("state.appended")) },
 checkTransitionInto: { _,_,_,sink in counts.edgeChecks += 1; try sink.push(.passed("edge.checked")) }), counts)
}
func finiteLimits() -> BinaryTraceLimits {
 var limits = BinaryTraceLimits()
 limits.maxTotalBytes = 8192; limits.maxFrameBytes = 2048; limits.maxBlobBytes = 64; limits.maxStringBytes = 256
 limits.maxSteps = 16; limits.maxChecksPerStep = 16; limits.maxOutputsPerStep = 16; limits.maxParameters = 32; limits.maxItems = 256
 return limits
}
let config = RunConfig(strategy: "manual")
func load(_ path: String) throws -> [UInt8] { Array(try Data(contentsOf: URL(fileURLWithPath: path))) }
func save(_ bytes: [UInt8], _ path: String) throws { try Data(bytes).write(to: URL(fileURLWithPath: path)) }
func rejects(_ operation: () throws -> Void) -> Bool { do { try operation(); return false } catch { return true } }
func checksJSON(_ checks: [Check]) -> [[String: Any]] { checks.map { ["id": $0.id, "status": $0.status.rawValue, "details": $0.details] } }
func dispositionJSONValue(_ value: Disposition) -> [String: Any] {
 switch value { case .accepted: return ["kind":"accepted", "reason":""]; case let .rejected(reason): return ["kind":"rejected", "reason":reason]; case let .ignored(reason): return ["kind":"ignored", "reason":reason] }
}
func reportJSON(_ report: ReplayReport) -> [String: Any] {
 ["outcome":report.outcome, "divergence_step":report.step.map { $0 as Any } ?? NSNull(), "divergence_field":report.field.map { $0 as Any } ?? NSNull(),
  "steps_verified":report.stepsVerified, "failure_reproduced":report.failureReproduced, "build_matches":report.buildMatches]
}
func differencesJSON(_ d: ReplayDifferences) -> [String: Bool] { ["disposition":d.disposition,"outputs":d.outputs,"state":d.state,"checks":d.checks] }
func stepJSON(_ step: TraceStep) -> [String: Any] {
 ["input":hexString(step.input),"disposition":dispositionJSONValue(step.disposition),"outputs":step.outputs.map(hexString),"post_state":hexString(step.postState),"checks":checksJSON(step.checks)]
}
func observed(_ model: Model<Int,Int,String>, _ audit: AuditTrace, bounded: Bool) throws -> [String: Any] {
 var observations: [[String: Any]] = []
 let observer: (ReplayObservation<Int,Int,String>, ReplayReport) throws -> Void = { observation, report in
  switch observation {
  case let .initial(value): observations.append(["kind":"initial","sequence":0,"state":value.state,"checks":checksJSON(value.checks),"expected_state":hexString(value.expectedState),"actual_state":hexString(value.actualState),"expected_checks":checksJSON(value.expectedChecks),"differences":differencesJSON(value.differences),"report":reportJSON(report)])
  case let .turn(value): observations.append(["kind":"turn","sequence":value.sequence,"before":value.before,"input":value.input,"transition":["state":value.transition.state,"outputs":value.transition.outputs,"disposition":dispositionJSONValue(value.transition.disposition)] as [String:Any],"checks":checksJSON(value.checks),"state_check_count":value.stateCheckCount,"expected":stepJSON(value.expected),"actual_input":hexString(value.actualInput),"actual_outputs":value.actualOutputs.map(hexString),"actual_state":hexString(value.actualState),"differences":differencesJSON(value.differences),"report":reportJSON(report)])
  case .error: throw ModelError("unexpected replay error observation")
  }
 }
 let report: ReplayReport
 if bounded { report = try replayWithObservationsBounded(model, trace: audit, limits: finiteLimits(), observer: observer) }
 else { report = try replayWithObservations(model, trace: audit.portable().trace, observer: observer) }
 return ["report":reportJSON(report),"observations":observations]
}
func options(_ n:Int=3)->RecorderOptions { RecorderOptions(maxSteps:n,maxRetainedBytes:8192,limits:finiteLimits()) }
func count(_ c:Counts)->[String:Int] { ["initial_state":c.initial,"reducer":c.step,"state":c.stateChecks,"transition":c.edgeChecks] }
func attach(_ m:Model<Int,Int,String>,_ state:Int=0,_ origin:UInt64=0,_ n:Int=3)throws->Recorder { try Recorder(checkedInitial:checkInitial(m,state:state),sequenceOrigin:origin,config:config,options:options(n)) }
func token(_ m:Model<Int,Int,String>,_ before:Int,_ seq:UInt64,_ policy:CheckPolicy=CheckPolicy(),_ output:String="changed")throws->CheckedTurn<Int,Int,String>{try checkTurn(m,before:before,input:1,transition:Transition(state:before+1,outputs:[output]),sequence:seq,policy:policy)}
func retainedConfig(_ a:AuditTrace)->[String:Any]{["strategy":a.config.strategy,"seed":a.config.seed.map { String($0) as Any } ?? NSNull(),"parameters":a.config.parameters.map { [$0.key,$0.value] }]}
func controls() throws -> ([String:Any],[String:Any]) {
 let (m,c)=application(); let initial=try checkInitial(m,state:1), before=count(c)
 let origin=try Recorder(checkedInitial:initial,sequenceOrigin:10,config:config,options:options());try require(count(c)==before,"attach rechecked")
 for i in 0..<2 {let t=try token(m,i+1,UInt64(i+11)),before=count(c);try origin.observeChecked(t);try require(count(c)==before,"handoff rechecked")}
 try require(origin.isFrozen && origin.snapshot().initialState == ascii(1) && origin.snapshot().config.parameters.contains(where:{$0.key=="stateless.monitor.sequence_origin" && $0.value=="10"}),"origin lost")
 let start=try checkInitial(m,state:0),first=try token(m,0,1),left=try Recorder(checkedInitial:start,config:config,options:options()),right=try Recorder(checkedInitial:start,config:config,options:options())
 try left.observeChecked(first);try right.observeChecked(first);try require(encodeAuditTrace(left.snapshot()) == encodeAuditTrace(right.snapshot()),"token reuse")
 for kind in ["sampled","duplicate","gap"] {let rec=try attach(m);if kind=="duplicate"{try rec.observeChecked(first)}
 let bad:CheckedTurn<Int,Int,String>
 if kind=="duplicate" { bad=first } else { bad=try token(m,0,kind=="gap" ? 2:1,kind=="sampled" ? CheckPolicy(stateEvery:2):CheckPolicy()) }
 try require(rejects {try rec.observeChecked(bad)} && rec.isFrozen,"invalid token accepted");let prefix=try encodeAuditTrace(rec.snapshot());try require(rec.retainedSteps == (kind=="duplicate" ? 1:0),"prefix lost");try require(rejects{try rec.observeChecked(first)},"freeze lost");try require(prefix == encodeAuditTrace(rec.snapshot()),"freeze overwritten")}
 var diagnostic=try first.observation(); var copiedChecks=first.intoChecks();copiedChecks.removeAll();diagnostic=TurnObservation(sequence:99,before:99,input:1,transition:Transition(state:99),checks:[],stateCheckCount:0)
 try require(first.sequence==1 && first.checks.count==3 && first.transition.state==1 && diagnostic.sequence==99,"accessor ownership")
 // Compile-time checked parameter types exclude this diagnostic from observeChecked.
 let ordinary=try attach(m);let beforeChecks=c.stateChecks;try ordinary.observe(model:m,before:0,input:1,transition:Transition(state:1,outputs:["changed"]));try require(c.stateChecks==beforeChecks+1,"ordinary diagnostic path bypassed checks")
 let bounded=try attach(m);try bounded.observeChecked(first);let bad=try token(m,1,2,CheckPolicy(),String(repeating:"x",count:65)),beforeError=count(c);try require(rejects{try bounded.observeChecked(bad)} && count(c)==beforeError,"bounded accepted or checks repeated")
 let prefix=bounded.snapshot(),bytes=try encodeAuditTrace(prefix,limits:finiteLimits());try require(bounded.isFrozen && prefix.steps.count==1 && bytes == encodeAuditTrace(decodeAuditTrace(bytes,limits:finiteLimits()),limits:finiteLimits()),"bounded prefix incoherent")
 let ring=try attach(m,0,0,1);try ring.observeChecked(first);try ring.observeChecked(token(m,1,2));let audit=ring.snapshot();try require(audit.initialState==ascii(1) && audit.initialChecks.count==2 && !audit.initialChecks.contains(where:{$0.id=="edge.checked"}) && ring.evictedSteps==1,"ring promoted edge checks")
 return (["check_once":true,"attach_rechecks":false,"handoff_rechecks":false,"token_reused":true,"sampled_rejected":true,"duplicate_rejected":true,"gap_rejected":true,"origin_preserved":true,"ring_state_prefix_only":true,"bounded_error_prefix":true,"statically_sealed":true,"accessor_ownership":true],["rich":try observed(m,audit,bounded:false),"bounded":try observed(m,audit,bounded:true),"retained_config":retainedConfig(audit)])
}
let args = CommandLine.arguments
try require(args.count == 3, "expected record PREFIX, replay BIN, or replay-json JSON")
let (model, counts) = application()
var receipt: [String:Any]
switch args[1] {
case "record":
 let initial=try checkInitial(model,state:0),rec=try Recorder(checkedInitial:initial,config:config,options:options())
 var certificates:[CheckedTurn<Int,Int,String>]=[]
 for sequence in 1...3 {let actual=try model.step(sequence-1,1),checked=try checkTurn(model,before:sequence-1,input:1,transition:actual,sequence:UInt64(sequence)),before=count(counts);certificates.append(checked);try rec.observeChecked(checked);try require(count(counts)==before,"handoff reexecuted")}
 let audit=rec.snapshot(),mainCounts=count(counts);try require(mainCounts == ["initial_state":0,"reducer":3,"state":4,"transition":3],"check-once counts differ");let (control,ring)=try controls()
 try save(encodeAuditTrace(audit, limits:finiteLimits()), args[2]+".bin"); try save(encodeTrace(audit.portable().trace), args[2]+".json")
 receipt = ["steps":audit.steps.count,"termination":"property_failed","rich":try observed(model,audit,bounded:false),"bounded":try observed(model,audit,bounded:true),"controls":control,"ring":ring,"main_counts":mainCounts,"checked":["initial_checks":initial.stateCheckCount,"state_check_count":certificates[0].stateCheckCount,"full_checking":certificates[0].fullChecking,"sequence":String(certificates[0].sequence)] as [String:Any],"retained_config":["strategy":audit.config.strategy,"seed":audit.config.seed.map { String($0) as Any } ?? NSNull(),"parameters":audit.config.parameters.map { [$0.key,$0.value] }] as [String:Any]]
case "replay":
 let audit = try decodeAuditTrace(load(args[2]), limits:finiteLimits())
 receipt = ["rich":try observed(model,audit,bounded:false),"bounded":try observed(model,audit,bounded:true),"retained_config":["strategy":audit.config.strategy,"seed":audit.config.seed.map { String($0) as Any } ?? NSNull(),"parameters":audit.config.parameters.map { [$0.key,$0.value] }] as [String:Any]]
case "replay-json":
 receipt = reportJSON(try replay(model,trace:parseTrace(load(args[2]))))
default: throw ModelError("unknown mode")
}
receipt["legacy_encoder_calls"] = counts.legacy; receipt["codec_into_calls"] = counts.encodes
receipt["producer"] = "swift"; receipt["mode"] = args[1]
print(String(decoding:try JSONSerialization.data(withJSONObject:receipt,options:[.sortedKeys]),as:UTF8.self))
