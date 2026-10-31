package debug

import (
	"fmt"
	"io"
	"math"
	"sort"
	"strings"
	"unicode/utf8"
)

type Severity uint8

const (
	TraceSeverity Severity = iota
	DebugSeverity
	InfoSeverity
	WarnSeverity
	ErrorSeverity
)

type CaptureContext struct {
	FailingProperties                                                                                          []string
	Phase                                                                                                      string
	PropertyStatusKnown                                                                                        bool
	TurnComplete                                                                                               *bool
	Producer, RunID                                                                                            uint64
	Origin, Model, Machine, Entity, Correlation, InputVariant, OutputVariant, Property, EffectKind, SourceSite string
	PropertyFailed                                                                                             bool
	Snapshot                                                                                                   SnapshotID
}
type ScopeSelector struct {
	Producer                                                                                                   *uint64
	Origin, Model, Machine, Entity, Correlation, InputVariant, OutputVariant, Property, EffectKind, SourceSite string
	PropertyFailed                                                                                             *bool
}

func (s ScopeSelector) matches(c CaptureContext) bool {
	if s.Producer != nil && *s.Producer != c.Producer || s.PropertyFailed != nil && (!c.PropertyStatusKnown || *s.PropertyFailed != c.PropertyFailed) {
		return false
	}
	pairs := [][2]string{{s.Origin, c.Origin}, {s.Model, c.Model}, {s.Machine, c.Machine}, {s.Entity, c.Entity}, {s.Correlation, c.Correlation}, {s.InputVariant, c.InputVariant}, {s.OutputVariant, c.OutputVariant}, {s.Property, c.Property}, {s.EffectKind, c.EffectKind}, {s.SourceSite, c.SourceSite}}
	for _, pair := range pairs {
		if pair[0] != "" && pair[0] != pair[1] {
			return false
		}
	}
	return true
}

type ProbeSubscription struct {
	Kind            string
	SampleEvery     uint64
	Sink            uint64
	Site            string
	MinimumSeverity Severity
	Scope           ScopeSelector
	Watch           WatchConfig
}
type DiagnosticLimits struct {
	MaxSinks, MaxSubscriptions   int
	MaxProducers, MaxProbeStates int
	MaxSiteBytes, MaxScopeBytes  uint64
	MaxTurnRecords               int
	MaxTurnBytes                 uint64
	Watch                        WatchLimits
}

func DefaultDiagnosticLimits() DiagnosticLimits {
	return DiagnosticLimits{MaxSinks: 8, MaxSubscriptions: 64, MaxProducers: 64, MaxProbeStates: 256, MaxSiteBytes: 256, MaxScopeBytes: 4096, MaxTurnRecords: 256, MaxTurnBytes: 256 * 1024, Watch: DefaultWatchLimits()}
}

type diagnosticSink struct {
	queue, protocolQueue                                                     *WatchRegistry
	auth                                                                     WatchAuthorization
	captured, sampled, filtered, truncated, observationGaps, baselineEvicted uint64
	// abandoned counts host-queue records discarded by Abandon. The protocol
	// queue keeps its own admission and abandonment counters because it is the
	// Rust-shaped view (one queue of enveloped probe/marker records).
	abandoned, protocolCaptured, protocolAbandoned uint64
}

// SinkHealth mirrors Rust diagnostic.rs SinkHealth field for field. Capture
// decisions (filtered, sampled, truncated, inspection_failed, baseline_evicted,
// observation_gaps) are per sink; queue counters (captured, dropped, abandoned)
// describe the queue the view was taken from.
type SinkHealth struct {
	Captured, Filtered, Sampled, Dropped, Truncated, ExporterFailed, Abandoned, InspectionFailed, BaselineEvicted, ObservationGaps uint64
}
type probeEntry struct {
	subscription                   ProbeSubscription
	registries                     map[uint64]*WatchRegistry
	markerCounts                   map[uint64]uint64
	pendingReasons                 map[uint64]string
	addedRevision, removedRevision uint64
}

