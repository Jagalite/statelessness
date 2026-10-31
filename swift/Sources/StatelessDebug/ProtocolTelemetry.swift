import Foundation
import Statelessness

private func tn<T: BinaryInteger>(_ v: T) -> String { "u64:\(v)" }
private func tb(_ v: Bool) -> String { "bool:\(v)" }
private func tv(_ value: String) -> String { value.prefix(1).uppercased() + value.dropFirst() }
// Keep the reference protocol's insertion order while retaining dictionary
// access for existing host bindings. Optional fields are included only when present.
private func telemetryOrder(_ fields: [String:String], command: ProtocolHostCommand) -> [String] {
    var order: [String] = []
    var seen: Set<String> = []
    func add(_ keys: [String]) { for key in keys where fields[key] != nil && seen.insert(key).inserted { order.append(key) } }
    func group(_ prefix: String, _ suffixes: [String]) { add(suffixes.map { prefix + "." + $0 }) }
    func indices(_ prefix: String) -> [Int] { Array(Set(fields.keys.compactMap { key in guard key.hasPrefix(prefix + ".") else { return nil }; return Int(key.dropFirst(prefix.count + 1).split(separator:".").first ?? "") })).sorted() }
    let label = ["operation.id","component.id","worker_pool.id","outcome","measurement_origin","debugger_affected"]
    func instantOrder(_ prefix: String) { add([prefix + ".clock_domain",prefix + ".nanos",prefix]) }
    func effect() {
        add(["effect.run","effect.epoch","effect.serial","effect.origin.run","effect.origin.epoch","effect.origin.machine","effect.origin.transition","effect.origin.output_index","effect.origin"])
        group("effect",Array(label.dropLast())); add(["effect.labels.debugger_affected"])
        group("effect",["complete","admission","resolution","settled","running_attempts","deliveries_observed","cancellation_requested","cancellation_acknowledged","debugger_affected","abandoned"])
        instantOrder("effect.requested")
    }
    switch command {
    case .metricCatalog:
        add(["catalog.total","catalog.next_offset"])
        for i in indices("catalog") { group("catalog.\(i)",["family","name","kind","unit","population","reset"]) }
    case .metricSnapshot, .metricWindow:
        add(["metrics.handle","metrics.revision","metrics.epoch","metrics.complete","metrics.temporality","metrics.gauge_scope","metrics.histogram_schema","metrics.histogram_storage_unit","metrics.series.total","metrics.series.offset","metrics.series.next_offset"])
        instantOrder("metrics.start"); instantOrder("metrics.end")
        add(["metrics.limits.series","metrics.limits.bytes","metrics.health.rejected_series","metrics.health.invalid_measurements","metrics.health.omitted_measurements","metrics.series.returned"])
        for i in indices("metrics.series") {
            let p = "metrics.series.\(i)"
            group(p,["name","kind","unit","population"]); group(p,label)
            group(p,["operation.name","component.name","worker_pool.name","value","gauge_scope","count","sum_ns"])
            for b in indices(p + ".buckets") { group(p + ".buckets.\(b)",["count","upper_bound_ns"]) }
            group(p,["quantile_method"])
            for q in ["p50","p95","p99"] { group(p,[q + ".upper_bound_ns",q + ".observations",q + ".low_sample_warning",q]) }
        }
    case .effectDetails: effect()
    case .effectTimeline:
        effect(); add(["timeline.events.total","timeline.timings.total","timeline.truncated"])
        for i in indices("timeline.events") { group("timeline.events.\(i)",["sequence","debugger_affected","clock_domain","nanos","admission","attempt","delay_ns","outcome","delivery","disposition","kind"]) }
        for i in indices("timeline.timings") { group("timeline.timings.\(i)",["family","outcome","duration_ns","attempt","delivery","debugger_affected"]) }
    case .telemetryHealth:
        group("telemetry",["destructor_hook_losses","callback_faults","pause_interval_evictions","detail_evictions","detail_rejections","detail_event_drops","duplicate_observations","duplicate_deliveries","invalid_lifecycle","abandoned","clock_failures","debugger_affected_measurements","active_effect_handles","retained_detail_records","retained_detail_bytes","metric.rejected_series","metric.invalid_labels","metric.arithmetic_errors","metric.omitted_measurements","metric.invalid_measurements","metric.imported_ignored","metric.snapshots_evicted","metric.snapshot_rejections","unobserved_native_debugger_interference_possible"])
        add(["pauses.total","pauses.truncated"])
        for i in indices("pauses") { let p = "pauses.\(i)"; group(p,["generation"]); instantOrder(p + ".start"); instantOrder(p + ".end") }
        add(["metric_windows.total"]); for i in indices("metric_windows") { add(["metric_windows.\(i)"]) }
    default: break
    }
    return order
}
private func orderedTelemetry(_ fields: [String:String], _ command: ProtocolHostCommand, commit: @escaping () throws -> Void = {}) throws -> PreparedProtocolResponse {
    let order = telemetryOrder(fields,command:command)
    guard order.count == fields.count else { throw ProtocolError.invalidInput }
    return try PreparedProtocolResponse(orderedFields: order.map { ($0,fields[$0]!) },commit:commit)
}
private func labels(_ value: MetricLabels,prefix: String,fields: inout [String:String]) {
    fields[prefix+".operation.id"] = tn(value.operation); fields[prefix+".component.id"] = tn(value.component); fields[prefix+".worker_pool.id"] = tn(value.workerPool)
    fields[prefix+".outcome"] = "enum:"+tv(value.outcome.rawValue); fields[prefix+".measurement_origin"] = "enum:"+tv(value.origin.rawValue); fields[prefix+".debugger_affected"] = tb(value.debuggerAffected)
}
private func instant(_ value: LocalInstant?,prefix: String,unknown: String = "unknown:clock",fields: inout [String:String]) {
    if let value { fields[prefix+".clock_domain"] = tn(value.domain); fields[prefix+".nanos"] = tn(value.nanos) } else { fields[prefix] = unknown }
}
private func telemetryFields(_ observer: EffectObserver,limit: Int) -> [String:String] {
    let h = observer.health, m = h.metric
    var f: [String:String] = [:]
    for (key,value) in [("destructor_hook_losses",h.destructorHookLosses),("callback_faults",h.callbackFaults),("pause_interval_evictions",h.pauseIntervalEvictions),("detail_evictions",h.detailEvictions),("detail_rejections",h.detailRejections),("detail_event_drops",h.detailEventDrops),("duplicate_observations",h.duplicateObservations),("duplicate_deliveries",h.duplicateDeliveries),("invalid_lifecycle",h.invalidLifecycle),("abandoned",h.abandoned),("clock_failures",h.clockFailures),("debugger_affected_measurements",h.debuggerAffectedMeasurements),("active_effect_handles",h.activeEffectHandles),("retained_detail_records",h.retainedDetailRecords),("retained_detail_bytes",h.retainedDetailBytes),("metric.rejected_series",m.rejectedSeries),("metric.invalid_labels",m.invalidLabels),("metric.arithmetic_errors",m.arithmeticErrors),("metric.omitted_measurements",m.omittedMeasurements),("metric.invalid_measurements",m.invalidMeasurements),("metric.imported_ignored",m.importedIgnored),("metric.snapshots_evicted",m.snapshotsEvicted),("metric.snapshot_rejections",m.snapshotRejections)] { f["telemetry."+key] = tn(value) }
    f["telemetry.unobserved_native_debugger_interference_possible"] = tb(h.unobservedNativeDebuggerInterferencePossible)
    let pauses = observer.pauseIntervals; f["pauses.total"] = tn(pauses.count); f["pauses.truncated"] = tb(pauses.count > limit)
    for (i,p) in pauses.prefix(limit).enumerated() { let key = "pauses.\(i)"; f[key+".generation"] = tn(p.generation); instant(p.start,prefix:key+".start",unknown:"unknown:clock_or_open_interval",fields:&f); instant(p.end,prefix:key+".end",unknown:"unknown:clock_or_open_interval",fields:&f) }
    let windows = observer.availableWindows; f["metric_windows.total"] = tn(windows.count)
    for (i,revision) in windows.prefix(limit).enumerated() { f["metric_windows.\(i)"] = tn(revision) }
    return f
}
private func effectFields(_ effect: EffectDetails) -> [String:String] {
    var f = ["effect.run":tn(effect.id.run),"effect.epoch":tn(effect.id.epoch),"effect.serial":tn(effect.id.serial),"effect.complete":tb(effect.complete),"effect.admission":effect.admission.map { $0 ? "enum:Accepted" : "enum:Rejected" } ?? "unknown","effect.resolution":effect.resolution.map { "enum:"+tv(String(describing:$0)) } ?? "unknown","effect.settled":tb(effect.settled),"effect.running_attempts":tn(effect.runningAttempts),"effect.deliveries_observed":tn(effect.deliveriesObserved),"effect.cancellation_requested":tb(effect.cancellationRequested),"effect.cancellation_acknowledged":tb(effect.cancellationAcknowledged),"effect.debugger_affected":tb(effect.debuggerAffected),"effect.abandoned":tb(effect.abandoned)]
    labels(effect.labels,prefix:"effect",fields:&f)
    f["effect.labels.debugger_affected"] = tb(effect.labels.debuggerAffected)
    f["effect.debugger_affected"] = tb(effect.debuggerAffected)
    if let o = effect.origin { f["effect.origin.run"] = tn(o.run); f["effect.origin.epoch"] = tn(o.epoch); f["effect.origin.machine"] = tn(o.machine); f["effect.origin.transition"] = tn(o.transitionSequence); f["effect.origin.output_index"] = tn(o.outputIndex) } else { f["effect.origin"] = "unknown" }
    instant(effect.requested,prefix:"effect.requested",fields:&f); return f
}
private func lifecycle(_ value: EffectLifecycleKind,prefix: String,fields: inout [String:String]) {
    let kind: String
    switch value {
    case .requested: kind = "requested"
    case let .admission(accepted): kind = "admission"; fields[prefix+".admission"] = accepted ? "enum:Accepted" : "enum:Rejected"
    case let .attemptCreated(id): kind = "attempt_created"; fields[prefix+".attempt"] = tn(id.serial)
    case let .ready(id): kind = "ready"; fields[prefix+".attempt"] = tn(id.serial)
    case let .readyRemoved(id): kind = "ready_removed"; fields[prefix+".attempt"] = tn(id.serial)
    case let .retryScheduled(id,delay): kind = "retry_scheduled"; fields[prefix+".attempt"] = tn(id.serial); fields[prefix+".delay_ns"] = tn(delay)
    case let .attemptStarted(id): kind = "attempt_started"; fields[prefix+".attempt"] = tn(id.serial)
    case let .attemptFinished(id,outcome): kind = "attempt_finished"; fields[prefix+".attempt"] = tn(id.serial); fields[prefix+".outcome"] = "enum:"+tv(String(describing:outcome))
    case let .resolved(outcome): kind = "resolved"; fields[prefix+".outcome"] = "enum:"+tv(String(describing:outcome))
    case .cancellationRequested: kind = "cancellation_requested"
    case .cancellationAcknowledged: kind = "cancellation_acknowledged"
    case let .publicationReserved(id): kind = "publication_reserved"; fields[prefix+".delivery"] = tn(id.serial)
    case let .publicationFailed(id): kind = "publication_failed"; fields[prefix+".delivery"] = tn(id.serial)
    case let .deliveryBegun(id): kind = "delivery_begun"; fields[prefix+".delivery"] = tn(id.serial)
    case let .deliveryObserved(id,disposition): kind = "delivery_observed"; fields[prefix+".delivery"] = tn(id.serial); fields[prefix+".disposition"] = "enum:"+tv(String(describing:disposition))
    case .settled: kind = "settled"
    case let .abandoned(id): kind = "abandoned"; fields[prefix+".attempt"] = id.map {tn($0.serial)} ?? "none"
    }
    fields[prefix+".kind"] = "enum:"+kind
}
private func metricFields(_ snapshot: MetricSnapshot,handle: UInt64,offset: Int,limit: Int) -> [String:String] {
    let families = MetricFamily.allCases
    let outcomes: [MetricOutcome] = [.none,.accepted,.rejected,.success,.failure,.cancelled,.abandoned,.ignored]
    let origins: [MeasurementOrigin] = [.live,.debugControlledLive,.testClock,.replayHarness,.importedLive]
    let series = snapshot.series.sorted { a,b in
        let ak = a.key,bk = b.key, ai = families.firstIndex(of:ak.family)!,bi = families.firstIndex(of:bk.family)!
        if ai != bi { return ai < bi }
        let al = ak.labels,bl = bk.labels
        if al.operation != bl.operation { return al.operation < bl.operation }; if al.component != bl.component { return al.component < bl.component }; if al.workerPool != bl.workerPool { return al.workerPool < bl.workerPool }
        if al.outcome != bl.outcome { return outcomes.firstIndex(of:al.outcome)! < outcomes.firstIndex(of:bl.outcome)! }; if al.origin != bl.origin { return origins.firstIndex(of:al.origin)! < origins.firstIndex(of:bl.origin)! }; return !al.debuggerAffected && bl.debuggerAffected
    }
    let end = min(series.count,offset.addingReportingOverflow(limit).overflow ? Int.max : offset+limit)
    var f = ["metrics.handle":tn(handle),"metrics.revision":tn(snapshot.revision),"metrics.epoch":tn(snapshot.epoch),"metrics.complete":tb(snapshot.complete),"metrics.temporality":"enum:"+tv(String(describing:snapshot.temporality)),"metrics.gauge_scope":"enum:"+tv(String(describing:snapshot.gaugeScope)),"metrics.histogram_schema":tn(snapshot.schema.id),"metrics.histogram_storage_unit":"enum:nanoseconds","metrics.series.total":tn(series.count),"metrics.series.offset":tn(offset),"metrics.series.next_offset":offset < series.count && end < series.count ? tn(end) : "none","metrics.limits.series":tn(snapshot.limits.maxSeries),"metrics.limits.bytes":tn(snapshot.limits.maxBytes),"metrics.health.rejected_series":tn(snapshot.health.rejectedSeries),"metrics.health.invalid_measurements":tn(snapshot.health.invalidMeasurements),"metrics.health.omitted_measurements":tn(snapshot.health.omittedMeasurements),"metrics.series.returned":tn(max(0,end-offset))]
    instant(snapshot.start,prefix:"metrics.start",fields:&f); instant(snapshot.end,prefix:"metrics.end",fields:&f)
    if offset < series.count { for (i,item) in series[offset..<end].enumerated() {
        let key = "metrics.series.\(i)", descriptor = item.key.family.descriptor, l = item.key.labels
        f[key+".name"] = "string:"+descriptor.name; f[key+".kind"] = "enum:"+tv(String(describing:descriptor.kind)); f[key+".unit"] = "string:"+descriptor.unit; f[key+".population"] = "string:"+descriptor.population; labels(l,prefix:key,fields:&f)
        if Int(l.operation) < snapshot.labels.operations.count { f[key+".operation.name"] = "string:"+snapshot.labels.operations[Int(l.operation)] }; if Int(l.component) < snapshot.labels.components.count { f[key+".component.name"] = "string:"+snapshot.labels.components[Int(l.component)] }; if Int(l.workerPool) < snapshot.labels.workerPools.count { f[key+".worker_pool.name"] = "string:"+snapshot.labels.workerPools[Int(l.workerPool)] }
        switch item.value {
        case let .counter(value): f[key+".value"] = tn(value)
        case let .gauge(value): f[key+".value"] = tn(value); f[key+".gauge_scope"] = "enum:"+tv(String(describing:snapshot.gaugeScopes[item.key] ?? .unknown))
        case let .histogram(h):
            f[key+".count"] = tn(h.count); f[key+".sum_ns"] = "u128:\(h.sumNS)"
            for index in 0...snapshot.schema.finiteBoundsNS.count { f[key+".buckets.\(index).count"] = tn(h.buckets[index]); f[key+".buckets.\(index).upper_bound_ns"] = index < snapshot.schema.finiteBoundsNS.count ? tn(snapshot.schema.finiteBoundsNS[index]) : "infinity" }
            f[key+".quantile_method"] = "enum:histogram_bucket_upper_bound"
            for (name,numerator) in [("p50",UInt64(50)),("p95",95),("p99",99)] { if let q = h.quantile(schema:snapshot.schema,numerator:numerator,denominator:100) { f[key+".\(name).upper_bound_ns"] = q.upperBoundNS.map(tn) ?? "infinity"; f[key+".\(name).observations"] = tn(q.observations); f[key+".\(name).low_sample_warning"] = tb(q.lowSampleWarning) } else { f[key+".\(name)"] = "unavailable:no_observations" } }
        }
    } }
    return f
}
private final class TelemetryCapture { var current: (UInt64,MetricSnapshot)? }

