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
 let codec = Codec<Int, Int, String>(metadata: { Metadata(name: "native-evidence-threshold", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "application-v1") },
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
 limits.maxTotalBytes = 16384; limits.maxFrameBytes = 4096; limits.maxBlobBytes = 64; limits.maxStringBytes = 256
 limits.maxSteps = 16; limits.maxChecksPerStep = 16; limits.maxOutputsPerStep = 16; limits.maxParameters = 32; limits.maxItems = 256
 return limits
}
let config = RunConfig(strategy: "manual")
func load(_ path: String) throws -> [UInt8] { Array(try Data(contentsOf: URL(fileURLWithPath: path))) }
func save(_ bytes: [UInt8], _ path: String) throws { try Data(bytes).write(to: URL(fileURLWithPath: path)) }
func rejects(_ operation: () throws -> Void) -> Bool { do { try operation(); return false } catch { return true } }

func evidenceLimits()->EvidenceLimits { var l=EvidenceLimits();l.trace=finiteLimits();l.maxMetadataBytes=1024;l.maxLabels=8;l.maxLabelBytes=128;l.maxCausesPerStep=4;l.maxTotalCauses=8;return l }
func provenance()->Provenance {Provenance(run:"installed-evidence-run",epoch:7,manifest:"manifest-v1",source:"consumer-source",environment:[AuditParameter("region","test"),AuditParameter("note","é")])}
func context(_ seq:Int,_ full:Bool=true)->CausalContext {CausalContext(machine:full ? "worker-a":nil,generation:full ? 9:nil,correlation:full ? "request-1":nil,logicalTime:full ? UInt64(10+seq):nil,causes:seq==1 ? []:[OutputOrigin(sequence:UInt64(seq-1),output:0)])}
func options(_ n:Int=3)->RecorderOptions {RecorderOptions(maxSteps:n,maxRetainedBytes:16384,limits:finiteLimits())}
func attach(_ m:Model<Int,Int,String>,_ n:Int=3,_ l:EvidenceLimits=evidenceLimits(),_ p:Provenance=provenance())throws->EvidenceRecorder {try EvidenceRecorder(checkedInitial:checkInitial(m,state:0),origin:0,config:config,options:options(n),provenance:p,limits:l)}
func token(_ m:Model<Int,Int,String>,_ before:Int,_ seq:Int)throws->CheckedTurn<Int,Int,String>{try checkTurn(m,before:before,input:1,transition:Transition(state:before+1,outputs:["changed"]),sequence:UInt64(seq))}
func count(_ c:Counts)->[String:Int]{["initial_state":c.initial,"reducer":c.step,"state":c.stateChecks,"transition":c.edgeChecks]}
func optional<T>(_ value:T?)->Any {value.map{$0 as Any} ?? NSNull()}
func configJSON(_ t:AuditTrace)->[String:Any]{["strategy":t.config.strategy,"seed":optional(t.config.seed.map{String($0)}),"parameters":t.config.parameters.map{[$0.key,$0.value]}]}
func checksJSON(_ cs:[Check])->[[String:Any]]{cs.map{["id":$0.id,"status":$0.status.rawValue,"reason":$0.details.isEmpty ? NSNull():$0.details as Any]}}
func terminationJSON(_ t:AuditTermination)->[String:Any]{switch t{case .completed:return ["kind":"completed","reason":NSNull()];case .propertyFailed:return ["kind":"property_failed","reason":NSNull()];case .stepLimit:return ["kind":"step_limit","reason":NSNull()];case .interrupted:return ["kind":"interrupted","reason":NSNull()];case let .modelError(s):return ["kind":"model_error","reason":s]}}
func dispositionJSON(_ d:Disposition)->[String:Any]{switch d{case .accepted:return ["kind":"accepted","reason":NSNull()];case let .rejected(s):return ["kind":"rejected","reason":s];case let .ignored(s):return ["kind":"ignored","reason":s]}}
func normalize(_ v:TraceV2)->[String:Any]{let t=v.trace
 let trace:[String:Any]=["metadata":["name":t.metadata.name,"model_version":t.metadata.modelVersion,"properties_version":t.metadata.propertiesVersion,"codec_version":t.metadata.codecVersion,"build":t.metadata.build] as [String:Any],"config":configJSON(t),"initial_state":hexString(t.initialState),"initial_checks":checksJSON(t.initialChecks),"steps":t.steps.map{["input":hexString($0.input),"disposition":dispositionJSON($0.disposition),"outputs":$0.outputs.map(hexString),"post_state":hexString($0.postState),"checks":checksJSON($0.checks)] as [String:Any]},"termination":terminationJSON(t.termination)]
 let rows:[[String:Any]]=v.steps.map{s in ["sequence":String(s.sequence),"state_check_count":s.stateCheckCount,"causal":["machine":optional(s.causal.machine),"generation":optional(s.causal.generation.map{String($0)}),"correlation":optional(s.causal.correlation),"logical_time":optional(s.causal.logicalTime.map{String($0)}),"causes":s.causal.causes.map{["sequence":String($0.sequence),"output":$0.output] as [String:Any]}] as [String:Any]]}
 return ["trace":trace,"checkpoint_after":String(v.checkpointAfter),"provenance":["run":v.provenance.run,"epoch":String(v.provenance.epoch),"manifest":optional(v.provenance.manifest),"source":optional(v.provenance.source),"environment":v.provenance.environment.map{[$0.key,$0.value]}] as [String:Any],"steps":rows]}