// Diagnostics owns explicit local producer configuration. Each sink has an
// independent bounded queue; neither capture nor probing reads a runtime clock.
type producerCapture struct {
	active, effectiveSequence, lastSequence, epoch, eventSequence uint64
	observed, reconfigured                                        bool
	lastSource                                                    *diagnosticSource
}
type CaptureAck struct{ Producer, CaptureRevision, EffectiveSequence uint64 }
type Diagnostics struct {
	limits                               DiagnosticLimits
	nextSink, nextSubscription, revision uint64
	sinks                                map[uint64]*diagnosticSink
	subscriptions                        map[uint64]*probeEntry
	busy                                 bool
	scope                                *DiagnosticScope
	producers                            map[uint64]*producerCapture
	probeStates                          int
}

func NewDiagnostics(l DiagnosticLimits) (*Diagnostics, error) {
	if l.MaxSinks <= 0 || l.MaxSubscriptions <= 0 || l.MaxProducers <= 0 || l.MaxProbeStates <= 0 || l.MaxSiteBytes == 0 || l.MaxScopeBytes == 0 || l.MaxTurnRecords < 0 {
		return nil, fmt.Errorf("positive diagnostic budgets required")
	}
	return &Diagnostics{limits: l, nextSink: 1, nextSubscription: 1, sinks: map[uint64]*diagnosticSink{}, subscriptions: map[uint64]*probeEntry{}, producers: map[uint64]*producerCapture{}}, nil
}
func (d *Diagnostics) AddSink(auth WatchAuthorization) (uint64, error) {
	if d.busy || len(d.sinks) >= d.limits.MaxSinks || d.nextSink == math.MaxUint64 {
		return 0, fmt.Errorf("sink capacity or active capture")
	}
	queue, e := NewWatchRegistry(d.limits.Watch, auth)
	if e != nil {
		return 0, e
	}
	id := d.nextSink
	d.nextSink++
	d.sinks[id] = &diagnosticSink{queue: queue, auth: auth}
	return id, nil
}
func cloneScope(s ScopeSelector) ScopeSelector {
	if s.Producer != nil {
		x := *s.Producer
		s.Producer = &x
	}
	if s.PropertyFailed != nil {
		x := *s.PropertyFailed
		s.PropertyFailed = &x
	}
	s.Origin = strings.Clone(s.Origin)
	s.Model = strings.Clone(s.Model)
	s.Machine = strings.Clone(s.Machine)
	s.Entity = strings.Clone(s.Entity)
	s.Correlation = strings.Clone(s.Correlation)
	s.InputVariant = strings.Clone(s.InputVariant)
	s.OutputVariant = strings.Clone(s.OutputVariant)
	s.Property = strings.Clone(s.Property)
	s.EffectKind = strings.Clone(s.EffectKind)
	s.SourceSite = strings.Clone(s.SourceSite)
	return s
}
func (d *Diagnostics) Subscribe(expectedRevision uint64, config ProbeSubscription) (uint64, error) {
	if d.busy {
		return 0, fmt.Errorf("capture active")
	}
	if expectedRevision != d.revision {
		return 0, fmt.Errorf("stale capture revision")
	}
	d.reclaim()
	if len(d.subscriptions) >= d.limits.MaxSubscriptions || d.nextSubscription == math.MaxUint64 || d.revision == math.MaxUint64 {
		return 0, fmt.Errorf("subscription capacity or active capture")
	}
	if config.Kind == "" {
		config.Kind = "probe"
	}
	if config.Kind != "probe" && config.Kind != "marker" {
		return 0, fmt.Errorf("unknown diagnostic site kind")
	}
	sink := d.sinks[config.Sink]
	if sink == nil {
		return 0, fmt.Errorf("unknown sink")
	}
	if config.Site == "" || !utf8.ValidString(config.Site) || uint64(len(config.Site)) > d.limits.MaxSiteBytes || config.MinimumSeverity > ErrorSeverity {
		return 0, fmt.Errorf("invalid probe site or severity")
	}
	scope := config.Scope
	if scope.Origin != "" && !validDiagnosticOrigin(scope.Origin) {
		return 0, fmt.Errorf("invalid diagnostic origin")
	}
	for _, label := range []string{scope.Model, scope.Machine, scope.Entity, scope.Correlation, scope.InputVariant, scope.OutputVariant, scope.Property, scope.EffectKind, scope.SourceSite} {
		if len(label) > 256 || !utf8.ValidString(label) {
			return 0, fmt.Errorf("invalid scope selector label")
		}
	}
	scopeBytes := uint64(len(scope.Origin) + len(scope.Model) + len(scope.Machine) + len(scope.Entity) + len(scope.Correlation) + len(scope.InputVariant) + len(scope.OutputVariant) + len(scope.Property) + len(scope.EffectKind) + len(scope.SourceSite))
	if scopeBytes > d.limits.MaxScopeBytes {
		return 0, fmt.Errorf("scope selector budget")
	}
	limits := d.limits.Watch
	limits.MaxWatches = 1
	limits.MaxBaselineBytes /= uint64(d.limits.MaxProbeStates)
	registry, e := NewWatchRegistry(limits, sink.auth)
	if e != nil {
		return 0, e
	}
	registry.diagnosticProjection = true
	if _, e = registry.Add(config.Watch); e != nil {
		return 0, e
	}
	registry.Drain()
	if config.SampleEvery == 0 {
		config.SampleEvery = 1
	}
	config.Site = strings.Clone(config.Site)
	config.Scope = cloneScope(config.Scope)
	config.Watch = cloneWatchConfig(config.Watch)
	id := d.nextSubscription
	d.nextSubscription++
	d.revision++
	d.subscriptions[id] = &probeEntry{subscription: config, registries: map[uint64]*WatchRegistry{}, markerCounts: map[uint64]uint64{}, pendingReasons: map[uint64]string{}, addedRevision: d.revision}
	sink.queue.push(WatchEvent{WatchID: id, CaptureRevision: d.revision, Kind: "capture_configured"})
	return id, nil
}
func (d *Diagnostics) Unsubscribe(expectedRevision, id uint64) error {
	if expectedRevision != d.revision {
		return fmt.Errorf("stale capture revision")
	}
	if d.busy {
		return fmt.Errorf("capture active")
	}
	p := d.subscriptions[id]
	if p == nil {
		return fmt.Errorf("unknown subscription")
	}
	if d.revision == math.MaxUint64 {
		return fmt.Errorf("capture revision exhausted")
	}
	if p.removedRevision != 0 {
		return fmt.Errorf("subscription removed")
	}
	d.revision++
	p.removedRevision = d.revision
	d.sinks[p.subscription.Sink].queue.push(WatchEvent{WatchID: id, CaptureRevision: d.revision, Kind: "capture_removed"})
	return nil
}
func (d *Diagnostics) CaptureRevision() uint64 { return d.revision }
func (d *Diagnostics) Probe(context CaptureContext, site string, severity Severity, value func() InspectAdapter) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if !utf8.ValidString(site) || uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("probe site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	if producer == nil {
		return fmt.Errorf("unknown producer")
	}
	if context.Snapshot.Sequence < producer.effectiveSequence {
		return fmt.Errorf("stale producer boundary")
	}
	// Admit all selected producer-local registries before invoking a callback or
	// publishing any records, so a capacity error cannot partially capture.
	needed := 0
	if producer.eventSequence != math.MaxUint64 {
		for _, p := range d.subscriptions {
			config := p.subscription
			if config.Kind != "probe" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision || p.registries[context.Producer] != nil {
				continue
			}
			if _, pending := p.pendingReasons[context.Producer]; !pending {
				needed++
			}
		}
	}
	if needed > d.limits.MaxProbeStates-d.probeStates {
		return fmt.Errorf("probe state capacity")
	}
	revision := uint64(0)
	if producer != nil {
		if !producer.observed || context.Snapshot.Sequence > producer.lastSequence {
			producer.lastSequence = context.Snapshot.Sequence
			producer.observed = true
		}
		revision = producer.active
	}
	evaluated := false
	var selected InspectAdapter
	lazyValue := func() InspectAdapter {
		if !evaluated {
			evaluated = true
			if value != nil {
				selected = value()
			}
		}
		return selected
	}
	ids := make([]uint64, 0, len(d.subscriptions))
	for id := range d.subscriptions {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	var eventSequence uint64
	for _, id := range ids {
		p := d.subscriptions[id]
		config := p.subscription

		if revision < p.addedRevision || p.removedRevision != 0 && revision >= p.removedRevision {
			continue
		}
		if config.Kind != "probe" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) {
			continue
		}
		if eventSequence == 0 {
			if producer.eventSequence == math.MaxUint64 {
				return nil
			}
			producer.eventSequence++
			eventSequence = producer.eventSequence
		}
		c := WatchContext{Snapshot: context.Snapshot, Model: context.Model, Machine: context.Machine, InputVariant: context.InputVariant, OutputVariant: context.OutputVariant, Origin: context.Origin, PropertyFailed: context.PropertyFailed, PropertyStatusKnown: context.PropertyStatusKnown, FailingProperties: context.FailingProperties, Phase: context.Phase}
		registry, e := d.producerRegistry(p, context.Producer)
		if e != nil {
			return e
		}
		failuresBefore := registry.inspectionFailures
		evictionsBefore := registry.baselineEvictions
		var deliveredBefore, evaluatedBefore uint64
		for _, entry := range registry.entries {
			deliveredBefore = entry.delivered
			evaluatedBefore = entry.evaluated
		}
		observeErr := registry.Observe(c, lazyValue)
		failures := registry.inspectionFailures - failuresBefore
		sinkQueue := d.sinks[config.Sink].queue
		if failures > math.MaxUint64-sinkQueue.inspectionFailures {
			sinkQueue.inspectionFailures = math.MaxUint64
		} else {
			sinkQueue.inspectionFailures += failures
		}
		if observeErr != nil {
			return observeErr
		}
		evictions := registry.baselineEvictions - evictionsBefore
		sink := d.sinks[config.Sink]
		if evictions > math.MaxUint64-sink.baselineEvicted {
			sink.baselineEvicted = math.MaxUint64
		} else {
			sink.baselineEvicted += evictions
		}
		for _, entry := range registry.entries {
			sink := d.sinks[config.Sink]
			if entry.evaluated > evaluatedBefore && entry.delivered == deliveredBefore && sink.filtered < math.MaxUint64 {
				sink.filtered++
			}
			if entry.delivered > deliveredBefore && (entry.delivered-1)%config.SampleEvery != 0 {
				if sink.sampled < math.MaxUint64 {
					sink.sampled++
				}
			}
		}
		events, e := registry.Drain()
		if e != nil {
			return e
		}
		for _, event := range events {
			event.WatchID = id
			event.CaptureRevision = revision
			// Only complete value records have enough native evidence for a
			// protocol envelope. Error and lifecycle records remain explicit.
			if event.Kind == "inspection_error" {
				metadata := &DiagnosticEventMetadata{RunID: context.RunID, Producer: context.Producer, Epoch: producer.epoch, ProducerSequence: eventSequence, Transition: context.Snapshot.Sequence, Origin: context.Origin, Site: site, Path: config.Watch.Selector.Path, Severity: severity, Kind: "probe", SchemaVersion: config.Watch.Selector.SchemaVersion, SchemaKnown: event.FailureSchemaKnown, Baseline: event.FailureBaseline, BaselineReason: event.FailureBaselineReason, ChangeUnknown: true}
				if context.TurnComplete != nil {
					metadata.TurnCompletenessKnown = true
					metadata.IncompleteTurn = !*context.TurnComplete
				}
				event.Diagnostic = metadata
				for _, entry := range registry.entries {
					entry.pendingBaseline = "inspection_incomplete"
				}
			}
			if event.Node != nil && (strings.HasPrefix(event.Kind, "baseline_") || event.Kind == "watch_value" || event.Kind == "sampled_change") {
				baseline := strings.HasPrefix(event.Kind, "baseline_")
				reason := ""
				if baseline {
					reason = strings.TrimPrefix(event.Kind, "baseline_")
				}
				metadata := &DiagnosticEventMetadata{RunID: context.RunID, Producer: context.Producer, Epoch: producer.epoch, ProducerSequence: eventSequence, Transition: context.Snapshot.Sequence, Origin: context.Origin, Site: site, Path: event.Path, Severity: severity, Kind: "probe", SchemaVersion: config.Watch.Selector.SchemaVersion, SchemaKnown: true, Baseline: baseline, BaselineReason: reason, ChangeUnknown: !event.Node.IsComplete() || (baseline && reason != "initial")}
				if context.TurnComplete != nil {
					metadata.TurnCompletenessKnown = true
					metadata.IncompleteTurn = !*context.TurnComplete
				}
				event.Diagnostic = metadata
			}
			if event.Diagnostic != nil && event.Diagnostic.ChangeUnknown {
				sink := d.sinks[config.Sink]
				if sink.truncated < math.MaxUint64 {
					sink.truncated++
				}
			}
			d.emitDiagnostic(config.Sink, event)
		}
	}
	return nil
}
func (d *Diagnostics) DrainSink(id uint64) ([]WatchEvent, error) {
	if d.busy {
		return nil, fmt.Errorf("capture active")
	}
	sink := d.sinks[id]
	if sink == nil {
		return nil, fmt.Errorf("unknown sink")
	}
	return sink.queue.Drain()
}
func (d *Diagnostics) ExportSink(id uint64, w io.Writer) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	sink := d.sinks[id]
	if sink == nil {
		return fmt.Errorf("unknown sink")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	return sink.queue.Export(w)
}

