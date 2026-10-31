import Foundation

/// Host-approved diagnostic profile; it does not acknowledge a producer.
public struct ProtocolProbeProfile {
    public let id: UInt64
    public let label: String
    public let subscription: ProbeSubscription
    public init(id: UInt64, label: String, subscription: ProbeSubscription) { self.id = id; self.label = label; self.subscription = subscription }
}

private final class ProbeSelection {
    var revision: UInt64?
    var ids: [UInt64] = []
}
private func probeVariant(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }
private func probeNumber<T: BinaryInteger>(_ n: T) -> String { "u64:\(n)" }
private func probeBool(_ b: Bool) -> String { "bool:\(b)" }
func probeSegment(_ value: PathSegment) -> String {
    switch value {
    case let .field(s): return "field:" + s.value
    case let .variant(s): return "variant:" + s.value
    case let .index(i): return "index:\(i)"
    case let .mapKey(s): return "key.string:" + s.value
    case let .typedMapKey(k):
        switch k {
        case let .string(s): return "key.string:" + s.value
        case let .integer(kind,decimal): return "key.\(kind.value):\(decimal.value)"
        case let .bool(b): return "key.bool:\(b)"
        case let .character(c): return "key.char:" + (UnicodeScalar(c).map(String.init) ?? "�")
        }
    }
}
func probeNode(_ node: InspectNode, prefix: String, depth: Int, fields: inout [String:String]) throws {
    guard depth <= 32 else { throw ProtocolError.limit }
    switch node.completeness {
    case .complete: fields[prefix+".completeness"] = "enum:Complete"
    case let .partial(reason): fields[prefix+".completeness"] = "enum:Partial(\(probeVariant(reason.rawValue)))"
    }
    fields[prefix+".child_count"] = node.childCount.map(probeNumber) ?? "none"
    if let page = node.page {
        fields[prefix+".page.offset"] = probeNumber(page.offset); fields[prefix+".page.returned"] = probeNumber(page.returned); fields[prefix+".page.total"] = probeNumber(page.total); fields[prefix+".page.next"] = page.nextOffset.map(probeNumber) ?? "none"
    }
    let kind: String, value: String
    switch node.kind {
    case .scalar(.unit): kind = "unit"; value = "unit"
    case let .scalar(.bool(b)): kind = "bool"; value = probeBool(b)
    case let .scalar(.character(c)): kind = "char"; value = "char:" + (UnicodeScalar(c).map(String.init) ?? "�")
    case let .scalar(.integer(k,d)): kind = "integer"; value = "\(k.value):\(d.value)"
    case let .scalar(.float(k,d,b)): kind = "float"; value = "\(k.value):\(d.value)"; fields[prefix+".bits"] = probeNumber(b)
    case let .string(s,n): kind = "string"; value = "string:" + s.value; fields[prefix+".total_bytes"] = probeNumber(n)
    case let .bytes(b,n): kind = "bytes"; value = "bytes:" + b.map {String(format:"%02x",$0)}.joined(); fields[prefix+".total_bytes"] = probeNumber(n)
    case let .object(s): kind = "object"; value = "string:" + s.value
    case let .enumeration(n,id,label): kind = "enum"; value = "string:" + n.value; fields[prefix+".variant_id"] = "string:" + id.value; fields[prefix+".variant_label"] = "string:" + label.value
    case let .sequence(s): kind = "sequence"; value = "string:" + s.value
    case let .map(order): kind = "map"; value = order == .stable ? "enum:Stable" : "enum:Unstable"
    case let .opaque(s): kind = "opaque"; value = "string:" + s.value
    case .redacted: kind = "redacted"; value = "none"
    case .unavailable: kind = "unavailable"; value = "none"
    case let .truncated(reason): kind = "truncated"; value = "enum:" + probeVariant(reason.rawValue)
    }
    fields[prefix+".kind"] = "enum:" + kind; fields[prefix+".value"] = value; fields[prefix+".children.returned"] = probeNumber(node.children.count)
    for (index,child) in node.children.enumerated() {
        let key = prefix+".children.\(index)"; fields[key+".segment"] = probeSegment(child.segment); fields[key+".label"] = "string:" + child.label.value
        try probeNode(child.node,prefix:key+".node",depth:depth+1,fields:&fields)
    }
}
/// Matches the reference node field emission order, including nested rows.
func probeNodeOrder(_ node: InspectNode, prefix: String) -> [String] {
    var result = [prefix+".completeness",prefix+".child_count"]
    if node.page != nil { result += [prefix+".page.offset",prefix+".page.returned",prefix+".page.total",prefix+".page.next"] }
    switch node.kind {
    case .scalar(.float): result.append(prefix+".bits")
    case .string,.bytes: result.append(prefix+".total_bytes")
    case .enumeration: result += [prefix+".variant_id",prefix+".variant_label"]
    default: break
    }
    result += [prefix+".kind",prefix+".value",prefix+".children.returned"]
    for (index,child) in node.children.enumerated() {
        let key = prefix+".children.\(index)"
        result += [key+".segment",key+".label"]
        result += probeNodeOrder(child.node,prefix:key+".node")
    }
    return result
}
private func probeHealth(_ health: DiagnosticHealth, prefix: String, fields: inout [String:String]) {
    for (name,value) in [("captured",health.captured),("filtered",health.filtered),("sampled",health.sampled),("dropped",health.dropped),("truncated",health.truncated),("exporter_failed",health.exporterFaults),("abandoned",health.abandoned),("inspection_failed",health.inspectionErrors),("baseline_evicted",health.baselineEvicted),("observation_gaps",health.observationGaps)] { fields[prefix+".health."+name] = probeNumber(value) }
}