func reportJSON(_ r:ReplayReport)->[String:Any]{["outcome":r.outcome,"divergence_step":optional(r.step),"divergence_field":optional(r.field),"steps_verified":r.stepsVerified,"failure_reproduced":r.failureReproduced,"build_matches":r.buildMatches]}
func readArtifact(_ path:String)throws->Artifact{let f=try FileHandle(forReadingFrom:URL(fileURLWithPath:path));defer{try? f.close()};return try Artifact.read(limits:evidenceLimits()){n in Array(try f.read(upToCount:n) ?? Data())}}
func rejected(_ contains:String="",_ f:()throws->Void)throws{do{try f()}catch{try require(contains.isEmpty || String(describing:error).lowercased().contains(contains),"wrong rejection: \(error)");return};throw ModelError("expected rejection")}
func policy()->ExportPolicy{ExportPolicy(maxSteps:2,maxBytes:4096,propertyIDs:["threshold.bound"])}
func controls(_ v:TraceV2,_ prefix:String)throws->([String:Bool],String){
 for kind in ["metadata","cause","trace"]{let (m,_)=application();var l=evidenceLimits();l.maxMetadataBytes=kind=="metadata" ? 70:256;l.maxTotalCauses=kind=="cause" ? 1:8;let r=try attach(m,kind=="trace" ? 1:3,l,Provenance());for seq in 1...(kind=="trace" ? 2:3){try r.observe(token(m,seq-1,seq),causal:kind=="cause" ? context(seq,false):CausalContext())};let x=r.snapshot();try x.validate(limits:l);try require(x.checkpointAfter == (kind=="cause" ? 2:1) && x.steps.count==x.trace.steps.count && x.trace.initialChecks.count==2 && !x.trace.initialChecks.contains(where:{$0.id=="edge.checked"}) && x.replay(m,limits:l).outcome=="exact","coordinated eviction")}
 let (m,c)=application();var l=evidenceLimits();l.maxMetadataBytes=62;let r=try attach(m,3,l,Provenance());try r.observe(token(m,0,1));let bad=try token(m,1,2);try rejected("output"){try r.observe(bad,causal:CausalContext(causes:[OutputOrigin(sequence:1,output:99)]))};let x=r.snapshot();try x.validate(limits:l);try require(r.isFrozen && x.checkpointAfter==0 && x.steps.count==1,"invalid cause corrupted prefix")
 var rows=v.steps;rows[0]=StepEvidence(sequence:rows[0].sequence,stateCheckCount:1,causal:rows[0].causal);let changed=TraceV2(trace:v.trace,checkpointAfter:v.checkpointAfter,provenance:v.provenance,steps:rows),negative=try changed.replay(m,limits:evidenceLimits());try require(negative.outcome=="diverged" && negative.field=="check_phase" && negative.step==1 && negative.stepsVerified==0 && !negative.failureReproduced,"phase bypass")
 let up=try TraceV2.verifyUpgrade(m,trace:v.trace,provenance:provenance(),checkpointAfter:0,limits:evidenceLimits());try require(up.replay(m,limits:evidenceLimits()).outcome=="exact" && up.steps.allSatisfy{$0.causal.machine==nil && $0.causal.causes.isEmpty},"upgrade fabricated labels")
 let a=Artifact.v2(v),data=try diagnosticJSON(a,policy:policy()),fit=ExportPolicy(maxSteps:2,maxBytes:data.count,propertyIDs:["threshold.bound"]);try require(diagnosticJSON(a,policy:fit)==data,"diagnostic exactfit");try rejected{_ = try diagnosticJSON(a,policy:ExportPolicy(maxSteps:2,maxBytes:data.count-1,propertyIDs:["threshold.bound"]))};let zero=try JSONSerialization.jsonObject(with:Data(diagnosticJSON(a,policy:ExportPolicy(maxSteps:0,maxBytes:4096)))) as! [String:Any];try require((zero["observations"] as! [Any]).count==1 && zero["truncated"] as? Bool==true,"diagnostic omitted initial")
 try exportDiagnosticNew(a,path:prefix+".diagnostic",policy:policy());try rejected{try exportDiagnosticNew(a,path:prefix+".diagnostic",policy:policy())};try require(load(prefix+".diagnostic")==data,"diagnostic overwritten");try rejected{try v.writeRawNew(path:prefix+".v2",limits:evidenceLimits())}
 let before=count(c),legacy=try decodeArtifact(encodeAuditTrace(v.trace,limits:finiteLimits()),limits:evidenceLimits());try require(legacy.checkPhase(sequence:0,checkIndex:0) == .initialState && legacy.checkPhase(sequence:1,checkIndex:0)==nil && count(c)==before,"passive v1 phase invention")
 return (["check_once":true,"coordinated_metadata_eviction":true,"coordinated_cause_eviction":true,"coordinated_trace_eviction":true,"source_index_before_eviction":true,"phase_divergence":true,"verified_upgrade":true,"diagnostic_exact":true,"diagnostic_caps":true,"exclusive_private_files":true,"no_overwrite":true,"passive_v1_no_execution":true],String(decoding:data,as:UTF8.self))
}
let args=CommandLine.arguments;try require(args.count>=3,"missing operation/path");let mode=args[1],path=args[2],(model,counts)=application();var receipt:[String:Any]
switch mode{
case "record":let r=try attach(model);for seq in 1...3{let actual=try model.step(seq-1,1),checked=try checkTurn(model,before:seq-1,input:1,transition:actual,sequence:UInt64(seq)),before=count(counts);try r.observe(checked,causal:context(seq));try require(count(counts)==before,"handoff reexecuted")};let v=r.snapshot(),mainCounts=count(counts);try require(mainCounts == ["initial_state":0,"reducer":3,"state":4,"transition":3],"check-once counts");try v.writeRawNew(path:path+".v2",limits:evidenceLimits());try save(encodeAuditTrace(v.trace,limits:finiteLimits()),path+".v1");try save(encodeTrace(v.trace.portable().trace),path+".json");let (control,text)=try controls(v,path);receipt=["evidence":normalize(v),"report":try reportJSON(v.replay(model,limits:evidenceLimits())),"retained_config":configJSON(v.trace),"main_counts":mainCounts,"controls":control,"diagnostic_text":text]
case "read-v1":let a=try readArtifact(path);guard case .v1 = a else{throw ModelError("expected v1")};receipt=["version":1,"initial_phase":optional(a.checkPhase(sequence:0,checkIndex:0)?.rawValue),"turn_phase":optional(a.checkPhase(sequence:1,checkIndex:0)?.rawValue),"execution_calls":counts.initial+counts.step+counts.stateChecks+counts.edgeChecks,"retained_config":configJSON(a.trace)]
case "replay","upgrade":try require(args.count==4,"missing output");let a=try readArtifact(path),v:TraceV2;if mode=="replay"{guard case let .v2(evidence)=a else{throw ModelError("expected v2")};v=evidence}else{v=try TraceV2.verifyUpgrade(model,trace:a.trace,provenance:provenance(),checkpointAfter:0,limits:evidenceLimits())};try v.writeRawNew(path:args[3],limits:evidenceLimits());receipt=["version":2,"evidence":normalize(v),"report":try reportJSON(v.replay(model,limits:evidenceLimits())),"retained_config":configJSON(v.trace)]
case "diagnostic":try require(args.count==4,"missing output");let a=try readArtifact(path),data=try diagnosticJSON(a,policy:policy());try exportDiagnosticNew(a,path:args[3],policy:policy());try rejected{try exportDiagnosticNew(a,path:args[3],policy:policy())};try require(load(args[3])==data,"diagnostic overwritten");receipt=["diagnostic_text":String(decoding:data,as:UTF8.self),"execution_calls":counts.initial+counts.step+counts.stateChecks+counts.edgeChecks,"no_overwrite":true]
default:throw ModelError("unknown operation")}
receipt["producer"]="swift";receipt["mode"]=mode;receipt["legacy_encoder_calls"]=counts.legacy;receipt["codec_into_calls"]=counts.encodes
print(String(decoding:try JSONSerialization.data(withJSONObject:receipt,options:[.sortedKeys]),as:UTF8.self))