// RegisterProducer establishes an explicit producer epoch before configuration
// acknowledgement. It does not acknowledge a capture revision.
func (d *Diagnostics) RegisterProducer(producer, epoch uint64) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if epoch == 0 || d.producers[producer] != nil || len(d.producers) >= d.limits.MaxProducers {
		return fmt.Errorf("producer capacity or invalid epoch")
	}
	d.producers[producer] = &producerCapture{epoch: epoch}
	return nil
}

// ProducerStatus is a detached, ordered view of explicitly registered producers.
type ProducerStatus struct{ Producer, Epoch, CaptureRevision, EffectiveSequence uint64 }

func (d *Diagnostics) ProducerStatus() []ProducerStatus {
	ids := make([]uint64, 0, len(d.producers))
	for id := range d.producers {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	result := make([]ProducerStatus, 0, len(ids))
	for _, id := range ids {
		p := d.producers[id]
		result = append(result, ProducerStatus{id, p.epoch, p.active, p.effectiveSequence})
	}
	return result
}

// Acknowledge installs the selected configuration for an ordered producer;
// observations before the effective boundary are rejected.
func (d *Diagnostics) Acknowledge(producer, expectedRevision, effectiveSequence uint64) (CaptureAck, error) {
	if d.busy || expectedRevision != d.revision {
		return CaptureAck{}, fmt.Errorf("active capture or stale revision")
	}
	p := d.producers[producer]
	if p == nil {
		return CaptureAck{}, fmt.Errorf("unknown producer")
	}
	if p.observed && effectiveSequence <= p.lastSequence {
		return CaptureAck{}, fmt.Errorf("stale producer boundary")
	}
	p.reconfigured = p.active != 0
	p.lastSource = nil
	p.active = expectedRevision
	p.effectiveSequence = effectiveSequence
	// An acknowledgement installs a fresh producer-local capture baseline.
	for _, subscription := range d.subscriptions {
		if _, ok := subscription.pendingReasons[producer]; ok {
			delete(subscription.pendingReasons, producer)
			d.probeStates--
		}
		if subscription.registries[producer] != nil {
			delete(subscription.registries, producer)
			d.probeStates--
		}
		if _, ok := subscription.markerCounts[producer]; ok {
			delete(subscription.markerCounts, producer)
			d.probeStates--
		}
	}
	d.reclaim()
	return CaptureAck{producer, expectedRevision, effectiveSequence}, nil
}

// SinkHealth reports the host queue's occupancy and loss counters. Use Health
// for the Rust-shaped per-sink counters.
func (d *Diagnostics) SinkHealth(id uint64) (WatchHealth, error) {
	sink := d.sinks[id]
	if sink == nil {
		return WatchHealth{}, fmt.Errorf("unknown sink")
	}
	return sink.queue.Health(), nil
}

// Health returns the Rust SinkHealth counters for the host queue of a sink.
func (d *Diagnostics) Health(id uint64) (SinkHealth, error) {
	sink := d.sinks[id]
	if sink == nil {
		return SinkHealth{}, fmt.Errorf("unknown sink")
	}
	return sink.health(sink.queue, sink.captured, sink.abandoned), nil
}

// protocolHealth is the same view over the attached protocol queue, which is
// what the protocol drains; exporter failures remain those of the host sink.
func (s *diagnosticSink) protocolHealth() SinkHealth {
	if s.protocolQueue == nil {
		return s.health(s.queue, s.captured, s.abandoned)
	}
	return s.health(s.protocolQueue, s.protocolCaptured, s.protocolAbandoned)
}

func (s *diagnosticSink) health(q *WatchRegistry, captured, abandoned uint64) SinkHealth {
	host := s.queue.Health()
	return SinkHealth{Captured: captured, Filtered: s.filtered, Sampled: s.sampled, Dropped: q.droppedTotal, Truncated: s.truncated, ExporterFailed: host.ExporterFailures, Abandoned: abandoned, InspectionFailed: host.InspectionFailures, BaselineEvicted: s.baselineEvicted, ObservationGaps: s.observationGaps}
}

// Abandon discards every queued record of a sink after a host-selected drain,
// mirroring Rust diagnostic.rs DiagnosticHub::abandon. It returns the number of
// host-queue records discarded; an attached protocol queue is cleared too and
// counts its own abandoned records. Subscriptions and baselines are unchanged.
func (d *Diagnostics) Abandon(id uint64) (int, error) {
	if d.busy {
		return 0, fmt.Errorf("capture active")
	}
	sink := d.sinks[id]
	if sink == nil {
		return 0, fmt.Errorf("unknown sink")
	}
	n := len(sink.queue.ring)
	sink.abandoned = saturatingAdd(sink.abandoned, uint64(n))
	if err := consumeDiagnosticQueue(sink.queue, n); err != nil {
		return 0, err
	}
	if q := sink.protocolQueue; q != nil {
		m := len(q.ring)
		sink.protocolAbandoned = saturatingAdd(sink.protocolAbandoned, uint64(m))
		if err := consumeDiagnosticQueue(q, m); err != nil {
			return 0, err
		}
	}
	return n, nil
}

func saturatingAdd(a, b uint64) uint64 {
	if b > math.MaxUint64-a {
		return math.MaxUint64
	}
	return a + b
}

// Marker captures explicit bounded metadata only; it never evaluates a value.
func (d *Diagnostics) Marker(context CaptureContext, site string, severity Severity) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if !utf8.ValidString(site) || uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("marker site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	if producer == nil {
		return fmt.Errorf("unknown producer")
	}
	if context.Snapshot.Sequence < producer.effectiveSequence {
		return fmt.Errorf("stale producer boundary")
	}
	ids := make([]uint64, 0, len(d.subscriptions))
	for id := range d.subscriptions {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	// Simulate state admission in delivery order before changing the producer or
	// any sink. Pending states may be transferred or released by a marker.
	states := d.probeStates
	if producer.eventSequence != math.MaxUint64 {
		for _, id := range ids {
			p := d.subscriptions[id]
			config := p.subscription
			if config.Kind != "marker" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
				continue
			}
			_, pending := p.pendingReasons[context.Producer]
			_, exists := p.markerCounts[context.Producer]
			if config.Watch.Trigger == "equals" || config.Watch.Trigger == "above_u128" {
				if pending {
					states--
				}
			} else if !exists && !pending {
				states++
				if states > d.limits.MaxProbeStates {
					return fmt.Errorf("probe state capacity")
				}
			} else if exists && pending {
				states--
			}
		}
	}
	if !producer.observed || context.Snapshot.Sequence > producer.lastSequence {
		producer.lastSequence = context.Snapshot.Sequence
		producer.observed = true
	}
	var eventSequence uint64
	for _, id := range ids {
		p := d.subscriptions[id]
		config := p.subscription
		if config.Kind != "marker" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
			continue
		}
		if eventSequence == 0 {
			if producer.eventSequence == math.MaxUint64 {
				return nil
			}
			producer.eventSequence++
			eventSequence = producer.eventSequence
		}
		if config.Watch.Trigger == "equals" || config.Watch.Trigger == "above_u128" {
			if _, pending := p.pendingReasons[context.Producer]; pending {
				delete(p.pendingReasons, context.Producer)
				d.probeStates--
			}
			sink := d.sinks[config.Sink]
			if sink.filtered < math.MaxUint64 {
				sink.filtered++
			}
			continue
		}
		count, exists := p.markerCounts[context.Producer]
		pendingReason, pending := p.pendingReasons[context.Producer]
		if !exists {
			if !pending && d.probeStates >= d.limits.MaxProbeStates {
				return fmt.Errorf("probe state capacity")
			}
			p.markerCounts[context.Producer] = 0
			if !pending {
				d.probeStates++
			}
		}
		if pending {
			delete(p.pendingReasons, context.Producer)
			if exists {
				d.probeStates--
			}
		}
		reason := "initial"
		if count == 0 && producer.reconfigured {
			reason = "reconfigured"
		}
		if pending {
			reason = pendingReason
		}
		if count < math.MaxUint64 {
			count++
		}
		p.markerCounts[context.Producer] = count
		if (count-1)%config.SampleEvery != 0 {
			sink := d.sinks[config.Sink]
			if sink.sampled < math.MaxUint64 {
				sink.sampled++
			}
			continue
		}
		metadata := &DiagnosticEventMetadata{RunID: context.RunID, Producer: context.Producer, Epoch: producer.epoch, ProducerSequence: eventSequence, Transition: context.Snapshot.Sequence, Origin: context.Origin, Site: site, Path: config.Watch.Selector.Path, Severity: severity, Kind: "marker", Baseline: true, BaselineReason: reason}
		if context.TurnComplete != nil {
			metadata.TurnCompletenessKnown = true
			metadata.IncompleteTurn = !*context.TurnComplete
		}
		d.emitDiagnostic(config.Sink, WatchEvent{WatchID: id, CaptureRevision: producer.active, Sequence: context.Snapshot.Sequence, Kind: "marker", Diagnostic: metadata})
	}
	return nil
}