/// Bind an explicit hub to probe protocol commands. The adapter performs its
/// own response preflight before invoking the returned commit closures.
public func diagnosticProbeHost(hub: DiagnosticHub, profiles: [ProtocolProbeProfile], sinks: [UInt64], limits: ProtocolLimits = ProtocolLimits()) throws -> ProtocolHostBindings {
    try hub.assertAvailable()
    guard limits.maxPageSize >= 0,limits.maxResponseFields >= 0,limits.maxFieldBytes >= 0 else { throw ProtocolError.invalidInput }
    guard hub.captureRevision == 0, profiles.count <= limits.maxProbeProfiles, profiles.count <= hub.limits.subscriptions, sinks.count <= hub.limits.sinks, Set(profiles.map(\.id)).count == profiles.count, Set(sinks).count == sinks.count, profiles.allSatisfy({$0.id > 0 && $0.label.utf8.count <= 128 && $0.label.utf8.count <= limits.maxFieldBytes-min(7,limits.maxFieldBytes)}), sinks.allSatisfy({hub.queued(sink:$0) != nil}) else { throw ProtocolError.invalidInput }
    do { try hub.validateSelected(expectedRevision:0,subscriptions:profiles.map(\.subscription)) }
    catch let error as DiagnosticError {
        switch error { case .unauthorized: throw ProtocolError.unauthorized; case .capacity: throw ProtocolError.limit; default: throw ProtocolError.invalidInput }
    }
    // Bound the host-owned catalogue before the closure retains it. The hub's
    // validation has already bounded paths, labels, selectors and scalar values.
    guard limits.maxCandidateBytes >= 0 else { throw ProtocolError.limit }
    var remaining = limits.maxCandidateBytes
    func charge(_ amount: Int) throws { guard amount >= 0, amount <= remaining else { throw ProtocolError.limit }; remaining -= amount }
    for profile in profiles {
        let sub = profile.subscription, selector = sub.selector
        try charge(256); try charge(profile.label.utf8.count); try charge(sub.site.utf8.count)
        for label in [selector.model,selector.machine,selector.entity,selector.correlation,selector.inputVariant,selector.outputVariant,selector.property,selector.effectKind,selector.sourceSite].compactMap({$0}) { try charge(label.utf8.count) }
        for (key,value) in selector.labels { try charge(64); try charge(key.value.utf8.count); try charge(value.value.utf8.count) }
        for segment in sub.path { try charge(64); try charge(probeSegment(segment).utf8.count) }
        if case let .equals(value) = sub.trigger { try charge(try diagnosticNodeJSON(InspectNode(kind:.scalar(value)),maximumBytes:hub.limits.configBytes).utf8.count) }
    }
    let selection = ProbeSelection()
    return ProtocolHostBindings(commands:["probe_status","probe_configure","probe_events"],exposedProbeSinks:Set(sinks),approvedProbeProfiles:Set(profiles.map(\.id))) { command in
        try hub.assertAvailable()
        switch command {
        case let .probeConfigure(expected,ids):
            guard expected == hub.captureRevision else { throw ProtocolError.staleConfiguration }
            let configs = try ids.map { id -> ProbeSubscription in guard let p = profiles.first(where: {$0.id == id}) else { throw ProtocolError.unauthorized }; return p.subscription }
            do { try hub.validateSelected(expectedRevision:expected,subscriptions:configs) } catch let error as DiagnosticError {
                switch error { case .staleRevision: throw ProtocolError.staleConfiguration; case .unauthorized: throw ProtocolError.unauthorized; case .capacity: throw ProtocolError.limit; default: throw ProtocolError.invalidInput }
            }
            let total = hub.producerCount, shown = hub.producerIDs(limit:min(limits.maxPageSize,limits.maxResponseFields-min(4,limits.maxResponseFields)))
            var fields = ["probes.capture_revision":probeNumber(expected+1),"probes.selected_count":probeNumber(ids.count),"probes.pending_producers.total":probeNumber(total),"probes.pending_producers.truncated":probeBool(shown.count < total)]
            for (i,id) in shown.enumerated() { fields["probes.pending_producers.\(i)"] = probeNumber(id) }
            let order = ["probes.capture_revision","probes.selected_count","probes.pending_producers.total","probes.pending_producers.truncated"] + shown.indices.map { "probes.pending_producers.\($0)" }
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } },fit:{ fields, fits in
                if fits(fields) { return fields }
                func prefix(_ length: Int) -> [String:String] {
                    var result = fields
                    for index in length..<shown.count { result.removeValue(forKey:"probes.pending_producers.\(index)") }
                    if length < total { result["probes.pending_producers.truncated"] = "bool:true" }
                    return result
                }
                guard fits(prefix(0)) else { throw ProtocolError.limit }
                var low = 0,high = shown.count
                while low < high {
                    let middle = low+(high-low+1)/2
                    if fits(prefix(middle)) { low = middle } else { high = middle-1 }
                }
                return prefix(low)
            },commit:{ _ = try hub.configureSelected(expectedRevision:expected,subscriptions:configs); selection.revision = expected+1; selection.ids = ids })
        case let .probeStatus(offset,limit):
            guard offset <= UInt64(Int.max), limit <= UInt64(Int.max), !offset.addingReportingOverflow(limit).overflow else { throw ProtocolError.limit }
            let start = Int(offset), end = Int(min(offset+limit,UInt64(Int.max))), producerCount = hub.producerCount
            let producers = hub.producerStatusPage(offset:start,limit:end-start)
            let total = max(producerCount,profiles.count,sinks.count)
            var fields = ["probes.capture_revision":probeNumber(hub.captureRevision),"probes.producers.total":probeNumber(producerCount),"probes.producers.truncated":probeBool(end < producerCount),"probes.pending_producers.total":probeNumber(hub.pendingProducerCount),"probes.status_live":"bool:true","probes.offset":probeNumber(offset),"probes.next_offset":end < total ? probeNumber(end) : "none","probes.profiles.total":probeNumber(profiles.count),"probes.profiles.truncated":probeBool(end < profiles.count),"probes.selected_profiles_known":probeBool(selection.revision == hub.captureRevision),"probes.sinks.total":probeNumber(sinks.count)]
            for (i,p) in producers.enumerated() { let key = "probes.producers.\(i)"; fields[key+".id"] = probeNumber(p.producer); fields[key+".epoch"] = probeNumber(p.epoch); fields[key+".capture_revision"] = probeNumber(p.captureRevision); fields[key+".effective_transition"] = probeNumber(p.effectiveTransition); fields[key+".pending"] = probeBool(p.captureRevision != hub.captureRevision) }
            if start < profiles.count { for (i,p) in profiles[start..<min(end,profiles.count)].enumerated() { fields["probes.profiles.\(i).id"] = probeNumber(p.id); fields["probes.profiles.\(i).label"] = "string:" + p.label } }
            if selection.revision == hub.captureRevision && start < selection.ids.count { for (i,id) in selection.ids[start..<min(end,selection.ids.count)].enumerated() { fields["probes.selected.\(i)"] = probeNumber(id) } }
            if start < sinks.count { for (i,id) in sinks[start..<min(end,sinks.count)].enumerated() { let key = "probes.sinks.\(i)", q = hub.queued(sink:id)!; fields[key+".id"] = probeNumber(id); fields[key+".queued_records"] = probeNumber(q.records); fields[key+".queued_bytes"] = probeNumber(q.bytes); probeHealth(hub.sinkHealth(id)!,prefix:key,fields:&fields) } }
            var order = ["probes.capture_revision","probes.producers.total","probes.producers.truncated","probes.pending_producers.total"]
            for i in producers.indices { order += ["id","epoch","capture_revision","effective_transition","pending"].map { "probes.producers.\(i)."+$0 } }
            order += ["probes.status_live","probes.offset","probes.next_offset","probes.profiles.total","probes.profiles.truncated"]
            if start < profiles.count { for i in 0..<min(end,profiles.count)-start { order += ["probes.profiles.\(i).id","probes.profiles.\(i).label"] } }
            order.append("probes.selected_profiles_known")
            if selection.revision == hub.captureRevision && start < selection.ids.count { for i in 0..<min(end,selection.ids.count)-start { order.append("probes.selected.\(i)") } }
            order.append("probes.sinks.total")
            if start < sinks.count { for i in 0..<min(end,sinks.count)-start {
                let key = "probes.sinks.\(i)"
                order += ["id","queued_records","queued_bytes"].map { key+"."+$0 }
                order += ["captured","filtered","sampled","dropped","truncated","exporter_failed","abandoned","inspection_failed","baseline_evicted","observation_gaps"].map { key+".health."+$0 }
            } }
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } })
        case let .probeEvents(sink,limit,payload):
            guard sinks.contains(sink), let count = Int(exactly:limit), let health = hub.sinkHealth(sink) else { throw ProtocolError.unauthorized }
            let events = try hub.peekEvents(sink:sink,maximum:count)
            var fields = ["probes.sink":probeNumber(sink),"probes.capture_revision":probeNumber(hub.captureRevision),"probes.sink.health.dropped":probeNumber(health.dropped),"probes.sink.health.exporter_failed":probeNumber(health.exporterFaults)]
            for (i,event) in events.enumerated() {
                let key = "probes.events.\(i)"
                for (name,value) in [("schema_version",UInt64(event.schemaVersion)),("run",event.runID),("producer",event.producer),("epoch",event.epoch),("producer_sequence",event.producerSequence),("capture_revision",event.captureRevision),("transition",event.transition)] { fields[key+"."+name] = probeNumber(value) }
                fields[key+".origin"] = "enum:" + probeVariant(event.origin.rawValue); fields[key+".severity"] = "enum:" + probeVariant(String(describing:event.severity)); fields[key+".kind"] = "enum:" + probeVariant(event.kind.rawValue); fields[key+".baseline"] = probeBool(event.baseline); fields[key+".baseline_reason"] = event.baselineReason.map {"enum:" + probeVariant($0.rawValue)} ?? "none"; fields[key+".change_unknown"] = probeBool(event.changeUnknown); fields[key+".incomplete_turn"] = probeBool(event.incompleteTurn)
                if payload {
                    fields[key+".site"] = "string:" + event.site; for (j,segment) in event.path.enumerated() { fields[key+".path.\(j)"] = probeSegment(segment) }
                    if let schema = event.displaySchema { fields[key+".display_schema"] = "string:" + schema.name; fields[key+".display_schema_version"] = probeNumber(schema.version) }
                    if let node = event.value { try probeNode(node,prefix:key+".payload",depth:0,fields:&fields) } else { fields[key+".payload"] = "none" }
                } else { fields[key+".metadata_omitted"] = "bool:true"; fields[key+".payload"] = "omitted:by_request" }
            }
            fields["probes.events.returned"] = probeNumber(events.count)
            var order = ["probes.sink","probes.capture_revision","probes.sink.health.dropped","probes.sink.health.exporter_failed"]
            for (i,event) in events.enumerated() {
                let key = "probes.events.\(i)"
                order += ["schema_version","run","producer","epoch","producer_sequence","capture_revision","transition","origin","severity","kind","baseline","baseline_reason","change_unknown","incomplete_turn"].map { key+"."+$0 }
                if payload {
                    order.append(key+".site")
                    order += event.path.indices.map { key+".path.\($0)" }
                    if event.displaySchema != nil { order += [key+".display_schema",key+".display_schema_version"] }
                    if let node = event.value { order += probeNodeOrder(node,prefix:key+".payload") } else { order.append(key+".payload") }
                } else { order += [key+".metadata_omitted",key+".payload"] }
            }
            order.append("probes.events.returned")
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } },commit:{ for _ in events { _ = try hub.pop(sink:sink) } })
        default: throw ProtocolError.unsupported
        }
    }
}
