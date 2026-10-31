import Foundation
import Statelessness

typealias Object = [String:Any]
func ownedJSON(_ data:Data)throws->Any {
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
func text(_ value:Any?) -> String { value as? String ?? "" }
func u64(_ value:Any?) -> UInt64 { if let value=value as? String {return UInt64(value)!};return (value as! NSNumber).uint64Value }
func merge(_ base:Object,_ overrides:Object)->Object {var result=base;for(k,v)in overrides {if let child=v as? Object {result[k]=merge(result[k] as! Object,child)}else{result[k]=v}};return result}
func checks(_ values:Any)->[Check] {(values as! [Object]).map{Check(text($0["id"]),CheckStatus(rawValue:text($0["status"]))!,text($0["details"]))}}
func property(_ value:ResearchRequiredProperty)->Object {["id":value.id,"phase":value.phase == .state ? "state":"transition"]}
func violations(_ values:[Research.ViolationWitness<String>])->[Object] {values.map{["property":property($0.property),"details":$0.details,"inputs":$0.inputs]}}
func reachability(_ value:Research.Reachability<String>?)->Any {guard let value else{return NSNull()};return ["states":value.states,"edges":String(value.edges),"violations":violations(value.violations),"observed_properties":value.observedProperties.map(property),"skipped_checks":String(value.skippedChecks)] as Object}
func normalized(_ value:Research.ExperimentReport<String>,_ inputProposal:Object,_ inputConfig:Object,_ inputMetadata:Object)->Object {
 let p=value.proposal,a=p.assumptions,m=value.model,c=value.config
 let proposal:Object=["name":p.name,"version":p.version,"build":p.build,"required_properties":p.requiredProperties.map(property),"assumptions":["established":a.established,"finite_domain":a.finiteDomain,"equivalence":a.equivalence,"effects":a.effects,"transition_properties":a.transitionProperties,"queue_order":a.queueOrder,"oracle_history":a.oracleHistory,"visible_timing":a.visibleTiming] as Object]
 let config:Object=["enabled":c.enabled,"max_states":String(c.maxStates),"max_edges":String(c.maxEdges),"max_equivalence_pairs":String(c.maxEquivalencePairs)]
 let metadata:Object=["name":m.name,"model_version":m.modelVersion,"properties_version":m.propertiesVersion,"codec_version":m.codecVersion,"build":m.build]
 return ["model":metadata,"proposal":proposal,"config":config,"status":value.status.rawValue,"declared_states":String(value.declaredStates),"materialized_edges":String(value.materializedEdges),"compared_pairs":String(value.comparedPairs),"exact":reachability(value.exact),"reduced":reachability(value.reduced),"counterexamples":value.counterexamples.map{["left_state":$0.leftState,"right_state":$0.rightState,"left_prefix":$0.leftPrefix,"right_prefix":$0.rightPrefix,"reason":$0.reason] as Object},"missing_violations":violations(value.missingViolations),"missing_properties":value.missingProperties.map(property),"limitation":value.limitation]
}
func runCase(_ defaults:Object,_ row:Object)->Object {
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
do {
 guard CommandLine.arguments.count==4,CommandLine.arguments[1]=="run" else{throw ModelError("usage: consumer run CORPUS OUTPUT")}
 let data=try Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[2])), fixture=try ownedJSON(data) as! Object
 let results=(fixture["cases"] as! [Object]).map{runCase(fixture["defaults"] as! Object,$0)}
 let output=try JSONSerialization.data(withJSONObject:["profile":"research-equivalence-v1","results":results],options:[.sortedKeys]);try output.write(to:URL(fileURLWithPath:CommandLine.arguments[3]))
 let receipt=try JSONSerialization.data(withJSONObject:["producer":"swift","mode":"run","case_count":results.count,"production_pruning_enabled":false]);print(String(decoding:receipt,as:UTF8.self))
}catch{FileHandle.standardError.write(Data("\(error)\n".utf8));exit(1)}
