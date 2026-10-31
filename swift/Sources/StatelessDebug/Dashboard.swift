import Statelessness

public let dashboardFamilies: [MetricFamily] = [.requests,.admissions,.attempts,.queueWait,.attemptElapsed,.deliveryLag,.completionProcessing,.settlement,.unresolved,.running,.readyDepth,.completionDepth,.scheduledBackoff,.interAttemptGap,.cancellationRequests,.cancellationAcknowledgements,.dropped,.invalidMeasurements]
public struct DashboardRow {
    public let family: MetricFamily
    public let labels: MetricLabels
    /// Unknown series stay nil, never synthesized as zero.
    public let value: MetricValue?
    public let gaugeScope: GaugeScope?
    public let p50: Quantile?
    public let p95: Quantile?
    public let p99: Quantile?
}
public struct Dashboard {
    public let epoch: UInt64
    public let revision: UInt64
    public let temporality: Temporality
    public let start: LocalInstant?
    public let timestamp: LocalInstant?
    public let health: TelemetryHealth
    public let metricHealth: MetricHealth
    public let rows: [DashboardRow]
    public let truncated: Bool
    public let observationComplete: Bool
    /// Health may be read after the metric endpoint; no atomicity is promised.
    public init(snapshot: MetricSnapshot,health: TelemetryHealth,labels: [MetricLabels],maxRows: Int = 1000) throws {
        guard maxRows >= 0 else { throw ConfigError("negative dashboard budget") }
        epoch = snapshot.epoch; revision = snapshot.revision; timestamp = snapshot.timestamp; start = snapshot.start; temporality = snapshot.temporality; self.health = health; metricHealth = snapshot.health
        var rows: [DashboardRow] = []
        for label in labels {
            for family in dashboardFamilies {
                guard rows.count < maxRows else { break }
                let key = SeriesKey(family,labels: label),value = snapshot.series[key]
                func quantile(_ numerator: UInt64) -> Quantile? { if case let .histogram(h)? = value { return h.quantile(schema: snapshot.schema,numerator: numerator,denominator: 100) }; return nil }
                let scope: GaugeScope?; if case .gauge? = value { scope = snapshot.gaugeScopes[key] ?? snapshot.gaugeScope } else { scope = nil }
                rows.append(DashboardRow(family: family,labels: label,value: value,gaugeScope: scope,p50: quantile(50),p95: quantile(95),p99: quantile(99)))
            }
            if rows.count == maxRows { break }
        }
        self.rows = rows
        let (expected,overflow) = labels.count.multipliedReportingOverflow(by: dashboardFamilies.count)
        truncated = overflow || expected > rows.count
        observationComplete = snapshot.complete && !truncated && snapshot.health.isComplete && health.metric.isComplete && health.pauseIntervalEvictions == 0 && health.abandoned == 0 && health.duplicateObservations == 0 && health.callbackFaults == 0 && health.clockFailures == 0 && health.destructorHookLosses == 0 && health.invalidLifecycle == 0 && health.detailEvictions == 0 && health.detailRejections == 0 && health.detailEventDrops == 0
    }
    /// Streams bounded payload-free JSON. No raw effect IDs, labels, or model
    /// payloads are rendered. Full-width quantities remain exact decimal tokens.
    public func json(maxBytes: Int = 1_048_576) throws -> String {
        guard maxBytes >= 0 else { throw ConfigError("negative report budget") }
        var out = "",used = 0
        func append(_ text: String) throws {
            let bytes = text.utf8.count
            guard bytes <= maxBytes-used else { throw ModelError("dashboard byte budget") }
            used += bytes; out += text
        }
        func time(_ value: LocalInstant?) -> String { guard let value else { return "null" }; return "{\"clock_domain\":\(value.domain),\"nanos\":\(value.nanos)}" }
        func metricHealthJSON(_ value: MetricHealth) throws {
            try append("{\"rejected_series\":\(value.rejectedSeries),\"invalid_labels\":\(value.invalidLabels),\"arithmetic_errors\":\(value.arithmeticErrors),\"omitted_measurements\":\(value.omittedMeasurements),\"invalid_measurements\":\(value.invalidMeasurements),\"imported_ignored\":\(value.importedIgnored),\"snapshots_evicted\":\(value.snapshotsEvicted),\"snapshot_rejections\":\(value.snapshotRejections)}")
        }
        try append("{\"schema\":\"statelessness.effect-dashboard/1\",\"epoch\":\(epoch),\"revision\":\(revision),\"temporality\":\"\(temporality == .cumulative ? "cumulative" : "delta")\",\"start\":\(time(start)),\"end\":\(time(timestamp)),\"truncated\":\(truncated),\"observation_complete\":\(observationComplete),\"health\":{")
        try append("\"dropped_detail_events\":\(health.detailEventDrops),\"detail_evictions\":\(health.detailEvictions),\"detail_rejections\":\(health.detailRejections),\"clock_failures\":\(health.clockFailures),\"callback_faults\":\(health.callbackFaults),\"destructor_hook_losses\":\(health.destructorHookLosses),\"invalid_lifecycle\":\(health.invalidLifecycle),\"active_effect_handles\":\(health.activeEffectHandles),\"omitted_measurements\":\(metricHealth.omittedMeasurements),\"invalid_measurements\":\(metricHealth.invalidMeasurements),\"rejected_series\":\(metricHealth.rejectedSeries),")
        try append("\"invalid_labels\":\(metricHealth.invalidLabels),\"arithmetic_errors\":\(metricHealth.arithmeticErrors),\"imported_ignored\":\(metricHealth.importedIgnored),\"snapshots_evicted\":\(metricHealth.snapshotsEvicted),\"snapshot_rejections\":\(metricHealth.snapshotRejections),\"duplicate_observations\":\(health.duplicateObservations),\"duplicate_deliveries\":\(health.duplicateDeliveries),\"abandoned\":\(health.abandoned),\"pause_interval_evictions\":\(health.pauseIntervalEvictions),\"debugger_affected_measurements\":\(health.debuggerAffectedMeasurements),\"retained_detail_records\":\(health.retainedDetailRecords),\"retained_detail_bytes\":\(health.retainedDetailBytes),\"effect_handle_bytes\":\(health.effectHandleBytes),\"attempt_handle_bytes\":\(health.attemptHandleBytes),\"delivery_handle_bytes\":\(health.deliveryHandleBytes),\"unobserved_native_debugger_interference_possible\":\(health.unobservedNativeDebuggerInterferencePossible),\"host_metric_health\":")
        try metricHealthJSON(health.metric); try append("},\"rows\":[")
        for (index,row) in rows.enumerated() {
            if index > 0 { try append(",") }
            try append("{\"metric\":\"\(row.family.descriptor.name)\",\"operation\":\(row.labels.operation),\"component\":\(row.labels.component),\"worker_pool\":\(row.labels.workerPool),\"outcome\":\"\(diagnosticWireName(row.labels.outcome.rawValue))\",\"measurement_origin\":\"\(diagnosticWireName(row.labels.origin.rawValue))\",\"debugger_affected\":\(row.labels.debuggerAffected),\"value\":")
            switch row.value {
            case nil: try append("null")
            case let .counter(n)?,let .gauge(n)?: try append(String(n))
            case let .histogram(h)?: try append("{\"count\":\(h.count),\"sum_ns\":\(h.sumNS)}")
            }
            try append(",\"gauge_scope\":")
            if let scope = row.gaugeScope { try append("\"\(diagnosticScopeName(scope))\"") } else { try append("null") }
            for (name,q) in [("p50",row.p50),("p95",row.p95),("p99",row.p99)] {
                try append(",\"\(name)\":")
                if let q { try append("{\"upper_bound_ns\":\(q.upperBoundNS.map(String.init) ?? "null"),\"overflow_bucket\":\(q.upperBoundNS == nil),\"observations\":\(q.observations),\"low_sample_warning\":\(q.lowSampleWarning)}") } else { try append("null") }
            }
            try append("}")
        }
        try append("]}"); return out
    }
}
func diagnosticWireName(_ raw: String) -> String { raw.prefix(1).uppercased()+raw.dropFirst() }
func diagnosticScopeName(_ scope: GaugeScope) -> String { switch scope { case .completeParticipation: return "CompleteParticipation"; case .hostSnapshot: return "HostSnapshot"; case .observedSinceAttach: return "ObservedSinceAttach"; case .unknown: return "Unknown" } }