// DropScope records an explicitly omitted scoped observation and invalidates its
// baseline. It cannot recover an unselected earlier in-reducer value.
func (d *Diagnostics) DropScope(context CaptureContext, site string) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if !utf8.ValidString(site) || uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	if producer == nil {
		return fmt.Errorf("unknown producer")
	}
	if context.Snapshot.Sequence < producer.effectiveSequence {
		return fmt.Errorf("stale producer boundary")
	}
	// Reserve continuity state before changing the boundary or publishing records.
	needed := 0
	for _, p := range d.subscriptions {
		config := p.subscription
		if config.Site != site || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
			continue
		}
		if p.registries[context.Producer] == nil {
			if _, exists := p.pendingReasons[context.Producer]; !exists {
				needed++
			}
		}
	}
	if needed > d.limits.MaxProbeStates-d.probeStates {
		return fmt.Errorf("probe state capacity")
	}
	if !producer.observed || context.Snapshot.Sequence > producer.lastSequence {
		producer.lastSequence = context.Snapshot.Sequence
		producer.observed = true
	}
	ids := make([]uint64, 0, len(d.subscriptions))
	for id := range d.subscriptions {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		p := d.subscriptions[id]
		config := p.subscription
		if config.Site != site || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
			continue
		}
		registry := p.registries[context.Producer]
		if registry == nil {
			if _, exists := p.pendingReasons[context.Producer]; !exists {
				d.probeStates++
			}
			p.pendingReasons[context.Producer] = "observation_gap"
		} else {
			invalidateDiagnosticBaseline(registry, "observation_gap")
		}
		d.emitDiagnostic(config.Sink, WatchEvent{WatchID: id, CaptureRevision: producer.active, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "scope_incomplete", Completeness: "observation_gap"})
	}
	return nil
}

