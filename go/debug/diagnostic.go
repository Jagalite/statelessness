package debug

import (
	"fmt"
	"io"
	"math"
	"sort"
	"strings"
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
	Producer                                                                                                   uint64
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
	Watch                        WatchLimits
}

func DefaultDiagnosticLimits() DiagnosticLimits {
	return DiagnosticLimits{MaxSinks: 8, MaxSubscriptions: 64, MaxProducers: 64, MaxProbeStates: 256, MaxSiteBytes: 256, MaxScopeBytes: 4096, Watch: DefaultWatchLimits()}
}

type diagnosticSink struct {
	queue *WatchRegistry
	auth  WatchAuthorization
}
type probeEntry struct {
	subscription                   ProbeSubscription
	registries                     map[uint64]*WatchRegistry
	addedRevision, removedRevision uint64
}

// Diagnostics owns explicit local producer configuration. Each sink has an
// independent bounded queue; neither capture nor probing reads a runtime clock.
type producerCapture struct{ active, pending, effectiveSequence uint64 }
type CaptureAck struct{ Producer, CaptureRevision, EffectiveSequence uint64 }
type Diagnostics struct {
	limits                               DiagnosticLimits
	nextSink, nextSubscription, revision uint64
	sinks                                map[uint64]*diagnosticSink
	subscriptions                        map[uint64]*probeEntry
	busy                                 bool
	producers                            map[uint64]*producerCapture
	probeStates                          int
}