public enum Correlation: Equatable {
    case retained(localSequence: Int,output: UInt32,detailComplete: Bool)
    case beforeCheckpoint(RequestOrigin),missingIdentity,conflict(String)
}
/// Correlation validates host labels against retained output identity. It does
/// not authenticate provenance or prove that external execution happened.
public func correlate(_ evidence: TraceV2,_ detail: EffectDetails) -> Correlation {
    guard let origin = detail.origin,!evidence.provenance.run.isEmpty else { return .missingIdentity }
    guard detail.id.run == origin.run,detail.id.epoch == origin.epoch,exactText(evidence.provenance.run,String(origin.run)),evidence.provenance.epoch == origin.epoch else { return .conflict("run or epoch") }
    guard origin.transitionSequence > 0 else { return .conflict("zero transition sequence") }
    if origin.transitionSequence <= evidence.checkpointAfter { return .beforeCheckpoint(origin) }
    let offset = origin.transitionSequence-evidence.checkpointAfter-1
    guard offset < UInt64(evidence.steps.count) else { return .conflict("sequence outside evidence") }
    let index = Int(offset),step = evidence.steps[index]
    guard let machine = step.causal.machine else { return .missingIdentity }
    guard exactText(machine,String(origin.machine)) else { return .conflict("machine") }
    guard step.sequence == origin.transitionSequence,index < evidence.trace.steps.count,UInt64(origin.outputIndex) < UInt64(evidence.trace.steps[index].outputs.count) else { return .conflict("output outside evidence") }
    return .retained(localSequence: index+1,output: origin.outputIndex,detailComplete: detail.complete)
}