/// Combine disjoint host capabilities without overriding their ownership rules.
public func combineProtocolHosts(_ hosts: [ProtocolHostBindings]) throws -> ProtocolHostBindings {
    var commands = Set<String>(), sinks = Set<UInt64>(), profiles = Set<UInt64>(), shared = Set<String>()
    for host in hosts { guard commands.isDisjoint(with:host.commands) else { throw ProtocolError.invalidInput }; commands.formUnion(host.commands); sinks.formUnion(host.exposedProbeSinks); profiles.formUnion(host.approvedProbeProfiles); shared.formUnion(host.sharedHandleCommands) }
    return ProtocolHostBindings(commands:commands,exposedProbeSinks:sinks,approvedProbeProfiles:profiles,sharedHandleCommands:shared,reset:{ hosts.forEach { $0.reset() } },prepareWithHandle:{ command,reserved in
        let selected: String
        switch command {
        case .probeStatus: selected = "probe_status"; case .probeConfigure: selected = "probe_configure"; case .probeEvents: selected = "probe_events"
        case .metricCatalog: selected = "metric_catalog"; case .metricSnapshot: selected = "metric_snapshot"; case .metricWindow: selected = "metric_window"; case .effectDetails: selected = "effect_details"; case .effectTimeline: selected = "effect_timeline"; case .telemetryHealth: selected = "telemetry_health"
        case .watch: selected = "watch"; case .unwatch: selected = "unwatch"; case .watchStatus: selected = "watch_status"; case .watchCurrent: selected = "watch_current"
        case .inspect: selected = "inspect"; case .checks: selected = "checks"; case .outputs: selected = "outputs"; case .exportTrace: selected = "export_trace"; case .inputs: selected = "inputs"; case .select: selected = "select"; case .events: selected = "events"
        }
        guard let host = hosts.first(where:{$0.commands.contains(selected)}) else { throw ProtocolError.unsupported }
        if let prepare = host.prepareWithHandle { return try prepare(command,reserved) }
        return try host.prepare(command)
    })
}

