import Foundation
import Statelessness

func require(_ condition: Bool, _ message: String) throws { if !condition { throw ModelError(message) } }
final class Counts {
 var state = 0, input = 0, output = 0, legacy = 0, decode = 0, step = 0
 var encodes: Int { state + input + output }
}
func ascii(_ value: Int) -> [UInt8] { Array(String(value).utf8) }
func integer(_ bytes: [UInt8]) throws -> Int {
 guard !bytes.isEmpty, bytes.allSatisfy({ (48...57).contains($0) }), let value = Int(String(decoding: bytes, as: UTF8.self)) else { throw ModelError("invalid ASCII integer") }
 return value
}
func application(overrun: Bool = false, divergent: Bool = false) -> (Model<Int, Int, String>, Counts) {
 let counts = Counts()
 let codec = Codec<Int, Int, String>(metadata: { Metadata(name: "native-codec-replay-threshold", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "application-v1") },
  encodeState: { _ in counts.legacy += 1; throw ModelError("legacy state encoder called") },
  decodeState: { counts.decode += 1; return try integer($0) },
  encodeInput: { _ in counts.legacy += 1; throw ModelError("legacy input encoder called") },
  decodeInput: { counts.decode += 1; let value = try integer($0); try require(value == 0 || value == 1, "expected zero or one"); return value },
  encodeOutput: { _ in counts.legacy += 1; throw ModelError("legacy output encoder called") },
  encodeStateInto: { value, sink in counts.state += 1; try sink.extend(ascii(value)) },
  encodeInputInto: { value, sink in counts.input += 1; try sink.extend(ascii(value)) },
  encodeOutputInto: { value, sink in counts.output += 1; try sink.extend(Array(value.utf8)) })
 return (Model(initialState: { 0 }, step: { state, input in
  counts.step += 1; guard input == 0 || input == 1 else { throw ModelError("expected zero or one") }
  return Transition(state: state + input, outputs: input == 1 ? [overrun ? String(repeating:"x",count:65) : divergent ? "different" : "changed"] : [])
 }, checkState: { $0 >= 3 ? [.failed("threshold.bound", "at least three")] : [.passed("threshold.bound")] },
 cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec), counts)
}
func finiteLimits() -> BinaryTraceLimits {
 var limits = BinaryTraceLimits()
 limits.maxTotalBytes = 4096; limits.maxFrameBytes = 1024; limits.maxBlobBytes = 64; limits.maxStringBytes = 128
 limits.maxSteps = 16; limits.maxChecksPerStep = 16; limits.maxOutputsPerStep = 16; limits.maxParameters = 16; limits.maxItems = 128
 return limits
}
let config = RunConfig(strategy: "manual")
func load(_ path: String) throws -> [UInt8] { Array(try Data(contentsOf: URL(fileURLWithPath: path))) }
func save(_ bytes: [UInt8], _ path: String) throws { try Data(bytes).write(to: URL(fileURLWithPath: path)) }
func rejects(_ operation: () throws -> Void) -> Bool { do { try operation(); return false } catch { return true } }
func bufferControls() throws -> [String: Any] {
 var bytes: [UInt8] = [99, 99], escaped: EncodeBuffer?, nested: EncodeBuffer?
 var cleared = false, nestedExpired = false, parentActive = false
 try EncodeBuffer.withBuffer(&bytes, maximum: 10) { sink in
  cleared = try sink.count == 0; escaped = sink
  try sink.extend([7])
  try sink.framed { child in nested = child; try child.extend([8]) }
  nestedExpired = rejects { try nested!.extend([]) }
  parentActive = try sink.count == 10; try sink.finish()
 }
 let expired = rejects { try escaped!.extend([]) } && rejects { _ = try escaped!.count } && rejects { try escaped!.finish() }
 let framing = bytes == [7, 1, 0, 0, 0, 0, 0, 0, 0, 8]
 var sticky = false
 let rejected = rejects {
  try EncodeBuffer.withBuffer(&bytes, maximum: 1) { sink in
   _ = rejects { try sink.extend([1, 2]) }
   sticky = rejects { try sink.extend([]) } && rejects { try sink.finish() }
  }
 }
 try require(cleared && framing && expired && nestedExpired && parentActive && sticky && rejected, "buffer controls failed")
 var nestedBytes: [UInt8] = []
 try EncodeBuffer.withBuffer(&nestedBytes, maximum: 17) { sink in try sink.framed { child in try child.framed { try $0.extend([9]) } } }
 let nestedFraming = nestedBytes == [9,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,9]
 var small: [UInt8] = []
 let sharedBudget = rejects { try EncodeBuffer.withBuffer(&small, maximum: 8) { sink in try sink.framed { try $0.extend([1]) } } }
 return ["clear_entry": cleared, "sticky_finish": sticky && rejected, "framed_shared_budget": framing && sharedBudget, "nested_framing": nestedFraming,
  "escaped_sink_expired": expired, "nested_child_expired": nestedExpired, "outer_active_after_nested": parentActive]
}
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
func controls(_ audit: AuditTrace) throws -> [String:Any] {
 var result = try bufferControls()
 let (preflightModel, preflightCounts) = application(); var small = finiteLimits(); small.maxSteps = 2
 let preflightRejected = rejects { _ = try replayWithObservationsBounded(preflightModel, trace:audit, limits:small) { _,_ in } }
 try require(preflightRejected && preflightCounts.decode == 0 && preflightCounts.step == 0 && preflightCounts.legacy == 0 && preflightCounts.encodes == 0, "bounded preflight called decoder/reducer/encoder")
 let (oversizeModel, oversizeCounts) = application(overrun:true); var retained = false
 do { _ = try replayWithObservationsBounded(oversizeModel, trace:audit, limits:finiteLimits()) { _,_ in }; throw ModelError("oversize replay succeeded") }
 catch let error as ReplayError<Int,Int,String> {
  if case let .turn(sequence,before,input,transition,checks,count)? = error.actual {
   retained = sequence == 1 && before == 0 && input == 1 && transition.state == 1 && transition.outputs == [String(repeating:"x",count:65)] && checks == [.passed("threshold.bound")] && count == 1
  }
 }
 try require(oversizeCounts.step == 1 && retained, "oversize actual missing")
 var steps = audit.steps; let last = steps.removeLast()
 steps.append(TraceStep(input:last.input, disposition:last.disposition, outputs:last.outputs, postState:last.postState, checks:[.failed("threshold.bound","different expected detail")]))
 let changed = AuditTrace(metadata:audit.metadata, config:audit.config, initialState:audit.initialState, initialChecks:audit.initialChecks, steps:steps, termination:audit.termination)
 let (divergentModel, divergentCounts) = application()
 let divergence = try replayWithObservations(divergentModel, trace:changed.portable().trace) { _,_ in }
 let richDivergence = divergence.outcome == "diverged" && divergence.field == "checks" && divergence.step == 3 && divergence.failureReproduced
 try require(richDivergence, "rich divergence missing")
 result["preflight_decode_calls"] = preflightCounts.decode; result["oversize_reducer_calls"] = oversizeCounts.step
 result["oversize_actual_retained"] = retained; result["rich_divergence"] = richDivergence
 result["legacy_encoder_calls"] = preflightCounts.legacy + oversizeCounts.legacy + divergentCounts.legacy
 result["codec_into_calls"] = preflightCounts.encodes + oversizeCounts.encodes + divergentCounts.encodes
 return result
}
let args = CommandLine.arguments
try require(args.count == 3, "expected record PREFIX, replay BIN, or replay-json JSON")
let (model, counts) = application()
var receipt: [String:Any]
switch args[1] {
case "record":
 let audit = try recordAudit(model, inputs:InputIterator(values:[1,1,1]), config:config, limits:finiteLimits())
 try save(encodeAuditTrace(audit, limits:finiteLimits()), args[2]+".bin"); try save(encodeTrace(audit.portable().trace), args[2]+".json")
 receipt = ["steps":audit.steps.count,"termination":"property_failed","rich":try observed(model,audit,bounded:false),"bounded":try observed(model,audit,bounded:true),"controls":try controls(audit),"retained_config":["strategy":audit.config.strategy,"seed":audit.config.seed.map { String($0) as Any } ?? NSNull(),"parameters":audit.config.parameters.map { [$0.key,$0.value] }] as [String:Any]]
case "replay":
 let audit = try decodeAuditTrace(load(args[2]), limits:finiteLimits())
 receipt = ["rich":try observed(model,audit,bounded:false),"bounded":try observed(model,audit,bounded:true),"retained_config":["strategy":audit.config.strategy,"seed":audit.config.seed.map { String($0) as Any } ?? NSNull(),"parameters":audit.config.parameters.map { [$0.key,$0.value] }] as [String:Any],"controls":try controls(audit)]
case "replay-json":
 receipt = reportJSON(try replay(model,trace:parseTrace(load(args[2]))))
default: throw ModelError("unknown mode")
}
receipt["legacy_encoder_calls"] = counts.legacy; receipt["codec_into_calls"] = counts.encodes
receipt["producer"] = "swift"; receipt["mode"] = args[1]
print(String(decoding:try JSONSerialization.data(withJSONObject:receipt,options:[.sortedKeys]),as:UTF8.self))