func (d *Diagnostics) producerRegistry(p *probeEntry, producer uint64) (*WatchRegistry, error) {
	if registry := p.registries[producer]; registry != nil {
		return registry, nil
	}
	_, pending := p.pendingReasons[producer]
	if !pending && d.probeStates >= d.limits.MaxProbeStates {
		return nil, fmt.Errorf("probe state capacity")
	}
	limits := d.limits.Watch
	limits.MaxWatches = 1
	limits.MaxBaselineBytes /= uint64(d.limits.MaxProbeStates)
	registry, e := NewWatchRegistry(limits, d.sinks[p.subscription.Sink].auth)
	if e != nil {
		return nil, e
	}
	// Diagnostic projections can retain redacted nodes as incomplete evidence;
	// ordinary watch admission still treats a redacted path as unavailable.
	registry.diagnosticProjection = true
	config := cloneWatchConfig(p.subscription.Watch)
	config.EvaluationInterval = 1
	config.DeliveryInterval = p.subscription.SampleEvery
	ack, e := registry.Add(config)
	if e != nil {
		return nil, e
	}
	if producerState := d.producers[producer]; producerState != nil && producerState.reconfigured {
		registry.entries[ack.WatchID].pendingBaseline = "reconfigured"
	}
	if pending {
		registry.entries[ack.WatchID].pendingBaseline = p.pendingReasons[producer]
		delete(p.pendingReasons, producer)
	}
	registry.Drain()
	p.registries[producer] = registry
	if !pending {
		d.probeStates++
	}
	return registry, nil
}

