package debug

import (
	"fmt"
	"math"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

// protocolTelemetry binds a host-owned observer. Captures are staged so a
// response rejected by the protocol's byte budget cannot consume a window.
type protocolTelemetry struct {
	observer      *EffectObserver
	capture       *MetricSnapshot
	handle, epoch uint64
}

// AttachEffectObserver attaches concrete observer-backed metric and telemetry
// services. The observer does not dispatch effects or own application work.
func (p *ProtocolSession[S, I, O]) AttachEffectObserver(observer *EffectObserver) error {
	if p.busy {
		return ProtocolError("busy")
	}
	if observer == nil {
		return ProtocolError("invalid_input")
	}
	observer.mu.Lock()
	oversized := observer.options.MetricLimits.MaxBytes > p.limits.MaxTelemetrySnapshot || observer.options.MaxDetailBytes > p.limits.MaxTelemetrySnapshot
	observer.mu.Unlock()
	if oversized {
		return ProtocolError("limit")
	}
	names := []string{"metric_catalog", "metric_snapshot", "metric_window", "telemetry_health", "effect_details", "effect_timeline"}
	for _, name := range names {
		if p.adapters[name] != nil {
			return ProtocolError("busy")
		}
	}
	if p.adapters == nil {
		p.adapters = make(map[string]ProtocolAdapter)
	}
	attachment := &protocolTelemetry{observer: observer}
	p.telemetry = attachment
	for _, name := range names {
		name := name
		p.adapters[name] = func(request ProtocolRequest) (ProtocolPrepared, error) {
			return p.observerCommand(attachment, name, request)
		}
	}
	return nil
}

var protocolMetricFamilies = []MetricFamily{Requests, Admissions, Attempts, AttemptOutcomes, Resolutions, Deliveries, Unresolved, Running, ReadyDepth, CompletionDepth, QueueWait, AttemptElapsed, Resolution, DeliveryLag, EndToEnd, CompletionProcessing, AdmissionDelay, ResolutionOverhead, CompletionPreparation, CompletionQueueWait, EndToEndObserved, Settlement, ScheduledBackoff, EligibilityWait, InterAttemptGap, DuplicateObservations, DuplicateDeliveries, CancellationRequests, CancellationAcknowledgements, Abandoned, Dropped, InvalidMeasurements}
var protocolMetricNames = []string{"Requests", "Admissions", "Attempts", "AttemptOutcomes", "Resolutions", "Deliveries", "Unresolved", "Running", "ReadyDepth", "CompletionDepth", "QueueWait", "AttemptElapsed", "Resolution", "DeliveryLag", "EndToEnd", "CompletionProcessing", "AdmissionDelay", "ResolutionOverhead", "CompletionPreparation", "CompletionQueueWait", "EndToEndObserved", "Settlement", "ScheduledBackoff", "EligibilityWait", "InterAttemptGap", "DuplicateObservations", "DuplicateDeliveries", "CancellationRequests", "CancellationAcknowledgements", "Abandoned", "Dropped", "InvalidMeasurements"}

func telemetryVariant(s string) string {
	parts := strings.FieldsFunc(s, func(r rune) bool { return r == '-' || r == '_' })
	for i, part := range parts {
		if len(part) > 0 {
			parts[i] = strings.ToUpper(part[:1]) + part[1:]
		}
	}
	return strings.Join(parts, "")
}
func telemetryPage(offset, limit uint64, maximum int) error {
	if limit == 0 || limit > uint64(maximum) || offset > math.MaxUint64-limit {
		return ProtocolError("limit")
	}
	return nil
}
func copyMetricStore(src *MetricsStore) *MetricsStore {
	result := *src
	result.s = cloneSnapshot(src.s)
	result.historyCosts = append([]int(nil), src.historyCosts...)
	if src.history != nil {
		result.history = make([]MetricSnapshot, len(src.history))
		for i, snap := range src.history {
			result.history[i] = cloneSnapshot(snap)
		}
	}
	if src.lastValidEnd != nil {
		x := *src.lastValidEnd
		result.lastValidEnd = &x
	}
	return &result
}

// Count the retained result rather than the later live store: historical
// windows may remain small even after new metric series are admitted.
func telemetrySnapshotBytes(s MetricSnapshot) int {
	cost := 1024 + len(s.Schema.FiniteBoundsNS)*8
	for _, labels := range [][]string{s.Labels.Operations, s.Labels.Components, s.Labels.WorkerPools} {
		for _, name := range labels {
			cost += 24 + len(name)
		}
	}
	for _, value := range s.Series {
		cost += 128
		if value.Kind == HistogramMetric {
			cost += 640
		}
	}
	return cost
}
func (p *ProtocolSession[S, I, O]) observerCommand(a *protocolTelemetry, command string, req ProtocolRequest) (ProtocolPrepared, error) {
	var fields []ProtocolField
	put := func(k, v string) { fields = append(fields, ProtocolField{k, v}) }
	flag := func(k string, v bool) { put(k, "bool:"+strconv.FormatBool(v)) }
	arg := func(i int) uint64 { n, _ := parseNumber(req.Args[i]); return n }
	switch command {
	case "metric_catalog":
		offset, limit := arg(0), arg(1)
		if e := telemetryPage(offset, limit, p.limits.MaxPage); e != nil {
			return ProtocolPrepared{}, e
		}
		put("catalog.total", number(uint64(len(protocolMetricFamilies))))
		next := "none"
		if offset+limit < uint64(len(protocolMetricFamilies)) {
			next = number(offset + limit)
		}
		put("catalog.next_offset", next)
		for i := offset; i < min(offset+limit, uint64(len(protocolMetricFamilies))); i++ {
			f := protocolMetricFamilies[i]
			k := metricKind(f)
			unit := "count"
			if k == HistogramMetric {
				unit = "seconds"
			}
			d := metricDescriptor(f, k, unit)
			key := fmt.Sprintf("catalog.%d", i-offset)
			put(key+".family", "enum:"+protocolMetricNames[i])
			put(key+".name", "string:"+d.Name)
			put(key+".kind", "enum:"+telemetryVariant(string(d.Kind)))
			put(key+".unit", "string:"+d.Unit)
			put(key+".population", "string:"+d.Population)
			put(key+".reset", "string:"+d.Reset)
		}
	case "metric_snapshot":
		offset, limit := arg(1), arg(2)
		if e := telemetryPage(offset, limit, p.limits.MaxPage); e != nil {
			return ProtocolPrepared{}, e
		}
		if req.Args[0] != "none" {
			id := arg(0)
			if a.capture == nil || a.epoch != p.epoch || id != a.handle {
				return ProtocolPrepared{}, ProtocolError("stale_handle")
			}
			appendMetricFields(&fields, id, *a.capture, offset, limit)
			return ProtocolPrepared{Fields: fields}, nil
		}
		if offset != 0 {
			return ProtocolPrepared{}, ProtocolError("invalid_input")
		}
		if p.nextHandle == math.MaxUint64 {
			return ProtocolPrepared{}, ProtocolError("exhausted")
		}
		a.observer.mu.Lock()
		if a.observer.metrics.bytes+512 > p.limits.MaxTelemetrySnapshot {
			a.observer.mu.Unlock()
			return ProtocolPrepared{}, ProtocolError("limit")
		}
		before := copyMetricStore(a.observer.metrics)
		a.observer.mu.Unlock()
		// Capture the clock without mutating observer state before preflight.
		var at *LocalInstant
		clockFailed := false
		if a.observer.options.Timings {
			func() {
				defer func() {
					if recover() != nil {
						clockFailed = true
					}
				}()
				t, e := a.observer.clock.Now()
				if e != nil {
					clockFailed = true
				} else {
					at = &t
				}
			}()
		}
		candidate := copyMetricStore(before)
		if clockFailed {
			candidate.MarkIncomplete()
		}
		snap, e := candidate.Snapshot(at)
		if e != nil {
			return ProtocolPrepared{}, ProtocolError("exhausted")
		}
		id := p.nextHandle + 1
		appendMetricFields(&fields, id, snap, offset, limit)
		return ProtocolPrepared{Fields: fields, Commit: func() error {
			a.observer.mu.Lock()
			defer a.observer.mu.Unlock()
			if !reflect.DeepEqual(a.observer.metrics, before) {
				return fmt.Errorf("observer changed during capture")
			}
			*a.observer.metrics = *candidate
			if clockFailed {
				satInc(&a.observer.health.ClockFailures)
			}
			p.nextHandle = id
			a.handle = id
			a.epoch = p.epoch
			a.capture = &snap
			return nil
		}}, nil
	case "metric_window":
		from, to, limit := arg(0), arg(1), arg(2)
		if e := telemetryPage(0, limit, p.limits.MaxPage); e != nil {
			return ProtocolPrepared{}, e
		}
		if p.nextHandle == math.MaxUint64 {
			return ProtocolPrepared{}, ProtocolError("exhausted")
		}
		a.observer.mu.Lock()
		snap, e := a.observer.metrics.Window(from, to)
		a.observer.mu.Unlock()
		if e != nil {
			return ProtocolPrepared{}, ProtocolError("stale_handle")
		}
		if telemetrySnapshotBytes(snap) > p.limits.MaxTelemetrySnapshot {
			return ProtocolPrepared{}, ProtocolError("limit")
		}
		id := p.nextHandle + 1
		appendMetricFields(&fields, id, snap, 0, limit)
		return ProtocolPrepared{Fields: fields, Commit: func() error { p.nextHandle = id; a.handle = id; a.epoch = p.epoch; a.capture = &snap; return nil }}, nil
	case "effect_details", "effect_timeline":
		id := EffectID{Run: arg(0), Epoch: arg(1), Serial: arg(2)}
		if id.Run != a.observer.options.Run || id.Epoch != a.observer.options.Epoch {
			return ProtocolPrepared{}, ProtocolError("wrong_epoch")
		}
		detail, ok := a.observer.EffectDetails(id)
		if !ok {
			return ProtocolPrepared{}, ProtocolError("stale_handle")
		}
		appendEffectFields(&fields, detail)
		if command == "effect_timeline" {
			limit := arg(3)
			if e := telemetryPage(0, limit, p.limits.MaxPage); e != nil {
				return ProtocolPrepared{}, e
			}
			put("timeline.events.total", number(uint64(len(detail.Events))))
			put("timeline.timings.total", number(uint64(len(detail.Timings))))
			flag("timeline.truncated", uint64(len(detail.Events)) > limit || uint64(len(detail.Timings)) > limit)
			for i, event := range detail.Events[:min(len(detail.Events), int(limit))] {
				key := fmt.Sprintf("timeline.events.%d", i)
				put(key+".sequence", number(event.Sequence))
				flag(key+".debugger_affected", event.DebuggerAffected)
				if event.At != nil {
					put(key+".clock_domain", number(event.At.Domain))
					put(key+".nanos", number(event.At.Nanos))
				}
				kind, _, _ := strings.Cut(event.Kind, ":")
				switch kind {
				case "requested", "cancellation-requested", "cancellation-acknowledged", "settled":
				case "admission":
					put(key+".admission", "enum:"+telemetryVariant(string(detailEventOutcome(event))))
				case "attempt-created", "ready", "retry-scheduled", "attempt-started", "attempt-finished":
					if event.Attempt == nil {
						return ProtocolPrepared{}, ProtocolError("unsupported")
					}
					put(key+".attempt", number(event.Attempt.Serial))
					if kind == "retry-scheduled" {
						if event.DelayNS == nil {
							return ProtocolPrepared{}, ProtocolError("unsupported")
						}
						put(key+".delay_ns", number(*event.DelayNS))
					}
					if kind == "attempt-finished" {
						put(key+".outcome", "enum:"+runtimeOutcome(event.Outcome))
					}
				case "resolved":
					put(key+".outcome", "enum:"+runtimeOutcome(event.Outcome))
				case "publication-reserved", "publication-failed", "delivery-begun", "delivery-observed":
					if event.Delivery == nil {
						return ProtocolPrepared{}, ProtocolError("unsupported")
					}
					put(key+".delivery", number(event.Delivery.Serial))
					if kind == "delivery-observed" {
						put(key+".disposition", "enum:"+telemetryVariant(string(event.Outcome)))
					}
				case "abandoned":
					if event.Attempt == nil {
						put(key+".attempt", "none")
					} else {
						put(key+".attempt", number(event.Attempt.Serial))
					}
				default:
					return ProtocolPrepared{}, ProtocolError("unsupported")
				}
				put(key+".kind", "enum:"+strings.ReplaceAll(kind, "-", "_"))
			}
			for i, timing := range detail.Timings[:min(len(detail.Timings), int(limit))] {
				key := fmt.Sprintf("timeline.timings.%d", i)
				family := ""
				for j, f := range protocolMetricFamilies {
					if f == timing.Family {
						family = protocolMetricNames[j]
						break
					}
				}
				if family == "" {
					return ProtocolPrepared{}, ProtocolError("unsupported")
				}
				put(key+".family", "enum:"+family)
				outcome := telemetryVariant(string(timing.Outcome))
				if outcome == "" {
					outcome = "None"
				}
				put(key+".outcome", "enum:"+outcome)
				if timing.DurationNS != nil {
					put(key+".duration_ns", number(*timing.DurationNS))
				} else {
					reason := map[string]string{"missing endpoint": "MissingEndpoint", "different clock domain": "DifferentClockDomain", "reversed clock": "Reversed"}[timing.Error]
					if reason == "" {
						return ProtocolPrepared{}, ProtocolError("unsupported")
					}
					put(key+".duration_ns", "unknown:"+reason)
				}
				if timing.Attempt == nil {
					put(key+".attempt", "none")
				} else {
					put(key+".attempt", number(timing.Attempt.Serial))
				}
				if timing.Delivery == nil {
					put(key+".delivery", "none")
				} else {
					put(key+".delivery", number(timing.Delivery.Serial))
				}
				flag(key+".debugger_affected", timing.DebuggerAffected)
			}
		}
	case "telemetry_health":
		h := a.observer.TelemetryHealth()
		// Explicit field list in Rust protocol.rs append_telemetry_health order.
		// Go has no destructors, so the two Drop-hook counts stay unknown rather
		// than zero, following the TypeScript binding.
		known := func(v *uint64) string {
			if v == nil {
				return "unknown:explicit_disposal_only"
			}
			return number(*v)
		}
		for _, item := range []struct{ k, v string }{{"destructor_hook_losses", known(h.DestructorHookLosses)}, {"callback_faults", number(h.CallbackFaults)}, {"pause_interval_evictions", number(h.PauseIntervalEvictions)}, {"detail_evictions", number(h.DetailEvictions)}, {"detail_rejections", number(h.DetailRejections)}, {"detail_event_drops", number(h.DetailEventDrops)}, {"duplicate_observations", number(h.DuplicateObservations)}, {"duplicate_deliveries", number(h.DuplicateDeliveries)}, {"invalid_lifecycle", number(h.InvalidLifecycle)}, {"abandoned", number(h.Abandoned)}, {"clock_failures", number(h.ClockFailures)}, {"debugger_affected_measurements", number(h.DebuggerAffectedMeasurements)}, {"active_effect_handles", known(h.ActiveEffectHandles)}, {"retained_detail_records", number(h.RetainedDetailRecords)}, {"retained_detail_bytes", number(h.RetainedDetailBytes)}, {"metric.rejected_series", number(h.Metric.RejectedSeries)}, {"metric.invalid_labels", number(h.Metric.InvalidLabels)}, {"metric.arithmetic_errors", number(h.Metric.ArithmeticErrors)}, {"metric.omitted_measurements", number(h.Metric.OmittedMeasurements)}, {"metric.invalid_measurements", number(h.Metric.InvalidMeasurements)}, {"metric.imported_ignored", number(h.Metric.ImportedIgnored)}, {"metric.snapshots_evicted", number(h.Metric.SnapshotsEvicted)}, {"metric.snapshot_rejections", number(h.Metric.SnapshotRejections)}} {
			put("telemetry."+item.k, item.v)
		}
		flag("telemetry.unobserved_native_debugger_interference_possible", h.UnobservedNativeDebuggerInterferencePossible)
		pauses := a.observer.PauseIntervals()
		put("pauses.total", number(uint64(len(pauses))))
		flag("pauses.truncated", len(pauses) > p.limits.MaxPage)
		for i, pause := range pauses[:min(len(pauses), p.limits.MaxPage)] {
			key := fmt.Sprintf("pauses.%d", i)
			put(key+".generation", number(pause.Generation))
			for _, bound := range []struct {
				name string
				at   *LocalInstant
			}{{"start", pause.Start}, {"end", pause.End}} {
				if bound.at == nil {
					put(key+"."+bound.name, "unknown:clock_or_open_interval")
				} else {
					put(key+"."+bound.name+".clock_domain", number(bound.at.Domain))
					put(key+"."+bound.name+".nanos", number(bound.at.Nanos))
				}
			}
		}
		windows := a.observer.AvailableWindows()
		put("metric_windows.total", number(uint64(len(windows))))
		for i, rev := range windows[:min(len(windows), p.limits.MaxPage)] {
			put(fmt.Sprintf("metric_windows.%d", i), number(rev))
		}
	default:
		return ProtocolPrepared{}, ProtocolError("unsupported")
	}
	return ProtocolPrepared{Fields: fields}, nil
}

func runtimeOutcome(out Outcome) string {
	if out == OutcomeNone {
		return "Unclassified"
	}
	return telemetryVariant(string(out))
}
func detailEventOutcome(event LifecycleEvent) Outcome {
	if event.Outcome != OutcomeNone {
		return event.Outcome
	}
	_, value, _ := strings.Cut(event.Kind, ":")
	return Outcome(value)
}
func appendEffectFields(fields *[]ProtocolField, detail EffectTimeline) {
	put := func(k, v string) { *fields = append(*fields, ProtocolField{k, v}) }
	flag := func(k string, v bool) { put(k, "bool:"+strconv.FormatBool(v)) }
	put("effect.run", number(detail.Effect.Run))
	put("effect.epoch", number(detail.Effect.Epoch))
	put("effect.serial", number(detail.Effect.Serial))
	put("effect.origin.run", number(detail.Origin.Run))
	put("effect.origin.epoch", number(detail.Origin.Epoch))
	put("effect.origin.machine", number(detail.Origin.Machine))
	put("effect.origin.transition", number(detail.Origin.TransitionSequence))
	put("effect.origin.output_index", number(uint64(detail.Origin.OutputIndex)))
	labels := detail.Labels
	put("effect.operation.id", number(uint64(labels.Operation)))
	put("effect.component.id", number(uint64(labels.Component)))
	put("effect.worker_pool.id", number(uint64(labels.WorkerPool)))
	outcome := telemetryVariant(string(labels.Outcome))
	if outcome == "" {
		outcome = "None"
	}
	put("effect.outcome", "enum:"+outcome)
	put("effect.measurement_origin", "enum:"+telemetryVariant(string(labels.Origin)))
	flag("effect.labels.debugger_affected", labels.DebuggerAffected)
	flag("effect.complete", detail.Complete)
	admission := "unknown"
	if detail.Admission != OutcomeNone {
		admission = "enum:" + telemetryVariant(string(detail.Admission))
	}
	put("effect.admission", admission)
	resolution := "unknown"
	if detail.ResolvedKnown {
		resolution = "enum:" + runtimeOutcome(detail.Resolution)
	}
	put("effect.resolution", resolution)
	flag("effect.settled", detail.Settled)
	put("effect.running_attempts", number(detail.RunningAttempts))
	put("effect.deliveries_observed", number(detail.DeliveriesObserved))
	flag("effect.cancellation_requested", detail.CancellationRequested)
	flag("effect.cancellation_acknowledged", detail.CancellationAcknowledged)
	flag("effect.debugger_affected", detail.DebuggerAffected)
	flag("effect.abandoned", detail.Abandoned)
	if detail.Requested == nil {
		put("effect.requested", "unknown:clock")
	} else {
		put("effect.requested.clock_domain", number(detail.Requested.Domain))
		put("effect.requested.nanos", number(detail.Requested.Nanos))
	}
}

func appendMetricFields(fields *[]ProtocolField, handle uint64, snap MetricSnapshot, offset, limit uint64) {
	put := func(k, v string) { *fields = append(*fields, ProtocolField{k, v}) }
	flag := func(k string, v bool) { put(k, "bool:"+strconv.FormatBool(v)) }
	put("metrics.handle", number(handle))
	put("metrics.revision", number(snap.Revision))
	put("metrics.epoch", number(snap.Epoch))
	flag("metrics.complete", snap.Complete)
	put("metrics.temporality", "enum:"+telemetryVariant(snap.Temporality))
	put("metrics.gauge_scope", "enum:"+telemetryVariant(string(snap.GaugeScope)))
	put("metrics.histogram_schema", number(snap.Schema.ID))
	put("metrics.histogram_storage_unit", "enum:nanoseconds")
	keys := make([]SeriesKey, 0, len(snap.Series))
	for key := range snap.Series {
		keys = append(keys, key)
	}
	rank := map[MetricFamily]int{}
	for i, f := range protocolMetricFamilies {
		rank[f] = i
	}
	outcomeRank := map[Outcome]int{OutcomeNone: 0, OutcomeAccepted: 1, OutcomeRejected: 2, OutcomeSuccess: 3, OutcomeFailure: 4, OutcomeCancelled: 5, OutcomeAbandoned: 6, OutcomeIgnored: 7}
	originRank := map[MeasurementOrigin]int{OriginLive: 0, OriginDebugLive: 1, OriginTestClock: 2, OriginReplay: 3, OriginImported: 4}
	sort.Slice(keys, func(i, j int) bool {
		a, b := keys[i], keys[j]
		if rank[a.Family] != rank[b.Family] {
			return rank[a.Family] < rank[b.Family]
		}
		if a.Labels.Operation != b.Labels.Operation {
			return a.Labels.Operation < b.Labels.Operation
		}
		if a.Labels.Component != b.Labels.Component {
			return a.Labels.Component < b.Labels.Component
		}
		if a.Labels.WorkerPool != b.Labels.WorkerPool {
			return a.Labels.WorkerPool < b.Labels.WorkerPool
		}
		if a.Labels.Outcome != b.Labels.Outcome {
			return outcomeRank[a.Labels.Outcome] < outcomeRank[b.Labels.Outcome]
		}
		if a.Labels.Origin != b.Labels.Origin {
			return originRank[a.Labels.Origin] < originRank[b.Labels.Origin]
		}
		return !a.Labels.DebuggerAffected && b.Labels.DebuggerAffected
	})
	put("metrics.series.total", number(uint64(len(keys))))
	put("metrics.series.offset", number(offset))
	next := "none"
	if offset+limit < uint64(len(keys)) {
		next = number(offset + limit)
	}
	put("metrics.series.next_offset", next)
	for _, v := range []struct {
		name string
		at   *LocalInstant
	}{{"start", snap.Start}, {"end", snap.End}} {
		if v.at == nil {
			put("metrics."+v.name, "unknown:clock")
		} else {
			put("metrics."+v.name+".clock_domain", number(v.at.Domain))
			put("metrics."+v.name+".nanos", number(v.at.Nanos))
		}
	}
	put("metrics.limits.series", number(uint64(snap.Limits.MaxSeries)))
	put("metrics.limits.bytes", number(uint64(snap.Limits.MaxBytes)))
	put("metrics.health.rejected_series", number(snap.Health.RejectedSeries))
	put("metrics.health.invalid_measurements", number(snap.Health.InvalidMeasurements))
	put("metrics.health.omitted_measurements", number(snap.Health.OmittedMeasurements))
	count := uint64(0)
	if offset < uint64(len(keys)) {
		count = min(limit, uint64(len(keys))-offset)
	}
	put("metrics.series.returned", number(count))
	for index := uint64(0); index < count; index++ {
		key := keys[offset+index]
		v := snap.Series[key]
		unit := "count"
		if v.Kind == HistogramMetric {
			unit = "seconds"
		}
		desc := metricDescriptor(key.Family, v.Kind, unit)
		prefix := fmt.Sprintf("metrics.series.%d", index)
		put(prefix+".name", "string:"+desc.Name)
		put(prefix+".kind", "enum:"+telemetryVariant(string(v.Kind)))
		put(prefix+".unit", "string:"+desc.Unit)
		put(prefix+".population", "string:"+desc.Population)
		l := key.Labels
		put(prefix+".operation.id", number(uint64(l.Operation)))
		put(prefix+".component.id", number(uint64(l.Component)))
		put(prefix+".worker_pool.id", number(uint64(l.WorkerPool)))
		outcome := telemetryVariant(string(l.Outcome))
		if outcome == "" {
			outcome = "None"
		}
		put(prefix+".outcome", "enum:"+outcome)
		put(prefix+".measurement_origin", "enum:"+telemetryVariant(string(l.Origin)))
		flag(prefix+".debugger_affected", l.DebuggerAffected)
		for _, x := range []struct {
			name   string
			id     uint16
			values []string
		}{{"operation", l.Operation, snap.Labels.Operations}, {"component", l.Component, snap.Labels.Components}, {"worker_pool", l.WorkerPool, snap.Labels.WorkerPools}} {
			if int(x.id) < len(x.values) {
				put(prefix+"."+x.name+".name", "string:"+x.values[x.id])
			}
		}
		if v.Kind != HistogramMetric {
			put(prefix+".value", number(v.Number))
			if v.Kind == GaugeMetric {
				scope, ok := snap.GaugeScopes[key]
				if !ok {
					scope = UnknownGaugeScope
				}
				put(prefix+".gauge_scope", "enum:"+telemetryVariant(string(scope)))
			}
			continue
		}
		h := v.Histogram
		if h == nil {
			continue
		}
		put(prefix+".count", number(h.Count))
		put(prefix+".sum_ns", "u128:"+h.SumNS.String())
		for i := 0; i <= len(snap.Schema.FiniteBoundsNS); i++ {
			b := fmt.Sprintf("%s.buckets.%d", prefix, i)
			put(b+".count", number(h.Buckets[i]))
			bound := "infinity"
			if i < len(snap.Schema.FiniteBoundsNS) {
				bound = number(snap.Schema.FiniteBoundsNS[i])
			}
			put(b+".upper_bound_ns", bound)
		}
		put(prefix+".quantile_method", "enum:histogram_bucket_upper_bound")
		for _, q := range []struct {
			name string
			n    uint64
		}{{"p50", 50}, {"p95", 95}, {"p99", 99}} {
			value := h.Quantile(snap.Schema, q.n, 100)
			if value == nil {
				put(prefix+"."+q.name, "unavailable:no_observations")
				continue
			}
			bound := "infinity"
			if value.UpperBoundNS != nil {
				bound = number(*value.UpperBoundNS)
			}
			put(prefix+"."+q.name+".upper_bound_ns", bound)
			put(prefix+"."+q.name+".observations", number(value.Observations))
			flag(prefix+"."+q.name+".low_sample_warning", value.LowSampleWarning)
		}
	}
}
