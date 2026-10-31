package debug

import (
	"fmt"
	"math"
	"strings"
	"unicode/utf8"
)

type stagedDiagnostic struct {
	sink  uint64
	event WatchEvent
}

type diagnosticSource struct {
	run                                         uint64
	origin, model, machine, entity, correlation string
}

func sourceOf(c CaptureContext) diagnosticSource {
	return diagnosticSource{c.RunID, c.Origin, c.Model, c.Machine, c.Entity, c.Correlation}
}

func validDiagnosticOrigin(origin string) bool {
	switch origin {
	case "simulation", "live", "debug_controlled_live", "test", "replay", "imported":
		return true
	default:
		return false
	}
}

func invalidateDiagnosticBaseline(registry *WatchRegistry, reason string) {
	if registry == nil {
		return
	}
	for _, entry := range registry.entries {
		registry.baselineBytes -= entry.baselineBytes
		entry.baseline, entry.baselineBytes = nil, 0
		entry.source = watchSource{}
		entry.pendingBaseline = reason
		entry.high = false
		entry.lastAlert = nil
	}
}

// DiagnosticScope is a single-owner turn. Captures are staged until Finish and
// do not grant configuration changes or producer acknowledgement.
type DiagnosticScope struct {
	hub                     *Diagnostics
	context                 CaptureContext
	staged                  []stagedDiagnostic
	bytes                   uint64
	active, running, closed bool
}

func (d *Diagnostics) BeginTurn(context CaptureContext) (*DiagnosticScope, error) {
	if d.busy || d.scope != nil {
		return nil, fmt.Errorf("capture active")
	}
	producer := d.producers[context.Producer]
	if producer == nil {
		return nil, fmt.Errorf("unknown producer")
	}
	sequence := context.Snapshot.Sequence
	if producer.observed && sequence <= producer.lastSequence {
		return nil, fmt.Errorf("stale producer boundary")
	}
	if sequence < producer.effectiveSequence {
		return nil, fmt.Errorf("pending configuration boundary")
	}
	if !validDiagnosticOrigin(context.Origin) {
		return nil, fmt.Errorf("invalid diagnostic origin")
	}
	for _, label := range []string{context.Model, context.Machine, context.Entity, context.Correlation, context.InputVariant, context.OutputVariant, context.Property, context.EffectKind, context.SourceSite} {
		if len(label) > 256 || !utf8.ValidString(label) {
			return nil, fmt.Errorf("invalid diagnostic context label")
		}
	}
	cost := uint64(0)
	for _, value := range []*string{&context.Origin, &context.Model, &context.Machine, &context.Entity, &context.Correlation, &context.InputVariant, &context.OutputVariant, &context.Property, &context.EffectKind, &context.SourceSite, &context.Phase} {
		if uint64(len(*value)) > d.limits.MaxScopeBytes-cost {
			return nil, fmt.Errorf("scope budget")
		}
		if value == &context.Phase && !utf8.ValidString(*value) {
			return nil, fmt.Errorf("invalid diagnostic phase")
		}
		cost += uint64(len(*value))
		*value = strings.Clone(*value)
	}
	if uint64(len(context.FailingProperties)) > (d.limits.MaxScopeBytes-cost)/16 {
		return nil, fmt.Errorf("scope budget")
	}
	cost += uint64(len(context.FailingProperties)) * 16
	owned := append([]string(nil), context.FailingProperties...)
	for i, value := range owned {
		if uint64(len(value)) > d.limits.MaxScopeBytes-cost {
			return nil, fmt.Errorf("scope budget")
		}
		if !utf8.ValidString(value) {
			return nil, fmt.Errorf("invalid failing property")
		}
		cost += uint64(len(value))
		owned[i] = strings.Clone(value)
	}
	context.FailingProperties = owned
	context.TurnComplete = nil
	selected := false
	for _, entry := range d.subscriptions {
		if producer.active >= entry.addedRevision && (entry.removedRevision == 0 || producer.active < entry.removedRevision) && entry.subscription.Scope.matches(context) {
			selected = true
			break
		}
	}
	source := sourceOf(context)
	sourceChanged := selected && producer.lastSource != nil && *producer.lastSource != source
	gap := producer.observed && sequence-producer.lastSequence > 1
	needed := 0
	for _, entry := range d.subscriptions {
		if producer.active < entry.addedRevision || (entry.removedRevision != 0 && producer.active >= entry.removedRevision) || entry.registries[context.Producer] != nil {
			continue
		}
		if _, exists := entry.pendingReasons[context.Producer]; !exists && (sourceChanged || gap || !entry.subscription.Scope.matches(context)) {
			needed++
		}
	}
	if needed > d.limits.MaxProbeStates-d.probeStates {
		return nil, fmt.Errorf("probe state capacity")
	}
	affectedSinks := map[uint64]bool{}
	for _, entry := range d.subscriptions {
		if producer.active < entry.addedRevision || (entry.removedRevision != 0 && producer.active >= entry.removedRevision) {
			continue
		}
		registry := entry.registries[context.Producer]
		reason := ""
		if sourceChanged {
			reason = "source_changed"
		} else if gap {
			reason = "observation_gap"
		}
		if gap {
			affectedSinks[entry.subscription.Sink] = true
		}
		if !entry.subscription.Scope.matches(context) {
			reason = "selector_gap"
		}
		if reason != "" {
			if registry != nil {
				invalidateDiagnosticBaseline(registry, reason)
			} else {
				if _, exists := entry.pendingReasons[context.Producer]; !exists {
					d.probeStates++
				}
				entry.pendingReasons[context.Producer] = reason
			}
		}
	}
	for id := range affectedSinks {
		sink := d.sinks[id]
		if sink.observationGaps < math.MaxUint64 {
			sink.observationGaps++
		}
	}
	if selected && (sourceChanged || producer.lastSource == nil) {
		producer.lastSource = &source
	}
	producer.lastSequence = sequence
	producer.observed = true
	scope := &DiagnosticScope{hub: d, context: context, active: true}
	d.scope = scope
	d.busy = true
	return scope, nil
}
func (s *DiagnosticScope) invoke(fn func() error) (err error) {
	if s == nil || !s.active || s.running || s.hub.scope != s {
		return fmt.Errorf("diagnostic scope unavailable")
	}
	s.running = true
	s.hub.busy = false
	defer func() { s.hub.busy = true; s.running = false }()
	return fn()
}
func (s *DiagnosticScope) Probe(site string, severity Severity, value func() InspectAdapter) error {
	return s.invoke(func() error { return s.hub.Probe(s.context, site, severity, value) })
}
func (s *DiagnosticScope) Marker(site string, severity Severity) error {
	return s.invoke(func() error { return s.hub.Marker(s.context, site, severity) })
}