// Retired configurations are retained only while a registered ordered producer
// can still observe them. New producers must acknowledge current configuration.
func (d *Diagnostics) reclaim() {
	for id, p := range d.subscriptions {
		if p.removedRevision == 0 {
			continue
		}
		retired := true
		for _, producer := range d.producers {
			if producer.active >= p.addedRevision && producer.active < p.removedRevision {
				retired = false
				break
			}
		}
		if retired {
			d.probeStates -= len(p.registries) + len(p.markerCounts) + len(p.pendingReasons)
			delete(d.subscriptions, id)
		}
	}
}

func (d *Diagnostics) RemoveProducer(expectedRevision, producer uint64) error {
	if d.busy || expectedRevision != d.revision {
		return fmt.Errorf("active capture or stale revision")
	}
	if d.producers[producer] == nil {
		return fmt.Errorf("unknown producer")
	}
	delete(d.producers, producer)
	for _, p := range d.subscriptions {
		if _, ok := p.pendingReasons[producer]; ok {
			delete(p.pendingReasons, producer)
			d.probeStates--
		}
		if p.registries[producer] != nil {
			delete(p.registries, producer)
			d.probeStates--
		}
		if _, ok := p.markerCounts[producer]; ok {
			delete(p.markerCounts, producer)
			d.probeStates--
		}
	}
	d.reclaim()
	return nil
}