/// Explicit observer-backed telemetry. Prepares all mutating snapshot work before
/// the adapter's response preflight and commits it only after that preflight.
public func telemetryProtocolHost(observer: EffectObserver,limits: ProtocolLimits = ProtocolLimits()) -> ProtocolHostBindings {
    let cache = TelemetryCapture()
    return ProtocolHostBindings(commands:["metric_catalog","metric_snapshot","metric_window","effect_details","effect_timeline","telemetry_health"],sharedHandleCommands:["metric_snapshot","metric_window"],reset:{ cache.current = nil },prepareWithHandle:{ command,reserved in
        switch command {
        case let .metricCatalog(offset,limit):
            guard let start = Int(exactly:offset), let count = Int(exactly:limit), !start.addingReportingOverflow(count).overflow else { throw ProtocolError.limit }
            let catalog = observer.metricCatalog, end = min(catalog.count,start+count)
            var fields = ["catalog.total":tn(catalog.count),"catalog.next_offset":end < catalog.count ? tn(end) : "none"]
            if start < catalog.count { for (i,item) in catalog[start..<end].enumerated() { let key = "catalog.\(i)"; fields[key+".family"] = "enum:"+tv(item.family.rawValue); fields[key+".name"] = "string:"+item.name; fields[key+".kind"] = "enum:"+tv(String(describing:item.kind)); fields[key+".unit"] = "string:"+item.unit; fields[key+".population"] = "string:"+item.population; fields[key+".reset"] = "string:"+item.reset } }
            return try orderedTelemetry(fields,command)
        case let .metricSnapshot(handle,offset,limit):
            guard let start = Int(exactly:offset),let count = Int(exactly:limit) else { throw ProtocolError.limit }
            if let handle { guard let existing = cache.current,existing.0 == handle else { throw ProtocolError.staleHandle }; return try orderedTelemetry(metricFields(existing.1,handle:handle,offset:start,limit:count),command) }
            guard start == 0 else { throw ProtocolError.invalidInput }; guard let handle = reserved else { throw ProtocolError.invalidInput }
            let (snapshot,commit) = try observer.prepareProtocolSnapshot()
            guard snapshot.estimatedBytes <= limits.maxTelemetrySnapshotBytes else { throw ProtocolError.limit }
            let fields = metricFields(snapshot,handle:handle,offset:start,limit:count)
            return try orderedTelemetry(fields,command,commit:{ try commit(); cache.current = (handle,snapshot) })
        case let .metricWindow(from,to,limit):
            guard let count = Int(exactly:limit),let handle = reserved else { throw ProtocolError.limit }
            let snapshot: MetricSnapshot
            do { snapshot = try observer.metricWindow(from:from,to:to) } catch { throw ProtocolError.staleHandle }
            guard snapshot.estimatedBytes <= limits.maxTelemetrySnapshotBytes else { throw ProtocolError.limit }
            return try orderedTelemetry(metricFields(snapshot,handle:handle,offset:0,limit:count),command,commit:{ cache.current = (handle,snapshot) })
        case let .effectDetails(run,epoch,serial), let .effectTimeline(run,epoch,serial,_):
            guard run == observer.options.run,epoch == observer.options.epoch else { throw ProtocolError.wrongEpoch }
            let id = EffectID(run:run,epoch:epoch,serial:serial)
            guard let detail = observer.effectDetails(id) else { throw ProtocolError.staleHandle }
            var fields = effectFields(detail)
            if case let .effectTimeline(_,_,_,limit) = command {
                guard let count = Int(exactly:limit) else { throw ProtocolError.limit }
                fields["timeline.events.total"] = tn(detail.events.count); fields["timeline.timings.total"] = tn(detail.timings.count); fields["timeline.truncated"] = tb(detail.events.count > count || detail.timings.count > count)
                for (i,event) in detail.events.prefix(count).enumerated() { let key = "timeline.events.\(i)"; fields[key+".sequence"] = tn(event.sequence); fields[key+".debugger_affected"] = tb(event.debuggerAffected); if let time = event.time { instant(time,prefix:key,fields:&fields) }; lifecycle(event.detail,prefix:key,fields:&fields) }
                for (i,timing) in detail.timings.prefix(count).enumerated() { let key = "timeline.timings.\(i)"; fields[key+".family"] = "enum:"+tv(timing.family.rawValue); fields[key+".outcome"] = "enum:"+tv(timing.outcome.rawValue); switch timing.durationNS { case let .success(n): fields[key+".duration_ns"] = tn(n); case let .failure(e): fields[key+".duration_ns"] = "unknown:"+tv(String(describing:e)) }; fields[key+".attempt"] = timing.attempt.map {tn($0.serial)} ?? "none"; fields[key+".delivery"] = timing.delivery.map {tn($0.serial)} ?? "none"; fields[key+".debugger_affected"] = tb(timing.debuggerAffected) }
            }
            return try orderedTelemetry(fields,command)
        case .telemetryHealth: return try orderedTelemetry(telemetryFields(observer,limit:limits.maxPageSize),command)
        default: throw ProtocolError.unsupported
        }
    })
}