func NewDiagnostics(l DiagnosticLimits) (*Diagnostics, error) {
	if l.MaxSinks <= 0 || l.MaxSubscriptions <= 0 || l.MaxProducers <= 0 || l.MaxProbeStates <= 0 || l.MaxSiteBytes == 0 || l.MaxScopeBytes == 0 {
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
	d.sinks[id] = &diagnosticSink{queue, auth}
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
	d.reclaim()
	if expectedRevision != d.revision {
		return 0, fmt.Errorf("stale capture revision")
	}
	if d.busy || len(d.subscriptions) >= d.limits.MaxSubscriptions || d.nextSubscription == math.MaxUint64 || d.revision == math.MaxUint64 {
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
	if config.Site == "" || uint64(len(config.Site)) > d.limits.MaxSiteBytes || config.MinimumSeverity > ErrorSeverity {
		return 0, fmt.Errorf("invalid probe site or severity")
	}
	scope := config.Scope
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
	d.subscriptions[id] = &probeEntry{subscription: config, registries: map[uint64]*WatchRegistry{}, addedRevision: d.revision}
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
	if uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("probe site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	revision := uint64(0)
	if producer != nil {
		if producer.pending != 0 && context.Snapshot.Sequence >= producer.effectiveSequence {
			producer.active = producer.pending
			producer.pending = 0
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
	for _, id := range ids {
		p := d.subscriptions[id]
		config := p.subscription

		if revision < p.addedRevision || p.removedRevision != 0 && revision >= p.removedRevision {
			continue
		}
		if config.Kind != "probe" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) {
			continue
		}
		c := WatchContext{Snapshot: context.Snapshot, Model: context.Model, Machine: context.Machine, InputVariant: context.InputVariant, OutputVariant: context.OutputVariant, Origin: context.Origin, PropertyFailed: context.PropertyFailed, PropertyStatusKnown: context.PropertyStatusKnown, FailingProperties: context.FailingProperties, Phase: context.Phase}
		registry, e := d.producerRegistry(p, context.Producer)
		if e != nil {
			return e
		}
		if e := registry.Observe(c, lazyValue); e != nil {
			return e
		}
		events, e := registry.Drain()
		if e != nil {
			return e
		}
		for _, event := range events {
			event.WatchID = id
			event.CaptureRevision = revision
			d.sinks[config.Sink].queue.push(event)
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

// Acknowledge is a host assertion about an ordered producer boundary. Producers
// retain their prior configuration until this boundary is actually observed.
func (d *Diagnostics) Acknowledge(producer, expectedRevision, effectiveSequence uint64) (CaptureAck, error) {
	if d.busy || expectedRevision != d.revision {
		return CaptureAck{}, fmt.Errorf("active capture or stale revision")
	}
	p := d.producers[producer]
	if p == nil {
		if len(d.producers) >= d.limits.MaxProducers {
			return CaptureAck{}, fmt.Errorf("producer capacity")
		}
		p = &producerCapture{}
		d.producers[producer] = p
	}
	if p.pending != 0 {
		return CaptureAck{}, fmt.Errorf("producer acknowledgement pending")
	}
	p.pending = expectedRevision
	p.effectiveSequence = effectiveSequence
	return CaptureAck{producer, expectedRevision, effectiveSequence}, nil
}

func (d *Diagnostics) SinkHealth(id uint64) (WatchHealth, error) {
	sink := d.sinks[id]
	if sink == nil {
		return WatchHealth{}, fmt.Errorf("unknown sink")
	}
	return sink.queue.Health(), nil
}

// Marker captures explicit bounded metadata only; it never evaluates a value.
func (d *Diagnostics) Marker(context CaptureContext, site string, severity Severity) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("marker site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	if producer == nil {
		return nil
	}
	if producer.pending != 0 && context.Snapshot.Sequence >= producer.effectiveSequence {
		producer.active = producer.pending
		producer.pending = 0
	}
	ids := make([]uint64, 0, len(d.subscriptions))
	for id := range d.subscriptions {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		p := d.subscriptions[id]
		config := p.subscription
		if config.Kind != "marker" || config.Site != site || severity < config.MinimumSeverity || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
			continue
		}
		d.sinks[config.Sink].queue.push(WatchEvent{WatchID: id, CaptureRevision: producer.active, Sequence: context.Snapshot.Sequence, Kind: "marker"})
	}
	return nil
}

// DropScope records an explicitly omitted scoped observation and invalidates its
// baseline. It cannot recover an unselected earlier in-reducer value.
func (d *Diagnostics) DropScope(context CaptureContext, site string) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	if uint64(len(site)) > d.limits.MaxSiteBytes {
		return fmt.Errorf("site budget")
	}
	d.busy = true
	defer func() { d.busy = false; d.reclaim() }()
	producer := d.producers[context.Producer]
	if producer == nil {
		return nil
	}
	if producer.pending != 0 && context.Snapshot.Sequence >= producer.effectiveSequence {
		producer.active = producer.pending
		producer.pending = 0
	}
	for id, p := range d.subscriptions {
		config := p.subscription
		if config.Site != site || !config.Scope.matches(context) || producer.active < p.addedRevision || p.removedRevision != 0 && producer.active >= p.removedRevision {
			continue
		}
		registry := p.registries[context.Producer]
		if registry == nil {
			continue
		}
		if e := registry.Gap(WatchContext{Snapshot: context.Snapshot, Model: context.Model, Machine: context.Machine, Origin: context.Origin}); e != nil {
			return e
		}
		registry.Drain()
		d.sinks[config.Sink].queue.push(WatchEvent{WatchID: id, CaptureRevision: producer.active, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "scope_incomplete", Completeness: "observation_gap"})
	}
	return nil
}

func (d *Diagnostics) producerRegistry(p *probeEntry, producer uint64) (*WatchRegistry, error) {
	if registry := p.registries[producer]; registry != nil {
		return registry, nil
	}
	if d.probeStates >= d.limits.MaxProbeStates {
		return nil, fmt.Errorf("probe state capacity")
	}
	limits := d.limits.Watch
	limits.MaxWatches = 1
	limits.MaxBaselineBytes /= uint64(d.limits.MaxProbeStates)
	registry, e := NewWatchRegistry(limits, d.sinks[p.subscription.Sink].auth)
	if e != nil {
		return nil, e
	}
	config := cloneWatchConfig(p.subscription.Watch)
	config.EvaluationInterval = 1
	config.DeliveryInterval = p.subscription.SampleEvery
	if _, e = registry.Add(config); e != nil {
		return nil, e
	}
	registry.Drain()
	p.registries[producer] = registry
	d.probeStates++
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
			if producer.active < p.removedRevision {
				retired = false
				break
			}
		}
		if retired {
			d.probeStates -= len(p.registries)
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
		if p.registries[producer] != nil {
			delete(p.registries, producer)
			d.probeStates--
		}
	}
	d.reclaim()
	return nil
}