// DropScope records an omitted observation within this turn. Its notification
// follows the same staging and turn budgets as probe and marker records.
func (s *DiagnosticScope) DropScope(site string) error {
	return s.invoke(func() error { return s.hub.DropScope(s.context, site) })
}

func (d *Diagnostics) emitDiagnostic(sink uint64, event WatchEvent) {
	scope := d.scope
	if scope == nil {
		d.enqueueDiagnostic(sink, event)
		return
	}
	q := d.sinks[sink].queue
	if len(scope.staged) >= d.limits.MaxTurnRecords || retainedEvent(event) > d.limits.MaxTurnBytes-scope.bytes {
		q.recordDropped()
		d.dropProtocolEvent(sink, event)
		return
	}
	// Reuse the bounded queue's ownership and node-admission rules before staging.
	scratch, err := NewWatchRegistry(q.limits, q.auth)
	if err != nil {
		q.recordDropped()
		d.dropProtocolEvent(sink, event)
		return
	}
	scratch.push(event)
	if len(scratch.ring) != 1 {
		q.recordDropped()
		d.dropProtocolEvent(sink, event)
		return
	}
	owned := scratch.ring[0]
	scope.bytes += retainedEvent(owned)
	scope.staged = append(scope.staged, stagedDiagnostic{sink, owned})
}

func (d *Diagnostics) enqueueDiagnostic(id uint64, event WatchEvent) {
	sink := d.sinks[id]
	// The protocol queue is the Rust-shaped view: Rust enqueues only scoped
	// probe/marker DiagnosticEvents, each with a complete envelope including
	// turn completeness. Go-only host records (lifecycle notifications and
	// unscoped capture without a known turn outcome) stay in the host queue so
	// they can never block or poison protocol polling.
	if sink.protocolQueue != nil && protocolEnveloped(event) {
		if sink.protocolQueue.push(event) && sink.protocolCaptured < math.MaxUint64 {
			sink.protocolCaptured++
		}
	}
	if sink.queue.push(event) && sink.captured < math.MaxUint64 {
		sink.captured++
	}
}

// protocolEnveloped reports whether a record carries every field of a Rust
// DiagnosticEvent wire envelope.
func protocolEnveloped(event WatchEvent) bool {
	m := event.Diagnostic
	return m != nil && m.TurnCompletenessKnown && (m.Kind == "probe" || m.Kind == "marker")
}

// Finish publishes the staged records exactly once, preserving whether the host
// completed the turn. A false value records an incomplete turn, not a rollback.
// Finish after Close is a no-op.
func (s *DiagnosticScope) Finish(complete bool) error {
	if s != nil && s.closed {
		return nil
	}
	if s == nil || !s.active || s.running || s.hub.scope != s {
		return fmt.Errorf("diagnostic scope unavailable")
	}
	for _, record := range s.staged {
		if record.event.Diagnostic != nil {
			record.event.Diagnostic.TurnCompletenessKnown = true
			record.event.Diagnostic.IncompleteTurn = !complete
		}
		s.hub.enqueueDiagnostic(record.sink, record.event)
	}
	s.staged = nil
	s.bytes = 0
	s.active = false
	s.hub.scope = nil
	s.hub.busy = false
	return nil
}

// Close mirrors Rust's Drop for ScopedDiagnostics: a scope that was not
// finished publishes its staged records as an incomplete turn and releases the
// hub. Hosts should defer it immediately after BeginTurn so a panic or early
// return cannot leave the hub locked:
//
//	scope, err := hub.BeginTurn(ctx)
//	if err != nil { ... }
//	defer scope.Close()
//	... scope.Probe(...) ...
//	return scope.Finish(true)
//
// Close is idempotent and a no-op after Finish. It fails only while a scoped
// capture callback is still running on this scope.
func (s *DiagnosticScope) Close() error {
	if s == nil || s.closed || !s.active {
		return nil
	}
	if s.running {
		return fmt.Errorf("diagnostic scope callback active")
	}
	if err := s.Finish(false); err != nil {
		return err
	}
	s.closed = true
	return nil
}

// A Go-only notification has no reference wire representation. Actual probe
// records rejected by a shared turn limit count as loss for both consumers.
func (d *Diagnostics) dropProtocolEvent(id uint64, event WatchEvent) {
	if sink := d.sinks[id]; sink != nil && sink.protocolQueue != nil && event.Diagnostic != nil {
		sink.protocolQueue.recordDropped()
	}
}
